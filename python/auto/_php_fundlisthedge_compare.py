"""把「对冲值」的 Python 实现与**真实 PHP** 逐例对照。

    python -B _php_fundlisthedge_compare.py             # 对照
    python -B _php_fundlisthedge_compare.py --selftest  # 证明它真的能抓到错

PHP 那一侧由 :file:`_php_fundlisthedge_check.php` 现场从 ``php/stock.php`` 与
``woody/res/php/_qdiigroup.php`` 里**抽出函数体**跑（不是手抄），所以改了 PHP
这边立刻跟着变。

Python 这一侧跑的是**真正会被页面用到**的那条链：

    qdii_fund_list._hedge_of()  ->  MySqlFundDataSource.get_stock_hedge()

也就是 ``GetStockHedge()`` 的迁移版。为了让它是真跑而不是替身，本脚本给它喂一个
**假数据库连接**（``_Cursor`` 按 SQL 文本回答四种查询），于是连 SQL 都在路径里。

## 两条刻意不复制的 PHP 行为

``case.5`` 与 ``case.6`` 是**已知差异**，不是对照失败：

* ``0.0`` —— PHP 里 ``'0.0'`` 是**真值**（只有恰好 ``'0'`` 才是假值），
  于是 PHP 8 真的去除 → ``DivisionByZeroError`` → **整页 500**；
  Python 由 ``_hedge_of`` 兜成 ``1.0``，页面照常显示。
* ``'0'`` —— PHP 判假，跳过这一天继续往下扫；Python 的 ``get_stock_hedge``
  只看 ``is not None``，``0.0`` 被当成有效值 → 除零 → 同样兜成 ``1.0``。

两种都是"校准值等于 0"这种不该出现的数据。**不复制崩溃**是本项目的既有取向
（页面上的一个装饰列不该让整页 500），所以记为偏差而不是去改共享的
``get_stock_hedge`` —— 它被其它页面共用，改它是在用别处的风险换这里的 fidelity。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

AUTO_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from stockdata import MySqlFundDataSource  # noqa: E402
from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services.qdii_fund_list import _hedge_of, _number_cell  # noqa: E402

#: 已知差异：PHP 会崩 / 会跳过，Python 统一兜成 1.0。理由见模块 docstring。
KNOWN_DIVERGENCE = {
    "case.5": "PHP 8 抛 DivisionByZeroError（整页 500）；本版兜成 1.0，不复制崩溃",
    "case.6": "PHP 把 '0' 判假并跳过该日期；本版 0.0 被当有效值 -> 除零 -> 兜 1.0",
}

TOLERANCE = 1e-9


# ---------------------------------------------------------------------------
# 假数据库连接：让 get_stock_hedge 的 SQL 真的跑一遍
# ---------------------------------------------------------------------------
class _Cursor:
    """按 SQL 文本回答查询的假游标。

    只认 ``get_stock_hedge`` 会发出的那几条：符号查 id、查仓位、
    查某标的的全部校准行（已按日期倒序，与 PHP 的 ``ORDER BY date DESC`` 一致）、
    查某标的某天的校准值。认不出的查询返回空 —— 让上层走它自己的兜底，
    而不是静默给出看着合理的数。
    """

    def __init__(self, fixture: dict):
        self._fixture = fixture
        self._rows: list = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        text = " ".join(str(sql).split())
        fixture = self._fixture
        if "FROM stock WHERE symbol" in text:
            stock_id = fixture["ids"].get(params[0])
            self._rows = [{"id": stock_id}] if stock_id is not None else []
        elif "FROM fundposition" in text:
            value = fixture["positions"].get(str(params[0]))
            self._rows = [] if value is None else [{"close": value}]
        elif text.startswith("SELECT date, close FROM calibrationhistory"):
            rows = fixture["calibration"].get(str(params[0]), [])
            self._rows = [{"date": date, "close": close} for date, close in rows]
        elif text.startswith("SELECT close FROM calibrationhistory"):
            stock_id, date = str(params[0]), str(params[1])
            for row_date, close in fixture["calibration"].get(stock_id, []):
                if row_date == date:
                    self._rows = [{"close": close}]
                    return
            self._rows = []
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, fixture: dict):
        self._fixture = fixture

    def cursor(self):
        return _Cursor(self._fixture)


def _python_side(case: dict) -> dict:
    """跑 Python 这一侧，返回 ``{"hedge", "display", "raw"}``。

    ``hedge`` 是**页面真正会显示**的口径（走 ``_hedge_of``，异常兜 1.0）；
    ``raw`` 记录未兜底时发生了什么，用来解释 ``case.5`` / ``case.6``。
    """
    fixture = {
        "ids": case["ids"],
        "positions": {str(k): v for k, v in case["positions"].items()},
        "calibration": case["calibration"],
    }
    source = MySqlFundDataSource(_Connection(fixture))
    my_symbol, my_stock_id = case["my"][0], case["my"][1]

    try:
        raw = source.get_stock_hedge(my_symbol, my_stock_id, case["row"])
    except Exception as exc:  # noqa: BLE001 —— 记录就够了，不吞掉信息
        raw = "%s: %s" % (type(exc).__name__, exc)

    hedge = _hedge_of(source, my_symbol, my_stock_id, case["row"])
    cell = _number_cell(hedge, 0)
    return {
        "hedge": hedge,
        "display": cell if isinstance(cell, str) else cell[0],
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# 跑 PHP
# ---------------------------------------------------------------------------
def _run_php() -> dict:
    script = os.path.join(AUTO_DIR, "_php_fundlisthedge_check.php")
    proc = subprocess.run(
        ["php", "-d", "error_reporting=0", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit("PHP 侧失败：%s" % (proc.stderr or proc.stdout))
    # PHP 启动时会往 **stdout** 打一行 openssl 模块警告，JSON 从第一个 '{' 开始
    start = proc.stdout.find("{")
    if start < 0:
        raise SystemExit("PHP 侧没给出 JSON：%s" % proc.stdout[:400])
    return json.loads(proc.stdout[start:])


#: 与 PHP 脚本里的 ``$cases`` 一一对应（顺序、数值必须一致）。
CASES = [
    {
        "name": "两边最新日期都对得上",
        "ids": {"SZ161125": "2", "SPY": "5"},
        "positions": {"2": 0.95, "5": 1.0},
        "calibration": {
            "2": [("2026-10-09", "15810.585263"), ("2026-10-01", "22.110973")],
            "5": [("2026-10-09", "10.035")],
        },
        "my": ("SZ161125", "2"),
        "row": "SPY",
    },
    {
        "name": "只有旧日期对得上：扫描要往后走",
        "ids": {"SZ161125": "2", "SPY": "5"},
        "positions": {"2": 0.95, "5": 1.0},
        "calibration": {
            "2": [("2026-10-09", "15810.585263"), ("2026-10-01", "22.110973")],
            "5": [("2026-10-01", "10.035")],
        },
        "my": ("SZ161125", "2"),
        "row": "SPY",
    },
    {
        "name": "杠杆标的没有校准值 -> 1.0",
        "ids": {"SZ161125": "2", "SPY": "5"},
        "positions": {"2": 0.95, "5": 1.0},
        "calibration": {
            "2": [("2026-10-09", "15810.585263")],
            "5": [],
        },
        "my": ("SZ161125", "2"),
        "row": "SPY",
    },
    {
        "name": "本基金没有校准值 -> 1.0",
        "ids": {"SZ161125": "2", "SPY": "5"},
        "positions": {"2": 0.95, "5": 1.0},
        "calibration": {
            "2": [],
            "5": [("2026-10-09", "10.035")],
        },
        "my": ("SZ161125", "2"),
        "row": "SPY",
    },
    {
        "name": "做空标的：仓位为负，对冲值跟着变负号",
        "ids": {"SZ161125": "2", "SH": "8"},
        "positions": {"2": 0.95, "8": -1.0},
        "calibration": {
            "2": [("2026-10-09", "15810.585263")],
            "8": [("2026-10-09", "245.591")],
        },
        "my": ("SZ161125", "2"),
        "row": "SH",
    },
    {
        "name": "陷阱：杠杆标的的校准值是 0.0 —— PHP 8 直接抛错",
        "ids": {"SZ161125": "2", "SPY": "5"},
        "positions": {"2": 0.95, "5": 1.0},
        "calibration": {
            "2": [("2026-10-09", "15810.585263"), ("2026-10-01", "22.110973")],
            "5": [("2026-10-09", "0.0"), ("2026-10-01", "10.035")],
        },
        "my": ("SZ161125", "2"),
        "row": "SPY",
    },
    {
        "name": "陷阱：杠杆标的的校准值是字符串 0 —— PHP 判假跳过",
        "ids": {"SZ161125": "2", "SPY": "5"},
        "positions": {"2": 0.95, "5": 1.0},
        "calibration": {
            "2": [("2026-10-09", "15810.585263"), ("2026-10-01", "22.110973")],
            "5": [("2026-10-09", "0"), ("2026-10-01", "10.035")],
        },
        "my": ("SZ161125", "2"),
        "row": "SPY",
    },
]


def _compare() -> int:
    php = _run_php()
    print("=== 逐例对照：_callbackFundListHedge / GetStockHedge ===")
    print("")
    differing = 0
    uncompared = 0

    for index, case in enumerate(CASES):
        key = "case.%d" % index
        php_case = php["cases"].get(key)
        if php_case is None:
            print("  %s  PHP 侧缺失（两边用例数不一致？）" % key)
            uncompared += 1
            continue
        python = _python_side(case)
        php_value = php_case["hedge"]
        python_value = python["hedge"]

        if key in KNOWN_DIVERGENCE:
            print("  %s  %s" % (key, case["name"]))
            print("       PHP    = %r" % (php_value,))
            print("       Python = %r  （raw：%s）" % (python_value, python["raw"]))
            print("       已知差异：%s" % KNOWN_DIVERGENCE[key])
            continue

        same = (
            isinstance(php_value, (int, float))
            and isinstance(python_value, (int, float))
            and abs(float(php_value) - float(python_value)) <= TOLERANCE
        )
        display_same = str(php_case["display"]) == str(python["display"])
        mark = "✓" if (same and display_same) else "✗"
        if not (same and display_same):
            differing += 1
        print("  %s %s  %s" % (mark, key, case["name"]))
        print("       PHP    %r  显示 %s" % (php_value, php_case["display"]))
        print("       Python %r  显示 %s" % (python_value, python["display"]))

    print("")
    print("陷阱（PHP 语义）：")
    for name, value in sorted(php.get("traps", {}).items()):
        print("   %-24s %s" % (name, value))
    print("")
    print("不一致 %d 项，未比对 %d 项，已知差异 %d 项"
          % (differing, uncompared, len(KNOWN_DIVERGENCE)))
    return 1 if (differing or uncompared) else 0


# ---------------------------------------------------------------------------
# 自检：注入错误，看对照脚本抓不抓得到
# ---------------------------------------------------------------------------
def _wrong_subject(case: dict) -> float:
    """方向搞反：拿行里的杠杆标的当"本基金"。"""
    fixture = {"ids": case["ids"],
               "positions": {str(k): v for k, v in case["positions"].items()},
               "calibration": case["calibration"]}
    source = MySqlFundDataSource(_Connection(fixture))
    return source.get_stock_hedge(case["row"], case["ids"][case["row"]],
                                  case["my"][0])


def _ignores_position(case: dict) -> float:
    """忘了除仓位：直接用校准值之比。"""
    rows = case["calibration"].get(case["my"][1], [])
    lev = case["calibration"].get(case["ids"][case["row"]], [])
    for date, close in rows:
        for lev_date, lev_close in lev:
            if lev_date == date and float(lev_close):
                return float(close) / float(lev_close)
    return 1.0


def _latest_only(case: dict) -> float:
    """只看最新日期，不往下扫（case.1 会被它抓到）。"""
    rows = case["calibration"].get(case["my"][1], [])
    lev = dict(case["calibration"].get(case["ids"][case["row"]], []))
    if not rows:
        return 1.0
    date, close = rows[0]
    if date not in lev:
        return 1.0
    my_pos = float(case["positions"][case["my"][1]])
    lev_pos = float(case["positions"][case["ids"][case["row"]]])
    return (float(close) / my_pos) / (float(lev[date]) / lev_pos)


def _no_fallback(case: dict) -> float:
    """找不到共同日期时返回 None 而不是 1.0。"""
    rows = case["calibration"].get(case["my"][1], [])
    lev = dict(case["calibration"].get(case["ids"][case["row"]], []))
    for date, close in rows:
        if date in lev:
            my_pos = float(case["positions"][case["my"][1]])
            lev_pos = float(case["positions"][case["ids"][case["row"]]])
            return (float(close) / my_pos) / (float(lev[date]) / lev_pos)
    return None


def _selftest() -> int:
    """四种典型错误各注入一次：每注入一种，对照必须出现**新的**失败。"""
    php = _run_php()
    wrongs = [
        ("对冲方向搞反", _wrong_subject),
        ("忘了除仓位", _ignores_position),
        ("只看最新日期不往下扫", _latest_only),
        ("缺数据时返回 None 而不是 1.0", _no_fallback),
    ]
    caught = 0
    print("=== 自检：注入错误，看对照抓不抓得到 ===")
    print("")
    for label, wrong in wrongs:
        hits = 0
        for index, case in enumerate(CASES):
            key = "case.%d" % index
            if key in KNOWN_DIVERGENCE:
                continue
            php_value = php["cases"][key]["hedge"]
            try:
                python_value = wrong(case)
            except Exception as exc:  # noqa: BLE001
                python_value = "EXC:%s" % type(exc).__name__
            same = (
                isinstance(php_value, (int, float))
                and isinstance(python_value, (int, float))
                and abs(float(php_value) - float(python_value)) <= TOLERANCE
            )
            if not same:
                hits += 1
        ok = hits > 0
        caught += 1 if ok else 0
        print("  %s %-32s 触发 %d 例不一致"
              % ("✓" if ok else "✗", label, hits))
    print("")
    print("自检抓到 %d / %d 种注入错误" % (caught, len(wrongs)))
    return 0 if caught == len(wrongs) else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else _compare())
