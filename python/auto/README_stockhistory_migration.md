# StockHistory 迁移（第一刀 + 第二刀）

`php/stockhis.php`（586 行）迁移到 `python/auto/stockhistory.py`。

## 一、为什么分刀，切在哪

`StockHistory` 一个类的构造要做四件事：

| # | 做什么 | 对应 PHP | 状态 |
|---|--------|----------|------|
| 1 | **算数** —— 均线 / 布林两式 / 二次方程 / 牛熊 | `_estSma`、`GetQuadraticEquationRoot`、`_estBollingerBands`、`_estNextBollingerBands` | ✅ 第一刀 |
| 2 | **查库与切片** —— 起点、日/周/月三套序列 | `_calcStartDate`、`_ignoreCurrentTradingData`、`_getDayWeekMonthData`、`NeedAfterHourEst`、`_onTest` | ✅ 第二刀 |
| 2b | **EMA** —— 查 `stockema50/200` | `_get_EMA` | ✅ 第二刀 |
| 3 | **配置** —— 缓存 SMA 结果 | `_configSMA` → INIFile | 🟡 接口已建，**默认内存**，文件后端待评审 |
| 4 | **渲染** —— 表格与回调 | `php/ui/smaparagraph.php`（225 行） | ✅ 已完成，见 `README_sma_paragraph_migration.md` |

第 3 件单独隔出来的理由：PHP 的 `_configSMA()` 会 `$cfg->save_data()`
**回写配置文件**——直译过来就是"渲染一个页面会写工作区文件"：
测试会污染工作区、并发请求会互相覆盖。所以本版把它抽成
:class:`ConfigSmaStore` 接口，**默认实现是纯内存**（见第七节）。

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

## 七、第二刀：查库与切片

### 7.1 ⚠️ 踩到的坑：`GetFromDate` 是 `date <= startDate`，不是 `>=`

这是我**实际写错并被测试抓到**的一处。`php/sql/sqldailyclose.php:41`：

```php
function _buildWhereFromDate($strKeyId, $strDate)
{
    return $this->BuildWhere_key($strKeyId)." AND date <= '$strDate'";
}
```

我第一版写成 `date >= %s`，于是 `get_from_date(stock_id, start_date, 620)`
**只返回一行**（起始日那天）。后果：

```text
daily = [99.5]          # 只有一天
D5 = D10 = D20 = 99.5   # 所有均线退化成同一个价格
```

而且**不报错** —— 页面上"有数"，只是全一样。

正确语义：先算 `start_date`（**最新可用日期**），再**按它往回**取 620 天。
所以 `start_date` 是**上界**而不是下界。之所以要这个上界，是为了把
"今天还没收盘"那条排除掉（见 7.2）。

### 7.2 `_calcStartDate()` 为什么取最近 **2** 条

```php
if ($result = $his_sql->GetAll($this->GetStockId(), 0, 2))
{
    while ($record = mysqli_fetch_assoc($result))
    {
        $strDate = $record['date'];
        if (_ignoreCurrentTradingData($strDate))  continue;   // ← 跳过
        else  return $strDate;
    }
    return false;
}
```

若最新一条**就是今天、而市场还没收盘**，它会被跳过，起点落到**上一个交易日**。
只取 1 条就没得跳了。返回 `false` → 本版 `None`。

`_ignoreCurrentTradingData()` 的判据是"是今天 **且** 当地还没到收盘"：

```php
$ymd = GetNowYMD();
if ($ymd->GetYMD() == $strDate)
    if ($ymd->IsStockTradingHourEnd() == false)  return true;
return false;
```

`IsStockTradingHourEnd()` 是 `GetHour() <= 16`（`php/class/year_month_day.php:217`）
—— 注意是**当地 16 点**，不是 15:00 收盘就出价。**16:59 仍算未收盘，17:00 才算**。
这个边界在对照脚本里单独钉了（自检第 ⑥ 项）。

### 7.3 遍历方向与切片方向的配合（最容易搞反）

`GetFromDate()` 给的是 `ORDER BY date DESC`，所以循环**从新到旧**。
`$strNextDayYMD` 保存"上一条处理过的日期"，也就是**时间上更晚**的那条。

于是 `_isWeekEnd($strYMD, $strNextDayYMD)` 的实际含义是
"`strYMD` 是不是它后面那条（更晚的）之前最后一个交易日" ——
判据落在 `GetDayOfWeek() >= next` 上（见第四节）。

第一刀把这 360 个日期判据逐项对照过，第二刀只是把它们接到真实数据上，
所以固定数据下实测周线取 `[99.5, 98.0, 95.5]`（三个周五）、月线取 `[97.0]`（9 月最后一条）。

### 7.4 `NeedAfterHourEst()` 的两个条件

```php
if ($this->GetStartDate() == $ref->GetDate())  return false;      // ①
return ($now_ymd->GetHourMinute() > 1520) ? true : false;         // ②
```

① 起始日就是最新行情日 → 今天的数据已经在序列里，不需要盘后补；
② 当地时刻晚于 **15:20**（`1520`）。两个条件都要满足。

`_onTest()` 会在取数**之前**先把当天现价塞进序列（`$afClose = array($fPrice)`），
所以现价排在**最前面**。三条序列各算一遍，结果进 `arAfterHour`。

### 7.5 `_get_EMA` 的三个细节

```php
$sql = GetStockEmaSql($iDays);
$this->arSMA[$strName] = $sql->GetClose($this->GetStockId(), $this->strStartDate);
$this->arNext[$strName] = false;
$this->arAfterHour[$strName] = false;
```

* EMA 直接查 `stockema50` / `stockema200`，**不是**算出来的；
* `GetClose()` 是按 `strStartDate` **精确取**，不是取最近一条 —— 那天没有就为空；
* `next` 与 `after_hour` 都是 `false`（本版 `None`）—— EMA 不做 T+1 推算，
  也不参与盘后估值。照抄。

顺带一个列名差异：这两张表只有 `id` / `stock_id` / `date` / `close`，
**没有 `adjclose`**（`sql/schema.sql`）—— 而 `stockhistory` 表用的是 `adjclose`。
所以 `StockHistorySql.get_close()` 单独写了 `SELECT \`close\``。

## 八、第二刀：配置改成了可替换的内存实现

PHP 的 `_configSMA()` 判断"配置里的 `Date` 是否等于本次的 `strStartDate`"：

* 相等 → 读缓存（`_loadConfigSMA`）；
* 不等或没有该段落 → 重算并写回（`_saveConfigSMA`，末尾 `$cfg->save_data()`）。

本版把 INI 换成 :class:`ConfigSmaStore`，**默认纯内存**：

```python
store = ConfigSmaStore()
store.group_exists("SMA")        # 对应 $cfg->group_exists()
store.read_var("SMA", "D5")      # 对应 $cfg->read_var()
store.set_var("SMA", "D5", 98.2) # 对应 $cfg->set_var()
store.save_data()                # 内存实现是 no-op
```

**这个替换在语义上是等价的**：内存实现每次进程重启都"缓存未命中"，
等价于永远重算 —— 结果一样，只是少一次文件读写。而文件读写的正确性
（路径从哪来、并发写、测试隔离）本来就要单独评审。

### 一个真踩到的类型问题

配置里存的是**字符串**（INI 本来就是文本）。读回来必须转数值，否则
`self.sma['D5']` 会是 `'98.2'`，调用方一做减法就 `TypeError`。
实测在"缓存命中"那条路径上才暴露 —— 平时走重算分支看不出来。
`_cfg_get_sma()` 现在显式 `_as_number()`。

## 九、`MaxMin` 的 `== false` 陷阱

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

## 十、`_getColorAndOrderArray` 依赖调用方顺序（已显式处理）

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

## 十一、对照验证：454 例，0 意外差异

```powershell
cd F:\woody\woodyweb\python\auto
$env:PATH = "D:\xampp\php;" + $env:PATH

python -B _php_sma_compare.py              # 454 例
python -B _php_sma_compare.py --selftest   # 证明它抓得到注入的错误
```

| 对照项 | 用例数 |
|---|---|
| `_estSma`（6 条序列 × 4 个周期） | 24 |
| `GetQuadraticEquationRoot` | 6 |
| `_estBollingerBands` + `_estNextBollingerBands` | 12 |
| `_isWeekEnd` + `_isMonthEnd`（4 个"今天" × 45 天 × 2 条分支） | 360 |
| `_ignoreCurrentTradingData`（6 个时刻 × 3 个日期） | 18 |
| 配色（4 组 × 各键） | 25 |
| 纵轴标尺 | 4 |
| `MaxMin` | 8 |
| **合计** | **454** |

**自检**（`--selftest`）在内存里注入三种错误 + 两条不变式，断言抓得到：

```text
① 基准                          与 PHP 不一致：0
② 把 _estSma 的除数改成 avg      抓到 20 例（php=98.2025 python=98.234）
③ 布林带 sigma 的 /2 改成 /4     抓到 5 例
④ 两条布林式子结果必须不同        当日 (102.568…) ≠ T+1 (102.914…) ✓
⑤ 收盘边界从 16 点改成 15 点      抓到 2 例（php=True python=False）
⑥ 16:59 未收盘 / 17:00 收盘       实测一致 ✓
```

第 ⑤⑥ 项是第二刀加的 —— `_ignoreCurrentTradingData` 决定 `_calcStartDate()`
的返回值，而 `GetFromDate()` 是**按那个日期往回取**（第 7.1 节那个方向 bug
就是在这条链上）。

## 十二、测试

`python/auto/test_stockhistory.py` —— **85 例**，钉的是**PHP 的原意**
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
| `IgnoreCurrentTradingDataTest` | 收盘前跳过今天、**16 点边界**（16:59 未收盘 / 17:00 收盘） |
| `CalcStartDateTest` | 只取 2 条、盘中跳过今天落到上一交易日、无历史返回 `None` |
| `DayWeekMonthSlicingTest` | 最新在前、周线取三个周五、月线取 9 月最后一条、参数为 `(start_date, 620)` |
| `ConfigSmaTest` | 17 个键的布局、T+1 存 `i-1` 值、EMA 的 next/after_hour 为 `None`、EMA 按日期精确取、缓存命中读回**数值** |
| `NeedAfterHourEstTest` | 两个条件、**15:20 边界**（15:20 假 / 15:21 真） |
| `AfterHourTest` | 现价并入序列并排在最前、两轨布林都在 `after_hour` 里 |
| `BullBearTest` | `EMA50 > EMA200` 判牛熊、无 EMA50 返回 `None` |
| `TimeZoneTest` | 用 `tzutil` 折算时区，**不改进程默认时区** |
| `ColorIntegrationTest` | 构造完就有配色与标尺、键全覆盖 |

全量：`auto` **813** / `web` **251**。

## 十三、下一刀要做什么

**第 3 刀 —— 配置的文件后端**（单独评审）。本版已经把接口建好、默认走内存
（第八节），要接真实 INI 文件时需要先明确：

1. 文件路径怎么来 —— PHP 用 `$this->stock_ref->strConfigName`，而
   `MyStockReference` 未迁；
2. 并发下多个请求同时写同一份配置会不会互相覆盖（PHP 每请求一进程，
   天然没有这个问题；常驻进程下有）；
3. 测试里怎么避免污染工作区（临时目录？还是干脆永远用内存？）。

**第 4 刀 —— `smaparagraph.php`**（225 行）：纯渲染（列头 + 回调）。
`_callbackQdiiSma` / `_callbackFundPairSma` / `_callbackAhPairSma` /
`_callbackFutureSma` 四个回调，以及 `EchoFutureSmaParagraph` 里的期货理论溢价
（走 `futurepremium` 表）—— **已做完**，落在
`python/web/services/sma_service.py`、`python/auto/pairestimate.py`、
`python/auto/futurepremium.py`，记录见
`python/auto/README_sma_paragraph_migration.md`。
