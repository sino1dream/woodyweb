"""美股 ETF 分红抓取与落库（``woody/res/submitdividend.php``）。

## PHP 那一页在做什么

``_AdminDividendAccount::AdminProcess()``：按符号判断该问谁，然后逐个
除息日 ``StockDividendSql::WriteDaily($strStockId, $strDate, $dividend)``。
**没有 UI**，``AdminRun()`` 跑完就 ``SwitchToSess()`` 跳回上一页。

两个源（``php/externalurl.php`` 给出 URL，本模块照抄）：

* **SPDR / SSGA**（``GetSpdrOfficialUrl()`` 只认 SPY / XBI / XLE / XLY / XOP）
  ``https://www.ssga.com/bin/v1/ssmp/fund/dividend-distribution?ticker=XOP&…``
  返回**降序**数组，一只要 80 行到 2006 年::

      [{"exDate":"09/21/2026","recordDate":"09/21/2026","dividend":"0.757469",
        "payableDate":"09/23/2026","shortTeamCapital":"-","longTeamCapital":"-"}, …]

* **Proshares**（除上面 5 只以外的**所有**符号都往这儿送，PHP 是 ``else`` 兜底）
  ``https://www.proshares.com/api/distributionsummary?fund=TQQQ&year=YYYY``::

      [{"CashDividendPerShare":0.275411,"Dividend":.275411,
        "ExDate":"2024-12-23T00:00:00", …}, …]

## 两处必须照抄的日期口径

* SPDR 的 ``exDate`` 是 **``m/d/Y``**（``09/21/2026``），PHP 走
  ``strtotime()`` —— 那是**美式**月/日/年。按 ISO 解会得到 "9 月 21 日"
  以外的东西，2026-09-21 与 2026-21-09 之间只有前者合法，运气好时不报错、
  直接少写一半行。
* Proshares 的 ``ExDate`` 是 ISO **带时间**（``2024-12-23T00:00:00``），
  PHP 用 ``substr(..., 0, 10)`` **切字符串**、不做时区换算。照抄切片 ——
  一旦改用 ``datetime.fromisoformat()`` 再按本机时区格式化，负时区会退一天。

## 与 PHP 的两处刻意偏离

1. **Proshares 的年份**：PHP 写死 ``&year=2025``（``submitdividend.php:111``），
   到 2026 年就只补 2025 的分红，等于每年 1 月 1 日静默失效一年。这里取
   **当前年份**，可用 ``year=`` 覆盖。
2. **不静默失败**：PHP 抓不到就什么都不做（页面上看不出区别）。这里返回
   带 ``reason`` 的结果，路由把原因显示出来。

## Proshares 现在抓不到（2026-10-30 实测）

``www.proshares.com`` 全站挂在 Cloudflare 的浏览器挑战后面：裸 ``urllib``、
带 UA、带 ``Accept``/``Referer``、``curl`` 全都 **403**（返回的是挑战页
HTML，不是 JSON）。同一天同机器的 SPDR 与 Invesco 都能取到，所以不是本机
网络问题。解析函数
:func:`parse_proshares_dividends` 与 URL 拼接都已按 PHP 写好并用 PHP
docblock 里的真实样本测过，接上可用通道（浏览器控制台 / 换个出口）即可用；
在此之前这一支只能报"没取到"。
"""

from __future__ import annotations

import datetime
import json
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from sql.optionstore import StockDividendSql

#: SSGA 分红接口。``GetSpdrUrl()`` = ``https://www.ssga.com/``（``externalurl.php:256``）。
SPDR_DIVIDEND_URL = (
    "https://www.ssga.com/bin/v1/ssmp/fund/dividend-distribution"
    "?ticker=%s&country=us&language=en&role=individual&product=etfs"
)

#: Proshares 分红接口（``GetProsharesUrl()`` = ``https://www.proshares.com/``）。
PROSHARES_DIVIDEND_URL = "https://www.proshares.com/api/distributionsummary?fund=%s&year=%d"

#: ``GetSpdrOfficialUrl()`` 认得的那五只（``externalurl.php:266``）。
#: 只有它们走 SSGA 接口，其余全部走 Proshares —— 与 PHP 的 if/else 一致。
SPDR_SYMBOLS = ("SPY", "XBI", "XLE", "XLY", "XOP")

#: 各站点都按 UA 判断"是不是脚本"，缺了会 403。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 25.0


def is_spdr_symbol(symbol: str) -> bool:
    """``GetSpdrOfficialUrl($strSymbol) != false``。"""
    return str(symbol).strip().upper() in SPDR_SYMBOLS


def spdr_dividend_url(symbol: str) -> str:
    """SPDR 分红 JSON 的 URL（符号原样拼进去，PHP 也是原样）。"""
    return SPDR_DIVIDEND_URL % symbol


def proshares_dividend_url(symbol: str, year: int = None) -> str:
    """Proshares 分红 JSON 的 URL。``year`` 默认取当前年份（PHP 写死 2025）。"""
    if year is None:
        year = datetime.date.today().year
    return PROSHARES_DIVIDEND_URL % (symbol, int(year))


def dividend_url_of(symbol: str, year: int = None) -> str:
    """按符号选源，与 ``_AdminDividendAccount::AdminProcess()`` 的分派一致。"""
    if is_spdr_symbol(symbol):
        return spdr_dividend_url(symbol)
    return proshares_dividend_url(symbol, year)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def parse_spdr_dividends(text: str) -> List[Dict[str, Any]]:
    """SPDR 的降序数组 → ``[{"date": "YYYY-MM-DD", "dividend": "0.757469"}]``。

    顺序**保持接口给的顺序**（降序）—— ``WriteDaily`` 逐行写，与顺序无关，
    但不排序才能与 PHP 的 ``for ($i = 0; $i < count($ar); $i++)`` 逐行对照。

    三种行要跳过，都照 PHP 的实际行为：

    * 缺 ``exDate``（PHP ``strtotime()`` 得到 0 → ``DebugGetDate(0)`` 是
      ``1970-01-01``，会被 ``WriteDaily`` 的周末闸门或日期合法性拦掉；
      这里直接跳过，效果一致且不产生假行）；
    * 日期不是 ``m/d/Y``；
    * ``dividend`` 不是数字（例如 ``"-"``）——PHP 会把它当 0 写进去，
      那是**脏数据**，这里跳过并把条数报出来。
    """
    rows = _load_json(text)
    if not isinstance(rows, list):
        return []

    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = _parse_us_date(row.get("exDate"))
        dividend = _parse_number(row.get("dividend"))
        if not date or dividend is None:
            continue
        result.append({"date": date, "dividend": dividend})
    return result


def parse_proshares_dividends(text: str) -> List[Dict[str, Any]]:
    """Proshares 的降序数组 → 同形状。

    ``ExDate`` 用 ``substr($ar[$i]['ExDate'], 0, 10)`` 切前 10 个字符
    （PHP 原文），**不做时区换算** —— 见模块 docstring。
    """
    rows = _load_json(text)
    if not isinstance(rows, list):
        return []

    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("ExDate")
        if not isinstance(raw, str) or len(raw) < 10:
            continue
        date = _normalize_date(raw[:10])
        if not date:
            continue
        dividend = _parse_number(row.get("CashDividendPerShare"))
        if dividend is None:
            continue
        result.append({"date": date, "dividend": dividend})
    return result


def parse_dividends(symbol: str, text: str) -> List[Dict[str, Any]]:
    """按符号选解析器。"""
    if is_spdr_symbol(symbol):
        return parse_spdr_dividends(text)
    return parse_proshares_dividends(text)


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------
def update_etf_dividend(
    source: Any,
    symbol: str,
    *,
    year: int = None,
    fetch: Optional[Callable[[str], Optional[str]]] = None,
) -> Dict[str, Any]:
    """对应 ``_AdminDividendAccount::AdminProcess()``。

    返回 ``{"symbol", "source", "url", "rows", "written", "reason"}``：

    * ``rows``：解析出的条数（含"值没变所以没写"的）；
    * ``written``：``WriteDaily`` 真的写了库的条数；
    * ``reason``：拿不到数据时的原因（正常为 ``None``）。

    ``fetch`` 是取文本的函数，测试注入用；默认走 :func:`fetch_text`。
    """
    symbol = str(symbol).strip()
    result: Dict[str, Any] = {
        "symbol": symbol,
        "source": "spdr" if is_spdr_symbol(symbol) else "proshares",
        "url": dividend_url_of(symbol, year),
        "rows": 0,
        "written": 0,
        "reason": None,
    }

    connection = getattr(source, "connection", source)
    stock_id = source.get_stock_id(symbol)
    if stock_id is None:
        result["reason"] = "stock 表里没有 %s 这个符号" % symbol
        return result

    reader = fetch or fetch_text
    text = reader(result["url"])
    if not text:
        result["reason"] = "没取到数据（%s）" % _source_hint(symbol)
        return result

    rows = parse_dividends(symbol, text)
    if not rows:
        result["reason"] = "取到的内容里解析不出分红行（%s）" % _source_hint(symbol)
        return result

    dividend_sql = StockDividendSql(connection)
    written = 0
    for row in rows:
        if dividend_sql.write_daily(stock_id, row["date"], row["dividend"]):
            written += 1
    connection.commit()

    result["rows"] = len(rows)
    result["written"] = written
    return result


def _source_hint(symbol: str) -> str:
    """给"为什么没数据"补一句可操作的提示。"""
    if is_spdr_symbol(symbol):
        return "SSGA 接口；检查 ticker 是否仍是 SPDR 的 ETF"
    return "Proshares 接口被 Cloudflare 拦（403），见模块 docstring"


# ---------------------------------------------------------------------------
# 抓取与转换
# ---------------------------------------------------------------------------
def fetch_text(url: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """取文本，失败返回 ``None``（约定同 ``updater.forexsource``）。

    **不抛异常**：这一页的语义是"能补就补"，抓不到由调用方报原因。
    """
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except Exception:
        return None


def _load_json(text: Any) -> Any:
    """``json_decode($str, true)``：坏 JSON 当"没数据"，不抛。"""
    if isinstance(text, (list, dict)):
        return text
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _parse_us_date(value: Any) -> Optional[str]:
    """``strtotime("09/21/2026")`` + ``date('Y-m-d')``。

    PHP 的 ``strtotime`` 对 ``m/d/Y`` 是**美式**解释；``09/21/2026`` 只能是
    9 月 21 日（21 不能是月）。写成 ``strptime("%m/%d/%Y")`` 就对了。
    """
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.datetime.strptime(value.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None
    return moment.isoformat()


def _normalize_date(value: str) -> Optional[str]:
    """已经是 ``YYYY-MM-DD`` 就原样用；否则返回 ``None``（不猜格式）。"""
    try:
        moment = datetime.datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
    return moment.isoformat()


def _parse_number(value: Any) -> Optional[Any]:
    """分红金额：**校验**是不是数字，然后**原样**返回。

    照 pitfall #149 的口径 —— 值原样交给 MySQL，不在 Python 里先做
    ``int()``/``float()`` 转换：SPDR 给的是 ``"0.757469"``（字符串）、
    Proshares 给的是 JSON 数字，两种都要落进 ``stockdividend.close``
    （``double(10,6)``）。``_prepare_close`` 会负责收尾。

    非数字（``"-"`` / ``""`` / ``None`` / 布尔）返回 ``None``。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if not value:
            return None
    try:
        float(value)
    except (TypeError, ValueError):
        return None
    return value
