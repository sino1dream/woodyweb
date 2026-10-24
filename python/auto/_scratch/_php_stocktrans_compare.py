# ``core.stocktrans`` 写入口纯函数的逐例对照（PHP harness 自起进程）。
#
# 跑法::
#
#     PYTHONPATH=/f/woody/woodyweb/python/auto:/f/woody/woodyweb/python \
#         D:/python/python.exe _php_stocktrans_compare.py
#
# 对照对象：``core/stocktrans.py`` 的 :func:`get_stock_quantity` /
# :func:`get_stock_cost` / :func:`strval_round` —— 对应 PHP
# ``woody/res/php/_submittransaction.php`` 的 ``_getStockQuantity`` /
# ``_getStockCost`` 与 ``php/debug.php`` 的 ``strval_round``。
#
# 手法：三个用例（quantity / cost / round）各起一个 PHP 进程跑 harness
# （``_php_stocktrans_check.php``，真实源码切片 + 真 StockSymbol 类），
# Python 侧用同一批输入算一遍，逐键比对。
#
# ``cost`` 用例的符号判定走**真 StockSymbol**（PHP 整个类切出来 eval），
# 不是假的 —— 六种费率分支的归属（期货/外汇/A股基金/A股/港股/美股）
# 因此也被覆盖。

import json
import os
import subprocess
import sys

SCRATCH = os.path.dirname(os.path.abspath(__file__))
AUTO_ROOT = os.path.abspath(os.path.join(SCRATCH, ".."))
PYTHON_ROOT = os.path.abspath(os.path.join(AUTO_ROOT, ".."))
for path in (AUTO_ROOT, PYTHON_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.stocksymbol import StockSymbol  # noqa: E402
from core.stocktrans import (  # noqa: E402
    get_stock_cost,
    get_stock_quantity,
    strval_round,
)

#: PHP 可执行文件。用 Windows 路径（Python 的 subprocess 不认 MSYS 的
#: ``/d/xampp`` 形式），环境变量 ``PHP_BIN`` 可覆盖。
PHP_BIN = os.environ.get("PHP_BIN", r"D:\xampp\php\php.exe")

#: cost 用例的 groupitem_id → 符号（harness 的 GetGroupItemSym 预设表）
COST_ITEMS = {
    "101": "hf_NQ",
    "102": "fx_susdcnh",
    "103": "SZ161125",
    "104": "SZ000001",
    "105": "00700",
    "106": "GLD",
}

#: 与 harness 的 cost 用例逐字一致
COST_CASES = [
    ("future", "101", "10", "100", {}),
    ("forex", "102", "1000", "7.1", {}),
    ("fund_a", "103", "1000", "1.65", {}),
    ("a_buy", "104", "1000", "10", {}),
    ("a_sell", "104", "-1000", "10", {}),
    ("a_buy_min", "104", "100", "10", {}),
    ("hk", "105", "500", "300", {}),
    ("us_small", "106", "100", "300", {}),
    ("us_big", "106", "500", "300", {}),
    ("us_sell", "106", "-500", "300", {}),
    ("explicit", "106", "100", "300", {"commission": "3"}),
    ("explicit_permil", "106", "100", "300",
     {"commission": "1", "commissionselect": "1"}),
    ("with_tax", "106", "100", "300",
     {"commission": "1", "commissionselect": "1",
      "taxselect": "1", "tax": "0.5"}),
    ("commission_zero", "106", "100", "300", {"commission": "0"}),
    ("tax_no_select", "106", "100", "300", {"commission": "1"}),
]

#: 与 harness 的 quantity 用例逐字一致
QUANTITY_CASES = [
    ("buy", {"quantity": "100", "type": "1"}),
    ("sell", {"quantity": "100", "type": "0"}),
    ("spaced", {"quantity": " 100 ", "type": "0"}),
    ("empty_sell", {"quantity": "", "type": "0"}),
    ("no_type", {"quantity": "100"}),
    ("zero_type", {"quantity": "50", "type": "0"}),
]

#: 与 harness 的 round 用例逐字一致
ROUND_VALUES = [0.62, 15.0, 0.165, 5.0, 300.0, 1.0, 2.5, 6.7, 45.0,
                1.5, 15.0, 150.0, 0.1255, 2.6755, 0.045, 1.005,
                -0.004, 0.0]


def run_php(case):
    """一个用例一个 PHP 进程（被抽函数里有 static 累计器，见 pitfalls #110）。"""
    result = subprocess.run(
        [PHP_BIN, "-n", "-d", "display_errors=stderr",
         os.path.join(SCRATCH, "_php_stocktrans_check.php"), case],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError("PHP harness 失败(%s): %s"
                           % (case, result.stderr.decode("utf-8", "replace")))
    text = result.stdout.decode("utf-8").strip()
    return json.loads(text) if text else {}


def case_quantity():
    out = {}
    for name, params in QUANTITY_CASES:
        out[name] = get_stock_quantity(params)
    return out


def case_cost():
    out = {}
    for name, item_id, quantity, price, extra in COST_CASES:
        symbol = StockSymbol(COST_ITEMS[item_id])
        out[name] = get_stock_cost(symbol, quantity, price, extra)
    return out


def case_round():
    out = {}
    for index, value in enumerate(ROUND_VALUES):
        # 索引键 + [p3, auto] 二元组：PHP 的 float→str 会丢 '.0'
        # （150.0 -> '150'），键形状不是被测语义
        out["v%d" % index] = [strval_round(value, 3), strval_round(value)]
    return out


CASES = {
    "quantity": case_quantity,
    "cost": case_cost,
    "round": case_round,
}


def main():
    failures = 0
    for name, builder in sorted(CASES.items()):
        expected = run_php(name)
        actual = builder()
        diffs = []
        for key in sorted(set(expected) | set(actual)):
            left = expected.get(key)
            right = actual.get(key)
            if left != right:
                diffs.append("  %s: PHP=%r Python=%r" % (key, left, right))
        if diffs:
            failures += 1
            print("== %s: %d 处差异" % (name, len(diffs)))
            for line in diffs:
                print(line)
        else:
            print("== %s: 一致（%d 键）" % (name, len(expected)))
    if failures:
        print("\n%d 个用例存在差异" % failures)
        return 1
    print("\n全部用例与 PHP 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
