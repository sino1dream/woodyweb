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
| P0.4 | 实时链路（`quoteupdatercli --daemon` → `realtimequote` + 盘口 + `estquote`） | 待做 |
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

### 4.3 P0.5 的两个待办（都已定位，动代码前必读）

**① 中间层净值缺口。** QQQ/SPY 各只有 10-07 一条净值，`^NDX`/`^GSPC`
一条都没有。注意 `navcli` **永远补不了**它们：`nav_code_of()` 只认 6 位
数字代码，QQQ/SPY 没有（SH000869 那种有代码的才需要 §3.3 的排除）。
唯一现成的填充通道是 `autocalibrationcli sync-nav`（派生净值），
而它正踩在下面这个坑上。

**② `_implied_middle_nav` 的旧尺度恒等式。**
`autocalibration._implied_middle_nav()` 用 `cny / cal_m` 反推净值，
该恒等式**只在种子尺度成立**（cal_m = 0.008945 → 6.705/0.008945 = 749.58，
与真实 NAV 吻合）。2026-10-13 按生产端口径重算后 QQQ 的 cal_m 已是
**41.5793**（= 指数/净值），同一式子给出 6.69/41.58 = **0.161** ——
差 4648 倍。`usnavsource.fetch_middle_nav()` 与 `calibrate_middle()`
都走它，所以**现在跑 `sync-nav --apply` 会把 0.161 写进
`netvaluehistory`**，重新制造 pitfalls #60/#61 那一类事故
（净值优先的 `_official_est_value` 会直接把它当 `est_now`）。

修复方向（P0.5 定稿，先写自检再写库）：生产尺度下正确的派生是
**净值 = 配对标的（指数）当日收盘 / cal_m**（QQQ：30883.148/41.5793 = 742.6，
正是价格尺度）；或者更简单 —— 直接用 QQQ/SPY **当日收盘价**当净值
（ETF 净值与价格只差千分位，见生产端 `netvalueclosecn.php`），
彻底不碰校准值。无论选哪条，写入前加**量级自检**（净值应落在当日价格
的 ±20% 内，否则拒绝写），把这类尺度事故挡在写库之前。

### 4.4 顺带确认

页面加载时的自动校准（`fund_list_service.py:189` 的
`calibrate_middle(..., apply=True)`）**没有**改写校准历史 ——
闸门（净值日期 == 校准日期）与估值守卫都拦住了，`calibrationhistory`
仍是回填前的 10 行（10-09 基金 / 10-07 中间层）。

---

## 五、P0.3 及以后要遵守的约定

* 动演示库数据前先 `mysqldump` 全库到 `python/dbtools/_snapshots/`
  （已 gitignore，先例 `calib_before_20261012.sql` / `calib_before_20261013.sql`）；
* 数据脚本**默认 dry-run**，`--apply` 才写库，把"旧值 / 新值 / 倍数 / 算式"
  打成表给人核（`auto/_scratch/_calibration_recount.py` 是范例）；
* 每步四件套：单测 + 本文档对应小节 + 真实数据对照（`_scratch` 探针）+
  踩坑记录（`.dsh/skills/palmmicro-php-to-python/references/pitfalls.md`）；
* 写入顺序：`stock` 行 → `fundpair` 行 → 历史回填 → 实时链路，
  每一步都可单独重跑（P0.8 的 bootstrap 就是把这四步串起来）。
