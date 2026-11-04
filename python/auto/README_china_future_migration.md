# A 股商品基金（国内期货）迁移

`woody/res/chinafuturecn.php` → `/category/chinafuture`。

这条链路与外盘期货（`README_futures_migration.md`）**是两族**，只是都叫"期货"：

| | 外盘（`hf_`） | 国内商品（`nf_`） |
| --- | --- | --- |
| 符号 | `hf_NQ` / `hf_GC` / `hf_HSI` | `nf_AU0` / `nf_AG0` / `nf_M0` |
| 数据源 | 新浪外盘期货 | 新浪内盘期货（`InnerFuturesNewService`） |
| 抓取进程 | `futurescli` | 普通行情链路（`quoteupdatercli`） |
| 引用类 | `QdiiReference` + 配对 | **`FundPairReference`** |
| 汇率 | 有（USCNY/HKCNY/…） | **没有**（`GetDefaultCny()` = 1.0） |
| 配对层数 | 两层（基金 → ETF → 指数 → 期货） | **一层**（基金 → 期货） |
| 官方 EST 取价 | 指数/ETF 收盘 | 豆粕/白银取**均价**，黄金取结算价 |

## 一、生产端对照（2026-11-04 11:30，逐位核对过）

| 符号 | 名称 | 参考数据（现价） | 官方 EST | 参考 EST |
| --- | --- | --- | --- | --- |
| SH518800 | 黄金ETF国泰 | 8.436 | 8.374 | — |
| SH518880 | 黄金ETF华安 | 8.046 | 8.047 | — |
| SZ159934 | 黄金ETF易方达 | 8.407 | 8.367 | — |
| SZ159937 | 黄金ETF博时 | 8.028 | 7.978 | — |
| SZ159985 | 豆粕ETF华夏 | 2.116 | 2.054 | 2.049 |
| SZ161226 | 国投白银LOF | 1.415 | 1.342 | 1.328 |

本地页面（`/category/chinafuture`）现在是这 12 个数字逐位一致，含两列溢价
（0.74% / -0.01% / 0.48% / 0.63% / 3.04%+3.27% / 5.42%+6.53%）。

**黄金没有参考 EST 列**不是缺失：`FundPairReference.GetFairNetValue()`
（`fundpairref.php:256`）在"官方日期 == 配对标的日期 且不是 `ExceptGoldCN`"
时返回 `false`。黄金走 `GetNetValue(date)`（结算价），与官方 EST 同源，
参考值没有信息量。

## 二、数据补齐（`dbtools/patch_china_future.py`）

四样东西缺一不可，缺哪样都能"页面 200 但空白"：

| 表 | 内容 | 缺了会怎样 |
| --- | --- | --- |
| `stock` | 3 只期货 + 6 只基金的名字 | 参考表显示「**库里无此符号**」 |
| `fundpair` | 基金 → `nf_XX0`，**一层** | 同上（引用对象建不出来） |
| `calibrationhistory` | `cal = 期货价 ÷ 净值` | 官方 EST = 期货价本身（差 100~8800 倍） |
| `fundposition` | **1.0**（商品基金满仓） | 官方 EST 被往上一日净值方向拉 0.05 倍 |

`stockhistory`（期货日线，`adjclose` = **结算价**）与 `netvaluehistory`（基金净值）
是"有数才出数"的原料：前者由 `dbtools/seed_china_future_history.py` 回填
（默认 800 个交易日），后者走 `backfill_nav_history.py`。

### 2.1 为什么要写 `fundposition = 1.0`

`fundpairref.php:91` 的 `EstFromPair` 最后一步是

```php
FundAdjustPosition($this->GetPosition(), $fVal, $fLastCalibrationVal)
// = last + (val - last) × 仓位
```

默认仓位是 `IsLofA() ? 0.95 : 1.0`（`stocksymbol.php:1003`）。`SZ161226`
是深市 LOF，本地原先一行 `fundposition` 都没有 → 落到 0.95：

* 官方 EST：`1.3546 + (1.3422 - 1.3546) × 0.95 = 1.3429` → 页面 **1.343**，
  生产端 1.342；
* 参考 EST：1.33 vs 1.328，溢价 6.43% vs 6.53%。

生产端这六只的仓位都是 1.0（商品基金持有的是期货合约本身，不是股票篮子）。
其余五只是 ETF，默认就是 1.0，写进去只是把"数据"和"默认值"对齐。

## 三、行情：`nf_` 三个必须知道的坑

### 3.1 符号**必须原样大写**

`get_sina_symbol()` 原先会把它当成普通 A 股基金，拼成 `gb_nf_au0`：

| 请求 | 结果 |
| --- | --- |
| `nf_AU0` | 有数据 |
| `nf_au0` | **空串** |
| `gb_nf_au0` | **空串** |

修法是在 `core/stocksymbol.py` 的 `get_sina_symbol()` 里加一支
`if self.is_sina_future_cn(): return self.str_symbol`（PHP 走
`LoadSinaFutureData` → `DebugGetSinaFileName(GetSymbol())`，没有这一支）。

症状特别有迷惑性：`realtimequote` 不写行 → 参考数据整行空白、EST 表整段消失，
但页面照样 200、不报任何错。

### 3.2 均价要单独落库（两处）

新浪内盘期货那一行的第 **28** 个字段是**当日均价（VWAP）**，
`StockReference._on_sina_future_cn()` 把它存进 `str_vwap`。商品期货的净值
按均价计，所以它必须进快照表：

| 表 | 谁读 | 补丁 |
| --- | --- | --- |
| `estquote.vwap` | `fundestcalc`（估算器那条路） | `patch_estquote_vwap.py` |
| `realtimequote.vwap` | `FundPairReference.get_official_net_value()` | `patch_realtimequote_vwap.py` |

两份**不是重复**：PHP 只有一个 `MyStockReference` 对象，`strVWAP` 是它身上的
字段，页面与命令行共用；本项目把快照拆成两表，于是均价两边都要有。
只补 `estquote` 那一份时，页面那条路拿不到均价，官方 EST 退回**结算价**
（豆粕 2.0846 vs 2.054、白银 1.3546 vs 1.342）。

`refquote.apply_vwap()` 负责把快照里的 `vwap` 装回引用对象（空值不覆盖）。

### 3.3 日线的 `adjclose` 是**结算价**

PHP `_updateStockHistory()` 把 `strSettlePrice` 作为 `WriteHistory` 的第 5 个
参数（`mystockref.php:143`），而 `DailyCalibration()` 里
`$pair_ref->GetNetValue($strDate)` 读的就是 `adjclose`。

实测 AU0 2026-11-03：收盘 841.8 / 结算 846.12；AG0：11655 / 12013（差 3%）。
写成收盘价会让官方 EST 系统性偏低。`auto/updater/asharehistory.py` 的
`parse_cn_future_response()` 已经带出 `s` 字段。

## 四、计算：为什么要按符号换引用类

PHP `StockGetFundReference()`（`php/stock.php:317`）：

```php
if ($ref = StockGetQdiiReference($strSymbol))                     return $ref;
else if (in_arrayQdiiMix($strSymbol))                             return new HoldingsReference($strSymbol);
else if (in_arrayChinaIndex($strSymbol) || in_arrayChinaFuture($strSymbol))  return new FundPairReference($strSymbol);
return new FundReference($strSymbol);
```

`web/services/category_page_service.py` 的 `build_fund_reference()` 就是这份
分派的迁移。**不分派不会报错，只会整段消失**：`QdiiReference` 的官方 EST
必须靠汇率的收盘价才算得出来（`qdiiref.php:133` 的 `if ($this->strOfficialCNY = ...)`），
国内期货没有汇率 → 走"汇率缺失"兜底去读 `fundest` 表 → 这些基金从没算过 →
`official_net_value` 恒 `None` → `classify_symbols` 把它们挡在 `funds` 外 →
「估值」表整段不出现。

`EstimateCalculator` 那条路（`fundestcalc`）仍然有用：它服务 `fundestcli`
写 `fundest` 表，也服务基金列表页。两边口径在 `fundestcalc` 里已经对齐
（`qdii_est_cny_symbol()` 对 `nf_` 返回 `None` = 不折汇率；官方 EST 先取
`estquote.vwap`；`est_cal` 取 `adjclose`；`compute_fair()` 对黄金直接
`return None`）。

## 五、脚本与命令

```bash
# 一次性：补 stock / fundpair / 校准 / 仓位（幂等，默认预演）
D:/python/python.exe -B dbtools/patch_china_future.py           # 预演
D:/python/python.exe -B dbtools/patch_china_future.py --apply

# 期货日线（adjclose = 结算价），默认 800 个交易日
D:/python/python.exe -B dbtools/seed_china_future_history.py --apply

# 两个 vwap 列（幂等）
D:/python/python.exe -B dbtools/patch_estquote_vwap.py --apply
D:/python/python.exe -B dbtools/patch_realtimequote_vwap.py --apply

# 日常：行情快照（含均价）→ 页面
cd python/auto && D:/python/python.exe -B cli/quoteupdatercli.py --once
```

`default_est_symbols()` 要能列出 `nf_AU0` / `nf_AG0` / `nf_M0`，否则 daemon
不写 `estquote` —— 三个符号登记在 `core/symbolinventory.py` 的
`CHINA_FUTURE_SYMBOLS`。

## 六、测试

* `auto/test_china_future.py`（16 条）：汇率符号判空、`ExceptGold` 判据、
  均价取舍（豆粕取 / 黄金忽略 / 快照过期忽略 / 缺失回落）、期货日线解析
  （结算价入 `adjclose`）。
* `auto/test_refquote.py`：`apply_vwap` 三条（装填 / 空值不覆盖 / 无属性跳过）。
* `auto/test_orderbook.py`：`realtimequote.vwap` 落库与两级可选列回退
  （全列 → 只有盘口 → 老结构）。
* `web/tests/test_qdii_page.py`：`china_future` 走 `FundPairReference`。

## 七、仍未做

1. **`SH513800`（`znb_TPX`）**：东财与新浪都没有东证 TOPIX 的历史数据，
   与 A 股商品无关，记在这里只是免得下次又去找一遍。
2. **商品基金的 `AverageCalibraion` 自动触发**：与外盘期货同一条待办 ——
   本地 `FundPairReference` 的 `auto_write` 默认关，校准交给
   `autocalibrationcli`。生产端是"每次渲染顺手校准"，本地刻意不照抄。
3. **`nf_` 五个以上品种**：目前只有黄金/白银/豆粕三只（`ChinaFutureGetSymbolArray()`
   就这六只基金）。新浪内盘期货的符号列表（`nf_XX0` 连续合约）没有枚举文档，
   新增品种要人工确认代码。
