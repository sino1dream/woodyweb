# StockSymbol 迁移说明（php/stock/stocksymbol.php → python/auto/stocksymbol.py）

本文档记录 `php/stock/stocksymbol.php` 到 `python/auto/stocksymbol.py` 的迁移范围、
语义取舍和验证方式，对应 `python/WEB_MIGRATION_PLAN.md` 中「后续迁移优先级」第 1 步。

## 已迁移内容

`python/auto/stocksymbol.py` 是纯逻辑层：不访问数据库、不发网络请求、不渲染 HTML。

对应关系如下：

| PHP | Python |
| --- | --- |
| `define()` 常量（SINA_*、BJ/SH/SZ、YAHOO_INDEX_CHAR） | 模块级常量，名称不变 |
| `StrHasPrefix()` | `str_has_prefix()` |
| `GetSecondaryListingArray()` | `get_secondary_listing_array()` |
| 各组 `*GetSymbolArray()` / `in_array*()` | 同名 snake_case 函数 |
| `IsChinaStockDigit()`、`_isDigit*()` | `is_china_stock_digit()`、各 `_is_digit_*()` |
| `BuildChinaFundSymbol()`、`BuildChinaStockSymbol()`、`BuildHongkongStockSymbol()`、`BuildYahooNetValueSymbol()` | 同名 snake_case 函数 |
| `StockSymbol` 类 | `StockSymbol` 类 |

`StockSymbol` 已迁移的方法：`IsIndex`、`IsYahooNetValue`、`IsSymbolUS`、`IsSymbolH`、
`GetDigitA`、`IsSymbolA`、`IsShangHaiA`、`IsShenZhenA`、`IsBeiJingA`、
`IsShangHaiEtf/Lof/Star/B`、`IsShenZhenB/Etf/Lof/Gem`、`IsLofA`、`IsEtfA`、`IsFundA`、
`IsIndexA`、`IsStockB`、`IsEastMoneyForex`、`IsSinaForex`、`IsForex`、`IsSinaFund`、
`IsSinaGlobalIndex`、`IsSinaFutureCN`、`IsSinaFutureExceptGoldCN`、`IsSinaFutureUS`、
`IsSinaFuture`、`GetSinaFundSymbol`、`GetSinaIndexH`、`GetSinaIndexUS`、`GetSinaSymbol`、
`GetYahooSymbol`、`GetPrecision`、`GetDefaultPosition`、`IsTradable`、
`IsBeforeStockMarket`、`IsAfterStockMarket`、`IsStockMarketTrading`、`GetTimeZone`、
`SetTimeZone`、`GetDisplay`、`GetSymbol`。

Python 侧使用 snake_case 方法名，同时保留 camelCase 别名，使远端分支
`origin/devin/1780933111-migrate-php-core-to-python` 上 `python/core/stock_symbol.py`
的调用方式仍然可用。

## 命名与返回值约定

* PHP 返回 `false` 的“字符串类”方法，统一返回 `None`（例如 `is_symbol_a()`）。
* PHP 返回 `false` 的“布尔类”方法，统一返回 `False`（例如 `is_symbol_h()`）。
* `is_sina_forex()` 是例外：PHP 写的是 `strtoupper(StrHasPrefix(...))`，不匹配时得到
  空字符串而不是 `false`，因此这里也返回 `''`，在两种语言里同为假值。
* `is_stock_market_trading()` 接受 `datetime.datetime` 或 `datetime.date`。
  传入日期时按当日 00:00 处理（与 PHP 传入零点时刻一致）。
  传字符串会抛 `TypeError`，避免静默按错误时点判断交易状态。

## 集成改动

迁移后 `stocksymbol.py` 成为符号表的唯一来源：

* `stockdata.py`
  * `stock_get_symbol()` 改为复用 `stocksymbol.stock_get_symbol()`。
    迁移前这一版只做 `strip()` + 大写，漏了 PHP `StockGetSymbol()` 里的
    `BuildChinaFundSymbol()` / `BuildChinaStockSymbol()` 两步，因此输入 `162411`
    不会被补成 `SZ162411`。现在与 PHP 一致（该函数原先定义在 `stockdata.py`，
    现由 `stocksymbol` 提供并在此处再导出，调用方无需改动）。
  * `YAHOO_INDEX_CHAR` 改为从 `stocksymbol` 导入，不再本地重复定义。
* `fundcalc.py`
  * 原先重复维护的 QDII 符号数组（`QDII_QQQ_MATCH_SYMBOLS` 等）改为由
    `stocksymbol` 构造；`qdii_symbols()` 直接复用 `qdii_get_symbol_array()`。
    这些常量仍以 tuple 形式在本模块导出，保持原有 API 与身份比较可用。

## 验证

### 单元测试

```powershell
cd F:\woody\woodyweb\python
python -B -m unittest discover -s auto -p "test_*.py"
python -B -m unittest discover -s web\tests -p "test_*.py"
```

`python/auto/test_stocksymbol.py` 覆盖常量、全部符号表、成员判断、数字区间、
符号构造、各类市场分类、Sina/Yahoo 符号转换、精度、交易时段和时区，
共计 70 个用例。加入第 2 步（`stockref` / `stockprefetch`）的用例后，
自动层当前 222 个测试、Web 层 11 个测试全部通过。

### 与真实 PHP 逐项对照（推荐）

`python/auto/_php_port_compare.py` 会驱动 PHP 侧的 `_php_port_check.php`，
用同一组输入分别调用 PHP 类方法和 Python 端口，逐 key 比较返回值：

```powershell
cd F:\woody\woodyweb\python\auto
python -B _php_port_compare.py      # 需要 php 在 PATH 中
```

当前结果（PHP 8.0.10）：

```text
compared cases : 6280
php only       : 0
python only    : 0
mismatches     : 1 (1 known, 0 unexpected)
  KNOWN  IsChinaStockDigit( 16241)  php=' 16241' python=None
```

覆盖 61 个符号 × 42 个类方法、`IsBeforeStockMarket` / `IsAfterStockMarket` ×
26 个时间点、全部符号表、全部 `in_array*()` 成员判断和模块级构造函数。
通过后退出码为 0，出现未登记差异时退出码为 1，可作为回归门禁。

## 已知偏差

| 差异 | PHP 行为 | 本端口行为 | 说明 |
| --- | --- | --- | --- |
| `IsChinaStockDigit(' 16241')` | 前导空格计入 `strlen()` 得到 6，`is_numeric()` 通过，返回带空格的 `' 16241'` | 先 `strip()` 再校验，返回 `None` | 本端口不把带空白的符号交回调用方；所有真实调用方（`stock_get_symbol()`）都会先 `strip()`，实践中不会出现分歧 |
| `is_china_stock_digit` 数字判定 | `is_numeric()` 接受非 ASCII 数字以外的形式，且 `strlen()` 按字节计 | ASCII 数字正则 `[0-9]{6}` | 刻意避免 `str.isdigit()` 接受全角数字 `'１２３４５６'` 这类 PHP 会拒绝的输入 |

## 尚未迁移

以下内容不在本次范围内，属于后续步骤：

* `GetMyStockLink()` / `GetStockLink()`：依赖 `php/stocklink.php` 与
  `php/sql/sqlstocksymbol.php`，需要先迁移链接与数据库层。
  本模块已提供它们所需的 `get_symbol()` 和 `get_display()`。
* `SetTimeZone()` 的语义差异：Python 没有进程级默认时区，这里设置 `TZ`
  环境变量并调用 `time.tzset()`（Windows 上不提供 `tzset`，会抛 `RuntimeError`）。
  需要做时区换算的代码应使用 `get_time_zone()` 配合显式的 `zoneinfo` / `pytz` 时区。
  `pytz` 在 `python/auto/.env` 对应的依赖清单里并不存在，故未引入外部依赖。
* 后续优先级（见 `python/WEB_MIGRATION_PLAN.md`）：
  `stockref.php` / `stockprefetch.php`、`mysqlref.php` / `fundref.php` / `qdiiref.php`、
  `ui/table.php` 等渲染层、`layout.php` / `menu.php` / `stocklink.php`。

## 重要语义说明

`IsBeforeStockMarket()` / `IsAfterStockMarket()` 的 `else if` 链在 PHP 里是嵌在
`if (IsSinaGlobalIndex())` 分支**内部**的（该文件在这两处的缩进不规则，
容易误读），因此：

* 只有 `znb_CAC`、`znb_DAX`、`znb_NKY`、`znb_TPX`、`znb_SENSEX` 这五个全球指数
  会因时间点返回真；
* 其它 `znb_*` 指数（例如 `znb_UKX`）以及所有 A/H/美股符号都直接走到最后的
  `return false`，即只要不是周末就视为在交易时间内。

这一点已用 PHP 8.0.10 的实际执行结果逐项确认（见上方对照输出）。移植时按 PHP
的真实控制流实现，未按注释或缩进“修正”。
