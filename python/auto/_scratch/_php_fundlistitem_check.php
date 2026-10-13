<?php
// ---------------------------------------------------------------------------
// 对着**真实 PHP** 跑 `_echoFundListItem()` —— 不带回调那一版（末列「参考值」）
//
//   D:\xampp\php\php.exe -n -d display_errors=stderr _php_fundlistitem_check.php
//
// ## 为什么这一条要单独对照
//
// `EchoFundListParagraph()` 有两种形态，此前只对照过带回调（对冲值）那版
// （`_php_fundlisthedge_check.php`）。不带回调这版走的是 **else 分支**
// （`fundlistparagraph.php:33`）：
//
//     if ($strVal = $last_sql->ReadVal($strStockId, true))
//         $ar[] = $ref->GetPriceDisplay(floatval($strVal));
//
// 两个容易抄错的地方正是本文件要钉的：
//
// 1. **染色基准是 ref 自己的现价**，不是 0 —— `GetPriceDisplay()` 只传一个参数时
//    走 `stockref.php:162` 的 `$fPrev = floatval($this->strPrice)`。而同一行的
//    「仓位」/「校准值」两列走 `GetNumberDisplay()`，基准恒为 0。**两列不是一套
//    规则**，写成一样的话只有"参考值明显高于/低于现价"时才会露馅。
// 2. **`"0"` 是假值**：lastcalibration 里存 0 时 PHP 那一行**只有五格**
//    （`$ar[]` 没执行）。Python 侧补了空串，属于刻意偏差，比较脚本会单列。
//
// ## 手法：函数体从源码现场切出来
//
// 同 `_php_fundlisthedge_check.php`：`GetNumberDisplay` / `GetPriceDisplay` /
// `_echoFundListItem` 全部从 PHP 源码里切出后再 eval，改了 PHP 这边立刻跟着变。
// 命名空间 `Harness` 用来装替身 —— 未限定的函数名先查当前命名空间。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

function ExtractFunctionBody($text, $name)
{
    $pattern = '/(?:^|\n)[ \t]*(?:public |protected |private |static )*function[ \t]+'
             . preg_quote($name, '/') . '[ \t]*\(/';
    if (preg_match($pattern, $text, $m, PREG_OFFSET_CAPTURE) !== 1) { return false; }
    $start = $m[0][1];
    $len = strlen($text);
    $depth = 0;
    $in = false;
    for ($i = $start; $i < $len; $i++) {
        $ch = $text[$i];
        if ($ch === '{') { $depth++; $in = true; }
        else if ($ch === '}') {
            $depth--;
            if ($in && $depth === 0) { return substr($text, $start, $i - $start + 1); }
        }
    }
    return false;
}

function SlurpFunctions($path, $names)
{
    $text = file_get_contents($path);
    if ($text === false) { fwrite(STDERR, "读不到 $path\n"); exit(2); }
    $out = '';
    foreach ($names as $name) {
        $body = ExtractFunctionBody($text, $name);
        if ($body === false) { fwrite(STDERR, "找不到函数 $name\n"); exit(2); }
        $out .= $body . "\n";
    }
    return $out;
}

$stockCode = SlurpFunctions($rootDir . '/php/stock.php', array(
    'StockGetPriceDisplay', 'GetNumberDisplay'));
// 真实的 GetPriceDisplay 方法体（stockref.php:155）：$fDisp 为假值时回落到
// ref 自己的现价与昨收，这条回落正是"单参数调用"的语义来源。
$refCode   = SlurpFunctions($rootDir . '/php/stock/stockref.php', array('GetPriceDisplay'));
$paraCode  = SlurpFunctions($rootDir . '/php/ui/fundlistparagraph.php', array(
    '_getFundPairLink', '_echoFundListItem'));

$stubs = <<<'STUBS'
define('CALIBRATION_PRECISION', 3);

// PHP 那边颜色是烧进 HTML 的（<font color>），这里退化成 "颜色:文本" 便于比较。
// black 不包标签 -> 纯文本，与 Python 侧的约定一致。
function GetFontElement($str, $strColor) { return $strColor . ':' . $str; }

// 链接两格在本对照里退化成**纯文本**：URL 由调用方决定（Python 侧是注入的
// link_builder），不是这一段要对照的东西。
function SymCalibrationHistoryLink($sym) { return $sym->GetDisplay(); }

function RefEchoTableColumn($ref, $ar, $bWide = false) { $GLOBALS['rows'][] = $ar; }

function GetCalibrationSql() { return $GLOBALS['CalSql']; }

class CalStub
{
    public $dates = array();
    public function GetDateNow($strKeyId = false)
    {
        return isset($this->dates[$strKeyId]) ? $this->dates[$strKeyId] : false;
    }
}

// 对应 php/sql/sqlval.php:98 —— 表 lastcalibration，ReadVal($strId, true) 返回字符串
class LastCalibrationSql
{
    public $vals = array();
    public function ReadVal($strId, $bString = false)
    {
        return array_key_exists($strId, $this->vals) ? $this->vals[$strId] : false;
    }
}

class PairRefStub
{
    private $symbol;
    private $display;
    public function __construct($strSymbol, $strDisplay = false)
    {
        $this->symbol = $strSymbol;
        $this->display = ($strDisplay === false) ? $strSymbol : $strDisplay;
    }
    public function GetSymbol()  { return $this->symbol; }
    public function GetDisplay() { return $this->display; }
    public function GetMyStockLink() { return $this->display; }
}

class RefStub
{
    public $strPrice = '0';
    public $strPrevPrice = '0';
    public $iPrecision = 3;
    public $stockId;
    public $symbol;
    public $display;
    public $position = 1.0;
    public $factor = 1.0;
    public $pairRef;

    public function GetStockId()  { return $this->stockId; }
    public function GetSymbol()   { return $this->symbol; }
    public function GetDisplay()  { return $this->display; }
    public function GetPosition() { return $this->position; }
    public function GetFactor()   { return $this->factor; }
    public function GetPairRef()  { return $this->pairRef; }
    public function GetPrecision() { return $this->iPrecision; }
STUBS;

//: 两个 `}`：先闭合 RefStub 类，再闭合 namespace Harness
$code = "namespace Harness {\n" . $stockCode . "\n" . $paraCode . "\n" . $stubs
      . "\n" . $refCode . "\n}\n}\n";
eval($code);

//: 用例。每个 case 给出校准日期、lastcalibration 的值、以及一到两行 ref。
//: 数值量级照生产端：ETF 校准值 10 上下、参考值 1.5 上下。
$cases = array(
    array(
        'name' => '正常：参考值高于现价',
        'dates' => array('4' => '2026-10-09'),
        'last' => array('4' => '1.5234'),
        'refs' => array(
            array('symbol' => 'QQQ', 'display' => 'QQQ', 'stock_id' => '4',
                  'position' => 1.0, 'factor' => 10.035, 'price' => '1.5',
                  'precision' => 3, 'pair' => '^NDX'),
        ),
    ),
    array(
        'name' => '参考值低于现价 -> green',
        'dates' => array('4' => '2026-10-09'),
        'last' => array('4' => '1.5234'),
        'refs' => array(
            array('symbol' => 'QQQ', 'display' => 'QQQ', 'stock_id' => '4',
                  'position' => 1.0, 'factor' => 10.035, 'price' => '1.6',
                  'precision' => 3, 'pair' => '^NDX'),
        ),
    ),
    array(
        'name' => '参考值与现价之差在容差内 -> grey',
        'dates' => array('4' => '2026-10-09'),
        'last' => array('4' => '1.50001'),
        'refs' => array(
            array('symbol' => 'QQQ', 'display' => 'QQQ', 'stock_id' => '4',
                  'position' => 1.0, 'factor' => 10.035, 'price' => '1.5',
                  'precision' => 3, 'pair' => '^NDX'),
        ),
    ),
    array(
        'name' => '负仓位：仓位格 red，校准值格照常',
        'dates' => array('4' => '2026-10-09'),
        'last' => array('4' => '1.5234'),
        'refs' => array(
            array('symbol' => 'SH', 'display' => 'SH', 'stock_id' => '4',
                  'position' => -1.0, 'factor' => 245.591, 'price' => '1.5',
                  'precision' => 3, 'pair' => '^GSPC'),
        ),
    ),
    array(
        'name' => '校准值极小 -> grey（容差 0.5/10^3）',
        'dates' => array('4' => '2026-10-09'),
        'last' => array('4' => '1.5234'),
        'refs' => array(
            array('symbol' => 'QQQ', 'display' => 'QQQ', 'stock_id' => '4',
                  'position' => 0.0004, 'factor' => 0.0004, 'price' => '1.5',
                  'precision' => 3, 'pair' => '^NDX'),
        ),
    ),
    array(
        'name' => '没有校准日期：日期格是 false',
        'dates' => array(),
        'last' => array('4' => '1.5234'),
        'refs' => array(
            array('symbol' => 'QQQ', 'display' => 'QQQ', 'stock_id' => '4',
                  'position' => 1.0, 'factor' => 10.035, 'price' => '1.5',
                  'precision' => 3, 'pair' => '^NDX'),
        ),
    ),
    array(
        'name' => 'lastcalibration 里是 0 -> PHP 那一行只有五格',
        'dates' => array('4' => '2026-10-09'),
        'last' => array('4' => '0'),
        'refs' => array(
            array('symbol' => 'QQQ', 'display' => 'QQQ', 'stock_id' => '4',
                  'position' => 1.0, 'factor' => 10.035, 'price' => '1.5',
                  'precision' => 3, 'pair' => '^NDX'),
        ),
    ),
    array(
        'name' => 'lastcalibration 里没有这一行 -> 同样只有五格',
        'dates' => array('4' => '2026-10-09'),
        'last' => array(),
        'refs' => array(
            array('symbol' => 'QQQ', 'display' => 'QQQ', 'stock_id' => '4',
                  'position' => 1.0, 'factor' => 10.035, 'price' => '1.5',
                  'precision' => 3, 'pair' => '^NDX'),
        ),
    ),
    array(
        'name' => '精度 0：参考值取整（precision<=0 时不 rtrim0）',
        'dates' => array('4' => '2026-10-09'),
        'last' => array('4' => '1.5234'),
        'refs' => array(
            array('symbol' => 'QQQ', 'display' => 'QQQ', 'stock_id' => '4',
                  'position' => 1.0, 'factor' => 10.035, 'price' => '1.5',
                  'precision' => 0, 'pair' => '^NDX'),
        ),
    ),
    array(
        'name' => '两行共用一个跟踪标的 -> 第二行退化成纯文本',
        'dates' => array('4' => '2026-10-09', '17' => '2026-10-09'),
        'last' => array('4' => '1.5234', '17' => '1.5234'),
        'refs' => array(
            array('symbol' => 'QQQ', 'display' => 'QQQ', 'stock_id' => '4',
                  'position' => 1.0, 'factor' => 10.035, 'price' => '1.5',
                  'precision' => 3, 'pair' => '^NDX'),
            array('symbol' => 'TQQQ', 'display' => 'TQQQ', 'stock_id' => '17',
                  'position' => 3.0, 'factor' => 30.0, 'price' => '1.5',
                  'precision' => 3, 'pair' => '^NDX'),
        ),
    ),
);

$out = array('cases' => array(), 'traps' => array());

foreach ($cases as $index => $case) {
    $calSql = new Harness\CalStub();
    $calSql->dates = $case['dates'];
    $GLOBALS['CalSql'] = $calSql;

    $lastSql = new Harness\LastCalibrationSql();
    $lastSql->vals = $case['last'];

    $GLOBALS['rows'] = array();
    foreach ($case['refs'] as $spec) {
        $ref = new Harness\RefStub();
        $ref->stockId = $spec['stock_id'];
        $ref->symbol = $spec['symbol'];
        $ref->display = $spec['display'];
        $ref->position = $spec['position'];
        $ref->factor = $spec['factor'];
        $ref->strPrice = $spec['price'];
        $ref->strPrevPrice = $spec['price'];
        $ref->iPrecision = $spec['precision'];
        $ref->pairRef = new Harness\PairRefStub($spec['pair']);
        try {
            Harness\_echoFundListItem($ref, $calSql, $lastSql, false);
        } catch (Throwable $e) {
            $GLOBALS['rows'][] = 'ERROR:' . $e->getMessage();
        }
        fwrite(STDERR, "diag case.$index rows=" . count($GLOBALS['rows']) . " exists="
            . (function_exists('Harness\_echoFundListItem') ? 'y' : 'n') . "\n");
    }

    // false 统一成空串：Python 侧没有 false 这个形态。
    // **变量名不能叫 $rows** —— 脚本顶层作用域里 `$rows` 就是 `$GLOBALS['rows']`，
    // 赋空数组等于把刚收集到的行清掉（实测踩过：JSON 里 rows 全是空）。
    $cleanRows = array();
    foreach ($GLOBALS['rows'] as $row) {
        $cells = array();
        foreach ($row as $cell) { $cells[] = ($cell === false) ? '' : $cell; }
        $cleanRows[] = $cells;
    }
    $out['cases']['case.' . $index] = array(
        'name' => $case['name'],
        'rows' => $cleanRows,
    );
}

//: PHP 语义陷阱，单独输出供 Python 侧自检
$out['traps']['string_zero_is_falsy'] = ('0' ? true : false);
$out['traps']['string_zero_dot_is_truthy'] = ('0.0' ? true : false);
$out['traps']['precision_zero_keeps_digits'] = number_format(1.5234, 0, '.', '');

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), "\n";
