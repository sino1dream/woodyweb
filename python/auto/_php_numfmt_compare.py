"""数字/时间格式化与 PHP 的逐项对照。

这是第一步对照工具（``_php_port_compare.py``）的姊妹工具，专门校验
``php/debug.php`` 与 ``php/stock.php`` 里的格式化函数，因为 PHP 的
``strval(float)`` 用的是 14 位有效数字，和 Python ``repr()`` 的 17 位不同，
必须逐值验证而不能靠肉眼。

    cd python\\auto
    python -B _php_numfmt_compare.py     # 需要 php 在 PATH 中
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import stockref as sr

HERE = Path(__file__).resolve().parent
PHP_HARNESS = HERE / "_php_numfmt_check.php"

# 与 PHP 侧脚本保持完全一致的关键字构造，顺序无关
VALUES = [
    "0", "0.000", "-0", "3.0", "1.5", "1.50", "0.1", "-2.25", "100",
    "1234.5678", "0.001", "0.0001", "0.00001", "0.000001", "0.0000001", "0.00000001",
    "1e-5", "1e-6", "1e-7", "1e-8", "1.5e-7", "2.5e-7",
    "1e5", "1e10", "1e14", "1e15", "1e16", "1e20", "1e21", "1e-20",
    "123456789012345", "1234567890123456", "12345678901234567",
    "123456789012345678", "1234567890123456789",
    "9007199254740993", "0.2", "0.3", "0.30000000000000004",
    "3.14159265358979", "3.141592653589793", "1.7976931348623157e308",
    "5e-324", "2.2250738585072014e-308", "1.0000000000000002",
    "99999999999999.9", "999999999999999.9", "0.1234567890123456",
    "2147483647", "4294967296", "-1e-7", "-1e20", "", "abc", " 42 ",
    "1,234", "0x1A", "1e", ".5", "5.", "+7", "-.5",
]
TIMES = ["06:56:22", "08:55:00", "00:00:00", "12:00", "", "9:5:3", "23:59:59"]
PERCENTS = ["0", "0.5", "1.00", "-1.50", "12.34", "-0.05", "100.00", "2.10", "-0.00"]
ZERO_CANDIDATES = ["0", "0.000", "0.0000004", "0.0000005", "0.0000006", "-0.0000004", "1", "", "abc"]


def load_reference():
    """让 PHP 把参考结果打到 stdout；PHP 进程可能无权写入工作区。"""
    completed = subprocess.run(
        ["php", "-n", "-d", "display_errors=stderr", str(PHP_HARNESS)],
        cwd=str(HERE),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.stderr:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
    reference = json.loads(completed.stdout.decode("utf-8"))
    # floatval(...) 是 PHP 侧的中间量，只用于人工核对，不参与逐项比较
    return {k: v for k, v in reference.items() if not k.startswith("floatval(")}


def build_python_cases():
    cases = {}
    for value in VALUES:
        cases["rtrim0(%s)" % value] = sr.rtrim0(value)
    for time_text in TIMES:
        cases["GetHM(%s)" % time_text] = sr.get_hm(time_text)
    for percent in PERCENTS:
        cases["percent_display(%s)" % percent] = _percent_display(percent)
    for candidate in ZERO_CANDIDATES:
        cases["IsZeroString(%s)" % candidate] = sr.is_zero_string(candidate)
    return cases


def _percent_display(percent: str) -> str:
    """复刻 PHP 侧脚本里 sprintf + rtrim 那段。

    两个要点：
    * PHP ``sprintf('%.2f', -0.0)`` 会规范化成 ``'0.00'``，Python 会得到
      ``'-0.00'``，所以先按数值把负零归一。
    * 比较顺序上 PHP 先判断 ``$str != '' && $str != '0'``（此时 ``$str`` 还是
      格式化结果，如 ``'0.00'``），通过之后才 rtrim，'0.00' 因此直接返回 '0'。
    """
    value = float(percent)
    if value == 0.0:
        value = 0.0
    formatted = "%.2f" % value
    trimmed = formatted.rstrip("0").rstrip(".")
    if formatted != "" and float(formatted) != 0.0:
        return trimmed + "%"
    return trimmed


def main():
    reference = load_reference()
    python_cases = build_python_cases()

    only_php = sorted(set(reference) - set(python_cases))
    only_python = sorted(set(python_cases) - set(reference))

    mismatches = []
    for key in sorted(set(reference) & set(python_cases)):
        if reference[key] != python_cases[key]:
            mismatches.append((key, reference[key], python_cases[key]))

    print("compared cases : %d" % len(set(reference) & set(python_cases)))
    print("php only       : %d" % len(only_php))
    print("python only    : %d" % len(only_python))
    print("mismatches     : %d" % len(mismatches))

    for key in only_php[:20]:
        print("  PHP-ONLY  %s = %r" % (key, reference[key]))
    for key in only_python[:20]:
        print("  PY-ONLY   %s = %r" % (key, python_cases[key]))
    for key, php_value, py_value in mismatches[:60]:
        print("  MISMATCH  %-42s php=%r python=%r" % (key, php_value, py_value))

    return 1 if (mismatches or only_php or only_python) else 0


if __name__ == "__main__":
    sys.exit(main())
