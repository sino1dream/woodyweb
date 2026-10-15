"""基金详情页结构侦察：本地 /fund/<symbol> 现在渲染哪些段、什么顺序。

与生产端 ``sz161125cn.php``（``php/_qdii.php EchoAll()`` +
``_qdiigroup.php EchoCommonParagraphs()``）逐段对照，用来列对齐清单。

    python -B auto/_scratch/_fund_page_state.py [符号]
"""

from __future__ import annotations

import os
import re
import sys

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
WEB_DIR = os.path.join(PYTHON_DIR, "web")
for _path in (AUTO_DIR, PYTHON_DIR, WEB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import create_app  # noqa: E402

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "SZ161125"


def main() -> int:
    app = create_app()
    client = app.test_client()
    resp = client.get("/fund/%s" % SYMBOL)
    print("GET /fund/%s -> %s" % (SYMBOL, resp.status_code))
    if resp.status_code != 200:
        print(resp.get_data(as_text=True)[:2000])
        return 1
    html = resp.get_data(as_text=True)

    #: 存一份快照，供 grep memo / 逐段文字对照（Windows 下别写 /tmp）
    snapshot = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_fund_%s_local.html" % SYMBOL)
    with open(snapshot, "w", encoding="utf-8") as f:
        f.write(html)
    print("快照 -> %s" % snapshot)

    #: 段落 = <h2> 标题 + 紧随其后的表格 class；没有 h2 的表（杠杆均线们）
    #: 单独按 class 列出，保持出现顺序。
    print()
    print("=== 本地页标题与表格顺序 ===")
    for m in re.finditer(r"<h([12])[^>]*>(.*?)</h\1>|<table class=\"([^\"]+)\"", html, re.S):
        if m.group(2) is not None:
            text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            print("H%s: %s" % (m.group(1), text))
        else:
            print("    TABLE: %s" % m.group(3))

    #: 每张表的列头（第一行 <th>）
    print()
    print("=== 各表列头 ===")
    for m in re.finditer(r"<table class=\"([^\"]+)\"(.*?)</table>", html, re.S):
        cls = m.group(1)
        heads = re.findall(r"<th[^>]*>(.*?)</th>", m.group(2), re.S)
        heads = [re.sub(r"<[^>]+>", "", h).strip() for h in heads]
        if heads:
            print("%-42s %s" % (cls.split()[0], heads))

    #: 行数粗估
    print()
    print("=== 各表数据行数 ===")
    for m in re.finditer(r"<table class=\"([^\"]+)\"(.*?)</table>", html, re.S):
        cls = m.group(1)
        body = re.search(r"<tbody[^>]*>(.*?)</tbody>", m.group(2), re.S)
        if body:
            n = len(re.findall(r"<tr[^>]*>", body.group(1))) - len(
                re.findall(r"<th[^>]*>", body.group(1)) and [1] or [])
            print("%-42s %d 行" % (cls.split()[0], n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
