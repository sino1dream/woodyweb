<?php
// 拖拉机自动化说明页的 PHP 真身探针。
//
// `woody/res/php/_autotractor.php` 整页只有 `EchoAll()` 里一串拼字符串的 echo，
// 没有数据库、没有行情。所以探针的做法是：把 `EchoAll()` 这一整段**从源码里
// 原样抠出来 eval**，其余依赖（元素函数、外链、文件链接、已知问题外壳）用真身
// 或最小替身补齐，然后把 HTML 抓进缓冲区吐成 JSON。
//
// 两条 `require_once` 不能照原样走：
//   * `_stock.php` —— 拉数据库与会话，本页一行都不需要；
//   * `imagedisp.php` —— `ImgAutoQuote()` 会用 GD 现场生成缩放图并写盘，
//     本版不渲染图片（见 autotractor_service.py 的"刻意偏离"）。
//
// stdout 只放 JSON，错误走 stderr（`php -d display_errors=stderr`）。

error_reporting(E_ALL);
ini_set('display_errors', 'stderr');

require_once('../../../php/ui/htmlelement.php');	// GetHtmlElement / GetListElement / GetFontElement …
require_once('../../../php/httplink.php');		// GetFileLink / GetExternalLink
require_once('../../../php/ui/plaintext.php');	// CheckMetaDescription（只做长度告警，不截断）

// -- _autotractor.php:5 原样 -------------------------------------------------
define('YINHE_AU3_VER', '99');

// -- php/stocklink.php:292 原样（GetTitle() 与"仅登录查询"那条文案都用它）----
define('AUTO_TRACTOR_DISPLAY', '拖拉机自动化');

// -- imagedisp.php:75 原样（本页只用到这个常量的位置参数，图片本身不渲染）---
define('PATH_BLOG_PHOTO', '/woody/blog/photo/');

// -- 本版不渲染图片：两张截图函数的替身，返回空串 ------------------------------
function ImgAutoQuote($strPathName, $strText = '', $bChinese = true)	{ return ''; }
function GetWoodyImgQuote($strFileName, $strText, $strTextUs = '', $bChinese = true)	{ return ''; }

// -- echoelement.php:12 的等价物（不 require 整份，它会连带别的 UI 依赖）------
function EchoHtmlElement($strContent, $strTag = 'p', $arAttribute = false)
{
	echo GetHtmlElement($strContent, $strTag, $arAttribute);
}

// -- externalurl.php:101 + externallink.php:177 原样（不 require：那两份拉
//    stocklink.php，而后者要 PEAR 的 HTML/Table，本机没装）--------------------
function GetXueqiuUrl()
{
	return 'https://xueqiu.com/';
}

function GetXueqiuIdLink($strId, $strDisplay)
{
	return GetExternalLink(GetXueqiuUrl().'u/'.$strId, $strDisplay);
}

// -- _stock.php:186 原样 -----------------------------------------------------
function GetKnownBugs($arBug)
{
	return GetHeadElement('已知问题').GetListElement($arBug);
}

// -- 把 `function EchoAll()` 整段抠出来 --------------------------------------
// 要跳过引号里的花括号（文案里没有，但 `\Software\...` 有反斜杠，别用正则偷懒）。
function ExtractFunction($strSource, $strFunc)
{
	$iStart = strpos($strSource, 'function '.$strFunc.'(');
	if ($iStart === false)	throw new Exception('function not found: '.$strFunc);

	$iDepth = 0;
	$bQuote = false;
	$bEscaped = false;
	$iLen = strlen($strSource);
	for ($i = $iStart; $i < $iLen; $i ++)
	{
		$c = $strSource[$i];
		if ($bQuote)
		{
			if ($bEscaped)		$bEscaped = false;
			else if ($c == '\\')	$bEscaped = true;
			else if ($c == "'")	$bQuote = false;
			continue;
		}
		if ($c == "'")	{ $bQuote = true; continue; }
		if ($c == '{')	$iDepth ++;
		else if ($c == '}')
		{
			$iDepth --;
			if ($iDepth == 0)	return substr($strSource, $iStart, $i - $iStart + 1);
		}
	}
	throw new Exception('unbalanced function body: '.$strFunc);
}

// -- 页面上下文里那两个东西的替身 --------------------------------------------
// EchoAll() 里 `global $acct;` 只在末尾调 `$acct->EchoLinks('chaos')`（页脚），
// 本版页脚由 base.html 统一给，所以这里只要有个能接住调用的对象。
class FakeAccount
{
	public function EchoLinks($strVer = false, $callback = false)	{}
}

$acct = new FakeAccount();

$strSource = file_get_contents('../../../woody/res/php/_autotractor.php');
eval(ExtractFunction($strSource, 'EchoAll'));

ob_start();
EchoAll();
$strHtml = ob_get_clean();

// GetMetaDescription() / GetTitle() 也照原样求值（两份都在同一文件末尾）
eval(ExtractFunction($strSource, 'GetMetaDescription'));
eval(ExtractFunction($strSource, 'GetTitle'));

echo json_encode(array(
	'html' => $strHtml,
	'meta' => GetMetaDescription(),
	'title' => GetTitle(),
), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
