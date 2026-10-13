"""portfolioparagraph 迁移的 PHP 逐例对照。

覆盖面与 ``_php_portfolio_check.php`` 一一对应：

* ``php/ui/portfolioparagraph.php`` —— ``_getPortfolioTestVal`` /
  ``_getArbitrageTestStr`` / ``fx_susdcnh`` 行的两个展示串；
* ``php/ui/stockcomm.php`` —— ``GetArbitrageQuantity``；
* ``woody/res/php/_myportfolio.php`` —— 行序（``_echoPortfolio`` 的分块 +
  ``_transSortBySymbol`` 的分组 ksort）；
* ``php/stocktrans.php`` —— ``StockTransaction`` / ``MyStockTransaction`` /
  ``AddSqlTransaction`` / ``GetSqlTransactionDate``。

PHP 侧用**抽函数体 + 命名空间打桩**的方式产出参考值（不连库），
细节见 harness 头注释。本脚本负责在 Python 侧构造同样的输入、
逐项比对，刻意偏差列在 :data:`KNOWN_DIVERGENCES`，与真 mismatch 分开报。

用法::

    cd python\\auto
    python -B _php_portfolio_compare.py            # 自己跑 PHP harness
    python -B _php_portfolio_compare.py ref.json   # 复用存档的参考值

需要 php 在 PATH 中。

## 这个脚本抓到的真 bug

``_getArbitrageTestStr()`` 的耗尽路径：PHP 的
``while ($record = mysqli_fetch_assoc(...))`` 在**自然耗尽时把 null 写回
$record**，``if ($record)`` 为假 → 返回空串。初版 Python 移植误写成
"取最后一行"，本对照的第 5/6/7/10 例当场抓出（见 pitfalls 第 47 条）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# 脚本在 python/auto/_scratch/ 下，要把 python/auto 与 python 两级目录显式
# 补进来，才 import 得到 core/、sql/、web.services 这些包。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.stocktrans import (  # noqa: E402
    MyStockTransaction,
    StockTransaction,
    add_sql_transaction,
    get_sql_transaction_date,
)
from web.services.portfolio_service import (  # noqa: E402
    PortfolioCnyTotal,
    build_portfolio_row,
    get_arbitrage_quantity,
    get_arbitrage_test_str,
    get_portfolio_test_val,
    sort_trans_by_symbol,
)

HERE = Path(__file__).resolve().parent
PHP_HARNESS = HERE / "_php_portfolio_check.php"

# ---------------------------------------------------------------------------
# 用例（与 _php_portfolio_check.php 逐项一致）
# ---------------------------------------------------------------------------

# ① _getPortfolioTestVal(iShares, strSymbol)
TEST_VAL_CASES = [
    ("KWEB", 500), ("KWEB", 200), ("KWEB", 0), ("KWEB", -100),
    ("KWEB", 199), ("KWEB", 201),
    ("SH600104", 12000), ("SH600104", 13000), ("SH600104", 0),
    ("SH600104", -12000),
    ("TLT", 500), ("TLT", 700), ("TLT", 0),
    ("XOP", 400), ("ASHR", 601), ("SPY", 100), ("SH510300", 30000),
    ("SZ162411", 91200), ("XBI", 0),
    ("SH513100", 1000), ("fx_susdcnh", 100000), ("hf_ES", 25),
    ("", 1234),
]

# ② _getArbitrageTestStr(iShares, groupId, stockId, symbol='KWEB')
#    (items, self, shares, hedges, symbols)
ARB_STR_CASES = [
    ([], "502", 300, {}, {}),
    ([("501", "600"), ("502", "300"), ("503", "200")], "503", 300, {}, {}),
    ([("501", "600"), ("502", "300"), ("503", "200")], "501", 300, {}, {}),
    ([("501", "600"), ("502", "300"), ("503", "200")], "503", 300, {"501": 2.0}, {}),
    ([("501", "0"), ("503", "-5")], "502", 300, {}, {}),
    ([("501", "0"), ("502", "0")], "502", 300, {}, {}),
    ([("502", "300")], "502", 300, {}, {}),
    ([("501", " 600 "), ("502", "0300")], "503", 300, {}, {}),
    ([("501", "600"), ("502", "300")], "503", 300, {"501": 3.0}, {"501": "SZ161125"}),
    ([("501", "600")], "501", 100, {}, {}),
    ([("501", "600"), ("502", "300")], "503", 300, {"501": 0.5}, {}),
]

# ③ GetArbitrageQuantity(symbol, stockId, fQuantity)：(stock_id, quantity, hedge)
ARB_QTY_CASES = [
    ("501", 1000, 1.0),
    ("501", 1234567, 1.0),
    ("501", 999.5, 1.0),
    ("501", 0.4, 1.0),
    ("501", 2.5, 1.0),
    ("501", -2.5, 1.0),
    ("501", -1234.5, 1.0),
    ("501", 0, 1.0),
    ("501", 1000, 2.0),
    ("501", 1000, 0.5),
    ("501", 1000, 0.001),
    ("501", 1234567.89, 3.7),
    ("501", "1234567", 1.0),
]

# ④ fx_susdcnh 行的两个展示串。
#    PHP 侧直接喂 (fVal, fCny, val)；Python 侧走 build_portfolio_row，
#    fVal 由 (shares, price) 的乘积复现 —— 选的都是能精确表示的乘积。
FX_CASES = [
    # (fVal, fCny, val)              (shares, price)   ← 乘积须精确等于 fVal
    ((710000.0, 0.0, 7.1),           (100000, "7.1")),
    ((710000.0, 1140.0, 7.1),        (100000, "7.1")),
    ((710000.0, 500.0, 7.1),         (100000, "7.1")),
    ((100000.0, 0.0, 7.12),          (100000, "1.0")),
    ((0.0, 1740.0, 7.1),             (100, "0")),
    ((-5000.0, 0.0, 7.1),            (-1000, "5.0")),
    ((12345678.0, 0.0, 6.5),         (12345678, "1.0")),
    ((999.5, 0.5, 7.1),              (1999, "0.5")),
    ((1234567.5, 1234.5, 6.5),       (2469135, "0.5")),
    ((0.5, 0.0, 7.1),                (1, "0.5")),
    ((-0.5, 0.0, 7.1),               (-1, "0.5")),
]

# ⑤ 行序：每个用例是一串 (symbol, is_a, is_h)
SORT_CASES = [
    [("KWEB", False, False), ("SZ161125", True, False),
     ("HK00700", False, True), ("SH513100", True, False)],
    [("fx_susdcnh", False, False), ("KWEB", False, False),
     ("SPY", False, False), ("TLT", False, False)],
    [("hf_ES", False, False), ("fx_susdcnh", False, False)],
    [("AAPL", False, False), ("SH600104", True, False)],
    [("KWEB", False, False), ("KWEB", False, False), ("SPY", False, False)],
    [("SH600104", True, False), ("SZ161125", True, False),
     ("HK00700", False, True), ("hf_ES", False, False),
     ("fx_susdcnh", False, False)],
    [],
]

# ⑥ 持仓模型：(price, [(shares, cost), ...])
TRANS_CASES = [
    ("1.2", [(100, 123.4), (200, 150.0)]),
    ("1.2", [(-400, -1000.0), (100, 250.5)]),
    ("33.80", [(300, 24000.0)]),
    ("", [(100, 120.0)]),
    ("0", [(100, 120.0)]),
    ("7.1", []),
    ("1.5", [(0, 0.0)]),
    ("1.5", [(100, 120.0), (0, 0.0), (-100, -120.0)]),
]

# ⑦ AddSqlTransaction：[[{quantity, price, fees}, ...], ...]
SQL_TRANS_CASES = [
    [{"quantity": "100", "price": "1.234", "fees": "5"}],
    [{"quantity": "100", "price": "1.234", "fees": "5"},
     {"quantity": "200", "price": "1.5", "fees": ""}],
    [{"quantity": "200.7", "price": "1.5", "fees": ""}],
    [{"quantity": "-400", "price": "2.5", "fees": "3.5"}],
    [{"quantity": "100", "price": "", "fees": ""}],
    [{"quantity": "0", "price": "1.0", "fees": "0"}],
    [{"quantity": " 100 ", "price": " 1.234 ", "fees": " 5 "}],
]

# ⑧ GetSqlTransactionDate
SQL_DATE_CASES = [
    "2026-10-13 09:30:00",
    "2026-10-13",
    "2026-10-13  ",
    " 2026-10-13 09:30:00",
    "",
    " ",
]

# 刻意偏差：(key, 原因)。PHP false 已由 flatten_php 归一成 None
# （AGENTS.md：返回 false 的字符串类方法映射为 None），端口同 Rule，故当前为空。
KNOWN_DIVERGENCES = {}


# ---------------------------------------------------------------------------
# Python 侧替身
# ---------------------------------------------------------------------------


class _Ref:
    """持仓模型 / 排序 / fx 行共用的最小引用对象替身。"""

    def __init__(self, symbol, price, is_a=False, is_h=False, val=None):
        self._symbol = symbol
        self._price = price
        self._is_a = is_a
        self._is_h = is_h
        self._val = price if val is None else val

    def get_symbol(self):
        return self._symbol

    def get_stock_id(self):
        return self._symbol

    def get_price(self):
        return self._price

    def get_val(self):
        return self._val

    def get_position(self):
        return 1.0

    def get_price_display(self, disp=None, prev=None, precision=None):
        # 均价显示不在对照范围内（那是引用对象的职责），给个占位串
        return "avg(%s)" % disp

    def is_symbol_a(self):
        return self._is_a

    def is_symbol_h(self):
        return self._is_h


class _Trans:
    """排序用的假交易：带到达序号，用来钉组内顺序。"""

    def __init__(self, index, ref):
        self.index = index
        self.ref = ref

    def get_ref(self):
        return self.ref

    def get_symbol(self):
        return self.ref.get_symbol()


# ---------------------------------------------------------------------------
# Python 侧构造同样的用例
# ---------------------------------------------------------------------------


def build_python_cases():
    cases = {}

    for symbol, shares in TEST_VAL_CASES:
        cases["test_val|%s|%s" % (symbol, shares)] = get_portfolio_test_val(shares, symbol)

    for i, (items, self_id, shares, hedges, symbols) in enumerate(ARB_STR_CASES):
        cases["arb_str|%d" % i] = get_arbitrage_test_str(
            shares, "g1", self_id, "KWEB",
            items=[{"stock_id": sid, "quantity": qty} for sid, qty in items],
            symbol_of=lambda sid: symbols.get(sid, sid),
            hedge_of=lambda sym, sid: hedges.get(sid, 1.0))

    for i, (stock_id, quantity, hedge) in enumerate(ARB_QTY_CASES):
        cases["arb_qty|%d" % i] = get_arbitrage_quantity(
            "SZ161125", stock_id, quantity,
            hedge_of=lambda sym, sid: hedge)

    for i, ((f_val, f_cny, val), (shares, price)) in enumerate(FX_CASES):
        trans = MyStockTransaction(_Ref("fx_susdcnh", price, val=val), "g1")
        trans.add_transaction(shares, 0.0)
        row = build_portfolio_row(trans, cny=PortfolioCnyTotal(f_cny))
        match = re.search(r"overnightcnh=(-?\d+)", row.hrefs[6])
        cases["fx_row|%d" % i] = {
            "text": row.cells[6],
            "amount": match.group(1) if match else None,
        }

    for i, specs in enumerate(SORT_CASES):
        trans_list = [_Trans(j, _Ref(sym, "1.0", is_a=a, is_h=h))
                      for j, (sym, a, h) in enumerate(specs)]
        ordered = sort_trans_by_symbol(trans_list)
        cases["sort|%d" % i] = [[t.index, t.get_symbol()] for t in ordered]

    for i, (price, ops) in enumerate(TRANS_CASES):
        trans = MyStockTransaction(_Ref("SZ161125", price, is_a=True), False)
        for shares, cost in ops:
            trans.add_transaction(shares, cost)
        cases["trans|%d" % i] = {
            "shares": trans.get_total_shares(),
            "cost": trans.get_total_cost(),
            "records": trans.get_total_records(),
            "avg": trans.get_avg_cost(),
            "value": trans.get_value(),
            "profit": trans.get_profit(),
        }

    for i, records in enumerate(SQL_TRANS_CASES):
        trans = StockTransaction()
        for record in records:
            add_sql_transaction(trans, record)
        cases["sql_trans|%d" % i] = {
            "shares": trans.get_total_shares(),
            "cost": trans.get_total_cost(),
            "records_count": trans.get_total_records(),
        }

    for filled in SQL_DATE_CASES:
        cases["sql_date|%s" % filled] = get_sql_transaction_date({"filled": filled})

    return cases


# ---------------------------------------------------------------------------
# PHP 参考值
# ---------------------------------------------------------------------------


def load_reference(reference_path):
    """返回 PHP 参考值；缺存档时自己跑 harness（stdout 只承载 JSON）。"""
    if reference_path.exists():
        return json.loads(reference_path.read_text(encoding="utf-8"))

    print("reference missing, running the PHP harness...")
    completed = subprocess.run(
        # -n 跳过 php.ini：否则启动警告会落到 stdout 污染 JSON 载荷
        ["php", "-n", "-d", "display_errors=stderr", str(PHP_HARNESS), "-"],
        cwd=str(HERE),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.stderr:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
    payload = completed.stdout.decode("utf-8")
    start = payload.find("{")
    if start < 0:
        raise SystemExit("PHP harness produced no JSON on stdout")
    return json.loads(payload[start:])


def flatten_php(reference):
    """把 harness 的分段 JSON 摊成与 build_python_cases() 同构的平表。"""
    flat = {}

    for case in reference["test_val"]:
        flat["test_val|%s|%s" % (case["symbol"], case["shares"])] = case["value"]

    for i, case in enumerate(reference["arb_str"]):
        flat["arb_str|%d" % i] = case["value"]

    for i, case in enumerate(reference["arb_qty"]):
        flat["arb_qty|%d" % i] = case["value"]

    for i, case in enumerate(reference["fx_row"]):
        flat["fx_row|%d" % i] = {"text": case["text"], "amount": case["amount"]}

    for i, order in enumerate(reference["sort"]):
        flat["sort|%d" % i] = [[item["id"], item["symbol"]] for item in order]

    for i, case in enumerate(reference["trans"]):
        flat["trans|%d" % i] = {
            "shares": case["shares"],
            "cost": case["cost"],
            "records": case["records"],
            "avg": case["avg"],
            "value": case["value"],
            "profit": case["profit"],
        }

    for i, case in enumerate(reference["sql_trans"]):
        flat["sql_trans|%d" % i] = {
            "shares": case["shares"],
            "cost": case["cost"],
            "records_count": case["records_count"],
        }

    for case in reference["sql_date"]:
        value = case["value"]
        # PHP false（找不到空格）归一成 None，与 Python 侧 '' → None 对齐
        flat["sql_date|%s" % case["filled"]] = None if value is False else value

    return flat


def main():
    reference_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "_php_portfolio_reference.json"

    reference = load_reference(reference_path)
    php_cases = flatten_php(reference)
    python_cases = build_python_cases()

    only_php = sorted(set(php_cases) - set(python_cases))
    only_python = sorted(set(python_cases) - set(php_cases))

    mismatches = []
    for key in sorted(set(php_cases) & set(python_cases)):
        if php_cases[key] != python_cases[key]:
            mismatches.append((key, php_cases[key], python_cases[key]))

    known = [item for item in mismatches if item[0] in KNOWN_DIVERGENCES]
    unexpected = [item for item in mismatches if item[0] not in KNOWN_DIVERGENCES]

    print("compared cases : %d" % len(set(php_cases) & set(python_cases)))
    print("php only       : %d" % len(only_php))
    print("python only    : %d" % len(only_python))
    print("mismatches     : %d (%d known, %d unexpected)"
          % (len(mismatches), len(known), len(unexpected)))

    for key in only_php[:20]:
        print("  PHP-ONLY  %s = %r" % (key, php_cases[key]))
    for key in only_python[:20]:
        print("  PY-ONLY   %s = %r" % (key, python_cases[key]))
    for key, php_value, py_value in known:
        print("  KNOWN     %-40s php=%r python=%r" % (key, php_value, py_value))
    for key, php_value, py_value in unexpected[:40]:
        print("  MISMATCH  %-40s php=%r python=%r" % (key, php_value, py_value))

    return 1 if (unexpected or only_php or only_python) else 0


if __name__ == "__main__":
    sys.exit(main())
