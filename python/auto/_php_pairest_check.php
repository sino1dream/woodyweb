<?php
// ---------------------------------------------------------------------------
// MyPairReference 的 EstFromPair / EstToPair 参考值
//
//   D:\xampp\php\php.exe -n -d display_errors=stderr _php_pairest_check.php
//
// ## 做法：只抽方法体，再拼进一个替身类
//
// 与 `_php_sma_check.php` 同款手段：不 `require` 整个 `fundpairref.php`
// —— 那会连带拖进数据库会话和一堆渲染函数。改成按名字抠出
// `EstFromPair` / `EstToPair` / `GetDefaultCny` 的函数体，粘到一个
// 只带必要成员的替身类里。
//
// 替身类要提供公式读得到的全部成员：
// pair_ref / cny_ref / fFactor / fLastCalibrationVal / GetPosition() /
// IsSymbolA() / GetPrice()。它们都是**纯数据**，不涉及 IO，
// 所以对照出来的是真实的 PHP 公式结果。
//
// ## 为什么连 FundAdjustPosition 一起抽
//
// 它们才是真正会算错的那一层 —— `QdiiGetVal` 与 `(价/汇)/校准`
// 差一个倒数方向，而那个差异常常被"结果看着还算合理"掩盖。
//
// **注意**：本文件里不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$phpDir = realpath(__DIR__ . '/../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }

//: 自由函数：文件相对路径 => 函数名列表
$freeTargets = array(
    'stock/fundref.php' => array('FundAdjustPosition', 'FundReverseAdjustPosition'),
    'stock/qdiiref.php' => array('QdiiGetVal', 'QdiiGetPeerVal'),
);

//: 类方法：文件相对路径 => 类名 => 方法名列表
$methodTargets = array(
    'stock/fundpairref.php' => array(
        'MyPairReference' => array('EstFromPair', 'EstToPair', 'GetDefaultCny'),
    ),
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

$code = "<?php\n";
foreach ($freeTargets as $rel => $names) {
    $code .= SlurpFunctions($phpDir . DIRECTORY_SEPARATOR . $rel, $names);
}

// 替身类：把抽出来的三个方法贴进去，其余成员手工准备
$code .= "\nclass PairEstStub\n{\n";
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
foreach ($methodTargets as $rel => $classes) {
    foreach ($classes as $className => $names) {
        foreach ($names as $name) {
            $body = ExtractFunctionBody(
                file_get_contents($phpDir . DIRECTORY_SEPARATOR . $rel), $name);
            if ($body === false) { fwrite(STDERR, "找不到方法 $name\n"); exit(2); }
            $code .= $body . "\n";
        }
    }
}
$code .= "}\n\n";

// 被询价那一侧的替身：只要 GetPrice()/GetVal()
$code .= "class PairSideStub { var \$_price='0'; var \$_val='0';\n";
$code .= "    function GetPrice() { return \$this->_price; }\n";
$code .= "    function GetVal(\$strDate = false) { return \$this->_val; }\n";
$code .= "}\n\n";

// 拼出来的代码写成临时文件再 require —— 直接 eval 也行，但落盘方便排查
// eval($code) 里出现语法错误时行号不好认。
$tmp = tempnam(sys_get_temp_dir(), 'pairest_') . '.php';
if (file_put_contents($tmp, $code) === false) {
    fwrite(STDERR, "写临时文件失败\n"); exit(2);
}
require $tmp;
// Python 侧用同一份矩阵（见 _php_pairest_compare.py）
$cases = array(
    // 非 A 股：(价/汇)/校准 后按仓位混合
    array('symA' => false, 'pair' => 100.0, 'cny' => 2.0, 'factor' => 4.0, 'pos' => 1.0, 'last' => false, 'myPrice' => 12.5),
    array('symA' => false, 'pair' => 100.0, 'cny' => 2.0, 'factor' => 4.0, 'pos' => 0.5, 'last' => 10.0, 'myPrice' => 12.5),
    array('symA' => false, 'pair' => 100.0, 'cny' => 1.0, 'factor' => 1.2, 'pos' => 0.95, 'last' => false, 'myPrice' => 83.3333333333),
    array('symA' => false, 'pair' => 31137.575, 'cny' => 6.7035, 'factor' => 0.829884, 'pos' => 0.95, 'last' => 0.83, 'myPrice' => 4.5),
    // A 股：QdiiGetVal = 价*汇/校准
    array('symA' => true, 'pair' => 100.0, 'cny' => 2.0, 'factor' => 4.0, 'pos' => 1.0, 'last' => false, 'myPrice' => 50.0),
    array('symA' => true, 'pair' => 100.0, 'cny' => 2.0, 'factor' => 4.0, 'pos' => 0.5, 'last' => 10.0, 'myPrice' => 50.0),
    array('symA' => true, 'pair' => 3.3, 'cny' => 0.854, 'factor' => 1.1, 'pos' => 1.0, 'last' => false, 'myPrice' => 2.5),
);

//: 是否给 `GetVal()` 的替身挂 cny（false 表示没有 cny_ref，PHP 会走 1.0 分支）
$results = array();
foreach ($cases as $i => $c) {
    $ref = new PairEstStub();
    $ref->_symbolA = $c['symA'];
    $ref->fFactor = $c['factor'];
    $ref->_position = $c['pos'];
    $ref->fLastCalibrationVal = $c['last'];
    $ref->_price = strval($c['myPrice']);

    $side = new PairSideStub();
    $side->_price = strval($c['pair']);
    $side->_val = strval($c['cny']);
    $ref->pair_ref = $side;
    $ref->cny_ref = $side;

    $from = $ref->EstFromPair(floatval($c['pair']), floatval($c['cny']));
    $to = $ref->EstToPair(floatval($c['myPrice']), floatval($c['cny']));
    $results[] = array(
        'index' => $i,
        'from' => strval($from),
        'to' => strval($to),
    );
}

// 兜一组"没传参数"的：验证 `$x == false` 的宽松比较确实会回落到 pair_ref 的价
$fallback = array();
$ref = new PairEstStub();
$ref->fFactor = 4.0;
$ref->_position = 1.0;
$side = new PairSideStub();
$side->_price = '100.00';
$side->_val = '2.0';
$ref->pair_ref = $side;
$ref->cny_ref = $side;
foreach (array(0, 0.0, '0', '', null, false) as $j => $sentinel) {
    $fallback[] = array(
        'sentinel' => is_null($sentinel) ? 'null' : (is_bool($sentinel) ? 'false' : strval($sentinel)),
        'from' => strval($ref->EstFromPair($sentinel, false)),
    );
}

echo json_encode(array('cases' => $results, 'fallback' => $fallback));
exit(0);
