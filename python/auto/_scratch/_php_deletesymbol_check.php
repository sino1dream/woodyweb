<?php
/**
 * deletesymbol.php 的 PHP 真身探针。
 *
 * 目的：把 `woody/res/deletesymbol.php` 里的 `_deleteStockSymbol()` **原样**跑
 * 在一个一次性符号上，看它到底删了哪几张表、拦在哪道闸门。供
 * `_php_deletesymbol_compare.py` 与 Python 的 `deletesymbol.apply()` 逐例比对。
 *
 * 用法：
 *
 *     PALMMICRO_DB_PASSWORD=... php -d display_errors=stderr \
 *         _php_deletesymbol_check.php <symbol>
 *
 * stdout 只有一个 JSON；调试与 SQL 报错一律走 stderr。
 *
 * ## 为什么能跑起来（与 `_php_dividend_split_check.php` 同一套路，见 pitfalls #145）
 *
 * `TableSql::__construct()` 无条件调 `Create()` → `CreateTable()`，后者把库名
 * 硬编码成生产库（pitfalls #131）。在 `SqlDieByQuery()` 里拦掉 `CREATE TABLE`
 * 之后，所有 `new XxxSql()` 都变成纯读写的空操作 —— 于是
 * `php/sql/sqlstocksymbol.php` / `sqlstockgroup.php` / `deletestock.php`
 * 可以**整份 require**，`GetCalibrationSql()` / `GetAbPairSql()` /
 * `SqlDeleteStockGroupItemByStockID()` / `DeleteStock()` 全是**真身**。
 *
 * ## 唯一不能 require 的东西
 *
 * `woody/res/deletesymbol.php` 第 2-4 行 require `_stock.php` / `_emptygroup.php`，
 * 那一串会拉到 layout / ui / email；文件末尾还有
 * `$acct = new _DeleteSymbolAccount(); $acct->AdminRun();`
 * 整份 require 会在输出 JSON 之前就把页面跑起来。所以六个 `_delete*` /
 * `_deleteStockSymbol` 函数用 `ExtractFunction()` 抠出来 eval，
 * **函数体一个字符都没改**。
 *
 * 因此本脚本写的是**真表**：一次性的 `ZZ*` 符号由对端建好、跑完由对端清掉。
 *
 * ## 注意
 *
 * * 本文件不能出现 PHP 结束标签（注释里也不行）。
 * * `CREATE TABLE` 被拦掉是本文件能跑起来的前提，别"修"成真的执行。
 */

error_reporting(E_ALL);
ini_set('display_errors', 'stderr');

$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }
if (!isset($argv[1])) { fwrite(STDERR, "用法: php _php_deletesymbol_check.php <symbol>\n"); exit(2); }
$g_strSymbol = $argv[1];

// `php/sql.php` 里的 TABLE_* 常量（那个文件拉 UI，进不来）—— 值照抄。
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

//: `_deleteStockSymbol()` 成功后才调它，改的是 `$_SESSION` —— 探针没有会话。
function SwitchRemoveFromSess($strVal)  { fwrite(STDERR, 'DEBUG sess -' . $strVal . PHP_EOL); }

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
    if ($result = mysqli_query($g_link, $strQry))
    {
        return true;
    }
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
require_once($phpDir . '/sql/sqlstockgroup.php');
require_once($phpDir . '/deletestock.php');

// `$g_stock_sql` 的真身就是 `sql.php` 里那句 `$g_stock_sql = new StockSql();`。
// 现在 CREATE TABLE 被拦掉，`StockSql::__construct()` 里那十个子 Sql
// 全是空操作，可以直接建 —— 比上一组探针手搓匿名类更接近真身。
InitGlobalStockSql();

// ---------------------------------------------------------------------------
// 抠函数（`woody/res/deletesymbol.php` 整份 require 会把页面跑起来）
// ---------------------------------------------------------------------------
/**
 * 从源文件里抠出一个函数的源码，按花括号配平；引号段跳过
 * （pitfalls #153：数组字面量里的半角括号会把配平带偏）。
 */
function ExtractFunction($strFile, $strName)
{
    $strSource = file_get_contents($strFile);
    $iStart = strpos($strSource, 'function ' . $strName . '(');
    if ($iStart === false)    throw new Exception('找不到函数 ' . $strName);
    $iBrace = strpos($strSource, '{', $iStart);

    $iDepth = 0;
    $bQuote = false;
    for ($i = $iBrace; $i < strlen($strSource); $i++)
    {
        $c = $strSource[$i];
        if ($bQuote)
        {
            if ($c == '\\')  { $i++; continue; }
            if ($c == "'")   $bQuote = false;
            continue;
        }
        if ($c == "'")   { $bQuote = true; continue; }
        if ($c == '{')   { $iDepth++; continue; }
        if ($c == '}')
        {
            $iDepth--;
            if ($iDepth == 0)    return substr($strSource, $iStart, $i - $iStart + 1);
        }
    }
    throw new Exception('括号不配平：' . $strName);
}

$strDeleteFile = $phpDir . '/../woody/res/deletesymbol.php';
foreach (array('_deleteHasAbPair', '_deleteHasAhPair', '_deleteHasAdrPair',
               '_deleteHasFundPair', '_deleteHasCalibration', '_deleteStockSymbol') as $strFunc)
{
    eval(ExtractFunction($strDeleteFile, $strFunc));
}

/** `_deleteStockSymbol($ref)` 只要两个方法：`GetSymbol()` / `GetStockId()`。 */
class FakeRef
{
    private $strSymbol;
    private $strStockId;

    public function __construct($strSymbol, $strStockId)
    {
        $this->strSymbol = $strSymbol;
        $this->strStockId = $strStockId;
    }

    public function GetSymbol()   { return $this->strSymbol; }
    public function GetStockId()  { return $this->strStockId; }
}

// ---------------------------------------------------------------------------
// 跑
// ---------------------------------------------------------------------------
$arOut = array('symbol' => $g_strSymbol, 'stock_id' => null, 'exists' => false, 'counts' => array());

$strStockId = GetStockSql()->GetId($g_strSymbol);
if ($strStockId !== false && $strStockId !== null && $strStockId !== '')
{
    $arOut['stock_id'] = intval($strStockId);
    $arOut['exists'] = true;

    _deleteStockSymbol(new FakeRef($g_strSymbol, $strStockId));

    // 与 Python 端**完全同一个查询**：删完各表还剩几行。
    // `id` 列存 stock id 的表（stockhistorydate / lastcalibration /
    // fundposition）用 `id`，`estquote` / `realtimequote` 按符号字符串。
    $arSpec = array(
        'calibrationhistory' => array('stock_id', $strStockId),
        'stockema50'         => array('stock_id', $strStockId),
        'stockema200'        => array('stock_id', $strStockId),
        'stockhistory'       => array('stock_id', $strStockId),
        'netvaluehistory'    => array('stock_id', $strStockId),
        'stock'              => array('id', $strStockId),
        'stockhistorydate'   => array('id', $strStockId),
        'lastcalibration'    => array('id', $strStockId),
        'fundposition'       => array('id', $strStockId),
        'quarterreport'      => array('stock_id', $strStockId),
        'stockdividend'      => array('stock_id', $strStockId),
        'realtimequote'      => array('symbol', $g_strSymbol),
    );
    foreach ($arSpec as $strTable => $arOne)
    {
        $arOut['counts'][$strTable] = SqlCountTableData(
            $strTable, _SqlBuildWhere($arOne[0], $arOne[1]));
    }

    // 五道闸门：配对表按"本符号在任一侧"数（两个方向都覆盖）
    foreach (array('abpair', 'adrpair', 'ahpair', 'fundpair') as $strTable)
    {
        $arOut['counts']['gate_' . $strTable] = SqlCountTableData(
            $strTable, 'id = ' . intval($strStockId) . ' OR stock_id = ' . intval($strStockId));
    }
    $arOut['counts']['gate_stockgroupitem'] = SqlCountTableData(
        'stockgroupitem', _SqlBuildWhere('stock_id', $strStockId));
}

echo json_encode($arOut, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), PHP_EOL;
