# 指数历史种子：解开 `^NDX` 的鸡生蛋

## 一、为什么需要这一步

SMA 表（`smaparagraph`）的代码早就完成了，但**页面上一直看不到**。
根因不是代码，是数据 —— 而且是**自锁**的：

行情 daemon 的 `collect_symbols_from_source()` 是**从 `stockhistory` 反推**
要抓哪些符号的（`php/quoteupdater` / `python/auto/updater/quoteupdater.py:600`）：

```sql
SELECT s.id, s.symbol FROM stock s
INNER JOIN stockhistory h ON h.stock_id = s.id GROUP BY s.id, s.symbol
```

所以**一个符号没有历史就永远进不了抓取集合**，进不了集合就永远不会有历史。
演示库里 `^NDX` 恰好一行历史都没有 —— 而它正是 QDII 的**估值标的**，
`EchoQdiiSmaParagraph()` 用的就是它。

我之前的生产端核对也确认了这不是"生产端本来就不显示"：`sz161130cn.php`
的 `^NDX` 那一行有真实数据（`30883.15 / 0.51% / 17:16`），均线表也是满的。

## 二、数据来源与可信度

**新浪美股日线接口**（`auto/indexhistory.py`）：

```
https://stock.finance.sina.com.cn/usstock/api/jsonp.php/
  IO.XSRV2.CallbackList%5B%5D/US_MinKService.getDailyK?symbol=.NDX
```

返回 JSONP：`IO.XSRV2.CallbackList[]([{"d":"2014-02-18","o":"...","h":"...","l":"...","c":"...","v":"..."}])`

* 指数用**点号前缀**：`.NDX`（纳斯达克 100）、`.INX`（标普 500）；
* 普通美股直接用符号（`QQQ`）；
* 日期**升序**，最新在最后。

**实测与生产端逐字一致** —— 这是"这不是编出来的数"的证据：

| 来源 | 值 |
|---|---|
| 新浪日线 `^NDX` 2026-10-09 | `30883.15` |
| 生产端 `sz161130cn.php` 参考数据表 | `30883.15` |
| 新浪日线 `QQQ` 2026-10-08 | `747.58` |
| 生产端 `QQQnetvalueclosetable` | `747.58` |
| 新浪日线 `^GSPC` 2026-10-09 | `7811.54` |
| 实时抓取（`quotecli --check`） | `7811.54` |

为什么不试 Yahoo：`navsource.py` 已经记过"`query1/query2.finance.yahoo.com`
的 v7/v8 接口与网页**全部返回 403**"。东财的 `push2his` 指数接口也能用
（`secid=100.NDX`），但新浪这条与生产端同源，优先用它。

## 三、EMA 为什么一起算

`StockHistory._get_EMA()` 是**直接查表**的，不是算出来的：

```php
$sql = GetStockEmaSql($iDays);
$this->arSMA[$strName] = $sql->GetClose($this->GetStockId(), $this->strStartDate);
```

而 `stockema50` / `stockema200` 原来是空的 → `GetBullBear()` 恒为 `None` →
页面上不会出现"牛市/熊市"。所以 `seed_index_history.py` 用标准递推补上：

```
EMA_t = k * close_t + (1 - k) * EMA_{t-1},  k = 2 / (n + 1)
```

种子取**前 n 个的简单均值**（业界常见做法）。

> **已知偏差**：PHP 那边的 EMA 是外部算好写库的，所以这里只保证"合理"，
> 不保证与某个 PHP 公式逐字对齐。实测 `EMA50 = 29893.8` 与生产端
> `小牛熊分界 29893.81`、`EMA200 = 27925.8` 与 `牛熊分界 27926.18`
> 在四位有效数字内一致 —— 说明递推口径与生产端相同。

## 四、用法

```powershell
cd F:\woody\woodyweb\python
python -B sql\backup_demo_tables.py                 # 改数据前先备份
python -B sql\seed_index_history.py                 # 预演（只抓不写）
python -B sql\seed_index_history.py --apply         # 真写
python -B sql\seed_index_history.py --apply --days 800

cd auto
python -B quoteupdatercli.py --once                 # daemon 会自然收上两个指数
```

实测结果（`--days 800`）：

```
^NDX    写入 800 行历史（2023-08-03 ~ 2026-10-09）+ EMA50 751 行 + EMA200 601 行
^GSPC   同上
```

## 五、⚠️ 第一步"改代码"改成了"不改代码"

原计划的第一步是"给 `collect_symbols_from_source()` 加上指数类标的"。
**实际做下来发现不需要改** —— 那个函数是从 `stockhistory` 反推符号的，
所以种子一种下去它就**自动**把两个指数收进来了。

实测种子后：

```
daemon 现在会抓 10 个符号：
   QQQ, SH513100, SPY, SZ161125, SZ161130, SZ162411,
   ^GSPC, ^NDX, hf_ES, hf_NQ
```

这比"改代码加白名单"更好：不用维护一份"要抓哪些指数"的清单，
机制本身就是自持续的（有历史 → 被收 → 持续更新历史）。

## 六、踩到的一个真 bug：整列「溢价」都是空的

`SmaService._row()` 原来调用 `ref.get_percentage_display(fval)`，
而 **`refobj.EstReference` 上没有那个方法**（它只有
`get_percentage_text`）→ `_invoke` 静默返回空串 → **整列「溢价」都是空的**，
页面不报错。

PHP 的 `GetPercentageDisplay()` 是"文本 + 染色"两步
（`php/stockref.php:146`）：

```php
$strDisp = $this->GetPercentageText($fDivisor, $fDividend);
if (str_ends_with($strDisp, '%'))  $strColor = str_starts_with($strDisp, '-') ? 'red' : 'black';
else                               $strColor = 'gray';
return GetFontElement($strDisp, $strColor);
```

而本项目的染色由 `reference_service.percentage_cell()` 负责，所以 SMA 表
只需要**文本** —— 改成 `get_percentage_text` 之后与生产端逐字一致：

```
5日  30998.38  -0.37%   30923.01  -0.13%      ← 生产端完全相同
10日 30791.92   0.3%    30848.49   0.11%
20日 30376.32   1.67%   30456.24   1.4%
```

### 这个 bug 为什么没有测试抓到

`web/tests/test_sma.py` 的 `FakeRef` **多实现了一个真实类上没有的方法**
（`get_percentage_display`），于是测试全绿、真实页面空白。

已补 `FakeRefContractTest`：直接对着 `refobj.EstReference` 检查
"服务调用的每个方法真实类上都有"，并显式断言
`get_percentage_display` **不该**存在（存在就说明那段 PHP 行为要重新核对）。

**教训**：替身多实现一个方法，就会把"真实类没有它"这件事掩盖掉。
这与 pitfalls 第 35/36 条同属"静默错误"家族。

## 七、验收（对应生产端 `sz161130cn.php`）

| 项 | 生产端 | 本版 |
|---|---|---|
| 参考数据 `^NDX` 行 | `30883.15 / 0.51% / 17:16` | **逐字一致** |
| 参考数据 `^GSPC` 行 | `7811.54 / 0.59% / 16:37` | **逐字一致** |
| 均线表 `5日` | `30998.43 / -0.37%` | `30998.38 / -0.37%` |
| 均线表 `20日` | `30376.34 / 1.67%` | `30376.32 / 1.67%` |
| 均线表 `牛熊分界` | `27926.18 / 10.59%` | `27925.8 / 10.59%` |
| 顶部「牛市」 | 有 | 有 |
| 页面体积 | — | 21 KB → **55 KB** |

（均线数值的小数末位差异来自"最新一条历史 vs 实时快照"的取法，
以及演示库与生产库的日期边界；百分比完全一致。）

参考数据表从 **4 行 → 7 行**（多了有价的 `^NDX`）。

## 八、仍未完成

1. **`TQQQ` 行还是空的**：`fundpair` 里已补 `TQQQ → ^NDX`，
   但 `TQQQ` 自己**没有 `stockhistory`** → 进不了 daemon 的抓取集合。
   解法与 `^NDX` 完全相同：`seed_index_history.py` 的 `TARGETS` 加上 `TQQQ`
   （它是普通美股，接口直接支持），或者把那个脚本的 `TARGETS` 做成"所有
   无历史的 ETF"。
2. **`USDCNH`/`USDCNY` 行还是空的**：库里的即期符号是
   `fx_susdcny`（`refobj.QDII_REGIONS` 用的），而 `USDCNH` 是生产端
   参考数据表里的显示名。这一条是"符号口径"问题，不是缺数据 ——
   需要先决定参考数据表该显示哪个符号。
3. **详情页还缺生产端的几段**：校准记录、基金指数对照表、基金溢价记录
   （8 列）、净值价格比较（QQQ/TQQQ）。参考数据生产端是 **10 行**
   （比本版多盘后交易行）。
