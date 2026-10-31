"""义工群对冲计算器 —— 对应 ``woody/res/overnightcnhcn.php``（200 行）。

## 这一页回答什么问题

"我手上有一笔要平衡的离岸人民币 CNH，换成义工群里覆盖的这些 QDII 基金，
**每只该买/卖多少股**、对冲标的要动几张。"

与轮动交易页（:mod:`web.services.rotation_service`）是姊妹页，但方向相反：

* 轮动页：**换掉**已经持有的某只，输入是"要卖多少股"；
* 本页：**新建**一堆仓位，输入是"要平衡多少钱"。

## 输入串的四种形态

输入框里的东西不是纯数字，末几位决定换算口径（``_convertCnhInput``）：

============  ==========================================================
后缀           含义
============  ==========================================================
``F``         基金申购：金额 → 份额。``÷(1+申购费) × × 溢价 × 仓位``
``MCL``       原油期货：金额 → 桶。``期货价 × 中间价 × 溢价修正 × 100``
``MGC``       黄金期货：金额 → 盎司。同上，乘数 ``10``
（无后缀）     纯金额，直接拿去算
============  ==========================================================

"金额 → 股数"的最后一步是共用的：``(CNH ÷ 仓位) ÷ 价格``。

**方向由首字符决定**（``substr($strInput, 0, 1) == '-'``）：负数 = 卖，
表头前缀"可卖"、取买盘；正数 = 买，前缀"可买"、取卖盘。

## 两套"对冲操作"文案

同一列在两种引用对象下写法完全不同（``_echoOverNightCnhItem`` 的 if/else）：

* **有 ``GetStockRef()``**（QDII 那一族）→ 用 ``GetStockHedge()`` 换算成对冲
  标的的股数，文案 ``买入5000股QQQ``；
* **只有 ``GetHoldingsDate()``**（持仓型）→ 按季报成分股**逐只**折成股数，
  文案 ``买入1234.5股NVDA、5678.9股MSFT，共6913股。``。

两者都不成立（比如中间层建不出来的 QDII）时，这一格**整格不出现**。

## 五道闸门

1. **不属于任何一族**——不存在这道闸门，本页的符号表是写死的 23 只
   （``GetOverNightSymbolArray()``），没有分派；
2. **输入为空**（点"清除"）→ 只出表单，不出表；
3. **引用对象建不出来** → 这一行跳过（PHP 那边 `new` 不会失败，本版连不上库会）；
4. **该方向没有挂单** → 这一行只有代码一格（变长行，与轮动页同一形状）；
5. **数量换算不出来**（净值/汇率缺） → 建议数量与对冲格留空，符号进页脚
   "算不出"清单（见偏离 2）。

## 偏离

1. **表 id** 用 ``overnightcnh-table``（PHP 是 ``$acct->GetPage()``，恒为
   ``overnightcnh``）；本页只有一张表，id 不冲突；
2. **换算不出来时不 500**：``_fundCnhInput`` 里 ``价格 ÷ 官方净值`` 与
   ``CNH ÷ 仓位 ÷ 价格`` 两步都可能有 0 分母，PHP 8 会抛
   ``DivisionByZeroError`` 整页 500（净值缺失的标的一进这一页就白屏）。
   本版把这类行标成"算不出"，页脚列出符号与原因 —— 少一行数值，不少整页。
   同理，``MCL`` / ``MGC`` 那两支取不到期货价或中间价时也按"算不出"处理：
   PHP 会拿到 0 并让 23 行一起显示 0 股，那种页面既不是正确值也没给出理由；
3. ``EchoTableParagraphBegin()`` 里"表格超过屏幕宽度就整张不输出"未迁；
4. ``GetNumberDisplay()`` / ``GetFontElement()`` 的 ``<font>`` 换成格子的
   颜色字段，模板负责上色（与轮动页同一套）；
5. ``EchoEditInputForm()`` 的三个"点击复制"链接（``CopyPhpLink``）改成
   普通 GET 链接 —— 本地没有剪贴板脚本，行为是"点一下就按那个口径算一遍"；
6. 页面底部 ``$acct->EchoLinks()`` 那一整块相关链接不迁（账号体系暂缓），
   只留三个站内回链。

## 数据量

符号表 23 只，每只建一个引用对象。``QdiiReference`` 构造会把三档估值都算一遍
（``auto_estimate``），所以整页约 3 秒 —— 与 PHP 现场算的量级相同，本版没有
加缓存：这一页本来就是"点一下、算一遍"的工具页。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from web.bootstrap import install_paths

install_paths()

from core.stocksymbol import get_over_night_symbol_array  # noqa: E402
from core.submittransaction import stock_get_fund_fee_ratio  # noqa: E402
from sql.historytable import TableRow, php_round  # noqa: E402
from ref.stockref import _php_strval_float  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    column_base,
    column_hedge,
    column_price,
    column_quantity,
    column_symbol,
    columns_to_dicts,
    table_column_get_last_width,
)
from web.services.rotation_service import (  # noqa: E402
    number_display,
    stock_ref_of,
)

#: ``OVERNIGHT_CNH_DISPLAY``（``php/stocklink.php:18``）。
OVERNIGHT_CNH_DISPLAY = "义工群对冲计算器"

#: 什么都没给时的默认金额（``overnightcnhcn.php:167``）。
DEFAULT_INPUT = "100000"

#: URL 查询项名（``GetOvernightCnhLink()`` 拼的是 ``overnightcnh=95000``）。
INPUT_QUERY_ITEM = "overnightcnh"

#: 建议股数凑整粒度（``round($fHintQuantity / 100.0) * 100.0``）。
QUANTITY_LOT = 100.0

#: 表头最后那一列：吃剩余宽度，文本是 "建议" + "对冲操作"。
MEMO_COLUMN_TEXT = "建议对冲操作"

#: 持仓折股时**不补**"，共N股。"的四个符号（``overnightcnhcn.php:124``）。
#: 作者没写理由；从名单看是当时还没上市或成分股对不上的那几只，照抄。
NO_TOTAL_SYMBOLS = ("SZ160216", "SZ161815", "SZ163208", "SZ164701")

#: 期货那一支的乘数（一手的单位数）：原油 100 桶、黄金 10 盎司。
FUTURE_MULTIPLE = {"MCL": 100.0, "MGC": 10.0}

#: 后缀 → 期货符号。``SZ161226`` 是唯一例外（下面单独处理汇率）。
FUTURE_SYMBOL = {"MCL": "hf_CL", "MGC": "hf_GC"}

#: 唯一一只**不用本基金自己的中间价**、写死 ``USCNY`` 的符号
#: （``overnightcnhcn.php:13``）。它是白银基金，持仓在伦敦/纽约两地计价，
#: 作者选择用离岸人民币直接折。
FORCE_USCNY_SYMBOL = "SZ161226"


# ---------------------------------------------------------------------------
# PHP 语义小工具（与 rotation_service 的三只同名，按本仓库惯例各模块自留一份）
# ---------------------------------------------------------------------------
def _php_truthy(value: Any) -> bool:
    """PHP 的布尔转换：``''`` / ``'0'`` / ``0`` / ``None`` 都是假。"""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value) not in ("", "0")


def _floatval(value: Any) -> float:
    """``floatval()``：非数字串与 ``None`` 都给 0.0（不抛异常）。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _call(obj: Any, snake: str, camel: str, *args: Any) -> Any:
    """先 snake 后 camel 取方法；没有该方法或抛异常时返回 ``None``。

    **异常在这里被吞掉是有意的**：引用对象来自真库，某些取数路径在数据不全时
    会抛（实测 ``HoldingsReference.get_official_net_value()`` 在没有净值行时
    抛 ``TypeError``）。PHP 那边对应的是"取不到就 false"，页面照常出。
    """
    if obj is None:
        return None
    method = getattr(obj, snake, None)
    if method is None:
        method = getattr(obj, camel, None)
    if method is None or not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def _strval_number(value: Any) -> str:
    """PHP ``strval()`` 对算出来的浮点数。

    先走 :func:`ref.stockref._php_strval_float`（14 位有效数字），再去掉
    ``.0`` 尾巴 —— PHP 的 ``strval(871000.0)`` 是 ``'871000'`` 不是
    ``'871000.0'``。``-0.0`` 保留负号（PHP 实测 ``strval(-0.0)`` 得 ``'-0'``）。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    text = _php_strval_float(number)
    if text.endswith(".0"):
        text = text[:-2]
    return text


# ---------------------------------------------------------------------------
# 输入串
# ---------------------------------------------------------------------------
def parse_input(value: Any) -> str:
    """``GetEditInput()`` + ``GetQuery()`` 之后再落到默认值。

    PHP 那边三段（``overnightcnhcn.php:165-168``）::

        if (($strInput = GetEditInput()) === false)          // POST 没提交
            if (($strInput = $acct->GetQuery()) === false)   // URL 也没带
                $strInput = '100000';

    所以"点清除"给的 ``''`` 是**通过**的（不是 false），页面因此只出表单 ——
    这一点与轮动页不同（那边 ``''`` 会回落到 -1000000 占位值）。
    """
    if value is None:
        return DEFAULT_INPUT
    return str(value)


def is_sell_input(input_text: str) -> bool:
    """``$bSell = (substr($strInput, 0, 1) == '-')``：负数 = 卖。"""
    return str(input_text)[:1] == "-"


# ---------------------------------------------------------------------------
# 金额 → 对冲口径（_convertCnhInput 及其两个分支）
# ---------------------------------------------------------------------------
def convert_cnh_input(
    ref: Any,
    stock_ref: Any,
    input_text: str,
    future_ref_builder: Callable[[str], Any] = None,
) -> Optional[float]:
    """``_convertCnhInput()``；**换算不出来时返回 ``None``**（偏离 2）。

    返回值的绝对值就是"这笔钱折成多少 CNH 的对冲敞口"，后面统一
    ``÷ 仓位 ÷ 价格`` 换成股数。
    """
    text = "" if input_text is None else str(input_text)
    if text.endswith("F"):
        return _fund_cnh_input(ref, stock_ref, text)
    if text.endswith("MCL"):
        return _future_cnh_input(ref, future_ref_builder, "MCL", text)
    if text.endswith("MGC"):
        return _future_cnh_input(ref, future_ref_builder, "MGC", text)
    return abs(_floatval(text))


def _fund_cnh_input(ref: Any, stock_ref: Any, input_text: str) -> Optional[float]:
    """``_fundCnhInput()``：申购金额 → 份额 → 折成 CNH。

    .. code-block:: php

        $fPremium = floatval($stock_ref->GetAvailablePrice(false)) / $ref->GetOfficialNetValue();
        $fCnh = floatval(substr($strInput, 0, strlen($strInput) - 1));   // 去掉 'F'
        $fCnh /= 1 + $fFeeRatio;                                        // 申购费
        $fCnh *= $fPremium;                                             // 溢价
        $fCnh *= $ref->GetPosition();

    **溢价用卖一价（``false``）算，与方向无关** —— 申购永远按卖价成交，
    哪怕是"卖出这笔钱"的口径也一样。照抄，别改成 `is_sell`。

    官方净值取不到时返回 ``None``：PHP 在这里会除零（见偏离 2）。
    **卖一价为 0 不算失败** —— PHP 得 ``premium = 0``、整笔金额折成 0，
    本版照样给 0（少数据是数据的事，别替它改判）。
    """
    symbol = str(_call(ref, "get_symbol", "GetSymbol") or "")
    fee_ratio = stock_get_fund_fee_ratio(symbol)

    price = _floatval(_call(stock_ref, "get_available_price", "GetAvailablePrice", False))
    official = _floatval(_call(ref, "get_official_net_value", "GetOfficialNetValue"))
    if not official:
        return None

    amount = _floatval(str(input_text)[:-1])
    cnh = amount / (1.0 + fee_ratio)
    cnh *= price / official
    cnh *= _floatval(_call(ref, "get_position", "GetPosition"))
    return abs(cnh)


def _future_cnh_input(
    ref: Any,
    future_ref_builder: Callable[[str], Any],
    suffix: str,
    input_text: str,
) -> Optional[float]:
    """``_futureCnhInput()``：金额 → 期货合约数 → 折算成 CNH。

    .. code-block:: php

        $cny_ref = ($strSymbol != 'SZ161226') ? $ref->GetCnyRef() : new CnyReference('USCNY');
        $fCnh = $future_ref->GetVal() * $cny_ref->GetVal();
        if ($fRealtime = $ref->GetRealtimeNetValue()) {
            $fPremium = $ref->GetVal() / $fRealtime;
            $fCnh *= 1.0 + ($fPremium - 1.0) * $ref->GetPosition();   // 只补溢价里属于仓位的那部分
        }
        $fCnh *= $fMultiple * floatval(substr($strInput, 0, strlen($strInput) - 3));

    注意被注释掉的那行 ``$fCnh *= $fPremium;`` —— 作者**试过**全额乘溢价，
    最后改成按仓位加权。别看到注释就当废弃代码删掉，那个 1.0 的基准不能动。

    ## 期货价或中间价取不到时返回 ``None``（偏离 2 的一部分）

    PHP 那边取不到就是 ``floatval('') = 0``，于是**整页 23 行一起显示 0 股**，
    页面上没有任何"为什么"可看。这两个输入缺失时这一页毫无意义，所以本版按
    "算不出"处理，让符号进页脚清单、说明缺的是什么。
    """
    future_symbol = FUTURE_SYMBOL.get(suffix)
    if future_symbol is None or future_ref_builder is None:
        return None
    future_ref = future_ref_builder(future_symbol)
    if future_ref is None:
        return None

    symbol = str(_call(ref, "get_symbol", "GetSymbol") or "")
    cny_ref = _choose_cny_ref(ref, symbol)
    cny_value = _floatval(_call(cny_ref, "get_val", "GetVal"))
    future_value = _floatval(_call(future_ref, "get_val", "GetVal"))
    if not future_value or not cny_value:
        return None

    cnh = future_value * cny_value
    realtime = _call(ref, "get_realtime_net_value", "GetRealtimeNetValue")
    if _php_truthy(realtime):
        price = _floatval(_call(ref, "get_val", "GetVal"))
        premium = price / _floatval(realtime) if _floatval(realtime) else 0.0
        cnh *= 1.0 + (premium - 1.0) * _floatval(_call(ref, "get_position", "GetPosition"))

    contracts = _floatval(str(input_text)[:-3])
    cnh *= FUTURE_MULTIPLE[suffix] * contracts
    return abs(cnh)


def _choose_cny_ref(ref: Any, symbol: str) -> Any:
    """白银那一只用写死的 ``USCNY``，其余用本基金自己的中间价。"""
    if symbol == FORCE_USCNY_SYMBOL:
        return _build_cny_ref("USCNY", getattr(ref, "source", None))
    return _call(ref, "get_cny_ref", "GetCnyRef")


def _build_cny_ref(symbol: str, source: Any) -> Any:
    """``new CnyReference('USCNY')``；建不出来返回 ``None``。"""
    try:
        from ref.refobj import CnyReference

        return CnyReference(symbol, source=source)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 单行
# ---------------------------------------------------------------------------
def build_row(
    symbol: str,
    ref: Any,
    input_text: str,
    source: Any = None,
    fund_link: Callable[[str], str] = None,
    stock_link: Callable[[str], str] = None,
    future_ref_builder: Callable[[str], Any] = None,
) -> Tuple[Optional[TableRow], str]:
    """``_echoOverNightCnhItem()``；返回 ``(行, 算不出的原因)``。

    原因非空表示"这一行出了，但建议数量那一格空着"（偏离 2）——
    调用方把它收进页脚的说明清单。

    行结构（PHP 是"先压代码一格，再看有没有量"）::

        代码 | 价格 | 可买/卖量 | 建议数量 | 对冲值 | 建议对冲操作

    该方向没有挂单时只有**代码一格**，后面五格全不出 —— 变长行，照抄。
    """
    if ref is None:
        return None, "引用对象建不出来"

    stock_ref = stock_ref_of(ref)
    is_sell = is_sell_input(input_text)

    row = TableRow()
    row.add(symbol)
    if fund_link is not None:
        try:
            row.href = fund_link(symbol) or ""
        except Exception:
            row.href = ""

    quantity = _call(stock_ref, "get_available_quantity", "GetAvailableQuantity", is_sell)
    if not _php_truthy(quantity):
        return row, ""

    price_text = _call(stock_ref, "get_available_price", "GetAvailablePrice", is_sell)
    price = _floatval(price_text)
    row.add(_call(stock_ref, "get_price_display", "GetPriceDisplay", price) or "")
    row.add(str(quantity))

    reason = ""
    cnh = convert_cnh_input(ref, stock_ref, input_text, future_ref_builder)
    if cnh is None:
        reason = "缺少净值/汇率/价格，算不出数量"
        hint = None
        hedge_cell = ""
        memo = ""
    else:
        hint, hedge_cell, memo = _hint_and_memo(
            symbol, ref, source, cnh, price, stock_link
        )
        if hint is None:
            #: 仓位或本方向价格是 0 —— 那一步除法 PHP 会直接抛异常。
            reason = "缺少仓位/价格，算不出数量"

    if hint is None:
        row.add("")
    else:
        hint = php_round(hint / QUANTITY_LOT, 0) * QUANTITY_LOT
        text = _strval_number(hint)
        #: 装不进这一档 → 红字（PHP ``GetFontElement($strHintQuantity)``）。
        row.add((text, "red") if hint > _floatval(quantity) else text)

    row.add(hedge_cell)
    if memo:
        #: $ar[] = ($bSell ? '买入' : '卖出').$strMemo;  —— **方向是反的**：
        #: 卖本基金就要**买**对冲标的，所以卖的时候前缀是"买入"。
        row.html_indexes.append(len(row.cells))
        row.add(("买入" if is_sell else "卖出") + memo)
    return row, reason


def _hint_and_memo(
    symbol: str,
    ref: Any,
    source: Any,
    cnh: float,
    price: float,
    stock_link: Callable[[str], str] = None,
) -> Tuple[Optional[float], Any, str]:
    """``(建议股数, 对冲值那一格, "建议对冲操作"那一格)``。

    判据与 PHP 一样按"引用对象有没有某个方法"走，**顺序不能换**::

        if ($est_ref)                                // 有 GetStockRef 且建出了中间层
            → 对冲值口径
        else if (method_exists($ref, 'GetHoldingsDate'))
            → 按季报成分股逐只折股
        else
            → 两格都空

    注意 ``$est_ref`` 在"有 ``GetStockRef`` 但中间层建不出来"时也是假值，
    所以那种 QDII 会掉进第二支 —— 而它没有 ``GetHoldingsDate``，于是两格皆空。
    """
    position = _floatval(_call(ref, "get_position", "GetPosition"))
    if not position or not price:
        return None, "", ""

    hint = (cnh / position) / price

    est_ref = None
    if _has_method(ref, "get_stock_ref", "GetStockRef"):
        est_ref = _call(ref, "get_est_ref", "GetEstRef")

    if est_ref:
        hedge = _hedge_of(source, symbol, _call(ref, "get_stock_id", "GetStockId"))
        if not hedge:
            return hint, "", ""
        hedge_quantity = php_round(hint / hedge, 0)
        hint = hedge_quantity * hedge
        hedge_symbol = _hedge_symbol_of(est_ref, symbol)
        memo = "%s股%s" % (
            _strval_number(hedge_quantity),
            _link_html(hedge_symbol, stock_link),
        )
        return hint, number_display(hedge, 0), memo

    if _has_method(ref, "get_holdings_date", "GetHoldingsDate"):
        return hint, "", _holdings_memo(symbol, ref, source, hint, position, stock_link)

    return hint, "", ""


def _has_method(obj: Any, snake: str, camel: str) -> bool:
    """``method_exists($obj, 'GetXxx')`` 的等价物（snake / camel 任一个即可）。"""
    return callable(getattr(obj, snake, None)) or callable(getattr(obj, camel, None))


def _hedge_symbol_of(est_ref: Any, symbol: str) -> str:
    """``($strEtf = GetLeverageHedgeSymbol($strSymbol)) ? $strEtf : $est_ref->GetSymbol()``。"""
    from core.stockdata import get_leverage_hedge_symbol

    symbol_etf = get_leverage_hedge_symbol(symbol)
    if symbol_etf:
        return str(symbol_etf)
    return str(_call(est_ref, "get_symbol", "GetSymbol") or "")


def _holdings_memo(
    symbol: str,
    ref: Any,
    source: Any,
    hint: float,
    position: float,
    stock_link: Callable[[str], str] = None,
) -> str:
    """持仓型：按季报成分股**逐只**折成股数，拼成一串。

    .. code-block:: php

        $fCny = floatval($ref->GetNetValueString()) * $fHintQuantity * $fPos;
        $fUsd = $fCny / $cny_ref->GetVal($strDate);
        foreach ($ref->GetHoldingsRatioArray() as $strHoldingId => $strRatio) {
            if ($strClose = $his_sql->GetClose($strHoldingId, $strDate)) {
                $fHoldingQuantity = round($fUsd * floatval($strRatio) / 100.0 / floatval($strClose), 1);
                $fTotalQuantity += $fHoldingQuantity;
                $strMemo .= _buildHedgeString($fHoldingQuantity, $sql->GetStockSymbol($strHoldingId)).'、';
            }
        }
        $strMemo = rtrim($strMemo, '、');
        if ($strSymbol != 'SZ160216' && ...)	$strMemo .= '，共'.strval(round($fTotalQuantity)).'股。';

    两处照抄的怪处：

    * 汇率取的是**季报那天**的（``GetVal($strDate)``），不是今天；
    * 那句"，共N股。"是**无条件补**的 —— 哪怕一只都没折出来（演示库里
      持仓型基金全是 0 行），文案也会是"，共0股。"。看着像 bug，但这是
      PHP 的实际输出，改写会让"这一格出现/不出现"与生产端不一致。
    """
    date = _call(ref, "get_holdings_date", "GetHoldingsDate")
    ratios = _call(ref, "get_holdings_ratio_array", "GetHoldingsRatioArray") or {}
    if not date or not ratios:
        return _total_suffix(symbol, 0.0)

    net_value = _floatval(_call(ref, "get_net_value_string", "GetNetValueString"))
    cny_ref = _call(ref, "get_cny_ref", "GetCnyRef")
    rate = _floatval(_call(cny_ref, "get_val", "GetVal", date))
    if not rate:
        #: 汇率是分母 —— PHP 在 ``$fCny / $cny_ref->GetVal($strDate)`` 这里除零。
        return _total_suffix(symbol, 0.0)

    usd = net_value * hint * position / rate
    history = _history_sql(source)
    if history is None:
        return _total_suffix(symbol, 0.0)

    parts: List[str] = []
    total = 0.0
    for holding_id, ratio in ratios.items():
        close = _history_close(history, holding_id, date)
        if not close:
            continue
        quantity = php_round(usd * _floatval(ratio) / 100.0 / close, 1)
        total += quantity
        holding_symbol = str(_call(source, "get_stock_symbol", "GetStockSymbol", holding_id) or "")
        parts.append(
            "%s股%s" % (_strval_number(quantity), _link_html(holding_symbol, stock_link))
        )
    return "、".join(parts) + _total_suffix(symbol, total)


def _total_suffix(symbol: str, total: float) -> str:
    """"，共N股。" —— 四个特例符号不补（见 :data:`NO_TOTAL_SYMBOLS`）。"""
    if symbol in NO_TOTAL_SYMBOLS:
        return ""
    return "，共%s股。" % _strval_number(php_round(total, 0))


def _history_sql(source: Any) -> Any:
    """``GetStockHistorySql()``：借数据源上的连接建一个（取不到返回 ``None``）。"""
    connection = getattr(source, "connection", None)
    if connection is None:
        return None
    try:
        from sql.stockhistory import StockHistorySql

        return StockHistorySql(connection)
    except Exception:
        return None


def _history_close(history: Any, stock_id: Any, date: Any) -> float:
    """``$his_sql->GetClose($strHoldingId, $strDate)``：精确按日期取，取不到 0。"""
    try:
        return _floatval(history.get_close(stock_id, date))
    except Exception:
        return 0.0


def _hedge_of(source: Any, symbol: str, stock_id: Any) -> float:
    """``GetStockHedge($strSymbol, $strStockId)``（``php/stock.php:401``）。

    与轮动页同一个口子：数据源上没有这个方法时给 ``1.0``
    （PHP "两边都取不到校准值"的兜底）。
    """
    if source is None or not symbol:
        return 1.0
    getter = getattr(source, "get_stock_hedge", None)
    if getter is None:
        return 1.0
    try:
        return float(getter(symbol, stock_id))
    except Exception:
        return 1.0


def _link_html(symbol: str, link_builder: Callable[[str], str]) -> str:
    """一格里的链接片段（PHP ``GetMyStockLink($strSymbol)``）。

    PHP 那边直接拼 ``<a href=...>`` 进单元格；本版把 URL 交给调用方注入
    （只有路由层知道 ``url_for``），拿不到构造器时退回纯文本符号。
    """
    if not symbol:
        return ""
    if link_builder is None:
        return symbol
    try:
        url = link_builder(symbol)
    except Exception:
        url = ""
    if not url:
        return symbol
    return '<a href="%s">%s</a>' % (url, symbol)


# ---------------------------------------------------------------------------
# 整页
# ---------------------------------------------------------------------------
def build_columns(prefix: str, layout_width: int = 0) -> List[Dict[str, Any]]:
    """六列表头（``overnightcnhcn.php:142-147``）。

    最后一列 ``new TableColumn($strHint.'对冲操作', TableColumnGetLastWidth($ar))``
    吃剩余宽度 —— 这里由 ``layout_width`` 显式传入，不猜屏幕宽度。
    """
    columns = [
        column_symbol(),
        column_price(prefix),
        column_quantity(prefix),
        column_quantity("建议"),
        column_hedge(),
    ]
    width = table_column_get_last_width(columns, layout_width) if layout_width else 0
    columns.append(column_base(MEMO_COLUMN_TEXT, width))
    return columns_to_dicts(columns)


def build_page(
    input_text: Any = None,
    symbols: Sequence[str] = (),
    refs: Sequence[Any] = (),
    source: Any = None,
    fund_link: Callable[[str], str] = None,
    stock_link: Callable[[str], str] = None,
    future_ref_builder: Callable[[str], Any] = None,
    layout_width: int = 0,
) -> Dict[str, Any]:
    """装配整页。

    ==================  ==================================================
    返回键               含义
    ==================  ==================================================
    ``input``           输入框回显
    ``direction``       ``{"is_sell", "op", "prefix"}``
    ``table``           表格；``None`` = 只出表单（输入为空）
    ``missing``         ``[{"symbol","reason"}]`` —— 算不出的符号（偏离 2）
    ==================  ==================================================

    ``refs`` 与 ``symbols`` **必须等长同序**：行序就是符号表顺序，建不出来的
    位置放 ``None``（服务层跳过并记入 ``missing``）。建对象要连库，由路由层做。
    """
    text = parse_input(input_text)
    is_sell = is_sell_input(text)
    page: Dict[str, Any] = {
        "title": OVERNIGHT_CNH_DISPLAY,
        "input": text,
        "direction": {
            "is_sell": is_sell,
            "op": "卖" if is_sell else "买",
            "prefix": "可" + ("卖" if is_sell else "买"),
        },
        "table": None,
        "missing": [],
    }
    if text == "":
        #: 点"清除" → PHP 只出表单（`if ($strInput != '')` 为假）。
        return page

    rows: List[TableRow] = []
    missing: List[Dict[str, str]] = []
    for index, symbol in enumerate(symbols):
        ref = refs[index] if index < len(refs) else None
        row, reason = build_row(
            symbol,
            ref,
            text,
            source=source,
            fund_link=fund_link,
            stock_link=stock_link,
            future_ref_builder=future_ref_builder,
        )
        if row is None:
            missing.append({"symbol": symbol, "reason": reason})
            continue
        if reason:
            missing.append({"symbol": symbol, "reason": reason})
        rows.append(row)

    page["missing"] = missing
    page["table"] = {
        "id": "%s-table" % INPUT_QUERY_ITEM,
        "title": OVERNIGHT_CNH_DISPLAY,
        "columns": build_columns(page["direction"]["prefix"], layout_width),
        "rows": rows,
        "count": len(rows),
    }
    return page


#: 三个"点一下换个口径"的快捷入口（``EchoAll()`` 里那三个 ``CopyPhpLink``）。
def input_presets(current: str = "") -> List[Dict[str, str]]:
    """``MCL对冲`` / ``MGC对冲`` / ``基金申购`` 三个链接的目标值。

    PHP 是 ``CopyPhpLink($strQuery.'1MCL', 'MCL对冲')`` —— ``$strQuery`` 已经带了
    ``overnightcnh=``，所以目标值就是 ``1MCL`` 这三个字面量（``1`` 桶/1 盎司）。
    """
    return [
        {"text": "MCL对冲", "value": "1MCL"},
        {"text": "MGC对冲", "value": "1MGC"},
        {"text": "基金申购", "value": "100000F"},
    ]


def overnight_symbols() -> List[str]:
    """``GetOverNightSymbolArray()``（``php/stock/stocksymbol.php:382``）：23 只。"""
    return list(get_over_night_symbol_array())


def build_fund_reference(symbol: str, source: Any = None) -> Any:
    """``StockGetFundReference($strSymbol)``（``php/stock.php:315``）。

    .. code-block:: php

        if ($ref = StockGetQdiiReference($strSymbol))                                return $ref;
        else if (in_arrayQdiiMix($strSymbol))                                        return new HoldingsReference($strSymbol);
        else if (in_arrayChinaIndex($strSymbol) || in_arrayChinaFuture($strSymbol))  return new FundPairReference($strSymbol);
        return new FundReference($strSymbol);

    ## 为什么不用现成的 ``StockGetHoldingsReference`` / ``StockGetFundPairReference``

    那两个都带**"表里有没有行"的闸门**（``SqlCountHoldings() > 0`` /
    ``fundpair`` 有记录），而 PHP 这里用的是**裸 ``new``**。演示库里 8 只
    ``QdiiMix`` 基金一行持仓都没有、``SZ161226`` 连 ``stock`` 行都没有 ——
    走闸门版会整批返回 ``None``，页面上直接少 9 行；而 PHP 那边照样建对象，
    数据不全只体现在"某几格是空的"。所以这里照 PHP 直接构造。

    建不出来（没有数据源、类构造抛异常）时返回 ``None``，由调用方记进
    ``missing``。
    """
    if source is None:
        return None

    from core.stocksymbol import (
        in_array_china_future,
        in_array_china_index,
        in_array_qdii_mix,
    )

    try:
        from ref.holdingsref import HoldingsReference
        from ref.refobj import FundPairReference, FundReference, build_qdii_reference
        from core.stocksymbol import (
            in_array_qdii,
            in_array_qdii_eu,
            in_array_qdii_hk,
            in_array_qdii_jp,
        )

        if (
            in_array_qdii(symbol)
            or in_array_qdii_hk(symbol)
            or in_array_qdii_jp(symbol)
            or in_array_qdii_eu(symbol)
        ):
            return build_qdii_reference(symbol, source=source)
        if in_array_qdii_mix(symbol):
            return HoldingsReference(symbol, source=source)
        if in_array_china_index(symbol) or in_array_china_future(symbol):
            return FundPairReference(symbol, source=source)
        return FundReference(symbol, source=source)
    except Exception:
        return None


def build_future_reference(symbol: str, source: Any = None) -> Any:
    """``new MyStockReference($strSymbol)``（``MCL``/``MGC`` 两支用）。

    期货引用只要一个"现价"，本地用 :class:`ref.refobj.MysqlReference` 顶上
    —— 它读的是 ``realtimequote`` 快照，与 daemon 抓回来的那份同源。
    建不出来返回 ``None``（页面上那几行就会标成"算不出"）。
    """
    if source is None:
        return None
    try:
        from ref.refobj import MysqlReference

        return MysqlReference(symbol, source=source)
    except Exception:
        return None
