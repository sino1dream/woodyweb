# 详情页补「校准记录」（`web/services/calibration_service.py`）

对应 `php/ui/calibrationhistoryparagraph.php`（81 行，一个函数）。
生产端 `sz161125cn.php` 上有**两张**这样的表，本文两部分都覆盖。

---

## 一、结论先摆在前面

| 问题 | 结论 |
|---|---|
| 要不要另写一套取数 | **不要**。`HistoryService` 的 `calibrationhistory` 已经是同一个 PHP 文件的迁移版，这里只做形状转换 |
| DOM id 用哪个 | 沿用 PHP 的 `<符号>calibrationhistory`，两张表的 id 分别是 `SZ161125calibrationhistory` 与 `^GSPCcalibrationhistory` |
| 没有记录怎么办 | **整段不画**（有意偏离 PHP：PHP 会留一张只有表头的空表） |

## 二、为什么不重写取数

`php/ui/calibrationhistoryparagraph.php` 只有 `EchoCalibrationHistoryParagraph()`
一个函数，它被**三处**调用：

* `/history/calibrationhistory/<符号>` 整页 —— 已迁移为 `HistoryService`；
* `php/ui/fundestparagraph.php:157` —— `($ref, 0, 1)`，基金自己，紧跟估值表；
* `php/ui/smaparagraph.php:218` —— `($ref->GetEstRef(), 0, 1)`，中间层标的，
  摆在期货均线表**之前**。

PHP 里这三处本来就是同一个函数，所以 Python 也不该有两份实现。本模块把
`HistoryService.get_table()` 的结果（`{"columns", "rows", "total"}`）
转成详情页统一的段落结构（`{"id", "title", "columns", "rows", "count", "link"}`），
交给 `_data_table.html` 渲染。**判列（`IsFundA`）与对冲倍数表仍在历史服务那边**，
那里改动的话详情页自动跟着走。

### 两个调用点的差别只有"传给谁的符号"

```python
_build_calibration(symbol)                     # fundestparagraph:157 —— 基金自己
_calibration_of(ref.get_est_ref())             # smaparagraph:218 —— 中间层标的
```

写起来像一行，看错了却不报错：页面照样出一张表、行数也对，
只是那一列的数据来自另一张历史表。

## 三、一处有意偏离：没有记录就整段不画

PHP 的 `EchoTableParagraphBegin()` 先 echo 表头，没有数据行时页面留一张空表。
本版返回 `None`、模板整段跳过 —— 与详情页其它四张表一致。

现实理由：`calibrationhistory` 只有**自动校准跑过**才有数据，
演示库里多数符号是空的，一律画表头会让详情页挂一串空框。

## 四、实测（真实演示库）

| 符号 | 区块数 | 备注 |
|---|---|---|
| `SZ161125` | 1 | 6 列（含「净值 / 对冲值」），2026-10-09 / 15810.585 / 23:28 / 1 |
| `SZ161130` | 1 | 同上 |
| `SH513100` | 1 | 同上 |
| `QQQ` / `SPY` | 1 | 4 列（非 A 股基金，没有净值两列） |
| `SZ162411` | 0 | 库里没有校准记录 |

## 五、途中踩到的两个坑

### 5.1 取数用的是过期的构造签名

最初 `_history_service()` 自己 `new` 一个数据源：

```python
HistoryService(MySqlFundDataSource())        # 缺 connection
```

`HistoryService.__init__` 要 `(source, connection)`，构造直接抛异常，
而它被 `except Exception` 吞掉 → 一律返回 `None` → 页面上一张表都没有。
改成 `default_history_service_factory()`（走`web.db.get_db` 的**请求级**连接），
顺带与其它四个段落同源、不多开连接。

### 5.2 测试替身的列形状造错了

`FakeHistoryService` 的 `columns` 写成了字符串列表，
而真实的 `history_service._column()` 返回 `{"text", "color", "width"}`。
单元测试**照样全绿**（它只比列表相等），页面却渲染出一排空表头。
现在替身照抄真实列形状，并在路由测试里钉住 `>日期<` 这种断言。

> 教训：服务层返回的是"给模板的数据契约"，替身必须与真值的**形状**一致，
> 光比数值会让模板那一侧的契约裸奔。

## 六、测试

* `web/tests/test_calibration.py`（19 个，离线）—— 复用关系、空表返回 None、
  id 形状、链接构造；
* `web/tests/test_calibration_route.py`（8 个，需要 flask）—— 页面上真有那张表、
  没有记录时整段消失、第二个调用点查的是 `^NDX` 而不是基金自己。

## 七、仍未完成

* 「净值」那一格在当前演示库里是空的（需要 `netvaluehistory` 有对应日期的行）；
* PHP 链接只在 `IsTableCommonDisplay()` 为真时挂，本版两个调用点都传 link，
  将来若加"显示全部"的开关，得把这个判断补回来。
