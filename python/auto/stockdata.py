from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from fundcalc import qdii_est_symbol, qdii_hk_est_symbol, qdii_jp_est_symbol, qdii_eu_est_symbol, qdii_qqq_symbols, qdii_spy_symbols

FLOAT_PRECISION = 6
YAHOO_INDEX_CHAR = "^"


@dataclass(frozen=True)
class DailyRecord:
    date: str
    close: float
    adjclose: float | None = None


@dataclass(frozen=True)
class FundProfile:
    symbol: str
    stock_id: str
    position: float = 1.0
    official_date: str | None = None
    cny_est: float | None = None
    cny_current: float | None = None
    is_lof: bool = False
    est_symbol: str | None = None
    est_netvalue: float | None = None
    pair_symbol: str | None = None
    holdings_date: str | None = None
    holdings_netvalue: float | None = None


class FundDataSource:
    def get_stock_id(self, symbol: str) -> str | None:
        ...

    def get_stock_symbol(self, stock_id: str) -> str | None:
        ...

    def get_fund_profile(self, symbol: str) -> FundProfile | None:
        ...

    def get_position(self, stock_id: str) -> float:
        ...

    def get_calibration_now(self, stock_id: str) -> DailyRecord | None:
        ...

    def get_calibration_from_date(self, stock_id: str, date: str) -> DailyRecord | None:
        ...

    def get_netvalue_close(self, stock_id: str, date: str) -> float | None:
        ...

    def get_last_calibration(self, stock_id: str) -> float | None:
        ...

    def get_fund_pair_id(self, stock_id: str) -> str | None:
        ...

    def get_holdings(self, stock_id: str) -> Mapping[str, float]:
        ...

    def get_history_close(self, stock_id: str, date: str) -> float | None:
        ...

    def get_history_adjclose(self, stock_id: str, date: str) -> float | None:
        ...

    def get_stock_hedge(self, symbol: str, stock_id: str, leverage_symbol: str | None = None) -> float:
        ...


def rtrim0(value: Any) -> str:
    return str(float(value))


def stock_get_symbol(text: str) -> str:
    symbol = text.strip()
    if "_" not in symbol:
        symbol = symbol.upper()
    return symbol


def get_input_symbol_array(symbols: str) -> list[str]:
    normalized = re.sub(r"[,，、\r\n]+", " ", symbols)
    return [stock_get_symbol(part) for part in normalized.split() if part]


def get_leverage_hedge_symbol(symbol: str) -> str | None:
    if symbol in qdii_spy_symbols():
        return "SPY"
    if symbol in qdii_qqq_symbols():
        return "QQQ"
    return None


class StockDataArrayBuilder:
    """Build the Python equivalent of PHP GetStockDataArray for fund estimation."""

    def __init__(self, source: FundDataSource):
        self.source = source

    def build(self, symbols: str, symbol_range: set[str] | None = None) -> dict[str, dict[str, Any]]:
        input_symbols = get_input_symbol_array(symbols)
        if symbol_range is not None:
            input_symbols = [symbol for symbol in input_symbols if symbol in symbol_range]

        result: dict[str, dict[str, Any]] = {}
        for symbol in input_symbols:
            result[symbol] = self._build_symbol(symbol, result)
        return result

    def _build_symbol(self, symbol: str, result: dict[str, dict[str, Any]]) -> dict[str, Any]:
        profile = self.source.get_fund_profile(symbol)
        if profile is None:
            return {}

        data: dict[str, Any] = {
            "position": str(profile.position),
        }
        official_date = profile.official_date
        if official_date:
            data["est_date"] = official_date
        if profile.cny_est is not None:
            data["CNYest"] = rtrim0(profile.cny_est)
            if profile.is_lof and profile.cny_current is not None:
                data["CNY"] = profile.cny_current

        stock_id = profile.stock_id
        calibration = self.source.get_calibration_now(stock_id)
        if calibration is not None:
            self._add_calibrated_fund_data(data, result, symbol, stock_id, profile, calibration)
        else:
            self._add_holdings_fund_data(data, result, profile)
        return data

    def _add_calibrated_fund_data(
        self,
        data: dict[str, Any],
        result: dict[str, dict[str, Any]],
        symbol: str,
        stock_id: str,
        profile: FundProfile,
        calibration: DailyRecord,
    ) -> None:
        data["calibration"] = rtrim0(calibration.close)
        data["date"] = calibration.date

        netvalue = self.source.get_netvalue_close(stock_id, calibration.date)
        if netvalue is not None:
            data["netvalue"] = rtrim0(netvalue)

        hedge_symbol = profile.est_symbol or profile.pair_symbol
        leverage_symbol = get_leverage_hedge_symbol(symbol)
        if hedge_symbol and leverage_symbol:
            self._add_index_array(result, hedge_symbol, leverage_symbol, calibration.date)
            self._add_fund_pair_array(result, hedge_symbol)
            hedge_symbol = leverage_symbol

        if profile.est_netvalue is not None:
            data["est_netvalue"] = str(profile.est_netvalue)
        if hedge_symbol:
            data["symbol_hedge"] = hedge_symbol
        data["hedge"] = str(round(self.source.get_stock_hedge(symbol, stock_id), FLOAT_PRECISION))

    def _add_holdings_fund_data(self, data: dict[str, Any], result: dict[str, dict[str, Any]], profile: FundProfile) -> None:
        if profile.holdings_date is None or profile.holdings_netvalue is None:
            return

        data["date"] = profile.holdings_date
        data["netvalue"] = rtrim0(profile.holdings_netvalue)
        if profile.cny_est is not None:
            data["CNYholdings"] = rtrim0(profile.cny_est)

        holdings: dict[str, dict[str, str]] = {}
        for holding_id, ratio in self.source.get_holdings(profile.stock_id).items():
            holding_symbol = self.source.get_stock_symbol(holding_id)
            if holding_symbol is None:
                continue

            holding_data: dict[str, str] = {"ratio": str(ratio)}
            close = self.source.get_history_close(holding_id, profile.holdings_date)
            if close is not None:
                holding_data["price"] = rtrim0(close)
            if profile.official_date:
                adjclose = self.source.get_history_adjclose(holding_id, profile.official_date)
                if adjclose is not None:
                    holding_data["est_price"] = rtrim0(adjclose)
            holdings[holding_symbol] = holding_data

            if not holding_symbol.startswith(YAHOO_INDEX_CHAR):
                self._add_fund_pair_array(result, holding_symbol)

        if holdings:
            data["symbol_hedge"] = holdings

    def _add_index_array(self, result: dict[str, dict[str, Any]], index_symbol: str, symbol: str, date: str) -> None:
        if symbol in result:
            return

        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            return
        calibration = self.source.get_calibration_from_date(stock_id, date)
        if calibration is None:
            return

        netvalue = self.source.get_netvalue_close(stock_id, calibration.date)
        result[symbol] = {
            "position": str(self.source.get_position(stock_id)),
            "calibration": rtrim0(calibration.close),
            "date": calibration.date,
            "netvalue": rtrim0(netvalue) if netvalue is not None else "",
            "symbol_hedge": index_symbol,
        }

    def _add_fund_pair_array(self, result: dict[str, dict[str, Any]], symbol: str) -> None:
        if symbol in result:
            return

        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            return
        pair_id = self.source.get_fund_pair_id(stock_id)
        if pair_id is None:
            return
        pair_symbol = self.source.get_stock_symbol(pair_id)
        calibration = self.source.get_calibration_now(stock_id)
        if pair_symbol is None or calibration is None:
            return

        result[symbol] = {
            "position": str(self.source.get_position(stock_id)),
            "calibration": rtrim0(calibration.close),
            "date": calibration.date,
            "netvalue": str(self.source.get_last_calibration(stock_id)),
            "symbol_hedge": pair_symbol,
        }


def build_stock_data_from_mysql_env(symbols: str, symbol_range: set[str] | None = None) -> dict[str, dict[str, Any]]:
    source = MySqlFundDataSource.from_env()
    return StockDataArrayBuilder(source).build(symbols, symbol_range)


@dataclass
class MemoryFundDataSource:
    stocks: dict[str, str]
    profiles: dict[str, FundProfile] = field(default_factory=dict)
    positions: dict[str, float] = field(default_factory=dict)
    calibration: dict[str, list[DailyRecord]] = field(default_factory=dict)
    netvalue: dict[tuple[str, str], float] = field(default_factory=dict)
    last_calibration: dict[str, float] = field(default_factory=dict)
    fund_pairs: dict[str, str] = field(default_factory=dict)
    holdings: dict[str, dict[str, float]] = field(default_factory=dict)
    history_close: dict[tuple[str, str], float] = field(default_factory=dict)
    history_adjclose: dict[tuple[str, str], float] = field(default_factory=dict)
    hedges: dict[tuple[str, str | None], float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbols_by_id = {stock_id: symbol for symbol, stock_id in self.stocks.items()}

    def get_stock_id(self, symbol: str) -> str | None:
        return self.stocks.get(symbol)

    def get_stock_symbol(self, stock_id: str) -> str | None:
        return self.symbols_by_id.get(stock_id)

    def get_fund_profile(self, symbol: str) -> FundProfile | None:
        return self.profiles.get(symbol)

    def get_position(self, stock_id: str) -> float:
        return self.positions.get(stock_id, 1.0)

    def get_calibration_now(self, stock_id: str) -> DailyRecord | None:
        records = self.calibration.get(stock_id, [])
        return records[0] if records else None

    def get_calibration_from_date(self, stock_id: str, date: str) -> DailyRecord | None:
        for record in self.calibration.get(stock_id, []):
            if record.date <= date:
                return record
        return None

    def get_netvalue_close(self, stock_id: str, date: str) -> float | None:
        return self.netvalue.get((stock_id, date))

    def get_last_calibration(self, stock_id: str) -> float | None:
        return self.last_calibration.get(stock_id)

    def get_fund_pair_id(self, stock_id: str) -> str | None:
        return self.fund_pairs.get(stock_id)

    def get_holdings(self, stock_id: str) -> Mapping[str, float]:
        return self.holdings.get(stock_id, {})

    def get_history_close(self, stock_id: str, date: str) -> float | None:
        return self.history_close.get((stock_id, date))

    def get_history_adjclose(self, stock_id: str, date: str) -> float | None:
        return self.history_adjclose.get((stock_id, date))

    def get_stock_hedge(self, symbol: str, stock_id: str, leverage_symbol: str | None = None) -> float:
        return self.hedges.get((symbol, leverage_symbol), 1.0)


class MySqlFundDataSource:
    """DB-API backed data source for the legacy PHP stock/fund tables.

    The connection object is intentionally injected so callers may use PyMySQL,
    mysql-connector-python, or a test double with the same cursor API.
    """

    def __init__(self, connection: Any):
        self.connection = connection

    @classmethod
    def from_env(cls) -> "MySqlFundDataSource":
        import os

        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("Install PyMySQL or pass an existing DB-API connection to MySqlFundDataSource.") from exc

        connection = pymysql.connect(
            host=os.getenv("PALMMICRO_DB_HOST", "mysql"),
            user=os.getenv("PALMMICRO_DB_USER", "n5gl0n39mnyn183l_woody"),
            password=os.environ["PALMMICRO_DB_PASSWORD"],
            database=os.getenv("PALMMICRO_DB_DATABASE", "n5gl0n39mnyn183l_camman"),
            charset="utf8",
            cursorclass=pymysql.cursors.DictCursor,
        )
        return cls(connection)

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    def get_stock_id(self, symbol: str) -> str | None:
        row = self._fetchone("SELECT id FROM stock WHERE symbol = %s LIMIT 1", (symbol,))
        return str(row["id"]) if row else None

    def get_stock_symbol(self, stock_id: str) -> str | None:
        row = self._fetchone("SELECT symbol FROM stock WHERE id = %s LIMIT 1", (stock_id,))
        return str(row["symbol"]) if row else None

    def get_fund_profile(self, symbol: str) -> FundProfile | None:
        stock_id = self.get_stock_id(symbol)
        if stock_id is None:
            return None

        calibration = self.get_calibration_now(stock_id)
        official_date = calibration.date if calibration else self._read_date("holdingsdate", stock_id)
        cny_est = self._get_default_cny_est(symbol, official_date)
        holdings_date = None if calibration else self._read_date("holdingsdate", stock_id)
        holdings_netvalue = self.get_netvalue_close(stock_id, holdings_date) if holdings_date else None

        return FundProfile(
            symbol=symbol,
            stock_id=stock_id,
            position=self.get_position(stock_id),
            official_date=official_date,
            cny_est=cny_est,
            cny_current=cny_est,
            is_lof=True,
            est_symbol=self._get_est_symbol(symbol),
            est_netvalue=self._get_est_netvalue(symbol, official_date),
            pair_symbol=self._get_pair_symbol(stock_id),
            holdings_date=holdings_date,
            holdings_netvalue=holdings_netvalue,
        )

    def get_position(self, stock_id: str) -> float:
        value = self._read_val("fundposition", stock_id)
        return float(value) if value is not None else 1.0

    def get_calibration_now(self, stock_id: str) -> DailyRecord | None:
        return self._get_daily_record("calibration", stock_id)

    def get_calibration_from_date(self, stock_id: str, date: str) -> DailyRecord | None:
        return self._get_daily_record("calibration", stock_id, "date <= %s", (date,))

    def get_netvalue_close(self, stock_id: str, date: str) -> float | None:
        if date is None:
            return None
        row = self._fetchone("SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s LIMIT 1", (stock_id, date))
        return float(row["close"]) if row else None

    def get_last_calibration(self, stock_id: str) -> float | None:
        value = self._read_val("lastcalibration", stock_id)
        return float(value) if value is not None else None

    def get_fund_pair_id(self, stock_id: str) -> str | None:
        row = self._fetchone("SELECT stock_id FROM fundpair WHERE id = %s LIMIT 1", (stock_id,))
        return str(row["stock_id"]) if row else None

    def get_holdings(self, stock_id: str) -> Mapping[str, float]:
        rows = self._fetchall("SELECT holding_id, ratio FROM holdings WHERE stock_id = %s ORDER BY ratio DESC", (stock_id,))
        return {str(row["holding_id"]): float(row["ratio"]) for row in rows}

    def get_history_close(self, stock_id: str, date: str) -> float | None:
        row = self._fetchone("SELECT close FROM stockhistory WHERE stock_id = %s AND date = %s LIMIT 1", (stock_id, date))
        return float(row["close"]) if row else None

    def get_history_adjclose(self, stock_id: str, date: str) -> float | None:
        row = self._fetchone(
            "SELECT adjclose FROM stockhistory WHERE stock_id = %s AND date <= %s ORDER BY date DESC LIMIT 1",
            (stock_id, date),
        )
        return float(row["adjclose"]) if row else None

    def get_stock_hedge(self, symbol: str, stock_id: str, leverage_symbol: str | None = None) -> float:
        position = self.get_position(stock_id)
        leverage_symbol = leverage_symbol or get_leverage_hedge_symbol(symbol)
        if leverage_symbol:
            leverage_id = self.get_stock_id(leverage_symbol)
            if leverage_id is None:
                return 1.0
            for record in self._get_daily_records("calibration", stock_id):
                leverage_close = self._get_daily_close("calibration", leverage_id, record.date)
                if leverage_close is not None:
                    return (record.close / position) / (leverage_close / self.get_position(leverage_id))
            return 1.0

        calibration = self.get_calibration_now(stock_id)
        return calibration.close / position if calibration else 1.0

    def _read_val(self, table: str, stock_id: str) -> Any:
        row = self._fetchone(f"SELECT close FROM {table} WHERE id = %s LIMIT 1", (stock_id,))
        return row["close"] if row else None

    def _read_date(self, table: str, stock_id: str) -> str | None:
        row = self._fetchone(f"SELECT date FROM {table} WHERE id = %s LIMIT 1", (stock_id,))
        return str(row["date"]) if row else None

    def _get_daily_record(self, table: str, stock_id: str, extra_where: str | None = None, extra_params: tuple[Any, ...] = ()) -> DailyRecord | None:
        where = "stock_id = %s"
        params: tuple[Any, ...] = (stock_id,)
        if extra_where:
            where += f" AND {extra_where}"
            params += extra_params
        row = self._fetchone(f"SELECT date, close FROM {table} WHERE {where} ORDER BY date DESC LIMIT 1", params)
        return DailyRecord(str(row["date"]), float(row["close"])) if row else None

    def _get_daily_records(self, table: str, stock_id: str) -> list[DailyRecord]:
        rows = self._fetchall(f"SELECT date, close FROM {table} WHERE stock_id = %s ORDER BY date DESC", (stock_id,))
        return [DailyRecord(str(row["date"]), float(row["close"])) for row in rows]

    def _get_daily_close(self, table: str, stock_id: str, date: str) -> float | None:
        row = self._fetchone(f"SELECT close FROM {table} WHERE stock_id = %s AND date = %s LIMIT 1", (stock_id, date))
        return float(row["close"]) if row else None

    def _get_pair_symbol(self, stock_id: str) -> str | None:
        pair_id = self.get_fund_pair_id(stock_id)
        return self.get_stock_symbol(pair_id) if pair_id else None

    def _get_est_symbol(self, symbol: str) -> str | None:
        return qdii_est_symbol(symbol) or qdii_hk_est_symbol(symbol) or qdii_jp_est_symbol(symbol) or qdii_eu_est_symbol(symbol)

    def _get_est_netvalue(self, symbol: str, official_date: str | None) -> float | None:
        est_symbol = self._get_est_symbol(symbol)
        if est_symbol is None or official_date is None:
            return None
        est_id = self.get_stock_id(est_symbol)
        if est_id is None:
            return None
        return self.get_netvalue_close(est_id, official_date) or self.get_history_close(est_id, official_date)

    def _get_default_cny_est(self, symbol: str, official_date: str | None) -> float | None:
        if official_date is None or self._get_est_symbol(symbol) is None:
            return None
        cny_id = self.get_stock_id("USCNY")
        if cny_id is None:
            return None
        return self.get_history_close(cny_id, official_date) or self._get_daily_close("stockhistory", cny_id, official_date)
