"""清掉 PHP 探针误写进**真表** ``calibrationhistory`` 的三行（一次性）。

背景：``_php_dailyclose_write_check.php`` 里的 ``ProbeCalibrationSql`` 忘了覆写
构造函数，而 PHP 的 ``CalibrationSql::__construct()`` 是**无参数**的、多余实参
被静默忽略 —— 于是探针把 ``stock_id=1``（SZ161130）的 2026-09-27 / 09-29 /
09-30 三行写进了真表（id 419/420/421，close 200/50/60，time 08:48:52）。

判据：写之前 ``calibrationhistory`` 是 325 行，现在是 328 行，多出来的正好这
三条；这三条之前不存在（探针的第一条是 INSERT，其余全是 UPDATE 它）。

脚本默认 dry-run，``--apply`` 才删；删之前先把这三行导出到
``dbtools/_snapshots/``。
"""

from __future__ import annotations

import json
import os
import sys

SCRATCH = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRATCH)
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pymysql  # noqa: E402

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402

PROBE_IDS = (419, 420, 421)


def main() -> int:
    apply = "--apply" in sys.argv
    config = mysql_config_from_env(load_mysql_env(os.environ))
    connection = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **config)
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT COUNT(*) AS c FROM calibrationhistory")
        total = int(cursor.fetchone()["c"])
        cursor.execute(
            "SELECT id, stock_id, `date`, close, `time`, num FROM calibrationhistory "
            "WHERE id IN %s ORDER BY id",
            (PROBE_IDS,),
        )
        rows = cursor.fetchall()
        print("calibrationhistory 总行数: %d" % total)
        for row in rows:
            print("  %s" % row)
        if len(rows) != len(PROBE_IDS):
            print("命中 %d 行，与预期 %d 行不符，停止" % (len(rows), len(PROBE_IDS)))
            return 1

        snapshot_dir = os.path.join(PYTHON_DIR, "dbtools", "_snapshots")
        os.makedirs(snapshot_dir, exist_ok=True)
        path = os.path.join(snapshot_dir, "calibrationhistory_probe_rows.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([{k: str(v) for k, v in row.items()} for row in rows], handle, ensure_ascii=False, indent=2)
        print("已导出待删行: %s" % path)

        if not apply:
            print("dry-run：没有删。确认后加 --apply")
            return 0

        cursor.execute("DELETE FROM calibrationhistory WHERE id IN %s", (PROBE_IDS,))
        connection.commit()
        cursor.execute("SELECT COUNT(*) AS c FROM calibrationhistory")
        print("删除后总行数: %d（应为 %d）" % (int(cursor.fetchone()["c"]), total - len(rows)))
        return 0
    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
