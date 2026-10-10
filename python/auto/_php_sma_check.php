<?php
// ---------------------------------------------------------------------------
// stockhistory 数值核心的 PHP 参考值
//
//   php -n -d display_errors=stderr _php_sma_check.php -
//
// ## 做法：按名字抽函数体再求值
//
// 不 `require` 整个 `php/stockhis.php` —— 那会连带拖进 `class/ini_file.php`
// 与一堆渲染函数。改成用正则+括号匹配把要对照的函数体抠出来，
// 拼成临时文件 `require`。
//
// **注意**：这个文件里**绝不能出现 PHP 结束标签**（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout，
// 把 JSON 污染掉。这个坑踩过一次，见 README_refobj_php_compare.md。
//
// ## 日期函数为什么要切片
//
// `_isWeekEnd` / `_isMonthEnd` 依赖 `StringYMD` / `GetNowYMD`（`php/class/`
// `year_month_day.php`）。那个文件末尾有一行 `$g_now_ymd = new NowYMD();`
// 会执行真实时钟初始化，所以只取**类定义那一段**（到 `GetNowYMD()` 为止），
// 再手工塞一个固定的 `$g_now_ymd`，让"现在"可控、可复现。
// ---------------------------------------------------------------------------

$phpDir = realpath(__DIR__ . '/../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }

//: 要抽出来的函数（文件相对路径 => 函数名列表）
$targets = array(
    'stockhis.php' => array(
        '_estSma',
        'GetQuadraticEquationRoot',
        '_estBollingerBands',
        '_estNextBollingerBands',
        '_isWeekEnd',
        '_isMonthEnd',
        '_ignoreCurrentTradingData',
    ),
);

function extract_function($source, $name)
{
    // 匹配 `function name(` 而不是 `function _name(`：用 \b 卡边界
    if (!preg_match('/function\s+' . preg_quote($name, '/') . '\s*\(/', $source, $m, PREG_OFFSET_CAPTURE))
    {
        return null;
    }
    $start = $m[0][1];
    $cursor = strpos($source, '{', $start);
    if ($cursor === false)  return null;

    $depth = 0;
    $length = strlen($source);
    for ($i = $cursor; $i < $length; $i++)
    {
        $char = $source[$i];
        if ($char == '{')       $depth++;
        else if ($char == '}')
        {
            $depth--;
            if ($depth == 0)    return substr($source, $start, $i - $start + 1);
        }
    }
    return null;
}

$blocks = array();
foreach ($targets as $file => $names)
{
    $path = $phpDir . DIRECTORY_SEPARATOR . $file;
    if (!is_readable($path)) { fwrite(STDERR, "读不到 $file\n"); exit(2); }
    $source = file_get_contents($path);
    foreach ($names as $name)
    {
        $body = extract_function($source, $name);
        if ($body === null) { fwrite(STDERR, "$name 抽取失败（$file）\n"); exit(3); }
        $blocks[] = $body;
    }
}

// --- 日期类：只取类定义段，跳过末尾的真实时钟初始化 --------------------------
$ymdSource = file_get_contents($phpDir . DIRECTORY_SEPARATOR . 'class' . DIRECTORY_SEPARATOR . 'year_month_day.php');
$ymdStart = strpos($ymdSource, 'class YearMonthDay');
$ymdEnd = strpos($ymdSource, 'function GetNowYMD()');
if ($ymdStart === false || $ymdEnd === false) { fwrite(STDERR, "year_month_day.php 切片失败\n"); exit(3); }
$ymdEnd = strpos($ymdSource, '}', $ymdEnd) + 1;
$blocks[] = substr($ymdSource, $ymdStart, $ymdEnd - $ymdStart);

$header = "<?php\n"
    . "if (!defined('SECONDS_IN_DAY'))  define('SECONDS_IN_DAY', 86400);\n"
    . "// 被抽出来的代码引用的调试助手：harness 里不需要真的输出\n"
    . "function DebugString(\$str, \$bWrite = false) {}\n"
    . "function DebugGetDate(\$iTick, \$bTime = false) { return date('Y-m-d', \$iTick); }\n"
    . "function GetFontElement(\$str) { return \$str; }\n"
    . "function GetRemarkElement(\$str) { return \$str; }\n"
    . "\$GLOBALS['g_now_ymd'] = null;\n";

$tmp = tempnam(sys_get_temp_dir(), 'sma') . '.php';
// 见文件顶部注释：这里绝不能写 PHP 结束标签
file_put_contents($tmp, $header . implode("\n", $blocks) . "\n");
require $tmp;

// `GetNowYMD()` 会调 `$g_now_ymd->CheckTimeZone()`，且
// `_ignoreCurrentTradingData()` 会调 `$ymd->IsStockTradingHourEnd()` ——
// 这两个方法都只定义在切片范围外的 `TickYMD` 上（真实的 `NowYMD` 就是
// `extends TickYMD`，所以没问题）。这里照那个继承关系补一个固定时刻的类。
class FixedYMD extends TickYMD
{
    public function __construct($strText)
    {
        // 接受 '2026-10-09' 或 '2026-10-09 16:30'
        $parts = explode(' ', trim($strText));
        $arYMD = explode('-', $parts[0]);
        $hour = 0;
        $minute = 0;
        if (isset($parts[1]))
        {
            $arHM = explode(':', $parts[1]);
            $hour = intval($arHM[0]);
            $minute = isset($arHM[1]) ? intval($arHM[1]) : 0;
        }
        $iTick = mktime($hour, $minute, 0, intval($arYMD[1]), intval($arYMD[2]), intval($arYMD[0]));
        parent::__construct($iTick);
    }

    function CheckTimeZone() {}
}

foreach (array('_estSma', 'GetQuadraticEquationRoot', '_estBollingerBands',
               '_estNextBollingerBands', '_isWeekEnd', '_isMonthEnd',
               '_ignoreCurrentTradingData') as $needed)
{
    if (!function_exists($needed)) { fwrite(STDERR, "$needed was not defined\n"); exit(3); }
}

// ---------------------------------------------------------------------------
// 与 _php_sma_compare.py 一致的用例集（改一边必须同时改另一边）
// ---------------------------------------------------------------------------
//: 收敛的行情序列（最新在前），长度 64 —— 够 20 日布林与 next 布林用
$serie = array();
$value = 100.0;
for ($i = 0; $i < 64; $i++)
{
    $value += (($i % 7) - 3) * 0.37 + 0.021 * $i;
    $serie[] = round($value, 4);
}

//: 定长等值序列：用来暴露 off-by-one 与除零
$flat = array_fill(0, 32, 50.0);

//: 上升序列
$rising = array();
for ($i = 0; $i < 40; $i++)  $rising[] = 10.0 + $i;

//: 下降序列
$falling = array();
for ($i = 0; $i < 40; $i++)  $falling[] = 100.0 - 1.5 * $i;

//: 只有 3 个元素 —— 触发"元素不足仍除以 iNum"那条
$short = array(12.5, 11.0, 9.5);

//: 含负数（期货差价可能为负）
$negative = array(-3.5, -1.25, 0.0, 2.75, -8.0, 4.5, -0.5, 1.0, 6.25, -2.0,
                  -3.5, -1.25, 0.0, 2.75, -8.0, 4.5, -0.5, 1.0, 6.25, -2.0);

$series = array(
    'serie'    => $serie,
    'flat'     => $flat,
    'rising'   => $rising,
    'falling'  => $falling,
    'short'    => $short,
    'negative' => $negative,
);

function norm($value)
{
    if ($value === false)  return null;
    return $value;
}

function fmt($value)
{
    // PHP 与 Python 的科学计数法写法不同（E-9 vs E-09），所以数值比较交给
    // Python 侧的 normalize_value()，这里只保证精度一致
    if (is_float($value))  return sprintf('%.14G', $value);
    return $value;
}

$out = array();

// --- _estSma：按 (序列, avg) 逐个算 -----------------------------------------
foreach ($series as $name => $quotes)
{
    foreach (array(5, 10, 20, 2) as $avg)
    {
        $out['_estSma(' . $name . ',' . $avg . ')'] = fmt(floatval(_estSma($quotes, $avg)));
    }
}

// --- 二次方程：正常 / 判别式为 0 / 判别式为负 ---------------------------------
$quadratics = array(
    array(1.0, -3.0, 2.0),          // 两根 2 与 1
    array(1.0, 2.0, 1.0),           // 判别式 0
    array(1.0, 0.0, 1.0),           // 判别式 -4 → false
    array(-4.0, 8.0, 4.0),          // a 为负
    array(2.5, -7.5, 1.25),         // 非整数系数
    array(1e-6, 1e-3, 1.0),         // 极端系数
);
foreach ($quadratics as $index => $q)
{
    $r = GetQuadraticEquationRoot($q[0], $q[1], $q[2]);
    if ($r === false)
    {
        $out['GetQuadraticEquationRoot[' . $index . ']'] = null;
    }
    else
    {
        $out['GetQuadraticEquationRoot[' . $index . ']'] = array(fmt($r[0]), fmt($r[1]));
    }
}

// --- 布林两式：按序列算 ------------------------------------------------------
foreach ($series as $name => $quotes)
{
    $r = _estBollingerBands($quotes, 20);
    if ($r === false)
    {
        $out['_estBollingerBands(' . $name . ')'] = null;
    }
    else
    {
        $out['_estBollingerBands(' . $name . ')'] = array(fmt(floatval($r[0])), fmt(floatval($r[1])));
    }

    $rn = _estNextBollingerBands($quotes, 20);
    if ($rn === false)
    {
        $out['_estNextBollingerBands(' . $name . ')'] = null;
    }
    else
    {
        $out['_estNextBollingerBands(' . $name . ')'] = array(fmt(floatval($rn[0])), fmt(floatval($rn[1])));
    }
}

// --- 月末/周末判定：固定"现在"，逐日扫一大段 ---------------------------------
// 用固定日期当"今天"，避免依赖真实时钟（PHP 的 GetNowYMD 走 $g_now_ymd）
$fixedNow = array('2026-10-09', '2026-10-11', '2026-10-31', '2026-11-01');
$dates = array();
$cursor = strtotime('2026-09-25');
for ($i = 0; $i < 45; $i++)
{
    $dates[] = date('Y-m-d', $cursor + $i * 86400);
}

foreach ($fixedNow as $nowIndex => $nowText)
{
    $GLOBALS['g_now_ymd'] = new FixedYMD($nowText);
    foreach ($dates as $dateIndex => $dateText)
    {
        $key = '[' . $nowIndex . ',' . $dateIndex . ']';
        // 有"下一天"（时间上更早的一条）与没有，两条分支都要走
        $prev = isset($dates[$dateIndex + 1]) ? $dates[$dateIndex + 1] : false;
        $out['_isWeekEnd' . $key]     = _isWeekEnd($dateText, $prev) ? true : false;
        $out['_isMonthEnd' . $key]    = _isMonthEnd($dateText, $prev) ? true : false;
    }
}

// --- `_ignoreCurrentTradingData`：收盘前不能用今天的数据 ---------------------
// 这一条单独对照是有原因的：它决定 `_calcStartDate()` 的返回值，而
// `GetFromDate()` 是**按那个日期往回取**（`date <= start_date`）。
// 早期把方向写成 `>=` 时只取回一行，所有均线退化成同一个价格且不报错。
$nowCases = array(
    '2026-10-09 10:00',     // 当天盘中
    '2026-10-09 16:00',     // 边界：GetHour() == 16 → 仍算未收盘
    '2026-10-09 16:59',     // 边界内最后一分钟
    '2026-10-09 17:00',     // 收盘
    '2026-10-09 23:30',
    '2026-10-10 09:00',     // 次日
);
foreach ($nowCases as $nowIndex => $nowText)
{
    $GLOBALS['g_now_ymd'] = new FixedYMD($nowText);
    foreach (array('2026-10-09', '2026-10-08', '2026-10-10') as $dateIndex => $dateText)
    {
        $out['_ignoreCurrentTradingData[' . $nowIndex . ',' . $dateIndex . ']']
            = _ignoreCurrentTradingData($dateText) ? true : false;
    }
}

// `_getColorAndOrderArray` 与 `MaxMin` 是类方法，这里用等价的独立函数复刻，// 逐个动作与 php/stockhis.php:174-205 / 504-548 对齐。
class MaxMin
{
    var $fMax;
    var $fMin;

    public function __construct() { $this->fMax = false; $this->fMin = false; }
    function Init($fMax, $fMin)
    {
        if ($this->fMin == false && $this->fMax == false) { $this->fMin = $fMin; $this->fMax = $fMax; }
    }
    function Set($fVal)
    {
        if ($fVal > $this->fMax)  $this->fMax = $fVal;
        if ($fVal < $this->fMin)  $this->fMin = $fVal;
    }
    function Fit($fVal) { return ($fVal > $this->fMin && $fVal < $this->fMax) ? true : false; }
}

function getColorAndOrderArray($arSMA)
{
    $mm = new MaxMin();
    $mmW = new MaxMin();
    $arVal = array();
    $arColor = array();
    foreach ($arSMA as $strKey => $strVal)
    {
        $fVal = floatval($strVal);
        $strColor = false;
        $strFirst = substr($strKey, 0, 1);
        if ($strFirst == 'D')
        {
            $mm->Init(0.0, 10000000.0);
            $mm->Set($fVal);
            $arVal[] = $fVal;
        }
        else if ($strFirst == 'W')
        {
            $mmW->Init($mm->fMax, $mm->fMin);
            $mmW->Set($fVal);
            if ($mm->Fit($fVal))  $strColor = 'silver';
            else                  $arVal[] = $fVal;
        }
        else if ($strFirst == 'M')
        {
            if ($mm->Fit($fVal))        $strColor = 'silver';
            else if ($mmW->Fit($fVal))  $strColor = 'gray';
            else                        $arVal[] = $fVal;
        }
        else
        {
            if ($mm->Fit($fVal))  $strColor = 'silver';
            else                  $strColor = 'yellow';
        }
        $arColor[$strKey] = $strColor;
    }

    sort($arVal, SORT_NUMERIC);
    $ar = array();
    foreach ($arVal as $fVal)  $ar[] = number_format($fVal, 2, '.', '');
    return array($arColor, array_values(array_unique($ar)));
}

//: 与 PHP 读取顺序一致的 SMA 集合（先 D、再 W、再 M、最后 EMA）
$smaSets = array(
    'daily_first' => array(
        'D5' => '101.25', 'D10' => '99.5', 'D20' => '97.0',
        'W5' => '105.0', 'W10' => '95.0', 'W20' => '103.0',
        'M5' => '98.0', 'M10' => '102.0', 'M20' => '106.0',
        'EMA50' => '100.5', 'EMA200' => '90.0',
    ),
    'only_ema' => array('EMA50' => '55.0', 'EMA200' => '44.0'),
    'all_inside' => array(
        'D5' => '10.0', 'D10' => '20.0', 'W5' => '15.0', 'M5' => '12.0',
        'EMA50' => '18.0',
    ),
    'all_outside' => array(
        'D5' => '1.0', 'D10' => '2.0', 'W5' => '100.0', 'M5' => '200.0',
        'EMA50' => '300.0',
    ),
);
foreach ($smaSets as $name => $sma)
{
    list($colors, $order) = getColorAndOrderArray($sma);
    foreach ($colors as $key => $color)
    {
        $out['color(' . $name . ',' . $key . ')'] = ($color === false) ? null : $color;
    }
    $out['order(' . $name . ')'] = $order;
}

// MaxMin 的三个动作单独钉一遍（init 只生效一次、fit 是开区间）
$mm = new MaxMin();
$out['MaxMin.initial'] = array(($mm->fMax === false) ? null : $mm->fMax,
                                ($mm->fMin === false) ? null : $mm->fMin);
$mm->Init(0.0, 10000000.0);
$out['MaxMin.after_init'] = array(fmt($mm->fMax), fmt($mm->fMin));
$mm->Init(5.0, 6.0);        // 应当不生效
$out['MaxMin.after_second_init'] = array(fmt($mm->fMax), fmt($mm->fMin));
foreach (array(-1.0, 5.0, 20000000.0, 3.0) as $v)  $mm->Set($v);
$out['MaxMin.after_sets'] = array(fmt($mm->fMax), fmt($mm->fMin));
$out['MaxMin.fit_inside']  = $mm->Fit(4.0) ? true : false;
$out['MaxMin.fit_below']   = $mm->Fit(-10.0) ? true : false;
$out['MaxMin.fit_above']   = $mm->Fit(1e9) ? true : false;

$json = json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
if (isset($argv[1]) && $argv[1] !== '-')
{
    file_put_contents($argv[1], $json);
    fwrite(STDERR, "php cases: " . count($out) . " -> " . $argv[1] . "\n");
}
else
{
    echo $json, "\n";
}
