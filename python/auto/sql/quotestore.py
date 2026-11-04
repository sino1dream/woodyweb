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

import json
from typing import Any, Mapping

# 对应 PHP `_invalidHistoryData()` 的判空：空字符串或 'N/A' 都视为无效
INVALID_FIELD_VALUES = ("", "N/A")

# ---------------------------------------------------------------------------
# 五档盘口（realtimequote.orderbook）
#
# 对应 PHP ``StockReference::_onSinaDataCN()``（php/stock/stockref.php:316）里的
# 四个平行数组：arBidPrice / arBidQuantity / arAskPrice / arAskQuantity。
# PHP 里"缺档"就是数组没那么长 —— ``GetAskQuantity($i)`` 取不到时返回 ``false``，
# ``php/ui/tradingparagraph.php`` 正是靠这个假值决定"这一档整行不渲染"。
# 所以这里**原样保留列表长度**，不补齐到 5 档：长度本身就是信息。
# ---------------------------------------------------------------------------

#: 盘口列名。
ORDERBOOK_COLUMN = "orderbook"

#: 当日均价列名（只有国内商品期货 ``nf_`` 有值）。
#:
#: PHP 侧 ``strVWAP`` 是**同一个行情对象**上的字段（``_onSinaFutureCN()`` 从新浪
#: 第 28 个字段填），``fundpairref.php:242`` 的官方 EST 取的就是它。本项目把快照
#: 拆成"价格"（本表）与"估值层数值"（``estquote``）两张表，但均价属于**行情**本身，
#: 所以放回本表 —— 否则 ``FundPairReference.get_official_net_value()`` 里那句
#: ``getattr(pair_ref, "str_vwap", None)`` 永远拿不到值，商品期货基金的官方 EST
#: 会退回结算价（实测豆粕 2.0846 vs 生产端 2.054，差 1.5%）。
VWAP_COLUMN = "vwap"

#: 盘口的四个平行列表，顺序与 PHP 的数组一一对应（买价、买量、卖价、卖量）。
ORDERBOOK_KEYS = ("bid_price", "bid_quantity", "ask_price", "ask_quantity")

#: MySQL 错误码 1054：Unknown column。老库还没打盘口补丁时会撞上。
ER_BAD_FIELD_ERROR = 1054


def empty_orderbook() -> dict:
    """四个空列表 —— 表示"没有盘口信息"（区别于"有盘口但都是空的"）。"""
    return {key: [] for key in ORDERBOOK_KEYS}


def orderbook_from_lists(bid_prices=None, bid_quantities=None,
                         ask_prices=None, ask_quantities=None) -> dict:
    """把四个平行列表规整成盘口字典。

    * 值一律转成**字符串**（与 PHP 一样保留数据源的原始文本，不做数值化）；
    * ``None`` / ``False``（PHP 取不到档位时的返回值）转成空串 —— 空串在
      ``web/services/trading_service._php_truthy()`` 里同样是假值，语义一致；
    * **没有任何一格非空时返回 ``{}``**：这样"没有盘口"和"有盘口但没挂单"在上层
      可以区分开（``refquote.apply_snapshot`` 只在非空时才覆盖引用对象）。
      判空要**看格子内容**而不是列表长度 —— 美股/港股那几套解析不填盘口，
      ``StockReference`` 里是五个 ``False``，转出来就是五个空串，
      那跟"没盘口"是一回事，不该存成一坨空 JSON。
    """
    book = {
        "bid_price": _stringify_list(bid_prices),
        "bid_quantity": _stringify_list(bid_quantities),
        "ask_price": _stringify_list(ask_prices),
        "ask_quantity": _stringify_list(ask_quantities),
    }
    return book if _has_any_level(book) else {}


def encode_orderbook(book: Any) -> Any:
    """把盘口字典编成要写进库的 JSON 文本；没有盘口时返回 ``None``。

    返回 ``None``（而不是 ``'{}'``）是为了让老库里那一行保持 NULL ——
    "没抓到盘口"与"抓到了但没挂单"在排查时是两回事。
    """
    if not book:
        return None
    payload = {key: list(book.get(key) or ()) for key in ORDERBOOK_KEYS}
    if not _has_any_level(payload):
        return None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def decode_orderbook(text: Any) -> dict:
    """把库里的 JSON 文本解回盘口字典。

    **任何异常都降级成"没有盘口"**：列不存在（``None``）、空串、坏 JSON、
    结构不是字典、缺 key，都不能让"读行情"这件事失败 —— 盘口只是附加信息，
    主字段（价格/昨收）仍然有效。
    """
    if text is None or text == "":
        return {}
    if isinstance(text, (bytes, bytearray)):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if isinstance(text, dict):
        payload = text
    else:
        try:
            payload = json.loads(str(text))
        except (ValueError, TypeError):
            return {}
    if not isinstance(payload, dict):
        return {}
    book = {key: _stringify_list(payload.get(key)) for key in ORDERBOOK_KEYS}
    return book if _has_any_level(book) else {}


def _has_any_level(book: Mapping) -> bool:
    """盘口里是否**至少有一格**非空（决定"值不值得存"）。"""
    for key in ORDERBOOK_KEYS:
        for value in book.get(key) or ():
            if str(value or "").strip():
                return True
    return False


def _stringify_list(values: Any) -> list:
    """把一档一档的值转成字符串列表；``None``/``False`` 转成空串。"""
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        values = [values]
    result = []
    for value in values:
        if value is None or value is False:
            result.append("")
        else:
            result.append(str(value))
    return result


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

        # 外汇（``fx_susdcnh`` 这类）**不进 ``stockhistory``**。
        # ``load_sina_forex_data()`` 也会填 open/high/low/close，字段校验过得去，
        # 于是汇率会被当成股价写一行历史 —— 实测就是这么污染到库里的
        # （``fx_susdcnh 2026-10-15 close=6.69``）。汇率的"历史"有自己那条路
        # （``forexupdater`` / ``forexsource`` 按日积累），不走行情表。
        if getattr(reference, "is_sina_forex", None) and reference.is_sina_forex():
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


#: 建库时就有的一列不缺；``orderbook`` / ``vwap`` 是后来加的
#: （``dbtools/patch_realtimequote_orderbook.sql`` / ``patch_realtimequote_vwap.py``）。
_LEGACY_COLUMNS = ("symbol", "source_symbol", "price", "prev_price", "name",
                   "quote_date", "quote_time", "source", "fetched_at")
_BOOK_COLUMNS = _LEGACY_COLUMNS + (ORDERBOOK_COLUMN,)
_ALL_COLUMNS = _BOOK_COLUMNS + (VWAP_COLUMN,)

_SELECT_SQL_WITH_BOOK_VWAP = "SELECT %s FROM realtimequote" % ", ".join(_ALL_COLUMNS)
_SELECT_SQL_WITH_BOOK = "SELECT %s FROM realtimequote" % ", ".join(_BOOK_COLUMNS)
_SELECT_SQL_LEGACY = "SELECT %s FROM realtimequote" % ", ".join(_LEGACY_COLUMNS)

_INSERT_SQL_WITH_BOOK_VWAP = (
    "INSERT INTO realtimequote (id, symbol, source_symbol, price, prev_price, name, "
    "quote_date, quote_time, source, fetched_at, orderbook, vwap) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON DUPLICATE KEY UPDATE symbol = VALUES(symbol), source_symbol = VALUES(source_symbol), "
    "price = VALUES(price), prev_price = VALUES(prev_price), name = VALUES(name), "
    "quote_date = VALUES(quote_date), quote_time = VALUES(quote_time), "
    "source = VALUES(source), fetched_at = VALUES(fetched_at), orderbook = VALUES(orderbook), "
    "vwap = VALUES(vwap)"
)
_INSERT_SQL_WITH_BOOK = (
    "INSERT INTO realtimequote (id, symbol, source_symbol, price, prev_price, name, "
    "quote_date, quote_time, source, fetched_at, orderbook) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON DUPLICATE KEY UPDATE symbol = VALUES(symbol), source_symbol = VALUES(source_symbol), "
    "price = VALUES(price), prev_price = VALUES(prev_price), name = VALUES(name), "
    "quote_date = VALUES(quote_date), quote_time = VALUES(quote_time), "
    "source = VALUES(source), fetched_at = VALUES(fetched_at), orderbook = VALUES(orderbook)"
)
_INSERT_SQL_LEGACY = (
    "INSERT INTO realtimequote "
    "(id, symbol, source_symbol, price, prev_price, name, quote_date, quote_time, source, fetched_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON DUPLICATE KEY UPDATE symbol = VALUES(symbol), source_symbol = VALUES(source_symbol), "
    "price = VALUES(price), prev_price = VALUES(prev_price), name = VALUES(name), "
    "quote_date = VALUES(quote_date), quote_time = VALUES(quote_time), "
    "source = VALUES(source), fetched_at = VALUES(fetched_at)"
)


def _error_code(exc: BaseException) -> Any:
    """取数据库异常的错码；取不到返回 ``None``（不同驱动的 args 结构不一样）。"""
    args = getattr(exc, "args", None)
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _select_snapshots(cursor: Any, where: str = "", params: Any = ()) -> list:
    """读 ``realtimequote``；老库缺列时**逐级退让**。

    两级可选列（``orderbook``、``vwap``）各有"打过补丁 / 没打过"两种库，
    所以按 ``全列 → 只有盘口 → 老结构`` 依次尝试；靠 MySQL 的 1054
    （Unknown column）来判断"这一版能不能用"，不必自己查 ``information_schema``。

    退让后给每行补上缺列（值为 ``None``），这样上层只看这几个 key 就够了，
    不必到处判断"这次到底有没有读到那一列"。
    """
    attempts = (
        (_SELECT_SQL_WITH_BOOK_VWAP, {}),
        (_SELECT_SQL_WITH_BOOK, {VWAP_COLUMN: None}),
        (_SELECT_SQL_LEGACY, {ORDERBOOK_COLUMN: None, VWAP_COLUMN: None}),
    )
    for index, (sql, defaults) in enumerate(attempts):
        try:
            cursor.execute(sql + where, params)
            break
        except Exception as exc:
            if _error_code(exc) != ER_BAD_FIELD_ERROR or index == len(attempts) - 1:
                raise
    rows = [dict(row) for row in cursor.fetchall()]
    if defaults:
        rows = [dict(row, **defaults) for row in rows]
    return rows


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
             quote_time: Any = None, source: str = "sina", fetched_at: Any = None,
             orderbook: Any = None, vwap: Any = None) -> bool:
        """写入/更新一只符号的最新快照（按主键 UPSERT，一个符号只留一行）。

        ``orderbook`` 是 :func:`orderbook_from_lists` 形状的字典（五档买卖挂单），
        ``vwap`` 是当日均价（只有国内商品期货 ``nf_`` 有值）。

        两级可选列都可能还没打补丁，所以按 ``全列 → 只有盘口 → 老结构`` 逐级退让：
        盘口与均价都是附加信息，不该让"价格写不进去"这种事发生。
        """
        if not stock_id:
            return False
        params = (
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
        )
        book = encode_orderbook(orderbook)
        attempts = (
            (_INSERT_SQL_WITH_BOOK_VWAP, params + (book, "" if vwap in (None, "") else str(vwap))),
            (_INSERT_SQL_WITH_BOOK, params + (book,)),
            (_INSERT_SQL_LEGACY, params),
        )
        cursor = self.connection.cursor()
        try:
            for index, (sql, args) in enumerate(attempts):
                try:
                    cursor.execute(sql, args)
                    break
                except Exception as exc:
                    if _error_code(exc) != ER_BAD_FIELD_ERROR or index == len(attempts) - 1:
                        raise
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
            return [dict(row) for row in _select_snapshots(cursor)]
        finally:
            cursor.close()

    def load(self, symbol: str):
        """按符号读一条快照；没有返回 ``None``。"""
        cursor = self.connection.cursor()
        try:
            rows = _select_snapshots(cursor, " WHERE symbol = %s LIMIT 1", (symbol,))
            return dict(rows[0]) if rows else None
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
    详见 ``dbtools/schema.sql`` 里 ``estquote`` 的注释。
    """

    def __init__(self, connection: Any):
        self.connection = connection
        #: ``estquote.vwap`` 列是否存在（老库没有，``patch_estquote_vwap.py``
        #: 打过才有）。懒探测一次就缓存 —— daemon 每轮都要写，不能每次问库。
        self._vwap_column = None

    def has_vwap_column(self) -> bool:
        """``estquote`` 有没有 ``vwap`` 列。"""
        if self._vwap_column is not None:
            return self._vwap_column
        exists = False
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'estquote' "
                "AND COLUMN_NAME = 'vwap'"
            )
            row = cursor.fetchone()
            exists = bool(row and int(row["n"]))
        except Exception:
            exists = False
        finally:
            cursor.close()
        self._vwap_column = exists
        return exists

    def save(self, stock_id: Any, symbol: str, source_symbol: str, close: Any,
             prev_close: Any = "", quote_date: Any = None, quote_time: Any = None,
             source: str = "sina", fetched_at: Any = None, vwap: Any = None) -> bool:
        """写入/更新一个中间层标的的最新数值（一符号一行）。

        ``vwap`` 是**当日均价**，只有国内商品期货（``nf_``）才有值：商品期货
        基金的净值按均价计，PHP ``GetOfficialNetValue()`` 因此对它取
        ``strVWAP``（``fundpairref.php:242``）。其它符号留空串。
        """
        if not stock_id:
            return False
        cursor = self.connection.cursor()
        try:
            if self.has_vwap_column():
                cursor.execute(
                    "INSERT INTO estquote "
                    "(id, symbol, source_symbol, close, prev_close, quote_date, quote_time, "
                    "source, fetched_at, vwap) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE symbol = VALUES(symbol), "
                    "source_symbol = VALUES(source_symbol), "
                    "close = VALUES(close), prev_close = VALUES(prev_close), "
                    "quote_date = VALUES(quote_date), quote_time = VALUES(quote_time), "
                    "source = VALUES(source), fetched_at = VALUES(fetched_at), "
                    "vwap = VALUES(vwap)",
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
                        "" if vwap in (None, "") else str(vwap),
                    ),
                )
            else:
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

    def _select_columns(self) -> str:
        columns = ("symbol, source_symbol, close, prev_close, quote_date, quote_time, "
                   "source, fetched_at")
        if self.has_vwap_column():
            columns += ", vwap"
        return columns

    def load_all(self) -> list:
        """读出全部中间层数值。"""
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT %s FROM estquote" % self._select_columns())
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def load(self, symbol: str):
        """按符号读一条；没有返回 ``None``。"""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT %s FROM estquote WHERE symbol = %%s LIMIT 1" % self._select_columns(),
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
