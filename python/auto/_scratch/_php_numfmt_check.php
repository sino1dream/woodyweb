<?php
// Numeric / time helper reference for the stockref port.
//
//   php -n _php_numfmt_check.php            # JSON on stdout
//
// Prints the PHP result for rtrim0(), GetHM(), and the rtrim patterns used by
// GetPercentageText(), so the Python helpers can be compared case by case.

ini_set('display_errors', 'stderr');
error_reporting(E_ALL & ~E_WARNING & ~E_DEPRECATED);

require __DIR__ . '/../../../php/debug.php';

// rtrim0() is strval(floatval($str)) in PHP. Cover the shapes that show up in
// quote files (prices, volumes) plus the exponent boundaries where PHP switches
// to its 14-significant-digit %.14G form.
$values = array(
    '0', '0.000', '-0', '3.0', '1.5', '1.50', '0.1', '-2.25', '100',
    '1234.5678', '0.001', '0.0001', '0.00001', '0.000001', '0.0000001', '0.00000001',
    '1e-5', '1e-6', '1e-7', '1e-8', '1.5e-7', '2.5e-7',
    '1e5', '1e10', '1e14', '1e15', '1e16', '1e20', '1e21', '1e-20',
    '123456789012345', '1234567890123456', '12345678901234567',
    '123456789012345678', '1234567890123456789',
    '9007199254740993', '0.1', '0.2', '0.3', '0.30000000000000004',
    '3.14159265358979', '3.141592653589793', '1.7976931348623157e308',
    '5e-324', '2.2250738585072014e-308', '1.0000000000000002',
    '99999999999999.9', '999999999999999.9', '0.1234567890123456',
    '2147483647', '4294967296', '-1e-7', '-1e20', '1.5', '', 'abc', ' 42 ',
    '1,234', '0x1A', '1e', '.5', '5.', '+7', '-.5',
);

$out = array();
foreach ($values as $v) {
    $out['rtrim0(' . $v . ')'] = rtrim0($v);
    $out['floatval(' . $v . ')'] = floatval($v);
}

// GetHM(): substr($strHMS, 0, 5) -- pure string slicing.
$times = array('06:56:22', '08:55:00', '00:00:00', '12:00', '', '9:5:3', '23:59:59');
foreach ($times as $t) {
    $out['GetHM(' . $t . ')'] = GetHM($t);
}

// The rtrim chain that GetPercentageText() applies after number_format(x, 2).
// Use sprintf('%.2f') instead of number_format() on purpose: number_format()
// follows the ini "precision" setting, so it would not be reproducible across
// environments. The Python side uses '%.2f' as well.
$percents = array('0', '0.5', '1.00', '-1.50', '12.34', '-0.05', '100.00', '2.10', '-0.00');
foreach ($percents as $p) {
    $formatted = sprintf('%.2f', floatval($p));
    $trimmed = rtrim(rtrim($formatted, '0'), '.');
    $out['percent_display(' . $p . ')'] = ($formatted != '' && $formatted != '0') ? $trimmed . '%' : $trimmed;
}

// IsZeroString(): abs(floatval($str)) < MIN_FLOAT_VAL
$zero_candidates = array('0', '0.000', '0.0000004', '0.0000005', '0.0000006', '-0.0000004', '1', '', 'abc');
foreach ($zero_candidates as $z) {
    $out['IsZeroString(' . $z . ')'] = IsZeroString($z);
}

echo json_encode($out, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);
