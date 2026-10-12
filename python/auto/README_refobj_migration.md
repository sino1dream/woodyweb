# php/stock 引用对象层迁移：`refobj.py`

本文件记录 `python/auto/refobj.py` 这一步（优先级列表第 3 项）的范围、取舍与
已知偏差。按 `AGENTS.md` 的约定，每一步都要写清**对应哪个 PHP 源文件**、
**什么没迁**、**哪里故意和 PHP 不一样**。

| PHP | 行数 | 本版 |
| --- | --- | --- |
| `php/stock/mysqlref.php` | 182 | `refobj.MysqlReference` |
| `php/stock/fundref.php` | 204 | `refobj.FundReference` |
| `php/stock/qdiiref.php` | 299 | `refobj.QdiiReference` + `build_qdii_reference()` |
| `php/stock/cnyref.php` | 23 | `refobj.CnyReference` |
| `php/gb2312.php` 的 `GbToUtf8()` | — | `refobj.gb_to_utf8()` |
| `php/debug.php:13` 的 `NETVALUE_PRECISION` | — | `refobj.NETVALUE_PRECISION = 4` |

产出：`refobj.py` 1063 行、`test_refobj.py` 614 行（52 个用例）。
`auto` 全量 **574 个**在 Python 3.7（生产，联网）与 3.13（离线，无 `requests`）
上**都全绿**；`web` 61 个保持全绿。

---

## 一、这一层在解决什么

PHP 的引用对象是"**一个有状态的符号**"：它同时持有

* 行情（新浪抓来的现价 / 昨收 / 时间），
* 库里的 id、净值、仓位、校准值，
* 三个估值（官方 / 参考 / 实时）。

页面上几乎所有数字都从它身上取。所以它**不是又一套算法**，而是把已经迁好的
那些能力（`stocksymbol`、`stockref`、`fundcalc`、`fundestcalc`）按 PHP 的
**字段语义**组织起来。

## 二、刻意的三条取舍

### 取舍 1：不在构造时联网

PHP 的 `FundReference::LoadData()` 是

```php
$this->LoadSinaFundData();          // 联网抓基金名
$this->bConvertGB2312 = true;       // 新浪名字是 GB2312
```

本版改成**从数据源取名字**（`navsource` 已经实现了名称抓取），构造函数只做
纯组装。理由与第 2 步（`stockprefetch` 用回调注入数据库依赖）一致：**外部依赖
必须能顶掉，否则"逻辑可测"是空话**。取不到名字就 `SetHasData(false)`，与 PHP
抓失败时的行为一致。

为此专门加了回归用例 `ConstructionIsOfflineTest`：把
`StockReference.load_sina_data` 换成"一调用就抛 `AssertionError`"，断言构造
引用对象不会碰到它。

### 取舍 2：三个 EST 的算法不在这里实现

官方文档 EST / 参考 EST / 实时 EST 的**唯一实现**是
`fundestcalc.EstimateCalculator`；`QdiiReference.est_net_value()` 只负责
"调闸门 + 把结果填进字段"。

理由是一个代价不小的历史事故：`fundestcalc` 之外曾存在一条"看起来等价"的估值
路径，基于它做的受控实验得出"中间层校准值对估值零影响"的**错误结论**，写入后
把 `SZ161130` 的官方 EST 从 4.59 搞成 20245，而且**页面不报错**
（详见 `README_autocalibration_migration.md`）。

所以这里的纪律是：**三个 EST 只有一个实现**，引用对象只承载结果。
`QdiiEstimateTest` 用假 estimator 断言"三个字段被正确填上"，而不是在测试里
重算一遍公式 —— 重算就等于又造了一份实现。

### 取舍 3：外部依赖全部可注入

`source` / `quote_loader` / `estimator` / `stock_ref` 都能从外面给。
数据源**缺少某个方法**时走 `MysqlReference._call()` 的兜底返回 `None`，
与 PHP 里"取不到就是 `false`"对齐。

异常只在这里被吞，且**只用于取数、不用于写库**：引用对象是给页面用的，
页面不能因为某项数据取不到就挂。写库路径（`update_est_net_value` /
`insert_fund_calibration`）只返回 `True`/`False`，不吞异常、不静默失败。

## 三、与 PHP 的逐点对照

### 3.1 构造函数顺序（顺序有意义）

```php
$this->SetTimeZone();
$this->LoadData();                                  // 子类覆盖
if (IsZeroString($this->strPrice)) $this->strPrice = $this->strPrevPrice;
$this->_loadSqlId(...);                             // 净值条数 + 仓位
```

本版 `__init__` 照抄这个顺序。仓位那句是 **PHP 的真值判断**
（`$pos_sql->ReadVal() ? ... : GetDefaultPosition()`），所以仓位写成 `0`
也等同于"没读到"，会回落默认值 —— 这条写了用例 `test_zero_position_is_treated_as_missing`。

默认仓位 `IsLofA() ? 0.95 : 1.0` 也单独盯着：早期版本把它硬编码成 `1.0`
（等于满仓），而官网 `sz161125cn.php` 明写"仓位估算值使用0.95"。

### 3.2 `GetNetValue()` 三条路

1. 新浪期货（黄金除外）→ 取**复权**收盘价；
2. 库里有净值记录（`iNetValueCount > 0`）→ 取净值；
3. 都没有 → 退回 `GetVal()`，**拿行情当净值用**（这是 PHP 的原设计，不是兜底 bug）。

`GetNetValueDisplay()` 的精度跟着分岔：有净值记录用 4 位
（`NETVALUE_PRECISION`），没有的按符号本身精度 —— 因为那时显示的是行情价。

### 3.3 `IsFund()` 的第二条路与数据源无关

```php
if ($this->CountNetValue() > 0)  return true;
if ($this->IsFundA())            return true;      // 只认符号本身
```

`SZ161130` 是 A 股基金，所以**即使数据源什么方法都没有**，`IsFund()` 也返回
`True`。写测试时在这里踩过一次：原本断言"数据源缺方法 → `is_fund()` 为
`False`"，用 `SZ161130` 必然红。改成用非基金符号 `QQQ`，并把这条语义写进注释。

### 3.4 `MysqlReference` 的三个估值恒为 `false`

`mysqlref.php:81-94` 是**写死**的：

```php
public function GetOfficialNetValue()  { return false; }
public function GetFairNetValue()      { return false; }
public function GetRealtimeNetValue()  { return false; }
```

三个字段声明在子类 `fundref.php:26-28`。所以本版基类同样返回 `None`，
并加了 `test_base_class_has_no_estimation_at_all` 把这一点钉住 ——
否则后人很容易"顺手"把基类也改成读属性，从而让 `MysqlReference` 与
`FundReference` 的语义悄悄分岔。

### 3.5 参考 EST 的闸门（`qdiiref.php:168`）

```php
if ($this->IsEtfA() || ($cny_ref->GetDate() != $this->strOfficialDate)
                    || ($strDate != $this->strOfficialDate))
{
    $this->fFairNetValue = ...;
}
```

即："汇率日期"或"中间层日期"与官方日期**都相同**时，参考 EST 就是官方 EST，
没有信息量，不算。本版 `est_realtime_net_value()` 照抄。

这条给测试带来一个约束：**用例里必须把汇率日期错开一天**，否则参考 EST 永远
是空。这不是迁就测试，而是还原 PHP 语义 —— 已在
`QdiiEstimateTest._reference()` 里注释说明。

### 3.6 `GB18030` 取代手写查表

PHP 的 `GbToUtf8()` 是手写 GB2312 → Unicode 查表再拼 UTF-8 字节。Python 标准库
的 `gb18030` 是 GB2312 超集，直接解码即可。入参有两种形态：

* `bytes` —— 抓回来的原始字节，直接解码；
* `str` —— 字节流被当成单字节字符读进来的那种，先 `latin-1` 还原成字节再解码。
  **已经是中文的 `str` 会在这一步抛 `UnicodeEncodeError`**，此时原样返回
  （说明不需要转换）。

### 3.7 四个地区合成一个类 + `region` 参数

PHP 是 `_QdiiReference`（抽象中间类）+ 四个地区子类，差异只有中间价符号与
即期外汇符号各一个字符串。Python 里合成一个类 + `QDII_REGIONS` 查表，
没必要写四遍：

| PHP 子类 | 中间价 | 即期外汇 |
| --- | --- | --- |
| `QdiiReference` | `USCNY` | `fx_susdcny` |
| `QdiiHkReference` | `HKCNY` | `fx_shkdcny` |
| `QdiiJpReference` | `JPCNY` | `fx_sjpycny` |
| `QdiiEuReference` | `EUCNY` | `fx_seurcny` |

`build_qdii_reference()` 按符号**猜地区**：先试 hk/jp/eu 的 `est_symbol` 查表
（它们各自只认自己那批符号），剩下的归 `us`。

### 3.8 `GbToUtf8` 之外的别名

按 `AGENTS.md` 保留 camelCase 别名（`GetClose`、`GetQdiiValue` …），
让按 PHP 写法调用的代码仍可用。`AliasTest` 断言别名**指向同一个实现**，
而不是各自抄一份。

## 四、本次在测试里踩到并修掉的四个坑

写完 51 个用例后跑出 3 失败 + 5 错误，逐个查下来**四个都是测试自己的问题**，
但其中三个背后是真实的语义陷阱，值得记一笔：

1. **`FundReference` 的字段必须是类级默认值。**
   `MysqlReference.__init__` 会调 `load_data()`，`load_data()` 又会走到
   `get_symbol()` —— 那时 `FundReference.__init__` 还没执行到赋值那几行，
   访问 `self.stock_ref` 会 `AttributeError`。PHP 里访问未定义属性只是得到
   `null`，所以不会有这个问题。**这是 PHP → Python 迁移的典型语义差**，
   已在源码注释里写清原因。

2. **`GetEstValue()` 隐式依赖汇率引用，取不到就返回 `0.0` 而不是报错。**
   `GetEstValue(GetQdiiValue(x)) == x` 是它能自证的唯一约束，但两个方向取
   汇率的方式必须一致：都不传时走 `GetForexRef()->GetPrice()`。假数据里
   USCNY 没给最新中间价时，`float(None)` 抛异常被吞，返回 `0.0` ——
   看起来像"公式错了"，其实是**假数据缺一项**。修法是在 `_reference()` 里
   补上 USCNY 的最新中间价，并断言 `get_forex_ref().get_price()` 确实拿到了它。

3. **份额回环会丢一点，是 `int()` 截断。** `100000 → est 3117 → 回 99991`，
   差 9。原本写死了期望值 `99999`，是算错了：丢失量上界是**一个中间层单位**
   （校准值 32.08），不是一位小数。改写成对 `(100000, 50000, 12345)` 三组
   断言 `quantity - factor < back <= quantity`，比写死一个数字更能防回归。

4. **`is_fund()` 的期望值**（见 3.3）。

## 五、已知偏差

1. **`EstReference` 是占位实现。** PHP 在这个位置上用的是
   `FundPairReference` / `HoldingsReference`（`fundpairref.php` 278 行、
   `holdingsref.php` 441 行），且 `QdiiReference::__construct` 会按
   `SqlCountHoldings($strEstSymbol) > 0` 在两者之间选。**两者都不在本步范围**
   （配对与期货校准已分别落在 `futurescalib` / `autocalibration`）。
   `EstReference` 只实现 `_QdiiReference` 真正用到的那几个动作：取 id、
   取最新净值（没有则退回最新行情）、给个日期。
   连带影响：`_getOfficialEstVal()` / `_getFairEstVal()` 里的
   `method_exists($est_ref, 'GetHoldingsDate')` 分支（持仓型基金）**未迁移**。

2. **`stock_ref` 自动建，调用方也可注入。** PHP 是
   `new MyStockReference($strSymbol)`（`mystockref.php`，未迁移），
   `is_fund_a()` 为真时构造时自动建 —— 由 `MysqlReference` 顶上
   （同样读库快照行情）。委托（`get_symbol` / `get_stock_id` /
   `get_price_display` / `get_percentage_text`）都转发给 `stock_ref`。
   详见 `README_stock_ref_autobuild.md`。

3. **`GetPercentageDisplay()` 只迁了文本部分。** PHP 返回带颜色的 `<font>`
   片段，属于渲染层（`php/ui` 未迁移），这里返回不带标记的文本。

4. **`UpdateOfficialNetValue()` 用"该日期是否已有净值"等价代替写库。**
   PHP 的语义是"把当前价格当作官方净值写进 `netvaluehistory`，**新写入**才
   返回 `true`"（`InsertDaily` 已有则不写）。这个返回值直接决定
   `AdjustFactor()` 会不会重算校准值 —— **只在新的官方净值到货那天重算**。
   本版数据源没有"写净值"的口子，所以用"该日期是否已有净值"等价判断；
   数据源提供 `write_net_value` 时优先用它。

5. **净值表没有"前一日"的口子。** `load_daily_sql_data()` 里 `str_prev_price`
   只能从行情表取。它只用于显示涨跌幅，不影响估值。

6. **`insert_fund_calibration()` 与 `autocalibration._write()` 是同一条 SQL。**
   那里已经踩过 `time` 列 NOT NULL 的坑，这里照抄（含 `time` 与 `num=1`）。

## 六、还没接上的线

* `est_net_value_with_est_quote()` 目前只被 `_debug_forex_verify.py` 调用，
  **页面的实时 EST 列还没接上** —— 这是下一步最便宜的一个接线点。
* 渲染层（`php/ui/table.php` 等）未迁移，所以 `refobj` 现在还没有真正的
  生产调用方；接下来的第 4 步会消费它。
