<?php
// _onAddFundPurchase / adjust 三个纯计算的逐例对照 harness。
// 对应 Python：core/submittransaction.py 的 stock_get_fund_fee_ratio /
// get_arbitrage_quantity，以及 adjust 分支里那句 strval(floatval...)。
// 真实源码切片 + 假 $_POST，输出 JSON 与 Python 逐键比对。
//
// 用法：/d/xampp/php/php -n -d display_errors=stderr _php_submittransaction_check.php <用例>

$ROOT = dirname(__FILE__).'/../../..';

// ---- 从真实源码切片 ------------------------------------------------------
// 与 _php_stocktrans_check.php 同款的花括号配对切片。

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

// GetStockHedge 要连库查对冲比率；本用例只比公式，shim 成 1.0
// （Python 侧 hedge_of=None 同样是 1.0）。
function GetStockHedge($strSymbol, $strStockId)
{
    return 1.0;
}

$strStock = file_get_contents($ROOT.'/woody/res/php/_stock.php');
$strComm = file_get_contents($ROOT.'/php/ui/stockcomm.php');

$arFunctions = array();
foreach (array('StockGetFundFeeRatio') as $strName)
{
    if ($str = ExtractFunction($strStock, $strName))    $arFunctions[] = $str;
}
foreach (array('GetArbitrageQuantity') as $strName)
{
    if ($str = ExtractFunction($strComm, $strName))    $arFunctions[] = $str;
}
eval(implode("\n\n", $arFunctions));

// ---- 用例 ------------------------------------------------------------------
$strCase = isset($argv[1]) ? $argv[1] : 'fee';

if ($strCase === 'fee')
{
    // 覆盖 switch 的每一档 + 未列出的默认值
    $arSymbols = array('SZ160416', 'SZ161116', 'SZ161226', 'SH501018',
                       'SZ161125', 'SZ161126', 'SZ161127', 'SZ161128',
                       'SZ161129', 'SZ161130', 'SZ162719', 'SZ164824',
                       'SZ164906', 'SZ161815', 'SZ999999', 'SH513100',
                       'GLD', '');
    $arOut = array();
    foreach ($arSymbols as $iIndex => $strSymbol)
    {
        $arOut['v'.$iIndex] = StockGetFundFeeRatio($strSymbol);
    }
    echo json_encode($arOut), "\n";
}
else if ($strCase === 'arb')
{
    // GetArbitrageQuantity($strSymbol, $strStockId, $fQuantity)
    $arVals = array(1000.0, 1234567.0, 0.0, -500.0, 99.5, 1.0,
                    1000000.0, 3.14159, 250.0, -1.0);
    $arOut = array();
    foreach ($arVals as $iIndex => $fVal)
    {
        $arOut['v'.$iIndex] = GetArbitrageQuantity('SZ161125', '501', $fVal);
    }
    echo json_encode($arOut), "\n";
}
else if ($strCase === 'adjust')
{
    // _submittransaction.php:244 的 adjust 分支：
    //   strval(floatval($record['quantity']) * floatval($record['price'])
    //         / floatval($strNetValue))
    // 内联在 Process 里没法切片，照原式逐字复算。
    $arCases = array(
        array('1000', '1.65', '1.10'),
        array('1000', '1.65', '1.5'),
        array('-500', '1.72', '2.0'),
        array('100', '0.0', '1.0'),
        array('', '1.65', '1.0'),
        // 净值为 0 的一档**不比**：PHP 8 的 ``/`` 直接抛
        // DivisionByZeroError，Python 侧同款 ZeroDivisionError ——
        // 两边都不设防，表单校验该在更前面挡住。
        array('123456', '0.1234', '0.0567'),
        array('7', '3', '7'),
    );
    $arOut = array();
    foreach ($arCases as $iIndex => $ar)
    {
        $arOut['v'.$iIndex] = strval(floatval($ar[0]) * floatval($ar[1])
                                     / floatval($ar[2]));
    }
    echo json_encode($arOut), "\n";
}
else
{
    fwrite(STDERR, "未知用例: $strCase\n");
    exit(1);
}
