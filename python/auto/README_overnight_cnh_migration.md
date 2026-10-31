# 义工群对冲计算器迁移（`overnightcnhcn.php` → `/overnightcnh`）

> 复核日期：2026-10-31
> 源文件：`woody/res/overnightcnhcn.php`（200 行）
> Python：`web/services/overnight_cnh_service.py` + `web/templates/overnight_cnh.html`
> 路由：`GET|POST /overnightcnh`（Flask `url_map` 51 → **52** 条）

## 一、这一页干什么

回答一句话："我手上有一笔要平衡的离岸人民币 CNH，换成义工群覆盖的这些 QDII
基金，**每只该买/卖多少股**、对冲标的要动几张。"

它与轮动交易页（`README_rotationtrading_migration.md`）是姊妹页，方向相反：

| | 轮动交易页 | 本页 |
|---|---|---|
| 输入 | "要卖掉多少股" | "要平衡多少钱" |
| 符号表 | 按当前标的**分四族**取候选 | **写死的 23 只**（`GetOverNightSymbolArray()`） |
| 输出 | 每只候选的股数 | 每只基金的钱折成股数 + 对冲标的股数 |

**它不下单、不写库**：表单 POST 回自己，只是用新金额重算一遍。

## 二、输入串的四种形态（`_convertCnhInput`）

输入框里的东西不是纯数字，末几位决定换算口径：

| 后缀 | 含义 | 算式 |
|---|---|---|
| `F` | 基金申购 | `金额 ÷ (1+申购费) × 溢价 × 仓位` |
| `MCL` | 原油期货 | `期货价 × 中间价 × 溢价修正 × 100 × 手数` |
| `MGC` | 黄金期货 | 同上，乘数 `10` |
| （无） | 纯金额 | `floatval($strInput)` |

三种都走最后一步 `abs()` —— 方向由**首字符**表达，数量只关心大小。

`F` 那一支的"溢价"是 `卖一价 ÷ 官方净值`，**与买卖方向无关**（申购永远按卖价
成交）。这一点是照抄：别因为它出现在"卖出这笔钱"的语境里就改成买一价。

`MCL`/`MGC` 的溢价修正只补**属于仓位的那一部分**：

```php
$fPremium = $ref->GetVal() / $fRealtime;
// $fCnh *= $fPremium;                                  ← 作者试过的写法，已注释
$fCnh *= 1.0 + ($fPremium - 1.0) * $ref->GetPosition();
```

那一行注释掉的代码和那个 `1.0` 的基准都不能动 —— 全额乘溢价会把"这只基金本身
的涨跌"也算进对冲敞口，而实际只需要补仓位那部分。

## 三、金额 → 股数

```
CNH 敞口  ÷ 仓位  ÷ 可成交价   →  建议股数
                              round(x / 100) × 100   →  凑整到 100 股
```

`可成交价` 由方向决定（`GetAvailablePrice($bSell)`：卖出看买一、买入看卖一），
与 `§二` 里那个恒用卖一价的"溢价"是两回事。

**建议数量超过该档挂单量时染红**（PHP `GetFontElement($strHintQuantity)`）。

## 四、两套"建议对冲操作"文案（`_echoOverNightCnhItem` 的 if / else）

判据是**引用对象上有没有某个方法**，顺序不能换：

```php
if ($est_ref)                                  // ① 中间层建出来了
{ ... GetStockHedge() 换成对冲标的股数 ... }    //    文案：买入5000股QQQ
else if (method_exists($ref, 'GetHoldingsDate'))
{ ... 按季报成分股逐只折股 ... }                //    文案：买入1234.5股NVDA、5678.9股MSFT，共6913股。
```

注意 `$est_ref` 在"有 `GetStockRef` 但中间层没建出来"时也是假值 —— 那种 QDII
会掉进第二支，而它没有 `GetHoldingsDate`，于是两格皆空。本版用
`_has_method()` + `if est_ref:` 复现同一条路径。

**前缀是反的**：`$ar[] = ($bSell ? '买入' : '卖出').$strMemo` —— 卖本基金就要
**买**对冲标的。抄错这一层会让整页文案反过来。

持仓那一支有两处看着像 bug 但必须照抄的行为：

1. 汇率取的是**季报那天**的（`GetVal($strDate)`），不是今天的；
2. 那句 `"，共N股。"` 是**无条件补**的 —— 一只成分股都没折出来时文案是
   `"，共0股。"`。演示库的持仓型基金全是 0 行持仓，所以本地看到的就是这个。

`SZ160216` / `SZ161815` / `SZ163208` / `SZ164701` 四只**不补**这句（作者没写
理由，照抄）。

## 五、闸门

1. **输入为空**（点"清除"）→ 只出表单，不出表。注意 `''` 是**通过**的：
   PHP 判的是 `GetEditInput() === false`，清除给的是 `''` 不是 `false`。
   （与轮动页相反 —— 那边 `''` 会回落到 -1000000 占位值。）
2. **引用对象建不出来** → 这一行跳过，符号进页脚的"算不出"清单。
3. **该方向没有挂单** → 这一行只有代码一格（变长行，与轮动页同一形状）。
4. **换算不出来** → 建议数量与对冲格留空，符号进清单（见 §七偏离 2）。

输入的三段回落顺序（`overnightcnhcn.php:165-168`）：

```php
if (($strInput = GetEditInput()) === false)              // ① POST
    if (($strInput = $acct->GetQuery()) === false)       // ② ?overnightcnh=
        $strInput = '100000';                            // ③ 默认
```

`GetOvernightCnhLink()` 拼的是 `?overnightcnh=95000`，所以 ② 这一支在真实
链接上是常用的。

## 六、表与列

六列（`overnightcnhcn.php:142-147`）：

```
代码 | 可买/卖价格 | 可买/卖数量 | 建议数量 | 对冲值 | 建议对冲操作
```

最后一列是 `new TableColumn($strHint.'对冲操作', TableColumnGetLastWidth($ar))`
—— **吃剩余宽度**，宽度依赖 `LayoutGetDisplayWidth()`（布局层，未迁移），
本版改成 `build_columns(prefix, layout_width)` 显式传入。

## 七、刻意偏离清单

| # | 偏离 | 理由 |
|---|---|---|
| 1 | 表 id 用 `overnightcnh-table` | PHP 是 `$acct->GetPage()`，本页只有一张表，不冲突 |
| 2 | 换算不出来时**不 500** | 见下 |
| 3 | `MCL` / `MGC` 取不到期货价或中间价时按"算不出"处理 | PHP 得 0，整页 23 行一起显示 0 股 |
| 4 | 三个"点击复制"链接（`CopyPhpLink`）改成普通 GET 链接 | 本地没有剪贴板脚本；语义是"点一下按那个口径算一遍" |
| 5 | `<font>` 换成格子的颜色字段 | 渲染层归模板，与项目既有做法一致 |
| 6 | `EchoTableParagraphBegin()` 的"表格超屏就不输出"未迁 | 依赖 `LayoutGetDisplayWidth()` 与 `$_SESSION['mobile']` |
| 7 | 页尾 `$acct->EchoLinks()` 那一整块不迁 | 账号体系暂缓，只留三个站内回链 |
| 8 | `StockPrefetchArrayExtendedData($arSymbol)` 不迁 | 那是 PHP 侧省网络请求的预取；本版引用对象从库快照取数，没有可省的一步 |

### 偏离 2 细说：哪一步会除零

PHP 8 里 `/` 的除数为 0 会抛 `DivisionByZeroError`（**整页 500**，不是警告）：

| 位置 | 表达式 | 缺什么就炸 |
|---|---|---|
| `_fundCnhInput` | `floatval(卖一价) / $ref->GetOfficialNetValue()` | 官方净值 |
| `_echoOverNightCnhItem` | `($fCnh / $fPos) / $fPrice` | 仓位、本方向价格 |
| 持仓那一支 | `$fCny / $cny_ref->GetVal($strDate)` | 季报那天的汇率 |

演示库里 11 只标的没有官方净值（8 只 `QdiiMix` 的净值行取不到、`SZ161226`
连 `stock` 行都没有），**PHP 打开这一页会直接白屏**。本版把这三处都做成
"这一行算不出"，页脚列出符号与原因。

还有一处结构差异值得记下来：PHP 的 `_convertCnhInput()` 在"有没有挂单"那道
闸门**之前**调用，所以哪怕这一行最后只剩代码一格，它也已经除过一次零了；
本版先判挂单量再换算。两种顺序对**能算出来的行**结果完全一样，差别只在
"本来就没有挂单、净值也缺"的那些行上是崩还是空 —— 那种行本来也没有信息量。

## 八、对照结果（69 例 + 5 例合成，617 条断言全通过）

`auto/_scratch/_php_overnight_cnh_check.php` + `_php_overnight_cnh_compare.py`。

### 对照口径：喂同样的原始数，比公式与文案

**这一页没法整份 require PHP 真身**：每个数都从引用对象上取，而 PHP 的
`MyStockReference` / `QdiiReference` **构造时联网抓新浪**，盘口五档在 PHP 侧
只有"现场抓"这一条路（`grep realtimequote php/` 为空 —— 那张快照表是迁移时
才加的）。

所以探针把 Python 从真库读到的原始数（盘口价/量、官方净值、仓位、对冲值、
中间价、期货价、持仓明细与收盘价）写成 JSON 喂给 PHP，跑**从源码原样抠出来**
的算式，再和 Python 用同一个真引用对象算出来的结果逐格比：

| 对照项 | 断言数 |
|---|---|
| `StockGetFundFeeRatio()` 费率表（23 只 + `SZ160416` / `SZ161128` / `SZ161116` / 不存在的前缀） | 26 |
| `_convertCnhInput()` 四种后缀的换算 | 64 |
| 六格骨架（格数 / 代码 / 价格 / 数量 / 建议数量 / 超量标红 / 对冲值 / 文案） | 522 |
| `MCL` / `MGC` 两支（合成数，含溢价修正） | 5 |

**不在本对照范围**：那些原始数是谁从哪张表取的。那一半由
`web/tests/test_overnight_cnh_page.py`（47 例）与真库冒烟覆盖。

**排除 92 例**，三类，都不是"两边算得不一样"：

1. `MCL` / `MGC` —— 演示库没有 `hf_CL` / `hf_GC` 快照，本版判定"算不出"；
2. `F` / `-F` 遇上没有官方净值的 11 只 —— PHP 会除零；
3. 持仓型但季报日期缺失的 8 只（纯金额那几例）—— PHP 在"CNH ÷ 当日汇率"
   那一步除零。

脚本会把排除数量打印在最后一行，随演示库数据补齐而减少。

## 九、演示库数据缺口（不是 bug）

| 现象 | 原因 |
|---|---|
| 11 只标的的"建议数量"是空的 | 官方净值取不到（8 只 `QdiiMix` 净值行缺失、`SZ161226` 无 `stock` 行） |
| `1MCL` / `1MGC` 整页 22 只算不出 | `hf_CL` / `hf_GC` 在 `realtimequote` 里没有行 |
| 持仓型基金文案是"，共0股。" | `holdings` 表里这些基金是 0 行 —— PHP 那句后缀是无条件补的 |
| `SZ161226` 只有代码一格 | 没有 `stock` 行 → 没有盘口 |

用纯金额（如 `?overnightcnh=100000`）时 23 只全部算得出来，是最能看出这一页
长什么样的入口。

## 十、文件清单

| 文件 | 说明 |
|---|---|
| `web/services/overnight_cnh_service.py` | 新增：四种后缀 + 两套文案 + 六列 + 三层闸门 + `build_fund_reference()` |
| `web/templates/overnight_cnh.html` | 新增：表单（含三个预设链接）+ 表 + "算不出"清单 |
| `web/routes/overnight.py` | 新增：`GET\|POST /overnightcnh` |
| `web/routes/__init__.py` | 注册 `overnight_bp` |
| `web/app.py` | 新增注入点 `overnight_refs_builder` |
| `web/tests/test_overnight_cnh_page.py` | 新增 47 例 |
| `auto/_scratch/_php_overnight_cnh_check.php` | 新增：PHP 真身探针（公式与文案） |
| `auto/_scratch/_php_overnight_cnh_compare.py` | 新增：逐例对照 |

### 为什么引用对象自己建（`build_fund_reference`）

`StockGetFundReference()` 对 `QdiiMix` 是**裸 `new HoldingsReference()`**，而
现成的 `StockGetHoldingsReference()` / `StockGetFundPairReference()` 都带
"表里有没有行"的闸门。演示库里 8 只 `QdiiMix` 一行持仓都没有、`SZ161226` 连
`stock` 行都没有 —— 走闸门版会整批返回 `None`，页面上直接少 9 行；而 PHP
那边照样建对象，数据不全只体现在"某几格是空的"。所以照 PHP 直接构造。
