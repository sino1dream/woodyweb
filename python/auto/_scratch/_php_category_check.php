<?php
// 分类页符号数组的 PHP 真身探针。
//
// 目的：`woody/res/php/_mystockgroup.php:154` 里
// `_echoStockGroupArray(GetCategoryArray($strPage), ...)` 是这些页面的唯一
// 内容来源，所以"数组对不对"就是这一批页面迁移的全部逻辑。这里直接 require
// 真文件把 16 个分类的数组吐成 JSON，供 `_php_category_compare.py` 与
// Python 侧注册表逐例比对。
//
// 注意：`lof` / `abcompare` / `ahcompare` / `adrhcompare` 要查库（前者遍历
// GetAllSymbolArray() 判 IsLofA()，后三者读配对表），不在本次范围内 —— 它们
// 不是"基金分类页"。这里只跑纯静态数组那 16 个。
//
// stdout 只放 JSON，错误走 stderr（`php -d display_errors=stderr`）。

require_once('../../../php/stock/stocksymbol.php');
require_once('../../../woody/res/php/_stocklink.php');

// 前 17 个是 2026-10-30 迁的那批；后 2 个（lof / overnight）与它们同一套壳子
// （`lofcn.php` / `overnightcn.php`），当初漏在清单外，2026-11-01 补上。
$arCategory = array('biotech', 'chinafuture', 'chinaindex', 'chinainternet', 'commodity',
                    'hangseng', 'hshares', 'hstech', 'mscius50', 'oilfund',
                    'qdii', 'qdiieu', 'qdiihk', 'qdiijp', 'qdiimix',
                    'qqqfund', 'spyfund',
                    'lof', 'overnight');

$result = array();
foreach ($arCategory as $strCategory)
{
    $ar = GetCategoryArray($strCategory);
    $result[$strCategory] = array(
        'count' => count($ar),
        // array_values 一下：PHP 数组键是 0..n，json 里要保持数组形状
        'symbols' => array_values($ar),
    );
}

echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
