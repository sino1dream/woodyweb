"""五档交易表**最后一列**：中间层标的与杠杆标的的换算价。

对应 ``woody/res/php/_qdiigroup.php:5`` 的 ``_tradingUserDefined()``，
由同文件 ``:96`` 的 ``EchoFundTradingParagraph($ref, '_tradingUserDefined')``
挂到 ``php/ui/tradingparagraph.php`` 那张五档表上。

生产端 ``sz161125cn.php`` 实测的表头与数据行::

    <th width=198><b><font color=maroon>^GSPC</font></b>|<b><font color=maroon>SH</font></b>|<b><font color=maroon>SPY</font></b><b><font color=blue>价格</font></b></th>
    <td><font color=red>8996.19</font>|<font color=green>26.98</font>|<font color=red>896.51</font></td>

一句话讲清它在干什么：**把这一档基金价格换成"基金经理看到的那些标的"的价格**。
同样一张表，左边三列是"我这基金现在多少钱"，这一列是"对应的指数 / 可替代 ETF
现在多少钱"，用来判断该挂哪一档。

## 一个 callable 干两件事

``TradingService.fund_table(fund, callback)`` 的回调契约（见
``trading_service._table`` / ``_make_row``）：

* **不传参** —— 返回**列头**文本；
* **传价格** —— 返回这一行那一格的文本。

PHP 靠 ``call_user_func($callback)`` 与 ``call_user_func($callback, $strPrice)``
两种写法区分，本版用一个带默认值的参数表达同一件事。

## PHP 原文

.. code-block:: php

    if ($strVal)
    {
        if ($strVal == '0')  { return ''; }
        else
        {
            $fEst = $fund->GetEstValue($strVal);
            $strLev = '';
            foreach ($arLev as $leverage_ref)
            {
                $fLev = $leverage_ref->EstFromPair($fEst);
                $strLev .= '|'.$leverage_ref->GetPriceDisplay($fLev);
            }
            return $est_ref->GetPriceDisplay($fEst).$strLev;
        }
    }
    $strLev = '';
    foreach ($arLev as $leverage_ref) { $strLev .= '|'.TableColumnGetStock($leverage_ref); }
    return TableColumnGetStock($est_ref).$strLev.TableColumnGetPrice();

## 三处关键点

1. **``$strVal == '0'`` 那个分支是死代码**。PHP 里字符串 ``'0'`` **是假值**，
   ``if ($strVal)`` 已经把它挡在外面。本版不抄它 —— 抄了反而要写注释解释
   "这一行永远不会执行"。上游 ``_make_row`` 的 ``if (empty($strPrice) == false)``
   同样会把 ``'0'`` 滤掉，两边行为一致。
2. **``GetEstValue`` 是 ``GetQdiiValue`` 的逆**：基金净值口径 → 中间层点位。
   它由 :class:`refobj.QdiiReference` 提供，**不能再写一遍换算公式**。
3. **``EstFromPair`` 认的是"我跟踪的那个标的的价格"**。杠杆 ETF（``SPY`` /
   ``QQQ`` / ``TQQQ``）跟踪的是估值标的（``^GSPC`` / ``^NDX``），所以刚刚算出的
   ``$fEst`` 正好是 ``EstFromPair`` 的入参。公式在 :mod:`pairestimate`，
   本模块只负责把它串起来。

## 已知偏差

* **表头与格子里各段的分色没照搬**。PHP 那列在同一格里塞了 maroon 的标的名与
  blue 的"价格"，每个数字还各自红/绿；本项目的列与格都只有「一个文本 + 一个颜色」
  （见 ``web/templates/_data_table.html``），其中一个格子是 dot-all 的。
  所以这里返回**纯文本** ``^GSPC|SH|SPY价格`` / ``8996.19|26.98|896.51``，
  与 :mod:`trading_service` 里"官方/参考/实时"三档估值那格的做法一致
  （那边也是 ``|`` 拼成的纯文本）。
* **列宽不用 PHP 的 ``strlen()*11``**（生产端算出来是 198）。理由见
  ``trading_service`` 模块 docstring 第四节：CSS 才是真正的宽度决策者，
  而 PHP 的 ``strlen()`` 按字节数，混排中文时会偏窄三重。这里按向下兼容的固定值。
* **``CnyReference`` 那张地区对照表没迁**。PHP ``MyPairReference`` 构造时会按
  本体与配对标的的地区组合挑一个汇率引用。本模块目前要处理的 QDII 场景是
  "美股 ETF 跟踪美股指数" —— PHP 在这条分支上**不建** ``cny_ref``、
  ``GetDefaultCny()`` 得 1.0，本版同样不给 ``cny_ref``、同样得 1.0，结果一致。
  将来若要支持跨境配对（如 A 股基金跟踪港股标的），得把那张对照表补进来。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from web.bootstrap import install_paths

install_paths()

from pairestimate import est_from_pair  # noqa: E402


#: ``TableColumnGetPrice()`` 那段后缀。单独提常量是因为表头拼接里要用，
#: 而它与 :data:`LEVERAGE_SEPARATOR` 一起决定了表头长什么样。
PRICE_COLUMN_TEXT = "价格"

#: 各标的之间的分隔符。PHP 里在两处循环各写一个字面 ``'|'``，这里集中一次 ——
#: 少分歧也少一处能改漏的地方。
LEVERAGE_SEPARATOR = "|"

#: 这一列的宽度。见模块 docstring「已知偏差」第二条：不是 PHP 的 strlen()*11。
PAIR_ESTIMATE_WIDTH = 200


# ---------------------------------------------------------------------------
# 回调本体
# ---------------------------------------------------------------------------
def build_trading_column(fund: Any, leverage_refs: Any = None) -> Callable[..., Any] | None:
    """返回 ``_tradingUserDefined`` 的同构 callable；**没有中间层标的就返回 ``None``**。

    ``fund`` 必须是**基金引用对象**（``refobj.QdiiReference``），因为它要提供
    ``GetEstRef()`` 与 ``GetEstValue()`` —— 传成纯行情引用会拿不到中间层标的。

    ``leverage_refs`` 是 :func:`build_leverage_references` 的结果；``None`` 或空
    表时不报错，只是这一列只有中间层标的那一格（``^GSPC价格``）。
    """
    est_ref = _invoke(fund, "get_est_ref", "GetEstRef")
    if est_ref is None:
        # PHP 里 ``$fund->GetEstRef()`` 恒返回一个对象，但本项目的引用对象是
        # 逐步迁移的：**没有中间层标的就不补这一列**，而不是补一列空数据。
        return None
    refs = [ref for ref in (leverage_refs or ()) if ref is not None]

    def column(price: Any = False) -> str:
        # PHP: if ($strVal) —— **假值一律走表头分支**。Python 的 '0' 是真值，
        # 所以这里必须过 _php_truthy，写成 `if price:` 会把价格 0 当成"要表头"。
        if not _php_truthy(price):
            return column_header(est_ref, refs)
        return estimate_cell(fund, est_ref, refs, price)

    column.__name__ = "_tradingUserDefined"
    return column


#: PHP 原名。按 AGENTS.md 第 3 条保留 camelCase 别名。
tradingUserDefined = build_trading_column


def column_header(est_ref: Any, leverage_refs: Sequence[Any] = ()) -> str:
    """表头：``^GSPC|SH|SPY价格``。

    PHP 是 ``TableColumnGetStock($est_ref).$strLev.TableColumnGetPrice()``。
    注意 ``$strLev`` 每段自带前导 ``'|'``，**但 ``价格`` 前面没有** ——
    生产端实测就是连着写的，别顺手补成 ``^GSPC|SH|SPY|价格``。
    """
    parts = [_display_of(est_ref)]
    for ref in leverage_refs:
        parts.append(LEVERAGE_SEPARATOR + _display_of(ref))
    return "".join(parts) + PRICE_COLUMN_TEXT


def estimate_cell(fund: Any, est_ref: Any, leverage_refs: Sequence[Any], price: Any) -> str:
    """一格：换算价按 ``|`` 拼起来。

    ``fund->GetEstValue($strVal)`` → ``fEst``，然后中间层自己一份、
    每个杠杆标的一份（``EstFromPair``），全部过各自的 ``GetPriceDisplay()``。
    """
    est_value = _invoke(fund, "get_est_value", "GetEstValue", price)
    parts = [_display_price(est_ref, est_value)]
    for ref in leverage_refs:
        parts.append(_display_price(ref, est_from_pair(ref, est_value)))
    return LEVERAGE_SEPARATOR.join(parts)


# ---------------------------------------------------------------------------
# 杠杆标的：MyPairReference 的最小替身
# ---------------------------------------------------------------------------
def build_leverage_references(est_symbol: Any, source: Any) -> list:
    """查 ``fundpair`` 建出 PHP ``$arLev``（``_qdiigroup.php:69-82``）。

    PHP 那里是 ``new FundPairReference($strSymbol)``，**该类未迁移**
    （它是除这两个方法还管 ``DailyCalibration()`` 的一整个类）。本函数只补齐
    ``_tradingUserDefined`` 真正用到的那三样：

    * **行情** —— ``GetPriceDisplay()`` 需要现价当"昨收"来比色、也需要精度，
      所以复用 ``reference_service.build_stock_references``（先读快照再兜底联网）；
    * **校准值 ``fFactor``** —— ``EstFromPair`` 的核心参数；
    * **``fLastCalibrationVal``** —— ``FundAdjustPosition`` 的混合基准。

    顺序照 PHP 的 ``ORDER BY symbol``（``reference_service._LEVERAGE_SQL``），
    因为表头与格子都按这个顺序拼接。
    """
    if not est_symbol or source is None:
        return []

    from web.services.reference_service import (
        _leverage_symbols,
        build_stock_references,
    )

    symbols = _leverage_symbols(est_symbol, source)
    if not symbols:
        return []

    refs = build_stock_references(symbols, source=source)
    for ref in refs:
        _load_pair_calibration(ref, source)
    return refs


def _load_pair_calibration(ref: Any, source: Any) -> None:
    """``MyPairReference::LoadCalibration()``（``fundpairref.php:43``）的最小等价物。

    .. code-block:: php

        if ($record = $cal_sql->GetRecordNow($strStockId))
        {
            $this->fFactor = floatval($record['close']);
            $this->fLastCalibrationVal = $sql->ReadVal($strStockId);
            if ($this->fLastCalibrationVal === false)
                $this->fLastCalibrationVal = floatval(SqlGetNetValueByDate($strStockId, $record['date']));
        }
        else    $this->fFactor = 1.0 / $this->GetPosition();

    **失败一律不管**：少了校准值时 ``pairestimate.est_from_pair`` 会退化成
    ``factor = 1 / position``（默认值见该函数），结果是"算得粗糙"而不是"整页崩"。
    """
    try:
        stock_id = _stock_id_of(ref, source)
        record = source.get_calibration_now(stock_id)
        if record is None:
            return
        close = getattr(record, "close", None)
        if close in (None, ""):
            return
        ref.factor = float(close)
        last = source.get_last_calibration(stock_id)
        if last is None or last is False:
            # PHP 的兜底是"校准那天的净值"，不是因子本身
            last = source.get_netvalue_close(stock_id, getattr(record, "date", None))
        if last is not None and last is not False:
            ref.last_calibration_val = last
    except Exception:
        # 校准值查不到就用默认仓位 —— 见本模块 docstring「已知偏差」外的另一条：
        # 这个是 page-level 的补偿，不该让它把整张表拖没
        pass


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _invoke(ref: Any, snake: str, camel: str, *args: Any) -> Any:
    """按 snake_case 优先、camelCase 兜底调用；取不到返回 ``None``。

    与其它四个 ``_invoke`` 同款写法（每个模块各留一份），
    理由是**假对象也有可能只有 camelCase**（本模块要同时接受 QDII 引用对象、
    股票引用对象和测试替身）。
    """
    if ref is None:
        return None
    method = getattr(ref, snake, None)
    if method is None:
        method = getattr(ref, camel, None)
    if method is None or not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def _display_of(ref: Any) -> str:
    """``TableColumnGetStock($ref)`` → ``$ref->GetDisplay()``。"""
    value = _invoke(ref, "get_display", "GetDisplay")
    if value in (None, ""):
        value = _invoke(ref, "get_symbol", "GetSymbol")
    return "" if value is None else str(value)


def _display_price(ref: Any, value: Any) -> str:
    """``$ref->GetPriceDisplay($fVal)`` 的文本部分。

    **只传一个参数**是与 PHP 一致的故意行为：``GetPriceDisplay`` 的第二个参数
    缺省时取 ref 自己的现价当对比基准（``php/stock/stockref.php:162``）。
    """
    text = _invoke(ref, "get_price_display", "GetPriceDisplay", value)
    return "" if text is None else str(text)


def _stock_id_of(ref: Any, source: Any) -> Any:
    """优先用 ref 自带的 ``stock_id``；没有再按符号查库。"""
    stock_id = getattr(ref, "stock_id", None)
    if stock_id:
        return stock_id
    symbol = _invoke(ref, "get_symbol", "GetSymbol")
    if not symbol:
        return None
    try:
        return source.get_stock_id(str(symbol))
    except Exception:
        return None


def _php_truthy(value: Any) -> bool:
    """PHP 语境下的"真值"：与 Python 唯一的区别是**字符串 ``'0'``**。

    PHP 判假、Python 判真。``_tradingUserDefined`` 开头那句 ``if ($strVal)``
    正踩在这上面 —— 写成 ``if price:``，价格恰好是 ``'0'`` 时会返回表头文本。
    """
    if isinstance(value, str):
        return value not in ("", "0")
    return bool(value)
