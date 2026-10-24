<?php
// ---------------------------------------------------------------------------
// 对着**真实 PHP** 跑 ETF 持仓申赎清单的下载解析与落库
//
//   /d/xampp/php/php -n -d display_errors=stderr _php_etfholdings_check.php <用例>
//
// 对照对象（``python/auto/updater/etfholdings.py`` 与
// ``holdingsupdater.py``）：
//
// 1. ``ReadSzseHoldingsFile()`` —— 深市 PCF 文本逐行解析（真实样本：
//    ``_scratch/_szse_159605.txt``，2025-09-01 的 159605 申赎清单）；
// 2. ``ReadSseHoldingsFile()``  —— 沪市 XML 解析（真实样本：
//    ``_scratch/_sse_513050.xml``，query.sse.com.cn 现抓的 513050）；
// 3. ``UpdateStockOptionHoldings()`` / ``StockOptionDecodeHoldings()``
//    —— 管理员提交的编码串写回明细与仓位（``submitholdings.php`` 那条路）。
//
// ## 手法：类/函数体从 PHP 源码现场切出（不是手抄）
//
// 与 ``_php_holdingscn_check.php`` 同一套：按花括号配对把
// ``_HoldingsCsvFile`` / ``_EtfHoldingsFile`` / ``_SzseHoldingsFile`` 三个类
// 与相关自由函数**原样**切进 ``namespace Harness``，只 stub 非被测的
// 数据库全局（``GetStockSql`` 一族）与两个 IO 入口
// （``StockDebugXml`` / ``StockSaveDebugCsv`` 直接读硬盘上的样本文件，
// 不走网络）。
//
// ## 两个有意的替身
//
// * ``GbToUtf8`` 用 ``iconv('GB2312', 'UTF-8', ...)`` 顶替 —— 真实实现走
//   ``GB2312Sql`` 映射表（``php/gb2312.php:35``），要连库。Python 侧用
//   ``gb18030`` 解码，两者对合法 GB2312 字节完全一致（实测样本逐字相同）。
// * ``HoldingsDateSql`` 是假类（真实继承链 ``DateSql -> ValSql -> TableSql``
//   构造函数就建表连库），只录 ``WriteDate`` / ``ReadDate``。
//
// ## 输出
//
// 一个 JSON：``{symbols, holdings, holdingsdate, positions, cash, sum, date,
// report, done}`` —— Python 侧用同一批假 sql 对象录同一份结构，逐键比对。
//
// **本文件不能出现 PHP 结束标签**（注释里也不行）—— 解析器会在那里结束
// 脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

function ReadSource($rootDir, $relPath)
{
    $path = $rootDir . '/' . $relPath;
    $text = file_get_contents($path);
    if ($text === false) { fwrite(STDERR, "读不到 $path\n"); exit(2); }
    return $text;
}

function ExtractFunction($text, $name)
{
    // 匹配 `function 名字(...) {` 起手，按花括号配对吃到函数结束。
    // ``\r?`` 不能省：PHP 源码是 Windows 换行（实测踩到）。
    $pattern = '/(?:^|\n)[ \t]*(?:public |protected |private |static )*function[ \t]+'
             . preg_quote($name, '/') . '[ \t]*\([^)]*\)[ \t]*\r?\n?[ \t]*\{/';
    if (!preg_match($pattern, $text, $m, PREG_OFFSET_CAPTURE)) { return null; }
    $start = $m[0][1];
    $i = $m[0][1] + strlen($m[0][0]) - 1;   // 指向起手的 '{'
    $depth = 0;
    $len = strlen($text);
    while ($i < $len)
    {
        if ($text[$i] === '{')      { $depth++; }
        elseif ($text[$i] === '}')  { $depth--; if ($depth === 0) { $i++; break; } }
        $i++;
    }
    return substr($text, $start, $i - $start);
}

function ExtractClass($text, $name)
{
    // 与 ExtractFunction 同款，只是起手换成 `class 名字 ... {`。
    // 两个坑：① 声明可能带 extends（``_SzseHoldingsFile extends
    // _EtfHoldingsFile``）；② 本仓库的类**起手花括号都在下一行**
    // （``class CsvFile\n{``），所以签名段要允许跨一个换行。
    $pattern = '/(?:^|\n)[ \t]*(?:final |abstract )*class[ \t]+'
             . preg_quote($name, '/') . '(?:[ \t]+[^\n{]*)?\r?\n?[ \t]*\{/';
    if (!preg_match($pattern, $text, $m, PREG_OFFSET_CAPTURE)) { return null; }
    $start = $m[0][1];
    $i = $m[0][1] + strlen($m[0][0]) - 1;
    $depth = 0;
    $len = strlen($text);
    while ($i < $len)
    {
        if ($text[$i] === '{')      { $depth++; }
        elseif ($text[$i] === '}')  { $depth--; if ($depth === 0) { $i++; break; } }
        $i++;
    }
    return ltrim(substr($text, $start, $i - $start));
}

$src = array(
    'csvfile'    => ReadSource($rootDir, 'php/csvfile.php'),
    'holdingscsv'=> ReadSource($rootDir, 'woody/res/php/_holdingscsvfile.php'),
    'etf'        => ReadSource($rootDir, 'woody/res/php/_etfholdings.php'),
    'szse'       => ReadSource($rootDir, 'woody/res/php/_szseholdings.php'),
    'sse'        => ReadSource($rootDir, 'woody/res/php/_sseholdings.php'),
    'update'     => ReadSource($rootDir, 'woody/res/php/_updateholdings.php'),
    'externalurl'=> ReadSource($rootDir, 'php/externalurl.php'),
    'ymd'        => ReadSource($rootDir, 'php/class/year_month_day.php'),
    'debug'      => ReadSource($rootDir, 'php/debug.php'),
    'stocksymbol'=> ReadSource($rootDir, 'php/stock/stocksymbol.php'),
    'stock'      => ReadSource($rootDir, 'php/stock.php'),
);

$classes = array();
$functions = array();

// -- 类（顺序即继承顺序：先基类后子类）--------------------------------------
foreach (array(
    array('src' => 'csvfile',     'name' => 'CsvFile'),
    array('src' => 'csvfile',     'name' => 'DebugCsvFile'),
    array('src' => 'holdingscsv', 'name' => '_HoldingsCsvFile'),
    array('src' => 'etf',         'name' => '_EtfHoldingsFile'),
    array('src' => 'szse',        'name' => '_SzseHoldingsFile'),
) as $item)
{
    $body = ExtractClass($src[$item['src']], $item['name']);
    if ($body === null)
    {
        fwrite(STDERR, sprintf("切不出类 %s（来自 %s）\n", $item['name'], $item['src']));
        exit(2);
    }
    $classes[] = $body;
}

// -- 自由函数 ---------------------------------------------------------------
foreach (array(
    array('src' => 'szse',        'name' => 'ReadSzseHoldingsFile'),
    array('src' => 'sse',         'name' => 'ReadSseHoldingsFile'),
    array('src' => 'update',      'name' => 'StockOptionDecodeHoldings'),
    array('src' => 'update',      'name' => 'UpdateStockOptionHoldings'),
    array('src' => 'externalurl', 'name' => 'GetSzseEtfFileName'),
    array('src' => 'externalurl', 'name' => 'GetSzseHoldingsUrl'),
    array('src' => 'externalurl', 'name' => 'GetSzseUrl'),
    array('src' => 'externalurl', 'name' => 'GetSseUrl'),
    array('src' => 'ymd',         'name' => 'ConvertYMD'),
    array('src' => 'debug',       'name' => 'DebugDecode'),
    array('src' => 'stock',       'name' => 'StockGetSymbol'),
    array('src' => 'stocksymbol', 'name' => 'IsChinaStockDigit'),
    array('src' => 'stocksymbol', 'name' => '_isDigitShangHaiEtf'),
    array('src' => 'stocksymbol', 'name' => '_isDigitShangHaiLof'),
    array('src' => 'stocksymbol', 'name' => '_isDigitShenZhenEtf'),
    array('src' => 'stocksymbol', 'name' => '_isDigitShenZhenLof'),
    array('src' => 'stocksymbol', 'name' => '_isDigitShangHaiFund'),
    array('src' => 'stocksymbol', 'name' => '_isDigitShenZhenFund'),
    array('src' => 'stocksymbol', 'name' => 'BuildChinaFundSymbol'),
    array('src' => 'stocksymbol', 'name' => 'BuildChinaStockSymbol'),
    array('src' => 'stocksymbol', 'name' => 'BuildHongkongStockSymbol'),
) as $item)
{
    $body = ExtractFunction($src[$item['src']], $item['name']);
    if ($body === null)
    {
        fwrite(STDERR, sprintf("切不出函数 %s（来自 %s）\n", $item['name'], $item['src']));
        exit(2);
    }
    $functions[] = $body;
}

// -- 假数据库（全局，供 Harness 命名空间里的代码回落调用）---------------------
class FakeStockSql
{
    public $arId = array();      // symbol => id
    public $arName = array();    // symbol => name
    public $iNext = 1000;
    public $arInsert = array();  // 逐次记录 InsertSymbol 的调用

    public function GetId($strSymbol)
    {
        return isset($this->arId[$strSymbol]) ? $this->arId[$strSymbol] : false;
    }
    public function GetName($strSymbol)
    {
        return isset($this->arName[$strSymbol]) ? $this->arName[$strSymbol] : false;
    }
    public function InsertSymbol($strSymbol, $strName)
    {
        $this->arInsert[] = array($strSymbol, $strName);
        if (isset($this->arId[$strSymbol]))   return false;
        $this->arId[$strSymbol] = (string)(++$this->iNext);
        $this->arName[$strSymbol] = $strName;
        return $this->arId[$strSymbol];
    }
}

class FakeHoldingsSql
{
    public $ar = array();        // [[stock_id, holding_id, ratio], ...] 保序
    public function InsertHoldingId($strStockId, $strHoldingId, $strRatio)
    {
        $this->ar[] = array((string)$strStockId, (string)$strHoldingId, (string)$strRatio);
        return count($this->ar);
    }
    public function DeleteAll($strStockId)
    {
        $this->ar = array_values(array_filter($this->ar, function ($row) use ($strStockId) {
            return $row[0] !== (string)$strStockId;
        }));
        return true;
    }
    public function GetHoldingsArray($strStockId)
    {
        $ar = array();
        foreach ($this->ar as $row)
        {
            if ($row[0] === (string)$strStockId)   $ar[$row[1]] = $row[2];
        }
        return $ar;
    }
}

class FakeHistorySql
{
    //: (stock_id, date) => close；深市样本要能回答"2025-08-28 的复权价"
    public $arClose = array();
    public function GetRecord($strStockId, $strDate)
    {
        $key = $strStockId.'|'.$strDate;
        return isset($this->arClose[$key]) ? array('close' => $this->arClose[$key]) : false;
    }
    public function GetAdjClose($strStockId, $strDate, $bStrict = false)
    {
        if ($record = $this->GetRecord($strStockId, $strDate))     return $record['close'];
        if ($bStrict === false)
        {
            // 回落上一个有记录的日期（PHP 同款：when hongkong market on holiday）
            $fBest = false; $strBest = false;
            foreach ($this->arClose as $key => $fClose)
            {
                list($strId, $strDay) = explode('|', $key);
                if ($strId !== (string)$strStockId)     continue;
                if ($strDay >= $strDate)                continue;
                if ($strBest === false || $strDay > $strBest) { $strBest = $strDay; $fBest = $fClose; }
            }
            if ($strBest !== false)     return $fBest;
        }
        return false;
    }
}

class FakeNetValueSql
{
    public $arClose = array();    // (stock_id, date) => close
    public $arNow = array();      // stock_id => close
    public function GetClose($strStockId, $strDate)
    {
        $key = $strStockId.'|'.$strDate;
        return isset($this->arClose[$key]) ? $this->arClose[$key] : false;
    }
    public function GetCloseNow($strStockId)
    {
        return isset($this->arNow[$strStockId]) ? $this->arNow[$strStockId] : false;
    }
}

class FakePositionSql
{
    public $arPos = array();      // stock_id => 写入值（字符串原样）
    public function WriteVal($strId, $strVal, $bString = false)
    {
        $this->arPos[(string)$strId] = (string)$strVal;
        return true;
    }
    public function WritePos($strStockId, $fPos)
    {
        return $this->WriteVal($strStockId, strval($fPos));
    }
}

class FakeHoldingsDateSql
{
    //: **静态存储**：``new HoldingsDateSql()``（class_alias 桥接到本类）建出
    //: 的实例与 ``$GLOBALS['fake_date']`` 是不同对象，实例属性各写各的 ——
    //: UpdateHoldingsDate 写进新实例、输出读全局，holdingsdate 恒为空
    //: （实测踩到）。静态属性让两边共享同一份记录。
    public static $arDate = array();     // stock_id => date
    public function WriteDate($strId, $strDate)
    {
        self::$arDate[(string)$strId] = (string)$strDate;
        return true;
    }
    public function ReadDate($strId)
    {
        return isset(self::$arDate[(string)$strId]) ? self::$arDate[(string)$strId] : false;
    }
}

$GLOBALS['fake_stock']    = new FakeStockSql();
$GLOBALS['fake_holdings'] = new FakeHoldingsSql();
$GLOBALS['fake_history']  = new FakeHistorySql();
$GLOBALS['fake_netvalue'] = new FakeNetValueSql();
$GLOBALS['fake_position'] = new FakePositionSql();
$GLOBALS['fake_date']     = new FakeHoldingsDateSql();

function GetStockSql()            { return $GLOBALS['fake_stock']; }
function GetHoldingsSql()         { return $GLOBALS['fake_holdings']; }
function GetStockHistorySql()     { return $GLOBALS['fake_history']; }
function GetNetValueHistorySql()  { return $GLOBALS['fake_netvalue']; }
function GetPositionSql()         { return $GLOBALS['fake_position']; }
function SqlGetStockId($strSymbol){ return $GLOBALS['fake_stock']->GetId($strSymbol); }

function DebugString($str)  { }
function DebugVal($f, $str = '') { }
function DebugPrint($ar)    { }
function GetMyStockLink($strSymbol) { return $strSymbol; }
function DebugGetPath($strName)     { return sys_get_temp_dir(); }
function DebugGetPathName($strFileName) { return sys_get_temp_dir().'/'.$strFileName; }

//: 真实 GbToUtf8 走 GB2312Sql 映射表（要连库）；iconv 对合法 GB2312 等价
function GbToUtf8($str)
{
    $strUtf8 = @iconv('GB2312', 'UTF-8', $str);
    return $strUtf8 === false ? $str : $strUtf8;
}

//: 两个下载入口：直接读 _scratch 里的样本文件，不走网络。
//: StockSaveDebugCsv 照 PHP 的行为**把内容写到缓存路径**再返回 ——
//: 下游 ``CsvFile::Read()`` 是从那个路径 fopen 回读的，不落盘就白下。
function StockDebugXml($strPathName, $strUrl, $iInterval = 60, $arExtraHeaders = false, $strFileName = false)
{
    $strPath = __DIR__ . '/_sse_513050.xml';
    if (file_exists($strPath) === false)     return false;
    return simplexml_load_string(file_get_contents($strPath));
}
function StockSaveDebugCsv($strFileName, $strUrl, $arExtraHeaders = false)
{
    $strSrc = __DIR__ . '/_szse_159605.txt';
    if (file_exists($strSrc) === false)     return false;
    $strContent = file_get_contents($strSrc);
    file_put_contents(DebugGetPath('csv') . '/' . $strFileName, $strContent);
    return $strContent;
}

// -- 组装 Harness 命名空间 ----------------------------------------------------
$harnessCode = "namespace Harness;\n"
    . "const SH_PREFIX = 'SH';\n"
    . "const SZ_PREFIX = 'SZ';\n"
    . "const BJ_PREFIX = 'BJ';\n"
    . "const MIN_FLOAT_VAL = 0.0000005;\n"
    . "\n";
foreach ($classes as $body)    { $harnessCode .= $body . "\n\n"; }
foreach ($functions as $body)  { $harnessCode .= $body . "\n\n"; }
eval($harnessCode);

//: ``_holdingscsvfile.php:53`` 与 ``_updateholdings.php:39/56`` 是
//: ``new HoldingsDateSql()`` **直接实例化**（真实构造走 DateSql->ValSql->
//: TableSql 会建表连库）。eval 出来的代码在 Harness 命名空间，用别名
//: 桥接到上面的假类 —— 假类没有构造函数，``new`` 不会碰库。
class_alias('FakeHoldingsDateSql', 'Harness\\HoldingsDateSql');

// -- 深市样本的预置数据（Python 侧逐字一致）-----------------------------------
// 159605（互联中概 ETF）2025-09-01 的 PCF 里列出了 31 只港股 + A 股。
// 复权价按"持仓日 2025-08-28"给一份**假**数据 —— 两边同一份，绝对值不
// 影响对照（要的是一致的乘法与除法）。
function PresetDemoData()
{
    // **键是 stock_id 不是符号** —— CalcCurrency 先 GetId('USCNY') 拿 id
    // （'501'/'502'），再拿 id 查 netvaluehistory。用符号名当天查不到，
    // 两个汇率都会是 0，PHP 当场 DivisionByZeroError（实测踩到）。
    $GLOBALS['fake_netvalue']->arClose = array(
        '501|2025-08-28' => '7.1200',
        '502|2025-08-28' => '0.9100',
    );
    $GLOBALS['fake_netvalue']->arNow = array('501' => '7.1300', '502' => '0.9150');
    // USCNY / HKCNY 两个符号要先在 stock 表里（CalcCurrency 要 GetId）
    $GLOBALS['fake_stock']->arId['USCNY'] = '501';
    $GLOBALS['fake_stock']->arId['HKCNY'] = '502';
    // **31 个成分符号也要预置** —— ``OnLineArray`` 里 GetMarketVal 发生在
    // AddHolding→InsertSymbol **之前**（``_szseholdings.php:57`` 先算市值、
    // ``:58`` 才 AddHolding 落库）。假 stock 表若开局为空，SqlGetStockId
    // 全部落空、市值恒 0。生产库里这些符号早就建过，照生产状态预置。
    // id 顺序即 PCF 文件里的出现顺序（159900 申赎现金被跳过，不占 id）。
    $arPcfHoldings = array(
        array('01024', '快手-W'),      array('01810', '小米集团-W'),
        array('00241', '阿里健康'),    array('02423', '贝壳-W'),
        array('02587', '健康之路'),    array('03690', '美团-W'),
        array('03888', '金山软件'),    array('06060', '众安在线'),
        array('06618', '京东健康'),    array('00700', '腾讯控股'),
        array('00772', '阅文集团'),    array('00780', '同程旅行'),
        array('09618', '京东集团-SW'), array('09626', '哔哩哔哩-W'),
        array('09888', '百度集团-SW'), array('09899', '网易云音乐'),
        array('09961', '携程集团-S'),  array('09988', '阿里巴巴-W'),
        array('09999', '网易-S'),
        array('ATHM', 'ATHM'),   array('BZ', 'BZ'),     array('IQ', 'IQ'),
        array('JOYY', 'JOYY'),   array('PDD', 'PDD'),   array('QFIN', 'QFIN'),
        array('TAL', 'TAL'),     array('TME', 'TME'),   array('VIPS', 'VIPS'),
        array('WB', 'WB'),       array('YMM', 'YMM'),
    );
    $i = 1000;
    foreach ($arPcfHoldings as $arHolding)
    {
        $GLOBALS['fake_stock']->arId[$arHolding[0]] = (string)(++$i);
        $GLOBALS['fake_stock']->arName[$arHolding[0]] = $arHolding[1];
    }
    // 预置是直接写 arId 的，没走 InsertSymbol 的自增 —— 必须把 iNext 推到
    // 已占用的最大值之后，否则 sse_xml 用例里新符号从 1001 重新起号、
    // 与预置符号撞 id（实测踩到）。
    $GLOBALS['fake_stock']->iNext = $i;
    // 样本里出现的成分股：给 2025-08-28 的复权价
    $GLOBALS['fake_history']->arClose = array(
        '1001|2025-08-28' => '55.10',    // 01024 快手-W
        '1002|2025-08-28' => '48.20',    // 01810 小米集团-W
        '1003|2025-08-28' => '4.35',     // 00241 阿里健康
        '1004|2025-08-28' => '38.90',    // 02423 贝壳-W
        '1005|2025-08-28' => '11.20',    // 02587 健康之路
        '1006|2025-08-28' => '98.40',    // 03690 美团-W
        '1007|2025-08-28' => '31.50',    // 03888 金山软件
        '1008|2025-08-28' => '13.60',    // 06060 众安在线
        '1009|2025-08-28' => '36.80',    // 06618 京东健康
        '1010|2025-08-28' => '420.00',   // 00700 腾讯控股
    );
}

// -- 用例 --------------------------------------------------------------------
$caseName = isset($argv[1]) ? $argv[1] : '';
if ($caseName === '')
{
    fwrite(STDERR, "用法: php _php_etfholdings_check.php <szse_pcf|sse_xml|admin_submit>\n");
    exit(2);
}

PresetDemoData();

if ($caseName === 'szse_pcf')
{
    // 真实函数端到端：ReadSzseHoldingsFile 内部 new _SzseHoldingsFile 并 Read()
    $bDone = Harness\ReadSzseHoldingsFile('SZ159605', '9001', '2025-09-01');
    $out = array(
        'return'      => $bDone,
        'symbols'     => $GLOBALS['fake_stock']->arId,
        'names'       => $GLOBALS['fake_stock']->arName,
        'insert_calls'=> $GLOBALS['fake_stock']->arInsert,
        'holdings'    => $GLOBALS['fake_holdings']->ar,
        'holdingsdate'=> FakeHoldingsDateSql::$arDate,
        'positions'   => $GLOBALS['fake_position']->arPos,
    );
    echo json_encode($out), "\n";
}
elseif ($caseName === 'sse_xml')
{
    $bDone = Harness\ReadSseHoldingsFile('SH513050', '9002');
    $out = array(
        'return'      => $bDone,
        'symbols'     => $GLOBALS['fake_stock']->arId,
        'names'       => $GLOBALS['fake_stock']->arName,
        'insert_calls'=> $GLOBALS['fake_stock']->arInsert,
        'holdings'    => $GLOBALS['fake_holdings']->ar,
        'holdingsdate'=> FakeHoldingsDateSql::$arDate,
        'positions'   => $GLOBALS['fake_position']->arPos,
    );
    echo json_encode($out), "\n";
}
elseif ($caseName === 'admin_submit')
{
    // UpdateStockOptionHoldings($strStockId, $strDate, $strVal)
    // 入参形状：DebugDecode 掉的 JSON 对象体（无花括号）
    // GLD/SPY 预置进 stock 表 —— 生产库里有这两个符号，GetId 才解析得出；
    // 不预置的话 PHP 会把两个都没解析出的条目都写到 holding_id=0
    // （$arRatio[false] 的键就是 0，后写的覆盖先写的）。
    $GLOBALS['fake_stock']->arId['GLD'] = '2001';
    $GLOBALS['fake_stock']->arId['SPY'] = '2002';
    Harness\UpdateStockOptionHoldings('9003', '2026-10-22', '"GLD":100,"SPY":50');
    $out = array(
        'symbols'     => $GLOBALS['fake_stock']->arId,
        'insert_calls'=> $GLOBALS['fake_stock']->arInsert,
        'holdings'    => $GLOBALS['fake_holdings']->ar,
        'holdingsdate'=> FakeHoldingsDateSql::$arDate,
        'positions'   => $GLOBALS['fake_position']->arPos,
    );
    echo json_encode($out), "\n";
}
elseif ($caseName === 'admin_decode')
{
    // 只测解码：各种边界（0、0.0、未知符号、空串）
    // GLD/SPY 预置、NOSUCH 不预置 —— 'unknown' 用例专门抓 PHP 对未解析符号
    // 的行为：$arRatio[false] 的键是 0，且 50 仍计入 fPos。
    // （SPY 也要预置：否则 'normal' 用例里 SPY 也会掉进键 0，分不清是
    //  "未知符号"还是"没预置"。）
    $GLOBALS['fake_stock']->arId['GLD'] = '2001';
    $GLOBALS['fake_stock']->arId['SPY'] = '2002';
    $arInputs = array(
        'normal'   => '"GLD":100,"SPY":50',
        'zeros'    => '"GLD":100,"SPY":0',
        'zero_dot' => '"GLD":100,"SPY":0.0',
        'unknown'  => '"GLD":100,"NOSUCH":50',
        'bad'      => 'not-json',
        'empty'    => '',
    );
    $out = array();
    foreach ($arInputs as $strName => $strVal)
    {
        $arRatio = array();
        $fPos = Harness\StockOptionDecodeHoldings($strVal, $arRatio);
        $out[$strName] = array('pos' => $fPos, 'ratio' => $arRatio);
    }
    echo json_encode($out), "\n";
}
else
{
    fwrite(STDERR, "未知用例: $caseName\n");
    exit(2);
}
