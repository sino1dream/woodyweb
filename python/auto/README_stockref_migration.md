# 行情引用与预取迁移说明（stockref.php / stockprefetch.php → python/auto）

本文档记录第 2 步迁移的范围、设计取舍与验证方式，对应
`python/WEB_MIGRATION_PLAN.md` 中「后续迁移优先级」第 2 步。

## 已迁移内容

| PHP 源文件 | Python 模块 | 说明 |
| --- | --- | --- |
| `php/stock/stockref.php` | `python/auto/ref/stockref.py` | `StockReference`、`ExtendedTradingReference` 与行情字段解析 |
| `php/stock/stockprefetch.php` | `python/auto/updater/stockprefetch.py` | 「要不要重新取行情」的调度判断 |
| `php/debug.php` 的格式化函数 | `python/auto/ref/stockref.py` | `rtrim0()`、`IsZeroString()`、`GetHM()`、日期时间格式化 |
| `php/stock.php` 的行情解析函数 | `python/auto/ref/stockref.py` | `explodeQuote()`、`RemoveDoubleQuotationMarks()`、`StockGetPercentage()` |
| `php/class/year_month_day.php` 的时间判定 | `python/auto/updater/stockprefetch.py` | `NeedFile()`、周几判断 |
| 新增 | `python/auto/core/tzutil.py` | 时区获取：优先 `zoneinfo`，旧解释器回退 |

### stockref.py

* `StockReference`：继承 `stocksymbol.StockSymbol`，提供 `get_price()`、
  `get_prev_price()`、`get_volume()`、十档买卖盘、涨跌幅、`get_hour_minute()`、
  `convert_tick()`、`is_extended_market()`、`check_adjust_factor_time()` 等。
* 各市场字段解析：`_on_sina_data_us()`、`_on_sina_data_hk()`、`_on_sina_data_cn()`、
  `_on_sina_global_index()`、`_on_sina_future()`、`_on_sina_future_cn()`；
  以及分派入口 `load_sina_data()`、`load_sina_future_data()`、
  `load_sina_fund_data()`、`load_sina_forex_data()`。
* `ExtendedTradingReference`：美股盘前/盘后引用，含 `get_market_session()`。

### stockprefetch.py

* `need_file()`、`stock_need_new_quotes()`、`future_need_new_file()`、
  `sina_fund_need_file()`、`is_future_market_trading()`、
  `_check_between_market_close()`。
* 调度：`prefetch_sina_stock_data()`、`symbols_to_fetch()`、`_prefetch()`。
* 缓存文本读写：`save_sina_data()`、`load_sina_data()`。

### tzutil.py

`get_time_zone()`、`localize()`、`uses_zoneinfo()`。详见下方「时区处理」。

## 设计取舍

行情抓取层要连的数据库和缓存文件都还没迁移，如果照抄 PHP 会在 Python 里到处
出现「读文件 + 查库」的耦合。所以这一层做成了**纯逻辑 + 回调注入**：

| 外部依赖 | PHP 里的来源 | Python 接口 |
| --- | --- | --- |
| 缓存文件修改时间 | `filemtime()` | `file_mtime` 参数，支持真实文件、内存字典或自定义回调 |
| 行情原始文本 | `PrefetchLoadSinaData()` | `quote_loader` 回调（`StockReference`） |
| 行情文本写入 | `PrefetchSaveSinaData()` | `save_quote` 回调 |
| 行情批量抓取 | `GetSinaQuotes()` | `fetch_quotes` 回调 |
| 净值是否已入库 | `SqlGetNetValueByDate()` | `net_value_exists` 回调 |
| 净值核对日期 | `GetStockHistorySql()->GetDateNow()` + `UseSameDayNetValue()` | `net_value_date` 回调 |

**未迁移的渲染部分**：PHP 的 `GetPercentageDisplay()`、`GetPriceDisplay()` 会返回
带颜色的 `<font>` 片段，属于页面层。本模块改为提供不含标记的
`get_percentage_text()` 与 `format_price()`，等 `php/ui/*` 迁移时再包标签。

**未迁移的链接部分**：`DebugLink()` / `DebugConfigLink()` 在 PHP 里生成调试用的
URL，这里返回文件名；`strExternalLink` 由 `GetSina*Link()` 生成，属于页面层，
本模块初始化时留空。

## 时区处理（重要）

PHP 在预取前调用 `$sym->SetTimeZone()` 改进程默认时区，之后 `GetNowYMD()` 取的是
**该市场的当地时间**。Python 没有进程级默认时区，所以：

* `stockprefetch` 要求传入时区感知的 `datetime`；只给朴素时间时会按符号的
  `get_time_zone()` 补上，保持与 PHP 相同的语义；
* 时区对象统一由 `tzutil` 提供。有 `zoneinfo`（Python 3.9+ 标准库）时用真实时区库；
  **没有时回退到 `_LocalOffsetTimeZone`**，它按日期自行切换夏令时
  （美东用「3 月第二个周日 ~ 11 月第一个周日」，欧洲用「3 月最后周日 ~ 10 月最后周日」）。

项目当前的运行环境是 Python 3.7（Flask 依赖装在那里），而 `zoneinfo` 是 3.9 才进
标准库的，所以这个回退不是可选项——否则美东冬令时会算错一小时。回退实现里不能用
`datetime.timezone(timedelta(...))` 那种纯固定偏移对象：`astimezone()` 会完全采信
目标时区给出的 `utcoffset()`，恒定偏移会把上海 21:00 换算成美东 09:00（正确是 08:00）。

**验证方式**：`_php_prefetch_compare.py` 在 Python 3.7 和 3.12 上分别运行，各
26800 个用例结果完全一致，说明回退实现与真实时区库在这些日期上等价。

## 集成改动

* `stocksymbol.py`：不再被 `stockref` 之外的地方重复实现；本次未改动其逻辑，
  只按新约定把注释改为中文。
* `stockdata.py`：第 1 步已把 `stock_get_symbol()` 接到 `stocksymbol`，本步未再改动。
* 新增 `tzutil.py`，供 `stockref.py` 与 `stockprefetch.py` 共用。

## 验证

### 单元测试

```powershell
cd F:\woody\woodyweb\python
python -B -m unittest discover -s auto -p "test_*.py"        # 222 个
python -B -m unittest discover -s web\tests -p "test_*.py"   # 11 个
```

新增用例：`test_stockref.py`（46 个）、`test_stockprefetch.py`（42 个）。

### 与真实 PHP 逐项对照

```powershell
cd F:\woody\woodyweb\python\auto
python -B _php_numfmt_compare.py      # 数字/时间格式化，87 个用例
python -B _php_prefetch_compare.py    # 预取判定，26800 个用例
python -B _php_rtrim0_fuzz.py 2000    # rtrim0 随机模糊，2000 个值
```

当前结果（PHP 8.0.10，Python 3.7 与 3.12 结果相同）：

| 工具 | 用例数 | 非预期差异 |
| --- | --- | --- |
| `_php_numfmt_compare.py` | 87 | 0 |
| `_php_prefetch_compare.py` | 26800 | 0 |
| `_php_rtrim0_fuzz.py` | 2016 | 0 |

`_php_prefetch_compare.py` 覆盖 `NeedFile()`、`_checkBetweenMarketClose()`（三组市场
参数 × 四种时间差）、`_isFutureMarketTrading()`、以及 6 个 A 股/港股符号 ×
400 个时间点 × 8 种文件年龄的 `StockNeedNewQuotes()` 完整分支。

## 已知偏差

| 差异 | PHP 行为 | 本端口行为 | 说明 |
| --- | --- | --- | --- |
| `sprintf('%.2f', -0.0)` | 规范化成 `'0.00'` | Python `'%.2f'` 得到 `'-0.00'` | `format_price()` 里显式把负零归零对齐 PHP |
| `IsChinaStockDigit(' 16241')` | 前导空格计入 `strlen()`，返回带空格的 `' 16241'` | 先 `strip()` 再判断，返回 `None` | 第 1 步已登记；`stock_get_symbol()` 会先 `strip()`，实践中不出现分歧 |
| 美股符号的 `StockNeedNewQuotes()` | 依赖系统时区库按 `America/New_York` 换算 | 有 `zoneinfo` 时完全一致；3.7 回退实现按 DST 规则近似 | 回退边界（美国历史规则变更、个别年份例外）不在覆盖范围，行情只关心近期日期 |
| 夏令时切换当天的 02:00-03:00 | PHP 用系统时区库处理歧义时刻 | `zoneinfo` 取首次出现；回退实现按日期规则 | 该时段不是有效交易日，实际不影响判断 |

## 尚未迁移

* `php/stock/mysqlref.php`、`fundref.php`、`qdiiref.php`、`mystockref.php` 等引用
  对象层（第 3 步）。本次用回调把它们挡在预取逻辑之外，等这一层迁移完成后可以
  去掉回调改为直接调用。
* `php/stock/yahoostock.php`、`szse.php`：历史数据回补。
* `php/ui/*` 渲染层与 `php/layout.php`、`menu.php`、`stocklink.php`（第 4、5 步）。
* `StockReference` 的 `GetStockId()` 等数据库方法：PHP 里由 `mysqlref.php` 提供。

## 重要语义说明

1. **`is_before_stock_market()` / `is_after_stock_market()` 的控制流**：A 股/港股/
   美股的判断嵌在「全球指数」分支内部（PHP 原文件缩进不规则，容易误读）。因此
   除 `znb_CAC`、`znb_DAX`、`znb_NKY`、`znb_TPX`、`znb_SENSEX` 之外的 `znb_*`
   指数会直接落到 `return false`。已用 PHP 实际执行结果确认。

2. **`_onSinaDataCN()` 实际只解析 5 档**：PHP 的循环是
   `for ($i = 10; $i < 20; $i += 2)`，只读 10/12/14/16/18 五个下标，卖盘顺延 10 位。
   虽然注释里叫「十档」，实现的档位数就是 5。端口按真实行为实现。

3. **`_checkBetweenMarketClose()` 用的是严格大于号**：时间差正好等于阈值时返回
   `false`。写测试时要注意这个边界。

4. **期货判定会先短路**：`FutureNeedNewFile()` 会先看当前时刻和文件时刻是否落在
   交易时段，只有当两者都不在交易时段时才会走阈值判断。测试里要构造两个非交易
   时刻才能测到阈值本身。
