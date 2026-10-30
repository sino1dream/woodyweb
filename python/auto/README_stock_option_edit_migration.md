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
> 单测：`auto/test_optionstore.py`（25 个）+ `web/tests/test_stock_option_edit.py`（86 个）。
> 逐例对照：`auto/_scratch/_php_optionstore_compare.py`（19 个操作全一致）+
> `auto/_scratch/_php_calibration_write_compare.py`（14 例全一致）+
> `auto/_scratch/_php_dividend_split_compare.py`（19 例：14 一致 / 5 按设计差异 / 0 失败）。

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
| 1 | `editcalibration` | 手工校准值 | `calibrationhistory` + `lastcalibration` | 净值表 | **已迁**（§六） |
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
| 12 | `editstockdividend` | 分红 | `stockdividend` + 复权重算 | 行情表 | **已迁**（§七） |
| 13 | `editstockema` | 修改200/50日EMA | `stockema200` + `stockema50` | 行情表 | **已迁** |
| 14 | `editstockha` | 修改H股对应A股代码 | `ahpair` | 无 | **已迁** |
| 15 | `editstockholdings` | 修改持仓 | `holdings` + `holdingsdate` + `fundposition` | 无 | 已有入口 |
| 16 | `editstocksplit` | 拆股或合股 | `stocksplit` + `stockhistory` 全序列 | 行情表 | **已迁**（§七） |

**已迁 13 项，未迁 3 项。** 未迁的分两类，理由直接写在 `StockOptionSpec.note`
里，页面照原样显示 —— 不做成"能点但会算错"的按钮：

1. **已有专门页**：`editquarterreport` → `/holdings/quarter/<symbol>`；
   `editstockholdings` → 穷举持仓页的「确认更新持仓」（`POST /holdings/submit`）；
2. **依赖账号体系**：`editstockamount` 要先用 member 邮箱 + 分组名换
   `stockgroupitem.id`（`SqlGetMyStockGroupItemId`），账号体系整体暂缓。

第三类"**缺表 / 要连带重算**"原本有 3 项，现在都落地了：`editsharesdiff` 只是
一天一个数字（`_updateOptionDailySql` 一条路）；`editstockdividend` /
`editstocksplit` 的连带重算（校准值 / 自身净值 / 复权序列 / 拆股交易流水）
收在 `_write_stockdividend` / `_write_stocksplit` 里，见 §七。

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
（见 §九）。分层照抄，别"整理"。

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

## 六、`editcalibration`：填的是估算值，落库的是校准值

`_updateStockOptionCalibration()`（`_submitstockoptions.php:340`）与其它 15 项
**长得一样但语义不同**：表单里那一栏写的是**估算值**（页面上显示的那个点位），
写库前要先换算成**校准值**：

```
境内指数基金   cal = est ÷ 净值              （$strCNY 写死 '1.0'）
QDII 四地区    cal = est × 地区中间价 ÷ 净值
```

`QdiiGetCalibration($strEst, $strCNY, $strNetValue)` 只有一行
`floatval($strEst) * floatval($strCNY) / floatval($strNetValue)`
（`php/stock/qdiiref.php:3`）。**净值是这只基金自己的**（`netvaluehistory`
的 close，不是行情价）；中间价是 `USCNY` / `HKCNY` / `JPCNY` / `EUCNY` 四张
外汇符号的净值。

### 读初值：值取"对方标的"，日期取净值日

`_read_calibration` 走的是"对方标的的净值"（`_best_est_net_value`：
该日净值 → 该日收盘 → 昨收），不是一个数。日期口径是 `DATE_NETVALUE`，
与 `editnetvalue` 同一支 —— 校准值本来就跟着净值日走。

**`KWEB` 是例外**：`_getStockOptionDate()` 的 `DATE_NETVALUE` 分支里，
KWEB 取的是"行情前一日"（`GetDatePrev($his_sql->GetDateNow($strStockId))`），
**不**退到净值表/行情表最新一天。判断写在 `_default_date()` 里，旁边注了
PHP 行号 —— 这类"某一只标的有自己的日期口径"的分支最容易在重构时被抹平。

### 空值：一次删两处，而且前者不分日期

```php
if (empty($strVal))  $last_sql->DeleteById($strStockId);        // 整只标的一条
_updateOptionDailySql(..., $strVal);                            // 按日期删
```

`lastcalibration` 的主键就是 `stock_id`，所以那一条**没有日期概念** ——
删它就是这只基金的"上次校准值"彻底没了。少删这一步的症状很阴：估值链路
继续拿旧校准值兜底，页面上看着像"删了没生效"。
`'0'` 也走这一支（`empty('0')` 为真，§五）。

### 三支没迁的特例，一律拒绝写入

| 符号 | PHP 怎么算 | 本版 |
|---|---|---|
| `INDA` / `ASHR` | 先 `YahooGetNetValue($ref)` 抓官方净值，再 `CalcFactor` | 拒绝：抓取链路没迁 |
| `hf_CHA50CFD` | 用期货 VWAP（`$ref->GetVal()`）换算 | 拒绝：行情源没迁 |
| 商品期货基金（`in_arrayChinaFuture`） | PHP 自己也是 `unhandled China future symbol; return;` | 拒绝，理由与 PHP 一致 |

不猜、不退化成"用别的数硬算"。**读初值那一侧也不假装能算**：`KWEB` 这类
取不到 est 的符号，读出来是占位串 `对方净值`，不是 0。

### 一处容错偏离：分母为 0 不写

基金净值取不到时 PHP 是 `floatval(false)` = 0：

* **PHP 8**（本机 8.0.10）除零直接抛 `DivisionByZeroError` → 整页 500；
* **PHP 7** 算出 `INF`，`strval(INF)` 是 `"INF"`，非 strict 模式往 `double`
  列写进去变 **0** —— 校准值变 0 等于把这只基金的估值整条打废，**而且不报错**。

本版直接拒绝并说清缺什么。这一条是拿真 PHP 跑出来的：探针把
`DivisionByZeroError` 记成 `throw:DivisionByZeroError:Division by zero`，
所以它在对照表里是"设计差异"，不是"通过"（见 §九）。

### 另一处只有对着真身跑才会看见的：周末闸门静默丢行

`calibrationhistory` 的 `WriteDaily` 继承的是 `DailyCloseSql::WriteDaily`，
**周末闸门在**（`WriteDailyAverage` 那三行才是被注释掉的，注释写着
`hf_CHA50CFD calibration may in Sunday`）。所以周六写新值 → 一行都不插。

坑在于 PHP 的返回值是"**要写的值**"不是"写没写"：闸门拦下时它照样把换算结果
返回给页面，看起来像成功。本版把这一句说出来（`…与库里那条相同（或落在周末
闸门），未写入`），值和库里的对得上才算数。

## 七、`editstockdividend` / `editstocksplit`：写一行，连带改一片

这两项与前面 11 项的结构差别只有一条：**它们不止写自己那张表**。分红写
`stockdividend` 之后要让校准值、基金净值、复权序列一起动；拆股写 `stocksplit`
之后还要补成员交易流水、删 SMA 配置。所以 `_write_stockdividend` /
`_write_stocksplit` 的 docstring 比代码长 —— 每一段副作用为什么在这、边界在哪，
都写在方法里。这里只记**会写坏数据的那几条口径**。

### 分红：三段副作用，次序照抄，整天包在 `if` 里

```php
if (_updateOptionDailySql($div_sql, $strStockId, $strDate, $strVal))   // 写/删分红行
{   ... 改校准值 → 改自身净值 → 重算复权价 ... }
```

1. **校准值**：`calibrationhistory` 里 `date_prev` 那一行改成
   `昨收 × 原净值 ÷ 新净值`。分红让净值掉了 `D`，同一个配对关系下估值因子必须
   跟着涨，否则一除就跳空；
2. **自身净值**：`netvaluehistory` 的 `date_prev` 减去 `D`；
3. **复权价**：`stockhistory` 里 `date <= 该日` 的全部 `adjclose` 减 `D`，
   **当天那一行除外**（`if ($record['date'] != $strYMD)`）—— 当天已经是除权后的
   价格，再减一次就是重复扣。

包在 `if` 里的后果得说清：**值没变（`MIN_FLOAT_VAL` 内）、落在周末闸门、
清空/填 `0` 删行，三种情况都不重算**。前两种是"本来就不用算"，第三种是照抄的
硬伤 —— 删掉分红以后复权价不会涨回去、净值也不会加回来。这一版修不了：删行那
一刻"当初减了多少"只剩 `stockdividend` 里那一行，而它正是要被删掉的东西。
页面消息直接把这句话说出来（`复权价与净值**没有**回滚`），不让人以为删了会自动
还原。

### 拆股：四段副作用，后两段在 `if` 外面

1. **交易流水**：给每个**持有该标的**的成员补两笔 —— 按昨收价卖出全部 `Q` 股、
   再按 `昨收 ÷ 比例` 买回 `intval(比例 × Q)` 股，然后回填成员预统计
   （`quantity/cost/record`）。这一步在 `if ($sql->InsertDaily(...))` 里面；
2. **校准值**：本标的有校准行 → `昨收 × 比例` 写回 `date_prev`；否则遍历把它
   当 est 的 QDII 基金 → `昨收 ÷ 比例`。**两支的乘除方向相反，这是 PHP 原文**，
   和分红那两支一样，照抄不动；
3. **复权价**：`date <= 该日` 的全部 `adjclose` 除以比例，**连当天一起除**；
4. **删 SMA 配置**：`unlinkConfigFile($strSymbol)` → 删
   `<PALMMICRO_SMA_CACHE_DIR>/config/<符号>.txt`。比例变了，缓存里的均线全作废。

第 2、3 段写在 `if` 外面，于是**同一天提交两次 `10:1`，复权价会被除两次**
（1/100）、校准值会被乘两次。流水不会重复插（`InsertDaily` 已有行返回 false）。
照抄，但返回消息里明说"当天已有拆股记录、本次没有新增"外加一句"校准与复权价
又重算了一遍"，免得管理员看没变化又点一次。

`intval` 是**向零截断**：3 股按 1.5 倍拆只给 4 股，半股被吞。交易表只记整数股，
这是 PHP 的口径。

### `0` 与 `0:0`：一个走空值、一个走松散比较

| 输入 | 判据 | 结果 |
|---|---|---|
| 分红 `''` / `'0'` | `empty($strVal)` | 按日期删 `stockdividend` 那一行，**不重算** |
| 分红 `'0.0'` | 非空字符串 | **写 0**（与 `'0'` 不是一回事，§五） |
| 拆股 `0:0` / `0.0:0` / `00:0` | `$ar[0] == '0' && $ar[1] == '0'`，PHP 8 松散比较 | 删 `stocksplit` 那一行，**流水/校准/复权全不回滚** |
| 拆股 `10:0` | `floatval(0)` 当分母 | PHP 抛 `DivisionByZeroError` 整页 500；**本版写之前就拒绝** |
| 拆股 `0:10` | 比例算出来是 0 | PHP 先插 `stocksplit`、把校准值写成 0，再在"复权价 ÷ 比例"处抛；**本版拒绝** |

`php_text_equal`（双轨数字比）就是给 `0.0:0` 这一格用的；`_php_empty` 只管
`''` 与 `'0'`。两个助手别合并 —— 同一族里 `_write_fund` 用的又是第三套
`_is_zero_text()`（§五）。

### 先算后写：`_dividend_plan`

分红那三段副作用要的分母（原净值）在 `date_prev` 上，取不到就是
`floatval(false)` = 0：PHP 7 算出 `INF`、PHP 8 抛 `DivisionByZeroError`，
而那时**分红行已经插进去了**。本版把"要改哪些行、各改成多少"抽成只读的
`_dividend_plan()`，取不到分母就在写库之前整笔拒绝 —— 库里的状态要么全改、
要么一点没动。拆股侧同理：比例、昨收价（流水要按昨收成交）都在 `insert_daily`
之前取。

### 两处刻意偏离（都不写数据）

1. **QDII 分支不写净值**。PHP 的最后一行用的是**循环变量**：

   ```php
   foreach ($arQdii as $strQdii) { ... $strDatePrev = ...; $fNewNetValue = ...; }
   if ($strClosePrev)  $net_sql->WriteDaily($strStockId, $strDatePrev, strval($fNewNetValue));
   ```

   `$strDatePrev` / `$fNewNetValue` 来自最后一个命中的 QDII，却写进**本标的**的
   净值表。SPY / QQQ 这类 ETF 在 `netvaluehistory` 里常常没有那一天，
   `GetClose` 给 `false` → 会凭空插一条 `-分红值` 的**负净值**。本版只在本标的
   分支写净值，QDII 分支的 `plan["net"]` 恒为 `None`；
2. **分红值不是数字时拒绝**。PHP `floatval('abc')` 是 0，于是插一条"0 元分红"，
   接着拿原净值当分母算校准值（等于什么都没变，但库多一行）。本版拒绝。

真库受控实测（QQQ，快照备份 → 做完复原 → 逐行核对 `0 / 34` 差异）：
分红 `0.5` → 校准 `41.107056 → 41.136253`、净值 `704.47 → 703.97`、复权价
33 行（当天 `704.4` 不动）；拆股 `10:1` → 复权价 34 行（含当天，
`704.4 → 70.44`）、校准 `41.136253 → 411.36253`，同一天再提交变成
`7.044` / `4113.6253`（就是"再除一次"）。

## 八、演示库缺的三张表

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

## 九、PHP 逐例对照：19 个操作 + 14 例校准写入 + 19 例分红拆股

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

### 校准写入：14 例

`_scratch/_php_calibration_write_check.php` + `_php_calibration_write_compare.py`
把 `_updateStockOptionCalibration()` 的**整条分支**照着抄一遍（方法体用真身
`CalibrationSql::InsertDaily` / `UpdateDaily` / `ValSql::DeleteById`），两边在
三张临时表 `cw_probe_cal` / `cw_probe_last` / `cw_probe_nav` 上跑，逐例比
**校准表整表快照 + lastcalibration 快照 + `time` 列**：

```
[OK  ] QDII 新行换算    44325.176400018      [OK  ] 缺中间价            false
[OK  ] QDII 同值再写    44325.176400018      [设计] 缺基金净值          throw:DivisionByZeroError
[OK  ] QDII 覆盖已有行  43810.078478109      [OK  ] 周末闸门            43695.067264574
[OK  ] 空值双删         （空）                [OK  ] 其它符号            false
[OK  ] 填 0 双删        0                     [OK  ] 商品期货            false
[OK  ] 港股 QDII 换算   8275.6356877323       [OK  ] INDA 未迁           false
[OK  ] 境内指数换算      50                   [OK  ] 期货 VWAP 未迁       false
14 例全一致
```

四个**只有这么跑才会发现**的点：

1. **净值也得换成临时表**。演示库只有 179 个符号，没有 A 股个股、没有境内
   指数基金、也没有商品期货基金，而这三类恰好各走一条不同分支
   （`in_arrayChinaIndex` 的 `'1.0'`、`in_arrayChinaFuture` 直接 return、
   其余 unhandled）。真库凑不出来，又绝不能往真表塞种子 —— 所以净值也临时表，
   符号、净值、中间价全由用例自己给，**真库只被读**（读 `stock` 表翻 id）。
2. **`num` 列会暴露继承错层**。第一版探针类 `extends DailyTimeSql`，
   而 `num` 只在 `CalibrationSql::InsertDaily()` 里写 —— INSERT 漏掉 `num`，
   `NOT NULL` 列被 MySQL 悄悄填 **0**，快照里一眼看见。改成
   `extends CalibrationSql`、只在构造器里绕到
   `DailyTimeSql::__construct($table, 'stock')`。
3. **只在快照里比 `close` 是比不出"值没变不发 UPDATE"的**。覆盖成同一个值和
   一行都不写，落库结果一模一样。两边都把 `DebugGetTime()` / `now_time()`
   换成本例归零的计数器（`09:00:01` / `09:00:02`），"发了几条写语句"才进快照。
4. **pymysql 把 `TIME` 读成 `timedelta`**，`str()` 出来是 `9:00:01`（无前导零），
   PHP 那边是字符串 `09:00:01` —— 不对齐这一处，14 例会全部假报差异。
   对照脚本的 `_cell()` 里单独处理。

探针还逐句搬了 `php/sql.php` 的三个全局函数（`SqlGetSingleTableData` /
`SqlCountTableData` / `SqlDeleteTableData`）：`GetRecord()` / `DeleteData()` 走
的就是它们，而 `sql.php` include 了 email / httplink / ui，CLI 起不来。
**搬函数体，不改函数体**。

### 分红 / 拆股：19 例，这次用的是"真身"

上一组只能逐句照抄方法体，因为 `sqlstocksymbol.php` 一连串 `require` 拉不起来。
这一组换了个办法：**在 `SqlDieByQuery()` 里把 `CREATE TABLE` 拦掉**
（`Create()` 只在表缺失时才建，本地表都在），于是 `php/sql/sqlstocksymbol.php`
与 `sqlstockgroup.php` 可以**整份 require** —— `Sql` 类与
`$g_stock_sql->GetId()` / `GetClosePrev()` 全是真身，探针只搬
`_updateStockOptionDividend` / `Split` / `SplitTransactions` /
`_updateStockHistoryAdjCloseByDividend` / `BySplit` 五个函数体。

```
基线：stockdividend=0  stocksplit=0  calibrationhistory=164  netvaluehistory=302
      stockhistory=1480  stockgroupitem=5  stocktransaction=4
= dividend_write             一致（7 张表快照相同）
= dividend_write_spy         一致
= dividend_clear             一致
= dividend_zero              一致
= dividend_same_value        一致
~ dividend_bad_number        差异 ['dividend']  ← 按设计差异（PHP 插一条 0 分红）
= dividend_empty_date        一致
= split_10_1                 一致
= split_1_10                 一致
= split_3_2_truncates        一致（3 股 × 1.5 → 4 股）
= split_delete               一致
= split_zero_zero_tenths     一致（0.0:0 也算删除）
= split_no_colon             一致
~ split_zero_denominator     差异 []            ← PHP throw，两边都没落数据
~ split_zero_numerator       差异 ['split','cal'] ← PHP 先插行、把校准写成 0，再抛
= split_already_exists        一致（再除一次的那条）
= split_with_member           一致（含流水配平 + 预统计回填）
~ qdii_branch_with_nav        差异 ['nav']     ← PHP 用循环变量写本标的净值
~ qdii_branch_without_nav     差异 ['history'] ← PHP throw，PY 已写分红行
19 例：14 例完全一致，5 例按设计差异，0 例失败
```

七张表进快照：`stockdividend` / `stocksplit` / `calibrationhistory`（含 `num`）/
`netvaluehistory` / `stockhistory`（按 `history_id` 单取那一行）/ `stockgroupitem`
/ `stocktransaction`。`time` 列与成员预统计的 `filled` **刻意不入快照** ——
前者两边时钟不同，后者是"上次结算日期"，与本次写入无关。

三个只有对着真身跑才会踩到的点：

1. **mysqli 与 pymysql 给的 double 形状不同**：前者原样字符串 `"4.569300"`，
   后者 `4.5693`。第一版每例都假报差异，直到 PHP 侧加 `CellValue()`
   （日期原样、其余 `strval(floatval($v))`）把两边压成同一形状；
2. **`$g_stock_sql` 不是 `GetStockSql()` 返回值那么简单**，它要带
   `cal_sql` / `his_sql` / `net_sql` 三个属性，且 `net_sql` 是
   `new DailyCloseSql('netvaluehistory')` —— **PHP 里没有 `NetValueHistorySql`
   这个类**，净值表在 PHP 侧就是一个换了表名的 `DailyCloseSql`；
3. **五个 `TABLE_*` 常量定义在 `sql.php` 里**，探针要把
   `MIN_FLOAT_VAL` / `TABLE_STOCK_DIVIDEND` / `TABLE_STOCK_SPLIT` /
   `TABLE_STOCK_GROUP` / `TABLE_STOCK_GROUP_ITEM` 全 `define` 一遍。

脚本末尾会把基线灌回去，并**用同一套 select 重新数一遍**，`dirty` 非空就返回 2
——"跑完以为复原了、其实多了一行"是这类对照最容易留的尾巴。

## 十、单测

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

`web/tests/test_stock_option_edit.py`（86 个）用 `tests/sqlitedb.py` 的假连接
（`SqliteConnection` / `SqliteCursor` / `FakeSource`），覆盖：索引页 16 项、
未知选项 404、符号不在库不摆表单、未迁项 GET/POST、13 项的默认值、
日频写删、EMA 清空后回填、AH/HA 删除方向、ADR 比例、fund 三支解析、
非管理员 POST 403、失败回填提交值、三个 PHP 口径助手
（`_php_empty` / `_is_zero_text` / `_intval`）。

`StockOptionCalibrationTest`（15 个）单列校准这一项：读初值三级回落、
`对方净值` 占位、QDII 换算 `29500*6.7147/4.4324`、境内指数 `cny=1.0`、覆盖已有行、
同值不再写、空值双删、`0` 也算删、缺中间价 / 缺净值 / 商品期货 / 未分类符号 /
空日期各自拒绝。

分红与拆股各一个类（9 + 11）——**每一段副作用都单独断言**，因为"分红写进去了"
和"复权价减对了"是两件事：

* `StockOptionDividendTest`（假库：`SZ161130` 在 10-26 校准值 `41.111`、净值
  `4.3784`）：校准值 `41.111 × 4.3784 ÷ 4.2784`、净值 `4.3784 → 4.2784`、
  复权价 `4.70 → 4.60` / `4.769 → 4.669` 且**当天那行 `4.80` 不动**、QDII 分支
  改多只基金的校准值但本标的净值不动、空值与 `'0'` 只删不重算、同值不重算、
  非数字拒绝、缺净值整笔不写（`stockdividend` 一行都没写）、空日期拒绝；
* `StockOptionSplitTest`：`10:1` 复权价 `0.47` / `0.4769` / **当天 `0.48` 也除**、
  校准 `41.111 → 411.11`、QDII 侧 `÷ 比例`、`0:0` / `0.0:0` 删除且不回滚、
  无冒号 / `10:0` / `0:10` / 缺昨收各自拒绝且不落任何数据、同一天重复提交变成
  `0.047` / `4111.1`、成员流水两笔配平（`-100 @ 4.769` 与 `+1000 @ 0.4769`）
  + 预统计回填（`quantity=1000`、`record=3`）、`record = 0` 的成员跳过。

全量：auto **1606** 绿、web **1033** 绿、`compileall` 干净。

## 十一、踩过的坑（写进 pitfalls 的）

1. `WriteString` 一族在 `IntSql` 上，不在 `ValSql` 上；
2. `ValSql` 的构造器接受表名，`CalibrationSql` **不接受** —— 后者多传的实参
   被静默忽略（#132）；
3. EMA 的占位串 `EMA200/50` 含 `/`，照抄 PHP 的顺序会把 `EMA200` 写进
   `double` 列并删掉真 EMA（本版已改顺序，见 §四）；
4. `intval('1.5')` 是 **1** → `ADR/1.5` 的 `intval != 1` 不成立 → **不写仓位**。
   照抄。1.5 这种比例只能先存 2 再改，或者直接动库。
5. `CalibrationSql` 的子类探针**必须 `extends CalibrationSql`**，只绕构造器；
   继承成 `DailyTimeSql` 会让 INSERT 漏写 `num`（`NOT NULL` 被填 0）。
6. `KWEB` 的默认日期取"行情前一日"，**不**退到净值表/行情表最新一天 ——
   全局的"取最新一天"回落对它不适用。
7. 周末闸门在 `calibrationhistory` 上是**在的**（只有 `WriteDailyAverage` 那三行
   被注释掉）；而 PHP 的返回值是"要写的值"不是"写没写"，闸门拦下也返回数字。
8. **`GetFromDate()` 是 `date <= $strDate`**（`sqldailyclose.php:43`），
   `DailyCloseSql.get_from_date` 第一版写成 `>=`，症状是"取回了该日之后那一头"，
   单测里表现为 `4.7 != 4.6`。拆股/分红要重算的是**过去**，不是未来。
9. **pymysql 把 `DATE` 列读成 `datetime.date`**，直接与 `'YYYY-MM-DD'` 比较
   **永远不相等** —— 分红"当天那一行不减"的分支会失效（当天被多减一次）。
   凡是 `get_date_prev()` / `row["date"]` 的返回值都要过 `_date_text()`。
10. **mysqli 与 pymysql 给的 double 形状不同**（`"4.569300"` vs `4.5693`），
   逐例对照必须两边都走 `strval(floatval($v))`，否则每例都假报差异。
11. **PHP 里没有 `NetValueHistorySql`** —— 净值表就是一个
    `new DailyCloseSql('netvaluehistory')`。探针的 `$g_stock_sql` 要带
    `cal_sql` / `his_sql` / `net_sql` 三个属性，`GetId()` 才不空指针。
12. **`CREATE TABLE` 可以在 `SqlDieByQuery()` 里拦掉**：`Create()` 只在表缺失时
    才建，本地表都在 → `sqlstocksymbol.php` / `sqlstockgroup.php` 能整份 require，
    `Sql` 类与工厂函数就是真身。比逐句照抄方法体可靠得多（§九）。

## 十二、下一步

* 剩 3 项：`editstockamount` 等账号体系（另外 2 项已有专门入口）；
* `submit` 族还剩 3 个独立页：`submitdividend.php`（抓 SPDR 官方分红）、
  `submitnetvalue.php`（抓 Invesco / SPDR 官方净值）、`submithistory.php`
  （刷新 Yahoo 历史图表）；
* `deletesymbol.php`（删标的，要连带清 8 张表）最后做，风险最高。
