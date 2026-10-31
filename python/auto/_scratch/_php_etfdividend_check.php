<?php
/**
 * `woody/res/etfdividendcn.php` 的 PHP 真身探针。
 *
 * 目的：这一页的全部逻辑就是 `_echoEtfDividendData()` 那二十行 ——
 * `StockDividendSql::GetAll($strStockId)` 拿分红、逐行 `StockHistorySql::GetClose()`
 * 拿当天收盘价、取不到就跳过；`rtrim0()` 只作用于分红金额、`number_format(..., 2)`
 * 管比例。这里把**真** `StockDividendSql` / `StockHistorySql` / `rtrim0()` 跑在
 * 真库上吐成 JSON，供 `_php_etfdividend_compare.py` 与 Python 逐格比对。
 *
 * 除了表格本身，还输出 `CountNetValue()` 那一侧的净值条数 —— 它是页面
 * **第二道闸门**（为 0 时 PHP 在 `EchoTableParagraphBegin()` 之前就 return，
 * 连表头都不输出）。不单独验它，就分不清"表空"与"整段不输出"。
 *
 * 用法：
 *
 *     php -d display_errors=stderr _php_etfdividend_check.php [symbol]
 *
 * symbol 默认 QQQ。stdout 只有一个 JSON；调试与 SQL 报错一律走 stderr。
 *
 * ## 为什么能跑起来
 *
 * 与 `_php_compare_check.php` / `_php_deletesymbol_check.php` 同一套路
 * （见 pitfalls #145 / #165）：`TableSql::__construct()` 会 `Create()` 建表、
 * 且把库名硬编码成生产库。在 `SqlDieByQuery()` 里拦掉 `CREATE TABLE` 之后，
 * 所有 `new XxxSql()` 变成纯读写，于是能整份 require 真的 Sql 类。
 *
 * ## 为什么不 require `php/debug.php`
 *
 * `rtrim0()` 住在 `debug.php`，而那个文件第 2 行就 `require_once('url.php')`，
 * 后面还牵连一整串 UI 依赖。这里只照抄 `rtrim0()` 的三行实现
 * （`php/debug.php:60`，实际是 `strval(floatval($str))`）与两个常量，
 * 不值得为两个函数把 UI 层拖进来。
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

// `php/sql.php` / `php/debug.php` 里的常量（那两个文件拉 UI，进不来）—— 值照抄。
define('TABLE_STOCK_DIVIDEND', 'stockdividend');
define('MIN_FLOAT_VAL', 0.0000005);

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

//: 照抄 `php/debug.php:60`（那三行是 `strval(floatval($str))`）。
function rtrim0($str)
{
    return strval(floatval($str));
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
require_once($phpDir . '/sql/sqldailyclose.php');
require_once($phpDir . '/sql/sqlstocksymbol.php');

//: `sql.php` 里那句 `$g_stock_sql = new StockSql();` 的等价物。
InitGlobalStockSql();

// ---------------------------------------------------------------------------
// 照 `_echoEtfDividendData()`（`woody/res/etfdividendcn.php:5-23`）跑一遍
// ---------------------------------------------------------------------------
$strSymbol = isset($argv[1]) ? trim($argv[1]) : 'QQQ';

$arOut = array(
    'symbol'           => $strSymbol,
    'stock_id'         => false,
    'net_value_count'  => 0,
    'rows'             => array(),
    'skipped'          => array(),
    'dividend_rows'    => 0,
);

$stock_id = GetStockSql()->GetId($strSymbol);
if ($stock_id)
{
    $arOut['stock_id'] = intval($stock_id);
    $arOut['net_value_count'] = GetNetValueHistorySql()->Count($stock_id);

    $sql = new StockDividendSql();
    $his_sql = GetStockHistorySql();
    $arOut['dividend_rows'] = $sql->Count($stock_id);
    if ($result = $sql->GetAll($stock_id))
    {
        while ($arDividend = mysqli_fetch_assoc($result))
        {
            $strDate = $arDividend['date'];
            if ($strClose = $his_sql->GetClose($stock_id, $strDate))
            {
                $strDividend = rtrim0($arDividend['close']);
                $ar = array($strDate, $strDividend, $strClose);
                $ar[] = number_format(floatval($strDividend) / floatval($strClose) * 100.0, 2);
                $arOut['rows'][] = $ar;
            }
            else
            {
                $arOut['skipped'][] = $strDate;
            }
        }
        mysqli_free_result($result);
    }
}

$strJson = json_encode($arOut, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
if ($strJson === false) { fwrite(STDERR, 'json_encode 失败' . PHP_EOL); exit(4); }
echo $strJson . PHP_EOL;
