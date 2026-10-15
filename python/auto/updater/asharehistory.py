"""抓取 A 股 / 场内基金的历史日线（新浪 ``CN_MarketData`` 接口）。

## 为什么需要

P0.3 回填历史时只有美股/指数那一条路（:mod:`updater.indexhistory`，
``US_MinKService.getDailyK``），**A 股与场内基金（``SZ161125`` 这类）没有源**，
于是演示库里它们的 ``stockhistory`` 只有零星几天（实测 SZ161125 只有
09-29 / 09-30 / 10-14 三行），直接后果：

* 「基金溢价记录」（``fundhistoryparagraph``）按**价格日**逐行出，
  价格日缺 4 天 → 表上少 4 行；
* 「历史价格」段同样是空的。

生产端 ``sz161125cn.php`` 那张表有 09-29 / 09-30 / 10-08 / 10-09 / 10-12 /
10-13 / 10-14 七行，所以这个缺口是**数据**问题，不是渲染问题。

## 接口

``https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/
CN_MarketData.getKLineData?symbol=sz161125&scale=240&ma=no&datalen=40``

返回 **JSON 数组**（不是 JSONP，比美股那条好解析）：::

    [{"day":"2026-10-13","open":"3.330","high":"3.34","low":"3.32",
      "close":"3.330","volume":"3522546"}, ...]

* 符号**小写**（``sz161125`` / ``sh513100``）；
* 日期**升序**，最新在最后；只给**交易日**（周末与节假日自动跳过）；
* ``scale=240`` 是日线。

## 可信度

实测与生产端 ``sz161125cn.php`` 的「基金溢价记录」逐行一致：
``10-08=3.294 / 10-09=3.312 / 10-12=3.337 / 10-13=3.330 / 10-14=3.340``。
与 :mod:`updater.indexhistory` 同源同口径，不是编出来的数。
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Optional

SINA_CN_DAILY_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                     "CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={datalen}")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
REFERER = "https://finance.sina.com.cn/"

DEFAULT_TIMEOUT = 20.0

#: 单次最多要多少条。接口上限不小，但回填只要补最近一段就够。
DEFAULT_DATALEN = 40


def quote_code_of(symbol: str) -> str:
    """新浪要**小写**符号（``SZ161125`` → ``sz161125``）。"""
    return str(symbol).strip().lower()


def parse_daily_response(text: str) -> List[Dict[str, Any]]:
    """解析 JSON 数组，返回**日期升序**的 ``[{"date","close","volume"}, ...]``。

    抓不到 / 格式不对时返回空列表 —— 调用方按"没补到"处理，不要让它抛异常
    （与 :func:`updater.indexhistory.fetch_daily_history` 同款约定）。
    """
    try:
        rows = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(rows, list):
        return []

    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = row.get("day")
        close = row.get("close")
        if not date or close in (None, ""):
            continue
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        result.append({
            "date": str(date)[:10],
            "close": value,
            "volume": _to_int(row.get("volume")),
        })
    return result


def fetch_daily_history(symbol: str, limit: int = 0, timeout: float = DEFAULT_TIMEOUT,
                        opener: Any = None) -> List[Dict[str, Any]]:
    """抓某个 A 股符号的日线，**日期升序**；``limit`` 是"最多保留最近多少条"。

    ``datalen`` 直接按 ``limit`` 给（两者含义一致），``limit`` 为 0 时用
    :data:`DEFAULT_DATALEN`。抓不到返回空列表。
    """
    datalen = limit if limit > 0 else DEFAULT_DATALEN
    url = SINA_CN_DAILY_URL.replace("{code}", quote_code_of(symbol)).replace(
        "{datalen}", str(datalen))
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
    })
    try:
        if opener is not None:
            text = opener(url)
        else:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    rows = parse_daily_response(text)
    if limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    return rows


def _to_int(value: Any) -> int:
    """新浪的成交量是**字符串**，缺值时给 0（不让它把整行写坏）。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
