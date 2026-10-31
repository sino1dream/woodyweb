# `etfdividendcn.php` → `/stock/<symbol>/etfdividend`

> 迁移日期：2026-10-31
> PHP 源：`woody/res/etfdividendcn.php`（73 行）+ `php/sql/sqldailyclose.php:291`
> 落地：`python/web/services/etf_dividend_service.py`、`python/web/routes/stock.py`、
> `python/web/templates/stock_etf_dividend.html`、
> `python/auto/sql/stockhistory.py::get_close_text`

## 一、这一页是干什么的

一个 `SymbolAccount`（`woody/res/php/_emptygroup.php`）页面，整页只有一张表：

```
日期 / 分红 / 价格 / 比例(%)
```

用途写在它自己的 meta description 里 —— **通过历史数据为夜盘跨市场套利工具
估算美股 ETF 分红除权当日对应的 QDII 基金的假溢价幅度**。

机制：QDII 基金（如 `SZ161125` 标普500）持有美股 ETF（`SPY`）。ETF 除息那天
价格会跌掉分红金额 D，而基金拿到的是**现金分红、净值不跌**，于是用 ETF 价格
算出来的实时估值端会冒出一段并不存在的折价，幅度约等于 `D ÷ 收盘价`。
表里第四列就是这个百分比。

## 二、两道闸门（顺序不能换）

| # | 条件 | 后果 | PHP 出处 |
|---|---|---|---|
| 1 | 建不出引用对象，或 `stock` 表里没有这个符号 | 整页只剩标题，`page is None` | `EchoStockGroup()` → `GetSymbolRef()` → `StockCheckSymbol()` |
| 2 | `CountNetValue() == 0` | **连表头都不输出**，`page["table"] is None` | `_EchoEtfDividendHistoryParagraph()` 开头 `return` |
| 3 | 单行：该除息日在 `stockhistory` 里没有收盘价 | 这一行**整行跳过** | `if ($strClose = $his_sql->GetClose(...))` |

**闸门 2 的判据是"净值"，不是"分红"。** 把它写成"有分红记录才显示表"是最容易
犯的错：生产端 `?symbol=SH600030`（A 股个股、无净值记录）页面上一个表都没有，
但 `?symbol=SZ161125`（有 31 条净值）表头是有的、只是没有行。

实测确认：`SH600030` / `00700` 走闸门 2，`SZ161125` / `SH513100` / `QQQ` 通过。

## 三、逐列口径

| 列 | 取值 | 注意 |
|---|---|---|
| 日期 | `stockdividend.date` | `GetAll()` 是 `ORDER BY date DESC`，最新在上，**无分页** |
| 分红 | `rtrim0(stockdividend.close)` | `rtrim0()` 实际是 `strval(floatval($str))`（`php/debug.php:60`），`0.500` → `0.5` |
| 价格 | `StockHistorySql.get_close_text()` | **不过 rtrim0**，`741.470` 保持三位 |
| 比例(%) | `php_number_format(分红 / 价格 × 100.0, 2)` | 固定两位，`0.10134` → `0.10` |

### 价格列的坑：`double(10,3)` 不只是精度问题

`stockhistory.close` 是 **`double(10,3)`**。mysqli 把 DOUBLE 当字符串交出来，
而且是**按列定义补零**的 —— `'741.470'`。pymysql 却转成 Python `float`，
`str()` 得到 `'741.47'`。

第一版直接 `str(close)` 写出去，对照立刻报差异：

```
PHP rows  [['2026-09-21', '0.751474', '741.470', '0.10'], …]
Python    [['2026-09-21', '0.751474', '741.47',  '0.10'], …]
```

修法是给 `StockHistorySql` 加 `get_close_text()`，SQL 里 `CAST(close AS CHAR)`，
让**数据库自己**格式化。不硬编码三位小数：列定义从 `(10,3)` 变成 `(10,4)` 时
这里自动跟着走。

**不要**把 `get_close()` 也改成返回字符串 —— 均线、EMA、估值都在用它的 float。

## 四、外链：只留 Sina，Yahoo 去掉

`GetStockDividendLink()`（`php/externallink.php:305`）原本三派：

| 分支 | PHP 行为 | 本版 | 实测（2026-10-31） |
|---|---|---|---|
| `IsSymbolA()` | 新浪「A 股分红配股」`vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/<六位数字>.phtml` | **保留** | `600030` 正常（每 10 股派息；2026-06-04 派 4.1 元）；`513100` / `161125` 是「暂时没有数据！」 |
| `IsSymbolH()` | 新浪港股「分红派息」`stock.finance.sina.com.cn/hkstock/dividends/<符号>.html` | **保留** | `00700` 正常（末期息 5.3 港元 + 除净日） |
| 其余（美股） | Yahoo `finance.yahoo.com/quote/<sym>/history?filter=div` | **去掉，不生成链接** | 该域名对大陆返回"自 2021-11-01 起服务停止"公告页 |

`IsSymbolA()` 只看"六位数字 + SH/SZ/BJ 前缀"，**不区分股票与基金** ——
所以 `SH513100` / `SZ161125` 这类 ETF、LOF 也会拿到 Sina 链接。那两个页面在
新浪是空的，但链接本身可点、挂着不花成本，照抄。

## 五、已知偏差

1. **Yahoo 外链去掉**（见 §四）。程序从来不靠它抓数据 —— 抓取走
   `submitdividend.php`：`GetSpdrOfficialUrl()` 命中的 5 只（SPY / XBI / XLE /
   XLY / XOP）问 `ssga.com`，其余全部送 `proshares.com`（PHP 的 `else` 兜底）。
   `DebugGetYahooFileName()` 只是缓存文件名沿用了 Yahoo 时代的命名，与数据源无关。
2. **`$bAdmin` 的"更新分红数据"入口未迁**。抓取链路已在
   `python/auto/updater/etfdividend.py`，本版没有登录态，要更新走 `/submit`。
3. **价格 0 的行跳过**（PHP 会整页 500）。PHP 8 里 `分红 / 0` 抛
   `DivisionByZeroError`；一行脏数据不该让整页挂掉。正常数据不会走到这条分支。
4. **入口比 PHP 多一个**。PHP 的入口是 `GetFundLinks()` 的 `else` 分支
   （只给非中国基金标的）；本版在 `/stock/<symbol>` 详情页底部统一放了一条
   「分红数据」链接 —— 基金符号点了会看到闸门 2 的说明，无害。
5. PHP 页头那串历史链接（历史价格 / 基金溢价记录 / 净值记录 / 净值价格比较 /
   基金场内份额）在本版是站内 `/history/<kind>/<symbol>`，不是生产端 `*cn.php`。

## 六、验证

### PHP 真身对照：9 项全一致

`_scratch/_php_etfdividend_check.php` 跑**真** `StockDividendSql` /
`StockHistorySql` / `rtrim0()`（在 `SqlDieByQuery()` 里拦 `CREATE TABLE`，
与 pitfalls #145 / #165 同一套路），`_scratch/_php_etfdividend_compare.py`
负责插入探针数据、调 Python 服务、逐格比对、最后把库恢复原样。

演示库 `stockdividend` **只有 4 行且全是孤儿**（`stock_id` 182 / 184 / 186 / 214，
`stock` 表最大 id 181），所以不插数据一条分支都验不到。三行探针数据：

| 日期 | 分红 | 验什么 |
|---|---|---|
| 2026-09-21 | 0.751474 | QQQ 有收盘价 → 正常出四格 |
| 2026-09-03 | 0.500 | 分红过 `rtrim0` → `0.5`；价格仍是 `717.670` |
| 2020-01-01 | 0.300 | 当天没有收盘价 → 整行跳过 |

结果：

```
PHP rows        [['2026-09-21', '0.751474', '741.470', '0.10'],
                 ['2026-09-03', '0.5', '717.670', '0.07']]
Python rows     同上
跳过 2020-01-01  ✓     表 id QQQetfdividend ✓
外链（美股为空）  ✓     净值条数 7 ✓
库已恢复：4 行 / max(id) 236 ✓
```

改库前另有 `mysqldump` 落在系统临时目录（`stockdividend_before_20261031.sql`）。

### 单元测试

* `python/web/tests/test_etf_dividend_page.py`（20 例）：外链三派、两道闸门、
  行装配、`rtrim0` 只作用于分红、价格三位、比例两位、表不存在不 500、路由四种状态；
* `python/auto/test_stockhistory.py::GetCloseTextTest`（3 例）：三位字符串、
  没有该日期返回 `None`、**SQL 里确实有 `CAST(... AS CHAR)`**（防止有人"优化"掉）。

## 七、演示库现状（数据缺口，不是 bug）

| 事实 | 影响 |
|---|---|
| `stockdividend` 4 行全是孤儿 `stock_id` | 任何符号都查不到分红行 |
| `QQQ` 有 7 条净值、`SZ161125` 31 条 | 闸门 2 能过，表头出得来 |
| `00700` 有 `stock` 行但无净值 | 证明闸门 2 在真库路径下生效 |

所以本地 `/stock/<symbol>/etfdividend` 最好的结果是"表头 + 空行提示"。
要在本地看到完整一张表，得先 `dbtools/seed_option_tables.py` 补数据或手工插行。
