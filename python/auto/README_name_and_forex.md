# 名称纠错 + 汇率接入估值 + 仓位默认值 + 列表页表头

本文档记录五件事：

1. **修掉一个数据错误**：SZ161130 的名称被写成了 SZ161128 的；
2. **迁移汇率对估值的影响**：汇率此前恒为 1.0，等于把美元当人民币用；
3. **修掉仓位默认值**：`get_position()` 硬编码 1.0，而 PHP 对 LOF 用 0.95；
4. **修掉校准值恒为 1.0**：改用生产口径 `C_f = N_f × cny / p_f`（见 0.4）；
5. **列表页表头对齐生产端**：拆开两处同名"日期"，补上行情时间、涨跌幅、数据源
   （见 0.5）。

## 零、仓位 / 校准值 / 列表页表头

### 0.5 列表页表头对齐生产端（最新）

#### 0.5.1 生产端有两张不同的表

`https://palmmicro.com/woody/res/qdiicn.php` 和 `fundlistcn.php` 上其实有**两张**
表，列义完全不同：

| 表 | 表头 |
| --- | --- |
| 参考数据（行情） | `代码 \| 价格 \| 涨幅 \| 日期 \| 时间 \| 名称` |
| 基金指数对照表 | `代码 \| 跟踪代码 \| 仓位 \| 校准值 \| 日期 \| 参考值` |

两张表各有一个 **`日期`**，但含义不同：前者是**行情日期**，后者是**校准值日期**。

#### 0.5.2 一个必须指出的问题

原来 `stockdata` 侧的列表页用一列 `日期` 承载的是 `calibrationhistory.date`
（校准值日期），**不是行情日期**：

```python
calibration = self.source.get_calibration_now(stock_id)
if calibration is not None:
    row["date"] = calibration.date     # ← 这是校准值日期
```

所以那一列**不能**直接改名成「最新行情日期」，否则会把校准值的日期冒充成行情日期。
正确做法是**拆成两列**：

```
校准日期        ← calibrationhistory.date        （如 2026-10-01）
最新行情日期     ← 行情快照的 quote_date          （如 2026-09-30）
```

本页把生产端的两张表合并成一张，所以必须靠列名区分开。

#### 0.5.3 最终列（12 列）

```text
基金代码 | 名称 | 跟踪代码 | 仓位 | 校准值 | 校准日期 |
最新行情日期 | 行情更新时间 | 最新价 | 涨跌幅 | 溢价率 | 数据源
```

| 列 | 来源 | 对应生产端 |
| --- | --- | --- |
| 基金代码 | `stock.symbol` | 参考数据「代码」 |
| 名称 | `stock.name` | 参考数据「名称」 |
| 跟踪代码 | `FundProfile.est_symbol`（估值标的） | 对照表「跟踪代码」 |
| 仓位 | `fundposition.close` | 对照表「仓位」 |
| 校准值 | `calibrationhistory.close` | 对照表「校准值」 |
| 校准日期 | `calibrationhistory.date` | 对照表「日期」 |
| 最新行情日期 | `realtimequote.quote_date` | 参考数据「日期」 |
| 行情更新时间 | `realtimequote.quote_time` | 参考数据「时间」 |
| 最新价 | `realtimequote.price` | 参考数据「价格」 |
| 涨跌幅 | `(price/prev_price-1)×100` | 参考数据「涨幅」 |
| 溢价率 | `(price/net_value-1)×100` | 生产端基金页「溢价」 |
| 数据源 | `realtimequote.source` | （本页新增，便于排查） |

#### 0.5.4 实测与生产端逐项吻合

```text
符号        名称                 跟踪   仓位   校准值     校准日期      行情日期      行情时间   最新价   涨跌幅   溢价率   数据源
SZ161130   易方达纳斯达克100LOF  ^NDX   0.95   32.079543  2026-10-01   2026-09-30   15:00:00  4.796   1.46%   5.52%   sina
SZ161125   易方达标普500LOF      ^GSPC  0.95   22.110973  2026-10-01   2026-09-30   15:00:00  3.275   1.08%   4.54%   sina
SH513100   国泰纳斯达克100ETF    ^NDX   0.98   13.942979  2026-10-01   2026-09-30   15:34:59  2.352   1.2%    15.41%  sina
```

生产端 `sz161130cn.php` / `sz161125cn.php` 的参考数据表：

```text
SZ161130  4.796  1.46%  2026-09-30  15:00  纳斯达克100LOF
SZ161125  3.275  1.08%  2026-09-30  15:00  标普500LOF
```

**价格、涨幅、行情日期、行情时间四项全部一致。** 涨跌幅直接复用 PHP
`StockGetPercentage()`（`quotestore.stock_get_percentage`），不另立算法。

#### 0.5.5 途中修掉的两个真问题

**① 页面读不到已存的行情（最重要）**

`QuotesUpdater.get()` 的 TTL 默认只有 **60 秒**，是给"要不要重新抓"用的。
列表页原先用它取行情，于是**只要抓取进程没在跑，行情列就全空白** ——
而库里其实存着上一次抓到的行情（实测 `realtimequote` 里有 2026-10-03 抓的
SZ161130/SZ161125）。

修法：新增 `QuotesUpdater.get_last_stored()`，读 `realtimequote` 里那条快照
**不判 TTL**；页面改用它，并保留 `get()` 作为没有 store 时的回落（测试替身、
不落库的部署仍照旧工作）。

**② MySQL `TIME` 列取回来是 `timedelta`**

`quote_time` 在库里是 MySQL `TIME` 类型，PyMySQL 取回来是
`datetime.timedelta(seconds=54000)`。直接 `str()` 恰好得到 `"15:00:00"` 看似没问题，
但一旦跨天就会变成 `"1 day, 0:00:00"` 这种不能显示给用户的文本。

修法：新增 `_normalize_quote_time()` 显式换算（并处理 `time`/`datetime`/字符串
三种形态），`quote_date` 也走 `_normalize_quote_date()`。同时把行→对象这段映射
抽成 `_quote_from_row()`，两个读取入口共用，避免以后加字段时漏一处。

回归测试：`test_quoteupdater.StaleQuoteReadTest`（6 个）+
`test_web` 的 4 个表头/列值用例。


### 0.1 问题

页面 `/fund/qdiilof` 上 SZ161130 仓位 0.95、SZ161125 却是 **1.0**。
但 PHP 官网明写的是 0.95 ——
`https://palmmicro.com/woody/res/sz161125cn.php` 页面上有一句：

> 仓位估算**值使用0.95**

### 0.2 根因（两处独立错误叠加）

**① 代码：`get_position()` 把默认值硬编码成 1.0**

PHP 的做法（`php/stock/mysqlref.php` 构造函数）：

```php
$this->fRatio = ($fRatio = $pos_sql->ReadVal($strSqlId)) ? $fRatio : $this->GetDefaultPosition();
```

而 `GetDefaultPosition()`（`php/stock/stocksymbol.php:1001`）是：

```php
function GetDefaultPosition() { return $this->IsLofA() ? 0.95 : 1.0; }
```

Python 侧原本写的是 `return float(value) if value is not None else 1.0` ——
**没有按符号类型回落**。受影响的不止 SZ161125：任何库里没有 `fundposition`
行的 LOF 都会拿到 1.0（实测 `SZ162411`、`SH501018` 都中招）。

**② 数据：种子数据显式把 SZ161125 写成了 1.0**

即使修好代码，库里那一行 `fundposition(id=2, close=1.0)` 仍然会优先返回 1.0。
所以种子里 `POSITIONS["SZ161125"] = 1.00` 也是错的。

### 0.3 修法

| 位置 | 改动 |
| --- | --- |
| `stockdata.py` `MySqlFundDataSource.get_position()` | 无行时回落到 `StockSymbol(symbol).get_default_position()` |
| `stockdata.py` `MemoryFundDataSource.get_position()` | 同样回落（保持两个实现一致） |
| `dbtools/seed_demo_data.py` `POSITIONS` | `SZ161125` 改为 `0.95` |
| 数据库 | `UPDATE fundposition SET close=0.95 WHERE id=2`，随后重标定 |

修后实测：

```text
符号        仓位    官方净值    静态估值      带中间层估值
SZ161130   0.95   4.5452    4.545199    4.545199    一致
SZ161125   0.95   3.1328    3.132797    3.132797    一致
SH513100   0.98   2.0379    2.037898    2.037898    一致
```

回归测试 `test_stockdata.PositionDefaultTest`（3 个）锁住这个行为。

### 0.4 校准值为什么显示 1.0（已修）

页面「校准值」列原来恒为 **1.0**。原因**确实是种子数据**，但根源是我选的归一化口径。

**① 生产口径（官网可验证）**

官网 QQQ 页面（`mystockcn.php?symbol=QQQ`，无需登录）直接给出：

| 项目 | 生产值 |
| --- | --- |
| QQQ 校准值 | **41.111**（2026-10-02） |
| QQQ 仓位 | 1 |
| QQQ 净值历史 | 749.3773（10-02）、739.6175（09-30）、737.9142（09-29）|
| ^NDX 价格 | 30807.93（10-02） |

把它代入题目里那条式子（`php/stock/qdiiref.php:3` 的 `QdiiGetCalibration`）：

```text
校准值 = 源值 × 汇率 / 净值 = ^NDX / QQQ净值 = 30807.93 / 749.3773 = 41.1114  ✓ 完全吻合
```

**关键点：生产把「ETF 真实净值」存进了 `netvaluehistory`**（749.3773），
不是归一化的 1.0。

**② 我原来的口径**

为了省事，我把中间层归一化了：中间层 `netvalue = 1.0`、`C_m = 1/est ≈ 0.001334`，
于是基金层校准值必然退化为 `1.0`。**这不是计算错误，是口径选择的必然结果。**

**③ 修法：改用生产口径的等价式**

```text
C_f = N_f × cny / p_f
```

它与 `源值×汇率/净值` 等价（推导：完整链路里 `est_value = E_m·C_m/(cny·p_m)`，
代入 `N_f·C_f/p_f = E_m·C_m/(cny·p_m)` 即得）。修后：

```text
符号        仓位    净值      校准值（修前 → 修后）
SZ161130   0.95   4.5452   1.0 → 32.079543
SZ161125   0.95   3.1328   1.0 → 22.110973
SH513100   0.98   2.0379   1.0 → 13.942979
QQQ        1      1.0      0.001334 → 0.008945
SPY        1      1.0      0.001299 → 0.008712
```

**估值不受影响**（仍等于官方净值）—— 因为 `hedge_value` 是从基金层自己的
`N_f × C_f / cny` 算出来的，两者同步变化后互相抵消。

顺带清掉两类垃圾数据（`_clean_stale_calibration.py`）：

* **陈旧校准行**：种子留下的 `2026-01-02`、中间层多余的 `2026-10-02` ——
  它们让 `get_calibration_now()`（取最新日期）读到旧值，重置脚本刚写的行"看不见"；
* **钳制脏值** `9999999.999999`：早期迭代求解失败时被上限截断写进库的。

**④ 一个仍未完全对齐的点（如实说明）**

生产 `calibrationhistory` 的**绝对量级**我仍未复现：

```text
生产：SZ161125 校准值 16505.969（净值 3.1328）  SPY 校准值 10.037
我们：SZ161125 校准值 22.110973                SPY 校准值 0.008712
```

而且生产满足的不变式 `hedge = (C_f/p_f)/(C_m/p_m) ≈ 1`，
在我们的种子里**不成立**（比值约 3775）。

原因：中间层 `netvalue`/`C_m` 的**配对约定**与生产不同 ——
生产用「ETF 真实净值 + 指数点位/净值」，我用「归一化 1.0 + cny/源值」。
两种约定各自自洽（估值都对），但**不能跨约定比较绝对数值**。

要完全对齐只有两条路：拿到生产库导出，或把种子的中间层改成生产约定。
在此之前，**页面上的校准值是有意义的相对量，但不是生产口径的绝对值** ——
不要拿它去和生产库比对。这点必须记着，免得下次又被当成 bug 追。

## 一、名称纠错

### 1.1 错误现象

页面 `/fund/qdiilof` 上：

```text
SZ161130   易方达标普信息科技LOF     ← 错！这是 SZ161128 的名字
```

### 1.2 权威值核对

| 代码 | 权威名称（新浪 ``f_`` 接口） | 深交所 |
| --- | --- | --- |
| 161130 | 易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额) | 纳斯达克100LOF |
| 161125 | 易方达标普500指数(QDII-LOF)A(人民币份额) | 标普500LOF |
| 513100 | 国泰纳斯达克100(QDII-ETF) | —— |
| 161128 | 易方达标普信息科技指数(QDII-LOF)A(人民币份额) | —— |

净值也互相印证：161130 = 4.5452、161128 = 6.9694，是两只不同的基金。

### 1.3 错在哪

**根因是种子数据写错**，不是代码逻辑问题：

```python
# python/dbtools/seed_demo_data.py（修前）
STOCKS = [
    ("SZ161130", "易方达标普信息科技LOF"),   # ← 错，这是 161128 的名字
    ...
]
```

`seed_demo_data.py` 按符号 UPSERT 名称，所以这个错名被写进了 `stock.name`，
页面直接显示它。同文件在 `web/app_demo.py` 里也有一份同样的错值。

值得一提的是：**新浪一直返回正确的名称**，只是从没人拿它去核对库里的值。

### 1.4 改进（两层）

**① 修数据**：`seed_demo_data.py` 与 `app_demo.py` 改成正确短名，
并加注释说明易错点：

```text
SZ161130 -> 易方达纳斯达克100LOF
SZ161125 -> 易方达标普500LOF
SH513100 -> 国泰纳斯达克100ETF
```

**② 加机制**（这才治本）：`NavUpdater.sync_names()` 用权威源核对 `stock.name`。

```powershell
python -B auto\cli\navcli.py --check-names    # 只报告，不改库
python -B auto\cli\navcli.py --sync-names     # 不一致时改成权威全称
```

**比对用的是"关键片段包含关系"而不是字符串相等**，因为：

* 权威源给的是**全称**（`易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)`），
  页面列窄、放不下，库里存的是简洁短名；
* 只要短名的主要部分能在全称里找到就算相容；
* 反过来，短名说的是**另一只基金**（把 161130 写成"标普信息科技"），
  关键片段就对不上 → 判为不一致。

实测这个机制精确抓到了本次的错误，另两只判为一致：

```text
$ python -B auto\cli\navcli.py --check-names
符号         代码       库里的名称              权威名称                       结果
SZ161130   161130   易方达标普信息科技LOF       易方达纳斯达克100ETF联接(...)    不一致   ← 抓到了
SZ161125   161125   易方达标普500LOF         易方达标普500指数(...)          一致
SH513100   513100   国泰纳斯达克100ETF       国泰纳斯达克100(QDII-ETF)      一致
```

修完之后再核对，三只全部一致。

## 二、汇率接入估值

### 2.1 汇率在估值里干什么

QDII 的中间层标的（QQQ 749.58）是**美元**计价，基金净值是**人民币**计价。
`fundcalc.calc_with_calibration()` 里靠汇率换算：

```text
est_f = (1 - p_f) * nv_f + p_f * est_value * cny / cal_f
                                            ↑
                                      这个 cny 就是汇率
```

对应 PHP `php/stock/qdiiref.php:126`：

```php
$strOfficialCNY = $cny_ref->GetClose($strDate);   // 取当天 USD/CNY
$this->fOfficialNetValue = $this->GetQdiiValue($this->_getOfficialEstVal($strDate), $this->strOfficialCNY);
```

### 2.2 修前的状态：汇率恒为 1.0

`stockdata._get_default_cny_est()` 会去读 `USCNY`，但：

1. **库里根本没有 `USCNY` 这个符号** → 直接返回 `None`；
2. 即使补上符号，它查的是 **`stockhistory`**，而汇率（PHP 与我们的实现）写在
   **`netvaluehistory`**（`CnyReference` 读的就是后者）；
3. `_get_daily_close()` 是**精确日期**查询，我却当"往前回退"用 ——
   汇率周末不更新，日期对不上就永远取不到。

结果 `cny = 1.0`，**等于把美元当人民币用**，QDII 估值差约 7 倍。

### 2.3 修法

| 层 | 改动 |
| --- | --- |
| 新模块 | `forexsource.py`：汇率源抽象（`sina` / `chinamoney` / `fixed` / `crosscheck`） |
| 新模块 | `forexupdater.py`：抓汇率写 `netvaluehistory`（对应 `GetChinaMoney()`） |
| 新 CLI | `forexcli.py`：`--check` / `--compare` / `--api` / 写入 |
| 数据 | 种子加入 `USCNY` / `HKCNY` / `EUCNY` / `JPCNY` 符号 |
| `stockdata.py` | `_get_default_cny_est()` 改为 **netvaluehistory 优先**，并用 as-of 查询回退 |
| `stockdata.py` | 新增 `_get_daily_close_asof()`（明确的"该日期或之前最近一天"语义） |
| `stockdata.py` | `_attach_hedge_value()` 的贡献值改为 `nv_f * cal_f / cny` |
| `quotestore.py` | `write_net_value(..., allow_weekend=True)` —— 汇率周末有报价 |
| `_reset_est_scale.py` | 校准值公式带上汇率：`cal_m = nv_f·cal_f / (cny·est_m·p_m)` |

### 2.4 两个容易错的地方

**① 贡献值必须先除掉汇率。**

`_attach_hedge_value()` 算的 `est_value` 后面在公式里**还要乘一次 cny**，
所以要先除：

```text
est_value = nv_f * cal_f / cny      ← 先除
est_f = ... + p_f * est_value * cny / cal_f   ← 后面又乘回来
```

不除的话汇率被算两遍，**估值放大 7 倍**。

**② 汇率不能被"周末守卫"拦掉。**

`write_net_value()` 原本拒绝周末日期（基金净值周末不发布，这是对的），
但**外汇市场周末有报价** —— 实测把 HKCNY / EUCNY / JPCNY 全拦了。
所以加了 `allow_weekend` 参数，汇率传 `True`。

### 2.5 双源校验

与净值同样的思路，新浪为主、中国货币网为对照，**用相对差**（默认 1%）而不是
绝对值（汇率是 6~7 量级的数）：

```text
$ python -B auto\cli\forexcli.py --compare
符号       主源汇率           对照源汇率          相对差          结论
USCNY    6.705          6.7351         0.4469%      一致
HKCNY    0.8544890583   0.85842        0.4579%      一致
EUCNY    7.5406         7.6215         1.0615%      差异超容差   ← 抓到
JPCNY    0.0424798009   0.042727       0.5786%      一致
```

欧元那个 1.06% 的差异是真实的：新浪给的是**即期实时价**，中国货币网给的是
**上一交易日的中间价**，两者本来就会差一点。容差可用
`PALMMICRO_FOREX_TOLERANCE` 调整。

### 2.6 验证结果

汇率（真实抓取，2026-09-30/10-01）：

```text
USCNY  7.1 → 6.705    HKCNY  0.91 → 0.854489
EUCNY  7.7 → 7.5406   JPCNY  0.047 → 0.04248
```

估值（含汇率）：

```text
符号        官方净值    静态估值      带中间层估值   结论
SZ161130   4.5452     4.545200     4.545200     一致
SZ161125   3.1328     3.132797     3.132797     一致
SH513100   2.0379     2.037899     2.037899     一致
```

`SZ161130` 行的 `CNYest = CNY = 7.1`、`hedge_value = 0.640169`
（= 4.5452 / 7.1），链路完整。

## 四、汇率历史序列（第 3 项）

### 4.1 先纠正一个数据问题：`7.1` 不是任何一天的真实数据

用户质疑得对。查清如下：

```text
USCNY 的全部记录
   2026-01-02   7.1     ← 我在**种子数据**里手填的占位值，不是真实汇率
   2026-10-01   6.705   ← 真实（新浪）
```

而基金的官方日期是 `2026-09-29`，那天**没有**汇率 → as-of 回退命中了
`2026-01-02` 的占位值 7.1，**相差 270 天**。

后果：用 1 月的假汇率配 9 月的净值，估值口径是错的（虽然数值看起来"能算"）。

**三层修复**：

1. **清掉假占位**：`forexcli.py --purge-fake`，并给存储过程加了专门的清理函数；
   种子里的占位值也从 `7.1` 改成量级正确的 `6.70`，并注明"会被真实抓取覆盖"；
2. **统一日期**：`_reset_est_scale.py` 默认取"**已有汇率的最近一天**"作为统一日期，
   让净值 / 校准值 / 汇率三层对齐到同一天（实测对齐到 `2026-10-01`，
   `CNY = 6.705` 精确命中，不再回退）；
3. **加陈旧度警告**：`stockdata._note_rate_age()` 在回退取汇率时记录日期差，
   超过 `MAX_RATE_FALLBACK_DAYS = 10` 天就写进 `source.rate_warnings`，
   由校验工具打印。这样"看起来能算、其实口径错"的情况能被看见：

```text
SZ161130：2026-09-29 没有汇率，回退用了 2026-01-02 的值（相差 270 天，超过 10 天阈值）
```

修完之后警告数为 0。

### 4.2 历史回补：**找到了可用接口**（国家外汇管理局）

#### 4.2.1 先说清我之前的错误

上一轮我说"上游接口不可用"，但那四个接口里**只有一个是 PHP 项目的**：

| 接口 | 来源 |
| --- | --- |
| `chinamoney.com.cn/.../ccpr.json` | ✅ **PHP 项目的**（`php/externalurl.php:349`） |
| `push2his.eastmoney.com` K 线 | ❌ 我自己想出来的 |
| 新浪 `ForexService.getDayKLine` | ❌ 我自己想出来的 |
| 腾讯 `web.ifzq.gtimg.cn` | ❌ 我自己想出来的 |

而且 PHP 项目**根本没有汇率历史**：`GetChinaMoney()` 只取 `lastDate` 当天的中间价
写一次；另一条 `IsEastMoneyForex()` 路径的源码注释写着
"Only WX call may into this, nothing need to be done"（WX = 微信公众号机器人，
见 `weixin.php`），本质仍是从库里读。

**最关键的失误**：我没有想到用成熟的数据包，而是自己去逆向第三方 API。

#### 4.2.2 按建议试了数据包

| 包 | 结果 |
| --- | --- |
| **akshare** | 要求 **Python >= 3.11**（实测 1.19.1 的 `requires_python`），本项目运行时是 **3.7**，装不上；pip 的临时目录又在本沙箱外被拒 |
| **baostock** | 查了它的接口面：**只做 A 股**（K 线/财务/指数），**没有汇率接口**，解决不了这个问题 |
| tushare | 环境里已装但是 1.4.7 旧版，只有 `get_deposit_rate` / `get_loan_rate`，无汇率 |

**但 akshare 装不上 ≠ 用不上它**：我把源码包下载下来
（`.aksrc/akshare-1.19.1/`，需 Python >= 3.11 才能**运行**，但源码可**读**），
读它对汇率历史的实现，找到了真正可用的接口：

```text
akshare/currency/currency_safe.py  →  currency_boc_safe()
POST https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do
     startDate / endDate / queryYN=true
```

响应是 HTML 表格：`日期 | 美元 | 欧元 | 日元 | 港元 | 英镑 | ...`（26 列）。

#### 4.2.3 实测结果（真实数据）

```text
日期         美元      欧元      日元       港元
2026-09-30  673.51   762.15   4.2727   85.842
2026-09-29  674.11   764.94   4.275    85.935      ← 基金官方日期，有真值
2026-09-28  673.99   765.64   4.2685   85.923
```

按 100 外币挂牌，除以 100 得到"每 1 外币 = 多少人民币"：
`674.11 / 100 = 6.7411`；日元同样是 100 日元标价（`4.275 / 100 = 0.04275`）。

已实现并注册 `SafeForexSource`（`safe`）：**一次请求取回整张表**、四币种共享。
回补实测写入 **244 条**，每币种 62 天：

```text
USCNY  62 条   2026-07-07 ~ 2026-10-01
HKCNY  62 条   2026-07-07 ~ 2026-10-03
EUCNY  62 条   2026-07-07 ~ 2026-10-03
JPCNY  62 条   2026-07-07 ~ 2026-10-03
```

分工：`sina` / `chinamoney` 负责**实时**报价，`safe` 负责**历史**序列。

### 4.3 修好之后的状态

```text
SZ161130: 日期=2026-10-01 CNYest=6.705 CNY=6.705 hedge_value=0.677882
汇率回退警告数: 0

符号        官方净值    静态估值      带中间层估值   结论
SZ161130   4.5452     4.545199     4.545199     一致
SZ161125   3.1328     3.132797     3.132797     一致
SH513100   2.0379     2.037898     2.037898     一致
```

USCNY 历史（外汇管理局真实中间价）：

```text
2026-09-24  6.7489
2026-09-28  6.7399
2026-09-29  6.7411      ← 基金官方日期
2026-09-30  6.7351
2026-10-01  6.705       ← 用户确认的正确值
```

### 4.4 三条落地路径（现在都成立）

1. **历史回补（新，已可用）**：`forexcli.py --backfill` → 外汇管理局，
   一次补齐 90 天；
2. **持续积累（主路径）**：把 `forexcli.py` 放进定时任务每天抓一次；
3. **官方日期补汇率**：`--fill-official-dates`，取之前最近一条**真实**汇率且差距
   ≤ 5 天才填；有了历史序列后这条基本用不上，保留作兜底。

### 4.5 外部依赖说明

`.aksrc/` 是下载的 akshare 源码包，**仅用于查阅接口实现，不参与运行**。
项目运行不依赖 akshare，需要的接口已用 `requests` 自行实现，
以保持"汇率源可替换"的设计。若以后升级到 Python 3.11+，可以选择直接依赖它。

## 四点五、参考数据表外汇实时快照的更新链路（2026-10-19）

### 4.6 问题现象

用户对照生产端 `sz161130cn.php` 发现参考数据表里两行外汇停在 10-15：

| 行 | 生产端 | 本地（修复前） |
| --- | --- | --- |
| USDCNH 离岸 | 6.6813 −0.06% **2026-10-19** 00:15 | 6.6911 −0.07% **2026-10-15** 06:24 |
| USDCNY 在岸 | 6.6919 −0.07% **2026-10-19** 00:15 | 6.6919 −0.09% **2026-10-15** 06:23 |
| USCNY 中间价 | 6.7208 −0.02% 2026-10-19 09:15 | 6.722 0 2026-10-16 09:15 |

同时**五档交易表少了「参考溢价」列**（生产端有官方/参考/实时三列）。
用户早上跑过 `forexcli.py --backfill`，输出只有「汇率源」一行就没了
（外汇管理局慢，没等完；重跑实测写入 236 条、耗时约 1-2 分钟）。

### 4.7 根因：两条链路，只修了一半

外汇数据在 PHP 里本来就是**两条独立的链路**，迁移时第二条没有接线：

1. **USCNY 中间价** → `netvaluehistory`（PHP `chinamoney.php` 的正路，
   `forexcli` 主路径已覆盖）—— 用户跑 backfill 后这条已经到 10-19；
2. **USDCNH / USDCNY 实时价** → 只进内存/快照（PHP `LoadSinaForexData`，
   `stockref.php:493`，对应 `IsSinaForex` 即 `fx_` 前缀符号）。

第二条在 Python 侧没有常驻更新：`quoteupdater` 的符号集合从
`stockhistory` 反推，**外汇没有日线历史 → 永远进不去列表**，快照一直停在
`seed_sh_hedge.py` 手写那天（10-15）。参考数据表的「日期/时间」列直接读
`realtimequote.quote_date/quote_time`（README §0.5 的映射表），于是比
生产端落后 4 天。

「参考溢价」列不显示也与本数据相关：PHP 的放行条件是
`IsEtfA() || cny_date != official_date || mid_date != official_date`
（`qdiiref.php:161-172`）。USCNY 中间价补到 10-19 后
`cny_date(10-19) != official(10-16)` 条件成立，fair 有值、参考列自然恢复
—— **不用改任何估值代码**（实测 `EST4.365|4.364|4.419` 三列齐）。

### 4.8 修法

* `updater/forexupdater.py`：新增 `PAGE_FOREX_SYMBOLS = ("fx_susdcny",
  "fx_susdcnh")` 与 `update_page_forex()` —— 直接走新浪（crosscheck 的
  对照源没有离岸/在岸实时代码），写 `realtimequote` 快照；
* `source/forexsource.py`：`SinaForexSource.fetch_rate()` 增加返回 `prev`
  字段（`fields[3]`，照 PHP `$ar[3]`）。页面「涨幅」列靠
  `(price - prev) / prev` —— 之前快照的 `prev_price` 恒为空串，涨幅常年为 0。
  **注意 PHP 时代的 `[3]` 是昨收、当前新浪布局里 `[3]` 是当日最高**，
  生产端 USDCNH 的 −0.06% 正是 `(6.6809−6.685)/6.685` 算的，照抄不"纠正"；
* `cli/forexcli.py`：默认 `update()` 之后顺手调 `update_page_forex()`；
  新增 `--fx-only` 只刷实时快照（不碰中间价历史）；`--check` 增加
  `sina(rt)` 两行便于诊断。

### 4.9 每日例程中的位置

* **凌晨窗口**（quotecli 之后）：`forexcli.py --fx-only` —— 只刷两个实时
  快照，快（两次 HTTP），不重复跑中间价那套慢请求；
* **晚间窗口**（backfill_nav_history 之后）：完整 `forexcli.py`。

### 4.10 验证

* 单测：`test_forexsource.py` 新增 18 字段布局解析（prev=6.685）与
  `ForexPageForexUpdateTest` 6 例（快照写入含 prev、缺符号报错、
  源异常不抛、无快照存储报错、dry-run 只计数、不进 netvaluehistory）；
  全量 auto 1257 例 OK；
* 端到端：`--fx-only` 写入 2 条；页面三行外汇全部变为 10-19
  （USDCNH 6.6812 −0.06% / USDCNY 6.6829 −0.06% / USCNY 6.7208 09:15），
  五档表恢复「官方 | 参考 | 实时」三列溢价。数字与生产端的微小差异是
  **抓取时点**（本地 12:42 vs 生产端凌晨 00:15），量级与口径一致。

## 五、命令与测试

```powershell
cd F:\woody\woodyweb\python

python -B auto\cli\forexcli.py --compare              # 双源汇率对比
python -B auto\cli\forexcli.py                        # 抓当日汇率写库（主路径）
python -B auto\cli\forexcli.py --fx-only              # 只刷参考数据表 USDCNH/USDCNY 实时快照
python -B auto\cli\forexcli.py --purge-fake           # 清掉手工占位汇率
python -B auto\cli\forexcli.py --fill-official-dates  # 给官方日期补汇率
python -B auto\cli\forexcli.py --backfill             # 历史回补（外汇管理局，可用）
python -B auto\cli\forexcli.py --api                  # 汇率源接口说明（换源参考）

python -B auto\cli\navcli.py --check-names            # 名称核对
python -B auto\updater\estcalibration.py --diagnose       # 中间层尺度诊断
python -B auto\_reset_est_scale.py --apply        # 重标定（含汇率与统一日期）

python -B -m unittest discover -s auto -p "test_*.py"       # 377 个
python -B -m unittest discover -s web\tests -p "test_*.py"  # 36 个
```

`test_forexsource.py`（58 个）覆盖：汇率源注册与递归防护、新浪两种字段布局、
中国货币网 `100JPY` 除 100、日期不能把数字当日期、双源一致/超容差告警、
主源回落、**汇率周末可写**、官方日期补汇率的 **5 天阈值**、清占位、
汇率**陈旧度警告**、**外汇管理局表格解析**（四币种 ÷100、行序排序、
单次请求四币种共享）、**18 字段布局的 prev 字段**（`fields[3]`，照 PHP），
以及 `update_page_forex()` 的 6 个用例（见 §四点五）。

## 六、仍未完成

1. **中间层校准值仍需手动重标定**：还没像 PHP `DailyCalibration()` 那样
   进每日流程自动重算，目前靠 `_reset_est_scale.py`。
2. **`estquote` 只覆盖 QQQ/SPY**：其余 16 个中间层标的要等行情抓过才出现。
3. **历史回补默认只到 90 天**：可调 `backfill_history(days=...)`；
   更早的历史要靠持续积累。
4. 功能 B（管理端日线历史）与上交所接口按此前决定暂缓。
