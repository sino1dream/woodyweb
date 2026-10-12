<?php
// rtrim0() reference for the fuzzer, reading one value per line from stdin.
//
//   php -n _php_rtrim0_dump.php            # values on stdin, JSON array on stdout
//
// Why not just call rtrim0() in php/debug.php? Because `php -n` skips php.ini and
// therefore sets ini precision to its built-in default (-1), which makes PHP print
// the full 17-digit shortest round-trip instead of strval()'s usual 14 significant
// digits. Production runs with precision=14, so this script reproduces that rule
// explicitly whenever the current ini precision is not 14.

ini_set('display_errors', 'stderr');
error_reporting(E_ALL & ~E_WARNING & ~E_DEPRECATED);

require __DIR__ . '/../../../php/debug.php';

function _ref_strval_float($value)
{
    if (ini_get('precision') == 14) {
        return strval($value);      // 真实的 PHP 行为
    }
    // 复刻 precision=14 时的 strval()：整数值不带小数点，指数用 1.0E-7 形式
    $text = sprintf('%.14G', $value);
    if (strpos($text, 'E') !== false)
    {
        list($mantissa, $exponent) = explode('E', $text);
        if (ctype_digit(ltrim($mantissa, '-')))    $mantissa .= '.0';
        $text = $mantissa . 'E' . ($exponent[0] === '-' ? '-' : '+') . abs(intval($exponent));
    }
    else if (substr($text, -2) === '.0')
    {
        $text = substr($text, 0, -2);
    }
    return $text;
}

function _ref_rtrim0($str)
{
    if (ini_get('precision') == 14)        return rtrim0($str);
    return _ref_strval_float(floatval($str));
}

$values = array();
while (($line = fgets(STDIN)) !== false)
{
    $values[] = rtrim($line, "\r\n");
}

$out = array();
foreach ($values as $value)
{
    $out[] = _ref_rtrim0($value);
}
echo json_encode($out);
