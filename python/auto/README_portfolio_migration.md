# `portfolioparagraph` 迁移记录

对应 `php/ui/portfolioparagraph.php`（160 行）**外加** `woody/res/php/_myportfolio.php`
里的合并段落（`_transEchoMergeParagraph` / `_echoMergeParagraph`，`:51-119`）——
两段消费的是同一批交易，合并在同一轮迁完。调用方：

```php
woody/res/php/_myportfolio.php:153   EchoPortfolioParagraph($arTrans);   // 整组合一页一张表
woody/res/php/_myportfolio.php:154   _transEchoMergeParagraph($arTrans); // 同一页还有一张合并表
woody/res/php/_stockaccount.php:207  EchoPortfolioParagraph($group->GetStockTransactionArray());
```

这是 `php/ui` 里**唯一按"用户自己的交易"渲染**的段落：数据不是行情，而是
`stocktransaction` 表里那个人自己的买卖记录。

## 一、结论

| 项 | 结果 |
|---|---|
| 新增代码 | `auto/core/stocktrans.py`、`web/services/portfolio_service.py`（含合并段落）、`web/routes/portfolio.py`、`web/templates/portfolio.html` |
| 改动代码 | `auto/ref/stocklink.py`（两个链接函数）、`auto/sql/historytable.py`（`format_int_display`）、路由注册 + 导航 |
| 真实 PHP 逐例对照 | **125 例 0 差异**（`_php_portfolio_check.php` + `_php_portfolio_compare.py`，含合并段落 39 例） |
| 对照脚本自检 | 注入旧 bug → 精确复现 **4 + 2 + 5 处** mismatch（`arb_str\|4/5/6/9`、吞并 `merge_dict\|7`、KWEB 口径 `merge_rows\|0/1/2/3/7`） |
| 测试 | auto **943** OK（+26 `test_stocktrans`）、web **514** OK（+83 `test_portfolio`）、compileall 双绿 |
| 页面 | `/portfolio` 演示页（内存交易集、两个分组），导航与首页有入口 |
| 抓到的真 bug | 1 个 —— `_getArbitrageTestStr()` 的耗尽路径（见第三节，pitfalls 第 47 条） |

## 二、这一轮到底迁了什么

### 1. 数据模型：`auto/core/stocktrans.py`

`php/stocktrans.php` 的两个类 + 两个函数。三个容易迁错的点：

* **成本含手续费** —— `AddSqlTransaction()` 的公式是
  `quantity * price + fees`，fees 加进成本**不加进数量**；
* **`GetValue()` 不带绝对值** —— 做空时 `iTotalShares` 为负，市值与盈亏
  跟着为负，`if ($iShares != 0)` 靠符号区分方向；
* **`GetAvgCost()` 在零持仓时回 0.0**，不是除零错误。

PHP 的 `MyStockTransaction` 构造函数会当场查 `StockGroupItemSql` 拿
`strStockGroupItemId` —— 属于数据库层，改成**调用方显式传入**
（账号体系暂缓，模型不连库）。

### 2. 链接：`auto/ref/stocklink.py` 补两个函数

* `get_transaction_link(group_id, symbol)` —— 首格跳交易明细页
  （`mystocktransaction`，groupid + symbol 查询；无 symbol 时用 `$` 前缀
  的 stock_id）；
* `get_overnight_cnh_link(f_val, cny_str)` —— `fx_susdcnh` 行跳隔夜人民币
  页面（`overnightcnh=<金额>`）。

### 3. 数字格式：`format_int_display()`

`number_format($x)` **单参数版**（0 位小数 + 千分位逗号）与文件里其它
`format_*` 都不同（它们显式关掉了分隔符），所以单独立函数。PHP 的
`number_format` 是四舍五入远离零，Python 内建 `round` 是银行家舍入，
必须显式做。负数逗号在负号之后。

### 4. 段落装配：`web/services/portfolio_service.py`

对应 `portfolioparagraph.php` 的全部四个函数 + 行序。列契约 7 列
（总宽 640），与既有 `ui.uicolumn` 对齐。三处 PHP  quirks 照抄：

| PHP 行为 | 处理 |
|---|---|
| `static $fCny` 跨调用持久 | 显式 `PortfolioCnyTotal`，一次页面建一个、多张表传同一个 |
| 行宽 2 / 6 / 7 三种（`iShares != 0` 整块跳过 + switch 无 default） | **不补空格** —— 补了就让"没持仓"和"持仓为 0"看起来一样 |
| `_getArbitrageTestStr()` 的 `while ($record = ...)` 语义 | 见下节，**照抄到 break 路径为止** |

### 5. 演示页：`/portfolio`

演示库里 `stockgroup` / `stockgroupitem` / `stocktransaction` 三张表都
不存在，账号登录（`$acct->Auth()`）用户要求暂缓。页面用**写死的样例
交易集**（两个分组，第二组与第一组有 `KWEB` / `SPY` 符号重叠）把每个
分支走到：六个 LOF 套利符号中的三个、KWEB 的跨标的测试列、
`SH600104` / `TLT` 的偏离量、`hf_ES` 的合约乘数、六格行、清仓两格行、
`fx_susdcnh` 的美元折算行，以及合并表（KWEB 三格行 + SPY 两格行）。
页面顶部明确标注"演示数据"。

### 6. 合并段落：`merge_transactions_by_symbol()` / `build_merge_table()`

`_myportfolio.php:85-119`。**同一个符号出现在多个分组里时**，把各分组的
合计量并成一笔（`new MyStockTransaction($trans->GetRef())` 后逐个
`Add()`），按符号组结束的顺序写进 `$arMerge`；渲染三列
（代码 / 数量 / 测试，总宽 290），只对 `KWEB`（`-200+700`）和 `XOP`
（`-00-00`）补第三格。三处**照抄**的不严谨：

| PHP 行为 | 本版处理 |
|---|---|
| 算法假定同符号交易在数组里**连续**（生产上由调用方排序保证）。万一同符号被拆到两端（`s, u, s`），第二个 `s` 会把 `u` 的合计量一并吞掉 | 逐字复刻，单测 + 对照用例各钉一条 |
| 收尾 `if ($cur_trans) $arMerge[$strSymbol] = $cur_trans;` 用的是**泄漏出循环的** `$strSymbol`（最后一个符号）—— 连续前提下碰巧正确 | 照抄；`last_symbol` 变量名 + 注释说明这是泄漏 |
| `Add()` 每并一笔交易对象只让 `total_records` **+1**（不是加对方的记录数） | 合并行"记录数" = 被合并的交易对象个数，docstring 写明 |
| ref 缺失时 `GetSymbol()` 回 `False`，PHP 数组键把 `False` **铸成 `0`** | 端口保留 `False` 原值作键（只用于 `switch` 匹配，渲染无差别）；对照时两侧归一成 `None` |

首格链接是 **mystock 页面**（`RefGetMyStockLink` → `$ref->GetMyStockLink()`），
不是个股表首格那个交易明细链接 —— 两个段落各走各的。

## 三、PHP 逐例对照（本轮的主要收获）

`python/auto/_scratch/_php_portfolio_check.php` + `_php_portfolio_compare.py`。

### 做法：抽函数体 + 命名空间打桩

与 `_php_futurepremium_check.php` 同款，不 require 整个
`portfolioparagraph.php`（那会拖进 `stocktable.php` / `table.php`）。
区别在桩：`_getArbitrageTestStr()` 要查 `stockgroupitem` 表，于是把抽出来的
函数体塞进 `namespace PortfolioCheck`，在**同一命名空间**里定义假的
`StockGroupItemSql` / `mysqli_fetch_assoc` / `GetStockHedge` /
`SqlGetStockSymbol` —— PHP 的非限定函数调用先在当前命名空间找、再回退
全局，所以桩能盖住，而 `number_format` / `intval` 这些没被遮盖的内建
仍然走真的。桩只替代取数，被测逻辑全是 PHP 原文逐字执行。

覆盖 9 组 125 例：`_getPortfolioTestVal`（22）、`_getArbitrageTestStr`（11）、
`GetArbitrageQuantity`（13）、`fx_susdcnh` 行的两个展示串（11）、
行序组合（7）、持仓模型算术（8）、`AddSqlTransaction`（7）、
`GetSqlTransactionDate`（6）、**合并段落（13 例 × 3 个对照键 = 39）**。

合并段落这组的打桩方式与其它组不同：`_transEchoMergeParagraph()` 末尾会调
`_echoMergeParagraph()`（真身上改名成 `_echoMergeParagraphReal`），桩负责把
`$arMerge` 截下来喂 JSON 后再转调真身 —— 合并字典与渲染出的行**两边都跑到**。
渲染三件套（`EchoTableParagraphBegin` / `RefEchoTableColumn` /
`EchoTableParagraphEnd`）打桩截获；`TableColumnGetQuantity()` 打桩成字面量
`'数量'`（PHP 的 `GetDisplay()` 会包 `<b>`，端口标题是纯文本，
README_ui_column_migration 已记的偏差）；`RefGetMyStockLink` 抽的是
**真身**（`if ($ref) return $ref->GetMyStockLink(); return '';`），
`FakeRef::GetMyStockLink()` 返回同形状的 `<a>` —— URL 形状本身由
`test_stocklink` 与 `_ref_my_stock_link` 的单测覆盖，不在 harness 重复。

### 抓到的真 bug：`while ($record = ...)` 的双向泄漏

初版把 `_getArbitrageTestStr()` 的耗尽路径实现成"取最后一行"，
还写了条测试把 `95` 这个错误值钉住。PHP 参考值一出，
`arb_str` 组 4 例不符（4/5/6/9），全部指向同一处：

```php
while ($record = mysqli_fetch_assoc($result)) { ... break; }
if ($record)   // break 出来才是触发行；自然耗尽时这里是 null
```

**耗尽时 while 的最后一次赋值把 null 留在 `$record` 里**，函数返回空串 ——
"组内没有正数量条目"时这一列整个不显示。修正后端口、docstring、
测试三处一起改（pitfalls 第 47 条）。注入旧 bug 重跑对照脚本，
4 处 mismatch 精确复现，确认 harness 有牙。

### 顺带对齐：`GetSqlTransactionDate()` 的 false 映射

PHP 的 `strstr($filled, ' ', true)` 找不到空格时返回 `false`。初版端口
返回 `''`，按 AGENTS.md"PHP 返回 false 的字符串类方法映射为 None"改成
`None`；同时区分"第一个空格就在开头"（PHP 返回**真空串**）—— 那是
"日期为空"，与"没有日期"是两回事。

### 合并段落：又一处循环变量泄漏 + 一个 PHP 数组键陷阱

`_transEchoMergeParagraph()` 的收尾句 `$arMerge[$strSymbol] = $cur_trans;`
用的是**泄漏出 `foreach` 的** `$strSymbol`（最后一个符号）—— 与
`_getArbitrageTestStr()` 的 `$record` 同款写法，这是本特性区的第二处。
这次端口一次写对，但对照仍然钉住三件事：

1. **吞并**（`merge_dict|7` / `merge_rows|7`）：非连续同符号时，第二个
   `KWEB` 把中间 `SPY` 的合计量一并吞掉 —— 注入"顺手修好"的版本，
   2 处 mismatch 精确复现；
2. **KWEB 口径**（`merge_rows|0/1/2/3/7`）：合并表是 `-200+700`，
   与个股表的 `-200`（且走跨标的套利测试）**不是一套** —— 注入错口径，
   5 处 mismatch；
3. **PHP 数组键把 `False` 铸成 `0`**（ref 缺失的退化输入）—— 端口保留
   `False`，对照两侧归一成 `None` 后一致。

### 一处文档漂移（行为没错）

`sort_trans_by_symbol()` 的 docstring 原先把来源归成 `_transSortBySymbol()`，
但 A/H/US 分块其实在**调用方** `_echoPortfolio()`（`_myportfolio.php:118-151`）
里，被调函数只做"按符号分组 + ksort + 拼平"。Python 侧一个函数实现两段
代码的合效果（分块 + 稳定排序 ≡ 分组拼平），行为没迁错，docstring 已改为
同时引用两处（pitfalls 第 48 条）。

## 四、刻意不照抄的地方

| PHP | 本版 | 理由 |
|---|---|---|
| 函数级 `static $fCny` | 显式 `PortfolioCnyTotal` 由页面持有并传递 | 模块级变量会把状态泄漏进进程，多线程互相踩；显式传入还能表达"两张表共享同一累加器" |
| `GetNumberDisplay` 只把负数染红 | 盈亏格统一涨红跌绿 | 全站配色约定（见 `reference_service.percentage_cell`） |
| `GetOvernightCnhLink()` 返回 `<a>` 片段 | 服务层返回 URL + 文本，模板拼 `<a>` | 服务层不产出 HTML 片段（列契约是 文本/颜色/宽度） |
| `fx_susdcnh` 的 `GetVal()` 为 0 | Python 侧除零保护回 1.0 | 生产上汇率永不为 0；PHP 那边只会警告 + 产出垃圾。该分支不进对照 |
| 数据库取数（`stockgroup` / `stocktransaction` / `StockGroupItemSql`） | 调用方注入（`items_of` / `symbol_of` / `hedge_of`） | 账号体系暂缓；演示页用内存数据集 |

## 五、已知缺口

1. **`_stockaccount.php:207` 的第二个调用点未接线** —— `EchoStockTransaction()`
   里 `EchoPortfolioParagraph($group->GetStockTransactionArray())`：单分组
   **原始顺序**的数组（不过 A/H/US 排序，排序是 `_myportfolio.php` 那个调用方
   额外做的事）。服务层已支持（`RawGroupArrayTest` 钉住"给什么顺序渲染什么
   顺序"；多张表共享同一个 `PortfolioCnyTotal` 由 `CnyTotalTest` 钉住），
   但页面外壳属于账号体系：`StockAccount`（登录 / `GetGroupSql`）、
   `StockEditTransactionForm` 都要等 `stockgroup` 三张表与账号体系。
   真实调用方（`_mystockgroup.php:143`、`_stockgroup.php:71`）都是
   **一页一组**，所以 `static $fCny` 的跨表累积在那一页只会涉及一张表。

   > **2026-10-13 更新**：这一段里"仍未迁"的三件（`EchoTransactionParagraph`
   > / `EchoMoneyParagraphs` / `MyStockGroup` 模型）已在下一轮补齐，见
   > `README_account_migration.md`；页面外壳仍按上面的理由挂着。
2. **`MyStockTransaction.group_item_id` 恒为 false** —— PHP 构造函数当场
   查库，本版等数据库层接入后由调用方传入。
3. **参考数据段落**（`_transEchoReferenceParagraph`，`_myportfolio.php:31`）
   未在 `/portfolio` 演示 —— 它已有 `/qdii` 与基金详情页复用，属另一条线。
