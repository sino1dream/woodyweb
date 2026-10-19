"""把「基金溢价记录」那一段的行与**真实 PHP** 逐例对照。

    python -B _php_fundhistory_compare.py             # 对照
    python -B _php_fundhistory_compare.py --selftest  # 证明它真的能抓到错

PHP 那一侧由 :file:`_php_fundhistory_check.php` 现场从 ``php/stock.php`` /
``php/stock/stockref.php`` / ``php/stock/mysqlref.php`` / ``php/debug.php`` /
``php/ui/fundhistoryparagraph.php`` 抽出函数体跑（不是手抄）。

Python 这一侧跑 ``HistoryService._build_fundhistory()`` —— 也就是
``/history/fundhistory/<sym>`` 与详情页那一段**共用**的取数，连 SQL 都在
路径里（假连接按 SQL 文本回答）。

## 本版不染色的三列（既有偏差，不是对照失败）

``_row_fundhistory`` 里净值 / 溢价 / 误差三格只给文本、不给颜色，与 PHP 的
``GetNetValueDisplay()`` / ``GetPercentageDisplay()`` 不同：

* **净值格**：PHP 的 ``GetNetValueDisplay()`` 调的是
  ``GetPriceDisplay($fNetValue, false)``，单参数时基准取 **ref 的现价**
  （``stockref.php:162``）；本版的数据层不持有 ref，拿不到现价，于是不染色。
* **溢价 / 误差格**：PHP 的 ``GetPercentageDisplay()`` 把负数染红、正数不染
  （``stockref.php:148``）；本版的 ``format_percentage_display()`` 只返回文本。

三处都是"数据层不持有引用对象"这个架构差异的结果（PHP 的 ref 既是数据源
又是显示对象）。对照时**只比文本**，颜色差异单列成一条已知偏差。

会**严格**比颜色的是两列：价格（基准净值）与官方 EST（基准净值）。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(HERE)
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core.stocksymbol import in_array_qdii, in_array_hk_mix, in_array_qdii_mix  # noqa: E402
from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services.history_service import HistoryService  # noqa: E402


#: 会严格比颜色的列下标：0 日期 / 1 价格 / 2 净值 / 3 溢价 /
#: 4 官方EST / 5 时间 / 6 误差 / 7 标的净值。理由见模块 docstring。
COLORED_COLUMNS = (1, 4)


# ---------------------------------------------------------------------------
# 假数据库连接：让 HistoryService 的 SQL 真的跑一遍
# ---------------------------------------------------------------------------
class _Cursor:
    """按 SQL 文本回答查询；认不出的查询返回空（让上层走自己的兜底）。"""

    def __init__(self, case: dict):
        self._case = case
        self._rows: list = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        text = " ".join(str(sql).split())
        case = self._case
        table = _table_of(text)
        stock_id = str(params[0]) if params else ""

        if "COUNT(*)" in text:
            if table == "fundest":
                self._rows = [{"n": case["est_count"]}]
            elif table == "netvaluehistory":
                #: ``_est_has_netvalue()`` 按 stock_id 数 est 的净值行 ——
                #: 决定末列精度档（4 位还是 est 符号自身精度）。
                stock_id = str(params[0]) if params else ""
                self._rows = [{"n": case.get("nav_counts", {}).get(stock_id, 0)}]
            else:
                self._rows = [{"n": 0}]
            return
        #: ``date <`` 必须先判：它也带 ``ORDER BY date DESC``（get_record_prev），
        #: 放后面会被当成分页查询，于是把日期当成了 ``start``（实测踩过）。
        if table == "stockhistory" and "`date` <" in text:
            date = str(params[1])
            for row in case["history"].get(stock_id, []):
                if str(row["date"]) < date:
                    self._rows = [row]
                    return
            self._rows = []
            return
        if table == "stockhistory" and "ORDER BY `date` DESC" in text:
            rows = case["history"].get(stock_id, [])
            start = int(params[1]) if len(params) > 1 else 0
            num = int(params[2]) if len(params) > 2 else 0
            self._rows = rows[start:] if num == 0 else rows[start:start + num]
            return
        if table == "netvaluehistory" and "`date` =" in text:
            date = str(params[1])
            value = case["netvalue"].get(stock_id, {}).get(date)
            self._rows = [] if value is None else [{"date": date, "close": value}]
            return
        if table == "fundest" and "`date` =" in text:
            date = str(params[1])
            record = case["est"].get(stock_id, {}).get(date)
            self._rows = [] if record is None else [record]
            return
        self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self) -> None:
        pass


def _table_of(sql: str) -> str:
    for name in ("stockhistory", "netvaluehistory", "fundest"):
        if "FROM `%s`" % name in sql:
            return name
    return ""


class _Connection:
    def __init__(self, case: dict):
        self._case = case

    def cursor(self):
        return _Cursor(self._case)


class _Profile:
    def __init__(self, est_symbol):
        self.est_symbol = est_symbol
        self.pair_symbol = None


class _Source:
    """``_est_symbol()`` 要的那一口：`get_fund_profile()` + `get_stock_id()`。"""

    def __init__(self, case: dict):
        self._case = case

    def get_fund_profile(self, symbol):
        est = self._case.get("est_ref_symbol")
        return None if est is None else _Profile(est)

    def get_stock_id(self, symbol):
        return self._case["ids"].get(symbol)

    def get_history_close(self, stock_id, date):
        """中间层没有净值记录时的回落（PHP ``GetNetValue()`` 第三支）。

        est 有净值记录但缺该日期时**不回落**（PHP 返回 false -> 空格），
        由 ``est_has_netvalue`` 把着门，这里只按例数据回答。
        """
        return self._case.get("est_history", {}).get(str(stock_id), {}).get(str(date))


def _render(cell) -> str:
    if isinstance(cell, str):
        return cell
    text, color = cell
    return text if color in ("", "black") else "%s:%s" % (color, text)


def _python_side(case: dict) -> list:
    service = HistoryService(_Source(case), _Connection(case))
    _columns, rows, _total = service._build_fundhistory(
        case["symbol"], case["stock_id"], case["start"], case["num"])
    return [[_render(cell) for cell in row.cells] for row in rows]


# ---------------------------------------------------------------------------
# 跑 PHP
# ---------------------------------------------------------------------------
def _run_php() -> dict:
    script = os.path.join(HERE, "_php_fundhistory_check.php")
    proc = subprocess.run(
        ["php", "-d", "error_reporting=0", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit("PHP 侧失败：%s" % (proc.stderr or proc.stdout))
    start = proc.stdout.find("{")
    if start < 0:
        raise SystemExit("PHP 侧没给出 JSON：%s" % proc.stdout[:400])
    return json.loads(proc.stdout[start:])


def _history(symbol_id: str, rows: list) -> dict:
    return {symbol_id: [{"stock_id": symbol_id, "date": date, "close": close}
                        for date, close in rows]}


#: 与 PHP 探针里的 ``$cases`` 一一对应。**history 已按日期倒序**
#: （两边都是 ``ORDER BY date DESC`` 的口径）。
CASES = [
    {
        "name": "QDII：净值取前一交易日",
        "symbol": "SZ161130",
        "stock_id": "1",
        "ids": {"SZ161130": "1"},
        "start": 0, "num": 0,
        "history": _history("1", [("2026-10-02", "2.041"), ("2026-10-01", "2.033"),
                                  ("2026-09-30", "2.020")]),
        "netvalue": {"1": {"2026-09-30": "2.037898", "2026-09-29": "2.030",
                           "2026-09-28": "2.010"}},
        "est_count": 0,
        "est": {},
        "est_ref_symbol": None,
    },
    {
        "name": "非 QDII：净值取同一天",
        "symbol": "QQQ",
        "stock_id": "4",
        "ids": {"QQQ": "4"},
        "start": 0, "num": 0,
        "history": _history("4", [("2026-10-01", "742.03"), ("2026-09-30", "738.5")]),
        "netvalue": {"4": {"2026-10-01": "740.0", "2026-09-30": "736.0"}},
        "est_count": 0,
        "est": {},
        "est_ref_symbol": None,
    },
    {
        "name": "有官方 EST 与标的：每行多三格 + 末列标的净值",
        "symbol": "SZ161130",
        "stock_id": "1",
        "ids": {"SZ161130": "1", "QQQ": "4"},
        "start": 0, "num": 0,
        "history": _history("1", [("2026-10-02", "2.041"), ("2026-10-01", "2.033")]),
        "netvalue": {"1": {"2026-10-01": "2.037898", "2026-09-30": "2.030"},
                     #: 末列取的是**中间层**在 EST 日期上的净值
                     "4": {"2026-10-01": "740.0"}},
        "est_count": 2,
        "est": {"1": {"2026-10-01": {"id": "11", "date": "2026-10-01",
                                     "close": "2.040", "time": "00:40:06"}}},
        "est_ref_symbol": "QQQ",
        #: QQQ 有净值记录 -> 末列走 NETVALUE_PRECISION(4)
        "nav_counts": {"4": 1},
        "est_history": {},
    },
    {
        "name": "有 EST 但没有标的引用：末列不出现",
        "symbol": "SZ161130",
        "stock_id": "1",
        "ids": {"SZ161130": "1"},
        "start": 0, "num": 0,
        "history": _history("1", [("2026-10-02", "2.041"), ("2026-10-01", "2.033")]),
        "netvalue": {"1": {"2026-10-01": "2.037898"}},
        "est_count": 1,
        "est": {"1": {"2026-10-01": {"id": "11", "date": "2026-10-01",
                                     "close": "2.040", "time": "23:28:31"}}},
        "est_ref_symbol": None,
    },
    {
        "name": "EST 记录的 close 是 0 -> PHP 不加那三格",
        "symbol": "SZ161130",
        "stock_id": "1",
        "ids": {"SZ161130": "1"},
        "start": 0, "num": 0,
        "history": _history("1", [("2026-10-02", "2.041"), ("2026-10-01", "2.033")]),
        "netvalue": {"1": {"2026-10-01": "2.037898"}},
        "est_count": 1,
        "est": {"1": {"2026-10-01": {"id": "11", "date": "2026-10-01",
                                     "close": "0", "time": "00:40:06"}}},
        "est_ref_symbol": None,
    },
    {
        "name": "净值缺失的行情日整行不出现",
        "symbol": "QQQ",
        "stock_id": "4",
        "ids": {"QQQ": "4"},
        "start": 0, "num": 0,
        "history": _history("4", [("2026-10-01", "742.03"), ("2026-09-30", "738.5")]),
        "netvalue": {"4": {"2026-10-01": "740.0"}},
        "est_count": 0,
        "est": {},
        "est_ref_symbol": None,
    },
    {
        "name": "前一个交易日取不到 -> 整行不出现",
        "symbol": "SZ161130",
        "stock_id": "1",
        "ids": {"SZ161130": "1"},
        "start": 0, "num": 0,
        "history": _history("1", [("2026-10-02", "2.041")]),
        "netvalue": {"1": {"2026-10-01": "2.037898"}},
        "est_count": 0,
        "est": {},
        "est_ref_symbol": None,
    },
    {
        "name": "溢价方向：价格低于净值 -> 溢价为负",
        "symbol": "QQQ",
        "stock_id": "4",
        "ids": {"QQQ": "4"},
        "start": 0, "num": 0,
        "history": _history("4", [("2026-10-01", "735.0")]),
        "netvalue": {"4": {"2026-10-01": "740.0"}},
        "est_count": 0,
        "est": {},
        "est_ref_symbol": None,
    },
    # -- 净值列精度（2026-10-19 用户对照生产端 sz161125cn.php 发现少一位） --
    {
        "name": "净值四位：3.1548 不四舍五入成 3.155",
        "symbol": "SZ161125",
        "stock_id": "2",
        "ids": {"SZ161125": "2"},
        "start": 0, "num": 0,
        "history": _history("2", [("2026-10-16", "3.299"), ("2026-10-15", "3.266")]),
        "netvalue": {"2": {"2026-10-15": "3.1548"}},
        "est_count": 0,
        "est": {},
        "est_ref_symbol": None,
    },
    {
        "name": "est 无净值行：末列取行情价、精度用 est 符号的（指数 2 位）",
        "symbol": "SZ161125",
        "stock_id": "2",
        "ids": {"SZ161125": "2", "^GSPC": "7"},
        "start": 0, "num": 0,
        "history": _history("2", [("2026-10-16", "3.299"), ("2026-10-15", "3.266")]),
        "netvalue": {"2": {"2026-10-15": "3.1548"}},
        "est_count": 1,
        "est": {"2": {"2026-10-15": {"id": "64", "date": "2026-10-15",
                                     "close": "3.15348", "time": "11:37:50"}}},
        "est_ref_symbol": "^GSPC",
        #: ^GSPC 没有净值记录 -> 末列回落行情价，7744.0625 按 2 位成 7744.06
        "nav_counts": {},
        "est_history": {"7": {"2026-10-15": "7744.0625"}},
    },
    {
        "name": "est 有净值行：末列四位",
        "symbol": "SZ161130",
        "stock_id": "1",
        "ids": {"SZ161130": "1", "QQQ": "4"},
        "start": 0, "num": 0,
        "history": _history("1", [("2026-10-02", "2.041"), ("2026-10-01", "2.033")]),
        "netvalue": {"1": {"2026-10-01": "2.037898"},
                     "4": {"2026-10-01": "740.1234"}},
        "est_count": 1,
        "est": {"1": {"2026-10-01": {"id": "11", "date": "2026-10-01",
                                     "close": "2.040", "time": "00:40:06"}}},
        "est_ref_symbol": "QQQ",
        "nav_counts": {"4": 1},
        "est_history": {},
    },
    {
        "name": "est 有净值行但缺 EST 日期：末列空（不回落行情价）",
        "symbol": "SZ161130",
        "stock_id": "1",
        "ids": {"SZ161130": "1", "QQQ": "4"},
        "start": 0, "num": 0,
        "history": _history("1", [("2026-10-02", "2.041"), ("2026-10-01", "2.033")]),
        "netvalue": {"1": {"2026-10-01": "2.037898"},
                     #: QQQ 只有 09-30 的净值，EST 记录在 10-01
                     "4": {"2026-09-30": "740.0"}},
        "est_count": 1,
        "est": {"1": {"2026-10-01": {"id": "11", "date": "2026-10-01",
                                     "close": "2.040", "time": "00:40:06"}}},
        "est_ref_symbol": "QQQ",
        "nav_counts": {"4": 1},
        #: 即使有行情价也不该出现（"有净值记录"分支里缺日期即 false）
        "est_history": {"4": {"2026-10-01": "740.5"}},
    },
]


def _strip_color(cell: str) -> str:
    """去掉 PHP 探针加的 ``颜色:`` 前缀。"""
    for color in ("red", "green", "grey", "gray", "black"):
        prefix = color + ":"
        if cell.startswith(prefix):
            return cell[len(prefix):]
    return cell


def _compare_rows(php_rows: list, python_rows: list) -> list:
    problems = []
    if len(php_rows) != len(python_rows):
        problems.append("行数不同 PHP=%d Python=%d" % (len(php_rows), len(python_rows)))
        return problems
    for index, (php_row, python_row) in enumerate(zip(php_rows, python_rows)):
        if len(php_row) != len(python_row):
            problems.append("行%d 格数不同 PHP=%d Python=%d"
                            % (index, len(php_row), len(python_row)))
            continue
        for column, (php_cell, python_cell) in enumerate(zip(php_row, python_row)):
            if _strip_color(php_cell) != _strip_color(python_cell):
                problems.append("行%d 第%d格 文本 PHP=%r Python=%r"
                                % (index, column, php_cell, python_cell))
            elif column in COLORED_COLUMNS and php_cell != python_cell:
                problems.append("行%d 第%d格 颜色 PHP=%r Python=%r"
                                % (index, column, php_cell, python_cell))
    return problems


def _compare() -> int:
    php = _run_php()
    print("=== 基金溢价记录（_echoHistoryTableData）PHP 逐例对照 ===")
    print("")
    failures = 0
    for index, case in enumerate(CASES):
        key = "case.%d" % index
        php_rows = php["cases"][key]["rows"]
        python_rows = _python_side(case)
        problems = _compare_rows(php_rows, python_rows)
        failures += 1 if problems else 0
        print("  %-8s %-46s %s" % (key, case["name"], "一致" if not problems else "不一致"))
        for problem in problems:
            print("        %s" % problem)
    print("")
    print("不一致 %d 例" % failures)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def _wrong_same_day(case: dict) -> list:
    """净值日期不往前挪（恒取同一天）。"""
    import web.services.history_service as hs

    original = hs.use_same_day_net_value
    hs.use_same_day_net_value = lambda *a, **k: True
    try:
        return _python_side(case)
    finally:
        hs.use_same_day_net_value = original


def _wrong_no_filter(case: dict) -> list:
    """净值取不到也照样出行（去掉行过滤）。"""
    import web.services.history_service as hs

    original = hs.HistoryService._build_fundhistory

    def patched(self, symbol, stock_id, start, num):
        history = hs.StockHistorySql(self.connection)
        netvalue = hs.NetValueHistorySql(self.connection)
        rows = []
        for record in history.get_all(stock_id, start, num):
            rows.append(self._row_fundhistory(record, "1.0", None, None, netvalue, False))
        return hs._columns_fundhistory(None), rows, history.count(stock_id)

    hs.HistoryService._build_fundhistory = patched
    try:
        return _python_side(case)
    finally:
        hs.HistoryService._build_fundhistory = original


def _wrong_est_gate(case: dict) -> list:
    """EST 的 close 不做 PHP 假值判断（'0' 也当有效值）。"""
    import web.services.history_service as hs

    original = hs.HistoryService._row_fundhistory

    def patched(self, record, net_value, estimate, est_symbol, netvalue, est_has_netvalue=False):
        row = hs.TableRow()
        row.add(str(record["date"]))
        row.add(hs.format_price_display(record["close"], net_value))
        row.add(hs.format_price_display(net_value))
        row.add(hs.format_percentage_display(net_value, record["close"]))
        if estimate is not None:
            close = estimate.get("close")
            row.add(hs.format_price_display(close, net_value))
            row.add(hs.format_hm(estimate.get("time")))
            row.add(hs.format_percentage_display(net_value, close))
        return row

    hs.HistoryService._row_fundhistory = patched
    try:
        return _python_side(case)
    finally:
        hs.HistoryService._row_fundhistory = original


def _wrong_netvalue_precision(case: dict) -> list:
    """净值列退回默认 3 位 —— 2026-10-19 修掉的那个 bug。

    只把净值格（下标 2）换回 ``format_price_display(net_value)``，
    其余格子仍走真实现，确保对照抓到的是精度本身。
    """
    import web.services.history_service as hs

    original = hs.HistoryService._row_fundhistory

    def patched(self, record, net_value, estimate, est_symbol, netvalue, est_has_netvalue=False):
        row = original(self, record, net_value, estimate, est_symbol, netvalue, est_has_netvalue)
        row.cells[2] = hs.format_price_display(net_value)
        return row

    hs.HistoryService._row_fundhistory = patched
    try:
        return _python_side(case)
    finally:
        hs.HistoryService._row_fundhistory = original


def _wrong_premium_direction(case: dict) -> list:
    """溢价方向搞反（净值/价格 而不是 价格/净值）。"""
    import web.services.history_service as hs

    original = hs.format_percentage_display

    def patched(prev, value):
        return original(value, prev)

    hs.format_percentage_display = patched
    try:
        return _python_side(case)
    finally:
        hs.format_percentage_display = original


def _selftest() -> int:
    php = _run_php()
    wrongs = [
        ("净值日期不往前挪", _wrong_same_day),
        ("净值取不到也出行", _wrong_no_filter),
        ("EST 的 0 不做假值判断", _wrong_est_gate),
        ("溢价方向搞反", _wrong_premium_direction),
        ("净值列退回 3 位", _wrong_netvalue_precision),
    ]
    caught = 0
    print("=== 自检：注入错误，看对照抓不抓得到 ===")
    print("")
    for label, wrong in wrongs:
        hits = 0
        for index, case in enumerate(CASES):
            php_rows = php["cases"]["case.%d" % index]["rows"]
            try:
                python_rows = wrong(case)
            except Exception as exc:  # noqa: BLE001
                python_rows = [["EXC:%s" % type(exc).__name__]]
            if _compare_rows(php_rows, python_rows):
                hits += 1
        ok = hits > 0
        caught += 1 if ok else 0
        print("  %s %-28s 触发 %d 例不一致" % ("√" if ok else "×", label, hits))
    print("")
    print("自检抓到 %d / %d 种注入错误" % (caught, len(wrongs)))
    return 0 if caught == len(wrongs) else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else _compare())
