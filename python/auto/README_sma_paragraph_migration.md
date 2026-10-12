# 均线段落迁移（`php/ui/smaparagraph.php`，225 行）

对应 `php/ui/smaparagraph.php`。数值与取数是上一刀
（`php/stockhis.php` → `auto/stockhistory.py`，见
`README_stockhistory_migration.md`），本刀只做**把 `StockHistory` 的结果摆成
一张表**，外加四个回调与期货理论溢价。

| PHP | Python | 状态 |
|-----|--------|------|
| `EchoSmaParagraph()` `:104` | `web/services/sma_service.py` → `SmaService.sma_table()` | ✅ |
| `_getSmaRow()` `:5` | `sma_row_text()` | ✅ |
| `_echoSmaTableItem()` `:29` | `SmaService._row()` | ✅ |
| `_getSmaParagraphMemo()` `:68` | `SmaService.memo()` | ✅ |
| `_getSmaParagraphWarning()` `:81` | `SmaService.warning()`（委托 `warning_source`） | ✅ |
| `_callbackQdiiSma()` `:141` | `callback_qdii_sma()` | ✅ |
| `_callbackFundPairSma()` `:151` | `callback_fund_pair_sma()` | ✅ |
| `_callbackAhPairSma()` `:169` | `callback_ah_pair_sma()` | ✅ |
| `_callbackFutureSma()` `:200` | `SmaService._future_callback()` | ✅ |
| `GetFutureInterestPremium()` `:179` | `auto/futurepremium.py` | ✅ |
| `RefGetFuturePremium()` `:189` | 同上 | ✅ |
| `EchoFutureSmaParagraph()` `:212` | `SmaService.future_table()` | ✅ |
| `MyPairReference::EstFromPair/EstToPair` | `auto/pairestimate.py` | ✅（鸭子类型自由函数） |

## 一、分刀：三个模块而不是一个

`smaparagraph.php` 看着是"纯渲染"，其实里面塞了两类**不属于渲染**的东西：

1. **配对双向估算**（`EstFromPair` / `EstToPair`）在
   `php/stock/fundpairref.php`，`MyPairReference` 那一档还没迁；
2. **期货理论溢价**（`GetFutureInterestPremium` / `RefGetFuturePremium`）
   带时区副作用与 `futurepremium` 表读取。

所以拆成三块：

* `auto/pairestimate.py` —— 双向估算，**自由函数 + 鸭子类型**。
  这样 `MyPairReference` 落地时 `sma_service` 一行都不用改：它只调
  `est_from_pair(ref, ...)`，参数对象有 `pair_ref` / `factor` / `get_position()`
  就够了。
* `auto/futurepremium.py` —— 溢价公式与读表，纯数值 + 一个数据源接口，
  可脱离 Web 单测。
* `web/services/sma_service.py` —— 只摆表。

`sma_service` **不 import** `refobj`，避免把渲染层和引用对象层焊死。

## 二、⚠️ 又一次遇到 PHP 的 `'0'` 假值

`_getSmaCallbackPriceDisplay()` `:19` 与 trading 那处是**同一个坑**：

```php
if ($strVal) { ... }        // '0' 在 PHP 是假值
```

Python 的 `'0'` 是真值。本模块一律走 `_php_truthy()`，并在
`test_sma.ColumnAndRowTest` 里钉住"值是 0 时**这一格照样要画出来**"。

顺带一个更细的：`memo` 参数用的是 `memo is False` 严格判断而不是"假值就替换"——
PHP 写的是 `if ($str === false)`，所以 `EchoFundPairSmaParagraphs()` 传进来的
**空串不会被默认 memo 顶掉**。这个差异决定了一组配对标的并排展示时
只有第一张表带标题。

## 三、两个 PHP 全局量改成注入

```php
$bAfterHour = LayoutUseWide();    // ← php/layout.php，未迁
$bAdmin     = DebugIsAdmin();     // ← 🔴 membership 域，未迁
```

* `LayoutUseWide()` → 构造参数 `wide`。它决定要不要多一列"盘后"，
  **两步收窄**：先用 `wide` 当 `StockHistory` 的初值，构造之后再问一次
  `NeedAfterHourEst()`（PHP 的 `if ($bAfterHour) $bAfterHour = $his->NeedAfterHourEst()`）。
  合成一步会让"宽屏但不在盘后时段"多出一列。
* `DebugIsAdmin()` → 构造参数 `is_admin`，默认 `False`。
  **不编、也不假装能判定**（与 `trading_service.link_builder`、
  `stock_service` 的 admin 列同款处理）。

## 四、列数组里那个"重复的对象"

```php
$premium_col = new TableColumnPremium();
$ar = array(new TableColumn('均线', 90), new TableColumnEst(),
            $premium_col, $next_col, $premium_col);   // ← 同一个实例两次
```

第 3 列与第 5 列是**同一个对象**，列头因此长得一样。看着像笔误，
去重就与生产端不一致了 —— 照抄，并由 `columns_to_dicts` 原样输出两份。

`_echoSmaTableItem()` 的行也是**变长**的：`else` 分支显式 `''` 两格
（没有 T+1 时也要占位），改成不追加会让整行错位。

## 五、期货理论溢价：三处不照抄

### 5.1 `date_default_timezone_set()` 是永久副作用

```php
date_default_timezone_set('America/New_York');   // 之后不恢复
```

执行过一次之后**整个请求剩下的部分都是美东时间**。本版不照抄：
Windows 上没有 `time.tzset()`，`stocksymbol.set_time_zone()` 一调就抛
（项目里已有先例）。改成把时间点**显式折算**到 `America/New_York`。

### 5.2 天数差是"零点对零点"，跨夏令时是小数

`GetTick()` 是 `mktime(0,0,0,...)`，两边都落在当时的进程时区上。
本版两边都取美东零点 —— 于是 **2026-10-11 → 2026-12-18 是 68.0417 天**
（中间隔了 11 月 DST 结束），不是整 68 天。
用"两个 `date` 直接相减"会差 0.04 天 = 0.0000055 的溢价，逐项对照会炸。

### 5.3 PHP 的 `round()` 是四舍五入，Python 是银行家舍入

```php
$f = round(4.0 * $f) / 4.0;      // 先取到 0.25 的整数倍（外盘指数期货的报价步长）
return strval(round($f, 2));
```

`round(0.125, 2)` 在两边分别是 `0.13` 和 `0.12`。所以走 `php_round()`
（绝对值四舍五入后恢复符号），不用内建 `round`。

## 六、`futurepremium` 表

`schema.sql` 新增（幂等，`build_schema.py` 已建）：

```sql
CREATE TABLE IF NOT EXISTS futurepremium (
  id ..., stock_id ..., date ..., close ...,
  UNIQUE KEY (date, stock_id)
);
```

演示数据由 `dbtools/seed_future_premium.py` 灌（幂等）：
`hf_NQ` 5.00625 / `hf_ES` 4.75，都记在 `2026-12-18`。

**单位是百分点**（`5.00625` 表示 5.00625%），所以要 `/ 100.0` ——
`RefGetFuturePremium()` 里那句 `floatval($strClose) / 100.0` 就是这个意思。

读表复用数据源的通用日记录口子 `_get_daily_record("futurepremium", stock_id)`
（对应 PHP 的 `FuturePremiumSql`，它继承 `DailyCloseSql`）。
**表不存在时不能让页面 500**：`_read_future_premium` 吞掉异常返回
`(None, None)`，调用方 `if ($fPremium = ...)` 那一档守卫照旧生效。

## 七、两件 PHP 干了而本版不做的事

1. `EchoCalibrationHistoryParagraph($ref->GetEstRef(), 0, 1)` 会在均线表
   **之前**再输出一张校准历史表（`php/ui/calibrationhistoryparagraph.php`，
   未迁）。本版只在 `future_table()` 的返回值里放一个
   `calibration_history` 标记位 —— 服务层不该"顺手 echo 第二张表"，
   那是调用方的事。
2. 标题末尾的 `GetStockOptionLink(STOCK_OPTION_PREMIUM, ...)` 是
   管理员专属设置链接（🔴 membership 域）。本版只给纯文本
   `理论溢价：1.0093`，链接等那一档迁完再补。

## 八、⚠️ 一处有意偏离 PHP：全 0 的均线不摆表

PHP 遇到"该标的没有历史数据"时表现很糟：`_calcStartDate()` 拿不到日期，
后面一串 `_estSma()` 全算出 `0`，于是页面上**摆一整张全是 0 的均线表**。
生产端每个标的都有历史，作者没被这个问题困扰过。

演示库里 `^NDX` 一行 `stockhistory` 都没有（且 `stockema50` / `stockema200`
两张表是空的），照抄就是一张误导性的表。所以
`_has_any_level()` 加了一道守卫：**全部档位都是 PHP 假值时返回 `None`**，
让上层整段跳过 —— 与详情页其它"没有数据就不摆空表"的处理一致。

代价是演示库上这一段现在**看不到**。没有选择"造 200 天假历史"来让它显示，
因为 `sql/_clean_seed_history.py` 已经确立的约定是：合成值一旦显示在页面上
就会被误当成真实历史（该脚本就是为了删掉这类占位数据而写的）。
纹理由 `web/tests/test_sma_route.py` 用注入的假 `StockHistory` 钉住。

## 九、接线

`web/app.py` 注册 `sma_service_factory`；`web/routes/fund.py` 的
`_build_sma()` 一次建**两张**表（`woody/res/php/_qdiigroup.php:97` 与
`:103` 两个入口都调了）：

* `qdii` —— 中间层（`^NDX`）的均线，回调列是基金自己的估值；
* `future` —— 同一批均线，回调列换算成期货理论价。

模板 `web/templates/fund_detail.html` 渲染这两张，期货那张上方带
`理论溢价：…` 说明。

新增两个注入口（与 `trading_refs_builder` 同款，为的是路由测试不连库）：

* `sma_refs_builder` —— 顶替"按符号建 QDII 引用对象"；
* `sma_history_factory` —— 顶替 `StockHistory` 的构造。

## 十、PHP 逐项对照

```powershell
cd F:\woody\woodyweb\python\auto
python -B _php_pairest_compare.py            # 13 例，0 意外差异
python -B _php_pairest_compare.py --selftest # 注入 3 类错误，抓到 3 类
python -B _php_futurepremium_compare.py            # 13 例，0 意外差异
python -B _php_futurepremium_compare.py --selftest # 注入 3 类错误，抓到 3 类
```

`_php_pairest_*` 覆盖四类情形：A 股 / 非 A 股（两条式子互为倒数方向）、
给参数 / 不给参数（PHP 的 `$x == false` 是宽松比较，`0` / `'0'` / `''` 全落
"没传参数"那一档）、有旧校准值 / 没有（`FundAdjustPosition` 第三参数）、
以及一条贴近真实量级（纳指期货那组）的用例。

**这类对照的价值在于：抄错一个方向往往不崩，结果仍在合理区间**，
只有把 PHP 的真实输出摆在旁边才看得出。

`_php_futurepremium_*` 覆盖 DST 两侧（`68` vs `68.0417` 天）、
`php_round` 的 `.5` 边界、以及默认参数那两个魔数。

## 十一、测试

| 模块 | 用例 | 覆盖 |
|------|------|------|
| `auto/test_pairestimate.py` | 15 | 双向估算、仓位混合、`$x == false` 六哨兵 |
| `auto/test_futurepremium.py` | 21 | 时区折算、天数是小数、`php_round`、缺表降级 |
| `web/tests/test_sma.py` | 31 | `_getSmaRow` 三层分支、列/格九个组合、memo 的 `=== false` |
| `web/tests/test_sma_route.py` | 5 | 页面上真的出表、理论溢价说明、无历史时整段跳过 |

`test_sma.py` 不依赖 flask（两套解释器都能跑）；
`test_sma_route.py` 需要 flask，**单独成模块**，理由与 `test_trading_route.py` 同。

全量：`auto` **849**（3.7 / 3.13 双绿）、`web` **287**。
