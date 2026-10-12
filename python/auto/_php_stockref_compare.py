#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 ``FundReference`` 的四个委托方法与**真实 PHP** 逐例对照。

用法（在 ``python/auto`` 下）::

    D:/python/python.exe -B _php_stockref_compare.py
    D:/python/python.exe -B _php_stockref_compare.py --selftest

## 验的是什么

``php/stock/fundref.php:19`` 那句 ``if ($this->IsFundA())`` 被补上之后，
真正生效的是四个委托方法（``GetSymbol`` / ``GetStockId`` /
``GetPriceDisplay`` / ``GetPercentageDisplay``）。抄漏一个参数页面上看不出来，
所以这里让 PHP 亲口说一遍"转发给谁、参数是什么"。

PHP 侧用 ``_php_stockref_check.php`` 把方法体从源码里切出来跑；Python 侧用
**真的** ``refobj.FundReference`` —— 委托逻辑一个字都不替身。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import refobj  # noqa: E402
from refobj import FundReference  # noqa: E402
import stockref  # noqa: E402
from test_refobj import FakeSource  # noqa: E402

CHECK = os.path.join(HERE, "_php_stockref_check.php")


# ---------------------------------------------------------------------------
# 记账：把"落到 parent 还是 stock_ref"记下来
# ---------------------------------------------------------------------------
CALLS: list = []
_PROBES_INSTALLED = False


def _wrap_owner(owner, name, label):
    """给 ``owner`` 的 ``name`` 方法包一层记账，**原实现照旧执行**。

    PHP 那侧的 ``BaseRef`` 是替身，能随口记一笔；Python 这侧不能那么干 ——
    委托逻辑正是被测对象。所以只在外面包记账，真代码一字不改。
    """
    original = getattr(owner, name)

    def wrapper(self, *args, **kwargs):
        CALLS.append([label] + list(args))
        return original(self, *args, **kwargs)

    wrapper.__name__ = name
    setattr(owner, name, wrapper)


def _install_probes():
    """``get_stock_id`` 是 ``MysqlReference`` 加的，其余三个在 ``StockReference``。

    包在**最靠近 FundReference 的那一层**：``FundReference.get_symbol`` 自己就是
    委托实现，包它会把"走 parent 还是走 stock"的判断也盖掉。

    只装一次：重复包装会让同一个调用被记两遍（内层记一次、外层再记一次）。
    """
    global _PROBES_INSTALLED
    if _PROBES_INSTALLED:
        return
    _PROBES_INSTALLED = True

    for name, label in (("get_symbol", "parent.GetSymbol"),
                        ("get_price_display", "parent.GetPriceDisplay"),
                        ("get_percentage_text", "parent.GetPercentageDisplay")):
        _wrap_owner(stockref.StockReference, name, label)
    _wrap_owner(refobj.MysqlReference, "get_stock_id", "parent.GetStockId")


class StockRefStub:
    """PHP 侧 ``StockRefStub`` 的镜像：返回值格式故意做得一模一样。"""

    def get_symbol(self):
        CALLS.append(["stock.GetSymbol"])
        return "STOCKREF"

    def get_stock_id(self):
        CALLS.append(["stock.GetStockId"])
        return "99"

    def get_price_display(self, disp=None, prev=None, precision=None):
        CALLS.append(["stock.GetPriceDisplay", disp, prev, precision])
        return "stock:" + json.dumps([disp, prev, precision])

    def get_percentage_text(self, divisor=None, dividend=None):
        CALLS.append(["stock.GetPercentageDisplay", divisor, dividend])
        return "stock:" + json.dumps([divisor, dividend])


# ---------------------------------------------------------------------------
# 用例（与 PHP 脚本的 ``$cases`` 一一对应）
# ---------------------------------------------------------------------------
def run_python():
    _install_probes()
    source = FakeSource()
    out = {}

    # case 0：没有 stock_ref —— 也就是补那行**之前**的状态。
    # PHP 那边是 ``$stock_ref = false``；这里构造完再置空，同一个意思。
    ref = FundReference("SZ161130", source=source)
    ref.stock_ref = None
    del CALLS[:]
    out["case.0"] = {
        "symbol": ref.get_symbol(),
        "stock_id": ref.get_stock_id(),
        # 真调用（返回值落不进比对：PHP 那侧的 parent 是替身，返回的是记账串）
        "price": ref.get_price_display(),
        "percentage": ref.get_percentage_text(),
        "stock_ref": "false" if ref.get_stock_ref() is None else "object",
        "calls": list(CALLS),
    }

    # case 1：有 stock_ref —— 四个方法全转发
    ref = FundReference("SZ161130", source=source, stock_ref=StockRefStub())
    del CALLS[:]
    out["case.1"] = {
        "symbol": ref.get_symbol(),
        "stock_id": ref.get_stock_id(),
        "price": ref.get_price_display(),
        "percentage": ref.get_percentage_text(),
        "stock_ref": "false" if ref.get_stock_ref() is None else "object",
        "calls": list(CALLS),
    }

    # case 2：GetPriceDisplay 三参数
    ref = FundReference("SZ161130", source=source, stock_ref=StockRefStub())
    del CALLS[:]
    out["case.2"] = {"ret": ref.get_price_display(3.14159, 3.0, 2), "calls": list(CALLS)}

    # case 3：没有 stock_ref 时参数照样传给 parent
    ref = FundReference("SZ161130", source=source)
    ref.stock_ref = None
    del CALLS[:]
    out["case.3"] = {"ret": ref.get_price_display(3.14159, 3.0, 2), "calls": list(CALLS)}

    # case 4：GetPercentageDisplay 两参数
    ref = FundReference("SZ161130", source=source, stock_ref=StockRefStub())
    del CALLS[:]
    out["case.4"] = {"ret": ref.get_percentage_text(1.0, 0.5), "calls": list(CALLS)}

    return out


def run_reference():
    raw = subprocess.check_output(["php", "-d", "error_reporting=0", CHECK],
                                  cwd=HERE).decode("utf-8", "replace")
    # PHP 的 openssl 警告也打在 stdout 上，从第一个 '{' 开始解析
    return json.loads(raw[raw.find("{"):])


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------
def _norm(value):
    """PHP 的 ``false`` 与 Python 的 ``None`` 是同一个意思（"没传"）。"""
    if value is None or value is False:
        return "-"
    if isinstance(value, float):
        return round(value, 6)
    return value


def _norm_calls(calls):
    return [[c[0]] + [_norm(v) for v in c[1:]] for c in calls]


def compare(reference):
    """返回 ``(不一致清单, 未比对清单)``。"""
    values = run_python()
    unexpected = []
    missing = []

    for key in sorted(reference.get("cases", {})):
        expected = reference["cases"][key]
        if key not in values:
            missing.append(key)
            continue
        got = values[key]

        if _norm_calls(expected["calls"]) != _norm_calls(got["calls"]):
            unexpected.append((key + ".calls", _norm_calls(expected["calls"]),
                               _norm_calls(got["calls"])))
            continue

        # 返回值只在**落到 stock_ref** 时才可比 —— PHP 那侧的 parent 是替身，
        # 它返回的是记账串，而 Python 的 parent 是真实现（返回格式化好的价格）。
        if any(name.startswith("parent.") for name in [c[0] for c in expected["calls"]]):
            continue
        for field in ("symbol", "stock_id", "price", "percentage", "ret"):
            if field in expected and _norm_ret(expected[field]) != _norm_ret(got[field]):
                unexpected.append((key + "." + field, expected[field], got[field]))

    for key in values:
        if key not in reference.get("cases", {}):
            missing.append(key)
    return unexpected, missing


def _norm_ret(text):
    """``stock:[3.14159,3,2]`` 这种串要**按结构**比，不能按字符串比。

    PHP 的 ``json_encode`` 不给空格、把 ``3.0`` 写成 ``3``、把"没传"写成
    ``false``；Python 这边是 ``null`` 与带空格 —— 逐字符比会全是假差异。
    """
    if not isinstance(text, str):
        return text
    marker = text.find(":[")
    if marker < 0:
        return text
    try:
        args = json.loads(text[marker + 1:])
    except ValueError:
        return text
    return (text[:marker], [_norm(value) for value in args])


def selftest():
    """**故意**改坏四处，验证这套对照真的能抓出差错。

    一个只会打印绿勾的对照脚本比没有更糟 —— 它会让人以为已经验过了。
    """
    caught = []
    reference = run_reference()

    original_symbol = FundReference.get_symbol
    original_price = FundReference.get_price_display
    original_pct = FundReference.get_percentage_text
    original_build = FundReference._build_stock_ref

    # 1. 委托整个不生效：永远走 parent
    def no_delegate(self):
        return stockref.StockReference.get_symbol(self)

    FundReference.get_symbol = no_delegate
    caught.append("委托方向" if compare(reference)[0] else None)
    FundReference.get_symbol = original_symbol

    # 2. GetPriceDisplay 少传一个参数（三参数抄成两参数）
    def short_args(self, disp=None, prev=None, precision=None):
        if self.stock_ref is not None:
            return self.stock_ref.get_price_display(disp, prev)
        return stockref.StockReference.get_price_display(self, disp, prev, precision)

    FundReference.get_price_display = short_args
    caught.append("参数个数" if compare(reference)[0] else None)
    FundReference.get_price_display = original_price

    # 3. 百分比两个参数传反
    def swapped(self, divisor=None, dividend=None):
        if self.stock_ref is not None:
            return self.stock_ref.get_percentage_text(dividend, divisor)
        return stockref.StockReference.get_percentage_text(self, divisor, dividend)

    FundReference.get_percentage_text = swapped
    caught.append("参数顺序" if compare(reference)[0] else None)
    FundReference.get_percentage_text = original_pct

    # 4. 那一行又掉了：A 股基金也不建 stock_ref
    FundReference._build_stock_ref = lambda self, symbol: None
    caught.append("自动建" if _auto_build_broken() else None)
    FundReference._build_stock_ref = original_build

    return [name for name in caught if name]


def _auto_build_broken():
    """``_build_stock_ref`` 被改坏时，真构造的 A 股基金不该再有 stock_ref。"""
    return FundReference("SZ161130", source=FakeSource()).get_stock_ref() is None


def main():
    if "--selftest" in sys.argv:
        names = ["委托方向", "参数个数", "参数顺序", "自动建"]
        caught = selftest()
        print("自检：故意改坏 %d 处，看对照能不能抓到\n" % len(names))
        for name in names:
            print("  %-10s %s" % (name, "注入错误已被抓到 ✓" if name in caught
                                  else "注入错误**没被抓到** ✗"))
        print("\n抓到 %d / %d" % (len(caught), len(names)))
        return 1 if len(caught) != len(names) else 0

    reference = run_reference()
    unexpected, missing = compare(reference)

    print("PHP 分支语义确认：")
    for key in sorted(reference.get("traps", {})):
        print("  %-24s -> %s" % (key, reference["traps"][key]))
    print()

    print("逐例对照（%d 例）：" % len(reference.get("cases", {})))
    for key in sorted(reference["cases"]):
        print("  %-8s %s" % (key, reference["cases"][key]["name"]))

    print("\n不一致 %d 项，未比对 %d 项" % (len(unexpected), len(missing)))
    for key, expected, got in unexpected:
        print("  ✗ %s\n      PHP    %s\n      Python %s" % (key, expected, got))
    for key in missing:
        print("  ? %s 没有比对上" % key)
    return 1 if unexpected or missing else 0


if __name__ == "__main__":
    sys.exit(main())
