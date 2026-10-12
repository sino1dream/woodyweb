"""临时测量：估值对「配对值」与「校准值」的幂次。

## 为什么要测量而不是推导

我为换算公式做了 4 次代数推导，每次都得到一个不同答案（41.35 / 0.00885 /
1.93e-06），因为对 ``_qdii_value`` 内部哪些量是独立的不清楚。

正确做法：**把库改成已知状态，量出估值怎么变**。

方法：

1. 固定基金侧不动，只把中间层校准值 ``cal_m`` 乘以 ``r``；
2. 记录估值变化倍数 ``E(r)``；
3. 对 ``log E = k × log r`` 做一点最小二乘，得到幂次 ``k``；
4. 配对值等价于把 ``cal_m`` 按同方向改（因为两者都通过 ``est_quote_ref`` 进入），
   所以 ``k`` 也直接告诉我们"配对值变了该怎么改 cal_m"。
"""

from __future__ import annotations

import io
import math
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from calc.fundestcalc import build_estimate_calculator  # noqa: E402
from core.stockdata import MySqlFundDataSource  # noqa: E402

FUND = "SZ161130"
MIDDLE = "QQQ"


def estimate(source):
    calculator = build_estimate_calculator(source)
    record = calculator.compute_official(FUND)
    return getattr(record, "value", None)


source = MySqlFundDataSource.from_env()
connection = source.connection
cursor = connection.cursor()

cursor.execute(
    "SELECT id, close FROM calibrationhistory WHERE stock_id = "
    "(SELECT id FROM stock WHERE symbol = %s) ORDER BY `date` DESC LIMIT 1",
    (MIDDLE,),
)
row = cursor.fetchone()
if row is None:
    print("找不到 %s 的校准值" % MIDDLE)
    sys.exit(1)
row_id, original = row["id"], float(row["close"])
print("原始 cal_m = %s（id=%s）" % (original, row_id))
print("")

ratios = [0.5, 0.8, 1.0, 1.25, 1.6]
points = []
print("%-10s %-18s %-14s %s" % ("cal_m 倍数", "cal_m", "估值", "log 比"))
print("-" * 62)
try:
    baseline = None
    for ratio in ratios:
        cursor.execute("UPDATE calibrationhistory SET close = %s WHERE id = %s",
                       (original * ratio, row_id))
        connection.commit()
        value = estimate(source)
        if ratio == 1.0:
            baseline = value
        points.append((ratio, value))
        print("%-10s %-18s %-14s %s" % (
            ratio, round(original * ratio, 8),
            round(value, 6) if value else "-", ""))
finally:
    cursor.execute("UPDATE calibrationhistory SET close = %s WHERE id = %s",
                   (original, row_id))
    connection.commit()
    print("")
    print("已还原 cal_m = %s" % original)

# 最小二乘拟合 log E = k log r
if baseline:
    xs = [math.log(r) for r, _ in points if _]
    ys = [math.log(v / baseline) for _, v in points if v]
    if len(xs) >= 2:
        n = len(xs)
        sx, sy = sum(xs), sum(ys)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, ys))
        denominator = n * sxx - sx * sx
        k = (n * sxy - sx * sy) / denominator if denominator else float("nan")
        print("")
        print("估值对 cal_m 的幂次 k = %.6f" % k)
        print("（k=1 表示线性正比；k=2 表示平方）")
        print("")
        print("=> 配对值变了要改 cal_m，若估值线性依赖配对值，则：")
        print("     cal_m_new = cal_m_old × (pair_old / pair_new)^k")
        print("   取 k=%.4f" % k)

connection.close()
