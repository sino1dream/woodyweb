<?php
// ---------------------------------------------------------------------------
// 对着**真实 PHP** 跑净值记录段落的选档与列组成（CK4-E 的逐例对照）
//
//   D:/xampp/php/php.exe -n -d display_errors=stderr _php_netvaluehistory_check.php
//
// ## 这一段在页面上是哪个数
//
// `fundestparagraph.php:171` 的 CK4 持仓分支：
//
// ```php
// EchoHoldingsEstParagraph($holdings_ref);   // 里面末尾调
//     EchoNetValueHistoryParagraph($ref, false, 0, 1);
// ```
//
// 生产端 `holdingscn.php?symbol=SH513050` 那张 `netvaluehistorytable`
// （日期/净值/涨幅 三列 + 一行 2026-10-13 / 1.0115 / -0.24%）就是它。
//
// ## 验什么
//
// 1. ``GetNetValueTableColumn()`` —— **摆几列、什么顺序**。这是本段落的骨架：
//    选错一支，页面上要么少一列、要么多一列，而且不报错。
// 2. ``_QdiiGetStockPosition()`` —— 仓位反算式（纯数值）。
// 3. ``_QdiiMixGetPosition()`` —— 持仓型按比例加权后再反算。
//
// ## 手法：函数体从 PHP 源码现场切出（不是手抄）
//
// 与 `_php_holdings_check.php` 同一套：按花括号配对把三个函数从
// `php/ui/netvaluehistoryparagraph.php` 里原样切出来，一个字不改。
// 手抄一遍公式就失去了对照的意义。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

function ExtractMethod($text, $name)
{
    // ``\r?`` 不能省：PHP 源码是 Windows 换行（实测踩到，少了一个都切不出来）
    $pattern = '/(?:^|\n)[ \t]*(?:public |protected |private |static )*function[ \t]+'
             . preg_quote($name, '/') . '[ \t]*\([^)]*\)[ \t]*\r?\n?[ \t]*\{/';
    if (!preg_match($pattern, $text, $m, PREG_OFFSET_CAPTURE)) { return null; }
    $start = $m[0][1];
    $i = $m[0][1] + strlen($m[0][0]) - 1;
    $depth = 0;
    $len = strlen($text);
    while ($i < $len)
    {
        if ($text[$i] === '{')      { $depth++; }
        elseif ($text[$i] === '}')  { $depth--; if ($depth === 0) { $i++; break; } }
        $i++;
    }
    return substr($text, $start, $i - $start);
}

$srcPath = $rootDir . '/php/ui/netvaluehistoryparagraph.php';
$src = file_get_contents($srcPath);
if ($src === false) { fwrite(STDERR, "读不到 $srcPath\n"); exit(2); }

$methods = array();
foreach (array('_QdiiGetStockPosition', '_QdiiMixGetPosition', 'GetNetValueTableColumn') as $name)
{
    $body = ExtractMethod($src, $name);
    if ($body === null) { fwrite(STDERR, "切不出 $name\n"); exit(2); }
    $methods[$name] = $body;
}

// -- 常量与自由函数（真身会连库或散在别的文件里） -------------------------
define('MIN_FLOAT_VAL', 0.0000005);      // php/debug.php:11
define('POS_NETVALUE_DIFF', 4.0);        // php/ui/stockdisp.php:32
define('STOCK_DISP_NETVALUE', '净值');   // php/ui/stockdisp.php:15
define('STOCK_DISP_CHANGE', '涨幅');     // php/ui/stockdisp.php:7
define('STOCK_DISP_POSITION', '仓位');   // php/ui/stockdisp.php:19

function StockGetPercentage($fDivisor, $fDividend)   // php/stock.php:121
{
    if (abs($fDivisor) > MIN_FLOAT_VAL)   return ($fDividend / $fDivisor - 1.0) * 100.0;
    return 0.0;
}

function GetFontElement($strContent, $strColor = 'red', $strStyle = false)
{
    return $strContent;    // 只比文本与宽度，不比 <font> 外壳
}

// -- 列替身：只记"文本 + 宽度"，够比对列组成 ------------------------------
class TableColumn
{
    public $strText; public $iWidth;
    public function __construct($strText = '', $iWidth = 80, $strColor = false, $strPrefix = false)
    {
        $this->iWidth = $iWidth;
        $this->strText = $strText;
        if ($strPrefix)   $this->strText = $strPrefix . $this->strText;
    }
    public function describe() { return $this->strText . '|' . $this->iWidth; }
}

class TableColumnDate extends TableColumn
{ public function __construct($strPrefix = false) { parent::__construct('日期', 100, false, $strPrefix); } }

class TableColumnChange extends TableColumn
{ public function __construct($strPrefix = false) { parent::__construct(STOCK_DISP_CHANGE, 70, false, $strPrefix); } }

class TableColumnNetValue extends TableColumn
{ public function __construct($strPrefix = false) { parent::__construct(STOCK_DISP_NETVALUE, 90, 'olive', $strPrefix); } }

class TableColumnPrice extends TableColumn
{ public function __construct($strPrefix = false, $iWidth = 80) { parent::__construct('价格', $iWidth, false, $strPrefix); } }

class TableColumnPosition extends TableColumn
{ public function __construct() { parent::__construct(STOCK_DISP_POSITION, 60); } }

class TableColumnStock extends TableColumn
{ public function __construct($sym, $iWidth = 80) { parent::__construct($sym->GetDisplay(), $iWidth, 'maroon'); } }

function RefGetTableColumnNetValue($ref)   // php/ui/stocktable.php:320
{
    $strStockDisplay = TableColumnGetStock($ref);
    if ($ref->CountNetValue() > 0)             return new TableColumnNetValue($strStockDisplay);
    if ($ref->IsSinaFutureExceptGoldCN())      return new TableColumnSettlePrice($strStockDisplay);
    return new TableColumnPrice($strStockDisplay, 90);
}

function TableColumnGetStock($sym) { return $sym->GetDisplay(); }

// 上面那个函数引用了不存在的类，补一个（真身在 stocktable.php 里，
// 本对照走不到那一支，但不能因此解析失败）
class TableColumnSettlePrice extends TableColumn
{ public function __construct($strPrefix = false) { parent::__construct('结算价', 70, false, $strPrefix); } }

// -- 引用替身 -------------------------------------------------------------
class EstRefStub
{
    public $netValueCount = 2;
    function GetDisplay()             { return '^NDX'; }
    function CountNetValue()          { return $this->netValueCount; }
    function IsSinaFutureExceptGoldCN() { return false; }
    function GetNetValue($strDate)    { return false; }
}

class CnyRefStub
{
    function GetDisplay() { return 'USCNY'; }
}

class HoldingsRefStub
{
    public $proportions;
    public $ratios;
    function __construct($proportions, $ratios) { $this->proportions = $proportions; $this->ratios = $ratios; }
    function GetProportionArray($strDate, $strPrevDate) { return $this->proportions; }
    function GetHoldingsRatioArray() { return $this->ratios; }
}

// -- 把切出来的三个**自由函数**原样 eval 到全局作用域 ----------------------
// （PHP 里这三个都不在类里；``_QdiiMixGetPosition`` 内部是**不限命名空间**调用
// ``_QdiiGetStockPosition()``，放全局才能照原样解析 —— 装进类里会变成
// ``Harness\_QdiiGetStockPosition`` 然后 undefined。）
foreach (array('_QdiiGetStockPosition', '_QdiiMixGetPosition', 'GetNetValueTableColumn') as $name)
{
    eval($methods[$name]);
}

// ---------------------------------------------------------------------------
// 输出
// ---------------------------------------------------------------------------
$out = array();

// 1. 列组成：PHP 的 `GetNetValueTableColumn($est_ref, $cny_ref)`
//    false/false → 三列；cny → 加汇率+涨幅(+est)+仓位；est 且无 cny → 仍只有三列
$out['columns']['neither']   = array_map(function ($c) { return $c->describe(); },
                                         GetNetValueTableColumn(false, false));
$out['columns']['both']      = array_map(function ($c) { return $c->describe(); },
                                         GetNetValueTableColumn(new EstRefStub(), new CnyRefStub()));
$out['columns']['cny_only']  = array_map(function ($c) { return $c->describe(); },
                                         GetNetValueTableColumn(false, new CnyRefStub()));
$out['columns']['est_only']  = array_map(function ($c) { return $c->describe(); },
                                         GetNetValueTableColumn(new EstRefStub(), false));

// 2. 仓位反算式：六组，含"波动太小不算"与"比例 0"
//    （PHP 这里 proportion 为 0 会 DivisionByZeroError fatal，单独一个用例验）
$cases = array(
    array('name' => 'normal',        'pro' => 1.0,   'prev' => 100.0, 'net' => 95.0,  'cnyPrev' => 7.0,  'cny' => 6.63,  'input' => 4.0),
    array('name' => 'exact_one',     'pro' => 1.0,   'prev' => 100.0, 'net' => 95.0,  'cnyPrev' => 7.0,  'cny' => 6.65,  'input' => 4.0),
    array('name' => 'small_move',    'pro' => 1.0,   'prev' => 100.0, 'net' => 98.0,  'cnyPrev' => 7.0,  'cny' => 6.63,  'input' => 4.0),
    array('name' => 'loose_input',   'pro' => 1.0,   'prev' => 100.0, 'net' => 98.0,  'cnyPrev' => 7.0,  'cny' => 6.63,  'input' => 1.0),
    array('name' => 'cny_flat',      'pro' => 1.0,   'prev' => 100.0, 'net' => 95.0,  'cnyPrev' => 7.0,  'cny' => 7.0,   'input' => 4.0),
    array('name' => 'negative',      'pro' => 1.0,   'prev' => 95.0,  'net' => 100.0, 'cnyPrev' => 7.0,  'cny' => 6.63,  'input' => 4.0),
    // 四舍五入边界：这两个值算出来正好是 0.125 与 0.625（二进制精确表示），
    // ``number_format`` 与 Python ``%`` 的舍入方式在此可能分叉（pitfalls #51
    // 就是数字显示上的分歧），必须逐例钉住。
    array('name' => 'half_even',     'pro' => 1.0,   'prev' => 100.0, 'net' => 95.0,  'cnyPrev' => 7.0,  'cny' => 4.20,  'input' => 4.0),
    array('name' => 'half_odd',      'pro' => 1.0,   'prev' => 100.0, 'net' => 95.0,  'cnyPrev' => 7.0,  'cny' => 6.44,  'input' => 4.0),
);
foreach ($cases as $c)
{
    $value = _QdiiGetStockPosition($c['pro'], $c['prev'], $c['net'], $c['cnyPrev'], $c['cny'], $c['input']);
    $out['stock_position'][$c['name']] = ($value === false) ? null : strval($value);
}

// 3. 持仓型加权：Σ[比例 × 持仓比例/100] 后再反算
$mixCases = array(
    array('name' => 'two_holdings', 'pro' => array(0.02, 0.05), 'ratios' => array('11' => '40.0', '12' => '60.0')),
    array('name' => 'one_holding',  'pro' => array(0.10),       'ratios' => array('11' => '100.0')),
    array('name' => 'no_pro',       'pro' => false,             'ratios' => array('11' => '100.0')),
    array('name' => 'empty_pro',    'pro' => array(),           'ratios' => array('11' => '100.0')),
);
foreach ($mixCases as $c)
{
    $ref = new HoldingsRefStub($c['pro'], $c['ratios']);
    $value = _QdiiMixGetPosition($ref, '2026-10-14', '2026-10-13', 100.0, 95.0, 7.0, 6.63, 4.0);
    $out['mix_position'][$c['name']] = ($value === false) ? null : strval($value);
}

echo json_encode($out);
