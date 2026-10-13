"""CK2b 冒烟：``/stock/<symbol>`` 对 fundpair 类符号有没有多出表来。

跑法（在 ``python/`` 下）::

    D:/python/python.exe -B auto/_scratch/_ck2b_smoke.py

对比 CK3 的预期：``fundpair`` 表里有记录的符号（``^NDX`` / ``QQQ`` / ``SPY`` /
``TQQQ`` / ``^GSPC``）应当落到第 2 分支，页面上多出**估值**表；其余符号走
第 4 分支，只有历史/均线。
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

SYMBOLS = [
    # fundpair 表里有记录 → 应当走第 2 分支
    "^NDX", "^GSPC", "QQQ", "TQQQ", "SPY",
    # 没有配对记录 → 第 4 分支
    "FX_SUSDCNY", "hf_CL",
]


def probe(client, svc, symbol: str):
    """先问服务层落在哪个分支，再看页面渲染出什么。

    **分支判定不能靠 HTML** —— 页面上有没有某张表取决于数据齐不齐
    （``TQQQ`` 有 ``fundpair`` 记录、走的是第 2 分支，但它的 EST 表是空的），
    拿"有没有表"反推分支会把这种情况误报成第 4 分支。
    """
    try:
        detail = svc.get_fund_pair_detail(symbol)
    except Exception as exc:  # 服务层异常必须暴露出来，不能静默降级
        return {"branch": "EXC", "sections": [], "error": "%s" % exc}

    branch = "第2分支(fundpair)" if detail is not None else "第4分支(else)"

    resp = client.get("/stock/%s" % symbol)
    html = resp.get_data(as_text=True)
    sections = []
    for heading in re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.S):
        text = re.sub(r"<[^>]+>", "", heading).strip()
        if text:
            sections.append(text)
    values = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    return {
        "branch": branch,
        "code": resp.status_code,
        "sections": sections,
        "est_rows": len(re.findall(r'class="[^"]*est-table', html)),
        "h1": values[0].strip() if values else "",
    }


def main():
    from core.stockdata import MySqlFundDataSource
    from web.services.stock_detail_service import StockDetailService

    source = MySqlFundDataSource.from_env()
    svc = StockDetailService(source=source)

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    print("=== CK2b 冒烟：/stock/<symbol> 的分支判定 ===")
    for symbol in SYMBOLS:
        info = probe(client, svc, symbol)
        print("\n%-12s %s  HTTP %s" % (symbol, info["branch"], info.get("code")))
        if info["sections"]:
            for name in info["sections"]:
                print("      · %s" % name)
        else:
            print("      （没有任何表）")
    source.connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
