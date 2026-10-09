"""临时工具：清掉中间层在指定日期的校准行，便于重测自动校准。

只动 ``QQQ`` / ``SPY``，只动 ``--date`` 指定的那一天（默认 2026-10-02）。
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from stockdata import MySqlFundDataSource  # noqa: E402

SYMBOLS = ("QQQ", "SPY")
DATE = "2026-10-02"


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    placeholders = ", ".join(["%s"] * len(SYMBOLS))
    cursor.execute(
        "SELECT c.id, s.symbol, c.`date`, c.close FROM calibrationhistory c "
        "JOIN stock s ON s.id = c.stock_id "
        "WHERE s.symbol IN (%s) AND c.`date` = %%s" % placeholders,
        tuple(SYMBOLS) + (DATE,),
    )
    rows = cursor.fetchall()
    print("=== 待清理（%s 在 %s）===" % ("/".join(SYMBOLS), DATE))
    for row in rows:
        print("  id=%-4s %-5s %s  %s" % (row["id"], row["symbol"], row["date"], row["close"]))
    if not rows:
        print("  没有。")
        cursor.close()
        source.connection.close()
        return 0

    if not apply:
        print("\n这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    for row in rows:
        cursor.execute("DELETE FROM calibrationhistory WHERE id = %s", (row["id"],))
    source.connection.commit()
    print("\n已删除 %d 行。" % len(rows))
    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
