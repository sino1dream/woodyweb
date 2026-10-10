# StockHistory 数值核心迁移（第一刀）

`php/stockhis.php`（586 行）里的**纯数值**部分。对应
`python/auto/stockhistory.py`。

## 一、为什么分刀，这一刀切在哪

`StockHistory` 一个类的构造要做四件事：

| # | 做什么 | 对应 PHP | 本步 |
|---|--------|----------|------|
| 1 | **算数** —— 均线 / 布林两式 / 二次方程 / 牛熊 | `_estSma`、`GetQuadraticEquationRoot`、`_estBollingerBands`、`_estNextBollingerBands`、`GetBullBear` | ✅ **已完成** |
| 2 | **查库** —— 取日/周/月三套收盘序列并切片 | `_calcStartDate`、`_getDayWeekMonthData`、`_isWeekEnd`、`_isMonthEnd` | 🟡 切片判据已完成，查库未做 |
| 3 | **读写配置文件** —— 缓存 SMA 结果 | `_configSMA` → `$cfg->set_var/read_var/save_data` | ⛔ 未做（有外部副作用） |
| 4 | **渲染** —— 表格与回调 | `php/ui/smaparagraph.php`（225 行） | ⛔ 未做 |

把第 3 件单独隔出来的理由：它是**外部可见副作用**（回写工作区配置文件），
迁错了会让测试污染工作区，也会在并发下互相覆盖。需要单独评审。

## 二、⚠️ 输入顺序：数组是**最新在前**

`_getDayWeekMonthData()` 用 `GetStockHistorySql()->GetFromDate()` 取数，而
`SqlDailyClose::BuildOrderBy()` 返回的是 `ORDER BY date DESC`（`php/sql/sqldailyclose.php:31`）。

所以 `afClose[0]` 是**最新**的收盘价，`_estSma()` 累加的是**开头**那几个。

```python
est_sma([10.0, 20.0, 30.0, 40.0, 100.0, 200.0], 3)   # (10+20)/2 = 15.0
```

这一条不搞清楚，所有均线会上下颠倒 —— 而序列本身还在合理区间里，不报错。

## 三、`_estSma` 的 off-by-one（照抄）

```php
$iNum = $iAvg - 1;          // ← 元素个数
...
return strval($f / $iNum);  // ← 除数也是 iAvg - 1
```

**元素个数与除数都是 `avg - 1`**，所以 `est_sma(x, 5)` 是"最近 **4** 个的均值"。
看着像 bug，但它是原意；改掉会让所有均线整体偏移。

实测（PHP harness 与 Python 两侧一致）：

```text
_estSma([100.0, 100.021, 100.079, 100.174], 5) = 98.2025    # 前 4 个之和 ÷ 4
```

另一个照抄点：**元素不足时 PHP 循环自然结束、仍除以 `iNum`**（把缺的数据当 0）。
短序列（新上市标的）会因此得到偏低的均线 —— 这是原行为。

`avg = 1` 时 `iNum = 0`，PHP 8 抛 `DivisionByZeroError`；本版显式抛
`ValueError`，不让它变成 `inf`。

## 四、布林两式的差别（最易混的地方）

两条式子都是"解二次方程反推标准差"，但**参数完全不同**：

| | `_estBollingerBands` | `_estNextBollingerBands` |
|---|---|---|
| 取样个数 | `iAvg - 1` | `iAvg - 2` |
| `factor` | `iAvg - 4` | `iAvg - 8` |
| `a` 里的常数 | `4 * iNum` | `16 * iNum` |
| `b` 里的常数 | `8` / `2` | `32` / `2` |
| `c` 里的常数 | `4 * ΣX²` | `16 * ΣX²` |
| `sigma` 除数 | **`/ 2`** | **`/ 4`** |

抄错一个下标**不会崩**，只会让轨道慢慢偏 —— 页面上"看起来还是个布林带"。

实测同一组报价两条式子结果确实不同（这也是自检的第 ④ 项，防止只迁了一式）：

```text
当日：(102.56813910146684, 96.79712405642816)
T+1 ：(102.91423119518971, 96.22232436036587)
```

`GetQuadraticEquationRoot` 的**返回顺序有意义**：先 `(-b + √Δ)/2a`（对应上轨），
后 `(-b - √Δ)/2a`。判别式为负返回 `false` → 本版 `None`。
`a == 0` 时 PHP 抛除零，本版显式抛 `ValueError`。

## 五、⚠️ `GetDayOfWeek()` 是 `date('w')`，不是 ISO

`php/class/year_month_day.php:93` 返回 `$this->local[6]`，来自 `date('w')`：

```text
周日 = 0   周一 = 1   周二 = 2   周三 = 3   周四 = 4   周五 = 5   周六 = 6
```

**不是** ISO-8601 的"周一 = 1 … 周日 = 7"。`_isWeekEnd` / `_isMonthEnd` 里的
`>=` / `>` 比较依赖这个 0 基映射，换成 `datetime.weekday()` 会静默错位一天。

```python
_day_of_week("2026-10-09")   # 周五 → 5
_day_of_week("2026-10-11")   # 周日 → 0
```

## 六、已知偏差：`GetNextTradingDayTick()`

`_isMonthEnd()` 在没有"下一天"时，会用"下一个交易日"代替今天比月份。
PHP 那个 `GetNextTradingDayTick()` 走**节假日表**（`IsHoliday()` 至少识别新年）：

```php
function GetNextTradingDayTick()
{
    $iTick = $this->GetNextWeekDayTick();
    $next_ymd = new YearMonthDay($iTick);
    if ($next_ymd->IsHoliday())  return $next_ymd->GetNextTradingDayTick();
    return $iTick;
}
```

本版简化为"下一个工作日"（跳过周六周日），**不识别节假日**。

**落差范围**：只有当月末最后一个工作日恰好是节假日时才会差。正常周末行为一致。
对照脚本里 180 个 `_isMonthEnd` 用例**全部一致**（说明常用日期上没差），
这条偏差没有触发到具体用例，所以 `KNOWN_DIVERGENCES` 是空的。

## 七、`MaxMin` 的 `== false` 陷阱

```php
function Init($fMax, $fMin)
{
    if ($this->fMin == false && $this->fMax == false)   // ← PHP 松散比较
    {   $this->fMin = $fMin;  $this->fMax = $fMax;   }
}
```

PHP 里 `0.0 == false` 为**真**，所以：

* 初值是 `false` 而不是 `0.0`，显式复刻（`self.f_max = False`）；
* 只调一次 `Init(0.0, 10000000.0)` 之后再 `Init(a, b)` **不生效**；
* 但 `Init(0.0, 0.0)` 之后两个字段都成了 `0`，`== false` 仍为真，
  所以**再调一次 `Init` 会生效** —— 这是 PHP 的怪行为，照抄并钉在测试里。

用 `is None` 判断会把"初值 0.0"错当成"已初始化"。

## 八、`_getColorAndOrderArray` 依赖调用方顺序（已显式处理）

PHP 里 `$mm` 只在遇到 `D` 键时才 `Init(0.0, 10000000.0)`。所以**若字典没有
`D` 键**，`$mm->fMax/fMin` 一直是初始的 `false`：

* `$mm->Fit($fVal)` → `$fVal > false && $fVal < false` → 恒为假；
* 于是 EMA 全部落到 `yellow` 分支。

这不是"顺序对错"的问题 —— 只要没有 `D` 键就必然如此。实测
`_php_sma_compare.py` 的 `only_ema` 用例正是这个结果（两条 EMA 都 `yellow`），
本版照抄。

`arOrder` 的生成有两点要注意：

* `sort($arVal, SORT_NUMERIC)` 是**数值**排序 —— 字符串排序会把 `"100.00"`
  排在 `"20.00"` 前面；
* `array_unique` 在已排序数组上去重，等价于升序去重；本版用
  `dict.fromkeys` 保持首次出现顺序。

## 九、对照验证：436 例，0 意外差异

```powershell
cd F:\woody\woodyweb\python\auto
$env:PATH = "D:\xampp\php;" + $env:PATH

python -B _php_sma_compare.py              # 436 例
python -B _php_sma_compare.py --selftest   # 证明它抓得到注入的错误
```

| 对照项 | 用例数 |
|---|---|
| `_estSma`（6 条序列 × 4 个周期） | 24 |
| `GetQuadraticEquationRoot` | 6 |
| `_estBollingerBands` + `_estNextBollingerBands` | 12 |
| `_isWeekEnd` + `_isMonthEnd`（4 个"今天" × 45 天 × 2 条分支） | 360 |
| 配色（4 组 × 各键） | 25 |
| 纵轴标尺 | 4 |
| `MaxMin` | 8 |
| **合计** | **436** |

**自检**（`--selftest`）在内存里注入两种错误，断言抓得到：

```text
① 基准                        与 PHP 不一致：0
② 把 _estSma 的除数改成 avg    抓到 20 例（php=98.2025 python=98.234）
③ 布林带 sigma 的 /2 改成 /4   抓到 5 例
④ 两条布林式子结果必须不同      当日 ≠ T+1 ✓
```

## 十、测试

`python/auto/test_stockhistory.py` —— 46 例，钉的是**PHP 的原意**
（含看着像 bug 的地方），不是"我觉得应该怎样"。

| 测试类 | 钉住什么 |
|---|---|
| `EstSmaTest` | `avg-1` 既是个数又是除数、最新在前、元素不足仍除 `iNum`、`avg=1` 抛错 |
| `QuadraticEquationRootTest` | 两根顺序（+√Δ 在前）、判别式 0 / 负、`a=0` 抛错 |
| `BollingerBandsTest` | 旗舰数值对照 PHP、两式必须不同、sigma 除数 2 vs 4 |
| `DayOfWeekSemanticsTest` | `date('w')` 映射（周日 0、周五 5） |
| `WeekEndTest` / `MonthEndTest` | 倒序数据下"下一天"的语义、两条分支 |
| `MaxMinTest` | `== false` 语义、`Init` 只生效一次、`Fit` 开区间 |
| `ColorAndOrderTest` | 四态配色、数值排序去重、无 `D` 键的退化行为 |
| `ModuleIsImportCleanTest` | 数值核心不依赖 pymysql / configparser / 文件读写 |

全量：`auto` **774** / `web` **251**。

## 十一、下一刀要做什么

**第 2 刀 —— 查库与切片**：

* `_calcStartDate()`：取最近 2 条记录、跳过"当前交易日未收盘"那条
  （`_ignoreCurrentTradingData`，要 `IsStockTradingHourEnd()`）；
* `_getDayWeekMonthData()`：取 620 天、按 `_isWeekEnd`/`_isMonthEnd` 切出
  日/周/月三套序列。**注意遍历方向与切片方向的关系**（倒序遍历 +
  "下一天"是时间更早的那条），这里最容易搞反；
* `NeedAfterHourEst()`：`$now_ymd->GetHourMinute() > 1520`（15:20）。

**第 3 刀 —— 配置读写**（单独评审）：`_configSMA` 会回写
`$this->stock_ref->strConfigName` 指向的 INI 文件，需要先明确：

1. 文件路径怎么来（依赖 `MyStockReference`，未迁）；
2. 并发下多个请求同时写同一份配置会不会互相覆盖；
3. 测试里怎么避免污染工作区（临时目录？内存实现？）。

**第 4 刀 —— `smaparagraph.php`**：纯渲染（列头 + 回调），到那一步很直白。
