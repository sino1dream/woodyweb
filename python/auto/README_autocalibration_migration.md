# 自动校准迁移（接进页面访问路径）

把 PHP 的 ``FundPairReference::DailyCalibration()`` 迁到 Python，并接进页面。

| PHP | 本版 |
| --- | --- |
| ``php/stock/fundpairref.php:188`` ``DailyCalibration()`` | ``autocalibration.AutoCalibrator.calibrate_middle()`` |
| ``php/stock/fundpairref.php:213`` ``CalcFactor()`` | ``AutoCalibrator.calc_factor()`` |
| ``php/stock/fundpairref.php:174`` ``AverageCalibraion()`` | ``futurescalib.FuturesCalibrator.calibrate()``（期货那一路） |
| ``woody/res/php/_qdiigroup.php:81`` 页面触发 | ``web/services/fund_list_service.py`` 的 ``_auto_calibrate()`` |

新增 ``python/auto/updater/autocalibration.py`` 与 ``python/auto/test_autocalibration.py``（19 个测试）。

## 一、触发点

PHP 在页面装配时调用（``woody/res/php/_qdiigroup.php:69-82``）：

```php
foreach ($arLev as $strSymbol)          // $arLev 是 'QQQ' 这种**中间层**符号
{
    $leverage_ref = new FundPairReference($strSymbol);
    $this->ar_leverage_ref[] = $leverage_ref;
    if ($strSymbol == 'QQQ') { ... UpdateInvescoNetValue($strSymbol); }
    else                     { YahooUpdateNetValue($leverage_ref); }
    $leverage_ref->DailyCalibration();      // ← 抓完净值，顺手校准
}
```

本版接在 ``FundListService.get_group()`` 里（对应 `/fund/qdiilof`、`/fund/qdiietf`）：

```python
symbols = definition["symbols"]()
self._auto_calibrate(symbols)      # 必须在 _build_row 之前
rows = [self._build_row(symbol) for symbol in symbols]
```

三条约束（都写进了 ``_auto_calibrate`` 的 docstring）：

1. **校准对象是中间层**（``QQQ``），不是基金；
2. **不能影响页面**：异常吞掉并记进 ``self.calibration_notes``；
3. **同分组只做一次**（按中间层符号去重）—— 同页多只基金常共用 QQQ。

开关：环境变量 ``WOODY_AUTO_CALIBRATE=0`` 可关闭（默认开启，与 PHP 行为一致）。
测试替身没有 ``connection``，会静默跳过。

## 二、两个必须搞清的层级问题（都踩到了）

### 2.1 校准的是**中间层**，不是基金（第一版搞错了）

``DailyCalibration()`` 里用的是 ``$this->GetStockId()``，而调用它的
``$leverage_ref`` 是 **中间层**（``QQQ``）。所以：

* **被校准**：``QQQ`` / ``SPY``
* **用来算**：它的 ``pair_ref``（``^NDX`` / ``^GSPC``）
* **式子是**：``校准值 = pair 的净值 × 汇率 / 本符号的净值``

实测数据印证：``QQQ`` 净值日期 10-02 ≠ 校准日期 10-01 ⇒ 该校准；
而三只**基金**层符号的净值日期与校准日期相同 ⇒ 已校准过。

第一版把校准对象写成基金，方向是错的 —— 那样会一直在校准基金，
而真正需要按当天美股重标的的中间层永远不更新。

### 2.2 归一化尺度导致量级差 750 倍（第二版踩到）

PHP 直接用 ``$net_sql->GetCloseNow($strStockId)`` 当分母，因为它那边**中间层的
``netvaluehistory`` 存的就是真实每份净值**（QQQ = 749.378）。

本项目演示库把中间层净值**归一化成了 1.0**（见 ``seed_demo_data.MARKET_LAYERS``
的注释，那是刻意的设计），于是照抄公式会得到：

```text
校准值 = ^NDX 31099 / 1.0 = 31099     ← 生产端是 41.111，大了 750 倍
```

数学上没错（恒等式 ``est_m × cal_m`` 守恒：``31099 × 1.0 ≈ 41.111 × 749.378``），
但页面显示 ``31099`` 看起来像坏了。

修法：先反推"该部署尺度下的真实每份净值"，再套同一个公式：

```text
真实每份净值 = 汇率 / 本符号原校准值
             = 6.705 / 0.008945 = 749.58      （与生产端 QQQ 净值 749.378 吻合到 0.03%）

新校准值 = 配对标的点位 × 本符号原校准值 / 汇率
         = 31099.79 × 0.008945 / 6.705 = 41.49   （生产端 41.111 ✓）
```

见 ``AutoCalibrator._implied_middle_nav()`` 与 ``test_autocalibration.ScaleConversionTest``
（该测试明确断言结果落在 30–60 区间，防止量级再跑偏）。

## 三、"改中间层校准值会不会动到基金估值" —— 一次代价不小的错误结论

### 3.1 勘误

我当时用受控实验（把 ``QQQ``/``SPY`` 的校准值 **×1000**，重建估值）得到：

```text
改之前                            改之后
SZ161130 估值=4.545199908386476   SZ161130 估值=4.545199908386476   ← 完全相同
SZ161125 估值=3.132799923118264   SZ161125 估值=3.132799923118264
SH513100 估值=2.037900214942492   SH513100 估值=2.037900214942492
```

于是判定"中间层校准值对基金估值零影响"，并据此把 ``QQQ=41.488229`` 写进了库。

**这个结论是错的。** 那个实验用的是
``PalmmicroFundCalculator.est_net_value(symbol)``（**不带** source）——
而这条路径**恰好是对 ``cal_m`` 不敏感的那一条**。真实管线不是它。
写入之后的实测结果：

```text
SZ161130 官方 EST = 20245.200164     （应为 4.59）
```

页面**不报错**，只显示一个荒谬数字。

### 3.2 三条路径的实测对比

| 路径 | 改 ``cal_m`` 后 |
| --- | --- |
| ``est_net_value(symbol)``（不带 source） | 4.5452 → 4.5452（**不敏感**） |
| ``est_net_value(symbol, est_source())`` | 0.229 → 8.465（敏感但绝对值不对） |
| ``fundestcalc.compute_official()`` | 4.59 → **20245**（真实管线） |

### 3.3 正确的做法

``fundestcalc._qdii_value()`` 里：

```python
est_quote_ref = cny_ref / cal_m
ratio = est_value / est_quote_ref          # 对 cal_m 线性
```

所以 ``est_value × cal_m`` 这个**乘积必须守恒** —— 演示库里 ``est_value`` 是
中间层实时价（QQQ ≈ 757），``cal_m`` 是归一化尺度的 ``0.008945``，
两者**成对**，不能只改一个。

修法（幂等，改完用真实管线自证）：

```powershell
python -B sql\fix_middle_calibration.py --apply
```

```text
SZ161130   估值=4.592143   净值=4.5452   偏差= 1.03%  OK
SZ161125   估值=3.162158   净值=3.1328   偏差= 0.94%  OK
SH513100   估值=2.060055   净值=2.0379   偏差= 1.09%  OK
```

### 3.4 估值守卫（写入前拦截）

``autocalibration.EstimateGuard``：写入前**临时写库 → 跑真实管线 → 比对净值
→ 还原**，偏差 > ``GUARD_TOLERANCE``（5%）就拒写并给原因。实测：

```text
QQQ=41.488229   拒    偏差 468757% > 容差 5%
QQQ=0.008945    放行  偏差 1.09%
```

守卫有两条必须守住的纪律，都有测试盯着：

1. **必须还原**（``finally`` 里写回原值，异常路径也不例外）——
   否则"检查者变成破坏者"，而且它是以"检查"的名义破坏，最难发现；
2. **必须真的检查了** —— 守卫最初挂在 ``est_net_value()`` 上，
   六个候选值（含明显错误的 ``208519``）**全部放行**，等于没守。
   所以测试断言"写候选值 / 写回原值"两个动作都发生且顺序正确，
   而不是只断言返回值。

## 四、闸门

```php
$strDate = $net_sql->GetDateNow($strStockId);      // 净值最新日期
if ($strDate == $cal_sql->GetDateNow($strStockId)) return;   // 已校准 -> 不重做
```

本版照抄。**一个陷阱**：``_calibration_date()`` 对"没有校准记录"必须返回
``None`` 而不是空串，否则"从没校准过"可能被误判成"已校准过"。
``test_autocalibration.GateTest.test_no_calibration_record_is_not_treated_as_same_date``
盯着这一点。

写库用 ``WriteDaily()`` 语义：**已有就不动**（双保险，闸门已先挡一层）。

## 五、实测结果（2026-10-07）

```text
中间层   净值日期      配对      配对值          算出的校准值
QQQ    2026-10-02   ^NDX    31098.7787    41.48822900395227
SPY    2026-10-02   ^GSPC   7790.5298     10.122460196510065
```

页面上的基金行**完全未变**（这是正确性的关键证据）：

```text
SZ161130 | 易方达纳斯达克100LOF | ^NDX | 0.95 | 32.079543 | 2026-10-01 | 4.796 | 5.52%
SZ161125 | 易方达标普500LOF   | ^GSPC | 0.95 | 22.110973 | 2026-10-01 | 3.275 | 4.54%
SH513100 | 国泰纳斯达克100ETF | ^NDX | 1.0  | 13.66412  | 2026-10-01 | 2.352 | 15.41%
```

溢价与生产端一致（4.54% / 5.52% / 15.41%）。

## 六、配对来源的三条路径

``_pair_symbol()`` 按可靠性排序：

1. ``fundpair`` 表（``id`` = 本体，``stock_id`` = 配对标的是）；
2. :data:`MIDDLE_PAIR_FALLBACK` 内置映射（``QQQ → ^NDX``、``SPY → ^GSPC``）；
3. ``fundcalc.qdii_est_symbol_map()`` 反查。

**第 2 条必需**：demo 的 ``fundpair`` 只存"基金 → ETF"（``SZ161130 → QQQ``），
**没有"ETF → 指数"**（QQQ 不在 ``id`` 侧）。少了它自动校准会一直
"没有配对标的是"而静默失效 —— 这正是实测踩到的。

配对标的的值（``^NDX`` 点位）也有兜底：净值表 → 行情表 → **新浪实时点位**
（``^NDX`` → ``gb_ndx``，实测可用）。因为两个指数在本项目里都没落库。

## 七、测试

``python/auto/test_autocalibration.py``（19 个），覆盖：

* ``CalcFactor()`` 的基本算式与坏输入；
* ``_pair_symbol()`` 的三条路径（含"表里没有时走内置映射"）；
* **尺度换算**（断言结果落在生产端量级 30–60，不是 31098）；
* 隐含净值接近真实的 QQQ 净值（749 左右）；
* 闸门（同日跳过 / 日期不同则校准 / 无记录不被误判）；
* 写库语义（新行写入、已有不覆盖、``time`` 非空）；
* "基金 → 中间层"的杠杆替代解析。

全量：`auto` **515 个**、`web` **59 个**。

## 八、配方：把演示数据搞乱后怎么恢复

本轮排查时因为误读了 ``SEED_DATE``（是 ``2026-01-02`` 这个"原点"，
不是真实行情日期），重播种把数据写乱过。后来加了备份/还原工具：

```powershell
cd F:\woody\woodyweb\python

python -B sql\backup_demo_tables.py       # 备份 10 张演示表到 sql\_demo_backup.json
python -B sql\restore_demo_tables.py      # 预演还原
python -B sql\restore_demo_tables.py --apply
python -B sql\show_calibration.py         # 看 calibrationhistory 全表 + 中间层净值
python -B sql\clear_middle_calibration.py --apply   # 清中间层某天校准值（便于重测）
python -B sql\purge_index_calibration.py --apply    # 清误写到指数上的校准行
```

**教训**：``seed_demo_data.py`` 的 ``MARKET_LAYERS`` / ``NET_VALUES`` 写的都是
``SEED_DATE`` 那一行；真实行情行由 ``sql/`` 下按实际日期写入的脚本负责。
两者是**不同日期的两行**，不要为了对齐生产端的数值去改种子里的数。

## 九、仍未完成

1. **``AverageCalibraion()`` 的自动触发**：PHP 在 ``CheckAdjustFactorTime()``
   通过时顺手做"当天多次校准取平均"。本版自动校准走 ``WriteDaily()``
   语义（已有就不动），没有做平均；期货那一路（``futurescalib``）做了平均。
2. **原油/日本/欧洲的中间层**：``MIDDLE_PAIR_FALLBACK`` 只有 ``QQQ``/``SPY``。
3. **页面展示校准动作**：``self.calibration_notes`` 已经攒了记录，
   但模板还没渲染出来（PHP 那边也不显示，只是写库）。
