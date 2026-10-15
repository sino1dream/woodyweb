<?php
// CK6-1：校准记录列头谓词对照 —— PHP 侧 IsFundA()。
//
//   php -n -d display_errors=stderr auto/_scratch/_php_isfunda_check.php
//
// 直接 include 整个 php/stock/stocksymbol.php（该文件自带全部常量与
// _isDigit* 助手，IsFundA 那条调用链不需要外部函数），逐个符号
// new StockSymbol($str)->IsFundA()，stdout 只出 JSON。
//
// 对照对象：python/auto/core/stocksymbol.py 的 StockSymbol.is_fund_a()
// （CK6-1 用它换掉 history_service 里写错的 get_fund_profile 谓词）。

ini_set('display_errors', 'stderr');
error_reporting(E_ALL & ~E_WARNING & ~E_DEPRECATED);

require __DIR__ . '/../../../php/stock/stocksymbol.php';

// 符号清单：演示库里 37 个有校准记录的符号 + 各类边界（港股/日欧/期货/
// 外汇/中间价/普通 A 股/指数/B 股），确保谓词在每个分支上都对得上。
$arSymbol = array(
    // 中间层标的与杠杆 ETF（PHP 判 False —— 当年就是这四个被误判成基金）
    '^GSPC', '^NDX', 'QQQ', 'SPY', 'TQQQ', 'SH', 'AGG', 'IBBB', 'KWEB', 'XOP', 'XBI',
    // A 股 QDII 基金（True）
    'SZ161125', 'SZ161130', 'SH513100', 'SH513050', 'SZ159632', 'SZ159941',
    'SH501300', 'SZ160140', 'SZ160416', 'SZ161126', 'SZ161128', 'SZ162415',
    'SZ162719', 'SZ164906', 'SH513290', 'SH513400', 'SZ162411',
    // 其它 A 股基金 / 非基金
    'SZ000001', 'SH600000', 'SH900901', 'SZ200992', 'BJ430047',
    'SZ399001', 'SH000300', 'SH000688',
    // 港股 / 日本 / 欧洲 QDII
    'SH501025', 'SZ161124', 'SH513800', 'SH513080',
    // 外汇与期货
    'USDCNY', 'USDCNH', 'USCNY', 'HKCNY', 'JPCNY', 'EUCNY', 'hf_ES', 'hf_NQ',
    'znb_DAX', 'fx_susdcny',
);

$out = array();
foreach ($arSymbol as $strSymbol)
{
    $sym = new StockSymbol($strSymbol);
    $out[] = array(
        'symbol'  => $strSymbol,
        // IsFundA() 命中时返回 strDigitA（字符串），否则 false
        'IsFundA' => $sym->IsFundA(),
        // 顺带把中间两步也打出来，错了好定位是哪一层
        'IsLofA'  => $sym->IsLofA(),
        'IsEtfA'  => $sym->IsEtfA(),
    );
}

echo json_encode($out), "\n";
