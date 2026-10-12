"""拿真实 PHP 逐项对照五档表那一列「换算价」。

用法::

    php -n -d display_errors=stderr _php_qdiicolumn_check.php > reference.json
    python -B _php_qdiicolumn_compare.py --reference reference.json

不传 ``--reference`` 时会自己去调 PHP 生成（``--selftest`` 也是这条路）。

## 这一列为什么值得单独对照

``_tradingUserDefined`` 把**三段**已经存在的换算串起来：

1. ``GetEstValue`` —— 基金净值口径 → 中间层点位（``GetQdiiValue`` 的逆）；
2. ``EstFromPair``  —— 中间层点位 → 杠杆标的报价；
3. ``GetPriceDisplay`` —— 各引用按自己的精度格式化。

每一段单独看都"还挺合理"，串错一个方向页面上照样出一串数字。
PHP harness（``_php_qdiicolumn_check.php``）直接从
``woody/res/php/_qdiigroup.php`` 里抠出被测函数本体，
替身对象只提供纯数据，所以对照出来的是 PHP 的真值。

## 为什么比的是"剥掉颜色后的文本"

PHP 在每个数字外面包一层 ``<font color=…>``；本版有意只返回纯文本
（见 ``web/services/qdiicolumn`` 的模块 docstring「已知偏差」）。
所以这里先把标签剥掉再比数字，另有一组 ``traps`` 单独确认 PHP 的分支语义。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PHP_EXE = "php"
PHP_CHECK = "_php_qdiicolumn_check.php"

# 与 _php_qdiicolumn_check.php 的 $cases **必须逐项一致**（顺序也不能变）。
# 取值刻意包含：生产端那一组量级 / 没有杠杆标的 / 两个不同精度的杠杆标的 /
# A 股（走 QdiiGetVal 那条倒数方向）/ 仓位不是 1（混合项要参与）。
CASES = (
    {
        "fund": {"factor": 2459.0, "position": 0.95, "cny": "6.4328", "base": 3.30},
        "price": "3.318",
        "est": {"display": "^GSPC", "precision": 3, "price": "8157.14"},
        "lev": (
            {"display": "SPY", "precision": 2, "price": "812.89", "factor": 10.03,
             "position": 1.0, "last": None, "symbol_a": False, "cny": "1.0"},
        ),
    },
    {
        "fund": {"factor": 2459.0, "position": 0.95, "cny": "6.4328", "base": 3.30},
        "price": "5.372",
        "est": {"display": "^NDX", "precision": 3, "price": "31137.575"},
        "lev": (),
    },
    {
        "fund": {"factor": 0.008945, "position": 1.0, "cny": "1.0", "base": 111.0},
        "price": "1.234",
        "est": {"display": "^NDX", "precision": 3, "price": "31137.575"},
        "lev": (
            {"display": "QQQ", "precision": 2, "price": "620.00", "factor": 0.008945,
             "position": 1.0, "last": None, "symbol_a": False, "cny": "1.0"},
            {"display": "TQQQ", "precision": 4, "price": "92.50", "factor": 0.026835,
             "position": 1.0, "last": 90.0, "symbol_a": False, "cny": "1.0"},
        ),
    },
    {
        "fund": {"factor": 0.83, "position": 0.95, "cny": "6.7035", "base": 4.5},
        "price": "4.545",
        "est": {"display": "^NDX", "precision": 2, "price": "31137.57"},
        "lev": (
            {"display": "SH513100", "precision": 3, "price": "2.5", "factor": 1.1,
             "position": 1.0, "last": None, "symbol_a": True, "cny": "1.0"},
        ),
    },
    {
        "fund": {"factor": 1000.0, "position": 0.95, "cny": "7.1", "base": 1.2},
        "price": "0.995",
        "est": {"display": "XOP", "precision": 2, "price": "130.68"},
        "lev": (
            {"display": "FOO", "precision": 2, "price": "10.00", "factor": 2.0,
             "position": 0.5, "last": 9.0, "symbol_a": False, "cny": "2.0"},
        ),
    },
)

#: PHP 侧一并确认的分支语义 —— 本模块的 ``if ($strVal)`` 全靠它撑着。
TRAP_KEYS = (
    "traps.bool_string_zero",
    "traps.bool_empty",
    "traps.bool_string_zero_dot",
    "traps.empty_string_zero",
)

#: PHP 给我们的答案是带 ``<font …>`` 的，比之前要先剥掉。
FONT_TAG = re.compile(r"</?font[^>]*>")


class RefStub:
    """显示用那一侧：对应 PHP harness 里的 ``RefStub``。

    ``get_price_display`` 走**真正迁移过的**格式化函数而不是在本文中重写一遍 ——
    重写的话，这里就算全绿也说明不了 PHP 与 Python 一致。
    """

    def __init__(self, display, precision, price):
        self._display = display
        self._precision = precision
        self._price = str(price)

    def get_display(self):
        return self._display

    def get_price(self):
        return self._price

    def get_price_display(self, disp=None, prev=None, precision=None):
        from ref.stockref import format_price

        value = self._price if disp is None else disp
        base = self._price if prev is None else prev
        return format_price(float(value), float(base),
                            self._precision if precision is None else precision)


class LevStub(RefStub):
    """杠杆标的：``pairestimate`` 认 factor / position / cny_ref / symbol_a。"""

    def __init__(self, spec):
        super().__init__(spec["display"], spec["precision"], spec["price"])
        self.factor = spec["factor"]
        self._position = spec["position"]
        self.last_calibration_val = spec["last"]
        self._symbol_a = spec["symbol_a"]
        self.cny_ref = _CnyStub(spec["cny"])

    def get_position(self):
        return self._position

    def get_last_calibration_val(self):
        return self.last_calibration_val

    def is_symbol_a(self):
        return self._symbol_a


class _CnyStub:
    def __init__(self, value):
        self._value = value

    def get_val(self, date=None):
        return self._value


class FundStub:
    """基金：``GetEstValue`` 用真实迁移过的纯函数拼出。

    与 ``refobj.QdiiReference.get_est_value`` 同一步骤
    （先 ``ReverseAdjustPosition``，再 ``QdiiGetPeerVal``），
    区别只是校准基准值由用例给出而不是去查库。
    """

    def __init__(self, spec, est_ref):
        self.factor = spec["factor"]
        self._position = spec["position"]
        self._cny = spec["cny"]
        self._base = spec["base"]
        self.est_ref = est_ref

    def get_est_ref(self):
        return self.est_ref

    def get_est_value(self, qdii_value):
        from calc.fundcalc import fund_reverse_adjust_position, qdii_get_peer_value

        reverse = fund_reverse_adjust_position(
            self._position, float(qdii_value), self._base)
        return qdii_get_peer_value(reverse, float(self._cny), self.factor)


def _python_values():
    """用真实实现算出每一例的本版答案。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from web.services.qdii_trading_column import estimate_cell

    values = {}
    for index, case in enumerate(CASES):
        est = RefStub(case["est"]["display"], case["est"]["precision"],
                      case["est"]["price"])
        lev = [LevStub(spec) for spec in case["lev"]]
        fund = FundStub(case["fund"], est)
        values["cell.%d" % index] = estimate_cell(fund, est, lev, case["price"])
    return values


def _strip_tags(text):
    return FONT_TAG.sub("", str(text))


def run_reference():
    """调 PHP 生成参考值。"""
    here = Path(__file__).resolve().parent
    result = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", PHP_CHECK],
        cwd=str(here), capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        sys.stderr.write(result.stderr or "PHP harness 失败\n")
        raise SystemExit(2)
    return json.loads(result.stdout)


def compare(reference):
    """返回 (不一致的 key 列表, 未覆盖的 key 列表)。"""
    values = _python_values()
    unexpected = []
    missing = []

    for key, expected in sorted(reference.items()):
        if key.startswith("traps."):
            continue
        if key not in values:
            missing.append(key)
            continue
        got = values[key]
        # PHP 给出的是带 <font> 的 HTML，本版有意只给纯文本
        if _strip_tags(expected) != _strip_tags(got):
            unexpected.append((key, expected, got))

    for key in values:
        if key not in reference:
            missing.append(key)
    return unexpected, missing


def selftest():
    """**故意**改坏三处，验证这套对照真的能抓出差错。

    照搬 ``_php_pairest_compare.py`` 的做法：一个只会打印绿勾的对照脚本
    比没有更糟 —— 它会让人以为已经验过了。
    """
    import calc.fundcalc
    import calc.pairestimate
    import ref.stockref

    originalPairestimate = pairestimate.est_from_pair
    originalFactor = pairestimate._factor_of
    originalFormat = stockref.format_price
    originalPeer = fundcalc.qdii_get_peer_value

    caught = []
    reference = run_reference()

    # 1. EstFromPair 抄反方向：非 A 股那条例改用乘法
    def bad_est_from_pair(ref, pair_val=False, cny=False):
        value = originalPairestimate(ref, pair_val, cny)
        return None if value is None else value * 10.03

    pairestimate.est_from_pair = bad_est_from_pair
    caught.append("EST方向" if compare(reference)[0] else None)
    pairestimate.est_from_pair = originalPairestimate

    # 2. 校准值读错：一律退回 1/仓位
    pairestimate._factor_of = lambda ref: 1.0
    caught.append("校准值" if compare(reference)[0] else None)
    pairestimate._factor_of = originalFactor

    # 3. 格式化掉一位小数
    stockref.format_price = lambda disp, prev, precision: originalFormat(
        disp, prev, max(precision - 1, 0))
    caught.append("精度" if compare(reference)[0] else None)
    stockref.format_price = originalFormat

    # 4. GetEstValue 的逆公式换成正向那个
    fundcalc.qdii_get_peer_value = fundcalc.qdii_get_value
    caught.append("EST_PTR" if compare(reference)[0] else None)
    fundcalc.qdii_get_peer_value = originalPeer

    return [name for name in caught if name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="PHP 生成的参考值 JSON")
    parser.add_argument("--selftest", action="store_true",
                        help="故意注入错误，验证对照脚本抓得出来")
    args = parser.parse_args()

    if args.selftest:
        caught = selftest()
        for name in ("EST方向", "校准值", "精度", "EST_PTR"):
            if name in caught:
                print("  %-8s 注入错误已被抓到 ✓" % name)
            else:
                print("  %-8s 注入错误**没被抓到** ✗" % name)
        return 0 if len(caught) == 4 else 1

    reference = json.loads(args.reference.read_text(encoding="utf-8")) if args.reference else run_reference()
    unexpected, missing = compare(reference)

    traps = {key: reference.get(key) for key in TRAP_KEYS}
    print("PHP 分支语义确认：")
    print("  if ('0')                -> %s" % traps["traps.bool_string_zero"])
    print("  if ('0.0')              -> %s" % traps["traps.bool_string_zero_dot"])
    print("  empty('0')              -> %s" % traps["traps.empty_string_zero"])
    print()

    print("逐项对照（%d 例，只比剥掉 <font> 后的文本）：" % len(CASES))
    for key, expected, got in unexpected:
        print("  ✗ %s  PHP=%s  本版=%s" % (key, expected, got))
    print("  不一致 %d 项，未比对 %d 项" % (len(unexpected), len(missing)))
    for key in missing:
        print("    - %s" % key)
    return 1 if unexpected or missing else 0


if __name__ == "__main__":
    sys.exit(main())
