# 轮动交易页迁移（`rotationtradingcn.php` → `/stock/<symbol>/rotationtrading`）

> 复核日期：2026-10-31
> 源文件：`woody/res/rotationtradingcn.php`（150 行）
> Python：`web/services/rotation_service.py` + `web/templates/stock_rotation_trading.html`
> 路由：`GET|POST /stock/<symbol>/rotationtrading`（Flask `url_map` 50 → **51** 条）

## 一、这一页干什么

回答一句话："我持有某指数的 QDII 基金，现在想换成**同一指数的另一只**，该挂
多少、能不能成交、换完划不划算。"

同一个指数在国内往往有十来只 QDII 跟踪（纳指 100 有 12 只、标普 500 有 4 只），
折溢价率差得很远 —— 生产端实测 `SH513100` 溢价 **18.99%**、`SH513110` 只有
**13.68%**。卖掉高溢价那只、买进低溢价那只，对冲敞口不变，5.31% 的差价落袋。
本页把这个差价换算成可以直接下单的股数。

页面的 meta description（`GetMetaDescription()`）写的就是这个用途。

**它不下单、不写库**：表单 POST 回自己，只是用新数量重算一遍。

## 二、计算链（`_echoRotationTradingItem`）

```
轮动数量 fInput
   ÷ GetStockHedge(本基金)          → fEstQuantity（对冲标的数量，可负）
   × GetStockHedge(候选)            → 每只候选的对等股数
   round(x / 100) × 100，再 abs()   → 建议数量
```

三层要注意的地方：

1. **`GetStockHedge()`（`php/stock.php:401`）** 有两条分支：
   * 本基金属于 `SpyQdii` / `QqqQdii`（有杠杆配对）→ 在 `calibrationhistory`
     里**从最近日期往回找**第一个"本基金与 QQQ/SPY 同日都有校准值"的日期，
     返回 `(cal_self ÷ pos_self) ÷ (cal_lev ÷ pos_lev)`；
   * 否则用 `GetCloseNow()` 的当日校准 ÷ 仓位；
   * 都取不到 → **1.0**（页面不崩，只是退化成 1:1）。
   `pos` 读 `fundposition`（`FundPositionSql`），SH 反向 ETF 是负数。
2. **`fEstQuantity` 是负数**（卖出方向），但**建议数量取 `abs()`** —— 方向已经由
   "卖本基金 / 买候选"表达，数量本身只关心大小。
3. **建议数量用未取整的 hedge 算**，而"对冲值"列显示的是取整后的。所以拿
   页面上那两个数反推会对不上几百股（`871000` vs `2127 × -409 = 869943`），
   这是照抄 PHP，不是笔误。

## 三、方向翻转两次（最容易抄错的地方）

```php
$bSell = ($fInput < 0.0);                 // 本基金：负数 = 卖
$bRotationSell = $bSell ? false : true;   // 候选：**反过来**
```

| 输入 | 本基金 | 候选 | 表头前缀 | 本基金用哪一档 |
|---|---|---|---|---|
| `-1000000`（默认） | 卖 | 买 | `可买价格` / `可买数量` | `GetBidPrice` / `GetBidQuantity` |
| `100000` | 买 | 卖 | `可卖价格` / `可卖数量` | `GetAskPrice` / `GetAskQuantity` |

没填时 `$fInput = -1000000.0`（`rotationtradingcn.php:90`）—— 一个必然超过任何
盘口量的占位值，让首屏先按"卖 100 万"算一版，方便估量级。

`GetEditInput()` 对 `''`（点"清除"）与 `'0'` 都返回假值，**于是都回落到
-1000000**，不是 0。这一条在 `_rotation_input()` + `_php_truthy()` 里钉住。

## 四、四族分派与一处刻意偏离

`_getRotationSymbolArray()` 顺序固定，命中即返回：

| 序 | 判据 | 数组 | 成员数 | 第六列前缀 |
|---|---|---|---|---|
| 1 | `in_arrayXopQdii` | `QdiiGetXopSymbolArray` | 3 | 中间层符号（如 `^SPXOP`） |
| 2 | `in_arrayXbiQdii` | `QdiiGetXbiSymbolArray` | 2 | 中间层符号 |
| 3 | `in_arrayQqqMatch` | `QdiiGetQqqMatchArray` | 12 | `QQQ` |
| 4 | `in_arraySpyMatch` → **本版 `in_arraySpyQdii`** | `QdiiGetSpyMatchArray` | 4 | `SPY` |

第六列的符号来自 `GetLeverageHedgeSymbol()`，为假时退回 `GetEstRef()->GetSymbol()`。

### 偏离：标普这一支放宽到 `SpyQdii`（用户 2026-10-31 要求）

生产端详情页的入口链接（`php/ui/tradingparagraph.php:151`）判的是
`in_arraySpyQdii()`（**5 只**，含 `SZ159655`），而页面自己判
`in_arraySpyMatch()`（**4 只**，不含）。两边不齐的后果实测过：

```
$ curl 'https://palmmicro.com/woody/res/rotationtradingcn.php?symbol=SZ159655'
# 除了 <h1>SZ159655轮动交易</h1>，页面上什么都没有
```

本版把**页面这一侧**放宽到 `SpyQdii`，入口不动。结果是 `SZ159655` 拿到 4 行
候选（它自己不在匹配数组里，所以四只全列），两边从此对齐。没有收窄入口，
原有可用的标的全部不受影响。

对照脚本把这条差异**显式断言**成"PHP null / Python 4 只"，不是当失败跳过 ——
哪天有人改回 `SpyMatch`，对照会立刻红。

## 五、三层闸门

| # | PHP | 本地 | 表现 |
|---|---|---|---|
| 1 | `_getRotationSymbolArray()` 为假 | `page["available"] = False` | 整块跳过，**只剩标题**（连分组行都没有） |
| 2 | `if ($strQuantity = $ref->GetAvailableQuantity($bSell))` | `page["summary"] is None` | 说明行与表都不出（那个 `if` 包住了全部） |
| 3 | 单行 `if ($strQuantity = $stock_ref->GetAvailableQuantity($bRotationSell))` | 行只有**一格**（代码） | 变长行，表头七列、这一行一格 |

第 3 条是照抄：PHP 先把代码那一格压进 `$ar`，再看有没有量。本地不补空格。

另外对冲值为 0 时**不做除法** —— PHP 8 那里会 `DivisionByZeroError` 整页 500，
本版退化成一段说明文字（偏离 5）。

## 六、表与列

表 id = `<symbol>rotationtrading`（PHP 是 `GetPage().'table'`，所有符号共用一个
id；本地带上符号以便唯一 —— 偏离 2）。

| 列 | 取什么 | 本地实现 |
|---|---|---|
| 代码 | `CopyPhpLink('symbol=X')`，**同页切标的** | `row.href` → `/stock/<X>/rotationtrading` |
| 可买/可卖价格 | `GetAvailablePrice()`，`GetPriceDisplay()` 格式化 | `stock_ref.get_price_display()` |
| 可买/可卖数量 | 同档挂单量，原样 | 原样字符串 |
| 建议数量 | `abs(round(hedge × estQty / 100) × 100)` | `hint_quantity()` |
| 对冲值 | `GetNumberDisplay($fHedge, 0)` | `number_display()` |
| QQQ/SPY 数量 | `GetNumberDisplay($fEstQuantity, 0)` —— **每行相同** | 同上 |
| 溢价 | `GetPercentageDisplay($fEst, $fPrice)`，无估值则**少一格** | `get_percentage_text()` |

两处颜色（`<font>` 换成 `TableRow` 的格子颜色，偏离 4）：

* **建议数量超过该档可成交数量 → 红**（PHP `GetFontElement($q)` 默认色 `red`）；
* **负数 → 红**（`GetNumberDisplay` 的 `$strLess = 'red'`；注意它的
  `$strGreater` 是 `black`，**正数不染色**，与 `StockGetPriceDisplay` 默认的
  "涨红跌绿"不是一回事）。

## 七、顺手修的一个真偏差：价格为零不该丢盘口

`refquote.apply_snapshot` 原来在 `price` 为 `'0'` 时直接 `return False`，
把同一份快照里的 `orderbook` 一起丢了。演示库 `SH513100` 正是这种数据
（`price='0'`、盘口完整：买一 2.289 × 2615400），于是本页显示"没有可卖的挂单"，
而生产端有数据。

PHP 那边不会这样：`_onSinaDataCN()` 先把五档数组填满，之后才用
`IsZeroString($strPrice)` 把"价格为零"换成昨收。所以改成判零分支里
**先装盘口、再返回 False**（返回值语义不变，仍表示"价格没装上"）。

副作用面：`trading_service` 那边也会因此看到更多盘口 —— 那正是 PHP 的行为。

## 八、对照结果（PHP 真身，全一致）

`_php_rotationtrading_check.php` 整份 require 真
`php/sql/sqlstocksymbol.php`（在 `SqlDieByQuery()` 里拦 `CREATE TABLE`）与
**零依赖**的 `php/stock/stocksymbol.php`，所以 `in_array*` 查表、
`fundposition` / `calibrationhistory` 读数全是真身；`GetStockHedge()` /
`GetNumberDisplay()` / `_getRotationSymbolArray()` 三个函数从源码原样抠出。

`_php_rotationtrading_compare.py` 逐项比：

| 组 | 例数 | 结果 |
|---|---|---|
| 四族分派（含 `''` / 陌生符号 / 边界符号） | 10 | 全一致 |
| 刻意差异 `SZ159655` | 2 条断言 | PHP `null`、Python 4 只（符合设计） |
| 对冲值（8 只，真库） | 8 | 全一致，如 `SH513100 = 2442.026654547` |
| `GetNumberDisplay($v, 0)` 文本 | 9 | 全一致（含 `-0.5 → "-1"`、`0.4 → "0"`） |
| 同上颜色 | 9 | 全一致（含 `-0.4` 是 grey 不是 red） |
| 建议数量两层取整 | 5 | 全一致 |

合计 **43 条断言**（含"刻意差异"那 2 条），一次跑完 `全部一致`。

颜色那两行值得单说：`-0.4` 落在容差（`0.5 / 10^0 = 0.5`）之内，PHP 给
**grey**，不是 red；`-0.5` 不满足 `< -0.5`，也是 grey，但文本是 `-1`
（`number_format` 的 half-away-from-zero）。两条都在用例里。

## 九、演示库数据缺口（不是 bug）

`realtimequote` 里纳指 12 只的盘口：

| 状态 | 符号 | 页面表现 |
|---|---|---|
| 两侧完整 | `SH513100`、`SZ161130` | 表头 + 多行数据 |
| 只有 `price`，盘口全是 `0` | `SH513110` / `SH513390` / `SH513870` | 这些行**只有代码一格** |
| `price=0` 且盘口为 0 | `SZ159501` / `SZ159513` / `SZ159632` / `SZ159659` / `SZ159660` / `SZ159696` / `SZ159941` | 同上 |

所以本地 `/stock/SH513100/rotationtrading` 只有 `SZ161130` 一行完整 —— 与 PHP
的 `if ($strQuantity = ...)` 判据一致，缺的是数据。正文里已说明"盘口来自
`realtimequote` 快照"。

## 十、刻意偏离清单

| # | 偏离 | 理由 |
|---|---|---|
| 1 | 标普分支 `SpyMatch` → `SpyQdii` | 修 PHP 的入口/页面判据不齐（用户要求），见 §四 |
| 2 | 表 id 带符号 | PHP 用页面名，多符号共用一个 id |
| 3 | 不做页头"分组名称代码"行 | 本地详情页已有同类信息，重复无增量 |
| 4 | `<font>` 换成 `cell-red` | 渲染层归模板，与项目既有做法一致 |
| 5 | 对冲值 0 不做除法 | PHP 8 会整页 500 |
| 6 | `EchoTableParagraphBegin()` 的"表格超屏就不输出"未迁 | 依赖 `LayoutGetDisplayWidth()` 与 `$_SESSION['mobile']`，本地列宽归 CSS |
| 7 | 提交后的 `$_POST` 清洗（`SqlCleanString`）未做 | 本地只把它当数字解析（`floatval` 等价），不拼 SQL |

## 十一、文件清单

| 文件 | 说明 |
|---|---|
| `web/services/rotation_service.py` | 新增：族分派 + 七列 + 三层闸门 + `number_display` / `hint_quantity` |
| `web/templates/stock_rotation_trading.html` | 新增：说明行 + 表单 + 表 + 两种空态 |
| `web/routes/stock.py` | 新增 `GET\|POST /stock/<symbol>/rotationtrading`；`_get_trading_service()` 补链接构造器 |
| `web/services/trading_service.py` | 新增 `make_rotation_link_builder()`；默认工厂的 docstring 更新 |
| `web/routes/fund.py` | `_get_trading_service()` 同样补构造器（基金详情页的"轮动交易"入口就此可用） |
| `auto/ref/refquote.py` | 修：价格为零时仍装盘口 |
| `auto/test_orderbook.py` | 新增 `ApplySnapshotTest`（2 例） |
| `web/tests/test_rotation_trading_page.py` | 新增 35 例 |
| `auto/_scratch/_php_rotationtrading_check.php` | 新增：PHP 真身探针 |
| `auto/_scratch/_php_rotationtrading_compare.py` | 新增：逐例对照 |
