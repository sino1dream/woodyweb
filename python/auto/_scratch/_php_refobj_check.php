<?php
// Differential harness: exercise the **pure math** functions of the
// `refobj` port (php/stock/qdiiref.php, fundref.php, mysqlref.php, stockref.php)
// and print a JSON map of key -> value, so _php_refobj_compare.py can compare
// case by case.
//
// Usage (normally driven by the Python side, which builds the identical key set):
//
//     php -n -d display_errors=stderr _php_refobj_check.php -    # JSON on stdout
//
// Why only the pure math: the rest of refobj is stateful (it reads MySQL) and
// cannot be exercised without a database. The formulas below are exactly where
// a silent porting error would hurt -- they feed every number on the page.
//
// Function bodies are extracted **by name** from the PHP sources rather than by
// line number, so editing the PHP keeps this check honest.
//
// Diagnostics go to stderr so stdout carries nothing but the JSON payload.
ini_set('display_errors', 'stderr');
error_reporting(E_ALL & ~E_WARNING & ~E_DEPRECATED);

$roots = array(__DIR__ . '/../../../php', __DIR__ . '/php');
$phpDir = false;
foreach ($roots as $candidate)
{
    if (is_dir($candidate)) { $phpDir = $candidate; break; }
}
if ($phpDir === false) { fwrite(STDERR, "cannot find the php/ directory\n"); exit(2); }

// ---------------------------------------------------------------------------
// Extract a function (or a "function-like" block) by name, brace matched.
// ---------------------------------------------------------------------------
function extract_block($source, $pattern, $what)
{
    if (!preg_match($pattern, $source, $m, PREG_OFFSET_CAPTURE))
    {
        fwrite(STDERR, "cannot locate $what\n");
        exit(2);
    }
    $start = $m[0][1];
    $cursor = strpos($source, '{', $start);
    if ($cursor === false) { fwrite(STDERR, "$what has no body\n"); exit(2); }

    $depth = 0;
    $length = strlen($source);
    for ($i = $cursor; $i < $length; $i++)
    {
        if ($source[$i] === '{')      $depth++;
        else if ($source[$i] === '}') { $depth--; if ($depth === 0) return substr($source, $start, $i - $start + 1); }
    }
    fwrite(STDERR, "$what body is unterminated\n");
    exit(2);
}

function load_file($phpDir, $relative)
{
    $path = $phpDir . '/' . $relative;
    if (!is_file($path)) { fwrite(STDERR, "cannot find $relative\n"); exit(2); }
    return file_get_contents($path);
}

$deps = array(
    // php/stock.php: StockGetPercentage, StockCalcHedge
    "function\s+StockGetPercentage\s*\(",
    "function\s+StockCalcHedge\s*\(",
);
$stockSrc = load_file($phpDir, 'stock.php');

$qdiirefSrc = load_file($phpDir, 'stock/qdiiref.php');
$fundrefSrc = load_file($phpDir, 'stock/fundref.php');

$blocks = array();
foreach ($deps as $pattern)
{
    $blocks[] = extract_block($stockSrc, '/' . $pattern . '/', $pattern);
}
foreach (array('QdiiGetCalibration', 'QdiiGetVal', 'QdiiGetPeerVal') as $name)
{
    $blocks[] = extract_block($qdiirefSrc, '/function\s+' . $name . '\s*\(/', $name);
}
foreach (array('FundAdjustPosition', 'FundReverseAdjustPosition') as $name)
{
    $blocks[] = extract_block($fundrefSrc, '/function\s+' . $name . '\s*\(/', $name);
}

// MIN_FLOAT_VAL is a constant in php/debug.php; the harness only needs its value.
$header = "<?php\n"
        . "if (!defined('MIN_FLOAT_VAL')) define('MIN_FLOAT_VAL', 0.0000005);\n"
        . "function DebugString(\$str) { return \$str; }\n";

$tmp = tempnam(sys_get_temp_dir(), 'ro') . '.php';
// NOTE: never write the PHP closing tag inside a comment in this file --
// the parser ends the script there and everything after it is echoed verbatim,
// which corrupts the JSON on stdout (this bit us once already).
$payload = $header . implode("\n", $blocks) . "\n";
if (getenv('REFOBJ_CHECK_DEBUG'))
{
    fwrite(STDERR, "=== phpDir: $phpDir\n");
    fwrite(STDERR, "=== tmp: $tmp\n");
    fwrite(STDERR, "=== payload (" . strlen($payload) . " bytes) ===\n");
    fwrite(STDERR, $payload . "\n=== end payload ===\n");
}
file_put_contents($tmp, $payload);
require $tmp;

foreach (array('QdiiGetCalibration', 'FundAdjustPosition', 'StockCalcHedge') as $needed)
{
    if (!function_exists($needed)) { fwrite(STDERR, "$needed was not defined\n"); exit(3); }
}

// ---------------------------------------------------------------------------
// Same case set as _php_refobj_compare.py
// ---------------------------------------------------------------------------
function norm($value)
{
    if ($value === false)  return null;
    if ($value === true)   return true;
    return $value;
}

//: Canonical key suffix. **Indices, not numbers**: PHP's "%.12E" prints "E-9"
//: while Python prints "E-09" (PHP does not zero-pad the exponent), so building
//: keys from the numbers themselves made one case produce two different keys --
//: every case showed up as PHP-ONLY plus PY-ONLY. Indices side-step that
//: entirely, and the two case tables below must stay in the same order.
//: (No helper needed any more; keys are just '[' . $index . ']'.)
function fmt($value)
{
    // Keep floats repr-stable across the two languages: use PHP's 14 significant
    // digits, the same shortest-repr rule the port already applies for rtrim0.
    if (is_float($value))  return sprintf('%.14G', $value);
    return $value;
}

//: Every math call goes through here so a thrown Error becomes a value instead of
//: killing the whole harness. Some cases legitimately blow up in PHP 8
//: (``FundReverseAdjustPosition`` divides by the position, and a position of 0 is
//: an error there) while the Python port yields ``inf`` -- recording that
//: difference is the point, not hiding it.
const ERR_PREFIX = '#ERR:';
function call($name, $args)
{
    try
    {
        return fmt(call_user_func_array($name, $args));
    }
    catch (Throwable $e)
    {
        return ERR_PREFIX . get_class($e);
    }
}

//: Drop ``k()``: keys are indices now, so the numeric spelling never reaches a key.
$out = array();

//: Same value set as _php_refobj_compare.py's VALUES.
$values = array(
    0.0, 0.5, 1.0, 1.5, 2.0, 10.0, 100.0,
    -3.5, -1.0,
    1e-6, 1e-7, 4e-7, 5e-7, 6e-7, 1e-9,
    1e6, 1e9,
    4.814, 4.884, 32.079543, 749.378, 6.705, 0.95,
);
$count = count($values);

// triples: (v[i], v[i+3], v[i+7]) -- non-overlapping indices avoid degenerate cases
$triples = array();
for ($i = 0; $i < $count - 3; $i += 4)
{
    $triples[] = array($values[$i], $values[($i + 3) % $count], $values[($i + 7) % $count]);
}
foreach ($triples as $index => $t)
{
    $key = '[' . $index . ']';
    $out['QdiiGetCalibration' . $key] = call('QdiiGetCalibration', $t);
    $out['QdiiGetVal' . $key]          = call('QdiiGetVal', $t);
    $out['QdiiGetPeerVal' . $key]      = call('QdiiGetPeerVal', $t);
}

// AdjustPosition family: (position, value, old value)
$adjusts = array();
foreach (array(0.95, 1.0) as $position)
{
    for ($i = 0; $i < $count; $i += 5)
    {
        $adjusts[] = array($position, $values[$i], $values[($i + 2) % $count]);
    }
}
foreach ($adjusts as $index => $t)
{
    $key = '[' . $index . ']';
    $out['FundAdjustPosition' . $key]        = call('FundAdjustPosition', $t);
    $out['FundReverseAdjustPosition' . $key] = call('FundReverseAdjustPosition', $t);
}

// Percentage: divisor / dividend -- threshold sides of MIN_FLOAT_VAL (5e-7)
$percents = array(
    array(4e-7, 1.0), array(5e-7, 1.0), array(6e-7, 1.0),
    array(0.0, 1.0), array(0.0, 0.0),
    array(4.814, 4.884), array(3.294, 3.312), array(2.374, 2.398),
    array(1.0, 1.0), array(1.0, 0.0), array(1.0, 2.0), array(-1.0, 2.0),
    array(100.0, 101.0), array(1e9, 1e9 - 1.0),
);
foreach ($percents as $index => $t)
{
    $out['StockGetPercentage[' . $index . ']'] = call('StockGetPercentage', $t);
}

// Hedge: calibration / position
$hedges = array(
    array(32.079543, 0.95), array(22.110973, 0.95), array(13.66412, 1.0),
    array(100358.446, 1.0), array(1.0, 0.95), array(0.0, 0.95),
    array(-5.0, 0.5), array(-5.0, -0.5), array(1e-9, 0.95),
);
foreach ($hedges as $index => $t)
{
    $out['StockCalcHedge[' . $index . ']'] = call('StockCalcHedge', $t);
}

// Quantity conversions are intdiv-style on the reference object; the port has
// them as methods, so mirror the formula here with the same factor set.
$factors = array(1.0, 32.079543, 0.5, 2.5);
$quantities = array(100, 0, -100, 7, 999999);
foreach ($factors as $f_index => $f)
{
    foreach ($quantities as $q_index => $q)
    {
        $key = '[' . $f_index . ',' . $q_index . ']';
        $out['GetEstQuantity' . $key]  = intval($q / $f);
        $out['GetQdiiQuantity' . $key] = intval($q * $f);
    }
}

$json = json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
if (isset($argv[1]) && $argv[1] !== '-') {
    file_put_contents($argv[1], $json);
    fwrite(STDERR, "php cases: " . count($out) . " -> " . $argv[1] . "\n");
} else {
    fwrite(STDERR, "php cases: " . count($out) . "\n");
    echo $json;
}
