<?php
// ---------------------------------------------------------------------------
// MyPairReference 构造函数里 cny_ref 推导分支的参考值
//
//   D:\xampp\php\php.exe -n -d display_errors=stderr _php_pairref_check.php
//
// ## 与 `_php_pairest_check.php` 的差别：判定用**真** StockSymbol
//
// `_php_pairest_check.php` 只验公式，所以符号分类（IsSymbolA 等）可以用
// 手工填的替身成员顶上。本文件验的是**分支表**，而分支表的输入全都是符号
// 分类 —— 如果分类也手工填，对照的就只是"我以为 PHP 会怎么判"，失去意义。
// 所以这里直接 `require` 真实的 `php/stock/stocksymbol.php`
// （该文件**没有任何 require/include**，可独立加载），让两侧判定都由真代码给出。
//
// ## 分支体本身也从源码抠
//
// `fundpairref.php:16-43` 那段 `if / else if` 是按行抠出来贴进替身类的，
// 不手抄 —— 手抄一次就等于把 PHP 的分支顺序再翻译一遍，翻译错了对照样绿。
//
// 抠出来的代码里会 `new CnyReference(...)` / `new MyStockReference(...)`，
// 这两个类在真实环境里要连库，这里用同名的替身顶上，只记录符号与类名。
// **类名是要紧的**：PHP 两条赋值路径建的就是两个不同的类
// （中间价 → CnyReference，离岸外汇 fx_* → MyStockReference）。
//
// **注意**：本文件里不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }

// 真实符号分类：唯一的判定来源
require $phpDir . '/stock/stocksymbol.php';

//: 从 `$strPair` 分支块的开头，切到 `new CnyReference($strCNY);` 那一行结束
function ExtractCnyBranch($path)
{
    $text = file_get_contents($path);
    if ($text === false) { fwrite(STDERR, "读不到 $path\n"); exit(2); }
    $start = strpos($text, 'if ($this->pair_ref->IsSinaFutureCN())');
    if ($start === false) { fwrite(STDERR, "找不到 cny 分支起始\n"); exit(2); }
    $marker = 'new CnyReference($strCNY);';
    $end = strpos($text, $marker, $start);
    if ($end === false) { fwrite(STDERR, "找不到 cny 分支结束\n"); exit(2); }
    // 吃到该行末尾（含分号与换行）
    $eol = strpos($text, "\n", $end);
    if ($eol === false) { $eol = strlen($text); }
    return substr($text, $start, $eol - $start);
}

$branch = ExtractCnyBranch($phpDir . '/stock/fundpairref.php');

// 两个被 new 出来的类：只记符号与类名，不碰 IO
eval('
class CnyReference {
    var $sym; function __construct($s) { $this->sym = $s; }
    function GetVal($strDate = false) { return 1.0; }
}
class MyStockReference {
    var $sym; function __construct($s) { $this->sym = $s; }
    function GetVal($strDate = false) { return 1.0; }
}
');

// 替身：pair_ref 一侧的判定委托给真实 StockSymbol
eval('
class PairSideStub {
    var $_sym;
    function __construct($s) { $this->_sym = new StockSymbol($s); }
    function IsSinaFutureCN() { return $this->_sym->IsSinaFutureCN(); }
    function IsSymbolA()       { return $this->_sym->IsSymbolA(); }
    function IsSymbolH()       { return $this->_sym->IsSymbolH(); }
    function IsShangHaiB()     { return $this->_sym->IsShangHaiB(); }
    function IsShenZhenB()     { return $this->_sym->IsShenZhenB(); }
    function GetPrice()        { return "1.0"; }
}
');

// 替身：本体一侧。判定同样委托给真实 StockSymbol，分支体从源码贴进来
$code = "class PairCnyStub {\n";
$code .= "    var \$pair_ref = false;\n";
$code .= "    var \$cny_ref = false;\n";
$code .= "    var \$_sym;\n";
$code .= "    function __construct(\$s) { \$this->_sym = new StockSymbol(\$s); }\n";
$code .= "    function IsSymbolA()   { return \$this->_sym->IsSymbolA(); }\n";
$code .= "    function IsSymbolH()   { return \$this->_sym->IsSymbolH(); }\n";
$code .= "    function IsSymbolUS()  { return \$this->_sym->IsSymbolUS(); }\n";
$code .= "    function IsSinaFutureUS() { return \$this->_sym->IsSinaFutureUS(); }\n";
$code .= "    function IsSinaFutureCN() { return \$this->_sym->IsSinaFutureCN(); }\n";
$code .= "    function Build(\$strPair) {\n";
$code .= "        \$this->pair_ref = new PairSideStub(\$strPair);\n";
$code .= "        \$strCNY = false;\n";
$code .= $branch . "\n";
$code .= "    }\n";
$code .= "}\n";

// 落盘再 require：语法错误时行号好认
$tmp = tempnam(sys_get_temp_dir(), 'pairref_') . '.php';
if (file_put_contents($tmp, "<?php\n" . $code) === false) {
    fwrite(STDERR, "写临时文件失败\n"); exit(2);
}
require $tmp;

//: 用例矩阵：本体符号 + 配对符号。覆盖 fundpairref.php:16-43 的每一条分支
$cases = array(
    // -- 配对是境内期货：空分支，cny_ref 保持 false
    array('self' => 'SH000300', 'pair' => 'nf_AU0'),
    // -- 配对是 A 股
    array('self' => 'SZ161725', 'pair' => 'SH900901'),   // 我也是 A，配对沪 B → USCNY
    array('self' => 'SZ161725', 'pair' => 'SH999999'),   // 数值区间上沿，非前缀口径
    array('self' => 'SZ161725', 'pair' => 'SZ200011'),   // 配对深 B → HKCNY
    array('self' => 'SZ161725', 'pair' => 'SH600519'),   // 配对是普通 A，两侧都非 B → 无
    array('self' => 'HWM',      'pair' => 'SH600519'),   // 我是美股 → fx_susdcnh
    array('self' => 'hf_CL',    'pair' => 'SH600519'),   // 我是境外期货 → IsSinaFutureUS
    array('self' => '00700',    'pair' => 'SH600519'),   // 我是 H：PHP 该分支被注释掉 → 无
    // -- 配对是 H 股
    array('self' => 'SZ161725', 'pair' => '00700'),      // A → HKCNY
    array('self' => 'HWM',      'pair' => '00700'),      // 美股 → fx_susdhkd
    array('self' => '00700',    'pair' => '00700'),      // H 对 H → 无
    // -- 配对是印度 Sensex
    array('self' => 'SZ161725', 'pair' => 'znb_SENSEX'), // → fx_susdinr
    array('self' => 'HWM',      'pair' => 'znb_SENSEX'), // 非 A 也无妨，只看配对符号
    // -- 兜底分支：配对不属于以上任何一类
    array('self' => 'SZ161725', 'pair' => '^GSPC'),      // 我是 A → USCNY
    array('self' => 'HWM',      'pair' => '^GSPC'),      // 我是美股 → 无
);

$results = array();
foreach ($cases as $i => $c) {
    $ref = new PairCnyStub($c['self']);
    $ref->Build($c['pair']);
    $cny = $ref->cny_ref;
    $results[] = array(
        'index' => $i,
        'self' => $c['self'],
        'pair' => $c['pair'],
        // false 表示 PHP 没建汇率引用
        'cny' => ($cny === false) ? false : $cny->sym,
        'kind' => ($cny === false) ? '' : get_class($cny),
    );
}

echo json_encode(array('cases' => $results));
exit(0);
