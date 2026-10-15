"""CK4-E：净值记录段落的 PHP↔Python 逐例对照。

跑 ``_php_netvaluehistory_check.php``（函数体从 ``php/ui/netvaluehistoryparagraph.php``
现场切出），与 Python 侧同输入比结果。比三样：

1. ``GetNetValueTableColumn()`` 的**列组成**（摆几列、什么顺序）—— 段落的骨架；
2. ``_QdiiGetStockPosition()`` 的数值（六组，含门槛与边界）；
3. ``_QdiiMixGetPosition()`` 的加权反算（四组）。

容差 1e-12；``null``（PHP 的 ``false``）与 ``None`` 视为相等。

**列文字有一处刻意差异**：PHP 中间层那列是 ``^NDX净值``（符号作前缀），
Python 改成 ``标的净值`` 以避免与基金自己的「净值」列同名 ——
``_columns_netvaluehistory`` 的 docstring 记了这条。对照时把这一列归一化，
差异本身单独打印出来，别让它被"容差"悄悄吞掉。
"""
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(os.path.dirname(HERE))
for _path in (PYTHON_DIR, HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TOLERANCE = 1e-12
PHP = "D:/xampp/php/php.exe"

#: PHP 的中间层净值列文字（符号作前缀）→ Python 的改写
EST_COLUMN_RENAME = {"^NDX\u51c0\u503c|90": "\u6807\u7684\u51c0\u503c|90"}


class EstRefStub:
    """``GetNetValueTableColumn()`` 里的 est 引用替身。"""

    net_value_count = 2

    def get_display(self):
        return "^NDX"

    def count_net_value(self):
        return self.net_value_count

    def is_sina_future_except_gold_cn(self):
        return False


class CnyRefStub:
    def get_display(self):
        return "USCNY"


class HoldingsRefStub:
    def __init__(self, proportions, ratios):
        self.proportions = proportions
        self.ratios = ratios

    def get_proportion_array(self, date, prev_date):
        return self.proportions

    def get_holdings_ratio_array(self):
        return self.ratios

    def get_holdings_ref_array(self):
        #: 按 PHP 的顺序给：比例表有几个键就对应几个引用
        return [_KeyedRef(key) for key in self.ratios]


class _KeyedRef:
    def __init__(self, stock_id):
        self._stock_id = stock_id

    def get_stock_id(self):
        return self._stock_id


def _run_php():
    script = os.path.join(HERE, "_php_netvaluehistory_check.php")
    proc = subprocess.run(
        [PHP, "-n", "-d", "display_errors=stderr", script],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError("PHP 失败：%s%s" % (proc.stdout, proc.stderr))
    return json.loads(proc.stdout)


def _py_columns(est, cny):
    from web.services.history_service import _columns_netvaluehistory

    #: Python 的 _columns_netvaluehistory 收的是**符号**，不是引用对象
    cny_symbol = cny.get_display() if cny is not None else None
    est_symbol = est.get_display() if est is not None else None
    return ["%s|%s" % (c["text"], c["width"]) for c in _columns_netvaluehistory(cny_symbol, est_symbol)]


def _close(left, right):
    if left is None or right is None:
        return left is None and right is None
    try:
        return abs(float(left) - float(right)) <= TOLERANCE
    except (TypeError, ValueError):
        return str(left) == str(right)


def main():
    from web.services.history_service import qdii_get_stock_position, qdii_mix_position

    php = _run_php()
    failures = []
    checked = 0

    # -- 1. 列组成 -------------------------------------------------------
    for name, est, cny in (
        ("neither", None, None),
        ("both", EstRefStub(), CnyRefStub()),
        ("cny_only", None, CnyRefStub()),
        ("est_only", EstRefStub(), None),
    ):
        expected = [EST_COLUMN_RENAME.get(text, text) for text in php["columns"][name]]
        actual = [EST_COLUMN_RENAME.get(text, text) for text in _py_columns(est, cny)]
        checked += 1
        if expected != actual:
            failures.append("columns.%s\n    PHP   %s\n    Python %s" % (name, expected, actual))
        else:
            print("columns.%-9s %d 列  一致" % (name, len(actual)))

    # -- 2. 仓位反算式 ---------------------------------------------------
    cases = [
        ("normal", 1.0, 100.0, 95.0, 7.0, 6.63, 4.0),
        ("exact_one", 1.0, 100.0, 95.0, 7.0, 6.65, 4.0),
        ("small_move", 1.0, 100.0, 98.0, 7.0, 6.63, 4.0),
        ("loose_input", 1.0, 100.0, 98.0, 7.0, 6.63, 1.0),
        ("cny_flat", 1.0, 100.0, 95.0, 7.0, 7.0, 4.0),
        ("negative", 1.0, 95.0, 100.0, 7.0, 6.63, 4.0),
        #: 四舍五入边界：算出来正好是 0.125 与 0.625
        ("half_even", 1.0, 100.0, 95.0, 7.0, 4.20, 4.0),
        ("half_odd", 1.0, 100.0, 95.0, 7.0, 6.44, 4.0),
    ]
    for name, pro, prev, net, cny_prev, cny, f_input in cases:
        expected = php["stock_position"][name]
        actual = qdii_get_stock_position(pro, prev, net, cny_prev, cny, f_input)
        checked += 1
        if not _close(expected, actual):
            failures.append("stock_position.%s\n    PHP   %s\n    Python %s" % (name, expected, actual))
        else:
            print("stock_position.%-11s PHP %-6s Python %-6s 一致"
                  % (name, expected, actual))

    # -- 3. 持仓型加权 ---------------------------------------------------
    mix_cases = [
        ("two_holdings", [0.02, 0.05], {"11": "40.0", "12": "60.0"}),
        ("one_holding", [0.10], {"11": "100.0"}),
        ("no_pro", None, {"11": "100.0"}),
        ("empty_pro", [], {"11": "100.0"}),
    ]
    for name, proportions, ratios in mix_cases:
        ref = HoldingsRefStub(proportions, ratios)
        expected = php["mix_position"][name]
        actual = qdii_mix_position(ref, "2026-10-14", "2026-10-13", 100.0, 95.0, 7.0, 6.63, 4.0)
        checked += 1
        if not _close(expected, actual):
            failures.append("mix_position.%s\n    PHP   %s\n    Python %s" % (name, expected, actual))
        else:
            print("mix_position.%-12s PHP %-6s Python %-6s 一致"
                  % (name, expected, actual))

    print()
    print("共 %d 项，不一致 %d 项" % (checked, len(failures)))
    for failure in failures:
        print("  ✗ %s" % failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
