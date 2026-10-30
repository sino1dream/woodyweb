# 通用编辑页 `/stock/<symbol>/edit/<option>` 迁移

> 对应 PHP：
> * `woody/res/php/_editstockoption.php`（4 行壳子，16 个页面共用）
> * `woody/res/php/_editstockoptionform.php`（407 行表单 + 三张 `switch` 初值表）
> * `woody/res/php/_submitstockoptions.php`（POST 目标，`_updateStockOption*` 一族）
> * `php/stocklink.php:255` `GetStockOptionArray()`（16 项分派表）
> * `php/sql/sqlval.php` / `sqlint.php` / `sqlpair.php` / `sqlstockpair.php`
>
> Python 落点：
> * `python/auto/sql/optionstore.py`（键值表 + 配对表 + 六张日频小表）
> * `python/web/services/stock_option_service.py`（16 项表 + 读写分派）
> * `python/web/routes/stock.py`（`GET /stock/<symbol>/edit`、`GET|POST /stock/<symbol>/edit/<option>`）
> * `python/web/templates/stock_option_{index,form}.html`
> * `python/dbtools/seed_option_tables.py`（演示库缺的三张表 DDL）
>
> 单测：`auto/test_optionstore.py`（25 个）+ `web/tests/test_stock_option_edit.py`（51 个）。
> 逐例对照：`auto/_scratch/_php_optionstore_compare.py`（19 个操作全一致）。

## 一、为什么用"一个路由顶 16 个页面"

生产端那 16 个 `edit*.php` 全是同一段壳子：

```php
<?php
require('php/_editstockoption.php');      // 分派 + EchoAll
require('../../php/ui/_editcn.php');
```

壳子之间**唯一的差别就是文件名** —— `_getEditStockOptionSubmit()` 拿
`$_SERVER['PHP_SELF']` 去 `GetStockOptionArray()` 里 `array_search()`，
反查出"当前是哪个选项"。所以页面名不是导航，是**参数**。

本版把它变成路径参数：`/stock/SZ161130/edit/editnetvalue`。收益不只是少写
15 个文件：

* **可发现**：PHP 那 16 页不进任何导航（`GetStockMenuLinks()`、`EchoLinks()`
  都没有入口），只能手敲 URL。这里补了一个索引页 `/stock/<symbol>/edit`，
  已迁的直接给链接、未迁的写明理由；
* **口径集中**：`empty('0')` → 删除、`WriteDaily` 的容差、周末闸门，
  全都收在服务层一处，16 个页面不可能各自跑偏；
* **写入口带守卫**：PHP 的表单没有"表不存在"这条分支（演示库比生产端少
  三张表），本版把驱动异常原样带到页面上。

## 二、16 项对照表

顺序与 `GetStockOptionArray()`（`php/stocklink.php:255`）**一致**。
PHP 用 `array_search()` 反查，改顺序会让分派错位；本版靠 key 直接查，
但保持原序，方便逐行对照。

| # | key | 动作（`STOCK_OPTION_*` 原文） | 落库 | 日期口径 | 状态 |
|---|---|---|---|---|---|
| 1 | `editcalibration` | 手工校准值 | `calibrationhistory` + `lastcalibration` | 净值表 | 未迁 |
| 2 | `editfund` | 修改对应配对代码和仓位 | `fundpair` + `fundposition` + `calibrationhistory` + `lastcalibration` | 无 | **已迁** |
| 3 | `editnetvalue` | 修改净值 | `netvaluehistory` | 净值表 | **已迁** |
| 4 | `editpremium` | 期货升水 | `futurepremium` | `futurepremium` | **已迁** |
| 5 | `editquarterreport` | 修改季报持仓 | `quarterreport` | 无 | 已有专门页 |
| 6 | `editsharesdiff` | 场内新增(万) | `sharesdiff` | `shareshistory` | **已迁** |
| 7 | `editstock` | 修改股票说明 | `stock.name` | 无 | **已迁** |
| 8 | `editstockadr` | 修改H股对应ADR代码 | `adrpair` + `fundposition` | 无 | **已迁** |
| 9 | `editstockah` | 修改A股对应H股代码 | `ahpair` | 无 | **已迁** |
| 10 | `editstockamount` | 基金申购金额 | `groupitemamount` | 无 | 未迁（账号体系） |
| 11 | `editstockclose` | 更新收盘价 | `stockhistory`（只改已有行） | 昨收那天 | **已迁** |
| 12 | `editstockdividend` | 分红 | `stockdividend` + 复权重算 | 行情表 | 未迁（要重算） |
| 13 | `editstockema` | 修改200/50日EMA | `stockema200` + `stockema50` | 行情表 | **已迁** |
| 14 | `editstockha` | 修改H股对应A股代码 | `ahpair` | 无 | **已迁** |
| 15 | `editstockholdings` | 修改持仓 | `holdings` + `holdingsdate` + `fundposition` | 无 | 已有入口 |
| 16 | `editstocksplit` | 拆股或合股 | `stocksplit` + `stockhistory` 全序列 | 行情表 | 未迁（要重算） |

**已迁 10 项，未迁 6 项。** 未迁的分三类，理由直接写在 `StockOptionSpec.note`
里，页面照原样显示 —— 不做成"能点但会算错"的按钮：

1. **已有专门页**：`editquarterreport` → `/holdings/quarter/<symbol>`；
   `editstockholdings` → 穷举持仓页的「确认更新持仓」（`POST /holdings/submit`）；
2. **依赖账号体系**：`editstockamount` 要先用 member 邮箱 + 分组名换
   `stockgroupitem.id`（`SqlGetMyStockGroupItemId`），账号体系整体暂缓；
3. **要连带重算**：`editstockdividend` / `editstocksplit` 写一行之后要重算
   该日之后的全部复权价与校准值（`_updateStockHistoryAdjCloseByDividend` /
   `BySplit`），`editstocksplit` 还要改交易流水
   （`_updateStockOptionSplitTransactions`）。那是估值链路，不能在这页顺手做；
   `editcalibration` 同理 —— 它要把"手工估算值"换算成校准值
   （`QdiiGetCalibration` + 地区中间价 + INDA/ASHR 的 Yahoo 净值特例）。

## 三、三种表的形状，混用就会写坏

`_updateOptionDailySql()`（`_submitstockoptions.php:208`）对所有**日频表**都是
"空值→按日期删、有值→`WriteDaily`"，而 `WriteVal` 那几张**键值表**根本没有
日期概念。混用会写成"同一天多行"或"把整只标的的仓位按日期删掉"。

| 表 | 主键 | 值列 | 值列类型与比较方式 | 写入口 |
|---|---|---|---|---|
| `fundposition` / `lastcalibration` | `id`（**就是 stock_id**） | `close` | `double`，`MIN_FLOAT_VAL` | `WriteVal`（upsert） |
| `groupitemamount` | `id`（`stockgroupitem.id`） | `num` | `int`，**字符串比较** | `WriteString` |
| `ahpair` / `abpair` / `adrpair` / `fundpair` | `id`（本体） | `stock_id`（配对） | `int`，**字符串比较** | `WritePair` |
| `stockema50/200`/`futurepremium`/`sharesdiff`/`stockdividend`/`stocksplit` | 自增 `id` | `close` | `double`，`MIN_FLOAT_VAL` | `WriteDaily` |

**`WriteString` / `ReadString` / `Add` / `Inc` / `WriteInt` 长在 `IntSql` 上，
不在 `ValSql` 上** —— 这不是笔误。PHP 的分层就是 `ValSql` 只有数值读写，
`IntSql extends ValSql` 才补字符串那套，`PairSql extends IntSql` 因为配对表的
比较方式是字符串。本版第一版把 `write_string` 放在了 `ValStore` 上，是
**PHP 探针报 `Call to undefined method ProbeValSql::WriteString()` 才发现的**
（见 §七）。分层照抄，别"整理"。

**配对表的方向不能反**：`fundpair` 里 `id=SH513100, stock_id=^NDX` 表示
"SH513100 跟踪 ^NDX"。`editstockah`（A 股页）与 `editstockha`（H 股页）
的**删除方向也不同** —— 前者 `DeleteBySymbol(A)`、后者
`DeleteByPairSymbol(H)`，搞反会删掉别人的配对。

## 四、四处刻意偏离（前两条会改变库里的数据）

| # | 位置 | PHP 行为 | 本版行为 | 理由 |
|---|---|---|---|---|
| 1 | `_write_fund` 的仓位判零 | `if ($strPos == '0')`，而 `false == '0'` 为真 → **只填 INDEX 也会走删除分支**（删校准历史 + 删配对 + 删仓位） | `pos` 缺失（`None`）不算零，只有显式 `*0` / `0` 才删 | 页面说明写的是"输入 `INDEX*0` 删除对应关系"，照抄等于把生产端的坑搬过来 |
| 2 | `_write_stockema` 的校验位置 | 先 `SqlDeleteStockEma()` 清空两表，再判断有没有 `/`；而**占位提示串本身就是 `EMA200/50`**，里面有 `/` → 管理员不动那栏直接提交，会把 `'EMA200'` 当数字写进 `double` 列（变 0）并删掉真 EMA | 先把"两段都必须是数字"校验完，不合法**一个字节都不写** | 变量语义没变；合法输入的两条路径与 PHP 完全一致 |
| 3 | `_write_stock` / `_write_stockclose` 的空值 | 空串直接落库（名称被抹空 / 收盘价变 0） | 拒绝并回一句错误 | 名称为空页面就认不出标的；收盘价 0 会让当天涨幅 -100%，所有依赖它的对比失真 |
| 4 | `_read_stock` 的显示 | 把行情里抓到的中文名/英文名接在后面当对照（要联网） | 只显示 `stock.name` | 离线环境。**不影响写入**：这栏是 `stock.name` 的整份替换，看到什么写什么 |

另有几处**容错**上的差异（不改语义）：

* `get_form` 在符号不在 `stock` 表、或选项未迁时仍然渲染页面，只给
  `notice` 不摆表单；PHP 那边是 `EchoStockGroup()` 拿不到引用后直接空页。
* `_read_value` 里各读函数外面包了 `try`，表缺了给空值；PHP 这些取数都在
  `try` 之外，缺表直接 500。演示库少表时"能看见取不到"比白屏有用。
* `_prev_price` 两处都取不到时给**空串**。PHP 的 `rtrim0('')` 得到 `'0'`，
  那个值提交上去等于把收盘价改成 0 —— 宁可不给。
* 未迁项的 POST 直接回错误、不写库（PHP 那边有 15 个能写的分支）。

## 五、`_updateOptionDailySql` 的语义（最容易错的一条）

```php
if (empty($strVal))          return $sql->DeleteByDate(...);   // ← 填 0 也是删除
return $sql->WriteDaily(...);
```

`empty('0')` 为真，所以**管理员把净值填成 `0` 时 PHP 是删行**；
而 `'0.0'` / `'0.00'` 是非空字符串，是**写 0**。这是两套语义，
`_php_empty()`（`''` 与 `'0'`）与 `_is_zero_text()`（`'0'`/`'0.0'`/`'0.00'`）
分别对应，别合并。

`_write_fund` 用的是**后者**（`$strPos == '0'`），`_update_daily` 用的是
**前者**（`empty($strVal)`）。同一个文件里 PHP 自己就是两套，
照抄两套，不要统一。

`WriteDaily` 的三条口径见 `README_dailyclose_write_migration.md` §三：
值差 > `MIN_FLOAT_VAL` 才 UPDATE（不是"已有就不动"）、周末闸门**只在
INSERT 分支**、`UpdateDaily` 按主键 `id` 而不是 `(stock_id, date)`。

页面上"值没变"与"写进去了"长得很像，所以 `_update_daily` 分三种提示：
`已删除该日记录（N 行）` / `已写入 X` / `值没有变化（或落在周末闸门），未写入`。

## 六、演示库缺的三张表

`sharesdiff` / `stockdividend` / `stocksplit` 在演示库里**根本没有**
（生产端有，DDL 由 `DailyCloseSql::Create()` 落地）。`seed_option_tables.py`
按 `CreateDailyCloseTable()` 的形状补上，**幂等**、默认 dry-run：

```
D:\python\python.exe -B dbtools\seed_option_tables.py            # 预演
D:\python\python.exe -B dbtools\seed_option_tables.py --apply    # 真建表
```

列定义要点：`close` 是 **`double(13,6)`**（不是 `stockhistory` 的 `10,3` ——
份额是整数级、分红有 6 位小数，`10,3` 存分红会丢精度）、`UNIQUE(date, stock_id)`、
`KEY(stock_id)`、引擎 **MyISAM**、字符集 `utf8/utf8_unicode_ci`。

建表前先备份（`dbtools/backup_db.py`）。2026-10-30 那次：备份
`E:\mysqldata\stock_data_20261030_091058.sql`（820.55 KB / 30 张表），
建完库从 30 张变 33 张，幂等复查通过。

## 七、PHP 逐例对照：19 个操作

`_scratch/_php_optionstore_check.php` 在本地库建三张临时表
`os_probe_val` / `os_probe_pair` / `os_probe_daily`，用**真**
`sqltable.php` / `sqlkey.php` / `sqlval.php` / `sqlint.php` / `sqlpair.php` /
`sqldailyclose.php` 跑同一串写操作，`_php_optionstore_compare.py` 逐步比
"返回值 + 整表快照"：

```
== ValSql（5 例）==     全部一致
== PairSql（6 例）==    全部一致
== DailyCloseSql（8 例）== 全部一致
19 个操作全一致（val / pair / daily 三段）
```

跑完删表，`os_probe%` 无残留。

### 两处只有"对真身跑"才暴露的错

这两处都是**我的 Python 写错了、探针把它抓出来的**，记在这里免得以后重复：

1. **`WriteString` 的分层错了**。第一版把 `write_string` / `read_string` /
   `write_int` / `add` / `inc` 放在 `ValStore` 上，探针立刻报
   `Call to undefined method ProbeValSql::WriteString()` —— PHP 那边这些方法
   只在 `IntSql` 上。**把 Python 改对，不是把探针绕开**：整块挪进 `IntStore`。
2. **数值文本的打印形式**。对照报了 19 处不一致，全是 `"0"` vs `"0.0"` ——
   PHP `strval(0.0)` 是 `"0"`，Python `str(0.0)` 是 `"0.0"`。
   改用 `ref/stockref.py::_php_floatval` 同一条口径（唯一实现处），不是对称地
   各写一份。

**探针自身的坑**（PHP 侧静默失败，Python 侧会当场 `TypeError`）见 pitfalls
#131 / #132 —— 一个是 `Create()` 硬编码生产库名，一个是
`CalibrationSql::__construct()` 无参导致"多传的表名被静默忽略、写进了真表"。

## 八、单测

`auto/test_optionstore.py`（25 个）用**内存 sqlite**，两处方言差异在包装层抹平：

* `%s` → `?`（pymysql 与 sqlite 的占位符不同）；
* `UPDATE ... LIMIT 1` 去掉 `LIMIT`（sqlite 不支持；WHERE 已是主键，语义不变）。

分组：`ValueStore`（数值/字符串两种比较）、`IntStore`（`add`/`inc`/`read_int`
的"0 与没有同形"）、`PairStore`（ADR 方向、AH 两种删除方向）、
`DailyTableWriteTest`（插入/判重/容差/周末闸门）、`DailyCloseDeleteAllTest`。

**一条刻意的测试限制**：`test_string_mode_compares_text` 只验 `int` 列。
sqlite 的 `REAL` 列读回 `100.0`，`str()` 是 `"100.0"` 而非 `"100"` —— 在
`double` 列上文本比较必然"看起来变了"再写一次。**这不是 bug**：
PHP 从 `double(13,6)` 读回来也是 `"100.000000"`，两边都会多写一次，
结果一样。测试里写清这一点，别拿它当"应该相等"的证据。

`web/tests/test_stock_option_edit.py`（51 个）用 `tests/sqlitedb.py` 的假连接
（`SqliteConnection` / `SqliteCursor` / `FakeSource`），覆盖：索引页 16 项、
未知选项 404、符号不在库不摆表单、未迁项 GET/POST、10 项的默认值、
日频写删、EMA 清空后回填、AH/HA 删除方向、ADR 比例、fund 三支解析、
非管理员 POST 403、失败回填提交值、三个 PHP 口径助手
（`_php_empty` / `_is_zero_text` / `_intval`）。

全量：auto **1606** 绿、web **998** 绿、`compileall` 干净。

## 九、踩过的坑（写进 pitfalls 的）

1. `WriteString` 一族在 `IntSql` 上，不在 `ValSql` 上；
2. `ValSql` 的构造器接受表名，`CalibrationSql` **不接受** —— 后者多传的实参
   被静默忽略（#132）；
3. EMA 的占位串 `EMA200/50` 含 `/`，照抄 PHP 的顺序会把 `EMA200` 写进
   `double` 列并删掉真 EMA（本版已改顺序，见 §四）；
4. `intval('1.5')` 是 **1** → `ADR/1.5` 的 `intval != 1` 不成立 → **不写仓位**。
   照抄。1.5 这种比例只能先存 2 再改，或者直接动库。

## 十、下一步

* 剩 6 项按依赖顺序：`editcalibration` 跟 `fundestcalc` 的校准写入口一起做；
  `editstockdividend` / `editstocksplit` 要先把"改一行 → 重算复权序列与校准值"
  抽成独立链路；`editstockamount` 等账号体系；
* `submit` 族还剩 3 个独立页：`submitdividend.php`（抓 SPDR 官方分红）、
  `submitnetvalue.php`（抓 Invesco / SPDR 官方净值）、`submithistory.php`
  （刷新 Yahoo 历史图表）；
* `deletesymbol.php`（删标的，要连带清 8 张表）最后做，风险最高。
