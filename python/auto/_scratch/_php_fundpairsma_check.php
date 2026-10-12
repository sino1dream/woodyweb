<?php
// ---------------------------------------------------------------------------
// `EchoFundPairSmaParagraphs()` 的参考值（php/ui/smaparagraph.php:156）
//
//   php -d include_path=".;../../php/ui;../../php" _php_fundpairsma_check.php
//
// ## 这一轮对照的是什么
//
// 上一轮（`_php_qdiicolumn_check.php`）已经把 `EstFromPair` 的数值路径验过了。
// 这一轮的新东西是**列怎么组装**——`EchoSmaParagraph()` 里那段 `$ar`：
//
//   $ar = array(new TableColumn('均线', 90), new TableColumnEst(), $premium_col,
//               $next_col, $premium_col);
//   if ($callback) { $ar[] = new TableColumnStock($est_ref, 90);
//                    $ar[] = $next_col; }
//
// 所以本脚本**直接用 PHP 自己的三个列类**（table.php / stocktable.php），
// 不是抽函数体重建——列类是纯数据结构，能整个 require 进来。
// 这样"对照列是 maroon、宽 90"这种事就是 PHP 亲口说的。
//
// ## 数值那一格仍然抽函数体
//
// 单元格走 `_getSmaCallbackPriceDisplay($callback, $ref, $strVal)`，
// 从 smaparagraph.php 现场抠出来；它内部的 `EstFromPair` 也照旧抠
// `MyPairReference` 的真身。手抄的那一版再像也是第二份实现。
//
// ## 一个 PHP 语义陷阱
//
// `_getSmaCallbackPriceDisplay()` 的 `if ($strVal)` 用的是 PHP 假值：
// `'0'` 为假（返回空串），`'0.0'` 为真。见输出里的 `traps` 段。
//
// **注意**：本文件里不能出现 PHP 结束标签 —— 解析器会在那里结束脚本，
// 之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$rootDir = realpath(__DIR__ . '/../../..');
$phpDir  = $rootDir . DIRECTORY_SEPARATOR . 'php';
$uiDir   = $phpDir . DIRECTORY_SEPARATOR . 'ui';
if ($phpDir === false || !is_dir($uiDir)) {
    fwrite(STDERR, "找不到 php/ui 目录\n"); exit(2);
}

// 真实列类要的三个文件。**必须用绝对路径**：include_path 里 PEAR 排在前面，
// `require_once 'table.php'` 会命中 `D:\xampp\php\pear\Table.php`（PEAR 的
// HTML_Table），它再去 require 'HTML/Common.php' 直接 fatal。踩过一次。
// 同时把 ui/php 两个目录塞进 include_path —— 这三个文件彼此是相对 require。
set_include_path($uiDir . PATH_SEPARATOR . $phpDir . PATH_SEPARATOR . get_include_path());
require_once $uiDir . DIRECTORY_SEPARATOR . 'htmlelement.php';  // GetFontElement
require_once $uiDir . DIRECTORY_SEPARATOR . 'table.php';
require_once $uiDir . DIRECTORY_SEPARATOR . 'stocktable.php';

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

function SlurpMethods($root, $targets)
{
    $out = '';
    foreach ($targets as $rel => $classes) {
        $text = file_get_contents($root . DIRECTORY_SEPARATOR . $rel);
        if ($text === false) { fwrite(STDERR, "读不到 $rel\n"); exit(2); }
        foreach ($classes as $names) {
            foreach ($names as $name) {
                $body = ExtractFunctionBody($text, $name);
                if ($body === false) { fwrite(STDERR, "找不到方法 $name\n"); exit(2); }
                $out .= $body . "\n";
            }
        }
    }
    return $out;
}

$freeTargets = array(
    'php/stock/fundref.php'    => array('FundAdjustPosition', 'FundReverseAdjustPosition'),
    'php/stock/qdiiref.php'    => array('QdiiGetVal', 'QdiiGetPeerVal'),
    'php/stock.php'            => array('StockGetPriceDisplay'),
    'php/ui/smaparagraph.php'  => array('_callbackFundPairSma', '_getSmaCallbackPriceDisplay'),
);

$code = "<?php\n";
foreach ($freeTargets as $rel => $names) {
    $code .= SlurpFunctions($rootDir . DIRECTORY_SEPARATOR . $rel, $names);
}

// 杠杆标的替身：`EstFromPair` / `GetDefaultCny` / `GetPriceDisplay` 都是真身
$code .= "\nclass LevStub\n{\n";
$code .= "    var \$_display = '';\n";
$code .= "    var \$_precision = 2;\n";
$code .= "    var \$_price = '0';\n";
$code .= "    var \$pair_ref = false;\n";
$code .= "    var \$cny_ref = false;\n";
$code .= "    var \$fFactor = 1.0;\n";
$code .= "    var \$fLastCalibrationVal = false;\n";
$code .= "    var \$_position = 1.0;\n";
$code .= "    var \$_symbolA = false;\n";
$code .= "    function GetDisplay() { return \$this->_display; }\n";
$code .= "    function GetPrecision() { return \$this->_precision; }\n";
$code .= "    function GetPrice() { return \$this->_price; }\n";
$code .= "    function GetPosition() { return \$this->_position; }\n";
$code .= "    function IsSymbolA() { return \$this->_symbolA; }\n";
$code .= SlurpMethods($rootDir, array('php/stock/fundpairref.php' => array(array('EstFromPair', 'GetDefaultCny'))));
$code .= SlurpMethods($rootDir, array('php/stock/stockref.php'    => array(array('GetPriceDisplay'))));
$code .= "}\n";

// 汇率引用替身。**GetVal 不能少**：GetDefaultCny 探的是
// `method_exists($this->cny_ref, 'GetVal')`，不是 GetPrice。少了它 PHP 会
// **静默**退化成 1.0 —— 汇率根本不进公式，而页面上照样是一串合理的数。
// （上一轮在 _php_qdiicolumn_check.php 里踩过一次，这里照抄同一个教训。）
$code .= "\nclass CnyStub\n{\n";
$code .= "    var \$_price = '1.0';\n";
$code .= "    function GetVal(\$strDate = false) { return \$this->_price; }\n";
$code .= "}\n";

$tmp = tempnam(sys_get_temp_dir(), 'fpsma_') . '.php';
if (file_put_contents($tmp, $code) === false) {
    fwrite(STDERR, "写临时文件失败\n"); exit(2);
}
require $tmp;

// ---------------------------------------------------------------------------
// 一、列组装 —— 用 PHP 自己的类照抄 EchoSmaParagraph() 里那段 $ar
// ---------------------------------------------------------------------------
$first = new LevStub();
$first->_display = 'SH';

$premium_col = new TableColumnPremium();
$next_col    = new TableColumnEst('T+1');
$ar = array(new TableColumn('均线', 90), new TableColumnEst(), $premium_col, $next_col, $premium_col);
$est_ref = call_user_func('_callbackFundPairSma', $first);   // 不给价格 → ref 自己
$ar[] = new TableColumnStock($est_ref, 90);
$ar[] = $next_col;

$out = array('columns' => array(), 'cases' => array(), 'traps' => array());
foreach ($ar as $index => $col) {
    $out['columns']['col.' . $index] = array($col->GetText(), $col->GetWidth());
}

// ---------------------------------------------------------------------------
// 二、数值 —— 必须与 _php_fundpairsma_compare.py 的 CASES 逐项一致（顺序也不能变）
// ---------------------------------------------------------------------------
function MakeLev($spec)
{
    $lev = new LevStub();
    $lev->_display   = $spec['display'];
    $lev->_precision = $spec['precision'];
    $lev->fFactor    = $spec['factor'];
    $lev->_position  = $spec['position'];
    $lev->fLastCalibrationVal = $spec['last'];
    $lev->_symbolA   = $spec['symbol_a'];
    $lev->_price     = $spec['price'];
    if ($spec['cny'] !== '1.0') {
        $cny = new CnyStub();
        $cny->_price = $spec['cny'];
        $lev->cny_ref = $cny;
    }
    return $lev;
}

$cases = array(
    // 生产端 sz161125cn.php 的量级：^GSPC 5 日均线 7799.4，对照 SPY
    array('display' => 'SPY', 'precision' => 2, 'price' => '777.24',
          'factor' => 10.035, 'position' => 1.0, 'last' => false,
          'symbol_a' => false, 'cny' => '1.0', 'val' => '7799.4'),
    // 同一个均线值、换成做空那一侧：仓位 -1，因子 245.591
    array('display' => 'SH', 'precision' => 2, 'price' => '31.86',
          'factor' => 245.591, 'position' => -1.0, 'last' => false,
          'symbol_a' => false, 'cny' => '1.0', 'val' => '7799.4'),
    // 有上次校准值：FundAdjustPosition 的混合项要参与
    array('display' => 'TQQQ', 'precision' => 4, 'price' => '92.50',
          'factor' => 0.026835, 'position' => 1.0, 'last' => 90.0,
          'symbol_a' => false, 'cny' => '1.0', 'val' => '31137.575'),
    // A 股一侧：IsSymbolA() 为真时走 QdiiGetVal 那条倒数方向的式子
    array('display' => 'SH513100', 'precision' => 3, 'price' => '2.5',
          'factor' => 1.1, 'position' => 1.0, 'last' => false,
          'symbol_a' => true, 'cny' => '1.0', 'val' => '31137.57'),
    // 汇率不是 1：确认 GetDefaultCny 真的进了公式（少了 GetVal 会静默退化）
    array('display' => 'FOO', 'precision' => 2, 'price' => '10.00',
          'factor' => 2.0, 'position' => 0.5, 'last' => 9.0,
          'symbol_a' => false, 'cny' => '2.0', 'val' => '1000.00'),
);

foreach ($cases as $index => $spec) {
    $lev = MakeLev($spec);
    $key = 'cell.' . $index;
    $out['cases'][$key] = array(
        'name'   => $spec['display'],
        'header' => (new TableColumnStock($lev, 90))->GetText(),
        'cell'   => _getSmaCallbackPriceDisplay('_callbackFundPairSma', $lev, $spec['val']),
        'no_arg' => (call_user_func('_callbackFundPairSma', $lev) === $lev) ? 'self' : 'other',
    );
}

// ---- 陷阱：`if ($strVal)` 的 PHP 假值语义 ----
$probe = MakeLev($cases[0]);
$out['traps']['bool_string_zero']     = (bool)('0' ? true : false);
$out['traps']['bool_string_zero_dot'] = (bool)('0.0' ? true : false);
$out['traps']['cell_string_zero']     = _getSmaCallbackPriceDisplay('_callbackFundPairSma', $probe, '0');
$out['traps']['cell_empty']           = _getSmaCallbackPriceDisplay('_callbackFundPairSma', $probe, '');

echo json_encode($out, JSON_UNESCAPED_UNICODE);
