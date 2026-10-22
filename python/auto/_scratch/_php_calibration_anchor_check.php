<?php
// ---------------------------------------------------------------------------
// 用**真实 PHP** 跑 ``_QdiiReference::AdjustFactor()``，验「锚点 = 基金净值日」
//
//   php -d display_errors=stderr _php_calibration_anchor_check.php \
//        _calibration_anchor_input.json
//
// ## 为什么要这个探针
//
// ``calibrate_fund`` 的锚点口径从"est 标的最新行情日"改成了 PHP 的
// "**基金净值日**"。这是照着 PHP 源码改的，光看源码不够 —— 要让它自己和
// Python 跑同一个库、比同一组数，才算数。
//
// ## 手法：真身现场切出（不是手抄）
//
// 与 ``_php_fundposition_check.php`` 同一套 ``ExtractMethod``：按花括号配对把
// 函数体**原文**切下来再 eval。三段真身：
//
// * ``QdiiGetCalibration()`` —— 算式本体，一个字符不改；
// * ``AdjustFactor()``       —— 控制流本体（净值 → 汇率 → est 两级），
//                               只有"取数/写库"那几个方法是替身；
// * ``_getEstNetValue()``    —— est 的第一级取数（``SqlGetNetValueByDate``）。
//
// ## 替身（逐条标明为什么）
//
// ============================  ==========================================
// ``UpdateOfficialNetValue()``  真身是 ``StockCompareEstResult()``，会**写**
//                               fundest 记录。探针只要它的返回值与副作用
//                               （``strDate`` / ``strPrice``），所以替身只查
//                               netvaluehistory 最新一行。这正是被验的那一步：
//                               **锚点日期从这里来**。
// ``GetCnyRef()``/``GetEstRef()``  替身只是"按表查某天收盘价"。
//                               cny 走 **netvaluehistory**（演示库 USCNY 只有
//                               这张表有行），est 走 **stockhistory**。
// ``InsertFundCalibration()``   真身写 calibrationhistory；探针只读数，
//                               替身把值记在对象上。
// ============================  ==========================================
//
// ## 注意
//
// * 本文件**不能出现 PHP 结束标签**（注释里也不行）—— 解析器会在那里结束
//   脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// * 这里**不能用** ``php -n``：``mysqli`` 是 php.ini 里加载的扩展，``-n``
//   会把它一起关掉。改用 ``-d display_errors=stderr`` 把 openssl 那条
//   无关警告赶到 stderr，stdout 仍然是干净的 JSON。
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

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

// -- 切出三段真身 ----------------------------------------------------------
$srcFile = $rootDir . '/php/stock/qdiiref.php';
$src = file_get_contents($srcFile);
if ($src === false) { fwrite(STDERR, "读不到 $srcFile\n"); exit(2); }

$calBody = ExtractMethod($src, 'QdiiGetCalibration');
$adjBody = ExtractMethod($src, 'AdjustFactor');
$estBody = ExtractMethod($src, '_getEstNetValue');
foreach (array('QdiiGetCalibration' => $calBody, 'AdjustFactor' => $adjBody,
               '_getEstNetValue' => $estBody) as $name => $body)
{
    if ($body === null) { fwrite(STDERR, "切不出 $name\n"); exit(2); }
}
eval($calBody);      // 自由函数：算式本体，原文不改一个字

// -- 连库（凭据从 python/auto/.env 读，不硬编码） --------------------------
$GLOBALS['gDb'] = null;
$envFile = $rootDir . '/python/auto/.env';
$env = array();
foreach (file($envFile, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line)
{
    if (strpos(ltrim($line), '#') === 0)            continue;
    $pair = explode('=', $line, 2);
    if (count($pair) === 2)     $env[trim($pair[0])] = trim($pair[1]);
}
$db = new mysqli($env['PALMMICRO_DB_HOST'], $env['PALMMICRO_DB_USER'],
                 $env['PALMMICRO_DB_PASSWORD'], $env['PALMMICRO_DB_DATABASE']);
if ($db->connect_errno) { fwrite(STDERR, "连不上库：" . $db->connect_error . "\n"); exit(2); }
$GLOBALS['gDb'] = $db;

function DbCloseOf($table, $strStockId, $strDate)
{
    // 某表某天的 close；没有就 false（PHP 的 GetClose 取不到也是 false）
    $db = $GLOBALS['gDb'];
    $sql = "SELECT close FROM " . $table . " WHERE stock_id = " . intval($strStockId)
         . " AND date = '" . $db->real_escape_string($strDate) . "' LIMIT 1";
    $result = $db->query($sql);
    if ($result === false)                          return false;
    $row = $result->fetch_assoc();
    $result->free();
    return ($row === null) ? false : $row['close'];
}

function DbLatestNetValue($strStockId)
{
    // UpdateOfficialNetValue() 的替身只用到这一步：最新净值行（date DESC）
    $db = $GLOBALS['gDb'];
    $sql = "SELECT date, close FROM netvaluehistory WHERE stock_id = "
         . intval($strStockId) . " ORDER BY date DESC LIMIT 1";
    $result = $db->query($sql);
    if ($result === false)                          return false;
    $row = $result->fetch_assoc();
    $result->free();
    return ($row === null) ? false : $row;
}

// _getEstNetValue() 真身里调的全局函数（php/stock/stockref.php 里的同名函数）
function SqlGetNetValueByDate($strStockId, $strDate)
{
    return DbCloseOf('netvaluehistory', $strStockId, $strDate);
}

// -- 替身：只替换"取数与写库"，控制流与算式全用真身 ----------------------
class CloseRef
{
    public $strTag;
    public $strTable;
    public $strStockId;
    function __construct($strTag, $strTable, $strStockId)
    {
        $this->strTag = $strTag;
        $this->strTable = $strTable;
        $this->strStockId = $strStockId;
    }
    function GetStockId() { return $this->strStockId; }
    function GetClose($strDate)
    {
        // 每问一次都记下来：输出里能看到 PHP **到底问了哪一天**，
        // 这是"锚点日"最直接的证据（比只看结果更难糊弄过去）。
        $str = DbCloseOf($this->strTable, $this->strStockId, $strDate);
        $GLOBALS['gTrace'][] = array('who' => $this->strTag, 'date' => strval($strDate),
                                     'value' => ($str === false) ? null : strval($str));
        return $str;
    }
}

$classSrc = 'class QdiiRefStub'
    . '{'
    . '  public $fFactor = false;'
    . '  public $strDate = false;'
    . '  public $strPrice = false;'
    . '  public $arIds;'
    . '  function __construct($arIds) { $this->arIds = $arIds; }'
    . '  function GetStockId() { return $this->arIds["fund_id"]; }'
    . '  function GetDate() { return $this->strDate; }'
    . '  function GetPrice() { return $this->strPrice; }'
    . '  function UpdateOfficialNetValue()'
    . '  {'
    . '      $row = DbLatestNetValue($this->arIds["fund_id"]);'
    . '      if ($row === false)    return false;'
    . '      $this->strDate = $row["date"];'
    . '      $this->strPrice = $row["close"];'
    . '      return true;'
    . '  }'
    . '  function GetCnyRef()'
    . '  { return new CloseRef("cny", "netvaluehistory", $this->arIds["cny_id"]); }'
    . '  function GetEstRef()'
    . '  { return new CloseRef("est", "stockhistory", $this->arIds["est_id"]); }'
    . '  function InsertFundCalibration() { $GLOBALS["gWritten"][] = $this->fFactor; }'
    . $estBody
    . $adjBody
    . '}';
eval($classSrc);

// ---------------------------------------------------------------------------
// 输入与输出
// ---------------------------------------------------------------------------
$strFile = $argv[1];
$arInput = json_decode(file_get_contents($strFile), true);
if ($arInput === null) { fwrite(STDERR, "JSON 解析失败：$strFile\n"); exit(1); }

$arOut = array();
foreach ($arInput as $strSymbol => $arIds)
{
    $GLOBALS['gTrace'] = array();
    $GLOBALS['gWritten'] = array();

    $ref = new QdiiRefStub($arIds);
    $fFactor = $ref->AdjustFactor();

    $arRow = array(
        'est_symbol' => $arIds['est_symbol'],
        'date' => ($ref->GetDate() === false) ? null : strval($ref->GetDate()),
        'nav' => ($ref->GetPrice() === false) ? null : strval($ref->GetPrice()),
        'trace' => $GLOBALS['gTrace'],
    );
    if ($fFactor === false)
    {
        // PHP 的三种失败：净值取不到 / 当天没汇率 / est 两级都取不到
        $arRow['skipped'] = true;
        $arRow['cal'] = null;
    }
    else
    {
        // 从 trace 里挑出**锚点日那天**被问到的值（est 可能先问净值再问行情）
        $strEst = null;
        $strCny = null;
        foreach ($GLOBALS['gTrace'] as $arAsk)
        {
            if ($arAsk['date'] !== $arRow['date'] || $arAsk['value'] === null)  continue;
            if ($arAsk['who'] === 'est' && $strEst === null)    $strEst = $arAsk['value'];
            if ($arAsk['who'] === 'cny' && $strCny === null)    $strCny = $arAsk['value'];
        }
        $arRow['skipped'] = false;
        $arRow['est'] = $strEst;
        $arRow['cny'] = $strCny;
        $arRow['cal'] = floatval($fFactor);
    }
    $arOut[$strSymbol] = $arRow;
}

echo json_encode($arOut);
