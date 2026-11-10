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
| P0.5 | 校准链路跑通（中间层净值 → 闸门 → `calibrationhistory`） | ✅ `a2e84b05`，本文 §四.3 |
| P0.6–0.7 | `fundest` 官方 EST 落库 + 期货实时 EST | ✅ `ee6dcee2`，本文 §六 |
| P0.8 | 一键重跑 `dbtools/bootstrap_fund_data.py`（默认 dry-run） | ✅ `533c459a`，本文 §七 |
| Plan A | fundest 日期口径对齐（数据修复 + 每日例程，**不改代码口径**） | ✅ 本文 §十 |

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
QdiiMix 成员，EST 走持仓口径（CK4 `HoldingsReference`，
**2026-10-15 已迁**，`4d681a0f`，见 `README_holdings_migration.md`）。
迁完只解决"类存在"，这 8 只仍要等各自的 `holdings` / `holdingsdate` 数据入库。
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

## 四、残留偏差归因 + P0.5 的结果（探针 `_p0_3_residual_probe.py` 实测）

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

净值换口径（见 ②）之后这条通道通了。**已按 §八 的安全约定备份后写入**
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

## 六、P0.6：官方 EST 落 `fundest` + 期货实时 EST（2026-10-15 完成）

备份：`dbtools/_snapshots/fundcalib_before_20261015_p06.sql`（动库前，
`calibrationhistory` / `lastcalibration` / `fundest` / `stockhistory` 四张表）。
探针：`auto/_scratch/_p0_6_probe.py`（闸门归因）、`_p0_6_state.py`（落库取样）、
`_p0_6_est_compare.py`（与生产端 `qdiicn.php` 逐只对照）。

### 6.1 校准值有两个层次，分母不是同一个东西

P0.5 打通的是**中间层**，P0.6 补的是**基金层**（`_QdiiReference::AdjustFactor()`，
`qdiiref.php:176`）：

| 层次 | 对象 | 式子 | 例子 |
| --- | --- | --- | --- |
| 中间层 `cal_m` | `QQQ` | `指数 / ETF净值` | 41.111773 |
| 基金层 `cal_f` | `SZ161130` | `指数 × 汇率 / 基金净值` | 44605.71 |

关键差别在分母：中间层用"当日收盘价"当净值（美股 ETF 净值与价格只差千分位，
P0.5 定稿口径②）；**基金层必须是 `netvaluehistory` 里的单位净值** ——
QDII 的场内价与净值差着折溢价（SH513100：净值 2.0563、场内价 2.399，差 16.7%）。
两个层次混用会把校准值整项带偏，见 §6.6 那条实测抓到的错记录（pitfalls #66）。

`cal_f` 的 `est` 必须是**指数**（`^NDX`），不是杠杆替代后的 ETF（`QQQ`）——
`qdii_est_symbol()` 返回的就是 PHP `QdiiGetEstSymbol()` 的原始值，两者差约 41 倍。

### 6.2 锚点日的口径 = **基金净值日**（PHP 口径，2026-10-22 改）

```text
锚点 = 基金最新净值日（净值本身就取这一天的）
汇率 = 锚点当天（取不到就**不校准**，照抄 PHP 的 return false）
est  = 锚点当天的净值 → 收盘价（两级都取不到就**不校准**）
```

对应 `AdjustFactor()`（`php/stock/qdiiref.php:176-197`）—— 三个量锚在**同一天**，
`$strDate = $this->GetDate()` 是 `UpdateOfficialNetValue()` 之后的**基金净值日**：

```php
if ($this->UpdateOfficialNetValue())          // 净值取不到 -> 整段 false
{
    $strDate = $this->GetDate();              // ← 锚点 = 净值日
    if ($strCNY = $cny_ref->GetClose($strDate))       // 当天没有汇率 -> false
    {
        if (($strEst = $this->_getEstNetValue($est_ref, $strDate)) === false)
            if (($strEst = $est_ref->GetClose($strDate)) === false)   return false;
        $this->fFactor = QdiiGetCalibration($strEst, $strCNY, $this->GetPrice());
        $this->InsertFundCalibration();       // 写库的日期就是这一天
    }
}
```

## 为什么改：旧口径"est 最新行情日"必然混日

这里**曾经**用"est 标的最新行情日"（净值日与行情日取较大者），理由是"净值日那天
指数常常没数据，一条都算不出来"。代价是**混日**：QDII 净值比海外行情晚一天，早上
跑时 est = T−1、净值 = T−2，于是"拿昨天的净值配今天的行情"。

2026-10-22 实测 SZ161125：锚 10-21 配净值 10-20 算出 **16599.36**，按 PHP 口径
（净值日 10-20：7712.57 × 6.72 / 3.14）应是 **16505.88**，差 **0.56%**。而页面上
`est_cal` / `cny_cal` 又是按"校准日"取的，于是不自洽：由 `cal × nv / cny` 反推的
est 是 7710.54，库里 10-20 的行情行却是 7712.57（差 0.044%）。

**"净值日那天指数没数据"是数据缺口，不是口径问题** —— 正解是补 `stockhistory`
（`dbtools/seed_index_history.py --days N --apply`），不该换锚点去凑。

两个易错点（都踩过）：锚点**不能**取 `realtimequote`（快照是"此刻"，锚点必须是
已收盘的那天）；取 est 值**不能**用 `_pair_value`（它的兜底会去新浪抓实时点位，
于是拿今天的指数配昨天的净值）—— 用 `_est_value_on()` 严格按日期取，**两级都没有
就返回 `None`（不校准）**，旧实现还有第三级快照兜底，那是把 SH513100 的 cal 算成
97692 而非 100390 的元凶。

## 对照结果（真实 PHP，33 只逐只）

`_scratch/_php_calibration_anchor_check.php` 把 `AdjustFactor()` / `_getEstNetValue()`
/ `QdiiGetCalibration()` 三段真身从 `qdiiref.php` 现场切出来 eval（只把取数/写库换成
连库替身），与 Python 跑同一个演示库：

```text
对照 33 只基金（其中同日跳过 15 只）—— 锚点日 / est / 汇率 / cal 全部一致
SZ161125  2026-10-20  7712.57 × 6.72 / 3.1400 = 16505.882293   php=py
SZ161130  2026-10-20  29490.28 × 6.72 / 4.4119 = 44918.217004  php=py
SH513100  2026-10-20  29490.28 × 6.72 / 1.9741 = 100387.357074 php=py
```

15 只跳过的是 est 为美股 ETF 的那些（AGG/IBB/XOP/XBI/VNQ/IXC/RSPH/VGT/XLY/IEO/
KWEB/`^DJI`）：它们的 `stockhistory` 停在 10-16，净值日 10-20 没有 est 数据 ——
PHP 也是同一天取不到，同样不校准。**这是数据缺口（要补美股 ETF 历史），不是口径差异。**

### 6.3 官方 EST：为什么 33 只里一开始只有 3 只写进去

`StockUpdateEstResult()` 的三道闸门（净值未到 / 时刻早于 20:55 / 该日期已有记录）
**一条都没挡**。真正的原因是 `fundestcalc._build()` 里中间层那行是*有条件*创建的
（`if middle_calibration:`），而 AGG/IBB/XBI/VNQ 这些"中间层自己就是估值标的"
的基金本来就没有 `cal_m` —— 生产端页面上它们**全都有值**（SH501300=0.914、
SH513290=1.646、SZ159502=1.453）。

改成无条件建行（缺省 `calibration=0.0`）、去掉 `_qdii_value()` 里那道
`if not cal_m` 之后，33 只全部可写。这一类"守卫保护的前提已经消失"的静默少数据，
记在 pitfalls #69。

### 6.4 快照归档：`realtimequote` 不会自己进历史

29 只基金一开始报"锚点日 2026-10-09 及之前没有单位净值" —— 它们的净值只有
10-12 一条，晚于 10-09 的锚点。补法是先把指数快照落进历史：

```text
python -B auto/cli/quotecli.py --archive-snapshot
    ^NDX  30569.4809 @ 2026-10-13
    ^GSPC 7805.4502 @ 2026-10-13
```

daemon 只写 `realtimequote`（一行快照、覆盖式），`stockhistory` 要单独归档这一步
—— **快照与历史是两条独立的通道**，架构约定见 `auto/refquote.py`。归档后锚点推到
10-13，`_nav_on_or_before` 找到 10-12 的净值，33 只全部算得出。

### 6.5 期货实时 EST：四处修正后第一次真正接通

`实时 EST = 参考 EST + 期货`。此前它永远回落到中间层快照，四件事叠加：

| # | 问题 | 对应 PHP |
| --- | --- | --- |
| ① | 期货时间戳是**上海时间**，指数是纽约时间，日期经常差一天 | `_onSinaFuture()` 之后要 `_convertDateTimeToUS()` |
| ② | `CheckAdjustFactorTime()` 有**三个**条件，只实现了两条 | 还有 `if ($etf_ref->IsExtendedMarket()) return false;`（只许常规时段） |
| ③ | 拿刚抓的期货去比库里**昨天**的指数快照 | PHP 里两者是同一批抓的，时刻天然相同 → 抓期货前先刷新指数快照 |
| ④ | `CalcFactor` 把 `cny=None` 当"缺参数"返回 `None` | PHP 是 `if ($this->cny_ref)`；美股指数/期货这一对 `cny_ref` 为 false → 应取 1.0 |

修完写入期货校准值（`^NDX` 1.007178、`^GSPC` 1.006373，即"期货 / 指数"）。

**量纲换算还翻过一次方向**（pitfalls #67）：P0.5 把 `cal_m` 从 `ETF/指数`
（0.008945）改成 `指数/净值`（41.111），分子分母互换了，旧的一步式
`ETF价 = 期货价 × cal_m / cal_futures` 给出 123 万（1660 倍），实时 EST 显示成
3414 而不是 2.05。现在拆成两步 + 量级保险：

```python
index_price = price / factor              # 期货 -> 指数点位
value = index_price / middle_calibration  # 指数 -> ETF 价
if abs(value / 中间层已知价 - 1.0) > 0.5: return None   # 宁可回落
```

### 6.6 落库结果与生产端对照

```text
fundest              39 行 / 33 个符号（33 行 @ 2026-10-13）
calibrationhistory   35 行 @ 2026-10-13（33 只基金 + QQQ + SPY）
                      3 行 @ 2026-10-14（^NDX / ^GSPC 的期货校准值）
中间层               QQQ 41.111773、SPY 10.032583（生产锚点 41.111 / 10.035）
```

实时 EST 的来源（`futurescli --est` 实测）：`^NDX`/`^GSPC` 系的基金走
**期货 hf_NQ / hf_ES**（SH513100 2.0195、SH513300 2.5072、SH513500 2.4587 …），
港日欧那些没有期货配对的回落**中间层快照** —— 与 PHP 的分支一致。

与生产端 `qdiicn.php`（2026-10-15 抓取）逐只对照：本地是 10-13 的数、生产端是
10-14 的，**同日可比 0 只**。跨日只做量级检查：33 只偏差 |max| **3.13%**
（SZ159502）、|min| 0.04% —— 没有尺度错误，剩下的是数据新鲜度差一个交易日
（P0.8 的定时重跑会补上）。

生产端 41 只里我们有 33 只：缺的 8 只（SH501312、SH513850、SZ159509、SZ159529、
SZ159577、SZ160216、SZ163208、SZ164701）是 §1.3 说的**持仓口径** ——
`HoldingsReference` 已于 2026-10-15 迁完（`4d681a0f`），所以这 8 只现在的
阻塞点是**各自的 `holdings` 数据**，不再是"类没迁"。页面归属也是 P1 的事。

### 6.7 事后抓到的一条错记录（层次混用）

写完第二天复查时发现 `SH513100 2026-10-14 = 83792.206883`。反解三个输入
（逐候选代入，只有一组对到 1e-11）：

```text
est = 30039.8261（^NDX 实时）  cny = 6.6917  nav = 2.399 ← 场内价，不是净值
正确：est × cny / 2.0563 = 97756.9（10-14）或 99480.5（10-13）
```

后果不是"数大了"，而是 `GetQdiiValue = est × cny / fFactor` 会**等于场内价**
（2.441）—— 量级、单位都对，只有逐格对生产端才看得出来。

两件事：① 备份后删掉该行（`_scratch/_p0_6_fix_bad_calibration.py`，默认 dry-run），
删除后官方 EST 2.0563、实时 2.0195（生产端 10-14 是 2.01）；② 加**层次守卫** ——
`calibrate_middle()` 开头挡掉"有 QDII 估值标的的基金"，判据是
`qdii_est_symbol_map()` 的键（33 个基金与 14 个标的没有交集，不误伤中间层），
回归测试 `test_autocalibration.LayerGuardTest`。

顺带确认了一条 PHP 语义：`AdjustFactor()` 用的是 `GetPrice()`，而
`FundReference::LoadSinaFundData()` 里 `$this->strPrice = $ar[1]; // net value`
（`php/stock/stockref.php:483`）—— **基金的"价"就是单位净值**。所以照抄 PHP 字面去
取实时行情价，反而错。

---

## 七、P0.8：一键重跑 `dbtools/bootstrap_fund_data.py`（2026-10-15 完成）

前面七步各自都能单独跑，但换机器 / 重建演示库时要按**依赖顺序**连续执行九条
命令 —— 顺序记错一次就会得到一份"看起来齐了、其实少一层"的数据。所以串成一个
脚本，**默认 dry-run**：

```text
python -B dbtools/bootstrap_fund_data.py                  # 体检 + 打印计划
python -B dbtools/bootstrap_fund_data.py --apply          # 备份 + 真跑
python -B dbtools/bootstrap_fund_data.py --apply --only archive,fundest
python -B dbtools/bootstrap_fund_data.py --apply --from calib
```

### 7.1 九步与它们的依赖关系

| # | 步骤 | 命令 | 为什么在这个位置 |
| --- | --- | --- | --- |
| 1 | `symbols` | `seed_p0_symbols.py --apply` | 抓取层**跳过库里没有的符号**，不先写符号后面全是空转 |
| 2 | `forex` | `forexcli --backfill` | 估值要汇率 |
| 3 | `nav` | `navcli` | 基金净值，是基金层校准值的分母 |
| 4 | `quotes` | `quotecli --all-qdii` | 行情历史，也是中间层"当日收盘价"的来源 |
| 5 | `middle-nav` | `autocalibrationcli --sync-nav --apply` | 推进中间层净值日期，闸门才开得了 |
| 6 | `archive` | `quotecli --archive-snapshot --all-qdii` | daemon 只写快照，**历史要单独归档**；不归档锚点推不动（§6.4）。`--all-qdii` 会自动并入 `^NDX` / `^GSPC`，**不要只归档指数** —— 那会漏掉 est 走美股 ETF 的 14 只基金（§10.5 第 3 步） |
| 7 | `calib` | `autocalibrationcli --apply` | 中间层 `cal_m` |
| 8 | `fund-calib` | `autocalibrationcli --funds --apply` | 基金层 `cal_f` |
| 9 | `fundest` | `fundestcli` | 官方 EST，要前面全部就位 |

第 6 步之前还要一次 `quoteupdatercli --once` 才有快照可归档 —— **脚本不代跑
长驻进程**（`--daemon` 是另一件事），只在 dry-run 里提示。

**这九步覆盖不了"指数的多日历史"**：archive 只归档**当天**那一行。均线要的
620 天窗口得另外跑 `dbtools/seed_index_history.py --days 800 --apply` ——
它的默认清单现在是八个估值标的指数（`^NDX` `^GSPC` `^HSI` `^HSTECH` `^HSCE`
`znb_NKY` `znb_DAX` `znb_CAC`），ETF 要显式 `--symbols` 点名。漏这一步的症状是
「均线表整段为空 / EMA 空」，不是报错。

### 7.2 两条安全约定（与 `seed_p0_symbols.py` 同一套）

* **默认 dry-run**：只做体检（查库，不联网）并打印要跑的命令；`--apply` 才执行；
* `--apply` 前 `mysqldump` 全库到 `dbtools/_snapshots/`；找不到 mysqldump 就拒绝
  执行，除非显式 `--skip-backup`。

每步一个子进程，失败默认**停下**（`--keep-going` 继续），子命令的输出**透传**
（排查要看的就是它们自己打的那张表）。

### 7.3 单测钉的是顺序，不是命令

`auto/test_bootstrap.py`（14 例）断言的几件事：符号在最前、EST 在最后、
归档在校准之前、行情在中间层净值之前；每个步骤指向的脚本真实存在；会写库的
步骤**显式**带 `--apply`；`--only` 按声明顺序而不是参数顺序。

两个实现细节：`dbtools` **不是包**（`test_layout` 钉住它没有 `__init__.py`，
加了就会和 `auto/sql` 抢包名），所以测试按文件路径加载；加载期间要把
`sys.stdout` 换成没有 `buffer` 属性的对象 —— 否则脚本那个 UTF-8 包装器被回收时
会关掉真正的 stdout buffer，后面 import `cli.*` 的用例会报
`I/O operation on closed file`（`test_layout.test_every_module_imports` 同源）。

---

## 八、P0.3 及以后要遵守的约定

* 动演示库数据前先 `mysqldump` 全库到 `python/dbtools/_snapshots/`
  （已 gitignore，先例 `calib_before_20261012.sql` / `calib_before_20261013.sql`）；
* 数据脚本**默认 dry-run**，`--apply` 才写库，把"旧值 / 新值 / 倍数 / 算式"
  打成表给人核（`auto/_scratch/_calibration_recount.py` 是范例）；
* 每步四件套：单测 + 本文档对应小节 + 真实数据对照（`_scratch` 探针）+
  踩坑记录（`.dsh/skills/palmmicro-php-to-python/references/pitfalls.md`）；
* 写入顺序：`stock` 行 → `fundpair` 行 → 历史回填 → 实时链路，
  每一步都可单独重跑（P0.8 的 bootstrap 就是把这四步串起来）。

---

## 九、CK6-3：补 SH 对冲线与两类历史（2026-10-15 完成）

CK6 是"让 `/fund/SZ161125` 与生产端 `sz161125cn.php` 逐段对齐"。前两步
（CK6-1 谓词、CK6-2 段落集合）是渲染层的事，这一步补的是**演示库缺的数据**。
每次 `--apply` 前都 `mysqldump` 全库到
`dbtools/_snapshots/before_20261015_ck6_3_sh.sql`（以及脚本自带的
`symbols_before_20261015_*.sql`）。

### 9.1 SH 这条对冲线（新增脚本 `dbtools/seed_sh_hedge.py`）

生产端「基金指数对照表」有 SH / SPY 两行、「净值价格比较」有两张表，
参考数据表里还有 USDCNH —— 本地一样都没有。根子不是渲染，是**库里没有 SH**：

| 缺什么 | 后果 |
| --- | --- |
| `stock` 没有 SH 行 | 整个符号不存在 |
| `fundpair` 没有 `(SH, ^GSPC)` | `_LEVERAGE_SQL`（谁跟踪估值标的）列不出它 → 对照表 / 净值价格比较 / 各杠杆均线三段全空 |
| `fundposition` 没有 SH 的仓位 | 仓位退回 1，反向 ETF 变成"正向"，对冲值少一个负号 |

脚本按"`stock` 行 → `fundpair` 行 → 仓位 → 外汇快照 → 历史 + EMA"五步走，
每步幂等。写入结果（2026-10-15 实测）：

* `SH` id=179，名称取新浪（`标普500指数ETF-ProShares做空`，生产端同）；
* `fx_susdcnh` id=180（`离岸人民币（香港）`，生产端同）；
* `stockhistory` 800 行 2023-08-08 ~ 2026-10-14，**`10-14 = 32.17` 与生产端
  参考数据表逐字一致**；EMA50/200 一并补齐；
* 校准值 `239.942179`（`^GSPC 7718.94 / SH 32.17`），生产端 `239.882` ——
  差 0.025%，因为生产端的 SH 净值来自 Yahoo（32.178）而不是收盘价
  （32.17）。我们这条是"中间层净值 = 收盘价"的既有口径，不追着改。

**净值 / 校准 / 快照刻意不在这个脚本里写**，留给既有工具各自的闸门
（`autocalibrationcli --sync-nav --apply --symbol SH` 与
`--apply --symbol SH`），这样每一步都能单独重跑、单独诊断。

### 9.2 外汇快照：鸡生蛋又来一次

`quoteupdater` 的符号集合是**从 `stockhistory` 反推**的，外汇本来就没有日线
历史，永远进不了那个集合 —— 与当年 `^NDX` 那一模一样。所以外汇的
`realtimequote` 快照由 seed 脚本直接写（`RealtimeQuoteStore`）。
实测 `USDCNH 6.6902`（生产端 04:45 是 6.6909）、`USDCNY 6.6919`。

顺带修掉两处（见 pitfalls #80 / #81 / #82）：

1. `get_sina_symbol()` 给 `fx_s*` 加了 `gb_` 前缀 → 新浪返回空串；
2. 分派在 `MyStockReference::LoadData()`（PHP 子类）而不是 `LoadSinaData()`，
   Python 侧没人调 `load_sina_forex_data()`；
3. 外汇的 `open/high/low/close` 能通过字段校验，会被当成股价写进
   `stockhistory` —— 已在 `save_reference()` 提前拦掉（并清掉已污染的两行）。

### 9.3 A 股 / 场内基金历史：以前没有源（新增 `updater/asharehistory.py`）

`seed_index_history.py` 原来只有美股那条路（`US_MinKService`），于是
`SZ161125` 的 `stockhistory` 只有 3 行。新源走新浪
`CN_MarketData.getKLineData`（**裸 JSON 数组**，不是美股那条的 JSONP），
实测与生产端「基金溢价记录」逐行一致：
`10-08=3.294 / 10-09=3.312 / 10-12=3.337 / 10-13=3.330 / 10-14=3.340`。

入口 `dbtools/seed_a_share_history.py`（默认 dry-run，`--symbols` / `--days`）。

**同一个入口现在还管四个地区**（`indexhistory.fetch_daily_history()` 按符号分派）：

| 符号 | 信源 | 备注 |
| --- | --- | --- |
| `^NDX` / `^GSPC` / `^DJI` 等美股指数、美股 ETF | 新浪 `US_MinKService.getDailyK` | 指数用点号前缀 `.NDX` |
| `^HSI` / `^HSTECH` / `^HSCE` | **东财** `push2his`（`secid` 100.HSI / 124.HSTECH / 100.HSCEI） | 新浪港股日线已下线，见 §10.6 第 4 条 |
| `znb_NKY` / `znb_DAX` / `znb_CAC` | **东财** `push2his`（`secid` 100.N225 / 100.GDAXI / 100.FCHI） | 新浪全球指数只有实时；`znb_TPX` 两边都没有 |
| A 股 / 场内基金 | 新浪 `CN_MarketData.getKLineData` | 走 `seed_a_share_history.py`，不在本入口 |

### 9.4 净值历史回填（新增 `dbtools/backfill_nav_history.py`）

`navcli` 只写**最新一条**，没有回填能力，于是净值表缺 10-09 / 10-13。
新脚本用 `EastmoneyNavSource`（与 `navcli` 默认源同一个）补**库里没有那一天**
的行，**已存在的一律不覆盖**。实测与生产端一致：
`10-09=3.1843 / 10-12=3.1817 / 10-13=3.1803 / 10-14=3.1448`。

补完后「基金溢价记录」第一行（`10-14`）已经八格全齐，且与生产端逐格相同：
`3.34 / 3.1803 / 5.02% / 官方EST 3.182 / 误差 0.04% / ^GSPC价格 7805.45`。

**2026-11-09 追加：清单改成自动取全量，不再手写。** 日常例程此前是两条
`--symbols <33 只美股族>` / `--symbols <28 只港股族>`，只覆盖 **61 / 102** 只；
没被点名的 41 只基金 `netvaluehistory` 从来没推进过，净值一直停在 10 月，
`/fund/qdiilof` 的溢价率因此拿旧净值当分母。脚本现在提供：

| 参数 | 作用 |
| --- | --- |
| `--all-funds` | 取 `core.symbolinventory.fund_symbols()` 的 102 只（与 `quotecli --all-qdii` 同源）；可与 `--symbols` 并用取并集 |
| `--report` | 只读体检：锚点取**基金**最新净值日（排除 `forexcli` 写进同一张表的中间价行），按落后天数列出断档并给出可粘贴的 `--symbols` 清单；不联网、不写库 |
| `--stale-days N` | `--report` 的断档阈值，默认 5 个自然日（QDII 净值 T+1~T+2 + 周末） |
| `--apply` 内置备份 | 改调 `backup_db.main([])`，默认 `--lock-tables`，MyISAM 安全；失败即退出不动库。老的 `--skip-backup` 保留 |

东财 `lsjz` 的 `pageSize` 实测**上限 20 条**（`--days 40` 也只回 20 条，
约 20 个交易日，实测 `161125` 返回 `2026-10-09 ~ 2026-11-05`），
所以断档超过 20 个交易日要分多次 / 改分页才能补齐。单测见
`auto/test_backfill_nav_history.py`。

**2026-11-10 一次性追补已执行**：旧例程漏掉的 31 只（19 只原本只有 1~2 行
种子、12 只停在 10-29/10-30）用 `--symbols <清单> --days 40 --apply` 补
**304 行**，内置备份 `E:\mysqldata\stock_data_20261110_112353.sql`。
复跑同清单全部「会补 0 天」，`--report` 报断档 0 只。
**边界**：`--all-funds` / `--report` 只覆盖 `fund_symbols()` 的 102 只；
`SH518800` `SH518880` `SZ159934` `SZ159937` `SZ159985` `SZ161226` 这 6 只
黄金/白银商品 ETF 不在清单里（净值停在 11-03），由另一条链路维护，
别用「断档 0 只」推断它们已补齐。

### 9.5 剩下的断档：是 `fundest` 的**日期口径**，不是渲染

其余各行只有四格（没有官方 EST / 时间 / 误差 / 标的净值），因为
`fundest` 里查不到对应的**净值日**。生产端的 `fundest` 记录落在**净值日**
（`09-30 / 10-08 / 10-09 / 10-12 / 10-13`），我们的落在**估算当天**
（`10-02 / 10-07 / 10-13`）:

| 生产端 | 我们的 |
| --- | --- |
| `09-30 = 3.133` | `10-02 = 3.132846` |
| `10-08 = 3.168` | `10-07 = 3.168052` |
| `10-13 = 3.1817` | `10-13 = 3.1817` ✅ |

也就是说**值是对的，日期差了一天**（10-13 那天恰好对齐）。这是
`fundestcli` 的落库口径问题，牵动"官方 EST 什么时候记"这件事，
不在这次 CK6-3 里改 —— 单独立项。

（2026-10-16 已按 **A 方案**结项：归因与代码核对见本文 §十。结论是两边
落库口径本来就一致，差的是运行节奏与数据新鲜度；代码未改一行。）

### 9.6 顺带：EMA 续写

`stockema50/200` 停在 2026-10-09，而 `stockhistory` 已到 10-13，页面上
「小牛熊分界 / 牛熊分界」两行渲染成 `0 0`、牛熊标记为空。跑
`dbtools/seed_index_history.py --apply`（重抓 800 天并递推 EMA）后
均线段 memo 已是「^GSPC 2026-10-14数据 牛市」。

---

## 十、Plan A：fundest 日期口径对齐（2026-10-16 完成）

> 起源是 §9.5 的遗留项：生产端 `fundest` 日期 `09-30 / 10-08 / 10-09 /
> 10-12 / 10-13`，我们的 `10-02 / 10-07 / 10-13`，"值是对的、日期差一天"。
> 用户 2026-10-16 批准 **A 方案**：轻量数据维护（中间层 ETF 归档每日化 +
> 补 33 只净值 + 每天在美股收盘后窗口跑 `fundestcli`），**不改代码口径**。
> 结论先行：核对两边源码后，**落库口径本来就一致**，差的是运行节奏与数据
> 新鲜度 —— 代码一行未改，全部靠数据修复 + 每日例程结项。

### 10.1 归因：日期口径两边一致，差的是运行节奏与数据新鲜度

先把"记录记在哪一天"从两边源码钉死。

**PHP 写入侧**：

```php
// php/stock/qdiiref.php:134-139  _QdiiReference::EstNetValue()
$strDate = $est_ref->GetDate();            // 中间层（est 标的）的数据日期
if ($this->strOfficialCNY = $cny_ref->GetClose($strDate))
{
    $this->fOfficialNetValue = $this->GetQdiiValue($this->_getOfficialEstVal($strDate), $this->strOfficialCNY);
    $this->strOfficialDate = $strDate;
    $this->UpdateEstNetValue();            // php/stock/fundref.php:91-93
}
//   → StockUpdateEstResult(GetStockId(), GetOfficialNetValue(), GetOfficialDate())
//     php/stock.php:148-159：净值未到才写（"Only update when net value is NOT ready"）
```

**Python 写入侧**（`auto/calc/fundestcalc.py`）：

```python
return EstRecord(ctx["mid_date"], value, None)          # :223  mid_date = 中间层最新数据日期
...
self.store.write_if_ready(stock_id, record.date, record.value, ...)   # :461-463
```

两边记的都是**中间层（est 标的）最新数据的日期**。用户起初的归因
（"生产端记在净值日、我们记在估算当天"）与代码不符 —— 估算当天之所以
等于中间层日期，是因为估算就跑在美股收盘后那一刻。真正的差异是两条：

1. **运行节奏**：生产端 cron 每天在美股收盘后跑；我们手动 sporadic 跑 ——
   国庆周只跑了 10-02 / 10-07 / 10-13 三次，生产端从 09-30 起跑了 5 次；
2. **数据新鲜度**：演示库 ETF 净值/行情缺行、汇率断档、33 只基金净值停在
   10-12，`mid_date` 停在旧日期，记录日期跟着停。

**读数侧**（`php/ui/fundhistoryparagraph.php:36-44`）：QDII 走
`UseSameDayNetValue() == false`（`php/stock.php:381-387`，`in_arrayQdii`
直接 return false）→ 价格日 D 的行查**前一交易日**的 nav 与 fundest。所以
「基金溢价记录」10-14 行显示的是 nav[10-13] 与 fundest[10-13] ——
§9.4 实测那一行 `3.34 / 3.1803 / 5.02% / 官方EST 3.182 / 误差 0.04%` 正是
这个口径（nav 3.1803 = nav[10-13]，EST 3.182 ≈ fundest[10-13]）。

### 10.2 PHP 的记账时间线（每日例程的依据）

以北京时间、美股交易日 X 为例：

| 时刻 | 事件 |
| --- | --- |
| X+1 04:00 | 美股 X 日收盘，新浪数据随后就位 |
| X+1 04:04–04:09 | 生产端写 `fundest[X]`（实测，个别也有 05:05 / 09:13） |
| X+1 晚间 | `nav[X]` 公布（东财 lsjz）；`AdjustFactor()` 被 `UpdateOfficialNetValue()` 的 `InsertDaily` 返回 true 触发，写 `cal[X]`（`qdiiref.php:178-199`） |
| X+2 日起 | 页面价格日 X+1 的行显示 nav[X] 与 fundest[X]，误差列 = fundest[X]/nav[X] − 1 |

`AdjustFactor()` 的三个量**同锚一天 X**：
`cal[X] = QdiiGetCalibration(est[X], cny[X], nav[X]) = est[X] × cny[X] / nav[X]`。
由这条时间线推出两个安全窗口和一个禁忌窗口：

* **晚间 20:00–21:30**（nav[X] 刚到、美股尚未开盘）：锚点 = 净值日 X，
  与 est 日 X 重合，`--funds` 校准与 PHP 同时机、同口径；
* **早上 9:17 也能跑**（2026-10-22 起）。锚点已改成**基金净值日**（§6.2），
  净值没到新的一天锚点就不前进，闸门会跳过 —— **混日在结构上被消掉了**。
  早上跑的价值是"净值昨天更新过但当时没校准"的补写；
* **仍需注意**：早上跑时 est 的**最新行情日**通常比净值日新一天（美股 T−1
  收盘已就位）。这不影响口径（est 取的是净值日那天），但**净值日那天 est
  没数据就写不出**—— 表现为"锚点日 X 没有净值/行情"跳过，属数据缺口。
  所以 `seed_index_history.py --days N --apply` 仍要跑在 `--funds` 之前。

同理，**美股盘中（00:00–04:00）也不要跑 fundestcli**：那时 est 价是未收盘
的快照。证据就是 10-14 00:52 写下的 4 条 fundest[10-13]（见 §10.4②）。

### 10.3 数据修复（A 方案执行记录，2026-10-16 核对）

动库前 `mysqldump` 全库到 `dbtools/_snapshots/before_20261016_plan_a.sql`
（220KB）。背景：当天 9:49 用户自己跑过一次 `autocalibrationcli --funds`
（`--funds` 的唯一调用方），当时 nav 未补到 10-14、汇率还是新浪即期旧值、
ETF 无 10-14 行情 —— 等于按漂移窗口口径 + 旧数据写出了 18 条全错的
10-14 校准行（SZ161125 15471.03 vs 应有 16506.33，+6.69%；SH513100 +19.79%）。

| 补救项 | 入口 | 结果 |
| --- | --- | --- |
| 33 只基金净值补到 10-14 | `dbtools/backfill_nav_history.py --symbols <33 只> --days 40 --apply` | 33 只最新净值日全部 2026-10-14，逐值与生产端一致 |
| 33 只 A 股价格历史 | `dbtools/seed_a_share_history.py --apply` | 40 天（08-13 ~ 10-15） |
| 4 币种官方中间价 | `forexcli --backfill` / `--fill-official-dates` | 276 行，补掉 10-13 / 10-15 断档 |
| 14 个美股符号日线 | `_scratch/_plan_a_us_history.py --apply` | 420 行（09-03 ~ 10-15），补 10-14 缺口；**不算 EMA**（ETF 本无 `stockema` 行，30 天窗口反而是错种子） |
| 13 个中间层 ETF 派生净值 | `autocalibrationcli --sync-nav --apply` | 全部推进到 10-15 |
| 基金层校准重写 | `_scratch/_plan_a_recalibrate.py --apply` | 66 行（33 只 × 10-13/10-14）按 PHP 口径重写：`est × cny / nav` 同锚一日；10-13 那批旧汇率一并重写 |

`_plan_a_recalibrate.py` 先删后写（`DELETE ... WHERE stock_id AND date`），
把 9:49 那 18 条错行覆盖掉。两个脚本都默认 dry-run，`--apply` 才落库。

### 10.4 与生产端对照（2026-10-16 抓取 `qdiicn.php`）

探针 `_scratch/_plan_a_prod_compare.py`（解析「EST网页链接 共41项」表，
生产端 HTML 快照 `_scratch/_qdiicn_prod_1016.html`）：

| 项 | 结果 |
| --- | --- |
| 生产端 EST 表 | **41 行，日期全部 2026-10-15** |
| 本地 `fundest` | **33 条，日期全部 2026-10-15**（`fundestcli` 写入） |
| 值差分档 | 29 只 ≤0.1%；3 只 0.10~0.11%（SH513870 −0.103%、SZ159941 −0.104%、SZ160416 +0.100%）；SZ164906 +2.30% |
| SZ161125 | 本地 3.153480 vs 生产 3.155（−0.048%，SPY 对 ^GSPC 的跟踪差） |

三条残留，逐条坐实：

**① SZ164906 +2.30% 是数据源差异，不是公式错。** 生产端 KWEB 净值走
Yahoo NAV（`sz164906cn.php` 参考行：10-15 = 24.28、昨值 24.75，当日 **−1.9%**），
我们按"净值(t) = 当日收盘价"派生（新浪 10-15 = 24.61、10-14 = 24.51，
当日 **+0.41%**）。比值差 −2.29% 与 fundest 偏差 +2.297% 精确吻合；且
`nav[10-14] 0.8814 × 24.28/24.75 = 0.8647 ≈ 生产 0.865` —— 顺带把
`fundest[D] = nav[D−1] × est[D]/est[D−1] × fx` 整条链端到端验证了。
KWEB 持仓是中概股，官方净值（港股收盘定价）与美股收盘价天然有差；
我们这条口径是 P0.5 定稿的"净值 = 收盘价"（§4.3②），不追着改。

**② 4 只 ETF-est 基金的 10-13 记录超 1% 阈值**（SH513290 +2.43%、
SH513350 −2.73%、SZ159502 +3.22%、SZ159518 −2.73%）：四条全部
`time=00:52:5x`，即 10-14 凌晨**美股盘中**写入，est 价是未收盘快照。
属历史记录，随表格滚动消失；例程避开盘中窗口后不再产生。

**③ 「基金溢价记录」10-15 行仍只有四格**：fundest[10-14] 的写入窗口是
10-15 04:00–20:00，当天在做数据修复、没跑 fundestcli，而晚间 nav[10-14]
一回补，闸门永久关闭，这条记录补不回来。10-16 起的每日例程会让 fundest[X]
逐日归位 —— 今晚 nav[10-15] 公布后，10-15 行即八格全齐。

### 10.5 每日例程（运维口径）

两个窗口，顺序即依赖关系。**净值清单不再手写**：2026-11-09 之前这里是
33 只美股族的逗号串，加上运维 skill 里另一条 28 只港股族，合计只覆盖
**61 / 102 只**；其余 41 只的净值永远停在 10 月（溢价率拿旧净值当分母）。
现在统一用 `--all-funds` 取 `core.symbolinventory.fund_symbols()`，
与凌晨 `--all-qdii` 同一份清单。

**晚间（nav 公布后约 20:00，务必在美股开盘 21:30 前）**：

```bash
# 1) 全部 102 只基金净值（只补不覆盖；--days 3 足够覆盖周末/假期断档）
#    内置备份走 backup_db.py --lock-tables，MyISAM 安全，不必再 --skip-backup
python -B dbtools/backfill_nav_history.py --all-funds --days 3 --apply
# 1b) 每周一次只读体检：打印断档基金与可粘贴的补数清单（不联网、不写库）
python -B dbtools/backfill_nav_history.py --report
# 1c) 估值标的日线（**必须在校准之前**）：八个指数 + GLD
#     GLD 是四只黄金基金（SZ160719/SZ161116/SZ164701/SZ165513）的 est 标的，
#     由 fundestmap 动态注入，不在 --all-qdii / daemon 符号集里。GLD 日线断档
#     会让这四只的 calibrationhistory 不前进，页面 est_cal / cny_cal 卡在
#     校准旧日（2026-11-10 现场：10-30 的 355.78 / 6.7138）。
python -B dbtools/seed_index_history.py --days 5 --apply
# 2) 基金层校准：cal[X]，est / cny / nav 同锚一天 X（与 PHP AdjustFactor 同时机）
python -B auto/cli/autocalibrationcli.py --funds --apply
# 3) 可选：官方中间价回补（有断档时才需要）
python -B auto/cli/forexcli.py --backfill
```

`--funds` 覆盖 **73 只**"有 est 映射、需要校准"的基金：硬编码地区数组
69 只（美股 33 + 港股 28 + 日股 5 + 欧股 3），加 `fundestmap` 表动态注入的
4 只黄金（→ `GLD`）。与净值回填的 102 只**故意不相等**：另外 29 只不做校准，
但一样要有净值来算溢价。断档追补用
`--symbols <--report 输出的清单> --days 40 --apply`（东财一次最多回
20 个交易日），详见 §9.4。

**黄金那 4 只额外依赖 1c 的 GLD 日线**：`calibrate_fund()` 锚基金净值日，
那天没有 GLD 收盘价就照 PHP `AdjustFactor()` 的语义 `return false`、不写
校准行；`est_cal` / `cny_cal` 锚的是 `calibrationhistory` 最新一条的日期，
于是整族停在校准旧日。`--force` 也救不了（闸门没挡，是 est 取不到）。
2026-11-10 全量预演实测：67 只报"已校准过"、4 只黄金报
「GLD 在净值日 11-06 没有净值/行情」，补完 GLD 日线才写得出 11-06。

**凌晨（美股收盘后 04:30–09:00）**：

```bash
# 1) 抓行情：A 股基金与 14 个美股符号的 X 日收盘此时都已定
python -B auto/cli/quotecli.py --all-qdii
# 2) 刷新实时快照（estquote / realtimequote）
python -B auto/cli/quoteupdatercli.py --once
# 3) 归档快照进行情历史：--all-qdii 覆盖 102 基金 + 22 中间层，并自动并入
#    ^NDX / ^GSPC 两个指数（quotecli.INDEX_ARCHIVE_SYMBOLS）。
#    **别只归档指数** —— 2026-11-02 那次停摆就是这么来的：est 走美股 ETF 的
#    14 只基金历史停在 10-23、净值已到 10-29，一只都校不出来，页面
#    est_cal / cny_cal 卡在 10-23。
python -B auto/cli/quotecli.py --archive-snapshot --all-qdii
# 4) 中间层净值推进（净值(t) = 当日收盘价）
python -B auto/cli/autocalibrationcli.py --sync-nav --apply
# 5) 写官方 EST：fundest[X]，X = 中间层最新日期
python -B auto/cli/fundestcli.py
```

`--skip-backup` 只在确认当天已备份过时用（§八 的约定）。汇率说明：凌晨跑
fundestcli 时官方中间价最新只到 X 日（SAFE 当日 09:20 公布），`cny_mid`
取不到 X 日会退到最新一条，量级一致；断档由晚间第 3 步补。

### 10.6 遗留项

1. ~~**`calibrate_fund` 的锚点口径本身还没修**~~ **已于 2026-10-22 结项**：
   锚点改成**基金净值日**（§3.2 / §10.2），混日在结构上被消掉，任何时段都能
   跑 `--funds`。这条留作历史，别再按"漂移窗口禁忌"写运维文档 —— 2026-11-10
   排查黄金族基准价停摆时，误读这条会把根因猜成"跑错时间"，实际是
   GLD 日线断档（净值日那天 est 取不到，见 §10.5 1c）。
2. **中间层 `cal_m` 的每日推进**靠上面凌晨第 3 步归档的指数日线；它不进
   EST 数学（`cal_f` 在估值式里约掉、`cal_m` 根本不出现），只影响
   「校准记录」段落与中间层页面的新鲜度。
3. **ETF 的 `stockema` 行 2026-11-02 已补上**（`seed_index_history.py
   --symbols <ETF 清单> --days 800`，50/200 日均用 800 天窗口递推）。原来的
   说法是「`_plan_a_us_history.py` 刻意不算 EMA」，但那让均线 memo 对 ETF
   恒为空；既然补历史时手里就有 800 天收盘价，顺手算掉更省事。
4. **港股指数日线已补齐（2026-11-02），走东财**。原来的说法是"新浪美股接口
   也认港股指数、只是抓不到"，实际是**新浪的港股日线接口整体下线了**。

   | 用途 | 信源 | 结果 |
   | --- | --- | --- |
   | 港股指数**实时** | 新浪 `https://hq.sinajs.cn/list=hkHSI,hkHSTECH,hkHSCEI` | ✅ 可用，一次批量三个；字段含名称、今开、昨收、最高、最低、现价、成交额、日期时间 |
   | 港股指数**日线** | 新浪 `HK_IndexService.getIndexDailyKLine` / `HK_StockService.getDayKLine` / `HistoryTradeService.*`，在 `/hkstock/api/jsonp.php` 与 `quotes.sina.cn/hk/api/jsonp_v2.php` 两个 host 各试 | ❌ 全部 `{"__ERROR":3,"__ERRORMSG":"Service not valid"/"Service not found"}`；`/stock/hkstock/api/...` 这条旧路径直接 404 |
   | 港股指数**日线**（采用） | 东财 `push2his.eastmoney.com/api/qt/stock/kline/get`，`secid` = `100.HSI` / `124.HSTECH` / `100.HSCEI` | ✅ 可用；历史长度 HSI 8998、HSCEI 8212、HSTECH 2912 根，`klines` 每行 `日期,开,收,高,低,成交量,成交额` |

   **别拿 `HK_MinlineService.getMinLine?symbol=HSI&day=5` 当历史** —— 它只有最近
   5 天的**分钟线**，凑不出 EMA50/200，写进 `stockhistory` 只会污染。
   同理 `HistoryTradeService.getHistoryRange` 返回的 `{"max":"2019-4","min":"1980-4"}`
   是"可查范围"，不是数据。

   ## 落地形态（四处，缺一条港股就还是校不出来）

   | 位置 | 改动 | 为什么 |
   | --- | --- | --- |
   | `auto/updater/indexhistory.py` | `EASTMONEY_HK_SECIDS` + `fetch_eastmoney_history()`，`fetch_daily_history()` 按符号分派 | 新浪那条路走不通，得换成东财 |
   | `calc/fundcalc.qdii_fund_est_symbol()` | 美股表 → 港股 / 日股 / 欧股四个入口合并 | `qdii_est_symbol_map()` 只对应 PHP `QdiiGetEstArray()`（美股 33 只），港股 28 只**一个都不在里面** |
   | `calc/fundcalc.qdii_est_cny_symbol()` | est 标的 → 汇率符号（`^HSI`→`HKCNY` 等） | PHP 是按地区选 Reference 类各带一个汇率；我们只有一条 `calibrate_fund()`，得从 est 反查 |
   | `calc/fundestcalc` | `_resolve` / `_calibration_cny_with_date` / `_fair_cny` / `_spot_cny` 全部按地区取汇率 | 页面「基准汇率 cny_cal」写死 `USCNY` 时，港股显示 6.71 而校准值是按 0.856 算的，EST 差一个数量级 |

   ## 校准值口径（与生产端逐位一致）

   `cal = ^HSI × HKCNY ÷ 基金净值`，锚点 = 基金净值日。实测 SH513600：

   ```text
   23294.42（^HSI 10-30 收盘）× 0.85594（HKCNY 10-30）/ 2.5391 = 7852.635129
   生产端 sh513600cn.php 校准记录 2026-10-30 = 7852.6351     ✅ 逐位一致
   ```

   **日股 / 欧股 2026-11-02 同日补齐**（同一类缺口，同一套做法）：
   `znb_NKY` / `znb_DAX` / `znb_CAC` 在新浪**只有实时、没有日线**
   （`GlobalService.getDailyK` / `getKLine` 一律 `Service not found` 或 404，
   `klc_kl.js` 老路径也是 404；Yahoo 全站 403），改走东财同一条 kline 接口：
   `secid` = `100.N225` / `100.GDAXI` / `100.FCHI`，历史 9707 / 8971 / 9193 根。

   交叉验证用的是"库里 daemon 抓的那 7 行"：

   ```text
   znb_DAX  2026-10-30 库内 25818.2110   东财 25818.2100   差 0.0000%
   znb_CAC  2026-10-30 库内  7969.4200   东财  7969.4200   差 0.0000%
   znb_NKY  2026-10-26 库内 62214.6100   东财 62189.6500   差 0.0401%
   znb_NKY  2026-10-22 库内 63917.2100   东财 62907.8400   差 1.5792%
   ```

   欧洲两行逐位相同 —— 因为例程归档跑在上海早上，那时欧洲还没开盘，
   快照拿到的就是**上一交易日收盘**。日经不同：东京 = 上海 +1 小时，
   上海早上正是东京盘中，快照是**盘中价**，所以差 0.04–1.58%。
   落库后这 7 行被东财收盘价覆盖，是修正而不是回退。

   **`znb_TPX`（东证一部）仍然补不了**：东财 `100.TOPIX` / `100.TPX` 都返回
   `data:null`，新浪只有实时，`klc_kl.js` 404。只有 `SH513800` 用它，
   `--funds` 会如实报"`znb_TPX` 在净值日 2026-10-30 没有净值/行情"。

   ## 外汇即期也必须覆盖港 / 日 / 欧（同日一并补）

   补完日欧日线后还有半档没到位：`fundestcalc._fair_cny()` 对 ETF 用**即期**、
   对 LOF 用**中间价**（PHP `GetForexRef()` 口径），而即期取不到时会**静默退回
   中间价**。`PAGE_FOREX_SYMBOLS` 原来只有 `fx_susdcny` / `fx_susdcnh` 两个，
   于是港 / 日 / 欧 QDII 的参考 EST 全部退化成"用中间价算的值"：

   ```text
   SH513030  参考EST  我们 1.757（中间价 7.457 那档）  生产端 1.746（即期 7.4336）
   ```

   五个符号的新浪返回都是 18 字段、第 9 位是价、第 10 位是名称，
   `SinaForexSource.fetch_rate()` 不用为地区分叉。`fx_sjpycny` 是**每 1 日元**
   （0.0421），与 `JPCNY` 中间价的存储口径一致 —— 生产端页面上的
   "JPYCNY 4.2126 / JPCNY 4.2424" 是每 100 日元，只是显示倍数。

   ## 一次性修复的完整顺序

   ```text
   1. python -B dbtools\backup_db.py
   2. python -B dbtools\seed_index_history.py --days 800 --apply
      （默认清单已含八个：^NDX ^GSPC ^HSI ^HSTECH ^HSCE znb_NKY znb_DAX znb_CAC）
   3. python -B dbtools\backfill_nav_history.py --symbols <要补的基金> --days 30 --apply --skip-backup
      （第 1 步已锁表备份；不加 --skip-backup 会再 dump 一份，见 §9.4）
   4. python -B auto\cli\autocalibrationcli.py --funds --apply
   5. python -B auto\cli\forexcli.py --fx-only      # 五个即期快照
   ```

   第 3 步不能省：这些基金的净值原来只有 **1 条**、停在 10-13，
   锚点定不下来时 `calibrate_fund` 直接跳过（PHP `UpdateOfficialNetValue()`
   返回 false 同款行为），补了日线也白搭。
5. 持仓口径的 8 只（SH501312 等）仍无 est 符号，官方 EST 走不到 ——
   等各自的 `holdings` 数据（§1.3）。
6. **参考 EST 的闸门（2026-11-03 修正）**：`fundestcalc.compute_fair()` 原来只
   照抄了 `FundPairReference::GetFairNetValue()` 的闸门（`cny_date == mid_date`
   → 返回 `None`），于是**港股 / 日股 ETF 的参考 EST 整列消失**。

   两族的指数行情日与本地汇率中间价日**天然是同一天**（都在北京时间当天更新），
   条件天天成立；美元基金的中间层日期比北京晚一天，反而天天不成立 ——
   所以只有港日两族出问题（欧股因时差侥幸躲过，这也是当初没被发现的原因）。

   正确闸门在 `_QdiiReference::EstRealtimeNetValue()`（`php/stock/qdiiref.php:168`）：

   ```php
   if ($this->IsEtfA() || ($cny_ref->GetDate() != $this->strOfficialDate)
                      || ($strDate != $this->strOfficialDate))
   ```

   **ETF 那一项是"或"，无条件算** —— ETF 的参考 EST 走 `GetForexRef()`（即期），
   官方 EST 走中间价，两个值本来就不相等。生产端实测（2026-11-03）：

   ```text
   sh513600cn.php  官方 2.553  参考 2.527   （HKDCNY 0.8473 vs HKCNY 0.8560）
   sh513000cn.php  官方 2.113  参考 2.10    （JPYCNY 4.2126 vs JPCNY 4.2424）
   ```

   本地修复后逐只对齐：`SH513600` 官方 2.552 / 参考 2.526、`SH513000`
   官方 2.113 / 参考 2.098 —— 差 0.001 来自 est 取的是 11-02 收盘价
   （23409.06）而生产端抓的是当时的实时价。

   LOF 仍保留"日期一致就隐藏"：`SZ161124` 本地空、生产端有 0.793，
   差别只在**中间价日期**（我们 HKCNY 最新仍是 11-02，生产端已有 11-03），
   等凌晨 `forexcli` 跑过就一致。

7. **港日实时 EST 已接通**（2026-11-03，commit 见 `patch_hk_jp_futures.py`）。
   `FundReference.get_realtime_ref()` 要中间层引用带 `get_pair_ref()`，
   也就是 `fundpair` 里要有「指数 → 期货」的行。原来只有美元两对
   （`^NDX → hf_NQ`、`^GSPC → hf_ES`），港日缺四样，现已一次补齐：

   - `stock` 表补 `hf_HSI`（`hf_NK` 原本就有，id 16）；
   - `fundpair` 与 `futurespair` 各补两行：`^HSI → hf_HSI`、`znb_NKY → hf_NK`。
     两张表方向一致但用途不同：PHP 的 `FundPairReference` 走 `fundpair`，
     我们的 `futurescalib` 走 `futurespair`，而 `quoteupdater` 靠
     `futurespair` 反推"该抓哪些期货"（`collect_symbols_from_source`）；
   - `FALLBACK_FUTURES` / `FUTURES_SYMBOLS` / `FUTURES_NAMES` 三处符号表各加一条；
   - `StockSymbol.get_time_zone()` 给 `hf_HSI` / `hf_NK` 单独定市场时区。

   **`get_time_zone` 那条是必需的**：PHP `IsSinaFutureUS()` 按 `hf_` 前缀一律
   判纽约，那是"只有 NQ/ES/CL"时代的简化；照抄到 `hf_HSI` 上会把新浪给的
   **上海时间戳**换成前一天 20:59（上海 09:59 = 纽约前一日 20:59），日期都错，
   校准永远对不齐。现在 `hf_HSI` → `Asia/Shanghai`（与 `^HSI` 同区）、
   `hf_NK` → `Asia/Tokyo`（与 `znb_NKY` 同区），`check_adjust_time` 直接比 HH:MM。

   **`futurescli` 的两个洞也补了**：校准段原来只遍历 `FALLBACK_FUTURES`
   （表里配了也走不到），且 `--apply` 时仍调 `apply=False` —— 校准值**从未真正
   落库**（`calibrationhistory` 里 `^NDX`/`^GSPC` 那两条是
   `dbtools/calibrate_futures.py` 写的历史值）。现在遍历「表 + 兜底」合并结果，
   `--apply` 真写库。

   验收（2026-11-03 10:05，恒指交易中）：`SH513600` 官方 2.558302 / 参考
   2.533119 / **实时 2.533565（来源：期货 hf_HSI）**，`^HSI` 校准值 0.998713
   （期货 23443.56 / 指数 23475.x）。

8. **恒生科技 / 国企没有实时 EST，是数据源缺。** 新浪
   `hf_HSTECH` / `hf_HSCEI` / `hf_HTI` / `hf_HHI` 实测全部返回空串，只有
   `hf_HSI` 与 `hf_NK` 两个港股/日经期货可用。生产端 `sh513010cn.php`
   （恒生科技）与 `sh510900cn.php`（国企）同样**没有**「实时EST」列 ——
   我们页面也一致（表头五列，无实时EST）。**不要去"补"这两行配对**，
   配了也只会让列出现但值恒空。

9. **日经在 11-03 校准不了是休市，不是 bug。** 日本 11-03 是文化日，
   东证休市：新浪 `znb_NKY` 与东财 `100.N225` 都停在 11-02 15:30（东京），
   而 `hf_NK` 期货照常交易 → 日期不等 → 无法校准 → 日经实时 EST 回落中间层
   快照。等 11-04 开盘自然通。**判断方法**：`futurescli` 的
   `_misalign_reason` 会打印「日期不等（期货 2026-11-03 vs 指数 2026-11-02）」。

10. **「跟踪代码」列的来源与空缺（2026-11-03）**。列值在
    `web/services/fund_list_service.py:447`：
    `profile.est_symbol or profile.pair_symbol`，两级兜底 ——

    * **est 符号**：`core/stockdata.py::_get_est_symbol()` 依次查
      `qdii_est_symbol()` / `qdii_hk_est_symbol()` / `qdii_jp_est_symbol()` /
      `qdii_eu_est_symbol()`，全是 `calc/fundcalc.py:71-118` 里**硬编码**的
      字典与 if 分支（照抄 PHP `qdiiref.php:19-77` 的 `QdiiGetEstArray()` 一族）；
    * **pair 符号**：`fundpair` 表（本地 78 行）的配对标的。

    `est` 为空不只是"少一格显示"：`_QdiiReference` 不 `SetEstRef()` →
    `GetOfficialNetValue()` 拿不到 → **官方EST / EST日期 / 溢价整组为空**。
    `/fund/qdiilof` 26 只里 12 只有值，正好等于上述两张表的覆盖集合。

    14 只空的分四类，**多数不是漏配**：

    | 类别 | 基金 | 缺什么 |
    |---|---|---|
    | 有单只对标、代码没配 | 四只黄金（`SZ160719`/`SZ161116`/`SZ164701`/`SZ165513` → GLD） | 已由 `fundestmap` 补上 |
    | 走**持仓加权** | `SZ163208`（油气FOF）、`SZ160644`（港美互联网）、`SZ160216`（国泰商品）、`SZ161815`（银华通胀） | `holdings` 数据 + `HoldingsReference` |
    | 对标存在但库/源都没有 | `SH501225`（SOXX/SMH）、`SZ164824`（INDA）、`SH501312` | 标的符号要入库并回填历史 |
    | 没有 ETF 对标 | 原油 4 只 + 商品 1 只 | 跟踪原油期货 / 一篮子商品 |

    **别硬配前两类**：`SZ163208` 按 `USO` 算差 3.1%、按 `XOP` 差 0.6%，
    都没对上生产端的 1.482，说明它走的是 `HoldingsReference`；
    `SZ160644` 生产端「参考数据」只列汇率、没有任何标的，官方 EST 1.558
    同样来自持仓加权 —— 配 `KWEB` 会算出 1.609（差 3.3%），把生产端
    -0.9% 的溢价显示成 -4.1%，明显误导。实测数字见
    `dbtools/patch_fund_est_map.py` 的 `EXCLUDED`。

    **第二批（2026-11-03 晚）**：`SZ165513` 补齐，`SZ160216` / `SZ161815`
    仍然排除。判据是生产端页面里有没有那行「GLD → GC 校准 10.9441」的
    对照表 —— 有它才说明估值走单标的：

    | 基金 | 生产端对照表 | 按 GLD 算出 | 生产端官方 EST | 结论 |
    |---|---|---|---|---|
    | `SZ165513` 信诚商品 | **有** | 0.9034 | 0.904 | 补 |
    | `SZ160216` 国泰商品 | 无 | 0.585 | 0.578 | 差 1.1%，排除 |
    | `SZ161815` 银华通胀 | 无 | 1.162 | 1.122 | 差 3.5%，排除 |

    三只的净值都已回填（各 19 天，`backfill_nav_history.py`）—— 即使不配
    est，溢价记录表也能多出这 19 行，不算白做。`SZ161815` 是抗通胀组合
    （不止黄金），硬配 GLD 会把溢价算反。

    **库里覆盖层 `fundestmap`**（`id` = 基金 stock.id，`est_symbol` 文本）：
    优先级 **表 > 硬编码**，删行即回退。注入点只有一个 ——
    `MySqlFundDataSource.__init__` 调 `calc.fundcalc.set_extra_est_symbols()`，
    因为它是 web 每请求与全部 CLI/daemon 都必经的地方；放别处必有调用方
    静默用回硬编码。`qdii_est_symbol_map()` **不含**覆盖（它是
    `QdiiGetEstArray()` 的镜像），全表用 `qdii_fund_est_symbol_map()`。

    验收（2026-11-03，生产端 `sz160719cn.php` / `sz161116cn.php` /
    `sz164701cn.php`）：官方 EST 本地 1.742 / 1.446 / 1.514，
    生产端 1.743 / 1.447 / 1.514 —— 差 0.001 来自 est 取 11-02 收盘
    361.65，生产端抓的是当时实时价。

11. **黄金基金的「实时 EST」是两级链路，比港股多一跳（2026-11-03 晚）**。
    `^HSI` 那种是「指数 → 期货」，黄金是 **基金 → GLD（美股 ETF）→ hf_GC**：

    ```text
    SZ161116 ──est──> GLD ──期货──> hf_GC
    （A股LOF）      （美股ETF）    （纽约黄金，新浪 hf_GC 有实时价）
    ```

    缺的**不是**「GC 没有行情」——`hq.sinajs.cn/list=hf_GC` 实测一直有
    （3934.69 @ 13:44:34，时间戳是**纽约时间**，`hf_` 前缀判纽约在本例是对的）。
    缺的是后一跳的三样配置，缺任何一样都**静默**回落：

    1. `stock` 表没有 `hf_GC` 行（`hf_CL` 纽约原油倒是一直有，id=15）；
    2. `futurespair` / `fundpair` 都没有 `GLD → hf_GC` ——
       `futurescalib.future_symbol_of('GLD')` 返回 `None`，
       `fundestcalc._futures_realtime_quote()` 直接短路，**实时 EST 整列消失**；
    3. `calibrationhistory` 里 GLD 没有任何校准行 —— 有配对也算不出。

    `cal` 在这里是**单位换算**（≈10.9441 = 3957 / 361.55），不是指数的 ≈1.0，
    量级体检窗口要按 `[5, 20]` 判。来源见 `README_futures_migration.md` §3.1。
    一次性补齐用 `dbtools/patch_gold_futures.py`。

    验收（2026-11-03，四只黄金，官方 / 参考 / 实时）：

    | 基金 | 本地 | 生产端 |
    |---|---|---|
    | `SZ160719` | 1.742 / 1.742 / **1.731** | 1.743 / 1.743 / 1.728 |
    | `SZ161116` | 1.446 / 1.446 / **1.437** | 1.447 / 1.447 / 1.438 |
    | `SZ164701` | 1.514 / 1.514 / **1.504** | 1.514 / 1.514 / 1.504 |
    | `SZ165513` | 0.903 / 0.903 / **0.898** | 0.904 / 0.904 / 0.897 |

    `SZ160719` 实时差 0.003 是净值停在 10-30（生产端已更新）导致的，
    不是链路问题 —— 它三档里官方 / 参考完全对上。


