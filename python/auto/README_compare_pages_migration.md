# AB / AH / ADR 对比四页迁移（`ahcomparecn.php` 一族 → `/abcompare` 等四路由）

> 迁移日期：2026-10-31
> 对应 PHP：`woody/res/ahcomparecn.php`、`abcomparecn.php`、`adrhcomparecn.php`、`ahhistorycn.php`
> 路由：`GET /abcompare`、`GET /ahcompare`、`GET /adrhcompare`、`GET /ahhistory/<symbol>`
> 对照：`auto/_scratch/_php_compare_check.php` + `_php_compare_compare.py`，**39 例全部一致**

## 一、这一批是什么

三个对比页都是 **4 行壳子**，第 2 行就是全部内容：

```php
<?php
require('php/_mystockgroup.php');
require('../../php/ui/_dispcn.php');
?>
```

`ahhistorycn.php` 换成了 `require('php/_ahhistory.php')`。

也就是说：这三个对比页和已经迁完的 **16 个行业分类页是同一个 `EchoAll()`** ——
`_mystockgroup.php:154` 的
`_echoStockGroupArray(GetCategoryArray($strPage), LayoutUseWide())`。
唯一差别是**符号数组从哪来**：

| 页面族 | `GetCategoryArray()` 的分支 | 数组来源 |
|---|---|---|
| 16 个行业分类页 | `biotech` / `qdii` / … | `php/stock/stocksymbol.php` 里的硬编码 + 派生 |
| 这 3 个对比页 | `abcompare` / `ahcompare` / `adrhcompare` | **三张配对表** `abpair` / `ahpair` / `adrpair` 的 `GetSymbolArray()` |

数组在库里而不在代码里，是这一批不能照抄 `category_page_service` 的原因。

## 二、符号数组：三张配对表 + `sort()`

`woody/res/php/_stocklink.php:8-21`：

```php
case 'abcompare':
    $pair_sql = GetAbPairSql();
    $ar = $pair_sql->GetSymbolArray();
    break;
```

`StockPairSql::GetSymbolArray($strPairSymbol = false)`（`php/sql/sqlstockpair.php:5`）只有三步：

1. 传了配对符号时先 `$sql->GetId($strPairSymbol)`，**查不到就直接 `return array()`**
   （不是一个大方异常）；
2. `GetIdArray()` 给的是**本体 `id`**，逐个 `$sql->GetStockSymbol()` 换成符号；
3. **`sort($arSymbol)` —— 字符串升序**。

第 3 步是页面行序的唯一来源：`GetIdArray()` 的顺序看主键顺序，不保证。
Python 侧把这两步补在 `sql/optionstore.py::StockPairStore` 上
（`get_id_array()` / `get_symbol_array()`），四张配对表共用。

**反查方向别搞反**：表的 `id` 是**本体**、`stock_id` 是**配对标的**。
`GetSymbolArray('QQQ')` 问的是"谁拿 QQQ 当配对标的"，返回的是那些**本体**。

## 三、分类逻辑（`_echoStockGroupArray()` 的 foreach）

`woody/res/php/_mystockgroup.php:9-60`，逐句对应：

```php
if ($ref->IsFundA()) {
    $fund = StockGetFundReference($strSymbol);
    if ($fund->GetOfficialNetValue())  { $arFund[] = $fund; $bIncluded = true; }
} else {
    list($ab_ref, $ah_ref, $adr_ref) = StockGetPairReferences($strSymbol);
    if (in_array_ref($ab_ref, $arAbRef) == false)  { $arAbRef[] = $ab_ref; $bIncluded = true; }
    ...
}
if ($bWide == false || $bIncluded == false)  $arRef[] = $ref;
```

三处容易写错：

1. **`in_array_ref()` 按 `GetSymbol()` 去重**（`_stock.php:196`），不是对象身份；
2. **`$bIncluded` 一真就是真**：第一个配对命中之后，后两个即使重复也不会把它翻回假；
3. **参考数据表收人的条件是"宽屏且已被配对"才排除** ——
   `LayoutUseWide()` 没迁（各服务 `wide` 默认 `False`），所以本地**恒收**，
   与 PHP 窄屏行为一致。

Python 在 `web/services/compare_page_service.py::classify_symbols()`。

## 四、段落与行

五段的顺序与 `_echoStockGroupArray():62-66` 同序：
参考数据 → 净值估算 → `EchoAbParagraph` → `EchoAhParagraph` → `EchoAdrhParagraph`。
行与列全部复用**已有的** `web/services/pair_service.py`（`/stock/<symbol>` 详情页
那三个段落就是它），本模块只做分类与装配。

`php/ui/ahparagraph.php` 里三处口径：

* `_echoPairParagraph()`：**`if ($iCount > 2)` 才按 `GetPriceRatio()` 排序** ——
  两行以内保持 `GetSymbolArray()` 的顺序（上面那句 `sort()`）；
* `_echoPairItem()` 的行是**变长**的：本符号链接 →（宽屏五格行情）→
  **有配对标的才有**配对链接 / 配对价 / 折算价 → **比价非 0 才有**比价格 →
  ADR 再多一列仓位（`GetPositionSql()->ReadVal($strStockId, true)`）；
* `EchoAhParagraph()`：**`count($arRef) == 1` 时额外挂 AH 历史页链接**
  （`GetAhHistoryLink($arRef[0]->GetSymbol())`）。所以 `/ahhistory/<symbol>`
  的入口只在这一个地方，且只在"只有一只 A/H 配对"时出现。

## 五、AH 历史页（`_ahhistory.php`，103 行）

`EchoAll()` 先 `new AhPairReference($ref->GetSymbol())`，
**`if ($h_ref = $ref->GetPairRef())`** 才画表 —— 没有配对 H 股就整页空。

`_echoAhHistoryItem()` 的闸门（`:6-26`）：

1. **`$cny_ref->GetClose($strDate)` 取不到 → 这一天整行都不出现**。
   所以这张表的行数通常**少于** `stockhistory`：港币中间价表缺哪天，哪天就消失；
2. **`$h_ref->GetClose($strDate)` 有值才追加** H 价 + A/H + H/A 三格，
   否则行只有三格（变长，同上）；
3. 比价是 `$ref->GetPriceRatio($strDate)`，H/A 直接 `1.0 / $fAh`；
4. **只有 A 股收盘价过了 `rtrim0`**（`:11` `$strClose = rtrim0($record['close'])`）。
   中间价与 H 价是 `GetClose()` 的原样 —— 顺手一起 trim 会让三列"看起来统一"，
   实际与 PHP 不一致。这一条有单测钉住（见 §九）。

表 id 是 `$strSymbol.'ahhistory'`（`:48`），Python 同样拼。

## 六、URL 与导航

| PHP | Python | 为什么 |
|---|---|---|
| `abcomparecn.php` | `GET /abcompare` | 生产端页面名去掉 `cn`；这三个是**工具页**（`EchoLinks()` 把它们放在工具页那一段），不是基金分类 |
| `ahcomparecn.php` | `GET /ahcompare` | 同上 |
| `adrhcomparecn.php` | `GET /adrhcompare` | 同上 |
| `ahhistorycn.php?symbol=X` | `GET /ahhistory/<symbol>` | 入口只有 `EchoAhParagraph()` 那一个链接，路径化即可 |

**没有塞进 `/category/<name>`**：那 16 个是基金分类页，这三个是工具页，
PHP 的 `EchoLinks($strPage)` 也在同一 switch 里把它们分开处理。

**页尾链接**由 `web/services/site_links.py` 承担：`UTILITY_LINKS` 里原本有
`("ahcompare.php", "", "A股和H股对比")` 这条**站外/生产端**链接，已删
（否则本地站点上是一个死链），改在 `local_links()` 里追加本地 `/ahcompare`。
三个对比页末尾还有一段"其它对比"（`_nav()`），跳过当前页 —— 与 PHP
`EchoLinks()` 跳过自己同口径。

## 七、对照结果

`_php_compare_check.php` 让 PHP 直接跑真 `StockPairSql::GetSymbolArray()`（连真库，
`CREATE TABLE` 在 `SqlDieByQuery()` 里拦掉，这是它能 require 真
`sqlstocksymbol.php` 的前提，见 pitfalls #145）。三层各一遍：

| 层次 | PHP 出处 | Python 出处 | 例数 |
|---|---|---|---|
| 页面数组 | `GetCategoryArray('abcompare'…)` | `compare_symbols(name, source)` | 3 |
| 全表数组 | `GetSymbolArray()` | `StockPairStore.get_symbol_array()` | 4 |
| 按配对侧反查 | `GetSymbolArray($strPairSymbol)` | 同名方法带参数 | 32（8 符号 × 4 表） |

**共 39 例：一致 39，失败 0。**

反查那 8 个符号特意覆盖了四种情况：`QQQ`（查到 13 只）、`^GSPC`（2 只）、
`SZ161125` / `SH513100` / `SH` / `SZ161130`（0 只，本体与配对侧都试）、
`NOSUCHSYMBOL`（查不到 id → 空）、`''`（PHP 里空串是假值 → **退回全表 76 只**，
Python `if pair_symbol:` 同行为）。

**为什么把 `fundpair` 也拉进来**：`abpair` / `ahpair` / `adrpair` 在演示库是
**0 行**，只拿它们对"空"这一个值没有说服力。`fundpair` 有 76 行、走的是**同一个**
`StockPairSql::GetSymbolArray()`，`sort()` 与"id 查不到符号"两条分支只有它验得到。

## 八、演示库现状：三张表 0 行，页面恒空态

```
abpair 0    ahpair 0    adrpair 0    fundpair 76
```

演示库里**没有 A 股**（`stock` 表 `SH600*` 0 只）也没有真 H 股
（`HK*` 只有外汇 `HKCNY`），所以：

* 三个对比页的符号数组恒空 → 三个配对段落整段不出现 → 页面显示空态；
* `/ahhistory/<symbol>` 恒返回"没有 AH 配对记录"。

这与 PHP 的 `if ($h_ref = $ref->GetPairRef())` 守卫一致，**不是 bug**，
是与 `chinaindex` 同类的 **P0 数据缺口**（那 27 只 A 股 ETF 连 `stock` 行都没有）。
真库 smoke 只验到"四页 200 + 空态文案正确"。

## 九、单测

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `auto/test_optionstore.py` | +4（共 29） | `get_symbol_array()` 的 `sort()`（故意让 id 序与字母序相反）、跳过无 `stock` 行的 id、空表、按配对符号反查 |
| `web/tests/test_compare_pages.py` | 20 | `compare_symbols` 分表 / 无连接返回空 / 未知名抛 `CompareNotFoundError`；`classify_symbols` 去重与 `wide` 分支；`build_compare_page` 的 AH 单行走历史链接；`build_ah_history` 四道闸门（含 **rtrim0 只作用于 A 价**）；路由三页标题 / 空态不 500 / 页尾跳过自己 |
| `web/tests/test_site_links.py` | +2（共 35 含上表） | AH 对比链接不再是站外、指向本地 `/ahcompare` |

跑法：`cd python/auto && python -B -m unittest discover -s . -p "test_*.py"`（1683 OK）；
`cd python/web && python -B -m unittest discover -s tests -t .`（1120 OK）。

## 十、刻意偏离

1. **不画走势图、不给 CSV 下载**。`_ahhistory.php` 用 `DateImageFile` +
   `PageCsvFile` 画 A/H 走势并生成 CSV，这两样属于渲染/文件层（与分类页那一批
   同样处理），本版只迁表格数据，页面末尾有文字说明。
2. **`$bAdmin` 的"更新历史价格"链接未迁** ——
   `GetUpdateStockHistoryLink()` 在生产端本来也没有调用点（submit 族那一批已查过）。
3. **`GetExternalLink()` 只留文字**。三个页的"类似软件"段在新浪 / 阿思达克 /
   高盛三个外链，本地不拼 HTML 外链，只保留文字说明（见 `COMPARE_PAGES[*]["notes"]`）。
4. **`LayoutUseWide()` 不迁** → `wide` 默认 `False`（窄屏口径）。
5. **`GetSymbolArray()` 里查不到符号的 `id` 直接跳过**。PHP 原样把
   `GetStockSymbol()` 返回的 `false` 塞进数组再 `sort()`；Python 里 `None` 进
   `sort()` 会炸，且下游 `StockGetPairReferences()` 也建不出引用，所以跳过。
   演示库不存在这种脏行，**本次 39 例对照撞不到这条分支**。

## 十一、踩到的坑

1. **`GetIdArray()` 不去重不排序** —— 顺序完全交给 `GetSymbolArray()` 的
   `sort()`。写 `get_id_array()` 时别"顺手"加 `ORDER BY`，那会掩盖排序分支；
   单测里故意让 `SH600030`（id 59）排在 `SH513100`（id 63）之前来钉住它。
2. **`layout` 里 `LayoutUseWide()` 决定的是"参考数据表要不要排除已配对的标的"**，
   不是"表格宽不宽"。本地 `wide=False` → 恒收，别当成漏了过滤。
3. **AH 历史的行是 6 格还是 3 格取决于 H 价**，不是取决于中间价 ——
   中间价取不到是**整天跳过**。
4. **`_services()` 从 `reference_service_factory` 借数据源**：对比页额外要读三张
   配对表，用同一个连接即可。测试注入假服务时 `source` 没有 `connection`，
   符号数组为空、页面走空态而**不是 500**。
5. 顺手修掉一个**与本次无关的周末红灯**：`test_stockhistory` 的
   `test_monthly_picks_month_ends` 在真机**周六**会红。根因是
   `is_month_end()` / `is_week_end()` 无"下一天"分支回退到**本机**日期，
   而 PHP `_isMonthEnd()` 用的是 `SetTimeZone()` **之后**的 `GetNowYMD()` ——
   本机周六时周末分支会跳到"下一交易日"（跨月），把 10-09 误判成月末。
   已改成显式传 `_market_ymd()`，并补 `test_month_end_uses_market_now_not_local_date`
   把它钉住。
