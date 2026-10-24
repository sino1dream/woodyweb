<?php
// _getStockQuantity / _getStockCost / strval_round 的逐例对照 harness。
// 对应 Python：core/stocktrans.py 的 get_stock_quantity / get_stock_cost /
// strval_round。真实源码切片 + 假 $_POST，输出 JSON 与 Python 逐键比对。
//
// 用法：/d/xampp/php/php -n -d display_errors=stderr _php_stocktrans_check.php <用例>

// ---- 常量与 shim（全局命名空间，与 eval 出来的代码一致） -------------------
// MIN_FLOAT_VAL：PHP 在 php/stock.php 里 define（值 5e-7）。
if (!defined('MIN_FLOAT_VAL'))    define('MIN_FLOAT_VAL', 0.0000005);

// StockSymbol 用到的前缀常量（php/stock/stocksymbol.php:54-66 整段抄）。
foreach (array(
    'SINA_FOREX_PREFIX' => 'fx_s', 'SINA_FUTURE_PREFIX' => 'hf_',
    'SINA_CN_FUTURE_PREFIX' => 'nf_', 'SINA_FUND_PREFIX' => 'f_',
    'SINA_INDEX_PREFIX' => 'znb_', 'SINA_HK_PREFIX' => 'rt_hk',
    'SINA_US_PREFIX' => 'gb_',
    'BJ_PREFIX' => 'BJ', 'SH_PREFIX' => 'SH', 'SZ_PREFIX' => 'SZ',
    'YAHOO_INDEX_CHAR' => '^',
) as $strName => $strValue)
{
    if (!defined($strName))    define($strName, $strValue);
}

// SqlCleanString（php/sql.php:147）= UrlCleanString(trim) + mysqli 转义。
// Python 侧参数化查询不做转义，harness 同样只 trim，保持两边一致。
function SqlCleanString($str)
{
    return trim($str);
}

// GetGroupItemSym（_edittransactionform.php:5）：默认费率分支要用它解出
// 符号对象。harness 不连库，按 groupitem_id 从预设表取符号名。
function GetGroupItemSym($strGroupItemId)
{
    $ar = array(
        '101' => 'hf_NQ', '102' => 'fx_susdcnh', '103' => 'SZ161125',
        '104' => 'SZ000001', '105' => '00700', '106' => 'GLD',
    );
    $strSymbol = isset($ar[$strGroupItemId]) ? $ar[$strGroupItemId] : 'GLD';
    return new \StockSymbol($strSymbol);
}

// ---- 从真实源码切片 ------------------------------------------------------
// 与 _php_etfholdings_check.php 同款的花括号配对切片。

function ExtractFunction($strCode, $strName)
{
    $iPos = strpos($strCode, 'function '.$strName.'(');
    if ($iPos === false)    return false;
    $iBrace = strpos($strCode, '{', $iPos);
    if ($iBrace === false)    return false;
    $iDepth = 0;
    for ($i = $iBrace; $i < strlen($strCode); $i++)
    {
        $ch = $strCode[$i];
        if ($ch == '{')    $iDepth++;
        else if ($ch == '}')
        {
            $iDepth--;
            if ($iDepth == 0)
            {
                return substr($strCode, $iPos, $i - $iPos + 1);
            }
        }
    }
    return false;
}

function ExtractClass($strCode, $strName)
{
    $iPos = strpos($strCode, 'class '.$strName);
    if ($iPos === false)    return false;
    // 类声明可能 extends；找类名之后的第一个 '{'（跳过继承列表）
    $iBrace = strpos($strCode, '{', $iPos);
    if ($iBrace === false)    return false;
    // 但要排除属性默认值是数组的情况：类体第一个 '{' 一定在继承列表之后、
    // 第一个属性之前，中间不会出现 '{'
    $iDepth = 0;
    for ($i = $iBrace; $i < strlen($strCode); $i++)
    {
        $ch = $strCode[$i];
        if ($ch == '{')    $iDepth++;
        else if ($ch == '}')
        {
            $iDepth--;
            if ($iDepth == 0)
            {
                return substr($strCode, $iPos, $i - $iPos + 1);
            }
        }
    }
    return false;
}

$strStockTrans = file_get_contents(dirname(__FILE__).'/../../../php/stock/stocksymbol.php');
$strDebug = file_get_contents(dirname(__FILE__).'/../../../php/debug.php');
$strSubmit = file_get_contents(dirname(__FILE__).'/../../../woody/res/php/_submittransaction.php');

$arFunctions = array();
foreach (array('strval_round') as $strName)
{
    if ($str = ExtractFunction($strDebug, $strName))    $arFunctions[] = $str;
}
foreach (array('_getStockQuantity', '_getStockCost') as $strName)
{
    if ($str = ExtractFunction($strSubmit, $strName))    $arFunctions[] = $str;
}
$arClasses = array();
foreach (array('StockSymbol') as $strName)
{
    if ($str = ExtractClass($strStockTrans, $strName))    $arClasses[] = $str;
}

// stocksymbol.php 里 StockSymbol 用到的自由函数（同文件切片）
$arSymbolFunctions = array();
$arNames = array(
    'StrHasPrefix', 'GetSecondaryListingArray',
    'QdiiGetSymbolArray', 'QdiiGetXbiSymbolArray', 'QdiiGetXopSymbolArray',
    'QdiiGetOilEtfSymbolArray', 'QdiiGetQqqSymbolArray', 'QdiiGetSpySymbolArray',
    'QdiiHkGetIndexSymbolArray', 'QdiiHkGetSymbolArray',
    'QdiiHkGetTechSymbolArray', 'QdiiHkGetHSharesSymbolArray',
    'QdiiHkGetHangSengSymbolArray',
    'IsChinaStockDigit', 'StockGetChinaStockSymbol', 'BuildChinaStockSymbol',
    'ConvertHolding', 'StockCheckSymbol',
    '_isDigitShenZhenEtf', '_isDigitShenZhenLof', '_isDigitShenZhenB',
    '_isDigitShenZhenGem', '_isDigitShenZhenIndex', '_isDigitShangHaiIndex',
    '_isDigitShangHaiEtf', '_isDigitShangHaiLof', '_isDigitShangHaiStar',
    '_isDigitShangHaiB', '_isDigitShenZhenFund', '_isDigitShangHaiFund',
);
foreach ($arNames as $strName)
{
    if ($str = ExtractFunction($strStockTrans, $strName))    $arSymbolFunctions[] = $str;
}

$strHarness = '';
foreach ($arClasses as $strBody)        { $strHarness .= $strBody."\n\n"; }
foreach ($arSymbolFunctions as $strBody) { $strHarness .= $strBody."\n\n"; }
foreach ($arFunctions as $strBody)       { $strHarness .= $strBody."\n\n"; }
eval($strHarness);

// ---- 用例 ------------------------------------------------------------------
$strCase = isset($argv[1]) ? $argv[1] : 'cost';

if ($strCase === 'quantity')
{
    $arOut = array();
    $arCases = array(
        'buy'        => array('quantity' => '100', 'type' => '1'),
        'sell'       => array('quantity' => '100', 'type' => '0'),
        'spaced'     => array('quantity' => ' 100 ', 'type' => '0'),
        'empty_sell' => array('quantity' => '', 'type' => '0'),
        'no_type'    => array('quantity' => '100'),
        'zero_type'  => array('quantity' => '50', 'type' => '0'),
    );
    foreach ($arCases as $strName => $arPost)
    {
        $_POST = $arPost;
        $arOut[$strName] = _getStockQuantity();
    }
    echo json_encode($arOut), "\n";
}
else if ($strCase === 'cost')
{
    $arOut = array();
    // [名称, groupitem_id, quantity, price, post 附加]
    $arCases = array(
        'future'          => array('101', '10', '100', array()),
        'forex'           => array('102', '1000', '7.1', array()),
        'fund_a'          => array('103', '1000', '1.65', array()),
        'a_buy'           => array('104', '1000', '10', array()),
        'a_sell'          => array('104', '-1000', '10', array()),
        'a_buy_min'       => array('104', '100', '10', array()),
        'hk'              => array('105', '500', '300', array()),
        'us_small'        => array('106', '100', '300', array()),
        'us_big'          => array('106', '500', '300', array()),
        'us_sell'         => array('106', '-500', '300', array()),
        'explicit'        => array('106', '100', '300', array('commission' => '3')),
        'explicit_permil' => array('106', '100', '300', array('commission' => '1', 'commissionselect' => '1')),
        'with_tax'        => array('106', '100', '300', array('commission' => '1', 'commissionselect' => '1', 'taxselect' => '1', 'tax' => '0.5')),
        'commission_zero' => array('106', '100', '300', array('commission' => '0')),
        'tax_no_select'   => array('106', '100', '300', array('commission' => '1')),
    );
    foreach ($arCases as $strName => $arArgs)
    {
        list($strItemId, $strQuantity, $strPrice, $arExtra) = $arArgs;
        $_POST = array_merge($arExtra, array(
            'quantity' => $strQuantity, 'price' => $strPrice, 'type' => '1',
        ));
        // _getStockCost 只读 commission/commissionselect/tax/taxselect
        $arOut[$strName] = _getStockCost($strItemId, $strQuantity, $strPrice);
    }
    echo json_encode($arOut), "\n";
}
else if ($strCase === 'round')
{
    $arOut = array();
    $arVals = array(0.62, 15.0, 0.165, 5.0, 300.0, 1.0, 2.5, 6.7, 45.0,
                    1.5, 15.0, 150.0, 0.1255, 2.6755, 0.045, 1.005,
                    -0.004, 0.0);
    foreach ($arVals as $iIndex => $fVal)
    {
        // 用索引键而不是 'p3_'.$fVal —— PHP 的 float 转串会丢 '.0'
        // （150.0 -> '150'），键形状不是被测语义。
        $arOut['v'.$iIndex] = array(strval_round($fVal, 3), strval_round($fVal));
    }
    echo json_encode($arOut), "\n";
}
else
{
    fwrite(STDERR, "未知用例: $strCase\n");
    exit(1);
}

