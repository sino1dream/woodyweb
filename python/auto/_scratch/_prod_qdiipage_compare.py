# -*- coding: utf-8 -*-
"""把本地 /qdii 与生产端 qdiicn.php 做逐格结构对照。

为什么值得单独一个脚本：本次迁移的判断标准是**生产端**（本地 PHP 仓库是旧
快照，QDII 符号只有 33 个，生产端已是 41 个），所以"对照 PHP 源码"不够，
必须对照**页面输出**。脚本抓生产端 HTML 与本地渲染结果，比三样东西：

1. 两张表的符号顺序（必须逐位相同）；
2. 两张表的列数与表头文字；
3. 首格链接的形状（表1 是新浪外链、表2 是站内详情页）。

**不比数值**：演示库只有 14 个行情符号，生产端有全部 41 个，逐格比数字
只会得到一堆无意义差异。要比数字请用生产端同一时刻的数据灌库后再跑。

用法::

    python _prod_qdiipage_compare.py            # 抓生产端 + 本地渲染，出报告
    python _prod_qdiipage_compare.py --selftest # 在内存里改坏本地输出，验证抓得到
"""

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
AUTO_DIR = HERE.parents[1]
PYTHON_DIR = HERE.parents[2]
for _path in (str(AUTO_DIR), str(PYTHON_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

PROD_URL = "https://palmmicro.com/woody/res/qdiicn.php"

#: 表1（参考数据）与表2（估值）在两种 HTML 里的容器特征
REFERENCE_MARK = ("data-table reference-table", "参考数据")
EST_MARK = ("data-table fund-est-table", None)

SINA_LINK = re.compile(r"^https://finance\.sina\.com\.cn/fund/quotes/\d+/bc\.shtml$")
STOCK_LINK = re.compile(r"^/woody/res/[a-z0-9^]+cn\.php$")
#: 生产端是绝对 URL，本地是站点绝对路径，所以只比对路径部分
PROD_STOCK_PATH = re.compile(r"/woody/res/[a-z0-9^]+cn\.php(\?.*)?$")

#: 估值表的合法列数。PHP 的 realtime 那一档**没有 else 补空**
#: （``_echoFundEstTableItem``），所以 8 列表头下会出现 6 格的行：
#: 有实时估值的行 8 格，没有的 6 格。fair 那一档反而有 else 补两格空串。
EST_LEGAL_WIDTHS = (6, 8)


def _strip_tags(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def parse_production(path):
    """解析生产端 HTML（PHP 把两个段落放在同一个 <table> 里）。"""
    raw = Path(path).read_text(encoding="utf-8")
    table = re.search(r"<table.*?</table>", raw, re.S)
    if not table:
        raise SystemExit("生产端 HTML 里没有 <table>")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0), re.S)

    reference, est, mode = [], [], None
    for tr in rows:
        cells_raw = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)
        cells = [_strip_tags(c) for c in cells_raw]
        if not cells:
            continue
        if cells[0] == "代码":
            # 表头行：靠列数区分两张表（表1 六列、表2 八列）
            mode = "est" if len(cells) >= 8 else "reference"
            continue
        if not re.match(r"^(SH|SZ)\d{6}$", cells[0]):
            continue
        href = ""
        m = re.search(r'<a href="([^"]+)"', cells_raw[0])
        if m:
            href = m.group(1)
        row = {"symbol": cells[0], "cells": cells, "href": href}
        (est if mode == "est" else reference).append(row)
    return reference, est


def parse_local(html):
    """解析本地 /qdii 渲染结果（两个独立 <table>）。"""
    def one(css_class):
        m = re.search(
            r'<table class="data-table %s"[^>]*>(.*?)</table>' % re.escape(css_class),
            html,
            re.S,
        )
        if not m:
            return []
        body = m.group(1)
        tbody = re.search(r"<tbody>(.*?)</tbody>", body, re.S)
        if not tbody:
            return []
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody.group(1), re.S):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            if not tds:
                continue
            symbol = _strip_tags(tds[0])
            href = ""
            m = re.search(r'<a href="([^"]+)"', tds[0])
            if m:
                href = m.group(1)
            rows.append(
                {
                    "symbol": symbol,
                    "cells": [_strip_tags(td) for td in tds],
                    "href": href,
                }
            )
        return rows

    return one("reference-table"), one("fund-est-table")


def fetch_production():
    """抓生产端页面；抓不到就用上次存下来的副本。"""
    import urllib.request

    cache = HERE.parent / "_qdiicn_prod.html"
    try:
        with urllib.request.urlopen(PROD_URL, timeout=60) as resp:
            raw = resp.read()
        cache.write_bytes(raw)
        print("已抓取生产端：%s（%d 字节）" % (PROD_URL, len(raw)))
    except Exception as err:  # noqa: BLE001 - 离线时退回缓存副本
        if not cache.exists():
            raise SystemExit("抓不到生产端且没有缓存副本：%s" % err)
        print("抓取失败（%s），改用缓存副本 %s" % (err, cache))
    return cache


def render_local():
    """进程内渲染 /qdii，不起服务器。"""
    os.environ.setdefault("WOODY_AUTO_CALIBRATE", "0")
    from web.app import create_app

    client = create_app({"TESTING": True}).test_client()
    response = client.get("/qdii")
    if response.status_code != 200:
        raise SystemExit("本地 /qdii 返回 %s" % response.status_code)
    return response.get_data(as_text=True)


def compare(local, prod, label, expect_link, legal_widths=None, prod_link_pattern=None):
    """比一张表：符号顺序 + 列数 + 首格链接形状。

    ``legal_widths`` 给定时，只要求本地列数是其中某一个（估值表有 6 / 8 两种
    行宽，见 :data:`EST_LEGAL_WIDTHS`）；否则要求与生产端完全相同。
    ``expect_link`` 匹配本地 href。``prod_link_pattern`` 给定时**也**检查
    生产端的 href 形状（只比路径部分，见 :data:`PROD_STOCK_PATH`）。
    """
    problems = []
    local_syms = [r["symbol"] for r in local]
    prod_syms = [r["symbol"] for r in prod]

    if local_syms != prod_syms:
        # 本地数据覆盖不足时会缺行（演示库只有 14 个行情符号，生产端 41 个
        # 全有），所以**不要求行数相等**，只要求本地是生产端的**子序列**：
        # 缺行可以接受，顺序错不能接受。
        # ``s in it`` 会消费迭代器，这是判断子序列的惯用写法。
        remaining = iter(prod_syms)
        if all(symbol in remaining for symbol in local_syms):
            print("    提示：本地 %d 行是生产端 %d 行的子序列（缺 %d 行）—— 数据覆盖差异，非结构问题"
                  % (len(local_syms), len(prod_syms), len(prod_syms) - len(local_syms)))
        else:
            only_local = [s for s in local_syms if s not in prod_syms]
            only_prod = [s for s in prod_syms if s not in local_syms]
            problems.append(
                "符号不一致：本地 %d 个 / 生产端 %d 个；"
                "仅本地有 %s；仅生产端有 %s" % (len(local_syms), len(prod_syms), only_local, only_prod)
            )
            if sorted(local_syms) == sorted(prod_syms):
                problems.append("（集合相同，是顺序问题）")

    for row in local:
        if not expect_link.match(row["href"]):
            problems.append("%s 首格链接形状不对：%r" % (row["symbol"], row["href"]))

    # 只有声明了 prod_link_pattern 才检查生产端的 href 形状 —— 参考数据表的
    # 首格是新浪外链，估值表才是站内详情页，两张表的预期本来就不同。
    if prod_link_pattern is not None:
        for row in prod:
            if not prod_link_pattern.search(row["href"]):
                problems.append(
                    "生产端 %s 首格链接形状不对：%r" % (row["symbol"], row["href"])
                )

    widths = sorted({len(r["cells"]) for r in local})
    prod_widths = sorted({len(r["cells"]) for r in prod})
    if legal_widths is None:
        if widths != prod_widths:
            problems.append("列数不一致：本地 %s / 生产端 %s" % (widths, prod_widths))
    else:
        bad = [w for w in widths if w not in legal_widths]
        if bad:
            problems.append("列数不在合法集合 %s 内：%s" % (legal_widths, bad))
        missing = [w for w in prod_widths if w in legal_widths and w not in widths]
        if missing:
            print("    提示：本地没有 %s 列的行（生产端有）—— 数据覆盖差异，非结构问题" % missing)

    print("[%s] 本地 %d 行 / 生产端 %d 行，列数 %s / %s，链接形状 %s"
          % (label, len(local), len(prod), widths, prod_widths,
             "全部符合" if not any("链接形状" in p for p in problems) else "有异"))
    return problems


def main():
    selftest = "--selftest" in sys.argv

    prod_ref, prod_est = parse_production(fetch_production())
    local_ref, local_est = parse_local(render_local())

    print("生产端：参考数据 %d 行、估值 %d 行" % (len(prod_ref), len(prod_est)))
    print("本地　：参考数据 %d 行、估值 %d 行" % (len(local_ref), len(local_est)))

    problems = []
    problems += compare(local_ref, prod_ref, "参考数据", SINA_LINK)
    problems += compare(
        local_est,
        prod_est,
        "估值",
        STOCK_LINK,
        legal_widths=EST_LEGAL_WIDTHS,
        prod_link_pattern=PROD_STOCK_PATH,
    )

    if selftest:
        print("\n--- selftest：故意打乱本地顺序，验证对照抓得到 ---")
        broken_ref = list(reversed(local_ref))
        broken = compare(broken_ref, prod_ref, "参考数据(打乱)", SINA_LINK)
        if not any("顺序" in p for p in broken):
            print("!! selftest 失败：打乱顺序居然没报出来")
            problems.append("selftest 未抓到顺序问题")
        else:
            print("selftest 通过：抓到了顺序问题")

    print("\n===== 结果 =====")
    if problems:
        for p in problems:
            print("  ✗", p)
        print("共 %d 处差异" % len(problems))
        return 1
    print("  ✓ 符号顺序、列数、首格链接形状与生产端完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
