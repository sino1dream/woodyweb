<?php
// Differential harness: exercise the PHP StockSymbol class and print a JSON
// map of key -> value so the Python port can be compared case by case.
//
// Usage (normally driven by _php_port_compare.py, which is the Python side and
// builds the identical key set):
//
//     php -n -d display_errors=stderr _php_port_check.php -    # JSON on stdout
//     php -n -d display_errors=stderr _php_port_check.php out.json
//
// This is a development-only verification tool, not part of the Python port.
// It re-reads php/stock/stocksymbol.php on every run, so adding a symbol or a
// method to the PHP file and to _php_port_compare.py keeps the check honest.
//
// Diagnostics go to stderr so that stdout carries nothing but the JSON payload.
ini_set('display_errors', 'stderr');
error_reporting(E_ALL & ~E_WARNING & ~E_DEPRECATED);

// Locate php/stock/stocksymbol.php whether this runs from python/auto or the repo root.
$candidates = array(
    __DIR__ . '/../../../php/stock/stocksymbol.php',
    __DIR__ . '/php/stock/stocksymbol.php',
);
$src = false;
foreach ($candidates as $candidate)
{
    if (is_file($candidate)) { $src = file_get_contents($candidate); break; }
}
if ($src === false) {
    fwrite(STDERR, "cannot find php/stock/stocksymbol.php\n");
    exit(2);
}
$start = strpos($src, "define('SINA_FOREX_PREFIX'");
$end = strpos($src, '?>', $start);
if ($start === false || $end === false) {
    fwrite(STDERR, "unable to locate the stocksymbol.php body\n");
    exit(2);
}
$code = substr($src, $start, $end - $start);
$tmp = tempnam(sys_get_temp_dir(), 'ss') . '.php';
// The extracted body calls DebugString() from php/debug.php in production; the
// comparison only needs the return value, so a silent stub keeps output clean.
file_put_contents($tmp, "<?php\nfunction DebugString(\$str) { return \$str; }\n" . $code . "\n");
require $tmp;
if (!class_exists('StockSymbol')) {
    fwrite(STDERR, "StockSymbol was not defined by the extracted body\n");
    exit(3);
}

$symbols = array(
    '^HSI', '^HSCE', '^HSTECH', '^DJI', '^GSPC', '^NDX', '^FTSE',
    '^SZ162411-IV', '^HSI-IV',
    '00700', '09988', '000001',
    'SZ162411', 'SZ161227', 'SZ159518', 'SZ163208', 'SZ160416', 'SZ159502', 'SZ160723',
    'SZ000001', 'SZ200011', 'SZ300750', 'SZ399001',
    'SH600000', 'SH513350', 'SH501043', 'SH688981', 'SH900901', 'SH510300', 'SH513180',
    'SH000300', 'SH518880', 'SH513080',
    'BJ430047', 'BJ830799',
    'f_240019', 'f_24001',
    'znb_NKY', 'znb_CAC', 'znb_DAX', 'znb_TPX', 'znb_SENSEX', 'znb_UKX',
    'hf_ES', 'hf_CL', 'hf_CHA50CFD',
    'nf_AU0', 'nf_AG0',
    'fx_susdcnh', 'fx_sUSDCNH', 'fx_s',
    'USCNY', 'EUCNY', 'JPCNY', 'HKCNY',
    'XOP', 'AAPL', 'BRK.B', 'QQQ', 'SPY',
    'sz162411',
);

$methods0 = array(
    'is_index' => 'IsIndex',
    'is_yahoo_net_value' => 'IsYahooNetValue',
    'is_symbol_us' => 'IsSymbolUS',
    'is_symbol_h' => 'IsSymbolH',
    'get_digit_a' => 'GetDigitA',
    'is_symbol_a' => 'IsSymbolA',
    'is_shanghai_a' => 'IsShangHaiA',
    'is_shenzhen_a' => 'IsShenZhenA',
    'is_beijing_a' => 'IsBeiJingA',
    'is_shanghai_etf' => 'IsShangHaiEtf',
    'is_shanghai_lof' => 'IsShangHaiLof',
    'is_shanghai_star' => 'IsShangHaiStar',
    'is_shanghai_b' => 'IsShangHaiB',
    'is_shenzhen_b' => 'IsShenZhenB',
    'is_shenzhen_etf' => 'IsShenZhenEtf',
    'is_shenzhen_lof' => 'IsShenZhenLof',
    'is_shenzhen_gem' => 'IsShenZhenGem',
    'is_lof_a' => 'IsLofA',
    'is_etf_a' => 'IsEtfA',
    'is_fund_a' => 'IsFundA',
    'is_index_a' => 'IsIndexA',
    'is_stock_b' => 'IsStockB',
    'is_east_money_forex' => 'IsEastMoneyForex',
    'is_sina_forex' => 'IsSinaForex',
    'is_forex' => 'IsForex',
    'is_sina_fund' => 'IsSinaFund',
    'is_sina_global_index' => 'IsSinaGlobalIndex',
    'is_sina_future_cn' => 'IsSinaFutureCN',
    'is_sina_future_except_gold_cn' => 'IsSinaFutureExceptGoldCN',
    'is_sina_future_us' => 'IsSinaFutureUS',
    'is_sina_future' => 'IsSinaFuture',
    'get_sina_fund_symbol' => 'GetSinaFundSymbol',
    'get_sina_index_h' => 'GetSinaIndexH',
    'get_sina_index_us' => 'GetSinaIndexUS',
    'get_sina_symbol' => 'GetSinaSymbol',
    'get_yahoo_symbol' => 'GetYahooSymbol',
    'get_precision' => 'GetPrecision',
    'get_default_position' => 'GetDefaultPosition',
    'is_tradable' => 'IsTradable',
    'get_time_zone' => 'GetTimeZone',
    'get_display' => 'GetDisplay',
    'get_symbol' => 'GetSymbol',
);

$methods_hm = array(
    'is_before_stock_market' => 'IsBeforeStockMarket',
    'is_after_stock_market' => 'IsAfterStockMarket',
);
$hour_minutes = array(100, 359, 400, 800, 859, 900, 914, 915, 1140, 1141, 1200, 1259, 1300,
                      1505, 1506, 1535, 1536, 1605, 1606, 1615, 1616, 1805, 1806, 2005, 2006, 2359);

function norm($v)
{
    if ($v === false)   return null;
    if ($v === true)    return true;
    return $v;
}

$out = array();

foreach ($symbols as $symbol)
{
    $sym = new StockSymbol($symbol);
    foreach ($methods0 as $key_name => $php_method)
    {
        $key = $symbol . '|' . $key_name;
        $out[$key] = norm($sym->$php_method());
    }
    foreach ($methods_hm as $key_name => $php_method)
    {
        foreach ($hour_minutes as $hm)
        {
            $key = $symbol . '|' . $key_name . '|' . $hm;
            $out[$key] = norm($sym->$php_method($hm));
        }
    }
}

// Module level helpers
foreach (array('fx_susdcnh', 'hf_ES', 'znb_NKY', 'AAPL', '') as $s)
{
    $out['StrHasPrefix(' . $s . ',fx_s)'] = norm(StrHasPrefix($s, 'fx_s'));
}

foreach (array('162411', '1624', '16241X', ' 16241', '600000') as $d)
{
    $out['IsChinaStockDigit(' . $d . ')'] = norm(IsChinaStockDigit($d));
}

foreach (array('162411', '513180', '501043', '600000', '000001', '16241') as $d)
{
    $out['BuildChinaFundSymbol(' . $d . ')'] = norm(BuildChinaFundSymbol($d));
}

foreach (array('000001', '300750', '430047', '830799', '600000', '688981', '500000', '700000', '900000') as $d)
{
    $out['BuildChinaStockSymbol(' . $d . ')'] = norm(BuildChinaStockSymbol($d));
}

foreach (array('700', '09988', '00001') as $d)
{
    $out['BuildHongkongStockSymbol(' . $d . ')'] = norm(BuildHongkongStockSymbol($d));
}

$out['BuildYahooNetValueSymbol(SZ162411)'] = norm(BuildYahooNetValueSymbol('SZ162411'));
$out['BuildYahooNetValueSymbol(SZ162411,NV)'] = norm(BuildYahooNetValueSymbol('SZ162411', 'NV'));
$out['BuildYahooNetValueSymbol()'] = norm(BuildYahooNetValueSymbol(''));

$arrays = array(
    'GetSecondaryListingArray', 'ChinaIndexGetAshrArray', 'ChinaIndexGetSz100Array',
    'ChinaIndexGetCoalArray', 'ChinaIndexGetSymbolArray', 'ChinaFutureGetSymbolArray',
    'QdiiGetOilEtfSymbolArray', 'QdiiGetQqqMatchArray', 'QdiiGetQqqSymbolArray',
    'QdiiGetSpyMatchArray', 'QdiiGetSpySymbolArray', 'QdiiGetXopSymbolArray',
    'QdiiGetXbiSymbolArray', 'QdiiGetSymbolArray', 'QdiiHkGetTechSymbolArray',
    'QdiiHkGetHSharesSymbolArray', 'QdiiHkGetHangSengSymbolArray',
    'QdiiHkGetIndexSymbolArray', 'QdiiHkGetSymbolArray', 'QdiiJpGetNkySymbolArray',
    'QdiiJpGetSymbolArray', 'QdiiEuGetDaxSymbolArray', 'QdiiEuGetSymbolArray',
    'GetChinaInternetSymbolArray', 'GetMsciUs50SymbolArray', 'GetHkMixSymbolArray',
    'GetQdiiGoldSymbolArray', 'GetQdiiOilSymbolArray', 'GetLofMixSymbolArray',
    'QdiiMixGetSymbolArray', 'GetAllSymbolArray', 'GetOverNightSymbolArray',
);
foreach ($arrays as $fn)
{
    $out['array(' . $fn . ')'] = $fn();
}

$inArrays = array(
    'in_arrayAshrSymbol', 'in_arrayChinaIndex', 'in_arrayChinaFuture',
    'in_arrayOilEtfQdii', 'in_arrayQqqMatch', 'in_arrayQqqQdii', 'in_arraySpyMatch',
    'in_arraySpyQdii', 'in_arrayXopQdii', 'in_arrayXbiQdii', 'in_arrayQdii',
    'in_arrayTechQdiiHk', 'in_arrayHSharesQdiiHk', 'in_arrayHangSengQdiiHk',
    'in_arrayQdiiHk', 'in_arrayNkyQdiiJp', 'in_arrayQdiiJp', 'in_arrayDaxQdiiEu',
    'in_arrayQdiiEu', 'in_arrayHkMix', 'in_arrayLofMix', 'in_arrayQdiiMix',
    'in_arrayAll',
);
$probeSymbols = array('SH501043', 'SH510300', 'SZ160416', 'SH513100', 'SH513500',
                      'SH513350', 'SZ159502', 'SZ162411', 'SH513180', 'SH510900',
                      'SH501302', 'SH513000', 'SH513030', 'SH513090', 'SZ160644',
                      'SH513360', 'SZ161226', 'SH518880', 'SZ161725', 'AAPL', 'SH999999');
foreach ($inArrays as $fn)
{
    foreach ($probeSymbols as $s)
    {
        $out['inarray(' . $fn . ',' . $s . ')'] = norm($fn($s));
    }
}

// Write to the given path, or to stdout when no path is given. Writing to
// stdout lets a caller capture the reference without needing write access to
// the workspace from inside a sandboxed PHP process.
$json = json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
if (isset($argv[1]) && $argv[1] !== '-') {
    file_put_contents($argv[1], $json);
    fwrite(STDERR, "php cases: " . count($out) . " -> " . $argv[1] . "\n");
} else {
    fwrite(STDERR, "php cases: " . count($out) . "\n");
    echo $json;
}
unlink($tmp);
