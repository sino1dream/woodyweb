<?php
// ---------------------------------------------------------------------------
// 用**生产端 SH513050 的真实 36 只持仓**跑一遍 PHP 的 `_estNetValue`（CK4-D）
//
//   D:/xampp/php/php.exe -n -d display_errors=stderr _php_holdings_real.php <input.json>
//
// ## 为什么还要再验一次
//
// CK4-B 的 `_php_holdings_check.php` 只用了 3 只成分股的**人造**输入。真实数据
// 有个它在人造输入下碰不到的坑：`$arHoldingsRatio` 的键来自
// `SqlGetHoldingsArray()`（**整数** stock_id），而 `$ref->GetStockId()` 返回的是
// **字符串**。PHP 里 `$a['142']` 与 `$a[142]` 是同一个键（数字字符串数组键会被
// 自动转成整数），所以命中；Python 的 dict 没这层转换，`{'142': 30.68}[142]`
// 直接 KeyError —— 比例全取成 0，估值整列变成 0，**且不报错**。
//
// 本文件因此特意让 `GetStockId()` 返回**字符串**、而 `arHoldingsRatio` 用
// **整数**键，把真实形状复现出来（Python 侧那次修复正是为它）。
//
// ## 输入来自 Python 侧导出的 JSON
//
// 两遍必须喂**同一份**数，否则比不出东西。JSON 由 `_ck4_real_check.py`
// 从演示库导出（比例 / 持仓日旧价 / EST 日收盘价 / 当前价），本文件只读不算。
//
// ## 手法：函数体仍从 PHP 源码现场切出
//
// 与 `_php_holdings_check.php` 同款，不手抄公式。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）。
// ---------------------------------------------------------------------------

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

$inputPath = isset($argv[1]) ? $argv[1] : '';
if ($inputPath === '') { fwrite(STDERR, "用法：php _php_holdings_real.php <input.json>\n"); exit(2); }
$json = file_get_contents($inputPath);
if ($json === false) { fwrite(STDERR, "读不到 $inputPath\n"); exit(2); }
$input = json_decode($json, true);
if (!is_array($input)) { fwrite(STDERR, "JSON 解析失败\n"); exit(2); }

function ExtractMethod($text, $name)
{
    // `\r?` 不能省：PHP 源码是 CRLF，少了它一个方法都切不出来。
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

$srcPath = $rootDir . '/php/stock/holdingsref.php';
$src = file_get_contents($srcPath);
if ($src === false) { fwrite(STDERR, "读不到 $srcPath\n"); exit(2); }

$methods = array();
foreach (array('_estNetValue', 'GetAdjustUSD', 'GetAdjustHKD', '_adjustToCNY',
               'GetHoldingDateHistory', 'GetHoldingsRatioDisplay', 'GetProportionArray')
         as $name)
{
    $body = ExtractMethod($src, $name);
    if ($body === null) { fwrite(STDERR, "切不出 $name\n"); exit(2); }
    $methods[$name] = $body;
}
$refAdjustForex = ExtractMethod($src, 'RefAdjustForex');
if ($refAdjustForex === null) { fwrite(STDERR, "切不出 RefAdjustForex\n"); exit(2); }

// -- 替身 -------------------------------------------------------------------
class HoldingRef
{
    public $id; public $symbol; public $price; public $kind; public $date;

    // **返回字符串**：真实的 `MysqlReference::GetStockId()` 就是这样，
    // 而 `arHoldingsRatio` 的键是整数 —— 正是要复现的组合。
    function GetStockId()    { return (string)$this->id; }
    function GetPrice()      { return $this->price; }
    function GetDate()       { return $this->date; }
    function IsSymbolA()     { return $this->kind === 'a'; }
    function IsSymbolH()     { return $this->kind === 'hk'; }
}

class CnyRef
{
    public $price; public $date; public $closes;
    function GetPrice()          { return $this->price; }
    function GetDate()           { return $this->date; }
    function GetClose($strDate)  { return isset($this->closes[$strDate]) ? $this->closes[$strDate] : false; }
    function GetVal($strDate)    { return isset($this->closes[$strDate]) ? $this->closes[$strDate] : $this->price; }
}

class HisSql
{
    public $adjclose = array();
    public $close = array();

    function _key($id, $date) { return $id . '|' . $date; }

    function GetAdjClose($id, $date)
    {
        $key = $this->_key($id, $date);
        if (isset($this->adjclose[$key])) { return $this->adjclose[$key]; }
        $best = false; $bestDate = '';
        foreach ($this->adjclose as $k => $v)
        {
            $parts = explode('|', $k);
            if ($parts[0] != $id) { continue; }
            if (strcmp($parts[1], $date) <= 0 && ($best === false || strcmp($parts[1], $bestDate) > 0))
            { $best = $v; $bestDate = $parts[1]; }
        }
        return $best;
    }

    function GetClose($id, $date)
    {
        $key = $this->_key($id, $date);
        return isset($this->close[$key]) ? $this->close[$key] : false;
    }
}

// -- 组装被测类 -------------------------------------------------------------
$classCode = "namespace Harness;\n"
    . "const MIN_FLOAT_VAL = 0.0000005;\n"
    . "\n"
    . "class Ref\n"
    . "{\n"
    . "    public \$netvalue_ref = false;\n"
    . "    public \$uscny_ref = false; public \$usdcny_ref = false;\n"
    . "    public \$hkcny_ref = false; public \$hkdcny_ref = false;\n"
    . "    public \$ar_holdings_ref = array(); public \$ar_realtime_ref = array();\n"
    . "    public \$strNetValue; public \$strHoldingsDate = false;\n"
    . "    public \$arHoldingsDateHistory = array(); public \$arHoldingsRatio = array();\n"
    . "    public \$strEstDate; public \$fOfficialEstAdjust = 0.0;\n"
    . "    public \$fRatioCN = false; public \$fRatioHK = false; public \$fRatioUS = false;\n"
    . "    public \$strSymbol = ''; public \$fPosition = 1.0; public \$strDate = '';\n"
    . "\n"
    . "    function GetSymbol()    { return \$this->strSymbol; }\n"
    . "    function GetDate()      { return \$this->strDate; }\n"
    . "    function GetPosition()  { return \$this->fPosition; }\n"
    . "    function IsSymbolA()    { return (bool)preg_match('/^(SH|SZ|BJ)\\\\d{6}\$/', \$this->strSymbol); }\n"
    . "    function IsSymbolH()    { return \$this->strSymbol !== '' && \$this->strSymbol[0] === '0' && ctype_digit(\$this->strSymbol) && strlen(\$this->strSymbol) === 5; }\n"
    . "    function IsEtfA()       { return (bool)preg_match('/^(SH|SZ)(51|56|58|15|16)\\\\d{4}\$/', \$this->strSymbol); }\n"
    . "    function IsFundA()      { return \$this->IsEtfA() || (bool)preg_match('/^(SH|SZ)(50|16|15)\\\\d{4}\$/', \$this->strSymbol); }\n"
    . "\n"
    . $methods['_estNetValue'] . "\n"
    . $methods['GetAdjustUSD'] . "\n"
    . $methods['GetAdjustHKD'] . "\n"
    . $methods['_adjustToCNY'] . "\n"
    . $methods['GetHoldingDateHistory'] . "\n"
    . $methods['GetHoldingsRatioDisplay'] . "\n"
    . $methods['GetProportionArray'] . "\n"
    . "}\n"
    . "\n"
    . $refAdjustForex . "\n";

eval($classCode);

$g_his_sql = new HisSql();

function GetStockHistorySql()
{
    global $g_his_sql;
    return $g_his_sql;
}

// -- 按 JSON 组装 -----------------------------------------------------------
$g_his_sql->adjclose = $input['adjclose'];
$g_his_sql->close = $input['adjclose'];

$ref = new Harness\Ref();
$ref->strSymbol = $input['symbol'];
$ref->strNetValue = (string)$input['net_value'];
$ref->strHoldingsDate = $input['holdings_date'];
$ref->strDate = $input['est_date'];
$ref->fPosition = floatval($input['position']);

$refs = array();
$ratios = array();
$sumCN = false; $sumHK = false; $sumUS = false;
foreach ($input['holdings'] as $item)
{
    $r = new HoldingRef();
    $r->id = intval($item['id']);
    $r->symbol = $item['symbol'];
    $r->price = (string)$item['price'];
    $r->kind = $item['kind'];
    $r->date = $item['date'];
    $refs[] = $r;
    // **整数键**（与 `SqlGetHoldingsArray()` 一致），而 `GetStockId()` 给字符串
    $ratios[intval($item['id'])] = (string)$item['ratio'];
    $ratio = floatval($item['ratio']);
    if ($item['kind'] === 'a')       { $sumCN = ($sumCN === false) ? $ratio : $sumCN + $ratio; }
    elseif ($item['kind'] === 'hk')  { $sumHK = ($sumHK === false) ? $ratio : $sumHK + $ratio; }
    else                             { $sumUS = ($sumUS === false) ? $ratio : $sumUS + $ratio; }
}
$ref->ar_holdings_ref = $refs;
$ref->arHoldingsRatio = $ratios;
$ref->fRatioCN = $sumCN; $ref->fRatioHK = $sumHK; $ref->fRatioUS = $sumUS;

// 汇率替身：与演示库的现状对齐 —— uscny 只有 EST 日那条收盘价（持仓日的没有），
// usdcny / hkdcny 干脆没价，于是两处 `_adjustToCNY` 都退化成 1.0。
// Python 侧读的是同一份库，所以两边应该在同一个"没汇率"的前提下比。
foreach ($input['forex'] as $key => $spec)
{
    $c = new CnyRef();
    $c->price = (string)$spec['price'];
    $c->date = $spec['date'];
    $c->closes = $spec['closes'];
    $ref->$key = $c;
}

$out = array();
$out['adjust_usd_dated'] = $ref->GetAdjustUSD($input['est_date']);
$out['adjust_hkd_dated'] = $ref->GetAdjustHKD($input['est_date']);
$out['adjust_usd'] = $ref->GetAdjustUSD();
$out['adjust_hkd'] = $ref->GetAdjustHKD();
$out['official'] = $ref->_estNetValue($input['est_date']);
$out['fair'] = $ref->_estNetValue();
$out['ratio_display'] = $ref->GetHoldingsRatioDisplay();

echo json_encode($out), "\n";
