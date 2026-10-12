# 基金净值迁移说明（替代 yahoostock.php）

本文档记录「用国内数据源替代 `php/stock/yahoostock.php`」这一步。
分析结论与替代方案见 `python/NAV_SOURCE_PLAN.md`（已批准）。

## 一、为什么必须换源

`yahoostock.php` 抓的是 Yahoo：`GetYahooDataUrl('7')` →
`https://query1.finance.yahoo.com/v7/finance/chart/...`。实测**全部不可用**：

| 端点 | 结果 |
| --- | --- |
| `query1.finance.yahoo.com/v7/.../chart/^QQQ-IV` | HTTP 403 |
| `query1.finance.yahoo.com/v8/.../chart/QQQ` | HTTP 403 |
| `query2.finance.yahoo.com/v8/.../chart/QQQ` | HTTP 403 |
| `finance.yahoo.com/quote/QQQ`（网页） | HTTP 403 |

换 UA、换版本、换域名、直接访问网页都是 403。**照原样迁移一行数据也取不到。**

复验命令：`python -B auto\_nav_source_probe.py --yahoo`

## 二、yahoostock.php 原本做什么

它服务两条**不相干**的业务线：

| | 功能 A | 功能 B |
| --- | --- | --- |
| 入口 | `YahooUpdateNetValue()` / `YahooGetNetValue()` | `UpdateYahooHistoryChart()` |
| 抓什么 | `range=1d` 日内数据 | `range=2y` 日线 OHLCV |
| 写哪张表 | `netvaluehistory` | `stockhistory` |
| 用途 | QDII 估值的**中间层**数值 | 管理端补历史 |
| 频率 | 每日，页面自动 | 手工，管理端 |
| 调用点 | `_qdiigroup.php:61/79`、`_qdiimix.php:33`、`_chinaindex.php:38`、`_submitstockoptions.php:354` | `_editinput.php:199`、`_submithistory.php:14` |

**本次迁移功能 A 的"基金官方净值"部分；功能 B 按决定暂缓。**

### 一个容易混淆的点

PHP 里 `YahooUpdateNetValue()` 写的是**中间层**数值（`^QQQ-IV`），
**不是基金官方净值**。基金官方净值走的是另一条路：

```text
FundReference::GetOfficialNetValue()   → php/stock/fundref.php
  → StockUpdateEstResult()             → php/stock.php:148
  → fundest 表
```

而 `netvaluehistory` 这张表**同时**被两个口径写过（基金净值 + 中间层序列），
这是历史设计留下的坑，也是我早先做种子数据时被"量级"问题绕住的根源。

## 三、量级问题：从根上不需要换算系数

我原先担心"Yahoo `-IV` 序列换成新浪市价（≈750）量级差很多"。查代码后确认
**不需要任何换算系数**，原因在 `php/stock/qdiiref.php:193`：

```php
$this->fFactor = QdiiGetCalibration($strEst, $strCNY, $this->GetPrice());
//             = est_value * cny / net_value
```

`calibrationhistory` 里的值是**从"源值 + 净值"反算出来的派生量**，而估算公式

```php
QdiiGetVal($strEst, $strCNY, $this->fFactor)  // = est_value * cny / fFactor
```

恒等于净值 —— **与源用量纲无关**。换源时只要让 `calibrationhistory` 跟着重算
（`AdjustFactor()` 本来就这么做），一切自洽。

结论：不需要硬编码换算系数，也不需要在 `estquote` 里存"已换算的值"。

## 四、数据源与接口

### 4.1 三个国内源（全部实测可用且互相验证）

| 源 | 用途 | 实测 |
| --- | --- | --- |
| `eastmoney` 东方财富 | **首选**，有历史分页 | 161130 = 4.5452（2026-09-29） |
| `sina` 新浪 | 备选 | 与东财**数值完全一致** |
| `szse` 深交所 | 兜底交叉校验 | 4.5357（2026-09-28），仅深市 |

交叉验证命令与实测结果：

```powershell
python -B auto\navcli.py --all-sources 161130

源            日期           单位净值    累计净值    结果
eastmoney    2026-09-29   4.5452     4.5452     OK
sina         2026-09-29   4.5452     4.5452     OK
szse         2026-09-28   4.5357     -          OK
```

（szse 差一天是正常的，它更新更慢。）

### 4.2 接口设计（与行情源一致）

```python
class NavSource:
    name = "abstract"
    def fetch_latest(self, code) -> {"code","date","nav"[,"acc_nav"]} | None
    def fetch_history(self, code, limit=30) -> [ {...}, ... ]   # 日期倒序
    def describe(self) -> dict
```

* `code` 是 **6 位纯数字**基金代码（`161130`），不带市场前缀；
* 取不到数据返回 `None`，不抛异常（调用方按"跳过该基金"处理）；
* 注册切换：`register_nav_source("mysource", ...)` + 环境变量
  `PALMMICRO_NAV_SOURCE`。

自查接口：`python -B auto\navcli.py --api`

### 4.3 中间层标的：新浪全覆盖

`PHP` 用 `^XXX-IV` 覆盖的 18 个中间层标的，新浪**全部有**（实测）：

```text
AGG gb_agg   IBB gb_ibb   IEO gb_ieo   INDA gb_inda  IXC gb_ixc
KWEB gb_kweb QQQ gb_qqq   RSPH gb_rsph SPY gb_spy    USO gb_uso
VGT gb_vgt   VNQ gb_vnq   XBI gb_xbi   XLY gb_xly    XOP gb_xop
^DJI gb_dji  ^GSPC gb_inx ^NDX gb_ndx
```

所以**不需要 Yahoo 也能拿到全部中间层数据**。名单由
`quoteupdater.default_est_symbols()` 推导：

1. `fundcalc.qdii_est_symbol_map()` 的值集合；
2. 补上 `QQQ` / `SPY` —— 它们没有被显式映射，而是走
   `qdii_qqq_symbols()` / `qdii_spy_symbols()` 两条默认路径，
   不补上就会让最大的两类 QDII 基金反而没有中间层数据；
3. 补上 `HOLDINGS_EST_SYMBOLS`（INDA/IBB/USO）—— 持仓型估值用，
   该功能本身暂缓，名单先留着。

## 五、落库位置的变化（重要）

PHP 把中间层数值写进 `netvaluehistory`，与基金单位净值混在一张表。
本次**新开 `estquote` 表**分开存：

| 数据 | 表 | 说明 |
| --- | --- | --- |
| 基金**官方单位净值** | `netvaluehistory` | 溢价率的分母 |
| **中间层**估值数值 | `estquote`（新） | QDII 估算用 |
| 实时行情快照 | `realtimequote` | 页面"最新价" |
| 历史收盘价 | `stockhistory` | 来自行情累积 |

好处：语义清晰；溢价率不会被中间层数据污染；换源时互不影响。

`quoteupdater` 在写 `realtimequote` 的同时，对命中中间层名单的符号
**额外写一份 `estquote`** —— 对应 PHP `_yahooStockGetData()` 的行为，
但不再污染净值表。

## 六、涉及的文件

| 文件 | 职责 |
| --- | --- |
| `python/auto/source/navsource.py` | 净值源抽象 + 东方财富/新浪/深交所三个实现 |
| `python/auto/updater/navupdater.py` | 净值抓取与落库、`16:55` 守卫 |
| `python/auto/cli/navcli.py` | 命令行：抓取 / 检查 / 历史 / 交叉对比 / 接口说明 |
| `python/auto/sql/quotestore.py` | 新增 `EstQuoteStore`（`estquote` 表读写） |
| `python/auto/updater/quoteupdater.py` | 新增 `est_store` / `est_symbols`，派发中间层写入 |
| `python/dbtools/schema.sql` | 新增 `estquote` 表（含量级说明注释） |

## 七、踩到并修掉的一个真 bug

第一版 `should_update()` 拿**今天**去判断"是否已过公布时刻"：

```python
# 错误写法
today = now.strftime("%Y-%m-%d")
if not self.should_update(stock_id, today):   # 用今天判断
    continue
```

后果：下午 3 点运行时源返回的是**昨天**的净值（当天净值还没公布），
而"今天还没到 16:55"就整个跳过 —— **一条净值都写不进去**。
实测时正是这个现象（`已写入 0 条净值`）。

修法：判断依据改成**源给出的日期**（`record["date"]`）。语义变成：

1. 库里已有该日期 → 跳过；
2. 源日期 == 今天 且 未到 16:55 → 跳过（等公布）；
3. 其余写入。

已回归测试 `test_writes_yesterdays_nav_when_today_not_published`。

## 八、验证结果

```powershell
cd F:\woody\woodyweb\python

python -B dbtools\build_schema.py          # 新建 estquote 表
python -B auto\navcli.py --check       # 只看源返回什么
python -B auto\navcli.py               # 真实写入
python -B auto\navcli.py --all-sources 161130   # 三源交叉验证
python -B auto\quoteupdatercli.py --once        # 抓行情（顺带写 estquote）
python -B dbtools\verify_pages.py          # 页面复核
```

实测（真实联网 + 真实数据库）：

```text
navcli  :  待处理 3 只基金：SZ161130, SZ161125, SH513100
           已写入 3 条净值。

estquote:  QQQ  gb_qqq  749.58  742.03  2026-10-02 16:00:00
           SPY  gb_spy  769.64  763.99  2026-10-02 16:00:00

/fund/qdiilof
  SZ161130  易方达标普信息科技LOF  0.95  0.829884  2026-01-02  4.796  5.52%
  SZ161125  易方达标普500LOF      1.0   1.215251  2026-01-02  3.275  4.54%
```

**溢价率已经是真实数字**（5.52% / 4.54% / 15.41%），不再是之前按假设
反算的 -0.50%。

测试：`python/auto` **303 个**（新增 `test_navsource.py` 30 个 +
中间层写入 4 个）、`python/web/tests` **36 个**，全绿。

## 九、口径说明（看页面时要知道）

* 页面「最新价」取 `stockhistory` / 实时行情里**最新**的市价；
  「净值」取 `netvaluehistory` 里**最新**的单位净值。
* QDII 基金净值天然**滞后一天**（T 日净值反映 T-1 日的美股），
  所以两边的日期常常不同（实测市价 09-30、净值 09-29）。这是正常的，
  PHP 也是同样口径。
* 因此溢价率反映的是"当前场内价 vs 最近已公布净值"，与交易所公布的
  溢价率会有细微时点差异。

## 十、仍未完成

1. **功能 B（管理端补日线历史）按决定暂缓**。需要时再迁
   `UpdateYahooHistoryChart()`，数据源同样可用新浪 `gb_*` 累积。
2. **中间层数值尚未接进估值公式**。`estquote` 已在写入，但
   `fundcalc.calc_with_calibration(source=...)` 的 `source` 仍由调用方提供。
   接进去时需要同时让 `calibrationhistory` 重算（`AdjustFactor` 逻辑），
   这一点在第三节已说明不需要换算系数。
3. **上交所净值接口未测**。目前深市走 `szse`，沪市只有东财/新浪。
4. **`gb_inx` 与 `int_sp500` 数值不一致**（实测 7722 vs 6643），
   说明新浪自家不同代码的更新时间/口径可能不同。中间层用的是 `gb_inx`，
   建议后续用真实数据核对一次。
