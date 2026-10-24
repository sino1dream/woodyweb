# 持仓页迁移（`holdingscn.php`，CK4-F）

对应 PHP：`woody/res/holdingscn.php`（139 行；生产端
https://palmmicro.com/woody/res/holdingscn.php?symbol=SH513050）。
Python 入口：`/holdings/<symbol>`（路由 `web/routes/holdings.py`，
模板 `web/templates/holdings.html`）。

本 README 只管**页面**（七列测算表 + 整页装配）。持仓数据层与估值式
（`holdings` / `holdingsdate` 表、`HoldingsReference`）见
`README_holdings_migration.md`（CK4-A/B）；`/stock/<symbol>` 第 3 分支与
本页共用的 est 三段见 `README_stock_detail_migration.md`。

## 0. 这一页是干什么的

一句话：**把一只持仓型基金（持有一篮子成分股，而不是跟踪单一标的）的持仓
明细连同"据此测算的涨幅"摆在一张表里**，回答"每只成分股跌了多少、把基金
拖累了多少、剩下的仓位怎么变"。

页面标题（PHP `GetTitle()`）= 符号 + `HOLDINGS_DISPLAY`（`'基金持仓'`）。
`EchoAll()`（`holdingscn.php:69-118`）的六步，Python 侧
`HoldingsService.get_page()` 逐步对应：

| # | PHP | Python | 说明 |
| --- | --- | --- | --- |
| ① | `$acct->EchoStockGroup()` | 两道守卫合成"返回 `None`" | `HasData() \|\| IsAdmin()`，见 §6 |
| ② | `if ($ref->GetHoldingsDate())` | 同上 | 没有持仓日整页不渲染 |
| ③ | `EchoHoldingsEstParagraph($ref)` | `get_holdings_est_sections()` | 估值表 + 说明 + 净值记录，**与 `/stock` 共用** |
| ④ | `EchoReferenceParagraph(array_merge(array($ref), RefSort(...)))` | `_reference_table()` | 基金自己排第一，后面 A→H→US |
| ⑤ | 七列测算表（`_echoHoldingItem` 循环） | `get_table()` | 本页核心 |
| ⑥ | `EchoAdrhParagraph($arAdrhRef, ...)` | `_adr_table()` | 没有 ADR 配对时整段不出现 |

**没有迁的两块**（都属于账号体系，站点暂缓）：`EchoStockGroup()` 前面的
「分组名称」表（`EchoStockGroupParagraph()`）与 `GetMetaDescription()`
（模板没有 meta description 槽位，文本保留在 `PAGE_DESCRIPTION` 常量里）。

## 1. 七列测算表（`_echoHoldingItem`，`holdingscn.php:24-67`）

列定义（`holdingscn.php:87-93`）—— 前缀直接拼在列头文本前：

| 列 | PHP | 宽 | 颜色 | Python 取值 |
| --- | --- | --- | --- | --- |
| 代码 | `TableColumnSymbol()` | 80 | maroon | `row.href` + 符号文本 + `row.title`（股票名） |
| 旧比例(%) | `TableColumnPercentage('旧')` | 100 | — | `number_format($fRatio, 2)` |
| 旧价格 | `TableColumnPrice('旧')` | 70 | blue | `mysql_round(strval($fClose), 2)` |
| 此后涨幅 | `TableColumnChange('此后')` | 70 | — | `GetPercentageDisplay($fClose, $fPrice)` → `(文本, 颜色)` |
| 新比例(%) | `TableColumnPercentage('新')` | 100 | — | `number_format($fNewRatio, 2)` |
| 影响比例(%) | `TableColumnPercentage('影响')` | 100 | — | `number_format($fRatioChange, 4)` |
| 汇率调整 | `TableColumn('汇率调整', 100)` | 100 | — | `number_format($fAdjust, 4)` |

**「影响比例(%)」不是笔误** —— `TableColumnPercentage` 的文本本来就是
「比例(%)」，前缀「影响」拼在前面；生产端表头逐字如此。

逐行公式（`holdingscn.php:37-66`）:

```php
$fClose   = $arHistory[$strStockId];                 // 持仓日 adjclose（floatval 后的浮点）
$fPrice   = floatval($ref->GetPrice());
$fChange  = ($fClose > MIN_FLOAT_VAL) ? $fPrice / $fClose : 0.0;
$fChange /= $fAdjust;                                // 成分股自己的汇率调整
$fRatio   = floatval($arRatio[$strStockId]);
$fNewRatio    = $fRatio * $fChange / $fNetValueChange;
$fRatioChange = $fRatio * ($fChange - 1.0);
```

两个取数细节照抄：

* **比例与旧价的键类型不一致**：`$arRatio` 的键是 MySQL 取出的 **int**，
  `GetStockId()` 返回 **str**。PHP 的数字字符串数组键自动转 int 所以照样命中；
  Python 侧是 `ref.holdingsref.ratio_of` 与 `holdings_service._history_value`
  的 int/str 双向容错（见 `README_holdings_migration.md` 的"整列归零"教训）。
* **缺键不抛错**：`$arHistory[$id]` 缺键在 PHP 8 只是 Warning + `null`，
  随后 `$fClose > MIN_FLOAT_VAL` 为假 → 涨幅按 0、旧价格按 `mysql_round('', 2)`
  = `'0'` 出。Python 侧 `_history_value` 缺键返回 `0.0`，同效。

**此后涨幅**走 `GetPercentageDisplay($fClose, $fPrice)`：阈值归零
（`|涨幅 × 除数| < 50/10^精度` → `'0'`，**不带 %**）与"除数为假回落到昨收"
两条规则都在 `_percentage_text` 里（pitfalls #90 的延伸，见 #100）。
配色按**站点统一口径涨红跌绿**（PHP 原文是涨黑跌红）——
`percentage_cell()` 与参考数据表同一个函数。

## 2. 累计器与合计尾行

三个 `static` 累计器（`$fTotalOld` / `$fTotalNew` / `$fTotalChange`）跨行累加，
循环结束后 `_echoHoldingItem(false, ...)` 输出合计尾行。**尾行只有 6 格**
（缺「汇率调整」）—— PHP 原样就是 6 个元素的数组，生产端实测也只有 6 个
`<td>`；照抄不补（pitfalls #102）。

尾行三个与数据行不同的口径，一起照抄：

1. 第 4 格是 `number_format(($fNetValueChange - 1.0) * 100, 2).'%'` ——
   **净值涨幅**，不是各成分股涨幅的汇总；
2. 最后乘的汇率调整是**基金自己**的 `RefAdjustForex($ref, ...)`（数据行各调
   各的）—— 同一个表里两种口径；
3. 「新比例」≠「旧比例」+「影响」（后者没除净值涨幅）—— 自洽性检查会误报，
   单测 `test_total_row_is_not_old_plus_change` 钉住"故意不自洽"。

Python 侧 `get_table()` 每次建表清零三个累计器：等价语义的依据是"PHP 的
static 也是**一个请求只累积一张表**"（pitfalls #101 记了这句为什么重要）。

## 3. 汇率调整的两套口径

`RefAdjustForex($ref, $fAdjustHKD, $fAdjustUSD)`（`holdingsref.php:3`）：
A 股不调（`1.0`）、港股用 `GetAdjustHKD()`、其余（美股等）用 `GetAdjustUSD()`。
数据行除的是**成分股自己**的那个，尾行乘的是**基金自己**的那个。
演示库里 `fx_shkdcny` 没有价 → H 股那一列退 1.0（数据缺口，不是 bug）。

## 4. 行序：测算表不排序，参考数据表排序

* **测算表**按 `GetHoldingsRefArray()` 的**原始顺序**（持仓表查询顺序）出行；
* **参考数据表**（第 ④ 段）用 `RefSort()`：`IsSymbolA` / `IsSymbolH` 分三组，
  各组 `RefSortBySymbol()`（按符号去重后 `ksort`）后按 A→H→其它拼接，
  且**基金自己排在第一个**（`array_merge(array($ref), ...)`）。

两个顺序不是同一个列表，别把 `ref_sort_ahus()` 用到测算表上。

## 5. ADR 段与反查方向

循环里顺手收集：`if ($holding_ref->IsSymbolH())` 且
`SqlGetHadrPair($holding_ref->GetSymbol())` 非空 → `new AdrPairReference(...)`。

**方向是这张表最容易搞反的地方**：`adrpair` 表的 `id` = ADR 侧、
`stock_id` = H 侧。从 **H 符号**找 ADR 是**反查**
（`StockPairSql::GetSymbol()` → `PairSql::GetRecord` 按 `stock_id` 查行取 `id`），
对应 `ref.refobj._lookup_pair_symbol_reverse(source, symbol, "adrpair")`；
正查（`GetPairSymbol`）是 ADR→H 方向，给 `AdrPairReference.get_pair_ref()` 用。
SQL 形状：`WHERE f.stock_id = (SELECT id FROM stock WHERE symbol = %s)`。

**ADR 表不给 position_reader**：PHP 的 `if ($strPos = $pos_sql->ReadVal(...))`
在库里没有 `fundposition` 行时那一格本来就不出，而数据源的 `get_position()`
带默认仓位回落（0.95 / 1.0），会把空格子填出一个值来。生产端 SH513050 的
ADR 表五行都少这一格。**待办**：演示库补了 ADR 配对数据后复核这一列。

## 6. 页面守卫（两道合成一个 `None`）

| PHP | 条件 | Python |
| --- | --- | --- |
| `EchoStockGroup()` | `HasData() \|\| IsAdmin()` | `ref_has_data(holdings_ref)` |
| `GetHoldingsDate()` | 持仓日非空 | `_invoke(ref, "get_holdings_date", ...)` |

站点没有账号体系，所以只保留 `HasData` 那一半；判定对象用**持仓引用自己**
而不是另建通用引用（两者读同一份行情快照，`has_data` 相同，少一次全表查询）。
守卫不成立时 `get_page()` 返回 `None`，路由层渲染"取不到该基金的持仓数据"
提示并返回 **200**（与 `/stock/<symbol>` 同一条约定：旧链接里的符号可能只是
暂时没有行情，404 会让历史外链全部失效）。

## 7. 与 `/stock/<symbol>` 第 3 分支的共用

`_mystock.php:138-142` 的 holdings 分支与 `holdingscn.php` 内容重叠但不相同：
前者还有均线 / 历史价 / 净值价格比较 / 份额四段（`_mystock.php` 公共尾部）。
共用的只有 est 三段，抽在
`stock_detail_service.get_holdings_est_sections()`：

* `get_holdings_detail(..., links=...)`（`/stock` 分支用）；
* `HoldingsService.get_page(..., note_links=...)`（本页用）。

说明文字里的三个链接（仓位估算页 / 基金持仓页 / 穷举持仓比例页）由路由层用
`url_for` 注入：**PHP 形 URL（`/woody/res/xxx.php`）是迁移替身，点不动**，
所以两个路由都传 Flask 版（`routes/stock.py::_holdings_note_links` 与
`routes/holdings.py::_note_links` 是同一份字典）。「穷举持仓比例」本站点没有
路由，恒传 `None` → 退化成纯文字（少一个链接不能少一句文案）。

## 8. 已知取舍与偏差

| # | 内容 | 理由 |
| --- | --- | --- |
| 1 | `LayoutUseWide()` 未迁移 | 全站 `wide=False`（`php/layout.php` 未迁） |
| 2 | `EchoTableParagraphBegin` 的"总宽超屏宽则不渲染"未建模 | 七列合计 620px，与站点其它表的一致处理 |
| 3 | 页首地区文案「港股/美股」 | 沿用**仓库 PHP**；生产端跑的是更新过的代码（「香港/美国」），以仓库为准 |
| 4 | 此后涨幅配色涨红跌绿 | 站点统一口径；PHP 原文涨黑跌红 |
| 5 | `GetNetValueChange()` 为 0 时退回 0.0 | PHP 8 会 DivisionByZeroError fatal；页面还能出数 |
| 6 | ADR 表不传 position_reader | 见 §5，生产端五行都无仓位格 |
| 7 | meta description 未接模板 | 模板没有槽位，文本留在 `PAGE_DESCRIPTION` |
| 8 | 分组名称表 / 账号守卫未迁 | 账号体系暂缓 |
| 9 | H 股汇率调整退 1.0 | 演示库 `fx_shkdcny` 无价，数据缺口 |
| 10 | 渲染出的表格 id 与生产端不同 | 生产端是 `estimationtable` / `netvaluehistorytable` / `referencetable`，本仓库三个组件分别给 `estimation` / 无 id / 无 id —— **`/fund`、`/stock` 也是这个形状**，属全站既有，不在本页单独改（改了反与站内其它页不一致） |

## 9. 测试与 PHP 逐例对照

* **单测**：`web/tests/test_holdings.py`（42 个）—— 排序 / 列定义 /
  逐格口径（期望值用显式算术手写）/ 累计器 / 尾行 6 格 / 阈值归零 /
  三种 falsy / ADR 反查方向（SQL 形状与参数）/ 页面守卫 / links 注入；
  `auto/test_historytable.py::PhpRoundingTest`（19 个）钉舍入口径。
* **PHP 对照**：`auto/_scratch/_php_holdingscn_check.php`（现场切出
  `_echoHoldingItem` 及全部依赖，一个字不改）+
  `_php_holdingscn_compare.py`（同输入，逐格比）。**70 项全一致**
  （2 个用例 × 每行 7 格 + 尾行 + title）。跑法：

      D:/xampp/php/php.exe -n -d display_errors=stderr auto/_scratch/_php_holdingscn_check.php prod_like
      python -B auto/_scratch/_php_holdingscn_compare.py

  **一个用例一个 PHP 进程**：`static` 累计器跨调用存活（pitfalls #101）。
* **踩坑记录**：pitfalls #99（舍入分叉）、#100（三种 falsy）、#101
  （static 累计器）、#102（尾行 6 格）、#103（替身缺符号判定静默走错分支）。

### 9.1 关键结论就地版（pitfalls 在 .dsh/ 下，不进仓库）

1. **舍入分叉**：PHP `number_format` / `round` 按**最短十进制表示**
   half-away-from-zero（`2.675` → `2.68`、`0.125` → `0.13`、
   `-2.675` → `-2.68`），Python `"%.2f"` 按二进制精确值 half-even
   （`2.675` → `'2.67'`）。本表「旧价格」列入参正是 MySQL 三位小数串，
   `.xx5` 边界必踩。全站显示一律走
   `sql/historytable.php_number_format` / `mysql_round` /
   `php_round`（`Decimal(repr(x))` + `ROUND_HALF_UP`），
   **不要再用 `round()` 或 `%f` 做显示**。另注意两个函数对负零的处理
   **相反**：`number_format` 归正（`-0.004` → `'0.00'`），
   `mysql_round` 保留（`strval(round(-0.004, 2))` = `'-0'`）。
2. **三种 falsy**：PHP 里 falsy 的字符串只有 `''` 和 `'0'` ——
   **字符串 `'0.0'` 是真值**（不回落除数），而**浮点 `0.0` 是假**
   （`GetPercentage` 回落到昨收），缺键得到的 `null` 也是假（同样回落）。
   真实流里 history 的值恒为 `floatval` 后的浮点，所以"close = 0 →
   涨幅除数回落到昨收"成立；**harness 的输入必须复刻类型**（写 `0.0`
   不要写 `'0.0'`）。
3. **替身缺符号判定会静默走错分支**：`RefAdjustForex()` 只问
   `IsSymbolA()` / `IsSymbolH()`，鸭子替身两个都没实现就落 else 拿
   **USD** 调整 —— 数值只差一点点（实测 0.8%），不报错。替身要么继承
   真类，要么补全判定；单测要显式断言一次分流结果。

## 10. 基金详情页（`/fund/<group>/<symbol>`）也要判这一支

### 10.1 起因：PHP 的 qdiimix 页面用的是持仓引用

``woody/res/sz164701cn.php`` 与 ``sh513050cn.php`` 都只 ``require('php/_qdiimix.php')``
，而 ``_qdiimix.php::Create()`` 里::

    $this->ref = new HoldingsReference($strSymbol);

**qdiimix 这一族（QDII-LOF 混合 / QDII-ETF）用的是 ``HoldingsReference``**，
所以 ``EchoAll()`` 第一段是 ``EchoHoldingsEstParagraph($ref)``，不是 QDII 那支
``EchoFundEstParagraph($ref)``。生产端 ``sz164701cn.php``（2026-10-23 抓取）::

    <a href=".../fundpositioncn.php?symbol=SZ164701">仓位估算</a>值使用1.00。
    <a href=".../holdingscn.php?symbol=SZ164701">基金持仓</a>更新于2026-10-22。
    <a href=".../exhaustiveholdingscn.php?symbol=SZ164701">穷举持仓比例</a>

本仓库改这里之前，**``/fund/<group>/<symbol>`` 恒走 QDII 分支**（``_build_fundest``），
持仓型基金在基金详情页少了「基金持仓」链接与后面的净值记录段 —— 那两支只接进
了 ``/stock`` 与 ``/holdings``。

### 10.2 判据与装配

判据与 ``/stock`` 第 3 分支同一个：``holdings`` 表有没有明细行
（``SqlCountHoldings() > 0``），实现在
``routes/fund.py::_build_holdings_ref``。命中就用
``get_holdings_est_sections()`` 的三段（估值表 / 说明 / 净值记录），
**不再走** ``_build_fundest``。

两支的三个差别：

| | 持仓分支 | QDII 分支 |
| --- | --- | --- |
| 说明文字 | 「仓位估算值使用X。基金持仓更新于Y。」+ 持仓 ≤4 时补「穷举持仓比例」 | 「仓位估算值使用X。」+ 实时估值那句「关联程度按照100%估算。」 |
| 估值表后随段落 | 净值记录 ``EchoNetValueHistoryParagraph($ref, false, 0, 1)`` | 校准记录 ``EchoCalibrationHistoryParagraph($ref, 0, 1)`` |
| 仓位值 | 持仓引用自己的 ``GetPosition()`` | 同 |

### 10.3 坑：「仓位估算」链接**不带判据**

``_getFundPositionStr()``（``php/ui/fundestparagraph.php:129-135``）调
``GetFundPositionLink()`` 是**无条件**的；``php/stocklink.php:201-202`` 里那个
``$bQdii || $bQdiiHk || $bQdiiJp || $bQdiiEu || $bLofMix`` 的判据属于**页尾
``GetFundLinks()`` 那一族链接**，是另一处。

生产端 ``sh513050cn.php`` 上的「仓位估算」就是链接，而 ``SH513050`` 两个谓词
都为假（``in_array_qdii`` = ``in_array_lof_mix`` = False）。本仓库的
``_fund_position_href()`` 带那个判据，**只适用于页尾链接行** ——
``_build_holdings_est`` 里改成无条件 ``url_for``，与
``routes/stock.py::_holdings_note_links`` / ``routes/holdings.py::_note_links``
逐键相同（三处共用这句说明，给不一样的地址会让同一符号在不同页面点出不同结果）。

### 10.4 演示库的数据缺口

``SZ164701`` 在演示库里 **``holdings`` 0 行**（生产端有，更新于 2026-10-22），
所以本地它仍然走 QDII 分支 —— 这是数据缺口，不是代码没生效。
``SH513050`` 有生产端搬来的 36 行样本，用它验证：

    /fund/qdiietf/SH513050
    → <a href="/history/fundposition/SH513050">仓位估算</a>值使用1.00。
      <a href="/holdings/SH513050">基金持仓</a>更新于2026-10-13。
    → 后面跟净值记录段

要补 ``SZ164701`` 用 ``dbtools/seed_sz164701_holdings.py``（**默认 dry-run**，
``--apply`` 才写）。生产端真实值：持有 **GLD 一只、比例 100%**、持仓日
2026-10-22、GLD 当日收盘 369。演示库连 ``GLD`` 这个符号都没有，所以脚本会
**新建符号**（结构性改动，故不自动执行）。

## 11. 文件清单

| 文件 | 作用 |
| --- | --- |
| `web/services/holdings_service.py` | 七列测算表 + 整页装配（本 README 的主体） |
| `web/routes/holdings.py` | `/holdings/<symbol>` 路由、链接构造器 |
| `web/templates/holdings.html` | 模板（段落顺序照 PHP `EchoAll()`） |
| `web/services/stock_detail_service.py` | `get_holdings_est_sections()`（与 `/stock` 共用） |
| `web/routes/stock.py` | `_holdings_note_links()` + 持仓分支接线 |
| `web/routes/fund.py` | `_build_holdings_ref()` / `_build_holdings_est()`（§10 基金详情页分支） |
| `web/app.py` | `holdings_ref_builder` 注入口 |
| `web/templates/fund_detail.html` | 持仓分支的说明文字 + 净值记录段 |
| `web/tests/test_holdings.py` | 42 个单测 |
| `web/tests/test_fund_holdings.py` | 9 个单测（§10 的分流与链接判据） |
| `dbtools/seed_sz164701_holdings.py` | 演示库持仓种子（默认 dry-run） |
| `auto/test_historytable.py` | `PhpRoundingTest`（舍入口径） |
| `auto/_scratch/_php_holdingscn_check.php` | PHP 对照 harness |
| `auto/_scratch/_php_holdingscn_compare.py` | 逐格对照脚本 |
