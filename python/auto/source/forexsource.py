"""汇率数据源抽象层。

对应 PHP 源码：

* ``php/stock/cnyref.php`` 的 ``CnyReference`` —— 汇率引用对象，取
  ``USCNY`` / ``HKCNY`` / ``EUCNY`` / ``JPCNY`` 的每日汇率；
* ``php/stock/chinamoney.php`` 的 ``GetChinaMoney()`` —— 从**中国货币网**
  （``chinamoney.com.cn``）抓中间价并写进 ``netvaluehistory``。

## 汇率在估值里干什么

QDII 基金估值的中间层标的是**美元计价**的（QQQ 749.58 美元），而基金净值是
**人民币**计价。所以估值公式里必须有汇率把美元换成人民币：

    php/stock/qdiiref.php:126  EstNetValue()
        $strOfficialCNY = $cny_ref->GetClose($strDate);     // 取当天的 USD/CNY
        $this->fOfficialNetValue = $this->GetQdiiValue(..., $this->strOfficialCNY);

而 ``fundcalc.calc_with_calibration()`` 里那两项：

    est_f = (1 - p_f) * nv_f + p_f * est_value * cny / cal_f
                                   ↑
                             这个 cny 就是汇率

**没有它，估值等于把美元当人民币用** —— 会差 7 倍左右。
Python 侧原先 ``_get_default_cny_est()`` 会去读 ``USCNY`` 的行情，但库里
根本没有 ``USCNY`` 这个符号，于是 ``cny = 1.0``，等于没换算。本模块负责补上。

## 已注册的源

* ``sina``：新浪 ``fx_susdcny`` / ``USDCNY`` 等 —— 实时性好；
* ``chinamoney``：中国货币网 —— PHP 原本用的源，权威中间价，
  ``100JPY/CNY`` 这种标价要除以 100 才是"每 1 日元"；
* ``fixed``：固定值，仅用于测试或离线场景。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional

# 环境变量：汇率源名字
ENV_SOURCE = "PALMMICRO_FOREX_SOURCE"

#: 默认汇率源：``crosscheck``（中国货币网为主 + 新浪对照 + 差异告警）。
#: 要退回单源就设 ``PALMMICRO_FOREX_SOURCE=sina``（或 chinamoney）。
DEFAULT_FOREX_SOURCE = "crosscheck"

#: 单源默认（crosscheck 没有拿到主源名字时用它，避免自我递归）。
#:
#: ## 必须是 ``chinamoney``，不能是 ``sina``
#:
#: ``netvaluehistory`` 里存的是**中间价历史**（PHP ``CnyReference`` 读的就是它：
#: ``SetForex('USCNY')`` → ``GetClose($date)``）。而新浪那个代码
#: ``SINA_FOREX_CODES['USCNY'] = 'fx_susdcny'`` 是**即期**汇率，两者差 0.6% 左右：
#:
#: ==========  ==============  ==================
#: 符号          新浪（即期）     货币网（中间价）
#: ==========  ==============  ==================
#: USCNY        6.679           6.72
#: HKCNY        0.85113         0.85628
#: ==========  ==============  ==================
#:
#: 曾经默认 ``sina``，于是**即期价被写进中间价历史**：``USCNY`` 的
#: 2026-10-20 那条成了 ``6.6798``（与 ``fx_susdcny`` 快照逐位相同），
#: 而当天真实中间价是 ``6.72``。后果是参考 EST / 实时 EST 系统性偏低
#: 约 0.6%（实测 SZ161125 参考 EST 3.1145，生产端 3.132）。
#:
#: 中间价只有一个权威来源：**中国货币网**（每天 09:15 左右发布）。
#: 新浪的即期价留给"参考数据表的实时快照"那条链（``forexcli --fx-only``），
#: 两者的用途不同，不能混写。
DEFAULT_PRIMARY_FOREX = "chinamoney"

# 各币种对应的**库里符号**（与 PHP 的 CnyReference 符号一致）
FOREX_SYMBOLS = ("USCNY", "HKCNY", "EUCNY", "JPCNY")

# 库里符号 -> 新浪外汇代码（实时行情用）
SINA_FOREX_CODES = {
    "USCNY": "fx_susdcny",
    "HKCNY": "fx_shkdcny",
    "EUCNY": "fx_seurcny",
    "JPCNY": "fx_sjpycny",
}

# 库里符号 -> 中国货币网里的字段名
CHINAMONEY_NAMES = {
    "USCNY": "USD/CNY",
    "HKCNY": "HKD/CNY",
    "EUCNY": "EUR/CNY",
    "JPCNY": "100JPY/CNY",
}

# 中国货币网把日元按 100 报价，换算成"每 1 日元"要除的数
CHINAMONEY_DIVISORS = {"JPCNY": 100.0}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# 历史汇率（回补用）
# ---------------------------------------------------------------------------
# 东方财富的历史 K 线接口。**注意可用性不稳定**：实测同一台机器上前几次能通，
# 之后持续 ConnectionError（TCP 可达、HTTP 层空响应），新浪与腾讯的对外历史
# 接口也已失效（500 / 参数错误）。所以：
#   * 正式路径是**每日抓取、持续积累**（见 forexupdater.ForexUpdate）；
#   * 本接口用于"能通就回补历史"，通了就多一段序列，不通也不影响主流程。
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_KLINE_REFERER = "https://quote.eastmoney.com/"

# 库里符号 -> 东财 secid（市场号.代码）。133 是离岸人民币市场。
EASTMONEY_FOREX_SECIDS = {
    "USCNY": "133.USDCNH",
    "HKCNY": "133.HKDCNH",
    "EUCNY": "133.EURCNH",
    "JPCNY": "133.JPYCNH",
}


def fetch_history_eastmoney(symbol: str, limit: int = 400, session: Any = None, timeout: float = 20.0):
    """从东方财富回补历史汇率日线。

    返回 ``[{"symbol","date","rate"}, ...]``（日期升序）；接口不可用时返回空列表
    —— 调用方按"没回补到"处理即可，不要抛异常。
    """
    secid = EASTMONEY_FOREX_SECIDS.get(symbol)
    if secid is None:
        return []

    url = ("%s?secid=%s&fields1=f1,f2,f3&fields2=f51,f52,f53&klt=101&fqt=0"
           "&end=20500101&lmt=%d" % (EASTMONEY_KLINE_URL, secid, max(1, limit)))
    headers = {"User-Agent": USER_AGENT, "Referer": EASTMONEY_KLINE_REFERER}
    try:
        if session is not None:
            payload = session.get(url, headers=headers, timeout=timeout).json()
        else:
            import requests

            payload = requests.get(url, headers=headers, timeout=timeout, verify=False).json()
    except Exception:
        return []

    data = (payload or {}).get("data") or {}
    result = []
    for line in data.get("klines") or []:
        # 每行形如 "2026-09-29,6.7110,6.7079"：日期,开盘,收盘
        fields = str(line).split(",")
        if len(fields) < 3:
            continue
        date = _normalize_date(fields[0])
        rate = _to_float(fields[2])  # 取收盘
        if date and rate:
            result.append({"symbol": symbol, "date": date, "rate": rate})
    return result


class ForexSource:
    """汇率数据源接口。

    子类实现 :meth:`fetch_rate`。返回 ``{"symbol","date","rate"}`` 或 ``None``。
    """

    name = "abstract"

    def fetch_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        """取某个汇率符号的最新值。

        ``symbol`` 用**库里符号**（``USCNY`` / ``HKCNY`` / ``EUCNY`` / ``JPCNY``），
        与本项目其它地方（``_get_default_cny_est()``）保持一致。
        """
        raise NotImplementedError

    def fetch_all(self, symbols: List[str] = None) -> List[Dict[str, Any]]:
        """批量取汇率；取不到的跳过。"""
        result = []
        for symbol in (symbols or list(FOREX_SYMBOLS)):
            try:
                record = self.fetch_rate(symbol)
            except Exception:
                record = None
            if record:
                result.append(record)
        return result

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name}


class ForexSourceRegistry:
    """汇率源注册表。"""

    def __init__(self):
        self._factories: Dict[str, Any] = {}

    def register(self, name: str, factory) -> None:
        self._factories[name] = factory

    def names(self) -> List[str]:
        return sorted(self._factories)

    def create(self, name: str = None, **kwargs) -> ForexSource:
        target = name or os.getenv(ENV_SOURCE) or DEFAULT_FOREX_SOURCE
        if target not in self._factories:
            raise ValueError(
                "未知汇率源 %r；可用的有：%s" % (target, ", ".join(self.names()))
            )
        return self._factories[target](**kwargs)


registry = ForexSourceRegistry()


def fetch_history_safe(symbol: str, start_date: str, end_date: str,
                       session: Any = None, timeout: float = 30.0) -> List[Dict[str, Any]]:
    """一次性从外汇管理局取某币种在区间内的历史中间价。

    返回 ``[{"symbol","date","rate"}, ...]``（**日期升序**）；失败返回空列表。

    与 :class:`SafeForexSource` 的区别：这个是无状态的单次调用，
    适合"只补一个币种"的场景；``SafeForexSource`` 会缓存整张表，
    适合一次补多个币种。
    """
    target = SAFE_COLUMNS.get(symbol)
    if target is None:
        return []

    headers = {"User-Agent": USER_AGENT, "Referer": SAFE_REFERER}
    payload = {"startDate": start_date, "endDate": end_date, "queryYN": "true"}
    try:
        if session is not None:
            response = session.post(SAFE_URL, data=payload, headers=headers, timeout=timeout)
        else:
            import requests

            response = requests.post(SAFE_URL, data=payload, headers=headers,
                                     timeout=timeout, verify=False)
        response.encoding = "utf-8"
        text = response.text
    except Exception:
        return []

    divisor = SAFE_DIVISORS.get(symbol, 100.0)
    return [
        {"symbol": symbol, "date": date, "rate": value / divisor}
        for date, value in _parse_safe_table(text, target)
    ]


def register_forex_source(name: str, factory) -> None:
    registry.register(name, factory)


def get_forex_source(name: str = None, **kwargs) -> ForexSource:
    """取一个汇率源实例；``name`` 缺省看环境变量与默认值。"""
    return registry.create(name, **kwargs)


def list_forex_sources() -> List[str]:
    return registry.names()


# ---------------------------------------------------------------------------
# 新浪外汇
# ---------------------------------------------------------------------------
class SinaForexSource(ForexSource):
    """新浪外汇实时汇率。

    实测响应（``hq.sinajs.cn``，GB18030）::

        var hq_str_USDCNY="03:00:00,6.7050,6.7072,6.7085,51,6.7050,6.7072,6.7021,
                           6.7050,美元人民币,2026-10-01";

    字段（**以 2026-10 实测的 18 字段布局为准**，与 PHP 取的下标对照）：

    * 0 时间、1 今开、**2 昨收**、3 当日最高、4 成交量、
    * 5 买入、6 卖出、7 最低、**8 最新价**、9 名称、17 日期

    这里取 ``fields[8]``（最新价）作为汇率 —— PHP 的 ``LoadSinaForexData``
    同样取 ``$ar[8]``（php/stock/stockref.php:502）；``fields[3]`` 作为
    prev（PHP 取 ``$ar[3]``，ibid:499），页面涨幅列用它。
    旧版注释把 [8] 写成"中间价"、字段表整体错位，是照早期 11 字段布局逆推的，
    已按实测纠正 —— 取数下标没有变，只是注释对上真实布局。
    """

    name = "sina"

    def __init__(self, session: Any = None, timeout: float = 20.0, use_cny_suffix: bool = False):
        self.session = session
        self.timeout = timeout
        # True 时用 fx_s* 代码，False 时用裸代码（USDCNY）。两者新浪都支持，
        # 但字段布局不同：裸代码的日期在最后一个字段。
        self.use_cny_suffix = use_cny_suffix

    def _fetch_text(self, symbol: str) -> Optional[str]:
        code = SINA_FOREX_CODES.get(symbol, symbol) if not self.use_cny_suffix else symbol
        url = "http://hq.sinajs.cn/list=" + code
        headers = {"User-Agent": USER_AGENT, "Referer": "https://finance.sina.com.cn"}
        try:
            if self.session is not None:
                response = self.session.get(url, headers=headers, timeout=self.timeout)
            else:
                import requests

                response = requests.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
        except Exception:
            return None

        content = getattr(response, "content", None)
        if content is None:
            return response.text
        for encoding in ("utf-8", "gb18030"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return content.decode("gb18030", errors="replace")

    def fetch_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        text = self._fetch_text(symbol)
        if text is None:
            return None

        for line in text.splitlines():
            if "hq_str_" not in line or '"' not in line:
                continue
            fields = line.split('"')[1].split(",")
            if len(fields) < 9:
                continue

            rate = _to_float(fields[8])
            if rate is None or rate == 0:
                continue
            return {
                "symbol": symbol,
                # 日期字段位置**不固定**：用 fx_ 代码时在第 11 位，
                # 用裸代码（USDCNY）时在最后一位。所以扫描识别而不是写死下标。
                "date": _find_date(fields),
                "rate": rate,
                # "昨收"类字段：PHP ``LoadSinaForexData`` 取 ``$ar[3]`` 当
                # ``strPrevPrice``（php/stock/stockref.php:500），页面"涨幅"列
                # 由 (price - prev) / prev 得来。字段布局多年变迁——PHP 时代的
                # [3] 是昨收、当前新浪布局里 [3] 是当日最高（[2] 才是昨收），
                # 但**与生产端对齐优先**：生产端 USDCNH 显示 -0.06% 正是
                # (6.6809 - 6.685) / 6.685 算出来的。照抄，不"纠正"。
                "prev": _to_float(fields[3]),
                "time": fields[0] or None,
                "name": _find_name(fields),
                "source": self.name,
            }
        return None

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "codes": dict(SINA_FOREX_CODES)}


# ---------------------------------------------------------------------------
# 中国货币网（PHP 原本的源）
# ---------------------------------------------------------------------------
CHINAMONEY_URL = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/ccpr.json"
CHINAMONEY_REFERER = "https://www.chinamoney.com.cn/"


class ChinaMoneyForexSource(ForexSource):
    """中国货币网中间价。

    对应 PHP ``php/stock/chinamoney.php`` 的 ``GetChinaMoney()``。
    实测响应::

        {"data":{"lastDate":"2026-09-30 9:15"},
         "records":[{"vrtEName":"USD/CNY","price":"6.7351"},
                    {"vrtEName":"100JPY/CNY","price":"4.2727"}, ...]}

    两个要注意的点：

    1. 日元按 **100JPY** 报价，要除以 100 才是"每 1 日元"（表见
       :data:`CHINAMONEY_DIVISORS`）；
    2. ``lastDate`` 带时间（``2026-09-30 9:15``），取日期部分。
    """

    name = "chinamoney"

    def __init__(self, session: Any = None, timeout: float = 20.0):
        self.session = session
        self.timeout = timeout
        # 整份 JSON 一次拿全，避免每个币种都请求一遍
        self._cache = None

    def _load(self) -> Optional[Dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        headers = {"User-Agent": USER_AGENT, "Referer": CHINAMONEY_REFERER}
        try:
            if self.session is not None:
                payload = self.session.get(CHINAMONEY_URL, headers=headers, timeout=self.timeout).json()
            else:
                import requests

                payload = requests.get(CHINAMONEY_URL, headers=headers, timeout=self.timeout).json()
        except Exception:
            return None
        self._cache = payload
        return payload

    def fetch_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        target = CHINAMONEY_NAMES.get(symbol)
        if target is None:
            return None
        payload = self._load()
        if not payload:
            return None

        date = _normalize_date((payload.get("data") or {}).get("lastDate"))
        divisor = CHINAMONEY_DIVISORS.get(symbol, 1.0)
        for record in payload.get("records") or []:
            if record.get("vrtEName") != target:
                continue
            rate = _to_float(record.get("price"))
            if rate is None or rate == 0:
                continue
            return {
                "symbol": symbol,
                "date": date,
                "rate": rate / divisor,
                "name": target,
                "source": self.name,
            }
        return None

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "url": CHINAMONEY_URL,
                "note": "PHP chinamoney.php 原本用的源；日元按 100JPY 报价"}


# ---------------------------------------------------------------------------
# 国家外汇管理局：历史人民币汇率中间价（**历史序列的正解**）
# ---------------------------------------------------------------------------
# 接口来自 AKShare 的 ``currency_boc_safe()``
# （``akshare/currency/currency_safe.py``，本机装在 ``.aksrc`` 的源码里核对过）。
# AKShare 要求 Python >= 3.11，而本项目运行时是 3.7，装不上，
# 所以这里用 requests 自己实现同一接口 —— 也符合项目"汇率源可替换"的设计。
#
# 这是**唯一实测可用的汇率历史接口**：PHP 项目没有这一环
# （``chinamoney.php`` 只取当天），而东方财富/新浪/腾讯的历史接口都不可用。
SAFE_URL = "https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do"
SAFE_REFERER = "https://www.safe.gov.cn/safe/rmbhlzjj/index.html"

# 表头里的币种名 -> 库里符号
SAFE_COLUMNS = {
    "USCNY": "美元",
    "EUCNY": "欧元",
    "JPCNY": "日元",
    "HKCNY": "港元",
}

# 换算除数。外汇管理局的中间价按"100 单位外币"挂牌，所以要除以 100
# 才得到"每 1 单位外币 = 多少人民币"。
# 实测：美元 674.11 → 6.7411；日元 4.275 → 0.04275（日元也是 100 日元标价）。
SAFE_DIVISORS = {
    "USCNY": 100.0,
    "EUCNY": 100.0,
    "JPCNY": 100.0,
    "HKCNY": 100.0,
}


class SafeForexSource(ForexSource):
    """国家外汇管理局的历史人民币汇率中间价。

    为什么需要它：``forexupdater.fill_official_dates()`` 要给**基金官方日期**
    （往往在过去几天）补汇率，而实时源只给当下。没有历史接口时只能向前回退，
    实测曾回退到 9 个月前的种子占位值。

    它同时支持"取最新"（:meth:`fetch_rate`）与"取区间"（:meth:`fetch_history`），
    且同一区间只请求一次、多个币种共享结果（返回的是整张表）。
    """

    name = "safe"

    def __init__(self, session: Any = None, timeout: float = 30.0,
                 start_date: str = None, end_date: str = None):
        self.session = session
        self.timeout = timeout
        self._start_date = start_date
        self._end_date = end_date
        self._cache = None

    def _range(self):
        import datetime as _datetime

        today = _datetime.date.today()
        start = self._start_date or (today - _datetime.timedelta(days=90)).isoformat()
        end = self._end_date or today.isoformat()
        return start, end

    def _load(self) -> Dict[str, List]:
        """一次取回区间内所有币种的中间价并缓存。失败时缓存为空字典。"""
        if self._cache is not None:
            return self._cache

        start, end = self._range()
        headers = {"User-Agent": USER_AGENT, "Referer": SAFE_REFERER}
        payload = {"startDate": start, "endDate": end, "queryYN": "true"}
        try:
            if self.session is not None:
                response = self.session.post(SAFE_URL, data=payload, headers=headers,
                                             timeout=self.timeout)
            else:
                import requests

                response = requests.post(SAFE_URL, data=payload, headers=headers,
                                         timeout=self.timeout, verify=False)
            response.encoding = "utf-8"
            text = response.text
        except Exception:
            self._cache = {}
            return self._cache

        cache = {}
        for symbol, column in SAFE_COLUMNS.items():
            divisor = SAFE_DIVISORS.get(symbol, 100.0)
            cache[symbol] = [
                (date, value / divisor)
                for date, value in _parse_safe_table(text, column)
            ]
        self._cache = cache
        return cache

    def fetch_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        rows = self.fetch_history(symbol)
        if not rows:
            return None
        latest = rows[-1]
        return {
            "symbol": symbol,
            "date": latest["date"],
            "rate": latest["rate"],
            "name": SAFE_COLUMNS.get(symbol),
            "source": self.name,
        }

    def fetch_history(self, symbol: str, limit: int = 400) -> List[Dict[str, Any]]:
        """取区间内的历史序列，日期升序。"""
        rows = (self._load().get(symbol) or [])[-max(1, limit):]
        return [{"symbol": symbol, "date": date, "rate": rate} for date, rate in rows]

    def describe(self) -> Dict[str, Any]:
        start, end = self._range()
        return {
            "name": self.name,
            "url": SAFE_URL,
            "range": "%s ~ %s" % (start, end),
            "note": "国家外汇管理局人民币汇率中间价；按 100 外币挂牌，已换算为每 1 单位",
        }


def _parse_safe_table(text: str, target: str):
    """解析外汇管理局的 HTML 表格，返回``[(日期, 原始挂牌价), ...]``。

    表头形如 ``日期 | 美元 | 欧元 | 日元 | 港元 | ...``（26 列），
    数据行形如 ``2026-09-29 | 674.11 | 764.94 | 4.275 | 85.935 | ...``。

    注意返回的是**未换算的原始值**，由调用方按 :data:`SAFE_DIVISORS` 除。
    """
    import re

    tables = re.findall(r"<table.*?</table>", text, re.S | re.I)
    if not tables:
        return []

    # 数据表是页面里最后一张 table
    column_index = None
    result = []
    for row in re.findall(r"<tr.*?</tr>", tables[-1], re.S | re.I):
        cells = [_clean_cell(cell)
                 for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue

        # 表头行：第一次看到目标币种名时记下列号
        if column_index is None:
            if target in cells:
                column_index = cells.index(target)
            continue

        if column_index >= len(cells):
            continue
        date = _normalize_date(cells[0])
        value = _to_float(cells[column_index])
        if date and value:
            result.append((date, value))

    # 明确按日期升序排。接口在区间较大时返回的**行序不保证**（实测 90 天区间
    # 回来的顺序不是日期序），排序后调用方才能安全地用 rows[-1] 取最新。
    result.sort(key=lambda item: item[0])
    return result


def _clean_cell(cell: str) -> str:
    """去掉 HTML 注释与标签，压缩空白。"""
    import re

    value = re.sub(r"<!--.*?-->", "", cell, flags=re.S)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", value).strip()


# ---------------------------------------------------------------------------
# 固定值（测试/离线）
# ---------------------------------------------------------------------------
class FixedForexSource(ForexSource):
    """固定汇率，仅用于测试或离线场景。

    构造：``FixedForexSource({"USCNY": 7.1})``
    """

    name = "fixed"

    def __init__(self, rates: Mapping[str, Any] = None, date: str = "2026-01-02"):
        self.rates = dict(rates or {})
        self.date = date

    def fetch_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        rate = _to_float(self.rates.get(symbol))
        if rate is None:
            return None
        return {"symbol": symbol, "date": self.date, "rate": rate, "source": self.name}

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "rates": dict(self.rates)}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--"):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _normalize_date(value: Any) -> Optional[str]:
    """取 ``YYYY-MM-DD``；不像日期的返回 ``None``。

    中国货币网的 ``lastDate`` 带时间（``2026-09-30 9:15``），要去掉。
    新浪外汇的日期夹杂在数字字段里，所以这里要**校验**而不是无脑截断 ——
    否则会把 ``0.0000`` 当成日期传出去（实测踩到过）。
    """
    if value is None:
        return None
    text = str(value).strip().replace("/", "-")
    if not text:
        return None
    head = text[:10]
    if _is_iso_date(head):
        return head
    return None


def _is_iso_date(text: str) -> bool:
    """``YYYY-MM-DD`` 且月日在合法范围。"""
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return False
    parts = text.split("-")
    if not all(part.isdigit() for part in parts):
        return False
    year, month, day = (int(part) for part in parts)
    return 1970 <= year <= 2999 and 1 <= month <= 12 and 1 <= day <= 31


def _find_date(fields: List[str]) -> Optional[str]:
    """在字段里找第一个像日期的值。

    新浪不同代码的日期位置不一致（``fx_susdcny`` 在第 11 位、
    裸代码 ``USDCNY`` 在最后一位），所以扫描而不是写死下标。
    """
    for field in fields:
        date = _normalize_date(field)
        if date:
            return date
    return None


def _find_name(fields: List[str]) -> Optional[str]:
    """取名称字段：不含冒号、不是数字、长度大于 1 的第一个字符串。

    实测：``fx_`` 代码的名称在第 9 位，裸代码的也在第 9 位但日期跟在后面，
    两种情况下"第一个中文/非数字文本"都是名称。
    """
    for field in fields[1:]:
        text = str(field or "").strip()
        if len(text) < 2:
            continue
        if _to_float(text) is not None:
            continue
        if ":" in text:  # 时间字段
            continue
        return text
    return None


# ---------------------------------------------------------------------------
# 双源交叉校验（与 navsource.CrossCheckNavSource 同思路）
# ---------------------------------------------------------------------------
# 环境变量：汇率容差（相对比例）。超过就告警。
ENV_TOLERANCE = "PALMMICRO_FOREX_TOLERANCE"

# 默认容差：1%。汇率两个源一个是实时价、一个是每日中间价，
# 差 0.5% 左右是常态；超过 1% 才值得看一眼。
DEFAULT_TOLERANCE = 0.01


class CrossCheckForexSource(ForexSource):
    """双源汇率抓取 + 对比 + 差异告警。

    与 ``navsource.CrossCheckNavSource`` 的设计一致（那边是净值）：

    * 主源默认**中国货币网**（权威中间价，``netvaluehistory`` 里要的就是它）；
    * 对照源默认**新浪**（即期，实时性好，用来交叉核对）；
    * 相对差异超容差 → ``ALERT``；
    * 主源取不到 → 自动用对照源，并 ``WARN``。

    ## 主源为什么是货币网而不是新浪

    详见 :data:`DEFAULT_PRIMARY_FOREX`：``netvaluehistory`` 存的是**中间价**，
    而新浪 ``fx_susdcny`` 是**即期**，两者差约 0.6%。默认用新浪会把即期价
    写进中间价历史（实测 ``USCNY`` 2026-10-20 被写成 6.6798，真值 6.72），
    参考 EST / 实时 EST 因此系统性偏低。

    ``WARN`` 那条"主源无数据改用对照源"仍然保留 —— 但要知道它意味着
    **这一次写进去的是即期价**，所以 ``alert_on_missing`` 打开时能看见。

    注意容差用的是**相对比例**（默认 1%）：汇率是 6~7 量级的数，用绝对值不合适。
    """

    name = "crosscheck"

    def __init__(
        self,
        primary: Any = None,
        secondary: Any = "sina",
        tolerance: float = None,
        alert=None,
        alert_on_missing: bool = False,
        **kwargs
    ):
        if primary is None:
            name = os.getenv(ENV_SOURCE) or DEFAULT_PRIMARY_FOREX
            if name == self.name:
                name = DEFAULT_PRIMARY_FOREX
            primary = name
        self.primary = _as_source(primary)
        self.secondary = _as_source(secondary) if secondary is not None else None
        self.tolerance = float(
            tolerance if tolerance is not None
            else os.getenv(ENV_TOLERANCE, DEFAULT_TOLERANCE)
        )
        self.alert = alert or _default_alert
        self.alert_on_missing = alert_on_missing
        self.last_comparison = None

    def fetch_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        primary = self._safe_fetch(self.primary, symbol)
        secondary = self._safe_fetch(self.secondary, symbol) if self.secondary else None
        self.last_comparison = self.compare(symbol, primary, secondary)

        if primary:
            return primary
        if secondary:
            self._alert("WARN", "%s：主汇率源 %s 无数据，已改用对照源 %s 的 %s" % (
                symbol, self._name_of(self.primary), self._name_of(self.secondary),
                secondary.get("rate")))
            return secondary
        return None

    def compare(self, symbol: str, primary: Any, secondary: Any) -> Dict[str, Any]:
        """对比两个源的汇率并触发告警。"""
        result = {
            "symbol": symbol, "comparable": False, "match": None,
            "primary": primary, "secondary": secondary, "diff": None, "reason": "",
        }
        if self.secondary is None:
            result["reason"] = "未配置对照源，跳过对比"
            return result
        if not primary or not secondary:
            result["reason"] = "一侧无数据，无法对比"
            if self.alert_on_missing:
                self._alert("WARN", "%s：%s" % (symbol, result["reason"]))
            return result

        rate_primary = _to_float(primary.get("rate"))
        rate_secondary = _to_float(secondary.get("rate"))
        if rate_primary is None or rate_secondary is None or rate_secondary == 0:
            result["reason"] = "汇率无法解析，无法对比"
            self._alert("WARN", "%s：%s" % (symbol, result["reason"]))
            return result

        result["comparable"] = True
        # 用相对差：汇率是 6~7 量级，绝对值不好定容差
        relative = abs(rate_primary - rate_secondary) / abs(rate_secondary)
        result["diff"] = relative
        result["match"] = relative <= self.tolerance

        if result["match"]:
            result["reason"] = "一致"
            return result

        self._alert("ALERT", "%s 汇率差异超容差：主源 %s=%s vs 对照源 %s=%s，相对差 %.4f%% > %.2f%%" % (
            symbol,
            self._name_of(self.primary), rate_primary,
            self._name_of(self.secondary), rate_secondary,
            relative * 100.0, self.tolerance * 100.0,
        ))
        result["reason"] = "差异超容差"
        return result

    def _safe_fetch(self, source, symbol: str):
        if source is None:
            return None
        try:
            return source.fetch_rate(symbol)
        except Exception as exc:
            self._alert("WARN", "%s：源 %s 抓取异常 %s: %s" % (
                symbol, self._name_of(source), type(exc).__name__, exc))
            return None

    @staticmethod
    def _name_of(source) -> str:
        return getattr(source, "name", "unknown") if source is not None else "-"

    def _alert(self, level: str, message: str) -> None:
        self.alert(level, message)

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "primary": self._name_of(self.primary),
            "secondary": self._name_of(self.secondary),
            "tolerance": self.tolerance,
            "note": "中国货币网（中间价，入库）为主、新浪（即期）为对照；相对差超容差会告警",
        }


def _default_alert(level: str, message: str) -> None:
    """默认告警输出：``INFO`` 走 stdout，``WARN``/``ALERT`` 走 stderr。"""
    import sys

    line = "[汇率校验][%s] %s" % (level, message)
    stream = sys.stdout if level == "INFO" else sys.stderr
    try:
        stream.write(line + "\n")
        stream.flush()
    except Exception:
        pass


def _as_source(value):
    """把字符串或实例统一成汇率源。"""
    if value is None:
        return None
    if isinstance(value, ForexSource):
        return value
    if isinstance(value, str):
        return get_forex_source(value)
    if hasattr(value, "fetch_rate"):
        return value
    raise TypeError("不是可用的汇率源：%r" % (value,))


register_forex_source("sina", lambda **kwargs: SinaForexSource(**kwargs))
register_forex_source("chinamoney", lambda **kwargs: ChinaMoneyForexSource(**kwargs))
register_forex_source("safe", lambda **kwargs: SafeForexSource(**kwargs))
register_forex_source("fixed", lambda **kwargs: FixedForexSource(**kwargs))
register_forex_source("crosscheck", lambda **kwargs: CrossCheckForexSource(**kwargs))
