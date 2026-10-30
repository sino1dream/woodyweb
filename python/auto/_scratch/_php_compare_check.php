<?php
/**
 * AB / AH / ADR 对比四页的 PHP 真身探针。
 *
 * 目的：这三个对比页（`woody/res/ahcomparecn.php` 一族）内容全部来自
 * `_mystockgroup.php::_echoStockGroupArray(GetCategoryArray($strPage))`，而
 * `GetCategoryArray()` 的 `abcompare` / `adrhcompare` / `ahcompare` 三个分支
 * 只是 `GetXxxPairSql()->GetSymbolArray()` —— 所以"数组对不对"就是这一批页面
 * 数组侧的全部逻辑。这里把真 `StockPairSql::GetSymbolArray()` 跑在真库上，
 * 吐成 JSON 供 `_php_compare_compare.py` 与 Python 逐例比对。
 *
 * `fundpair` 也一并跑：`abpair` / `ahpair` / `adrpair` 在演示库里是 **0 行**，
 * 只拿它们对"空"这一个值没有说服力；`fundpair` 有 76 行、走的是**同一个**
 * `StockPairSql::GetSymbolArray()`，排序与"id 查不到符号"两条分支只有它验得到。
 *
 * 用法：
 *
 *     php -d display_errors=stderr _php_compare_check.php
 *
 * stdout 只有一个 JSON；调试与 SQL 报错一律走 stderr。
 *
 * ## 为什么能跑起来（与 `_php_deletesymbol_check.php` 同一套路，见 pitfalls #145）
 *
 * `TableSql::__construct()` 无条件调 `Create()` → `CreateTable()`，后者把库名
 * 硬编码成生产库（pitfalls #131）。在 `SqlDieByQuery()` 里拦掉 `CREATE TABLE`
 * 之后，所有 `new XxxSql()` 都变成纯读写的空操作 —— 于是
 * `php/sql/sqlstocksymbol.php` 可以**整份 require**，`GetAbPairSql()` /
 * `GetStockSql()->GetId()` 全是**真身**。
 *
 * ## 注意
 *
 * * 本文件不能出现 PHP 结束标签（注释里也不行）。
 * * `CREATE TABLE` 被拦掉是本文件能跑起来的前提，别"修"成真的执行。
 * * `$_SERVER['argv']` 之外的输入一律不读 —— 本脚本**只读**，不写库。
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
require_once($phpDir . '/../woody/res/php/_stocklink.php');

// `$g_stock_sql` 的真身就是 `sql.php` 里那句 `$g_stock_sql = new StockSql();`。
// CREATE TABLE 被拦掉后，StockSql 里那十个子 Sql 全是空操作，可以直接建。
InitGlobalStockSql();

// ---------------------------------------------------------------------------
// 跑
// ---------------------------------------------------------------------------

//: 四张配对表。**键名与 Python `PAIR_STORES` 对齐**（ab/ah/adr 是页面用的三张，
//: fundpair 只是拿来验排序分支的"有数据的同款"）。
$arSql = array(
    'abpair'   => GetAbPairSql(),
    'ahpair'   => GetAhPairSql(),
    'adrpair'  => GetAdrPairSql(),
    'fundpair' => GetFundPairSql(),
);

//: 用来验 `GetSymbolArray($strPairSymbol)` 那支的配对符号。前两个在 fundpair
//: 里是"被配对"的那一侧（查得到），后两个是根本不在配对表里的（查不到 → 空数组）。
$arPairSymbol = array('SZ161125', 'SH513100', 'QQQ', '^GSPC', 'SH', 'SZ161130', 'NOSUCHSYMBOL', '');

$arOut = array('tables' => array(), 'by_pair' => array(), 'categories' => array());

//: 页面真正的入口：`GetCategoryArray()` 的三个 compare 分支。它与上面的
//: `GetSymbolArray()` 是同一条路径，但对端要验的是**页面**拿到的数组，
//: 所以单独吐一份（万一哪天 PHP 在分支里加了过滤，这里能看出来）。
foreach (array('abcompare', 'ahcompare', 'adrhcompare') as $strPage)
{
    $ar = GetCategoryArray($strPage);
    $arOut['categories'][$strPage] = array(
        'count'   => count($ar),
        'symbols' => array_values($ar),
    );
}

foreach ($arSql as $strTable => $pair_sql)
{
    $ar = $pair_sql->GetSymbolArray();
    // array_values：PHP 数组键是 0..n，json 里要保持数组形状
    $arOut['tables'][$strTable] = array(
        'count'   => count($ar),
        'symbols' => array_values($ar),
    );
}

foreach ($arPairSymbol as $strPairSymbol)
{
    $arOut['by_pair'][$strPairSymbol] = array();
    foreach ($arSql as $strTable => $pair_sql)
    {
        $ar = $pair_sql->GetSymbolArray($strPairSymbol);
        $arOut['by_pair'][$strPairSymbol][$strTable] = array(
            'count'   => count($ar),
            'symbols' => array_values($ar),
        );
    }
}

echo json_encode($arOut, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
