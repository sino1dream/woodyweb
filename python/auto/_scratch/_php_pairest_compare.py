"""拿真实 PHP 逐项对照 ``MyPairReference`` 的双向估算。

用法::

    D:\\xampp\\php\\php.exe -n -d display_errors=stderr _php_pairest_check.php > reference.json
    python -B _php_pairest_compare.py --reference reference.json

不传 ``--reference`` 时会自己去调 PHP 生成（需要 XAMPP 那个 php，
PATH 里的 ``C:\\php`` **没有 mysqli 扩展**）。

对照矩阵刻意覆盖四类情形：

* **非 A 股 / A 股** —— 两条式子互为倒数方向；
* **给参数 / 不给参数** —— PHP 的 ``$x == false`` 是宽松比较；
* **有旧校准值 / 没有** —— ``FundAdjustPosition`` 第三个参数的取值；
* **一条贴近真实数据的用例**（纳指期货那一组量级）——
  防止"公式只在 1.0/2.0 这种整数上碰巧对上"。

这类对照的价值在于：**抄错一个方向往往不会崩，结果仍在合理区间**，
只有把 PHP 的真实输出摆在旁边才看得出。
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

# 与 _php_pairest_check.php 的 $cases **必须逐项一致**（顺序也不能变）
CASES = (
    # (是否A股, 配对价, 汇率, 校准值, 仓位, 旧校准值, 自己的价)
    (False, 100.0, 2.0, 4.0, 1.0, False, 12.5),
    (False, 100.0, 2.0, 4.0, 0.5, 10.0, 12.5),
    (False, 100.0, 1.0, 1.2, 0.95, False, 83.3333333333),
    (False, 31137.575, 6.7035, 0.829884, 0.95, 0.83, 4.5),
    (True, 100.0, 2.0, 4.0, 1.0, False, 50.0),
    (True, 100.0, 2.0, 4.0, 0.5, 10.0, 50.0),
    (True, 3.3, 0.854, 1.1, 1.0, False, 2.5),
)

#: PHP 侧对 ``$x == false`` 的哨兵值（她在 PHP 里六个都转发成"没传参数"）
FALLBACK_SENTINELS = ("0", 0, 0.0, "", None, False)


class Stub:
    """``MyPairReference`` 的替身 —— 与 PHP harness 里的 ``PairEstStub`` 同形状。"""

    def __init__(self, symbol_a, factor, position, last, my_price, pair_price, cny):
        self.pair_ref = Side(pair_price, cny)
        self.cny_ref = Side(pair_price, cny)
        self.factor = factor
        self._last = last
        self._position = position
        self._symbol_a = symbol_a
        self._my_price = str(my_price)

    def get_position(self):
        return self._position

    def get_price(self):
        return self._my_price

    def is_symbol_a(self):
        return self._symbol_a

    def get_last_calibration_val(self):
        return self._last


class Side:
    """``$this->pair_ref`` / ``$this->cny_ref`` 的替身。"""

    def __init__(self, price, cny):
        self._price = str(price)
        self._cny = str(cny)

    def get_price(self):
        return self._price

    def get_val(self, date=False):
        return self._cny


def run_php() -> dict:
    """调 PHP 生成参考值。"""
    process = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", "_php_pairest_check.php"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        # 脚本名是相对的：**换目录跑就找不到**。按本文件所在目录固定下来，
        # 这样从 python/ 下也能跑（第一次就是在这里踩到的）。
        cwd=str(Path(__file__).resolve().parent),
    )
    if process.returncode != 0:
        sys.stderr.write(process.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP harness 失败，返回码 %d" % process.returncode)
    return json.loads(process.stdout.decode("utf-8"))


def compare(reference: dict) -> tuple:
    """比对，返回 ``(差异说明列表, 条数)``。

    与 :func:`selftest` 共用 —— 自检也是"换个实现再比对一遍"。
    """
    from calc.pairestimate import est_from_pair, est_to_pair

    problems = []
    for case in reference["cases"]:
        index = case["index"]
        symbol_a, pair, cny, factor, pos, last, my_price = CASES[index]
        stub = Stub(symbol_a, factor, pos, last, my_price, pair, cny)

        for name, python_value, php_value in (
            ("EstFromPair", _fmt(est_from_pair(stub, pair, cny)), case["from"]),
            ("EstToPair", _fmt(est_to_pair(stub, my_price, cny)), case["to"]),
        ):
            if not _close_enough(python_value, php_value):
                problems.append("第%d例 %s  php=%s python=%s"
                                % (index, name, php_value, python_value))

    stub = Stub(False, 4.0, 1.0, False, 12.5, 100.0, 2.0)
    sentinel_of = {"0": "0", "": "", "null": None, "false": False}
    for item in reference["fallback"]:
        sentinel = sentinel_of.get(item["sentinel"], 0.0)
        python_value = _fmt(est_from_pair(stub, sentinel, False))
        if not _close_enough(python_value, item["from"]):
            problems.append("哨兵 %r  php=%s python=%s"
                            % (item["sentinel"], item["from"], python_value))
    return problems, len(problems)


def selftest(reference: dict) -> int:
    """注入错误后必须能抓到 —— 对照脚本自己先证明自己是有效的。

    三类错误各打一处，都是"看着像真的能编译通过"的错法：
    """
    from calc import pairestimate

    original = {
        "qdii_get_value": pairestimate.qdii_get_value,
        "fund_adjust_position": pairestimate.fund_adjust_position,
        "_is_symbol_a": pairestimate._is_symbol_a,
    }

    def run():
        problems, _ = compare(reference)
        return len(problems)

    baseline = run()
    if baseline:
        print("基线就有 %d 处差异，先修实现再谈自检" % baseline)
        _restore(original)
        return 1

    caught = 0

    # ① A 股那条式子借用了非 A 股的（方向反了）
    pairestimate.qdii_get_value = lambda est, cny, cal: (est / cny) / cal
    if run() > 0:
        caught += 1
    pairestimate.qdii_get_value = original["qdii_get_value"]

    # ② FundAdjustPosition 丢掉了仓位混合
    pairestimate.fund_adjust_position = lambda ratio, value, old: value
    if run() > 0:
        caught += 1
    pairestimate.fund_adjust_position = original["fund_adjust_position"]

    # ③ IsSymbolA() 恒为真 —— 于是所有用例都走 A 股那条
    pairestimate._is_symbol_a = lambda ref: True
    if run() > 0:
        caught += 1
    pairestimate._is_symbol_a = original["_is_symbol_a"]

    print("=== 自检 ===")
    print("  注入 3 类错误，抓到 %d 类" % caught)
    return 0 if caught == 3 else 1


def _restore(original: dict) -> None:
    """把 monkeypatch 过的名字还原。"""
    from calc import pairestimate

    for name, value in original.items():
        setattr(pairestimate, name, value)


def _fmt(value) -> str:
    """数值转成与 PHP ``strval()`` 可比的字符串。

    PHP 的 ``strval(12.5)`` 是 ``"12.5"``，``strval(100.0)`` 是 ``"100"``；
    Python 的 ``str(100.0)`` 是 ``"100.0"``。**不统一的话每条都是差异**。
    """
    if value is None:
        return ""
    number = float(value)
    if number == int(number):
        return str(int(number))
    return repr(number)


def _close_enough(python_value: str, php_value: str) -> bool:
    """按数值比较，容差 1e-6。

    PHP 的浮点字符串有时带更多尾数（如 ``99.99999999996``），
    直接比文本会误报 —— 这也是本项目其它对照脚本的做法。
    """
    try:
        return abs(float(python_value) - float(php_value)) < 1e-6
    except (TypeError, ValueError):
        return python_value == php_value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", help="PHP harness 生成的 JSON")
    parser.add_argument("--selftest", action="store_true", help="注入错误，验证对照能抓到")
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
    total = len(reference["cases"]) + len(reference["fallback"])
    print("对照例数：%d" % total)
    print("意外差异：%d" % count)
    return 1 if count else 0


if __name__ == "__main__":
    sys.exit(main())
