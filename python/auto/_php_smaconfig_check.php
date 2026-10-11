<?php
/**
 * 拿真实 PHP 对照 SMA 缓存文件的**路径规则**与 **INI 格式**。
 *
 * 用法::
 *
 *   php _php_smaconfig_check.php <root>
 *
 * 对照两件事：
 *
 * 1. ``DebugGetConfigFileName($symbol)`` 拼出来的路径，与 Python 的
 *    ``stockref.config_file_name(symbol, root)`` 是否一致；
 * 2. ``php/class/ini_file.php`` 的 ``INIFile`` 写出来的文件，Python 能不能读；
 *    反过来 Python 写的，PHP 能不能读。
 *
 * ``<root>`` 当 ``$_SERVER['DOCUMENT_ROOT']`` 用 —— 一定是临时目录，
 * PHP 的 ``DebugGetPath()`` 会在它下面 mkdir。
 */

$strRoot = $argv[1];
$_SERVER['DOCUMENT_ROOT'] = $strRoot;

// ``DebugString()``（debug.php:186）会写一行 ``UrlGetIp().UrlGetUri()``，
// 而 ``UrlGetIp()`` 在 ``REMOTE_ADDR`` 缺失时直接 ``die('Unknown IP')`` ——
// 于是 ``INIFile`` 报"文件不存在"时整个 harness 会静默死掉。
// 命令行里本来就没有这两个变量，补上即可（与真实请求无关，只是让它别 die）。
$_SERVER['REMOTE_ADDR'] = '127.0.0.1';
$_SERVER['HTTP_HOST'] = 'localhost';
// ``UrlGetUri()`` 还要求 URI 长得像 ``*.php``，否则同样 die。
$_SERVER['REQUEST_URI'] = '/index.php';

$strPhpDir = dirname(dirname(__DIR__)) . DIRECTORY_SEPARATOR . 'php';
require_once($strPhpDir . DIRECTORY_SEPARATOR . 'url.php');
require_once($strPhpDir . DIRECTORY_SEPARATOR . 'debug.php');
require_once($strPhpDir . DIRECTORY_SEPARATOR . 'class' . DIRECTORY_SEPARATOR . 'ini_file.php');

function _slash($str)
{
    return str_replace('\\', '/', $str);
}

$arSymbols = array('^NDX', 'hf_NQ', 'SZ161130', 'A/B', 'QQQ', 'fx_susdcny', '^GSPC', 'hf_ES');

$arPaths = array();
foreach ($arSymbols as $strSymbol)
{
    $arPaths[] = array(
        'symbol' => $strSymbol,
        'path'   => _slash(DebugGetConfigFileName($strSymbol)),
    );
}

// ① PHP 写一份，让 Python 去读
$strPhpWritten = DebugGetConfigFileName('^NDX');
$cfg = new INIFile($strPhpWritten);
$cfg->add_group('SMA');
$cfg->set_var('SMA', 'Date', '2026-10-09');
$cfg->set_var('SMA', 'D5', '100.5');
$cfg->set_var('SMA', 'DBOLLUP', '101.25');
$cfg->save_data();

// ② Python 写一份（由 compare 脚本先落盘），PHP 去读
$strPythonWritten = $strRoot . '/from_python.txt';
$cfg2 = new INIFile($strPythonWritten);
$arRead = array();
if ($cfg2->group_exists('SMA'))
{
    foreach (array('Date', 'D5', 'DBOLLUP', 'WBOLLUP') as $strName)
    {
        $strVal = $cfg2->read_var('SMA', $strName);
        $arRead[] = array('name' => $strName, 'value' => ($strVal === false ? null : $strVal));
    }
}

echo json_encode(array(
    'root'              => _slash($strRoot),
    'paths'             => $arPaths,
    'php_written_file'  => _slash($strPhpWritten),
    'php_written_bytes' => file_get_contents($strPhpWritten),
    'read_from_python'  => $arRead,
));
