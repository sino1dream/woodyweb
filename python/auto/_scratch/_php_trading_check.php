<?php
// PHP 逐例对照：EchoFundTradingParagraph 的三档溢价列装配
// 源：php/ui/tradingparagraph.php:107-153（EchoFundTradingParagraph 整体）
//
// 方法（项目惯例）：把该函数的**列与标题装配段**从源码原样抠出（保留行号注释），
//   * 末尾 `_echoTradingParagraph(...)` 换成 return（本对照只关心列与标题）；
//   * 回调列与轮动交易链接两段不参与本对照（与三档装配无关），原样保留但
//     用 stub 顶掉外部依赖；
//   * 外部依赖（TableColumn*、StockGetPriceDisplay、四个 in_array* 查表、
//     GetRotationTradingLink、GetXueqiuLink）按 PHP 源码等价 stub。
// 8 种组合：official/fair/realtime 各有或无，stdout 只承载 JSON。

define('STOCK_DISP_OFFICIAL', '官方');     // php/ui/stockdisp.php:16
define('STOCK_DISP_FAIR', '参考');          // php/ui/stockdisp.php:9
define('STOCK_DISP_REALTIME', '实时');      // php/ui/stockdisp.php:25
define('STOCK_DISP_EST', 'EST');            // php/ui/stockdisp.php:8
define('STOCK_DISP_PREMIUM', '溢价');       // php/ui/stockdisp.php（查表下方）
define('STOCK_DISP_PRICE', '价格');
define('STOCK_DISP_QUANTITY', '数量');

// -- php/ui/htmlelement.php:28-69 原样 --------------------------------------
function GetHtmlElement($strContent, $strTag = 'p', $arAttribute = false)
{
	$strStart = $strTag;
	if ($arAttribute)
	{
		foreach ($arAttribute as $strAttribute => $strValue)
		{
			$strStart .= ' '.$strAttribute;
			if ($strValue !== false)		$strStart .= '='.$strValue;
		}
	}
	return "<$strStart>$strContent</$strTag>";
}
function GetBoldElement($strContent)      { return GetHtmlElement($strContent, 'b'); }
function GetFontElement($strContent, $strColor = 'red', $strStyle = false)
{
	$ar = array('color' => $strColor);
	if ($strStyle)	$ar['style'] = GetDoubleQuotes($strStyle);
	return GetHtmlElement($strContent, 'font', $ar);
}
function GetDoubleQuotes($str)            { return '"'.$str.'"'; }

// -- php/ui/table.php:23-54（TableColumn 基类）原样 --------------------------
class TableColumn
{
	var $strText;
	var $iWidth;

	public function __construct($strText = '', $iWidth = 80, $strColor = false, $strPrefix = false)
	{
		$this->iWidth = $iWidth;
		$this->strText = $strColor ? GetFontElement($strText, $strColor) : $strText;
        if ($strPrefix)	$this->strText = $strPrefix.$this->strText;
	}
	function GetText()    { return $this->strText; }
	function GetWidth()   { return $this->iWidth; }
	function GetDisplay() { return GetBoldElement($this->strText); }
}

// -- php/ui/stocktable.php 用到的三个子类（原样） ----------------------------
class TableColumnEst extends TableColumn
{
	public function __construct($strPrefix = false)
	{
        parent::__construct(STOCK_DISP_EST, 80, 'magenta', $strPrefix);
	}
}
class TableColumnPremium extends TableColumn
{
	public function __construct($strPrefix = false)
	{
        parent::__construct(STOCK_DISP_PREMIUM, 70, 'orange', $strPrefix);
	}
}
class TableColumnPrice extends TableColumn
{
	public function __construct($strPrefix = false, $iWidth = 70)
	{
        parent::__construct(STOCK_DISP_PRICE, $iWidth, 'blue', $strPrefix);
	}
}
class TableColumnQuantity extends TableColumn
{
	public function __construct($strPrefix = false)
	{
        parent::__construct(STOCK_DISP_QUANTITY, 100, 'indigo', $strPrefix);
	}
}
function TableColumnGetEst()     { $col = new TableColumnEst();     return $col->GetDisplay(); }
function TableColumnGetPremium() { $col = new TableColumnPremium(); return $col->GetDisplay(); }

// -- php/stock.php:91-109（StockGetPriceDisplay）原样 ------------------------
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

// -- 本对照不涉及的四个查表与轮动链接：stub 恒假/空 --------------------------
function in_arrayXopQdii($strSymbol)   { return false; }
function in_arrayXbiQdii($strSymbol)   { return false; }
function in_arraySpyQdii($strSymbol)   { return false; }
function in_arrayQqqMatch($strSymbol)  { return false; }
function GetRotationTradingLink($strSymbol) { return ''; }
function GetXueqiuLink($ref)           { return $ref->GetSymbol(); }
function GetStockRef($fund)            { return $fund->GetStockRef(); }

// -- 假引用对象 --------------------------------------------------------------
class FakeRef
{
	public $strPrevPrice = '4.814';
	function GetSymbol()        { return 'SZ161130'; }
	function GetPrevPrice()     { return $this->strPrevPrice; }
	function GetPriceDisplay($fDisp = false, $fPrev = false, $iPrecision = false)
	{
		return StockGetPriceDisplay(floatval($fDisp), floatval($fPrev), 3);
	}
}
class FakeFund
{
	public $ref;
	public $official;
	public $fair;
	public $realtime;
	function __construct($official, $fair, $realtime)
	{
		$this->ref = new FakeRef();
		$this->official = $official;
		$this->fair = $fair;
		$this->realtime = $realtime;
	}
	function GetStockRef()           { return $this->ref; }
	function GetOfficialNetValue()   { return $this->official; }
	function GetFairNetValue()       { return $this->fair; }
	function GetRealtimeNetValue()   { return $this->realtime; }
}

// -- tradingparagraph.php:7-11（_getTradingTableColumn）原样 ------------------
function _getTradingTableColumn()
{
	return array(new TableColumn('交易', 50),
				  new TableColumnPrice(),
				  new TableColumnQuantity());
}

// -- tradingparagraph.php:94-101（_getTradingParagraphStr）原样 ---------------
function _getTradingParagraphStr($ref, $arColumn)
{
    $strSymbol = GetXueqiuLink($ref);
	$strPrice = $arColumn[1]->GetDisplay();
	$str = "{$strSymbol}当前5档交易{$strPrice}";
    return $str;
}

// ---- tradingparagraph.php:107-153：EchoFundTradingParagraph 列与标题装配 ----
// 与源码的差异只有两处：末尾 _echoTradingParagraph 换成 return；
// 回调列那段（$callback 为假时不进）与轮动链接段原样保留。
function EchoFundTradingParagraphColumns($fund)
{
   	$ref = GetStockRef($fund);

    $arColumn = _getTradingTableColumn();
    $strPrice = _getTradingParagraphStr($ref, $arColumn);
	$fRealtime = false;
    if ($fOfficial = $fund->GetOfficialNetValue())
    {
    	$arColumn[] = new TableColumnPremium(STOCK_DISP_OFFICIAL);      // :115
    	$strPrev = $ref->GetPrevPrice();
    	$strEstPrice = $ref->GetPriceDisplay($fOfficial, floatval($strPrev));

    	if ($fFair = $fund->GetFairNetValue())                          // :121
    	{
    		$arColumn[] = new TableColumnPremium(STOCK_DISP_FAIR);      // :122
    		$strEstPrice .= '|'.$ref->GetPriceDisplay($fFair, floatval($strPrev));
    	}

   		if ($fRealtime = $fund->GetRealtimeNetValue())                  // :127
   		{
   			$arColumn[] = new TableColumnPremium(STOCK_DISP_REALTIME); // :128
   			$strEstPrice .= '|'.$ref->GetPriceDisplay($fRealtime, floatval($strPrev));
    	}

    	$strEst = TableColumnGetEst();
    	$strPremium = TableColumnGetPremium();
    	$str = "{$strPrice}相对于{$strEst}{$strEstPrice}的{$strPremium}";
    }
    else
	{
		$str = $strPrice;
		$fFair = false;
	}
    return array('columns' => array_map(function($col) { return $col->GetText(); }, $arColumn),
                 'title'   => $str);
}

// -- 跑 8 种组合 --------------------------------------------------------------
$arCases = array(
	array('official' => 4.545, 'fair' => false,   'realtime' => false),
	array('official' => 4.545, 'fair' => 4.549,   'realtime' => false),
	array('official' => 4.545, 'fair' => false,   'realtime' => 4.6),
	array('official' => 4.545, 'fair' => 4.549,   'realtime' => 4.6),
	array('official' => false, 'fair' => false,   'realtime' => false),
	array('official' => false, 'fair' => 4.549,   'realtime' => false),
	array('official' => false, 'fair' => false,   'realtime' => 4.6),
	array('official' => false, 'fair' => 4.549,   'realtime' => 4.6),
);
$arOut = array();
foreach ($arCases as $arCase)
{
	$fund = new FakeFund($arCase['official'], $arCase['fair'], $arCase['realtime']);
	$arOut[] = EchoFundTradingParagraphColumns($fund);
}
echo json_encode($arOut, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
