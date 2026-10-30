<?php
// ---------------------------------------------------------------------------
// 用**真实 PHP** 跑 `_updateStockOptionDividend()` / `_updateStockOptionSplit()`
// 的全部分支，逐例比对 Python 的
// `web/services/stock_option_service.py::_write_stockdividend` / `_write_stocksplit`。
//
//   PALMMICRO_DB_PASSWORD=... php -d display_errors=stderr \
//       _php_dividend_split_check.php _dividend_split_case.json
//
// 对端：`_php_dividend_split_compare.py`（导出基线 + 跑 Python + 比对 + 复原）
//
// ## 与上一组（editcalibration）的关键差别
//
// 上一组是**逐句照抄**业务函数体再跑（因为 `_submitstockoptions.php` 在仓库里
// 无法独立加载：它 require `_stock.php` → `_resstock.php` → `../../php/layout.php`，
// 而那个路径在生产部署里才存在）。这一次找到了更硬的办法：
//
//   `TableSql::__construct()` 无条件调 `Create()` → `CreateTable()`，后者把库名
//   硬编码成生产库（pitfalls #131）。只要在 `SqlDieByQuery()` 里**拦掉
//   `CREATE TABLE`**，所有 `new XxxSql()` 就变成纯读写的空操作 ——
//   于是 `php/sql/sqlstocksymbol.php` 与 `php/sql/sqlstockgroup.php` 可以整份
//   require，`GetCalibrationSql()` / `GetNetValueHistorySql()` /
//   `SqlGetStockId()` 这些工厂也是**真身**。
//
// 所以本脚本里 `_updateStockOption*` 五个函数仍然照抄（它们所在的文件进不来），
// 但**它们调用的每一个 Sql 类与工厂函数都是真的**，SQL 拼装、`WriteDaily` 的
// 判据、`GetFromDate` 的边界、`GetStockIdArray(true)` 的过滤全部没有替身。
//
// ## 三个自己实现的全局函数（都在 `php/` 里，但拉不起或不该动）
//
// * `QdiiGetArray($strEst)` —— 实现在 `php/stock/qdiiref.php`，那个文件除了它
//   还挂着 `class QdiiReference extends FundReference` 的整条类链。这里是
//   查表（`qdii_pairs` 由对端从 Python 的 `qdii_est_symbol_map()` 导出，
//   两边同源于 `QdiiGetEstArray()`）。
// * `unlinkConfigFile($strSymbol)` —— 实现在 `php/debug.php`，删的是
//   `<root>/config/<符号>.txt`。本地跑对照不该去动那份缓存，这里直接返回 true。
// * `DebugString` / `DebugVal` / `DebugGetTime` / `DebugGetDateTime` —— 换成
//   打 stderr 的桩 + 假时钟。`time` / `filled` 两列因此**不进快照**（那是
//   "跑的时刻"，不是业务结果），但 `calibrationhistory.num` 进 —— 它反映
//   "写了几次"，正是分得出"值没变不发 UPDATE"的东西。
//
// ## 真库 + 前后快照
//
// 本脚本**写的是真表**（`stockdividend` / `stocksplit` / `calibrationhistory` /
// `netvaluehistory` / `stockhistory` / `stockgroupitem` / `stocktransaction`），
// 每跑完一例由对端复原到基线。之所以不学上一组用临时表：这次要连带改五张表、
// 还要走 `TableSql(TABLE_STOCK_GROUP)` 的全表遍历，把表名全换掉等于把真身
// 也换掉了。基线快照由 `_php_dividend_split_compare.py` 导出并负责复原。
//
// ## 注意
//
// * 本文件**不能出现 PHP 结束标签**（注释里也不行）。
// * `CREATE TABLE` 被拦掉是本文件能跑起来的前提，别"修"成真的执行。

$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }
if (!isset($argv[1])) { fwrite(STDERR, "用法: php _php_dividend_split_check.php <case.json>\n"); exit(2); }

define('MIN_FLOAT_VAL', 0.0000005);
//: 这三个常量在 `php/sql.php`（拉 UI，进不来）里定义 —— 值照抄同名常量。
define('TABLE_STOCK_DIVIDEND', 'stockdividend');
define('TABLE_STOCK_GROUP', 'stockgroup');
define('TABLE_STOCK_GROUP_ITEM', 'stockgroupitem');
define('TABLE_STOCK_SPLIT', 'stocksplit');

$g_iClock = 0;

function DebugGetTime()
{
    global $g_iClock;
    $g_iClock++;
    return sprintf('09:00:%02d', $g_iClock);
}

function DebugGetDateTime()
{
    return '2026-10-30 ' . DebugGetTime();
}

function DebugString($str, $bAdminOnly = false)
{
    fwrite(STDERR, 'DEBUG ' . $str . PHP_EOL);
}

function DebugVal($iVal, $strPrefix = false, $bAdminOnly = false)
{
    fwrite(STDERR, 'DEBUG ' . ($strPrefix === false ? '' : $strPrefix . ' ') . $iVal . PHP_EOL);
}

// 照抄 PHP 但**不真删**：本地跑对照不该动生产的 SMA 缓存文件。
function unlinkConfigFile($strSymbol)
{
    fwrite(STDERR, 'DEBUG unlinkConfigFile ' . $strSymbol . PHP_EOL);
    return true;
}

function die_mysql_error($str)
{
    global $g_link;
    $str .= ' ' . mysqli_error($g_link);
    fwrite(STDERR, 'SQL FAIL ' . $str . PHP_EOL);
    die($str);
}

// **本文件能跑起来的关键**：`TableSql::__construct` → `Create()` →
// `CreateTable()` 拼的是 `CREATE TABLE IF NOT EXISTS `n5gl0n39mnyn183l_camman`.…`
// （生产库名，pitfalls #131）。本地没有那个库，真执行会 die；更不该让它去建表。
function SqlDieByQuery($strQry, $strDie)
{
    global $g_link;

    if (stripos(ltrim($strQry), 'CREATE TABLE') === 0)     return true;
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
require_once($phpDir . '/stocktrans.php');

// ---------------------------------------------------------------------------
// `$g_stock_sql`：`SqlGetStockId()` 依赖它（`php/sql/sqlstocksymbol.php:200`）
// ---------------------------------------------------------------------------
//
// 真身是 `sql.php` 里的 `$g_stock_sql = new StockSql();`，而 `StockSql::__construct()`
// 顺手 new 了 CalibrationSql / StockEmaSql / HoldingsSql / StockPairSql / FundPositionSql
// 一堆类 —— 那些文件拉进来又是一条链。这里只补 `SqlGetStockId()` 用到的那一个方法，
// 方法体照 `KeyNameSql::GetId()` → `GetRecordId()` → `GetRecord()` 的等价写法：
// `SELECT id FROM stock WHERE symbol = ? LIMIT 1`，取不到给 false（PHP 同款）。
$g_stock_sql = new class {
    //: `GetCalibrationSql()` / `GetStockHistorySql()` / `GetNetValueHistorySql()`
    //: 都是 `$g_stock_sql->xxx_sql` 的转发（`sqlstocksymbol.php:228/240/295`），
    //: 所以三个属性必须齐。`StockSql` 真身里 `net_sql` 就是
    //: `new DailyCloseSql('netvaluehistory')` —— 没有同名的类。
    public $cal_sql;
    public $his_sql;
    public $net_sql;

    public function __construct()
    {
        $this->cal_sql = new CalibrationSql();
        $this->his_sql = new StockHistorySql();
        $this->net_sql = new DailyCloseSql('netvaluehistory');
    }

    public function GetId($strSymbol)
    {
        global $g_link;

        $strQry = "SELECT `id` FROM `stock` WHERE `symbol` = '"
            . mysqli_real_escape_string($g_link, $strSymbol) . "' LIMIT 1";
        if ($result = mysqli_query($g_link, $strQry))
        {
            if ($record = mysqli_fetch_assoc($result))
            {
                mysqli_free_result($result);
                return $record['id'];
            }
        }
        return false;
    }
};

// ---------------------------------------------------------------------------
// `QdiiGetArray()`：查表代替 `php/stock/qdiiref.php`（见文件头）
// ---------------------------------------------------------------------------
$g_arQdii = array();

function QdiiGetArray($strEst)
{
    global $g_arQdii;
    return isset($g_arQdii[$strEst]) ? $g_arQdii[$strEst] : array();
}

// ---------------------------------------------------------------------------
// 以下是 `woody/res/php/_submitstockoptions.php` 的**逐句照抄**
// （`$ref` 参数原样留着 —— 真身里有它却没用）
// ---------------------------------------------------------------------------

function _updateOptionDailySql($sql, $strStockId, $strDate, $strVal)
{
    DebugString(__FUNCTION__.' '.$strVal.' '.$strDate, true);
    if (empty($strVal))
    {
        $iCount = $sql->DeleteByDate($strStockId, $strDate);
        DebugVal($iCount, __FUNCTION__.' deleted', true);
        return false;
    }
    return $sql->WriteDaily($strStockId, $strDate, $strVal);
}

function _updateStockHistoryAdjCloseBySplit($ref, $strSymbol, $strStockId, $his_sql, $strYMD, $fRatio)
{
    $ar = array();
    if ($result = $his_sql->GetFromDate($strStockId, $strYMD))
    {
        while ($record = mysqli_fetch_assoc($result))
        {
            $ar[$record['id']] = floatval($record['adjclose']);
        }
        mysqli_free_result($result);
    }

    foreach ($ar as $strId => $fAdjClose)
    {
        $fAdjClose /= $fRatio;
        $his_sql->UpdateAdjClose($strId, strval($fAdjClose));
    }
    unlinkConfigFile($strSymbol);
}

function _updateStockHistoryAdjCloseByDividend($ref, $strSymbol, $strStockId, $his_sql, $strYMD, $strDividend)
{
    $ar = array();
    if ($result = $his_sql->GetFromDate($strStockId, $strYMD))
    {
        while ($record = mysqli_fetch_assoc($result))
        {
            if ($record['date'] != $strYMD)     $ar[$record['id']] = floatval($record['adjclose']);
        }
        mysqli_free_result($result);
    }

    $fDividend = floatval($strDividend);
    foreach ($ar as $strId => $fAdjClose)
    {
        $fAdjClose -= $fDividend;
        $his_sql->UpdateAdjClose($strId, strval($fAdjClose));
    }
    unlinkConfigFile($strSymbol);
}

function _updateStockOptionSplitGroupTransactions($strGroupId, $strStockId, $strDate, $fRatio, $fPrice)
{
    $sql = new StockGroupItemSql($strGroupId);
    if ($arStockId = $sql->GetStockIdArray(true))
    {
        if (in_array($strStockId, $arStockId))
        {
            $record = $sql->GetRecord($strStockId);
            $strGroupItemId = $record['id'];
            $iQuantity = intval($record['quantity']);
            $sql->trans_sql->Insert($strGroupItemId, strval(0 - $iQuantity), strval($fPrice));
            $sql->trans_sql->Insert($strGroupItemId, strval(intval($fRatio * $iQuantity)), strval($fPrice / $fRatio));
            UpdateStockGroupItemTransaction($sql, $strGroupItemId);
        }
    }
}

function _updateStockOptionSplitTransactions($ref, $strStockId, $his_sql, $strDate, $fRatio)
{
    $fPrice = floatval($his_sql->GetClosePrev($strStockId, $strDate));

    $sql = new TableSql(TABLE_STOCK_GROUP);
    if ($result = $sql->GetData())
    {
        while ($record = mysqli_fetch_assoc($result))
        {
            _updateStockOptionSplitGroupTransactions($record['id'], $strStockId, $strDate, $fRatio, $fPrice);
        }
        mysqli_free_result($result);
    }
}

function _updateStockOptionSplit($ref, $strSymbol, $strStockId, $his_sql, $strDate, $strVal)
{
    if (strpos($strVal, ':') === false)     return;
    $ar = explode(':', $strVal);

    $sql = new StockSplitSql();
    if ($ar[0] == '0' && $ar[1] == '0')
    {
        $sql->DeleteByDate($strStockId, $strDate);
    }
    else
    {
        $fRatio = floatval($ar[0])/floatval($ar[1]);
        if ($sql->InsertDaily($strStockId, $strDate, strval($fRatio)))
        {
            _updateStockOptionSplitTransactions($ref, $strStockId, $his_sql, $strDate, $fRatio);
        }

        $cal_sql = GetCalibrationSql();
        if ($strClosePrev = $cal_sql->GetClosePrev($strStockId, $strDate))
        {
            $strDatePrev = $cal_sql->GetDatePrev($strStockId, $strDate);
            DebugString($strSymbol.' Change calibaration on '.$strDatePrev);
            $fFactor = floatval($strClosePrev) * $fRatio;
            $cal_sql->WriteDaily($strStockId, $strDatePrev, strval($fFactor));
        }
        else
        {
            $arQdii = QdiiGetArray($strSymbol);
            foreach ($arQdii as $strQdii)
            {
                $strQdiiId = SqlGetStockId($strQdii);
                if ($strClosePrev = $cal_sql->GetClosePrev($strQdiiId, $strDate))
                {
                    $strDatePrev = $cal_sql->GetDatePrev($strQdiiId, $strDate);
                    DebugString(__FUNCTION__.' '.$strQdii.' Change calibaration on '.$strDatePrev);
                    $fFactor = floatval($strClosePrev) / $fRatio;
                    $cal_sql->WriteDaily($strQdiiId, $strDatePrev, strval($fFactor));
                }
            }
        }
        _updateStockHistoryAdjCloseBySplit($ref, $strSymbol, $strStockId, $his_sql, $strDate, $fRatio);
    }
}

function _updateStockOptionDividend($ref, $strSymbol, $strStockId, $his_sql, $strDate, $strVal)
{
    $sql = new StockDividendSql();
    if (_updateOptionDailySql($sql, $strStockId, $strDate, $strVal))
    {
        DebugString('Dividend updated');
        $cal_sql = GetCalibrationSql();
        $net_sql = GetNetValueHistorySql();
        if ($strClosePrev = $cal_sql->GetClosePrev($strStockId, $strDate))
        {
            $strDatePrev = $cal_sql->GetDatePrev($strStockId, $strDate);
            DebugString($strSymbol.' Change calibaration on '.$strDatePrev);
            $fNetValue = floatval($net_sql->GetClose($strStockId, $strDatePrev));
            $fNewNetValue = $fNetValue - floatval($strVal);
            $fFactor = floatval($strClosePrev) * $fNetValue / $fNewNetValue;
            $cal_sql->WriteDaily($strStockId, $strDatePrev, strval($fFactor));
        }
        else
        {
            $arQdii = QdiiGetArray($strSymbol);
            foreach ($arQdii as $strQdii)
            {
                $strQdiiId = SqlGetStockId($strQdii);
                if ($strClosePrev = $cal_sql->GetClosePrev($strQdiiId, $strDate))
                {
                    $strDatePrev = $cal_sql->GetDatePrev($strQdiiId, $strDate);
                    DebugString(__FUNCTION__.' '.$strQdii.' Change calibaration on '.$strDatePrev);
                    $fNetValue = floatval($net_sql->GetClose($strStockId, $strDatePrev));
                    $fNewNetValue = $fNetValue - floatval($strVal);
                    $fFactor = floatval($strClosePrev) * $fNewNetValue / $fNetValue;
                    $cal_sql->WriteDaily($strQdiiId, $strDatePrev, strval($fFactor));
                }
            }
        }
        if ($strClosePrev)  $net_sql->WriteDaily($strStockId, $strDatePrev, strval($fNewNetValue));
        _updateStockHistoryAdjCloseByDividend($ref, $strSymbol, $strStockId, $his_sql, $strDate, $strVal);
    }
}

// ---------------------------------------------------------------------------
// 逐例执行 + 快照
// ---------------------------------------------------------------------------
//: mysqli 把 ``double`` 列读成 DB 原样字符串（``"4.569300"``），而 pymysql 给的是
//: Python ``float``（``4.5693``）。两边要能逐格比，就得有个共同的文本形状 ——
//: 用 PHP 自己的 ``strval(floatval($v))``（14 位有效数字，``4.5693``）。
//: ``date`` 列原样留着（``strval(floatval('2026-10-23'))`` 会是 ``"0"``）。
function CellValue($v)
{
    if (preg_match('/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/', $v))     return $v;
    return strval(floatval($v));
}

function SnapshotRows($strQry)
{
    global $g_link;

    $ar = array();
    if ($result = mysqli_query($g_link, $strQry))
    {
        while ($record = mysqli_fetch_assoc($result))
        {
            $ar[] = array_map('CellValue', $record);
        }
        mysqli_free_result($result);
    }
    return $ar;
}

function MakeSnapshot()
{
    global $g_strWatch, $g_strHistoryId;

    //: `time` / `filled` 两列刻意不进快照 —— 那是"跑的时刻"，两边永远不同。
    //: `num` 进：它反映"这一行被写了几次"。
    return array(
        'dividend' => SnapshotRows('SELECT `stock_id`, `date`, `close` FROM `stockdividend` ORDER BY `stock_id`, `date`'),
        'split' => SnapshotRows('SELECT `stock_id`, `date`, `close` FROM `stocksplit` ORDER BY `stock_id`, `date`'),
        'cal' => SnapshotRows('SELECT `stock_id`, `date`, `close`, `num` FROM `calibrationhistory` WHERE `stock_id` IN (' . $g_strWatch . ') ORDER BY `stock_id`, `date`'),
        'nav' => SnapshotRows('SELECT `stock_id`, `date`, `close` FROM `netvaluehistory` WHERE `stock_id` IN (' . $g_strWatch . ') ORDER BY `stock_id`, `date`'),
        //: 复权序列只有**本标的**会被改（拆股/分红不碰配对标的的行情），
        //: 所以按单个 id 过滤 —— 否则 ^NDX 那种 800 多行的会灌满快照。
        'history' => SnapshotRows('SELECT `id`, `stock_id`, `date`, `close`, `adjclose` FROM `stockhistory` WHERE `stock_id` = ' . intval($g_strHistoryId) . ' ORDER BY `date`'),
        'trans' => SnapshotRows('SELECT `groupitem_id`, `quantity`, `price`, `fees` FROM `stocktransaction` ORDER BY `groupitem_id`, `quantity`, `price`, `fees`'),
        'item' => SnapshotRows('SELECT `id`, `stock_id`, `quantity`, `cost`, `record` FROM `stockgroupitem` ORDER BY `id`'),
    );
}

$arInput = json_decode(file_get_contents($argv[1]), true);
if (!$arInput) { fwrite(STDERR, "用例文件读不出来\n"); exit(4); }
$g_arQdii = isset($arInput['qdii_pairs']) ? $arInput['qdii_pairs'] : array();
//: 要快照哪些 stock_id —— 由对端按用例算出（本标的 + 可能被连带改的配对基金）。
$g_strWatch = implode(', ', array_map('intval', $arInput['watch_ids']));
//: 复权序列按单个 id 快照（只有本标的会被改）。
$g_strHistoryId = intval($arInput['history_id']);

//: 每例的 "setup" 是本例专属的库内预置（对端已先把库复原到基线）。
foreach ($arInput['setup'] as $strQry)
{
    if (!mysqli_query($g_link, $strQry))     die_mysql_error('setup: ' . $strQry);
}

$strSymbol = $arInput['symbol'];
$strDate = $arInput['date'];
$strVal = $arInput['value'];
$strType = $arInput['type'];

$ref = null;
$strStockId = SqlGetStockId($strSymbol);
if ($strStockId === false) { fwrite(STDERR, '找不到符号 ' . $strSymbol . PHP_EOL); exit(5); }
$his_sql = GetStockHistorySql();

$mixResult = null;
try
{
    if ($strType == 'dividend')
    {
        $mixResult = _updateStockOptionDividend($ref, $strSymbol, $strStockId, $his_sql, $strDate, $strVal);
    }
    else
    {
        $mixResult = _updateStockOptionSplit($ref, $strSymbol, $strStockId, $his_sql, $strDate, $strVal);
    }
    $strOutcome = 'ok';
}
catch (Throwable $e)
{
    //: 抛异常本身就是要比的结论之一（分母为 0、比例除零），不能打断整轮。
    $strOutcome = 'throw:' . get_class($e) . ':' . $e->getMessage();
}

$arOut = MakeSnapshot();
$arOut['outcome'] = $strOutcome;
$arOut['result'] = is_bool($mixResult) ? ($mixResult ? 'true' : 'false') : ($mixResult === null ? 'void' : strval($mixResult));

mysqli_close($g_link);

echo json_encode($arOut, JSON_UNESCAPED_UNICODE);
