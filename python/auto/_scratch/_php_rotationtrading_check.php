<?php
/**
 * 轮动交易页（`woody/res/rotationtradingcn.php`）的 PHP 真身探针。
 *
 * 目的：这一页的数值全压在三样东西上 ——
 *
 *   1. `_getRotationSymbolArray()` 的四族分派（真 `in_array*` 查表）；
 *   2. `GetStockHedge()`（`php/stock.php:401`）的对冲值，它读**真**
 *      `fundposition` / `calibrationhistory`；
 *   3. `GetNumberDisplay()`（`php/stock.php:111`）的取整与颜色，
 *      以及 `abs(round($fHedge * $fEstQuantity / 100.0) * 100.0)` 那两层取整。
 *
 * 这里把这三段从源码**原样**抠出来跑在真库上，吐成 JSON 供
 * `_php_rotationtrading_compare.py` 与 Python 逐例比对。
 *
 * 用法：
 *
 *     php -d display_errors=stderr _php_rotationtrading_check.php
 *
 * stdout 只有一个 JSON；调试与 SQL 报错一律走 stderr。
 *
 * ## 为什么能跑起来（与 `_php_compare_check.php` 同一套路，见 pitfalls #145）
 *
 * `TableSql::__construct()` 无条件调 `Create()` → `CreateTable()`，后者把库名
 * 硬编码成生产库（pitfalls #131）。在 `SqlDieByQuery()` 里拦掉 `CREATE TABLE`
 * 之后，所有 `new XxxSql()` 都变成纯读写的空操作 —— 于是
 * `php/sql/sqlstocksymbol.php` 可以**整份 require**，`GetPositionSql()` /
 * `GetCalibrationSql()` / `SqlGetStockId()` 全是**真身**。
 *
 * `php/stock/stocksymbol.php` 零依赖（只有数组与 `in_array*`），也可以整份
 * require —— 四个族数组因此是**真的**，不是照抄。
 *
 * ## 注意
 *
 * * 本文件不能出现 PHP 结束标签（注释里也不行）。
 * * `CREATE TABLE` 被拦掉是本文件能跑起来的前提，别"修"成真的执行。
 * * 本脚本**只读**，不写库。
 */

error_reporting(E_ALL);
ini_set('display_errors', 'stderr');

$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录" . PHP_EOL); exit(2); }

// `php/sql.php` 里的常量（那个文件拉 UI，进不来）—— 值照抄。
define('TABLE_MEMBER', 'member');
define('TABLE_STOCK_DIVIDEND', 'stockdividend');
define('TABLE_STOCK_GROUP', 'stockgroup');
define('TABLE_STOCK_GROUP_ITEM', 'stockgroupitem');
define('TABLE_STOCK_SPLIT', 'stocksplit');
define('TABLE_STOCK_TRANSACTION', 'stocktransaction');

// ---------------------------------------------------------------------------
// 桩
// ---------------------------------------------------------------------------
function DebugString($str, $bAdminOnly = false)  { fwrite(STDERR, 'DEBUG ' . $str . PHP_EOL); }
function DebugVal($iVal, $strPrefix = false, $bAdminOnly = false)
{
    fwrite(STDERR, 'DEBUG ' . ($strPrefix === false ? '' : $strPrefix . ' ') . $iVal . PHP_EOL);
}

function die_mysql_error($str)
{
    global $g_link;
    $str .= ' ' . mysqli_error($g_link);
    fwrite(STDERR, 'SQL FAIL ' . $str . PHP_EOL);
    die($str);
}

//: **本文件能跑起来的关键**：拦掉建表（见文件头）。
function SqlDieByQuery($strQry, $strDie)
{
    global $g_link;

    if (stripos(ltrim($strQry), 'CREATE TABLE') === 0)    return true;
    if ($result = mysqli_query($g_link, $strQry))        return true;
    fwrite(STDERR, 'SQL FAIL: ' . $strDie . ' -> ' . mysqli_error($g_link) . PHP_EOL);
    return false;
}

function SqlGetTableData($strTableName, $strWhere = false, $strOrder = false, $strLimit = false)
{
    global $g_link;

    $strQry = 'SELECT * FROM ' . $strTableName . _SqlAddWhere($strWhere) . _SqlAddOrder($strOrder) . _SqlAddLimit($strLimit);
    if ($result = mysqli_query($g_link, $strQry))
    {
        if (mysqli_num_rows($result) > 0)   return $result;
    }
    else
    {
        die_mysql_error($strTableName . ' query data by ' . $strQry . ' failed');
    }
    return false;
}

function SqlGetSingleTableData($strTableName, $strWhere = false, $strOrder = false)
{
    global $g_link;

    $strQry = 'SELECT * FROM ' . $strTableName . _SqlAddWhere($strWhere) . _SqlAddOrder($strOrder) . _SqlAddLimit('1');
    if ($result = mysqli_query($g_link, $strQry))
    {
        if (mysqli_num_rows($result) == 1)
        {
            $record = mysqli_fetch_assoc($result);
            mysqli_free_result($result);
            return $record;
        }
    }
    else
    {
        die_mysql_error($strTableName . ' query single data by ' . $strQry . ' failed');
    }
    return false;
}

function SqlCountTableData($strTableName, $strWhere = false)
{
    global $g_link;

    $strQry = "SELECT count(*) as total FROM $strTableName" . _SqlAddWhere($strWhere);
    if ($result = mysqli_query($g_link, $strQry))
    {
        $record = mysqli_fetch_array($result);
        if (isset($record['total']))        return intval($record['total']);
    }
    return 0;
}

function SqlDeleteTableData($strTableName, $strWhere, $strLimit = false)
{
    if ($strWhere)
    {
        $strQry = 'DELETE FROM ' . $strTableName . _SqlAddWhere($strWhere) . _SqlAddLimit($strLimit);
        return SqlDieByQuery($strQry, $strTableName . ' delete table data by ' . $strQry . ' failed');
    }
    return false;
}

function SqlDeleteTableDataById($strTableName, $strId)
{
    return SqlDeleteTableData($strTableName, _SqlBuildWhere_id($strId), '1');
}

// -- php/ui/htmlelement.php:28-69 原样（色彩只在 <font> 里，本对照比的是文本与颜色名）--
function GetDoubleQuotes($str)            { return '"' . $str . '"'; }
function GetHtmlElement($strContent, $strTag = 'p', $arAttribute = false)
{
	$strStart = $strTag;
	if ($arAttribute)
	{
		foreach ($arAttribute as $strAttribute => $strValue)
		{
			$strStart .= ' ' . $strAttribute;
			if ($strValue !== false)		$strStart .= '=' . $strValue;
		}
	}
	return "<$strStart>$strContent</$strTag>";
}
function GetFontElement($strContent, $strColor = 'red', $strStyle = false)
{
	$ar = array('color' => $strColor);
	if ($strStyle)	$ar['style'] = GetDoubleQuotes($strStyle);
	return GetHtmlElement($strContent, 'font', $ar);
}

// -- php/stock.php:91-114 原样 ----------------------------------------------
function StockGetPriceDisplay($fDisp, $fPrev, $iPrecision, $strGreater = 'red', $strLess = 'green')
{
	$iDiff = 0.5;
	$iCur = $iPrecision;
	while ($iCur > 0)
	{
		$iDiff /= 10.0;
		$iCur --;
	}

    if ($fDisp > $fPrev + $iDiff)         $strColor = $strGreater;
    else if ($fDisp < $fPrev - $iDiff)    $strColor = $strLess;
    else                                  $strColor = 'grey';
	$strDisp = number_format($fDisp, $iPrecision, '.', '');
	if ($iPrecision > 0)	$strDisp = rtrim($strDisp, '0');
	$strDisp = rtrim($strDisp, '.');
    if ($strColor != 'black')	return GetFontElement($strDisp, $strColor);
	return $strDisp;
}

function GetNumberDisplay($fVal, $iPrecision = 2, $strGreater = 'black', $strLess = 'red')
{
    return StockGetPriceDisplay($fVal, 0.0, $iPrecision, $strGreater, $strLess);
}

// -- php/stock.php:389-438 原样（GetStockHedge 与它的两个零件）--------------
function StockCalcHedge($fCalibration, $fPos)
{
	return $fCalibration / $fPos;
}

function GetLeverageHedgeSymbol($strSymbol)
{
	if (in_arraySpyQdii($strSymbol))	return 'SPY';
	if (in_arrayQqqQdii($strSymbol))	return 'QQQ';
    return false;
}

function GetStockHedge($strSymbol, $strStockId, $strLev = false)
{
	$pos_sql = GetPositionSql();
	$fPos = $pos_sql->ReadPos($strStockId);
	$cal_sql = GetCalibrationSql();
	if ($strLev || ($strLev = GetLeverageHedgeSymbol($strSymbol)))
	{
		$strLevId = SqlGetStockId($strLev);
	if ($result = $cal_sql->GetAll($strStockId))
	{
		while ($record = mysqli_fetch_assoc($result))
		{
				$strDate = $record['date'];
				if ($strLevCal = $cal_sql->GetClose($strLevId, $strDate))
				{
				mysqli_free_result($result);
					return StockCalcHedge(floatval($record['close']), $fPos) / StockCalcHedge(floatval($strLevCal), $pos_sql->ReadPos($strLevId));
				}
			}
			mysqli_free_result($result);
	}
    }
	else
	{
		if ($strCal = $cal_sql->GetCloseNow($strStockId))
	{
			return StockCalcHedge(floatval($strCal), $fPos);
		}
	}
	return 1.0;
}

// -- woody/res/rotationtradingcn.php:61-70 原样 -----------------------------
function _getRotationSymbolArray($strSymbol)
{
    if (in_arrayXopQdii($strSymbol))		return QdiiGetXopSymbolArray();
	else if (in_arrayXbiQdii($strSymbol))	return QdiiGetXbiSymbolArray();
	else if (in_arrayQqqMatch($strSymbol))	return QdiiGetQqqMatchArray();
	else if (in_arraySpyMatch($strSymbol))	return QdiiGetSpyMatchArray();
    return false;
}

// ---------------------------------------------------------------------------
// 连库 + 装载真身
// ---------------------------------------------------------------------------
$g_link = mysqli_connect(
    getenv('PALMMICRO_DB_HOST') ?: '127.0.0.1',
    getenv('PALMMICRO_DB_USER') ?: 'root',
    getenv('PALMMICRO_DB_PASSWORD'),
    getenv('PALMMICRO_DB_DATABASE') ?: 'stock_data',
    intval(getenv('PALMMICRO_DB_PORT') ?: 3306)
);
if (!$g_link) { fwrite(STDERR, '连库失败: ' . mysqli_connect_error() . PHP_EOL); exit(3); }
mysqli_set_charset($g_link, 'utf8');

set_include_path(
    $phpDir . DIRECTORY_SEPARATOR . 'sql' . PATH_SEPARATOR
    . $phpDir . DIRECTORY_SEPARATOR . 'class' . PATH_SEPARATOR
    . get_include_path()
);
require_once($phpDir . '/class/year_month_day.php');
require_once($phpDir . '/sql/_sqlcommon.php');
require_once($phpDir . '/sql/sqlstocksymbol.php');
require_once($phpDir . '/stock/stocksymbol.php');

InitGlobalStockSql();

// ---------------------------------------------------------------------------
// 跑
// ---------------------------------------------------------------------------
$arOut = array('groups' => array(), 'hedges' => array(), 'number_display' => array(), 'hints' => array());

//: 四族各取一个代表 + 三个**故意落在族外/边界**的符号。
//: `SZ159655` 是"在 SpyQdii 里但不在 SpyMatch 里"的那个 —— PHP 原样会返回 false，
//: Python 侧刻意放宽，所以它必须出现在对照里，差异才看得见。
$arSymbol = array(
    'SH513100', 'SH513500', 'SH513350', 'SZ159502',   // 四族各一
    'SZ159655', 'SH513300', 'SPY', 'SZ164906', 'NOSUCHSYMBOL', '',
);

foreach ($arSymbol as $strSymbol)
{
    $ar = _getRotationSymbolArray($strSymbol);
    $arOut['groups'][$strSymbol] = ($ar === false)
        ? null
        : array('symbols' => array_values($ar), 'leverage' => GetLeverageHedgeSymbol($strSymbol));
}

//: 对冲值：两个族各取一批。`SZ159655` 单独一条（它走 SpyQdii 分支）。
$arHedgeSymbol = array_merge(
    array('SH513100', 'SH513110', 'SZ161130', 'SZ159655'),
    array_values(QdiiGetSpyMatchArray())
);
foreach ($arHedgeSymbol as $strSymbol)
{
    if ($strId = SqlGetStockId($strSymbol))
    {
        $arOut['hedges'][$strSymbol] = array(
            'stock_id' => strval($strId),
            'hedge'    => GetStockHedge($strSymbol, $strId),
        );
    }
}

//: `GetNumberDisplay($v, 0)` 的文本与颜色（颜色从 `<font color=xxx>` 里取）。
foreach (array(-409.49, 2126.909, 2442.027, 0.4, -0.4, 0.0, 871000.0, -0.5, 0.5) as $fVal)
{
    $str = GetNumberDisplay($fVal, 0);
    $strColor = '';
    if (preg_match('/color=([a-z]+)/', $str, $arMatch))    $strColor = $arMatch[1];
    $arOut['number_display'][] = array(
        'value' => $fVal,
        'text'  => strip_tags($str),
        'color' => $strColor,
    );
}

//: 建议数量的两层取整：`abs(round($fHedge * $fEstQuantity / 100.0) * 100.0)`。
foreach (array(
    array(1149.3, -409.49),
    array(2126.909, -409.49),
    array(2107.699, -577.1),
    array(0.0, -100.0),
    array(1000.0, 500.0),
) as $arPair)
{
    $fHedge = $arPair[0];
    $fEstQuantity = $arPair[1];
    $fHint = abs(round($fHedge * $fEstQuantity / 100.0) * 100.0);
    $arOut['hints'][] = array(
        'hedge'        => $fHedge,
        'est_quantity' => $fEstQuantity,
        'text'         => strval($fHint),
    );
}

echo json_encode($arOut, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
