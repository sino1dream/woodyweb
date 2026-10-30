# `DailyCloseSql` 写方法迁移

> 对应 PHP：`php/sql/sqldailyclose.php`（`InsertDaily` / `UpdateDaily` /
> `WriteDaily` / `WriteDailyArray` / `DeleteByDate` / `DeleteClose` /
> `IsInvalidDate` / `DeleteInvalidDate`），以及两条子类覆写
> `php/sql/sqldailytime.php`、`php/sql/sqlstocksymbol.php`。
>
> Python 落点：`python/auto/sql/historytable.py`。
> 单测：`python/auto/test_historytable.py`（105 个，其中 36 个是本次新增）。
> 逐例对照：`auto/_scratch/_php_dailyclose_write_compare.py`（22 例全一致）。

## 一、为什么补这一步

`DailyCloseSql` 是全站日频历史表的共同底座 —— `stockhistory` /
`netvaluehistory` / `calibrationhistory` / `fundest` / `shareshistory` /
`futurepremium` / `stockema50` / `stockema200` 全是
`(id, stock_id, date, close[, ...])` 这个形状。

但迁移到这一步时它**只有读方法**：`insert_daily` / `update_daily` /
`delete_by_date` / `write_daily` 长在 `DailyStringSql`（季报那条线）上，
位置不对、语义也不对（季报是字符串比较 + 无周末闸门）。

补齐之后，编辑类页面（`editstockclosecn.php`、`editstocksplitcn.php`、
`editstockemacn.php`、`editcalibrationcn.php` …共 7 个）才有共同的落库层，
而不是每页手搓 `UPDATE stockhistory SET ...`。

## 二、方法对照

| Python | PHP | 语义 |
|---|---|---|
| `_execute(sql, params)` | `TableSql::_query()` | 执行并 `commit()`，返回 rowcount |
| `_update_by_id(id, fields)` | `UpdateById($ar, $id)` | 按主键改，`WHERE id = %s LIMIT 1` |
| `make_field_array(sid, date, close)` | `MakeFieldArray()` | `{stock_id, date, close}`，子类往里加列 |
| `insert_daily(sid, date, close)` | `InsertDaily()` | 空日期不写；已有同日行不写；否则 INSERT |
| `update_daily(id, close)` | `UpdateDaily()` | 只改 `close` |
| `write_daily(sid, date, close)` | `WriteDaily()` | 有行比数值、无行过周末闸门再插 |
| `write_daily_array(sid, {date: close})` | `WriteDailyArray()` | 逐日写，返回**真正写进库的条数** |
| `delete_by_date(sid, date)` | `DeleteByDate()` | 返回**删除行数**（PHP 也是行数） |
| `delete_close(close, dry_run=True)` | `DeleteClose()` | 全表删 `close = 0.000000`，**默认 dry-run** |
| `is_invalid_date(record, today)` | `IsInvalidDate()` | 太老 / 周末 / 未来 |
| `find_invalid_records()` | `DeleteInvalid()` 的扫描段 | 只读，返回待删行 |
| `delete_invalid_dates(dry_run=True)` | `DeleteInvalidDate()` | **默认 dry-run** |
| `DailyTimeSql.make_field_array` / `update_daily` | `DailyTimeSql` 覆写 | 多带 `time` |
| `CalibrationSql.update_daily_average` | `UpdateDailyAverage()` | `num` 重置为 1 |
| `CalibrationSql.write_daily_average` | `WriteDailyAverage()` | `num` 累加平均 |
| `StockHistorySql.write_history` | `WriteHistory()` | 三列各有判据（见 §四） |

## 三、四条最容易写错的口径

### 1. `write_daily` 是"值变了就覆盖"，不是"已有就不动"

```php
if ($record = $this->GetRecord($strKeyId, $strDate))
{
    if (abs(floatval($record['close']) - floatval($strClose)) > MIN_FLOAT_VAL)
        return $this->UpdateDaily($record['id'], $strClose);
}
```

`MIN_FLOAT_VAL = 0.0000005`。差值在阈值内视为没变、返回 `False`（一句 SQL 都不发）；
超出就 UPDATE。

**为什么强调**：早期实现只照抄了 `InsertDaily` 的那句判重，结果校准值一旦写错
就再也刷不回来 —— 改锚点口径后正是靠这一支把旧值刷成新值。

### 2. 周末闸门**只在 INSERT 分支**

```php
else
{
    $ymd = new StringYMD($strDate);
    if ($ymd->IsWeekend())  return false;   // sina fund may provide false weekend data
    return $this->InsertDaily($strKeyId, $strDate, $strClose);
}
```

库里**已有**的周末行照样能改（历史数据修复场景）；只有"新增一行周末数据"被挡。

### 3. `InsertDaily` 不是 upsert

先 `GetRecord` 查一遍，有就返回 `False`。不靠 `UNIQUE (date, stock_id)` 兜底 ——
那样会抛异常。同时 `InsertDaily` **本身没有周末闸门**（闸门在 `WriteDaily` 里），
所以 `insert_daily` 能写周末行，`write_daily` 不能。

### 4. `update_daily` 按主键 id，不是按 `(stock_id, date)`

`UpdateById($ar, $strId)` → `WHERE id = 'xxx' LIMIT 1`。拿 `record["id"]` 才有用；
拿 `(stock_id, date)` 去改会静默无效果。

## 四、三张表的写方法不是同一套

| 表 | 类 | 写方法 | 差异 |
|---|---|---|---|
| `netvaluehistory` / `shareshistory` / `futurepremium` / `stockema*` | `DailyCloseSql` | `write_daily` | 基准 |
| `quarterreport` | `DailyStringSql` | `write_daily` | 字符串比较（`php_text_equal`）、**无**周末闸门 |
| `calibrationhistory` / `fundest` | `DailyTimeSql` | 继承 `write_daily` | INSERT/UPDATE 多带 `time`（`DebugGetTime()`，**Asia/Shanghai**） |
| `calibrationhistory` | `CalibrationSql` | 继承 + 覆写 | `num`：`insert`=1、`update_daily` **重置 1**、`write_daily_average` **累加平均** |
| `stockhistory` | `StockHistorySql` | **`write_history`** | 多 `volume`/`adjclose`，不能用父类方法 |

**`stockhistory` 那条要单独说**：它比日频表多 `volume`（`bigint unsigned NOT NULL`）
与 `adjclose`（`double(13,6) NOT NULL`），两列都没有默认值。父类的
`insert_daily` 只写三列，直接调用会撞 `1364 Field doesn't have a default value`。
PHP 那边同样不覆写 `WriteDaily`，而是另起 `WriteHistory()`（`sqlstocksymbol.php:27`）：

* `close` 的容差是**硬编码 0.0005**（因为列是 `double(10,3)`），不是 `MIN_FLOAT_VAL`；
* `volume` 用 `==` 宽松比较（PHP 8 的两个数字串按数值比）；
* `adjclose` 用 `MIN_FLOAT_VAL`；
* **没有周末闸门** —— 抓回来的历史行情就是权威数据；
* 三列都没变返回 `False`（`count($ar) > 0` 不成立）。

`WriteDailyAverage` 那条路**也没有周末闸门**，PHP 里那三行是被注释掉的，注释写着
"hf_CHA50CFD calibration may in Sunday, which is Monday for SH000300" —— 期货的
"周日"在这边可能是标的的"周一"，挡了就漏数据。

## 五、MyISAM：没有事务，所以"先查后写"

`stock_data` 30 张表里 28 张是 MyISAM（只有 `stock_minute_data` 与
`convertible_bond_meta` 是 InnoDB），日频历史表**全部**是 MyISAM。

后果：`connection.commit()` 在 MyISAM 上是空操作，**写坏了回滚不回来**。

所以本类的写方法一律"先查后写"（`get_record` → 判断 → 单条 DML），不靠事务兜底；
批量改历史数据前先跑 `python/dbtools/backup_db.py`。`_execute` 的 docstring 里写了
这一条，别改成 `executemany` + 事务。

## 六、三处刻意偏离

| 位置 | PHP | Python | 理由 |
|---|---|---|---|
| `_prepare_close(None)` | 拼成 `close = ''`，由 MySQL 非 strict 模式转 0 | 显式给 `0.0` | 不依赖服务器 `sql_mode`（生产端 strict 与否未知）；结果相同 |
| `_update_by_id` 收到空 id | `_SqlBuildWhere_id('')` 拼出 `id = ''`，执行后影响 0 行 | 直接返回 `False`、不发 SQL | 少一次无意义往返，行为等价 |
| `is_invalid_date` 解析不出的日期 | `StringYMD` 退回 `time()`（当作今天）→ 反而**不算** invalid | 判 `True`（当脏行报出来） | `date` 列存不进畸形值；清理场景宁可多报 |

`delete_close` / `delete_invalid_dates` 默认 **dry-run**（PHP 无此概念）：这两个是
**全表**操作，PHP 侧唯一调用点是调试脚本 `php/test.php:129/132`，同文件第 132 行
自己还写着 "this can be very slow!"。要真删得显式 `dry_run=False`。

## 七、测试与逐例对照

单测（`test_historytable.py`，36 个新增）按口径分组：

* `DailyCloseSqlWriteTest` —— 插入/判重/覆盖/容差/周末闸门/批量计数/删除/`delete_close` dry-run；
* `InvalidDateTest` —— 太老/周末/未来/边界（2014-01-01 是周三，界内）/畸形串/清理 dry-run；
* `TimeTableWriteTest` —— `time` 字段、上海时区、`num` 重置 vs 累加、累加路无周末闸门；
* `StockHistoryWriteTest` —— 五列插入、`adjclose` 默认、0.0005 容差、只改变化列、空 volume 落 0。

真实 PHP 对照（`_scratch/_php_dailyclose_write_compare.py`）在本地库建临时表
`dcw_probe` / `dcw_probe_cal`，同一序列跑 PHP 真身与 Python，逐步比
"返回值 + 全表快照"：

```
== DailyCloseSql（15 例）==   全部 OK
== CalibrationSql（7 例）==   全部 OK
22 例，0 处不一致
```

PHP 侧用的是真实 `sqltable.php` / `sqlkey.php` / `sqldailyclose.php` /
`sqldailytime.php`，只有最底下那四个执行函数（在 `php/sql.php`，它 include 了
email/httplink/ui，CLI 下跑不起来）换成等价实现 —— **拼 SQL 的是真身**。
`time` 是动态值，快照里不比。

## 八、踩到的坑（写进 pitfalls 的两条）

1. **`TableSql::__construct` 无条件调 `Create()`，而 `CreateTable()` 把库名
   硬编码成生产库**（`sqltable.php:10` / `:88`）。所以任何 `new XxxSql()` 在本地库
   上都会打一条 `Unknown database` —— 是个**无害但极易误判**的噪音，探针里把
   `Create()` 短路掉即可。
2. **`CalibrationSql::__construct()` 是无参的，PHP 对多余实参静默忽略**。
   探针子类 `new ProbeCalibrationSql('dcw_probe_cal')` 照收不误，**实际写进了真表
   `calibrationhistory`**：SZ161130 多了 2026-09-27/29/30 三行（id 419/420/421）。
   已按"写前 325 行、写后 328 行、原 max(id)=418"三条证据定位并删除，行数复原。
   正确写法是绕到 `DailyTimeSql::__construct($table, 'stock')`。

## 九、下一步

底座有了，接着是**一个通用编辑页** `/stock/<symbol>/edit/<option>`：

* 选项表由 `GetStockOptionArray()`（`php/stocklink.php:255`）驱动，一套模板 + 一个
  POST 顶 15 个页面；
* `submitstockoptions.php::_updateOptionDailySql()` 的语义是 `empty($strVal)` →
  `DeleteByDate`，即**填 0 也是删除**（`empty('0')` 为真，`'0.00'` 不为真），
  已有 `_php_empty()` 可复用；
* 先做能落在 `DailyCloseSql` 上的 7 项（收盘价 / EMA / 拆股 / 校准 / 净值 /
  份额 / 期货溢价），其余 8 项要么缺表（`sharesdiff`、`stockdividend` 演示库里
  根本没有）、要么要配 `ahpair` / `abpair` / `adrpair` 三张配对表。
