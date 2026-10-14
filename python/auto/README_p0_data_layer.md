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
| P0.3 | 历史数据回填（`quotecli` → `stockhistory`，`navcli` → `netvaluehistory`） | 待做 |
| P0.4 | 实时链路（`quoteupdatercli --daemon` → `realtimequote` + 盘口 + `estquote`） | 待做 |
| P0.5–0.7 | 校准链路 + `fundest` + 期货实时 EST | 待做 |
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

## 三、P0.3 及以后要遵守的约定

* 动演示库数据前先 `mysqldump` 全库到 `python/dbtools/_snapshots/`
  （已 gitignore，先例 `calib_before_20261012.sql` / `calib_before_20261013.sql`）；
* 数据脚本**默认 dry-run**，`--apply` 才写库，把"旧值 / 新值 / 倍数 / 算式"
  打成表给人核（`auto/_scratch/_calibration_recount.py` 是范例）；
* 每步四件套：单测 + 本文档对应小节 + 真实数据对照（`_scratch` 探针）+
  踩坑记录（`.dsh/skills/palmmicro-php-to-python/references/pitfalls.md`）；
* 写入顺序：`stock` 行 → `fundpair` 行 → 历史回填 → 实时链路，
  每一步都可单独重跑（P0.8 的 bootstrap 就是把这四步串起来）。
