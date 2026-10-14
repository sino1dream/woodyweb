# P0 数据层补齐：计划与进度

> 目标：让演示库的数据获取与落库链路完整跑通，`/qdii` 等页面与生产端
> （<https://palmmicro.com/woody/res/qdiicn.php>）逐格对齐。**准确性以生产端
> 数据为准。** 只做基金相关业务，其余业务暂缓。
>
> 用户 2026-10-14 批准的两点调整：① qdiihk / qdiijp 的 est 符号一并进 P0；
> ② 按生产语义常驻 `quoteupdatercli --daemon`。

## 里程碑

| # | 内容 | 状态 |
| --- | --- | --- |
| P0.1 | 符号清单：基金 + est 符号全集，与生产端页面核对 | ✅ `bfe86469`，本文 §一 |
| P0.2 | 备份并写入 `stock` + `fundpair` | ✅ `69aa649e`，本文 §二 |
| P0.3 | 历史数据回填（`quotecli` → `stockhistory`，`navcli` → `netvaluehistory`） | ✅ `344d5383`，本文 §三 |
| P0.4 | 实时链路（`quoteupdatercli --daemon` → `realtimequote` + 盘口 + `estquote`） | ✅ `05b6f2c3`，本文 §五 |
| P0.5–0.7 | 校准链路 + `fundest` + 期货实时 EST | 待做，两个已定位的坑见 §四.3 |
| P0.8 | 一键重跑 `dbtools/bootstrap_fund_data.py`（默认 dry-run） | 待做 |

P0.5a（修 fundestcalc 中间层基准价尺度错位，EST 放大 4648 倍）已单独提交
`c5f44b69`，见 `README_fundest_migration.md` §十。

---

## 一、P0.1 符号清单

探针：`auto/_scratch/_p0_1_symbol_inventory.py`（只读，不写库）。
映射来源：`core/stocksymbol.py` 的数组 + `calc/fundcalc.py` 的
`qdii_est_symbol()` 族，与 PHP `qdiiref.php:19-37` 的 `QdiiGetEstArray()`
逐条对照过。**杠杆替代**与 `stockdata.py:279-284` 同顺序：
`qdii_qqq_symbols()` 用 `QQQ`、`qdii_spy_symbols()` 用 `SPY` 取数
（指数 `^NDX` / `^GSPC` 不落 `netvaluehistory` / `stockhistory`）。

### 1.1 基金总数

| 数组 | 只数 | 生产端页面 | 页面只数 |
| --- | --- | --- | --- |
| `qdii` | 41 | `qdiicn.php` | 41（逐只同序一致） |
| `qdiihk` | 29 | `qdiihkcn.php` | **38**（29 + 9 只 hkmix，见 §1.4） |
| `qdiijp` | 5 | `qdiijpcn.php` | 5 |
| `qdiieu` | 3 | `qdiieucn.php` | 3 |
| `qdiimix` | 32 | `qdiimixcn.php` | **15**（见 §1.4） |

去重后 **102 只**唯一基金（8 只同时属于 `qdii` 与 `qdiimix`：
SZ159509、SZ159529、SH513850、SZ159577、SH501312、SZ160216、SZ164701、SZ163208）。

### 1.2 演示库缺口（2026-10-14 实测）

| 项 | 数量 | 明细 |
| --- | --- | --- |
| 已在库的基金 | 4 | SZ161130、SZ161125、SH513100、SZ162411 |
| 缺 `stock` 行的基金 | **98** | 其余全部 |
| 缺 `stock` 行的 est 符号 | **20** | AGG、IBB、^DJI、VNQ、RSPH、VGT、XLY、KWEB、XBI、XOP、IXC、IEO、SH000869、^HSI、^HSTECH、^HSCE、znb_TPX、znb_NKY、znb_CAC、znb_DAX |
| 需要 `fundpair` 行 | **70** | 每只"有 est 的基金"一对（库里已有 3 对） |

汇率 / 期货辅助符号已齐：USCNY、HKCNY、EUCNY、JPCNY、hf_NQ、hf_ES、
hf_CL、hf_NK、fx_susdcny、fx_shkdcny、fx_sjpycny、fx_seurcny。

`fundpair` 语义（演示库是简化形状，见 `dbtools/schema.sql:243-245`）：
**`id` = 基金 id，`stock_id` = 配对标的 id**（扮演 PHP `PairSql` 的 `pair_id`）。
库里 8 行现存：3 基金→ETF、2 指数→期货、2 ETF→指数、TQQQ→^NDX。
缺的那一对是 SZ162411（华宝油气LOF）→ XOP。

### 1.3 est 符号全集（中间层）

22 个中间层符号服务 70 只基金：

| est | 服务只数 | est | 服务只数 |
| --- | --- | --- | --- |
| QQQ | 13 | ^HSCE | 6 |
| ^HSTECH | 13 | ^HSI | 9 |
| SPY | 5 | znb_NKY | 4 |
| XOP | 3 | znb_DAX | 2 |
| XBI | 2 | SH000869 | 1 |
| AGG / IBB / ^DJI / VNQ / RSPH / VGT / XLY / KWEB / IXC / IEO | 各 1 | znb_TPX / znb_CAC | 各 1 |

`qdii` 数组里 8 只（即 §1.1 那 8 只跨数组的）**没有 est 符号** —— 它们是
QdiiMix 成员，EST 走持仓口径（CK4 `HoldingsReference`，未迁）。
`qdiiimix` 32 只全都没有 est 符号。

### 1.4 生产端与仓库 PHP 的两处漂移（以生产端为准）

抓生产端四个页面的原始 HTML（`_qdii*.html`，2026-10-14）数与仓库
`php/stock/stocksymbol.php` 的数组比对：

1. **港股页多 9 只**：生产端 `qdiihkcn.php` 的 EST 表有 38 行，
   仓库 `QdiiHkGetSymbolArray()` 只有 29。多出的 9 只是
   `GetHkMixSymbolArray()`（SH513090、SH513230、SH513750、SH513990、
   SZ159567、SZ159570、SZ159615、SZ159751、SZ159792）—— 生产端把它们
   并进了港股页，且有官方 / 参考 EST（持仓口径，实时 EST 为空）。
2. **qdiimix 页只有 15 只**：生产端 = 仓库 32 只减去移到 qdii 页的 8 只、
   减去移到港股页的 9 只、再减去中概互联与 MSCI 美国50 两组
   （那两组有自己的页面）。

**处理**：P0.2 写库只认**符号并集**（102 只），页面归属是 P1 的事；
届时 Python 侧的页面成员表要按生产端这四页的实际行数来，不照抄仓库数组。

---

## 二、P0.2 符号写入（2026-10-14 完成，commit `69aa649e`）

写入脚本 `dbtools/seed_p0_symbols.py`，清单来自 `core/symbolinventory.py`
（单一事实源，7 个单测钉住；探针与 seed 脚本都引它，不会漂移）。

### 2.1 为什么必须先写符号

`cli/quotecli.py` 对**库里没有的符号直接跳过落库**。P0.2 之前演示库
21 个符号里只有 4 只是 QDII 基金，整条抓取链路对其余符号全是空转。

### 2.2 写入结果

| 项 | 写入前 | 写入后 |
| --- | --- | --- |
| `stock` 行 | 21 | **139**（+118：98 只基金 + 20 个 est） |
| `fundpair` 行 | 8 | **75**（+67；70 对需求里 3 对原来就有） |

* 名称**全部从新浪行情抓**（124/124 成功），不手填 —— 手填就会出现
  "两个名字指同一个东西"的口径重影（`seed_reference_symbols.py` 的
  `REMOVE_STOCKS` 就是擦这个屁股的）；
* `fundpair` 只补缺的行：表里"基金→ETF"与"ETF→指数"两个方向并存
  （QQQ→^NDX 等），覆盖会毁掉后者；
* 备份：`dbtools/_snapshots/symbols_before_20261014_115541.sql`（472KB）。

### 2.3 安全约定（脚本内置）

* 默认 dry-run，`--apply` 才写库；
* `--apply` 前自动 `mysqldump` 全库；找不到 mysqldump 就拒绝执行，
  除非显式 `--skip-backup`；
* 新浪抓不到名称的符号**不写**（`name` 是 NOT NULL），只在报告里列出，
  等网络好的时候重跑即可补齐。

---

## 三、P0.3 历史数据回填（2026-10-14 完成，commit `344d5383`）

写入前 `mysqldump` 全库到 `dbtools/_snapshots/history_before_20261014_p03.sql`。

### 3.1 回填结果（当日实测）

| 链路 | 入口 | 结果 |
| --- | --- | --- |
| 汇率 | `forexcli` | **4 条**进 `netvaluehistory`（USCNY / HKCNY / EUCNY / JPCNY，各 65 行历史） |
| 净值 | `navcli` | **102 只**基金净值进 `netvaluehistory` |
| 行情 | `quotecli --all-qdii` | **123/124** 进 `stockhistory` |

`--all-qdii` 取 `symbolinventory.sina_quote_symbols()`（124 = 102 基金 + 22 est 中间层），
**不含**期货与外汇：它们各有 `futurescli` / `forexcli` 入口，且
`get_sina_symbol()` 对 `hf_*` 会给出 `gb_hf_*` 这种没有入口使用的形式
（PHP `stocksymbol.php:913` 同样是落空到底）。

回填后存量（探针 `_p0_3_residual_probe.py` 实测）：

| 表 | 行数 | 覆盖符号 |
| --- | --- | --- |
| `netvaluehistory` | 378 | 108（102 基金 + 4 汇率 + QQQ/SPY 各 1 行） |
| `stockhistory` | 1735 | 125（123 新浪 + `^NDX`/`^GSPC` 两条种子历史） |
| `calibrationhistory` | 10 | 未动（闸门与守卫都拦住了，见 §四.3） |

### 3.2 唯一没写入的符号：`znb_TPX`

新浪对它返回**短格式**（无 OHLC 字段），且数据停在 2025-09-29；
`save_reference` 的字段校验照 PHP 规矩整行跳过。生产端 SH513800 的
EST 日期同样是 2025-09-29 —— 属同一处上游陈旧，不是我们的链路问题，
等新浪恢复即可（重跑 `quotecli znb_TPX` 会补上）。

### 3.3 顺带修掉的两个 P0.2 暴露问题

1. **`navcli` 会把 est 中间层当基金抓净值**：SH000869（恒生香港银行指数）
   剥出 6 位代码 `000869` 后被当成基金，东财返回**同名公募基金**的净值
   0.2628 —— 而该指数点位约 5322，差 2 万倍，写进去会让官方 EST 尺度崩掉。
   `collect_targets()` 现在排除 est 中间层（`test_navupdater.py` 钉住），
   `sync_names` 同源一并修好。
2. **清掉 QQQ/SPY 的 10 行 `close=1.0` 占位**
   （`dbtools/purge_placeholder_quotes.py`，默认 dry-run；只删 est 中间层里
   "本符号另有量级 >10 的行"证明是离群的）。这些是 pitfalls #60 那次
   345 万事故的种子：`_est_quote_ref()` 的上一交易日兜底和 `_net_value()`
   的净值优先都可能落到它们。

---

## 四、残留偏差归因 + P0.5 待办（探针 `_p0_3_residual_probe.py`实测）

### 4.1 残留有多大、由什么构成

回填后三个基金的官方 EST 对生产端（2026-10-12 页面快照）：

| 基金 | 本地 EST | 生产 EST | 偏差 | 去掉比值失真后 | 剩余偏差 |
| --- | --- | --- | --- | --- | --- |
| SZ161130 | 4.6216 | 4.572 | **+1.08%** | 4.5760 | +0.09% |
| SZ161125 | 3.1980 | 3.182 | **+0.50%** | 3.1754 | −0.21% |
| SH513100 | 2.0731 | 2.049 | **+1.18%** | 2.0526 | +0.18% |

"比值失真"指 `est_now / est_cal`。以 SZ161130 为例（探针打出逐项来源）：

```text
est_now = QQQ 10-07 净值 749.4131   （netvaluehistory 唯一一条真值）
est_cal = QQQ 10-01 行情 742.03     （校准日 10-09 当天没值，退到上一交易日）
ratio   = 749.4131 / 742.03 = 1.00995   → +0.995%
```

即：**本地 EST 偏高的主因不是公式，是一个价格/净值跨日混口径的比值**。
中间层数据稀疏（探针实测）：

| 中间层 | `netvaluehistory` | `stockhistory` |
| --- | --- | --- |
| QQQ | **1 行**（10-07 = 749.4131） | 2 行（10-01 = 742.03、10-13 = 743.57） |
| SPY | **1 行**（10-07 = 769.4559） | 2 行（10-01 = 763.99、10-13 = 778.01） |
| ^NDX | **0 行** | 8 行（到 10-09 = 30883.148） |
| ^GSPC | **0 行** | 8 行（到 10-09 = 7811.54） |

### 4.2 为什么这不是公式错

PHP 的设计本来就是**两个日期**（`qdiiref.php`）：

* 官方 EST 分子：`EstNetValue()` 用 `$est_ref->GetDate()`（中间层最新日期）
  调 `_getOfficialEstVal($strDate)`（净值优先、行情价兜底）；
* 分母锚点：`AdjustFactor()` 建 `fFactor` 时用**基金当天**的中间层值
  （`_getEstNetValue(est_ref, 基金日)` → `est_ref->GetClose(基金日)`）。

生产端两个日期都能落到**同源同日**的真实值，比值就是真实的市场变动信号。
演示库里 QQQ/SPY 在 10-09（基金校准日）既无净值也无行情，锚点只能退到
10-01 的价格，而分子用 10-07 的净值 —— 比值 1.00995 是**数据缺口的产物**，
不是市场真的涨了 1%。补上每日净值后（P0.5），这个比值会变成
nav(最新日)/nav(校准日) 的真实信号；从 §4.1 表看，去掉失真后三只基金
与生产端都在 ±0.25% 内 —— 公式本身是对的，剩下的是普通的数据陈旧
（指数行情停在 10-09、净值停在 10-12，生产端页面更新）。

### 4.3 P0.5 的两个坑（都已填平，2026-10-14）

**① 中间层净值缺口 —— 已填。** QQQ/SPY 各只有 10-07 一条净值，`^NDX`/`^GSPC`
一条都没有。注意 `navcli` **永远补不了**它们：`nav_code_of()` 只认 6 位
数字代码，QQQ/SPY 没有（SH000869 那种有代码的才需要 §3.3 的排除）。
唯一现成的填充通道是 `autocalibrationcli sync-nav`（派生净值），而这个坑②
刚好堵在它上面。

净值换口径（见 ②）之后这条通道通了。**已按 §六 的安全约定备份后写入**
（备份 `dbtools/_snapshots/navcalib_before_20261014_p05.sql`）：

```text
中间层      日期            派生净值       会不会写     （--sync-nav --apply）
AGG       2026-10-13     94.71          已写
QQQ       2026-10-13     743.57         已写
SPY       2026-10-13     778.01         已写
...        共 13 条，净值全部落在价格尺度上
```

写入后闸门从"挡住（净值日期==校准日期）"变成"可校准"，QQQ/SPY 的校准链路
第一次真正跑通。

**② `_implied_middle_nav` 的旧尺度恒等式 —— 已修（2026-10-14）。**

旧式子 `cny / cal_m` **只在种子尺度成立**（cal_m = 0.008945 →
6.705/0.008945 = 749.58，与真实 NAV 吻合到 0.03%）。按生产端口径重算后
QQQ 的 cal_m 是 **41.5793**（= 指数/净值），同一式子给出 6.69/41.58 =
**0.161** —— 差 4648 倍，而它会被 `sync-nav` 直接写进 `netvaluehistory`，
重演 pitfalls #60/#61 那一类事故。

**根因：`cal_m` 随部署尺度漂移，净值不漂移。任何把 `cal_m` 当分母的式子
都会随尺度坏掉。**

分两步修。第一步是**量级自检**（先挡住再修），第二步才是换口径：

1. `autocalibration.nav_matches_price(净值, 价格)`：拿**当日价格**当尺子
   （ETF 净值与价格同量级，尺度无关），偏离超过 ±25% 判定"口径不成立"，
   接在两个写库点上 —— `usnavsource._implied_nav()`（挡下后退回
   `FALLBACK_BASE_NAV` 并记 warning）与 `--set-nav`（挡下后拒绝写入，
   显式 `--value` 也过检，手工输错小数点同样是灾难）。
2. **换口径（用户选定：直接用当日收盘价当净值）**：

   ```text
   净值(t) = 当日收盘价(t)            日期(t) = 该价格的行情日期
   ```

   ETF 的净值与交易价只差千分位（做市商一级申赎套利抹平折溢价），生产端
   自己也是这么用的（`netvalueclosecn.php` 上美股 ETF 的"单位净值"就是
   收盘价）。关键是这条式子**一个 `cal_m` 都不出现**，刻度怎么重算都不会坏。
   剩余误差只有折溢价（千分位）；分红除息时 NAV 与价格同额下降，不像
   开放式基金那样错位。

   三个地方同步：`AutoCalibrator._implied_middle_nav`（默认走
   `stockhistory`，传了 `symbol` 优先进 `realtimequote`，与 PHP 语义一致）、
   `usnavsource._implied_nav` / `fetch_middle_nav`、`--set-nav` 的默认值。
   `cny` 参数留下只为不破坏已有调用方，新口径用不到它。

   ```text
   验证（真实库预演，生产尺度 cal_m = 41.5793）：
   QQQ  --set-nav  -> 743.57（当日收盘价）      旧口径是 0.160938
   SPY  --set-nav  -> 778.01（当日收盘价）      旧口径是 0.659974
   两个写库点口径一致。
   ```

另补一条清理：删掉了旧口径的两条配套设施 ——
`DerivedMiddleNavSource._cny()` 与 `autocalibrationcli._latest_cny()`
（都没有别的调用者了）。留着它们会让下一个人以为还在用汇率。

**③ 净值修好之后，暴露出校准链路本身还有三处偏差（2026-10-14 修）。**

净值尺度一正确，跑出来的校准值反而**变差**了：QQQ 43.027 / SPY 10.232，
对生产锚点（41.111 / 10.035）偏 **+4.66% / +1.96%**，比改之前的旧值
（41.579 / 10.139，偏 +1.14% / +1.04%）还不如。这说明偏差不在净值那端，
而在下面三处——每一处都对得上 PHP 源码：

1. **`calibrate_middle` 走的是"按配对比值缩放"，PHP 从来没有这一步。**
   PHP `DailyCalibration()`（`fundpairref.php:196-201`）原文直接就是
   `$cal_sql->WriteDaily($strStockId, $strDate, CalcFactor($fPairNetValue,
   $strNetValue, $strDate))`。那个 `cal_old × pair_old / pair_new` 是本演示库
   在"中间层净值被归一化成 1.0"时做的权宜之计，它把**净值的变化整项丢了**
   ——完整换算应是 `cal_old × pair_new/pair_old × nav_old/nav_new`，
   实测丢掉的这一项就是那 4.66%。

   既然净值现在是真实每份净值，就直接走 PHP 原路径。切换的**判据复用第 1 步
   那把尺子**：`nav_matches_price(net_value, implied)` 通过才敢用 `pair / net`。
   没有当日价格时退回旧路（**没有尺子 ≠ 可信**，否则 `pair / 1.0 = 31500`
   会被当校准值）。旧路径抽成 `_scale_converted()`，它的代价写在 docstring 里。

2. **`_pair_value` 拿到了"此刻"的点位去配"那天"的净值。**
   它的兜底是新浪实时点位（`gb_ndx`），而净值日期是 10-13 —— 拿到的 30111
   是当时的实时值，10-13 的收盘点位其实是 30569.4809（P0.4 之后就在
   `realtimequote` 里）。这一项单独就让 QQQ 从 41.111 掉到 40.49。
   新增第三级 "`realtimequote`，**且行情日期必须等于要取的日期**" ——
   这正是 PHP `$this->pair_ref->GetNetValue($strDate)` 按日期取值的语义。

3. **`CalcFactor` 不该乘汇率。** PHP `CalcFactor()` 里有
   `if ($this->cny_ref)`；而 `cny_ref` 是在构造函数里按"本符号/配对标的是不是
   A 股"决定的（`fundpairref.php:14-43`）—— **本符号不是 A 股、配对标的是美股
   指数时 `cny_ref` 为 `false`**，`CalcFactor` 直接 `return pair / net`。
   所以我们对 QQQ/SPY 必须传 1.0，传真实汇率 6.69 会把 41.11 变 275。
   新增 `_calc_cny()`：**注意要区分"中间价是多少"和"这个符号有没有汇率换算"
   这两个问题** —— 之前图省事传了 `_cny(date)` 的返回值。

   顺带：`_pair_symbol` 第 3 条反查 `qdii_est_symbol_map()` 时会把中间层
   **自己**当成配对标的（`est == middle_symbol` → `return est`），于是
   AGG/XBI 那批符号算出 `cal = pair/net = 1.0`。现已统一排除"自己配自己"
   （PHP 也不会校准没有配对标的的符号 —— `FundPairReference` 只在
   `GetPairSymbol()` 有值时才构造 `pair_ref`），它们改为明确 "没有配对标的是"。

三处修完的结果：

```text
QQQ  cal = 41.111773    生产锚点 41.111    偏差 +0.002%   （旧值 41.579312，+1.139%）
SPY  cal = 10.032583    生产锚点 10.035    偏差 -0.024%   （旧值 10.139333，+1.040%）
```

**下游估值也要一起验**（改 `cal_m` 历来会把估值按同比例带崩）：

```text
SZ161130  EST = 4.5874     生产端 4.572
SZ161125  EST = 3.2317     生产端 3.182
SH513100  EST = 2.0569     生产端 2.049
```

量级正常，没有再出 20245 那种事故。

### 4.4 顺带确认

页面加载时的自动校准（`fund_list_service.py:189` 的
`calibrate_middle(..., apply=True)`）**没有**改写校准历史 ——
闸门（净值日期 == 校准日期）与估值守卫都拦住了，`calibrationhistory`
仍是回填前的 10 行（10-09 基金 / 10-07 中间层）。

---

## 五、P0.4 实时链路（2026-10-14 完成）

探针：`auto/_scratch/_p0_4_estquote_probe.py`（只读，报 estquote 的覆盖与空值）。
备份：动库前已 `mysqldump` 两张快照表到
`python/dbtools/_snapshots/snapshots_before_20261014_p04.sql`。

### 5.1 做了什么（按生产语义常驻 `--daemon`）

| # | 改动 | 对应 PHP |
| --- | --- | --- |
| 1 | 逐符号节流接进 daemon：`update(respect_throttle=True)` | `stockprefetch.php` 的 `SinaFundNeedFile` / `FutureNeedNewFile` / `StockNeedNewQuotes`（已迁 `updater/stockprefetch.py`，与 PHP 逐条对应）。`--once` 是显式全量，**不**走节流 |
| 2 | `default_est_symbols()` 补第四部分：港日欧中间层 | 见下 |
| 3 | 断点续跑：启动把 `realtimequote.fetched_at` 灌成"上次抓取时刻" | PHP 用缓存文件 mtime 承担同一角色；不灌则每次重启都无条件全量抓一轮，那正是最容易触发新浪限流的时刻 |
| 4 | `is_any_market_open()` 时区修复 | 见 §5.2 ④ |
| 5 | `--refresh-symbols`（默认 900 秒）定期重收集符号表 | PHP 每次页面请求都重新收集；我们没必要每轮查库，但 900 秒能接住"回填后新入库的符号" |
| 6 | `collect_symbols_from_source()` 加第三来源：est 中间层 | 见 §5.2 ③ |

**第 2 项的必要性**：`default_est_symbols()` 原来只有三摊 —— `qdii_est_symbol_map()`
的值集合、QQQ/SPY、持仓型的 INDA/IBB/USO，全都是**美股**。港日欧的映射散在
`QdiiHkGetEstSymbol()` 等函数里，早期漏了，于是 daemon 抓到了行情也只写进
`realtimequote`，官方 EST 的"实时快照优先"一步（`_middle_day_quote` 第 2 级）对
港股基金全部落空，只能退到 `stockhistory` 的收盘价。现在补上
`symbolinventory.est_symbols()` 的 22 个（^HSI / ^HSTECH / ^HSCE / SH000869 /
znb_NKY / znb_CAC / znb_DAX / znb_TPX …）。

### 5.2 真实数据抓出来的四个问题（都已修）

**① 解析分派用了"新浪代码"而不是"库内符号"——港股指数全空。**
`parse_symbol()` 原来把新浪代码（`rt_hkHSI`）交给 `StockReference` /
`StockSymbol` 做分派；可 `is_symbol_h()` 认的是 `^HSI` 这种带 `^` 的写法，
`rt_hkHSI` 既不是 A 股也不是港股符号，于是落到最后"字段数 >= 29 才算美股"
那一支 —— 而 `rt_hk` 那行只有 25 个字段，`has_data = False`。

后果不是"少一列"，而是**写进库里的空值**：`realtimequote` 与 `estquote` 各多出
3 条 `price`/`close` 为空的行，且 `quote_date` 被 `_coerce_date()` 填成了抓取
当天（`_coerce_time()` 填 `00:00:00`），看上去就像"刚抓到过"。而
`_middle_quote()` 读 estquote **只看 close 不看日期**，空值会一路传下去。

修法是照 PHP 来：PHP 的引用对象一直握着**股票符号**，`LoadSinaData()` 内部才调
`GetSinaSymbol()` 算文件名。对照实验（同一行真实数据）：

```text
parse_symbol('rt_hkHSI', line)  ->  has_data=False, price=''
parse_symbol('^HSI',     line)  ->  price=24339.23  date=2026-10-14  time=15:16:58
```

**② 取不到价也写库，把上一轮的好值覆盖成空串。**
`_save_to_store()` 现在**没价就不写库**（内存缓存照旧写入，页面按空值显示空白）。
这是对 PHP 的有意偏离：PHP 抓到空行照样写缓存文件，那一轮页面显示空白；
而 `realtimequote` / `estquote` 是**跨进程共享的唯一数据源**，一覆盖，所有进程
都读不到价了。保留旧值 + 旧 `fetched_at`，调用方凭时间戳照样能看出它不新鲜
（`is_stored_fresh`）。

**③ est 中间层没进抓取范围（`znb_TPX` 永远缺一行）。**
`collect_symbols_from_source()` 只认"有 `stockhistory` 的符号" + 期货。可中间层
的作用就是给 QDII 估值当"当前值"，不该依赖历史表写没写 —— 实测 `znb_TPX`
上游只有 6 个字段、抓不到历史序列（§三.2），于是它一直不在抓取范围里，
`estquote` 里永远缺这一行。现在加第三来源：按 est 名单从 `stock` 表补齐，
`znb_TPX` 即刻进链（值 3132.6，与生产端一致，见 §5.5）。

**④ `is_any_market_open()` 把服务器时间当成了各市场本地时间。**
`is_stock_market_trading()` 把传入时刻的时分按**该符号所在市场**解释，所以必须
先按上海 `localize` 再逐符号 `astimezone`。原来直接传服务器本地时间，结果是
美股符号在北京时间白天被当成"交易中"、在北京时间深夜（恰恰是美股盘中）被当成
休市 —— 方向是**少抓**，比多抓危险，`--only-open` 因此会漏掉整个美股时段。

### 5.3 决策：外汇符号不进 daemon 符号表

PHP `_getAllSymbolArray()` 预取 `fx_*`，但**它的消费方（页面外汇显示）Python 侧
没有**；EST 的数学用的是 `netvaluehistory` 里的中间价（`forexcli` / chinamoney
链路），与 `fx_*` 的即期价（spot）不是一回事，混进快照表反而有 spot / mid-rate
语义混淆的风险。所以不加，等真有消费方再说。

### 5.4 daemon 实测（真实数据，非 mock）

修复前 4 轮（13:52–13:55，每轮 65 秒）：每轮稳定"更新 109 条（节流跳过 18）"。
修复后单轮（15:41，上海时间）：

```text
[15:41:05] 更新 7 条（节流跳过 121）
  ^HSCE=8181.09 ^HSI=24321.15 ^HSTECH=4172.3
  hf_ES=7866.462 hf_NQ=30812.975
  znb_CAC=7862.2998 znb_DAX=25316.7598
```

逐条核对：港股三指盘中（每分钟抓）✅；期货几乎全天 ✅；欧指在上海 15:41 =
巴黎 09:41 已开盘 ✅；A 股 15:00 已收、美股休市、`znb_NKY` 东京 16:41 已收 ——
全部跳过 ✅。这正是 PHP 的逐符号节流语义。

落库结果（`_p0_4_estquote_probe.py`）：`estquote` **24 行、0 空值**，
`symbolinventory.est_symbols()` 的 22 个中间层**全覆盖**（另有 ^GSPC / ^NDX
由 fundpair 侧带入，它们不在 `est_symbols()` 里但同样是中间层）。

> 排查笔记：第一次前台跑时出现过"更新 0 条"。那不是代码问题 —— 上一个
> `timeout` 没杀干净的 daemon 与它**并发**，后者的 seeding 读到前者刚写的
> `fetched_at`（间隔 < 60 秒），于是全部被节流跳过。同一时刻只跑一个 daemon。

### 5.5 与生产端的一处已知偏差：`znb_TPX` 时刻差 1 小时

生产端 <https://palmmicro.com/woody/res/sh513800cn.php>（唯一用 TPX 的基金）：

| | 价格 | 涨幅 | 日期 | 时间 |
| --- | --- | --- | --- | --- |
| 生产端 | 3132.6 | -1.71% | 2025-09-29 | **16:12** |
| 本地 | 3132.6 | -1.71% | 2025-09-29 | **15:12** |

价格与日期**完全一致**（上游确实陈旧到 2025-09-29，与 §三.2 一致），只有时刻差
一小时。根因是 PHP 的**双重换算**（`php/stock/stockref.php:331-355`）：
`new TickYMD(intval($ar[5]))` 先按**当前进程时区**渲染出 15:12
（`DebugGetTime($iTime, false)` 的 `false` 分支不改时区），紧接着又把"2025-09-29
15:12:00"这串**已是东京时间**的文本 `strtotime()` 回上海时间，再
`DebugGetTime(..., 'Asia/Tokyo')` 转到东京 → 16:12。Python 只换算一次，
**15:12 才是正确的东京时间**。

影响面：只作用于 6 字段短格式的 `znb_` 行（目前仅 `znb_TPX`），且只是显示的
`HH:MM`，不进估值计算。不修 —— 修了反而错。

---

## 六、P0.3 及以后要遵守的约定

* 动演示库数据前先 `mysqldump` 全库到 `python/dbtools/_snapshots/`
  （已 gitignore，先例 `calib_before_20261012.sql` / `calib_before_20261013.sql`）；
* 数据脚本**默认 dry-run**，`--apply` 才写库，把"旧值 / 新值 / 倍数 / 算式"
  打成表给人核（`auto/_scratch/_calibration_recount.py` 是范例）；
* 每步四件套：单测 + 本文档对应小节 + 真实数据对照（`_scratch` 探针）+
  踩坑记录（`.dsh/skills/palmmicro-php-to-python/references/pitfalls.md`）；
* 写入顺序：`stock` 行 → `fundpair` 行 → 历史回填 → 实时链路，
  每一步都可单独重跑（P0.8 的 bootstrap 就是把这四步串起来）。
