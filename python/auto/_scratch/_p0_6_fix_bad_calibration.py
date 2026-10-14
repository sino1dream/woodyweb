"""P0.6 收尾：删掉 SH513100 那条**用场内价当净值**算出来的基金层校准值。

## 这条记录错在哪

    calibrationhistory id=88  SH513100  2026-10-14  83792.206883（01:19:12 写入）

反解它的三个输入（逐个候选比对，只有这一组对得上 1e-11）：

    est = 30039.8261   ^NDX 的实时快照（2026-10-14 13:11）
    cny = 6.6917
    nav = 2.399         ← SH513100 的**场内交易价**，不是净值

而 PHP 的分母是**单位净值** —— ``FundReference::LoadSinaFundData()``
（``php/stock/stockref.php:483``）里 ``$this->strPrice = $ar[1]; // net value``，
``AdjustFactor()`` 的 ``GetPrice()`` 拿的就是它。SH513100 的净值是 **2.0563**
（2026-10-12 发布），与场内价差 **16.7%**（这就是 QDII 的折溢价）。

后果：``GetQdiiValue(est) = est × cny / fFactor``，用错的分母算出来的 EST
会**等于场内价**（2.441）而不是估值（2.056） —— 数量级看着完全正常，
最难发现的那一种错。

## 为什么只删这一条

同一只基金 2026-10-13 那条（99480.521328）是按净值算的，逐位可复现
（^NDX 30569.481 × 6.6917 / 2.0563 = 99480.52），留着。

用法（默认 dry-run，``--apply`` 才删）：

    python -B auto/_scratch/_p0_6_fix_bad_calibration.py [--apply]
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource  # noqa: E402

BAD_ROW_ID = 88
SYMBOL = "SH513100"
#: 正确的分母（净值）与错用的分母（场内价），写死在这里当证据
NAV = 2.0563
PRICE = 2.399
EST = 30039.8261
CNY = 6.6917


def main(argv: list) -> int:
    apply_changes = "--apply" in argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    def query(sql, params=()):
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    try:
        stock_id = source.get_stock_id(SYMBOL)
        print("=== 待删记录 ===")
        rows = query("SELECT id, `date`, close, `time`, num FROM calibrationhistory "
                     "WHERE id = %s", (BAD_ROW_ID,))
        if not rows:
            print("id=%s 不存在（已删过？）" % BAD_ROW_ID)
            return 0
        row = rows[0]
        print("  id=%s %s %s close=%s time=%s num=%s" % (
            row["id"], SYMBOL, row["date"], row["close"], row["time"], row["num"]))
        print("")
        print("=== 证据：反解出的三个输入 ===")
        print("  est=%s  cny=%s  nav=%s  ->  %s" % (
            EST, CNY, PRICE, EST * CNY / PRICE))
        print("  正确的分母是净值 %s（%s 发布）->  %s" % (
            NAV, "2026-10-12", EST * CNY / NAV))
        print("  两者差 %.1f%%（= QDII 的折溢价）" % ((PRICE / NAV - 1.0) * 100.0))
        print("")
        print("=== 该基金剩下的校准记录 ===")
        for item in query("SELECT id, `date`, close FROM calibrationhistory "
                          "WHERE stock_id = %s ORDER BY `date` DESC", (stock_id,)):
            mark = "   <== 待删" if item["id"] == BAD_ROW_ID else ""
            print("  id=%-4s %s  %s%s" % (item["id"], item["date"], item["close"], mark))

        if not apply_changes:
            print("")
            print("这是预演。确认后加 --apply 删除。")
            return 0

        cursor.execute("DELETE FROM calibrationhistory WHERE id = %s", (BAD_ROW_ID,))
        source.connection.commit()
        print("")
        print("已删除 id=%s。" % BAD_ROW_ID)
        print("")
        print("=== 删除后：该基金的校准与官方 EST ===")
        for item in query("SELECT id, `date`, close FROM calibrationhistory "
                          "WHERE stock_id = %s ORDER BY `date` DESC", (stock_id,)):
            print("  id=%-4s %s  %s" % (item["id"], item["date"], item["close"]))
        from calc.fundestcalc import build_estimate_calculator

        calculator = build_estimate_calculator(source)
        official = calculator.compute_official(SYMBOL)
        fair = calculator.compute_fair(SYMBOL)
        realtime = calculator.compute_realtime(SYMBOL)
        print("  官方EST=%s  参考EST=%s  实时EST=%s（生产端 2026-10-14 官方 2.01）" % (
            getattr(official, "value", None), getattr(fair, "value", None),
            getattr(realtime, "value", None)))
    finally:
        cursor.close()
        source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
