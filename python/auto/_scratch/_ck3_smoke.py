# -*- coding: utf-8 -*-
"""CK3 冒烟：用真实库跑一遍 ``/stock/<symbol>``，看段落有没有真的出来。

## 为什么单测之外还要这一遍

``web/tests/test_stock_detail.py`` 里注入的全是假服务，验的是**分支选得对
不对**；这一遍走真实数据库，验的是**接得上接不上** —— 引用对象建不建得
出来、历史服务取不取得回行、均线算不算得出来。

## 挑符号的讲究

演示库的 ``stock`` 表里只有 26 个符号，其中能落到 ``_mystock.php`` 第 4 分支
（非基金、非 FundPair、非 Holdings）的是外汇、指数、期货这几类。基金符号
（SH513100 / SZ161125 等）归 ``/fund/<symbol>`` 管，不拿来冒烟。

用法：

    cd python/auto && python -B _scratch/_ck3_smoke.py
"""

from __future__ import annotations

import io
import os
import sys

# 用 reconfigure 而不是把 sys.stdout 重新包一层 TextIOWrapper ——
# 后者会留下一个"原 stdout 已被替换但底层 buffer 仍被引用"的对象，
# Flask 在请求收尾时关掉它，后面的 print 就报 "I/O operation on closed file"。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

_HERE = os.path.abspath(__file__)
_AUTO_DIR = os.path.dirname(os.path.dirname(_HERE))          # python/auto
_PYTHON_DIR = os.path.dirname(_AUTO_DIR)                     # python
sys.path.insert(0, _AUTO_DIR)
sys.path.insert(0, _PYTHON_DIR)


#: 落在第 4 分支的符号（外汇 / 指数 / 期货），以及一个肯定不存在的符号
PROBE_SYMBOLS = [
    "fx_susdcny",
    "^NDX",
    "hf_CL",
    "SPY",
    "SZ_NOTEXIST",
]

#: 段落标记：(说明, HTML 里应当出现的片段)
SECTION_MARKS = [
    ("历史价格表", "stockhistory-table"),
    ("均线表", "sma-table"),
    ("AB对比表", "ab-table"),
    ("AH对比表", "ah-table"),
    ("ADR对比表", "adr-table"),
    ("净值价格比较表", "netvalueclose-table"),
    ("份额表", "share-table"),
]


def _probe(client, symbol: str) -> None:
    response = client.get("/stock/%s" % symbol)
    html = response.get_data(as_text=True)
    print("=== /stock/%s -> HTTP %s ===" % (symbol, response.status_code))

    if response.status_code != 200:
        print("  非 200，页面没渲染出来")
        print("")
        return

    for label, mark in SECTION_MARKS:
        print("  %-16s %s" % (label, "有" if mark in html else "—"))
    if "取不到该符号的行情数据" in html:
        print("  (页面走了「取不到数据」分支)")
    print("")


def main() -> int:
    from web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    for symbol in PROBE_SYMBOLS:
        try:
            _probe(client, symbol)
        except Exception as exc:  # noqa: BLE001 - 冒烟脚本，一个符号失败不影响其它
            print("=== /stock/%s 抛异常：%s ===" % (symbol, exc))
            print("")

    return 0


if __name__ == "__main__":
    sys.exit(main())
