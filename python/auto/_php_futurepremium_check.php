<?php
// ---------------------------------------------------------------------------
// GetFutureInterestPremium 的 PHP 参考值
//
//   D:\xampp\php\php.exe -n -d display_errors=stderr _php_futurepremium_check.php
//
// ## 做法：抽函数体 + 切片日期类
//
// 与 `_php_sma_check.php` 同款：不 require 整个 `php/ui/smaparagraph.php`
// （那会拖进 `calibrationhistoryparagraph.php` 与一堆渲染函数），
// 只抠 `GetFutureInterestPremium` 的函数体；日期类按 `YearMonthDay` 那一段
// 切片，再手工塞一个固定时刻的 `$g_now_ymd` 让"现在"可控。
//
// ## 为什么连 StringYMD 一起用真的
//
// `GetTick()` 是 `mktime(0,0,0,...)`，天数差就来自它。用真的类才能保证
// "零点对零点"这件事被原样验证 —— 自己写 `(end - begin)/86400`
// 就看不出 PHP 到底有没有归一到零点。
//
// **注意**：本文件里不能出现 PHP 结束标签（注释里也不行）。
// ---------------------------------------------------------------------------

$phpDir = realpath(__DIR__ . '/../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }

function FExtractFunction($source, $name)
{
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

// ① 被对照的函数本体
$smapara = file_get_contents($phpDir . DIRECTORY_SEPARATOR . 'ui' . DIRECTORY_SEPARATOR . 'smaparagraph.php');
$body = FExtractFunction($smapara, 'GetFutureInterestPremium');
if ($body === null) { fwrite(STDERR, "GetFutureInterestPremium 抽取失败\n"); exit(3); }

// ② 日期类：切到 GetNowYMD() 之前，跳过末尾的真实时钟初始化
$ymdSource = file_get_contents($phpDir . DIRECTORY_SEPARATOR . 'class' . DIRECTORY_SEPARATOR . 'year_month_day.php');
$ymdStart = strpos($ymdSource, 'class YearMonthDay');
$ymdEnd = strpos($ymdSource, 'function GetNowYMD()');
if ($ymdStart === false || $ymdEnd === false) { fwrite(STDERR, "year_month_day.php 切片失败\n"); exit(3); }
$ymdEnd = strpos($ymdSource, '}', $ymdEnd) + 1;
$ymdClass = substr($ymdSource, $ymdStart, $ymdEnd - $ymdStart);

$header = "<?php\n"
    . "if (!defined('SECONDS_IN_DAY'))  define('SECONDS_IN_DAY', 86400);\n"
    . "\$GLOBALS['g_now_ymd'] = null;\n"
    . "// 切片里引用到的调试助手：harness 不需要真的输出\n"
    . "function DebugString(\$str, \$bWrite = false) {}\n"
    . "function DebugGetDate(\$iTick, \$bTime = false) { return date('Y-m-d', \$iTick); }\n";

$blocks = array($ymdClass, $body);
$tmp = tempnam(sys_get_temp_dir(), 'fprem') . '.php';
file_put_contents($tmp, $header . implode("\n", $blocks) . "\n");
require $tmp;

// 固定时刻的 YMD：GetNowYMD() 会返回它，于是"今天"是可控的
class FixedYMD extends StringYMD
{
    public function __construct($strText)
    {
        parent::__construct($strText);
        $GLOBALS['g_now_ymd'] = $this;
    }
    function CheckTimeZone() {}
}
// `GetNowYMD()` 已经在切片里被 require 进来（它就在 `year_month_day.php`
// 那段里），这里**不能再声明一次**，否则 Cannot redeclare。
// 它读的是 `global $g_now_ymd`，上面 FixedYMD 的构造已经把它设好了。

// ---- 用例 ----------------------------------------------------------------
// 与 _php_futurepremium_compare.py 的 CASES **必须逐项一致**
$cases = array(
    // (今天, 到期日, 利率百分点)
    array('2026-10-11', '2026-12-18', 5.00625),
    array('2026-10-11', '2025-03-21', 5.00625),   // 到期日已过 → 溢价 < 1
    array('2026-10-11', '2026-10-11', 5.00625),   // 同一天 → 恰好 1.0
    array('2026-01-01', '2026-12-31', 5.00625),   // 跨年 364 天
    array('2026-10-11', '2027-03-19', 4.5),
    array('2026-10-11', '2026-11-20', 0.0),
    array('2024-02-28', '2024-03-01', 5.00625),   // 闰年 2 天
);

// 回调那一侧的两步舍入（round(4*$f)/4 再 round(...,2)）
// PHP 的 round() 是 half away from zero，与 Python 内建不同，必须真跑一遍
$roundCases = array(
    array(31137.575, 1.009326712328767),
    array(31137.575, 1.01),
    array(7866.45, 1.02),
    array(100.0, 1.0),
    array(2.5, 1.0),
    array(0.125, 1.0),
);

$results = array();
foreach ($cases as $i => $c) {
    // 关键：PHP 那句 `date_default_timezone_set('America/New_York')` 在函数体
    // **中间**执行 —— 所以第一次调用时 `$end_ymd` 用的还是进程默认时区，
    // 而调用完之后进程时区被永久改掉了（它从不恢复）。
    //
    // 那个"第一次不一样"是 PHP 的隐性环境依赖，不是我们要对齐的语义。
    // 这里在每个用例前先把时区设成美东，让两次 `mktime` 落在同一时区上 ——
    // 这样结果可复现，也才是作者在美东部署时的真实行为。
    date_default_timezone_set('America/New_York');
    new FixedYMD($c[0]);
    $results[] = array(
        'index' => $i,
        'premium' => strval(GetFutureInterestPremium($c[2] / 100.0, $c[1])),
    );
}

$roundResults = array();
foreach ($roundCases as $j => $r) {
    $f = floatval($r[0]) * $r[1];
    $f = round(4.0 * $f) / 4.0;
    $roundResults[] = array(
        'index' => $j,
        'value' => strval(round($f, 2)),
    );
}

echo json_encode(array('premium' => $results, 'rounded' => $roundResults));
exit(0);
