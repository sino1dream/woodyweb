# `portfolioparagraph` 迁移记录

对应 `php/ui/portfolioparagraph.php`（160 行），调用方两处：

```php
woody/res/php/_myportfolio.php:153   EchoPortfolioParagraph($arTrans);        // 整组合一页一张表
woody/res/php/_stockaccount.php:207  EchoPortfolioParagraph($group->GetStockTransactionArray());
```

这是 `php/ui` 里**唯一按"用户自己的交易"渲染**的段落：数据不是行情，而是
`stocktransaction` 表里那个人自己的买卖记录。

## 一、结论

| 项 | 结果 |
|---|---|
| 新增代码 | `auto/core/stocktrans.py`、`web/services/portfolio_service.py`、`web/routes/portfolio.py`、`web/templates/portfolio.html` |
| 改动代码 | `auto/ref/stocklink.py`（两个链接函数）、`auto/sql/historytable.py`（`format_int_display`）、路由注册 + 导航 |
| 真实 PHP 逐例对照 | **86 例 0 差异**（`_php_portfolio_check.php` + `_php_portfolio_compare.py`） |
| 对照脚本自检 | 注入旧 bug → 精确复现 **4 处** mismatch（`arb_str\|4/5/6/9`） |
| 测试 | auto **943** OK（+26 `test_stocktrans`）、web **493** OK（+62 `test_portfolio`）、compileall 双绿 |
| 页面 | `/portfolio` 演示页（内存交易集），导航与首页有入口 |
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
交易集**把每个分支走到：六个 LOF 套利符号中的三个、KWEB 的跨标的测试列、
`SH600104` / `TLT` 的偏离量、`hf_ES` 的合约乘数、六格行、清仓两格行、
`fx_susdcnh` 的美元折算行。页面顶部明确标注"演示数据"。

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

覆盖 8 组 86 例：`_getPortfolioTestVal`（22）、`_getArbitrageTestStr`（11）、
`GetArbitrageQuantity`（13）、`fx_susdcnh` 行的两个展示串（11）、
行序组合（7）、持仓模型算术（8）、`AddSqlTransaction`（7）、
`GetSqlTransactionDate`（6）。

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

1. **`_transEchoMergeParagraph()`（合并段落）未迁** —— 同一分组下多笔交易
   按符号合并的视图（`_myportfolio.php:50`）。优先级低，且同样要等
   `stockgroup` 表。
2. **`_stockaccount.php` 的第二个调用点未接线** —— 单个分组一张表，
   等账号/分组页面一起做。
3. **`MyStockTransaction.group_item_id` 恒为 false** —— PHP 构造函数当场
   查库，本版等数据库层接入后由调用方传入。
