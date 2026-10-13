"""把「跟踪表」不带回调那一版（末列「参考值」）与**真实 PHP** 逐例对照。

    python -B _php_fundlistitem_compare.py             # 对照
    python -B _php_fundlistitem_compare.py --selftest  # 证明它真的能抓到错

PHP 那一侧由 :file:`_php_fundlistitem_check.php` 现场从 ``php/stock.php`` /
``php/stock/stockref.php`` / ``php/ui/fundlistparagraph.php`` 里**抽出函数体**
跑（不是手抄），所以改了 PHP 这边立刻跟着变。

Python 这一侧跑的是页面真正会走的那条链
``qdii_fund_list.fund_reference_table()`` —— 不用更底层的 ``_row_of``，
因为它才是"个股详情页那一段"的入口。

## 一条已知差异（不是对照失败）

``case.6`` / ``case.7``：PHP 用 ``if ($strVal = $last_sql->ReadVal(...))`` 守卫，
``"0"`` 与 ``false`` 都算没取到，那一行**只有五格**；本版补一个空串凑成六格。
渲染结果一样（行尾留白），但"这一行有几格"始终与列数对得上 ——
见 ``qdii_fund_list._reference_cell`` 的说明。

## 为什么链接两格不参与对照

PHP 那两格是 ``SymCalibrationHistoryLink()`` / ``GetMyStockLink()`` 产出的 HTML，
本版由装配层注入的 ``link_builder`` 决定。两边本来就是两套 URL 体系
（PHP 是 ``calibrationhistorycn.php?symbol=``），对照它们没有意义；
所以 PHP 探针把这两个函数替身成"返回显示文本"。
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

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services.qdii_fund_list import fund_reference_table  # noqa: E402


#: 已知差异：PHP 少一格，本版补空串。理由见模块 docstring。
KNOWN_DIVERGENCE = {
    "case.6": "PHP 把 '0' 判为假 -> 只有五格；本版补空串",
    "case.7": "PHP 把 false 判为假 -> 只有五格；本版补空串",
}


# ---------------------------------------------------------------------------
# Python 侧的替身
# ---------------------------------------------------------------------------
class _Record:
    """``get_calibration_now()`` 的返回：只要 ``date``。"""

    def __init__(self, date: str):
        self.date = date


class _PairRef:
    def __init__(self, symbol: str):
        self._symbol = symbol

    def get_symbol(self):
        return self._symbol

    def get_display(self):
        return self._symbol


class _Ref:
    """``FundPairReference`` 在这张表里用到的那几样。"""

    def __init__(self, spec: dict):
        self.stock_id = spec["stock_id"]
        self._symbol = spec["symbol"]
        self._display = spec["display"]
        self._position = spec["position"]
        self._factor = spec["factor"]
        self._price = spec["price"]
        self._precision = spec["precision"]
        self._pair = _PairRef(spec["pair"]) if spec["pair"] else None

    def get_symbol(self):
        return self._symbol

    def get_display(self):
        return self._display

    def get_position(self):
        return self._position

    def get_factor(self):
        return self._factor

    def get_pair_ref(self):
        return self._pair

    def get_val(self):
        """``strPrice``：参考值那一格的染色基准（``stockref.php:162``）。"""
        return self._price

    def get_precision(self):
        return self._precision


class _Source:
    """``lastcalibration`` 与校准日期的替身。"""

    def __init__(self, dates: dict, last: dict):
        self._dates = dates
        self._last = last

    def get_calibration_now(self, stock_id):
        date = self._dates.get(str(stock_id))
        return None if date is None else _Record(date)

    def get_last_calibration(self, stock_id):
        return self._last.get(str(stock_id))

    def get_stock_name(self, symbol):
        return None


def _render(cell) -> str:
    """把 Python 的单元格渲染成 PHP 探针的同形态字符串。

    PHP 那边颜色是 ``<font color>``（``GetFontElement``），探针替身成
    ``"颜色:文本"``；``black`` 不包标签，等于纯文本 —— 与本版的约定一致。
    """
    if isinstance(cell, str):
        return cell
    text, color = cell
    return text if color in ("", "black") else "%s:%s" % (color, text)


def _python_side(case: dict) -> list:
    source = _Source(case["dates"], case["last"])
    refs = [_Ref(spec) for spec in case["refs"]]
    table = fund_reference_table(refs, source=source)
    return [[_render(cell) for cell in row.cells] for row in table["rows"]]


# ---------------------------------------------------------------------------
# 跑 PHP
# ---------------------------------------------------------------------------
def _run_php() -> dict:
    script = os.path.join(HERE, "_php_fundlistitem_check.php")
    proc = subprocess.run(
        ["php", "-d", "error_reporting=0", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit("PHP 侧失败：%s" % (proc.stderr or proc.stdout))
    start = proc.stdout.find("{")
    if start < 0:
        raise SystemExit("PHP 侧没给出 JSON：%s" % proc.stdout[:400])
    return json.loads(proc.stdout[start:])


#: 与 PHP 探针里的 ``$cases`` 一一对应（顺序、数值必须一致）。
#: ``last`` 用 float 是因为 ``MySqlFundDataSource.get_last_calibration`` 返回 float。
CASES = [
    {
        "name": "正常：参考值高于现价",
        "dates": {"4": "2026-10-09"},
        "last": {"4": 1.5234},
        "refs": [dict(symbol="QQQ", display="QQQ", stock_id="4", position=1.0,
                      factor=10.035, price="1.5", precision=3, pair="^NDX")],
    },
    {
        "name": "参考值低于现价 -> green",
        "dates": {"4": "2026-10-09"},
        "last": {"4": 1.5234},
        "refs": [dict(symbol="QQQ", display="QQQ", stock_id="4", position=1.0,
                      factor=10.035, price="1.6", precision=3, pair="^NDX")],
    },
    {
        "name": "参考值与现价之差在容差内 -> grey",
        "dates": {"4": "2026-10-09"},
        "last": {"4": 1.50001},
        "refs": [dict(symbol="QQQ", display="QQQ", stock_id="4", position=1.0,
                      factor=10.035, price="1.5", precision=3, pair="^NDX")],
    },
    {
        "name": "负仓位：仓位格 red，校准值格照常",
        "dates": {"4": "2026-10-09"},
        "last": {"4": 1.5234},
        "refs": [dict(symbol="SH", display="SH", stock_id="4", position=-1.0,
                      factor=245.591, price="1.5", precision=3, pair="^GSPC")],
    },
    {
        "name": "校准值极小 -> grey（容差 0.5/10^3）",
        "dates": {"4": "2026-10-09"},
        "last": {"4": 1.5234},
        "refs": [dict(symbol="QQQ", display="QQQ", stock_id="4", position=0.0004,
                      factor=0.0004, price="1.5", precision=3, pair="^NDX")],
    },
    {
        "name": "没有校准日期：日期格是 false",
        "dates": {},
        "last": {"4": 1.5234},
        "refs": [dict(symbol="QQQ", display="QQQ", stock_id="4", position=1.0,
                      factor=10.035, price="1.5", precision=3, pair="^NDX")],
    },
    {
        "name": "lastcalibration 里是 0 -> PHP 那一行只有五格",
        "dates": {"4": "2026-10-09"},
        "last": {"4": 0.0},
        "refs": [dict(symbol="QQQ", display="QQQ", stock_id="4", position=1.0,
                      factor=10.035, price="1.5", precision=3, pair="^NDX")],
    },
    {
        "name": "lastcalibration 里没有这一行 -> 同样只有五格",
        "dates": {"4": "2026-10-09"},
        "last": {},
        "refs": [dict(symbol="QQQ", display="QQQ", stock_id="4", position=1.0,
                      factor=10.035, price="1.5", precision=3, pair="^NDX")],
    },
    {
        "name": "精度 0：参考值取整（precision<=0 时不 rtrim0）",
        "dates": {"4": "2026-10-09"},
        "last": {"4": 1.5234},
        "refs": [dict(symbol="QQQ", display="QQQ", stock_id="4", position=1.0,
                      factor=10.035, price="1.5", precision=0, pair="^NDX")],
    },
    {
        "name": "两行共用一个跟踪标的 -> 第二行退化成纯文本",
        "dates": {"4": "2026-10-09", "17": "2026-10-09"},
        "last": {"4": 1.5234, "17": 1.5234},
        "refs": [
            dict(symbol="QQQ", display="QQQ", stock_id="4", position=1.0,
                 factor=10.035, price="1.5", precision=3, pair="^NDX"),
            dict(symbol="TQQQ", display="TQQQ", stock_id="17", position=3.0,
                 factor=30.0, price="1.5", precision=3, pair="^NDX"),
        ],
    },
]


def _compare_rows(php_rows: list, python_rows: list) -> list:
    """逐格比较，返回差异描述列表。"""
    problems = []
    if len(php_rows) != len(python_rows):
        problems.append("行数不同 PHP=%d Python=%d" % (len(php_rows), len(python_rows)))
        return problems
    for index, (php_row, python_row) in enumerate(zip(php_rows, python_rows)):
        shared = min(len(php_row), len(python_row))
        for column in range(shared):
            if php_row[column] != python_row[column]:
                problems.append("行%d 第%d格 PHP=%r Python=%r"
                                % (index, column, php_row[column], python_row[column]))
        for column in range(shared, len(python_row)):
            if python_row[column] != "":
                problems.append("行%d 第%d格 PHP 没有、Python=%r"
                                % (index, column, python_row[column]))
    return problems


def _compare() -> int:
    php = _run_php()
    print("=== 跟踪表（末列「参考值」）PHP 逐例对照 ===")
    print("")
    failures = 0
    for index, case in enumerate(CASES):
        key = "case.%d" % index
        entry = php["cases"][key]
        php_rows = entry["rows"]
        python_rows = _python_side(case)
        problems = _compare_rows(php_rows, python_rows)
        #: PHP 那一行比本版少一格 —— 已知差异里说的就是这件事
        shorter = any(len(php_row) < len(python_row)
                      for php_row, python_row in zip(php_rows, python_rows))
        if key in KNOWN_DIVERGENCE:
            label = "已知差异" + ("（已复现）" if shorter else "（没复现）")
        else:
            label = "一致" if not problems else "不一致"
            failures += 1 if problems else 0
        print("  %-8s %-42s %s" % (key, case["name"], label))
        for problem in problems:
            print("        %s" % problem)
        if key in KNOWN_DIVERGENCE:
            print("        注：%s，PHP %d 格 / Python %d 格"
                  % (KNOWN_DIVERGENCE[key], len(php_rows[0]), len(python_rows[0])))
    print("")
    print("PHP 语义陷阱：%s" % php["traps"])
    print("不一致 %d 例" % failures)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# 自检：注入四种典型错误，看对照抓不抓得到
# ---------------------------------------------------------------------------
def _wrong_baseline(case: dict) -> list:
    """参考值按 0 为基准染色（与仓位/校准值列同款）—— 少了现价基准。"""
    import web.services.qdii_fund_list as qfl

    original = qfl._reference_cell

    def patched(ref, source, stock_id):
        value = qfl._last_calibration_val(source, stock_id)
        return qfl._number_cell(value, qfl.REFERENCE_PRECISION)

    qfl._reference_cell = patched
    try:
        return _python_side(case)
    finally:
        qfl._reference_cell = original


def _wrong_precision(case: dict) -> list:
    """精度写死 3，忽略 ``$ref->GetPrecision()``。"""
    import web.services.qdii_fund_list as qfl

    original = qfl._invoke

    def patched(ref, snake, camel, *args):
        if snake == "get_precision":
            return 3
        return original(ref, snake, camel, *args)

    qfl._invoke = patched
    try:
        return _python_side(case)
    finally:
        qfl._invoke = original


def _wrong_zero(case: dict) -> list:
    """``"0"`` 不做假值处理 —— 照常显示 ``0``。"""
    import web.services.qdii_fund_list as qfl

    original = qfl._reference_cell

    def patched(ref, source, stock_id):
        value = qfl._last_calibration_val(source, stock_id)
        if value is None or value == "":
            return ""
        return qfl.format_price_display(value, qfl._invoke(ref, "get_val", "GetVal"),
                                        qfl.REFERENCE_PRECISION)

    qfl._reference_cell = patched
    try:
        return _python_side(case)
    finally:
        qfl._reference_cell = original


def _wrong_source(case: dict) -> list:
    """参考值读 ``calibration`` 表而不是 ``lastcalibration``。"""
    class WrongSource(_Source):
        def get_last_calibration(self, stock_id):
            record = self.get_calibration_now(stock_id)
            return None if record is None else record.date

    import web.services.qdii_fund_list as qfl

    original = _Source
    globals()["_Source"] = WrongSource
    try:
        return _python_side(case)
    finally:
        globals()["_Source"] = original


def _selftest() -> int:
    php = _run_php()
    wrongs = [
        ("参考值按 0 为基准染色", _wrong_baseline),
        ("精度不取 ref 的 GetPrecision", _wrong_precision),
        ("0 值不做假值处理", _wrong_zero),
        ("读错表（calibration 而非 lastcalibration）", _wrong_source),
    ]
    caught = 0
    print("=== 自检：注入错误，看对照抓不抓得到 ===")
    print("")
    for label, wrong in wrongs:
        hits = 0
        for index, case in enumerate(CASES):
            key = "case.%d" % index
            php_rows = php["cases"][key]["rows"]
            try:
                python_rows = wrong(case)
            except Exception as exc:  # noqa: BLE001
                python_rows = [["EXC:%s" % type(exc).__name__]]
            if _compare_rows(php_rows, python_rows):
                hits += 1
        ok = hits > 0
        caught += 1 if ok else 0
        print("  %s %-34s 触发 %d 例不一致" % ("√" if ok else "×", label, hits))
    print("")
    print("自检抓到 %d / %d 种注入错误" % (caught, len(wrongs)))
    return 0 if caught == len(wrongs) else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else _compare())
