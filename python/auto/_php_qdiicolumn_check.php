<?php
// ---------------------------------------------------------------------------
// 五档表「换算价」那一列的参考值（对应 _qdiigroup.php 的 _tradingUserDefined）
//
//   D:\xampp\php\php.exe -n -d display_errors=stderr _php_qdiicolumn_check.php
//
// ## 做法：抽函数体 + 替身对象
//
// 与 `_php_pairest_check.php` 同款手段。`_tradingUserDefined()` 本体
// **直接从 `woody/res/php/_qdiigroup.php` 里抠出来**，不手抄 —— 手抄的那一版
// 再像也是第二份实现，改动不会跟着走。
//
// 它依赖的四个外部入口同样放宽到纯数据：
//
// * `$acct->GetRef()` / `GetLeverageRef()`  —— 替身；
// * `$fund->GetEstValue()`                  —— 抠 `_QdiiReference::GetEstValue`；
// * `$ref->EstFromPair()`                   —— 抠 `MyPairReference::EstFromPair`；
// * `$ref->GetPriceDisplay()`               —— 抠 `StockReference::GetPriceDisplay`。
//
// ## 为什么这一列值得单独做一次对照
//
// 出错不会崩、也不报错：公式方向抄反、数错一个除号，页面上照旧是一串数字，
// 且数量级常常看着也"还行"。只有把 PHP 的真值摆在旁边才看得出。
//
// ## 另外顺手确认两件 PHP 语义
//
// `if ($strVal)` 对 `'0'` 与 `''` 的结果 —— 这是本模块的分支开关，
// 用 Python 的 `if price:` 翻译就会翻车。见输出里的 `traps` 段。
//
// **注意**：本文件里不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$rootDir = realpath(__DIR__ . '/../..');
$phpDir = realpath(__DIR__ . '/../../php');
$pageDir = $rootDir . DIRECTORY_SEPARATOR . 'woody' . DIRECTORY_SEPARATOR
         . 'res' . DIRECTORY_SEPARATOR . 'php';
if ($phpDir === false || !is_dir($pageDir)) {
    fwrite(STDERR, "找不到 php 或 woody/res/php 目录\n"); exit(2);
}

//: 自由函数：文件相对根 => 函数名列表
$freeTargets = array(
    'php/stock/fundref.php' => array('FundAdjustPosition', 'FundReverseAdjustPosition'),
    'php/stock/qdiiref.php' => array('QdiiGetVal', 'QdiiGetPeerVal'),
    'php/stock.php'        => array('StockGetPriceDisplay'),
    // GetPriceDisplay 最终会把数字包进 <font>：颜色样式函数在 ui 层，一并抽出来
    'php/ui/htmlelement.php' => array('GetFontElement', 'GetHtmlElement', 'GetDoubleQuotes'),
);

//: 类方法：文件相对根 => 类名 => 方法名列表
$methodTargets = array(
    'php/stock/fundpairref.php' => array('MyPairReference' => array('EstFromPair', 'GetDefaultCny')),
    'php/stock/fundref.php'     => array('FundReference'   => array('ReverseAdjustPosition')),
    'php/stock/qdiiref.php'     => array('_QdiiReference'  => array('GetEstValue')),
    'php/stock/stockref.php'    => array('StockReference'  => array('GetPriceDisplay')),
);

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

function ExtractFunctionBody($text, $name)
{
    // 按名字找到 `function xxx(` 之后，用括号配对切出完整函数体
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

function SlurpMethods($root, $targets)
{
    $out = '';
    foreach ($targets as $rel => $classes) {
        $text = file_get_contents($root . DIRECTORY_SEPARATOR . $rel);
        if ($text === false) { fwrite(STDERR, "读不到 $rel\n"); exit(2); }
        foreach ($classes as $className => $names) {
            foreach ($names as $name) {
                $body = ExtractFunctionBody($text, $name);
                if ($body === false) { fwrite(STDERR, "找不到方法 $name\n"); exit(2); }
                $out .= $body . "\n";
            }
        }
    }
    return $out;
}

$code = "<?php\n";
foreach ($freeTargets as $rel => $names) {
    $code .= SlurpFunctions($rootDir . DIRECTORY_SEPARATOR . $rel, $names);
}

// ---- 替身：显示用的那一侧（指数 / 杠杆 ETF） ----
$code .= "\nclass RefStub\n{\n";
$code .= "    var \$strPrice = '0';\n";
$code .= "    var \$_precision = 3;\n";
$code .= "    var \$_display = '';\n";
$code .= "    function GetDisplay() { return \$this->_display; }\n";
$code .= "    function GetPrecision() { return \$this->_precision; }\n";
$code .= SlurpMethods($rootDir, array('php/stock/stockref.php' => array('StockReference' => array('GetPriceDisplay'))));
$code .= "}\n\n";

// ---- 替身：杠杆标的 —— 多出配对估算那三个成员 ----
$code .= "class LevStub extends RefStub\n{\n";
$code .= "    var \$pair_ref = false;\n";
$code .= "    var \$cny_ref = false;\n";
$code .= "    var \$fFactor = 1.0;\n";
$code .= "    var \$fLastCalibrationVal = false;\n";
$code .= "    var \$_position = 1.0;\n";
$code .= "    var \$_symbolA = false;\n";
$code .= "    var \$_price = '0';\n";
$code .= "    function GetPosition() { return \$this->_position; }\n";
$code .= "    function GetPrice() { return \$this->_price; }\n";
$code .= "    function IsSymbolA() { return \$this->_symbolA; }\n";
$code .= SlurpMethods($rootDir, array('php/stock/fundpairref.php' => $methodTargets['php/stock/fundpairref.php']));
$code .= "}\n\n";

// ---- 替身：基金 —— GetEstValue 走真实 PHP 函数体 ----
// GetDefaultCny 用的是 method_exists($ref, 'GetVal')，不是 GetPrice ——
// 少了 GetVal 会**静默**退化成 1.0（实测踩到：汇率根本没参与换算）。
$code .= "class ForexStub { var \$_price='1.0';\n";
$code .= "    function GetPrice() { return \$this->_price; }\n";
$code .= "    function GetVal(\$strDate = false) { return \$this->_price; }\n}\n\n";
$code .= "class FundStub\n{\n";
$code .= "    var \$fFactor = 1.0;\n";
$code .= "    var \$_position = 1.0;\n";
$code .= "    var \$_calibrationBase = 0.0;\n";
$code .= "    var \$_cny = '1.0';\n";
$code .= "    var \$est_ref = false;\n";
$code .= "    function GetPosition() { return \$this->_position; }\n";
$code .= "    function GetStockId() { return '1'; }\n";
$code .= "    function GetEstRef() { return \$this->est_ref; }\n";
$code .= "    function GetForexRef() { \$r = new ForexStub(); \$r->_price = \$this->_cny; return \$r; }\n";
$code .= "    function _getCalibrationBaseVal() { return \$this->_calibrationBase; }\n";
$code .= SlurpMethods($rootDir, array('php/stock/fundref.php' => array('FundReference' => array('ReverseAdjustPosition'))));
$code .= SlurpMethods($rootDir, array('php/stock/qdiiref.php' => array('_QdiiReference' => array('GetEstValue'))));
$code .= "}\n\n";

// ---- 替身：全局账号对象，对应 `global $acct` ----
$code .= "class AccountStub { var \$fund = false; var \$lev = array();\n";
$code .= "    function GetRef() { return \$this->fund; }\n";
$code .= "    function GetLeverageRef() { return \$this->lev; }\n}\n\n";

// ---- 被测函数本体：直接从页面文件里抠出来 ----
$pageText = file_get_contents($pageDir . DIRECTORY_SEPARATOR . '_qdiigroup.php');
if ($pageText === false) { fwrite(STDERR, "读不到 _qdiigroup.php\n"); exit(2); }
$body = ExtractFunctionBody($pageText, '_tradingUserDefined');
if ($body === false) { fwrite(STDERR, "找不到 _tradingUserDefined\n"); exit(2); }
$code .= $body . "\n";

$tmp = tempnam(sys_get_temp_dir(), 'qdiicol_') . '.php';
if (file_put_contents($tmp, $code) === false) {
    fwrite(STDERR, "写临时文件失败\n"); exit(2);
}
require $tmp;

// ---------------------------------------------------------------------------
// 用例矩阵 —— 必须与 _php_qdiicolumn_compare.py 的 CASES **逐项一致**（顺序也不能变）
// ---------------------------------------------------------------------------
$cases = array(
    // 生产端 sz161125cn.php 那一组量级：基金价格 3.318，中间层 ^GSPC，杠杆 SPY
    array(
        'fund'    => array('factor' => 2459.0, 'position' => 0.95, 'cny' => '6.4328', 'base' => 3.30),
        'price'   => '3.318',
        'est'     => array('display' => '^GSPC', 'precision' => 3, 'price' => '8157.14'),
        'lev'     => array(
            array('display' => 'SPY', 'precision' => 2, 'price' => '812.89',
                  'factor' => 10.03, 'position' => 1.0, 'last' => false,
                  'symbol_a' => false, 'cny' => '1.0'),
        ),
    ),
    // 没有杠杆标的：那一列只有中间层一格
    array(
        'fund'    => array('factor' => 2459.0, 'position' => 0.95, 'cny' => '6.4328', 'base' => 3.30),
        'price'   => '5.372',
        'est'     => array('display' => '^NDX', 'precision' => 3, 'price' => '31137.575'),
        'lev'     => array(),
    ),
    // 两个杠杆标的、不同精度：同时确认顺序与各引用自己的格式化
    array(
        'fund'    => array('factor' => 0.008945, 'position' => 1.0, 'cny' => '1.0', 'base' => 111.0),
        'price'   => '1.234',
        'est'     => array('display' => '^NDX', 'precision' => 3, 'price' => '31137.575'),
        'lev'     => array(
            array('display' => 'QQQ', 'precision' => 2, 'price' => '620.00',
                  'factor' => 0.008945, 'position' => 1.0, 'last' => false,
                  'symbol_a' => false, 'cny' => '1.0'),
            array('display' => 'TQQQ', 'precision' => 4, 'price' => '92.50',
                  'factor' => 0.026835, 'position' => 1.0, 'last' => 90.0,
                  'symbol_a' => false, 'cny' => '1.0'),
        ),
    ),
    // A 股一侧走 QdiiGetVal 那条倒数方向的式子
    array(
        'fund'    => array('factor' => 0.83, 'position' => 0.95, 'cny' => '6.7035', 'base' => 4.5),
        'price'   => '4.545',
        'est'     => array('display' => '^NDX', 'precision' => 2, 'price' => '31137.57'),
        'lev'     => array(
            array('display' => 'SH513100', 'precision' => 3, 'price' => '2.5',
                  'factor' => 1.1, 'position' => 1.0, 'last' => false,
                  'symbol_a' => true, 'cny' => '1.0'),
        ),
    ),
    // 仓位不是 1：FundAdjustPosition 的混合项要参与
    array(
        'fund'    => array('factor' => 1000.0, 'position' => 0.95, 'cny' => '7.1', 'base' => 1.2),
        'price'   => '0.995',
        'est'     => array('display' => 'XOP', 'precision' => 2, 'price' => '130.68'),
        'lev'     => array(
            array('display' => 'FOO', 'precision' => 2, 'price' => '10.00',
                  'factor' => 2.0, 'position' => 0.5, 'last' => 9.0,
                  'symbol_a' => false, 'cny' => '2.0'),
        ),
    ),
);

$out = array();
foreach ($cases as $index => $case) {
    $estRef = new RefStub();
    $estRef->_display   = $case['est']['display'];
    $estRef->_precision = $case['est']['precision'];
    $estRef->strPrice   = $case['est']['price'];

    $levRefs = array();
    foreach ($case['lev'] as $lev) {
        $ref = new LevStub();
        $ref->_display   = $lev['display'];
        $ref->_precision  = $lev['precision'];
        $ref->strPrice   = $lev['price'];
        $ref->_price     = $lev['price'];
        $ref->fFactor    = $lev['factor'];
        $ref->_position  = $lev['position'];
        $ref->fLastCalibrationVal = $lev['last'];
        $ref->_symbolA   = $lev['symbol_a'];
        if ($lev['cny'] !== false) {
            $side = new ForexStub();
            $side->_price = $lev['cny'];
            $ref->cny_ref = $side;
        }
        $levRefs[] = $ref;
    }

    $fund = new FundStub();
    $fund->fFactor            = $case['fund']['factor'];
    $fund->_position          = $case['fund']['position'];
    $fund->_cny               = $case['fund']['cny'];
    $fund->_calibrationBase   = $case['fund']['base'];
    $fund->est_ref            = $estRef;

    $acct = new AccountStub();
    $acct->fund = $fund;
    $acct->lev  = $levRefs;
    $GLOBALS['acct'] = $acct;

    $out['cell.' . $index] = _tradingUserDefined($case['price']);
}

// ---------------------------------------------------------------------------
// PHP 语义确认：为什么 `'0'` 走的是"表头"分支
// ---------------------------------------------------------------------------
$out['traps.bool_string_zero'] = (bool)'0';
$out['traps.bool_empty']       = (bool)'';
$out['traps.bool_string_zero_dot'] = (bool)'0.0';
$out['traps.empty_string_zero'] = empty('0');

echo json_encode($out), "\n";
