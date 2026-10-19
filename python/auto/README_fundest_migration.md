# 官方 EST（`fundest`）迁移

把 PHP 侧「官方 EST 的记录与显示」迁移到 Python。这是 161130 / 161125 实时 EST
的前置一步（实时 EST = 参考 EST + 期货，见 skill 第 1.4 节）。

| PHP | 本版 |
| --- | --- |
| `php/stock.php` `StockUpdateEstResult()` | `fundeststore.FundEstStore.write_if_ready()` |
| `php/stock.php` `StockCompareEstResult()` | `fundestcalc.EstimateCalculator.compare_with_net_value()` |
| `php/stock/qdiiref.php` `EstNetValue()` | `fundestcalc.EstimateCalculator.compute_official()` |
| `php/stock/qdiiref.php` `_getOfficialEstVal()` | `fundestcalc.EstimateCalculator._official_est_value()` |
| `php/stock/fundpairref.php:252` 的 20:55 闸门 | `FundEstStore.DEFAULT_GATE_HHMM` |
| `php/sql/sqldailytime.php` `DailyTimeSql('fundest')` | `fundeststore.FundEstStore` |
| 页面里「官方EST / 时间 / 误差」三列 | `history_service._row_fundhistory()` |

新增文件：

* `python/auto/sql/fundeststore.py` —— `fundest` 表读写（含闸门）
* `python/auto/calc/fundestcalc.py` —— 官方 EST 计算、记录、误差告警
* `python/auto/cli/fundestcli.py` —— 命令行（记录 / 查看 / 误差扫描）
* `python/auto/test_fundest.py` —— 27 个测试

## 一、`fundest` 存的到底是什么（最容易误解的一步）

**不是官方净值**，而是「**官方净值出来之前**那一刻算出来的估值」。

依据 `StockUpdateEstResult()` 的注释与判断：

```php
function StockUpdateEstResult($strStockId, $fNetValue, $strDate)
{
    if ($fNetValue)
    {
        $net_sql = GetNetValueHistorySql();
        if ($net_sql->GetRecord($strStockId, $strDate) == false)
        {   // Only update when net value is NOT ready
            $fund_est_sql = GetFundEstSql();
            $fund_est_sql->WriteDaily($strStockId, $strDate, strval($fNetValue));
        }
    }
}
```

原注释 **"Only update when net value is NOT ready"** 就是全部要点：
真实净值一旦到货，这一天就再也不写估值了。这样事后拿真实净值一比，
"当时算得准不准"一目了然 —— 这就是「官方 EST + 时间 + 误差」三列的用途。

写入时机是**用户访问页面**（PHP 的估值在构造函数里算），所以记录里的时间是
"第一次访问的时刻"。本版做成 CLI 定时任务（`fundestcli.py`），也可以由页面触发。

## 二、三个写入条件

| 条件 | 出处 |
| --- | --- |
| ① 当天真实净值还没到（`netvaluehistory` 无该 `(stock_id, date)` 行） | `StockUpdateEstResult()` 的注释 |
| ② 中间层时刻早于 **20:55** | `fundpairref.php:252` `if ($this->pair_ref->GetHourMinute() < 2055)` |
| ③ 该日期还没有记录（有就不覆盖） | `DailyTimeSql::InsertDaily()` 的 `if ($this->GetRecord(...)) return false;` |

**条件 ②默认不启用**（`gate_hhmm=None`，加 `--strict` 才启用）。原因：演示环境里的
行情快照可能是几天前的，套上 20:55 会一行都写不进去，不利于验证。
需要严格复刻 PHP 行为时用 `--strict`。

**条件 ③不可省**：这一列的价值在"**第一次**算出来的那个数"，每次访问都覆盖就变成
"最后一次访问时的收盘值"，失去比较意义。

## 三、官方 EST 的计算链路

对应 `_QdiiReference::EstNetValue()`：

```text
官方 EST = est_f
         = (1 - p) × nv + p × nv × (est / est_cal) × (cny / cny_cal)
```

其中：

* `nv` / `cal_f` —— 基金自己的单位净值与校准值；
* `est` / `est_cal` —— 中间层报价的当前值与**建这份校准值那天**的值；
* `cny` / `cny_cal` —— 当天与校准时的美元人民币中间价；
* `p` —— 仓位（`AdjustPosition` 的 `fRatio`）。

### 3.1 公式怎么从 PHP 推出来（2026-10-14 重写）

这条式子不是猜的，是 `qdiiref.php` 三个函数叠起来的：

```php
AdjustFactor():                                    // 校准值重算（新净值到货时）
    $strEst  = $est_ref 当日的值;                   // 净值优先，否则收盘价
    $strCNY  = $cny_ref->GetClose(当天);
    $this->fFactor = QdiiGetCalibration($strEst, $strCNY, $this->GetPrice());
    //                = $strEst * $strCNY / 基金价        （qdiiref.php:3）

GetQdiiValue($strEst, $strCNY):                     // 估值
    $fVal = QdiiGetVal($strEst, $strCNY, $this->fFactor);   // = est * cny / cal_f
    return $this->AdjustPosition($fVal);            // = p*fVal + (1-p)*nv_cal
```

把 `cal_f = est_cal × cny_cal / nv_cal` 代回 `QdiiGetVal`，汇率与标的各出现
**一次**，就是上面那个式子。Python 侧 `calc_with_calibration()` 的式子是

```text
est_f = (1 - p) × netvalue + p × hedge_value × cny / calibration
```

两者相等要求 `hedge_value = nv × cal_f / cny × (est / est_cal) × (cny / cny_cal)`
—— 这正是 `_qdii_value()` 的算法。**注意 `nv × cal_f` 除的是当前汇率 `cny`**，
对应 `stockdata._attach_hedge_value()` 的 (A) 式：估值公式里 `hedge_value`
后面还要乘一次 `cny`，先除掉汇率才不会被算两遍。

**生产端验算**（`sh513100cn.php`，2026-10-13，仓位 1.00）：

```text
参考 EST = est_now × cny_now / cal_f
        = 30569.48 × 6.6925 / 100380.914 = 2.0379    页面 2.038   ✓
官方 EST ≈ 当天净值（历史表里 2.056 / 2.072 / 2.063 / 2.041 逐日精确相等）
```

### 3.2 基准价 `est_cal` 必须取自真实数据，不能用恒等式反推

`est_cal` 由 `_est_quote_ref()` 取：校准日当天的中间层值（净值优先）→ 校准日
之前最近一条行情价 → 中间层最新日期的值 → 取不到按比例 1.0。

**曾经的实现用恒等式 `est_quote_ref = cny_ref / cal_m` 反推**，那条恒等式
（`est_m × cal_m = cny_ref`）只对**旧尺度**的中间层校准值成立 —— 演示库早期把
QQQ 播成 `0.008945`（= 汇率/报价）。2026-10-13 按生产端口径重算后 QQQ 变成
`41.58`（= 指数/净值），恒等式失效：基准价被算成 `6.69/41.58 = 0.16`，
比值放大 **4648 倍**，`/qdii` 与 `/fund` 页官方 EST 显示成 9625（应约 2.05）。
详见 pitfalls #61。

### 3.3 中间层符号要用「杠杆替代」解析，不能只用 `est_symbol`

真实库里 `SZ161130` 的 `est_symbol` 是 **`^NDX`（指数）**，而 `^NDX` 在
`netvaluehistory` / `stockhistory` 里**都没有行** —— 指数点位是从新浪直接抓的、
不落这两张表。必须与构建器同顺序解析（`stockdata.py:279-284`）：

```python
hedge_symbol = profile.est_symbol or profile.pair_symbol   # ^NDX
leverage_symbol = get_leverage_hedge_symbol(symbol)        # QQQ
if hedge_symbol and leverage_symbol:
    hedge_symbol = leverage_symbol                          # -> QQQ
```

**踩过的错**：只用 `est_symbol` 会让日期取不到 → 官方 EST 静默算不出来，
表现为"0 只写入"，很难查。

### 3.4 汇率有三层兜底

`EstNetValue()` 里 `$cny_ref->GetClose($strDate)` 失败时还有第三步
`$this->strOfficialCNY = $cny_ref->GetPrice();`（用最新实时汇率）。

缺了这条，只要**中间层的日期比汇率新**（很常见：美股收盘早于次日 9:15 的中间价发布），
官方 EST 就整个算不出来。实测正是这个原因。

## 四、误差告警

对应 `StockCompareEstResult()`（`php/stock.php:127`）：

```php
$fPercentage = StockGetPercentage(floatval($strNetValue), floatval($strEstValue));
if (abs($fPercentage) > 1.0)
{
    trigger_error('Net value estimation error ...');
}
```

即 |真实净值 → 当时估值| 的偏差超过 **1.0%** 就告警。本版把告警累积在
`EstimateCalculator.warnings`，由 `fundestcli.py --check` 打印。

## 五、页面显示

「基金溢价记录」在 `fundest` 有该符号记录时会多出四列
（对应 `fundhistoryparagraph.php:76-85`，**没有记录时整列不显示**，与 PHP 一致）：

```text
日期 | 价格 | 净值 | 溢价 | 官方EST | 时间 | 误差 | 标的净值
```

**口径要点**：`fundest` 按**净值日期**查，不是行情日期。依据
`fundhistoryparagraph.php:39-42` —— netvalue 与 fundest 用**同一个** `$strDate`
（QDII 就是前一交易日 `GetDatePrev()`）。

> 我一度以为该用行情日期（因为生产端 EST日期 列显示 09-30 而净值是 09-29 的），
> 改成行情日期后一行都查不到，回看 PHP 源码才发现理解错了，已改回。

## 六、实测结果

```text
$ python -B auto\cli\fundestcli.py
闸门：跳过 20:55，只要求当天真实净值未到

已写入 3 只：
  SH513100   2026-10-02  2.037898     00:40:06
  SZ161125   2026-10-02  3.132846     00:40:06
  SZ161130   2026-10-02  4.545196     00:40:06
```

三只的官方 EST 都精确等于各自的单位净值（2.0379 / 3.1328 / 4.5452），说明链路正确。

**页面上那三列目前仍是空的**，原因是演示库存在**日期错位**：

```text
fundest 记录日期        = 2026-10-02（中间层 QQQ 的最新日期）
stockhistory 最新日期   = 2026-09-30
  ⇒ QDII 的净值日期     = 2026-09-29
```

而 `fundest` 是按**净值日期**查的，两者没有交集。

**这是数据现状，不是代码问题。** 用受控实验验证过接线：
把那条记录临时改到 `2026-09-29`，页面立即显示

```text
2026-09-30 | 4.796 | 4.545 | 5.52% | 4.545 | 00:40 | 0
```

与生产端 `sz161130cn.php` 的 `4.545 | 04:00 | 0` 逐项对应；验证后已还原。
该实验已固化为测试
（`test_history.test_fundhistory_shows_official_est_time_and_error_when_recorded`）。

要让它自然出现，需要给 `stockhistory` 补上中间层最新那几天的行情
（当前库里 `stockhistory` 只有 09-29 / 09-30 两天）。

## 七、命令行

```powershell
cd F:\woody\woodyweb\python

python -B auto\cli\fundestcli.py                  # 记录一批基金的官方 EST
python -B auto\cli\fundestcli.py --symbol SZ161130
python -B auto\cli\fundestcli.py --strict         # 启用 PHP 的 20:55 闸门
python -B auto\cli\fundestcli.py --list           # 看已记录了哪些
python -B auto\cli\fundestcli.py --list SZ161130  # 看某只的全部记录
python -B auto\cli\fundestcli.py --check          # 扫描误差并告警
python -B auto\cli\fundestcli.py --est            # 并排显示三个 EST + 溢价
python -B auto\cli\fundestcli.py --est --symbol SZ161130
python -B auto\cli\fundestcli.py --api            # 打印口径说明
```

## 八、参考 EST 与实时 EST（同一批迁移）

三个 EST 由同一套代码算，差别只在"用哪个中间层值 + 哪天的汇率"。
`fundestcalc.py` 用 `_resolve()` 把公共部分抽出来，三个入口各传不同的值。

| EST | 中间层取值 | 汇率 | 入口 | PHP |
| --- | --- | --- | --- | --- |
| **官方** | **净值**，取不到才用当日行情价 | 中间层日期那条，缺则最新 | `compute_official()` | `_getOfficialEstVal()` |
| **参考** | **当日行情价** | **当前**汇率 | `compute_fair()` | `GetFairNetValue()` |
| **实时** | **实时快照价** | 当前汇率 | `compute_realtime()` | `GetRealtimeNetValue()` |

### 8.1 参考 EST 何时"不显示"

`GetFairNetValue()`（`php/stock/fundpairref.php:256`）：

```php
$strOfficialDate = $this->GetOfficialDate();   // = pair_ref 的日期 = 中间层日期
if ($this->cny_ref)
{
    if ($strOfficialDate != $this->cny_ref->GetDate())
        return $this->EstFromPair($this->pair_ref->GetVal(), $this->cny_ref->GetVal());
}
if ($this->pair_ref->IsSinaFutureExceptGoldCN() || ($strOfficialDate != $this->pair_ref->GetDate()))
    return $this->EstFromPair($this->pair_ref->GetVal());
return false;      // 两者都一致 -> 不显示
```

`GetOfficialDate()` 对 `FundPairReference` 返回的就是 **`pair_ref` 的日期**，
所以第二个条件恒为假 —— **真正决定参考 EST 出不出现的是"汇率日期"那一条**。

即：**汇率日期与中间层日期一致时，参考 EST 按定义为空**（那时它与官方 EST 相同，
没有信息量）。这不是算不出来，是设计。

### 8.2 途中修掉的三个真 bug

**① `_qdii_value` 里写死了取实时快照，忽略了 `est_value` 参数。**

```python
# 错：不管调用方传什么，都去取实时价
current_quote = self._middle_quote(hedge_symbol, data)
ratio = current_quote / est_quote_ref
```

后果：**官方 EST 也被实时价影响、不再等于基金净值**（实测 4.5452 变 4.5833），
而且三个 EST 恒等。修法：用传进来的 `est_value` 算 `ratio` ——
它才是三者分界的地方。

**② `cny_ref`（校准时汇率）缺失，回落到"当前汇率"。**

`_qdii_value` 用 `data["CNY"]` 当缩放基准，它**必须与 `calibrationhistory`
那条记录同期**（`QdiiGetCalibration(est, cny, netvalue)` 用的就是它）。
`_build()` 原来没提供，于是回落到传进来的 cny，
`est_quote_ref = cny / cny_ref` 被约掉，参考 EST 就算不对
（实测把 4.4110 算成 4.6139）。

修法：新增 `_calibration_cny()` —— 按校准日期去查 `USCNY`，
没有就退到该日期之前最近一条。

**③ `_getEstPrice` 的兜底顺序写反了。**

PHP 是"当日行情价 → 上一交易日收盘价"，我实现成了直接取昨收。
演示库里中间层的 `stockhistory` 只到 09-30 而最新日期是 10-02，
于是官方 EST 用了昨收 742.03、偏 1%（4.5017 而非 4.5452）。

修法：`_middle_day_quote()` 按 **当前报价 → 当日行情 → 上一交易日** 的顺序兜底。

### 8.3 为什么演示库上三个 EST 完全相同

```text
符号        官方 EST    参考 EST    实时 EST    最新价   官方溢价
SH513100   2.037898   2.037898   2.037898   2.352   15.41%
SZ161125   3.132846   3.132846   3.132846   3.275    4.54%
SZ161130   4.545196   4.545196   4.545196   4.796    5.52%
```

**输入相同、输出必然相同**：演示库里

* 中间层的实时快照价（749.58）= 它 `stockhistory` 里的值；
* 汇率的"最新值"（10-01 的 6.705）就是官方 EST 用的那条。

所以这不是 bug，而是**数据恰好对齐**。溢价与生产端一致（4.54% / 5.52% / 15.41%），
说明链路是对的。

要看到三者分开，需要"中间层当日价 ≠ 实时快照价"或"当前汇率 ≠ 官方用的汇率"。
`test_fundest.ThreeEstTest` 用这样的输入逐项验证：

```text
官方 EST（中间层净值 1.0、中间层日期汇率 6.705） = 4.5452
参考 EST（当日价 749.58、当前汇率 6.60）        = 4.4776
实时 EST（实时价 780.00、当前汇率 6.60）        = 4.6501
```

参考 / 实时相对净值分别是 **-1.49% / +2.31%**：汇率那一项与跌幅 1:1（-1.57%），
实时价高出当日价 4.06% 再叠上来 —— 两个因子各算一次，这就是公式
`est_f = (1-p) × nv + p × nv × (est/est_cal) × (cny/cny_cal)` 的样子。

> ⚠️ 上面 ② 里"实测把 4.4110 算成 4.6139"是**当时**三个 bug 叠加的组合值。
> 今天再看到 **4.4110** 这个数，含义完全不同 —— 它是"汇率被算两遍"
> （`(cny/cny_cal)²`）的签名，见 §十。

## 九、测试

`python/auto/test_fundest.py`（40 个），覆盖：

* `GetHourMinute()` 与 20:55 闸门（含 20:54 可写 / 20:55 起拒写）；
* 三个写入条件各自的判定；
* "已有记录不覆盖"；
* 官方 EST 数值链路（测试替身给出的官方 EST 精确等于基金净值 4.5452）；
* 误差告警的阈值上下两侧（<1% 不告警、>1% 告警）；
* `TIME` 列的 `timedelta` 规范化（`timedelta(seconds=2406)` → `00:40:06`）；
* **三个 EST 的区分**（`ThreeEstTest`，8 个用例）：官方用净值/中间层日期汇率、
  参考用当日价/当前汇率、实时用快照价，以及"汇率日期一致时参考 EST 为空"；
* **生产尺度回归**（`ProductionScaleTest`，3 个用例，2026-10-14 新增）：
  用演示库 SH513100 的真实尺度（`cal_f = 100390.448365`、`cal_m = 41.579312`）
  钉住"官方 EST 留在净值量级"，并断言汇率相对变化**只出现一次**；
* **接口名漂移护栏**（`InterfaceNameTest`，2 个用例，2026-10-14 新增）：
  把 `fundestcalc.py` 里每个 `getattr(self.source, "名字")` 与两个真实现对一遍。

全量：`auto` **1057 个**、`web` **670 个**。

## 十、2026-10-14 修的三件事（EST 放大 4648 倍）

生产端口径统一（commit `64b8261a`）之后，`/qdii` 与 `/fund` 的 EST 表炸了：
SH513100 官方 EST **9625.333**（生产 2.049）、SZ161130 **20386.593**（4.572）、
SZ161125 **3550.039**（3.182）。三个缺陷叠在一起，逐个修：

| # | 缺陷 | 症状 | 修法 |
| --- | --- | --- | --- |
| 1 | 基准价用恒等式 `cny_ref / cal_m` 反推 | 尺度错位，放大 4648 倍 | `_est_quote_ref()` 按"校准日 → 校准日之前 → 中间层最新日期"取真实报价 |
| 2 | `_net_value()` 查 `get_net_value_close`（接口真名 `get_netvalue_close`） | **净值表从来没被读过**；校准时汇率恒 `None`，`cny/cny_cal` 被约成 1 | 改真名 + `InterfaceNameTest` 护栏 |
| 3 | 贡献值除了 `cny_ref` 而非当前汇率 `cny` | 汇率被算两遍，人民币贬 1% 估值动 2% | 改除 `cny`（与 `_attach_hedge_value()` 的 (A) 式一致） |

第 2 条是这次才发现的：`getattr` 取数写错名字**不报错**，只是静默走兜底；
测试替身又照着错名定义了一份，所以 1052 个单测全绿而真实库全错。
第 3 条同一个坑在 `_attach_hedge_value()` 里早就踩过并写进
`README_name_and_forex.md` §2.4①，只是 EST 这条路径上又犯了一次。

### 修完的实测（真实演示库，`auto/_scratch/_est_scale_verify.py`）

```text
符号        官方EST    参考EST    实时EST   | 官方(生产)  参考(生产)  实时(生产)
SH513100    2.0793     2.0845     2.0845   | 2.0490      2.0380      2.0420
SZ161130    4.6338     4.6447     4.6447   | 4.5720      4.5770      -
SZ161125    3.1845     3.2203     3.2203   | 3.1820      3.1820      -
```

页面（`/qdii`、`/fund/qdiietf/SH513100`）同步恢复成 2.079 / 2.084 / 2.084。

残留 1.2%～2.3% 的偏差来自**演示库数据陈旧**，不是公式：中间层 QQQ 的
`stockhistory` 只到 10-01、`netvaluehistory` 到 10-07、`estquote` 快照是
10-09 抓的，而生产端是 10-13/14 的活数据。补上历史回填与常驻抓取
（P0.3 / P0.4）之后应当收敛。

## 十一、下一步

按 skill 第 1.4 节，实时 EST 还需叠加**期货**才算完整：

| 品种 | 叠加的期货 | 新浪代码 |
| --- | --- | --- |
| 纳指100 | NQ | `hf_NQ` |
| 标普500 | ES | `hf_ES` |
| 原油 | CL | `hf_CL` |
| 日经 | NK | `hf_NK` |

现在 `compute_realtime()` 用 `estquote` 快照当"实时数据"，
换成期货代码即可 —— 取数与口径都已就位。
