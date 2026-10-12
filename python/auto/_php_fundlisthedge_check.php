<?php
/**
 * 对着**真实 PHP** 跑 `_callbackFundListHedge()` / `GetStockHedge()`。
 *
 * 用法（在 python/auto 下）：
 *
 *     php _php_fundlisthedge_check.php
 *
 * ## 为什么要把函数体抽出来而不是手抄一份
 *
 * 手抄的对照脚本只能证明"我抄的那份和我写的一样"。这里用
 * `ExtractFunctionBody()` 按函数名从 `php/stock.php` 与
 * `woody/res/php/_qdiigroup.php` 里**现场切出**函数体再 eval，
 * 所以对照的是 PHP 自己的实现。改了 PHP，这边立刻跟着变。
 *
 * ## 一个必须用的手法：在命名空间里重定义 mysqli_* 函数
 *
 * `GetStockHedge()` 内部是 `mysqli_fetch_assoc($result)` —— 参数必须是真的
 * `mysqli_result`，替身对象传进去 PHP 8 直接 TypeError。
 *
 * 但 PHP 解析**未限定**的函数名时是「先看当前命名空间，再看全局」。于是把
 * 抽出来的函数体放进 `namespace Harness`，再在 `Harness` 里定义
 * `mysqli_fetch_assoc()` / `mysqli_free_result()` —— 函数体一个字不改，
 * 却会调到我们的替身。
 *
 * `GetAll()` 在 PHP 那边是 `ORDER BY \`date\` DESC`
 * （`DailyCloseSql::BuildOrderBy()` → `_SqlOrderByDate()`），
 * 所以 fixture 里的校准行**按日期倒序**给，与 SQL 的行为一致。
 */

$rootDir = realpath(__DIR__ . '/../..');
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

$hedgeCode = SlurpFunctions($rootDir . '/php/stock.php', array(
    'StockCalcHedge', 'GetLeverageHedgeSymbol', 'GetStockHedge'));
$callbackCode = SlurpFunctions($rootDir . '/woody/res/php/_qdiigroup.php', array(
    '_callbackFundListHedge'));

$stubs = <<<'STUBS'
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

class PosStub
{
    public $pos = array();
    // PHP 的 ReadPos() 没有记录时返回 false
    public function ReadPos($strStockId)
    {
        return array_key_exists($strStockId, $this->pos) ? $this->pos[$strStockId] : false;
    }
}

class CalStub
{
    public $rows = array();     // stock_id => array(array('date' => .., 'close' => ..), ..) 已按日期倒序
    public function GetAll($strStockId)
    {
        return new ResultStub(isset($this->rows[$strStockId]) ? $this->rows[$strStockId] : array());
    }
    public function GetClose($strStockId, $strDate)
    {
        if (!isset($this->rows[$strStockId])) { return false; }
        foreach ($this->rows[$strStockId] as $record) {
            if ($record['date'] === $strDate) { return $record['close']; }
        }
        return false;
    }
}

function GetPositionSql() { return $GLOBALS['PosSql']; }
function GetCalibrationSql() { return $GLOBALS['CalSql']; }
function SqlGetStockId($strSymbol)
{
    return isset($GLOBALS['Ids'][$strSymbol]) ? $GLOBALS['Ids'][$strSymbol] : false;
}

class RefStub
{
    private $symbol;
    private $stockId;
    public function __construct($strSymbol, $strStockId) { $this->symbol = $strSymbol; $this->stockId = $strStockId; }
    public function GetSymbol() { return $this->symbol; }
    public function GetStockId() { return $this->stockId; }
}

class AcctStub
{
    public $ref;
    public function GetRef() { return $this->ref; }
}
STUBS;

$code = "namespace Harness {\n" . $hedgeCode . "\n" . $callbackCode . "\n" . $stubs . "\n}\n";
eval($code);

$GLOBALS['PosSql'] = new Harness\PosStub();
$GLOBALS['CalSql'] = new Harness\CalStub();

//: 用例。my 是被看的基金（$acct->GetRef()），row 是行里的杠杆标的。
//: 数值量级照演示库与生产端：基金校准值上万、ETF 校准值 10 上下。
$cases = array(
    array(
        'name' => '两边最新日期都对得上',
        'ids' => array('SZ161125' => '2', 'SPY' => '5'),
        'positions' => array('2' => 0.95, '5' => 1.0),
        'calibration' => array(
            '2' => array(
                array('date' => '2026-10-09', 'close' => '15810.585263'),
                array('date' => '2026-10-01', 'close' => '22.110973'),
            ),
            '5' => array(array('date' => '2026-10-09', 'close' => '10.035')),
        ),
        'my' => array('SZ161125', '2'),
        'row' => 'SPY',
    ),
    array(
        'name' => '只有旧日期对得上：扫描要往后走',
        'ids' => array('SZ161125' => '2', 'SPY' => '5'),
        'positions' => array('2' => 0.95, '5' => 1.0),
        'calibration' => array(
            '2' => array(
                array('date' => '2026-10-09', 'close' => '15810.585263'),
                array('date' => '2026-10-01', 'close' => '22.110973'),
            ),
            '5' => array(array('date' => '2026-10-01', 'close' => '10.035')),
        ),
        'my' => array('SZ161125', '2'),
        'row' => 'SPY',
    ),
    array(
        'name' => '杠杆标的没有校准值 -> 1.0',
        'ids' => array('SZ161125' => '2', 'SPY' => '5'),
        'positions' => array('2' => 0.95, '5' => 1.0),
        'calibration' => array(
            '2' => array(array('date' => '2026-10-09', 'close' => '15810.585263')),
            '5' => array(),
        ),
        'my' => array('SZ161125', '2'),
        'row' => 'SPY',
    ),
    array(
        'name' => '本基金没有校准值 -> 1.0',
        'ids' => array('SZ161125' => '2', 'SPY' => '5'),
        'positions' => array('2' => 0.95, '5' => 1.0),
        'calibration' => array(
            '2' => array(),
            '5' => array(array('date' => '2026-10-09', 'close' => '10.035')),
        ),
        'my' => array('SZ161125', '2'),
        'row' => 'SPY',
    ),
    array(
        'name' => '做空标的：仓位为负，对冲值跟着变负号',
        'ids' => array('SZ161125' => '2', 'SH' => '8'),
        'positions' => array('2' => 0.95, '8' => -1.0),
        'calibration' => array(
            '2' => array(array('date' => '2026-10-09', 'close' => '15810.585263')),
            '8' => array(array('date' => '2026-10-09', 'close' => '245.591')),
        ),
        'my' => array('SZ161125', '2'),
        'row' => 'SH',
    ),
    array(
        // '0.0' 在 PHP 里是**真值**（只有恰好 '0' 才是假值），所以 PHP 8 会真去除
        // → DivisionByZeroError，整页 500。Python 那边由 _hedge_of 兜成 1.0，
        // 这是刻意不复制的崩溃，见 README_qdii_fund_list.md。
        'name' => '陷阱：杠杆标的的校准值是 0.0 —— PHP 8 直接抛错',
        'ids' => array('SZ161125' => '2', 'SPY' => '5'),
        'positions' => array('2' => 0.95, '5' => 1.0),
        'calibration' => array(
            '2' => array(
                array('date' => '2026-10-09', 'close' => '15810.585263'),
                array('date' => '2026-10-01', 'close' => '22.110973'),
            ),
            '5' => array(
                array('date' => '2026-10-09', 'close' => '0.0'),
                array('date' => '2026-10-01', 'close' => '10.035'),
            ),
        ),
        'my' => array('SZ161125', '2'),
        'row' => 'SPY',
    ),
    array(
        // 恰好是 '0'：PHP 判假 → 跳过这一天继续往下扫。
        // Python 的 get_stock_hedge 只看 `is not None`，0.0 会被当成有效值
        // → 除零 → 被 _hedge_of 兜成 1.0。同样是不复制的分支，见 README。
        'name' => '陷阱：杠杆标的的校准值是字符串 0 —— PHP 判假跳过',
        'ids' => array('SZ161125' => '2', 'SPY' => '5'),
        'positions' => array('2' => 0.95, '5' => 1.0),
        'calibration' => array(
            '2' => array(
                array('date' => '2026-10-09', 'close' => '15810.585263'),
                array('date' => '2026-10-01', 'close' => '22.110973'),
            ),
            '5' => array(
                array('date' => '2026-10-09', 'close' => '0'),
                array('date' => '2026-10-01', 'close' => '10.035'),
            ),
        ),
        'my' => array('SZ161125', '2'),
        'row' => 'SPY',
    ),
);

$out = array('cases' => array(), 'traps' => array());

foreach ($cases as $index => $case) {
    $GLOBALS['PosSql']->pos = $case['positions'];
    $GLOBALS['CalSql']->rows = $case['calibration'];
    $GLOBALS['Ids'] = $case['ids'];

    $acct = new Harness\AcctStub();
    $acct->ref = new Harness\RefStub($case['my'][0], $case['my'][1]);
    $GLOBALS['acct'] = $acct;

    try {
        $value = Harness\_callbackFundListHedge($case['row']);
    } catch (Throwable $e) {
        $value = 'ERROR:' . get_class($e);
    }
    $out['cases']['case.' . $index] = array(
        'name' => $case['name'],
        'hedge' => $value,
        // 格式化后的样子：与生产端页面上的「对冲值」列对照（精度 0）
        'display' => is_float($value) || is_numeric($value)
            ? rtrim(rtrim(number_format(floatval($value), 0, '.', ''), '0'), '.')
            : $value,
    );
}

//: PHP 语义陷阱，单独输出：Python 那边必须自己确认同样的行为
// '0.0' 是**真值**（只有恰好 '0' 才假）—— 这正是 case.5 会真去除的原因
$out['traps']['string_zero_dot_is_truthy'] = (bool)('0.0' ? true : false);
$out['traps']['string_zero_is_falsy'] = (bool)('0' ? true : false);
$out['traps']['empty_string_zero'] = empty('0') ? true : false;

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), "\n";
