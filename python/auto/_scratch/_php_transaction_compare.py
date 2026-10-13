#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交易明细 / 资金汇总段落的 PHP 逐例对照。

用法::

    python _scratch/_php_transaction_compare.py            # 跑 PHP harness 取参照
    python _scratch/_php_transaction_compare.py ref.json   # 用存档参照

PHP 侧是 ``_php_transaction_check.php``：它**从 PHP 源码里抠出函数体**
（``_echoTransactionTableItem`` / ``_echoMoneyItem`` / ``_EchoMoneyGroupData`` /
``StockGroup`` / ``MultiCurrency`` / 链接助手族 / ``GetNumberDisplay``），
放进一个命名空间里跑，数据库与账号那两档由同命名空间里的桩遮住。
本脚本用 Python 端口跑**同一批用例**，逐项比。

## 三条归一化（不是偏差，是同一件事的两种写法）

1. **剥 ``<font color=…>``** —— PHP 的 ``GetNumberDisplay()`` 会把灰（≈0）
   与红（负）包进 ``<font>``，Python 侧颜色归 CSS
   （``README_ui_column_migration.md`` 已记），所以只比剥掉标签后的文本；
2. **PHP 的 ``false`` 归一成 ``''``** —— ``GetSqlTransactionDate()``
   找不到空格时 PHP 回 false，Python 回 ``None``，而 ``TableRow.add()``
   再把它存成 ``''``（与 PHP echo false 一致）；
3. **价格格对照的是参数不是结果** —— 两侧都用桩固定成 ``sprintf('%.*f')``，
   于是比的是段落传进去的 ``disp / prev / precision``。

## 一个真实存在的差异（不在本对照范围内）

``QdiiGetSymbolArray()``：本地 PHP 仓库是 33 个符号的旧快照，而 Python 侧
按**生产端** 41 个（见 ``core/stocksymbol.qdii_get_symbol_array`` 的注释与
``commit 5ce76b75``）。本用例集刻意只用两边都有的符号（``SZ161125`` 等），
那 8 个新符号不进对照。

## 这个脚本抓到的真 bug

1. **清空链接的确认文字漏了 ``确认``** —— PHP 的问句是
   ``'确认清空'.'备注'.'：'.$strRemark.'？'``，初版端口写成
   ``'清空备注：…'``，**而单测也照着错的写**（``test_transaction``
   断言里的 ``confirm('清空备注：打新？')``）。479 例里 130 余处
   当场抓出。这是"测试保护着 bug"的典型：单测全绿不等于行为对。
2. **负零不归零** —— PHP 的 ``number_format(-0.004, 2)`` 是 ``'0.00'``
   （**不带负号**），Python 的 ``'%.2f'`` 给 ``'-0.00'``。修在
   ``historytable._trim_number_text``（数字层，不是本段落）。

## 自检：注入旧 bug，验证 harness 有牙

============================  ================================================
注入的 bug                      复现的 mismatch 数
============================  ================================================
操作格"只读非管理员"给空串      90（应为 ``' '`` 一个空格）
资金表美元行用 ``is not None``  40（应为 ``empty()``：``0.0`` 算空）
净值与价格用 Python 字符串比较   5（应为 PHP 松散比较：数字串按数值比）
============================  ================================================

**注入时的坑**（与 ``_php_portfolio_compare.py`` 同一个）：monkeypatch 要打在
**绑定该名字的模块**上。``money_service`` 是 ``from core.stockgroup import
php_truthy``，所以改 ``core.stockgroup.php_truthy`` 对
``build_python_cases()`` **不生效** —— 得改 ``money_service.php_truthy``。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent          # python/auto/_scratch → 仓库根
sys.path.insert(0, str(HERE.parent))      # python/auto
sys.path.insert(0, str(HERE.parent.parent))  # python

from web.bootstrap import install_paths  # noqa: E402

install_paths()

import re  # noqa: E402

from core.stockgroup import MultiCurrency, MyStockGroup, StockGroup  # noqa: E402
from core.stocktrans import MyStockTransaction  # noqa: E402
from ref.stocklink import (  # noqa: E402
    get_delete_link,
    get_edit_link,
    get_my_stock_group_link,
    get_on_click_link,
    get_transaction_link,
    stock_get_all_transaction_link,
)
from sql.historytable import format_number_display  # noqa: E402
from web.services.money_service import (  # noqa: E402
    ADMIN_EMAIL,
    DISP_ALL_CN,
    build_money_group_rows,
    build_money_row,
)
from web.services.transaction_service import build_transaction_row  # noqa: E402


#: PHP 的 ``<font color=grey>0</font>`` —— 颜色归 CSS，比对前剥掉。
_FONT_RE = re.compile(r"<font color=\w+>(.*?)</font>")


def strip_font(value):
    """把 PHP 的 ``<font>`` 剥掉。递归处理列表 / 字典。"""
    if isinstance(value, str):
        return _FONT_RE.sub(r"\1", value)
    if isinstance(value, list):
        return [strip_font(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_font(item) for key, item in value.items()}
    return value


def normalize(value):
    """PHP → Python 的等价归一：``false``→``''``，再剥 ``<font>``。

    要**递归**进列表：日期格在 PHP 里是 ``false``（``GetSqlTransactionDate()``
    找不到空格），而它嵌在 ``cells`` 列表里 —— 只在顶层判假会漏掉。
    """
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if value is False or value is None:
        return ""
    return strip_font(value)


# ---------------------------------------------------------------------------
# 用例（与 _php_transaction_check.php 逐项对齐）
# ---------------------------------------------------------------------------

SYMBOL_OF = {"11": "SH600104", "12": "SPY", "13": "HK00700", "99": "KWEB"}


class _Ref:
    """引用对象替身，与 PHP 侧的 ``FakeRef`` 一一对应。

    ``GetPriceDisplay()`` 固定成 ``sprintf('%.*f')``：于是对照的是
    **段落传进去的参数**，而不是引用对象自己的着色逻辑。
    """

    def __init__(self, symbol, stock_id="0", display=None, price="0",
                 is_fund=False, precision=2, is_a=False, is_h=False,
                 future_cn=False):
        self._symbol = symbol
        self._stock_id = stock_id
        self._display = display
        self._price = price
        self._is_fund = is_fund
        self._precision = precision
        self._is_a = is_a
        self._is_h = is_h
        self._future_cn = future_cn

    def get_symbol(self):
        return self._symbol

    def get_stock_id(self):
        return self._stock_id

    def get_display(self):
        return self._display if self._display else self._symbol

    def get_price(self):
        return self._price

    def is_fund_a(self):
        return self._is_fund

    def get_precision(self):
        return self._precision

    def is_symbol_a(self):
        return self._is_a

    def is_symbol_h(self):
        return self._is_h

    def is_sina_future_cn(self):
        return self._future_cn

    def get_price_display(self, disp=None, prev=None, precision=None):
        digits = precision if precision is not None else self._precision
        return ("%." + str(int(digits)) + "f") % disp


REF_CASES = {
    "us": _Ref("KWEB", "11", None, "33.80", False, 2),
    "fund_a": _Ref("SZ161125", "12", None, "1.234", True, 3),
    "future": _Ref("hf_ES", "13", "ES", "4200.0", False, 2, False, False, True),
    "hk": _Ref("HK00700", "14", None, "380.0", False, 2, False, True),
    "qdii": _Ref("SZ161125", "12", None, "1.234", False, 2),
}

RECORD_CASES = {
    "plain": {"id": "1", "filled": "2026-10-13 09:30:00", "quantity": "100",
              "price": "1.2340", "fees": "5.00", "remark": ""},
    "remark": {"id": "2", "filled": "2026-10-14 10:15:00", "quantity": "-50",
               "price": "33.50", "fees": "0", "remark": "打新"},
    "order_same": {"id": "3", "filled": "2026-10-15 11:00:00", "quantity": "200",
                   "price": "1.2340", "fees": "12.5", "remark": "申购 3"},
    "order_diff": {"id": "4", "filled": "2026-10-15 11:00:00", "quantity": "200",
                   "price": "1.234", "fees": "12.5", "remark": "申购 3"},
    "no_space": {"id": "5", "filled": "2026-10-16", "quantity": "10",
                 "price": "2.5", "fees": "0", "remark": "申购"},
    "zero_fees": {"id": "6", "filled": "2026-10-17 09:30:00", "quantity": "1",
                  "price": "0", "fees": "0", "remark": ""},
}

#: 净值桩：QDII 走 prev、其它走 close（PHP 侧两个方法返回同一个值）
NETVALUE_CASES = {"same": "1.234", "diff": "1.300", "none": None}

MONEY_CASES = {
    "normal": (1000.0, 100.0, 34370.0, 1520.0),
    "zero": (0.0, 0.0, 0.0, 0.0),
    "negative": (1000.0, -250.5, 500.0, -120.25),
    "big": (1234567.89, 98765.43, 2345678.9, 123456.78),
}

EMAIL_CASES = {
    "none": False,
    "admin": ADMIN_EMAIL,
    "mix": "mix@palmmicro.com",
    "other": "someone@example.com",
}

GROUP_CASES = {
    "cny_only": {
        "refs": [_Ref("SH600104", "11", None, "10.0", False, 2, True)],
        "items": [{"stock_id": "11", "record": "2", "quantity": "100", "cost": "900.0"}],
    },
    "with_usd": {
        "refs": [_Ref("SH600104", "11", None, "10.0", False, 2, True),
                 _Ref("SPY", "12", None, "470.0")],
        "items": [{"stock_id": "11", "record": "2", "quantity": "100", "cost": "900.0"},
                  {"stock_id": "12", "record": "1", "quantity": "10", "cost": "4500.0"}],
    },
    "with_hkd": {
        "refs": [_Ref("SH600104", "11", None, "10.0", False, 2, True),
                 _Ref("HK00700", "13", None, "40.0", False, 2, False, True)],
        "items": [{"stock_id": "11", "record": "1", "quantity": "100", "cost": "900.0"},
                  {"stock_id": "13", "record": "1", "quantity": "10", "cost": "380.0"}],
    },
    "record_zero": {
        "refs": [_Ref("SH600104", "11", None, "10.0", False, 2, True),
                 _Ref("SPY", "12", None, "470.0")],
        "items": [{"stock_id": "11", "record": "1", "quantity": "100", "cost": "900.0"},
                  {"stock_id": "12", "record": "0", "quantity": "10", "cost": "4500.0"}],
    },
    "all_three": {
        "refs": [_Ref("SH600104", "11", None, "10.0", False, 2, True),
                 _Ref("SPY", "12", None, "470.0"),
                 _Ref("HK00700", "13", None, "40.0", False, 2, False, True)],
        "items": [{"stock_id": "11", "record": "1", "quantity": "100", "cost": "900.0"},
                  {"stock_id": "12", "record": "1", "quantity": "10", "cost": "4500.0"},
                  {"stock_id": "13", "record": "3", "quantity": "50", "cost": "1900.0"}],
    },
    "new_symbol": {
        "refs": [_Ref("SH600104", "11", None, "10.0", False, 2, True)],
        "items": [{"stock_id": "11", "record": "1", "quantity": "100", "cost": "900.0"},
                  {"stock_id": "99", "record": "1", "quantity": "5", "cost": "3000.0"}],
    },
}

#: ``StockGetReference()`` 的替身表（PHP 侧 ``tx_ref_table``）
REF_BUILD_TABLE = {"KWEB": ("99", "33.8", False, False)}

BUCKET_CASES = {
    "a_share": _Ref("SH600104", "11", None, "10.0", False, 2, True),
    "hk": _Ref("HK00700", "13", None, "40.0", False, 2, False, True),
    "us": _Ref("SPY", "12", None, "470.0"),
    "future_cn": _Ref("hf_ES", "14", "ES", "4200.0", False, 2, False, False, True),
    "fx_cnh": _Ref("fx_susdcnh", "15", None, "7.1"),
}

CONVERT_CASES = [
    (100.0, 10.0, 20.0, "7.1", "0.9"),
    (100.0, 10.0, 20.0, False, False),
    (100.0, 0.0, 0.0, "7.1", "0.9"),
    (0.0, 0.0, 0.0, "7.1", "0.9"),
    (-50.0, 10.0, -5.0, "7.1", "0.9"),
    (100.0, 10.0, 20.0, "0", "0.9"),
]


def _ref_builder(symbol):
    stock_id, price, is_a, is_h = REF_BUILD_TABLE[symbol]
    return _Ref(symbol, stock_id, symbol, price, False, 2, is_a, is_h)


# ---------------------------------------------------------------------------
# Python 侧用例
# ---------------------------------------------------------------------------


def build_python_cases() -> dict:
    cases = {}

    # ① 链接助手族
    cases["links|on_click"] = get_on_click_link(
        "/woody/res/submittransaction.php?empty=12", "确认清空备注：申购 3？", "清空")
    cases["links|on_click_quotes"] = get_on_click_link("/x", 'say "hi"', "d")
    cases["links|delete_cn"] = get_delete_link(
        "/woody/res/submittransaction.php?delete=12", "2026-10-13 100交易记录")
    cases["links|delete_us"] = get_delete_link("/x.php?delete=1", "cn", "us str", False)
    cases["links|edit_cn"] = get_edit_link("/woody/res/editstocktransaction", "34")
    cases["links|edit_us"] = get_edit_link("/woody/res/editstocktransaction", "34", False)
    cases["links|all_trans"] = stock_get_all_transaction_link("g7")
    cases["links|my_group"] = get_my_stock_group_link()
    cases["links|my_group_query"] = get_my_stock_group_link("groupid=g7", "分组甲")
    cases["links|trans_link_usd"] = get_transaction_link("g1", False, "$")

    # ② GetNumberDisplay
    for value in (0.0, 0.004, -0.004, 0.006, -0.006, 1.234, -1.234, 1234.5,
                  -0.5, 100.0, 1679328.22):
        cases["number_display|%s" % value] = format_number_display(value)

    # ③ 交易明细行
    for ref_name, ref in REF_CASES.items():
        for rec_name, record in RECORD_CASES.items():
            for read_only, admin in ((True, False), (True, True), (False, False)):
                for nv_name, nv in NETVALUE_CASES.items():
                    row = build_transaction_row(
                        ref, record, read_only=read_only, admin=admin,
                        net_value_of=lambda stock_id, date, prev, _nv=nv: _nv)
                    key = "trans_row|%s|%s|%s|%s|%s" % (
                        ref_name, rec_name, read_only, admin, nv_name)
                    cases[key] = list(row.cells)

    # ④ 资金汇总行
    for case_name, values in MONEY_CASES.items():
        for group_text in ("g", DISP_ALL_CN):
            for email_name, email in EMAIL_CASES.items():
                for member_id in (False, "1001"):
                    row = build_money_row(
                        group_text, values[0], values[1], values[2], values[3],
                        member_email_of=lambda _e=email, _m=member_id: _e if _m else None)
                    key = "money_row|%s|%s|%s|%s" % (
                        case_name, group_text, email_name, "yes" if member_id else "no")
                    cases[key] = list(row.cells)

    # ⑤ 一个分组的三行
    for case_name, spec in GROUP_CASES.items():
        for group_id in ("g1", False):
            for usdcny in ("7.1", False):
                for hkdcny in ("0.9", False):
                    group = MyStockGroup(
                        group_id, spec["refs"], records=spec["items"],
                        symbol_of=lambda stock_id: SYMBOL_OF.get(stock_id, stock_id),
                        ref_builder=_ref_builder)
                    rows = build_money_group_rows(
                        group, usdcny, hkdcny, group_link_of=lambda gid: "分组甲")
                    key = "money_group|%s|%s|%s|%s" % (
                        case_name, group_id if group_id else "all",
                        usdcny if usdcny else "false",
                        hkdcny if hkdcny else "false")
                    cases[key] = [list(row.cells) for row in rows]

    # ⑥ 分组模型：分桶 + 折算
    for case_name, ref in BUCKET_CASES.items():
        group = StockGroup()
        trans = MyStockTransaction(ref, "g1")
        trans.set_value(1, 10, 95.0)
        group.on_stock_transaction(trans)
        group.convert_currency("7.1", "0.9")
        for which, multi in (("amount", group.multi_amount),
                             ("profit", group.multi_profit)):
            for field in ("cny", "hkd", "usd",
                          "convert_cny", "convert_usd", "convert_hkd"):
                cases["group_model|%s|%s|%s" % (case_name, which, field)] = \
                    getattr(multi, "f_" + field)

    # ⑦ MultiCurrency.Convert
    for index, spec in enumerate(CONVERT_CASES):
        multi = MultiCurrency()
        multi.f_cny = spec[0]
        multi.f_usd = spec[1]
        multi.f_hkd = spec[2]
        multi.convert(spec[3], spec[4])
        cases["convert|%d|convert_cny" % index] = multi.f_convert_cny
        cases["convert|%d|convert_usd" % index] = multi.f_convert_usd
        cases["convert|%d|convert_hkd" % index] = multi.f_convert_hkd

    return cases


# ---------------------------------------------------------------------------
# PHP 侧（跑 harness / 读存档）
# ---------------------------------------------------------------------------


def load_reference(reference_path: Path) -> dict:
    """取 PHP 参照：没有存档就现跑 harness。"""
    if reference_path.exists():
        return json.loads(reference_path.read_text(encoding="utf-8"))

    import tempfile

    handle, temp_name = tempfile.mkstemp(suffix=".json", prefix="tx_ref_")
    import os

    os.close(handle)
    temp_path = Path(temp_name)
    try:
        result = subprocess.run(
            ["php", "-n", "-d", "display_errors=stderr",
             str(HERE / "_php_transaction_check.php"), str(temp_path)],
            cwd=str(HERE), capture_output=True)
        if result.returncode != 0:
            sys.stderr.write(result.stderr.decode("utf-8", "replace"))
            raise SystemExit("PHP harness 失败：exit=%d" % result.returncode)
        return json.loads(temp_path.read_text(encoding="utf-8"))
    finally:
        temp_path.unlink(missing_ok=True)


def flatten_php(reference: dict) -> dict:
    """把 PHP 的分组输出摊平成 ``key → value``，与 Python 侧对齐。"""
    cases = {}

    for name, value in reference["links"].items():
        cases["links|%s" % name] = value

    for case in reference["number_display"]:
        # PHP 的 json_encode 把 0.0 写成 0（整数），Python 侧键是 '0.0' ——
        # 两侧都先过一遍 float 再格式化，键才对得上
        cases["number_display|%s" % (float(case["value"]),)] = case["text"]

    for case in reference["trans_row"]:
        cases["trans_row|%s|%s|%s|%s|%s" % (
            case["ref"], case["record"], case["read_only"],
            case["admin"], case["netvalue"])] = case["cells"]

    for case in reference["money_row"]:
        cases["money_row|%s|%s|%s|%s" % (
            case["case"], case["group"], case["email"], case["member"])] = case["cells"]

    for case in reference["money_group"]:
        cases["money_group|%s|%s|%s|%s" % (
            case["case"], case["group_id"], case["usdcny"],
            case["hkdcny"])] = case["rows"]

    for case_name, buckets in reference["group_model"].items():
        for which in ("amount", "profit"):
            for field, value in buckets[which].items():
                cases["group_model|%s|%s|%s" % (case_name, which, field)] = value

    for index, case in enumerate(reference["convert"]):
        cases["convert|%d|convert_cny" % index] = case["convert_cny"]
        cases["convert|%d|convert_usd" % index] = case["convert_usd"]
        cases["convert|%d|convert_hkd" % index] = case["convert_hkd"]

    return {key: normalize(value) for key, value in cases.items()}


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------

#: 刻意偏差（key → 原因）。目前为空 —— 说明两侧完全一致。
KNOWN_DIVERGENCES = {}


def main() -> int:
    reference_path = (Path(sys.argv[1]) if len(sys.argv) > 1
                      else HERE / "_php_transaction_reference.json")

    reference = load_reference(reference_path)
    php_cases = flatten_php(reference)
    python_cases = {key: normalize(value)
                    for key, value in build_python_cases().items()}

    only_php = sorted(set(php_cases) - set(python_cases))
    only_python = sorted(set(python_cases) - set(php_cases))

    mismatches = []
    for key in sorted(set(php_cases) & set(python_cases)):
        if php_cases[key] != python_cases[key]:
            mismatches.append((key, php_cases[key], python_cases[key]))

    known = [item for item in mismatches if item[0] in KNOWN_DIVERGENCES]
    unexpected = [item for item in mismatches if item[0] not in KNOWN_DIVERGENCES]

    print("compared cases : %d" % len(set(php_cases) & set(python_cases)))
    print("php only       : %d" % len(only_php))
    print("python only    : %d" % len(only_python))
    print("mismatches     : %d (%d known, %d unexpected)"
          % (len(mismatches), len(known), len(unexpected)))

    for key in only_php[:20]:
        print("  PHP-ONLY  %s = %r" % (key, php_cases[key]))
    for key in only_python[:20]:
        print("  PY-ONLY   %s = %r" % (key, python_cases[key]))
    for key, php_value, py_value in known:
        print("  KNOWN     %-44s php=%r python=%r" % (key, php_value, py_value))
    for key, php_value, py_value in unexpected[:40]:
        print("  MISMATCH  %-44s php=%r python=%r" % (key, php_value, py_value))

    return 1 if (unexpected or only_php or only_python) else 0


if __name__ == "__main__":
    sys.exit(main())
