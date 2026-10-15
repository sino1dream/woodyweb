<?php
// ---------------------------------------------------------------------------
// 对着**真实 PHP** 跑 `HoldingsReference` 的估值式（CK4-B 的逐例对照）
//
//   D:/xampp/php/php.exe -n -d display_errors=stderr _php_holdings_check.php
//
// ## 这一段在页面上是哪个数
//
// `_mystock.php:138` 的 Holdings 分支：
//
// ```php
// else if ($holdings_ref = StockGetHoldingsReference($strSymbol))
// {
//     EchoHoldingsEstParagraph($holdings_ref);   // 官方 EST / 参考 EST / 溢价
//     ...
// }
// ```
//
// 表里的「官方EST」= `GetOfficialNetValue()` = `_estNetValue($strEstDate)`，
// 「参考EST」= `_estNetValue()`。生产端 `holdingscn.php?symbol=SH513050` 页面上
// 那两列（1.025 / 1.006）就是它俩。所以**这一个方法算错，整张表全错**，
// 而且看不出来 —— 它不会报错，只是给出一个"看着合理"的数。
//
// ## 手法：函数体从 PHP 源码现场切出（不是手抄）
//
// 与 `_php_fundhistory_check.php` 同一套路子：把 `_estNetValue` 以及它依赖的
// 汇率调整三件套从 `php/stock/holdingsref.php` 里按花括号配对切出来，
// 原样放进 `namespace Harness\Ref`。**一个字不改** —— 手抄一遍公式就失去了
// 对照的意义（抄的时候会把理解错的地方一起抄对）。
//
// 切出来的是**方法**，所以 `Ref` 类里要补上 PHP 那边的实例属性与几个
// 符号判定（`GetPosition` / `IsSymbolA` / `IsSymbolH` / `IsEtfA` / `IsFundA`）。
// 这些是替身：真实实现在 `MyStockReference` 里，会连库 —— 本对照只验估值式，
// 符号判定由 `test_holdingsref.py` 单独钉。
//
// ## 四组输入
//
// 1. 无汇率引用 → 两个 adjust 都是 1.0，纯加权涨幅；
// 2. 有汇率引用 + A 股 ETF → 走 `_adjustToCNY(uscny, usdcny)` 那一支；
// 3. 指定日期 → 用那天的 adjclose 代替当前价（官方 EST 的路径）；
// 4. realtime → 对有 `EstFromPair` 的成分股换实时估值。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

function ExtractMethod($text, $name)
{
    // 匹配 `function 名字(...)  {` 起手，然后按花括号配对吃到方法结束。
    // **``\r?`` 不能省**：PHP 源码是 Windows 换行（``\r\n``），签名那行的
    // ``)`` 后面直接跟 ``\r`` —— 少了它就一个方法都切不出来（实测踩到）。
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

    function GetStockId()    { return $this->id; }
    function GetPrice()      { return $this->price; }
    function GetDate()       { return $this->date; }
    function IsSymbolA()     { return $this->kind === 'a'; }
    function IsSymbolH()     { return $this->kind === 'hk'; }
    // **故意不定义 EstFromPair**：`_estNetValue` 用 `method_exists` 判断，
    // 没有这个方法就不走实时分支。
}

class HoldingRefRealtime extends HoldingRef
{
    public $est;
    function EstFromPair() { return $this->est; }
}

class CnyRef
{
    public $price; public $date; public $closes;
    function GetPrice()          { return $this->price; }
    function GetDate()           { return $this->date; }
    function GetClose($strDate)  { return isset($this->closes[$strDate]) ? $this->closes[$strDate] : false; }
    // `GetProportionArray` 用的是 `GetVal()`（``:183-189``），与 `_adjustToCNY`
    // 用的 `GetClose()` 不是同一个入口，两个都得有。
    function GetVal($strDate)    { return isset($this->closes[$strDate]) ? $this->closes[$strDate] : $this->price; }
}

class HisSql
{
    public $adjclose = array();   // "id|date" => 值
    public $close = array();

    function _key($id, $date) { return $id . '|' . $date; }

    function GetAdjClose($id, $date)
    {   // 与 PHP `StockHistorySql::GetAdjClose()` 同款：当天没有就回落到上一个交易日
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

    function GetProportion($id, $date, $prevDate)
    {
        $str = $this->GetClose($id, $date);
        if ($str !== false)
        {
            $strPrev = $this->GetClose($id, $prevDate);
            if ($strPrev !== false)
            {
                $fPrev = floatval($strPrev);
                if ($fPrev > MIN_FLOAT_VAL_GLOBAL) { return floatval($str) / $fPrev; }
            }
        }
        return false;
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

define('MIN_FLOAT_VAL_GLOBAL', 0.0000005);

function GetStockHistorySql()
{
    global $g_his_sql;
    return $g_his_sql;
}

// -- 输入 -------------------------------------------------------------------
$HOLDINGS_DATE = '2026-10-13';
$g_his_sql = new HisSql();
$g_his_sql->adjclose = array(
    '11|2026-10-13' => '425.2',
    '12|2026-10-13' => '81.96',
    '13|2026-10-13' => '1500.0',
    '11|2026-10-14' => '420.0',
    '12|2026-10-14' => '82.5',
    '13|2026-10-14' => '1510.0',
);
$g_his_sql->close = $g_his_sql->adjclose;

function makeRef($withFx, $realtime = false)
{
    $a = new HoldingRef();  $a->id = 13; $a->symbol = 'SH600519'; $a->price = '1510'; $a->kind = 'a'; $a->date = '2026-10-15';
    $h = $realtime ? new HoldingRefRealtime() : new HoldingRef();
    $h->id = 11; $h->symbol = '00700'; $h->price = '418'; $h->kind = 'hk'; $h->date = '2026-10-15';
    if ($realtime) { $h->est = '500'; }
    $u = new HoldingRef();  $u->id = 12; $u->symbol = 'PDD';  $u->price = '81.45'; $u->kind = 'us'; $u->date = '2026-10-15';

    $ref = new Harness\Ref();
    $ref->strSymbol = 'SH513050';
    $ref->strNetValue = '1.0115';
    $ref->strHoldingsDate = '2026-10-13';
    $ref->ar_holdings_ref = array($a, $h, $u);
    $ref->arHoldingsRatio = array(13 => '5.0', 11 => '30.68', 12 => '7.31');
    $ref->fRatioCN = 5.0; $ref->fRatioHK = 30.68; $ref->fRatioUS = 7.31;
    $ref->fPosition = 1.0;
    if ($withFx)
    {
        $ref->uscny_ref = new CnyRef(); $ref->uscny_ref->price = '7.1';  $ref->uscny_ref->date = '2026-10-15';
        $ref->uscny_ref->closes = array('2026-10-13' => '7.12', '2026-10-14' => '7.11');
        $ref->usdcny_ref = new CnyRef(); $ref->usdcny_ref->price = '7.09'; $ref->usdcny_ref->date = '2026-10-15';
        $ref->hkcny_ref = new CnyRef(); $ref->hkcny_ref->price = '0.9'; $ref->hkcny_ref->date = '2026-10-15';
        $ref->hkcny_ref->closes = array('2026-10-13' => '0.91', '2026-10-14' => '0.905');
        $ref->hkdcny_ref = new CnyRef(); $ref->hkdcny_ref->price = '0.89'; $ref->hkdcny_ref->date = '2026-10-15';
    }
    return $ref;
}

$out = array();

// 组 1：无汇率引用
$r1 = makeRef(false);
$out['plain'] = $r1->_estNetValue();
$out['adjust_usd_plain'] = $r1->GetAdjustUSD();
$out['adjust_hkd_plain'] = $r1->GetAdjustHKD();

// 组 2：有汇率引用（A 股 ETF → 走 uscny/usdcny 那一支）
$r2 = makeRef(true);
$out['with_fx'] = $r2->_estNetValue();
$out['adjust_usd_etf'] = $r2->GetAdjustUSD();
$out['adjust_hkd_etf'] = $r2->GetAdjustHKD();
$out['adjust_usd_dated'] = $r2->GetAdjustUSD('2026-10-14');
$out['adjust_hkd_dated'] = $r2->GetAdjustHKD('2026-10-14');

// 组 3：指定日期（官方 EST 的路径）
$out['dated'] = $r2->_estNetValue('2026-10-14');

// 组 4：realtime
$r4 = makeRef(true, true);
$out['realtime'] = $r4->_estNetValue(false, true);

// 组 5：展示串与比例数组
$out['ratio_display'] = $r2->GetHoldingsRatioDisplay();
$out['proportion'] = $r2->GetProportionArray('2026-10-14', '2026-10-13');

echo json_encode($out), "\n";
