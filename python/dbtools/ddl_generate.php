<?php
// 只用于「从 PHP 权威生成 DDL」的一次性引导脚本。
//
//   php -n ddl_generate.php            # JSON 输出
//
// 为什么不直接 require php/sql.php？因为它 require 了 php/_private.php，
// 而该文件不在仓库里（部署时才有），直接 require 会失败。所以这里只 require
// 建表需要的 SQL 表类，并自行提供它们依赖的少量辅助函数与常量。
//
// 注意：本脚本依赖 php/sql/sqltable.php 里的库名被临时改成 stock_data
// （原值 n5gl0n39mnyn183l_camman），详见 python/DATABASE_PLAN.md 的记录。

ini_set('display_errors', 'stderr');
error_reporting(E_ALL & ~E_WARNING & ~E_DEPRECATED);

define('DDL_DATABASE', 'stock_data');

// ---- TableSql 依赖的常量与辅助函数（对齐 php/sql.php 的定义）----
define('TABLE_PAGE', 'page');
define('TABLE_STOCK_DIVIDEND', 'stockdividend');
define('TABLE_STOCK_SPLIT', 'stocksplit');

$g_link = false;

function SqlDieByQuery($strQry, $strDie)
{
    global $g_link;
    if ($result = mysqli_query($g_link, $strQry))        return $result;
    fwrite(STDERR, $strDie.' '.mysqli_error($g_link).' ['.$strQry.']'."\n");
    return false;
}

// TableSql 的读方法本脚本用不到，但类定义里引用了，给个保底实现
function SqlCountTableData($strTableName, $strWhere = false)                                     { return 0; }
function SqlGetTableData($strTableName, $strWhere = false, $strOrder = false, $strLimit = false) { return false; }
function SqlGetSingleTableData($strTableName, $strWhere = false, $strOrder = false)              { return false; }

$sql_dir = __DIR__ . '/../../php/sql/';
require $sql_dir . 'sqltable.php';
require $sql_dir . 'sqlval.php';
require $sql_dir . 'sqlint.php';
require $sql_dir . 'sqlpair.php';
require $sql_dir . 'sqlkey.php';
require $sql_dir . 'sqlkeyname.php';
require $sql_dir . 'sqldate.php';
require $sql_dir . 'sqldailyclose.php';
require $sql_dir . 'sqldailytime.php';
require $sql_dir . 'sqldailystring.php';
require $sql_dir . 'sqlholdings.php';
require $sql_dir . 'sqlstockpair.php';
require $sql_dir . 'sqlstocksymbol.php';

// 连本地 MariaDB（凭据与 python/auto/.env 一致）
$g_link = mysqli_connect('127.0.0.1', 'root', '271828', null, 3306);
if (!$g_link) {
    fwrite(STDERR, 'connect failed: '.mysqli_connect_error()."\n");
    exit(1);
}
mysqli_set_charset($g_link, 'utf8');
if (!mysqli_select_db($g_link, DDL_DATABASE)) {
    fwrite(STDERR, 'select db failed: '.mysqli_error($g_link)."\n");
    exit(1);
}

// 需要建的表：先删掉，保证拿到的是干净的权威 DDL
$drop = array(
    'holdings', 'fundpair', 'fundest', 'quarterreport',
    'stockema50', 'stockema200', 'stockhistory', 'netvaluehistory',
    'calibrationhistory', 'stockhistorydate', 'holdingsdate',
    'fundposition', 'lastcalibration', 'stock',
);
foreach ($drop as $table) {
    mysqli_query($g_link, 'DROP TABLE IF EXISTS `'.$table.'`');
}

// 按依赖顺序实例化（构造函数里就会 CREATE TABLE IF NOT EXISTS）
$objects = array(
    'stock'              => new StockSql(),
    'fundposition'       => new FundPositionSql(),
    'lastcalibration'    => new LastCalibrationSql(),
    'holdingsdate'       => new HoldingsDateSql(),
    'stockhistorydate'   => new StockHistoryDateSql(),
    'stockhistory'       => new StockHistorySql(),
    'netvaluehistory'    => new DailyCloseSql('netvaluehistory'),
    'calibrationhistory' => new CalibrationSql(),
    'fundest'            => new DailyTimeSql('fundest'),
    'quarterreport'      => new QuarterReportSql(),
    'stockema50'         => new StockEmaSql(50),
    'stockema200'        => new StockEmaSql(200),
    'holdings'           => new HoldingsSql(),
    'fundpair'           => new StockPairSql('fundpair'),
);

$out = array();
foreach ($objects as $name => $object) {
    $result = mysqli_query($g_link, 'SHOW CREATE TABLE `'.$name.'`');
    if (!$result) {
        fwrite(STDERR, 'SHOW CREATE failed for '.$name.': '.mysqli_error($g_link)."\n");
        continue;
    }
    $row = mysqli_fetch_assoc($result);
    $out[$name] = $row['Create Table'];
    mysqli_free_result($result);
}

echo json_encode($out, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);
mysqli_close($g_link);
