"""基金净值数据源抽象层。

替代 PHP ``yahoostock.php`` 的净值部分。为什么不用 Yahoo：实测
``query1/query2.finance.yahoo.com`` 的 v7/v8 接口与网页**全部返回 403**
（详见 ``python/NAV_SOURCE_PLAN.md``），照原样迁移一行数据也取不到。

接口形态刻意与 :mod:`quotesource` 保持一致，这样换源的做法在行情与净值两边
是一样的：写子类 → 注册 → 改环境变量。

已注册实现：

* ``eastmoney``：东方财富 ``api.fund.eastmoney.com/f10/lsjz`` —— **首选**，
  有完整历史分页；
* ``sina``：新浪 ``hq.sinajs.cn/list=f_<代码>`` —— 备选，抓取层已就绪；
* ``szse``：深交所官方 ``CATALOGID=1785_child`` —— 兜底交叉校验，
  只覆盖深市 LOF/ETF。

与 PHP 的对应关系
-----------------
============================  ====================================
本模块                         PHP
============================  ====================================
``NavSource.fetch_latest()``   ``_yahooStockGetData()`` 里"取最新一条"
``NavSource.fetch_history()``  ``UpdateYahooHistoryChart()`` 的数据来源
``eastmoney`` / ``sina``       ——（PHP 没有，它只有 Yahoo）
============================  ====================================
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

# 环境变量名，集中在这里避免散落
ENV_SOURCE = "PALMMICRO_NAV_SOURCE"

#: 默认信源。取 ``crosscheck``（东财为主 + 新浪对照 + 差异告警），
#: 这正是需求要的默认行为。要退回单源就设 ``PALMMICRO_NAV_SOURCE``。
DEFAULT_NAV_SOURCE = "crosscheck"

#: 单源默认（crosscheck 在没拿到主源名字时用它，避免自我递归）
DEFAULT_PRIMARY_SOURCE = "eastmoney"

# 各源共用的 UA。东财与深交所都对 UA/Referer 敏感，缺了会 403 或返回空。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


class NavSource:
    """基金净值数据源接口。

    子类必须实现 :meth:`fetch_latest`；:meth:`fetch_history` 默认退化为
    只返回最新一条，需要历史的源（如东财）自行覆写。
    """

    #: 数据源标识，用于日志与 ``PALMMICRO_NAV_SOURCE``
    name = "abstract"

    def fetch_latest(self, code: str) -> Optional[Dict[str, Any]]:
        """取最新一条单位净值。

        返回 ``{"code", "date", "nav"}``（可选 ``"acc_nav"``、``"change_pct"``），
        取不到返回 ``None``。

        ``code`` 是**6 位数字基金代码**（如 ``161130``），不带市场前缀 ——
        各源都按纯数字检索，由调用方负责从库里符号剥出数字。
        """
        raise NotImplementedError

    def fetch_history(self, code: str, limit: int = 30) -> List[Dict[str, Any]]:
        """取最近 ``limit`` 条净值，按日期倒序（最新在前）。"""
        latest = self.fetch_latest(code)
        return [latest] if latest else []

    def describe(self) -> Dict[str, Any]:
        """数据源自述，供诊断输出。"""
        return {"name": self.name}


class NavSourceRegistry:
    """净值源注册表。"""

    def __init__(self):
        self._factories: Dict[str, Any] = {}

    def register(self, name: str, factory) -> None:
        self._factories[name] = factory

    def names(self) -> List[str]:
        return sorted(self._factories)

    def create(self, name: str = None, **kwargs) -> NavSource:
        target = name or os.getenv(ENV_SOURCE) or DEFAULT_NAV_SOURCE
        if target not in self._factories:
            raise ValueError(
                "未知净值源 %r；可用的有：%s" % (target, ", ".join(self.names()))
            )
        return self._factories[target](**kwargs)


registry = NavSourceRegistry()


def register_nav_source(name: str, factory) -> None:
    """注册净值源工厂。"""
    registry.register(name, factory)


def get_nav_source(name: str = None, **kwargs) -> NavSource:
    """取一个净值源实例；``name`` 缺省看环境变量与默认值。"""
    return registry.create(name, **kwargs)


def list_nav_sources() -> List[str]:
    """列出已注册的净值源。"""
    return registry.names()


# ---------------------------------------------------------------------------
# 东方财富（首选）
# ---------------------------------------------------------------------------
EASTMONEY_URL = "https://api.fund.eastmoney.com/f10/lsjz"
EASTMONEY_REFERER = "https://fundf10.eastmoney.com/"


class EastmoneyNavSource(NavSource):
    """东方财富基金历史净值。

    实测返回::

        {"Data":{"LSJZList":[{"FSRQ":"2026-09-29","DWJZ":"0.9338",
          "LJJZ":"0.9338","JZZZL":"-0.80",...}]}}

    字段含义：``FSRQ`` 净值日期、``DWJZ`` 单位净值、``LJJZ`` 累计净值、
    ``JZZZL`` 日涨跌幅（%）。

    **必须带 Referer**，否则接口返回空。
    """

    name = "eastmoney"

    def __init__(self, session: Any = None, timeout: float = 20.0):
        self.session = session
        self.timeout = timeout

    def _get_json(self, url: str) -> Any:
        headers = {"User-Agent": USER_AGENT, "Referer": EASTMONEY_REFERER}
        if self.session is not None:
            response = self.session.get(url, headers=headers, timeout=self.timeout)
        else:
            import requests

            response = requests.get(url, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def fetch_history(self, code: str, limit: int = 30) -> List[Dict[str, Any]]:
        """取最近 ``limit`` 条净值（分页参数直接用 pageSize）。"""
        url = "%s?fundCode=%s&pageIndex=1&pageSize=%d" % (EASTMONEY_URL, code, max(1, limit))
        try:
            payload = self._get_json(url)
        except Exception:
            return []

        rows = (payload or {}).get("Data", {}).get("LSJZList") or []
        result = []
        for row in rows:
            date = row.get("FSRQ")
            nav = row.get("DWJZ")
            # 净值可能为空字符串（新基金/停牌），跳过而不是写 0
            if not date or nav in (None, "", "--"):
                continue
            result.append({
                "code": code,
                "date": date,
                "nav": str(nav),
                "acc_nav": str(row.get("LJJZ")) if row.get("LJJZ") not in (None, "") else None,
                "change_pct": str(row.get("JZZZL")) if row.get("JZZZL") not in (None, "") else None,
                "source": self.name,
            })
        return result

    def fetch_latest(self, code: str) -> Optional[Dict[str, Any]]:
        rows = self.fetch_history(code, limit=1)
        return rows[0] if rows else None

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "url": EASTMONEY_URL, "referer": EASTMONEY_REFERER}


# ---------------------------------------------------------------------------
# 新浪（备选）
# ---------------------------------------------------------------------------
SINA_NAV_URL = "http://hq.sinajs.cn/list=f_"
SINA_REFERER = "https://finance.sina.com.cn"


class SinaNavSource(NavSource):
    """新浪基金净值。

    响应（GB18030 编码）::

        var hq_str_f_161130="易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额),
                             4.5452,4.5452,4.5357,2026-09-29,3.89851";

    字段：名称, 当日净值, 累计净值, 昨日净值, 日期, 份额规模(亿份)。
    与东方财富的实测数值完全一致，所以定位是"备选 + 交叉校验"。
    """

    name = "sina"

    def __init__(self, session: Any = None, timeout: float = 20.0):
        self.session = session
        self.timeout = timeout

    def _get_text(self, code: str) -> str:
        url = SINA_NAV_URL + code
        headers = {"User-Agent": USER_AGENT, "Referer": SINA_REFERER}
        if self.session is not None:
            response = self.session.get(url, headers=headers, timeout=self.timeout)
        else:
            import requests

            response = requests.get(url, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        content = getattr(response, "content", None)
        if content is None:
            return response.text
        # 新浪是 GB18030，先试 UTF-8 再回退（与 sinaquotes 同一策略）
        for encoding in ("utf-8", "gb18030"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return content.decode("gb18030", errors="replace")

    def fetch_latest(self, code: str) -> Optional[Dict[str, Any]]:
        try:
            text = self._get_text(code)
        except Exception:
            return None

        for raw in text.splitlines():
            if "hq_str_f_%s=" % code not in raw:
                continue
            if '"' not in raw:
                continue
            fields = raw.split('"')[1].split(",")
            if len(fields) < 5:
                return None
            nav, date = fields[1], fields[4]
            if nav in ("", "0.0000") or not date:
                return None
            return {
                "code": code,
                "date": date,
                "nav": nav,
                "acc_nav": fields[2] or None,
                "prev_nav": fields[3] or None,
                "name": fields[0] or None,
                "source": self.name,
            }
        return None

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "url": SINA_NAV_URL, "referer": SINA_REFERER}


# ---------------------------------------------------------------------------
# 深交所（兜底交叉校验）
# ---------------------------------------------------------------------------
SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_REFERER = "https://www.szse.cn/"


class SzseNavSource(NavSource):
    """深交所官方单位净值。

    ``CATALOGID=1785_child`` 返回逐日单位净值，实测通用于深市 LOF 与 ETF::

        [{"metadata":{...},"data":[{"nav_date":"2026-09-28","fund_code":"161130",
          "security_short_name":"纳斯达克100LOF","nav_per_share":"4.5357"}, ...]}]

    两个坑：

    1. ``metadata.recordcount`` **恒为 0**（官方没填），数据在 ``data`` 数组里，
       不要据 recordcount 判断有没有数据；
    2. 只覆盖深市（``15xxxx``/``16xxxx``），沪市代码会返回空数组。
    """

    name = "szse"

    def __init__(self, session: Any = None, timeout: float = 20.0):
        self.session = session
        self.timeout = timeout

    def fetch_history(self, code: str, limit: int = 30) -> List[Dict[str, Any]]:
        url = ("%s?SHOWTYPE=JSON&CATALOGID=1785_child&TABKEY=tab1&txtDm=%s" % (SZSE_URL, code))
        headers = {"User-Agent": USER_AGENT, "Referer": SZSE_REFERER}
        try:
            if self.session is not None:
                payload = self.session.get(url, headers=headers, timeout=self.timeout).json()
            else:
                import requests

                payload = requests.get(url, headers=headers, timeout=self.timeout).json()
        except Exception:
            return []

        if not isinstance(payload, list) or not payload:
            return []
        rows = payload[0].get("data") or []

        result = []
        for row in rows[:max(1, limit)]:
            date = row.get("nav_date")
            nav = row.get("nav_per_share")
            if not date or nav in (None, "", "--"):
                continue
            result.append({
                "code": code,
                "date": date,
                "nav": str(nav),
                "name": row.get("security_short_name") or None,
                "source": self.name,
            })
        return result

    def fetch_latest(self, code: str) -> Optional[Dict[str, Any]]:
        rows = self.fetch_history(code, limit=1)
        return rows[0] if rows else None

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "url": SZSE_URL, "referer": SZSE_REFERER,
                "coverage": "仅深市 LOF/ETF"}


# ---------------------------------------------------------------------------
# 默认返回结果中的键（供调用方按统一口径取值）
# ---------------------------------------------------------------------------
RESULT_KEYS = ("code", "date", "nav", "acc_nav", "prev_nav", "change_pct", "name", "source")


# ---------------------------------------------------------------------------
# 双源交叉校验（默认使用）
# ---------------------------------------------------------------------------
# 环境变量：净值容差（绝对值，元）。超过就告警。
ENV_TOLERANCE = "PALMMICRO_NAV_TOLERANCE"

# 默认容差。基金单位净值一般 1~5 元，各源保留到 4 位小数，
# 偶有 0.0001 的舍入差，所以默认放到 0.001（千分之一元）。
DEFAULT_TOLERANCE = 0.001

# 环境变量：一侧取不到时是否也算告警（默认不算，只在完全取不到时 WARN）。
ENV_ALERT_ON_MISSING = "PALMMICRO_NAV_ALERT_ON_MISSING"


def normalize_nav(value: Any) -> Optional[float]:
    """把净值清洗成数字；无法解析返回 ``None``。

    要处理的脏数据（各源实际都会出现）：

    * 空字符串、``None``、``"--"``、``"-"``、``"N/A"``；
    * 带千分位逗号或百分号的文本；
    * 前后空白。
    """
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in ("", "-", "--", "N/A", "null", "None"):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def normalize_date(value: Any) -> Optional[str]:
    """把日期清洗成 ``YYYY-MM-DD``；无法解析返回 ``None``。

    各源写法不一致（``2026-09-29``、``2026/09/29``、``20260929``），
    统一之后再比较，避免"数值一样只是写法不同"被误报成差异。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("/", "-").replace(".", "-")
    if len(text) == 8 and text.isdigit():
        return "%s-%s-%s" % (text[:4], text[4:6], text[6:])
    return text[:10]


class CrossCheckNavSource(NavSource):
    """双源抓取 + 清洗 + 对比 + 差异告警。

    设计目标（对应需求）：

    * **默认以东方财富为主信源**，新浪作为对照；
    * 同一只基金两个源都抓，清洗后对比；
    * 数值超出容差 → 告警（默认打到 stderr，可换成日志或自己的回调）；
    * 日期不同但数值一致 → 只提示不告警（各源更新时点不同，属正常）；
    * 主源取不到时**自动用对照源的数值**，保证数据不断流；
    * 信源可用参数或环境变量切换，不必改代码。

    构造参数::

        CrossCheckNavSource(
            primary=None,          # 主信源名字或实例；缺省取 PALMMICRO_NAV_SOURCE 或 eastmoney
            secondary="sina",      # 对照信源；传 None 可关闭对比（退化为单源）
            tolerance=0.001,       # 净值容差（元）
            alert=None,            # alert(level, message) 回调；默认打 stdout/stderr
            alert_on_missing=None, # 一侧缺失是否也算告警
        )

    实现上继承 :class:`NavSource`，所以对上层（``navupdater`` / ``navcli``）
    完全透明；要换成单源只改 ``PALMMICRO_NAV_SOURCE``。
    """

    name = "crosscheck"

    def __init__(
        self,
        primary: Any = None,
        secondary: Any = "sina",
        tolerance: float = None,
        alert=None,
        alert_on_missing: bool = None,
        **kwargs
    ):
        if primary is None:
            # 未指定主源时：优先环境变量，其次东方财富。
            # 注意排除 "crosscheck" 自己，否则会自我递归。
            name = os.getenv(ENV_SOURCE) or DEFAULT_PRIMARY_SOURCE
            if name == self.name:
                name = DEFAULT_PRIMARY_SOURCE
            primary = name
        self.primary = _as_source(primary)

        self.secondary = _as_source(secondary) if secondary is not None else None

        self.tolerance = float(
            tolerance if tolerance is not None
            else os.getenv(ENV_TOLERANCE, DEFAULT_TOLERANCE)
        )
        self.alert = alert or _default_alert
        if alert_on_missing is None:
            alert_on_missing = os.getenv(ENV_ALERT_ON_MISSING, "0") == "1"
        self.alert_on_missing = alert_on_missing
        self.last_comparison = None

    # -- 对比 -------------------------------------------------------------
    def fetch_latest(self, code: str) -> Optional[Dict[str, Any]]:
        """抓主源与对照源，清洗对比后返回主源记录。

        主源取不到时改用对照源的记录（保证数据不断流）；两者都取不到返回 ``None``。
        """
        primary = self._safe_fetch(self.primary, code)
        secondary = self._safe_fetch(self.secondary, code) if self.secondary else None
        self.last_comparison = self.compare(code, primary, secondary)

        if primary:
            return primary
        if secondary:
            self._alert("WARN", "%s：主信源 %s 无数据，已改用对照信源 %s 的净值 %s（%s）" % (
                code, self._name_of(self.primary), self._name_of(self.secondary),
                secondary.get("nav"), secondary.get("date")))
            return secondary
        return None

    def fetch_history(self, code: str, limit: int = 30) -> List[Dict[str, Any]]:
        """历史只用主源：对照源（新浪）本身不提供历史分页。"""
        try:
            return self.primary.fetch_history(code, limit=limit)
        except Exception:
            return []

    def compare(self, code: str, primary: Any, secondary: Any) -> Dict[str, Any]:
        """对比两个源的结果，返回结论并按需告警。

        返回 ``{"code","comparable","match","primary","secondary","diff","reason"}``。
        """
        result = {
            "code": code,
            "comparable": False,
            "match": None,
            "primary": primary,
            "secondary": secondary,
            "diff": None,
            "reason": "",
        }
        if self.secondary is None:
            result["reason"] = "未配置对照信源，跳过对比"
            return result
        if not primary or not secondary:
            result["reason"] = "一侧无数据，无法对比"
            if self.alert_on_missing:
                self._alert("WARN", "%s：%s" % (code, result["reason"]))
            return result

        nav_primary = normalize_nav(primary.get("nav"))
        nav_secondary = normalize_nav(secondary.get("nav"))
        if nav_primary is None or nav_secondary is None:
            result["reason"] = "净值无法解析，无法对比"
            self._alert("WARN", "%s：%s（主源 %r / 对照源 %r）" % (
                code, result["reason"], primary.get("nav"), secondary.get("nav")))
            return result

        result["comparable"] = True
        diff = abs(nav_primary - nav_secondary)
        result["diff"] = diff
        result["match"] = diff <= self.tolerance

        date_primary = normalize_date(primary.get("date"))
        date_secondary = normalize_date(secondary.get("date"))

        if result["match"]:
            if date_primary != date_secondary:
                # 日期不同但数值一致：各源更新时点不同，只提示不告警
                self._alert("INFO", "%s：两源净值一致（%s），日期不同（%s vs %s）" % (
                    code, nav_primary, date_primary, date_secondary))
            result["reason"] = "一致"
            return result

        self._alert("ALERT", "%s 净值差异超容差：主信源 %s=%s(%s) vs 对照信源 %s=%s(%s)，差 %.4f > %.4f" % (
            code,
            self._name_of(self.primary), nav_primary, date_primary,
            self._name_of(self.secondary), nav_secondary, date_secondary,
            diff, self.tolerance,
        ))
        result["reason"] = "差异超容差"
        return result

    # -- 内部 -------------------------------------------------------------
    def _safe_fetch(self, source, code: str):
        """抓取并清洗；异常返回 ``None``（一只基金失败不影响整批）。"""
        if source is None:
            return None
        try:
            record = source.fetch_latest(code)
        except Exception as exc:
            self._alert("WARN", "%s：信源 %s 抓取异常 %s: %s" % (
                code, self._name_of(source), type(exc).__name__, exc))
            return None
        if record is None:
            return None

        nav = normalize_nav(record.get("nav"))
        if nav is None:
            return None
        cleaned = dict(record)
        cleaned["nav"] = str(nav)
        cleaned["date"] = normalize_date(record.get("date"))
        return cleaned

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
            "alert_on_missing": self.alert_on_missing,
            "note": "默认东财为主信源、新浪为对照；差异超容差会告警",
        }


def _default_alert(level: str, message: str) -> None:
    """默认告警输出：``INFO`` 走 stdout，``WARN``/``ALERT`` 走 stderr。

    分流是为了让日志系统能直接按 stderr 抓告警，而不必解析普通输出。
    """
    import sys

    line = "[净值校验][%s] %s" % (level, message)
    stream = sys.stdout if level == "INFO" else sys.stderr
    try:
        stream.write(line + "\n")
        stream.flush()
    except Exception:
        pass


def _as_source(value):
    """把字符串或实例统一成数据源实例。"""
    if value is None:
        return None
    if isinstance(value, NavSource):
        return value
    if isinstance(value, str):
        return create_source(value)
    # 鸭子类型：只要实现了 fetch_latest 就接受（便于测试注入假源）
    if hasattr(value, "fetch_latest"):
        return value
    raise TypeError("不是可用的净值源：%r" % (value,))


def create_source(name: str = None, **kwargs) -> NavSource:
    """按名字创建单个净值源（不包装交叉校验）。"""
    return registry.create(name, **kwargs)


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------
register_nav_source("eastmoney", lambda **kwargs: EastmoneyNavSource(**kwargs))
register_nav_source("sina", lambda **kwargs: SinaNavSource(**kwargs))
register_nav_source("szse", lambda **kwargs: SzseNavSource(**kwargs))
# 交叉校验源。它可以由 PALMMICRO_NAV_SOURCE=crosscheck 显式选用；
# **不要**把它设成 DEFAULT_NAV_SOURCE，否则 registry.create 会自我递归。
register_nav_source("crosscheck", lambda **kwargs: CrossCheckNavSource(**kwargs))
