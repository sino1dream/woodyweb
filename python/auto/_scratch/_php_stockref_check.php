<?php
/**
 * 对着**真实 PHP** 跑 ``FundReference`` 那四个委托方法。
 *
 * 用法（在 python/auto 下）：
 *
 *     php _php_stockref_check.php
 *
 * ## 为什么验的是委托而不是"那一行"
 *
 * 本轮补的是 ``php/stock/fundref.php:19`` 那句
 * ``if ($this->IsFundA()) $this->stock_ref = new MyStockReference($strSymbol);``
 * —— 它本身只有两行，真正**被它激活**的是这四个委托方法：
 *
 *     GetSymbol / GetStockId / GetPriceDisplay / GetPercentageDisplay
 *
 * 只要 stock_ref 是 ``false``，它们一律走 ``parent::``；一旦建出来就整列转发。
 * 抄错一个参数（``GetPriceDisplay`` 是**三**参数）页面上看不出来，
 * 所以这里让 PHP 亲口说一遍"转发了什么、参数是什么"。
 *
 * ## 手法
 *
 * 与 `_php_fundlisthedge_check.php` 一样：用 ``ExtractFunctionBody()`` 把方法体
 * 从 PHP 源码里现场切出来，塞进 ``namespace Harness`` 的 stub 子类。方法体里
 * 的 ``parent::`` 落到 ``BaseRef``，``$this->stock_ref`` 是可注入的属性 ——
 * 于是一字不改地跑 PHP 自己的实现。
 */

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

function ExtractFunctionBody($text, $name)
{
    $pattern = '/(?:^|\n)[ \t]*(?:public |protected |private |static )*function[ \t]+'
             . preg_quote($name, '/') . '[ \t]*\(/';
    if (preg_match($pattern, $text, $m, PREG_OFFSET_CAPTURE) !== 1) { return false; }
    $start = $m[0][1];
    $len = strlen($text);
    $depth = 0;
    $in = false;
    for ($i = $start; $i < $len; $i++) {
        $ch = $text[$i];
        if ($ch === '{') { $depth++; $in = true; }
        else if ($ch === '}') {
            $depth--;
            if ($in && $depth === 0) { return substr($text, $start, $i - $start + 1); }
        }
    }
    return false;
}

function SlurpFunctions($path, $names)
{
    $text = file_get_contents($path);
    if ($text === false) { fwrite(STDERR, "读不到 $path\n"); exit(2); }
    $out = '';
    foreach ($names as $name) {
        $body = ExtractFunctionBody($text, $name);
        if ($body === false) { fwrite(STDERR, "找不到函数 $name\n"); exit(2); }
        $out .= $body . "\n";
    }
    return $out;
}

//: 四个委托 + ``GetStockRef()``，全在 fundref.php 里。
$delegates = SlurpFunctions($rootDir . '/php/stock/fundref.php', array(
    'GetSymbol', 'GetStockId', 'GetPriceDisplay', 'GetPercentageDisplay', 'GetStockRef'));

$stubs = <<<'STUBS'
class BaseRef
{
    public $symbol = 'SZ161130';
    public $stockId = '1';

    // 每个替身方法都记一笔"被调了、参数是啥" —— Python 侧同样记账，
    // 于是比对的不只是返回值，还有**参数有没有传丢**。
    public function GetSymbol()
    {
        $GLOBALS['calls'][] = array('parent.GetSymbol');
        return $this->symbol;
    }
    public function GetStockId()
    {
        $GLOBALS['calls'][] = array('parent.GetStockId');
        return $this->stockId;
    }
    public function GetPriceDisplay($fDisp = false, $fPrev = false, $iPrecision = false)
    {
        $GLOBALS['calls'][] = array('parent.GetPriceDisplay', $fDisp, $fPrev, $iPrecision);
        return 'parent:' . json_encode(array($fDisp, $fPrev, $iPrecision));
    }
    public function GetPercentageDisplay($fDivisor = false, $fDividend = false)
    {
        $GLOBALS['calls'][] = array('parent.GetPercentageDisplay', $fDivisor, $fDividend);
        return 'parent:' . json_encode(array($fDivisor, $fDividend));
    }
}

class StockRefStub
{
    public function GetSymbol()
    {
        $GLOBALS['calls'][] = array('stock.GetSymbol');
        return 'STOCKREF';
    }
    public function GetStockId()
    {
        $GLOBALS['calls'][] = array('stock.GetStockId');
        return '99';
    }
    public function GetPriceDisplay($fDisp = false, $fPrev = false, $iPrecision = false)
    {
        $GLOBALS['calls'][] = array('stock.GetPriceDisplay', $fDisp, $fPrev, $iPrecision);
        return 'stock:' . json_encode(array($fDisp, $fPrev, $iPrecision));
    }
    public function GetPercentageDisplay($fDivisor = false, $fDividend = false)
    {
        $GLOBALS['calls'][] = array('stock.GetPercentageDisplay', $fDivisor, $fDividend);
        return 'stock:' . json_encode(array($fDivisor, $fDividend));
    }
}

class FundStub extends BaseRef
{
    public $stock_ref = false;
STUBS;

//: 末尾那个 ``}`` 是 ``FundStub`` 的 —— 抽出来的方法体就填在它里面。
$code = "namespace Harness {\n" . $stubs . "\n" . $delegates . "\n}\n}\n";
eval($code);

$out = array('cases' => array(), 'traps' => array());

//: 每个用例：``stock`` 为 null 表示 PHP 的 ``$stock_ref = false``。
$cases = array(
    array(
        'name' => '没有 stock_ref：四个方法全走 parent',
        'stock' => null,
        'call' => 'all',
    ),
    array(
        'name' => '有 stock_ref：四个方法全转发',
        'stock' => 'yes',
        'call' => 'all',
    ),
    array(
        'name' => 'GetPriceDisplay 三参数要原样传过去',
        'stock' => 'yes',
        'call' => array('GetPriceDisplay', 3.14159, 3.0, 2),
    ),
    array(
        'name' => '没有 stock_ref 时参数照样传给 parent',
        'stock' => null,
        'call' => array('GetPriceDisplay', 3.14159, 3.0, 2),
    ),
    array(
        'name' => 'GetPercentageDisplay 两参数',
        'stock' => 'yes',
        'call' => array('GetPercentageDisplay', 1.0, 0.5),
    ),
);

foreach ($cases as $index => $case) {
    $GLOBALS['calls'] = array();
    $ref = new Harness\FundStub();
    if ($case['stock'] === 'yes') { $ref->stock_ref = new Harness\StockRefStub(); }

    $result = array('name' => $case['name']);
    if ($case['call'] === 'all') {
        $result['symbol'] = $ref->GetSymbol();
        $result['stock_id'] = $ref->GetStockId();
        $result['price'] = $ref->GetPriceDisplay();
        $result['percentage'] = $ref->GetPercentageDisplay();
        $result['stock_ref'] = $ref->GetStockRef() === false ? 'false' : 'object';
    } else {
        $args = array_slice($case['call'], 1);
        $method = $case['call'][0];
        $result['ret'] = call_user_func_array(array($ref, $method), $args);
    }
    $result['calls'] = $GLOBALS['calls'];
    $out['cases']['case.' . $index] = $result;
}

//: 分支条件本身：``if ($this->stock_ref)`` 对各种假值的判定。
$out['traps']['false_is_falsy'] = (bool)(false ? true : false);
$out['traps']['null_is_falsy'] = (bool)(null ? true : false);
$out['traps']['zero_string_is_falsy'] = (bool)('0' ? true : false);
$out['traps']['object_is_truthy'] = (bool)(new Harness\StockRefStub() ? true : false);

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), "\n";
