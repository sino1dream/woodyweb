from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from stocksymbol import (
    qdii_eu_get_dax_symbol_array,
    qdii_get_qqq_match_array,
    qdii_get_spy_match_array,
    qdii_get_symbol_array,
    qdii_get_xbi_symbol_array,
    qdii_get_xop_symbol_array,
    qdii_hk_get_h_shares_symbol_array,
    qdii_hk_get_hang_seng_symbol_array,
    qdii_hk_get_tech_symbol_array,
    qdii_jp_get_nky_symbol_array,
)


# The QDII symbol groups live in stocksymbol.py (ported from
# php/stock/stocksymbol.php) because several callers outside the calculator need
# them. They stay re-exported here as tuples so the historical fundcalc API and
# the identity checks below keep working unchanged.
QDII_QQQ_MATCH_SYMBOLS = tuple(qdii_get_qqq_match_array())
QDII_SPY_MATCH_SYMBOLS = tuple(qdii_get_spy_match_array())
QDII_XOP_SYMBOLS = tuple(qdii_get_xop_symbol_array())
QDII_XBI_SYMBOLS = tuple(qdii_get_xbi_symbol_array())

QDII_HK_TECH_SYMBOLS = tuple(qdii_hk_get_tech_symbol_array())
QDII_HK_HSHARES_SYMBOLS = tuple(qdii_hk_get_h_shares_symbol_array())
QDII_HK_HANGSENG_SYMBOLS = tuple(qdii_hk_get_hang_seng_symbol_array())
QDII_JP_NKY_SYMBOLS = tuple(qdii_jp_get_nky_symbol_array())
QDII_EU_DAX_SYMBOLS = tuple(qdii_eu_get_dax_symbol_array())


def fund_adjust_position(ratio: float, value: float, old_value: float) -> float:
    """Python port of PHP FundAdjustPosition."""
    return ratio * value + (1.0 - ratio) * old_value


def fund_reverse_adjust_position(ratio: float, value: float, old_value: float) -> float:
    """Python port of PHP FundReverseAdjustPosition."""
    return value / ratio - old_value * (1.0 / ratio - 1.0)


def qdii_get_calibration(est_value: float, cny: float, net_value: float) -> float:
    return est_value * cny / net_value


def qdii_get_value(est_value: float, cny: float, calibration: float) -> float:
    return est_value * cny / calibration


def qdii_get_peer_value(qdii_value: float, cny: float, calibration: float) -> float:
    return qdii_value * calibration / cny


def qdii_qqq_symbols() -> tuple[str, ...]:
    return QDII_QQQ_MATCH_SYMBOLS + ("SH513300",)


def qdii_spy_symbols() -> tuple[str, ...]:
    return QDII_SPY_MATCH_SYMBOLS + ("SZ159655",)


def qdii_symbols() -> tuple[str, ...]:
    """All QDII symbols, delegating to the stocksymbol group builder."""
    return tuple(qdii_get_symbol_array())


def qdii_est_symbol_map() -> dict[str, str]:
    mapping = {
        "SH501300": "AGG",
        "SH513290": "IBB",
        "SH513400": "^DJI",
        "SZ160140": "VNQ",
        "SZ160416": "IXC",
        "SZ161126": "RSPH",
        "SZ161128": "VGT",
        "SZ162415": "XLY",
        "SZ162719": "IEO",
        "SZ164906": "KWEB",
    }
    mapping.update(dict.fromkeys(QDII_XOP_SYMBOLS, "XOP"))
    mapping.update(dict.fromkeys(QDII_XBI_SYMBOLS, "XBI"))
    mapping.update(dict.fromkeys(qdii_spy_symbols(), "^GSPC"))
    mapping.update(dict.fromkeys(qdii_qqq_symbols(), "^NDX"))
    return mapping


def qdii_est_symbol(symbol: str) -> str | None:
    return qdii_est_symbol_map().get(symbol)


def qdii_symbols_for_est(est_symbol: str) -> list[str]:
    return [symbol for symbol, est in qdii_est_symbol_map().items() if est == est_symbol]


def qdii_hk_est_symbol(symbol: str) -> str | None:
    if symbol == "SH501025":
        return "SH000869"
    if symbol in QDII_HK_TECH_SYMBOLS:
        return "^HSTECH"
    if symbol in QDII_HK_HANGSENG_SYMBOLS or symbol == "SZ161124":
        return "^HSI"
    if symbol in QDII_HK_HSHARES_SYMBOLS:
        return "^HSCE"
    return None


def qdii_jp_est_symbol(symbol: str) -> str | None:
    if symbol == "SH513800":
        return "znb_TPX"
    if symbol in QDII_JP_NKY_SYMBOLS:
        return "znb_NKY"
    return None


def qdii_eu_est_symbol(symbol: str) -> str | None:
    if symbol == "SH513080":
        return "znb_CAC"
    if symbol in QDII_EU_DAX_SYMBOLS:
        return "znb_DAX"
    return None


@dataclass(frozen=True)
class FundCalibration:
    position: float
    calibration: float
    netvalue: float
    symbol_hedge: str
    cny: float | None = None
    est_netvalue: float | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FundCalibration":
        return cls(
            position=float(data["position"]),
            calibration=float(data["calibration"]),
            netvalue=float(data["netvalue"]),
            symbol_hedge=str(data["symbol_hedge"]),
            cny=_optional_float(data.get("CNY")),
            est_netvalue=_optional_float(data.get("est_netvalue")),
        )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


class PalmmicroFundCalculator:
    """Pure-Python fund estimator for data returned by php/stockdataarray.php."""

    def __init__(self, config: Mapping[str, Mapping[str, Any]]):
        self.config: dict[str, Mapping[str, Any]] = dict(config)

    def get_config(self) -> dict[str, Mapping[str, Any]]:
        return dict(self.config)

    def get_param(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def est_source(self) -> dict[str, float]:
        """从配置里拼出"中间层估值贡献"的默认 source。

        对应 PHP 页面里 ``$est_ref->GetPrice()`` 那一层，但**不是**原始市价，
        而是已经缩放到"基金估值"尺度的贡献值。

        为什么不能直接塞原始 ``est_quote``：``calc_with_calibration()`` 用的是

            est_f = (1 - p_f) * nv_f + p_f * est_value / cal_f

        要让它等于官方净值 ``nv_f``，必须 ``est_value = nv_f * cal_f``。
        直接塞 QQQ 市价 749.58 时这一项变成 749.58 而不是 4.78，
        实测估值被放大到 643194。

        **为什么不能在这里乘系数来缩放**：``est_net_value()`` 在拿到 source 后
        还会调一次 ``reverse_calc_with_calibration()``（又要乘 ``cal_m``）。
        所以缩放必须在"贡献值"层面完成，由
        ``StockDataArrayBuilder._attach_hedge_value()`` 负责 —— 它能同时看到
        基金的净值与校准值，这是单看中间层求不出的。

        取值优先级：

        1. ``hedge_value`` —— 构建器按 (A) 式 ``nv_f * cal_f`` 算好、**挂在基金行上**
           的贡献值（一个中间层可服务多只基金，所以必须挂在基金行）；
        2. ``netvalue`` —— 来自 ``netvaluehistory``，PHP 时代 ``yahoostock.php``
           把中间层序列写在那里，保留这条回落路径。
        """
        source: dict[str, float] = {}
        for symbol, data in self.config.items():
            if not isinstance(data, Mapping):
                continue

            value = data.get("hedge_value")
            if value in (None, ""):
                # 没有预计算的贡献值时回落到净值（老数据源、或未抓过中间层行情）
                value = data.get("netvalue")
            if value in (None, ""):
                continue
            try:
                source[symbol] = float(value)
            except (TypeError, ValueError):
                continue
        return source

    def est_net_value_with_est_quote(self, symbol: str) -> float:
        """用中间层实时值（``est_quote``）估算，对应页面上"实时估值"那一列。

        与 ``est_net_value(symbol, self.est_source())`` **不等价**，必须走这里：

        ``est_net_value()`` 拿到 source 后还会调一次
        ``reverse_calc_with_calibration()``（乘一次 ``cal_m``）。而
        ``est_source()`` 给的是**已经缩放好的贡献值**，再乘一次就错了
        （实测 4.5452 被变成 0.0061）。

        所以这里绕开反算，直接把贡献值注入到中间层那一行再算基金。
        """
        data = self.config.get(symbol)
        if data is None or "calibration" not in data:
            return 0.0

        hedge_symbol = str(data.get("symbol_hedge") or "")
        hedge_row = self.config.get(hedge_symbol)
        if not hedge_symbol or not isinstance(hedge_row, Mapping):
            return self.calc_with_calibration(data)

        # 贡献值挂在**基金行**上（一个中间层可服务多只净值不同的基金）
        contribution = data.get("hedge_value")
        if contribution in (None, ""):
            # 没有预计算的贡献值：退回标准路径（会走反算）
            return self.est_net_value(symbol, self.est_source())

        merged = dict(self.est_source())
        merged[hedge_symbol] = float(contribution)
        return self.calc_with_calibration(data, merged)

    def set_param(self, key: str, value: Mapping[str, Any]) -> None:
        self.config[key] = value

    @staticmethod
    def reverse_calc_with_calibration(data: Mapping[str, Any], symbol: str, source: Mapping[str, float]) -> float:
        cny = float(source.get("CNY", data.get("CNY", 1.0)))
        position = float(data["position"])
        return (float(source[symbol]) - (1.0 - position) * float(data["netvalue"])) * float(data["calibration"]) / position / cny

    @staticmethod
    def calc_with_calibration(data: Mapping[str, Any], source: Mapping[str, float] | None = None) -> float:
        cny = 1.0
        est_value = 0.0
        if source is not None:
            hedge_symbol = str(data["symbol_hedge"])
            if hedge_symbol in source:
                est_value = float(source[hedge_symbol])
            else:
                est_value = PalmmicroFundCalculator._middle_est_value(data)
            cny = float(source.get("CNY", data.get("CNY", 1.0)))
        else:
            est_value = PalmmicroFundCalculator._middle_est_value(data)
            if "CNYest" in data:
                cny = float(data["CNYest"])
        position = float(data["position"])
        return (1.0 - position) * float(data["netvalue"]) + position * est_value * cny / float(data["calibration"])

    @staticmethod
    def _middle_est_value(data: Mapping[str, Any]) -> float:
        """取"中间层估值"用于 ``calc_with_calibration``。

        优先级：

        1. ``hedge_value`` —— 构建器按 ``nv_f * cal_f`` 算好的贡献值。
           有了它，静态估值与实时估值才会给出一致的结果
           （只有 ``est_netvalue`` 时两者会不同，实测差到 0.227 vs 4.545）。
        2. ``est_netvalue`` —— 对应 PHP ``stockdataarray.php`` 里写进数据的
           ``est_netvalue``（来自 ``FundProfile.est_netvalue``）。
        """
        value = data.get("hedge_value")
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
        if "est_netvalue" in data:
            return float(data["est_netvalue"])
        return 0.0

    @staticmethod
    def calc_with_holdings(data: Mapping[str, Any], source: Mapping[str, float] | None = None) -> float:
        total = 0.0
        for holding_symbol, holding in data["symbol_hedge"].items():
            est_value = float(source[holding_symbol]) if source is not None else float(holding["est_price"])
            total += float(holding["ratio"]) * est_value / float(holding["price"])
        total /= 100.0
        cny = float(data.get("CNY", 1.0))
        holdings_cny = float(data.get("CNYholdings", cny))
        return float(data["netvalue"]) * (1.0 + float(data["position"]) * (total * cny / holdings_cny - 1.0))

    def est_net_value(self, symbol: str, source: Mapping[str, float] | None = None) -> float:
        data = self.config.get(symbol)
        if data is None:
            return 0.0

        if "calibration" not in data:
            return self.calc_with_holdings(data, source)

        hedge_symbol = str(data["symbol_hedge"])
        if hedge_symbol not in self.config:
            return self.calc_with_calibration(data, source)

        if source is None:
            return self.calc_with_calibration(data)

        hedge_data = self.config[hedge_symbol]
        if hedge_symbol in source:
            hedge_value = self.reverse_calc_with_calibration(hedge_data, hedge_symbol, {hedge_symbol: source[hedge_symbol]})
        else:
            index_symbol = str(hedge_data["symbol_hedge"])
            index_data = self.config[index_symbol]
            future_symbol = str(index_data["symbol_hedge"])
            hedge_value = self.calc_with_calibration(index_data, {future_symbol: source[future_symbol]})

        merged_source = dict(source)
        merged_source[hedge_symbol] = hedge_value
        return self.calc_with_calibration(data, merged_source)

    def reverse_est(self, source: Mapping[str, float]) -> float:
        symbol = next((key for key in source if key != "CNY"), None)
        if symbol is None:
            return 0.0

        data = self.config.get(symbol)
        if data is None or "calibration" not in data:
            return 0.0

        est_value = self.reverse_calc_with_calibration(data, symbol, source)
        hedge_symbol = str(data["symbol_hedge"])
        if hedge_symbol in self.config:
            hedge_data = self.config[hedge_symbol]
            est_value = self.calc_with_calibration(hedge_data, {str(hedge_data["symbol_hedge"]): est_value})
        return est_value

    # Compatibility aliases for the existing scripts.
    def EstNetValue(self, strSymbol: str, arSrc: Mapping[str, float] | None = None) -> float:
        return self.est_net_value(strSymbol, arSrc)

    def ReverseEst(self, arSrc: Mapping[str, float]) -> float:
        return self.reverse_est(arSrc)
