# 外盘期货与实时 EST 迁移

把 PHP 里"实时 EST = 参考 EST + 期货"这条路迁到 Python。

> **国内商品期货（`nf_AU0` / `nf_AG0` / `nf_M0`）不在这条链路里** ——
> 那一族走普通行情抓取 + `FundPairReference`，没有汇率、只有一层配对、
> 官方 EST 取均价。见 `README_china_future_migration.md`。
> 两族的区别只有一句话：`hf_` 是"指数 → 期货"的两层链路，`nf_` 是
> "基金 → 期货"的一层链路（`futurespair` 表**不要**写 `nf_`，写进去会
> 污染 `futurescli` 的反推名单）。

| PHP | 本版 |
| --- | --- |
| `php/stock/stockref.php:447` `LoadSinaFutureData()` | `futuresquotes.SinaFuturesClient` |
| `php/stock/stockref.php:413` `_onSinaFuture($ar)` | `futuresquotes.parse_futures_payload()` |
| `php/stock/stockref.php:235` `CheckAdjustFactorTime()` | `futurescalib.check_adjust_time()` |
| `php/stock/fundpairref.php:174` `AverageCalibraion()` | `futurescalib.FuturesCalibrator.calibrate()` |
| `php/stock/fundpairref.php:179` `CalcFactor()` | `FuturesCalibrator.calc_factor()` |
| `php/stock/qdiiref.php:161` `EstRealtimeNetValue()` | `fundestcalc.compute_realtime()` |

新增文件：

* `python/auto/source/futuresquotes.py` —— 期货行情抓取与解析
* `python/auto/calc/futurescalib.py` —— 期货配对、校准、实时 EST 取值
* `python/auto/cli/futurescli.py` —— 命令行（抓取 / 校准 / 显示）
* `python/auto/test_futures.py` —— 36 个测试
* `python/dbtools/seed_futures_pair.py` / `calibrate_futures.py` —— 配对与校准数据
* `futurespair` 表（`schema.sql`）

## 一、先说一件重要的事：PHP 里**没有**可照抄的实现

作者 2020-09-15 与 2024-04-19 两篇文档都提到实时 EST 用 NQ 期货，
**但本工作区的 PHP 快照里没有把期货接上去**：

```text
$ grep -rn "FundPairReference(" php/
php/stock.php:319        new FundPairReference($strSymbol)      ← 无 callback
php/stock/holdingsref.php:67   new FundPairReference(...)       ← 无 callback
php/stock/qdiiref.php:246      new FundPairReference(...)       ← 无 callback
php/stock/qdiiref.php:262/277/292  new FundPairReference(...)   ← 无 callback
```

`FundPairReference::GetRealtimeNetValue()` 只有在 `realtime_callback` 非空时才返回值，
而六处构造**都没传**。所以在这个快照里 `GetRealtimeNetValue()` 恒返回 `false`，
实时 EST 也不会显示。

**结论：本步是按文档实现，不是照抄代码。** 依据是：

1. 作者的两句原话（"实时 EST 则是在参考 EST 的基础上，叠加了 NQ 期货实时数据的影响"）；
2. `EstRealtimeNetValue()` / `GetRealtimeNetValue()` / `EstFromPair()` 的现成结构；
3. 生产端页面上确实显示着实时 EST（说明真实部署里有这套接线）。

## 二、期货行情怎么抓（实测确认）

### 2.1 用裸符号，不要走 `get_sina_symbol()`

这是一个**真 bug**：`StockSymbol('hf_NQ').get_sina_symbol()` 返回 `gb_hf_nq`，
新浪当成美股代码、返回空。

原因在 `GetSinaSymbol()` 的最后一句——给所有非 A/H/指数符号加 `gb_` 前缀。
**PHP 与 Python 都有这个兜底，所以 PHP 的期货取数也没走它**：
`LoadSinaFutureData()` 用的是 `DebugGetSinaFileName($strSymbol)`，文件名就是裸符号。

新增 `futuresquotes.get_quote_symbol()`：期货返回裸符号，其余照常。
`collect_symbols_from_source()` 也改用它（否则行情抓取进程永远抓不到期货）。

### 2.2 实测响应与字段

```text
GET https://hq.sinajs.cn/list=hf_NQ          （Referer 必须带 finance.sina.com.cn）
    var hq_str_hf_NQ="31365.390,,31349.500,31350.000,31521.000,31143.000,
                      23:44:39,31483.250,31510.000,0,3,30,2026-10-07,纳斯达克100指数期货,0";
```

15 个字段，与 PHP `_onSinaFuture($ar)` 的映射**完全一致**：

| 下标 | 含义 |
| --- | --- |
| `0` | 最新价 |
| `4` / `5` | 最高 / 最低 |
| `6` | 时间 `HH:MM:SS` |
| `7` / `8` | 昨收 / 开盘 |
| `12` | 日期 |
| `13` | 名称（GB18030 编码） |

PHP 判 `count($ar) >= 13`，本版同。

## 三、配对与校准

### 3.1 配对表

```
^NDX    -> hf_NQ    纳斯达克100   → NQ 期货
^GSPC   -> hf_ES    标普500       → ES 期货
^HSI    -> hf_HSI   恒生指数      → 恒指期货      （2026-11-03 补）
znb_NKY -> hf_NK    日经225       → 日经期货      （2026-11-03 补）
GLD     -> hf_GC    金价ETF       → 纽约黄金      （2026-11-03 补，**两级链路**）
```

存在 `futurespair` 表（`id` = 指数，`stock_id` = 期货 —— 与 PHP `PairSql` 的列语义一致）。
表里查不到时退回 `futurescalib.FALLBACK_FUTURES`，**避免少一行配置就整条静默失效**。

**两张表都要有**，方向一致但用途不同：`fundpair` 是 PHP `FundPairReference`
走的（页面「参考数据」表那行期货 + 实时 EST 列出不出现），`futurespair` 是本项目
`fundestcalc` 走的（数值），而且 `quoteupdater` 靠它反推"该抓哪些期货"。
一次性补齐港股 / 日经用 `dbtools/patch_hk_jp_futures.py`，黄金用
`dbtools/patch_gold_futures.py`。

**恒生科技 `^HSTECH` / 国企 `^HSCE` 没有期货**：新浪 `hf_HSTECH` /
`hf_HSCEI` / `hf_HTI` / `hf_HHI` 实测全部空串，生产端
`sh513010cn.php` / `sh510900cn.php` 也没有实时EST 列。别补配对。

#### 黄金为什么键挂在 ETF 上，校准值还是 10.94

黄金基金多一跳：**基金 → GLD（美股 ETF）→ hf_GC（纽约黄金期货）**。
`fundestcalc._index_symbol()` 返回的是基金档案的 `est_symbol`，而黄金基金的
`est_symbol` 就是 `GLD`（`fundestmap`），所以配对键必须是 `GLD`，不能写成
某个指数。`cal` 在这个位置上不是"期货 / 指数"（≈1.0），而是**单位换算**：

```text
10.9441  ≈  3957 / 361.55
黄金期货按「美元/盎司」报价（≈3935），GLD 按「约 1/10 盎司份额」报价（≈361.65）
```

来源是生产端 `sz161116cn.php` 的「基金指数对照表」（实测值，2026-11-02）。
`realtime_source_value()` 的 `期货价 ÷ cal` 正好把这个换算吃掉，得到 GLD 价空间，
再与 `_index_reference_price('GLD')`（GLD 快照价）比对做量级保险 ——
两者相差 0.6%，`MIDDLE_VALUE_TOLERANCE = 0.5` 是 50%，安全。

**这个 cal 不会污染 `est_cal`**：`est_cal` 走
`fundestcalc._est_quote_ref()`，取的是 GLD 的**行情收盘价 / 净值**，
与 `calibrationhistory` 里 GLD 那一行无关。所以可以放心把 10.94 写进去。

### 3.2 校准：两种方式

**① PHP 原逻辑（时刻对齐）**——`check_adjust_time()` + `calibrate()`：

```text
校准值 = 期货价 × 汇率 / 指数价        （CalcFactor）
```

要求期货与指数的**日期与 HH:MM 都相同**（只有两个市场同时收着盘，
`期货 / 指数` 才是干净的比值）。写入用**平均**（`WriteDailyAverage()`）。

**② 本项目替代（日期对齐）**——`calibrate_from_proxy()`：

写这段时 `^NDX` / `^GSPC` 在库里**没有行情快照**（点位从新浪直接抓、不落
`stockhistory`），所以严格时刻对齐永不成立。改用**中间层 ETF 反推指数**：

```text
指数点位 = ETF价 / ETF的校准值       （实测恒等式：30807.93 / 749.3773 = 41.111 = QQQ 校准值）
⇒ 校准值 = 期货价 × 汇率 / (ETF价 / cal_m)
```

只要求**日期相同**。

### 3.3 实测校准结果（2026-10-07）

```text
指数       期货       ETF  ETF快照价    反推指数点位   校准值
^NDX     hf_NQ    QQQ  756.7999    84605.91     0.37090433
^GSPC    hf_ES    SPY  776.09      89082.87     0.08804296
```

自洽性检查：`期货价 / 校准值 = 反推指数点位` ✓
（`31365.06 / 0.37090433 = 84562`，与 84605.91 的差异来自两次抓取之间的价格变动。）

### 3.4 例外：换算族（`nf_AG0 ← hf_SI`）**不进**这两个循环

`FUTURES_VALUE_TRANSFORMS` 里的配对两端**量纲不同**（美元/盎司 vs 元/千克），
`期货 / 指数` 没有意义：

* **校准不做**：PHP `MyPairReference::__construct` 只在 `IsSinaFutureUS()` 时调
  `AverageCalibraion()`（`fundpairref.php:160`），国内期货那一支走净值日线的
  `DailyCalibration()`。硬算得到 ≈0.004，`--apply` 一跑就污染 `calibrationhistory`。
* **快照也不刷**：`RealtimeQuoteStore.save()` 是整行 UPSERT，不给 `vwap` /
  `orderbook` 就写空值 —— 而 `realtimequote.vwap` 是商品期货基金「官方 EST」
  取均价的来源（`fundpairref.php:242`）。这类符号的快照归 `quoteupdater` 守护进程。

`futurescli._calibratable_pairs()` 负责这个过滤；`_index_future_pairs()` 仍然
包含它（`--show` 要能看到这一对）。成因与实测见
`README_silver_si_migration.md` §九。

## 四、量纲坑（本轮踩到并修掉）

**把期货价直接当"中间层值"用，实时 EST 会大 100 倍**：

```text
实测：实时 EST = 487.68   （应为 4.59）
```

原因：期货价是**指数点位**（≈ 31365），中间层值在 `cal_m` 空间里（≈ 756）。

两者分母都是指数，可以直接换算：

```text
ETF价 = 期货价 × cal_m / cal_futures
实测：31369.43 × 0.008945 / 0.370904 = 756.85  ≈ QQQ 快照价 756.80  ✓
```

修完后：

```text
符号        官方EST      参考EST      实时EST      实时 EST 来源
SH513100   2.057527    2.057527    2.057668    期货 hf_NQ
SZ161125   3.157788    3.157788    3.157190    期货 hf_ES
SZ161130   4.586786    4.586786    4.587085    期货 hf_NQ
```

实时 EST 与官方 EST 相差 0.01% 量级 —— 这正是"期货 vs 现金指数"的基差，
也是生产端 `实时 EST` 该有的表现。

## 五、取数优先级

`compute_realtime()`：

1. **配对期货的实时价**（`futurespair` → `hf_NQ` / `hf_ES`）——
   作者文档里的正解；
2. 中间层的**实时快照价**（`estquote` / `realtimequote`）—— 没有期货配对时的退路。

两者都取不到返回 `None`（不编造）。

## 六、命令行

```powershell
cd F:\woody\woodyweb\python

python -B dbtools\seed_futures_pair.py --apply     # 建配对（一次性）
python -B auto\cli\futurescli.py                    # 抓期货 + 落快照 + 尝试校准
python -B auto\cli\futurescli.py --show             # 看期货行情与校准状态
python -B auto\cli\futurescli.py --est              # 三个 EST（实时用期货）
python -B auto\cli\futurescli.py --api              # 口径说明
python -B dbtools\calibrate_futures.py --apply     # 写期货校准值
```

`quoteupdatercli.py --once` 现在也会抓期货（`collect_symbols_from_source()` 已覆盖），
所以放进定时任务即可持续积累。

## 七、测试

`python/auto/test_futures.py`（36 个），覆盖：

* 期货符号识别与 `get_quote_symbol()`（**含与 `get_sina_symbol()` 的对照**）；
* 字段解析（用 2026-10-07 的真实响应文本，逐项对照 PHP `_onSinaFuture`）；
* 字段不足 13 个返回 `None`；
* `check_adjust_time()` 的日期/时刻闸门（含"秒被忽略"）；
* `calc_factor()` 与 `implied_index_price()`；
* `calibrate()` 的时刻对齐写入与**同一天取平均**；
* `calibrate_from_proxy()` 的日期闸门；
* **`realtime_source_value()` 的量纲换算**（断言结果落在**指数点位空间**，
  与 `est_cal` 同空间，绝不可能是 ETF 价空间的七百多；含"多除一次 cal_m"
  被量级保险拒收的用例，2026-10-20 pitfalls #91）。

全量：`auto` **496 个**、`web` **59 个**。

## 八、仍未完成

1. **原油类的实时 EST**：`hf_CL` 在 `stock` 表里有行（id=15），但 `USO` 没有
   stock 行、也没有校准值，所以原油基金（`SZ163208` / `SH501018` / `SZ160723` /
   `SZ161129`）仍算不出实时 EST。黄金那条两级链路的做法（
   `dbtools/patch_gold_futures.py`）可以照搬：补 `USO` 行 → 配对
   `USO → hf_CL` → 写 `cal = CL价 / USO价`（≈1.7，原油 97 美元、USO 约 57）。
   日经已通（`znb_NKY → hf_NK`），恒指已通（`^HSI → hf_HSI`），黄金已通
   （`GLD → hf_GC`）；恒生科技 / 国企 / 印度 / 欧洲**没有对应期货**，
   按作者文档实时 EST 直接用指数（欧洲三只目前连指数配对都没配，它们本可以
   走 `calibrate_from_proxy` 那条路）。
2. **`AverageCalibraion()` 的自动触发**：PHP 在构造 `FundPairReference` 时
   检查 `CheckAdjustFactorTime()` 并顺手校准。本版做成显式命令
   （`futurescli.py --apply` / `calibrate_futures.py`），还没接进页面访问路径。
3. **`^NDX` / `^GSPC` 的严格时刻对齐**：这两个指数的快照只覆盖美股常规盘
   （`realtimequote` 里是 16:50 收盘价），而期货在盘前盘后都在动，所以日常
   例程里它们**基本对齐不上**，用的仍是 2026-10-14 那两条由
   `calibrate_futures.py` 写下的历史校准值。港股 / 日经没有这个问题
   （指数与期货同时段交易）。**GLD 同理**：它的快照是 16:00 纽约收盘价，
   而 `hf_GC` 的 tick 落在别的分钟，`check_adjust_time()` 永远对齐不上，
   所以 GLD 的 10.9441 是**手写**的（来源见 §3.1），不是自动校准出来的。
   黄金的比值本身很稳（份额比例固定 1/10，只随期货升贴水漂移），
   每天自动更新一次意义不大；真要更新，得等 daemon 在美东 16:00
   那一分钟同时抓到 GLD 与 hf_GC。
