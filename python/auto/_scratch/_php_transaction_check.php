<?php
// ---------------------------------------------------------------------------
// 交易明细段落 / 资金汇总段落的 PHP 参照输出
//
// 与 _php_portfolio_check.php 同一套做法：**从 PHP 源码里抠出函数体**，
// 放进本文件的命名空间里跑，于是被测的是 PHP 原文而不是手抄的副本。
// 命名空间里同名的桩会遮住全局函数（PHP 先查当前命名空间），
// 数据库与账号那两档就这样被截住。
//
// ## 覆盖
//
//   ① _echoTransactionTableItem   交易明细行的七格（transactionparagraph.php:4）
//   ② _echoMoneyItem              资金汇总行的格子（_stock.php:13）
//   ③ _EchoMoneyGroupData         一个分组的 1~3 行（_stock.php:67）
//   ④ StockGroup / MyStockGroup   分桶累加与折算（stockgroup.php）
//   ⑤ MultiCurrency               Convert 的除数守卫（multi_currency.php）
//   ⑥ 链接助手族                  GetOnClickLink / GetDeleteLink / GetEditLink /
//                                 StockGetAllTransactionLink / GetMyStockGroupLink
//   ⑦ GetNumberDisplay            数字显示（stock.php:111）
//
// ## 刻意不进对照的
//
//   * **价格格的内容**：``GetPriceDisplay()`` 是 MyStockReference 的方法，
//     不是本段落的逻辑。桩固定成 ``sprintf('%.*f')``，于是对照的是
//     **传进去的参数**（disp / prev / precision）—— 那才是段落决定的事。
//     GetPriceDisplay 本身由 refobj 的单测覆盖。
//   * **翻页控件**：``GetMenuLink()`` 依赖"当前页 URI"，Python 侧走
//     ``menu_link_paging`` 返回数据、由模板拼 URL，形态不同；
//     该函数已在股票列表页迁过并有单测。
//   * **``_echoMoneyItem`` 的"补空格子"分支**：PHP 8 下
//     ``GetNumberDisplay()`` 对任何输入都不返回空串（``0.0`` 产出
//     ``<font color=grey>0</font>``），那两个分支是死代码；而 Python 的
//     ``format_number_display(None)`` 回空串，退化路径会走进去。
//     所以只比生产路径（四个值都是数字），退化路径由
//     ``test_money.MoneyRowEmptyBranchTest`` 钉住。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）。
// ---------------------------------------------------------------------------

namespace TransactionCheck {

// 按名字从源码里抠出整个函数（花括号配对）
function FExtractFunction($source, $name)
{
    if (!preg_match('/function\s+' . preg_quote($name, '/') . '\s*\(/', $source, $m, PREG_OFFSET_CAPTURE))
    {
        return null;
    }
    $start = $m[0][1];
    $cursor = strpos($source, '{', $start);
    if ($cursor === false)  return null;

    $depth = 0;
    $length = strlen($source);
    for ($i = $cursor; $i < $length; $i++)
    {
        $char = $source[$i];
        if ($char == '{')       $depth++;
        else if ($char == '}')
        {
            $depth--;
            if ($depth == 0)    return substr($source, $start, $i - $start + 1);
        }
    }
    return null;
}

// 抠出整个类（从 class 关键字到配对的右花括号）
function FExtractClass($source, $name)
{
    if (!preg_match('/class\s+' . preg_quote($name, '/') . '\b/', $source, $m, PREG_OFFSET_CAPTURE))
    {
        return null;
    }
    $cursor = strpos($source, '{', $m[0][1]);
    if ($cursor === false)  return null;

    $depth = 0;
    $length = strlen($source);
    for ($i = $cursor; $i < $length; $i++)
    {
        $char = $source[$i];
        if ($char == '{')       $depth++;
        else if ($char == '}')
        {
            $depth--;
            if ($depth == 0)    return substr($source, $m[0][1], $i - $m[0][1] + 1);
        }
    }
    return null;
}

// ---- 常量（PHP 里散在各处的 define）------------------------------------------
const PATH_STOCK = '/woody/res/';
const DISP_ALL_CN = '全部';
const DISP_EDIT_CN = '修改';
const DISP_EDIT_US = 'Edit';
const STOCK_TRANSACTION_DISPLAY = '交易记录';
const STOCK_GROUP_DISPLAY = '股票分组';
const STOCK_DISP_REMARK = '备注';
const STOCK_DISP_ORDER = '申购';
const STOCK_DISP_CALIBRATION = '校准';
const STOCK_DISP_NETVALUE = '净值';
const ADMIN_EMAIL = 'woody@palmmicro.com';

// ---- 桩：URL 与"当前页" ------------------------------------------------------
// UrlGetServer() 打桩成空串，与 Python 侧"不带服务器名"的取舍对齐
// （见 ref/stocklink.py 模块 docstring 的取舍 1）。
function UrlGetServer()   { return ''; }
function UrlGetPhp($bChinese = true) { return $bChinese ? 'cn.php' : '.php'; }
// 让 GetPageLink() 的"当前页"分支永不触发 —— Python 侧没有这个概念
function UrlGetPage()        { return 'zzz'; }
function UrlGetQueryString() { return ''; }

// ---- 桩：假结果集 ------------------------------------------------------------
class FakeResult
{
    public $arRecords;
    public $iPos = 0;

    public function __construct($arRecords)
    {
        $this->arRecords = $arRecords;
    }
}

function mysqli_fetch_assoc($result)
{
    if ($result instanceof FakeResult && $result->iPos < count($result->arRecords))
    {
        return $result->arRecords[$result->iPos++];
    }
    return null;
}

function mysqli_free_result($result)
{
    return true;
}

// ---- 桩：数据库 --------------------------------------------------------------
class StockGroupItemSql
{
    public function __construct($strGroupId) {}

    public function GetAll()
    {
        return new FakeResult($GLOBALS['tx_items']);
    }

    public function GetId($strStockId)
    {
        $ar = $GLOBALS['tx_item_ids'];
        return isset($ar[$strStockId]) ? $ar[$strStockId] : false;
    }
}

function SqlGetStockSymbol($strStockId)
{
    $ar = $GLOBALS['tx_symbols'];
    return isset($ar[$strStockId]) ? $ar[$strStockId] : $strStockId;
}

// ---- 桩：净值表（校准链接要用）----------------------------------------------
class FakeNetValueSql
{
    public function GetClose($strStockId, $strDate)
    {
        return isset($GLOBALS['tx_nv_close']) ? $GLOBALS['tx_nv_close'] : null;
    }

    public function GetClosePrev($strStockId, $strDate)
    {
        return isset($GLOBALS['tx_nv_prev']) ? $GLOBALS['tx_nv_prev'] : null;
    }
}

function GetNetValueHistorySql()
{
    return new FakeNetValueSql();
}

function SqlGetEmailById($strMemberId)
{
    return isset($GLOBALS['tx_email']) ? $GLOBALS['tx_email'] : false;
}

// ---- 桩：引用对象 ------------------------------------------------------------
// GetPriceDisplay() 刻意固定成 sprintf（见文件头"刻意不进对照的"第 1 条）：
// 于是对照的是**传进去的参数**，而不是引用对象自己的着色逻辑。
class FakeRef
{
    public $strSymbol;
    public $strStockId;
    public $strDisplay;
    public $strPrice;
    public $bFundA;
    public $iPrecision;
    public $bA;
    public $bH;
    public $bFutureCn;

    public function __construct($strSymbol, $strStockId = '0', $strDisplay = false,
                                $strPrice = '0', $bFundA = false, $iPrecision = 2,
                                $bA = false, $bH = false, $bFutureCn = false)
    {
        $this->strSymbol = $strSymbol;
        $this->strStockId = $strStockId;
        $this->strDisplay = $strDisplay ? $strDisplay : $strSymbol;
        $this->strPrice = $strPrice;
        $this->bFundA = $bFundA;
        $this->iPrecision = $iPrecision;
        $this->bA = $bA;
        $this->bH = $bH;
        $this->bFutureCn = $bFutureCn;
    }

    public function GetSymbol()     { return $this->strSymbol; }
    public function GetStockId()    { return $this->strStockId; }
    public function GetDisplay()    { return $this->strDisplay; }
    public function GetPrice()      { return $this->strPrice; }
    public function IsFundA()       { return $this->bFundA; }
    public function GetPrecision()  { return $this->iPrecision; }
    public function IsSymbolA()     { return $this->bA; }
    public function IsSymbolH()     { return $this->bH; }
    public function IsSinaFutureCN(){ return $this->bFutureCn; }

    public function GetPriceDisplay($fDisp, $fPrev = false, $iPrecision = false)
    {
        $i = ($iPrecision === false) ? $this->iPrecision : $iPrecision;
        return sprintf('%.' . intval($i) . 'f', $fDisp);
    }
}

// MyStockGroup 的 _checkSymbol() 遇到没见过的符号时要现建引用对象
function StockGetReference($strSymbol)
{
    $ar = $GLOBALS['tx_ref_table'];
    if (isset($ar[$strSymbol]))
    {
        $r = $ar[$strSymbol];
        return new FakeRef($strSymbol, $r[0], $strSymbol, $r[1], false, 2,
                           $r[2], $r[3], false);
    }
    return false;
}

// ---- 桩：账号（🔴 membership 域）--------------------------------------------
class FakeAcct
{
    public function GetMemberId()
    {
        return isset($GLOBALS['tx_member_id']) ? $GLOBALS['tx_member_id'] : false;
    }

    public function GetGroupLink($strGroupId)
    {
        return isset($GLOBALS['tx_group_link']) ? $GLOBALS['tx_group_link'] : '分组' . $strGroupId;
    }
}

// ---- 桩：表格输出（把行收集起来）--------------------------------------------
function EchoTableColumn($ar)
{
    $GLOBALS['tx_rows'][] = array_values($ar);
}


// ===========================================================================
// 下面这段**仍在** TransactionCheck 命名空间里（php 的括号命名空间一旦打开
// 就到文件末尾 / 配对的右括号为止）。于是 FExtractFunction 与下面各个
// 被测函数都能直接按名字调用，与 _php_portfolio_check.php 同款。

// ---- 抽取被测的 PHP 原文 -----------------------------------------------------
$phpDir = realpath(__DIR__ . '/../../../php');
$woodyDir = realpath(__DIR__ . '/../../../woody');
if ($phpDir === false || $woodyDir === false)
{
    fwrite(STDERR, "找不到 php / woody 目录\n");
    exit(2);
}

$htmlelement = file_get_contents($phpDir . '/ui/htmlelement.php');
$httplink    = file_get_contents($phpDir . '/httplink.php');
$stocklink   = file_get_contents($phpDir . '/stocklink.php');
$stockphp    = file_get_contents($phpDir . '/stock.php');
$stocksymbol = file_get_contents($phpDir . '/stock/stocksymbol.php');
$stocktrans  = file_get_contents($phpDir . '/stocktrans.php');
$stockgroup  = file_get_contents($phpDir . '/stockgroup.php');
$multicur    = file_get_contents($phpDir . '/class/multi_currency.php');
$tablephp    = file_get_contents($phpDir . '/ui/table.php');
$stocktable  = file_get_contents($phpDir . '/ui/stocktable.php');
$transpara   = file_get_contents($phpDir . '/ui/transactionparagraph.php');
$resstock    = file_get_contents($woodyDir . '/res/php/_stock.php');

$blocks = array(
    // —— 元素与链接（真身，逐字执行）
    'GetDoubleQuotes'   => FExtractFunction($htmlelement, 'GetDoubleQuotes'),
    'GetHtmlElement'    => FExtractFunction($htmlelement, 'GetHtmlElement'),
    'GetLinkElement'    => FExtractFunction($htmlelement, 'GetLinkElement'),
    'GetFontElement'    => FExtractFunction($htmlelement, 'GetFontElement'),
    'GetInfoElement'    => FExtractFunction($htmlelement, 'GetInfoElement'),
    'GetOnClickLink'    => FExtractFunction($httplink, 'GetOnClickLink'),
    'GetDeleteLink'     => FExtractFunction($httplink, 'GetDeleteLink'),
    'GetEditLink'       => FExtractFunction($httplink, 'GetEditLink'),
    'GetPhpLink'        => FExtractFunction($httplink, 'GetPhpLink'),
    'GetInternalLink'   => FExtractFunction($httplink, 'GetInternalLink'),
    'GetPageLink'       => FExtractFunction($httplink, 'GetPageLink'),
    'GetStockPageLink'  => FExtractFunction($stocklink, 'GetStockPageLink'),
    'StockGetTransactionLink' => FExtractFunction($stocklink, 'StockGetTransactionLink'),
    'StockGetAllTransactionLink' => FExtractFunction($stocklink, 'StockGetAllTransactionLink'),
    'GetMyStockGroupLink' => FExtractFunction($stocklink, 'GetMyStockGroupLink'),
    // —— 数字显示
    'StockGetPriceDisplay' => FExtractFunction($stockphp, 'StockGetPriceDisplay'),
    'GetNumberDisplay'     => FExtractFunction($stockphp, 'GetNumberDisplay'),
    'in_arrayQdii'      => FExtractFunction($stocksymbol, 'in_arrayQdii'),
    'in_arrayQdiiMix'   => FExtractFunction($stocksymbol, 'in_arrayQdiiMix'),
    // —— 交易模型
    'GetSqlTransactionDate' => FExtractFunction($stocktrans, 'GetSqlTransactionDate'),
    'StockTransaction'      => FExtractClass($stocktrans, 'StockTransaction'),
    'MyStockTransaction'    => FExtractClass($stocktrans, 'MyStockTransaction'),
    // —— 分组模型
    'MultiCurrency' => FExtractClass($multicur, 'MultiCurrency'),
    'StockGroup'    => FExtractClass($stockgroup, 'StockGroup'),
    'MyStockGroup'  => FExtractClass($stockgroup, 'MyStockGroup'),
    // —— 列（MoneyGroupData 用 TableColumnHKD 的文本）
    'TableColumn'     => FExtractClass($tablephp, 'TableColumn'),
    'TableColumnHKD'  => FExtractClass($stocktable, 'TableColumnHKD'),
    // —— 被测段落
    '_echoTransactionTableItem' => FExtractFunction($transpara, '_echoTransactionTableItem'),
    '_echoMoneyItem'            => FExtractFunction($resstock, '_echoMoneyItem'),
    '_EchoMoneyGroupData'       => FExtractFunction($resstock, '_EchoMoneyGroupData'),
);

// 符号清单那一族：``QdiiGetSymbolArray()`` 由 ``GetXxxSymbolArray()`` /
// ``QdiiGetXxxMatchArray()`` 层层
// ``array_merge`` 出来，逐个追名字很容易漏。这里按 ``*SymbolArray`` 批量抽，
// 于是"这个符号算不算 QDII"是 PHP 与 Python 的**真实清单**在比。
if (!preg_match_all('/function\s+(\w*(?:Symbol|Match)Array)\s*\(/', $stocksymbol, $matches))
{
    fwrite(STDERR, "stocksymbol.php 里找不到 *SymbolArray 函数\n");
    exit(3);
}
$iSymbolArray = 0;
foreach (array_unique($matches[1]) as $name)
{
    $body = FExtractFunction($stocksymbol, $name);
    if ($body === null)
    {
        fwrite(STDERR, "抽取失败: $name\n");
        exit(3);
    }
    $blocks[$name] = $body;
    $iSymbolArray++;
}

foreach ($blocks as $name => $body)
{
    if ($body === null)
    {
        fwrite(STDERR, "抽取失败: $name\n");
        exit(3);
    }
}
fwrite(STDERR, "抽取符号清单函数: $iSymbolArray 个\n");

$tmp = tempnam(sys_get_temp_dir(), 'tx') . '.php';
file_put_contents($tmp, "<?php\nnamespace TransactionCheck;\n" . implode("\n", $blocks) . "\n");
require $tmp;


// 全局数组：收集 EchoTableColumn 的行、给桩喂数据
$GLOBALS['tx_rows'] = array();
$GLOBALS['tx_items'] = array();
$GLOBALS['tx_item_ids'] = array();
$GLOBALS['tx_symbols'] = array();
$GLOBALS['tx_ref_table'] = array();
$GLOBALS['tx_nv_close'] = null;
$GLOBALS['tx_nv_prev'] = null;
$GLOBALS['tx_email'] = false;
$GLOBALS['tx_member_id'] = false;
$GLOBALS['tx_group_link'] = null;

$acct = new FakeAcct();

function _collect($callback)
{
    $GLOBALS['tx_rows'] = array();
    $callback();
    return $GLOBALS['tx_rows'];
}

// ① 链接助手族 ---------------------------------------------------------------
$links = array();
$links['on_click'] = GetOnClickLink(
    '/woody/res/submittransaction.php?empty=12', '确认清空备注：申购 3？', '清空');
$links['on_click_quotes'] = GetOnClickLink('/x', 'say "hi"', 'd');
$links['delete_cn'] = GetDeleteLink(
    '/woody/res/submittransaction.php?delete=12', '2026-10-13 100交易记录');
$links['delete_us'] = GetDeleteLink('/x.php?delete=1', 'cn', 'us str', false);
$links['edit_cn'] = GetEditLink('/woody/res/editstocktransaction', '34');
$links['edit_us'] = GetEditLink('/woody/res/editstocktransaction', '34', false);
$links['all_trans'] = StockGetAllTransactionLink('g7');
$links['my_group'] = GetMyStockGroupLink();
$links['my_group_query'] = GetMyStockGroupLink('groupid=g7', '分组甲');
$links['trans_link_usd'] = StockGetTransactionLink('g1', false, '$');

// ② GetNumberDisplay ---------------------------------------------------------
$numberDisplay = array();
foreach (array(0.0, 0.004, -0.004, 0.006, -0.006, 1.234, -1.234, 1234.5,
               -0.5, 100.0, 1679328.22) as $value)
{
    $numberDisplay[] = array('value' => $value,
                             'text' => GetNumberDisplay($value));
}

// ③ 交易明细行 ---------------------------------------------------------------
$refCases = array(
    'us'     => new FakeRef('KWEB', '11', false, '33.80', false, 2),
    'fund_a' => new FakeRef('SZ161125', '12', false, '1.234', true, 3),
    'future' => new FakeRef('hf_ES', '13', 'ES', '4200.0', false, 2, false, false, true),
    'hk'     => new FakeRef('HK00700', '14', false, '380.0', false, 2, false, true),
    'qdii'   => new FakeRef('SZ161125', '12', false, '1.234', false, 2),
);

$recordCases = array(
    'plain'       => array('id' => '1', 'filled' => '2026-10-13 09:30:00',
                           'quantity' => '100', 'price' => '1.2340',
                           'fees' => '5.00', 'remark' => ''),
    'remark'      => array('id' => '2', 'filled' => '2026-10-14 10:15:00',
                           'quantity' => '-50', 'price' => '33.50',
                           'fees' => '0', 'remark' => '打新'),
    'order_same'  => array('id' => '3', 'filled' => '2026-10-15 11:00:00',
                           'quantity' => '200', 'price' => '1.2340',
                           'fees' => '12.5', 'remark' => '申购 3'),
    'order_diff'  => array('id' => '4', 'filled' => '2026-10-15 11:00:00',
                           'quantity' => '200', 'price' => '1.234',
                           'fees' => '12.5', 'remark' => '申购 3'),
    'no_space'    => array('id' => '5', 'filled' => '2026-10-16',
                           'quantity' => '10', 'price' => '2.5',
                           'fees' => '0', 'remark' => '申购'),
    'zero_fees'   => array('id' => '6', 'filled' => '2026-10-17 09:30:00',
                           'quantity' => '1', 'price' => '0',
                           'fees' => '0', 'remark' => ''),
);

$transRow = array();
foreach ($refCases as $refName => $ref)
{
    foreach ($recordCases as $recName => $record)
    {
        foreach (array(array(true, false), array(true, true), array(false, false)) as $flags)
        {
            list($bReadOnly, $bAdmin) = $flags;
            // 净值：与价格相同 / 不同 / 查不到
            foreach (array('same' => '1.234', 'diff' => '1.300', 'none' => null) as $nvName => $nv)
            {
                $GLOBALS['tx_nv_close'] = $nv;
                $GLOBALS['tx_nv_prev'] = $nv;
                $rows = _collect(function () use ($ref, $record, $bReadOnly, $bAdmin) {
                    _echoTransactionTableItem($ref, $record, $bReadOnly, $bAdmin);
                });
                $transRow[] = array(
                    'ref' => $refName,
                    'record' => $recName,
                    'read_only' => $bReadOnly,
                    'admin' => $bAdmin,
                    'netvalue' => $nvName,
                    'cells' => $rows[0],
                );
            }
        }
    }
}

// ④ 资金汇总行（_echoMoneyItem）---------------------------------------------
// 只比生产路径：四个值都是数字（见文件头"刻意不进对照的"第 3 条）
$moneyCases = array(
    'normal'     => array(1000.0, 100.0, 34370.0, 1520.0),
    'zero'       => array(0.0, 0.0, 0.0, 0.0),
    'negative'   => array(1000.0, -250.5, 500.0, -120.25),
    'big'        => array(1234567.89, 98765.43, 2345678.9, 123456.78),
);

$moneyRow = array();
foreach ($moneyCases as $caseName => $values)
{
    foreach (array('g', DISP_ALL_CN) as $groupText)
    {
        foreach (array('none' => false,
                       'admin' => ADMIN_EMAIL,
                       'mix' => 'mix@palmmicro.com',
                       'other' => 'someone@example.com') as $emailName => $email)
        {
            foreach (array(false, '1001') as $memberId)
            {
                $GLOBALS['tx_email'] = $email;
                $GLOBALS['tx_member_id'] = $memberId;
                $rows = _collect(function () use ($groupText, $values) {
                    _echoMoneyItem(
                        $groupText, $values[0], $values[1], $values[2], $values[3]);
                });
                $moneyRow[] = array(
                    'case' => $caseName,
                    'group' => $groupText,
                    'email' => $emailName,
                    'member' => $memberId ? 'yes' : 'no',
                    'cells' => $rows[0],
                );
            }
        }
    }
}
$GLOBALS['tx_email'] = false;
$GLOBALS['tx_member_id'] = false;

// ⑤ 一个分组的三行（_EchoMoneyGroupData）------------------------------------
// 分组构造走 MyStockGroup 的真构造函数：StockGroupItemSql::GetAll() 由桩喂
// groupitem 记录，于是 ``intval($record['record']) > 0`` 那道过滤也跑到。
$groupCases = array(
    'cny_only' => array(
        'refs'  => array('cny' => new FakeRef('SH600104', '11', false, '10.0', false, 2, true)),
        'items' => array(array('stock_id' => '11', 'record' => '2',
                               'quantity' => '100', 'cost' => '900.0')),
    ),
    'with_usd' => array(
        'refs'  => array('cny' => new FakeRef('SH600104', '11', false, '10.0', false, 2, true),
                         'usd' => new FakeRef('SPY', '12', false, '470.0')),
        'items' => array(array('stock_id' => '11', 'record' => '2',
                               'quantity' => '100', 'cost' => '900.0'),
                         array('stock_id' => '12', 'record' => '1',
                               'quantity' => '10', 'cost' => '4500.0')),
    ),
    'with_hkd' => array(
        'refs'  => array('cny' => new FakeRef('SH600104', '11', false, '10.0', false, 2, true),
                         'hkd' => new FakeRef('HK00700', '13', false, '40.0', false, 2, false, true)),
        'items' => array(array('stock_id' => '11', 'record' => '1',
                               'quantity' => '100', 'cost' => '900.0'),
                         array('stock_id' => '13', 'record' => '1',
                               'quantity' => '10', 'cost' => '380.0')),
    ),
    'record_zero' => array(
        // record 为 0 的记录不进表 —— 预留了但没买过
        'refs'  => array('cny' => new FakeRef('SH600104', '11', false, '10.0', false, 2, true),
                         'usd' => new FakeRef('SPY', '12', false, '470.0')),
        'items' => array(array('stock_id' => '11', 'record' => '1',
                               'quantity' => '100', 'cost' => '900.0'),
                         array('stock_id' => '12', 'record' => '0',
                               'quantity' => '10', 'cost' => '4500.0')),
    ),
    'all_three' => array(
        'refs'  => array('cny' => new FakeRef('SH600104', '11', false, '10.0', false, 2, true),
                         'usd' => new FakeRef('SPY', '12', false, '470.0'),
                         'hkd' => new FakeRef('HK00700', '13', false, '40.0', false, 2, false, true)),
        'items' => array(array('stock_id' => '11', 'record' => '1',
                               'quantity' => '100', 'cost' => '900.0'),
                         array('stock_id' => '12', 'record' => '1',
                               'quantity' => '10', 'cost' => '4500.0'),
                         array('stock_id' => '13', 'record' => '3',
                               'quantity' => '50', 'cost' => '1900.0')),
    ),
    'new_symbol' => array(
        // groupitem 里出现的符号不在 refs 里 —— _checkSymbol() 要现建引用对象
        'refs'  => array('cny' => new FakeRef('SH600104', '11', false, '10.0', false, 2, true)),
        'items' => array(array('stock_id' => '11', 'record' => '1',
                               'quantity' => '100', 'cost' => '900.0'),
                         array('stock_id' => '99', 'record' => '1',
                               'quantity' => '5', 'cost' => '3000.0')),
    ),
);

$GLOBALS['tx_symbols'] = array('11' => 'SH600104', '12' => 'SPY',
                               '13' => 'HK00700', '99' => 'KWEB');
$GLOBALS['tx_ref_table'] = array('KWEB' => array('99', '33.8', false, false));
$GLOBALS['tx_item_ids'] = array('11' => '101', '12' => '102',
                                '13' => '103', '99' => '199');

$moneyGroup = array();
foreach ($groupCases as $caseName => $spec)
{
    foreach (array('g1', false) as $groupId)
    {
        foreach (array('7.1', false) as $usdcny)
        {
            foreach (array('0.9', false) as $hkdcny)
            {
                $GLOBALS['tx_items'] = $spec['items'];
                $GLOBALS['tx_group_link'] = '分组甲';
                $group = new MyStockGroup($groupId, array_values($spec['refs']));
                $rows = _collect(function () use ($acct, $group, $usdcny, $hkdcny) {
                    _EchoMoneyGroupData($acct, $group, $usdcny, $hkdcny);
                });
                $moneyGroup[] = array(
                    'case' => $caseName,
                    'group_id' => $groupId ? $groupId : 'all',
                    'usdcny' => $usdcny ? $usdcny : 'false',
                    'hkdcny' => $hkdcny ? $hkdcny : 'false',
                    'rows' => $rows,
                );
            }
        }
    }
}

// ⑥ 分组模型：分桶 + 折算 ----------------------------------------------------
$groupModel = array();
$GLOBALS['tx_items'] = array();
foreach (array(
    'a_share'   => array(new FakeRef('SH600104', '11', false, '10.0', false, 2, true), 'A'),
    'hk'        => array(new FakeRef('HK00700', '13', false, '40.0', false, 2, false, true), 'H'),
    'us'        => array(new FakeRef('SPY', '12', false, '470.0'), 'US'),
    'future_cn' => array(new FakeRef('hf_ES', '14', 'ES', '4200.0', false, 2, false, false, true), 'CN_future'),
    'fx_cnh'    => array(new FakeRef('fx_susdcnh', '15', false, '7.1'), 'CN_fx'),
) as $caseName => $spec)
{
    $group = new StockGroup();
    $trans = new MyStockTransaction($spec[0], 'g1');
    $trans->SetValue(1, 10, 95.0);
    $group->OnStockTransaction($trans);
    $group->ConvertCurrency('7.1', '0.9');
    $groupModel[$caseName] = array(
        'amount' => array('cny' => $group->multi_amount->fCNY,
                          'hkd' => $group->multi_amount->fHKD,
                          'usd' => $group->multi_amount->fUSD,
                          'convert_cny' => $group->multi_amount->fConvertCNY,
                          'convert_usd' => $group->multi_amount->fConvertUSD,
                          'convert_hkd' => $group->multi_amount->fConvertHKD),
        'profit' => array('cny' => $group->multi_profit->fCNY,
                          'hkd' => $group->multi_profit->fHKD,
                          'usd' => $group->multi_profit->fUSD,
                          'convert_cny' => $group->multi_profit->fConvertCNY,
                          'convert_usd' => $group->multi_profit->fConvertUSD,
                          'convert_hkd' => $group->multi_profit->fConvertHKD),
    );
}

// ⑦ MultiCurrency.Convert 的除数守卫 ----------------------------------------
$convert = array();
foreach (array(array(100.0, 10.0, 20.0, '7.1', '0.9'),
               array(100.0, 10.0, 20.0, false, false),
               array(100.0, 0.0, 0.0, '7.1', '0.9'),
               array(0.0, 0.0, 0.0, '7.1', '0.9'),
               array(-50.0, 10.0, -5.0, '7.1', '0.9'),
               array(100.0, 10.0, 20.0, '0', '0.9')) as $index => $spec)
{
    $multi = new MultiCurrency();
    $multi->fCNY = $spec[0];
    $multi->fUSD = $spec[1];
    $multi->fHKD = $spec[2];
    $multi->Convert($spec[3], $spec[4]);
    $convert[] = array(
        'cny' => $spec[0], 'usd' => $spec[1], 'hkd' => $spec[2],
        'usdcny' => $spec[3] === false ? 'false' : $spec[3],
        'hkdcny' => $spec[4] === false ? 'false' : $spec[4],
        'convert_cny' => $multi->fConvertCNY,
        'convert_usd' => $multi->fConvertUSD,
        'convert_hkd' => $multi->fConvertHKD,
    );
}

// ---- 输出 ------------------------------------------------------------------
$json = json_encode(array(
    'links' => $links,
    'number_display' => $numberDisplay,
    'trans_row' => $transRow,
    'money_row' => $moneyRow,
    'money_group' => $moneyGroup,
    'group_model' => $groupModel,
    'convert' => $convert,
), JSON_UNESCAPED_UNICODE);

if (isset($argv[1]) && $argv[1] !== '-')
{
    file_put_contents($argv[1], $json);
    fwrite(STDERR, "php cases: "
        . (count($links, COUNT_RECURSIVE) + count($numberDisplay)
           + count($transRow) + count($moneyRow) + count($moneyGroup)
           + count($groupModel) + count($convert))
        . " -> " . $argv[1] . "\n");
}
else
{
    echo $json;
}
unlink($tmp);
exit(0);

}
