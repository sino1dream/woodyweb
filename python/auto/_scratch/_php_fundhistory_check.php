<?php
// ---------------------------------------------------------------------------
// 对着**真实 PHP** 跑 `_echoHistoryTableData()` —— 基金溢价记录那一段的行
//
//   D:\xampp\php\php.exe -n -d display_errors=stderr _php_fundhistory_check.php
//
// ## 这一段在页面上是哪一张表
//
// `_mystock.php:135 EchoFundHistoryParagraph($fund_pair_ref)`。它和独立页
// `fundhistorycn.php` **共用** `_echoHistoryTableData()`
// （`fundhistoryparagraph.php:31`），本版也照这个复用来接详情页
// （`detail_sections.build_fund_history`），所以对照一次就够两边用。
//
// ## 三个值得单独对照的点
//
// 1. **净值日期要不要往前挪一天** —— `$bSameDay = UseSameDayNetValue($ref)`
//    （`php/stock.php:381`）：QDII 用**前一交易日**的净值（当天净值要等当晚
//    美股收盘），非 QDII 用同一天。挪错一天会整张表一行不剩，而不是算出
//    一个错的值 —— 这种错在页面上表现为"这段不见了"，很难自查。
// 2. **行过滤**：`if ($strNetValue = $net_sql->GetClose($strStockId, $strDate))`
//    —— 净值取不到就整行不出。所以"有没有行"取决于净值与行情的日期交集。
// 3. **净值列的精度** —— `GetNetValueDisplay()`（`mysqlref.php:75`）在
//    `iNetValueCount > 0` 时取 `NETVALUE_PRECISION`（**4 位**），否则退回
//    `GetPrecision()`。行能出来就意味着命中了净值表，所以正常恒 4 位。
//    **替身的 `$ref->iNetValueCount` 曾经恒为 0**，于是 PHP 侧也退到 3 位、
//    与 Python 的 3 位"一致地错"（生产端 sz161125cn.php 是 3.1548 四位）。
//    现在按例装配（默认 1），case.8 专门钉四位净值。
//    末列（est 标的净值）同一套两档：est 有净值行 4 位，否则 est 符号自身
//    精度（^GSPC 这类指数 2 位，见 case.9/case.10）。
//
// ## 手法
//
// 函数体从 PHP 源码现场切出（不是手抄），放进 `namespace Harness` 后 eval；
// 未限定的函数名先查当前命名空间，于是 `mysqli_fetch_assoc` 之类可以被替身
// 顶掉而函数体一个字不改。`GetFontElement()` 替身成 "颜色:文本"，
// 与 Python 侧的 `(文本, 颜色)` 直接可比。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

function ExtractFunctionBody($text, $name)
{
    $pattern = '/(?:^|\n)[ \t]*(?:public |protected |private |static )*function[ \t]+'
             . preg_quote($name, '/') . '[ \t]*\(/';
    if (preg_match($pattern, $text, $m, PREG_OFFSET_CAPTURE) !== 1) { return false; }
    $start = $m[0][1];
    $len = strlen($text);
    $depth = 0;
    $in = false;
    for ($i = $start; $i < $len; $i++) {
        $ch = $text[$i];
        if ($ch === '{') { $depth++; $in = true; }
        else if ($ch === '}') {
            $depth--;
            if ($in && $depth === 0) { return substr($text, $start, $i - $start + 1); }
        }
    }
    return false;
}

function SlurpFunctions($path, $names)
{
    $text = file_get_contents($path);
    if ($text === false) { fwrite(STDERR, "读不到 $path\n"); exit(2); }
    $out = '';
    foreach ($names as $name) {
        $body = ExtractFunctionBody($text, $name);
        if ($body === false) { fwrite(STDERR, "找不到函数 $name\n"); exit(2); }
        $out .= $body . "\n";
    }
    return $out;
}

$stockCode = SlurpFunctions($rootDir . '/php/stock.php', array(
    'StockGetPriceDisplay', 'GetNumberDisplay', 'StockGetPercentage', 'UseSameDayNetValue'));
$refCode   = SlurpFunctions($rootDir . '/php/stock/stockref.php', array(
    'GetPercentage', 'GetPercentageString', 'GetPercentageText', 'GetPercentageDisplay',
    'GetPriceDisplay'));
$netCode   = SlurpFunctions($rootDir . '/php/stock/mysqlref.php', array('GetNetValueDisplay'));
$hmCode    = SlurpFunctions($rootDir . '/php/debug.php', array('GetHM'));
$paraCode  = SlurpFunctions($rootDir . '/php/ui/fundhistoryparagraph.php', array(
    '_echoFundHistoryTableItem', '_echoHistoryTableData'));

$stubs = <<<'STUBS'
define('MIN_FLOAT_VAL', 0.000001);
define('NETVALUE_PRECISION', 4);

function GetFontElement($str, $strColor) { return $strColor . ':' . $str; }
function EchoTableColumn($ar, $strColor = false, $strFirstHint = false)
{
    $GLOBALS['outRows'][] = $ar;
}
function GetNetValueHistorySql() { return $GLOBALS['NetSql']; }

// UseSameDayNetValue() 用的三个符号集合：替身按 $GLOBALS['Qdii'] 回答，
// 于是"QDII 用前一交易日净值"这条分支由数据驱动，而不是写死。
function in_arrayQdii($strSymbol) { return in_array($strSymbol, $GLOBALS['Qdii'], true); }
function in_arrayQdiiMix($strSymbol) { return false; }
function in_arrayHkMix($strSymbol) { return false; }

class ResultStub
{
    private $rows;
    private $i = 0;
    public function __construct($rows) { $this->rows = $rows; }
    public function Next() { return $this->i < count($this->rows) ? $this->rows[$this->i++] : null; }
    public function Free() { $this->rows = array(); }
}
function mysqli_fetch_assoc($result) { return $result === false ? null : $result->Next(); }
function mysqli_free_result($result) { if ($result) { $result->Free(); } }

// stockhistory：GetAll 按日期倒序 + limit，GetDatePrev 取前一个有记录的日期
class HisStub
{
    public $rows = array();     // 已按日期倒序
    public function GetAll($strStockId, $iStart = 0, $iNum = 0)
    {
        $rows = isset($this->rows[$strStockId]) ? $this->rows[$strStockId] : array();
        if ($iNum == 0) { $slice = $rows; }
        else { $slice = array_slice($rows, $iStart, $iNum); }
        return new ResultStub($slice);
    }
    public function GetDatePrev($strStockId, $strDate)
    {
        if (!isset($this->rows[$strStockId])) { return false; }
        foreach ($this->rows[$strStockId] as $record) {
            if ($record['date'] < $strDate) { return $record['date']; }
        }
        return false;
    }
    public function Count($strStockId)
    {
        return isset($this->rows[$strStockId]) ? count($this->rows[$strStockId]) : 0;
    }
}

class NetStub
{
    public $rows = array();     // stock_id => array(date => close)
    public function GetClose($strStockId, $strDate)
    {
        if (!isset($this->rows[$strStockId])) { return false; }
        if (!isset($this->rows[$strStockId][$strDate])) { return false; }
        return $this->rows[$strStockId][$strDate];
    }
}

// fundest：GetRecord 返回 array('id','date','close','time')
class EstStub
{
    public $rows = array();
    public $count = 0;
    public function Count($strStockId) { return $this->count; }
    public function GetRecord($strStockId, $strDate)
    {
        if (!isset($this->rows[$strStockId])) { return false; }
        if (!isset($this->rows[$strStockId][$strDate])) { return false; }
        return $this->rows[$strStockId][$strDate];
    }
}

class RefStub
{
    public $strPrice = '0';
    public $strPrevPrice = '0';
    public $iPrecision = 3;
    public $iNetValueCount = 0;
    public $strSymbol;
    public $netValues = array();        // date => close，给"有净值记录"那支用
    public $historyCloses = array();    // date => close，GetVal() 的回落（行情史）

    public function GetSymbol() { return $this->strSymbol; }
    public function GetPrecision() { return $this->iPrecision; }
    public function IsYahooNetValue() { return false; }
    public function IsSinaFutureExceptGoldCN() { return false; }
    // mysqlref::GetNetValue($strDate)（mysqlref.php:60）的**三支**语义：
    //   1. 新浪期货（黄金除外）-> 复权价（本对照用不到）；
    //   2. 有净值记录 -> 该日期的净值，缺日期返回 false；
    //   3. 没有净值记录 -> GetVal() -> GetClose() 取行情收盘价。
    // 只被 est_ref 走到（基金自己的净值走 $net_sql->GetClose），但三支
    // 必须齐：第 3 支决定"est 无净值行时末列显示行情价还是空格"。
    public function GetNetValue($strDate)
    {
        if ($this->iNetValueCount > 0)
        {
            return isset($this->netValues[$strDate]) ? floatval($this->netValues[$strDate]) : false;
        }
        return isset($this->historyCloses[$strDate]) ? floatval($this->historyCloses[$strDate]) : false;
    }
STUBS;

//: 拼接顺序有意义：`$refCode` / `$netCode` 抽出来的是**方法**体，必须落在
//: RefStub 类里（它们在 mysqlref.php / stockref.php 里本来就是方法）；
//: 其余是普通函数，放命名空间层。
//: 两个 `}`：先闭合 RefStub 类，再闭合 namespace Harness。
$code = "namespace Harness {\n" . $stockCode . "\n" . $hmCode . "\n" . $paraCode . "\n"
      . $stubs . "\n" . $refCode . "\n" . $netCode . "\n}\n}\n";
eval($code);

//: 用例。history 已按日期倒序（PHP 的 ORDER BY date DESC），与 SQL 行为一致。
$cases = array(
    array(
        'name' => 'QDII：净值取前一交易日',
        'qdii' => array('SZ161130'),
        'symbol' => 'SZ161130',
        'stock_id' => '1',
        'history' => array('1' => array(
            array('date' => '2026-10-02', 'close' => '2.041'),
            array('date' => '2026-10-01', 'close' => '2.033'),
            array('date' => '2026-09-30', 'close' => '2.020'),
        )),
        'netvalue' => array('1' => array(
            '2026-09-30' => '2.037898', '2026-09-29' => '2.030', '2026-09-28' => '2.010',
        )),
        'est_count' => 0,
        'est' => array(),
        'est_ref' => false,
    ),
    array(
        'name' => '非 QDII：净值取同一天',
        'qdii' => array(),
        'symbol' => 'QQQ',
        'stock_id' => '4',
        'history' => array('4' => array(
            array('date' => '2026-10-01', 'close' => '742.03'),
            array('date' => '2026-09-30', 'close' => '738.5'),
        )),
        'netvalue' => array('4' => array('2026-10-01' => '740.0', '2026-09-30' => '736.0')),
        'est_count' => 0,
        'est' => array(),
        'est_ref' => false,
    ),
    array(
        'name' => '有官方 EST 与标的：每行多三格 + 末列标的净值',
        'qdii' => array('SZ161130'),
        'symbol' => 'SZ161130',
        'stock_id' => '1',
        'history' => array('1' => array(
            array('date' => '2026-10-02', 'close' => '2.041'),
            array('date' => '2026-10-01', 'close' => '2.033'),
        )),
        'netvalue' => array('1' => array(
            '2026-10-01' => '2.037898', '2026-09-30' => '2.030',
        )),
        'est_count' => 2,
        'est' => array('1' => array(
            '2026-10-01' => array('id' => '11', 'date' => '2026-10-01',
                                  'close' => '2.040', 'time' => '00:40:06'),
        )),
        'est_ref' => array('symbol' => 'QQQ', 'net' => array('2026-10-01' => '740.0'),
                           'history' => array(), 'nav_count' => 1, 'precision' => 2),
    ),
    array(
        'name' => '有 EST 但没有标的引用：末列不出现',
        'qdii' => array('SZ161130'),
        'symbol' => 'SZ161130',
        'stock_id' => '1',
        'history' => array('1' => array(
            array('date' => '2026-10-02', 'close' => '2.041'),
            array('date' => '2026-10-01', 'close' => '2.033'),
        )),
        'netvalue' => array('1' => array('2026-10-01' => '2.037898')),
        'est_count' => 1,
        'est' => array('1' => array(
            '2026-10-01' => array('id' => '11', 'date' => '2026-10-01',
                                  'close' => '2.040', 'time' => '23:28:31'),
        )),
        'est_ref' => false,
    ),
    array(
        'name' => 'EST 记录的 close 是 0 -> PHP 不加那三格',
        'qdii' => array('SZ161130'),
        'symbol' => 'SZ161130',
        'stock_id' => '1',
        'history' => array('1' => array(
            array('date' => '2026-10-02', 'close' => '2.041'),
            array('date' => '2026-10-01', 'close' => '2.033'),
        )),
        'netvalue' => array('1' => array('2026-10-01' => '2.037898')),
        'est_count' => 1,
        'est' => array('1' => array(
            '2026-10-01' => array('id' => '11', 'date' => '2026-10-01',
                                  'close' => '0', 'time' => '00:40:06'),
        )),
        'est_ref' => false,
    ),
    array(
        'name' => '净值缺失的行情日整行不出现',
        'qdii' => array(),
        'symbol' => 'QQQ',
        'stock_id' => '4',
        'history' => array('4' => array(
            array('date' => '2026-10-01', 'close' => '742.03'),
            array('date' => '2026-09-30', 'close' => '738.5'),
        )),
        'netvalue' => array('4' => array('2026-10-01' => '740.0')),
        'est_count' => 0,
        'est' => array(),
        'est_ref' => false,
    ),
    array(
        'name' => '前一个交易日取不到 -> GetDatePrev 为 false -> 整行不出现',
        'qdii' => array('SZ161130'),
        'symbol' => 'SZ161130',
        'stock_id' => '1',
        'history' => array('1' => array(
            array('date' => '2026-10-02', 'close' => '2.041'),
        )),
        'netvalue' => array('1' => array('2026-10-01' => '2.037898')),
        'est_count' => 0,
        'est' => array(),
        'est_ref' => false,
    ),
    array(
        'name' => '溢价方向：价格低于净值 -> 溢价为负',
        'qdii' => array(),
        'symbol' => 'QQQ',
        'stock_id' => '4',
        'history' => array('4' => array(
            array('date' => '2026-10-01', 'close' => '735.0'),
        )),
        'netvalue' => array('4' => array('2026-10-01' => '740.0')),
        'est_count' => 0,
        'est' => array(),
        'est_ref' => false,
    ),
    // -- 净值列精度（2026-10-19 用户对照生产端 sz161125cn.php 发现少一位） --
    array(
        'name' => '净值四位：3.1548 不四舍五入成 3.155',
        'qdii' => array('SZ161125'),
        'symbol' => 'SZ161125',
        'stock_id' => '2',
        'history' => array('2' => array(
            array('date' => '2026-10-16', 'close' => '3.299'),
            array('date' => '2026-10-15', 'close' => '3.266'),
        )),
        'netvalue' => array('2' => array('2026-10-15' => '3.1548')),
        'est_count' => 0,
        'est' => array(),
        'est_ref' => false,
    ),
    array(
        'name' => 'est 无净值行：末列取行情价、精度用 est 符号的（指数 2 位）',
        'qdii' => array('SZ161125'),
        'symbol' => 'SZ161125',
        'stock_id' => '2',
        'history' => array('2' => array(
            array('date' => '2026-10-16', 'close' => '3.299'),
            array('date' => '2026-10-15', 'close' => '3.266'),
        )),
        'netvalue' => array('2' => array('2026-10-15' => '3.1548')),
        'est_count' => 1,
        'est' => array('2' => array(
            '2026-10-15' => array('id' => '64', 'date' => '2026-10-15',
                                  'close' => '3.15348', 'time' => '11:37:50'),
        )),
        //: ^GSPC 没有净值记录 -> GetNetValue() 第三支 GetVal() 取行情价；
        //: 7744.0625 给四位，2 位精度应显示成 7744.06
        'est_ref' => array('symbol' => '^GSPC', 'net' => array(),
                           'history' => array('2026-10-15' => '7744.0625'),
                           'nav_count' => 0, 'precision' => 2),
    ),
    array(
        'name' => 'est 有净值行：末列四位',
        'qdii' => array('SZ161130'),
        'symbol' => 'SZ161130',
        'stock_id' => '1',
        'history' => array('1' => array(
            array('date' => '2026-10-02', 'close' => '2.041'),
            array('date' => '2026-10-01', 'close' => '2.033'),
        )),
        'netvalue' => array('1' => array('2026-10-01' => '2.037898')),
        'est_count' => 1,
        'est' => array('1' => array(
            '2026-10-01' => array('id' => '11', 'date' => '2026-10-01',
                                  'close' => '2.040', 'time' => '00:40:06'),
        )),
        //: 740.1234 四位全显示；3 位精度会成 740.123
        'est_ref' => array('symbol' => 'QQQ', 'net' => array('2026-10-01' => '740.1234'),
                           'history' => array(), 'nav_count' => 1, 'precision' => 2),
    ),
    array(
        'name' => 'est 有净值行但缺 EST 日期：末列空（不回落行情价）',
        'qdii' => array('SZ161130'),
        'symbol' => 'SZ161130',
        'stock_id' => '1',
        'history' => array('1' => array(
            array('date' => '2026-10-02', 'close' => '2.041'),
            array('date' => '2026-10-01', 'close' => '2.033'),
        )),
        'netvalue' => array('1' => array('2026-10-01' => '2.037898')),
        'est_count' => 1,
        'est' => array('1' => array(
            '2026-10-01' => array('id' => '11', 'date' => '2026-10-01',
                                  'close' => '2.040', 'time' => '00:40:06'),
        )),
        //: 净值只有 09-30 的，EST 记录在 10-01 -> GetNetValue 返回 false
        //: -> GetNetValueDisplay(false) = ''。**即使有行情价也不该出现**：
        //: "有净值记录"分支里缺日期就是空格，只有"没有净值记录"才回落行情价。
        'est_ref' => array('symbol' => 'QQQ', 'net' => array('2026-09-30' => '740.0'),
                           'history' => array('2026-10-01' => '740.5'),
                           'nav_count' => 1, 'precision' => 2),
    ),
);

$out = array('cases' => array(), 'traps' => array());

foreach ($cases as $index => $case) {
    $GLOBALS['Qdii'] = $case['qdii'];

    $hisSql = new Harness\HisStub();
    $hisSql->rows = $case['history'];
    $netSql = new Harness\NetStub();
    $netSql->rows = $case['netvalue'];
    $estSql = new Harness\EstStub();
    $estSql->rows = $case['est'];
    $estSql->count = $case['est_count'];
    // PHP 里 `_echoFundHistoryParagraph` 在 Count()==0 时把 $fund_est_sql 置 false
    if ($case['est_count'] == 0) { $estSql = false; }
    $GLOBALS['NetSql'] = $netSql;

    $ref = new Harness\RefStub();
    $ref->strSymbol = $case['symbol'];
    $ref->strPrice = isset($case['history'][$case['stock_id']][0]['close'])
        ? $case['history'][$case['stock_id']][0]['close'] : '0';
    $ref->strPrevPrice = $ref->strPrice;
    //: 行能出来就说明 netvaluehistory 命中了 —— 即 PHP 的 iNetValueCount > 0
    //: 必然成立，所以基金 ref 的净值计数按"有"装配（默认 1）。
    //: **这个字段曾经恒为 0**：GetNetValueDisplay() 于是退到 GetPrecision()(3)，
    //: Python 侧同样 3 位，逐例对照"一致"地错 —— 生产端是 4 位。
    //: 净值列精度的对照全靠它，见文件头「三个值得单独对照的点」。
    $ref->iNetValueCount = isset($case['fund_nav_count']) ? $case['fund_nav_count'] : 1;

    $estRef = false;
    if ($case['est_ref'] !== false) {
        $estRef = new Harness\RefStub();
        $estRef->strSymbol = $case['est_ref']['symbol'];
        $estRef->netValues = $case['est_ref']['net'];
        $estRef->historyCloses = $case['est_ref']['history'];
        $estRef->iNetValueCount = $case['est_ref']['nav_count'];
        //: est 符号自身精度：没有净值记录时 GetNetValueDisplay() 用它
        //: （指数/美股 2 位；有净值记录时走 NETVALUE_PRECISION 4 位）。
        $estRef->iPrecision = $case['est_ref']['precision'];
    }

    $GLOBALS['outRows'] = array();
    try {
        Harness\_echoHistoryTableData($hisSql, $estSql, false, $ref, $case['stock_id'],
                                      $estRef, 0, 0, false);
    } catch (Throwable $e) {
        $GLOBALS['outRows'][] = array('ERROR:' . $e->getMessage());
    }

    $rows = array();
    foreach ($GLOBALS['outRows'] as $row) {
        $cells = array();
        foreach ($row as $cell) { $cells[] = ($cell === false) ? '' : $cell; }
        $rows[] = $cells;
    }
    $out['cases']['case.' . $index] = array(
        'name' => $case['name'],
        'rows' => $rows,
    );
}

$out['traps']['net_value_precision'] = NETVALUE_PRECISION;

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), "\n";
