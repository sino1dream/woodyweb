"""临时工具：把演示数据表备份成 JSON（重播种前的安全网）。

只备份会被 ``seed_demo_data.py`` 覆盖的表：
``stock`` / ``stockhistory`` / ``netvaluehistory`` / ``calibrationhistory`` /
``fundposition`` / ``lastcalibration`` / ``fundpair`` / ``realtimequote`` / ``estquote``。

还原用同目录的 ``_restore_demo_tables.py``。
"""

from __future__ import annotations

import datetime
import io
import json
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

TABLES = (
    "stock",
    "stockhistory",
    "netvaluehistory",
    "calibrationhistory",
    "fundposition",
    "lastcalibration",
    "fundpair",
    "realtimequote",
    "estquote",
    "futurespair",
)

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_demo_backup.json")


def jsonable(value):
    """把 datetime/date/timedelta/Decimal 之类转成可序列化的形式。"""
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return {"__type__": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, datetime.timedelta):
        return {"__type__": "timedelta", "value": value.total_seconds()}
    if isinstance(value, bytes):
        return {"__type__": "bytes", "value": value.decode("utf-8", errors="replace")}
    return value


def main() -> int:
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    backup = {}
    for table in TABLES:
        try:
            cursor.execute("SELECT * FROM `%s`" % table)
            rows = cursor.fetchall()
        except Exception as exc:
            print("  跳过 %-20s（%s）" % (table, type(exc).__name__))
            continue
        backup[table] = [
            dict((key, jsonable(value)) for key, value in dict(row).items())
            for row in rows
        ]
        print("  已备份 %-20s %d 行" % (table, len(backup[table])))

    with io.open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(backup, handle, ensure_ascii=False, indent=1)

    print("")
    print("写入 %s（%d 字节）" % (OUTPUT, os.path.getsize(OUTPUT)))
    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
