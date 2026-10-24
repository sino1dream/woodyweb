"""CK4-F：七列持仓测算表（``holdingscn.php`` 的 ``_echoHoldingItem``）逐格对照。

    python -B auto/_scratch/_php_holdingscn_compare.py

左边是真 PHP（``_php_holdingscn_check.php`` 从 ``woody/res/holdingscn.php``
与 ``php/`` 下若干文件**现场切出** ``_echoHoldingItem`` 及其依赖，一个字不改），
右边是本项目的 ``web/services/holdings_service.py``。**两边用同一组输入**
（本文件与那个 PHP 脚本里的 ``$CASES`` 逐字一致），逐格比。

比的是**页面上看得见的七格 + 合计尾行 + 首格 title**：

    代码 | 旧比例(%) | 旧价格 | 此后涨幅 | 新比例(%) | 影响比例(%) | 汇率调整

两处形状差异在归一化时处理（其余格子逐字比）：

* **首格**：PHP 是 ``RefGetMyStockLink`` 的 ``<a>`` HTML，本站点是
  ``row.href``（URL）+ 纯符号文本 —— 只 anchor 文本参与比较；
* **此后涨幅**：PHP 是 ``<font color=...>``，本站点是 ``(文本, 颜色)`` 二元组。
  颜色按站点约定映射（PHP 的 black→red、red→green，即涨红跌绿），
  **文本逐字比**。

用例：

* ``prod_like`` —— 生产端 SH513050 的形状（A/H/US 各一只）；
* ``edges``     —— 旧价格 ``3.675``（half-up 边界）、price == close
  （阈值归零）、持仓日缺键、close 为 float 0.0（涨幅除数回落昨收）。

**一个用例起一个 PHP 进程**：``_echoHoldingItem`` 的三个累计器是 static，
Python 侧是每次 ``get_table`` 清零，同一进程连跑会互相污染。
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(HERE)
PYTHON_DIR = os.path.dirname(AUTO_DIR)
WEB_DIR = os.path.join(PYTHON_DIR, "web")
for _path in (AUTO_DIR, PYTHON_DIR, WEB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from ref.stockref import StockReference  # noqa: E402
from web.services.holdings_service import HoldingsService  # noqa: E402

PHP_EXE = r"D:\xampp\php\php.exe"
PHP_SCRIPT = os.path.join(HERE, "_php_holdingscn_check.php")

#: 此后涨幅那格的颜色映射：PHP 涨黑跌红，本站点统一涨红跌绿。
PHP_COLOR_TO_SITE = {"black": "red", "red": "green", "gray": "gray"}

#: 与 ``_php_holdingscn_check.php`` 的 ``$CASES`` 逐字一致。
CASES = {
    "prod_like": {
        "nvc": 1.0234,
        "adj_hkd": 0.9063,
        "adj_usd": 1.0082,
        # int 键：MySQL 取出的形状；get_stock_id() 返回字符串。
        "ratio": {13: 5.0, 11: 30.68, 12: 7.31},
        "history": {13: "1500.0", 11: "425.2", 12: "81.96"},
        "refs": [
            {"id": "13", "symbol": "SH600519", "price": "1501.0", "prev": "1490.0",
             "a": True, "h": False},
            {"id": "11", "symbol": "00700", "price": "418.0", "prev": "430.0",
             "a": False, "h": True},
            {"id": "12", "symbol": "PDD", "price": "81.96", "prev": "80.0",
             "a": False, "h": False},
        ],
    },
    "edges": {
        "nvc": 0.9985,
        "adj_hkd": 0.9,
        "adj_usd": 1.0,
        "ratio": {20: 1.0, 21: 2.5, 22: 0.0001, 23: 4.0},
        # 23 => float 0.0（PHP 侧同款 float，不是字符串 '0.0'）
        "history": {20: "3.675", 21: "10.0", 23: 0.0},
        "refs": [
            {"id": "20", "symbol": "SZ000001", "price": "3.7", "prev": "3.6",
             "a": True, "h": False},
            {"id": "21", "symbol": "01810", "price": "10.0", "prev": "10.0",
             "a": False, "h": True},
            {"id": "22", "symbol": "BILI", "price": "106.2", "prev": "100.0",
             "a": False, "h": False},
            {"id": "23", "symbol": "PDD", "price": "101.0", "prev": "100.0",
             "a": False, "h": False},
        ],
    },
}

FUND_SYMBOL = "SH513050"


# ---------------------------------------------------------------------------
# Python 侧替身（与 tests/test_holdings.py 同款，这里自包含）
# ---------------------------------------------------------------------------
class FakeHoldingRef(StockReference):
    """成分股引用替身：真 ``StockReference`` 给符号判定与涨幅口径。"""

    def __init__(self, spec):
        super().__init__(spec["symbol"])
        self.stock_id = spec["id"]
        self.str_price = spec["price"]
        self.str_prev_price = spec["prev"]

    def get_stock_id(self):
        return self.stock_id


class FakeHoldingsRef:
    """基金级持仓引用替身（页面侧表面）。"""

    def __init__(self, case):
        self._case = case

    def get_symbol(self):
        return FUND_SYMBOL

    def is_symbol_a(self):
        return True

    def is_symbol_h(self):
        return False

    def count_holdings(self):
        return len(self._case["refs"])

    def get_holdings_ratio_display(self):
        return ""

    def get_holdings_ref_array(self):
        return [FakeHoldingRef(spec) for spec in self._case["refs"]]

    def get_holdings_ratio_array(self):
        return dict(self._case["ratio"])

    def get_net_value_change(self):
        return self._case["nvc"]

    def get_holding_date_history(self):
        return dict(self._case["history"])

    def get_adjust_hkd(self):
        return self._case["adj_hkd"]

    def get_adjust_usd(self):
        return self._case["adj_usd"]


class NameSource:
    """只为 ``stock_name_of`` 供 ``get_stock_name``（与 PHP 的 SqlGetStockName 同款）。"""

    def get_stock_name(self, symbol):
        return symbol + "名称"


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------
ANCHOR_RE = re.compile(r'^<a href="[^"]*">(.*)</a>$')
FONT_RE = re.compile(r'^<font color=(\w+)>(.*)</font>$')


def normalize_php_cell(index: int, value):
    """PHP 格子 → 与本站点可比的形状。"""
    if index == 0:
        matched = ANCHOR_RE.match(value)
        return matched.group(1) if matched else value
    if index == 3:
        matched = FONT_RE.match(value)
        if matched:
            return (matched.group(2), PHP_COLOR_TO_SITE.get(matched.group(1), matched.group(1)))
        return value
    return value


def normalize_python_cell(index: int, value):
    """本站点格子 → 同一形状（cells 里颜色格子本来就是二元组）。"""
    return value


def python_rows(case):
    service = HoldingsService(source=NameSource())
    table = service.get_table(FakeHoldingsRef(case))
    return table["rows"]


def php_rows(case_name):
    result = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", PHP_SCRIPT, case_name],
        capture_output=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP 对照脚本失败（%s）：%d" % (case_name, result.returncode))
    return json.loads(result.stdout.decode("utf-8").strip().splitlines()[-1])


def main() -> int:
    bad = 0
    total = 0
    for case_name, case in CASES.items():
        php_side = php_rows(case_name)
        py_side = python_rows(case)
        if len(php_side) != len(py_side):
            print("%s: 行数不一致 PHP=%d Python=%d" % (case_name, len(php_side), len(py_side)))
            bad += 1
            continue
        for row_index, (php_row, py_row) in enumerate(zip(php_side, py_side)):
            php_cells = [normalize_php_cell(i, v) for i, v in enumerate(php_row["cells"])]
            py_cells = [normalize_python_cell(i, v) for i, v in enumerate(py_row.cells)]
            label = "合计尾行" if row_index == len(php_side) - 1 else "第%d行" % row_index
            if len(php_cells) != len(py_cells):
                print("%s %s: 格子数不一致 PHP=%d Python=%d"
                      % (case_name, label, len(php_cells), len(py_cells)))
                bad += 1
            for index, (a, b) in enumerate(zip(php_cells, py_cells)):
                total += 1
                if a != b:
                    bad += 1
                    print("%s %s 第%d格: PHP=%r Python=%r" % (case_name, label, index, a, b))
            # 首格 title（PHP 的 hint；尾行恒 false → 空串）
            php_hint = php_row.get("hint") or ""
            total += 1
            if php_hint != py_row.title:
                bad += 1
                print("%s %s title: PHP=%r Python=%r" % (case_name, label, php_hint, py_row.title))

    print("-" * 78)
    print("共 %d 项（格子 + title），不一致 %d 项" % (total, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
