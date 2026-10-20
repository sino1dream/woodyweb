# 历史数据页面迁移

迁移范围：PHP ``php/ui`` 下 6 个历史数据段落页面 → Python ``python/web`` +
``python/auto/sql/historytable.py``。

| 生产端页面 | 本版 URL | 对应 PHP |
| --- | --- | --- |
| [历史价格](https://palmmicro.com/woody/res/stockhistorycn.php?symbol=XOP) | `/history/stockhistory/<sym>` | `stockhistoryparagraph.php` |
| [基金溢价记录](https://palmmicro.com/woody/res/fundhistorycn.php?symbol=SZ162411) | `/history/fundhistory/<sym>` | `fundhistoryparagraph.php` |
| [净值记录](https://palmmicro.com/woody/res/netvaluehistorycn.php?symbol=SZ162411) | `/history/netvaluehistory/<sym>` | `netvaluehistoryparagraph.php` |
| [净值价格比较](https://palmmicro.com/woody/res/netvalueclosecn.php?symbol=SZ162411) | `/history/netvalueclose/<sym>` | `netvaluecloseparagraph.php` |
| [校准记录](https://palmmicro.com/woody/res/calibrationhistorycn.php?symbol=SZ162411) | `/history/calibrationhistory/<sym>` | `calibrationhistoryparagraph.php` |
| 基金场内份额 | `/history/shareshistory/<sym>` | `fundshareparagraph.php` |

**未迁移**（PHP 里存在但没有对应数据源，见文末"已知缺口"）：
基金场内申购账户（`fundaccountcn.php`）、小心愿佯谬（`thanousparadoxcn.php`）、
仓位估算（`fundpositioncn.php`）。

## 一、为什么单独抽出 `historytable.py`

PHP 里这 6 个页面各自独立，但共享同一套**取数语义**与**显示口径**。Python 侧把它们
集中到 `python/auto/sql/historytable.py`：

* **显示口径**（`format_price_display` / `format_number_display` /
  `format_ratio_display` / `format_turnover_display` / `format_hm`）——
  一一对应 PHP `php/stock.php` 与 `php/ui/stockcomm.php` 的原函数；
* **取数语义**（`DailyCloseSql` / `StockHistorySql` / `DailyTimeSql` 等）——
  对应 PHP `php/sql/sqldailyclose.php` 与 `php/sql/sqlstocksymbol.php`。

放在 `auto` 侧而不是 `web` 侧，因为这些是**业务口径**，以后后台脚本（导 CSV、
算统计）也要用；`web` 侧只把结果渲染成表格。

`python/web/services/history_service.py` 负责"某符号某张表的某几行 + 列定义"，
`python/web/routes/history.py` 只做 URL 解析与模板渲染。

## 二、显示口径逐条对照

### 2.1 涨跌配色（`StockGetPriceDisplay`，`php/stock.php:91`）

PHP 的做法是：先按精度算一个容差 `$iDiff`（`0.5 / 10^precision`），
再与基准比较决定颜色。

```text
$fDisp > $fPrev + 容差 → 涨（red）
$fDisp < $fPrev - 容差 → 跌（green）
否则                   → 平（grey）
```

**容差很重要**：没有它，浮点噪声会被显示成涨跌。实测 `1.0004` 相对 `1.0`
在 precision=3 时算持平（差 0.0004 < 容差 0.0005）。

### 2.2 数字与比例（`GetNumberDisplay` / `GetRatioDisplay`）

两者都是 `StockGetPriceDisplay` 的包装，**唯一差别是基准值**：

| 函数 | 基准 | 含义 |
| --- | --- | --- |
| `GetNumberDisplay($v, $p, 'black', 'red')` | **0.0** | 正数黑、负数红 |
| `GetRatioDisplay($v, $p = 4)` | **1.0** | 大于 1 红、小于 1 绿 |

### 2.3 换手率（`GetTurnoverDisplay`，`php/ui/stockcomm.php:70`）

```php
GetNumberDisplay(100.0 * $fVolume / ($fShare * 10000.0), $iPrecision).'%'
```

`$fShare` 的单位是**万份**，所以要先乘 10000。漏掉这个 10000 会让换手率
大 10000 倍（作者文档里出现过 `5992222%` 这种明显异常值就是这个量级问题）。

实测（作者开发历程文档里公开的 SZ162411 表格）：

```text
日期        份额(万)     场内新增(万)  数量        换手
2026-09-30  79553.18   -4190.48     62923045   7.91%
2026-09-29  83743.66   -9276.61     46053204   5.5%
2026-09-28  93020.27   -162.65      87542131   9.41%
```

本版渲染结果与上表**逐项一致**（份额差 −4190.48 / −9276.61 / −162.65，
换手 7.91% / 5.5% / 9.41%）。

### 2.4 时间取到分（`GetHM`，`php/debug.php:119`）

PHP 是 `substr($strHMS, 0, 5)`。Python 侧多了一层处理：MySQL `TIME` 列经
PyMySQL 取回是 `datetime.timedelta`（`17:30:00` → `timedelta(seconds=63000)`），
直接 `str()` 得到 `"17:30:00"` 看着没问题，但跨天会变成 `1 day, 0:00:00`。
所以先规范化成 `HH:MM:SS` 再截前 5 个字符。

### 2.5 涨跌幅的阈值归零（`GetPercentageString`，`php/stock/stockref.php:129`）

```php
if (abs($fPercentage * $fDivisor) < (50.0 / pow(10, $this->GetPrecision()])))
    return '0';
```

即 `|涨幅% × prev| < 50 / 10^precision` 时整格显示 `0`（不带 `%`）。
数学上等价于 `|value − prev| < 0.5 × 10^-precision`（因
`涨幅% × prev = (value − prev) × 100`）—— "差值小于半个最小显示单位
就不值得占一格"。

**精度取被调用 ref 自己的 `GetPrecision()`**（`stocksymbol.php:994`）：
基金 3 / 外汇 4 / 其余 2，**不是**净值列的 `NETVALUE_PRECISION(4)`。
7 个调用点各按自己的 ref 取精度（对照 `fundhistoryparagraph.php:14/23`、
`netvaluehistoryparagraph.php:72/78/90`、`netvaluecloseparagraph.php:16/17`）。

**实测钉住的差异**（2026-10-20 对照生产端 sz161130cn.php，见 §7.7）：
SZ161130 误差列 10-19 行（净值 4.3704 vs EST 4.37015）与 10-08 行
（4.5515 vs 4.551192）本地显示 `-0.01%`、生产端显示 `0`；10-13 行
（4.586 vs 4.585471，`|change×prev| = 0.0529 > 0.05`）两边都保留 `-0.01%`。

`format_percentage_display(prev, value, precision=None)` 的 `precision`
传 `None` 时不做阈值判定（旧调用兼容）；页面调用点都应显式传。
PHP 那边 `$fDivisor == false` 会回落 `strPrevPrice` 再比；Python 侧
`prev` 恒显式传入，无效时 `percentage_change()` 返回 `None`（页面留空），
不会走到阈值这一步。

## 三、取数语义（容易错的几条）

### 3.1 "上一个交易日"是查出来的，不是日期减一天

```sql
WHERE stock_id = ? AND `date` < ? ORDER BY `date` DESC LIMIT 1
```

历史表只存交易日，所以必须这样查。用日历减一天会在周末和节假日出错。

### 3.2 分页语义（`_SqlBuildLimit`，`php/sql/_sqlcommon.php:63`）

| 参数 | SQL |
| --- | --- |
| `start == 0 && num != 0` | `LIMIT num` |
| `start != 0` | `LIMIT start, num` |
| `num == 0` | **不加 limit**（全部记录） |

URL 参数名沿用 PHP 的 `start` / `num`，所以
`/history/stockhistory/XOP?start=0&num=0` 就是"全部记录"。

### 3.3 复权价允许回落到上一交易日

`GetAdjClose($id, $date, $bStrict = false)`（`php/sql/sqlstocksymbol.php:83`）
默认**非严格**：当天没有记录就用上一交易日的复权价。PHP 原注释写着
"when hongkong market on holiday" —— 港股与 A 股假期不同，不回落到页面会整列空白。

### 3.4 所有行都必须带 `stock_id`

PHP 那边是对象自己知道 `stock_id`；Python 侧行是纯 dict，所以 SELECT 必须
显式带出 `stock_id`，否则行处理去查上一交易日时会 `KeyError`（实测踩过）。

## 四、业务口径（最关键的两条）

### 4.1 QDII 用**前一交易日**的净值

`UseSameDayNetValue()`（`php/stock.php:381`）：

```php
if (in_arrayQdii($strSymbol))            return false;   // QDII 用前一日
else if (in_arrayQdiiMix($strSymbol))    return in_arrayHkMix($strSymbol);
return true;                                             // 其余用当天
```

**这是设计而不是 bug**。作者原话：

> 要计算华宝油气当天的交易溢价，肯定是要跟前一天的净值比较啊。
> 当天的净值要等当晚美股收盘后才出来，否则的话我写这个净值估算有什么意义呢。

所以「基金溢价记录」和「净值价格比较」都是拿**当天行情**配**前一交易日净值**。
如果哪天净值缺失，那一行就整行不显示 —— 与 PHP 一致（PHP 里 `if ($strNetValue = ...)`
不成立就跳过）。

### 4.2 净值记录里的仓位是"反算"出来的

`_QdiiGetStockPosition()`（`netvaluehistoryparagraph.php:5`）的推导注释：

```text
(est * cny / estPrev * cnyPrev - 1) * position = (nv / nvPrev - 1)
```

即**净值涨幅 = 仓位 × 标的涨幅（含汇率变动）**，于是：

```text
仓位 = 净值涨跌% / 标的（已换汇）涨跌%
```

**只在净值涨跌超过 4% 时才计算**（`POS_NETVALUE_DIFF = 4.0`，
`php/ui/stockdisp.php:32`）。作者用它规避官方 EST 的其他误差来源 ——
净值和汇率本身都有噪声，波动小时反算出的仓位没有意义。

## 五、与 PHP 的有意差异

### 5.1 列名去歧义：两个「净值」→「净值」+「标的净值」

PHP 的 `GetNetValueTableColumn()` 给基金净值和中间层净值**复用同一个
`TableColumnNetValue`**，页面上出现两个同名的「净值」列，靠位置区分。
本版把中间层那列标成**「标的净值」**—— 语义相同、但读者不会搞混。

### 5.2 颜色用 CSS 类而不是 `<font color>`

服务层返回颜色名（`red`/`green`/`grey`/`orange`/…），模板映射成 CSS 类。
好处是同一份数据也能给 JSON 接口用（`/api/history/<kind>/<sym>`）。

### 5.3 加了一张"页面切换"导航

PHP 每个历史页面是独立 URL，页面之间靠正文里的链接互跳。本版在顶部加了一排
`历史价格 | 基金溢价记录 | 净值记录 | 净值价格比较 | 校准记录 | 基金场内份额`
的切换条，同一个符号在 6 个视图之间一键切换。

## 六、数据库改动

### 6.1 新增 `shareshistory` 表

**PHP 里表名是 `shareshistory` 不是 `fundshares`**
（`php/sql/sqldailyclose.php:303` `new DailyCloseSql('shareshistory')`）。
形状与 `netvaluehistory` 完全一致（同样继承 `DailyCloseSql`），
已加入 `python/dbtools/schema.sql`。

### 6.2 清掉种子占位历史行

历史页面是**第一次**把 `netvaluehistory` / `stockhistory` 的内容直接显示出来，
于是种子里 `2025-12-31` / `2026-01-02` 那批合成占位值立刻暴露：

```text
净值记录显示 SZ161130 净值 = 1.5234   （真实值 4.5452）
基金溢价记录算出 214.82% 溢价          （真实值 5.52%）
```

处理（`python/dbtools/_clean_seed_history.py`）：

* **按符号白名单 + 日期收窄**清理，只清基金与中间层符号；
* **关键**：`netvaluehistory` 里同时存着 4 个币种的汇率历史
  （`USCNY`/`HKCNY`/`EUCNY`/`JPCNY`，由 `forexcli.py` 从国家外汇管理局回补，
  从 2026-07 起共 246 行）。**按日期一刀切会把这批真实数据一起删掉** ——
  预演时实测会误删 160 行。所以必须按符号收窄。
* 种子的 `NET_VALUES` 改成真实净值（4.5452 / 3.1328 / 2.0379），
  `HISTORY` 改成真实行情并补上 `09-29` 一行 ——
  QDII 的联接要用前一交易日，只留 `09-30` 的话两个页面都是空表。

### 6.3 场内份额的样本数据（诚实说明）

生产端 `fundsharecn.php` **需要登录**（实测无凭据访问会 302 到
`account/logincn.php`），取不到真实 `shareshistory`。

`python/dbtools/_seed_shares_sample.py` 写入的是**作者开发历程文档里公开可见的
SZ162411 表格**（4 行），用途是验证页面列与换手率算法渲染正确。
**这不是完整历史数据，也不能据此判断业务结论。** 真实数据应由
持续写入的入口积累（PHP 侧是 `submitholdings.php` 之类）。

## 七、与生产端逐项核对

### 7.1 核对入口：历史页面要登录，从公开的基金页取基准

**6 个历史数据页面本身无凭据访问全部跳登录页**（实测
`fundhistorycn.php` / `stockhistorycn.php` / `netvaluehistorycn.php` /
`calibrationhistorycn.php` / `fundsharecn.php` 均返回「登录帐号」）。

但**基金页是公开的，而且内嵌了同样的历史表**，所以校验基准从基金页取：

| 基金页里的表 | 可作为哪个历史页的基准 |
| --- | --- |
| `referencetable`（代码/价格/涨幅/日期/时间/名称） | 行情部分 |
| `<sym>fundhistorytable`（日期/价格/净值/溢价/官方EST/时间/误差/标的净值） | 基金溢价记录 |
| `<sym>calibrationhistorytable`（日期/校准值/时间/次数/净值/对冲值） | 校准记录 |
| `<sym>fundsharetable`（日期/份额/场内新增/数量/换手/新增换手） | 基金场内份额 |

### 7.2 溢价率：4/4 完全一致

```text
符号        日期          场内价 / 净值        我们      生产端
SZ161130   2026-09-30   4.796 / 4.5452     5.52%    5.52%   ✓
SZ161125   2026-09-30   3.275 / 3.1328     4.54%    4.54%   ✓
SH513100   2026-09-30   2.352 / 2.0379    15.41%   15.41%   ✓
SZ162411   2026-09-30   0.936 / 0.9338     0.24%    0.24%   ✓
```

净值全部取**前一交易日**（生产页面里 `2026-09-30` 那行的净值就是 `09-29` 的），
与 `UseSameDayNetValue()` 的判断一致。

### 7.3 对冲值公式：反推出第四个符号的**仓位错误**

`StockCalcHedge() = 校准值 / 仓位`，再取整（`format_number_display(x, 0)`）。
用生产校准记录反向验证，**同时反推出生产实际使用的仓位**：

```text
符号        校准值         对冲值(生产)   反推仓位 = 校准值/对冲值   应为
SZ161130   44997.025     47365         0.9500                  0.95 (LOF)  ✓
SZ161125   16505.969     17375         0.9500                  0.95 (LOF)  ✓
SH513100  100358.446    100358         1.0000                  1.00 (ETF)  ✓
SZ162411    1289.731      1358         0.9497                  0.95 (LOF)  ✓
```

**由此发现一处数据错误**：种子里 `SH513100` 的仓位写成了 **0.98**，
而生产用的是 **1.0** —— SH513100 是上海 51 号段的 **ETF**，
`IsLofA()` 为假，`GetDefaultPosition()` 返回 1.0。已修正
（`python/dbtools/_fix_sh513100_position.py`），重标定后校准值随之从
13.942979 变为 13.6641195（`= 2.0379 × 6.705 / 1.0`），估值仍精确等于净值。

这组生产向量已固化为回归测试
（`test_historytable.HedgeTest.test_production_hedge_vectors` 与
`test_production_positions_match_get_default_position`）。

### 7.4 页面可用性

```text
kind                  SZ161130  SZ161125  SH513100  SZ162411  QQQ  ^NDX
stockhistory             200       200       200       200     200   200(空)
fundhistory              200       200       200       200(空) 200   200(空)
netvaluehistory          200       200       200       200(空) 200   200(空)
netvalueclose            200       200       200       200(空) 200   200(空)
calibrationhistory       200       200       200       200(空) 200   200(空)
shareshistory            200(空)   200(空)   200(空)   200     200   200(空)
```

**非 200 页面数：0**（`^NDX` 之类没有历史数据的符号返回 200 + 空表提示，
与 PHP 行为一致，不是 404）。

### 7.5 与生产端的一处**有意差异**

生产端「基金溢价记录」在 `fundest` 表没有该符号的记录时，
**整列都不显示**（「官方EST / 时间 / 误差 / 标的净值」四列）。本版同样如此
（`_columns_fundhistory` 按 `FundEstSql.count()` 判断）。当前 `fundest` 为空，
所以这四列暂时不出现 —— 写入端迁移后会自动出现。

### 7.6 净值列的精度：`GetNetValueDisplay()` 的 4 位口径（2026-10-19）

**现象**（用户对照生产端 `sz161125cn.php`）：「基金溢价记录」的净值列
本地显示 `3.155`，生产端是 `3.1548`；东财 / 新浪的基金净值也都是 `3.1548`。
查库确认 `netvaluehistory` 里存的就是 `3.1548` —— **纯显示层四舍五入**。

**PHP 口径**（`php/stock/mysqlref.php:75-79`）：

```php
function GetNetValueDisplay($fNetValue)
{
    if ($fNetValue === false)	return '';
    return $this->GetPriceDisplay($fNetValue, false,
        ($this->iNetValueCount > 0 ? NETVALUE_PRECISION : $this->GetPrecision()));
}
```

* **有净值记录**（`iNetValueCount > 0`，构造时 `netvaluehistory.count()`）→
  `NETVALUE_PRECISION` = **4 位**（`php/debug.php:13`）；
* 没有 → 退回符号自身精度（`StockSymbol::GetPrecision()`：A 股基金 /
  新浪基金 / B 股 3 位，外汇 4 位，其余 2 位）。
* 配套的取值三支在 `GetNetValue()`（`:60-73`）：新浪期货→复权价；
  有净值记录→该日期净值（缺日期返回 false）；**没有净值记录→
  `GetVal()` 取行情收盘价**。

**本服务漏译的 5 个调用点**（PHP 全文共 6 处，第 6 处在
`kraneshares.php:39`，不在本服务）：

| PHP 调用点 | 表格 / 列 | 修法 |
| --- | --- | --- |
| `fundhistoryparagraph.php:13` | 基金溢价记录 · 净值 | 恒 4 位（行能出来即命中净值表） |
| `netvaluecloseparagraph.php:15` | 净值价格比较 · 净值 | 恒 4 位（整表有 `CountNetValue()>0` 守卫） |
| `calibrationhistoryparagraph.php:11` | 校准记录 · 净值 | 4 位；无净值记录时补 `GetVal()` 回退 |
| `fundhistoryparagraph.php:24` | 基金溢价记录 · 末列（est 标的净值） | est 有净值行 4 位，否则 est 符号精度（^GSPC 2 位） |
| `netvaluehistoryparagraph.php:87` | 净值记录 · 标的净值 | 同上；并补 `GetVal()` 回退（原来该列对指数 est 恒空） |

**两个附带着修掉的取数问题**（都与 `GetNetValue()` 三支有关）：

1. 基金溢价记录末列原来不区分"est 没有净值记录"和"有记录但缺该日期"，
   两种情况都回退到行情价 —— 后者 PHP 是**空格**。现在只在
   `est_has_netvalue == false` 时回落。
2. 净值记录的「标的净值」列原来完全没有 `GetVal()` 回退，演示库里
   ^GSPC / ^NDX 这类**指数 est** 的该列永远是空的，而生产端有值
   （指数收盘价，2 位精度）。补上后 `SZ161125` 该列显示
   `7718.94` / `7805.45`，与生产端同源。

**为什么单测和逐例对照都没抓到**：`_php_fundhistory_check.php` 的
`RefStub::$iNetValueCount` 恒为 **0**，PHP 侧于是也退到 3 位，与 Python
的 3 位"一致地错"。现在按例装配（默认 1，行存在即命中净值表），
并新增 4 个精度用例（case.8-11：净值四位 / est 无净值行取行情价 2 位 /
est 有净值行四位 / est 有净值行缺日期为空），自检注入"净值列退回 3 位"
可被 8 例抓到。

**实测**（本地 vs 生产端 `sz161125cn.php`，2026-10-19）：

```text
日期        价格    净值      溢价    官方EST  时间    误差
10-16       3.299   3.1548    4.57%   3.155    04:06   0        <- 生产端
10-16       3.299   3.1548    4.57%   3.153    11:37   -0.04%   <- 本地（修后）
```

净值 / 价格 / 溢价三列已逐格一致；**官方EST 与误差列的差异是数据新鲜度
问题不是显示问题**：本地 `fundest[10-15]` 是 11:37 的盘中快照
（`fundestcli` 的禁忌窗口），生产端是 04:06 美股收盘后算的；
`fundest[10-14]` 本地整条缺失、`^GSPC` 缺 10-15 行情，所以那两行
生产端有的 EST 三列本地不全。按每日例程（`README_p0_data_layer.md` §十）
重跑即可，不需要改代码。

### 7.7 误差列的阈值归零（2026-10-20）

**现象**（用户对照生产端 `sz161130cn.php`）：「基金溢价记录」误差列
10-19 / 10-08 两行本地显示 `-0.01%`，生产端显示 `0`；10-13 行两边都显示
`-0.01%`。净值 / 价格 / 溢价 / 官方EST / 时间各列此前已对齐（§7.6 + fundest
重算），误差列是最后一处差异。

**根因**：`format_percentage_display()` 没有实现 PHP
`GetPercentageString()` 的阈值归零（`stockref.php:129`，口径见 §2.5）。
按库里原值验算（精度 3 = 基金符号）：

```text
日期        净值      官方EST     |change%×prev|   阈值 50/10³   生产端
2026-10-19  4.3704    4.37015    0.0250 < 0.05     归零        0       ✓
2026-10-13  4.586     4.585471   0.0529 > 0.05     保留       -0.01%   ✓
2026-10-08  4.5515    4.551192   0.0308 < 0.05     归零        0       ✓
```

**修法**：`format_percentage_display(prev, value, precision=None)` 加
`precision` 参数与阈值；`history_service` 7 个调用点各按 PHP 对应 ref
传自己的 `GetPrecision()`（基金 3 / 汇率 4 / est 符号 2，见 §2.5 的表）。
`_row_fundhistory` 增收 `symbol` 参数（溢价与误差两格都用基金自己的精度，
PHP `$ref->GetStockRef()` 的 `GetPrecision()`）。

**对照面同步**：`_php_fundhistory_compare.py` 的 monkeypatch 签名随
`symbol` / `precision` 参数更新；新增 case.12（阈值归零三行实数）与
第 6 个自检注入「百分比不做阈值归零」（1 例抓到）；`_php_fundhistory_check.php`
的 `$ref->iPrecision` 改为按例装配（默认 3，QQQ 这类美股
`fund_precision => 2`）—— 原来恒 3 是替身失真，只是既有 QQQ 用例的百分比
都远大于阈值没暴露出来。

**实测**（本地 vs 生产端 `sz161130cn.php`，2026-10-20）：9 行误差列
逐格一致 —— 10-19 `0`、10-16 `0`、10-15 `0`、10-14 `0`、10-13 `-0.01%`、
10-12 `0`、10-09 `0`、10-08 `0`、09-30 `0`。至此「基金溢价记录」七列
与生产端全部对齐。

## 八、测试

`python/auto/test_historytable.py` + `web/tests/test_history.py`。
2026-10-19 净值精度轮新增 `web/tests/test_history.py::
NetValueDisplayPrecisionTest`（8 例：三张表的净值四位 + est 末列精度两档
+ est 缺日期为空 + 净值记录的 `GetVal()` 回退）；2026-10-20 阈值轮新增
`test_historytable.py::PercentageTest` 的 3 个阈值用例（取自
sz161130cn.php 误差列实数）。
全量（2026-10-20 实测）：`auto` **1278 个**、`web` **759 个**；
PHP 逐例对照 `_php_fundhistory_compare.py` **13/13**，自检 **6/6**。

## 九、已知缺口

1. **持仓反算仓位未实现**：`_QdiiMixGetPosition()` 需要 `holdings` 表（持仓明细），
   当前 `holdings` 为空，所以混合型（如 SZ164906）的仓位列会留空 ——
   **不编造**一个看起来合理的数字。等 `holdings` 有数据再补。
2. **`fundaccountcn.php` / `thanousparadoxcn.php` 没有对应表**
   （基金场内申购账户、小心愿佯谬），本次不迁移。
3. **`fundpositioncn.php`（仓位估算）** 需要按品种分别取中间层净值与汇率做回归，
   逻辑比其它页面重，留作后续单独一项。
4. **`fundest` 表为空**：官方 EST 的记录（PHP 在拿到官方净值时写入）还没有迁移
   写入端，所以「基金溢价记录」暂时不会出现「官方EST / 时间 / 误差」三列。
   列定义已经按 PHP 实现（`_columns_fundhistory`），有数据就会自动出现。
5. **中间层符号缺行情历史**：`^NDX` / `^GSPC` 在 `stockhistory` 里没有记录，
   所以「历史价格」页对基金显示对照列时会留空。补抓行情历史即可。
