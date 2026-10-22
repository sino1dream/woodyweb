# 仓位估算页迁移（`fundpositioncn.php`）

对应 PHP：`woody/res/fundpositioncn.php`（生产端
https://palmmicro.com/woody/res/fundpositioncn.php?symbol=SZ161125）。
Python 入口：`/history/fundposition/<symbol>`、`/api/history/fundposition/<symbol>`。

## 0. 这一页是干什么的

一句话：**不动 Form 不碰 K 线，只看净值曲线的拐点**——在两个相邻拐点之间，
净值的累计涨跌除以标的（经汇率折算）的累计涨跌，就是这只基金"实际表现出来的
仓位"。

PHP 的页面自己在 meta 里写得很清楚（`GetMetaDescription()`）::

    仅用于美股QDII和部分混合QDII基金，寻找A股基金净值连续几天累计涨跌超过某个
    阈值例如4%的机会，测算基金的实际表现出来的持仓仓位。

为什么要"累计"而不是"逐日"：单日净值涨跌常常只有零点几个百分点，
反算出来的仓位信噪比太低。**这是本页与 `/history/netvaluehistory/<sym>`
的唯一差别** —— 两者共用同一个 `EchoNetValueItem()`，只是"上期"不同：

| 页面 | 上期取自 | 代码 |
| --- | --- | --- |
| 净值记录 `netvaluehistory` | 上一交易日 `GetDatePrev()` | `netvaluehistoryparagraph.php:128` |
| 仓位估算 `fundposition` | 上一个**净值拐点** `GetSwitchDates()` | `fundpositioncn.php:12,28` |

对混合型 QDII 而言，区间本身还是刚需：`_QdiiMixGetPosition()` 要拿
`$strDate / $strPrevDate` 去问 `GetProportionArray()` 要**这段区间**的持仓比例。

## 0.5 仓位公式（页面下方「算法说明」段就是这一段）

`php/stock.php:121` 的百分比工具::

    StockGetPercentage($fDivisor, $fDividend) = ($fDividend / $fDivisor - 1.0) * 100.0

`_QdiiGetStockPosition()`（`php/ui/netvaluehistoryparagraph.php:5-21`）用它反算仓位::

    $fPercent    = StockGetPercentage($fPrev, $fNetValue);                  // 净值涨跌%
    $fEstPercent = StockGetPercentage($fCnyPrev / $fEstProportion, $fCny);  // 标的涨跌%（含汇率）
    $fVal        = $fPercent / $fEstPercent;                                // ← 仓位

拆开写就是：

```text
净值涨跌% = (净值 ÷ 上期净值 − 1) × 100
标的涨跌% = (标的 ÷ 上期标的 × 汇率 ÷ 上期汇率 − 1) × 100
仓位      = 净值涨跌% ÷ 标的涨跌%
```

PHP 源码注释给的就是这个式子解出来的样子（`netvaluehistoryparagraph.php:4`）::

    (est * cny / estPrev * cnyPrev - 1) * position = (nv / nvPrev - 1)

**三个"不"**：

1. **分母是标的涨跌%，不是净值涨跌%**。写反了仓位会变成 1.05 这种大于 1 的数
   （QDII 仓位恒 ≤ 1），一眼能看出来 —— 但只在"算得出来"的时候才看得出，
   所以页面上把公式与一行代入示例一起摆出来，让它可被逐格验算。
2. **汇率不退到最近一条**。cny 取不到就是取不到，闸门直接放空。
3. **三个闸门缺一不可**：`|净值涨跌%| > $fInput`（默认 4%）、
   `|标的涨跌%| > MIN_FLOAT_VAL`、`仓位 > MIN_FLOAT_VAL`（负数仓位也不出）。

`est / estPrev` 这一路有两条走法：

* QDII（有 est 标的）：就是两个价格的比；
* 混合型（`_QdiiMixGetPosition`）：换成 **Σ(成分股涨跌 × 持仓比例 ÷ 100)**，
  由 `GetProportionArray($strDate, $strPrevDate)` 给区间涨跌 —— 这也是本页
  必须用"拐点区间"而不是"上一交易日"的第二个原因。

页面上的说明段由 `history_service._position_formula_note()` 给出：公式常量
（`POSITION_FORMULA` 等）与 `qdii_get_stock_position()` 的实现必须同步改，
单测 `FormulaNoteTest` 把常量本身钉住了。示例取**第一行算出仓位的行**；
一整页都没算出仓位时公式照给、只省掉示例表（默认 4% 下 SZ161125 就是这种）。

## 1. 迁移范围（这次做了什么）

| 层 | 文件 | 对应 PHP |
| --- | --- | --- |
| SQL | `python/auto/sql/historytable.py`：`DailyCloseSql.get_switch_dates()` | `DailyCloseSql::GetSwitchDates()`（`php/sql/sqldailyclose.php:215-264`） |
| 服务 | `python/web/services/history_service.py`：`_build_fundposition()`、`HISTORY_KINDS["fundposition"]`、`f_input` 透传 | `_echoFundPositionData()`（`fundpositioncn.php:8-41`） |
| 行 | 复用 `_row_netvaluehistory()`（新增 `f_input` 参数） | `EchoNetValueItem()`（`netvaluehistoryparagraph.php:62-116`） |
| 路由 | `python/web/routes/history.py`：`?input=`、本页关闭翻页 | `GetEditInput()` / `$acct->GetNum()`（`fundpositioncn.php:70,91`） |
| 模板 | `templates/history.html` 阈值表单；`templates/fund_detail.html` 入口链接 | `EchoEditInputForm()`、`GetFundLinks()`（`php/stocklink.php:201`） |
| CSS | `python/web/static/style.css` `.threshold-form` | —— |

**没有迁的部分**（页面本体之外，依赖 PHP 的账号体系 / 文件输出）：

1. **CSV 下载与走势图**：`PageCsvFile` + `DateImageFile`
   （`fundpositioncn.php:47-59`）。要先把「文件产物<｜hy_place▁holder▁no▁813｜>哪」这件事定下来再说；
   CSV 的内容其实已经在 `_build_fundposition` 的循环里能顺手产出，
   但 Python 侧没有等价的"页面级临时文件"机制。
2. **管理员写回仓位**：`_adjustAdminPositionDisplay()` →
   `submitholdings.php?...&fundposition=`（`netvaluehistoryparagraph.php:57`）。
   依赖登录态**与写权限**，账号体系整体暂缓。页面上"仓位估算值使用 X"那句
   说明文字（PHP 在 `fundestparagraph.php:135`）也一并不在本页。
3. **`$acct->GetNum()` 的行数来源**：PHP 从账号会话取，Python 走
   `parse_paging()` 的 `num`（默认 10，`num=0` 全部）。语义一致、来源不同。
4. **`in_arrayQdiiMix` 的那些符号**：它们能进 `_echoFundPositionParagraph`，
   但 `GetFundLinks()` 的判据里**不含** QdiiMix（只有 Qdii/Hk/Jp/Eu + LofMix），
   所以详情页那句说明的「仓位估算」四字不包 `<a>`；这与 PHP 一致，不是漏了
   （判据在 `routes/fund.py::_has_fund_position()`，说明行的
   `href` 由它决定 —— 两处共用一个判据，避免"判据说有、链接给空"的半状态）。

## 2. `GetSwitchDates()`：无阈值的 zigzag 拐点检测

一句话概括算法：**三阶段状态机，纯方向反转检测，不看幅度、没有平滑**。

```php
while ($record = mysqli_fetch_assoc($result)) {     // GetAll() = date DESC，从新到旧
    if ($bFirst)      { $arDate[] = $strDate; $bSecond = true; $bFirst = false; }  // ①首条入组
    else if ($bSecond){ $bUp = ($fOld > $fCur); $bSecond = false; }                // ②第二条定方向
    else if ($bUp)    { if ($fOld < $fCur) { $bUp = false; $arDate[] = $strOldDate; } }
    else              { if ($fOld > $fCur) { $bUp = true;  $arDate[] = $strOldDate; } }
    $fOld = $fCur;  $strOldDate = $strDate;
}
```

* **输入顺序是语义的一部分**：`GetAll()` 恒 `date DESC`，所以 `$fOld` 是
  **日期更新**那条，`$bUp = ($fOld > $fCur)` 的语义是"顺时间在涨"（见 pitfalls #93）。
* **记的是上一条 `$strOldDate`**，不是当前那条：反转是"读到更旧那条才发现"的，
  而折返点属于旧趋势的末端。于是 `$bUp` 支（涨势反转）记的必是**局部极小**，
  `!$bUp` 支记的必是**局部极大**，两者严格交替。
* **严格不等号**：相邻两天净值相等时不触发反转，平台段会让拐点落到平台上
  更旧那条（`09-23`/`09-24` 同为 3.1494 正是这种情形）。
* **首尾两段**：数组第一个成员是序列最新点（未必是极值），第一段是未闭合的；
  最老那段的尾巴根本不在数组里（配不了对）。
  所以 `拐点数 = 极点数 + 1`，可用波段数 = 长度 − 1。

**阈值不在这里**：4%（`POS_NETVALUE_DIFF`，`php/ui/stockdisp.php:32`）是在
`EchoNetValueItem()` → `_QdiiGetStockPosition()` 第一行才判的。zigzag 本身无阈值，
所以会产出很多碎段 —— 实测 SZ161125 的 24 条净值切出 10 个拐点 = 9 个波段，
**全部不到 4%**（最大 2.47%）；SZ161130 只有 `10-16 → 10-09` 那段的 −5.35% 过关。

## 3. 三个照抄 PHP、不照抄就会错的行为

1. **`$iTotal` 数的是候选行，不是"写出了仓位的行"**。
   `EchoNetValueItem()` 结尾无条件 `EchoMatchTableColumn()`，所以没过阈值的行
   **照样整行显示**，只是仓位格空着（pitfalls #92）。
2. **最后一个拐点没有更早的拐点可配对**，`isset()` 为假即 break，
   所以它虽然在数组里，页面上却没有那一行。
3. **`$iNum` 是行数不是 SQL 的 LIMIT**。PHP 先全取再截断；换成 SQL LIMIT 会
   变成"按原始记录数截断"，显示的候选行数不再受控。

## 4. 验证

### 4.1 单元测试

* `python/auto/test_historytable.py::SwitchDatesTest`（6 例）：
  真实净值序列对照 PHP 输出、极值的"低谷/高峰严格交替"、平台段、单条记录、
  空表、`floatval('')=0.0` 的忠实行为。
* `python/web/tests/test_fundposition.py`（22 例）：
  行选择 / 上期配对 / 列组成与净值记录页一致 / `num` 截断 / `num=0` /
  仓位公式（手算值 `0.43 / 0.50 / 0.22`）/ 阈值放宽收紧 / 加黄判定 /
  本页无翻页、其它页仍有 / 路由与 `?input=` / JSON 接口。

### 4.2 真实 PHP 逐例对照

```
cd python/auto/_scratch
D:/python/python.exe -B _fundposition_export.py     # 从演示库导出共同输入
D:/python/python.exe -B _fundposition_compare.py    # 跑 PHP 探针并逐例比对
```

`_php_fundposition_check.php` 把三段真身**现场切出来 eval**
（`ExtractMethod`，与 `_php_netvaluehistory_check.php` 同一套）：

* `GetSwitchDates()` ← `php/sql/sqldailyclose.php`；
* `_echoFundPositionData()` ← `woody/res/fundpositioncn.php`；
* `_QdiiGetStockPosition()` ← `php/ui/netvaluehistoryparagraph.php`。

只有三处机械适配（`mysqli_fetch_assoc` 不能吃数组，见 pitfalls #94），
算法本身一个字符不改。

**结果（演示库，默认阈值 4.0、`num=10`）：四只基金 0 差异。**

| 符号 | est | 净值 | 拐点 | 显示行 | 有仓位的行 |
| --- | --- | --- | --- | --- | --- |
| SZ161125 | ^GSPC | 24 | 10 | 9 | 无（最大波动 2.47%） |
| SZ161130 | ^NDX | 16 | 9 | 8 | 10-16 → `0.95`（加黄） |
| SH513100 | ^NDX | 16 | 9 | 8 | 10-16 → `1.00` |
| SZ164906 | KWEB | 15 | 9 | 8 | 无 |

拐点数组、每行的日期与上期、仓位格的值，两边逐个相等。

单元格的**文本**（净值几位数、涨幅的颜色与阈值归零）不在本轮对照范围内：
那部分与净值记录页共用 `EchoNetValueItem()`，已在 CK6 阶段对着生产端核过
（见 `README_history_migration.md` §2.5 / §7.6 / §7.7，pitfalls #88 / #90）。

### 4.3 本地页面实测

```
http://127.0.0.1:5000/history/fundposition/SZ161130
```

* 默认（4%）：8 行，只有 `2026-10-16` 那一行出仓位 `0.95`，且因与库里登记的
  仓位一致而整行加黄；
* `?input=1`：三行出仓位（10-19 / 10-16 / 10-09）；
* `?input=6`：全部留空，但 8 行照旧列全（对应第 3.1 条）；
* 无翻页区块；
* 入口在**详情页估值表上方那句说明**里：PHP 那句的「仓位估算」四字就是
  `GetFundPositionLink()` 的锚文本（`fundestparagraph.php:135-152`），
  本版照办 —— `fundest_service.fund_position_memo_parts()` 把这句话拆成
  `label`（可链的四字）+ `rest`，路由按 `_has_fund_position()` 的判据给
  `href`（QDII 四族 + 持仓型 LOF），模板只把这四个字包 `<a>`。
  33 只 QDII + 4 只持仓型 LOF 实测各有一个链接，无重复入口。

## 5. 已知偏差

1. **无 CSV / 走势图**（见 §1.1）。
2. **无管理员写回**（见 §1.2）。
3. **`start` 被忽略**：PHP 这一页没有翻页参数，`_echoFundPositionData()` 里也
   没有 `$iStart`。URL 上带 `start=` 不会报错，但也不生效。
4. **`floatval` 的空串行为照抄**：净值缺失的那天 PHP 会当成一个假深谷，
   我们同样。选择忠实而不是"修正"，是因为一旦跳过就会与 PHP 的拐点序列错位，
   而页面是按拐点下标配对的。
