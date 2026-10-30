<?php
// ---------------------------------------------------------------------------
// 用**真实 PHP** 跑 ValSql / PairSql / DailyCloseSql 的几种写操作，逐例比对
// Python 的 sql/optionstore.py。
//
//   PALMMICRO_DB_PASSWORD=... php -d display_errors=stderr \
//       _php_optionstore_check.php _optionstore_cases.json
//
// 对端：`_php_optionstore_compare.py`（生成用例 + 跑 Python + 比对）
//
// ## 为什么要这一轮对照
//
// `optionstore.py` 的三类底座（(id,值) / (id,stock_id) / 日频）都被编辑页
// **先查后写**地调用，判据全在方法体里：
//
//   * `WriteVal` 的"值没变就不写"闸门（数值比 vs 字符串比）
//   * `WritePair` 走字符串比较（`IntSql::WriteString`）
//   * `DeleteAll($strKeyId)` 的 `$strKeyId !== false` 闸门
//   * `Delete($strPairId)` 删的是 `WHERE stock_id = ...` 的**全部**行
//
// 这几条一旦搬错，"写成功"与"没写"就分不清，页面上看不出来。所以这里把
// 真身的方法体跑一遍，逐操作快照整张临时表再比。
//
// 引导段与 `_php_dailyclose_write_check.php` 完全同源（连同样是三处
// stderr 化），区别只有探针类多了 ValSql / PairSql 两个。
//
// ## 注意
//
// * 本文件**不能出现 PHP 结束标签**（注释里也不行）。
// * 临时表 `os_probe_val` / `os_probe_pair` / `os_probe_daily` 由本脚本建、跑完删。
// * 四张表的 `Create()` 全部短路：`TableSql::__construct` 无条件调它，且
//   `CreateTable()` 把库名硬编码成生产库（见 pitfalls #131）。

$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }
if (!isset($argv[1])) { fwrite(STDERR, "用法: php _php_optionstore_check.php <cases.json>\n"); exit(2); }

define('MIN_FLOAT_VAL', 0.0000005);
define('OS_VAL_TABLE', 'os_probe_val');
define('OS_PAIR_TABLE', 'os_probe_pair');
define('OS_DAILY_TABLE', 'os_probe_daily');

function DebugString($str, $bAdminOnly = false)
{
    fwrite(STDERR, 'DEBUG ' . $str . PHP_EOL);
}

function DebugVal($iVal, $strPrefix = false, $bAdminOnly = false)
{
    fwrite(STDERR, 'DEBUG ' . ($strPrefix === false ? '' : $strPrefix . ' ') . $iVal . PHP_EOL);
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

function SqlDieByQuery($strQry, $strDie)
{
    global $g_link;
    if ($result = mysqli_query($g_link, $strQry))
    {
        return true;
    }
    fwrite(STDERR, 'SQL FAIL: ' . $strDie . ' -> ' . mysqli_error($g_link) . PHP_EOL);
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
        fwrite(STDERR, 'single query failed: ' . $strQry . ' -> ' . mysqli_error($g_link) . PHP_EOL);
    }
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
        fwrite(STDERR, 'query failed: ' . $strQry . ' -> ' . mysqli_error($g_link) . PHP_EOL);
    }
    return false;
}

function SqlDeleteTableData($strTableName, $strWhere, $strLimit = false)
{
    if (!$strWhere)     return false;
    $strQry = 'DELETE FROM ' . $strTableName . _SqlAddWhere($strWhere) . _SqlAddLimit($strLimit);
    return SqlDieByQuery($strQry, $strTableName . ' delete failed');
}

function SqlCountTableData($strTableName, $strWhere = false)
{
    global $g_link;
    $strQry = "SELECT count(*) as total FROM $strTableName" . _SqlAddWhere($strWhere);
    if ($result = mysqli_query($g_link, $strQry))
    {
        $record = mysqli_fetch_array($result);
        if (isset($record['total']))    return intval($record['total']);
    }
    return 0;
}

set_include_path(
    $phpDir . DIRECTORY_SEPARATOR . 'sql' . PATH_SEPARATOR
    . $phpDir . DIRECTORY_SEPARATOR . 'class' . PATH_SEPARATOR
    . get_include_path()
);
require_once($phpDir . '/class/year_month_day.php');
require_once($phpDir . '/sql/_sqlcommon.php');
require_once($phpDir . '/sql/sqldailyclose.php');
require_once($phpDir . '/sql/sqlint.php');
require_once($phpDir . '/sql/sqlpair.php');

// ValSql / IntSql / PairSql 的构造器都**接受表名**，所以不需要绕构造器；
// 只有 Create() 要短路（表由本脚本建）。
class ProbeValSql extends ValSql
{
    public function __construct($strTable)  {   parent::__construct($strTable);     }
    public function Create()                {   return true;                        }
}

class ProbePairSql extends PairSql
{
    public function __construct($strTable)  {   parent::__construct($strTable);     }
    public function Create()                {   return true;                        }
}

class ProbeDailySql extends DailyCloseSql
{
    public function __construct($strTable)  {   parent::__construct($strTable);     }
    public function Create()                {   return true;                        }
}

function BuildTable($strTable, $strBody)
{
    global $g_link;
    mysqli_query($g_link, 'DROP TABLE IF EXISTS ' . $strTable);
    $strSql = 'CREATE TABLE ' . $strTable . ' (' . $strBody . ') '
            . 'ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci';
    if (!mysqli_query($g_link, $strSql))
    {
        fwrite(STDERR, '建表失败 ' . $strTable . ': ' . mysqli_error($g_link) . PHP_EOL);
        exit(4);
    }
}

// close 用 double(13,6)（与 fundposition / lastcalibration 一致）。
// 列名必须显式给：日频表的 id 是自增的，种子里没有它。
function SeedRows($strTable, $arRows, $strColumns)
{
    global $g_link;
    mysqli_query($g_link, 'DELETE FROM ' . $strTable);
    foreach ($arRows as $arRow)
    {
        $arValue = array();
        foreach ($arRow as $value)
        {
            $arValue[] = "'" . mysqli_real_escape_string($g_link, strval($value)) . "'";
        }
        $strSql = 'INSERT INTO ' . $strTable . ' (' . $strColumns . ') VALUES (' . implode(',', $arValue) . ')';
        if (!mysqli_query($g_link, $strSql))
        {
            fwrite(STDERR, '播种失败 ' . $strTable . ': ' . mysqli_error($g_link) . PHP_EOL);
            exit(4);
        }
    }
}

// 快照：只留可比的列（id 在 AUTO_INCREMENT 表上会随重置变化，所以统一不取）。
function SnapshotRows($strTable, $strColumns, $strOrder)
{
    global $g_link;
    $arOut = array();
    $result = mysqli_query($g_link, 'SELECT ' . $strColumns . ' FROM ' . $strTable . ' ORDER BY ' . $strOrder);
    if (!$result)   return $arOut;
    while ($record = mysqli_fetch_assoc($result))
    {
        $arRow = array();
        foreach ($record as $key => $value)
        {
            // 数值列统一成"最简文本"，避免 MySQL 给 "1.500000"、sqlite 给 1.5
            $arRow[$key] = is_numeric($value) ? strval(floatval($value)) : strval($value);
        }
        $arOut[] = $arRow;
    }
    mysqli_free_result($result);
    return $arOut;
}

function RunOps($obj, $arOps, $strTable, $strColumns, $strOrder)
{
    $arOut = array();
    foreach ($arOps as $arOp)
    {
        $strOp = $arOp['op'];
        switch ($strOp)
        {
        case 'seed':
            SeedRows($strTable, $arOp['rows'], $strColumns);
            $result = true;
            break;
        case 'write_val':
            $result = $obj->WriteVal(strval($arOp['id']), $arOp['value']);
            break;
        case 'write_string':
            $result = $obj->WriteString(strval($arOp['id']), $arOp['value']);
            break;
        case 'delete_by_id':
            $result = $obj->DeleteById(strval($arOp['id']));
            break;
        case 'write_pair':
            $result = $obj->WritePair(strval($arOp['id']), strval($arOp['pair']));
            break;
        case 'delete':
            $result = $obj->Delete(strval($arOp['pair']));
            break;
        case 'write_daily':
            $result = $obj->WriteDaily(strval($arOp['stock_id']), $arOp['date'], $arOp['value']);
            break;
        case 'delete_all':
            $result = $obj->DeleteAll(strval($arOp['stock_id']));
            break;
        default:
            $result = 'unknown-op';
            break;
        }
        $arOut[] = array(
            'op' => $strOp,
            'result' => is_bool($result) ? ($result ? 1 : 0) : $result,
            'rows' => SnapshotRows($strTable, $strColumns, $strOrder),
        );
    }
    return $arOut;
}

$arInput = json_decode(file_get_contents($argv[1]), true);
if (!is_array($arInput) || !isset($arInput['val']) || !isset($arInput['pair']) || !isset($arInput['daily']))
{
    fwrite(STDERR, "用例文件解析失败（需要 val / pair / daily 三段）\n");
    exit(5);
}

BuildTable(OS_VAL_TABLE, '`id` int(10) unsigned NOT NULL, `close` double(13,6) NOT NULL, PRIMARY KEY (`id`)');
BuildTable(OS_PAIR_TABLE, '`id` int(10) unsigned NOT NULL, `stock_id` int(10) unsigned NOT NULL, PRIMARY KEY (`id`)');
BuildTable(OS_DAILY_TABLE, '`id` int(10) unsigned NOT NULL AUTO_INCREMENT, `stock_id` int(10) unsigned NOT NULL, '
                         . '`date` date NOT NULL, `close` double(13,6) NOT NULL, PRIMARY KEY (`id`)');

$arOut = array(
    'val' => RunOps(new ProbeValSql(OS_VAL_TABLE), $arInput['val'], OS_VAL_TABLE, '`id`, `close`', '`id`'),
    'pair' => RunOps(new ProbePairSql(OS_PAIR_TABLE), $arInput['pair'], OS_PAIR_TABLE, '`id`, `stock_id`', '`id`'),
    'daily' => RunOps(new ProbeDailySql(OS_DAILY_TABLE), $arInput['daily'], OS_DAILY_TABLE,
                      '`stock_id`, `date`, `close`', '`date`, `stock_id`'),
);

mysqli_query($g_link, 'DROP TABLE IF EXISTS ' . OS_VAL_TABLE);
mysqli_query($g_link, 'DROP TABLE IF EXISTS ' . OS_PAIR_TABLE);
mysqli_query($g_link, 'DROP TABLE IF EXISTS ' . OS_DAILY_TABLE);
mysqli_close($g_link);

echo json_encode($arOut, JSON_UNESCAPED_UNICODE);
