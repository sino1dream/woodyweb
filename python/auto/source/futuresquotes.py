"""新浪**外盘期货**行情（实时 EST 的数据源）。

对应 PHP：

* ``php/stock/stockref.php:447`` ``LoadSinaFutureData()``
* ``php/stock/stockref.php:413`` ``_onSinaFuture($ar)`` 的字段映射
* ``php/stock/qdiiref.php`` 的 ``EstRealtimeNetValue()`` 里那句
  ``if ($this->GetRealtimeRef())`` —— **实时 EST = 参考 EST + 期货**

## 关键结论：用**裸符号**取数，不要走 ``get_sina_symbol()``

``StockSymbol('hf_NQ').get_sina_symbol()`` 会返回 ``gb_hf_nq``
（因为 ``GetSinaSymbol()`` 最后那句兜底给所有非 A/H/指数符号加 ``gb_``，
PHP 的 ``GetSinaSymbol()`` 也一样）。**这是错的**。

PHP 的期货取数用的是 ``DebugGetSinaFileName($strSymbol)`` —— 即
**文件名就是符号本身**（``hf_NQ``），所以新浪认的也是裸符号。

本项目 2026-10-07 实测确认：

```text
GET https://hq.sinajs.cn/list=hf_NQ
    var hq_str_hf_NQ="31365.390,,31349.500,31350.000,31521.000,31143.000,
                      23:44:39,31483.250,31510.000,0,3,30,2026-10-07,纳斯达克100指数期货,0";
```

字段数与 PHP ``_onSinaFuture`` 的假设**完全一致**（15 个），映射为：

=====  ==========================
下标    含义
=====  ==========================
``0``  最新价
``4``  最高
``5``  最低
``6``  时间（``HH:MM:SS``）
``7``  昨收
``8``  开盘
``12`` 日期（``YYYY-MM-DD``）
``13`` 名称（GB18030 编码）
=====  ==========================
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Sequence

from source.sinaquotes import SinaQuoteClient, decode_sina_content, get_default_client, split_quote_lines


#: 字段下标，直接对应 PHP ``_onSinaFuture($ar)``。
#: 抽成常量是为了"下标来源"可追溯 —— 这些数字在 PHP 里是硬编码的。
FIELD_PRICE = 0
FIELD_HIGH = 4
FIELD_LOW = 5
FIELD_TIME = 6
FIELD_PREV_PRICE = 7
FIELD_OPEN = 8
FIELD_DATE = 12
FIELD_NAME = 13

#: 最少字段数。PHP 里是 ``if (count($ar) >= 13)``，实测返回 15 个。
MIN_FIELD_COUNT = 13


@dataclass
class FuturesQuote:
    """一条外盘期货行情。

    字段名与 PHP ``MyStockReference`` 的对应属性一致（``strPrice`` / ``strPrevPrice`` 等），
    便于与 PHP 逐项对照。
    """

    symbol: str            # 库里符号，如 hf_NQ
    sina_symbol: str       # 新浪代码，与 symbol 相同（裸符号）
    price: str = ""
    prev_price: str = ""
    high: str = ""
    low: str = ""
    open: str = ""
    date: str = ""
    time: str = ""
    name: str = ""

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "sina_symbol": self.sina_symbol,
            "price": self.price,
            "prev_price": self.prev_price,
            "high": self.high,
            "low": self.low,
            "open": self.open,
            "date": self.date,
            "time": self.time,
            "name": self.name,
        }

    def fetched_datetime(self) -> Any:
        """把 ``date`` + ``time`` 合成 ``datetime``；缺任一项返回 ``None``。"""
        if not self.date or not self.time:
            return None
        text = "%s %s" % (self.date, self.time[:8])
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None


def is_futures_symbol(symbol: str) -> bool:
    """该符号是不是外盘期货（``hf_`` 前缀）。

    用 ``StockSymbol`` 判定，保持与 PHP ``IsSinaFutureUS()`` 同源。
    """
    if not symbol:
        return False
    from core.stocksymbol import StockSymbol

    try:
        return bool(StockSymbol(symbol).is_sina_future_us())
    except Exception:
        return False


def get_quote_symbol(symbol: str) -> str | None:
    """取该符号在**新浪行情接口**里该用的代码。

    这是一个容易踩的坑：``StockSymbol.get_sina_symbol()`` 的最后一句是
    "给所有非 A/H/指数符号加 ``gb_`` 前缀"，于是：

    ```text
    StockSymbol('hf_NQ').get_sina_symbol()  ->  'gb_hf_nq'   ← 错的，取不到数据
    StockSymbol('hf_NQ').get_quote_symbol() ->  'hf_NQ'      ← 对的
    ```

    PHP 侧的 ``GetSinaSymbol()`` 有同样的兜底，**所以 PHP 的期货取数也没走它** ——
    ``LoadSinaFutureData()`` 用的是 ``DebugGetSinaFileName($strSymbol)``，
    文件名就是裸符号。本项目实测确认新浪认的就是 ``hf_NQ`` 这个裸符号。

    非期货符号照常走 ``get_sina_symbol()``。
    """
    if not symbol:
        return None
    if is_futures_symbol(symbol):
        return symbol
    from core.stocksymbol import StockSymbol

    try:
        return StockSymbol(symbol).get_sina_symbol()
    except Exception:
        return None


def parse_futures_payload(symbol: str, payload: str) -> Any:
    """把 ``hq_str_hf_NQ`` 引号里的内容解析成 :class:`FuturesQuote`。

    字段不足 ``MIN_FIELD_COUNT``（13）时返回 ``None`` —— 与 PHP
    ``if (count($ar) >= 13)`` 一致，避免把半截响应当行情用。
    """
    fields = payload.split(",")
    if len(fields) < MIN_FIELD_COUNT:
        return None

    def field(index: int) -> str:
        return fields[index].strip() if index < len(fields) else ""

    date = field(FIELD_DATE)
    time = field(FIELD_TIME)
    date, time = convert_to_symbol_time_zone(symbol, date, time)

    return FuturesQuote(
        symbol=symbol,
        sina_symbol=symbol,
        price=field(FIELD_PRICE),
        prev_price=field(FIELD_PREV_PRICE),
        high=field(FIELD_HIGH),
        low=field(FIELD_LOW),
        open=field(FIELD_OPEN),
        date=date,
        time=time,
        name=field(FIELD_NAME),
    )


def convert_to_symbol_time_zone(symbol: str, date: Any, time: Any):
    """把新浪给的 ``(日期, 时刻)`` 换到**该符号所在市场**的时区。

    对应 PHP ``_onSinaFuture()`` → ``_convertDateTimeToUS()``：新浪的期货
    时间戳是**上海时间**，PHP 先按上海解读、再换到本符号时区。

    ```php
    function _convertDateTimeToUS($strDate, $strTime) {
        $iTime = strtotime($strDateTime);
        $this->strTime = DebugGetTime($iTime, 'America/New_York');
        $this->strDate = $strYear.date('-m-d', $iTime);
    }
    ```

    ## 曾经漏了这一步，后果是期货永远校准不上

    ``parse_futures_payload`` 直接把新浪原值当成了本符号的本地时间。实测
    （2026-10-15 01:05 北京时间）新浪给的是 ``2026-10-15 01:04:19``，
    换算到纽约是 ``2026-10-14 13:04:19`` —— **差 12 小时、还跨了一天**。

    而 :func:`~calc.futurescalib.check_adjust_time` 要拿它跟指数的纽约时间比
    "日期 + HH:MM"，于是：期货 ``2026-10-15 01:04`` vs 指数
    ``2026-10-14 13:04`` → 永远不等 → ``AverageCalibraion()``
    永远不触发 → ``realtimequote`` 里躺着期货价、``calibrationhistory``
    里却没有期货校准值 → 实时 EST 永远回落到中间层快照。

    换算失败时**原样返回**（不编造），由调用方自己判断。
    """
    if not date or time in (None, ""):
        return date, time
    try:
        from core.stocksymbol import TIME_ZONE_SHANGHAI, StockSymbol
        from ref.stockref import _parse_datetime, format_date, format_time

        target = StockSymbol(symbol).get_time_zone()
        moment = _parse_datetime("%s %s" % (date, time), TIME_ZONE_SHANGHAI)
        return format_date(moment, target), format_time(moment, target)
    except Exception:
        return date, time


def parse_futures_text(text: str) -> dict:
    """从整批响应里解析出 ``{符号: FuturesQuote}``。"""
    result = {}
    for symbol, line in split_quote_lines(text).items():
        start = line.find('"')
        end = line.rfind('"')
        if start < 0 or end <= start:
            continue
        quote = parse_futures_payload(symbol, line[start + 1:end])
        if quote is not None:
            result[symbol] = quote
    return result


class SinaFuturesClient:
    """抓取外盘期货行情。

    ``client`` 可注入（测试用假客户端）。默认走 ``sinaquotes.get_default_client()``。
    """

    def __init__(self, client: SinaQuoteClient = None, fetcher: Any = None):
        self.client = client
        self._fetcher = fetcher

    def fetch(self, symbols: Sequence[str]) -> dict:
        """抓一批期货，返回 ``{符号: FuturesQuote}``。

        **用裸符号请求**（``hf_NQ``），不走 ``get_sina_symbol()`` ——
        后者会加上 ``gb_`` 前缀，新浪会当成美股代码而返回空。
        """
        wanted = [symbol for symbol in symbols if symbol]
        if not wanted:
            return {}
        if self._fetcher is not None:
            text = self._fetcher(wanted)
        else:
            client = self.client or get_default_client()
            text = client.fetch(wanted)
        # 客户端可能返回 bytes（复用 sinaquotes 的解码顺序：先 UTF-8 再 GB18030）
        if isinstance(text, (bytes, bytearray)):
            text = decode_sina_content(text)
        if not text:
            return {}
        return parse_futures_text(text)

    def fetch_one(self, symbol: str) -> Any:
        """抓单个，取不到返回 ``None``。"""
        return self.fetch([symbol]).get(symbol)


def get_default_futures_client() -> SinaFuturesClient:
    """缺省客户端（进程级单例由调用方缓存）。"""
    return SinaFuturesClient()


#: 常见外盘期货的中文名，供 ``--api`` 与文档展示。
FUTURES_NAMES = {
    "hf_NQ": "纳斯达克100指数期货（E-mini Nasdaq 100）",
    "hf_ES": "标普500指数期货（E-mini S&P 500）",
    "hf_CL": "纽约原油（WTI）",
    "hf_NK": "日经225指数期货",
    "hf_HSI": "恒生指数期货",
}
