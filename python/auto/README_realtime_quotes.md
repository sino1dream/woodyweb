# 实时行情接入 Web 迁移说明

本文档记录「把实时行情接进 Web」这一步：后台定时抓取 + 页面直读最新价，
以及为**更换行情源**预留的 API 接口。

## 一、先回答：PHP 有没有预留行情 API 接口

**没有。** 我把 PHP 侧翻了一遍，结论明确：

| 检查项 | 结果 |
| --- | --- |
| 是否存在行情 JSON/AJAX 端点 | **不存在**。全仓库只有 `php/telegram.php` 用 `json_encode`，那是 Telegram 机器人回包，不是行情接口 |
| 是否有前端 JS 抓行情 | **没有**。`palmmicro.js` 与模板里没有 `XMLHttpRequest` / `fetch(` / `.ajax` |
| 实时行情怎么进页面 | **页面请求内同步抓取**，服务端渲染 |

PHP 的实际链路（以 `/qdiigroup` 这类页面为例）：

```text
页面请求
  → StockPrefetchArrayData($arSymbol)          // php/stock.php
  → PrefetchSinaStockData($arSymbol)           // php/stock/stockprefetch.php
      · 按符号类型判断"是否需要新行情"
        （IsSinaFund / IsSinaFuture / IsSinaForex / 其它股票）
      · 用 debug/sina/*.txt 的**文件修改时间**做节流：60 秒内不重取
  → GetSinaQuotes($arSymbol)                   // 拼 hq.sinajs.cn/list=...，curl 取
  → 逐行写回 debug/sina/*.txt                  // PrefetchSaveSinaData
  → new MyStockReference($symbol)              // 请求内读文件并解析
  → 页面把实时价/估值直接渲染进 HTML
```

关键特征：**写死新浪**（`GetSinaDataUrl()` 直接返回 `http://hq.sinajs.cn/list=`）、
**没有抽象层**、**没有对外接口**。换源必须改 `GetSinaQuotes()` 与 `url_get_contents()`。

所以按你的要求，Python 版**自己预留了接口**，见下节。

## 二、Python 版的行情源接口（换源只改一处）

核心在 `python/auto/source/quotesource.py`：

```python
class MarketQuoteSource:
    name = "abstract"

    def fetch_lines(self, symbols):   # 必须实现
        """返回 {符号: 原始文本}；整批失败返回 None"""

    def fetch_raw(self, symbols):     # 可选覆写（已给默认实现）
        """按行拼接的原始响应，供 stockprefetch._prefetch() 使用"""

    def describe(self):               # 可选覆写（已给默认实现）
        """数据源自述，用于诊断"""
```

**返回约定**（重要）：

* 键 = 调用方传入的符号（数据源自己的代码，新浪就是 `sz161130`、`gb_qqq`）；
* 值 = 该符号的原始文本；
* 返回 `None` 表示整批失败，与 PHP `GetSinaQuotes()` 返回 `false` 对齐。

保持这个约定，`stockref` 的解析器就不用改。**换源三步**：

```python
# 1) 新写一个数据源
class MyQuoteSource(quotesource.MarketQuoteSource):
    name = "mysource"
    def fetch_lines(self, symbols):
        data = my_http_get(symbols)          # 你的行情源
        return {s: data[s] for s in symbols}

# 2) 注册
from quotesource import register_quote_source
register_quote_source("mysource", lambda **kw: MyQuoteSource(**kw))

# 3) 切换（不用改代码）
#    set PALMMICRO_QUOTE_SOURCE=mysource
```

命令行也可以自查接口与已注册的源：

```powershell
python -B auto\quoteupdatercli.py --api             # 打印接口契约与当前源自述
python -B auto\quoteupdatercli.py --list-sources    # 列出已注册的源
GET /api/quotes/source                              # Web 侧同样可查
```

如果新源不是「逐行 `var hq_str_X="...";`」这种格式（例如 JSON），
需要在解析层加一个适配函数，把字段映射到 `StockReference` 的属性上
（`quoteupdater.parse_symbol()` 就是新浪格式的适配点）。

## 三、定时抓取 + 页面直读的实现

### 3.1 与 PHP 的架构差异

| | PHP | 本实现 |
| --- | --- | --- |
| 抓取时机 | 页面请求内同步抓 | **独立后台进程定时抓** |
| 节流手段 | `debug/sina/*.txt` 的文件时间（60 秒） | 进程内 TTL + 数据库快照时间（默认 60 秒） |
| 页面取数 | 请求内读文件并解析 | **直读数据库快照表** |
| 换源 | 改代码 | 改环境变量 |

**为什么改成后台定时抓**：页面请求内同步抓会让响应变慢，也更容易触发新浪的频率
限制；而且 PHP 那套"每请求抓一次"在页面变多后会成倍放大请求数。

### 3.2 跨进程共享：`realtimequote` 表（这是关键）

第一版我把实时行情放在**进程内存**里，结果踩了坑：抓取进程写的缓存，
Web 进程读不到 —— 两个进程的内存互不可见，多 worker 更不可能共享。

所以新增了 `realtimequote` 表（`python/dbtools/schema.sql`），只保留每个符号的
**最新一条**快照（`id` 即 `stock_id`，一符号一行）：

```text
抓取进程  --写入-->  realtimequote  --读取-->  Web 进程  -->  页面最新价
```

读取是两级：先看本进程内存，未命中再读表（`QuotesUpdater.get()` / `get_all()`）。
这样既省查询，又保证多进程能共享。

**这一列还带了五档盘口**：表上有个 `orderbook` 列（JSON 文本，买一到买五、
卖一到卖五），抓取进程顺手写进去、页面读回来装填引用对象，
于是「五档交易」表**不必在请求期联网**就能渲染。
详见 `README_ui_column_migration.md` 的第四轮那一节。

> 加列走 `dbtools/patch_realtimequote_orderbook.sql`（用 `dbtools/apply_patch.py` 执行）——
> `schema.sql` 是 `CREATE TABLE IF NOT EXISTS`，不会给已存在的表补列。
> 读写两侧都能在"列不存在"时退回原样（只认 MySQL 1054 这一个错码）。

### 3.3 涉及的文件

| 文件 | 职责 |
| --- | --- |
| `python/auto/source/quotesource.py` | 行情源抽象与注册表（**换源接口**） |
| `python/auto/updater/quoteupdater.py` | 抓取 → 解析 → 缓存/落库；市场时段判断；进程级单例 |
| `python/auto/cli/quoteupdatercli.py` | 命令行：`--once` / `--daemon` / `--api` / `--only-open` |
| `python/auto/source/sinaquotes.py` | 新浪实现（`SinaMarketQuoteSource`） |
| `python/auto/sql/quotestore.py` | `RealtimeQuoteStore`（快照表读写）、溢价率 |
| `python/web/routes/quote.py` | `/api/quotes`、`/api/quotes/refresh`、`/api/quotes/source` |
| `python/web/services/fund_list_service.py` | 列表页优先用实时价，取不到才回落收盘价 |

### 3.4 页面行为

分组列表最后两列现在是**最新价**与**溢价率**：

```text
股票代码     名称                    仓位   校准值      日期        最新价              溢价率
SZ161130   易方达标普信息科技LOF      0.95  0.829884  2026-01-02  4.796 15:00:00     -0.5%
```

* 最新价后面小字是该行行情的**时间**，一眼能看出是不是实时；
* 溢价率 = 最新价 / 单位净值 - 1，正值（溢价）红、负值（折价）绿；
* 没有实时行情时自动回落到 `stockhistory` 的收盘价；两者都没有就留空；
* 行情过期（超过 TTL）不会被当成有效数据。

## 四、环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PALMMICRO_QUOTE_SOURCE` | `sina` | 行情源名字 |
| `PALMMICRO_QUOTE_INTERVAL` | `60` | 抓取间隔（秒），同时是缓存 TTL |
| `PALMMICRO_QUOTE_REFRESH_ON_REQUEST` | `0` | 设为 `1` 时页面请求内也顺手刷新一批 |

`REFRESH_ON_REQUEST` 默认关闭是刻意的：那是 PHP 的做法，会让页面响应变慢。
要临时看最新价，用 `POST /api/quotes/refresh` 更合适。

## 五、验证（真实联网 + 真实数据库）

后台抓取实测输出：

```text
[23:41:41] 更新 5 条  QQQ=749.375 SH513100=2.352 SPY=768.84 SZ161125=3.275 SZ161130=4.796
[23:42:10] 更新 5 条  QQQ=749.375 SH513100=2.352 SPY=768.84 SZ161125=3.275 SZ161130=4.796
```

Web 进程（**独立进程**）直读同一份数据：

```text
GET /api/quotes        → count=5
  QQQ       价=749.375  时间=11:41:00  龄=23.2s
  SPY       价=768.84   时间=11:41:00  龄=23.2s
  SZ161130  价=4.796    时间=15:00:00  龄=23.2s

GET /fund/qdiilof      → <td>4.796 <span class="quote-time">15:00:00</span></td>
                         <td class="premium negative">-0.5%</td>
```

跨进程共享成立的关键就是上面那张 `realtimequote` 表 —— 抓取进程写、Web 进程读。

### 命令

```powershell
cd F:\woody\woodyweb\python

# 建表（新增了 realtimequote）
python -B dbtools\build_schema.py

# 抓一轮
python -B auto\quoteupdatercli.py --once --verbose

# 常驻定时抓取
python -B auto\quoteupdatercli.py --daemon --interval 60 --only-open

# 换源参考
python -B auto\quoteupdatercli.py --api

# 排查
python -B auto\_quote_probe.py --realtime     # 看快照表与新鲜度
```

测试：`python/auto` **269 个**（新增 `test_quoteupdater.py` 24 个）、
`python/web/tests` **36 个**（新增 6 个覆盖 `/api/quotes` 与列表页实时价），全绿。

## 六、仍需注意

1. **`--only-open` 依赖市场时段判断**：非交易时段不抓，能显著减少请求；
   判断用的是 `stocksymbol.StockSymbol.is_stock_market_trading()`（已与 PHP 对照过）。
   注意 A 股收盘后仍会抓到当日收盘价，这是正常的。
2. **净值仍是占位值**，所以溢价率的绝对值还不可信（计算链路是通的）。真实净值要
   迁移 `fundref.php` / `yahoostock.php` 那条线。
3. **频率限制**：默认 60 秒一批，与 PHP 的"每分钟一次"一致。新浪没有给出明确的
   限额，但建议不要低于 30 秒。
4. **进程管理**：`--daemon` 目前只是个 `while` 循环。生产环境建议交给系统的计划
   任务/supervisor，用 `--once` 模式更稳妥（避免进程意外退出无人拉起）。
5. **`realtimequote` 不做历史**：历史收盘价属于 `stockhistory`，两者口径不同，
   刻意没有混在一张表里。
