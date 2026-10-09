# yahoostock.php 分析 + 国内数据源替代方案（待审核）

> 状态：**方案，未实施**。请审核后我再动手。

## 一、结论先行

| 问题 | 结论 |
| --- | --- |
| 是否抓 Yahoo 外网数据？ | **是**，抓 `query1.finance.yahoo.com` |
| 现在还能用吗？ | **完全不能用**，实测所有 Yahoo 端点都返回 **HTTP 403** |
| 该不该按原样迁移？ | **不该**（详见第三节） |
| 国内源能替代吗？ | **能**，且我已实测三个可用源（详见第五节） |

## 二、yahoostock.php 的功能分析

### 2.1 它抓什么

`_getYahooChartData()`（第 267 行）请求：

```text
https://query1.finance.yahoo.com/v7/finance/chart/<代码>?range=<范围>&interval=1d&indicators=quote&includeTimestamps=true
```

`GetYahooDataUrl('7')` 定义在 `php/externalurl.php:18`，硬编码 Yahoo 域名。
解析出 `timestamp[] / quote{open,high,low,close,volume}[] / adjclose[]`。

### 2.2 两个完全不同的功能

这个文件其实服务于**两条不相干的业务线**：

#### 功能 A：给 QDII 估值提供"中间层"数值序列

```text
YahooUpdateNetValue($est_ref)            # 第 370 行
  ├─ _yahooGetNetValueSymbol()           # 第 341 行：决定用哪个 Yahoo 代码
  │    · QQQ / SPY / XOP / INDA / IBB / USO 等 —— 中间层标的
  │    · Yahoo 代码 = BuildYahooNetValueSymbol($strSymbol)
  │                   = '^' + 代码 + '-IV'          # stocksymbol.php:506
  ├─ 节流：已有当天数据 → 返回；当天未收盘（<16:55）→ 返回
  └─ _yahooStockGetData($strSymbol, $strStockId)   # 第 308 行
       · range=1d（**日内**数据）→ 存入 **netvaluehistory**
       · 只写第一条成功记录（WriteDaily 遇已存在返回 false，循环即结束）
       · 返回 [净值, 日期]

YahooGetNetValue($ref)                   # 第 333 行 —— 强制更新版，无任何条件检查
```

**调用点**（全部与 QDII 估值页面有关）：

| 位置 | 说明 |
| --- | --- |
| `woody/res/php/_qdiigroup.php:61` | 对估值标的 `$est_ref` 更新 |
| `woody/res/php/_qdiigroup.php:79` | 对每个杠杆标的 `$leverage_ref` 更新 |
| `woody/res/php/_qdiimix.php:33` | 对 INDA（印度）标的更新 |
| `woody/res/php/_chinaindex.php:38` | 对中国指数页面的 `us_ref` 更新 |
| `woody/res/php/_submitstockoptions.php:354` | 管理页强制更新 |

#### 功能 B：抓美国标的的完整 OHLCV 历史

```text
UpdateYahooHistoryChart($ref)            # 第 406 行
  · range=2y、interval=1d（**日线**）→ 存入 **stockhistory**
  · 写入 open/high/low/close/volume/adjclose
  · 跳过：零成交额（假日）、重复日期、过早数据、close 为空
  · 结束后 DeleteByZeroVolume() 清理脏数据
```

**调用点**：`account/php/_editinput.php:199`（管理页）与
`woody/res/php/_submithistory.php:14`（手工提交历史），都是**管理端工具**。

### 2.3 和基金的关系（总结）

```text
                    yahoostock.php
                   /              \
        功能A（每日，页面自动）    功能B（手工，管理端）
                  |                        |
   给 QDII 估值的"中间层"           给任意标的补日线历史
   写入 netvaluehistory              写入 stockhistory
                  |
   QDII 页面 (fund/qdiilof/...) 的实时估值依赖它
```

**关键点**：功能 A 写的是**中间层**的数值，不是基金自己的官方净值。
基金官方净值走的是另一条路（`FundReference::GetOfficialNetValue()` →
`stock.php:StockUpdateEstResult()` → `fundest` 表）。这两者**不要混淆** ——
`netvaluehistory` 这张表同时被两个口径使用，是历史设计留下的坑。

## 三、为什么不该按原样迁移

### 3.1 Yahoo 已经彻底不可用（实测）

| 端点 | 结果 |
| --- | --- |
| `query1.finance.yahoo.com/v7/.../chart/^QQQ-IV` | **HTTP 403** |
| `query1.finance.yahoo.com/v8/.../chart/QQQ` | **HTTP 403** |
| `query2.finance.yahoo.com/v8/.../chart/QQQ` | **HTTP 403** |
| `finance.yahoo.com/quote/QQQ` | **HTTP 403** |

即使换 User-Agent、换 v7/v8、换 query1/query2 域名、甚至直接访问网页，全部 403。
Yahoo 近年加了区域限制 + Cookie/CRUMB 校验，老式"直接 GET"已经走不通。

结论：**即使照抄迁移过来，它一行数据也取不到。**

### 3.2 `-IV` 后缀含义存疑

`BuildYahooNetValueSymbol('QQQ')` 生成 `^QQQ-IV`。从命名看像是波动率指数，
但 `^XOP-IV` 在 PHP 注释样本里 `regularMarketPrice = 130.646`，与 XOP 当时
100～190 的价格区间同量级 —— 不像波动率（那通常是 20~40）。

**我无法确认**它到底指什么（Yahoo 已封，取不到数据核对）。这不影响方案，
但下面第五节第 4 条会提到它的**量级必须实测确认**。

### 3.3 附带的一个架构问题

PHP 的做法是「页面请求内同步抓 Yahoo」。按我们前面已经确立的方向
（后台定时抓 + 页面直读），这部分应该和新做好的实时行情链路统一，
而不是再写一套请求内同步抓取。

## 四、需要替代的数据清单

按上面两条业务线，需要替代的是：

| 用途 | 标的 | 写入表 | 频率 |
| --- | --- | --- | --- |
| 功能 A | QQQ、SPY、XOP、INDA 等中间层标的的**当日数值** | `netvaluehistory` | 每日（美股收盘后） |
| 功能 A（杠杆） | 各杠杆标的（如 TQQQ、SQQQ 等） | `netvaluehistory` | 每日 |
| 功能 B | 任意美国标的的 **2 年日线 OHLCV** | `stockhistory` | 手工/按需 |

## 五、替代方案

### 5.1 已实测可用的国内源

我实测了三个源，全部可用（都是在国内可正常访问的）：

#### 源 1：新浪财经 `hq.sinajs.cn`（**首选**）

* 美股/ETF 实时行情：`gb_qqq`、`gb_spy`、`gb_xop`、`gb_inda`、`gb_ibb`、`gb_uso`
* 全球指数：`int_nasdaq`、`int_sp500`、`int_dji`
* 基金净值：`f_161130`
* **我们已经迁移好了抓取层**（`sinaquotes.py` + `quotesource.py`），无需新写 HTTP 代码

实测响应：

```text
int_nasdaq  纳斯达克    22484.07   99.37   0.44
int_sp500   标普指数    6643.70    38.98   0.59
gb_qqq      纳百ETF     749.58     1.02    2026-10-03 09:44:30
gb_spy      SPDR标普500 769.64     0.74    2026-10-03 09:39:28
gb_xop      油气开采ETF 184.93     0.34    2026-10-03 09:40:02
gb_inda     印度股指ETF 46.52      0.35    2026-10-03 09:48:53
gb_ibb      生物科技ETF 205.29     -0.33   2026-10-03 09:34:21
gb_uso      油价ETF     147.37     -1.77   2026-10-03 09:40:47
f_161130    纳指LOF净值 4.5452     (2026-09-29)
```

#### 源 2：东方财富基金净值（**净值首选**）

```text
https://api.fund.eastmoney.com/f10/lsjz?fundCode=162411&pageIndex=1&pageSize=5
（需 Referer: https://fundf10.eastmoney.com/）
```

实测返回：`{"FSRQ":"2026-09-29","DWJZ":"0.9338","LJJZ":"0.9338",...}`
—— 日期、单位净值、累计净值都有，**支持历史分页**。

**和新浪净值交叉验证一致**：

| 基金 | 市价 | 新浪净值 | 东财净值 | 东财日期 |
| --- | --- | --- | --- | --- |
| SZ161130 | 4.796 | 4.5452 | 4.5452 | 2026-09-29 |
| SZ161125 | 3.275 | 3.1328 | 3.1328 | 2026-09-29 |
| SZ162411 | 0.913 | 0.9338 | 0.9338 | 2026-09-29 |
| SH513100 | 2.352 | 2.0379 | 2.0379 | 2026-09-29 |

两个独立源数值完全一致，可信度高。

#### 源 3：交易所官方（兜底交叉校验）

深交所 `CATALOGID=1785_child` 返回逐日单位净值（实测通用于 LOF 与 ETF）：

```text
161130 -> {"nav_date":"2026-09-28","fund_code":"161130",
           "security_short_name":"纳斯达克100LOF","nav_per_share":"4.5357"}
159919 -> {"nav_date":"2026-09-29","fund_code":"159919",
           "security_short_name":"沪深300ETF嘉实","nav_per_share":"4.6121"}
```

注意：深交所这个接口的 `metadata.recordcount` 恒为 0（不填），要取 `data` 数组。
上交所类似（需另测）。

### 5.2 数据落位设计

| 需要的数据 | 来源 | 落表 | 说明 |
| --- | --- | --- | --- |
| 中间层当日数值（功能 A） | 新浪 `gb_*` / `int_*` | **新增 `estquote`**（见下） | 不再写进 `netvaluehistory` |
| 基金官方净值 | 东方财富（主）/ 新浪 `f_*`（备）/ 交易所（校验） | `netvaluehistory` | 单位净值，与溢价率口径一致 |
| 标的日线历史（功能 B） | 新浪 `gb_*` 累积 + 东财/腾讯历史接口（待测） | `stockhistory` | 管理端按需补齐 |

**关于中间层该不该写 `netvaluehistory`**：PHP 写进去了，导致这张表混了两种口径
（基金净值 + 中间层估值序列），这也是我之前做种子数据时被量级问题绕住的根源。
建议**新开 `estquote` 表**单独放中间层数值，让 `netvaluehistory` 只放真正的单位净值。
好处：
1. 语义清晰，溢价率不会再被中间层数据污染；
2. 换源时互不影响；
3. `get_latest_net_value_close()` 这类查询天然正确。

### 5.3 接口设计（复用已有抽象）

我们已经有了 `quotesource.MarketQuoteSource`（行情源接口）。这次再加一个
**净值源接口**，形态一致：

```python
class NavSource:
    name = "abstract"
    def fetch_latest(self, code) -> {"date": ..., "nav": ..., "acc_nav": ...} | None
    def fetch_history(self, code, start=None, end=None) -> [ {date, nav, acc_nav}, ... ]
    def describe(self) -> dict
```

已注册实现：`eastmoney`（首选）、`sina`（备选）、`szse`（兜底校验）。
环境变量 `PALMMICRO_NAV_SOURCE` 切换，与行情源的做法完全一致。
这样你以后换源仍然是改一个环境变量。

### 5.4 迁移范围与步骤

| 步骤 | 内容 | 对应 PHP |
| --- | --- | --- |
| 1 | `navsource.py`：净值源接口 + 东财/新浪/深交所三个实现 | `yahoostock.php` 的净值部分 |
| 2 | `navupdater.py`：每日抓取官方净值，写 `netvaluehistory`；节流与交易时段守卫 | `YahooUpdateNetValue()` |
| 3 | `estquote` 表 + 中间层数值写入（复用已有的 `quoteupdater` 链路） | `_yahooStockGetData()` |
| 4 | 中间层日线累积进 `stockhistory`（沿用 `quotestore.QuoteStore`） | `UpdateYahooHistoryChart()` |
| 5 | Web：详情页显示官方净值、中间层估值、溢价率 | `EchoNetValueCloseParagraph` 等 |
| 6 | Python 侧**不提供** `YahooGetNetValue()` 的强制更新入口（PHP 第 333 行）——它只被管理页用，且是"无任何条件检查"的危险接口 | —— |

### 5.5 必须先验证的一件事（**量级**）

我目前的种子数据里，中间层 calibration 用的是**指数点位**（QQQ ≈ 560），
而真实生产库里用的是 Yahoo `-IV` 序列。**两者量级可能差很多**，会直接影响
`fundcalc.calc_with_calibration()` 的 `position × est_value × cny / calibration` 项。

所以第 3 步之前，我会先用真实生产库数据核对：

1. 从 `calibrationhistory` 取真实中间层 calibration；
2. 反推 PHP 当年 `-IV` 序列的量级；
3. 确认换成新浪 `gb_qqq`（≈750）后，是「直接可用」还是「需要一次性换算系数」。

**这一步不做，估值数字会是错的。** 如果确认需要换算，我会把系数写进配置并在
迁移说明里记录来源。

## 六、需要你确认的三个决策点

1. **中间层数值是否新开 `estquote` 表**（推荐），还是继续沿用 `netvaluehistory`？
2. **净值源首选东方财富还是新浪**？两者实测一致；东财有完整历史分页，新浪更简单
   且抓取层已就绪。我建议**东财为主、新浪为备**。
3. **功能 B（管理端补日线历史）本次是否一并迁移**？它只被管理页调用，优先级低。
   建议**本次先不做**，等前面的净值链路跑通再说。

## 七、我的建议

按第五节执行，其中：

* 立刻做：净值源接口 + 净值抓取（东财为主）+ `estquote` 表 + 中间层链路；
* 一并做：量级验证（5.5）；
* 暂缓：功能 B 的日线历史补齐、上交所需另测。

请审核，特别是第六节的三个决策点和 5.5 的量级验证是否同意。
