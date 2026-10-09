"""临时工具：从 akshare 源码里提取外汇/汇率接口。

用途：akshare 要求 Python >= 3.11，本机运行时是 3.7 装不上，所以直接读它的源码，
看它实际调用哪些 HTTP 接口 —— 有价值的接口我们再用 ``requests`` 自己实现
（这也符合项目"汇率源可替换"的设计）。
"""

from __future__ import annotations

import io
import os
import re
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".aksrc", "akshare-1.19.1", "akshare",
)

TARGETS = [
    r"forex\forex_em.py",
    r"currency\currency_safe.py",
    r"currency\currency_china_bank_sina.py",
    r"fx\fx_quote.py",
    r"fx\fx_quote_baidu.py",
    r"currency\currency.py",
]


def read_text(path: str) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            with open(path, encoding=encoding) as handle:
                return handle.read()
        except (UnicodeDecodeError, LookupError):
            continue
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


for relative in TARGETS:
    path = os.path.join(ROOT, relative)
    if not os.path.exists(path):
        print("(缺失) %s" % relative)
        continue
    text = read_text(path)
    print("=" * 78)
    print(relative)
    print("=" * 78)

    urls = re.findall(r"""["'](https?://[^"'\s]+)["']""", text)
    for url in dict.fromkeys(urls):
        print("  URL : %s" % url)

    functions = re.findall(r"^def (\w+)", text, re.M)
    if functions:
        print("  函数: %s" % ", ".join(functions))
    print("")
