"""临时维护：清掉误写到**指数**上的校准值。

## 为什么要清

"实时 EST 少一个假设"：PHP 的 ``DailyCalibration()`` 校准的是**中间层**
（``QQQ``），而它的 ``pair_ref`` 是**指数**（``^NDX``）。
指数只是"被读取的一方"，不该有自己的校准值。

本项目上一轮接期货时，把"期货 / 指数"的比值写到了 ``^NDX`` 自己的
``calibrationhistory`` 上（值 0.370904 / 0.088043）——
那是**误写**：指数不需要校准值，而且那个值还会干扰
"中间层校准"与"期货校准"的语义区分。

本脚本删掉它们。**只删指数**（``^`` 开头），不碰其它任何行。
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "auto"))

from stockdata import MySqlFundDataSource  # noqa: E402


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    cursor.execute(
        "SELECT c.id, s.symbol, c.`date`, c.close FROM calibrationhistory c "
        "JOIN stock s ON s.id = c.stock_id WHERE s.symbol LIKE '^%%' ORDER BY s.symbol"
    )
    rows = cursor.fetchall()
    print("=== 指数上的校准行（应删除）===")
    for row in rows:
        print("  %-8s %s  %s" % (row["symbol"], row["date"], row["close"]))
    print("  共 %d 行" % len(rows))

    if not rows:
        print("没有需要清理的。")
        cursor.close()
        source.connection.close()
        return 0

    if not apply:
        print("\n这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    cursor.execute(
        "DELETE c FROM calibrationhistory c JOIN stock s ON s.id = c.stock_id "
        "WHERE s.symbol LIKE '^%%'"
    )
    source.connection.commit()
    print("\n已删除 %d 行。" % cursor.rowcount)

    print("")
    print("=== 清理后：全部校准行 ===")
    cursor.execute(
        "SELECT s.symbol, c.`date`, c.close FROM calibrationhistory c "
        "JOIN stock s ON s.id = c.stock_id ORDER BY s.symbol, c.`date`"
    )
    for row in cursor.fetchall():
        print("  %-8s %s  %s" % (row["symbol"], row["date"], row["close"]))

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
