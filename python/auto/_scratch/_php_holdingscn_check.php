<?php
// ---------------------------------------------------------------------------
// 对着**真实 PHP** 跑「基金持仓」页的七列测算表（``holdingscn.php``）
//
//   D:/xampp/php/php.exe -n -d display_errors=stderr _php_holdingscn_check.php
//
// 对照的是 ``_echoHoldingItem($ref, $arRatio, $fNetValueChange, $arHistory,
// $fAdjust)``（``woody/res/holdingscn.php:24-67``）—— 持仓页最后那张表的
// **每一个成分股一行 + 合计尾行**：
//
//   代码 | 旧比例(%) | 旧价格 | 此后涨幅 | 新比例(%) | 影响比例(%) | 汇率调整
//
// 为什么值得单独对照：这些数算错了不报错，只是给出一个"看着合理"的结果 ——
// 生产端 SH513050 页面上旧比例合计 100.00、净值涨幅 -3.50%，哪一列错了都
// 只是"差一点点"。而且**入参就是 MySQL 的三位小数串**（``"3.675"``），
// Python 的二进制舍入与 PHP 的最短十进制 half-up 在 .xx5 边界必然分叉。
//
// ## 手法：函数体从 PHP 源码现场切出（不是手抄）
//
// 与 ``_php_holdings_check.php`` 同一套路子：把 ``_echoHoldingItem`` 以及它
// 依赖的 ``mysql_round`` / ``RefGetMyStockLink`` / ``RefEchoTableColumn`` /
// ``GetPercentage*`` / ``GetPrecision`` / ``GetMyStockLink`` /
// ``StockGetPercentage`` / ``RefAdjustForex`` 与整条 stocklink 链接链
// （``GetStockSymbolLink`` → … → ``GetLinkElement``）按花括号配对切出来，
// 原样放进 ``namespace Harness``。**一个字不改** —— 手抄一遍公式就失去了
// 对照的意义。
//
// 只 stub 四类**非被测**的东西：
// * ``EchoTableColumn`` —— 改成把 ``$ar`` 收进全局数组（原本是 echo HTML）；
// * ``SqlGetStockName`` —— 返回 ``符号.'名称'``（Python 侧的假数据源同款）；
// * ``UrlGetPage`` / ``UrlGetQueryString`` / ``UrlGetPhp`` / ``UrlGetServer``
//   —— 请求上下文，让 ``GetPageLink`` 走 ``GetPhpLink`` 那条真链路；
// * ``DISP_ALL_CN`` / ``MIN_FLOAT_VAL`` / ``PATH_STOCK`` 常量。
//
// ## 三组输入（与 ``_php_holdingscn_compare.py`` 逐字一致）
//
// 1. ``prod_like`` —— 生产端 SH513050 的形状：A/H/US 各一只，汇率
//    0.9063 / 1.0082，净值涨幅 2.34%；
// 2. ``edges`` —— 四个边界：旧价格 ``3.675``（half-up）、price == close
//    （阈值归零）、持仓日缺行（``$fClose`` null）、close 为 **float 0.0**
//    （``GetAdjClose`` 给 ``'0.00'`` 时 ``floatval`` 进来的形状 —— 注意
//    ``0.0 == false`` 为真，涨幅除数回落到昨收；而**字符串** ``'0.0'``
//    在 PHP 里是真值，不给回落。真实流里 history 的值恒为 float）。
//
// **一个用例一个进程**：``_echoHoldingItem`` 的三个累计器是 ``static``，
// 在同一进程里连跑两个用例会互相污染（实测第二个用例的旧比例合计会加上
// 第一个用例的 42.99）。Python 侧是每次 ``get_table`` 清零，所以对照时
// 必须一个用例起一个进程。
//
// **本文件不能出现 PHP 结束标签**（注释里也不行）—— 解析器会在那里结束
// 脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

function ExtractFunction($text, $name)
{
    // 匹配 `function 名字(...)  {` 起手，然后按花括号配对吃到函数结束。
    // **``\r?`` 不能省**：PHP 源码是 Windows 换行，签名那行的 ``)`` 后面
    // 直接跟 ``\r`` —— 少了它就一个函数都切不出来（实测踩到）。
    $pattern = '/(?:^|\n)[ \t]*(?:public |protected |private |static )*function[ \t]+'
             . preg_quote($name, '/') . '[ \t]*\([^)]*\)[ \t]*\r?\n?[ \t]*\{/';
    if (!preg_match($pattern, $text, $m, PREG_OFFSET_CAPTURE)) { return null; }
    $start = $m[0][1];
    $i = $m[0][1] + strlen($m[0][0]) - 1;   // 指向起手的 '{'
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

function ReadSource($rootDir, $relPath)
{
    $path = $rootDir . '/' . $relPath;
    $text = file_get_contents($path);
    if ($text === false) { fwrite(STDERR, "读不到 $path\n"); exit(2); }
    return $text;
}

// -- 逐个切出被测函数 ---------------------------------------------------------
$src = array(
    'holdingscn'  => ReadSource($rootDir, 'woody/res/holdingscn.php'),
    'debug'       => ReadSource($rootDir, 'php/debug.php'),
    'stockcomm'   => ReadSource($rootDir, 'php/ui/stockcomm.php'),
    'holdingsref' => ReadSource($rootDir, 'php/stock/holdingsref.php'),
    'stockref'    => ReadSource($rootDir, 'php/stock/stockref.php'),
    'stocksymbol' => ReadSource($rootDir, 'php/stock/stocksymbol.php'),
    'stock'       => ReadSource($rootDir, 'php/stock.php'),
    'htmlelement' => ReadSource($rootDir, 'php/ui/htmlelement.php'),
    'httplink'    => ReadSource($rootDir, 'php/httplink.php'),
    'stocklink'   => ReadSource($rootDir, 'php/stocklink.php'),
);

$methods = array();   // 类方法（放进 HoldingRef）
$functions = array(); // 自由函数（放进 Harness 命名空间）

foreach (array(
    array('src' => 'holdingscn',  'name' => '_echoHoldingItem',      'kind' => 'free'),
    array('src' => 'debug',       'name' => 'mysql_round',           'kind' => 'free'),
    array('src' => 'debug',       'name' => 'strval_round',          'kind' => 'free'),
    array('src' => 'stockcomm',   'name' => 'RefGetMyStockLink',     'kind' => 'free'),
    array('src' => 'stockcomm',   'name' => 'RefEchoTableColumn',    'kind' => 'free'),
    array('src' => 'holdingsref', 'name' => 'RefAdjustForex',        'kind' => 'free'),
    array('src' => 'stockref',    'name' => 'GetPercentage',         'kind' => 'method'),
    array('src' => 'stockref',    'name' => 'GetPercentageString',   'kind' => 'method'),
    array('src' => 'stockref',    'name' => 'GetPercentageText',     'kind' => 'method'),
    array('src' => 'stockref',    'name' => 'GetPercentageDisplay',  'kind' => 'method'),
    array('src' => 'stocksymbol', 'name' => 'GetPrecision',          'kind' => 'method'),
    array('src' => 'stocksymbol', 'name' => 'GetMyStockLink',        'kind' => 'method'),
    array('src' => 'stock',       'name' => 'StockGetPercentage',    'kind' => 'free'),
    array('src' => 'htmlelement', 'name' => 'GetHtmlElement',        'kind' => 'free'),
    array('src' => 'htmlelement', 'name' => 'GetDoubleQuotes',       'kind' => 'free'),
    array('src' => 'htmlelement', 'name' => 'GetFontElement',        'kind' => 'free'),
    array('src' => 'htmlelement', 'name' => 'GetLinkElement',        'kind' => 'free'),
    array('src' => 'httplink',    'name' => 'GetPageLink',           'kind' => 'free'),
    array('src' => 'httplink',    'name' => 'GetPhpLink',            'kind' => 'free'),
    array('src' => 'httplink',    'name' => 'GetInternalLink',       'kind' => 'free'),
    array('src' => 'stocklink',   'name' => 'GetMyStockLink',        'kind' => 'free'),
    array('src' => 'stocklink',   'name' => 'GetStockSymbolLink',    'kind' => 'free'),
    array('src' => 'stocklink',   'name' => 'GetStockPageLink',      'kind' => 'free'),
) as $item)
{
    $body = ExtractFunction($src[$item['src']], $item['name']);
    if ($body === null)
    {
        fwrite(STDERR, sprintf("切不出 %s（来自 %s）\n", $item['name'], $item['src']));
        exit(2);
    }
    if ($item['kind'] === 'method') { $methods[$item['name']]  = $body; }
    else                            { $functions[$item['name']] = $body; }
}

// -- 组装 Harness 命名空间 -----------------------------------------------------
$classCode = "namespace Harness;\n"
    . "const MIN_FLOAT_VAL = 0.0000005;\n"
    . "const FLOAT_PRECISION = 6;\n"
    . "const DISP_ALL_CN = '全部';\n"
    . "const PATH_STOCK = '/woody/res/';\n"
    . "\n"
    . "class HoldingRef\n"
    . "{\n"
    . "    public \$strStockId; public \$strSymbol; public \$strPrice;\n"
    . "    public \$strPrevPrice; public \$strDisplay;\n"
    . "    public \$bSymbolA = false; public \$bSymbolH = false;\n"
    . "\n"
    . "    function GetStockId()  { return \$this->strStockId; }\n"
    . "    function GetPrice()    { return \$this->strPrice; }\n"
    . "    function GetSymbol()   { return \$this->strSymbol; }\n"
    . "    function GetDisplay()  { return \$this->strDisplay; }\n"
    . "    function IsSymbolA()   { return \$this->bSymbolA; }\n"
    . "    function IsSymbolH()   { return \$this->bSymbolH; }\n"
    . "    // **下面四个恒 false**：只有普通股票才会进持仓表（演示库里\n"
    . "    // SH600519 / 00700 / PDD 都不是基金/B 股/外汇），precision 恒 2。\n"
    . "    // Python 侧用的是**真** StockSymbol 判定，选符号时已核对过一致。\n"
    . "    function IsFundA()     { return false; }\n"
    . "    function IsSinaFund()  { return false; }\n"
    . "    function IsStockB()    { return false; }\n"
    . "    function IsForex()     { return false; }\n"
    . "\n";
foreach ($methods as $body) { $classCode .= "    " . $body . "\n"; }
$classCode .= "}\n";

$freeCode = "namespace Harness;\n"
    . "// EchoTableColumn 原本是 echo HTML；这里把行收进全局数组，让 JSON 能带走。\n"
    . "function EchoTableColumn(\$ar, \$strColor = false, \$strFirstHint = false)\n"
    . "{\n"
    . "    \$GLOBALS['harness_rows'][] = array('cells' => array_values(\$ar), 'hint' => \$strFirstHint);\n"
    . "}\n"
    . "\n"
    . "function SqlGetStockName(\$strSymbol) { return \$strSymbol . '名称'; }\n"
    . "\n"
    . "// 请求上下文 stub：让 GetPageLink 落到 GetPhpLink 那条真链路\n"
    . "function UrlGetPage()        { return ''; }\n"
    . "function UrlGetQueryString() { return ''; }\n"
    . "function UrlGetPhp(\$bChinese = true) { return \$bChinese ? 'cn.php' : '.php'; }\n"
    . "function UrlGetServer()      { return 'https://palmmicro.com'; }\n"
    . "\n";
foreach ($functions as $body) { $freeCode .= $body . "\n"; }

eval($classCode);
eval($freeCode);

// -- 输入（与 _php_holdingscn_compare.py 逐字一致）---------------------------
$CASES = array(
    'prod_like' => array(
        'nvc'     => 1.0234,
        'adj_hkd' => 0.9063,
        'adj_usd' => 1.0082,
        // int 键：MySQL 取出的形状；GetStockId() 返回字符串 —— PHP 的
        // 数字字符串键会自动转 int，所以 ``$arRatio['13']`` 照样命中。
        'ratio'   => array(13 => 5.0, 11 => 30.68, 12 => 7.31),
        'history' => array(13 => '1500.0', 11 => '425.2', 12 => '81.96'),
        'refs'    => array(
            array('id' => '13', 'symbol' => 'SH600519', 'price' => '1501.0', 'prev' => '1490.0', 'a' => true,  'h' => false),
            array('id' => '11', 'symbol' => '00700',    'price' => '418.0',  'prev' => '430.0',  'a' => false, 'h' => true),
            array('id' => '12', 'symbol' => 'PDD',      'price' => '81.96',  'prev' => '80.0',   'a' => false, 'h' => false),
        ),
    ),
    'edges' => array(
        'nvc'     => 0.9985,
        'adj_hkd' => 0.9,
        'adj_usd' => 1.0,
        'ratio'   => array(20 => 1.0, 21 => 2.5, 22 => 0.0001, 23 => 4.0),
        // 23 => float 0.0（不是字符串 '0.0'）：真实流里 GetHoldingDateHistory
        // 的值恒为 floatval 的结果；而 '0' 会被 ``if ($strAdjClose = ...)``
        // 守卫整条跳过（键干脆不出现），那种情形由 22 号（缺键）覆盖。
        'history' => array(20 => '3.675', 21 => '10.0', 23 => 0.0),
        'refs'    => array(
            array('id' => '20', 'symbol' => 'SZ000001', 'price' => '3.7',    'prev' => '3.6',    'a' => true,  'h' => false),
            array('id' => '21', 'symbol' => '01810',    'price' => '10.0',   'prev' => '10.0',   'a' => false, 'h' => true),
            array('id' => '22', 'symbol' => 'BILI',     'price' => '106.2',  'prev' => '100.0',  'a' => false, 'h' => false),
            array('id' => '23', 'symbol' => 'PDD',      'price' => '101.0',  'prev' => '100.0',  'a' => false, 'h' => false),
        ),
    ),
);

$FUND_SYMBOL = 'SH513050';   // A 股 ETF → 尾行汇率调整 1.0

function MakeRef($ar)
{
    $ref = new Harness\HoldingRef();
    $ref->strStockId   = $ar['id'];
    $ref->strSymbol    = $ar['symbol'];
    $ref->strPrice     = $ar['price'];
    $ref->strPrevPrice = $ar['prev'];
    $ref->strDisplay   = $ar['symbol'];
    $ref->bSymbolA     = $ar['a'];
    $ref->bSymbolH     = $ar['h'];
    return $ref;
}

// -- 跑一个用例（static 累计器不跨进程）--------------------------------------
$caseName = isset($argv[1]) ? $argv[1] : '';
if ($caseName === '' || !isset($CASES[$caseName]))
{
    fwrite(STDERR, "用法: php _php_holdingscn_check.php <用例名>\n可选: "
         . implode(', ', array_keys($CASES)) . "\n");
    exit(2);
}
$case = $CASES[$caseName];

$GLOBALS['harness_rows'] = array();
$fundRef = new Harness\HoldingRef();
$fundRef->strSymbol = $FUND_SYMBOL;
$fundRef->strDisplay = $FUND_SYMBOL;
$fundRef->bSymbolA = true;

foreach ($case['refs'] as $ar)
{
    $ref = MakeRef($ar);
    // 每个成分股自己的汇率调整（RefAdjustForex）
    $fAdjust = Harness\RefAdjustForex($ref, $case['adj_hkd'], $case['adj_usd']);
    Harness\_echoHoldingItem($ref, $case['ratio'], $case['nvc'], $case['history'], $fAdjust);
}
// 合计尾行：$fAdjust 取**基金自己**的
Harness\_echoHoldingItem(false, $case['ratio'], $case['nvc'], $case['history'],
                         Harness\RefAdjustForex($fundRef, $case['adj_hkd'], $case['adj_usd']));

echo json_encode($GLOBALS['harness_rows']), "\n";
