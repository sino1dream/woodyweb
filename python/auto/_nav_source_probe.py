"""净值数据源可用性探查工具（开发用）。

用途：评估/切换**基金净值**与**中间层标的**的数据源时，先用它确认哪些源活着、
返回什么格式。对应 PHP ``yahoostock.php`` 的替代评估（见
``python/NAV_SOURCE_PLAN.md``）。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B auto\\_nav_source_probe.py            # 全部检查
    python -B auto\\_nav_source_probe.py --yahoo    # 附带检查 Yahoo 是否可用

检查内容：

1. 深交所官方 LOF/ETF 单位净值（``CATALOGID=1785_child``）
2. 东方财富基金历史净值（``api.fund.eastmoney.com/f10/lsjz``）
3. 新浪基金净值（``hq.sinajs.cn/list=f_<代码>``）
4. 新浪美股/全球指数覆盖（替代原 Yahoo ``^XXX-IV`` 序列的候选）
5. （可选）Yahoo 各端点的可用性
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import requests  # noqa: E402

requests.packages.urllib3.disable_warnings()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36")

# 需要净值的示例基金（深圳 16 号段 LOF + 上海 51 号段 ETF）
SAMPLE_FUNDS = ("161130", "161125", "162411", "513100")

# 原 PHP 通过 Yahoo 取的中间层标的，对应的新浪代码
MIDDLE_LAYER = [
    ("int_nasdaq", "纳斯达克综合"),
    ("int_sp500", "标普500"),
    ("gb_qqq", "QQQ"),
    ("gb_spy", "SPY"),
    ("gb_xop", "XOP"),
    ("gb_inda", "INDA"),
    ("gb_ibb", "IBB"),
    ("gb_uso", "USO"),
]


def get(url, headers=None, timeout=20):
    """GET，失败时返回异常对象（调用方按需处理）。"""
    merged = {"User-Agent": UA}
    if headers:
        merged.update(headers)
    try:
        return requests.get(url, headers=merged, timeout=timeout, verify=False)
    except Exception as exc:
        return exc


def decode(response, prefer=None):
    """按候选编码解码响应体；新浪是 GB18030。"""
    content = response.content
    for encoding in ([prefer] if prefer else []) + ["utf-8", "gb18030"]:
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


def check_szse():
    print("=" * 78)
    print("深交所官方单位净值（CATALOGID=1785_child）")
    print("=" * 78)
    print("注意：该接口 metadata.recordcount 恒为 0（不填），数据在 data 数组里。")
    for code in SAMPLE_FUNDS:
        url = ("https://www.szse.cn/api/report/ShowReport/data?"
               "SHOWTYPE=JSON&CATALOGID=1785_child&TABKEY=tab1&txtDm=%s" % code)
        response = get(url, {"Referer": "https://www.szse.cn/"})
        if isinstance(response, Exception):
            print("  %-8s -> 异常 %s" % (code, type(response).__name__))
            continue
        try:
            payload = response.json()
            rows = payload[0].get("data") or []
        except Exception as exc:
            print("  %-8s -> 解析失败 %s" % (code, exc))
            continue
        if not rows:
            print("  %-8s -> 无数据（该代码可能不在深交所）" % code)
            continue
        first = rows[0]
        print("  %-8s %-14s 净值=%-9s 日期=%s  (共 %d 条)" % (
            code, first.get("security_short_name", ""), first.get("nav_per_share", ""),
            first.get("nav_date", ""), len(rows)))
    print("")


def check_eastmoney():
    print("=" * 78)
    print("东方财富历史净值（api.fund.eastmoney.com/f10/lsjz）")
    print("=" * 78)
    for code in SAMPLE_FUNDS:
        url = ("https://api.fund.eastmoney.com/f10/lsjz?"
               "fundCode=%s&pageIndex=1&pageSize=3" % code)
        response = get(url, {"Referer": "https://fundf10.eastmoney.com/"})
        if isinstance(response, Exception):
            print("  %-8s -> 异常 %s" % (code, type(response).__name__))
            continue
        try:
            rows = response.json().get("Data", {}).get("LSJZList") or []
        except Exception as exc:
            print("  %-8s -> 解析失败 %s" % (code, exc))
            continue
        if not rows:
            print("  %-8s -> 无数据" % code)
            continue
        first = rows[0]
        print("  %-8s 净值=%-9s 累计=%-9s 日期=%-11s 涨跌=%s%%" % (
            code, first.get("DWJZ"), first.get("LJJZ"),
            first.get("FSRQ"), first.get("JZZZL")))
    print("")


def check_sina_fund():
    print("=" * 78)
    print("新浪基金净值（hq.sinajs.cn/list=f_<代码>）")
    print("=" * 78)
    print("字段：名称, 当日净值, 累计净值, 昨日净值, 日期, ?")
    codes = ["f_%s" % code for code in SAMPLE_FUNDS]
    response = get("http://hq.sinajs.cn/list=" + ",".join(codes),
                   {"Referer": "https://finance.sina.com.cn"})
    if isinstance(response, Exception):
        print("  异常 %s" % type(response).__name__)
        print("")
        return
    text = decode(response, prefer="gb18030")
    for code in codes:
        for raw in text.splitlines():
            if "hq_str_%s=" % code in raw:
                payload = raw.split('"')[1] if '"' in raw else ""
                print("  %-10s %s" % (code, payload))
                break
    print("")


def check_middle_layer():
    print("=" * 78)
    print("新浪美股/全球指数覆盖（替代原 Yahoo ^XXX-IV 序列的候选）")
    print("=" * 78)
    symbols = [symbol for symbol, _label in MIDDLE_LAYER]
    response = get("http://hq.sinajs.cn/list=" + ",".join(symbols),
                   {"Referer": "https://finance.sina.com.cn"})
    if isinstance(response, Exception):
        print("  异常 %s" % type(response).__name__)
        print("")
        return
    text = decode(response, prefer="gb18030")
    for symbol, label in MIDDLE_LAYER:
        found = False
        for raw in text.splitlines():
            if "hq_str_%s=" % symbol in raw:
                payload = raw.split('"')[1] if '"' in raw else ""
                print("  %-12s %-14s %s" % (symbol, label, payload[:70]))
                found = True
                break
        if not found:
            print("  %-12s %-14s (无响应)" % (symbol, label))
    print("")


def check_yahoo():
    print("=" * 78)
    print("Yahoo 可用性（原 PHP yahoostock.php 依赖的站点）")
    print("=" * 78)
    targets = [
        ("query1 v7 chart ^QQQ-IV", "https://query1.finance.yahoo.com/v7/finance/chart/%5EQQQ-IV?range=5d&interval=1d"),
        ("query1 v8 chart QQQ", "https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=5d&interval=1d"),
        ("query2 v8 chart QQQ", "https://query2.finance.yahoo.com/v8/finance/chart/QQQ?range=5d&interval=1d"),
        ("finance.yahoo.com/quote/QQQ", "https://finance.yahoo.com/quote/QQQ"),
    ]
    for name, url in targets:
        response = get(url)
        if isinstance(response, Exception):
            print("  %-26s -> 异常 %s" % (name, type(response).__name__))
        else:
            print("  %-26s -> HTTP %s (%d bytes)" % (name, response.status_code, len(response.content)))
    print("")


def main() -> int:
    parser = argparse.ArgumentParser(description="净值数据源可用性探查")
    parser.add_argument("--yahoo", action="store_true", help="附带检查 Yahoo 可用性")
    args = parser.parse_args()

    check_szse()
    check_eastmoney()
    check_sina_fund()
    check_middle_layer()
    if args.yahoo:
        check_yahoo()
    return 0


if __name__ == "__main__":
    sys.exit(main())
