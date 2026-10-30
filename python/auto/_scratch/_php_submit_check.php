<?php
/**
 * submitdividend.php / submitnetvalue.php 的 PHP 真身探针。
 *
 * 目的：把 PHP 那两个函数**原样**跑一遍（不重写逻辑），看它对同一份样本
 * 吐出来的 (日期, 值) 是什么，供 _php_submit_compare.py 与 Python 逐例比对。
 *
 * 做法：
 *  1. 只 define 需要的常量，不 require 整套框架；
 *  2. 从 woody/res/submitdividend.php **抠出两个函数的源码**再 eval
 *     （文件末尾有 $acct = new _AdminDividendAccount(); $acct->AdminRun();
 *     不能整份 require）；
 *  3. StockDebugJson / StockSaveDebugFile 换成"直接返回 fixture"的桩；
 *  4. $sql 换成记录器，WriteDaily 只把 (date, close) 收进数组。
 *
 * 输出：stdout 只有一个 JSON，调试信息一律走 stderr。
 */

error_reporting(E_ALL);
ini_set('display_errors', 'stderr');

define('MIN_FLOAT_VAL', 0.0000005);          // php/debug.php:11
define('SECONDS_IN_MIN', 60);                // php/debug.php
define('DEBUG_TIME_ZONE', 'Asia/Shanghai');  // php/debug.php:4
define('STOCK_DISP_NETVALUE', '单位净值');
define('STOCK_DISP_ETF', 'ETF');

$g_strFixtureDir = __DIR__.'/_fixtures/';

// ---------------------------------------------------------------------------
// 桩：PHP 原函数的依赖
// ---------------------------------------------------------------------------
$g_strJsonFixture = '';

function GetSpdrUrl()       { return 'https://www.ssga.com/'; }
function GetProsharesUrl()  { return 'https://www.proshares.com/'; }
function DebugString($str)  { fwrite(STDERR, $str."\n"); }
function DebugPrint($ar)    { }

function StockDebugJson($strPathName, $strUrl, $iInterval = 60, $arExtraHeaders = false, $strFileName = false)
{
    global $g_strJsonFixture;
    $str = @file_get_contents($g_strJsonFixture);
    if ($str === false)    return false;
    return json_decode($str, true);
}

// php/debug.php:93 / 102 —— 只看 Y-m-d，时区取 DEBUG_TIME_ZONE
function DebugFormat_date($strFormat, $iTime = false, $strTimeZone = DEBUG_TIME_ZONE)
{
    if ($strTimeZone)    date_default_timezone_set($strTimeZone);
    return date($strFormat, ($iTime ? $iTime : time()));
}

function DebugGetDate($iTime = false, $strTimeZone = DEBUG_TIME_ZONE)
{
    return DebugFormat_date('Y-m-d', $iTime, $strTimeZone);
}

// php/debug.php:16
function IsZeroString($strZero)
{
    return (abs(floatval($strZero)) < MIN_FLOAT_VAL) ? true : false;
}

//: 记录 WriteDaily 的假 Sql
class RecordingSql
{
    public $rows = array();

    function WriteDaily($strStockId, $strDate, $strClose)
    {
        $this->rows[] = array('date' => $strDate, 'close' => $strClose);
        return true;
    }
}

// ---------------------------------------------------------------------------
// 从源文件里抠函数
// ---------------------------------------------------------------------------
function ExtractFunction($strFile, $strName)
{
    $strSource = file_get_contents($strFile);
    $iStart = strpos($strSource, 'function '.$strName.'(');
    if ($iStart === false)    throw new Exception('找不到函数 '.$strName);
    $iBrace = strpos($strSource, '{', $iStart);
    $iDepth = 0;
    for ($i = $iBrace; $i < strlen($strSource); $i++)
    {
        $c = $strSource[$i];
        if ($c == '{')    $iDepth++;
        else if ($c == '}')
        {
            $iDepth--;
            if ($iDepth == 0)    return substr($strSource, $iStart, $i - $iStart + 1);
        }
    }
    throw new Exception('括号不配平：'.$strName);
}

$strDividendFile = '../../../woody/res/submitdividend.php';
eval(ExtractFunction($strDividendFile, '_updateSpdrEtfDividend'));
eval(ExtractFunction($strDividendFile, '_updateProsharesEtfDividend'));

// ---------------------------------------------------------------------------
// ① 分红：SPDR 与 Proshares 各跑一遍
// ---------------------------------------------------------------------------
$arResult = array();

$g_strJsonFixture = $g_strFixtureDir.'spdr-dividend-xop.json';
$sql = new RecordingSql();
_updateSpdrEtfDividend(5, 'XOP', '/tmp/ignored', $sql);
$arResult['spdr_dividend'] = $sql->rows;

$g_strJsonFixture = $g_strFixtureDir.'proshares-dividend-sample.json';
$sql = new RecordingSql();
_updateProsharesEtfDividend(17, 'TQQQ', '/tmp/ignored', $sql);
$arResult['proshares_dividend'] = $sql->rows;

// ---------------------------------------------------------------------------
// ② 净值：Invesco JSON（函数在 _updateinvesconetvalue.php 里，逻辑很短，
//    这里照抄那 6 行 —— 它没有可 eval 的边界，整段 require 会带进一堆依赖）
// ---------------------------------------------------------------------------
$g_strJsonFixture = $g_strFixtureDir.'invesco-nav-qqq.json';
$ar = StockDebugJson('/tmp/ignored', 'https://example.invalid/');
$arResult['invesco_nav'] = array(
    'effectiveDate' => $ar['effectiveDate'],
    'nav'           => $ar['nav'],
);

// ---------------------------------------------------------------------------
// ③ 三个"原语"：日期解析与 0 判定
// ---------------------------------------------------------------------------
$arResult['primitives'] = array(
    // SPDR 的 m/d/Y
    'spdr_date_09212026' => DebugGetDate(strtotime('09/21/2026'), false),
    'spdr_date_12222025' => DebugGetDate(strtotime('12/22/2025'), false),
    // xlsx 的 d-M-Y
    'xls_date_29Oct2026' => DebugGetDate(strtotime('29-Oct-2026'), false),
    'xls_date_31Dec2013' => DebugGetDate(strtotime('31-Dec-2013'), false),
    // 表头与免责声明：strtotime 失败 → 0
    'xls_header_tick'    => strtotime('Date'),
    // Proshares 的 ISO 切片
    'proshares_slice'    => substr('2024-12-23T00:00:00', 0, 10),
    // IsZeroString
    'zero_dash'          => IsZeroString('-'),
    'zero_null_text'     => IsZeroString('null'),
    'zero_0'             => IsZeroString('0'),
    'zero_769'           => IsZeroString('769.36'),
    // mysql_round（_readXlsFile 与 UpdateYahooHistoryChart 都用它）
    'round_769_506335'   => strval(round(floatval('769.506335'), 6)),
    'round_769_36'       => strval(round(floatval('769.36'), 6)),
    // 流通股数除以 10000
    'shares_div'         => strval(floatval('1057282116') / 10000.0),
);

echo json_encode($arResult, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
