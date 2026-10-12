"""临时工具：从 ``_demo_backup.json`` 还原演示数据表。

重播种前用 ``backup_demo_tables.py`` 生成备份，这里按主键 upsert 回去，
并把备份里没有的行删掉（即"还原成备份那一刻的状态"）。

用法（在 ``python`` 目录下执行）：

    python -B dbtools\\restore_demo_tables.py            # 预演
    python -B dbtools\\restore_demo_tables.py --apply    # 真还原
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

BACKUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_demo_backup.json")

#: 备份时用主键定位的表；没有主键的表按全列匹配去重。
REQUIRED = ("stock",)


def decode(value):
    """把备份里的 ``{"__type__": ..., "value": ...}`` 还原成 Python 值。"""
    if isinstance(value, dict) and "__type__" in value:
        kind = value["__type__"]
        raw = value["value"]
        if kind == "datetime":
            return datetime.datetime.fromisoformat(raw)
        if kind == "date":
            return datetime.date.fromisoformat(raw)
        if kind == "time":
            return datetime.time.fromisoformat(raw)
        if kind == "timedelta":
            return datetime.timedelta(seconds=float(raw))
        if kind == "bytes":
            return raw.encode("utf-8")
        return raw
    return value


def main() -> int:
    apply = "--apply" in sys.argv
    if not os.path.exists(BACKUP):
        print("找不到备份文件：%s" % BACKUP)
        return 1

    with io.open(BACKUP, encoding="utf-8") as handle:
        backup = json.load(handle)

    source = MySqlFundDataSource.from_env()
    connection = source.connection
    cursor = connection.cursor()

    print("备份文件：%s" % BACKUP)
    print("")
    for table, rows in backup.items():
        cursor.execute("SELECT COUNT(*) AS n FROM `%s`" % table)
        current = cursor.fetchone()["n"]
        print("  %-20s 备份 %-5d 行，当前 %-5d 行%s" % (
            table, len(rows), current,
            "  ← 需要还原" if current != len(rows) else ""))

    if not apply:
        print("")
        print("这是预演。真正执行请加 --apply。")
        cursor.close()
        connection.close()
        return 0

    print("")
    for table, rows in backup.items():
        cursor.execute("DELETE FROM `%s`" % table)
        if not rows:
            print("  %-20s 清空（备份里就是 0 行）" % table)
            continue
        columns = list(rows[0].keys())
        placeholders = ", ".join(["%s"] * len(columns))
        column_sql = ", ".join("`%s`" % name for name in columns)
        for row in rows:
            values = tuple(decode(row.get(name)) for name in columns)
            cursor.execute(
                "INSERT INTO `%s` (%s) VALUES (%s)" % (table, column_sql, placeholders),
                values,
            )
        print("  %-20s 已还原 %d 行" % (table, len(rows)))
    connection.commit()

    print("")
    print("=== 还原后核对 ===")
    for table, rows in backup.items():
        cursor.execute("SELECT COUNT(*) AS n FROM `%s`" % table)
        print("  %-20s %d 行（应为 %d）" % (table, cursor.fetchone()["n"], len(rows)))

    cursor.close()
    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
