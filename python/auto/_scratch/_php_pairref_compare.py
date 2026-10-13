#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""``MyPairReference`` 的 ``cny_ref`` 推导：Python 侧逐例对照 PHP。

用法（工作目录 ``python/auto``）::

    D:/python/python.exe -B _scratch/_php_pairref_compare.py

## 对照什么

``python/auto/ref/refobj.py`` 的 ``PairReference._build_cny_ref`` 对应
``php/stock/fundpairref.php:16-43`` 那段 ``if / else if`` 分支表。它决定
"A→H 用 HKCNY、A→沪B 用 USCNY、美股配对 A 用 fx_susdcnh ……" 这套汇率口径。

这是 CK2 里**最容易静默出错**的一处：分支顺序抄错、或把两条赋值路径
（``CnyReference`` vs ``MyStockReference``）当成同一个类，都不会抛异常，
只是汇率口径不对，页面照样出得来。

## 怎么对照

PHP 侧（``_php_pairref_check.php``）**不手抄**分支：它按行从 ``fundpairref.php``
抠出分支体贴进替身类，符号分类则用 ``require`` 进来的真实 ``StockSymbol``。
本脚本跑 PHP 拿参考值，再用**同一份用例矩阵**驱动 ``PairReference``，逐例比对
两样东西：

1. **汇率符号**（``USCNY`` / ``HKCNY`` / ``fx_susdcnh`` / ... / 无）；
2. **建出来的类**（``CnyReference`` / ``MyStockReference``）—— 见下。

## 为什么连"类"一起比

PHP 两条路径 ``new`` 的是两个不同的类：中间价走 ``CnyReference``，
离岸外汇 ``fx_*`` 走 ``MyStockReference``（当股票行情取的外汇符号）。
Python 侧对应 ``refobj.CnyReference`` 与 ``refobj.MysqlReference``。
只比符号名的话，"符号对了但类错"照样通过。

.. warning::
   ``CnyReference`` 是 ``MysqlReference`` 的子类，所以判定顺序必须是
   **先** ``CnyReference`` **后** ``MysqlReference``。写反了全是 CnyReference。

## 用例矩阵里那几例是专门挑的

* ``SH999999``：PHP 的 ``_isDigitShangHaiB`` 判的是
  ``900000 <= 数字 < 1000000``，不是字符串前缀 ``900``。这一例（配对侧 999999）
  专门钉住"区间"而不是"前缀" —— 迁的时候按前缀实现会在这里漏掉；
* ``00700`` 配 ``SH600519``：PHP 里 ``else if ($this->IsSymbolH())`` 那行是
  **注释掉的死分支**，H 对 A 拿不到汇率；
* ``znb_SENSEX``：走 ``else if ($strPair == ...)``，且**不看**本符号是什么；
* ``^GSPC``：落到最后那个 ``else``，只有本符号是 A 才给 ``USCNY``。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ref import refobj                                    # noqa: E402
from test_pairref import FakeSource                       # noqa: E402

#: PHP 解释器。与 ``_php_pairref_check.php`` 头部注释里的调用一致。
PHP_EXE = r"D:\xampp\php\php.exe"

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_PHP = os.path.join(HERE, "_php_pairref_check.php")


def kind_of(cny):
    """把 Python 侧的汇率引用映射成 PHP 的类名（供比对）。"""
    if cny is None:
        return ""
    # CnyReference 是 MysqlReference 的子类 —— 必须先判子类
    if isinstance(cny, refobj.CnyReference):
        return "CnyReference"
    if isinstance(cny, refobj.MysqlReference):
        return "MyStockReference"
    return type(cny).__name__


def run_php():
    """跑 PHP 参考脚本，返回解析后的用例列表。"""
    proc = subprocess.Popen(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", CHECK_PHP],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=HERE, universal_newlines=True,
    )
    out, err = proc.communicate()
    if proc.returncode != 0:
        sys.stderr.write("PHP 执行失败 (exit=%s)\n%s\n" % (proc.returncode, err))
        raise SystemExit(2)
    if err.strip():
        sys.stderr.write("[php stderr] %s\n" % err.strip())
    try:
        return json.loads(out)["cases"]
    except ValueError:
        sys.stderr.write("PHP 输出不是合法 JSON：\n%s\n" % out[:2000])
        raise SystemExit(2)


def main():
    cases = run_php()
    if not cases:
        sys.stderr.write("PHP 没给出用例\n")
        return 2

    failures = []
    for case in cases:
        self_symbol = case["self"]
        pair_symbol = case["pair"]
        # 分支表与用哪张配对表无关，统一用基类的 ``fundpair`` 驱动
        source = FakeSource(tables={"fundpair": {self_symbol: pair_symbol}})
        ref = refobj.PairReference(self_symbol, source=source)

        cny = ref.get_cny_ref()
        got_symbol = None if cny is None else cny.get_symbol()
        got_kind = kind_of(cny)

        want_symbol = case["cny"] if case["cny"] is not False else None
        want_kind = case["kind"] or ""

        if got_symbol != want_symbol or got_kind != want_kind:
            failures.append({
                "index": case["index"],
                "self": self_symbol,
                "pair": pair_symbol,
                "want_symbol": want_symbol, "got_symbol": got_symbol,
                "want_kind": want_kind, "got_kind": got_kind,
            })

    total = len(cases)
    print("PHP 逐例对照：cny_ref 推导（%d 例）" % total)
    print("-" * 72)
    for case in cases:
        print("  [%2d] %-9s 配对 %-11s -> %-10s %s"
              % (case["index"], case["self"], case["pair"],
                 case["cny"] if case["cny"] is not False else "(无)",
                 case["kind"] or ""))
    print("-" * 72)
    if failures:
        print("差异 %d 处：" % len(failures))
        for item in failures:
            print("  [%2d] %s 配对 %s" % (item["index"], item["self"], item["pair"]))
            print("      符号: PHP=%r  Python=%r" % (item["want_symbol"], item["got_symbol"]))
            print("      类  : PHP=%r  Python=%r" % (item["want_kind"], item["got_kind"]))
        return 1
    print("全部一致：0 差异")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
