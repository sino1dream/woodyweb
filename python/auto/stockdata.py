from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Mapping

from fundcalc import (
    PalmmicroFundCalculator,
    qdii_est_symbol,
    qdii_hk_est_symbol,
    qdii_jp_est_symbol,
    qdii_eu_est_symbol,
    qdii_qqq_symbols,
    qdii_spy_symbols,
)
from stocksymbol import (
    StockSymbol,
    YAHOO_INDEX_CHAR,
    stock_get_symbol,
)

FLOAT_PRECISION = 6
INPUT_SYMBOL_SEPARATORS = (",", chr(0xFF0C), chr(0x3001), "\r", "\n")

#: 取汇率时允许"往前回退"的最大天数。超过就记警告（见 _note_rate_age）。
#: 取 10 天是因为：周末 + 节假日最多连续几天，10 天足够覆盖；
#: 再大就说明汇率序列缺段，用旧汇率算出的估值口径已经不可信。
MAX_RATE_FALLBACK_DAYS = 10

# 内部短表名 -> 真实表名。键是调用方沿用的 PHP 说法，值是数据库里的表名。
_DAILY_TABLE_NAMES = {
    "calibration": "calibrationhistory",
}


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

    def get_stock_name(self, symbol: str) -> str | None:
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

    def get_est_quote(self, stock_id: str) -> DailyRecord | None:
        """中间层标的的最新估值数值（``estquote`` 表）。

        替代 PHP ``yahoostock.php`` 写进 ``netvaluehistory`` 的那条中间层序列。
        数据源没有这张表时返回 ``None``，调用方回落到 ``netvaluehistory``。
        默认实现返回 ``None``，所以老的数据源不用改。
        """
        return None

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


def get_input_symbol_array(symbols: str) -> list[str]:
    normalized = symbols
    for separator in INPUT_SYMBOL_SEPARATORS:
        normalized = normalized.replace(separator, " ")
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

        # 统一附加中间层估值数值（estquote）与其"估值贡献"。
        #
        # 为什么放在这里而不是各个 _add_* 里：中间层标的有三条进入路径
        # （输入符号直接构建、_add_index_array、_add_fund_pair_array），
        # 只在其中一条里附加会漏（实测 QQQ 作为输入符号时就没有）。
        for symbol, row in result.items():
            if not row:
                continue
            self._attach_est_quote(symbol, row)
        for symbol, row in result.items():
            if not row:
                continue
            self._attach_hedge_value(result, symbol, row)
        return result

    def _attach_hedge_value(
        self,
        result: dict[str, dict[str, Any]],
        symbol: str,
        row: dict[str, Any],
    ) -> None:
        """给**基金行**算出它用的中间层的"估值贡献"，存成 ``hedge_value``。

        为什么需要它：`fundcalc.est_net_value()` 的标准路径要先反算中间层
        （``reverse_calc_with_calibration``）再算基金。要**绕过**反算直接用实时源，
        就必须把反算结果算好 —— 而这需要**跨层信息**（基金净值、基金校准值），
        单看中间层自己是求不出来的。

        目标：让 ``calc_with_calibration(fund)`` 返回基金官方净值。
        那里用的是 ``p_f * est_value * cny / cal_f``，所以需要

            est_value = nv_f * cal_f / cny                             …(A)

        **注意那个 ``/ cny``**：估值公式里``est_value``后面还会乘一次汇率，
        所以这里必须先除掉，否则汇率会被算两遍 —— 汇率是 7 左右，
        实测会让估值放大 7 倍。

        **为什么挂在基金行而不是中间层行**：一个中间层标的可以服务多只净值不同的
        基金（例如 QQQ 同时服务 SZ161130 与 SH513100）。把贡献值挂在中间层行上，
        后算的那只基金会拿到别人的值 —— 实测 SH513100 因此算出 4.495（应为 2.0379）。
        挂在基金行上就天然是"这只基金自己的"贡献值。
        """
        if not isinstance(row, dict) or row.get("hedge_value"):
            return
        if row.get("calibration") in (None, "") or row.get("netvalue") in (None, ""):
            return
        hedge_symbol = row.get("symbol_hedge")
        if not hedge_symbol or hedge_symbol not in result:
            return
        # 只在中间层确实有实时源时才接管；否则保留标准路径
        if result[hedge_symbol].get("est_quote") in (None, ""):
            return

        try:
            fund_net_value = float(row["netvalue"])
            fund_calibration = float(row["calibration"])
            # 汇率：优先用数据行里的 CNY（有 CNY 时算实时），否则用 CNYest
            cny = row.get("CNY") or row.get("CNYest") or 1.0
            cny = float(cny)
        except (KeyError, TypeError, ValueError):
            return
        if cny == 0:
            return

        contribution = fund_net_value * fund_calibration / cny
        row["hedge_value"] = rtrim0(round(contribution, FLOAT_PRECISION))

    def _attach_est_quote(self, symbol: str, row: dict[str, Any]) -> None:
        """给一行数据补上中间层估值数值（``estquote`` 表）。

        为什么单独一个字段而不是覆盖 netvalue —— 两者语义不同：
          · netvalue  = 中间层官方净值（netvaluehistory）
          · est_quote = 中间层估值源的最新值（estquote，例如 QQQ 市价 749.58）

        两者**必须与 calibration 同尺度**，否则
        ``fundcalc.calc_with_calibration()`` 里 ``position * est * cny / calibration``
        会被算错（实测未对齐时会得到 641 这种离谱结果）。尺度由
        ``calibrationhistory`` 承担：``php/stock/qdiiref.php:193``
        ``fFactor = est * cny / netvalue`` 说明校准值是从"源值 + 净值"反算的
        派生量，换源时让它跟着重算即可（见 ``estcalibration.py``）。
        所以这里不做任何换算，只如实提供 est_quote。
        """
        if row.get("est_quote"):
            return
        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            return
        est_quote = self.source.get_est_quote(stock_id)
        if est_quote is None:
            return
        row["est_quote"] = rtrim0(est_quote.close)
        row["est_quote_date"] = est_quote.date

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
        # 中间层估值数值由 build() 统一附加（见 _attach_est_quote / _attach_hedge_value）

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


def build_fund_calculator(source: FundDataSource, symbols: str, symbol_range: set[str] | None = None) -> PalmmicroFundCalculator:
    return PalmmicroFundCalculator(StockDataArrayBuilder(source).build(symbols, symbol_range))


def build_fund_calculator_from_mysql_env(symbols: str, symbol_range: set[str] | None = None) -> PalmmicroFundCalculator:
    source = MySqlFundDataSource.from_env()
    return build_fund_calculator(source, symbols, symbol_range)


@dataclass
class MemoryFundDataSource:
    stocks: dict[str, str]
    names: dict[str, str] = field(default_factory=dict)
    profiles: dict[str, FundProfile] = field(default_factory=dict)
    positions: dict[str, float] = field(default_factory=dict)
    calibration: dict[str, list[DailyRecord]] = field(default_factory=dict)
    netvalue: dict[tuple[str, str], float] = field(default_factory=dict)
    last_calibration: dict[str, float] = field(default_factory=dict)
    # 中间层标的的最新估值数值（estquote 表的内存替身）
    est_quotes: dict[str, DailyRecord] = field(default_factory=dict)
    fund_pairs: dict[str, str] = field(default_factory=dict)
    holdings: dict[str, dict[str, float]] = field(default_factory=dict)
    history_close: dict[tuple[str, str], float] = field(default_factory=dict)
    history_adjclose: dict[tuple[str, str], float] = field(default_factory=dict)
    hedges: dict[tuple[str, str | None], float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbols_by_id = {stock_id: symbol for symbol, stock_id in self.stocks.items()}

    def get_stock_id(self, symbol: str) -> str | None:
        return self.stocks.get(symbol)

    def get_stock_name(self, symbol: str) -> str | None:
        return self.names.get(symbol)

    def get_stock_symbol(self, stock_id: str) -> str | None:
        return self.symbols_by_id.get(stock_id)

    def get_fund_profile(self, symbol: str) -> FundProfile | None:
        return self.profiles.get(symbol)

    def get_position(self, stock_id: str) -> float:
        """内存实现。

        与 :meth:`MySqlFundDataSource.get_position` 保持一致：没有显式仓位时
        回落到符号默认仓位（LOF 是 0.95），而不是硬编码 1.0。
        """
        if stock_id in self.positions:
            return float(self.positions[stock_id])
        symbol = self.symbols_by_id.get(stock_id)
        if symbol:
            try:
                return StockSymbol(symbol).get_default_position()
            except Exception:
                pass
        return 1.0

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

    def get_latest_net_value_close(self, stock_id: str) -> float | None:
        """内存实现：取日期最大的那条净值。"""
        candidates = [(date, value) for (sid, date), value in self.netvalue.items() if sid == stock_id]
        return max(candidates)[1] if candidates else None

    def get_latest_net_value_date(self, stock_id: str) -> str | None:
        """内存实现：取日期最大的那条净值的日期。

        与 ``get_latest_net_value_close()`` 必须取**同一行**，
        否则官方 EST 计算里的"净值"和"净值日期"会对不上。
        """
        candidates = [date for (sid, date) in self.netvalue if sid == stock_id]
        return max(candidates) if candidates else None

    def get_latest_history_close(self, stock_id: str) -> float | None:
        """内存实现：取日期最大的那条行情收盘价。"""
        candidates = [(date, value) for (sid, date), value in self.history_close.items() if sid == stock_id]
        return max(candidates)[1] if candidates else None

    def get_latest_history_date(self, stock_id: str) -> str | None:
        """内存实现：取日期最大的那条行情日期。

        页面「最新行情日期」列在没有实时行情时用它，所以要和
        ``get_latest_history_close()`` 取同一行，否则日期与价格会对不上。
        """
        candidates = [date for (sid, date) in self.history_close if sid == stock_id]
        return max(candidates) if candidates else None

    def get_prev_history_close(self, stock_id: str, date: str) -> float | None:
        """内存实现：取 ``date`` 之前最近一条行情收盘价。"""
        candidates = [(record_date, value) for (sid, record_date), value in self.history_close.items()
                      if sid == stock_id and record_date < date]
        return max(candidates)[1] if candidates else None

    def get_last_calibration(self, stock_id: str) -> float | None:
        return self.last_calibration.get(stock_id)

    def get_est_quote(self, stock_id: str) -> DailyRecord | None:
        """内存实现：读 ``est_quotes`` 字典（键是 stock_id）。"""
        return self.est_quotes.get(stock_id)

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
        # 汇率回退的警告（日期差过大时记录），由 _note_rate_age 填充、
        # 校验工具负责打印。放在这里而不是抛异常：估值仍应给出结果，
        # 但使用者要能知道"这个结果用的汇率其实来自很久以前"。
        self.rate_warnings: list = []

    @classmethod
    def from_env(cls) -> "MySqlFundDataSource":
        import os

        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("Install PyMySQL or pass an existing DB-API connection to MySqlFundDataSource.") from exc

        config = mysql_config_from_env(load_mysql_env(os.environ))
        try:
            connection = pymysql.connect(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
                charset="utf8",
                cursorclass=pymysql.cursors.DictCursor,
            )
        except Exception as exc:
            raise RuntimeError(mysql_connection_error_message(config, exc)) from exc
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

    def get_stock_name(self, symbol: str) -> str | None:
        row = self._fetchone("SELECT name FROM stock WHERE symbol = %s LIMIT 1", (symbol,))
        return str(row["name"]) if row else None

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
        """取该符号的仓位（成本占比）。

        库里没有 fundposition 行时，**回落到符号自己的默认仓位** ——
        对应 PHP ``MysqlReference::__construct()``：

            $this->fRatio = ($fRatio = $pos_sql->ReadVal($strSqlId))
                            ? $fRatio : $this->GetDefaultPosition();

        而 ``GetDefaultPosition()``（``php/stock/stocksymbol.php:1001``）是
        ``IsLofA() ? 0.95 : 1.0``。

        这里曾经硬编码 ``1.0``，于是 LOF 基金在库里没有行时会拿到 1.0 ——
        实测 SZ162411、SH501018 都因此算成 1.0，而 PHP 官网用的是 0.95。
        """
        value = self._read_val("fundposition", stock_id)
        if value is not None:
            return float(value)
        symbol = self.get_stock_symbol(stock_id)
        if symbol:
            try:
                return StockSymbol(symbol).get_default_position()
            except Exception:
                pass
        return 1.0

    def get_calibration_now(self, stock_id: str) -> DailyRecord | None:
        return self._get_daily_record("calibration", stock_id)

    def get_calibration_from_date(self, stock_id: str, date: str) -> DailyRecord | None:
        return self._get_daily_record("calibration", stock_id, "date <= %s", (date,))

    def get_stored_quote(self, symbol: str):
        """读 ``realtimequote`` 里的实时快照，返回 ``RecentQuote``；没有返回 ``None``。

        为什么数据源要提供这个：期货与中间层的**实时价**都存在快照表里
        （后台抓取进程写、Web 进程读）。实时 EST 与期货校准都要用它 ——
        没有它就会退到 ``stockhistory`` 的旧收盘价（实测把 QQQ 的 10-07 报价
        取成了 10-01 的 742.03，校准因此被"日期不一致"闸门挡掉）。

        只读快照、**不判 TTL**：这里的用途是"最近一次行情是什么"，
        不是"要不要重新抓"（后者由 ``quoteupdater`` 的 TTL 负责）。
        """
        store = getattr(self, "_stored_quote_store", None)
        if store is None:
            try:
                from quotestore import RealtimeQuoteStore
            except ImportError:
                return None
            store = RealtimeQuoteStore(self.connection)
            self._stored_quote_store = store
        try:
            row = store.load(symbol)
        except Exception:
            return None
        if not row:
            return None
        try:
            from quoteupdater import _parse_epoch, _quote_from_row
        except ImportError:
            return None
        return _quote_from_row(row, symbol, _parse_epoch(row.get("fetched_at")) or 0.0)

    def get_stored_quote_date(self, symbol: str) -> str | None:
        """快照的行情日期（`quote_date`）；没有返回 ``None``。

        期货校准要求"期货与指数**同日**"，判断用的就是它。
        """
        record = self.get_stored_quote(symbol)
        return getattr(record, "date", None) or None if record is not None else None

    def get_netvalue_close(self, stock_id: str, date: str) -> float | None:
        if date is None:
            return None
        row = self._fetchone("SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s LIMIT 1", (stock_id, date))
        return float(row["close"]) if row else None

    def get_latest_net_value_close(self, stock_id: str) -> float | None:
        """最新一条单位净值（只要数值）。"""
        record = self.get_latest_net_value(stock_id)
        return float(record.close) if record is not None else None

    def get_latest_net_value_date(self, stock_id: str) -> str | None:
        """最新一条单位净值的日期。

        官方 EST 的计算要用它当"中间层的日期"（对应 PHP
        ``$est_ref->GetDate()``）—— 中间层标的优先按**净值**取日期，
        净值表没有才退回行情表。
        """
        record = self.get_latest_net_value(stock_id)
        return str(record.date) if record is not None else None

    def get_latest_net_value(self, stock_id: str) -> DailyRecord | None:
        """最新一条单位净值，**带日期**。

        带日期是必要的：校准值必须与净值同日重算
        （对应 PHP ``qdiiref.php:AdjustFactor()`` 取当天净值再写校准值）。
        """
        row = self._fetchone(
            "SELECT close, date FROM netvaluehistory WHERE stock_id = %s ORDER BY date DESC LIMIT 1",
            (stock_id,),
        )
        if not row:
            return None
        return DailyRecord(close=row["close"], date=str(row["date"]))

    def get_last_calibration(self, stock_id: str) -> float | None:
        value = self._read_val("lastcalibration", stock_id)
        return float(value) if value is not None else None

    def get_est_quote(self, stock_id: str) -> DailyRecord | None:
        """中间层标的的最新估值数值，读 ``estquote`` 表。

        对应 PHP ``yahoostock.php`` 往 ``netvaluehistory`` 写的中间层序列，
        这里换成了独立的 ``estquote`` 表（见 ``sql/schema.sql`` 的说明）。
        表不存在或没有该符号时返回 ``None``，调用方回落到 ``netvaluehistory``。
        """
        row = self._fetchone(
            "SELECT close, quote_date FROM estquote WHERE id = %s LIMIT 1",
            (stock_id,),
        )
        if not row:
            return None
        return DailyRecord(close=row["close"], date=str(row["quote_date"]))

    def get_fund_pair_id(self, stock_id: str) -> str | None:
        row = self._fetchone("SELECT stock_id FROM fundpair WHERE id = %s LIMIT 1", (stock_id,))
        return str(row["stock_id"]) if row else None

    def get_holdings(self, stock_id: str) -> Mapping[str, float]:
        rows = self._fetchall("SELECT holding_id, ratio FROM holdings WHERE stock_id = %s ORDER BY ratio DESC", (stock_id,))
        return {str(row["holding_id"]): float(row["ratio"]) for row in rows}

    def get_history_close(self, stock_id: str, date: str) -> float | None:
        row = self._fetchone("SELECT close FROM stockhistory WHERE stock_id = %s AND date = %s LIMIT 1", (stock_id, date))
        return float(row["close"]) if row else None

    def get_latest_history_close(self, stock_id: str) -> float | None:
        """最新一条行情收盘价。用于溢价率的分母/分子（场内价）。"""
        row = self._fetchone(
            "SELECT close FROM stockhistory WHERE stock_id = %s ORDER BY date DESC LIMIT 1",
            (stock_id,),
        )
        return float(row["close"]) if row else None

    def get_latest_history_date(self, stock_id: str) -> str | None:
        """最新一条行情的日期，与 :meth:`get_latest_history_close` 同一行。

        页面「最新行情日期」列在**没有实时行情**时用它兜底；有实时行情时
        以行情自带的日期为准（行情可能比库里新）。
        """
        row = self._fetchone(
            "SELECT date FROM stockhistory WHERE stock_id = %s ORDER BY date DESC LIMIT 1",
            (stock_id,),
        )
        return str(row["date"]) if row else None

    def get_prev_history_close(self, stock_id: str, date: str) -> float | None:
        """``date`` 之前最近一条行情收盘价，对应 PHP ``GetClosePrev()``。"""
        row = self._fetchone(
            "SELECT close FROM stockhistory WHERE stock_id = %s AND date < %s ORDER BY date DESC LIMIT 1",
            (stock_id, date),
        )
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

    def _daily_table(self, table: str) -> str:
        """把内部短表名解析成真实表名。

        只映射 ``calibration``：调用方沿用了 PHP ``GetCalibrationSql()`` 的说法，
        但 PHP 里 ``CalibrationSql`` 的真实表名是 ``calibrationhistory``
        （见 php/sql/sqldailytime.php），照抄短名会得到 ``Table doesn't exist``。
        """
        return _DAILY_TABLE_NAMES.get(table, table)

    def _get_daily_record(self, table: str, stock_id: str, extra_where: str | None = None, extra_params: tuple[Any, ...] = ()) -> DailyRecord | None:
        where = "stock_id = %s"
        params: tuple[Any, ...] = (stock_id,)
        if extra_where:
            where += f" AND {extra_where}"
            params += extra_params
        table = self._daily_table(table)
        row = self._fetchone(f"SELECT date, close FROM {table} WHERE {where} ORDER BY date DESC LIMIT 1", params)
        return DailyRecord(str(row["date"]), float(row["close"])) if row else None

    def _get_daily_records(self, table: str, stock_id: str) -> list[DailyRecord]:
        table = self._daily_table(table)
        rows = self._fetchall(f"SELECT date, close FROM {table} WHERE stock_id = %s ORDER BY date DESC", (stock_id,))
        return [DailyRecord(str(row["date"]), float(row["close"])) for row in rows]

    def _get_daily_close(self, table: str, stock_id: str, date: str) -> float | None:
        """**精确日期**的收盘值；没有该日期返回 ``None``。

        注意：这是精确匹配，不做"往前找最近一天"。需要那种语义请用
        :meth:`_get_daily_close_asof` —— 我在这里踩过坑：把本方法当回退用，
        汇率（周末不更新、日期对不上）就永远取不到。
        """
        table = self._daily_table(table)
        row = self._fetchone(f"SELECT close FROM {table} WHERE stock_id = %s AND date = %s LIMIT 1", (stock_id, date))
        return float(row["close"]) if row else None

    def _get_daily_close_asof(self, table: str, stock_id: str, date: str) -> float | None:
        """**该日期或之前最近一天**的收盘值，对应 PHP ``GetClosePrev()`` 的语义。

        汇率、净值这类"按日发布、遇周末顺延"的数据都要用它：
        问 2026-09-29 的汇率时，若那天没有记录，应回退到最近的一个交易日。
        """
        table = self._daily_table(table)
        row = self._fetchone(
            f"SELECT close FROM {table} WHERE stock_id = %s AND date <= %s ORDER BY date DESC LIMIT 1",
            (stock_id, date),
        )
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
        """取该基金官方日期当天的美元汇率（USD/CNY）。

        对应 PHP ``php/stock/cnyref.php`` 的 ``CnyReference``：

            $this->strSqlId = SqlGetStockId($strSymbol);
            $this->LoadSqlNetValueData();          // ← 读的是 **netvaluehistory**

        以及 ``qdiiref.php:135``：``$cny_ref->GetClose($strDate)`` —— 取该日期的汇率。

        所以查找顺序必须是 **netvaluehistory 优先**：``forexupdater`` 与 PHP 的
        ``GetChinaMoney()`` 都把汇率写在 ``netvaluehistory``。
        之前只查了 ``stockhistory``，结果永远取不到（汇率不在那），
        ``cny`` 退化成 1.0 —— 等于把美元当人民币用，QDII 估值差约 7 倍。
        """
        if official_date is None or self._get_est_symbol(symbol) is None:
            return None
        cny_id = self.get_stock_id("USCNY")
        if cny_id is None:
            return None

        # 1) 官方日期当天的汇率（netvaluehistory，与 CnyReference 一致）
        value = self.get_netvalue_close(cny_id, official_date)
        if value is not None:
            return value

        # 2) 该日期之前最近一天的汇率 —— 汇率周末不更新，
        #    官方日期落在周末/节假日时要能往前找（对应 GetClosePrev 语义）
        row = self._fetchone(
            "SELECT date, close FROM netvaluehistory WHERE stock_id = %s AND date <= %s "
            "ORDER BY date DESC LIMIT 1",
            (cny_id, official_date),
        )
        if row:
            self._note_rate_age(official_date, str(row["date"]), symbol)
            return float(row["close"])

        # 3) 再退到 stockhistory（历史版本曾把汇率写在那里，保留兼容）
        history = self.get_history_close(cny_id, official_date)
        if history is not None:
            return history
        row = self._fetchone(
            "SELECT date, close FROM stockhistory WHERE stock_id = %s AND date <= %s "
            "ORDER BY date DESC LIMIT 1",
            (cny_id, official_date),
        )
        if row:
            self._note_rate_age(official_date, str(row["date"]), symbol)
            return float(row["close"])
        return None

    def _note_rate_age(self, wanted_date: str, actual_date: str, symbol: str) -> None:
        """记录"回退取汇率"的日期差，超过阈值就发警告。

        为什么需要：实时接口只给**当下**的汇率，而基金的官方日期往往在过去几天。
        没有历史序列时只能往前找最近一天 —— 差距可能很大（实测基金官方日期
        2026-09-29 回退命中了 2026-01-02 的占位值，差了 9 个月），
        估值就会用一个不相干时期的汇率。这种"看起来能算、其实口径错"的情况
        必须让人看得见，所以这里记警告，由校验工具打印。

        正式解决办法是让 ``forexcli.py`` 进定时任务**持续积累**汇率序列
        （见 ``python/auto/README_name_and_forex.md``）。
        """
        try:
            import datetime as _datetime

            wanted = _datetime.datetime.strptime(str(wanted_date)[:10], "%Y-%m-%d")
            actual = _datetime.datetime.strptime(str(actual_date)[:10], "%Y-%m-%d")
        except ValueError:
            return

        gap_days = (wanted - actual).days
        if gap_days <= MAX_RATE_FALLBACK_DAYS:
            return
        self.rate_warnings.append(
            "%s：%s 没有汇率，回退用了 %s 的值（相差 %d 天，超过 %d 天阈值）"
            % (symbol, wanted_date, actual_date, gap_days, MAX_RATE_FALLBACK_DAYS)
        )


def load_mysql_env(environ: Mapping[str, str], env_path: str | Path | None = None) -> dict[str, str]:
    merged = dict(parse_mysql_env_file(env_path or Path(__file__).with_name(".env")))
    merged.update({key: value for key, value in environ.items() if key.startswith("PALMMICRO_DB_")})
    return merged


def parse_mysql_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key.startswith("PALMMICRO_DB_"):
            values[key] = value
    return values


def mysql_config_from_env(environ: Mapping[str, str]) -> dict[str, Any]:
    password = environ.get("PALMMICRO_DB_PASSWORD")
    if not password:
        raise RuntimeError("PALMMICRO_DB_PASSWORD is required.")
    try:
        port = int(environ.get("PALMMICRO_DB_PORT", "3306"))
    except ValueError as exc:
        raise RuntimeError("PALMMICRO_DB_PORT must be an integer.") from exc
    config = {
        "host": environ.get("PALMMICRO_DB_HOST", "mysql"),
        "port": port,
        "user": environ.get("PALMMICRO_DB_USER", "n5gl0n39mnyn183l_woody"),
        "password": password,
        "database": environ.get("PALMMICRO_DB_DATABASE", "n5gl0n39mnyn183l_camman"),
    }
    _validate_mysql_config(config)
    return config


def _validate_mysql_config(config: Mapping[str, Any]) -> None:
    for key in ("host", "user", "password", "database"):
        value = str(config[key])
        if value.startswith("<") and value.endswith(">"):
            raise RuntimeError(f"PALMMICRO_DB_{key.upper()} still contains a placeholder value.")


def mysql_connection_error_message(config: Mapping[str, Any], exc: Exception) -> str:
    base = (
        "Unable to connect to MySQL "
        f"at {config['host']}:{config['port']} as {config['user']} "
        f"for database {config['database']}."
    )
    code = _mysql_error_code(exc)
    if code == 1045:
        hint = (
            " MySQL rejected the login. Check PALMMICRO_DB_USER and "
            "PALMMICRO_DB_PASSWORD, and confirm this user is allowed to connect "
            "from localhost/127.0.0.1."
        )
    elif code == 1049:
        hint = " The database does not exist or this user cannot see it. Check PALMMICRO_DB_DATABASE."
    elif code == 2003:
        hint = " The TCP connection failed. Check PALMMICRO_DB_HOST and PALMMICRO_DB_PORT."
    else:
        hint = (
            " Check PALMMICRO_DB_HOST, PALMMICRO_DB_PORT, PALMMICRO_DB_USER, "
            "PALMMICRO_DB_DATABASE, and PALMMICRO_DB_PASSWORD."
        )
    return f"{base}{hint} Original error: {exc}"


def _mysql_error_code(exc: Exception) -> int | None:
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None
