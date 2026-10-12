"""拿真实 PHP 逐项对照期货理论溢价。

用法::

    D:\\xampp\\php\\php.exe -n -d display_errors=stderr _php_futurepremium_check.php > ref.json
    python -B _php_futurepremium_compare.py --reference ref.json

不传 ``--reference`` 时自己调 PHP 生成（必须用 XAMPP 那个 php）。

## 对照什么

* ``GetFutureInterestPremium()`` —— 年化利率 × 剩余天数 / 365 + 1。
  坑在"天数"：PHP 的 ``GetTick()`` 是 ``mktime(0,0,0,...)``，
  **跨夏令时切换时天数不是整数**（10-11 → 12-18 是 68.0417 天）。
  拿两个 ``date`` 直接相减会得到整 68，与 PHP 差 0.04 天。
* ``_callbackFutureSma()`` 的两步舍入 —— PHP 的 ``round()`` 是
  **四舍五入**，Python 内建是**银行家舍入**，``0.125`` 上就分叉。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
# 脚本挪到 python/auto/_scratch/ 之后，sys.path[0] 只剩 _scratch，
# 所以要把 python/auto 与 python 两级目录显式补进来，才 import 得到 ref/、sql/ 这些子包。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


PHP_EXE = "D:/xampp/php/php.exe"

# 与 _php_futurepremium_check.php 的 $cases **必须逐项一致**
# (今天, 到期日, 利率百分点)
CASES = (
    ("2026-10-11", "2026-12-18", 5.00625),
    ("2026-10-11", "2025-03-21", 5.00625),   # 到期日已过 → 溢价 < 1
    ("2026-10-11", "2026-10-11", 5.00625),   # 同一天 → 恰好 1.0
    ("2026-01-01", "2026-12-31", 5.00625),   # 跨年
    ("2026-10-11", "2027-03-19", 4.5),
    ("2026-10-11", "2026-11-20", 0.0),       # 利率 0
    ("2024-02-28", "2024-03-01", 5.00625),   # 闰年
)

# (价格, 溢价) —— 验证两步舍入
ROUND_CASES = (
    (31137.575, 1.009326712328767),
    (31137.575, 1.01),
    (7866.45, 1.02),
    (100.0, 1.0),
    (2.5, 1.0),
    (0.125, 1.0),
)


def run_php() -> dict:
    process = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", "_php_futurepremium_check.php"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        # 脚本名是相对的：**换目录跑就找不到**。按本文件所在目录固定下来。
        cwd=str(Path(__file__).resolve().parent),
    )
    if process.returncode != 0:
        sys.stderr.write(process.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP harness 失败，返回码 %d" % process.returncode)
    return json.loads(process.stdout.decode("utf-8"))


def compare(reference: dict) -> tuple:
    """比对，返回 ``(差异说明, 条数)``。"""
    import datetime

    from calc.futurepremium import future_sma_value, get_future_interest_premium

    problems = []

    for case in reference["premium"]:
        index = case["index"]
        today, end_date, rate = CASES[index]
        moment = datetime.datetime.strptime(today, "%Y-%m-%d")
        python_value = get_future_interest_premium(rate / 100.0, end_date, now=moment)
        if not _close(python_value, case["premium"]):
            problems.append("第%d例 溢价 php=%s python=%r"
                            % (index, case["premium"], python_value))

    for case in reference["rounded"]:
        index = case["index"]
        price, premium = ROUND_CASES[index]
        python_value = future_sma_value(None, premium, price)
        if python_value is None:
            problems.append("第%d例 回调 php=%s python=None" % (index, case["value"]))
        elif not _close(python_value, case["value"]):
            problems.append("第%d例 回调 php=%s python=%r"
                            % (index, case["value"], python_value))
    return problems, len(problems)


def selftest(reference: dict) -> int:
    """注入错误，验证对照脚本本身有效。"""
    from calc import futurepremium

    original = {
        "php_round": futurepremium.php_round,
        "DAYS_IN_YEAR": futurepremium.DAYS_IN_YEAR,
        "FUTURE_TICK_SIZE": futurepremium.FUTURE_TICK_SIZE,
    }

    def run():
        return compare(reference)[1]

    if run():
        print("基线就有差异，先修实现再谈自检")
        return 1

    caught = 0
    # ① 用 Python 内建的 round（银行家舍入）顶替 PHP 的 round
    futurepremium.php_round = lambda value, digits=0: round(value, digits)
    if run() > 0:
        caught += 1
    futurepremium.php_round = original["php_round"]

    # ② 一年按 365.25 天
    futurepremium.DAYS_IN_YEAR = 365.25
    if run() > 0:
        caught += 1
    futurepremium.DAYS_IN_YEAR = original["DAYS_IN_YEAR"]

    # ③ 丢掉"取 0.25 整数倍"那一步
    futurepremium.FUTURE_TICK_SIZE = 1.0
    if run() > 0:
        caught += 1
    futurepremium.FUTURE_TICK_SIZE = original["FUTURE_TICK_SIZE"]

    print("=== 自检 ===")
    print("  注入 3 类错误，抓到 %d 类" % caught)
    return 0 if caught == 3 else 1


def _close(python_value, php_value: str) -> bool:
    try:
        return abs(float(python_value) - float(php_value)) < 1e-9
    except (TypeError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.reference:
        with open(args.reference, "r", encoding="utf-8") as handle:
            reference = json.load(handle)
    else:
        reference = run_php()

    if args.selftest:
        return selftest(reference)

    problems, count = compare(reference)
    for problem in problems:
        print("  ✗ " + problem)
    total = len(reference["premium"]) + len(reference["rounded"])
    print("对照例数：%d" % total)
    print("意外差异：%d" % count)
    return 1 if count else 0


if __name__ == "__main__":
    sys.exit(main())
