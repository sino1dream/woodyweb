<?php
// ---------------------------------------------------------------------------
// FundPairReference::CalcFactor 分支的参考值
//
//   D:\xampp\php\php.exe -n -d display_errors=stderr _php_fundpairref_check.php
//
// ## 为什么单这一条值得单独对照
//
// `CalcFactor()`（fundpairref.php:213）**两个分支的汇率方向相反**：
//
//     if ($this->IsSymbolA())   $fNetValue /= $fCny;    // A 股
//     else                      $fNetValue *= $fCny;    // 其它
//
// 写成同一个方向时**两个方向都算得出来**，因子会差 cny 的平方倍，
// 而且过程中不报错 —— 单测如果只用"两边都乘"的期望值，会一路绿过去。
// CK2b 的 Python 单测因此加了往返断言（见 test_fundpairref.py），但单测的
// 期望值仍然来自本文件。
//
// ## 判定用真 StockSymbol，分支体从源码抠
//
// 同 `_php_pairref_check.php`：符号分类若用手工替身，对照的就只是
// "我以为 PHP 会怎么判"。 `php/stock/stocksymbol.php` 无 require/include，
// 可独立加载。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }

require $phpDir . '/stock/stocksymbol.php';

//: 抠 `CalcFactor` 方法体：从函数头到那条 return 的行尾
function ExtractCalcFactor($path)
{
    $text = file_get_contents($path);
    if ($text === false) { fwrite(STDERR, "读不到 $path\n"); exit(2); }
    $head = 'function CalcFactor($fPairNetValue, $fNetValue, $strDate)';
    $start = strpos($text, $head);
    if ($start === false) { fwrite(STDERR, "找不到 CalcFactor 起始\n"); exit(2); }
    $marker = 'return $fPairNetValue / $fNetValue;';
    $end = strpos($text, $marker, $start);
    if ($end === false) { fwrite(STDERR, "找不到 CalcFactor 结束\n"); exit(2); }
    // **吃到 return 之后的第二行**：源码里 ``return`` 不是块的最后一行，
    // 函数体的闭合 ``}`` 在它下面（fundpairref.php:221 是 return、:222 是 ``}``）。
    // 只吃到 return 的行尾会得到缺一个闭括号的方法体 —— eval 时报
    // "Unclosed '{'"，行号还指向临时文件，很难看出关系是这儿。
    $eol = strpos($text, "\n", $end);
    if ($eol === false) { $eol = strlen($text); }
    $close = strpos($text, "\n", $eol + 1);
    $eol = ($close === false) ? strlen($text) : $close;
    return substr($text, $start, $eol - $start);
}

$method = ExtractCalcFactor($phpDir . '/stock/fundpairref.php');

// cny_ref 替身：只提供 CalcFactor 用到的 GetVal()
eval('
class CnyStub {
    var $val;
    function __construct($v) { $this->val = $v; }
    function GetVal($strDate = false) { return $this->val; }
}
');

// 本体替身：IsSymbolA 委托真 StockSymbol，CalcFactor 体从源码贴进来
$code = "class CalcFactorStub {\n";
$code .= "    var \$cny_ref = false;\n";
$code .= "    var \$_sym;\n";
$code .= "    function __construct(\$s) { \$this->_sym = new StockSymbol(\$s); }\n";
$code .= "    function IsSymbolA() { return \$this->_sym->IsSymbolA(); }\n";
$code .= "    function GetSymbol() { return \$this->_sym->GetSymbol(); }\n";
$code .= $method . "\n";
$code .= "}\n";

// 落盘再 require：语法错误时行号好认
$tmp = tempnam(sys_get_temp_dir(), 'fundpairref_') . '.php';
if (file_put_contents($tmp, "<?php\n" . $code) === false) {
    fwrite(STDERR, "写临时文件失败\n"); exit(2);
}
require $tmp;

//: 用例矩阵：本体符号 × 有无汇率引用 × 一组净值
//   cny 为 null 表示 PHP 的 `$cny_ref = false`（不建汇率引用）
$cases = array(
    // -- A 股：我对 **净值** 除汇率，等价于因子 × 汇率
    array('symbol' => 'SZ161725', 'cny' => 7.1,    'pair' => 71.0,  'net' => 10.0),
    array('symbol' => 'SH513500', 'cny' => 0.9,    'pair' => 100.0, 'net' => 25.0),
    array('symbol' => 'SZ161725', 'cny' => 1.0,    'pair' => 80.0,  'net' => 10.0),
    // -- 非 A：净值与汇率相乘后再除，等价于因子 ÷ 汇率
    array('symbol' => '^NDX',     'cny' => 7.1,    'pair' => 71.0,  'net' => 10.0),
    array('symbol' => 'hf_NQ',    'cny' => 0.9,    'pair' => 100.0, 'net' => 25.0),
    array('symbol' => 'SPY',      'cny' => 1.0,    'pair' => 80.0,  'net' => 10.0),
    array('symbol' => '00700',    'cny' => 0.92,   'pair' => 500.0, 'net' => 400.0),
    // -- 没有汇率引用：纯比值，两个分支应当收敛到同一个数
    array('symbol' => 'SZ161725', 'cny' => null,   'pair' => 80.0,  'net' => 10.0),
    array('symbol' => '^NDX',     'cny' => null,   'pair' => 80.0,  'net' => 10.0),
    // -- 汇率小于 1 时两个分支的数值大小关系翻转
    array('symbol' => 'SZ161725', 'cny' => 0.5,    'pair' => 10.0,  'net' => 4.0),
    array('symbol' => '^NDX',     'cny' => 0.5,    'pair' => 10.0,  'net' => 4.0),
    // -- 深 B / 沪 B：PHP 把它们也算 A 类（IsShangHaiB 先过 IsShangHaiA）
    array('symbol' => 'SZ200011', 'cny' => 7.1,    'pair' => 35.5,  'net' => 10.0),
    array('symbol' => 'SH900901', 'cny' => 7.1,    'pair' => 35.5,  'net' => 10.0),
);

$results = array();
foreach ($cases as $i => $c) {
    $ref = new CalcFactorStub($c['symbol']);
    if ($c['cny'] !== null) { $ref->cny_ref = new CnyStub($c['cny']); }

    $factor = $ref->CalcFactor($c['pair'], $c['net'], '2026-01-05');
    // PHP 的 IsSymbolA 返回数字串（真值），这里统一压成布尔，方便与 Python 比
    $isA = ($ref->IsSymbolA() !== false);

    // INF / NAN 编不进 JSON，按字符串带出来
    if (is_nan($factor))      { $str = 'NAN'; }
    else if (is_infinite($factor)) { $str = ($factor > 0 ? 'INF' : '-INF'); }
    else                      { $str = strval($factor); }

    $results[] = array(
        'index' => $i,
        'symbol' => $c['symbol'],
        'is_a' => $isA,
        'cny' => $c['cny'],
        'pair' => $c['pair'],
        'net' => $c['net'],
        'factor' => $str,
    );
}

echo json_encode(array('cases' => $results));
exit(0);
