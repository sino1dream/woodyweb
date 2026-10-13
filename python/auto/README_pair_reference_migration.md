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

## 4. 刻意不迁的部分

`FundPairReference` 的**期货自动校准**部分（`AverageCalibration` /
`DailyCalibration` / `GetOfficial*` / `GetRealtimeNetValue`）不在本轮范围，
单列为 **CK2b**：

* 与已迁的 `fundestcalc` / `futurescalib` 以及独立的 `autocalibration`
  任务高度重叠，重复迁一遍反而制造两份真相；
* CK3 的非基金详情页用不到；
* 基类 `PairReference` 的校准装载（`LoadCalibration`）已迁，够 CK3 用。

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
