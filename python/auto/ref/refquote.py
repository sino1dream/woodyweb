"""引用对象的行情装填：**先读库快照，再兜底联网**。

## 为什么需要这一层

PHP 的 ``MysqlReference`` 构造流程里，行情来自 ``LoadSinaData()`` ——
也就是"每次请求都同步抓新浪"。本项目的部署形态不同：已经有
``quoteupdatercli --daemon`` 按 60 秒把行情写进 ``realtimequote`` 表，
页面应该**读那份快照**（快、离线可用、不给站方压力），抓不到才联网。

这正是本项目一贯的分层：**抓取归抓取进程，页面只读**。

## 两条路各自做什么

========================================  ==========================================
路径                                        动作
========================================  ==========================================
读库（首选）                                从 ``realtimequote`` 取快照，直接装填字段
联网（兜底）                                取新浪原始文本，走 ``stockref`` 既有解析
========================================  ==========================================

## 盘口（五档挂单）也走这两条路

快照表的 ``orderbook`` 列存着买一到买五、卖一到卖五，
读库时由 :func:`apply_order_book` 一并装回引用对象的四个数组，
所以**页面不联网也能渲染五档表**。联网路径本来就会解析出盘口，不用额外处理。

**空字典表示"这次没带盘口信息"**，此时不会去动引用对象上已有的内容 ——
详见 :func:`apply_order_book` 的说明。

## 为什么不把快照拼成原始文本再喂给解析器

快照里存的就是拼装后的字段，
把结构化数据序列化成文本、再解析回来，既绕又容易与新浪的字段顺序脱钩。
直接装填更短、更可读。

## 与 PHP 的行为差异（有意）

PHP 抓不到行情时 ``SetHasData(false)``，表格输出四个空串。
本版读库命中就一定有数据；**两条路都失败**才置 ``has_data = False``，
与 PHP 的降级表现一致。
"""

from __future__ import annotations

from typing import Any

#: 装填后要落到引用对象上的字段名（与 ``stockref.StockReference`` 的属性同名）。
QUOTE_FIELDS = ("str_price", "str_prev_price", "str_date", "str_time", "str_name")


def stem_of(file_name: str) -> str:
    """把缓存文件名还原成"新浪符号的文件名主干"。

    ``stockref.sina_quote_file_name('sz161130')`` 会给出类似
    ``debug/sina/sz161130.txt`` 的路径；这里取出 ``sz161130``。
    传入的本来就是主干时原样返回（便于直接调用的场景）。
    """
    text = str(file_name or "").replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    if text.endswith(".txt"):
        text = text[:-4]
    return text


class SnapshotQuoteSource:
    """从 ``realtimequote`` 快照读行情。

    ``source`` 需要提供 ``get_stored_quote(symbol)`` 与 ``get_stock_id(symbol)``
    （``stockdata.MySqlFundDataSource`` 都有）。
    """

    def __init__(self, source: Any = None):
        self.source = source

    def fetch(self, symbol: str) -> Any:
        """取该符号的快照；没有数据源或没抓到返回 ``None``。"""
        if self.source is None:
            return None
        reader = getattr(self.source, "get_stored_quote", None)
        if reader is None:
            return None
        try:
            return reader(symbol)
        except Exception:
            return None


class NetworkQuoteSource:
    """兜底：抓新浪原始文本，交给 ``stockref`` 既有解析器。

    只在快照没有该符号时才用 —— 它会给站方发请求，别当默认路径。
    """

    def __init__(self, client: Any = None):
        self.client = client

    def fetch_text(self, sina_symbol: str) -> Any:
        """取一行原始行情文本（``var hq_str_xxx="...";``）。取不到返回 ``None``。"""
        if not sina_symbol:
            return None
        client = self.client
        if client is None:
            try:
                from source.sinaquotes import get_default_client
            except ImportError:
                return None
            try:
                client = get_default_client()
            except Exception:
                return None
        try:
            text = client.fetch([sina_symbol])
        except Exception:
            return None
        if not text:
            return None
        if isinstance(text, (bytes, bytearray)):
            try:
                from source.sinaquotes import decode_sina_content

                text = decode_sina_content(text)
            except Exception:
                return None
        # 批量响应可能多行；取含目标符号的那一行
        for line in str(text).splitlines():
            if sina_symbol in line:
                return line
        return str(text).split("\n")[0] if text else None


def apply_snapshot(ref: Any, quote: Any) -> bool:
    """把快照字段装填到引用对象上；装填成功返回 ``True``。

    ``quote`` 是 ``quotestore.RecentQuote`` 形状的对象（有
    ``price`` / ``prev_price`` / ``date`` / ``time`` / ``name``）。
    ``price`` 为空或 0 时**不算装填成功** —— 引用对象的
    ``is_zero_string()`` 判定同样把 ``'0'`` 当"没有行情"，这里保持一致。
    """
    if quote is None:
        return False
    price = getattr(quote, "price", None)
    if price in (None, "") or str(price).strip() in ("0", "0.0", "0.00"):
        return False

    ref.str_price = price
    ref.str_prev_price = getattr(quote, "prev_price", None)
    ref.str_date = getattr(quote, "date", None)
    ref.str_time = getattr(quote, "time", None)
    name = getattr(quote, "name", None)
    if name:
        ref.str_name = name

    setter = getattr(ref, "set_has_data", None)
    if setter is not None:
        try:
            setter(True)
        except Exception:
            pass
    else:
        ref.has_data = True

    apply_order_book(ref, getattr(quote, "orderbook", None))
    return True


#: 引用对象上的四个平行数组 <-> 盘口字典的四个 key（见 ``quotestore.ORDERBOOK_KEYS``）。
_ORDER_BOOK_ATTRS = (
    ("bid_prices", "bid_price"),
    ("bid_quantities", "bid_quantity"),
    ("ask_prices", "ask_price"),
    ("ask_quantities", "ask_quantity"),
)


def apply_order_book(ref: Any, book: Any) -> bool:
    """把快照里的五档盘口写回引用对象；写了返回 ``True``。

    ## 为什么"快照没有盘口"时不去清空

    ``orderbook`` 是空字典时表示**这次快照没带盘口信息**（老库的列还不存在、
    或者这一路行情本来就没有挂单数据）。这时引用对象上可能已经有内容 ——
    比如请求期刚联网补过一次 —— 拿"没有信息"去覆盖"有信息"只会把盘口弄丢。

    所以只在**非空**时覆盖，与 :func:`apply_snapshot` 里价格字段的处理一致
    （那边同样是"价格为空就不算装填成功"）。

    ## 只认引用对象本来就有的属性

    ``StockReference`` 的四个数组在 ``__init__`` 里就建好了；没有这些属性的对象
    （比如只算估值的那一层引用）不该被凭空挂上，跳过即可 ——
    它本来也不会去回答"买一价是多少"。
    """
    if not book:
        return False
    written = False
    for attribute, key in _ORDER_BOOK_ATTRS:
        values = book.get(key)
        if values is None or not hasattr(ref, attribute):
            continue
        setattr(ref, attribute, [str(value or "") for value in values])
        written = True
    return written


def apply_network(ref: Any, network: NetworkQuoteSource = None) -> bool:
    """联网兜底：抓原始文本，交给 ``stockref`` 既有解析器装填。

    装填成功返回 ``True``。**只在请求期显式调用**，不要放进构造流程 ——
    构造每次联网会让每个请求都慢一拍（见 :func:`load_quote_into` 的说明）。
    """
    network = network or NetworkQuoteSource()

    getter = getattr(ref, "get_sina_symbol", None)
    if not callable(getter):
        return False
    try:
        sina_symbol = getter()
    except Exception:
        return False
    if not sina_symbol:
        return False

    text = network.fetch_text(sina_symbol)
    if not text:
        return False

    # 交给 stockref 既有解析器，避免在这里重复实现字段映射
    loader = getattr(ref, "quote_loader", None)
    ref.quote_loader = lambda _file_name, _text=text: _text
    try:
        ref.load_sina_data()
    finally:
        ref.quote_loader = loader
    return bool(getattr(ref, "has_data", False))


def load_quote_into(ref: Any, snapshot: SnapshotQuoteSource = None,
                    network: NetworkQuoteSource = None,
                    source: Any = None, allow_network: bool = False) -> str:
    """给引用对象装填行情，返回实际走的路径。

    返回值是 ``'snapshot'`` / ``'network'`` / ``'none'`` 三者之一，
    便于调用方统计与测试断言 —— 光看 ``has_data`` 分不清是"读到了库"
    还是"联网抓到了"。

    ## ``allow_network`` 默认是 ``False``（重要）

    引用对象**构造**时会调它，而 ``QdiiReference`` 构造里还会算一次实时 EST，
    所以构造期联网会让"每个页面请求都去抓一遍新浪"。因此：

    * **构造期**（默认）：只读库快照，命中即返回，绝不联网；
    * **请求期**：显式传 ``allow_network=True``，快照没有再兜底联网。

    这不是偷懒 —— 与项目既有分层一致：抓取归 daemon，页面读快照，
    联网只是快照缺失时的最后手段。
    """
    snapshot = snapshot or SnapshotQuoteSource(source)
    network = network or NetworkQuoteSource()

    symbol = getattr(ref, "get_symbol", None)
    symbol = symbol() if callable(symbol) else getattr(ref, "str_symbol", None)

    # ① 读库快照（首选，构造期只做这一步）
    if symbol:
        if apply_snapshot(ref, snapshot.fetch(symbol)):
            return "snapshot"

    # ② 联网兜底（默认关闭）
    if allow_network and apply_network(ref, network):
        return "network"

    setter = getattr(ref, "set_has_data", None)
    if setter is not None and not getattr(ref, "has_data", False):
        try:
            setter(False)
        except Exception:
            pass
    return "none"


def build_quote_loader(source: Any = None) -> Any:
    """给 ``refobj`` 用的行情装填回调（签名 ``loader(ref) -> 路径名``）。

    **默认不联网**（只读快照），与构造期不联网的约定一致。
    需要请求期兜底联网时用 :func:`refresh_quotes`。

    形如::

        from ref.refobj import build_qdii_reference
        ref = build_qdii_reference("SZ161130", source=source,
                                   quote_loader=build_quote_loader(source))
    """
    snapshot = SnapshotQuoteSource(source)
    network = NetworkQuoteSource()

    def loader(ref: Any) -> str:
        return load_quote_into(ref, snapshot=snapshot, network=network)

    return loader


def refresh_quotes(refs: Any, source: Any = None) -> dict:
    """**请求期**兜底：给还没行情的引用对象联网补一次。

    对应"页面要用行情，但 daemon 还没抓到该符号"的情形。
    返回 ``{符号: 路径名}`` 便于日志与测试断言。

    设计上刻意做成显式调用（不藏在构造里），这样"什么时候会联网"是可数的。
    """
    snapshot = SnapshotQuoteSource(source)
    network = NetworkQuoteSource()
    result = {}
    for ref in refs:
        if ref is None:
            continue
        if getattr(ref, "has_data", False):
            continue
        symbol = getattr(ref, "get_symbol", None)
        symbol = symbol() if callable(symbol) else getattr(ref, "str_symbol", None)
        result[symbol] = load_quote_into(
            ref, snapshot=snapshot, network=network, allow_network=True)
    return result
