<?php
// ---------------------------------------------------------------------------
// portfolioparagraph.php 迁移的 PHP 参考值
//
//   php -n -d display_errors=stderr _php_portfolio_check.php            # JSON 到 stdout
//   php -n -d display_errors=stderr _php_portfolio_check.php out.json   # 写到文件
//
// ## 做法：抽函数体 + 命名空间打桩
//
// 与 `_php_futurepremium_check.php` 同款：不 require 整个
// `php/ui/portfolioparagraph.php`（那会拖进 stocktable.php / table.php
// 与一堆渲染函数，见 pitfalls 第 40 条），只抠函数体。区别在**桩**：
//
// `_getArbitrageTestStr()` 要查 `stockgroupitem` 表。这里把抽出来的函数体
// 塞进 `namespace PortfolioCheck`，并在**同一命名空间**里定义假的
// `StockGroupItemSql` / `mysqli_fetch_assoc` / `GetStockHedge` /
// `SqlGetStockSymbol` —— PHP 的非限定函数调用先在当前命名空间找、找不到
// 再回退全局，于是抽出来的函数体打到的是这些桩，而 `number_format` /
// `intval` 这些**没有被遮盖**的内建仍然走真的。
//
// 桩只替代**取数**；被测逻辑（循环、break、循环变量泄漏、算式、
// number_format 的舍入）全部是 PHP 原文逐字执行。
//
// ## 覆盖（与 _php_portfolio_compare.py 的 CASES 必须逐项一致）
//
//   ① _getPortfolioTestVal     写死目标持仓的偏离量（portfolioparagraph.php:4）
//   ② _getArbitrageTestStr     跨标的套利测试列（同文件:51，含泄漏语义）
//   ③ GetArbitrageQuantity     LOF 折股（stockcomm.php:65）
//   ④ fx_susdcnh 行的两个展示串（同文件:132 的表达式）
//   ⑤ _transSortBySymbol + 分块拼接  行序（_myportfolio.php:14 + :151）
//   ⑥ StockTransaction / MyStockTransaction / AddSqlTransaction /
//      GetSqlTransactionDate   持仓模型算术（stocktrans.php）
//
// ## 已知偏差（Python 侧刻意不同，compare 脚本里有 KNOWN_DIVERGENCES）
//
//   * fx_susdcnh 的 GetVal() 为 0 时 Python 有除零保护 —— 该分支不进
//     对照（生产上美元汇率永不为 0，PHP 那边只会警告 + 产出垃圾）。
//   * GetSqlTransactionDate 找不到空格时 PHP 回 false，端口按 AGENTS.md
//     的字符串类映射规则回 None（compare 侧把 false 归一成 None 后比对）。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）。
// ---------------------------------------------------------------------------

namespace PortfolioCheck {

// 按名字从源码里抠出整个函数（花括号配对），与 futurepremium harness 同款
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

// ---- 假结果集 ---------------------------------------------------------------
// 真的 mysqli_fetch_assoc() 逐行返回关联数组、耗尽时回 null；
// mysqli_free_result() 对任何参数都算成功。两者都在本命名空间被遮盖，
// 于是 _getArbitrageTestStr() 里那两个调用打到的是桩。
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

// 假 StockGroupItemSql：GetAll() 返回当前用例的条目队列
class StockGroupItemSql
{
    public function __construct($strGroupId)
    {
    }

    public function GetAll()
    {
        return new FakeResult($GLOBALS['pq_items']);
    }
}

// 假 SqlGetStockSymbol()：查用例表，查不到原样返回 stock_id
function SqlGetStockSymbol($strStockId)
{
    $ar = $GLOBALS['pq_symbols'];
    return isset($ar[$strStockId]) ? $ar[$strStockId] : $strStockId;
}

// 假 GetStockHedge()：按 stock_id 查用例表，查不到回 1.0（与真函数一致）
function GetStockHedge($strSymbol, $strStockId)
{
    $ar = $GLOBALS['pq_hedges'];
    return isset($ar[$strStockId]) ? floatval($ar[$strStockId]) : 1.0;
}

// ---- 排序用的假 trans / ref ---------------------------------------------------
// _transSortBySymbol() 只要 GetRef()->GetSymbol()；调用方分块还要
// IsSymbolA() / IsSymbolH()。价格单独一个字段，给持仓模型用。
class FakeRef
{
    public $strSymbol;
    public $bA;
    public $bH;
    public $strPrice;

    public function __construct($strSymbol, $bA = false, $bH = false, $strPrice = '')
    {
        $this->strSymbol = $strSymbol;
        $this->bA = $bA;
        $this->bH = $bH;
        $this->strPrice = $strPrice;
    }

    public function GetSymbol()
    {
        return $this->strSymbol;
    }

    public function IsSymbolA()
    {
        return $this->bA;
    }

    public function IsSymbolH()
    {
        return $this->bH;
    }

    public function GetPrice()
    {
        return $this->strPrice;
    }
}

class FakeTrans
{
    public $iId;
    public $ref;

    public function __construct($iId, $ref)
    {
        $this->iId = $iId;
        $this->ref = $ref;
    }

    public function GetRef()
    {
        return $this->ref;
    }
}

// ---- 抽取被测的 PHP 原文 -------------------------------------------------------
$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false)
{
    fwrite(STDERR, "找不到 php 目录\n");
    exit(2);
}

$portpara = file_get_contents($phpDir . DIRECTORY_SEPARATOR . 'ui' . DIRECTORY_SEPARATOR . 'portfolioparagraph.php');
$stockcomm = file_get_contents($phpDir . DIRECTORY_SEPARATOR . 'ui' . DIRECTORY_SEPARATOR . 'stockcomm.php');
$myportfolio = file_get_contents($phpDir . DIRECTORY_SEPARATOR . '..' . DIRECTORY_SEPARATOR . 'woody' . DIRECTORY_SEPARATOR . 'res' . DIRECTORY_SEPARATOR . 'php' . DIRECTORY_SEPARATOR . '_myportfolio.php');
$stocktrans = file_get_contents($phpDir . DIRECTORY_SEPARATOR . 'stocktrans.php');

$testValBody = FExtractFunction($portpara, '_getPortfolioTestVal');
$arbStrBody = FExtractFunction($portpara, '_getArbitrageTestStr');
$arbQtyBody = FExtractFunction($stockcomm, 'GetArbitrageQuantity');
$sortBody = FExtractFunction($myportfolio, '_transSortBySymbol');
$stockTransClass = FExtractClass($stocktrans, 'StockTransaction');
$myStockTransClass = FExtractClass($stocktrans, 'MyStockTransaction');
$sqlDateFn = FExtractFunction($stocktrans, 'GetSqlTransactionDate');
$addSqlFn = FExtractFunction($stocktrans, 'AddSqlTransaction');

foreach (array('_getPortfolioTestVal' => $testValBody,
               '_getArbitrageTestStr' => $arbStrBody,
               'GetArbitrageQuantity' => $arbQtyBody,
               '_transSortBySymbol' => $sortBody,
               'StockTransaction' => $stockTransClass,
               'MyStockTransaction' => $myStockTransClass,
               'GetSqlTransactionDate' => $sqlDateFn,
               'AddSqlTransaction' => $addSqlFn) as $name => $body)
{
    if ($body === null)
    {
        fwrite(STDERR, "抽取失败: $name\n");
        exit(3);
    }
}

// 拼一个临时文件：同样的命名空间 + 桩已在主文件里，这里只放原文
$tmp = tempnam(sys_get_temp_dir(), 'pf') . '.php';
$header = "<?php\nnamespace PortfolioCheck;\n";
$blocks = array($testValBody, $arbStrBody, $arbQtyBody, $sortBody,
                $stockTransClass, $myStockTransClass, $sqlDateFn, $addSqlFn);
file_put_contents($tmp, $header . implode("\n", $blocks) . "\n");
require $tmp;

// ---- 用例 ---------------------------------------------------------------------
// 与 _php_portfolio_compare.py 的 CASES **必须逐项一致**

// ① _getPortfolioTestVal($iShares, $strSymbol)
$testValCases = array(
    array('KWEB', 500), array('KWEB', 200), array('KWEB', 0), array('KWEB', -100),
    array('KWEB', 199), array('KWEB', 201),
    array('SH600104', 12000), array('SH600104', 13000), array('SH600104', 0),
    array('SH600104', -12000),
    array('TLT', 500), array('TLT', 700), array('TLT', 0),
    // PHP 源码里注释掉的分支（XOP/ASHR/SPY/SH510300/SZ162411/XBI）走 default
    array('XOP', 400), array('ASHR', 601), array('SPY', 100), array('SH510300', 30000),
    array('SZ162411', 91200), array('XBI', 0),
    array('SH513100', 1000), array('fx_susdcnh', 100000), array('hf_ES', 25),
    array('', 1234),
);
$testVal = array();
foreach ($testValCases as $c)
{
    $testVal[] = array('symbol' => $c[0], 'shares' => $c[1],
                       'value' => _getPortfolioTestVal($c[1], $c[0]));
}

// ② _getArbitrageTestStr($iShares, $strGroupId, $strStockId, $strSymbol)
//    symbol 恒为 KWEB（PHP 的 switch 只给它开了口）
$arbStrCases = array(
    // 组内一条记录都没有 → ''
    array('items' => array(), 'self' => '502', 'shares' => 300, 'hedges' => array(), 'symbols' => array()),
    // 自己是 503：扫到 501 的 600 就 break，偏离 300-200=100
    array('items' => array(array('501', '600'), array('502', '300'), array('503', '200')),
          'self' => '503', 'shares' => 300, 'hedges' => array(), 'symbols' => array()),
    // 自己是 501：502 的 300 即 break；300 + 100
    array('items' => array(array('501', '600'), array('502', '300'), array('503', '200')),
          'self' => '501', 'shares' => 300, 'hedges' => array(), 'symbols' => array()),
    // hedge 2.0：600 + 100*2
    array('items' => array(array('501', '600'), array('502', '300'), array('503', '200')),
          'self' => '503', 'shares' => 300, 'hedges' => array('501' => 2.0), 'symbols' => array()),
    // 全部非正：不 break，数量停在最后一个非本标的行（503 的 -5）
    array('items' => array(array('501', '0'), array('503', '-5')),
          'self' => '502', 'shares' => 300, 'hedges' => array(), 'symbols' => array()),
    // 最后一行是自己 → 循环变量泄漏指向自己
    array('items' => array(array('501', '0'), array('502', '0')),
          'self' => '502', 'shares' => 300, 'hedges' => array(), 'symbols' => array()),
    // 组里只有自己一条
    array('items' => array(array('502', '300')),
          'self' => '502', 'shares' => 300, 'hedges' => array(), 'symbols' => array()),
    // 数量是带空格/前导零的字符串 —— intval 的截断
    array('items' => array(array('501', ' 600 '), array('502', '0300')),
          'self' => '503', 'shares' => 300, 'hedges' => array(), 'symbols' => array()),
    // SqlGetStockSymbol 替身 + 非 1.0 hedge
    array('items' => array(array('501', '600'), array('502', '300')),
          'self' => '503', 'shares' => 300, 'hedges' => array('501' => 3.0),
          'symbols' => array('501' => 'SZ161125')),
    // 偏离为负：600 + (100-200)
    array('items' => array(array('501', '600')),
          'self' => '501', 'shares' => 100, 'hedges' => array(), 'symbols' => array()),
    // 小数 hedge：600 + 100*0.5
    array('items' => array(array('501', '600'), array('502', '300')),
          'self' => '503', 'shares' => 300, 'hedges' => array('501' => 0.5), 'symbols' => array()),
);
$arbStr = array();
foreach ($arbStrCases as $c)
{
    $GLOBALS['pq_items'] = array();
    foreach ($c['items'] as $item)
    {
        $GLOBALS['pq_items'][] = array('stock_id' => $item[0], 'quantity' => $item[1]);
    }
    $GLOBALS['pq_hedges'] = $c['hedges'];
    $GLOBALS['pq_symbols'] = $c['symbols'];
    $arbStr[] = array(
        'items' => $c['items'], 'self' => $c['self'], 'shares' => $c['shares'],
        'hedges' => $c['hedges'], 'symbols' => $c['symbols'],
        'value' => _getArbitrageTestStr($c['shares'], 'g1', $c['self'], 'KWEB'),
    );
}

// ③ GetArbitrageQuantity($strSymbol, $strStockId, $fQuantity)
//    hedge 由假 GetStockHedge() 按 stock_id 提供
$arbQtyCases = array(
    array('501', 1000, 1.0),
    array('501', 1234567, 1.0),
    array('501', 999.5, 1.0),
    array('501', 0.4, 1.0),
    array('501', 2.5, 1.0),
    array('501', -2.5, 1.0),
    array('501', -1234.5, 1.0),
    array('501', 0, 1.0),
    array('501', 1000, 2.0),
    array('501', 1000, 0.5),
    array('501', 1000, 0.001),
    array('501', 1234567.89, 3.7),
    array('501', '1234567', 1.0),      // 字符串数量（库里取出来可能是字符串）
);
$arbQty = array();
foreach ($arbQtyCases as $c)
{
    $GLOBALS['pq_hedges'] = array($c[0] => $c[2]);
    $arbQty[] = array('stock_id' => $c[0], 'quantity' => $c[1],
                      'value' => GetArbitrageQuantity('SZ161125', $c[0], $c[1]));
}

// ④ fx_susdcnh 行的两个展示串（portfolioparagraph.php:132 的表达式，
//    不含 GetOvernightCnhLink —— 链接构造在 stocklink 侧已单独对照）
$fxCases = array(
    array(710000.0, 0.0, 7.1),
    array(710000.0, 1140.0, 7.1),
    array(710000.0, 500.0, 7.1),
    array(100000.0, 0.0, 7.12),
    array(0.0, 1740.0, 7.1),
    array(-5000.0, 0.0, 7.1),
    array(12345678.0, 0.0, 6.5),
    array(999.5, 0.5, 7.1),
    array(1234567.5, 1234.5, 6.5),
    array(0.5, 0.0, 7.1),              // round(-0.5) = -1（远离零）
    array(-0.5, 0.0, 7.1),             // round(0.5) = 1
);
$fxRow = array();
foreach ($fxCases as $c)
{
    $fVal = $c[0] + $c[1];
    $fxRow[] = array(
        'fVal' => $c[0], 'fCny' => $c[1], 'val' => $c[2],
        'text' => '$' . number_format($fVal / $c[2] / 1000.0) . 'K',
        'amount' => strval(round(0.0 - $fVal)),
    );
}

// ⑤ 行序：_echoPortfolio()（_myportfolio.php:140-151）的分块 + 拼接
$sortCases = array(
    // A/H/US 各来一个
    array(array('KWEB', false, false), array('SZ161125', true, false),
          array('HK00700', false, true), array('SH513100', true, false)),
    // 全是美股：小写 f 排在大写之后
    array(array('fx_susdcnh', false, false), array('KWEB', false, false),
          array('SPY', false, false), array('TLT', false, false)),
    // hf_ES vs fx_susdcnh：'f' < 'h'，美元折算行排在期货前面
    array(array('hf_ES', false, false), array('fx_susdcnh', false, false)),
    // 美股符号可以排在 A 股前面（分块在调用方，不在排序里）
    array(array('AAPL', false, false), array('SH600104', true, false)),
    // 同符号两笔：并成一组、组内保持到达顺序
    array(array('KWEB', false, false), array('KWEB', false, false), array('SPY', false, false)),
    // 混合大户
    array(array('SH600104', true, false), array('SZ161125', true, false),
          array('HK00700', false, true), array('hf_ES', false, false),
          array('fx_susdcnh', false, false)),
    array(),
);
$sort = array();
foreach ($sortCases as $case)
{
    $arTransA = array();
    $arTransH = array();
    $arTransUS = array();
    foreach ($case as $i => $spec)
    {
        $trans = new FakeTrans($i, new FakeRef($spec[0], $spec[1], $spec[2]));
        if ($trans->GetRef()->IsSymbolA())       $arTransA[] = $trans;
        else if ($trans->GetRef()->IsSymbolH())  $arTransH[] = $trans;
        else                                     $arTransUS[] = $trans;
    }
    $arTrans = array_merge(_transSortBySymbol($arTransA),
                           _transSortBySymbol($arTransH),
                           _transSortBySymbol($arTransUS));
    $order = array();
    foreach ($arTrans as $trans)
    {
        $order[] = array('id' => $trans->iId, 'symbol' => $trans->GetRef()->GetSymbol());
    }
    $sort[] = $order;
}

// ⑥ 持仓模型：MyStockTransaction + AddTransaction
$transCases = array(
    array('1.2', array(array(100, 123.4), array(200, 150.0))),
    array('1.2', array(array(-400, -1000.0), array(100, 250.5))),
    array('33.80', array(array(300, 24000.0))),
    array('', array(array(100, 120.0))),            // 空价格 → floatval 0
    array('0', array(array(100, 120.0))),
    array('7.1', array()),                          // 没有交易
    array('1.5', array(array(0, 0.0))),             // 清仓
    array('1.5', array(array(100, 120.0), array(0, 0.0), array(-100, -120.0))),  // 买满再卖光
);
$trans = array();
foreach ($transCases as $c)
{
    $t = new MyStockTransaction(new FakeRef('SZ161125', true, false, $c[0]), false);
    foreach ($c[1] as $op)
    {
        $t->AddTransaction($op[0], $op[1]);
    }
    $trans[] = array(
        'price' => $c[0],
        'ops' => $c[1],
        'shares' => $t->GetTotalShares(),
        'cost' => $t->GetTotalCost(),
        'records' => $t->GetTotalRecords(),
        'avg' => $t->GetAvgCost(),
        'value' => $t->GetValue(),
        'profit' => $t->GetProfit(),
    );
}

// ⑦ AddSqlTransaction()：数量取整、成本含手续费
$sqlTransCases = array(
    array(array('quantity' => '100', 'price' => '1.234', 'fees' => '5')),
    array(array('quantity' => '100', 'price' => '1.234', 'fees' => '5'),
          array('quantity' => '200', 'price' => '1.5', 'fees' => '')),
    array(array('quantity' => '200.7', 'price' => '1.5', 'fees' => '')),     // intval 截断
    array(array('quantity' => '-400', 'price' => '2.5', 'fees' => '3.5')),   // 负数数量
    array(array('quantity' => '100', 'price' => '', 'fees' => '')),          // 缺价格
    array(array('quantity' => '0', 'price' => '1.0', 'fees' => '0')),
    array(array('quantity' => ' 100 ', 'price' => ' 1.234 ', 'fees' => ' 5 ')),  // 带空格
);
$sqlTrans = array();
foreach ($sqlTransCases as $case)
{
    $t = new StockTransaction();
    foreach ($case as $record)
    {
        AddSqlTransaction($t, $record);
    }
    $sqlTrans[] = array(
        'records' => $case,
        'shares' => $t->GetTotalShares(),
        'cost' => $t->GetTotalCost(),
        'records_count' => $t->GetTotalRecords(),
    );
}

// ⑧ GetSqlTransactionDate()：strstr($filled, ' ', true)
$sqlDateCases = array(
    '2026-10-13 09:30:00',
    '2026-10-13',
    '2026-10-13  ',
    ' 2026-10-13 09:30:00',
    '',
    ' ',
);
$sqlDate = array();
foreach ($sqlDateCases as $filled)
{
    $sqlDate[] = array('filled' => $filled, 'value' => GetSqlTransactionDate(array('filled' => $filled)));
}

// ---- 输出 ---------------------------------------------------------------------
// 诊断一律走 stderr，stdout 只承载 JSON。给了路径参数就写文件，方便
// compare 脚本以 ref.json 复用同一份参考值。
$json = json_encode(array(
    'test_val' => $testVal,
    'arb_str' => $arbStr,
    'arb_qty' => $arbQty,
    'fx_row' => $fxRow,
    'sort' => $sort,
    'trans' => $trans,
    'sql_trans' => $sqlTrans,
    'sql_date' => $sqlDate,
), JSON_UNESCAPED_UNICODE);

if (isset($argv[1]) && $argv[1] !== '-')
{
    file_put_contents($argv[1], $json);
    fwrite(STDERR, "php cases: " . count($testVal) + count($arbStr) + count($arbQty)
        + count($fxRow) + count($sort) + count($trans) + count($sqlTrans) + count($sqlDate)
        . " -> " . $argv[1] . "\n");
}
else
{
    fwrite(STDERR, "php cases written\n");
    echo $json;
}
unlink($tmp);
exit(0);

}
