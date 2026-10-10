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

第二轮（🟢 档，配色裁决之后）：

| PHP | 行数 | 本版 | 用例 |
| --- | --- | --- | --- |
| `php/ui/pricepoolparagraph.php` | 136 | `auto/pricepool.py` | 33 |
| `php/ui/ahparagraph.php` | 94 | `web/services/pair_service.py` | 31 |
| `php/ui/fundestparagraph.php` | 174 | `web/services/fundest_service.py` | 27 |

第一轮产出：`auto/uicolumn.py`（+ 30 个用例）、`web/services/reference_service.py`
（+ 20 个用例）、基金详情页接线、CSS 泛化。
第二轮另抽出 `web/templates/_data_table.html` 宏（见"第二轮"末）。

测试总数：第一轮后 `auto 604` / `web 81`；
第二轮后 **`auto 637` / `web 140`**（Python 3.7 与 3.13 均全绿）。

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

## 四、一个口径冲突，以及它的裁决结果（本轮最重要的发现）

`涨幅` 这一格的颜色，同一份 PHP 代码库里给出了**两个相反的答案**：

| 出处 | 规则 | 跌 | 涨 |
| --- | --- | --- | --- |
| `php/stock/stockref.php:147` `GetPercentageDisplay()` | `str_starts_with('-') ? 'red' : 'black'` | **红** | 黑 |
| `php/stock.php:91` `StockGetPriceDisplay()` 默认参数 | `($strGreater='red', $strLess='green')` | **绿** | 红 |

而 `python/web` 的分组列表页（`fund_list_service`）实现的是后者，即"涨红跌绿"。

第一轮的做法是**照抄 PHP 原文（跌红涨黑）**，并把冲突钉在三个地方等裁决。

### ✅ 裁决（2026-10-09）：全站统一成"涨红跌绿"

选多数派，理由有三条：

1. **跌红涨黑是孤例**。全代码库里只有 `GetPercentageDisplay()` 一处这么写；
   涨红跌绿是 `StockGetPriceDisplay()` 的默认参数，
   `GetRatioDisplay()`（基准 1.0）、`GetNumberDisplay()` 等一大片函数都共用它。
2. **改动面最小**。统一成涨红跌绿，要改的只有 `percentage_cell()` 一个函数；
   反过来统一成跌红涨黑，要动的是所有走 `StockGetPriceDisplay()` 的地方。
3. **与国内行情惯例一致**（涨红跌绿），用户不需要在两个页面间切换心智模型。

于是 `percentage_cell()` 的三分支**形状照抄 PHP、颜色改成涨红跌绿**：

| 情形 | PHP 原文 | 本实现 |
| --- | --- | --- |
| 带 `%` 且以 `-` 开头（跌） | `red` | **green** |
| 带 `%` 其它（涨或持平） | `black` | **red** |
| 不带 `%`（无昨收可比） | `gray` | `gray`（**唯一与 PHP 相同的一档**） |

第三档保持灰色很重要：那时 `GetPercentageText()` 返回的是空串，
灰色用来和"真的是 0.00%"区分开 —— 空串会被染色成红，就分不清了。

`test_matches_fund_list_convention` 把"同一个跌幅在两个页面必须同色"钉住，
防止有人把某一处单独改回去。

## 五、CSS 的一处泛化

原先的颜色类是 **`.history-table .col-*`**，只在该页生效。本轮把它们放宽到
**`.data-table .col-*`** —— `history.html` 的表格同时带 `class="data-table history-table"`，
所以不回归，而新表格可以复用同一套。

顺带补了两个缺失的类：

* `.col-red`：`TableColumnProfit`（盈亏列）用的红色，原先**没有任何 CSS 规则**，
  等于这个列一直接用不了；
* `td.cell-gray` + 斜体：参考数据表的盘前/盘后说明，PHP 是 `<i><font color=gray>`。

## 六、第一轮测试情况

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

## 六 bis、第二轮：🟢 档三个段落（pricepool / ah / fundest）

裁决完配色后接着做的"省事档"。三个段落各自的**产出与接线状态不一样**，
分开说清楚。

### `pricepoolparagraph`(136) → `auto/pricepool.py` + `test_pricepool.py`（33 用例）

零外部依赖，是整个 `php/ui` 里最接近模板的一个段落：数据部分只是两个
纯计数类（`PriceGoal` / `PricePool`），表现部分是固定五列的手写 `<tr>/<td>`。
放在 `auto` 而不是 `web/services`，因为它不依赖 Flask，将来导出 CSV 也要用。

顺带未迁 `PricePoolCsvFile extends PageCsvFile`：`PageCsvFile` 属于
`php/csvfile.php`（文件 I/O + 分页写盘，未迁移），它在这里唯一的作用就是
给子类挂一个 `$pool`。Python 侧调用方直接持有 `PricePool` 即可。

**⚠️ 抓到第二个 PHP bug：`$bTranspose` 只换标签不换数据**

`EchoPricePoolParagraph()` 的 `$bTranspose` **默认为 true**，而它做的事是把
「行标签数组」和「列标签数组」互换着传进渲染函数。渲染函数内部**行索引永远
对应 `h/u/l_goal`（溢价轴）、列索引永远对应 `higher/unchanged/lower`（涨跌轴）**
—— 这两条从不改变。于是默认那条路径下 9 格里只有对角线 3 格语义正确。

两个调用方的实际情况：

* `woody/res/php/_thanousparadox.php:23` 显式传 `false` —— **正确**；
* `woody/res/netvalueclosecn.php:21` 用默认值 —— **正在吃这个 bug**。

本版 `transpose=True` 改成**真的转置数据**（行列标签与数据一起换）；
另留 `legacy_labels=True` 复刻 PHP 行为，仅供逐格对照排查。
`test_legacy_labels_reproduce_the_php_bug` 用一组**刻意不对称**的样本把差异钉住
—— 对称样本会让两个实现撞出相同的数字，把 bug 盖住。

### `ahparagraph`(94) → `web/services/pair_service.py` + `test_pair.py`（31 用例）

一个 PHP 文件里有三个段落（A/B、A/H、ADR/H），结构完全一样、只有尾部列不同，
所以本版是"一张列定义表 + 一套行组装"，`kind` 决定用哪一套。
`adr` 比另外两种多一列仓位、用美元而不是人民币。

`MyPairReference`（`php/stock/fundpairref.php:3`）**尚未迁移**，所以
`GetPairRef()` / `EstFromPair()` / `GetPriceRatio()` 这几个方法在 Python 侧还不存在。
本模块通过按方法名取数的 `_invoke()` 容忍这一点：方法出现了格子自动填上，
没出现就整段不输出（与 PHP 里"配对标的缺失"的表现一致）。**本模块不需要为
`MyPairReference` 的迁移改代码。**

**⚠️ 抓到第三个 PHP bug：`ab` / `ah` 的行比表头多一格**

`_echoPairItem()` 不管哪种配对都会在最后追加"仓位"格，但只有 `adr` 的列定义
里有 `TableColumnPosition`。于是 `adr` 是 6 列 / 6 格（对齐），
`ab` `ah` 是 5 列 / 6 格 —— 多出来那格没有表头。
本版照抄（不静默砍掉那一格），用返回的 `cell_count` 暴露差异，
`RowLengthTest` 钉住。

### `fundestparagraph`(174) → `web/services/fundest_service.py` + `test_fundest.py`（27 用例）

`php/ui` 里**被引用最多**的一个段落（`_qdii*` / `_chinaindex` / `_chinafuture` /
`_mystock*` / `holdingscn` 都在用），是三档估值的主展示位。

表头由"**扫一遍所有 ref**"决定：只要有任意一个标的有参考/实时估值，
就给所有人加两列 —— 这是为了让列数统一。

**⚠️ 抓到第四个 PHP bug：实时估值那一档少一次补齐**

参考估值有 `else if ($bFair) { $ar[] = ''; $ar[] = ''; }`，实时估值**只有 `if`**。
所以"别人有实时估值、自己没有"时，那一行会**少两格**，后面的单元格整体左移。
本版照抄并暴露 `cell_count`，`RowLengthTest` 里放了"参考估值会补齐"的对照组。

**~~这一档刻意没有接线到生产页面~~ —— 已于 2026-10-10 推翻**，见下：

> 原文：三档估值必须由 `refobj.QdiiReference` 配 `fundestcalc` 的 estimator 填。
> 基金详情页现在只有一个 `PalmmicroFundCalculator.est_net_value()` 的结果，
> 把它硬塞进「官方EST」列就是**又造一条 EST 口径** —— 上一次两条口径并存的
> 后果是 SZ161130 的官方 EST 从 4.59 变成 20245 且页面不报错。
> 所以只留 `create_app(fundest_refs_builder=...)` 注入口，等 estimator 接线那一步。

后来实际接线了（见 `README_detail_page_sections.md`），原因是**重算了这笔账**：
当初担心的"两条口径并存"，成因是 `_qdii_value()` 写死取实时快照 —— 那是
`fundestcalc` 内部的 bug，已经连同估值守卫一起修掉。修完之后 `fundestcalc`
就是唯一实现，详情页直接调它不会分化出口径，反而少一处
"文档说支持、代码不支持"的落差。

接线方式保留两条：

* 显式注入 `create_app(fundest_refs_builder=...)` —— 测试与离线用；
* 不注入时走 `_build_qdii_refs()`，用 `refobj.QdiiReference` 现算三档。

**仍然没做的那一档**：实时 EST。它要 `GetRealtimeRef()` 的配对引用
（`MyPairReference` 未迁移），所以恒为 `None`；按 PHP 的行为这一行会少两格
（PHP 那边也只有 `if`、没有 `else` 补空）。

### 顺带抽出 `_data_table.html` 宏

参考数据表原先在 `fund_detail.html` 里内联二十行 Jinja。后面还有十几个段落
（trading / stock / sma / portfolio …）都是同一套"列定义 + 行数据"，
逐个复制会让每加一个段落都重复二十行。抽成宏后，
列头颜色 / 单元格颜色 / 首格外链这三条规则集中在一处。
`first_cell_is_html` **默认关闭**，只有确实需要透传 `<a>` 的表格才打开。

## 六 ter、第三轮：🟡 档两个段落（trading / stock）

接用户"先做 🟡 这一档"的指令。两个段落的依赖完全不同：一个是**全 `php/ui`
里唯一依赖实时五档**的，另一个是零依赖的两列表格；所以它们的可测性也
天差地别 —— trading 必须能独立跑通，stock 则顺手把 `StockSql` 接上了线。

### `tradingparagraph`(197) → `web/services/trading_service.py` + `test_trading.py`（39 用例）
+ `test_trading_route.py`（5 用例，需 flask）

行序与整行底色是这一段的全部骨架：涨停（橙）→ 卖5…卖1 → 买1…买5 → 跌停（橙），
其中**只有买一/卖一染黄**（`_getTradingColor(): ($i == 0) ? 'yellow' : false`）。
橙/黄是整行 `background-color`，所以走 `TableRow.background`（上一轮预置的字段），
与单元格文字色不是一回事。

三个入口按 PHP 原样保留为三个方法（列与溢价的装配各不相同）：

| 方法 | PHP | 列 |
| --- | --- | --- |
| `fund_table()` | `EchoFundTradingParagraph()` | 交易/价格/数量 + **嵌套**追加的官方/参考/实时溢价 |
| `pair_table()` | `EchoFundPairTradingParagraph()` | 无条件加一列"官方溢价" |
| `get_table()` | `EchoTradingParagraph()` | 用 `TableColumnStock`（对照标的名）当表头，格子里填溢价 |

#### 发现了两处 PHP 死代码/错位，都照抄 + 测试钉住

1. **`_checkTradingQuantity()` 里的 `if ($strQuantity != '0') return false;` 是不可达的**。
   外层 `if ($strQuantity = $ref->GetAskQuantity($i))` 已经把 `'0'` 判为假值滤掉了
   （PHP 的字符串 `'0'` 在布尔上下文里是 `false`）。这在 Python 里是**真陷阱**：
   `'0'` 在 Python 是**真值**，照字面翻译会把"没有挂单"误判成"有挂单"，
   于是多渲染一张空盘口表。本模块一律走 `_php_truthy()` 而不是 `if value:`，
   `PhpTruthinessTest` 把"Python 判真 / PHP 判假"这条差异写在断言里。
2. **回调列在价格缺失时少一格**：列头只看有没有传 callback，行里还要求
   `empty($strPrice) == false`。价格缺失（有量无量价）时该行比表头少一格。
   补空格等于掩盖，所以照抄，并把 `cell_count` / `column_count` 一起返回。

#### 删除了 `strlen()*11` 的像素宽度估算（用户明确同意）

PHP 在加回调列时按"一个字符 11 像素"估宽度、再用屏幕宽度截一刀。
在 CSS 布局下这条估算没有意义（列宽只是 hint），而且 `strlen()` 按**字节**算，
中文名一个字 3 字节必然偏窄；它还依赖未迁移的 `LayoutGetDisplayWidth()`
与 `$_SESSION['mobile']`，把"有没有 session"采进列定义，
会让同一个数据结构无法给 JSON API 征用。改用定宽 `TRADING_CALLBACK_WIDTH = 80`。

#### 接线：`/fund/<symbol>?live=1`

**默认不渲染**。盘口只在抓原始行情时才有 —— `realtimequote` 快照表只有
最新价/昨收/名称几个字段，没有买卖档位。而 `auto/refquote.py` 的取舍是
"抓取归 daemon，页面只读"，所以正常请求**不联网**，表格因此不出现。

要看到它必须显式要求一次：`?live=1` 或环境变量 `PALMMICRO_TRADING_LIVE_QUOTES=1`。
这与 `/api/quotes` 的 `?refresh=1` 同套路（见 `web/routes/quote.py`）。

**长期正解还没做**：把盘口写进快照表（daemon 每 60 秒一次），页面照样读库。
那要动 schema，属于单独一步。

实测 `?live=1` 时 `SZ161130` 的渲染结果（13 行 = 表头 + 12 行）：

```text
交易  价格    数量   官方溢价  参考溢价
涨停  5.372         18.19%    18.08%
卖5   4.889   7100   7.56%     7.47%
… 卖4 → 卖1 …
买1   4.880   4400   7.37%     7.27%
… 买2 → 买5 …
跌停  4.396         -3.28%    -3.37%
```

### `stockparagraph`(45) → `web/services/stock_service.py` + `test_stock.py`（29 用例）
+ `test_stock_route.py`（7 用例，需 flask）

真正的难点不在两列，而在**把 PHP 那句塞进标题的 `GetMenuLink()` HTML 拆成数据**：

```php
$strMenuLink = GetMenuLink(false, $sql->CountData(), $iStart, $iNum);
```

本版返回 `paging` dict（首页/上一页/下一页/末页/每页 N 条），由模板用 `url_for`
生成链接。翻页的 start 计算**复用** `auto/stocksql.stock_paging_columns()`
（上一轮就写好、一直没人接线的函数），不在服务里另写一份。

`GetMenuLink()` 最后一轮 `for` 的两个反直觉之处，测试各钉一条：

* 上界是 `总数 + 步长 - 1` 而不是总数 —— **总数 30 也给出"每页 100"**；
* 候选允许**超出总数**（总数 250 时给出 100/200/300）。

#### 顺带把 `auto/stocksql.py` 接上了线

上一轮写好的 `stocksql.py`（158 行 / 24 用例）一直是孤儿，这一档正好是它的
唯一调用点 —— `/stock` 列表页分页取 `stock` 表的符号与中文名。

#### 顺带补了 `externallink.get_xueqiu_url()` / `get_xueqiu_link()`

PHP 的 `GetXueqiuLink()`（`php/externallink.php:131`）。名称格需要它的**URL**，
所以拆成"URL 函数"+"`<a>` 函数"两个 —— 服务层只要地址，HTML 交给模板。
雪球不认 `^` 开头的指数写法，逐条换名（`^NDX → .NDX`、`^HSI → HKHSI`…），
换错的外链会 404 且**从表格上看不出来**，只能靠测试钉住。
这一条同时把"宽屏名称外链未迁"那条旧偏差（下面第 4 条）翻掉了。

### 新增 `/stock` 与 `/api/stock`

`web/routes/stock.py` + `templates/stock_list.html`。分页参数沿用 PHP 的
`start` / `num`，与 `/history/*` 同口径（`num=0` 表示全部）。

**链接构造器放在路由层**：生成 URL 需要请求上下文（`url_for`），而服务层
跑在没有请求的地方也要能用（脚本导出、测试）。所以工厂只管数据源，
链接由路由补 —— `_get_table()` 里那几行 `getattr` 就是为此。

### `TableRow.hrefs`：给非首格也开一个链接口子

上一轮只加了 `href`（首格）。但 `stockparagraph` 的名称格是雪球外链、
管理员列还要塞"编辑/删除"**两个**链接 —— 只有一个口子时，剩下的格子只能
由服务层自己拼 HTML，那与"数据与渲染分离"相悖。所以新增 `hrefs: dict`：

* 值是 **URL 字符串** → 把该格文本包成 `<a>`；
* 值是 **`[{"text", "url"}, ...]`** → 直接渲染成一串链接。

`_data_table.html` 现在按这个顺序取：首格 HTML → 非首格单链接 → 非首格多链接
→ 首格 `href` → 纯文本。

### 管理员那一列：加列、不加链接

`new TableColumn('', 270)` 的**列**照抄（空表头、宽 270），
但 `GetStockEditDeleteLink()` 属于 🔴 membership 域 —— 要登录 + 管理员角色，
本项目连账号体系都还没迁。所以做成 `admin_link_builder` 注入回调：
不注入就只有空表头，**现在先别编 URL**。

## 七、已知偏差

1. **涨跌幅配色与 PHP 原文相反**：本实现是"涨红跌绿"（已裁决，见第四节），
   `php/stock/stockref.php:147` 的原文是"跌红涨黑"。这是**故意**的偏差，
   不是抄错 —— 不要"顺手改回 PHP 原文"。
2. **`TableColumn.GetDisplay()` 不加粗**：PHP 返回 `<b>文本</b>`，本版返回纯文本，
   加粗由模板/CSS 决定。
3. **参考数据表的外链带 ``|safe``**：首格是 `GetExternalLink()` 产出的 `<a>` 标签
   （PHP 也是直接 echo），模板对 `loop.first` 关闭转义。**这是本轮唯一的 HTML 透传点**，
   如果以后要把这一块接到不可信数据上，需要改成结构化的 `{"text","href"}`。
4. **~~宽屏名称外链未迁~~（已过期，2026-10-10）**：第三轮补上了
   `externallink.get_xueqiu_url()`，股票列表页的名称格已经是雪球外链了。
   但**参考数据表**的名称格还没接上 —— 那条走的是 `$bWide` 分支，
   与 `stockparagraph` 不是同一个调用点，仍返回纯文本。
5. **`GetArbitrageQuantity()` 未迁**（服务 porfolio 域，`GetStockHedge()` 未迁移）。
6. **`RefEchoTableColumn()` 的 `title` 只实现了参考数据表那一条路径**
   （`$bWide` 时 PHP 传 `false`，不挂 tooltip）。
7. **~~估值表（`fundest`）默认不接线到生产页面~~（已过期，2026-10-10）**：
   现在不注入 `fundest_refs_builder` 也能渲染（走 `_build_qdii_refs`），
   推翻过程见"第二轮"那一节。**仍然没做的是实时 EST 那一档**：
   它依赖未迁移的 `MyPairReference`，恒为 `None`。
   真做这档之前要把这行记录也一并改掉 —— 它现在是唯一还在说"不接线"的地方。
8. **配对表（`ah`）的 ref 数据源未迁**：`MyPairReference` 缺失时行会退化成
   只有首格（宽屏时加行情五格），与 PHP 里"配对标的缺失"的表现一致，但**数据不全**。
9. **`pricepool` 的 `transpose` 是真转置**，与 PHP 同名参数（只换标签）不同；
   `legacy_labels=True` 可复刻 PHP。同理 `EchoTableParagraphBegin()` 那个
   "表太宽就 `trigger_error` 并返回 false" 的守卫没迁 ——
   `LayoutGetDisplayWidth()` 属于布局层（第 5 步）。
10. **五档表默认不渲染**：盘口只在抓实时行情时才有，库快照没有。
    `?live=1` / `PALMMICRO_TRADING_LIVE_QUOTES=1` 才会去抓。见"第三轮"那一节。
11. **五档表的"轮动交易"链接恒为 `None`**：`GetRotationTradingLink()` 指向
    `rotationtrading` 页面（`woody/res/rotationtradingcn.php`，🔴 membership 域），
    本站还没有该页面。等那一页迁完，在 `default_trading_service_factory()`
    补一个 `link_builder` 即可，服务本身不用改。
12. **股票列表的管理员列有表头无链接**：列照抄（`''` + 宽 270），
    操作链接等账号体系那一档。见"第三轮"末尾。
13. **`_checkTradingQuantity()` 的内层判定是死代码**，本版照样写、照样测，
    以免有人照字面翻译时把它当成有效分支。见"第三轮"。

## 八、还剩下什么（`php/ui` 全景）

**7 个段落其实早就在 `python/web` 里了**（不是本轮功劳，本轮才发现需要说清楚）：

`stockhistory` / `fundhistory` / `netvaluehistory` / `netvalueclose` /
`calibrationhistory` / `shareshistory`（`history_service.py`）+ `fundlist`
（`fund_list_service.py`）。

**第二轮已做掉 🟢 档三个**：`pricepoolparagraph`(136) / `ahparagraph`(94) /
`fundestparagraph`(174)，详见"第二轮"那一节。

**第三轮已做掉 🟡 档两个**：`tradingparagraph`(197) / `stockparagraph`(45)，
详见"第三轮"那一节。

剩下约 11 个待迁，按依赖从少到多：

| 档 | 文件 | 阻塞点 |
| --- | --- | --- |
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
