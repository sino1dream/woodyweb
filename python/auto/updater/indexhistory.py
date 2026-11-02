"""抓取美股/美股指数/港股指数的历史日线（新浪 jsonp + 东财 kline 两条信源）。

## 港股为什么走东财，不走新浪

新浪的港股**日线**接口在 2026-11-02 实测已全部下线：`HK_IndexService.getIndexDailyKLine`
/ `HK_StockService.getDayKLine` / `HK_MinlineService.getDayKLine` /
`HistoryTradeService.getHistory*`，两个 host（`stock.finance.sina.com.cn/hkstock`、
`quotes.sina.cn/hk`）都是一律 `{"__ERROR":3,"__ERRORMSG":"Service not valid"}`
或 `"Service not found"`；akshare 那套 `/stock/hkstock/api/...` 旧路径直接 404。
（新浪港股**实时**还好用：`hq.sinajs.cn/list=hkHSI`，见 `estquote` 表。）

东财 `push2his` 的日线是现成的：`secid` 分别为 `100.HSI` / `124.HSTECH`
/ `100.HSCEI`，历史长度 8998 / 2912 / 8212 根。**与新浪实时里的"昨收"逐字一致**
（2026-10-30：恒生 23294.42、恒生科技 3754.90、国企 7862.22），是同源公开行情。

所以本模块按符号**分派**：港股三个指数走东财，其余走新浪。

## 为什么需要

均线表（`smaparagraph`）要 620 天历史：20 个月线至少要 20 个月，
`BOLL_DAYS = 20` 的布林带也要 20 天。而演示库里 **`^NDX` 一行历史都没有** ——
行情 daemon 的 `collect_symbols_from_source()` 是从 `stockhistory` **反推**
要抓哪些符号的，于是 `^NDX` 永远进不了那个集合（鸡生蛋）。

本模块直接按符号抓历史，是打破那个死循环的入口。

## 接口

美股 / 美股指数（新浪）::

    https://stock.finance.sina.com.cn/usstock/api/jsonp.php/.../US_MinKService.getDailyK?symbol=.NDX

返回 JSONP：``IO.XSRV2.CallbackList[]([{"d":"2014-02-18","o":"...","h":"...",
"l":"...","c":"...","v":"...","a":"..."}, ...]);``

* 指数用**点号前缀**：``.NDX``（纳斯达克 100）、``.INX``（标普 500）；
* 普通美股直接用符号：``QQQ``；
* 日期**升序**，最新在最后；
* ``c`` 是收盘价。**没有复权列**，所以 ``adjclose`` 取 ``c``
  （指数与 ETF 的日线本来就按复权价发布，项目里 ``stockhistory.adjclose``
  对指数就是这么用的）。

港股指数（东财）::

    https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=100.HSI&klt=101&fqt=1&lmt=800
    → {"data":{"klines":["2026-10-30,26346.29,26346.29,26489.79,26193.12,2581102,0",...]}}

``klines`` 每行 ``日期,开,收,高,低,成交量,成交额``（请求的 ``fields2=f51..f57``）。

实测 `^NDX` 最新一条 ``2026-10-09 c=30883.15``，与生产端
``sz161130cn.php`` 参考数据表的 ``30883.15`` **逐字一致**。
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Dict, List, Optional

#: 新浪美股日线接口。对应 PHP ``GetYahooStockHistoryUrl()`` 那条的替代
#: （Yahoo 已全面 403，见 ``navsource.py`` 的说明）。
SINA_US_DAILY_URL = ("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/"
                     "IO.XSRV2.CallbackList%5B%5D/US_MinKService.getDailyK?symbol={code}")

#: 东财历史 K 线接口。港股指数只能走这条（新浪港股日线已下线，见模块 docstring）。
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_KLINE_REFERER = "https://quote.eastmoney.com/"

#: ``limit=0``（要全部）时给东财的 ``lmt``。接口要一个具体数字，给大一点即可：
#: 实测恒生 8998 根、国企 8212 根、恒生科技 2912 根，20000 够覆盖。
EASTMONEY_ALL_LIMIT = 20000

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
REFERER = "https://finance.sina.com.cn/"

DEFAULT_TIMEOUT = 20.0

#: 指数在接口里的代码：**点号前缀**，与库内符号（``^NDX``）不同。
INDEX_CODES = {
    "^NDX": ".NDX",
    "^GSPC": ".INX",
    "^DJI": ".DJI",
    "^IXIC": ".IXIC",
}

#: 港股指数 -> 东财 ``secid``（市场号.代码）。
#:
#: 三个都实测过（2026-11-02）：``100`` 是港股主板指数，``124`` 是恒生科技那一族。
#: 键用**库内符号**（与 ``qdii_hk_get_index_symbol_array()`` 一致），
#: 不跟新浪的 ``HSI`` / ``HSCEI`` / ``HSTECH`` 那套裸代码混。
EASTMONEY_HK_SECIDS = {
    "^HSI": "100.HSI",
    "^HSTECH": "124.HSTECH",
    "^HSCE": "100.HSCEI",
}


def quote_code_of(symbol: str) -> str:
    """把库内符号翻成新浪日线接口的代码。

    指数（``^`` 开头）用 :data:`INDEX_CODES` 里的点号代码；其余符号原样使用
    （``QQQ`` / ``SPY`` 这类美股 ETF 直接用符号）。
    """
    text = str(symbol).strip()
    if text in INDEX_CODES:
        return INDEX_CODES[text]
    if text.startswith("^"):
        # 没登记的指数：退化成点号 + 去帽，与新浪的命名习惯一致
        return "." + text[1:]
    return text


def parse_daily_response(text: str) -> List[Dict[str, Any]]:
    """解析 JSONP 响应，返回**日期升序**的 ``[{"date","close","volume"}, ...]``。

    响应形如::

        /*<script>location.href='//sina.com';</script>*/
        IO.XSRV2.CallbackList[]([{"d":"2014-02-18","o":"3668.99",...}, ...]);

    要剥两层：JSONP 的回调壳 ``...([](`` 与 ``]);``。
    直接找**最后一个 ``([``** 之后的 ``[`` 最稳，不依赖回调名。
    """
    marker = text.rfind("([")
    if marker >= 0:
        start = text.find("[", marker)
    else:
        start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return []

    try:
        rows = json.loads(text[start:end + 1])
    except ValueError:
        return []

    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = row.get("d")
        close = row.get("c")
        if not date or close in (None, ""):
            continue
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        volume = row.get("v")
        result.append({
            "date": _normalize_date(str(date)),
            "close": value,
            "volume": _to_int(volume),
        })
    return result


def fetch_daily_history(symbol: str, limit: int = 0, timeout: float = DEFAULT_TIMEOUT,
                        opener: Any = None) -> List[Dict[str, Any]]:
    """抓某个符号的日线历史，**日期升序**。

    对应 PHP ``UpdateYahooHistoryChart()`` 拿到的数据形状。
    ``limit`` 是"最多保留最近多少条"（0 = 全部）。

    港股三个指数（:data:`EASTMONEY_HK_SECIDS`）自动改走东财，其余走新浪
    —— 调用方不用关心是哪条信源。

    抓不到时返回**空列表** —— 调用方按"没补到"处理，不要让它抛异常
    （与 ``forexsource.fetch_history_eastmoney`` 同款约定）。
    """
    if symbol in EASTMONEY_HK_SECIDS:
        return fetch_eastmoney_history(symbol, limit=limit, timeout=timeout, opener=opener)

    code = quote_code_of(symbol)
    url = SINA_US_DAILY_URL.replace("{code}", code)
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
    })
    try:
        if opener is not None:
            text = opener(url)
        else:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
    except Exception:
        return []

    rows = parse_daily_response(text)
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    return rows


def fetch_eastmoney_history(symbol: str, limit: int = 0, timeout: float = DEFAULT_TIMEOUT,
                            opener: Any = None) -> List[Dict[str, Any]]:
    """走东财抓港股指数日线，**日期升序**，形状与 :func:`fetch_daily_history` 一致。

    ``limit`` 语义同新浪那条：0 = 全部，否则保留**最近** N 条。
    不在 :data:`EASTMONEY_HK_SECIDS` 里的符号直接返回空列表 —— 这条只服务港股。

    **当日 bar 是盘中未收盘价**（东财当天就会给一根）。这里**照原样返回**，
    不做过滤：新浪那条白天也返回当日盘中价，两条信源口径一致；落库靠
    ``ON DUPLICATE KEY UPDATE`` 次日覆盖这一行（``seed_index_history._write_history``
    就是这么写的）。所以别在这里加"跳过当天"，否则和新浪路径行为不一致。
    """
    secid = EASTMONEY_HK_SECIDS.get(symbol)
    if secid is None:
        return []

    request_limit = int(limit) if limit and limit > 0 else EASTMONEY_ALL_LIMIT
    url = ("%s?secid=%s&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57"
           "&klt=101&fqt=1&end=20500101&lmt=%d"
           % (EASTMONEY_KLINE_URL, secid, request_limit))
    try:
        if opener is not None:
            text = opener(url)
        else:
            request = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Referer": EASTMONEY_KLINE_REFERER,
            })
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
    except Exception:
        return []

    rows = parse_eastmoney_response(text)
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    return rows


def parse_eastmoney_response(text: str) -> List[Dict[str, Any]]:
    """解析东财 kline 的 JSON，返回**日期升序**的 ``[{"date","close","volume"}, ...]``。

    响应形如::

        {"rc":0,"data":{"code":"HSI","name":"恒生指数","klines":[
            "2026-10-30,26346.29,26346.29,26489.79,26193.12,2581102,0", ...]}}

    ``klines`` 每行字段顺序由请求的 ``fields2`` 决定，这里要的是
    ``f51..f57`` = **日期,开,收,高,低,成交量,成交额** —— 收盘是**第 3 列**，
    不是第 2 列。``data`` 为 ``null``（secid 写错或当天停牌）时返回空列表。
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return []

    data = (payload or {}).get("data") or {}
    result = []
    for line in data.get("klines") or []:
        fields = str(line).split(",")
        if len(fields) < 3:
            continue
        date = _normalize_date(fields[0])
        try:
            value = float(fields[2])  # 第 3 列才是收盘
        except (TypeError, ValueError):
            continue
        # 停牌/未开盘的行收盘是 0，写进去会把均线带偏
        if value <= 0:
            continue
        result.append({
            "date": date,
            "close": value,
            "volume": _to_int(fields[5]) if len(fields) > 5 else 0,
        })
    result.sort(key=lambda row: row["date"])
    return result


# ---------------------------------------------------------------------------
# 内部助手
# ---------------------------------------------------------------------------
def _normalize_date(value: str) -> str:
    """统一成 ``YYYY-MM-DD``。"""
    text = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return "%s-%s-%s" % (digits[0:4], digits[4:6], digits[6:8])
    return text


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


#: PHP 原名别名（见 AGENTS.md 第 3 条）
FetchDailyHistory = fetch_daily_history
GetSinaUsDailyUrl = lambda symbol: SINA_US_DAILY_URL.replace("{code}", quote_code_of(symbol))
FetchEastmoneyHistory = fetch_eastmoney_history
