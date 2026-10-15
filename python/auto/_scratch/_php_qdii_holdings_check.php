<?php
// ---------------------------------------------------------------------------
// 对着**真实 PHP** 跑 `_QdiiReference` 的选档逻辑（CK4 收尾：QDII 持仓口径接线）
//
//   D:/xampp/php/php.exe -n -d display_errors=stderr _php_qdii_holdings_check.php
//
// ## 验的是什么
//
// `php/stock/qdiiref.php:110-124` 两个方法一模一样的套路：**三档里挑一档**。
//
// ```php
// function _getOfficialEstVal($strDate)      // 官方 EST
// {
//     if ($str = $this->_getEstNetValue($est_ref, $strDate))  return $str;   // ①净值
//     if (method_exists($est_ref, 'GetHoldingsDate'))  ...                   // ②持仓
//     return $this->_getEstPrice($est_ref, $strDate);                        // ③行情价
// }
// function _getFairEstVal($strDate)          // 参考 EST
// {
//     if (method_exists($est_ref, 'GetHoldingsDate'))  ...                   // ①持仓
//     if ($str = $this->_getEstNetValue($est_ref, $strDate))  return $str;   // ②净值
//     return $this->_getEstPrice($est_ref, $strDate);                        // ③行情价
// }
// ```
//
// **两档顺序是反的**：官方 EST 净值优先，参考 EST 持仓优先。写错成一样的顺序，
// 页面照样出数（只是那格取自另一档），不报错 —— 这正是要拿 PHP 钉住的原因。
//
// Python 侧对应实现是 `auto/calc/fundestcalc.py::_official_est_value`（加了
// `_holdings_est_value` 那一档）。**参考 EST 那条 Python 走的是
// `FundPairReference::GetFairNetValue()` 的另一套语义，不在本对照范围内。**
//
// ## 手法：函数体从 PHP 源码现场切出（不是手抄）
//
// 与 `_php_holdings_check.php` 同一套路子：按花括号配对切出方法，原样放进
// `namespace Harness\Ref`。切出来用到的两个全局函数（`SqlGetNetValueByDate` /
// `GetStockHistorySql`）会连库，所以在同一命名空间下定义同名替身覆盖掉。
//
// ## 五组输入
//
// 净值有无 × 持仓与否，外加"行情价取不到要退到上一交易日收盘"那条兜底。
// 输出的每一项是**选中的那一档的名字**，不只是一个数 —— 数值对但档位错
// （比如净值恰好等于行情价）时，只比数是看不出来的。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$rootDir = realpath(__DIR__ . '/../../..');
if ($rootDir === false) { fwrite(STDERR, "找不到仓库根\n"); exit(2); }

function ExtractMethod($text, $name)
{
    // **``\r?`` 不能省**：PHP 源码是 Windows 换行（``\r\n``），签名那行的
    // ``)`` 后面直接跟 ``\r`` —— 少了它就一个方法都切不出来（实测踩到）。
    $pattern = '/(?:^|\n)[ \t]*(?:public |protected |private |static )*function[ \t]+'
             . preg_quote($name, '/') . '[ \t]*\([^)]*\)[ \t]*\r?\n?[ \t]*\{/';
    if (!preg_match($pattern, $text, $m, PREG_OFFSET_CAPTURE)) { return null; }
    $start = $m[0][1];
    $i = $m[0][1] + strlen($m[0][0]) - 1;   // 指向起手的 '{'
    $depth = 0;
    $len = strlen($text);
    while ($i < $len)
    {
        if ($text[$i] === '{')      { $depth++; }
        elseif ($text[$i] === '}')  { $depth--; if ($depth === 0) { $i++; break; } }
        $i++;
    }
    return substr($text, $start, $i - $start);
}

$srcPath = $rootDir . '/php/stock/qdiiref.php';
$src = file_get_contents($srcPath);
if ($src === false) { fwrite(STDERR, "读不到 $srcPath\n"); exit(2); }

$methods = array();
foreach (array('_getOfficialEstVal', '_getFairEstVal', '_getEstNetValue', '_getEstPrice') as $name)
{
    $body = ExtractMethod($src, $name);
    if ($body === null) { fwrite(STDERR, "切不出 $name\n"); exit(2); }
    $methods[$name] = $body;
}

$glue = implode("\n\n    ", $methods);

$code = <<<PHPCODE
namespace Harness\\Ref;

// -- 固件：净值表 / 上一交易日收盘（真身会连库） -------------------------
class QdiiFixture
{
    public static \$netValueRows = array();
    public static \$prevClose = '749.58';

    public static function netValue(\$strStockId, \$strDate)
    {
        \$key = \$strStockId . '|' . \$strDate;
        return array_key_exists(\$key, self::\$netValueRows) ? self::\$netValueRows[\$key] : false;
    }

    // `_getEstPrice()` 里的 `GetStockHistorySql()->GetClosePrev(...)`
    public function GetClosePrev(\$strStockId, \$strDate)
    {
        return self::\$prevClose;
    }
}

// -- 切出来的方法会调这两个全局函数（真身会连库），这里给替身 -------------
function SqlGetNetValueByDate(\$strStockId, \$strDate)
{
    return QdiiFixture::netValue(\$strStockId, \$strDate);
}

function GetStockHistorySql()
{
    return new QdiiFixture();
}

class QdiiRef
{
    public \$est_ref = false;

    public function GetEstRef()     { return \$this->est_ref; }
    public function SetEstRef(\$r)  { \$this->est_ref = \$r; }

    $glue
}

// -- 中间层引用替身 -------------------------------------------------------
// 判据是 `method_exists(\$est_ref, 'GetHoldingsDate')`，所以两个替身的差别
// **只在有没有那个方法** —— 别的都一样，否则对照就不是在比判据了。
class PairEstRef
{
    private \$id;
    private \$symbol;
    private \$price;

    public function __construct(\$id, \$symbol, \$price) { \$this->id = \$id; \$this->symbol = \$symbol; \$this->price = \$price; }
    public function GetStockId()  { return \$this->id; }
    public function GetSymbol()   { return \$this->symbol; }
    public function GetPrice()    { return \$this->price; }
}

class HoldingsEstRef
{
    private \$id;
    private \$symbol;
    private \$price;
    public \$calls = array();

    public function __construct(\$id, \$symbol, \$price) { \$this->id = \$id; \$this->symbol = \$symbol; \$this->price = \$price; }
    public function GetStockId()  { return \$this->id; }
    public function GetSymbol()   { return \$this->symbol; }
    public function GetPrice()    { return \$this->price; }
    // **这一个方法就是判据本身**
    public function GetHoldingsDate() { return '2026-10-13'; }
    public function _estNetValue(\$strDate = false)
    {
        \$this->calls[] = \$strDate;
        return 1.0263;
    }
}
PHPCODE;

eval($code);

$DATE = '2026-10-14';
define('FIXTURE', 'Harness\\Ref\\QdiiFixture');

function scene($name, $estRef, $netValue, $price)
{
    global $DATE;
    $fixture = FIXTURE;
    $fixture::$netValueRows = array();
    if ($netValue !== false)
    {
        $fixture::$netValueRows[$estRef->GetStockId() . '|' . $DATE] = $netValue;
    }

    $ref = new Harness\Ref\QdiiRef();
    $ref->SetEstRef($estRef);
    $official = $ref->_getOfficialEstVal($DATE);
    $fair = $ref->_getFairEstVal($DATE);

    return array(
        'name' => $name,
        'official' => strval($official),
        'fair' => strval($fair),
        // 持仓那一档被调到时，记下传进去的日期 —— 官方传 $strDate、参考传空
        'calls' => property_exists($estRef, 'calls') ? $estRef->calls : array(),
    );
}

$out = array();

// 1) 非持仓 + 有净值 -> 两档都取净值
$out[] = scene('pair_with_netvalue',
    new Harness\Ref\PairEstRef('4', 'QQQ', '600.5'), '1.0', '600.5');

// 2) 非持仓 + 无净值 -> 两档都取行情价
$out[] = scene('pair_without_netvalue',
    new Harness\Ref\PairEstRef('4', 'QQQ', '600.5'), false, '600.5');

// 3) 持仓 + 有净值 -> **官方取净值**（净值优先）、参考取持仓
$out[] = scene('holdings_with_netvalue',
    new Harness\Ref\HoldingsEstRef('6', 'SH513050', '1.4'), '1.0', '1.4');

// 4) 持仓 + 无净值 -> 官方取持仓（带日期）；参考也取持仓（无参）
$out[] = scene('holdings_without_netvalue',
    new Harness\Ref\HoldingsEstRef('6', 'SH513050', '1.4'), false, '1.4');

// 5) 非持仓 + 无净值 + 当前价为空 -> 退到上一交易日收盘（SH000869 bug fix）
$f = new Harness\Ref\PairEstRef('4', 'QQQ', '');
$out[] = scene('pair_price_empty_falls_back', $f, false, '');

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), "\n";
