"""配对标的的双向估算 —— ``MyPairReference`` 的``EstFromPair`` / ``EstToPair``。

对应 ``php/stock/fundpairref.php:84``。

## 为什么只迁这两个方法

``MyPairReference``（``php/stock/fundpairref.php:3``）本体还没迁，它除了这两个
方法还管着 ``pair_ref`` / ``cny_ref`` 的构造与校准装载（``LoadCalibration()``）。
但 ``smaparagraph`` 的四个回调里有**两个**只需要这两个公式：

.. code-block:: php

    function _callbackFundPairSma($ref, $strEst = false) { return $strEst ? $ref->EstFromPair($strEst) : $ref; }
    function _callbackAhPairSma($ref, $strEst = false)   { return $strEst ? $ref->EstToPair($strEst)   : $ref->GetPairRef(); }

所以这里把它们抽成**自由函数 + 鸭子类型**：认 ref 身上有没有
``pair_ref`` / ``cny_ref`` / ``factor`` / ``position``，有就算，没有就按 PHP
的默认值退化。等 ``MyPairReference`` 整体迁过来，把这两个函数挂成方法即可，
调用方（``web/services/sma_service.py``）不用改。

## 公式本体逐句对照

.. code-block:: php

    function EstFromPair($fPairVal = false, $fCny = false)
    {
    	if ($fPairVal == false)	$fPairVal = floatval($this->pair_ref->GetPrice());
    	if ($fCny == false)		$fCny = $this->GetDefaultCny();
    	if ($this->IsSymbolA())	$fVal = QdiiGetVal($fPairVal, $fCny, $this->fFactor);
    	else					$fVal = ($fPairVal / $fCny) / $this->fFactor;
    	return FundAdjustPosition($this->GetPosition(), $fVal,
    	                          ($this->fLastCalibrationVal ? $this->fLastCalibrationVal : $fVal));
    }

三个容易弄错的地方：

1. **``== false`` 是 PHP 的宽松比较**：``0.0`` / ``'0'`` / ``''`` 都为真命中，
   所以这里走 :func:`_is_php_false` 而不是 ``is False``。写错的话"价格正好是 0"
   会被当成"没给参数"而去读现价 —— 不报错，只是数不对；
2. **A 股与非 A 股走不同的式子**：A 股是 ``QdiiGetVal(价, 汇, 校准)``
   （``价 * 汇 / 校准``），非 A 股是 ``(价 / 汇) / 校准``。
   两者**互为倒数方向**，抄反了结果会差一个平方级；
3. **第三个参数是"上一次的校准值"，缺省才是新算出来的值**。
   这一步是 ``FundAdjustPosition`` 的"仓位调整"，把估值按仓位混合回旧值。

``EstToPair`` 是它的逆：先 ``FundReverseAdjustPosition`` 还原，
再 ``QdiiGetPeerVal``（A 股）或 ``* 校准``（非 A 股）。

## 已知偏差

* **除零**：PHP 会得到 ``INF`` 并继续画出来；本版返回 ``None`` 让那一格空着。
  校准值为 0 属于脏数据，摆一个 ``INF`` 在页面上更糟。
* ``fLastCalibrationVal`` 取自 ref 的 ``get_last_calibration_val()``；
  取不到时按 PHP 的 ``?: $fVal`` 退化。
"""

from __future__ import annotations

from typing import Any

from fundcalc import (
    fund_adjust_position,
    fund_reverse_adjust_position,
    qdii_get_peer_value,
    qdii_get_value,
)


def est_from_pair(ref: Any, pair_val: Any = False, cny: Any = False) -> Any:
    """``EstFromPair($fPairVal, $fCny)``：由**配对标的的价格**反推本标的估值。

    ``ref`` 为 ``None`` 直接返回 ``None`` —— PHP 里 ``$this`` 恒不为 null，
    所以这不改变任何 PHP 语义，只是给"调用方传空"定个清楚的口径
    （否则会退化成一串默认值，算出一个看着合理但没有来源的数）。
    """
    if ref is None:
        return None
    pair_price = _pair_price_of(ref) if _is_php_false(pair_val) else _floatval(pair_val)
    if _is_php_false(cny):
        cny = get_default_cny(ref)

    factor = _factor_of(ref)
    if factor in (None, 0, 0.0) or not cny:
        return None

    if _is_symbol_a(ref):
        value = qdii_get_value(pair_price, float(cny), float(factor))
    else:
        value = (pair_price / float(cny)) / float(factor)
    return fund_adjust_position(_position_of(ref), value, _last_calibration_of(ref, value))


def est_to_pair(ref: Any, my_val: Any = False, cny: Any = False) -> Any:
    """``EstToPair($fMyVal, $fCny)``：由**本标的的价格**推配对标的的价格。

    ``ref`` 为 ``None`` 同样返回 ``None``（理由见 :func:`est_from_pair`）。
    """
    if ref is None:
        return None
    my_price = _own_price_of(ref) if _is_php_false(my_val) else _floatval(my_val)
    if _is_php_false(cny):
        cny = get_default_cny(ref)

    factor = _factor_of(ref)
    if factor in (None, 0, 0.0):
        return None

    value = fund_reverse_adjust_position(
        _position_of(ref), my_price, _last_calibration_of(ref, my_price))
    if _is_symbol_a(ref):
        return qdii_get_peer_value(value, float(cny), float(factor))
    return (value * float(cny)) * float(factor)


def get_default_cny(ref: Any, date: Any = False) -> float:
    """``GetDefaultCny($strDate)``（``:75``）：汇率引用的值，没有就 1.0。

    .. code-block:: php

        if ($this->cny_ref) {
            if (method_exists($this->cny_ref, 'GetVal')) return $this->cny_ref->GetVal($strDate);
        }
        return 1.0;

    **没有汇率引用时是 1.0** —— 这决定了非 QDII 标的也能走同一套公式
    （汇率 = 1 就是"不涉及换算"）。
    """
    cny_ref = _attribute_of(ref, "cny_ref", "GetCnyRef")
    if cny_ref is None or cny_ref is False:
        return 1.0
    value = _call(cny_ref, "get_val", "GetVal", date)
    if value is None or value is False or value == "":
        return 1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


# ---------------------------------------------------------------------------
# 从 ref 上取依赖：一律鸭子类型，缺什么按 PHP 的缺省值退化
# ---------------------------------------------------------------------------
def _pair_price_of(ref: Any) -> float:
    """``floatval($this->pair_ref->GetPrice())``。"""
    pair_ref = _attribute_of(ref, "pair_ref", "GetPairRef")
    value = _call(pair_ref, "get_price", "GetPrice")
    return _floatval(value)


def _own_price_of(ref: Any) -> float:
    """``floatval($this->GetPrice())``。"""
    return _floatval(_call(ref, "get_price", "GetPrice"))


def _factor_of(ref: Any) -> Any:
    """``$this->fFactor``（校准值）。

    ``LoadCalibration()`` 里没有校准记录时会退化成 ``1.0 / 仓位``
    （``fundpairref.php:61``），这里照做 —— 不退化会让新标的的估值恒为空。
    """
    factor = _attribute_of(ref, "factor", "GetFactor")
    if factor is None or factor is False or factor == "":
        position = _position_of(ref)
        return 1.0 / position if position else None
    try:
        return float(factor)
    except (TypeError, ValueError):
        return None


def _position_of(ref: Any) -> float:
    """``GetPosition()``（仓位）。缺省 1.0 —— 与 PHP 的默认仓位一致。"""
    position = _call(ref, "get_position", "GetPosition")
    try:
        return float(position)
    except (TypeError, ValueError):
        return 1.0


def _last_calibration_of(ref: Any, fallback: float) -> float:
    """``$this->fLastCalibrationVal ? $this->fLastCalibrationVal : $fVal``。

    PHP 里它在 ``LoadCalibration()`` 中从 ``lastcalibration`` 表读出；
    本版从 ref 上取（``get_last_calibration_val()``），取不到就用本次的值。
    """
    value = _call(ref, "get_last_calibration_val", "GetLastCalibrationVal")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    # PHP 的 `?:` 判的是假值：0 与 0.0 都会被跳过，这里同口径
    return fallback if number == 0 else number


def _is_symbol_a(ref: Any) -> bool:
    """``IsSymbolA()``。"""
    method = getattr(ref, "is_symbol_a", None) or getattr(ref, "IsSymbolA", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            return False
    return False


def _is_php_false(value: Any) -> bool:
    """PHP 的 ``$x == false``（**宽松**比较）。

    命中范围是：``false`` / ``null`` / ``''`` / ``'0'`` / ``0`` / ``0.0``。
    写成 ``value is False`` 会让"价格刚好是 0"被误判成"没传参数"。
    """
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return value.strip() in ("", "0", "0.0")
    if isinstance(value, (int, float)):
        return float(value) == 0.0
    return False


def _floatval(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _attribute_of(ref: Any, snake: str, camel: str) -> Any:
    """先按属性取，没有再按 camelCase 无参方法取（PHP 里常常两者都有）。"""
    if ref is None:
        return None
    if snake.startswith("cny_") or snake.startswith("pair_") or snake == "factor":
        value = getattr(ref, snake, None)
        if value is not None:
            return value
    getter = getattr(ref, camel, None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def _call(ref: Any, snake: str, camel: str, *args: Any) -> Any:
    if ref is None:
        return None
    method = getattr(ref, snake, None) or getattr(ref, camel, None)
    if not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None
