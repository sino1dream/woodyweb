"""拿真实 PHP 逐项对照「每个杠杆标的各一张均线表」（``smaparagraph.php:156``）。

用法::

    php _php_fundpairsma_check.php > reference.json
    python -B _php_fundpairsma_compare.py --reference reference.json

不传 ``--reference`` 时会自己去调 PHP 生成（``--selftest`` 也是这条路）。

## 这一轮比的是什么

``EstFromPair`` 的数值路径上一轮（``_php_qdiicolumn_compare.py``）已经验过了，
所以这里**不重复验公式**，验的是**组装**：

1. ``EchoSmaParagraph()`` 那段 ``$ar`` —— 列序、列宽、**列头颜色**。
   PHP harness 直接 ``require`` 了 ``php/ui/table.php`` 与 ``php/ui/stocktable.php``，
   用的是 ``TableColumn`` / ``TableColumnEst`` / ``TableColumnPremium`` /
   ``TableColumnStock`` 的**真身**，不是抽函数体重建；
2. ``_getSmaCallbackPriceDisplay()`` —— 那一格文本（仍从 PHP 源码现场抠出）。

## 为什么颜色也要比

``TableColumnStock`` 是 ``parent::__construct($sym->GetDisplay(), $iWidth, 'maroon')``。
本版早期写成不带颜色的 ``column_base``，数值全对、只差一个颜色 —— 页面上一眼看不出。
所以颜色不是"装饰"，是逐项比对的一部分：PHP 给的是带 ``<font color=…>`` 的
HTML，这里把标签剥掉当文本比、**从标签里取出颜色当颜色比**。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PHP_EXE = "php"
PHP_CHECK = "_php_fundpairsma_check.php"

# 与 _php_fundpairsma_check.php 的 $cases **必须逐项一致**（顺序也不能变）。
# 取值刻意包含：生产端那一组量级 / 做空一侧（仓位 -1）/ 有上次校准值 /
# A 股（走 QdiiGetVal 那条倒数方向）/ 汇率不是 1（GetDefaultCny 真进了公式）。
CASES = (
    {"display": "SPY", "precision": 2, "price": "777.24", "factor": 10.035,
     "position": 1.0, "last": None, "symbol_a": False, "cny": "1.0",
     "val": "7799.4"},
    {"display": "SH", "precision": 2, "price": "31.86", "factor": 245.591,
     "position": -1.0, "last": None, "symbol_a": False, "cny": "1.0",
     "val": "7799.4"},
    {"display": "TQQQ", "precision": 4, "price": "92.50", "factor": 0.026835,
     "position": 1.0, "last": 90.0, "symbol_a": False, "cny": "1.0",
     "val": "31137.575"},
    {"display": "SH513100", "precision": 3, "price": "2.5", "factor": 1.1,
     "position": 1.0, "last": None, "symbol_a": True, "cny": "1.0",
     "val": "31137.57"},
    {"display": "FOO", "precision": 2, "price": "10.00", "factor": 2.0,
     "position": 0.5, "last": 9.0, "symbol_a": False, "cny": "2.0",
     "val": "1000.00"},
)

#: PHP harness 用它自己的 ``$first``（display='SH'）组装列
COLUMN_HEAD_DISPLAY = "SH"

#: PHP 侧一并确认的分支语义 —— ``if ($strVal)`` 全靠它撑着。
TRAP_KEYS = ("traps.bool_string_zero", "traps.bool_string_zero_dot")

#: PHP 给的答案带 ``<font …>``：剥掉当文本，取出来当颜色。
FONT_TAG = re.compile(r"</?font[^>]*>")
FONT_COLOR = re.compile(r"<font color=([^>]+)>")


class RefStub:
    """显示用那一侧：``get_price_display`` 走**真正迁移过的**格式化函数。

    在本文中重写一遍的话，这里就算全绿也说明不了 PHP 与 Python 一致。
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


def _services():
    """一次性把服务与两个被测口径取出来（都要走 install_paths）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from web.bootstrap import install_paths

    install_paths()
    from ui.uicolumn import columns_to_dicts
    from web.services import sma_service as module
    from web.services.sma_service import SmaService, callback_fund_pair_sma

    return module, SmaService, callback_fund_pair_sma, columns_to_dicts


def _python_values():
    """用真实实现算出每一例的本版答案。"""
    module, SmaService, callback, columns_to_dicts = _services()

    values = {}

    # 一、列组装 —— 与 PHP 那七个 col.N 对应
    first = LevStub({"display": COLUMN_HEAD_DISPLAY, "precision": 2, "price": "0",
                     "factor": 1.0, "position": 1.0, "last": None,
                     "symbol_a": False, "cny": "1.0"})
    columns = columns_to_dicts(
        SmaService()._columns(False, first, callback))
    for index, column in enumerate(columns):
        values["col.%d" % index] = (column["text"], column["color"], column["width"])

    # 二、每一例的表头与那一格
    for index, spec in enumerate(CASES):
        lev = LevStub(spec)
        values["cell.%d" % index] = {
            "name": spec["display"],
            "header": _triple(module._stock_column(lev, callback).to_dict()),
            "cell": module._callback_price_display(callback, lev, spec["val"]),
            "no_arg": "self" if callback(lev) is lev else "other",
        }
    return values


def _triple(column_dict):
    return (column_dict["text"], column_dict["color"], column_dict["width"])


def _strip_tags(text):
    return FONT_TAG.sub("", str(text))


def _split_font(text):
    """``<font color=magenta>EST</font>`` → ``('EST', 'magenta')``。

    没有 ``<font>`` 就是没染色（PHP 的 ``$strColor = false`` 分支）。
    """
    raw = str(text)
    match = FONT_COLOR.search(raw)
    return _strip_tags(raw), (match.group(1) if match else "")


def run_reference():
    """调 PHP 生成参考值。

    PHP 的 openssl 警告会打到 stdout，所以从第一个 ``{`` 开始解析。
    """
    here = Path(__file__).resolve().parent
    result = subprocess.run(
        [PHP_EXE, PHP_CHECK], cwd=str(here),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        sys.stderr.write(result.stderr or "PHP harness 失败\n")
        raise SystemExit(2)
    start = result.stdout.find("{")
    if start < 0:
        sys.stderr.write("PHP harness 没输出 JSON：%r\n" % result.stdout[:200])
        raise SystemExit(2)
    return json.loads(result.stdout[start:])


def compare(reference):
    """返回 (不一致的 key 列表, 未覆盖的 key 列表)。"""
    values = _python_values()
    unexpected = []
    missing = []

    def mismatch(key, expected, got):
        unexpected.append((key, expected, got))

    for key in sorted(reference.get("columns", {})):
        text, width = reference["columns"][key]
        expected = _split_font(text) + (width,)
        if key not in values:
            missing.append(key)
            continue
        if tuple(values[key]) != tuple(expected):
            mismatch(key, expected, values[key])

    for key in sorted(reference.get("cases", {})):
        expected = reference["cases"][key]
        if key not in values:
            missing.append(key)
            continue
        got = values[key]
        # 表头三项（文本 / 颜色 / 宽度）逐项比，哪一项错就报哪一项
        expected_header = _split_font(expected["header"]) + (90,)
        if tuple(got["header"]) != tuple(expected_header):
            mismatch(key + ".header", expected_header, got["header"])
        if _strip_tags(expected["cell"]) != _strip_tags(got["cell"]):
            mismatch(key + ".cell", expected["cell"], got["cell"])
        if expected["no_arg"] != got["no_arg"]:
            mismatch(key + ".no_arg", expected["no_arg"], got["no_arg"])

    for key in values:
        if key.startswith("cell."):
            continue
        if key not in reference.get("columns", {}):
            missing.append(key)
    return unexpected, missing


def selftest():
    """**故意**改坏四处，验证这套对照真的能抓出差错。

    一个只会打印绿勾的对照脚本比没有更糟 —— 它会让人以为已经验过了。
    """
    import calc.pairestimate
    import ref.stockref

    module, _SmaService, callback, _to_dicts = _services()
    from ui.uicolumn import TableColumn, column_base

    originalStock = module._stock_column
    originalEst = module.column_est
    originalFormat = stockref.format_price

    caught = []
    reference = run_reference()

    # 1. EstFromPair 抄反方向：改用 EstToPair。
    #    补丁必须打在 **sma_service 的命名空间**上 —— 那边是
    #    ``from pairestimate import est_from_pair``，改 pairestimate 模块本身
    #    不影响已经绑过来的名字。（第一版就是打错了地方，自检才会"没抓到"。）
    originalPair = module.est_from_pair
    module.est_from_pair = module.est_to_pair
    caught.append("EST方向" if compare(reference)[0] else None)
    module.est_from_pair = originalPair

    # 2. 对照列丢了 maroon —— 这正是本轮修掉的那个 bug
    def colourless(cb_ref, cb):
        display = module._invoke(cb_ref, "get_display", "GetDisplay")
        return column_base("" if display is None else str(display), 90)

    module._stock_column = colourless
    caught.append("对照列颜色" if compare(reference)[0] else None)
    module._stock_column = originalStock

    # 3. EST 列宽抄错：80 → 70
    def narrow_est(prefix=""):
        return TableColumn(text="EST", width=70, color="magenta", prefix=prefix)

    module.column_est = narrow_est
    caught.append("列宽" if compare(reference)[0] else None)
    module.column_est = originalEst

    # 4. 格式化掉一位小数
    stockref.format_price = lambda disp, prev, precision: originalFormat(
        disp, prev, max(precision - 1, 0))
    caught.append("精度" if compare(reference)[0] else None)
    stockref.format_price = originalFormat

    return [name for name in caught if name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="PHP 生成的参考值 JSON")
    parser.add_argument("--selftest", action="store_true",
                        help="故意注入错误，验证对照脚本抓得出来")
    args = parser.parse_args()

    if args.selftest:
        caught = selftest()
        for name in ("EST方向", "对照列颜色", "列宽", "精度"):
            if name in caught:
                print("  %-10s 注入错误已被抓到 ✓" % name)
            else:
                print("  %-10s 注入错误**没被抓到** ✗" % name)
        return 0 if len(caught) == 4 else 1

    reference = (json.loads(args.reference.read_text(encoding="utf-8"))
                 if args.reference else run_reference())
    unexpected, missing = compare(reference)

    traps = reference.get("traps", {})
    print("PHP 分支语义确认：")
    for key in TRAP_KEYS:
        print("  %-28s -> %s" % (key, traps.get(key.split(".", 1)[1])))
    print()

    print("列组装（%d 列，文本 / 颜色 / 宽度 三项全比）："
          % len(reference.get("columns", {})))
    for key, (text, width) in sorted(reference["columns"].items()):
        print("  %-6s PHP=%-34r w=%s" % (key, text, width))
    print()

    print("逐项对照（%d 例）：" % len(CASES))
    for key, expected, got in unexpected:
        print("  ✗ %s  PHP=%s  本版=%s" % (key, expected, got))
    print("  不一致 %d 项，未比对 %d 项" % (len(unexpected), len(missing)))
    for key in missing:
        print("    - %s" % key)
    return 1 if unexpected or missing else 0


if __name__ == "__main__":
    sys.exit(main())
