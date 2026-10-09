"""行情落库：把解析后的行情写进 stockhistory / netvaluehistory。

对应 PHP 源码：

* ``php/stock/sqlstocksymbol.php`` 的 ``StockHistorySql::WriteHistory()``；
* ``php/stock/sqldailyclose.php`` 的 ``DailyCloseSql::WriteDaily()``；
* ``php/stock/mystockref.php`` 的 ``_updateStockHistory()`` —— 落库前的字段校验；
* ``php/sqldate.php`` 的 ``StockHistoryDateSql`` —— 记录每只符号的最新日期。

只做写入，不做抓取（抓取在 ``sinaquotes.py``）也不做解析（解析在 ``stockref.py``），
所以可以在没有网络的情况下用解析好的 ``StockReference`` 直接测试。
"""

from __future__ import annotations

from typing import Any, Mapping

# 对应 PHP `_invalidHistoryData()` 的判空：空字符串或 'N/A' 都视为无效
INVALID_FIELD_VALUES = ("", "N/A")


def is_invalid_field(value: Any) -> bool:
    """对应 PHP ``MyStockReference::_invalidHistoryData()``。"""
    if value is None:
        return True
    return str(value).strip() in INVALID_FIELD_VALUES


def stock_get_percentage(divisor: float, dividend: float) -> float:
    """对应 PHP ``StockGetPercentage()``：百分比变化。

    抽到这里是为了让"溢价率"计算有单一实现；``stockref`` 里也有一份同样的
    函数，两者都指向同一个 PHP 原函数。
    """
    if abs(divisor) > 0.0000005:  # MIN_FLOAT_VAL
        return (dividend / divisor - 1.0) * 100.0
    return 0.0


class QuoteStore:
    """把行情写入数据库。

    ``connection`` 是任意 DB-API 连接（与 ``MySqlFundDataSource`` 共用同一个
    请求级连接即可）。
    """

    def __init__(self, connection: Any):
        self.connection = connection

    # -- 行情历史 ---------------------------------------------------------
    def write_history(
        self,
        stock_id: str,
        date: str,
        close: Any,
        volume: Any = "",
        adjclose: Any = None,
    ) -> bool:
        """对应 PHP ``StockHistorySql::WriteHistory()``。

        ``adjclose`` 缺省时取 ``close``（PHP 里 ``$strAdjClose == false`` 的语义）。
        已存在同一 (stock_id, date) 时按主键更新，不会插入重复行。
        """
        if date is None or str(date).strip() == "":
            return False
        if adjclose is None or adjclose is False:
            adjclose = close
        if volume is None or str(volume).strip() == "":
            volume = 0

        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO stockhistory (stock_id, date, close, volume, adjclose) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE close = VALUES(close), volume = VALUES(volume), "
                "adjclose = VALUES(adjclose)",
                (stock_id, date, close, volume, adjclose),
            )
        finally:
            cursor.close()
        return True

    def write_stock_history_date(self, stock_id: str, date: str) -> bool:
        """对应 PHP ``StockHistoryDateSql``：记录该符号的最新行情日期。"""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO stockhistorydate (id, date) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE date = VALUES(date)",
                (stock_id, date),
            )
        finally:
            cursor.close()
        return True

    # -- 净值历史 ---------------------------------------------------------
    def write_net_value(self, stock_id: str, date: str, close: Any, allow_weekend: bool = False) -> bool:
        """对应 PHP ``DailyCloseSql::WriteDaily()``：写入基金单位净值。

        周末守卫：基金单位净值周末不发布（新浪偶尔给出假的周末净值），
        所以默认拒绝周末日期 —— 与 PHP 行为一致。

        ``allow_weekend=True`` 用于**汇率**：外汇市场周末也有报价，
        不该被这个守卫拦掉（实测把 HKCNY/EUCNY/JPCNY 全拦了）。
        """
        if date is None or str(date).strip() == "":
            return False
        if not allow_weekend and _is_weekend(date):
            return False

        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE close = VALUES(close)",
                (stock_id, date, close),
            )
        finally:
            cursor.close()
        return True

    # -- 从解析结果落库 ---------------------------------------------------
    def save_reference(self, stock_id: str, reference, save_net_value: bool = True) -> dict:
        """把 ``stockref.StockReference`` 的解析结果落库。

        对应 PHP ``MyStockReference::_updateStockHistory()`` 的字段校验：
        open / high / low / close 任一无效就整体跳过，避免把半截数据写进历史表。

        返回 ``{"history": bool, "netvalue": bool}``，便于调用方统计。
        """
        result = {"history": False, "netvalue": False}
        date = reference.get_date()
        if is_invalid_field(date):
            return result

        if not (is_invalid_field(reference.str_open)
                or is_invalid_field(reference.str_high)
                or is_invalid_field(reference.str_low)):
            close = reference.get_price()
            if not is_invalid_field(close):
                result["history"] = self.write_history(
                    stock_id,
                    date,
                    close,
                    reference.get_volume(),
                    reference.str_settle_price,
                )
                self.write_stock_history_date(stock_id, date)

        # 场外基金（f_ 前缀）与基金净值走净值表
        if save_net_value and reference.is_sina_fund():
            net_value = reference.get_price()
            if not is_invalid_field(net_value):
                result["netvalue"] = self.write_net_value(stock_id, date, net_value)

        return result


def _is_weekend(date: str) -> bool:
    """``Y-m-d`` 是否落在周末（对应 PHP ``StringYMD::IsWeekend()``）。"""
    import datetime as _datetime

    text = str(date).strip().replace("/", "-")
    try:
        moment = _datetime.datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return False
    return moment.weekday() >= 5


class MemoryQuoteStore(QuoteStore):
    """测试用内存实现，不碰数据库。"""

    def __init__(self):
        super(MemoryQuoteStore, self).__init__(None)
        self.history = {}
        self.dates = {}
        self.net_values = {}

    def write_history(self, stock_id, date, close, volume="", adjclose=None):
        # 与父类保持一致的输入校验，避免内存实现比真实实现"宽松"
        if date is None or str(date).strip() == "":
            return False
        if adjclose is None or adjclose is False:
            adjclose = close
        self.history[(stock_id, date)] = {
            "close": close,
            "volume": 0 if volume in (None, "") else volume,
            "adjclose": adjclose,
        }
        return True

    def write_stock_history_date(self, stock_id, date):
        self.dates[stock_id] = date
        return True

    def write_net_value(self, stock_id, date, close, allow_weekend=False):
        # 与父类保持一致；汇率允许周末（见父类说明）
        if not allow_weekend and _is_weekend(date):
            return False
        self.net_values[(stock_id, date)] = close
        return True


class RealtimeQuoteStore:
    """实时行情快照的持久化（表 ``realtimequote``）。

    为什么不用进程内存：实时行情由**独立的后台进程**定时抓取，Web 进程负责
    "直读最新价"。内存缓存跨不了进程（多 worker 更不行），所以把最新一条快照
    落到库里：抓取进程写、Web 进程读。

    对应 PHP：PHP 没有这张表，它用 ``debug/sina/*.txt`` 缓存文件做跨请求共享；
    这里是同样思路的关系型实现，好处是能直接按符号查，也便于诊断来源与时间。
    """

    def __init__(self, connection: Any):
        self.connection = connection

    def save(self, stock_id: Any, symbol: str, source_symbol: str, price: Any,
             prev_price: Any = "", name: Any = "", quote_date: Any = None,
             quote_time: Any = None, source: str = "sina", fetched_at: Any = None) -> bool:
        """写入/更新一只符号的最新快照（按主键 UPSERT，一个符号只留一行）。"""
        if not stock_id:
            return False
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO realtimequote "
                "(id, symbol, source_symbol, price, prev_price, name, quote_date, quote_time, source, fetched_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE symbol = VALUES(symbol), source_symbol = VALUES(source_symbol), "
                "price = VALUES(price), prev_price = VALUES(prev_price), name = VALUES(name), "
                "quote_date = VALUES(quote_date), quote_time = VALUES(quote_time), "
                "source = VALUES(source), fetched_at = VALUES(fetched_at)",
                (
                    stock_id,
                    symbol,
                    source_symbol or "",
                    str(price or ""),
                    str(prev_price or ""),
                    str(name or ""),
                    _coerce_date(quote_date),
                    _coerce_time(quote_time),
                    source or "",
                    _coerce_datetime(fetched_at),
                ),
            )
        except Exception:
            # 快照写失败不应影响页面可用性：页面会退回 stockhistory 的收盘价
            try:
                self.connection.rollback()
            except Exception:
                pass
            return False
        finally:
            cursor.close()
        return True

    def load_all(self) -> list:
        """读出全部快照。"""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT symbol, source_symbol, price, prev_price, name, "
                "quote_date, quote_time, source, fetched_at FROM realtimequote"
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def load(self, symbol: str):
        """按符号读一条快照；没有返回 ``None``。"""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT symbol, source_symbol, price, prev_price, name, "
                "quote_date, quote_time, source, fetched_at FROM realtimequote WHERE symbol = %s LIMIT 1",
                (symbol,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()


def _coerce_date(value) -> str:
    """把 ``quote_date`` 规整成 ``YYYY-MM-DD``；无效时用今天占位。"""
    import datetime as _datetime

    text = str(value or "").strip()[:10].replace("/", "-")
    try:
        return _datetime.datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return _datetime.date.today().isoformat()


def _coerce_time(value) -> str:
    """把 ``quote_time`` 规整成 ``HH:MM:SS``；无效时用 00:00:00 占位。"""
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return text
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return text + ":00"
    return "00:00:00"


def _coerce_datetime(value) -> str:
    """把抓取时刻规整成 ``YYYY-MM-DD HH:MM:SS``。"""
    import datetime as _datetime

    if value is None:
        return _datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, (int, float)):
        return _datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip()
    if len(text) >= 19:
        return text[:19]
    return _datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 中间层估值快照（estquote 表）
# ---------------------------------------------------------------------------
class EstQuoteStore:
    """中间层标的当日数值的持久化（表 ``estquote``）。

    对应 PHP ``yahoostock.php`` 的 ``_yahooStockGetData()``。区别是 PHP 把它写进
    ``netvaluehistory``（与基金单位净值混在一张表），这里单独一张表。

    量级不需要在这一层处理：``calibrationhistory`` 是"源值 + 净值"反算出来的
    派生量（``php/stock/qdiiref.php:193``），换源时跟着重算即可，
    详见 ``sql/schema.sql`` 里 ``estquote`` 的注释。
    """

    def __init__(self, connection: Any):
        self.connection = connection

    def save(self, stock_id: Any, symbol: str, source_symbol: str, close: Any,
             prev_close: Any = "", quote_date: Any = None, quote_time: Any = None,
             source: str = "sina", fetched_at: Any = None) -> bool:
        """写入/更新一个中间层标的的最新数值（一符号一行）。"""
        if not stock_id:
            return False
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO estquote "
                "(id, symbol, source_symbol, close, prev_close, quote_date, quote_time, source, fetched_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE symbol = VALUES(symbol), source_symbol = VALUES(source_symbol), "
                "close = VALUES(close), prev_close = VALUES(prev_close), "
                "quote_date = VALUES(quote_date), quote_time = VALUES(quote_time), "
                "source = VALUES(source), fetched_at = VALUES(fetched_at)",
                (
                    stock_id,
                    symbol,
                    source_symbol or "",
                    str(close or ""),
                    str(prev_close or ""),
                    _coerce_date(quote_date),
                    _coerce_time(quote_time),
                    source or "",
                    _coerce_datetime(fetched_at),
                ),
            )
        except Exception:
            try:
                self.connection.rollback()
            except Exception:
                pass
            return False
        finally:
            cursor.close()
        return True

    def load_all(self) -> list:
        """读出全部中间层数值。"""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT symbol, source_symbol, close, prev_close, quote_date, quote_time, "
                "source, fetched_at FROM estquote"
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def load(self, symbol: str):
        """按符号读一条；没有返回 ``None``。"""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT symbol, source_symbol, close, prev_close, quote_date, quote_time, "
                "source, fetched_at FROM estquote WHERE symbol = %s LIMIT 1",
                (symbol,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()


# ---------------------------------------------------------------------------
# 溢价率
# ---------------------------------------------------------------------------
def get_premium_rate(price: Any, net_value: Any) -> Any:
    """溢价率（%）= (市价 / 净值 - 1) × 100。

    对应 PHP 里 ``StockGetPercentage()`` 用于"场内价 vs 净值"的场景。
    任一值为空/无效时返回 ``None``，让调用方显示空白而不是 0。
    """
    if is_invalid_field(price) or is_invalid_field(net_value):
        return None
    try:
        price_value = float(price)
        net_value_value = float(net_value)
    except (TypeError, ValueError):
        return None
    if net_value_value == 0:
        return None
    return stock_get_percentage(net_value_value, price_value)


def format_premium_rate(premium: Any, digits: int = 2) -> str:
    """把溢价率格式化成带符号的百分比文本，对应 PHP ``GetPercentageText()``。"""
    if premium is None:
        return ""
    text = "%.*f" % (digits, float(premium))
    text = text.rstrip("0").rstrip(".")
    if text in ("", "-0", "0"):
        return "0"
    return text + "%"
