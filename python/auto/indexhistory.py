"""抓取美股/美股指数的历史日线（新浪 jsonp 接口）。

## 为什么需要

均线表（`smaparagraph`）要 620 天历史：20 个月线至少要 20 个月，
`BOLL_DAYS = 20` 的布林带也要 20 天。而演示库里 **`^NDX` 一行历史都没有** ——
行情 daemon 的 `collect_symbols_from_source()` 是从 `stockhistory` **反推**
要抓哪些符号的，于是 `^NDX` 永远进不了那个集合（鸡生蛋）。

本模块直接按符号抓历史，是打破那个死循环的入口。

## 接口

``https://stock.finance.sina.com.cn/usstock/api/jsonp.php/.../US_MinKService.getDailyK?symbol=.NDX``

返回 JSONP：``IO.XSRV2.CallbackList[]([{"d":"2014-02-18","o":"...","h":"...",
"l":"...","c":"...","v":"...","a":"..."}, ...]);``

* 指数用**点号前缀**：``.NDX``（纳斯达克 100）、``.INX``（标普 500）；
* 普通美股直接用符号：``QQQ``；
* 日期**升序**，最新在最后；
* ``c`` 是收盘价。**没有复权列**，所以 ``adjclose`` 取 ``c``
  （指数与 ETF 的日线本来就按复权价发布，项目里 ``stockhistory.adjclose``
  对指数就是这么用的）。

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

    抓不到时返回**空列表** —— 调用方按"没补到"处理，不要让它抛异常
    （与 ``forexsource.fetch_history_eastmoney`` 同款约定）。
    """
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
