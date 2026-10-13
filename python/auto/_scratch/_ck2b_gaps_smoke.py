"""CK2b 两个缺口的冒烟：跟踪表与基金溢价记录有没有真的出现在详情页。

跑法（在 ``python/`` 下）::

    D:/python/python.exe -B auto/_scratch/_ck2b_gaps_smoke.py

对照的是 ``_mystock.php:131-136`` 第 2 分支的四段：估值 / 跟踪 / 配对均线 /
基金溢价记录。本轮收的是**第二与第四**段，所以这里专门看它们。

演示库的现状（决定了预期）：

* ``QQQ`` / ``SPY`` 有 ``netvaluehistory`` 与 ``stockhistory`` → 溢价记录有行；
* ``^NDX`` / ``^GSPC`` 有 800 条行情但**没有净值** → 溢价记录整段不出现；
* ``lastcalibration`` 表只有 id=1/2/3 三行（SZ161130 / SZ161125 / SH513100），
  所以中间层标的的「参考值」那格是空的 —— 与 PHP 一致，不是 bug。
"""

from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))  # .../python/auto/_scratch
_AUTO = os.path.dirname(_HERE)                      # .../python/auto
_PYTHON = os.path.dirname(_AUTO)                    # .../python

sys.path.insert(0, _AUTO)
sys.path.insert(0, _PYTHON)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from web.app import create_app  # noqa: E402

SYMBOLS = ["QQQ", "SPY", "^NDX", "^GSPC", "TQQQ", "HF_CL"]


def probe(client, symbol: str) -> dict:
    resp = client.get("/stock/%s" % symbol)
    html = resp.get_data(as_text=True)
    return {
        "code": resp.status_code,
        "fundlist": _table_rows(html, "fundlist-table"),
        "fundhistory": _table_rows(html, "fundhistory-table"),
        "sections": [re.sub(r"<[^>]+>", "", h).strip()
                     for h in re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.S)],
    }


def _table_rows(html: str, css_class: str) -> int:
    """数一张表里的数据行。

    表头那张 ``<thead>`` 不算，所以只数 ``<tbody>`` 里的 ``<tr>``。
    """
    match = re.search(r'<table[^>]*class="[^"]*%s.*?</table>' % css_class, html, re.S)
    if not match:
        return 0
    body = re.search(r"<tbody>(.*?)</tbody>", match.group(0), re.S)
    if not body:
        return 0
    return len(re.findall(r"<tr", body.group(1)))


def main() -> int:
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    print("=== CK2b 缺口冒烟：/stock/<symbol> ===")
    for symbol in SYMBOLS:
        info = probe(client, symbol)
        print("\n%-8s HTTP %s  跟踪表 %d 行  溢价记录 %d 行"
              % (symbol, info["code"], info["fundlist"], info["fundhistory"]))
        for name in info["sections"]:
            if name:
                print("      · %s" % name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
