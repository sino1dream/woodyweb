<?php
// ---------------------------------------------------------------------------
// 用**真实 PHP** 跑 `_updateFundPurchaseAmount()`（编辑页"基金申购金额"），
// 逐例比对 Python 的
// `web/services/stock_option_service.py::_write_stockamount`。
//
//   PALMMICRO_DB_PASSWORD=... php -d display_errors=stderr \
//       _php_amount_check.php _amount_case.json
//
// 对端：`_php_amount_compare.py`（准备靶子行 + 跑 Python + 比对 + 复原）
//
// ## 这一组为什么最短也最值得跑
//
// `_updateFundPurchaseAmount()` 只有 8 行，但它串起**三个真实查询**：
//
//   1. `SqlGetIdByEmail($_POST['login'])` —— `SqlGetSingleTableData('member', ...)`；
//   2. `SqlGetMyStockGroupItemId($memberId, $stockId)` —— 分组名必须**恰好等于
//      股票符号**（`SqlGetStockSymbol` 返回 `stock.symbol`），先 `KeyStringSql`
//      查组、再 `KeyTableSql` 查组内那一行；
//   3. `GroupItemAmountSql::WriteString()` —— `IntSql` 的**字符串比较**写入。
//
// 三处都在 `php/sql/` 里，而且都能整份 require（`sqlmember.php` /
// `sqlstockgroup.php` / `sqlstocksymbol.php`）—— 前提是把 `CREATE TABLE` 拦掉
// （`TableSql::__construct()` 无条件 `Create()`，见 pitfalls #131）。
// 所以本文件里**只有业务函数是抄的**，它调用的类与自由函数全是真身。
//
// ## 与 Python 侧对齐的口径
//
// * `$strVal` 原样交给 `WriteString`（**不** strip）—— Python 那边 strip 过；
//   MySQL 往 `int` 列塞 `' 12 '` 与 `'12'` 结果一样，正好用 `write_spaces`
//   这一例钉住；
// * 快照只取 `groupitemamount`（`id, num`）+ 本次的 `outcome`。金额表在演示库里
//   是空的，所以"写没写"一眼看得出来；
// * `outcome` 是把 PHP 那个组合 `if` 拆成标签（判断条件与顺序一字未改），
//   对端只拿它做提示，**比对以表内容为准**。
//
// ## 注意
//
// * 本文件**不能出现 PHP 结束标签**（注释里也不行）。
// * `CREATE TABLE` 被拦掉是本文件能跑起来的前提，别"修"成真的执行。

$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }
if (!isset($argv[1])) { fwrite(STDERR, "用法: php _php_amount_check.php <case.json>\n"); exit(2); }

define('MIN_FLOAT_VAL', 0.0000005);
//: 这些常量在 `php/sql.php`（拉 UI，进不来）里定义 —— 值照抄同名常量。
define('TABLE_MEMBER', 'member');
define('TABLE_STOCK', 'stock');
define('TABLE_STOCK_GROUP', 'stockgroup');
define('TABLE_STOCK_GROUP_ITEM', 'stockgroupitem');

function SqlDieByQuery($strQry, $strDie)
{
    global $g_link;

    if (stripos(ltrim($strQry), 'CREATE TABLE') === 0)     return true;
    if (mysqli_query($g_link, $strQry))
    {
        return true;
    }
    fwrite(STDERR, 'SQL FAIL: ' . $strDie . ' -> ' . mysqli_error($g_link) . PHP_EOL);
    return false;
}

function die_mysql_error($str)
{
    global $g_link;
    $str .= ' ' . mysqli_error($g_link);
    fwrite(STDERR, 'SQL FAIL ' . $str . PHP_EOL);
    die($str);
}

// 下面五个照抄 `php/sql.php`（那个文件 include email / httplink / ui，CLI 起不来）。
// **搬函数体，不改函数体**。

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
// 三个真身：`SqlGetIdByEmail` / `SqlGetMyStockGroupItemId` / `GroupItemAmountSql`
// 分别住在这三个文件里。
require_once($phpDir . '/sql/sqlmember.php');
require_once($phpDir . '/sql/sqlstocksymbol.php');
require_once($phpDir . '/sql/sqlstockgroup.php');

// `SqlGetStockId()` / `SqlGetStockSymbol()` 都要 `$g_stock_sql`
// （`sqlstocksymbol.php:200/206`）。真身是 `sql.php` 里的
// `new StockSql()`，那条链会拉起 CalibrationSql / StockEmaSql … 一堆类，
// 这里只补这两个方法用到的东西：`GetId`（KeyNameSql::GetKey 的反查）与
// `GetStockSymbol`（KeyNameSql::GetKey 本身）。
$g_stock_sql = new class {
    public function GetId($strSymbol)
    {
        $record = SqlGetSingleTableData(TABLE_STOCK, _SqlBuildWhere('symbol', $strSymbol));
        return $record ? $record['id'] : false;
    }

    public function GetStockSymbol($strStockId)
    {
        $record = SqlGetSingleTableData(TABLE_STOCK, _SqlBuildWhere_id($strStockId));
        return $record ? $record['symbol'] : false;
    }
};

// ---------------------------------------------------------------------------
// 业务函数：照抄 `_submitstockoptions.php:70`，只在出口打标签
// ---------------------------------------------------------------------------
function _updateFundPurchaseAmount($strEmail, $strSymbol, $strVal, &$strOutcome)
{
    $strMemberId = SqlGetIdByEmail($strEmail);
    $strStockId = SqlGetStockId($strSymbol);
    if ($strMemberId && $strStockId && is_numeric($strVal))
    {
        if ($strGroupItemId = SqlGetMyStockGroupItemId($strMemberId, $strStockId))
        {
            $amount_sql = new GroupItemAmountSql();
            // `WriteString` 同值返回 false —— 与"没找到组项"必须分开记，
            // 否则"写成功"与"什么都没干"在快照里长得一样。
            $strOutcome = $amount_sql->WriteString($strGroupItemId, $strVal) ? 'wrote' : 'unchanged';
            return;
        }
        $strOutcome = 'no_item';
        return;
    }
    if (!$strMemberId)          $strOutcome = 'no_member';
    elseif (!$strStockId)       $strOutcome = 'no_stock';
    else                        $strOutcome = 'not_numeric';
}

// ---------------------------------------------------------------------------
// 快照
// ---------------------------------------------------------------------------
//: 数值统一成 `strval(floatval($v))` —— mysqli 给的是库里的原样字符串
//: （`"100000"`），pymysql 给 int，不压平会每例假报差异（pitfalls #143）。
function CellValue($v)
{
    if ($v === null)        return '';
    if (is_numeric($v))     return strval(floatval($v));
    return strval($v);
}

function MakeSnapshot()
{
    $ar = array();
    if ($result = SqlGetTableData('groupitemamount', false, '`id` ASC'))
    {
        while ($record = mysqli_fetch_assoc($result))
        {
            $ar[] = array('id' => strval($record['id']), 'num' => CellValue($record['num']));
        }
        mysqli_free_result($result);
    }
    return $ar;
}

// ---------------------------------------------------------------------------
// 主流程
// ---------------------------------------------------------------------------
$case = json_decode(file_get_contents($argv[1]), true);
if (!is_array($case)) { fwrite(STDERR, "用例文件读不出来\n"); exit(2); }

$strOutcome = 'skipped';
$strError = '';
try
{
    foreach ($case['values'] as $strVal)
    {
        _updateFundPurchaseAmount($case['email'], $case['symbol'], $strVal, $strOutcome);
    }
}
catch (Throwable $e)
{
    //: `is_numeric()` 之类不会抛；留一手是为了别在 TypeError 上整段 500。
    $strError = get_class($e) . ':' . $e->getMessage();
}

echo json_encode(array(
    'outcome' => $strOutcome,
    'error' => $strError,
    'snapshot' => MakeSnapshot(),
), JSON_UNESCAPED_UNICODE);
