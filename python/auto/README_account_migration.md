# 交易明细 / 资金汇总段落迁移记录

对应两个 PHP 段落 —— 它们消费的是**同一批分组数据**，所以合在同一轮迁完：

```php
php/ui/transactionparagraph.php:102    EchoTransactionParagraph($acct, $strGroupId, $ref, $bAll)
woody/res/php/_stockaccount.php:213    EchoMoneyParagraphs($arGroup, $uscny_ref, $hkcny_ref)
woody/res/php/_stockaccount.php:233    EchoMoneyParagraph($group, …)        // 单分组薄封装
```

调用现场：

```php
woody/res/php/_mystocktransaction.php:…   EchoTransactionParagraph($acct, $strGroupId, $ref, true)
woody/res/php/_stockaccount.php:206       EchoTransactionParagraph($this, $strGroupId, false, false)
woody/res/php/_mystockgroup.php:143       $acct->EchoMoneyParagraph($group, USCNY, HKCNY)
woody/res/php/_myportfolio.php:183        $acct->EchoMoneyParagraphs($portfolio->arStockGroup, …)
```

与 `README_portfolio_migration.md` 是**同一条线**：那一轮迁的是"按符号汇总"
的组合段落，这一轮迁的是"按笔流水"的交易明细与"按币种汇总"的资金段落。
三者共用 `stockgroup` / `stockgroupitem` / `stocktransaction` 三张表与
`StockGroup` 模型 —— 账号体系同样暂缓，演示数据同样写死。

## 一、结论

| 项 | 结果 |
|---|---|
| 新增代码 | `auto/core/stockgroup.py`、`web/services/transaction_service.py`、`web/services/money_service.py`、`web/routes/account.py`、`web/templates/account.html` |
| 改动代码 | `auto/ref/stocklink.py`（5 个链接助手）、`auto/sql/historytable.py`（`TableRow.html_indexes` + 负零）、`web/templates/_data_table.html`（`html_indexes` 分支）、路由注册 + 导航 |
| 真实 PHP 逐例对照 | **481 例 0 差异**（`_php_transaction_check.php` + `_php_transaction_compare.py`） |
| 对照脚本自检 | 注入旧 bug → 精确复现 **90 / 40 / 5 处** mismatch（操作格空格、`empty()` 闸门、PHP 松散比较） |
| 测试 | auto **988** OK（+45）、web **603** OK（+89）、compileall 双绿 |
| 页面 | `/account` 演示页（交易明细 + 资金汇总，可切分组），导航与首页有入口 |
| 抓到的真 bug | 2 个 —— 确认文字漏「确认」、负零不归零（见第四节，pitfalls 第 51 / 53 条） |

## 二、这一轮到底迁了什么

### 1. 模型：`auto/core/stockgroup.py`

`php/class/multi_currency.php`（34 行）+ `php/stockgroup.php`（146 行）。
三个容易迁错的点：

* **`fx_susdcnh` 归人民币桶** —— 分桶条件是
  `IsSymbolA() || IsSinaFutureCN() || GetSymbol() == 'fx_susdcnh'`。
  它是**对冲工具**不是持仓，走"其余 → 美元"那一支会让美元列多出一份
  "汇率本身"的市值；
* **三个 `fConvert*` 起步是 `null` 不是 `0.0`** —— PHP 构造函数只初始化
  `fCNY/fHKD/fUSD`，`var $fConvertCNY;` 未赋值。照抄成 `None` 是为了让
  "没折算过"与"折算出来是 0"在下游可区分（见 pitfalls 第 50 条）；
* **`Convert()` 的除数守卫是"有汇率才除"** —— 没汇率时直接给 `0.0`，
  不是跳过赋值（那会留着 null）、也不是除零。注意 `if ($strUSDCNY)`
  是 PHP 真假判断，`'0'` 是假值而 Python 的非空字符串都是真值。

PHP 的 `MyStockGroup::__construct()` **当场查** `StockGroupItemSql`，
本版把那段循环外提成构造参数 `records`（与 `core/stocktrans` 里
`group_item_id` 的取舍一致：模型不连库）。

### 2. 链接：`auto/ref/stocklink.py` 补五个助手

`get_on_click_link` / `get_delete_link` / `get_edit_link` /
`stock_get_all_transaction_link` / `get_my_stock_group_link`。

两个形态差异照抄：

* **删除确认的中文问号是全角 `？`、英文是半角 `?` 且 `Confirm delete`
  后有个尾空格** —— `GetDeleteLink()` 里两套文案，别顺手统一；
* **`GetMyStockGroupLink()` 不判当前页** —— PHP 的 `GetPageLink()` 在
  "目标就是当前页"时返回纯文本，那条判断依赖运行时页面名，服务层拿不到，
  于是按项目取舍直接给链接（与 `get_php_link` 的取舍 2 一致）。

### 3. `TableRow.html_indexes`：给"拼装型单元格"开口子

交易明细的**备注格**与**操作格**是拼好的 HTML，而且必须照拼：

* 备注格 = `清空链接 + 备注原文 + 校准链接` —— 原文夹在两个链接**中间**，
  且链接带 `onclick` 确认。现有的 `TableRow.hrefs` 只能表达"纯链接"，
  `first_cell_is_html` 只管首格，都不够；
* 操作格 = `修改链接 + ' ' + 删除链接`。只读且非管理员时两边都空，
  剩下**一个空格**而不是空串。

于是给 `TableRow` 加 `html_indexes`（需要透传 HTML 的格子下标列表），
Jinja 宏按下标开 `|safe`。**不整行开**：那样会把业务文本也一并关掉转义。

### 4. 交易明细：`web/services/transaction_service.py`

七列（日期 / 代码 / 数量 / 价格 / 费用 / 备注 / 操作），后两列走
`html_indexes`。三处 PHP 语义照抄：

| PHP 行为 | 处理 |
|---|---|
| `strpos($strRemark, STOCK_DISP_ORDER)` 判的是**拼过链接之后**的备注 | 照抄（两者语义等价，但照抄更省一次解释） |
| `$strNetValue != $strPrice` 是**松散比较** | 走 `_php_ne()`：`'1.234'` 与 `'1.2340'` 数值相等，Python 的字符串比较会误判 |
| QDII 取 `GetClosePrev`（上一交易日）、其余取 `GetClose` | 与 `calibration_service` 里"QDII 慢一天"是同一件事 |

引用对象有个**必须照抄的缓存**：`_echoAllTransactionTableData()` 按
`groupitem_id` 缓存引用对象。这不是单纯的性能优化 —— 引用对象构造时会
装填行情，重复构造会让同一列出现不同时刻的价格。

### 5. 资金汇总：`web/services/money_service.py`

六列，一行 = 一个分组的一种货币。人民币行恒有，美元行与港币行按
`empty()` 判定（见 pitfalls 第 50 条）。三处：

* **"补空格子"的两个分支在 PHP 8 下走不到** —— `GetNumberDisplay()` 走
  `StockGetPriceDisplay(..., 'black', 'red')`，只有 `black` 分支返回纯文本，
  灰（≈0）与红（负）都带 `<font>`，于是 `0.0` 产出
  `<font color=grey>0</font>`，**永不为空白板**。本版照抄分支结构；
* **测试列只对"全部"一行出现，且按会员邮箱分两种算法** ——
  `$fConvertProfit - 1679328.22`（作者自己的基准）与
  `$fConvertProfit / 2 - $fProfit`（合账的一半口径）。两个魔法数字照抄，
  **不要去猜它的含义**；
* **标题拆成文本 + 链接** —— PHP 是
  `GetMyStockGroupLink().$profit_col->GetDisplay()`，按项目"标题纯文本、
  装饰归模板"的取舍，服务层回两个键，模板拼。

### 6. 演示页：`/account`

演示库里那三张表都不存在、账号体系暂缓，于是路由里写死一份样例数据集：

* **两个分组**，币种**刻意分散**：分组 1 是人民币（A 股）+ 港币，
  分组 2 是人民币（QDII 的 `SZ` 前缀按 A 股记）+ 美元。于是"全部"那一行
  三个桶都非零、三行全出 —— 分组 1 只有港币行、分组 2 只有美元行；
* **交易明细**覆盖了备注格的五种形态：含「申购」+ QDII、含「申购」+
  非 QDII、含「申购」但净值等于成交价（**不出**校准链接）、不含「申购」、
  空备注；
* **分组名可以点**（`?groupid=` 切下面的交易明细）—— 这是 `group_link_of`
  注入口子的真实落点，不是摆设。其余链接（清空 / 校准 / 修改 / 删除）
  指向 PHP 页面，在这个演示环境里点不通，它们的作用是让**形态**与 PHP
  一致，逐例对照脚本比的就是这些串。

## 三、PHP 逐例对照

`_php_transaction_check.php` 从 PHP 源码里**抠函数体**跑（同
`_php_portfolio_check.php` 的做法）：链接助手族、`GetNumberDisplay`、
`in_arrayQdii*`、`\w*(?:Symbol|Match)Array`（批量 28 个）、
`StockTransaction` / `MyStockTransaction`、`MultiCurrency` /
`StockGroup` / `MyStockGroup`、`TableColumn*`，以及三个段落函数。
数据库与账号两档由同命名空间里的桩遮住。

七组用例：**481 例 0 差异**。

三条归一化（不是偏差，是同一件事的两种写法）：

1. **剥 `<font color=…>`** —— PHP 的颜色由 `GetNumberDisplay()` 包，
   Python 侧归 CSS；
2. **PHP 的 `false` 归一成 `''`** —— `GetSqlTransactionDate()` 找不到
   空格时 PHP 回 false、Python 回 `None`，而 `TableRow.add()` 再把它存成
   `''`（与 PHP echo false 一致）；
3. **价格格对照的是参数不是结果** —— 两侧都用桩固定成 `sprintf('%.*f')`，
   比的是段落传进去的 `disp / prev / precision`。

自检（注入旧 bug，验证 harness 有牙）：

| 注入的 bug | 复现的 mismatch 数 |
|---|---|
| 操作格"只读非管理员"给空串（应为 `' '`） | 90 |
| 资金表美元行用 `is not None`（应为 `empty()`） | 40 |
| 净值与价格用 Python 字符串比较（应为 PHP 松散比较） | 5 |

**注入时的坑**（与 `_php_portfolio_compare.py` 同一个，见 pitfalls 第 39 条）：
monkeypatch 要打在**绑定该名字的模块**上。`money_service` 是
`from core.stockgroup import php_truthy`，改 `core.stockgroup.php_truthy`
对构建用例**不生效**，得改 `money_service.php_truthy`。

## 四、抓到的两个真 bug

### 1. 清空链接的确认文字漏了「确认」

PHP 的问句是 `'确认清空'.STOCK_DISP_REMARK.'：'.$strRemark.'？'` ——
**确认**两个字属于问句、不属于动作名（动作名是 `清空`）。初版端口写成
`'清空备注：…'`，479 例里 130 余处当场抓出。

**更值得记的是**：单测里那条断言写的是 `confirm('清空备注：打新？')` ——
**照着错误实现抄的期望**，于是 39 个用例全绿。见 pitfalls 第 53 条。

### 2. 负零不归零

`number_display|-0.004`：PHP 给 `'0'`、Python 给 `'-0'`。根因是
`number_format` **先四舍五入再套格式**（-0.004 舍到 0.00 后已经是正零），
而 Python 的 `%f` 按符号位出字。修在 `historytable._trim_number_text`
—— **数字层**而不是本段落，因为那是全站共用的出口。见 pitfalls 第 51 条。

## 五、刻意不照抄的地方

| PHP | 本版 | 理由 |
|---|---|---|
| `EchoTransactionParagraph` 里三行取数（`GetStockTransaction` / `CountAllStockTransaction`） | 调用方注入 `records` | 🔴 数据库域；演示页用内存数据集 |
| `$acct->GetStart()` / `GetNum()` / `IsGroupReadOnly()` / `IsAdmin()` | 落成函数参数 | 🔴 membership 域，账号体系暂缓 |
| `SqlGetEmailById()` | `member_email_of` 注入；不注入 = 没有测试列（等价没登录） | 同上 |
| `$acct->GetGroupLink($strGroupId)` | `group_link_of` 注入；演示页指回本页 | 同上 |
| 翻页控件 `GetMenuLink()` | 复用 `stock_service.menu_link_paging`（同一个函数，股票列表页已迁） | 不重复实现 |
| 备注格 / 操作格整行 `echo` 拼好的 HTML | `TableRow.html_indexes` 按格子下标透传 | 只开这两格，业务文本的转义不关掉 |

## 六、已知缺口

1. **`_mystock.php` / `_mystockgroup.php` / `_mystocktransaction.php`
   三个整页未迁** —— 它们的外壳（登录、`$acct->Auth()`、`GetGroupSql()`、
   `StockEditTransactionForm` / `StockMergeTransactionForm`）都在账号体系里。
   本轮迁的是页面里的**两个段落**，页面外壳要等那条线。
2. **`MyStockTransaction.group_item_id` 恒为 `False`** —— PHP 构造函数
   当场查库，等数据库层接入后由调用方传入。
3. **交易明细的 `b_all=True` 分支（翻页控件当标题）没有演示页** ——
   `/account` 用的是 `_stockaccount.php:206` 那一支（`b_all=False`、
   标题是"交易记录"链接）。`b_all=True` 那一支由
   `test_transaction.py` 的 `build_transaction_table` 用例覆盖。
4. **资金汇总的测试列在演示页上看不到** —— 它只对"全部"行出现，
   且按会员邮箱分两种算法。演示页不注入 `member_email_of`
   （等价没登录），于是那一列一格都不出 —— 表头照渲染、行里少一格，
   是 PHP 的原样。
