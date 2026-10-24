# ``core/submittransaction`` 三个纯计算的逐例对照（PHP harness 自起进程）。
#
# 跑法::
#
#     PYTHONPATH=/f/woody/woodyweb/python/auto:/f/woody/woodyweb/python \
#         D:/python/python.exe _php_submittransaction_compare.py
#
# 对照对象：``core/submittransaction.py`` 的
# :func:`stock_get_fund_fee_ratio` / :func:`get_arbitrage_quantity`，
# 以及 adjust 分支里那句 ``strval(floatval(q) * floatval(p) / floatval(nv))``
# —— 对应 PHP ``woody/res/php/_stock.php::StockGetFundFeeRatio``、
# ``php/ui/stockcomm.php::GetArbitrageQuantity`` 与
# ``woody/res/php/_submittransaction.php:244``。
#
# 手法：三个用例（fee / arb / adjust）各起一个 PHP 进程跑 harness
# （``_php_submittransaction_check.php``，真实源码切片），Python 侧用同一批
# 输入算一遍，逐键比对。
#
# 没对照的部分：七个动作的分派与权限闸门是**流程**（靠单测覆盖），
# 没有可切片的纯函数；``UpdateStockGroupItem`` 的回填公式同样在单测里
# 按 ``AddSqlTransaction`` 的语义断言。

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

from core.submittransaction import (  # noqa: E402
    get_arbitrage_quantity,
    stock_get_fund_fee_ratio,
)

#: PHP 可执行文件。用 Windows 路径（Python 的 subprocess 不认 MSYS 的
#: ``/d/xampp`` 形式），环境变量 ``PHP_BIN`` 可覆盖。
PHP_BIN = os.environ.get("PHP_BIN", r"D:\xampp\php\php.exe")

#: 与 harness 的 fee 用例逐字一致（覆盖 switch 每一档 + 默认档）
FEE_SYMBOLS = ["SZ160416", "SZ161116", "SZ161226", "SH501018",
               "SZ161125", "SZ161126", "SZ161127", "SZ161128",
               "SZ161129", "SZ161130", "SZ162719", "SZ164824",
               "SZ164906", "SZ161815", "SZ999999", "SH513100",
               "GLD", ""]

#: 与 harness 的 arb 用例逐字一致
ARB_VALUES = [1000.0, 1234567.0, 0.0, -500.0, 99.5, 1.0,
              1000000.0, 3.14159, 250.0, -1.0]

#: 与 harness 的 adjust 用例逐字一致（quantity / price / netvalue）
ADJUST_CASES = [
    ("1000", "1.65", "1.10"),
    ("1000", "1.65", "1.5"),
    ("-500", "1.72", "2.0"),
    ("100", "0.0", "1.0"),
    ("", "1.65", "1.0"),
    ("123456", "0.1234", "0.0567"),
    ("7", "3", "7"),
]


def run_php(case):
    """一个用例一个 PHP 进程（被抽函数里有 static 累计器，见 pitfalls #110）。"""
    result = subprocess.run(
        [PHP_BIN, "-n", "-d", "display_errors=stderr",
         os.path.join(SCRATCH, "_php_submittransaction_check.php"), case],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError("PHP harness 失败(%s): %s"
                           % (case, result.stderr.decode("utf-8", "replace")))
    text = result.stdout.decode("utf-8").strip()
    return json.loads(text) if text else {}


def case_fee():
    out = {}
    for index, symbol in enumerate(FEE_SYMBOLS):
        out["v%d" % index] = stock_get_fund_fee_ratio(symbol)
    return out


def case_arb():
    out = {}
    for index, value in enumerate(ARB_VALUES):
        # hedge 用默认的 1.0（与 harness 的 GetStockHedge shim 一致）
        out["v%d" % index] = get_arbitrage_quantity("SZ161125", "501", value)
    return out


def case_adjust():
    """adjust 分支的数量重算：``strval(floatval(q)*floatval(p)/floatval(nv))``。"""
    from core.submittransaction import _php_floatval, _php_strval_float

    out = {}
    for index, (quantity, price, net_value) in enumerate(ADJUST_CASES):
        value = (_php_floatval(quantity) * _php_floatval(price)
                 / _php_floatval(net_value))
        out["v%d" % index] = _php_strval_float(value)
    return out


CASES = {
    "adjust": case_adjust,
    "arb": case_arb,
    "fee": case_fee,
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
            # PHP 的 json_encode 把 0.0 编成 0.0、0.012 编成 0.012；
            # Python 侧是 float，逐位相等才是真一致
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
