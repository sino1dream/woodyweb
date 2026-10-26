"""``calc.linearsolve`` 与 PHP 的逐例对照。

跑法（在 ``python/auto/_scratch`` 下）::

    PYTHONPATH=/f/woody/woodyweb/python/auto:/f/woody/woodyweb/python \
      D:/python/python.exe -B _php_linearsolve_compare.py

对每个用例同时比三件事：行列式、克莱姆解、超定解。失败时打印期望/实际，
不做"容差内就算过"的模糊处理 —— 两边算的是同一串浮点运算，正常应当
逐位相同（PHP 的 ``/`` 与 Python 的 ``/`` 都是 IEEE 双精度）。
"""

import json
import subprocess
import sys

from calc.linearsolve import (
    LinearSolveError,
    cramers_rule,
    determinant,
    gaussian_elimination,
    solve_overdetermined,
)

PHP = r"D:\xampp\php\php.exe"
HARNESS = "_php_linearsolve_check.php"


def run_php():
    result = subprocess.run(
        [PHP, "-n", "-d", "display_errors=stderr", HARNESS],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print("PHP harness 失败:", result.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def _either(func, equations):
    try:
        return func(equations)
    except LinearSolveError as error:
        return str(error)


def _same(left, right, tolerance=1e-9):
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right))
    return left == right


def main():
    cases = run_php()
    failures = 0
    for case in cases:
        group = case["group"]
        equations = case["equation"]
        checks = []

        if case["det"] is not None:
            try:
                mine = determinant([row[:-1] for row in equations])
            except LinearSolveError as error:
                mine = str(error)
            checks.append(("det", case["det"], mine))

        checks.append(("cramer", case["cramer"], _either(cramers_rule, equations)))
        checks.append(("gauss", case["gauss"], _either(gaussian_elimination, equations)))
        checks.append(("overdetermined", case["overdetermined"], _either(solve_overdetermined, equations)))

        for name, expected, actual in checks:
            if not _same(expected, actual):
                failures += 1
                print("[FAIL] %s.%s\n  PHP   : %r\n  Python: %r" % (group, name, expected, actual))

    print("共 %d 个用例，%d 处不一致" % (len(cases), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
