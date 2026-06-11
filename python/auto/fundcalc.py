from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


QDII_OIL_ETF_SYMBOLS = ("SZ160416", "SZ162719")
QDII_QQQ_MATCH_SYMBOLS = (
    "SH513100",
    "SH513110",
    "SH513390",
    "SH513870",
    "SZ159501",
    "SZ159513",
    "SZ159632",
    "SZ159659",
    "SZ159660",
    "SZ159696",
    "SZ159941",
    "SZ161130",
)
QDII_SPY_MATCH_SYMBOLS = ("SH513500", "SH513650", "SZ159612", "SZ161125")
QDII_XOP_SYMBOLS = ("SH513350", "SZ159518", "SZ162411")
QDII_XBI_SYMBOLS = ("SZ159502", "SZ161127")

QDII_HK_TECH_SYMBOLS = (
    "SH513010",
    "SH513130",
    "SH513180",
    "SH513260",
    "SH513380",
    "SH513580",
    "SH513890",
    "SH520570",
    "SH520590",
    "SH520920",
    "SZ159740",
    "SZ159741",
    "SZ159742",
)
QDII_HK_HSHARES_SYMBOLS = ("SH510900", "SZ159850", "SZ159954", "SZ159960", "SZ160717", "SZ161831")
QDII_HK_HANGSENG_SYMBOLS = ("SH501302", "SH513210", "SH513600", "SZ159312", "SZ159920", "SZ160924", "SZ164705")
QDII_JP_NKY_SYMBOLS = ("SH513000", "SH513520", "SH513880", "SZ159866")
QDII_EU_DAX_SYMBOLS = ("SH513030", "SZ159561")


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
    return (
        "SH501300",
        "SH513290",
        "SH513400",
        "SZ160140",
        "SZ161126",
        "SZ161128",
        "SZ162415",
        "SZ164906",
        *QDII_XBI_SYMBOLS,
        *QDII_XOP_SYMBOLS,
        *QDII_OIL_ETF_SYMBOLS,
        *qdii_qqq_symbols(),
        *qdii_spy_symbols(),
    )


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
            elif "est_netvalue" in data:
                est_value = float(data["est_netvalue"])
            cny = float(source.get("CNY", data.get("CNY", 1.0)))
        else:
            if "est_netvalue" in data:
                est_value = float(data["est_netvalue"])
            if "CNYest" in data:
                cny = float(data["CNYest"])
        position = float(data["position"])
        return (1.0 - position) * float(data["netvalue"]) + position * est_value * cny / float(data["calibration"])

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
