"""新浪行情抓取（HTTP 层）。

对应 PHP 源码：

* ``php/stock.php`` 的 ``GetSinaQuotes()`` —— 批量取行情文本；
* ``php/externalurl.php`` 的 ``GetSinaDataUrl()`` / ``GetSinaFinanceUrl()`` —— 拼 URL；
* ``php/url.php`` 的 ``url_get_contents()`` —— curl 取内容（含 Referer、跟随跳转）。

这一层只负责"把行情的原始文本取回来"，**不解析**：解析在 ``stockref.py``
（``explode_quote()`` 与各 ``_on_sina_data_*()``）。分开的好处是解析逻辑可以
用固定样本离线测试，抓取失败也不会污染解析结果。

为什么要单独的模块：``stockref`` 与 ``stockprefetch`` 当初都刻意把 IO 做成回调
（``quote_loader`` / ``fetch_quotes``），缺的正是这个把它们接到网络上的模块。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Iterable, Mapping, Sequence

from httpjson import DEFAULT_USER_AGENT

# hq.sinajs.cn 支持一次请求多个符号，用逗号分隔
SINA_DATA_URL = "http://hq.sinajs.cn/list="
SINA_FINANCE_URL = "https://finance.sina.com.cn"

# 各批行情共用的请求头。Referer 必须带，否则新浪会拒绝（实测返回 403）
DEFAULT_REFERER = SINA_FINANCE_URL

# 与 PHP 的 `$timeout = 2` 不同：那是个连接超时且偏激进，Python 这边给足
# 连接与整体超时，避免偶发慢响应被当成失败。
DEFAULT_CONNECT_TIMEOUT = 3.0
DEFAULT_READ_TIMEOUT = 10.0

# 缓存有效期。PHP 用「缓存文件 60 秒内不重取」实现同样效果（stockprefetch 里
# NeedFile() 的 interval 默认 SECONDS_IN_MIN）。
DEFAULT_CACHE_SECONDS = 60


def get_sina_data_url(symbols: Sequence[str]) -> str:
    """对应 PHP ``GetSinaDataUrl()``：拼出批量行情 URL。"""
    return SINA_DATA_URL + ",".join(symbols)


class SinaQuoteClient:
    """批量抓取新浪行情。

    用法::

        client = SinaQuoteClient()
        text = client.fetch(["sh600000", "gb_xop"])   # 原始文本，逐行对应符号
        text = client.load("debug/sina/gb_xop.txt")   # 取单个符号（stockref 的 loader）

    缓存在内存里按"整批请求"做，响应与 ``symbols`` 顺序一一对应，直接喂给
    ``stockprefetch._prefetch()`` 就能逐行回写。
    """

    def __init__(
        self,
        session: Any = None,
        timeout: float = DEFAULT_READ_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        cache_seconds: int = DEFAULT_CACHE_SECONDS,
        referer: str = DEFAULT_REFERER,
    ):
        self.session = session
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.cache_seconds = cache_seconds
        self.referer = referer
        # symbol -> (取回时刻, 原始文本)
        self._symbol_cache: dict = {}
        # 文件名 -> 符号（由 cache_quote_files 建立，供 stockref 的 loader 使用）
        self._file_symbols: dict = {}

    # -- 请求 -------------------------------------------------------------
    def fetch(self, symbols: Sequence[str]) -> Any:
        """取一批符号的行情原始文本；失败返回 ``None``。

        返回的文本按行排列，与 ``symbols`` 的顺序一致（新浪保证这个顺序），
        这正是 ``stockprefetch._prefetch()`` 期望的形式。
        """
        wanted = [symbol for symbol in symbols if symbol]
        if not wanted:
            return None

        now = time.time()
        lines = []
        missing = []
        for symbol in wanted:
            cached = self._symbol_cache.get(symbol)
            if cached is not None and (now - cached[0]) < self.cache_seconds:
                lines.append(cached[1])
            else:
                missing.append(symbol)
                lines.append(None)

        if missing:
            fetched = self._request(missing)
            if fetched is None:
                return None
            for symbol, line in fetched.items():
                self._symbol_cache[symbol] = (now, line)
            for index, symbol in enumerate(wanted):
                if lines[index] is None:
                    lines[index] = self._symbol_cache.get(symbol, (now, ""))[1]

        return "\n".join(lines)

    def _request(self, symbols: Sequence[str]) -> Any:
        """真正发起 HTTP 请求，返回 ``{符号: 行文本}``。"""
        url = get_sina_data_url(symbols)
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": self.referer,
        }
        try:
            if self.session is not None:
                response = self.session.get(url, headers=headers, timeout=self.timeout)
            else:
                import requests

                response = requests.get(
                    url,
                    headers=headers,
                    timeout=(self.connect_timeout, self.timeout),
                )
            response.raise_for_status()
        except Exception as exc:
            # 与 PHP 一致：抓取失败不抛异常，交给调用方决定（PHP 里返回 false）
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return None

        self.last_error = None
        content = getattr(response, "content", None)
        if content is None:
            text = response.text
        else:
            # 新浪返回 GBK（PHP 注释里写 GB2312），符号名可能是中文
            text = decode_sina_content(content)
        return split_quote_lines(text)

    # -- 给 stockref 用的 loader ------------------------------------------
    def cache_quote_files(self, file_names: Mapping[str, str]) -> None:
        """登记"缓存文件名 -> 新浪符号"的映射，供 :meth:`load` 使用。"""
        self._file_symbols.update(file_names)

    def load(self, file_name: str) -> Any:
        """按缓存文件名取单个符号的行情文本，对应 PHP ``PrefetchLoadSinaData()``。

        没登记过映射时返回 ``None``（等于"没有缓存"），与 PHP 判断文件不存在的
        行为一致。
        """
        symbol = self._file_symbols.get(file_name)
        if symbol is None:
            return None
        text = self.fetch([symbol])
        if text is None:
            return None
        return text.split("\n")[0]

    def make_fetch_callback(self):
        """返回可直接传给 ``stockprefetch.prefetch_sina_stock_data()`` 的回调。"""
        return self.fetch

    def make_store_callback(self, save_quote):
        """返回可直接传给 ``_prefetch()`` 的保存回调。

        ``save_quote(file_name, data)`` 由调用方提供（写文件或写库）。
        """
        return save_quote


def decode_sina_content(content: bytes) -> str:
    """解码新浪响应。

    策略：**先试 UTF-8，再回退 GB18030**。顺序不能反 —— ``gb18030`` 几乎能
    把任意字节序列解成"看似合法"的字符串（不会抛 ``UnicodeDecodeError``），
    所以先试它会把 UTF-8 的港美股响应解成乱码。PHP 那边用 curl + GB2312 转换，
    也是同样的问题根源。

    ``hq.sinajs.cn`` 的 A 股/基金响应是 GBK（PHP 注释写"GB2312"，实际是 GBK 超集），
    这类内容 UTF-8 解码必然失败，于是自然落到 GB18030 分支。
    """
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("gb18030", errors="replace")


def split_quote_lines(text: str) -> dict:
    """把整批响应切成 ``{新浪符号: 行文本}``。

    响应形如::

        var hq_str_sh600000="浦发银行,9.90,...";
        var hq_str_gb_xop="XOP NAME,168.23,...";

    空行、以及不带 ``var hq_str_`` 前缀的行会被忽略。
    """
    result = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "hq_str_" not in line:
            continue
        start = line.find("hq_str_")
        start += len("hq_str_")
        end = line.find("=", start)
        if end < 0:
            continue
        symbol = line[start:end].strip()
        if symbol:
            result[symbol] = line
    return result


def save_quote_file(directory: str, file_name: str, data: str) -> str:
    """对应 PHP ``PrefetchSaveSinaData()``：把一行行情写成缓存文件。

    行情落库之外还保留文件缓存，是为了与 PHP 行为对齐（PHP 由文件时间判断
    是否需要重取）。落库请用 ``quotestore.QuoteStore``。
    """
    path = os.path.join(directory, file_name) if directory else file_name
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(data)
    return path


# 模块级默认客户端，方便 CLI 与 Web 复用同一份缓存
_default_client = None


def get_default_client() -> SinaQuoteClient:
    """取模块级默认客户端（带进程内缓存）。"""
    global _default_client
    if _default_client is None:
        _default_client = SinaQuoteClient()
    return _default_client


def fetch_sina_quotes(symbols: Iterable[str], client: SinaQuoteClient = None) -> Any:
    """便捷函数：取一批符号的行情文本，对应 PHP ``GetSinaQuotes()``。"""
    return (client or get_default_client()).fetch(list(symbols))


class SinaMarketQuoteSource:
    """新浪数据源，适配 :class:`quotesource.MarketQuoteSource` 接口。

    换行情源时不用改 Web 层：新写一个同样实现 ``fetch_lines()`` 的类并注册即可。
    接口说明见 ``quotesource.py``。
    """

    name = "sina"

    def __init__(self, client: SinaQuoteClient = None, **kwargs):
        # 允许传入自定义 client（测试注入 session），否则用模块级默认客户端
        self.client = client or get_default_client()

    def fetch_raw(self, symbols):
        """取整批原始响应（按行），对应 PHP ``GetSinaQuotes()``。"""
        return self.client.fetch(list(symbols))

    def fetch_lines(self, symbols):
        """取 ``{新浪符号: 行文本}``。

        键就是新浪符号本身（``sz161130``、``gb_qqq``），与 ``stockref`` 期望的一致。
        """
        text = self.client.fetch(list(symbols))
        if text is None:
            return None
        return split_quote_lines(text)

    def describe(self):
        """数据源自述，供诊断输出使用。"""
        return {"name": self.name, "url": SINA_DATA_URL, "referer": self.client.referer}
