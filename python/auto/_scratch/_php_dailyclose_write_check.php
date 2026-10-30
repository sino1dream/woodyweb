<?php
// ---------------------------------------------------------------------------
// 用**真实 PHP** 跑 DailyCloseSql 的写方法，逐例比对 Python 的移植版。
//
//   PALMMICRO_DB_PASSWORD=... php -d display_errors=stderr \
//       _php_dailyclose_write_check.php _dailyclose_write_cases.json
//
// 对端：`_php_dailyclose_write_compare.py`（生成用例 + 跑 Python + 比对）
//
// ## 为什么这么搭
//
// 写方法（`WriteDaily` / `InsertDaily` / `UpdateDaily` / `DeleteByDate`）的
// 判据全在**方法体**里，但真正落库要靠 `InsertArray` / `UpdateById` /
// `DeleteData` 这几层。所以这里 include 真实的
// `sql/_sqlcommon.php` + `sql/sqltable.php` + `sql/sqlkey.php` +
// `sql/sqldailyclose.php`，只在**最底下那层执行函数**上用等价实现
// （`php/sql.php` 里那几个，它 include 了 email/httplink/ui，CLI 下跑不起来）。
// 也就是说：拼 SQL 的是真身，我只负责把拼好的 SQL 丢给 mysqli。
//
// ## 与真身的三处故意不同（都只影响"出错时"的表现）
//
// ==============================  ==========================================
// `SqlDieByQuery`                 真身失败就 `die()`；探针改成 return false
//                                 并写 stderr，否则第一个失败就把整轮打断。
// `DebugString` / `DebugVal`      真身写日志文件并读 `$_SERVER`；探针转到
//                                 stderr（`IsTooOld` / `DeleteData` 会调它们）。
// `DebugGetTime`                  真身走 `DebugFormat_date`；探针直接用
//                                 `DateTime` + `Asia/Shanghai`，格式同为 H:i:s。
// ==============================  ==========================================
//
// ## 注意
//
// * 本文件**不能出现 PHP 结束标签**（注释里也不行）。
// * 临时表 `dcw_probe` / `dcw_probe_cal` 由本脚本建、跑完删。
// * 密码从环境变量取，不写进文件。

$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }
if (!isset($argv[1])) { fwrite(STDERR, "用法: php _php_dailyclose_write_check.php <cases.json>\n"); exit(2); }

define('MIN_FLOAT_VAL', 0.0000005);
define('SECONDS_IN_DAY', 86400);
define('DEBUG_TIME_ZONE', 'Asia/Shanghai');
define('DCW_TABLE', 'dcw_probe');
define('DCW_CAL_TABLE', 'dcw_probe_cal');

function DebugString($str, $bAdminOnly = false)
{
    fwrite(STDERR, 'DEBUG ' . $str . PHP_EOL);
}

// DeleteData() 会调 DebugVal() 记一行"要删多少条"（sqltable.php:237）。
function DebugVal($iVal, $strPrefix = false, $bAdminOnly = false)
{
    fwrite(STDERR, 'DEBUG ' . ($strPrefix === false ? '' : $strPrefix . ' ') . $iVal . PHP_EOL);
}

function DebugGetTime($iTime = false, $strTimeZone = DEBUG_TIME_ZONE)
{
    if ($iTime === false)
    {
        $dt = new DateTime('now', new DateTimeZone($strTimeZone));
    }
    else
    {
        $dt = new DateTime('@' . intval($iTime));
        $dt->setTimezone(new DateTimeZone($strTimeZone));
    }
    return $dt->format('H:i:s');
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

// 下面四个是 php/sql.php 里那四个执行函数的等价实现（照抄，只改失败处理）。
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
require_once($phpDir . '/sql/sqldailytime.php');

// TableSql::__construct **无条件**调 $this->Create()（sqltable.php:10），
// 而 CreateTable() 把库名硬编码成生产库（sqltable.php:88）。本地库名不同，
// 于是每次 new 都会打一条「Unknown database」—— 表由本脚本自己建，这条失败
// 对写方法毫无影响，但噪音会让人以为探针没跑起来。所以只短路 Create()。
class ProbeDailyCloseSql extends DailyCloseSql
{
    public function Create()    {   return true;    }
}

class ProbeCalibrationSql extends CalibrationSql
{
    // **必须绕开 CalibrationSql 的构造器**：它是无参的、表名写死
    // 'calibrationhistory'（sqldailytime.php:33-36），而 PHP 对多余的实参
    // **静默忽略** —— 直接 `new ProbeCalibrationSql($tmp)` 会把探针数据写进
    // 真表（第一次跑就这么干了：SZ161130 多了 2026-09-27/29/30 三行）。
    // 所以这里退到 DailyTimeSql 的构造器，自己指定表名。
    public function __construct($strTable)
    {
        DailyTimeSql::__construct($strTable, 'stock');
    }

    public function Create()    {   return true;    }
}

function BuildProbeTable($strTable, $strExtra)
{
    global $g_link;
    mysqli_query($g_link, 'DROP TABLE IF EXISTS ' . $strTable);
    $strSql = 'CREATE TABLE ' . $strTable . ' (
    `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
    `stock_id` int(10) unsigned NOT NULL,
    `date` date NOT NULL,
    `close` double(13,6) NOT NULL' . $strExtra . ',
    PRIMARY KEY (`id`),
    UNIQUE KEY `date` (`date`,`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci';
    if (!mysqli_query($g_link, $strSql))
    {
        fwrite(STDERR, '建表失败 ' . $strTable . ': ' . mysqli_error($g_link) . PHP_EOL);
        exit(4);
    }
}

// time 是"现在几点"（动态值），逐例比不了，所以快照里只留 date/close/num。
function SnapshotRows($strTable, $bWithNum)
{
    global $g_link;
    $arOut = array();
    $strColumns = $bWithNum ? 'id, `date`, close, num' : 'id, `date`, close';
    $result = mysqli_query($g_link, 'SELECT ' . $strColumns . ' FROM ' . $strTable . ' ORDER BY `date`, id');
    while ($record = mysqli_fetch_assoc($result))
    {
        $arRow = array(
            'date' => $record['date'],
            'close' => sprintf('%.6f', floatval($record['close'])),
        );
        if ($bWithNum)      $arRow['num'] = intval($record['num']);
        $arOut[] = $arRow;
    }
    mysqli_free_result($result);
    return $arOut;
}

function RunCases($sql, $strTable, $arCases, $bWithNum)
{
    $arOut = array();
    foreach ($arCases as $arCase)
    {
        $strOp = $arCase['op'];
        $iStockId = isset($arCase['stock_id']) ? $arCase['stock_id'] : 1;
        $strDate = isset($arCase['date']) ? $arCase['date'] : '';
        $strClose = array_key_exists('close', $arCase) ? $arCase['close'] : null;

        switch ($strOp)
        {
        case 'insert_daily':
            $result = $sql->InsertDaily($iStockId, $strDate, $strClose);
            break;
        case 'update_daily':
            $result = $sql->UpdateDaily($arCase['id'], $strClose);
            break;
        case 'delete_by_date':
            $result = $sql->DeleteByDate($iStockId, $strDate);
            break;
        case 'write_daily':
            $result = $sql->WriteDaily($iStockId, $strDate, $strClose);
            break;
        case 'write_daily_average':
            $result = $sql->WriteDailyAverage($iStockId, $strDate, $strClose);
            break;
        default:
            $result = 'unknown-op';
            break;
        }

        $arOut[] = array(
            'op' => $strOp,
            'result' => is_bool($result) ? ($result ? 1 : 0) : $result,
            'rows' => SnapshotRows($strTable, $bWithNum),
        );
    }
    return $arOut;
}

$arInput = json_decode(file_get_contents($argv[1]), true);
if (!is_array($arInput) || !isset($arInput['daily']) || !isset($arInput['calibration']))
{
    fwrite(STDERR, "用例文件解析失败（需要 daily / calibration 两段）\n");
    exit(5);
}

BuildProbeTable(DCW_TABLE, '');
BuildProbeTable(DCW_CAL_TABLE, ', `time` time NOT NULL, `num` int(10) unsigned NOT NULL');

$arOut = array(
    'daily' => RunCases(new ProbeDailyCloseSql(DCW_TABLE), DCW_TABLE, $arInput['daily'], false),
    'calibration' => RunCases(new ProbeCalibrationSql(DCW_CAL_TABLE), DCW_CAL_TABLE, $arInput['calibration'], true),
);

mysqli_query($g_link, 'DROP TABLE IF EXISTS ' . DCW_TABLE);
mysqli_query($g_link, 'DROP TABLE IF EXISTS ' . DCW_CAL_TABLE);
mysqli_close($g_link);

echo json_encode($arOut, JSON_UNESCAPED_UNICODE);
