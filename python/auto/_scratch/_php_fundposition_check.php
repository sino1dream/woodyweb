<?php
// ---------------------------------------------------------------------------
// 对着**真实 PHP** 跑仓位估算页的行选择与仓位反算（`fundpositioncn.php`）
//
//   php -n -d display_errors=stderr _php_fundposition_check.php _fundposition_input.json
//
// ## 验什么
//
// 这一页新迁的部分只有两件事：
//
// 1. **行选择** —— 哪些日期出行、每行的"上期"是谁。这是
//    ``_echoFundPositionData()`` 的活，也是本页存在的意义
//    （对照 netvaluehistory 页用的是上一交易日）。
// 2. **仓位反算** —— ``_QdiiGetStockPosition()``，即在"波段"口径下过不过闸门、
//    出什么值。
//
// 单元格的文本格式化（`GetNetValueDisplay` / `GetPercentageDisplay`）不在本轮：
// 它与净值记录页共用同一个 ``EchoNetValueItem``，那边的数字已经在 CK6 阶段
// 对着生产端逐个核过（见 README_history_migration.md）。
//
// ## 手法：函数体从 PHP 源码现场切出（不是手抄）
//
// 与 `_php_netvaluehistory_check.php` 同一套 `ExtractMethod`：按花括号配对把
// 函数体原文切下来再 eval。手抄一遍逻辑就失去了对照的意义。
//
// 两处**机械适配**（PHP 的 mysqli API 不能对数组用，注释里逐个标明）：
//
// * ``GetSwitchDates`` / ``_echoFundPositionData`` 里的
//   ``while ($record = mysqli_fetch_assoc($result))`` 换成 ``foreach``，
//   并去掉 ``mysqli_free_result``；``$this->GetAll($strKeyId)`` 换成注入的数组。
//   除此之外一个字符都不改 —— 状态机的三个分支原样保留。
// * ``GetNetValueHistorySql()`` 是真身里连库取对象的全局函数，这里给一个
//   返回替身对象的同名函数，让 ``_echoFundPositionData`` 原样跑起来。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

function ExtractMethod($text, $name)
{
    // ``\r?`` 不能省：PHP 源码是 Windows 换行（实测踩到，少了一个都切不出来）
    $pattern = '/(?:^|\n)[ \t]*(?:public |protected |private |static )*function[ \t]+'
             . preg_quote($name, '/') . '[ \t]*\([^)]*\)[ \t]*\r?\n?[ \t]*\{/';
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
    return substr($text, $start, $i - $start);
}

function AdaptLoop($body)
{
    // 唯一的改写：把取数方式换成遍历注入的数组。算法本身一行不动。
    $body = str_replace('if ($result = $this->GetAll($strKeyId))',
                        'if ($result = $arRecords)', $body);
    $body = str_replace('while ($record = mysqli_fetch_assoc($result))',
                        'foreach ($result as $record)', $body);
    $body = str_replace('mysqli_free_result($result);', '', $body);
    return $body;
}

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

// -- 切出三段真身 ----------------------------------------------------------
$sqlSrc = file_get_contents($rootDir . '/php/sql/sqldailyclose.php');
$pageSrc = file_get_contents($rootDir . '/woody/res/fundpositioncn.php');
$uiSrc = file_get_contents($rootDir . '/php/ui/netvaluehistoryparagraph.php');
if ($sqlSrc === false || $pageSrc === false || $uiSrc === false)
{ fwrite(STDERR, "读不到源文件\n"); exit(2); }

$switchBody = ExtractMethod($sqlSrc, 'GetSwitchDates');
$dataBody = ExtractMethod($pageSrc, '_echoFundPositionData');
$posBody = ExtractMethod($uiSrc, '_QdiiGetStockPosition');
foreach (array('GetSwitchDates' => $switchBody, '_echoFundPositionData' => $dataBody,
               '_QdiiGetStockPosition' => $posBody) as $name => $body)
{
    if ($body === null) { fwrite(STDERR, "切不出 $name\n"); exit(2); }
}

// GetSwitchDates 是成员方法，这里改成吃数组的自由函数（只有签名与取数变了）
$switchBody = AdaptLoop($switchBody);
$switchBody = str_replace('function GetSwitchDates($strKeyId)',
                          'function GetSwitchDatesFromRecords($arRecords)', $switchBody);
eval($switchBody);

$dataBody = AdaptLoop($dataBody);
eval($dataBody);

eval($posBody);      // _QdiiGetStockPosition：原文不改一个字

// -- 常量与自由函数（真身会连库或散在别的文件里） -------------------------
define('MIN_FLOAT_VAL', 0.0000005);      // php/debug.php:11
define('POS_NETVALUE_DIFF', 4.0);        // php/ui/stockdisp.php:32

function StockGetPercentage($fDivisor, $fDividend)   // php/stock.php:121
{
    if (abs($fDivisor) > MIN_FLOAT_VAL)   return ($fDividend / $fDivisor - 1.0) * 100.0;
    return 0.0;
}

// -- 替身 ------------------------------------------------------------------
class Case_
{
    public $nav = array();      // date => close（基金净值）
    public $cny = array();      // date => close（汇率）
    public $estNetValue = array();
    public $estClose = array();
    public $rows = array();     // date DESC 的记录数组
    public $out = array('rows' => array());

    function Nav($strDate)
    {
        return array_key_exists($strDate, $this->nav) ? $this->nav[$strDate] : false;
    }
    function Cny($strDate)
    {
        return array_key_exists($strDate, $this->cny) ? $this->cny[$strDate] : false;
    }
    // GetNetValue() 两档：有净值记录取净值，否则回落行情价（mysqlref.php:60-73）
    function Est($strDate)
    {
        if (count($this->estNetValue) > 0)
        {
            return array_key_exists($strDate, $this->estNetValue) ? $this->estNetValue[$strDate] : false;
        }
        return array_key_exists($strDate, $this->estClose) ? $this->estClose[$strDate] : false;
    }
}

class SqlStub
{
    public $owner;
    function __construct($owner) { $this->owner = $owner; }
    function GetAll($strKeyId) { return $this->owner->rows; }
    function GetSwitchDates($strKeyId) { return GetSwitchDatesFromRecords($this->owner->rows); }
}

class RefStub
{
    public $owner;
    function __construct($owner) { $this->owner = $owner; }
    function GetStockId() { return '1'; }
}

class CsvStub
{
    public $owner;
    function __construct($owner) { $this->owner = $owner; }
    function Write($strDate, $strNetValue, $strPosition = null)
    {
        $this->owner->out['csv'][] = $strDate.','.$strNetValue
                                    .($strPosition === null ? '' : ','.$strPosition);
    }
}

// 真身是连库取 DailyCloseSql 的全局函数（GetNetValueHistorySql()），
// 这里返回替身，好让 ``_echoFundPositionData`` 原样跑起来
$GLOBALS['gCase'] = null;
function GetNetValueHistorySql() { return new SqlStub($GLOBALS['gCase']); }

function EchoNetValueItem($csv, $ref, $cny_ref, $est_ref, $strDate, $strNetValue,
                          $strPrevDate, $fInput = POS_NETVALUE_DIFF, $bAdmin = false)
{
    // 表格里的显示文本不在本轮对照范围（见文件头说明），这里只做两件事：
    // 调**真身** _QdiiGetStockPosition 反算仓位，并把 (日期, 上期, 仓位) 记下来。
    $case = $GLOBALS['gCase'];
    $fPrev = floatval($case->Nav($strPrevDate));
    $fNetValue = floatval($strNetValue);
    $fCnyPrev = floatval($case->Cny($strPrevDate));
    $fCny = floatval($case->Cny($strDate));
    $fEstPrev = $case->Est($strPrevDate);
    $fEst = $case->Est($strDate);

    $strPosition = false;
    if ($fEst !== false && $fEstPrev !== false && floatval($fEstPrev) != 0.0)
    {
        $strPosition = _QdiiGetStockPosition(floatval($fEst) / floatval($fEstPrev),
                                             $fPrev, $fNetValue, $fCnyPrev, $fCny, $fInput);
    }
    $case->out['rows'][] = array(
        'date' => $strDate,
        'prev' => $strPrevDate,
        'position' => ($strPosition === false) ? null : strval($strPosition),
    );
    return $strPosition !== false;
}

// ---------------------------------------------------------------------------
// 输入与输出
// ---------------------------------------------------------------------------
$strFile = $argv[1];
$arInput = json_decode(file_get_contents($strFile), true);
if ($arInput === null) { fwrite(STDERR, "JSON 解析失败：$strFile\n"); exit(1); }

$arOut = array();
foreach ($arInput as $strSymbol => $arCase)
{
    $case = new Case_();
    $case->nav = array();
    foreach ($arCase['nav'] as $pair) { $case->nav[$pair[0]] = $pair[1]; }
    $case->cny = $arCase['cny'];
    $case->estNetValue = $arCase['est_netvalue'];
    $case->estClose = $arCase['est_close'];
    // date DESC，与 GetAll() 一致
    $case->rows = array();
    foreach ($arCase['nav'] as $pair)
    {
        $case->rows[] = array('date' => $pair[0], 'close' => $pair[1]);
    }
    $case->out['csv'] = array();
    $GLOBALS['gCase'] = $case;

    $case->out['switch_dates'] = GetSwitchDatesFromRecords($case->rows);

    _echoFundPositionData(new CsvStub($case), new RefStub($case),
                          true, true, floatval(POS_NETVALUE_DIFF), $arCase['num'], false);

    $arOut[$strSymbol] = $case->out;
}

echo json_encode($arOut);
