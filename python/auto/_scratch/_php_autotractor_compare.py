#!/usr/bin/env python
"""拖拉机自动化说明页的 PHP 逐段对照。

## 比什么

这一页没有数字，比的是**文案与顺序**。``_php_autotractor_check.php`` 把
``EchoAll()`` 整段从源码抠出来 eval，吐原始 HTML；本脚本把两边都拆成
同一形状的段落序列，逐个比对：

=====================================  =========================================
段落                                   形状
=====================================  =========================================
``<p>``                                 ``{"kind": "p", "text": ...}``
``<ol>`` / ``<ul>``                     ``{"kind": "list", "ordered": …, "items": [...]}``
``<h3>已知问题</h3>`` + ``<ol>``        ``{"kind": "h3", "text": …}`` + 一个 list
=====================================  =========================================

**标记只用于切段，不进比对**：PHP 侧是 ``<font color=green>``，本版是
``<span class="remark">``（见 ``autotractor_service.py`` 的"刻意偏离"），
两边文字一样、标记不一样，所以 ``text`` 一律先 ``strip_tags`` 再比。

## 跑法

::

    D:/python/python.exe -B auto/_scratch/_php_autotractor_compare.py

需要 ``php`` 在 PATH 上（XAMPP 的 PHP 8）。
"""

from __future__ import annotations

import html as html_module
import json
import re
import subprocess
import sys
from pathlib import Path


SCRATCH_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRATCH_DIR.parents[1]
AUTO_DIR = SCRATCH_DIR.parent

for path in (str(PYTHON_DIR), str(AUTO_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

PHP_PROBE = SCRATCH_DIR / "_php_autotractor_check.php"

#: 顶层段落。``<li>`` 在 ``<ol>`` 里，不会有同名嵌套；``<p>`` 之间也不嵌套，
#: 所以非贪婪就能切对。``<h3>`` 单独一种（PHP ``GetHeadElement()``）。
BLOCK = re.compile(r"<(p|ol|ul|h3)\b[^>]*>(.*?)</\1>", re.S)
LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S)
TAG = re.compile(r"<[^>]+>")

_excluded = 0


def run_php() -> dict:
    """跑探针拿 JSON。stdout 只放 JSON，错误在 stderr。"""
    result = subprocess.run(
        ["php", "-d", "display_errors=stderr", str(PHP_PROBE)],
        capture_output=True,
        cwd=str(SCRATCH_DIR),
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP 探针失败（exit %d）" % result.returncode)
    # XAMPP 的 php 会在 stderr 打一条 "openssl already loaded"，与本次无关
    return json.loads(result.stdout.decode("utf-8"))


def plain(text: str) -> str:
    """去标记、反转义、压空白 —— ``<font>`` 与 ``<span>`` 的差异在这里抹平。"""
    text = TAG.sub("", text)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def php_blocks(html: str) -> list:
    """把 PHP 吐的 HTML 切成段落序列。"""
    blocks = []
    for tag, inner in BLOCK.findall(html):
        if tag == "p":
            blocks.append({"kind": "p", "text": plain(inner)})
        elif tag == "h3":
            blocks.append({"kind": "h3", "text": plain(inner)})
        else:
            blocks.append({
                "kind": "list",
                "ordered": tag == "ol",
                "items": [plain(item) for item in LI.findall(inner)],
            })
    return blocks


def python_blocks(page: dict) -> list:
    """把我们自己的 ``blocks`` 摊成同一形状。"""
    blocks = []
    for block in page["blocks"]:
        if block["type"] == "paragraph":
            blocks.append({"kind": "p", "text": plain(block["html"])})
        elif block["type"] == "list":
            blocks.append({
                "kind": "list",
                "ordered": bool(block["ordered"]),
                "items": [plain(item) for item in block["items"]],
            })
        elif block["type"] == "bugs":
            blocks.append({"kind": "h3", "text": block["heading"]})
            blocks.append({
                "kind": "list",
                "ordered": True,
                "items": [plain(item) for item in block["items"]],
            })
    return blocks


def compare_block(index: int, php: dict, py: dict) -> bool:
    """比一段。返回 True 表示一致。"""
    if php["kind"] != py["kind"]:
        print("  FAIL [%d] 段落类型不同：PHP=%s PY=%s" % (index, php["kind"], py["kind"]))
        return False
    if php["kind"] in ("p", "h3"):
        if php["text"] != py["text"]:
            print("  FAIL [%d] %s 文本不同" % (index, php["kind"]))
            print("       PHP: %s" % php["text"])
            print("       PY : %s" % py["text"])
            return False
        return True

    if php["ordered"] != py["ordered"]:
        print("  FAIL [%d] 列表有序性不同：PHP=%s PY=%s"
              % (index, php["ordered"], py["ordered"]))
        return False
    if len(php["items"]) != len(py["items"]):
        print("  FAIL [%d] 列表条数不同：PHP=%d PY=%d"
              % (index, len(php["items"]), len(py["items"])))
        for i in range(max(len(php["items"]), len(py["items"]))):
            a = php["items"][i] if i < len(php["items"]) else "<缺>"
            b = py["items"][i] if i < len(py["items"]) else "<缺>"
            if a != b:
                print("       [%d] PHP: %s" % (i, a))
                print("       [%d] PY : %s" % (i, b))
        return False
    ok = True
    for i, (a, b) in enumerate(zip(php["items"], py["items"])):
        if a != b:
            print("  FAIL [%d] 第 %d 条不同" % (index, i))
            print("       PHP: %s" % a)
            print("       PY : %s" % b)
            ok = False
    return ok


def main() -> int:
    from web.bootstrap import install_paths

    install_paths()
    from web.services.autotractor_service import build_page

    data = run_php()
    page = build_page()

    php = php_blocks(data["html"])
    py = python_blocks(page)

    passed = 0
    failed = 0

    # 1. 标题与 meta description
    print("== 1. 标题 / meta description ==")
    for name, a, b in (
        ("title", data["title"], page["title"]),
        ("meta description", data["meta"], page["meta_description"]),
    ):
        if a == b:
            print("  OK   %s" % name)
            passed += 1
        else:
            print("  FAIL %s\n       PHP: %r\n       PY : %r" % (name, a, b))
            failed += 1

    # 2. 段落序列
    print("\n== 2. 段落序列（%d 段 vs %d 段）==" % (len(php), len(py)))
    if len(php) != len(py):
        print("  FAIL 段数不同")
        failed += 1
    for index in range(min(len(php), len(py))):
        if compare_block(index, php[index], py[index]):
            passed += 1
        else:
            failed += 1

    # 3. 条目总数（一段一条地比完之后再给个总数，便于看规模）
    php_items = sum(len(b["items"]) for b in php if b["kind"] == "list")
    py_items = sum(len(b["items"]) for b in py if b["kind"] == "list")
    print("\n== 3. 列表条目总数 ==")
    if php_items == py_items:
        print("  OK   两边都是 %d 条" % php_items)
        passed += 1
    else:
        print("  FAIL PHP=%d PY=%d" % (php_items, py_items))
        failed += 1

    print("\n" + "-" * 60)
    print("通过 %d，失败 %d" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
