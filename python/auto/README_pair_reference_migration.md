# 配对引用层迁移：`MyPairReference` → `refobj.PairReference`

对应 PHP：**`php/stock/fundpairref.php`**（`MyPairReference` + `Ab` / `Ah` / `Adr` /
`Fund` 四个子类）与 **`php/stock.php:328-379`**（工厂）。

这是 `_mystock.php` 详情栈的 **CK2 关键石**：没有这层，AH/AB/ADR 对比、配对 SMA、
A 股带配对溢价的 trading、Holdings 分支全部拿不到数据。

## 1. 迁了什么

| PHP | Python | 说明 |
|---|---|---|
| `MyPairReference`（`fundpairref.php:3`） | `refobj.PairReference`（`:661`） | 基类；`pair_ref` / `cny_ref` 构造 + 校准装载 |
| `AbPairReference` / `AhPairReference` / `AdrPairReference` | `refobj.Ab/Ah/AdrPairReference`（`:886/:892/:898`） | 只是 `pair_table` 不同 |
| 构造里的 `cny_ref` 推导（`:16-43`） | `PairReference._build_cny_ref`（`:732`） | 汇率口径分支表，见 §3 |
| `LoadCalibration()`（`:48`） | `PairReference.load_calibration`（`:837`） | `fFactor` / `fLastCalibrationVal` |
| `GetPairRef` / `GetCnyRef` / `GetDefaultCny` | 同名 snake 方法 | `get_pair_ref` 等 |
| `EstFromPair` / `EstToPair` | 委托 `calc.pairestimate` 的自由函数 | 公式只有一份实现 |
| `GetPriceRatio()`（`:104`） | `get_price_ratio`（`:800`） | |
| `_getAbPairReference` 等三个（`:328-350`） | `_get_ab/ah/adr_pair_reference`（`:907/:917/:927`） | 双向查找 |
| `StockGetPairReferences`（`:352`） | 同名（`:937`） | 链式，返回 `(ab, ah, adr)` |
| `StockPairSql::GetPairSymbol` / `GetSymbol` | `_lookup_pair_symbol` / `_lookup_pair_symbol_reverse`（`:91/:116`） | 正查 / 反查 |

顺带：`MysqlReference.get_my_stock_link()`（对应 PHP `GetMyStockLink`）。

## 2. 四张配对表与"正查 / 反查"

PHP 里四张表（`abpair` / `adrpair` / `ahpair` / `fundpair`，见
`php/sql/sqlstocksymbol.php:123-126`）共用一个泛型 `StockPairSql`，
`GetPairSymbol()` 与 `GetSymbol()` 的逻辑完全相同，只是表名不同。

* **`GetPairSymbol($sym)`**（正查）：`sym → stock_id → ReadPair → pair_id → symbol`，
  即"我的配对是谁"；
* **`GetSymbol($pair)`**（反查）：`pair → pair_id → GetId → stock_id → symbol`，
  即"谁配对了我"。

所以 `_lookup_pair_symbol` 改成**表名参数化**（原来是写死 `fundpair`），
并新增 `_lookup_pair_symbol_reverse`。三个 `_getXPairReference` 工厂先正查、
查不到再反查 —— 这就是"从 B 侧 `SH900919` 进来也能构造出 A 侧"的来源。

## 3. `cny_ref` 推导（最容易静默出错的一段）

`fundpairref.php:16-43` 的分支表，决定汇率口径：

```php
if ($this->pair_ref->IsSinaFutureCN())          { }                      // 空分支
else if ($this->pair_ref->IsSymbolA()) {
    if ($this->IsSymbolA()) {
        if    ($this->pair_ref->IsShangHaiB())  $strCNY = 'USCNY';
        else if ($this->pair_ref->IsShenZhenB()) $strCNY = 'HKCNY';
    }
    else if ($this->IsSymbolUS() || $this->IsSinaFutureUS())
                                                $this->cny_ref = new MyStockReference('fx_susdcnh');
}
else if ($this->pair_ref->IsSymbolH()) {
    if    ($this->IsSymbolA())    $strCNY = 'HKCNY';
    else if ($this->IsSymbolUS()) $this->cny_ref = new MyStockReference('fx_susdhkd');
}
else if ($strPair == 'znb_SENSEX')  $this->cny_ref = new MyStockReference('fx_susdinr');
else                            { if ($this->IsSymbolA()) $strCNY = 'USCNY'; }
if ($strCNY)  $this->cny_ref = new CnyReference($strCNY);
```

### 3.1 两条赋值路径 `new` 的是**两个不同的类**

中间价（`USCNY` / `HKCNY`）走 `new CnyReference(...)`；离岸外汇 `fx_*` 走
`new MyStockReference(...)`（当股票行情取的外汇符号，不是中间价净值表）。

Python 侧对应 `refobj.CnyReference` 与 `refobj.MysqlReference`。**注意
`CnyReference` 是 `MysqlReference` 的子类**，所以单测里判定顺序必须是
「先 `CnyReference` 后 `MysqlReference`」，写反了全是 `CnyReference`。

写成同一个类不报错，只是汇率口径不对 —— 见 `test_pairref.py` 的
`test_fx_branch_uses_stock_ref_not_cny_ref`。

### 3.2 数值区间 ≠ 字符串前缀

PHP 的 `_isDigitShangHaiB` 判的是 **`900000 <= 数字 < 1000000`**
（`php/stock/stocksymbol.php:449`），不是"以 `900` 开头"。迁移时按前缀实现
会把 `901000`~`999999` 整段漏掉。

本轮**实际踩到**：CK2 初版在 `stocksymbol.py` 里新加了 `is_shang_hai_b()`
用 `str_digit_a.startswith("900")`。而代码库里**本来就有**正确的
`is_shanghai_b()`（`stocksymbol.py:788`，用 `_is_digit_shanghai_b`）。
已撤掉重复实现，改为调用已有的那个。详见 pitfalls #54。

## 4. 刻意不迁的部分（已由 CK2b 完成）

本轮**只**迁 `MyPairReference`，把 `FundPairReference`（`fundpairref.php:148-276`）
的期货自动校准与四个 EST 取值单列为 **CK2b**：

* 与已迁的 `fundestcalc` / `futurescalib` 以及独立的 `autocalibration`
  任务高度重叠，重复迁一遍反而制造两份真相；
* CK3 的非基金详情页用不到；
* 基类 `PairReference` 的校准装载（`LoadCalibration`）已迁，够 CK3 用。

CK2b 已落地，见 §8。拆开的另一层理由：**`PairReference` 是只读的，而 PHP 的
`FundPairReference` 构造时就写库** —— 页面路径上构造引用对象不能顺带写库。

## 5. 验证

* `auto/test_pairref.py`：**22 个用例**（cny 推导 / LoadCalibration /
  EstFromPair-EstToPair 互逆 / GetPriceRatio / `StockGetPairReferences`
  链式与双向）。全量 auto **1010** OK（原 988 + 22）、web **619** OK、
  `compileall` 双绿。
* **PHP 逐例对照**：`_scratch/_php_pairref_check.php` +
  `_scratch/_php_pairref_compare.py`，**15 例 0 差异**，同时比对
  **汇率符号**与**建出来的类**。

  PHP 侧不手抄分支：按行从 `fundpairref.php` 抠出分支体贴进替身类，
  符号分类则 `require` 真实的 `php/stock/stocksymbol.php`
  （该文件无任何 `require`，可独立加载）—— 否则对照的只是"我以为 PHP
  会怎么判"。
* 用例矩阵里专门挑了四例钉住易错点：`SH999999`（区间而非前缀）、
  `00700` 配 `SH600519`（PHP 那行 `IsSymbolH` 是**注释掉的死分支**）、
  `znb_SENSEX`（不看本符号）、`^GSPC`（落到最后那个 `else`）。
* 两处新增断言都做了**变异测试**确认不是空断言：把 `fx_*` 分支改成
  `CnyReference`、把区间判定改成前缀判定，对照脚本/单测分别报错。

## 6. 已知偏差

* **`GetPairRef()` 为空时的空指针**：PHP 的
  `$adr_ref = _getAdrPairReference($h_ref->GetSymbol())` 在 `$h_ref` 为
  `false` 时会直接崩。本版按 `if h_ref is not None` 守卫，拿到 `None`
  就跳过 —— PHP 那条路径在生产上不可达（进了 `if` 说明有配对），
  这里属于"不复制一个崩溃"。
* **`LoadCalibration` 的 `IsYahooNetValue` 分支**：PHP 会用
  `$this->IsYahooNetValue()` 的符号取 `stock_id`；本版沿用
  `MysqlReference` 的 `stock_id`（其构造已处理该分支）。
* **除零**：`GetPriceRatio` 分母为 0 时 PHP 得 `INF` 并画出来，
  本版回落 `1.0`（与 `pairestimate` 的口径一致）。
* **测试里的 H 股符号**：本仓库 H 股是 5 位 `0` 开头的裸代码
  （`00700` / `09988`），**不是** `HK00700` —— 后者
  `is_symbol_h()` 与 PHP 的 `IsSymbolH()` 都不认。

## 7. 消费方：为什么"方法一出现就通电"

`pair_service` / `trading_service` / `sma_service` 都通过
`_invoke(ref, snake, camel)` 鸭子类型取数。所以 `PairReference` 上一旦
有了 `est_from_pair` / `est_to_pair` / `get_cny_ref` / `get_price_ratio`，
这些服务**不用改**就能拿到数据。

`EstFromPair` / `EstToPair` 委托给 `calc.pairestimate` 的自由函数 ——
公式在本项目里只有一份实现，`PairReference` 只负责"把 `self` 当 ref 传进去"。

## 8. CK2b：`FundPairReference`（`fundpairref.php:148-276`）

### 8.1 迁了什么

`refobj.FundPairReference(PairReference)`，相对基类多三块：

| 块 | PHP 出处 | 说明 |
|---|---|---|
| 四个 EST 取值 | `:224` / `:238` / `:256` / `:267` | `get_official_date` / `get_official_net_value` / `get_fair_net_value` / `get_realtime_net_value` |
| `netvalue_ref` | `:150` / `:208` | 没有时 `get_net_value_ref()` 返回 `self` |
| 自动校准 | `:174` / `:188` | 见 8.3，默认不写库 |

外加工厂 `StockGetFundPairReference`（`woody/res/php/_stock.php:142`）；
门禁 `SqlGetFundPair` 就是 §2 那张 `fundpair` 表查找，现成复用。

### 8.2 `CalcFactor` 两个分支的**汇率方向相反**（最容易静默出错）

```
if ($this->IsSymbolA())   $fNetValue /= $fCny;   // A 股：factor = pair × cny / netvalue
else                      $fNetValue *= $fCny;   // 其它：factor = pair / (netvalue × cny)
```

与 `EstFromPair` 严格互逆（这是校准能自洽的前提）：

| 本符号 | `EstFromPair` | `CalcFactor` |
|---|---|---|
| A 股 | `pair * cny / factor` | `pair * cny / netvalue` |
| 其它 | `pair / cny / factor` | `pair / (netvalue * cny)` |

写成同一个方向时**两个方向都算得出来**，因子差 `cny²` 倍，且不报错 ——
所以不能复用 `futurescalib.FuturesCalibrator.calc_factor`（只有 A 股那一式，
`期货价 × cny / 指数价`）。

单测里用**往返**（`est_from_pair` 的结果喂回 `calc_factor` 要还原因子）钉住，
另外专门挑了 `cny=2.0` 而不是 `1.0`：`cny` 为 1 时两个分支结果相同，
那条断言会变空断言（PHP 对照的 13 例里就有 3 例因此抓不到变异）。

### 8.3 自动校准：与 PHP 的**刻意偏差**

PHP 在**构造函数里**无条件调 `AverageCalibraion()`，`GetOfficialNetValue()`
里还直接调 `StockUpdateEstResult()` —— 也就是每次页面渲染都在写库。

本版收进 `auto_write` 开关（默认关）：

* 架构原则是"实时抓取只跑 daemon、web 只读"（`auto/refquote.py`）；
* 引用对象在 web 路径上会被反复构造，顺手写库既违背分层、也没法在单测里关掉；
* 写库交给 `autocalibrationcli` / `fundestcli` 统一负责。

`FundDataSource` 目前没有 `WriteDailyAverage` / `WriteVal` 的写口子，
所以 `average_calibration` 保留 `_calibration_writer` 注入位：注入了才写，
没有就是 dry-run。**`DailyCalibration` 同样只留结构不接写路径** ——
它的闸门逻辑已由 `autocalibrationcli` 独立实现，再迁一遍会造两份真相。

已知偏差：`$fCny` 为 0 时 PHP 得 `INF`，本版退回不含汇率的比值。

### 8.4 页面出口（`/stock/<symbol>` 第 2 分支）

`_mystock.php:131-136` 的四段**全做了**，顺序与 PHP 一致：

* ✅ `EchoFundArrayEstParagraph` → `fundest_service`；
* ✅ `EchoFundListParagraph` → `qdii_fund_list.fund_reference_table`；
* ✅ `EchoFundPairSmaParagraph` → `sma_service.fund_pair_table`；
* ✅ `EchoFundHistoryParagraph` → `detail_sections.build_fund_history`。

演示库 `fundpair` 有 8 行，`^NDX` / `^GSPC` / `QQQ` / `TQQQ` / `SPY`
五个符号会走这一分支，页面上**多出估值表**，肉眼可见。

#### 跟踪表：PHP 是同一个函数的两种形态

`EchoFundListParagraph($arRef, $callback = false)` 的第二个参数决定末列：

============  ========================  ==========================
调用点          末列                      每格的值
============  ========================  ==========================
`_qdiigroup`    `TableColumnHedge`        `_callbackFundListHedge`
`_mystock:134`  `TableColumn('参考值')`    `lastcalibration` 表
============  ========================  ==========================

此前只迁了第一种（``fund_list_table``，QDII 分组页那张杠杆表），
所以当时记的是"接不上" —— 其实**不是两种表，是同一个函数的两个分支**。
本轮补了第二种 ``fund_reference_table``，与前五列的组装共用 ``_build``。

两处细节（PHP 逐例对照 `case.1` / `case.8` 钉住）：

* **参考值的染色基准是 ref 自己的现价**，不是 0 —— `GetPriceDisplay()` 只传一个
  参数时走 `stockref.php:162` 的 `$fPrev = floatval($this->strPrice)`。同行的
  「仓位」/「校准值」走 `GetNumberDisplay()`，基准恒为 0，**两列不是一套规则**；
* 精度取 `$ref->GetPrecision()`，ref 说不出来才兜底 3。

对照：`_scratch/_php_fundlistitem_check.php` + `_php_fundlistitem_compare.py`
—— **10 例，8 例逐格一致 + 2 例已知差异**（`lastcalibration` 里是 `0` /
没记录时 PHP 那行只有五格，本版补空串），自检 4/4。

#### 基金溢价记录：与 `/history/fundhistory/<sym>` 同一张表

PHP 那边详情页与独立页共用 `_echoHistoryTableData()`，本版同样复用
`history_service` 的 `fundhistory`（`detail_sections.build_fund_history`），
没有第二套取数。唯一多出来的是 PHP 的第一行守卫
`if ($ref->IsYahooNetValue()) return;` —— 雅虎净值符号没有场内价格，
摆溢价表没有意义；本版**按符号判断**（这一层还没有 ref）。

对照：`_scratch/_php_fundhistory_check.php` + `_php_fundhistory_compare.py`
—— **8 例逐格一致**，自检 4/4。三类列的颜色不参与比较，理由见下节。

#### 对照顺带修掉的一个真 bug

`case.4`（官方 EST 的 `close` 是 `"0"`）暴露出 `history_service._row_fundhistory`
用了 `if estimate_close:`：Python 里非空字符串 `"0"` 是**真值**，于是会给 0 值的
EST 摆出「官方EST / 时间 / 误差」三格；PHP 的
`if ($strEstValue = $arFundEst['close'])` 判它为假，那三格不出现。
已改成 `_php_val_is_false()`（`""` 与 `"0"` 为假，`"0.0"` 为真），
并补了单测 `test_fundhistory_est_of_zero_shows_no_est_cells`（变异确认有牙）。

#### 已知偏差：净值 / 溢价 / 误差三格不染色

`_row_fundhistory` 只给文本、不给颜色，与 PHP 不同：

* **净值格**：PHP 的 `GetNetValueDisplay()` 调 `GetPriceDisplay($fNetValue, false)`，
  单参数时基准取 **ref 的现价**（`stockref.php:162`）；本版的数据层不持有 ref。
* **溢价 / 误差格**：PHP 的 `GetPercentageDisplay()` 把负数染红、正数不染
  （`stockref.php:148`）；本版的 `format_percentage_display()` 只返回文本。

三处都是"数据层不持有引用对象"这个架构差异的结果（PHP 的 ref 既是数据源
又是显示对象）。对照时只比文本。会**严格**比颜色的是价格与官方 EST 两列。

### 8.5 已知偏差：中间层 ETF 的 EST 数值不可用

`/stock/QQQ` 的官方估值会算出 **3452559.84**，而 QQQ 实际价格约 751；
`/stock/SPY` 同理（算出 89 万）。

**这不是迁移 bug**，是演示库校准值口径的问题：

* 演示库 `QQQ` 的 `cal = 0.008945`，等于 `6.72 / 751` —— 它是
  "**QDII 基金净值 / ETF 价**"，即 QQQ **作为别人中间层**时的换算率；
* 生产端 QQQ 的 `cal = 41.11`，是"指数点位 / ETF 价"，即 QQQ **作为本体**
  时被 `EstFromPair` 除的那个数；
* 把 QQQ 当本体用，除法口径自然对不上。

`/fund/SZ161130` 显示"汇率估值 6.7035"是**正常的** —— 它用的是
`SZ161130` 自己那条 `cal = 42388`（QDII 校准口径），不走中间层那一条。

待办：校准口径统一（与已有 `autocalibration` 任务相关），不在 CK2b 范围。

### 8.6 验证

* `auto/test_fundpairref.py`：**35 个用例**（`calc_factor` 两分支与往返 /
  三路回落 / `get_fair_net_value` 的穿透控制流 / 工厂门禁 / `auto_write`）。
  全量 auto **1045**、web **650**、`compileall` 双绿。
* **PHP 逐例对照**：`_scratch/_php_fundpairref_check.php` +
  `_php_fundpairref_compare.py`，**13 例 0 差异**。同 §5 的做法：分支体从
  `fundpairref.php` 按行抠、符号分类用真 `StockSymbol`。

  对照**比数值不比字符串** —— PHP 用 `strval()`（14 位有效数字）、Python 用
  `repr()`（17 位），`strval(8.0)` 是 `"8"` 而 `repr(8.0)` 是 `"8.0"`，
  逐字符比会把 8 条全报成差异。
* 变异测试：把两个分支改成同一方向 → 单测 3 条 + 对照 4 例同时报错。
* 真实库冒烟：`_scratch/_ck2b_smoke.py`，5 个有配对记录的符号全部走第 2
  分支，2 个没有的走第 4 分支。

### 8.7 两个测试盲区（都是变异测试逼出来的）

1. **替身自给自足**：用自带 `get_fair_net_value` 的替身去断言"这三个方法被
   调用"，把真实类里的方法改名照样全绿 —— 替身根本不读生产代码。
   补了一条查 `FundPairReference.__dict__` 的断言钉住"本类确实覆盖了"。
2. **`hasattr` 藏住继承**：`FundReference` 上本来就有同名的四个 getters，
   `hasattr(FundPairReference, name)` 恒为真，看不出本类有没有重写。
   改用 `__dict__.get(name)` 才抓得到。
