"""Plan A 收尾核对：本地 fundest[2026-10-15] vs 生产端 qdiicn.php EST 表。

回答两个问题：

1. **日期对齐了吗** —— 生产端 41 行 EST 的日期是不是全部 2026-10-15，
   与我们 fundestcli 写入的 33 条是不是同一天；
2. **值差多少** —— 逐只算偏差，并按阈值分档（≤0.1% / >1%）。

生产端 HTML 快照：``auto/_scratch/_qdiicn_prod_1016.html``（2026-10-16 抓取，
「EST网页链接 共41项」表）。行格式（实测）：

```html
<a href=".../sz161125cn.php">SZ161125</a></td><td class=c1><font color=green>3.155</font></td><td class=c1>2026-10-15</td>...
```

    python -B auto/_scratch/_plan_a_prod_compare.py
"""

from __future__ import annotations

import io
import os
import re
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from calc.fundcalc import qdii_est_symbol_map  # noqa: E402
from core.stockdata import MySqlFundDataSource  # noqa: E402

HTML_PATH = os.path.join(AUTO_DIR, "_scratch", "_qdiicn_prod_1016.html")
TARGET_DATE = "2026-10-15"

# 生产端 EST 表的一行：符号 + 数值 + 日期（后面还有溢价列，不管）。
# 注意 href 里是**小写**（sz161125cn.php），链接文本才是大写符号。
ROW_RE = re.compile(
    r'<a href="[^"]*/(?P<href>[a-z0-9]+)cn\.php">(?P<symbol>[A-Z0-9]+)</a></td>'
    r'<td class=c1><font color=\w+>(?P<est>[\d.]+)</font></td>'
    r'<td class=c1>(?P<date>\d{4}-\d{2}-\d{2})</td>'
)


def parse_production(path):
    """从生产端 HTML 提取 EST 表：{符号: (est, date)}。"""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        html = handle.read()
    result = {}
    for match in ROW_RE.finditer(html):
        result[match.group("symbol")] = (float(match.group("est")), match.group("date"))
    return result


def main() -> int:
    prod = parse_production(HTML_PATH)
    print("生产端 EST 表：%d 行" % len(prod))
    dates = sorted({date for _, date in prod.values()})
    print("生产端日期集合：%s" % dates)
    print("")

    source = MySqlFundDataSource.from_env()
    try:
        cursor = source.connection.cursor()
        try:
            cursor.execute(
                "SELECT s.symbol, f.`date`, f.close FROM fundest f "
                "JOIN stock s ON s.id = f.stock_id WHERE f.`date` = %s "
                "ORDER BY s.symbol", (TARGET_DATE,))
            local = {row["symbol"]: float(row["close"]) for row in cursor.fetchall()}
        finally:
            cursor.close()
    finally:
        source.connection.close()

    print("本地 fundest[%s]：%d 条" % (TARGET_DATE, len(local)))
    print("")

    # 日期对齐：本地这 33 条的日期就是 TARGET_DATE（SQL 已过滤），
    # 关键是生产端那 41 条是不是也全部同一天。
    off_date = {sym: date for sym, (_, date) in prod.items() if date != TARGET_DATE}
    print("生产端日期 != %s 的行：%s" % (TARGET_DATE, off_date or "无"))
    print("")

    print("%-10s %-12s %-12s %-9s %s" % ("符号", "本地EST", "生产EST", "偏差%", "分档"))
    print("-" * 60)
    buckets = {"<=0.1%": 0, "0.1~1%": 0, ">1%": 0}
    worst = []
    for sym in sorted(prod):
        if sym not in local:
            continue
        mine = local[sym]
        theirs, _ = prod[sym]
        diff = (mine / theirs - 1.0) * 100.0
        if abs(diff) <= 0.1:
            bucket = "<=0.1%"
        elif abs(diff) <= 1.0:
            bucket = "0.1~1%"
        else:
            bucket = ">1%"
        buckets[bucket] += 1
        worst.append((abs(diff), sym, mine, theirs))
        print("%-10s %-12.6f %-12s %-+9.3f %s" % (sym, mine, theirs, diff, bucket))
    print("-" * 60)
    print("分档统计（共 %d 只可比）：%s" % (sum(buckets.values()), buckets))
    worst.sort(reverse=True)
    print("偏差最大的三只：")
    for diff, sym, mine, theirs in worst[:3]:
        print("  %-10s 本地 %.6f vs 生产 %s  %+.3f%%" % (sym, mine, theirs, diff))

    # 我们有记录但生产端表里没有的（持仓口径那 8 只不在 qdii 页）
    only_local = sorted(set(local) - set(prod))
    if only_local:
        print("只在本地有 EST 记录的：%s" % only_local)
    only_prod = sorted(set(prod) - set(local))
    print("只在生产端有的（持仓口径等，无 est 符号）：%d 只" % len(only_prod))
    return 0


if __name__ == "__main__":
    sys.exit(main())
