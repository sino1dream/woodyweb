<?php
// ---------------------------------------------------------------------------
// 用**真实 PHP** 跑 `_updateStockOptionCalibration()` 的完整分支，逐例比对
// Python 的 `web/services/stock_option_service.py::_write_calibration`。
//
//   PALMMICRO_DB_PASSWORD=... php -d display_errors=stderr \
//       _php_calibration_write_check.php _calibration_write_cases.json
//
// 对端：`_php_calibration_write_compare.py`（生成用例 + 跑 Python + 比对）
//
// ## 这一次要对照什么
//
// 「手工校准值」这一项填的是**估算值**，落库前要换算：
//
//   * 境内指数基金     估算值 ÷ 基金净值（汇率取 1.0）
//   * QDII 四个地区    估算值 × 地区中间价 ÷ 基金净值
//
// 再叠上 `_updateOptionDailySql()` 的"空值（含 '0'）→ 删除"。这几处一旦搬错，
// 落库的校准值会悄悄差一个汇率因子 —— 页面上的估算值只是"差几个点"，
// 而校准值是所有估值的地基。
//
// 所以这里比三样东西：**换算出来的值 + 校准表的整表快照 + lastcalibration 快照**。
// 空值分支要同时看后者：PHP 先删 `lastcalibration`（整只标的一条、不分日期），
// 再删当天的 `calibrationhistory` 行，少删一处就会让估值继续拿旧值兜底。
//
// ## 三个"为什么不能直接 require"
//
// 1. `php/sql.php`（`GetCalibrationSql()` / `GetNetValueHistorySql()` 的出处）
//    include 了 email / httplink / ui，CLI 起不来 —— 所以本脚本自己 new 探针类，
//    但**方法体用的是真身**（`DailyTimeSql::WriteDaily` / `ValSql::DeleteById`）；
// 2. `php/stock/qdiiref.php` 里除了一行 `QdiiGetCalibration()` 还挂着
//    `class QdiiReference extends FundReference` 的整条类链，require 会连坐
//    UI。那行公式是 `floatval($a) * floatval($b) / floatval($c)`，这里内联，
//    并在用例里对同一个式子逐例核对（数值必须与 Python 完全相等）；
// 3. `SqlGetNetValueByDate()` 只是 `GetNetValueHistorySql()->GetClose()` 的包装
//    （`sqlstocksymbol.php:301`），而 `GetClose()` 是 `DailyCloseSql` 的真方法 ——
//    用一个 `ProbeDailySql('netvaluehistory')` 就等价地拿到真身。
//
// ## 三张临时表，真库只被读
//
// `cw_probe_cal` / `cw_probe_last` / `cw_probe_nav` 由本脚本建、跑完删。为什么
// 净值也要临时表：演示库只有 179 个符号，没有 A 股个股、没有境内指数基金、也没有
// 商品期货基金，而这几支恰好各走一条不同分支（`in_arrayChinaIndex` 汇率写死
// `'1.0'`、`in_arrayChinaFuture` 直接 return、其余 unhandled）。真库里凑不出来，
// 又不能往真表里塞种子。`stock` 表仍然真查（就是 `SqlGetStockId()` 的真身），
// 合成符号的 id 由用例给。
//
// ## 假时钟
//
// `DebugGetTime()`（`php/debug.php:107`）被换成本例归零的计数器，一次写 =
// `09:00:01`、两次 = `09:00:02`。这样"到底发了几条写语句"也进了快照 ——
// 只看 `close` / `num` 是分不出"值没变不发 UPDATE"和"覆盖成同一个值"的。
//
// ## 注意
//
// * 本文件**不能出现 PHP 结束标签**（注释里也不行）。
// * 探针类的 `Create()` 全部短路：`TableSql::__construct` 无条件调它，且
//   `CreateTable()` 把库名硬编码成生产库（见 pitfalls #131）。
// * `_probeUpdateCalibration()` 外面套了 try/catch：`缺基金净值` 那一例在 PHP 8
//   上会抛 `DivisionByZeroError`（分母是 `floatval(false)`），而这一抛本身就是
//   要比的结论之一，不能让它把整轮对照打断。

$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }
if (!isset($argv[1])) { fwrite(STDERR, "用法: php _php_calibration_write_check.php <cases.json>\n"); exit(2); }

define('MIN_FLOAT_VAL', 0.0000005);
define('CW_CAL_TABLE', 'cw_probe_cal');
define('CW_LAST_TABLE', 'cw_probe_last');
define('CW_NAV_TABLE', 'cw_probe_nav');

$g_iClock = 0;

function DebugGetTime()
{
    global $g_iClock;
    $g_iClock++;
    return sprintf('09:00:%02d', $g_iClock);
}

function DebugString($str, $bAdminOnly = false)
{
    fwrite(STDERR, 'DEBUG ' . $str . PHP_EOL);
}

function DebugVal($iVal, $strPrefix = false, $bAdminOnly = false)
{
    fwrite(STDERR, 'DEBUG ' . ($strPrefix === false ? '' : $strPrefix . ' ') . $iVal . PHP_EOL);
}

// 下面三个是 `php/sql.php` 里的全局查询函数，**逐句照抄**。
// `TableSql::GetSingleData()` → `SqlGetSingleTableData()` → `SqlGetSingleTableData`
// 这条链是 `GetRecord()` / `GetClose()` 的必经之路，而 `sql.php` 又 include 了
// email / httplink / ui，CLI 起不来 —— 只能把那三个函数搬过来，方法体没改一个字。
function die_mysql_error($str)
{
    global $g_link;
    $str .= ' ' . mysqli_error($g_link);
    fwrite(STDERR, 'SQL FAIL ' . $str . PHP_EOL);
    die($str);
}

function SqlGetTableData($strTableName, $strWhere = false, $strOrder = false, $strLimit = false)
{
    global $g_link;

    $strQry = 'SELECT * FROM ' . $strTableName . _SqlAddWhere($strWhere) . _SqlAddOrder($strOrder) . _SqlAddLimit($strLimit);
    if ($result = mysqli_query($g_link, $strQry))
    {
        if (mysqli_num_rows($result) > 0)
        {
            return $result;
        }
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

// `DeleteData()` 先数再删（所以空值分支的"删了几行"也来自这里）。
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

set_include_path(
    $phpDir . DIRECTORY_SEPARATOR . 'sql' . PATH_SEPARATOR
    . $phpDir . DIRECTORY_SEPARATOR . 'class' . PATH_SEPARATOR
    . get_include_path()
);
require_once($phpDir . '/class/year_month_day.php');
require_once($phpDir . '/sql/_sqlcommon.php');
require_once($phpDir . '/sql/sqldailyclose.php');
require_once($phpDir . '/sql/sqldailytime.php');
require_once($phpDir . '/sql/sqlval.php');
// 四个 in_arrayQdii* / in_arrayChinaIndex / in_arrayChinaFuture 是真身
// （`php/stock/stocksymbol.php` 自包含，没有 require）。
require_once($phpDir . '/stock/stocksymbol.php');

// CalibrationSql 的构造器**不接受表名**（表名写死 calibrationhistory），
// 所以必须绕到上游 DailyTimeSql::__construct($table, $keyPrefix)（pitfalls #132）。
// **但类要继承 CalibrationSql 而不是 DailyTimeSql**：`num` 列只在
// `CalibrationSql::InsertDaily()` 里写（`sqldailytime.php:44`），继承错一层的话
// INSERT 会漏掉 `num`，`NOT NULL` 列被 MySQL 悄悄填 0 —— 快照一眼就能看出来。
class ProbeCalibrationSql extends CalibrationSql
{
    public function __construct($strTable)  {   DailyTimeSql::__construct($strTable, 'stock');  }
    public function Create()                {   return true;                                    }
}

class ProbeLastCalibrationSql extends ValSql
{
    public function __construct($strTable)  {   parent::__construct($strTable);     }
    public function Create()                {   return true;                        }
}

class ProbeNetValueSql extends DailyCloseSql
{
    public function __construct($strTable)  {   parent::__construct($strTable);     }
    public function Create()                {   return true;                        }
}

// SqlGetNetValueByDate() 的等价物：`GetNetValueHistorySql()->GetClose()`。
// 用的就是真身 GetClose()，只是表换成临时表、跳过了那个起不来的全局工厂。
function _probeGetNetValue($strStockId, $strDate)
{
    static $net_sql = null;
    if ($net_sql === null)      $net_sql = new ProbeNetValueSql(CW_NAV_TABLE);
    return $net_sql->GetClose($strStockId, $strDate);
}

// `_updateOptionDailySql()`（`_submitstockoptions.php:208`）的等价物：
// empty($strVal) → DeleteByDate，否则 WriteDaily。
function _probeUpdateOptionDailySql($sql, $strStockId, $strDate, $strVal)
{
    if (empty($strVal))
    {
        $sql->DeleteByDate($strStockId, $strDate);
        return false;
    }
    return $sql->WriteDaily($strStockId, $strDate, $strVal);
}

// 逐句照抄 `_updateStockOptionCalibration()`（`_submitstockoptions.php:340`），
// 只把三处不可 include 的东西换成等价物（见文件头的三个"为什么"）。
// 返回：换算后的校准值字符串；被"unhandled"挡下时返回 false。
function _probeUpdateCalibration($strSymbol, $strStockId, $strDate, $strVal)
{
    DebugString($strSymbol . ' ' . $strDate . ' ' . $strVal);
    $strResult = $strVal;
    if (empty($strVal))
    {
        $last_sql = new ProbeLastCalibrationSql(CW_LAST_TABLE);
        $last_sql->DeleteById($strStockId);
    }
    else
    {
        $strNetValue = _probeGetNetValue($strStockId, $strDate);
        if ($strSymbol == 'INDA' || $strSymbol == 'ASHR')
        {
            return false;       // 要先抓 Yahoo 官方净值，本例不覆盖（Python 侧同样拒绝）
        }
        else if ($strSymbol == 'hf_CHA50CFD')
        {
            return false;
        }
        else
        {
            if (in_arrayChinaFuture($strSymbol))
            {
                DebugString(__FUNCTION__ . ' unhandled China future symbol: ' . $strSymbol);
                return false;
            }
            else if (in_arrayChinaIndex($strSymbol))
            {
                $strCNY = '1.0';
            }
            else if (in_arrayQdii($strSymbol))      $strCNY = _probeGetNetValue(SqlGetStockIdByName('USCNY'), $strDate);
            else if (in_arrayQdiiHk($strSymbol))    $strCNY = _probeGetNetValue(SqlGetStockIdByName('HKCNY'), $strDate);
            else if (in_arrayQdiiJp($strSymbol))    $strCNY = _probeGetNetValue(SqlGetStockIdByName('JPCNY'), $strDate);
            else if (in_arrayQdiiEu($strSymbol))    $strCNY = _probeGetNetValue(SqlGetStockIdByName('EUCNY'), $strDate);
            else
            {
                DebugString(__FUNCTION__ . ' unhandled other symbol: ' . $strSymbol);
                return false;
            }

            if ($strCNY == false)       return false;
            // QdiiGetCalibration($strEst, $strCNY, $strNetValue) 的真身（qdiiref.php:3）
            $strResult = strval(floatval($strVal) * floatval($strCNY) / floatval($strNetValue));
        }
    }

    _probeUpdateOptionDailySql(new ProbeCalibrationSql(CW_CAL_TABLE), $strStockId, $strDate, $strResult);
    return $strResult;
}

// `SqlGetStockId()` 的等价物：只查 stock 表，不依赖全局工厂。
function SqlGetStockIdByName($strSymbol)
{
    global $g_link;
    $strSymbol = mysqli_real_escape_string($g_link, $strSymbol);
    $result = mysqli_query($g_link, "SELECT `id` FROM `stock` WHERE `symbol` = '$strSymbol' LIMIT 1");
    if ($result && ($record = mysqli_fetch_assoc($result)))
    {
        mysqli_free_result($result);
        return $record['id'];
    }
    return false;
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

// 快照：数值列统一成"最简文本"，与 Python 侧 _php_strval_float 同一个口径。
// `time` 是动态值，不进快照。
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
            $arRow[$key] = is_numeric($value) ? strval(floatval($value)) : strval($value);
        }
        $arOut[] = $arRow;
    }
    mysqli_free_result($result);
    return $arOut;
}

$arInput = json_decode(file_get_contents($argv[1]), true);
if (!is_array($arInput) || !isset($arInput['cases']))
{
    fwrite(STDERR, "用例文件解析失败（需要 cases 段）\n");
    exit(5);
}

BuildTable(CW_CAL_TABLE,
    '`id` int(10) unsigned NOT NULL AUTO_INCREMENT, `stock_id` int(10) unsigned NOT NULL, '
    . '`date` date NOT NULL, `close` double(13,6) NOT NULL, `time` time NOT NULL, '
    . '`num` int(10) unsigned NOT NULL, PRIMARY KEY (`id`), UNIQUE KEY `date` (`date`, `stock_id`), '
    . 'KEY `stock_id` (`stock_id`)');
BuildTable(CW_LAST_TABLE, '`id` int(10) unsigned NOT NULL, `close` double(13,6) NOT NULL, PRIMARY KEY (`id`)');
BuildTable(CW_NAV_TABLE,
    '`id` int(10) unsigned NOT NULL AUTO_INCREMENT, `stock_id` int(10) unsigned NOT NULL, '
    . '`date` date NOT NULL, `close` double(13,6) NOT NULL, PRIMARY KEY (`id`), '
    . 'UNIQUE KEY `date` (`date`, `stock_id`), KEY `stock_id` (`stock_id`)');

$arOut = array();
foreach ($arInput['cases'] as $arCase)
{
    // 每例自己带种子：净值 / 校准 / 末值三张表各播一遍，时钟归零。
    SeedRows(CW_NAV_TABLE, $arCase['nav'], '`stock_id`, `date`, `close`');
    SeedRows(CW_CAL_TABLE, $arCase['seed_cal'], '`stock_id`, `date`, `close`, `time`, `num`');
    SeedRows(CW_LAST_TABLE, $arCase['seed_last'], '`id`, `close`');
    $g_iClock = 0;

    $result = false;
    try
    {
        for ($i = 0; $i < intval($arCase['times']); $i++)
        {
            $result = _probeUpdateCalibration($arCase['symbol'], $arCase['stock_id'], $arCase['date'], $arCase['value']);
        }
    }
    catch (Throwable $e)
    {
        // 分母为 0 那一例（见文件头的"假时钟"后面那段）：抛异常本身就是结论。
        $result = 'throw:' . get_class($e) . ':' . $e->getMessage();
    }

    $arOut[] = array(
        'name' => $arCase['name'],
        'result' => is_bool($result) ? ($result ? 'true' : 'false') : strval($result),
        'cal' => SnapshotRows(CW_CAL_TABLE, '`stock_id`, `date`, `close`, `time`, `num`', '`date`'),
        'last' => SnapshotRows(CW_LAST_TABLE, '`id`, `close`', '`id`'),
    );
}

mysqli_query($g_link, 'DROP TABLE IF EXISTS ' . CW_CAL_TABLE);
mysqli_query($g_link, 'DROP TABLE IF EXISTS ' . CW_LAST_TABLE);
mysqli_query($g_link, 'DROP TABLE IF EXISTS ' . CW_NAV_TABLE);
mysqli_close($g_link);

echo json_encode($arOut, JSON_UNESCAPED_UNICODE);
