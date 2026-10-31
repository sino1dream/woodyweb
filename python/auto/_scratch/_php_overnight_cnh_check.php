<?php
/**
 * 义工群对冲计算器（`woody/res/overnightcnhcn.php`）的 PHP 真身探针。
 *
 * ## 这一页为什么没法整份 require
 *
 * 页面上的每个数都从**引用对象**上取，而 PHP 的引用对象
 * （`MyStockReference` / `QdiiReference`）**构造时联网抓新浪行情**。盘口五档
 * 在 PHP 侧只有"现场抓"这一条路 —— 库里那张 `realtimequote` 快照表是 Python
 * 迁移时才加的，PHP 代码里根本没有读它的地方（`grep realtimequote php/` 为空）。
 *
 * 所以本探针的对照口径是：**喂同样的原始数，比公式与文案**。
 *
 * 原始数（盘口价/量、官方净值、仓位、对冲值、中间价、期货价）由
 * `_php_overnight_cnh_compare.py` 从真库读出来写成 JSON 传进来；
 * PHP 这边跑的是**从源码原样抠出来**的：
 *
 *   * `StockGetFundFeeRatio()`（`woody/res/php/_stock.php:151`）—— 申购费表；
 *   * `_fundCnhInput()` / `_futureCnhInput()` / `_convertCnhInput()`
 *     （`overnightcnhcn.php:10-61`）；
 *   * `_buildHedgeString()`（`:5`）；
 *   * `_echoOverNightCnhItem()` 里"建议数量"那几行（`:87-131`）；
 *   * `GetNumberDisplay()`（`php/stock.php:111`）—— 对冲值那一格。
 *
 * 两套"建议对冲操作"文案都在：对冲值口径（`:93-103`）与持仓折股口径
 * （`:104-125`，含 `rtrim($strMemo, '、')` 与那句无条件的"，共N股。"）。
 *
 * 数据通路（谁从哪张表取这些原始数）不在本探针范围内，那一半由单测与
 * 真库冒烟覆盖 —— 这一点在 `README_overnight_cnh_migration.md` 里写清楚了。
 *
 * 用法：
 *
 *     php -d display_errors=stderr _php_overnight_cnh_check.php cases.json
 *
 * stdout 只有一个 JSON；调试与报错一律走 stderr。
 *
 * ## 注意
 *
 * * 本文件不能出现 PHP 结束标签（注释里也不行）。
 * * `GetMyStockLink()` 被替换成"只返回符号"—— 对照比的是文本，不是链接。
 * * 本脚本**只读**，不连库、不写库。
 */

error_reporting(E_ALL);
ini_set('display_errors', 'stderr');

// ---------------------------------------------------------------------------
// 原样搬过来的那几段
// ---------------------------------------------------------------------------
// -- woody/res/php/_stock.php:151-180 原样 ----------------------------------
function StockGetFundFeeRatio($strSymbol)
{
	switch ($strSymbol)
	{
	case 'SZ160416':
	case 'SZ161116':
		return 0.0;

	case 'SZ161226':
		return 0.01;

	case 'SH501018':
	case 'SZ161125':
	case 'SZ161126':
	case 'SZ161127':
	case 'SZ161128':
	case 'SZ161129':
	case 'SZ161130':
	case 'SZ162719':
	case 'SZ164824':
	case 'SZ164906':
		return 0.012;

	case 'SZ161815':
		return 0.016;
	}
	return 0.015;
}

// -- php/ui/htmlelement.php:28-69 原样（色彩只在 <font> 里，对照比文本与颜色名）--
function GetDoubleQuotes($str)            { return '"' . $str . '"'; }
function GetHtmlElement($strContent, $strTag = 'p', $arAttribute = false)
{
	$strStart = $strTag;
	if ($arAttribute)
	{
		foreach ($arAttribute as $strAttribute => $strValue)
		{
			$strStart .= ' ' . $strAttribute;
			if ($strValue !== false)		$strStart .= '=' . $strValue;
		}
	}
	return "<$strStart>$strContent</$strTag>";
}
function GetFontElement($strContent, $strColor = 'red', $strStyle = false)
{
	$ar = array('color' => $strColor);
	if ($strStyle)	$ar['style'] = GetDoubleQuotes($strStyle);
	return GetHtmlElement($strContent, 'font', $ar);
}

// -- php/stock.php:91-114 原样 -------------------------------------------------
function StockGetPriceDisplay($fDisp, $fPrev, $iPrecision, $strGreater = 'red', $strLess = 'green')
{
	$iDiff = 0.5;
	$iCur = $iPrecision;
	while ($iCur > 0)
	{
		$iDiff /= 10.0;
		$iCur --;
	}

    if ($fDisp > $fPrev + $iDiff)         $strColor = $strGreater;
    else if ($fDisp < $fPrev - $iDiff)    $strColor = $strLess;
    else                                  $strColor = 'grey';
	$strDisp = number_format($fDisp, $iPrecision, '.', '');
	if ($iPrecision > 0)	$strDisp = rtrim($strDisp, '0');
	$strDisp = rtrim($strDisp, '.');
    if ($strColor != 'black')	return GetFontElement($strDisp, $strColor);
	return $strDisp;
}

function GetNumberDisplay($fVal, $iPrecision = 2, $strGreater = 'black', $strLess = 'red')
{
    return StockGetPriceDisplay($fVal, 0.0, $iPrecision, $strGreater, $strLess);
}

// -- php/stocklink.php:126 原样（链接只保留文本，见文件头）--------------------
function GetMyStockLink($strSymbol = false, $strDisplay = false)
{
	return $strSymbol;
}

// -- woody/res/overnightcnhcn.php:5-61 原样 -----------------------------------
function _buildHedgeString($fQuantity, $strSymbol)
{
	return strval($fQuantity).'股'.GetMyStockLink($strSymbol);
}

function _futureCnhInput($ref, $future_ref, $strInput, $fMultiple)
{
	$strSymbol = $ref['symbol'];
	$fCnh = $future_ref * $ref['cny'];
	if ($fRealtime = $ref['realtime'])
	{
		$fPremium = $ref['price'] / $fRealtime;
		$fCnh *= 1.0 + ($fPremium - 1.0) * $ref['position'];
	}
	$fCnh *= $fMultiple * floatval(substr($strInput, 0, strlen($strInput) - 3));
	return $fCnh;
}

function _fundCnhInput($ref, $stock_ref, $strInput)
{
	$strSymbol = $ref['symbol'];
	$fFeeRatio = StockGetFundFeeRatio($strSymbol);

	$fPremium = floatval($stock_ref['ask_price']) / $ref['official'];

	$fCnh = floatval(substr($strInput, 0, strlen($strInput) - 1));
	$fCnh /= 1 + $fFeeRatio;
	$fCnh *= $fPremium;
	$fCnh *= $ref['position'];
	return $fCnh;
}

function _convertCnhInput($ref, $stock_ref, $strInput)
{
	if (str_ends_with($strInput, 'F'))
	{
		$fCnh = _fundCnhInput($ref, $stock_ref, $strInput);
	}
	else if (str_ends_with($strInput, 'MCL'))
	{
		$fCnh = _futureCnhInput($ref, $ref['future_cl'], $strInput, 100.0);
	}
	else if (str_ends_with($strInput, 'MGC'))
	{
		$fCnh = _futureCnhInput($ref, $ref['future_gc'], $strInput, 10.0);
	}
	else
	{
		$fCnh = floatval($strInput);
	}
	return abs($fCnh);
}

// -- woody/res/overnightcnhcn.php:63-134 的算法骨架 ----------------------------
//: 原函数直接吃引用对象；这里改成吃上面那份摘要数组，逻辑逐行照抄。
function _echoOverNightCnhItem($case)
{
	$ref = $case;
	$bSell = $case['is_sell'];
	$strInput = $case['input'];
	$fCnh = _convertCnhInput($ref, $ref, $strInput);

	$ar = array();
	$ar['symbol'] = $ref['symbol'];
	$strQuantity = $bSell ? $ref['bid_quantity'] : $ref['ask_quantity'];
	if (!$strQuantity)
	{
		$ar['cells'] = 1;
		return $ar;
	}
	$strPrice = $bSell ? $ref['bid_price'] : $ref['ask_price'];
	$fPrice = floatval($strPrice);

	$fPos = $ref['position'];
	$fHintQuantity = ($fCnh / $fPos) / $fPrice;
	$strHedge = '';
	$strMemo = '';

	if ($ref['has_stock_ref'])
	{
		if ($fHedge = $ref['hedge'])
		{
			$fHedgeQuantity = round($fHintQuantity / $fHedge);
			$fHintQuantity = $fHedgeQuantity * $fHedge;
			$strHedge = GetNumberDisplay($fHedge, 0);
			$strMemo = _buildHedgeString($fHedgeQuantity, $ref['hedge_symbol']);
		}
	}
	else if ($ref['is_holdings'])
	{
		$fCny = $ref['net_value'] * $fHintQuantity * $fPos;
		$fUsd = $fCny / $ref['cny_at_date'];
		$fTotalQuantity = 0.0;
		foreach ($ref['holdings'] as $item)
		{
			if ($item['close'])
			{
				$fHoldingQuantity = round($fUsd * floatval($item['ratio']) / 100.0 / floatval($item['close']), 1);
				$fTotalQuantity += $fHoldingQuantity;
				$strMemo .= _buildHedgeString($fHoldingQuantity, $item['symbol']).'、';
			}
		}
		$strMemo = rtrim($strMemo, '、');
		if (!in_array($ref['symbol'], array('SZ160216', 'SZ161815', 'SZ163208', 'SZ164701')))
		{
			$strMemo .= '，共'.strval(round($fTotalQuantity)).'股。';
		}
	}
	$fHintQuantity = round($fHintQuantity / 100.0) * 100.0;
	$strHintQuantity = strval($fHintQuantity);
	if ($fHintQuantity > floatval($strQuantity))	$strHintQuantity = GetFontElement($strHintQuantity);

	$ar['fcnh'] = $fCnh;
	$ar['price'] = $fPrice;
	$ar['quantity'] = $strQuantity;
	$ar['hint'] = $strHintQuantity;
	$ar['hint_over'] = ($fHintQuantity > floatval($strQuantity));
	$ar['hedge_display'] = $strHedge;
	$ar['memo'] = ($strMemo != '') ? (($bSell ? '买入' : '卖出').$strMemo) : '';
	$ar['cells'] = 5 + (($ar['memo'] != '') ? 1 : 0);
	return $ar;
}

// ---------------------------------------------------------------------------
// 跑
// ---------------------------------------------------------------------------
if ($argc < 2)
{
	fwrite(STDERR, 'usage: php _php_overnight_cnh_check.php <cases.json>' . PHP_EOL);
	exit(2);
}
$strJson = @file_get_contents($argv[1]);
if ($strJson === false)
{
	fwrite(STDERR, '读不到 ' . $argv[1] . PHP_EOL);
	exit(2);
}
$arCases = json_decode($strJson, true);
if (!isset($arCases['cases']))
{
	fwrite(STDERR, 'JSON 里没有 cases' . PHP_EOL);
	exit(2);
}

$arOut = array('fees' => array(), 'rows' => array());

//: 费率表整张过一遍：符号表由调用方给（本探针不认识 `GetOverNightSymbolArray()`，
//: 那个函数在 `php/stock/stocksymbol.php` 里、要连库才能建对象）。这样即使某只
//: 标的的全部用例都被排除了，费率表这一项仍然被覆盖到。
if (isset($arCases['symbols']))
{
	foreach ($arCases['symbols'] as $strSymbol)	$arOut['fees'][$strSymbol] = StockGetFundFeeRatio($strSymbol);
}

foreach ($arCases['cases'] as $case)
{
	$strSymbol = $case['symbol'];
	$strInput = $case['input'];
	$arOut['fees'][$strSymbol] = StockGetFundFeeRatio($strSymbol);
	$arOut['rows'][$strSymbol.'@'.$strInput] = _echoOverNightCnhItem($case);
}

//: 再补几个**不在符号表里**的：费率表的特例（`SZ160416` 免申购费）、
//: 只出现在其它页面的符号、以及一个完全不存在的前缀。
foreach (array('SZ160416', 'SZ161128', 'SZ161116', 'NOSUCHSYMBOL') as $strSymbol)
{
	$arOut['fees'][$strSymbol] = StockGetFundFeeRatio($strSymbol);
}

echo json_encode($arOut, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
