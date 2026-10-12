# `EchoFundPairSmaParagraphs` 迁移记录

对应 `php/ui/smaparagraph.php:156`，由 `woody/res/php/_qdiigroup.php:101` 调用：

```php
EchoQdiiSmaParagraph($ref);                                  // :97
if (count($arLev) > 0)
{
    EchoFundListParagraph($arLev, '_callbackFundListHedge');  // :100
    EchoFundPairSmaParagraphs($ref->GetEstRef(), $arLev);     // :101  ← 本轮
}
EchoFutureSmaParagraph($ref);                                 // :103
```

## 一、结论

| 项 | 结果 |
|---|---|
| 新增业务代码 | `web/services/qdii_fund_list.py` 之外**只改接线**，公式一行未重写 |
| 真实 PHP 逐例对照 | **5 例 0 差异**（列 7 项 + 每例表头/格子/回调口径） |
| 对照脚本自检 | **4/4** 注入错误被抓到 |
| 测试 | web **411** OK（+15）、auto **877** OK、新模块 3.13 双绿 |
| 页面实测 | `SZ161130` 两张（QQQ / TQQQ）、`SZ161125` 一张（SPY）、`SZ162411` 不出现 |

## 二、这一轮到底迁了什么

**服务层早就有了**：`SmaService.fund_pair_tables()`（`sma_service.py:253`）连
"`''` 不是 `false`" 那条严格比较都写好了注释。缺的三块是：

1. **接线** —— `web/routes/fund.py` 的 `_build_fund_pair_sma()`，挂在模板变量
   `fundpairsma` 上，插在「基金指数对照表」之后、期货均线之前（PHP 的顺序）；
2. **模板** —— `fund_detail.html` 一个 `for` 循环逐个渲染（`data_table` 宏复用）；
3. **测试** —— `fund_pair_tables` 此前**零覆盖**。

### 顺手修掉的一个真 bug：对照列漏了 maroon

```php
class TableColumnStock extends TableColumn
{
    public function __construct($sym, $iWidth = 80)
    {   parent::__construct($sym->GetDisplay(), $iWidth, 'maroon'); }
}
```

`sma_service._stock_column()` 原本写成 `column_base(display, 90)` —— **不带颜色**。
生产端表头是 `<th width=90><font color=maroon>SH</font></th>`，本版渲染成黑字。
数值全对、只差一个颜色，是最难发现的那一类偏差。改成 `column_stock(display, 90)`，
并在单测与路由测试里各钉一条（`test_the_stock_column_is_maroon` /
`test_the_stock_column_is_rendered_maroon`）。

**这个 bug 同时影响 `EchoQdiiSmaParagraph`** —— 同一个 `_stock_column`。

## 三、PHP 逐例对照

`_php_fundpairsma_check.php` + `_php_fundpairsma_compare.py`。

### 与上一轮的做法差别

上一轮（`_php_qdiicolumn_check.php`）是**抽函数体**重建。这一轮列组装改成
**直接 require PHP 自己的三个文件**：

```php
require_once $uiDir . '/htmlelement.php';
require_once $uiDir . '/table.php';
require_once $uiDir . '/stocktable.php';
```

`TableColumn` / `TableColumnEst` / `TableColumnPremium` / `TableColumnStock`
是纯数据结构，能整个加载 —— 于是"对照列是 maroon、宽 90"是 PHP 亲口说的，
不是我抄的。数值那一格仍然抽函数体：`_getSmaCallbackPriceDisplay` 与
`EstFromPair` 都从源码现场抠。

**踩到一个坑**：`require_once 'table.php'` 会命中 `D:\xampp\php\pear\Table.php`
（PEAR 的 HTML_Table，include_path 里排在前面），它再去 require `HTML/Common.php`
直接 fatal。必须用**绝对路径**加载。

### 结果

```
列组装（7 列，文本 / 颜色 / 宽度 三项全比）
  col.0  '均线'                              w=90
  col.1  '<font color=magenta>EST</font>'    w=80
  col.2  '<font color=orange>溢价</font>'     w=70
  col.3  'T+1<font color=magenta>EST</font>' w=80
  col.4  '<font color=orange>溢价</font>'     w=70
  col.5  '<font color=maroon>SH</font>'      w=90   ← 本轮修的那条
  col.6  'T+1<font color=magenta>EST</font>' w=80

逐项对照（5 例）：不一致 0 项，未比对 0 项
PHP 分支语义：if ('0') -> false    if ('0.0') -> true
```

`--selftest` 注入的四处错误全部被抓到：EST 方向（改用 `est_to_pair`）、
对照列颜色（去掉 maroon）、列宽（80→70）、精度（少一位小数）。

> 自检第一版报"EST 方向**没被抓到**"：补丁打在 `pairestimate.est_from_pair` 上，
> 但 `sma_service` 是 `from pairestimate import est_from_pair` —— 名字早就绑过来了。
> 补丁必须打在 `sma_service` 的命名空间上。改对之后 4/4。

**又踩到一次同一个坑**：PHP 侧的汇率替身一开始没写 `GetVal`，
`GetDefaultCny()` 探的是 `method_exists($ref, 'GetVal')`，缺了就**静默**退化成 1.0
—— case 4 立刻给出 `254.5` 对 `129.5` 的差异。这正是上一轮
`_php_qdiicolumn_check.php` 里记过的那条，换了个脚本还是踩上了。

## 四、刻意不照抄的地方

| PHP | 本版 | 理由 |
|---|---|---|
| `$str .= _getSmaParagraphWarning($est_ref)` | 只告警被算均线那个 ref | 告警源（`_warning_source`）默认 `None`，本就返回空串；等真接了再说 |
| 每张表的 `<font>` 逐数字染色 | 只给纯文本 | 本项目列/格模型是"文本 + 一个颜色"（见上一轮 README） |
| 循环里 echo | 服务返回 list，模板循环 | 服务层不负责输出 |

## 五、⚠️ 本轮发现、**没有**动的一个缺口

真库页面实测：

```
SZ161125  QDII 均线表  ['均线','EST','溢价','T+1EST','溢价','',     'T+1EST']
                       ['5日','7799.4','0.16%','7792.89','0.24%','','']
生产端同一张            ['5日','7799.4','0.16%','7792.89','0.24%','3.179','3.177']
```

第 6 列（`_callbackQdiiSma` 的对照列）**表头与格子都是空的**。根因：

```php
// php/stock/fundref.php:19
if ($this->IsFundA()) { $this->stock_ref = new MyStockReference($strSymbol); }
```

Python 的 `build_qdii_reference()` 没做这一步，`QdiiReference.get_stock_ref()`
返回 `None` → `callback_qdii_sma()` 无参调用得到 `None` → 列头与格子双双为空。

**为什么本轮不改**：`build_qdii_reference` 是 `reference_service` 的共享入口，
喂给详情页几乎所有段落（估值 / 对冲 / 参考数据）。改它要配套一次**受影响面
受控实验**，不是"顺手补一行"。单独排一轮，带上 PHP 对照。

## 六、另一个已知偏差（不是 bug）

`SZ161125` 的 SPY 列显示 `895247.93`，生产端是 `777.24`。这是演示库中间层
校准因子的尺度问题 —— `pitfalls.md` 第 38 条与 `README_qdii_trading_column.md`
第六节都记着：`0.008712` 是**刻意保留**的归一化种子值，改成生产量级会让所有
基金页的估值炸掉。换算路径本身已被 PHP 逐例验过。
