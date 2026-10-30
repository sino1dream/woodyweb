"""美股 ETF 单位净值抓取与落库（``woody/res/submitnetvalue.php``）。

## PHP 那一页在做什么

``_AdminNetValueAccount::AdminProcess()`` 只有两行分派::

    if (GetInvescoOfficialUrl($strSymbol))  UpdateInvescoNetValue($strSymbol);
    else                                    DebugNetValueXlsStr($ref);

即 **Invesco 走 JSON、其余走 XLS**。两条路的写入目标都一样：
``netvaluehistory``（SPDR 那条还顺带写 ``shareshistory``）。

## 三个源，形状各不相同

**① Invesco JSON**（只认 QQQ，``GetInvescoNetValueUrl()`` 里写死 CUSIP）::

    {"effectiveDate": "2026-10-29", "cusip": "46090E103", "nav": 715.178947,
     "sharesOutstanding": 672400000, "marketValue": 480886323695.28, …}

只写**一条**（最新净值）。``sharesOutstanding`` PHP 不写 —— 照抄。

> ``source/usnavsource.py`` 的模块 docstring 写着"Invesco dng-api 实测
> HTTP 406"。2026-10-30 复测是 **200**，差别在 ``User-Agent``：不带 UA 仍是
> 406，带上浏览器 UA 就正常返回 JSON。那条结论过时了，见
> ``README_submit_pages_migration.md`` §五。

**② SPDR xlsx**（SPY / XBI / XLE / XLY / XOP）::

    https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/navhist-us-en-spy.xlsx

一张表 ``navhist``，前 3 行是标题（第 3 行整行空白），第 4 行才是表头，
第 5 行起**按日期降序**::

    第4行: Date            NAV          Shares Outstanding  Total Net Assets
    第5行: 29-Oct-2026     769.506335   1057282116          813585286253.05

列下标照 PHP ``_readXlsFile``：``$ar[0]`` 日期 / ``$ar[1]`` 净值 /
``$ar[2]`` 流通股数。日期是**共享字符串** ``"29-Oct-2026"``（``d-M-Y``）。

**③ iShares 老式 .xls** —— **没做**。PHP 用 ``PHPExcel_IOFactory`` 能开
（``_getIsharesXlsUrl()`` 只登记了 IEO 一只，且返回的是 BIFF/OLE 复合文档），
本项目的 ``source/xlsxreader`` 只认 xlsx（zip）。见 §偏离 3。

## 三处必须照抄的细节

1. **``IsTooOld`` 是 ``break`` 不是 ``continue``**（``_spdrnetvaluexls.php:55``）：
   表是降序的，一旦读到 2014-01-01 之前就可以整段停下。写成 ``continue``
   会把 5926 行全扫一遍，结果虽然一样，但在最老的几行上白白多跑几十万次。
2. **净值写进去之后要删当天的校准值**（只有 SPDR 那条路有）::

       if ($net_sql->WriteDaily(...)) {
           if ($cal_sql->GetClose($strStockId, $strDate)) $cal_sql->DeleteByDate($strStockId, $strDate);
       }

   顺序是"先写净值、写成功了才删校准"，而且**只在净值真的变了**时才删 ——
   净值没变说明这天已经处理过，校准值是后来重新算的，不能误删。
3. **流通股数除以 10000**：`strval(floatval($ar[2]) / 10000.0)`，
   ``shareshistory.close`` 存的是"万份"。1057282116 → ``105728.2116``。

## 与 PHP 的刻意偏离

1. **没有本地缓存文件**。PHP 先把字节落进 ``debug/netvalue_XXX.xls`` 再读，
   并用 ``StockNeedFile`` 做分钟级闸门（防止每次页面渲染都下载）。本版的
   调用方是"管理员点一下"，不构成热循环，所以取字节直接解析 —— 与
   ``updater/etfholdings`` 的取舍一致。
2. **QQQ 的净值现在是真的官方 NAV**（715.178947，2026-10-29），不再走
   ``usnavsource`` 那条"拿收盘价当净值"的派生口径。两者差 0.02%
   （收盘价 715.34），足以让 ``netvalueclosecn`` 的折溢价列变化。
3. **iShares 不支持**，返回原因而不是静默。见上面 ③。
"""

from __future__ import annotations

import datetime
import json
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from sql.historytable import CalibrationSql, NetValueHistorySql, SharesHistorySql
from source import xlsxreader

#: Invesco 的 NAV 接口（``GetInvescoNetValueUrl()``，``externalurl.php:320``）。
#: PHP 用 CUSIP 定位份额类别，只有 QQQ 一个条目。
INVESCO_NAV_URL = (
    "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/"
    "46090E103/prices?idType=cusip&variationType=priceListing"
    "&productType=ETF&productSubType=ETF"
)

#: ``GetInvescoOfficialUrl()`` 认得的那一只。
INVESCO_SYMBOLS = ("QQQ",)

#: SPDR 的净值 XLS（``GetSpdrNetValueUrl()``，``externalurl.php:298``）。
SPDR_NAV_URL = (
    "https://www.ssga.com/us/en/individual/etfs/library-content/"
    "products/fund-data/etfs/us/navhist-us-en-%s.xlsx"
)

#: 与 ``updater/etfdividend.SPDR_SYMBOLS`` 同一批（都来自 ``GetSpdrOfficialUrl()``）。
SPDR_SYMBOLS = ("SPY", "XBI", "XLE", "XLY", "XOP")

#: SPDR 表里三列的 0 基下标（``_spdrnetvaluexls.php:32-38`` 的非 ishares 分支）。
SPDR_SHEET_INDEX = 0
SPDR_DATE_COLUMN = 0
SPDR_NAV_COLUMN = 1
SPDR_SHARES_COLUMN = 2

#: 流通股数存"万份"（PHP 除以 10000）。
SHARES_DIVISOR = 10000.0

#: ``OldestYMD`` 的界限（``php/class/year_month_day.php:164``）。
OLDEST_VALID_DATE = datetime.date(2014, 1, 1)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 30.0


def is_invesco_symbol(symbol: str) -> bool:
    """``GetInvescoOfficialUrl($strSymbol) != false``。"""
    return str(symbol).strip().upper() in INVESCO_SYMBOLS


def is_spdr_symbol(symbol: str) -> bool:
    """``GetSpdrOfficialUrl($strSymbol) != false``。"""
    return str(symbol).strip().upper() in SPDR_SYMBOLS


def spdr_nav_url(symbol: str) -> str:
    """``GetSpdrNetValueUrl()``：文件名里的 ticker 是**小写**。"""
    return SPDR_NAV_URL % str(symbol).strip().lower()


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def parse_invesco_nav(text: str) -> Optional[Dict[str, Any]]:
    """Invesco JSON → ``{"date": "2026-10-29", "nav": 715.178947}``。

    两个字段都在才认：``effectiveDate`` 是净值**生效日**（与 ``WriteDaily``
    的周末闸门正好对上 —— 净值只在交易日生效），``nav`` 是单位净值。
    """
    payload = _load_json(text)
    if not isinstance(payload, dict):
        return None
    date = _normalize_date(payload.get("effectiveDate"))
    nav = _parse_number(payload.get("nav"))
    if not date or nav is None:
        return None
    return {"date": date, "nav": nav}


def parse_spdr_nav_xlsx(data: bytes) -> List[Dict[str, Any]]:
    """SPDR 的 ``navhist`` 表 → ``[{"date","nav","shares"}]``，**保持原顺序**（降序）。

    ``shares`` 已经是"万份"（已除 10000）。解析不出的列给 ``None``，
    由调用方跳过 —— PHP 那边 ``floatval(null)`` 得 0，会写一条 0 净值进去，
    那是脏数据，不照抄。

    第 3 行整行空白（实测 SPY 文件如此），所以按行号读，不看"第几条"。
    """
    rows = xlsxreader.read_rows(data, SPDR_SHEET_INDEX)
    result = []
    for row in rows:
        if not row:
            continue
        date = _parse_excel_date(row[SPDR_DATE_COLUMN] if len(row) > SPDR_DATE_COLUMN else None)
        if not date:
            #: 表头与末尾的免责声明都在这里被跳过（``strtotime()`` 同样失败）
            continue
        nav = _parse_number(_at(row, SPDR_NAV_COLUMN))
        shares = _parse_number(_at(row, SPDR_SHARES_COLUMN))
        result.append({
            "date": date,
            "nav": nav,
            "shares": None if shares is None else float(shares) / SHARES_DIVISOR,
        })
    return result


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------
def update_etf_net_value(
    source: Any,
    symbol: str,
    *,
    fetch_text: Optional[Callable[[str], Optional[str]]] = None,
    fetch_bytes: Optional[Callable[[str], Optional[bytes]]] = None,
) -> Dict[str, Any]:
    """对应 ``_AdminNetValueAccount::AdminProcess()``。

    返回 ``{"symbol", "source", "url", "rows", "written", "shares_written",
    "calibration_deleted", "reason"}``。

    两条路的分派与 PHP 一致：Invesco 认得的一只走 JSON，其余走 XLS。
    """
    symbol = str(symbol).strip()
    result: Dict[str, Any] = {
        "symbol": symbol,
        "source": "invesco" if is_invesco_symbol(symbol) else "spdr",
        "url": INVESCO_NAV_URL if is_invesco_symbol(symbol) else None,
        "rows": 0,
        "written": 0,
        "shares_written": 0,
        "calibration_deleted": 0,
        "reason": None,
    }

    connection = getattr(source, "connection", source)
    stock_id = source.get_stock_id(symbol)
    if stock_id is None:
        result["reason"] = "stock 表里没有 %s 这个符号" % symbol
        return result

    if is_invesco_symbol(symbol):
        text = (fetch_text or fetch_default_text)(INVESCO_NAV_URL)
        if not text:
            result["reason"] = "没取到 Invesco 的净值 JSON"
            return result
        row = parse_invesco_nav(text)
        if row is None:
            result["reason"] = "Invesco 返回的内容里没有 effectiveDate / nav"
            return result
        return _write_net_value_rows(result, connection, stock_id, [row])

    if not is_spdr_symbol(symbol):
        result["url"] = None
        result["reason"] = (
            "%s 不是 SPDR 的 ETF（iShares 那条老式 .xls 本版不支持）" % symbol
        )
        return result

    result["url"] = spdr_nav_url(symbol)
    data = (fetch_bytes or fetch_default_bytes)(result["url"])
    if not data:
        result["reason"] = "没取到 SPDR 的净值 XLS"
        return result
    try:
        rows = parse_spdr_nav_xlsx(data)
    except xlsxreader.XlsxError as error:
        result["reason"] = "XLS 读不了：%s" % error
        return result
    if not rows:
        result["reason"] = "XLS 里没有可用的净值行"
        return result
    return _write_net_value_rows(result, connection, stock_id, rows, with_shares=True)


def _write_net_value_rows(
    result: Dict[str, Any],
    connection: Any,
    stock_id: Any,
    rows: List[Dict[str, Any]],
    *,
    with_shares: bool = False,
) -> Dict[str, Any]:
    """逐行写净值（SPDR 那条连带写流通股数与清校准值）。

    三道日期闸门照 ``OldestYMD`` + ``_readXlsFile`` 的写法：

    * 早于 2014-01-01 → **直接停**（表是降序的，后面的更老）；
    * 周末 / 未来日期 → 跳过这一行，**继续**往下读。

    ``WriteDaily`` 自己也有周末闸门，这里先拦一遍是为了与 PHP 的
    ``if ($oldest_ymd->IsInvalid($strDate) === false)`` 同构 —— 否则
    "净值写成功才删校准"这段会对着一个根本不会写成功的日期空转。
    """
    net_sql = NetValueHistorySql(connection)
    shares_sql = SharesHistorySql(connection) if with_shares else None
    calibration_sql = CalibrationSql(connection) if with_shares else None

    today = datetime.date.today()
    valid = 0
    for row in rows:
        moment = _to_date(row["date"])
        if moment is None:
            continue
        if moment < OLDEST_VALID_DATE:
            break
        if moment.weekday() >= 5 or moment > today:
            continue
        valid += 1
        if row.get("nav") is None:
            continue
        if net_sql.write_daily(stock_id, row["date"], row["nav"]):
            result["written"] += 1
            if calibration_sql is not None:
                #: 净值变了 → 那天的校准值是按旧净值算的，删掉让它重算
                if calibration_sql.get_close(stock_id, row["date"]) is not None:
                    calibration_sql.delete_by_date(stock_id, row["date"])
                    result["calibration_deleted"] += 1
        if shares_sql is not None and row.get("shares") is not None:
            if shares_sql.write_daily(stock_id, row["date"], row["shares"]):
                result["shares_written"] += 1

    connection.commit()
    result["rows"] = valid
    return result


# ---------------------------------------------------------------------------
# 抓取与转换
# ---------------------------------------------------------------------------
def fetch_default_text(url: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """取 JSON 文本，失败返回 ``None``。"""
    data = fetch_default_bytes(url, timeout=timeout)
    if data is None:
        return None
    return data.decode("utf-8", "replace")


def fetch_default_bytes(url: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[bytes]:
    """取字节，失败返回 ``None``。

    ``User-Agent`` 是 Invesco 那道闸的钥匙：缺了直接 406（见模块 docstring）。
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception:
        return None


def _load_json(text: Any) -> Any:
    if isinstance(text, (list, dict)):
        return text
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _parse_excel_date(value: Any) -> Optional[str]:
    """``strtotime("29-Oct-2026")`` —— ``d-M-Y``，月份是英文三字母缩写。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    for pattern in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _normalize_date(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(value.strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _parse_number(value: Any) -> Optional[Any]:
    """校验是数字后**原样**返回（同 ``etfdividend._parse_number``，pitfall #149）。"""
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


def _at(row: List[Any], index: int) -> Any:
    """安全取列：行比 index 短时给 ``None``。"""
    return row[index] if len(row) > index else None


def _to_date(value: Any) -> Optional[datetime.date]:
    if isinstance(value, datetime.date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
