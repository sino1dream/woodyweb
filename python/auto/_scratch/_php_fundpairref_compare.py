"""CK2b 对照：``FundPairReference.calc_factor`` vs PHP ``CalcFactor()``。

跑法::

    D:/python/python.exe -B _scratch/_php_fundpairref_compare.py

左边是真实 PHP（``_php_fundpairref_check.php``，分支体从源码抠、符号分类用真
``StockSymbol``），右边是 Python 迁移实现。**用例矩阵写在这边**，PHP 那边读
JSON 拿同一份 case，两边不会漂移。
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_AUTO = os.path.dirname(_HERE)
if _AUTO not in sys.path:
    sys.path.insert(0, _AUTO)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHECK_PHP = os.path.join(_HERE, "_php_fundpairref_check.php")
PHP_EXE = "D:/xampp/php/php.exe"

#: 与 PHP harness 里 ``$cases`` 逐项一致
CASES = [
    # -- A 股：对净值除汇率，等价于因子 × 汇率
    ("SZ161725", 7.1, 71.0, 10.0),
    ("SH513500", 0.9, 100.0, 25.0),
    ("SZ161725", 1.0, 80.0, 10.0),
    # -- 非 A：净值与汇率相乘后再除，等价于因子 ÷ 汇率
    ("^NDX", 7.1, 71.0, 10.0),
    ("hf_NQ", 0.9, 100.0, 25.0),
    ("SPY", 1.0, 80.0, 10.0),
    ("00700", 0.92, 500.0, 400.0),
    # -- 没有汇率引用：纯比值，两个分支应当收敛到同一个数
    ("SZ161725", None, 80.0, 10.0),
    ("^NDX", None, 80.0, 10.0),
    # -- 汇率小于 1 时两个分支的大小关系翻转
    ("SZ161725", 0.5, 10.0, 4.0),
    ("^NDX", 0.5, 10.0, 4.0),
    # -- 深 B / 沪 B：PHP 把它们也算 A 类（IsShangHaiB 先过 IsShangHaiA）
    ("SZ200011", 7.1, 35.5, 10.0),
    ("SH900901", 7.1, 35.5, 10.0),
]

DATE = "2026-01-05"


def run_php():
    """跑 PHP harness，返回它给的 case 列表。"""
    out = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", CHECK_PHP],
        capture_output=True, cwd=_HERE,
    )
    stderr = out.stderr.decode("utf-8", "replace").strip()
    if stderr:
        print("[php stderr] " + stderr, file=sys.stderr)
    if out.returncode != 0:
        raise SystemExit("PHP harness 退出码 %d" % out.returncode)
    return json.loads(out.stdout.decode("utf-8", "replace"))["cases"]


def python_side():
    """同一份 case 在 Python 侧跑一遍。"""
    from test_fundpairref import FakeCny, FakeSource, build_ref

    rows = []
    for symbol, cny, pair, net in CASES:
        ref = build_ref(symbol, source=FakeSource())
        ref.cny_ref = None if cny is None else FakeCny(cny)
        factor = ref.calc_factor(pair, net, DATE)
        rows.append({
            "symbol": symbol,
            "is_a": bool(ref.is_symbol_a()),
            "cny": cny,
            "pair": pair,
            "net": net,
            "factor": _fmt(factor),
            #: 原始值留着比较用 —— ``factor`` 是格式化过的文本，拿它比会
            #: 把"同一个数的两种写法"报成差异。
            "raw": factor,
        })
    return rows


def _fmt(value):
    """与 PHP 的 ``strval(float)`` 对齐：PHP 默认 14 位有效数字。"""
    if value is None:
        return "NONE"
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return "NAN" if math.isnan(value) else ("INF" if value > 0 else "-INF")
    return "%.14G" % value


def _same_value(php_text, py_value):
    """**比数值、不比字符串**。

    PHP 用 ``strval()`` 输出、Python 用 ``repr()``，两者对同一个浮点会给出不同
    的文本：``strval(8.0)`` 是 ``"8"`` 而 ``repr(8.0)`` 是 ``"8.0"``；
    精度也不同（PHP 默认 14 位有效数字，Python 是 17 位）。
    逐字符串比会把这 8 条全报成差异，实际数值一致。
    """
    if php_text in ("INF", "-INF", "NAN"):
        return _fmt(py_value) == php_text
    if py_value is None:
        return False
    try:
        return math.isclose(float(php_text), float(py_value), rel_tol=1e-12)
    except (TypeError, ValueError):
        return False


def _same_case_size(php_rows, py_rows):
    """两边用例数不一致就直接停 —— 否则只会对比前面几条，看着像全绿。"""
    if len(php_rows) != len(py_rows):
        raise SystemExit(
            "用例数不一致：PHP %d vs Python %d —— $_cases$ 与 CASES 已漂移"
            % (len(php_rows), len(py_rows)))


def main():
    php_rows = run_php()
    py_rows = python_side()
    _same_case_size(php_rows, py_rows)

    diff = 0
    print("=== FundPairReference.calc_factor vs PHP CalcFactor ===")
    print("%-4s %-10s %-6s %-8s %-8s %-8s  %-18s %-18s %s"
          % ("#", "symbol", "is_a", "cny", "pair", "net", "PHP", "Python", ""))
    for php, py in zip(php_rows, py_rows):
        same_symbol = php["symbol"] == py["symbol"]
        same_a = bool(php["is_a"]) == bool(py["is_a"])
        same_factor = _same_value(php["factor"], py["raw"])
        ok = same_symbol and same_a and same_factor
        if not ok:
            diff += 1
        print("%-4d %-10s %-6s %-8s %-8s %-8s  %-18s %-18s %s"
              % (php["index"], php["symbol"], php["is_a"], php["cny"],
                 php["pair"], php["net"], php["factor"], py["factor"],
                 "OK" if ok else "*** DIFF ***"))

    print("")
    if diff:
        print("结果：%d/%d 例有差异" % (diff, len(php_rows)))
        return 1
    print("结果：%d 例全部一致" % len(php_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
