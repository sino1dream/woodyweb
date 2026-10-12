"""实时行情更新器与定时抓取。

职责
----
1. 从库里取"最近有行情的符号"（对应 PHP ``_addFundPairSymbol()`` 那一圈收集逻辑）；
2. 通过 :class:`quotesource.MarketQuoteSource` 抓行情（可换源）；
3. 用 ``stockref.StockReference`` 解析（复用已迁移的解析层）；
4. 把结果放进**进程内缓存**供 Web 页面直读；
5. 提供一个 tick 函数，交给定时器按间隔调用（对应 PHP 的
   ``StockPrefetchArrayData()`` + 文件时间判断"是否需要重取"）。

与 PHP 的关系
-------------
PHP 是「每次请求内同步抓一次，用缓存文件时间控制 1 分钟最多一次」；
这里默认做成**后台定时抓 + 页面读缓存**，因为 Web 请求里同步抓会让页面变慢、
也更容易触发新浪的频率限制。两者的节流语义保持一致：默认 60 秒一批。

关于 stockhistory
-----------------
这里**不写** ``stockhistory``。原因是那张表同时也存收盘价，往里面塞盘中价会污染
数据。实时价只进缓存；需要留痕时用 ``quotecli.py`` 的落库模式（EOD 收盘后跑）。
"""

from __future__ import annotations

import datetime as _datetime
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from source import quotesource
from sql.quotestore import decode_orderbook, orderbook_from_lists
from ref.stockref import StockReference
from core.stocksymbol import StockSymbol

#: PHP ``define('TRADING_QUOTE_NUM', 5)``：五档。抓盘口时按这个档数向引用对象取值。
ORDER_BOOK_LEVELS = 5

# 相邻两批行情的最小间隔。与 PHP 一致（stockprefetch 的 NeedFile 默认
# SECONDS_IN_MIN = 60，即每分钟最多取一次）。
DEFAULT_TTL_SECONDS = 60

# 环境变量：设为 1 时，页面请求发现自己手上的行情过期就顺手更新一批。
# 默认关闭，避免"打开页面"变成同步抓取（那是 PHP 的做法，会拖慢响应）。
ENV_REFRESH_ON_REQUEST = "PALMMICRO_QUOTE_REFRESH_ON_REQUEST"

# 环境变量：定时抓取的间隔秒数。
ENV_INTERVAL = "PALMMICRO_QUOTE_INTERVAL"


@dataclass(frozen=True)
class RecentQuote:
    """一只符号的实时行情快照。"""

    symbol: str            # 库里的符号，如 SZ161130
    sina_symbol: str       # 数据源用的代码，如 sz161130
    price: str = ""        # 最新价（已按 rtrim0 口径处理）
    prev_price: str = ""   # 昨收
    name: str = ""         # 数据源返回的名称
    date: str = ""         # 行情日期
    time: str = ""         # 行情时间
    source: str = "sina"   # 数据来源标识，便于排查
    fetched_at: float = 0.0  # 取回时刻（epoch 秒）
    # 五档盘口：``{"bid_price": [...], "bid_quantity": [...], "ask_price": [...], "ask_quantity": [...]}``。
    # **空字典表示"这一次没有盘口信息"**（不是"盘口是空的"）—— 落库时写成 NULL，
    # 读回来也不会去覆盖引用对象上已有的内容。编解码见 ``quotestore``。
    orderbook: Dict[str, Any] = field(default_factory=dict)

    def has_order_book(self) -> bool:
        """这一条快照是否带了盘口。"""
        return bool(self.orderbook)

    def age(self, now: float = None) -> float:
        """距取回已过多少秒。"""
        return (now if now is not None else time.time()) - self.fetched_at

    def is_fresh(self, ttl: int = DEFAULT_TTL_SECONDS, now: float = None) -> bool:
        """缓存是否还在有效期内。"""
        return self.age(now) < ttl

    def as_dict(self) -> Dict[str, Any]:
        """给 JSON 输出用。"""
        return {
            "symbol": self.symbol,
            "sina_symbol": self.sina_symbol,
            "price": self.price,
            "prev_price": self.prev_price,
            "name": self.name,
            "date": self.date,
            "time": self.time,
            "source": self.source,
            "age_seconds": round(self.age(), 1),
        }

    def as_dict_with_order_book(self) -> Dict[str, Any]:
        """``as_dict()`` 再带上五档盘口 —— 给诊断接口用（有才有，没有就不加 key）。

        不直接改 ``as_dict()`` 的返回结构，是因为它是给列表页 JSON 用的：
        那儿有十来只符号，多挂 20 个值纯属浪费。
        """
        data = self.as_dict()
        if self.has_order_book():
            data["order_book"] = dict(self.orderbook)
        return data


def quote_from_reference(
    symbol: str,
    sina_symbol: str,
    reference: StockReference,
    source_name: str,
    now: float = None,
) -> RecentQuote:
    """把解析好的 ``StockReference`` 转成快照。

    没有取到数据（``has_quote_data()`` 为假）时价格留空，让调用方显示空白
    而不是把 0 当成真实价格。

    ``now`` 允许注入取回时刻（测试里用假时钟控制过期判定的场景）。
    """
    fetched_at = time.time() if now is None else now
    if not reference.has_quote_data():
        return RecentQuote(symbol=symbol, sina_symbol=sina_symbol, source=source_name, fetched_at=fetched_at)
    return RecentQuote(
        symbol=symbol,
        sina_symbol=sina_symbol,
        price=reference.get_price(),
        prev_price=reference.get_prev_price(),
        name=reference.str_name or "",
        date=reference.str_date or "",
        time=reference.str_time or "",
        source=source_name,
        fetched_at=fetched_at,
        orderbook=order_book_from_reference(reference),
    )


def order_book_from_reference(reference: Any, levels: int = None) -> dict:
    """从引用对象取出五档盘口，形状见 ``quotestore.orderbook_from_lists``。

    ## 为什么固定取 5 档、而不是"有多少取多少"

    ``StockReference._on_sina_data_cn()`` 照抄了 PHP 的 ``for ($i = 10; $i < 20; $i += 2)``，
    所以四个数组**恒为 5 项**，缺的那些位置是 ``False``（``_item()`` 越界返回值）。
    ``False`` 与空串在渲染层同样是假值，因此这里按 5 档取值再让
    ``orderbook_from_lists`` 把 ``False`` 折成空串，结果与 PHP 的数组逐位等价。

    美股/港股那几套解析**不填盘口**，取出来全是 ``False`` ——
    ``orderbook_from_lists`` 会因此返回 ``{}``（没有盘口），不会往库里写空 JSON。
    """
    count = ORDER_BOOK_LEVELS if levels is None else levels
    return orderbook_from_lists(
        [reference.get_bid_price(i) for i in range(count)],
        [reference.get_bid_quantity(i) for i in range(count)],
        [reference.get_ask_price(i) for i in range(count)],
        [reference.get_ask_quantity(i) for i in range(count)],
    )


def parse_symbol(sina_symbol: str, line: str, source_name: str = "sina") -> StockReference:
    """把一行原始行情解析成 ``StockReference``。

    这里复用 ``StockReference`` 而不是自己写解析：解析层已经与 PHP 逐项对照过
    （6280 个用例），复用它就不必再维护第二套解析逻辑。
    """
    reference = StockReference(sina_symbol, quote_loader=lambda _name: line)
    # StockReference 的符号就是新浪符号；下面按它的类型分派解析
    try:
        symbol_obj = StockSymbol(sina_symbol)
        if symbol_obj.is_sina_future():
            reference.load_sina_future_data()
        elif symbol_obj.is_sina_forex():
            reference.load_sina_forex_data()
        else:
            reference.load_sina_data()
    except ValueError:
        # 遇到无法解析的时间格式等：标记为无数据，不让整批失败
        reference.set_has_data(False)
    return reference


def _normalize_pair(item) -> tuple:
    """把 ``(符号, 源符号)`` 或 ``(符号, 源符号, stock_id)`` 统一成三元组。"""
    if len(item) >= 3:
        return (item[0], item[1], item[2])
    return (item[0], item[1], None)


def _normalize_quote_time(value) -> str:
    """把 ``realtimequote.quote_time`` 统一成 ``HH:MM:SS`` 文本。

    必要性：该列在库里是 MySQL ``TIME`` 类型，PyMySQL 会把它取成
    ``datetime.timedelta``（实测 15:00:00 回来是 ``timedelta(seconds=54000)``）。
    直接 ``str()`` 会得到 ``"15:00:00"`` 看着没错，但一旦带微秒或跨过 24 小时，
    文本就会变成 ``"1 day, 0:00:00"`` 这类不能显示给用户的形式。

    所以这里显式按秒数换算，并对超范围的值做取模，保证页面上永远是时钟文本。
    """
    import datetime as _datetime

    if value is None or value == "":
        return ""
    if isinstance(value, _datetime.timedelta):
        total = int(value.total_seconds())
    elif isinstance(value, _datetime.time):
        return value.strftime("%H:%M:%S")
    elif isinstance(value, _datetime.datetime):
        return value.strftime("%H:%M:%S")
    else:
        text = str(value).strip()
        # 已经是 HH:MM:SS（或带 .ffffff）就直接截断返回
        if len(text) >= 8 and text[2] == ":" and text[5] == ":":
            return text[:8]
        return text

    total %= 24 * 3600
    return "%02d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)


def _normalize_quote_date(value) -> str:
    """把 ``realtimequote.quote_date`` 统一成 ``YYYY-MM-DD`` 文本。

    MySQL ``DATE`` 列取回来是 ``datetime.date``，``str()`` 已经是 ISO 形式，
    但为免上游换成 datetime 或字符串格式不一，这里统一走一道。
    """
    if value is None or value == "":
        return ""
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else text


def _quote_from_row(row: dict, symbol: str, fetched_epoch: float) -> "RecentQuote":
    """把 ``realtimequote`` 的一行转成 :class:`RecentQuote`。

    ``_load_from_store()`` 与 ``get_last_stored()`` 共用，避免两处字段映射走样 ——
    之前只有 ``_load_from_store`` 一份，加第二个读取入口时最容易漏字段。
    """
    return RecentQuote(
        symbol=str(row.get("symbol") or symbol),
        sina_symbol=str(row.get("source_symbol") or ""),
        price=str(row.get("price") or ""),
        prev_price=str(row.get("prev_price") or ""),
        name=str(row.get("name") or ""),
        date=_normalize_quote_date(row.get("quote_date")),
        time=_normalize_quote_time(row.get("quote_time")),
        source=str(row.get("source") or ""),
        fetched_at=fetched_epoch,
        orderbook=decode_orderbook(row.get("orderbook")),
    )


def _parse_epoch(value):
    """把 ``realtimequote.fetched_at``（数据库返回的时间）转成 epoch 秒。

    失败返回 ``None``，调用方据此判定"这条快照不可信"。
    """
    import datetime as _datetime

    if value is None:
        return None
    if isinstance(value, _datetime.datetime):
        return value.timestamp()

    text = str(value).strip()
    candidates = [text, text[:19], text[:26]]
    for candidate in candidates:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return _datetime.datetime.strptime(candidate, fmt).timestamp()
            except ValueError:
                continue
    return None


class QuotesUpdater:
    """抓取实时行情并缓存，供 Web 页面直读。

    ``symbols_provider`` 是返回 ``[(符号, 数据源代码, stock_id), ...]`` 的可调用对象
    （也兼容 ``[(符号, 数据源代码), ...]``；此时不写库，只用内存缓存）。

    ``store`` 传入 ``quotestore.RealtimeQuoteStore`` 时，每轮抓取会把最新快照写进
    ``realtimequote`` 表 —— **这是让"后台抓取进程"与"Web 进程"共享数据的唯一方式**，
    因为两个进程的内存缓存互不可见。Web 侧读缓存未命中时会回落到读表。
    """

    def __init__(
        self,
        source: Any = None,
        symbols_provider=None,
        ttl: int = None,
        clock=None,
        store: Any = None,
        est_store: Any = None,
        est_symbols: Sequence = None,
    ):
        self.source = source or quotesource.get_quote_source()
        self.symbols_provider = symbols_provider or (lambda: [])
        self.ttl = int(ttl if ttl is not None else os.getenv(ENV_INTERVAL, DEFAULT_TTL_SECONDS))
        self.clock = clock or time.time
        self.store = store
        # 中间层快照表（estquote）。命中 est_symbols 的符号除了写 realtimequote，
        # 再写一份到 estquote 供 QDII 估值用 —— 对应 PHP 的 _yahooStockGetData()。
        # 分表是为了不把"中间层估值序列"混进"基金单位净值"，见 sql/schema.sql。
        self.est_store = est_store
        self.est_symbols = set(est_symbols or ())
        self._cache: Dict[str, RecentQuote] = {}
        self._lock = threading.Lock()
        self.last_error = None
        self.last_run_at = 0.0
        self.last_updated_count = 0

    # -- 缓存读取 ---------------------------------------------------------
    def get(self, symbol: str, ttl: int = None) -> Any:
        """取一只符号的实时行情；过期或没有则返回 ``None``。

        顺序：先看本进程内存缓存，未命中再读 ``realtimequote`` 表。
        读表这一步是多进程共享的关键——后台抓取进程写、Web 进程读。
        判定用的是注入的 ``clock``，这样测试可以控制过期。
        """
        limit = self.ttl if ttl is None else ttl
        now = self.clock()

        with self._lock:
            quote = self._cache.get(symbol)
        if quote is not None and quote.is_fresh(limit, now=now):
            return quote

        stored = self._load_from_store(symbol, limit, now)
        if stored is not None:
            self.put(stored)
            return stored
        return None

    def _load_from_store(self, symbol: str, limit: int, now: float) -> Any:
        """从数据库快照表读一条；不可用或过期时返回 ``None``。"""
        if self.store is None:
            return None
        try:
            row = self.store.load(symbol)
        except Exception:
            return None
        if not row:
            return None

        fetched_epoch = _parse_epoch(row.get("fetched_at"))
        if fetched_epoch is None:
            return None
        # 用"抓取时刻"判断新鲜度；存的是本地时间，直接转 epoch 比较
        if (now - fetched_epoch) >= limit:
            return None

        return _quote_from_row(row, symbol, fetched_epoch)

    def get_last_stored(self, symbol: str) -> Any:
        """取 ``realtimequote`` 里该符号**最近一次**快照，**不做过期判断**。

        与 :meth:`get` 的区别：``get`` 的 TTL 是给"要不要重新抓"用的（默认仅
        60 秒），过期就返回 ``None``；而列表页要显示"最近一次行情是什么时候的"，
        即使它已经不新鲜也比回退到 `stockhistory` 的收盘价更有信息量。

        所以这个方法总是返回库里那条快照（带 ``fetched_at``，调用方自己判断
        新鲜度）。没有抓取进程运行时，页面因此仍能显示上一次抓到的行情与时间，
        而不是显示一列空白。
        """
        if self.store is None:
            return None
        try:
            row = self.store.load(symbol)
        except Exception:
            return None
        if not row:
            return None
        fetched_epoch = _parse_epoch(row.get("fetched_at"))
        return _quote_from_row(row, symbol, fetched_epoch or 0.0)

    def is_stored_fresh(self, quote: Any, ttl: int = None) -> bool:
        """``get_last_stored()`` 拿到的快照是否还在 TTL 内。"""
        if quote is None:
            return False
        limit = self.ttl if ttl is None else ttl
        return (self.clock() - getattr(quote, "fetched_at", 0.0)) < limit

    def get_all(self, ttl: int = None) -> Dict[str, RecentQuote]:
        """取全部未过期的实时行情。

        与 :meth:`get` 一样分两级：先看本进程内存，再用 ``realtimequote`` 表补齐
        内存里没有的符号。**跨进程共享就靠这一步** —— 后台抓取进程写表、
        Web 进程这里读表，否则 Web 只能看到自己抓过的那几只。
        """
        now = self.clock()
        limit = self.ttl if ttl is None else ttl

        with self._lock:
            result = {
                symbol: quote
                for symbol, quote in self._cache.items()
                if quote.is_fresh(limit, now=now)
            }

        for symbol, quote in self._load_all_from_store(limit, now).items():
            result.setdefault(symbol, quote)

        # 读回来的补进内存，减少后续查库
        for symbol, quote in result.items():
            if symbol not in self._cache:
                self.put(quote)
        return result

    def _load_all_from_store(self, limit: int, now: float) -> Dict[str, RecentQuote]:
        """从数据库快照表读全部未过期行情；不可用时返回空字典。"""
        if self.store is None:
            return {}
        try:
            rows = self.store.load_all()
        except Exception:
            return {}

        result = {}
        for row in rows:
            fetched_epoch = _parse_epoch(row.get("fetched_at"))
            if fetched_epoch is None or (now - fetched_epoch) >= limit:
                continue
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            result[symbol] = RecentQuote(
                symbol=symbol,
                sina_symbol=str(row.get("source_symbol") or ""),
                price=str(row.get("price") or ""),
                prev_price=str(row.get("prev_price") or ""),
                name=str(row.get("name") or ""),
                date=str(row.get("quote_date") or ""),
                time=str(row.get("quote_time") or ""),
                source=str(row.get("source") or ""),
                fetched_at=fetched_epoch,
            )
        return result

    def put(self, quote: RecentQuote) -> None:
        """写入缓存（供测试或外部数据源使用）。"""
        with self._lock:
            self._cache[quote.symbol] = quote

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def describe(self) -> Dict[str, Any]:
        """诊断信息：数据源、缓存条数、最近一次运行情况。"""
        with self._lock:
            symbols = sorted(self._cache)
        return {
            "source": self.source.describe() if hasattr(self.source, "describe") else {"name": getattr(self.source, "name", "?")},
            "ttl_seconds": self.ttl,
            "cached": len(symbols),
            "cached_symbols": symbols,
            "last_run_at": self.last_run_at,
            "last_updated_count": self.last_updated_count,
            "last_error": self.last_error,
            "refresh_on_request": os.getenv(ENV_REFRESH_ON_REQUEST, "0") == "1",
        }

    # -- 抓取 -------------------------------------------------------------
    def update(self, pairs: Sequence = None) -> int:
        """抓一批并更新缓存，返回成功更新的条数。

        ``pairs`` 每项是 ``(符号, 数据源代码[, stock_id])``。失败时不改动缓存，
        把错误记在 ``last_error`` 里（对应 PHP 抓取失败返回 false 后不做处理）。
        """
        if pairs is None:
            pairs = self._provider_pairs()
        wanted = [_normalize_pair(item) for item in pairs]
        wanted = [item for item in wanted if item[0] and item[1]]
        if not wanted:
            self.last_updated_count = 0
            return 0

        sina_symbols = [item[1] for item in wanted]
        try:
            lines = self.source.fetch_lines(sina_symbols)
        except Exception as exc:  # 数据源实现有 bug 时也不要拖垮调用方
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self.last_run_at = self.clock()
            self.last_updated_count = 0
            return 0

        if lines is None:
            self.last_error = "数据源返回失败（None）"
            self.last_run_at = self.clock()
            self.last_updated_count = 0
            return 0

        self.last_error = None
        source_name = getattr(self.source, "name", "unknown")
        now = self.clock()
        updated = 0
        for symbol, sina_symbol, stock_id in wanted:
            line = lines.get(sina_symbol)
            if not line:
                continue
            reference = parse_symbol(sina_symbol, line, source_name)
            quote = quote_from_reference(symbol, sina_symbol, reference, source_name, now=now)
            self.put(quote)
            self._save_to_store(stock_id, quote)
            updated += 1

        self.last_run_at = now
        self.last_updated_count = updated
        return updated

    def _save_to_store(self, stock_id: Any, quote: RecentQuote) -> None:
        """把快照写进 ``realtimequote``；命中中间层名单时再写 ``estquote``。"""
        if not stock_id:
            return

        if self.store is not None:
            try:
                self.store.save(
                    stock_id,
                    quote.symbol,
                    quote.sina_symbol,
                    quote.price,
                    quote.prev_price,
                    quote.name,
                    quote.date,
                    quote.time,
                    quote.source,
                    quote.fetched_at,
                    quote.orderbook,
                )
            except Exception as exc:
                # 写库失败不影响内存缓存可用，但要让调用方看得见
                self.last_error = "写实时行情失败：%s: %s" % (type(exc).__name__, exc)

        # 中间层数值单独一份（对应 PHP _yahooStockGetData 写 netvaluehistory 的行为，
        # 但这里不污染净值表）
        if self.est_store is not None and quote.symbol in self.est_symbols:
            try:
                self.est_store.save(
                    stock_id,
                    quote.symbol,
                    quote.sina_symbol,
                    quote.price,
                    quote.prev_price,
                    quote.date,
                    quote.time,
                    quote.source,
                    quote.fetched_at,
                )
            except Exception as exc:
                self.last_error = "写中间层数值失败：%s: %s" % (type(exc).__name__, exc)

    def update_if_stale(self, pairs: Sequence = None) -> int:
        """只在缓存整体过期时才抓（定时 tick 用），返回更新条数。"""
        now = self.clock()
        if (now - self.last_run_at) < self.ttl:
            return 0
        return self.update(pairs)

    def tick(self) -> int:
        """定时器每轮调用一次；等价于"到点就抓一批"。"""
        return self.update_if_stale()

    def _provider_pairs(self) -> List:
        """安全地调用符号来源：出错时返回空列表并记下错误。

        这样"取符号列表失败"不会让整个页面请求 500 —— 与 PHP 抓不到行情时
        只是页面少一列数据的表现一致。
        """
        try:
            return list(self.symbols_provider())
        except Exception as exc:
            self.last_error = "取符号列表失败：%s: %s" % (type(exc).__name__, exc)
            return []


# ---------------------------------------------------------------------------
# 默认的符号收集：从库里取"最近有行情"的符号
# ---------------------------------------------------------------------------
def collect_symbols_from_source(fund_source, limit: int = 0) -> List:
    """从库里收集要抓实时行情的符号。

    PHP 的做法（``StockPrefetchArrayData`` 之前的 ``_addFundPairSymbol`` 等）是
    按当前页面涉及的符号逐个收集；Python 这边页面是分组列表，所以改成
    "取 stockhistory 里最近有数据的符号"，覆盖当前真正在跟的基金。

    返回 ``[(符号, 数据源代码, stock_id), ...]``。带上 ``stock_id`` 是为了能把
    快照写进 ``realtimequote`` 表。
    """
    pairs = []
    conn = getattr(fund_source, "connection", None)
    if conn is None:
        return pairs

    cursor = conn.cursor()
    try:
        # ① 有行情历史的符号（基金、ETF、指数）
        sql = (
            "SELECT s.id AS stock_id, s.symbol AS symbol FROM stock s "
            "INNER JOIN stockhistory h ON h.stock_id = s.id "
            "GROUP BY s.id, s.symbol ORDER BY s.symbol"
        )
        if limit:
            sql += " LIMIT %d" % int(limit)
        cursor.execute(sql)
        rows = list(cursor.fetchall())

        # ② **期货符号**：它们没有 stockhistory（不落那张表），
        # 但如果漏掉，"实时 EST = 参考 EST + 期货"就拿不到期货价。
        # 用 UNION 的味道在 Python 侧合并，避免 SQL 变复杂。
        have = {row["stock_id"] for row in rows}
        try:
            cursor.execute(
                "SELECT u.id AS stock_id, u.symbol AS symbol FROM futurespair fp "
                "JOIN stock u ON u.id = fp.stock_id ORDER BY u.symbol"
            )
            for row in cursor.fetchall():
                if row["stock_id"] not in have:
                    have.add(row["stock_id"])
                    rows.append(row)
        except Exception:
            # futurespair 不存在（尚未建表）时忽略，不影响原有行为
            pass
    finally:
        cursor.close()

    for row in rows:
        symbol = str(row["symbol"])
        # **用 get_quote_symbol 而不是 StockSymbol.get_sina_symbol()**：
        # 后者会给 `hf_NQ` 这类外盘期货加上 `gb_` 前缀（`gb_hf_nq`），
        # 新浪会当成美股代码而返回空 —— 期货的代码就是裸符号。
        # 详见 futuresquotes.get_quote_symbol()。
        from source.futuresquotes import get_quote_symbol

        sina_symbol = get_quote_symbol(symbol)
        if sina_symbol:
            pairs.append((symbol, sina_symbol, row["stock_id"]))
    return pairs


# ---------------------------------------------------------------------------
# 市场时段判断：决定"该不该在这个点抓"
# ---------------------------------------------------------------------------
def is_any_market_open(symbols: Sequence, now: _datetime.datetime = None) -> bool:
    """给的一批符号里，是否有任意一个处于交易时段。

    对应 PHP ``StockNeedNewQuotes()`` 里 ``IsStockMarketTrading()`` 的判断，
    用 ``stocksymbol.StockSymbol`` 的现成实现（已与 PHP 逐项对照过）。
    """
    moment = now or _datetime.datetime.now()
    for symbol in symbols:
        try:
            if StockSymbol(symbol).is_stock_market_trading(moment):
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# 进程级单例（Web 与定时器共用同一份缓存）
# ---------------------------------------------------------------------------
_updater = None
_updater_lock = threading.Lock()


def get_updater() -> QuotesUpdater:
    """取进程级更新器（带数据库快照表，跨进程共享）。

    创建时**不会**连库取符号列表：那是惰性的，第一次真正抓取时才去取，
    这样 Web 页面在数据库不可用时也不会因为初始化就报错。
    """
    global _updater
    if _updater is None:
        with _updater_lock:
            if _updater is None:
                _updater = build_default_updater()
    return _updater


def reset_updater() -> None:
    """清掉进程级更新器（测试用）。"""
    global _updater
    with _updater_lock:
        _updater = None


def _default_symbols_provider():
    """默认符号来源：连库取最近有行情的符号；连不上库就返回空列表。"""
    try:
        from core.stockdata import MySqlFundDataSource

        source = MySqlFundDataSource.from_env()
        return collect_symbols_from_source(source)
    except Exception:
        return []


#: 持仓型估值用到的中间层标的，不在 ``qdii_est_symbol_map()`` 里。
#: 来源：PHP ``holdingsref.php`` 的 ``GetRealtimeRefArray()`` / ``UseRealtimeEst()``
#: 与 ``_qdiimix.php:33`` 对 ``INDA`` 的显式处理。已实测新浪全部覆盖
#: （gb_inda / gb_ibb / gb_uso）。持仓型估值本身暂缓，名单先留着。
HOLDINGS_EST_SYMBOLS = ("INDA", "IBB", "USO")


def default_est_symbols() -> List:
    """中间层标的名单（要写 ``estquote`` 的符号）。

    三部分合起来才完整：

    1. ``fundcalc.qdii_est_symbol_map()`` 的**值集合** —— 显式映射的中间层标的
       （XOP / IBB / VNQ / ^DJI / ^GSPC / ^NDX …）；
    2. **QQQ 与 SPY** —— 它们没有被显式映射，而是作为默认值走
       ``qdii_qqq_symbols()`` / ``qdii_spy_symbols()`` 两条路径，
       不补上就会让最大的两类 QDII 基金反而没有中间层数据；
    3. :data:`HOLDINGS_EST_SYMBOLS` —— 持仓型估值用的标的。

    自动推导而不是手写列表，是为了以后加基金时不用记得同步这里。
    """
    symbols = set()
    try:
        from calc.fundcalc import qdii_est_symbol_map

        symbols.update(str(value) for value in qdii_est_symbol_map().values())
    except Exception:
        pass
    symbols.update(("QQQ", "SPY"))
    symbols.update(HOLDINGS_EST_SYMBOLS)
    return sorted(symbols)


def build_default_updater(**kwargs) -> QuotesUpdater:
    """构造"生产用"更新器：带数据库快照表，实现跨进程共享。

    Web 与定时抓取脚本都应该用这个（或 ``get_updater()``），
    这样抓取进程写的行情 Web 进程能立刻读到。
    """
    from sql.quotestore import EstQuoteStore, RealtimeQuoteStore

    connection = None
    store = None
    est_store = None
    try:
        import pymysql

        from core.stockdata import load_mysql_env, mysql_config_from_env

        config = mysql_config_from_env(load_mysql_env(os.environ))
        connection = pymysql.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
            charset="utf8",
            cursorclass=pymysql.cursors.DictCursor,
        )
        store = RealtimeQuoteStore(connection)
        est_store = EstQuoteStore(connection)
    except Exception:
        # 数据库不可用时退化为纯内存缓存，页面仍可读 stockhistory 的收盘价
        store = None
        est_store = None

    es_store = kwargs.pop("est_store", est_store)
    es_symbols = kwargs.pop("est_symbols", None)
    if es_symbols is None:
        es_symbols = default_est_symbols()

    updater = QuotesUpdater(
        source=kwargs.pop("source", None),
        symbols_provider=kwargs.pop("symbols_provider", _default_symbols_provider),
        store=kwargs.pop("store", store),
        est_store=es_store,
        est_symbols=es_symbols,
        **kwargs
    )
    updater.connection = connection  # 交给使用方自行关闭（进程级单例则随进程结束）
    return updater
