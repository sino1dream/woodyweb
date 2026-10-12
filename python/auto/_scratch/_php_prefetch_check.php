<?php
// stockprefetch 判定逻辑的 PHP 参考实现。
//
//   php -n _php_prefetch_check.php        # JSON on stdout
//
// 为什么不用 php/stock/stockprefetch.php 原文件？因为它的两个判定函数都要读
// 数据库（SqlGetStockId / GetStockHistorySql），而这里只需要文件时间驱动的部分。
// 所以这里把原文件里与文件时间相关的函数**逐字复制**过来，参数直接收时间戳和
// 修改时间，并与 PHP 原文件一样用真实临时文件提供 filemtime()。
//
// 如果 php/stock/stockprefetch.php 的逻辑改了，这个脚本要同步更新；脚本文末会
// 打印源码指纹，便于发现漂移。

ini_set('display_errors', 'stderr');
error_reporting(E_ALL & ~E_WARNING & ~E_DEPRECATED);

require __DIR__ . '/../../../php/debug.php';
require __DIR__ . '/../../../php/class/year_month_day.php';
require __DIR__ . '/../../../php/stock/stocksymbol.php';

// ---------------------------------------------------------------------------
// 以下函数从 php/stock/stockprefetch.php 逐字复制（仅去掉数据库相关部分）
// ---------------------------------------------------------------------------
function _checkBetweenMarketClose($now_ymd, $iFileTime, $iWeekday, $iWeekend)
{
	$iDiff = $now_ymd->GetTick() - $iFileTime;
	if ($now_ymd->IsSunday())
	{
		if ($iDiff > ($iWeekday + $iWeekend + 24) * SECONDS_IN_HOUR)							return true;
	}
	else if ($now_ymd->IsSaturday())
	{
		if ($iDiff > ($iWeekday + $iWeekend) * SECONDS_IN_HOUR)								return true;
	}
	else
	{
		if ($iDiff > $iWeekday * SECONDS_IN_HOUR)												return true;
	}

	return false;
}

function StockNeedNewQuotes($sym, $strFileName, $iInterval = SECONDS_IN_MIN)
{
	global $now_ymd_global;
	$now_ymd = $now_ymd_global;
	if (($iFileTime = $now_ymd->NeedFile($strFileName, $iInterval)) == false)		return false;	// update on every minute

	if ($sym->IsStockMarketTrading($now_ymd))											return true;
	$file_ymd = new TickYMD($iFileTime);
	if ($sym->IsStockMarketTrading($file_ymd))											return true;

	if ($now_ymd->GetDay() == $file_ymd->GetDay())
	{
		if ($sym->IsBeforeStockMarket($now_ymd->GetHourMinute()))						return false;
		if ($sym->IsAfterStockMarket($file_ymd->GetHourMinute()))						return false;
	}

	if ($sym->IsSymbolA())		return _checkBetweenMarketClose($now_ymd, $iFileTime, 2, 8);
	else if ($sym->IsSymbolH())	return _checkBetweenMarketClose($now_ymd, $iFileTime, 3, 8);
	return _checkBetweenMarketClose($now_ymd, $iFileTime, 16, 4);
}

function _isFutureMarketTrading($ymd)
{
   	$iHour = $ymd->GetHour();
   	if ($ymd->IsSunday())
   	{
   		if ($iHour < 18)		return false;
   	}
   	else if ($ymd->IsSaturday())
   	{
   		return false;
   	}
   	else if ($ymd->IsFriday())
   	{
   		if ($iHour >= 17)		return false;
   	}
   	else
   	{
   		if ($iHour == 17)		return false;
   	}
   	return true;
}

function FutureNeedNewFile($strFileName, $iInterval = SECONDS_IN_MIN)
{
	global $now_ymd_global;
	$now_ymd = $now_ymd_global;
	if (($iFileTime = $now_ymd->NeedFile($strFileName, $iInterval)) == false)		return false;	// update on every minute

	if (_isFutureMarketTrading($now_ymd))    												return true;
    if (_isFutureMarketTrading(new TickYMD($iFileTime)))								return true;
    return _checkBetweenMarketClose($now_ymd, $iFileTime, 23, 7);
}

// ---------------------------------------------------------------------------
// 跨系统稳定的市场时间换算
// ---------------------------------------------------------------------------
function _ref_convert($timestamp, $timeZone)
{
	// date_default_timezone_set()/date() 依赖系统的时区库，Windows 上按中国标准时间
	// 硬编码处理（无夏令时），保证 PHP 与 Python 得到同一结果。
	if ($timeZone === 'Asia/Shanghai')
	{
		return array(date('Y-m-d', $timestamp + 8 * SECONDS_IN_HOUR), (int)date('G', $timestamp + 8 * SECONDS_IN_HOUR));
	}
	// 美东按 EDT（UTC-4）近似；本脚本只在 TimeZoneTest 之外用它，用于说明偏差
	return array(date('Y-m-d', $timestamp - 4 * SECONDS_IN_HOUR), (int)date('G', $timestamp - 4 * SECONDS_IN_HOUR));
}

$out = array();

// ---- _isFutureMarketTrading：直接对函数结果取样 -----------------------------
$sampleTimestamps = array();
for ($i = 0; $i < 400; $i ++)
{
	$sampleTimestamps[] = 1767225600 + $i * 911;   // 从 2026-01-01 起，步长取质数避免重复
}
foreach ($sampleTimestamps as $timestamp)
{
	list($ymd, $hour) = _ref_convert($timestamp, 'Asia/Shanghai');
	// 用上海当地时间构造 TickYMD，等价于 PHP 在 SetTimeZone() 之后的行为
	$localTimestamp = $timestamp + 8 * SECONDS_IN_HOUR;
	$key = 'futureTrading|' . $localTimestamp;
	$out[$key] = _isFutureMarketTrading(new TickYMD($localTimestamp));
}

// ---- _checkBetweenMarketClose：三种市场参数分别取样 -------------------------
$marketParams = array('A' => array(2, 8), 'H' => array(3, 8), 'US' => array(16, 4));
foreach ($sampleTimestamps as $timestamp)
{
	$localTimestamp = $timestamp + 8 * SECONDS_IN_HOUR;
	foreach ($marketParams as $name => $params)
	{
		list($weekday, $weekend) = $params;
		foreach (array(SECONDS_IN_MIN, SECONDS_IN_HOUR, 12 * SECONDS_IN_HOUR, 3 * SECONDS_IN_DAY) as $delta)
		{
			$fileTime = $localTimestamp - $delta;
			$key = 'betweenClose|' . $name . '|' . $localTimestamp . '|' . $fileTime;
			$out[$key] = _checkBetweenMarketClose(new TickYMD($localTimestamp), $fileTime, $weekday, $weekend);
		}
	}
}

// ---- NeedFile：用真实临时文件提供 filemtime --------------------------------
$tempFile = tempnam(sys_get_temp_dir(), 'prefetch');
$needFileCases = array();
foreach ($sampleTimestamps as $timestamp)
{
	$localTimestamp = $timestamp + 8 * SECONDS_IN_HOUR;
	foreach (array(0, 1, 59, 60, 61, 3600) as $age)
	{
		$fileTime = $localTimestamp - $age;
		touch($tempFile, $fileTime);
		clearstatcache(true, $tempFile);
		$result = (new TickYMD($localTimestamp))->NeedFile($tempFile, SECONDS_IN_MIN);
		$out['needFile|' . $localTimestamp . '|' . $fileTime] = $result;
	}
}
unlink($tempFile);

// ---- StockNeedNewQuotes：普通股票是否需要新行情 ----------------------------
// 临时文件的 mtime 与 Python 侧构造的 file_mtime 取值一致，因此即便路径不同，
// 判定逻辑所处的条件完全相同。
//
// 这里只放时区固定为 UTC+8 的符号（A 股 / 港股 / 上证指数）。美股要按
// America/New_York 的夏令时规则换算当地时间，而本脚本若用 date() 会依赖系统
// 时区库、用固定偏移又会与真实夏令时冲突，所以美股的完整分支由
// python/auto/test_stockprefetch.py 的单元测试覆盖（那里用 zoneinfo，能正确处理
// EST/EDT 切换）。_checkBetweenMarketClose() 的 US 参数已经在上面单独验证过。
$quoteFile = tempnam(sys_get_temp_dir(), 'quote');
$symbols = array('SH600000', 'SZ300750', 'SH000300', '00700', '^HSI', '^HSCE');
foreach ($symbols as $symbolText)
{
	$sym = new StockSymbol($symbolText);
	foreach ($sampleTimestamps as $timestamp)
	{
		$localTimestamp = $timestamp + 8 * SECONDS_IN_HOUR;
		foreach (array(0, 30, 60, 600, 7200, 6 * SECONDS_IN_HOUR, 24 * SECONDS_IN_HOUR, 3 * SECONDS_IN_DAY) as $age)
		{
			$fileTime = $localTimestamp - $age;
			touch($quoteFile, $fileTime);
			clearstatcache(true, $quoteFile);
			$now_ymd_global = new TickYMD($localTimestamp);
			$key = 'stockNeed|' . $symbolText . '|' . $localTimestamp . '|' . $fileTime;
			$out[$key] = StockNeedNewQuotes($sym, $quoteFile);
		}
	}
}
unlink($quoteFile);

// ---- 源码指纹，便于发现 PHP 原文件改过而本脚本没同步 ----------------------
$sourcePath = __DIR__ . '/../../../php/stock/stockprefetch.php';
$out['_source_sha1'] = is_file($sourcePath) ? sha1_file($sourcePath) : 'missing';
$out['_year_month_day_sha1'] = sha1_file(__DIR__ . '/../../../php/class/year_month_day.php');

echo json_encode($out);
