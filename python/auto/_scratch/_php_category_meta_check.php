<?php
// 分类页标题与说明的 PHP 真身探针。
//
// `woody/res/php/_mystockgroup.php` 里的 `_getTitleStr($strPage)` 与
// `_getMetaDescriptionStr($strPage)` 各是一个 `$ar = array(...)` 字面量，
// 值大量由常量拼接而成（`BIOTECH_GROUP_DISPLAY.'基金的'.STOCK_DISP_NETVALUE...`）。
// 手抄一遍必然出错，所以这里直接从源文件里把字面量抠出来 `eval`，让 PHP 自己
// 拼好再吐 JSON。
//
// 两个数组都在函数体里、都只依赖常量与 `$strTool`，所以可以脱离
// `$acct = new GroupIdAccount();` 那套页面上下文单独求值 —— 否则 require
// 整个文件会连带起会话与数据库。
//
// stdout 只放 JSON，错误走 stderr（`php -d display_errors=stderr`）。

require_once('../../../php/ui/stockdisp.php');

define('LINK_SOURCE', '../../../php/stocklink.php');
define('META_SOURCE', '../../../woody/res/php/_mystockgroup.php');

// 不能 `require_once('stocklink.php')`：它第 2 行拉 `ui/stocktable.php`，
// 而后者 require PEAR 的 `HTML/Table.php`，这台机器的 PEAR 没装 HTML 包
// （`D:\xampp\php\pear\HTML\` 不存在）→ 直接 fatal。真实站点大概另有 include。
//
// 我只需要里面的常量，所以按行 eval 它的 `define()`。61 条全部单行；个别
// 依赖未定义常量的（PHP 8 下会抛 Error）跳过即可，用不到它们。
$strLink = file_get_contents(LINK_SOURCE);
preg_match_all('/^define\(.*?\);\s*$/m', $strLink, $arMatch);
foreach ($arMatch[0] as $strDefine)
{
    try
    {
        eval($strDefine);
    }
    catch (Throwable $e)
    {
        // 拼不出来的常量（依赖别处定义）与本探针无关
    }
}

// 从源码里抠出某个函数体内 `$ar = array(` 这段字面量（含配对的右括号）。
// 扫描要跳过引号内的括号 —— 说明文字里有 `信诚商品(SZ165513)` 这种。
function ExtractArrayText($strSource, $strFunc)
{
    $iFunc = strpos($strSource, 'function '.$strFunc);
    if ($iFunc === false)		throw new Exception('function not found: '.$strFunc);

    $iArray = strpos($strSource, 'array(', $iFunc);
    if ($iArray === false)	throw new Exception('array literal not found: '.$strFunc);

    $iLen = strlen($strSource);
    $iDepth = 1;
    $bQuote = false;
    for ($i = $iArray + 6; $i < $iLen; $i ++)
    {
        $c = $strSource[$i];
        if ($bQuote)
        {
            if ($c == '\\')			{ $i ++; continue; }
            if ($c == "'")			$bQuote = false;
            continue;
        }
        if ($c == "'")				{ $bQuote = true; continue; }
        if ($c == '(')				$iDepth ++;
        else if ($c == ')')
        {
            $iDepth --;
            if ($iDepth == 0)
            {
                return substr($strSource, $iArray, $i - $iArray + 1);
            }
        }
    }
    throw new Exception('unbalanced array literal: '.$strFunc);
}

$strSource = file_get_contents(META_SOURCE);
$strMeta = ExtractArrayText($strSource, '_getMetaDescriptionStr');
$strTitle = ExtractArrayText($strSource, '_getTitleStr');

$arMeta = eval('return '.$strMeta.';');
$strTool = '基金净值计算工具';		// _getTitleStr() 里的 $strTool
$arTitle = eval('return '.$strTitle.';');

$arCategory = array('biotech', 'chinafuture', 'chinaindex', 'chinainternet', 'commodity',
                    'hangseng', 'hshares', 'hstech', 'mscius50', 'oilfund',
                    'qdii', 'qdiieu', 'qdiihk', 'qdiijp', 'qdiimix',
                    'qqqfund', 'spyfund');

$result = array();
foreach ($arCategory as $strCategory)
{
    $result[$strCategory] = array(
        // 缺键就是 PHP 会 fatal 的分支（$ar[$strPage] 越界），如实报 null
        'title' => array_key_exists($strCategory, $arTitle) ? $arTitle[$strCategory] : null,
        'description' => array_key_exists($strCategory, $arMeta) ? $arMeta[$strCategory] : null,
    );
}

echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
