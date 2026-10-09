# php/ui 渲染层迁移（第一轮）：列定义地基 + 参考数据表

按优先级列表第 4 步（`php/ui` 渲染层）的第一轮。这一轮**不是把 35 个文件搬完**，
而是先把渲染层里被最多文件共享的那一个东西搬过来 —— **列定义** ——
再拿一个段落验证它真的能闭环。

| PHP | 行数 | 本版 |
| --- | --- | --- |
| `php/ui/stockdisp.php` | 34 | `auto/uicolumn.py` 的 `STOCK_DISP_*` 常量 |
| `php/ui/table.php` | 174 | `uicolumn.TableColumn`、宽度辅助、`TABLE_COMMON_DISPLAY` |
| `php/ui/stocktable.php` | 328 | 34 个列预置工厂 + `COLUMN_PRESETS` |
| `php/ui/stockcomm.php` | 75 | `ref_has_data` / `ref_sort_by_numeric` / `ref_sort_by_symbol` |
| `php/ui/referenceparagraph.php` | 104 | `web/services/reference_service.py` |
| `php/ui/htmlelement.php` | 156 | **刻意不迁**（见取舍 1） |

产出：`auto/uicolumn.py`（+ 30 个用例）、`web/services/reference_service.py`
（+ 20 个用例）、基金详情页接线、CSS 泛化。

---

## 零、先说一个推翻了预设的事实

动手前我以为 `php/ui/*paragraph.php` 里有一堆 `XxxParagraph` 类要搬。实际验证：

```text
grep -c "^class" php/ui/*.php
  commentparagraph.php:1   pricepoolparagraph.php:3
  stocktable.php:30        table.php:4
```

**只有 4 个文件里有类**，其余 20 多个 `EchoXxxParagraph()` 全是**过程式函数**，
靠 `require_once` 串起来，没有继承、没有 `GetText()` 契约，全程 `echo` HTML。
`stocktable.php` 那 30 个类也**只是"默认值不同"的常量预置**，没有一个覆写行为。

这决定了策略：**不要照搬 PHP 的类结构**。真正值得搬的是"列定义"这一个数据，
而 20 多个段落应该按 PHP 已经在做的事 —— 取数 + 拼行 —— 重构成返回数据的服务。

## 一、这一轮在消灭什么重复

迁移前每个 service 各写一份列定义：

```python
# web/services/history_service.py
_COL_DATE = _column("日期", "", 100)
_COL_PRICE = _column("价格", "blue", 70)

# web/services/fund_list_service.py
又写一遍相近的字符串与宽度
```

PHP 侧的单一来源是 `stocktable.php` 那 30 个类。**本轮把它搬过来，让列定义只有一个出处。**

已经顺手去重的第一处：`POS_NETVALUE_DIFF`（净值涨跌超过 4% 才反算仓位的阈值）
原本在 `history_service.py` 里带着注释重复定义了一遍，现在从 `uicolumn` 取值。

## 二、三条刻意的设计取舍

### 取舍 1：不迁 `htmlelement.php`（156 行）

那一整份文件是 `GetHtmlElement()` / `GetFontElement()` / `GetLinkElement()`
这类"**直接产出 HTML 片段**"的函数。逐字翻译它等于把前面几步刚拆开的
"数据与渲染"再粘回去，与 `refobj` 那一步定下的纪律（引用对象只承载数据与
字段语义）正好相反。新架构里 HTML 由模板负责，颜色随单元格作为数据返回。

同样地，`stockcomm.php` 里真搬过来的只有三个**排序操作**（`RefSortByNumeric`
等）；`GetArbitrageQuantity()` 没搬，因为它服务于 `portfolioparagraph`（未迁移），
且内部调 `GetStockHedge()`（未迁移），搬过来只能是一个半残的实现。

### 取舍 2：颜色不再烧进文本

PHP 的 `TableColumn::__construct()` 第 31 行把颜色烧成 HTML：

```php
$this->strText = $strColor ? GetFontElement($strText, $strColor) : $strText;
```

本版 `text` 保持纯文本、`color` 单独承载。三个理由：

1. Jinja2 有自动转义，文本里夹裸标签会被转义成 `&lt;font&gt;`；
2. 同一个列定义**既能给 HTML 模板用，也能给 JSON API / CSV 导出用**
   —— PHP 那边想导出就得先正则剥标签；
3. `TableColumn` 设成 `frozen=True`，列定义是常量预置，不该被人改。

代价：`table.php` 里唯一的 true 行为差异 —— `GetDisplay()` 返回 `<b>文本</b>` ——
本版只返回文本，加粗由调用方决定。已记入已知偏差。

### 取舍 3：`RefSortByNumeric` 不去重

PHP 版本用 `asort($arNum)` 且以 symbol 做键，**同符号会被静默覆盖**：

```php
foreach ($arRef as $ref) { $ar[$strSymbol] = $ref; ... }   // 后者覆盖前者
```

本版改成稳定的 `list.sort()`，**保留重复项**。理由是
**排序函数悄悄丢行会让"少了两行"变成查不出来的 bug** —— 去重应该是调用方
显式做的事（`RefSortBySymbol` **本来就要去重**，那里照做了）。

## 三、参考数据表：`referenceparagraph.php` → `reference_service.py`

PHP 那段组装有点绕，值得记一笔：

```php
$ar = array_merge(array($ref->GetExternalLink()), GetStockReferenceArray($ref));
$strFirstHint = array_pop($ar);        // 把「名称」拎出来
$ar[] = $strDescription;               // 最后一格换成说明文字
EchoTableColumn($ar, false, $strFirstHint);   // 名称降级成首格的 title 提示
```

**同一个 ref 会产出两行**：主行 + 盘前/盘后行（`extended_ref`）。盘后行的名称
不是被丢掉，而是**降级成首格的 tooltip**。本版用 `TableRow.title` 承载，
为此给共用的 `historytable.TableRow` 加了一个纯增量的 `title` 字段。

另外 PHP 有个" pop 掉默认名称列、再 append 一个 270 宽的名称列"的动作 ——
因为参考数据表是页面的主要参照物，150 不够。本版直接构造，
不重复"先塞进去再扔掉"。

### 接线：`/fund/<symbol>` 详情页

三个对外口子都可以注入（默认实现会真抓新浪，测试必须顶掉）：

| 注入点 | 替代什么 |
| --- | --- |
| `reference_service_factory` | 整个表格服务 |
| `reference_refs_builder` | "按符号建引用对象"这一步 |

容错原则是**页面不该因为行情抓不到就废掉**：取不到行的时候**整段跳过**
（宁可少一块，也不摆一张全空的表格 —— 那会让人以为数据丢了）；
加载抛异常时同样降级，`/fund/SZ162411` 照常返回 200。

## 四、⚠️ 一个必须裁决的口径冲突（本轮最重要的发现）

`涨幅` 这一格的颜色，同一份 PHP 代码库里给出了**两个相反的答案**：

| 出处 | 规则 | 跌 | 涨 |
| --- | --- | --- | --- |
| `php/stock/stockref.php:147` `GetPercentageDisplay()` | `str_starts_with('-') ? 'red' : 'black'` | **红** | 黑 |
| `php/stock.php:91` `StockGetPriceDisplay()` 默认参数 | `($strGreater='red', $strLess='green')` | **绿** | 红 |

而 `python/web` 的分组列表页（`fund_list_service`）实现的是后者，即"涨红跌绿"。

**本轮的选择：照抄 PHP 原文（`ReferenceService` 用"跌红涨黑"）**，因为这是迁移
而不是 redesign —— 但我没有偷偷统一它们。`percentage_cell()` 的注释、
`test_conflicts_with_fund_list_convention` 这条用例、以及本节，把冲突钉在三处，
等你裁决。**如果决定全站统一成"涨红跌绿"，改动只有 `percentage_cell()` 一处 + 那条用例。**

## 五、CSS 的一处泛化

原先的颜色类是 **`.history-table .col-*`**，只在该页生效。本轮把它们放宽到
**`.data-table .col-*`** —— `history.html` 的表格同时带 `class="data-table history-table"`，
所以不回归，而新表格可以复用同一套。

顺带补了两个缺失的类：

* `.col-red`：`TableColumnProfit`（盈亏列）用的红色，原先**没有任何 CSS 规则**，
  等于这个列一直接用不了；
* `td.cell-gray` + 斜体：参考数据表的盘前/盘后说明，PHP 是 `<i><font color=gray>`。

## 六、本轮测试情况

| 套件 | 数量 | 环境 |
| --- | --- | --- |
| `auto` | **604**（原 574 + 30） | Python 3.7 与 3.13 均全绿 |
| `web` | **81**（原 61 + 20） | Python 3.7 |

新用例里这几条是专门防回归的：

* `test_registry_covers_every_php_class` —— `COLUMN_PRESETS` 必须覆盖全部 34 个
  PHP 类，否则"按 PHP 类名反查"就漏了；
* `test_group_name_is_a_prefix_concatenation` —— `TableColumnGroupName` 的文本是
  `'分组'.'名称'` = **"分组名称"**，看着别扭但必须照抄，否则列头与生产端对不上；
* `test_display_is_plain_text_not_html` —— 列定义里不许出现 HTML；
* `test_without_quote_data_row_is_four_blanks` —— 没有行情时填四个空串而不是省略，
  否则整行串位；
* `test_extended_row_moves_name_to_tooltip` —— `array_pop` 那段降级行为。

### 写完 30 个用例后抓到的一个真 bug

`ref_get_table_column_net_value()` 里我一开始直接写 `ref.get_display()`，
没有走模块内建的别名兼容辅助 `_call()`。结果是：**只认 snake_case 的 ref
能选中列，用 camelCase 别名调用的会退化成 `str(ref)`** —— 列头变成一串对象地址，
而且这种错误**只有在页面上才看得见**。已修：改走 `_call()`，
并且连 `GetDisplay` 都没有时兜底到符号而不是对象地址。
`test_camel_case_alias_objects_are_supported` 钉住它。

## 七、已知偏差

1. **涨跌幅配色冲突**（见第四节，待裁决）。
2. **`TableColumn.GetDisplay()` 不加粗**：PHP 返回 `<b>文本</b>`，本版返回纯文本，
   加粗由模板/CSS 决定。
3. **参考数据表的外链带 ``|safe``**：首格是 `GetExternalLink()` 产出的 `<a>` 标签
   （PHP 也是直接 echo），模板对 `loop.first` 关闭转义。**这是本轮唯一的 HTML 透传点**，
   如果以后要把这一块接到不可信数据上，需要改成结构化的 `{"text","href"}`。
4. **宽屏名称外链未迁**：PHP 的 `$bWide` 会把名称做成雪球链接
   （`GetXueqiuLink()`，`php/externallink.php:131`）。外链函数族属于第 5 步，
   未迁移；本版始终返回纯文本名称。
5. **`GetArbitrageQuantity()` 未迁**（服务 porfolio 域，`GetStockHedge()` 未迁移）。
6. **`RefEchoTableColumn()` 的 `title` 只实现了参考数据表那一条路径**
   （`$bWide` 时 PHP 传 `false`，不挂 tooltip）。

## 八、还剩下什么（`php/ui` 全景）

**7 个段落其实早就在 `python/web` 里了**（不是本轮功劳，本轮才发现需要说清楚）：

`stockhistory` / `fundhistory` / `netvaluehistory` / `netvalueclose` /
`calibrationhistory` / `shareshistory`（`history_service.py`）+ `fundlist`
（`fund_list_service.py`）。

剩下约 16 个待迁，按依赖从少到多：

| 档 | 文件 | 阻塞点 |
| --- | --- | --- |
| 🟢 省事 | `pricepoolparagraph`(136) `ahparagraph`(94) `fundestparagraph`(174) | `pricepool` 零外部依赖、已经在手写 HEREDOC 插值，本质就是 Jinja2；另两个缺 `FundPairReference` 的少量方法，**不需要全迁那 278 行** |
| 🟡 中等 | `tradingparagraph`(197) `stockparagraph`(45) | 需要实时五档、`StockSql`；有一处 `strlen()*11` 估算像素宽，**在 CSS 布局下没有意义，应删掉而不是照抄** |
| 🟠 偏难 | `smaparagraph`(225) `portfolioparagraph`(160) `netvaluehistory` 收尾 | `StockHistory`（SMA/BOLL/EMA，16KB，无 Python 对应）；`static $fCny` 函数级静态跨行累加，**照抄翻译一定出 bug** |
| 🔴 建议不同方式处理 | `stockgroup` / `fundpurchase` / `transaction` / `comment`(≈450) | 会员/持仓/交易域，`$acct` 与一堆 Sql 全未迁；渲染函数里混着写库链接。建议 Python 版直接用 Flask-Login 重写而非平移 |

**三个贯穿后面的坑**（都已写进本文件以免重犯）：

* **函数级 `static` 是迁移杀手**：`portfolioparagraph:81`、`stockgroupparagraph:6`、
  `fundlistparagraph:5`，翻译时必须显式化成参数或迭代器状态。
* **三处 `date_default_timezone_set()` 全局副作用**：`referenceparagraph:58`、
  `smaparagraph:182/218`；Python 侧应该用已有的 `auto/tzutil.py`。
* **`Admin` 分支把渲染和写库绑在一起**（`netvaluehistoryparagraph:57` 生成
  `submitholdings.php?fundposition=` 等）。Python 版若是只读站点，一律先砍掉，
  否则会牵出一整套 POST/CSRF/权限端点 —— 那是这一层最大的范围膨胀源。
