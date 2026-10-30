"""``DailyCloseSql`` / ``CalibrationSql`` 写方法的真实 PHP 逐例对照。

用法（在 ``python/auto`` 下）::

    D:/python/python.exe -B _scratch/_php_dailyclose_write_compare.py

做三件事：

1. 把两组写操作序列写成 JSON（``_dailyclose_write_cases.json``）；
2. 用真实 PHP 跑一遍（``_php_dailyclose_write_check.php``，真 mysqli + 临时表
   ``dcw_probe`` / ``dcw_probe_cal``），拿到每步的返回值与全表快照；
3. 用 Python 的 ``sql.historytable`` 在同一张表、同一序列上跑一遍，逐步比对
   ``(返回值, 全表快照)``。

第一段覆盖 ``DailyCloseSql`` 的四条口径：

* ``InsertDaily`` 不是 upsert（已有同日行返回 false），且**没有**周末闸门；
* ``WriteDaily`` 有行时"值变了才 UPDATE"、无行时"周末不写"；
* ``DeleteByDate`` 返回删除行数；``UpdateDaily`` 按主键 id 改。

第二段覆盖 ``CalibrationSql`` 的 ``num`` 语义（``time`` 是动态值，不比）：

* ``UpdateDaily`` 把 ``num`` **重置为 1**；
* ``WriteDailyAverage`` 才累加平均，且新行**没有**周末闸门。

两侧都建/删同名临时表，跑完不留痕迹。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

SCRATCH = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRATCH)
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pymysql  # noqa: E402

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402
from sql.historytable import CalibrationSql, DailyCloseSql, DailyTimeSql  # noqa: E402

TABLE = "dcw_probe"
TABLE_CAL = "dcw_probe_cal"

DDL = """
CREATE TABLE `%s` (
    `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
    `stock_id` int(10) unsigned NOT NULL,
    `date` date NOT NULL,
    `close` double(13,6) NOT NULL%s,
    PRIMARY KEY (`id`),
    UNIQUE KEY `date` (`date`,`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci
"""

#: 2026-10-03 / 10-04 / 09-27 是周末，2026-09-30、10-01、10-05 是工作日。
DAILY_CASES = [
    {"op": "insert_daily", "date": "2026-09-30", "close": "1.234"},
    {"op": "insert_daily", "date": "2026-09-30", "close": "9.9"},
    {"op": "insert_daily", "date": "", "close": "1.0"},
    {"op": "insert_daily", "date": "2026-10-03", "close": "3.0"},
    {"op": "write_daily", "date": "2026-10-03", "close": "3.5"},
    {"op": "write_daily", "date": "2026-10-01", "close": "2.0"},
    {"op": "write_daily", "date": "2026-10-01", "close": "2.0000001"},
    {"op": "write_daily", "date": "2026-10-01", "close": "2.5"},
    {"op": "write_daily", "date": "2026-10-04", "close": "4.0"},
    {"op": "write_daily", "date": "2026-09-27", "close": "4.0"},
    {"op": "delete_by_date", "date": "2026-10-01"},
    {"op": "delete_by_date", "date": "2026-10-01"},
    {"op": "update_daily", "id": 1, "close": "1.111"},
    {"op": "write_daily", "date": "2026-09-30", "close": "1.111"},
    {"op": "insert_daily", "date": "2026-10-05", "close": None},
]

CAL_CASES = [
    {"op": "insert_daily", "date": "2026-09-30", "close": "100"},
    {"op": "write_daily_average", "date": "2026-09-30", "close": "106"},
    {"op": "write_daily_average", "date": "2026-09-30", "close": "103"},
    {"op": "write_daily", "date": "2026-09-30", "close": "200"},
    {"op": "write_daily_average", "date": "2026-09-29", "close": "50"},
    {"op": "write_daily_average", "date": "2026-09-27", "close": "60"},
    {"op": "update_daily", "id": 1, "close": "9"},
]


class ProbeCalibrationSql(CalibrationSql):
    """把表名换成临时表，方法全部继承（生产用 ``calibrationhistory``）。"""

    def __init__(self, connection):
        DailyTimeSql.__init__(self, connection, TABLE_CAL, extra=("num",))


def _env() -> dict:
    merged = load_mysql_env(os.environ)
    return {key: value for key, value in merged.items() if value}


def _snapshot(connection, table: str, with_num: bool) -> list:
    cursor = connection.cursor()
    try:
        columns = "id, `date`, close, num" if with_num else "id, `date`, close"
        cursor.execute("SELECT %s FROM `%s` ORDER BY `date`, id" % (columns, table))
        rows = []
        for row in cursor.fetchall():
            item = {"date": str(row["date"]), "close": "%.6f" % float(row["close"])}
            if with_num:
                item["num"] = int(row["num"])
            rows.append(item)
        return rows
    finally:
        cursor.close()


def _run_python(connection, table: str, cases: list, with_num: bool, extra: str) -> list:
    cursor = connection.cursor()
    cursor.execute("DROP TABLE IF EXISTS `%s`" % table)
    cursor.execute(DDL % (table, extra))
    cursor.close()

    if with_num:
        target = ProbeCalibrationSql(connection)
    else:
        target = DailyCloseSql(connection, table)

    results = []
    for case in cases:
        op = case["op"]
        if op == "insert_daily":
            value = target.insert_daily(1, case["date"], case.get("close"))
        elif op == "write_daily":
            value = target.write_daily(1, case["date"], case.get("close"))
        elif op == "write_daily_average":
            value = target.write_daily_average(1, case["date"], case.get("close"))
        elif op == "delete_by_date":
            value = target.delete_by_date(1, case["date"])
        elif op == "update_daily":
            value = target.update_daily(case["id"], case.get("close"))
        else:
            raise SystemExit("未知操作: %s" % op)
        results.append(
            {
                "op": op,
                "result": int(value) if isinstance(value, bool) else value,
                "rows": _snapshot(connection, table, with_num),
            }
        )
    return results


def _run_php(env: dict, cases_path: str) -> dict:
    child_env = dict(os.environ)
    child_env.update(env)
    completed = subprocess.run(
        [
            "php",
            "-d",
            "display_errors=stderr",
            os.path.join(SCRATCH, "_php_dailyclose_write_check.php"),
            cases_path,
        ],
        cwd=SCRATCH,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stderr = completed.stderr.decode("utf-8", "replace").strip()
    if stderr:
        print("PHP stderr:")
        print(stderr)
    if completed.returncode != 0:
        raise SystemExit("PHP 退出码 %s" % completed.returncode)
    return json.loads(completed.stdout.decode("utf-8"))


def _report(label: str, cases: list, php_rows: list, python_rows: list) -> int:
    failures = 0
    print("\n== %s（%d 例）==" % (label, len(cases)))
    for index, (left, right) in enumerate(zip(php_rows, python_rows)):
        same = left["result"] == right["result"] and left["rows"] == right["rows"]
        mark = "OK  " if same else "DIFF"
        case = cases[index]
        print(
            "%s #%02d %-18s %-10s php:%s py:%s"
            % (
                mark,
                index + 1,
                case["op"],
                case.get("date") or case.get("id"),
                left["result"],
                right["result"],
            )
        )
        if not same:
            failures += 1
            print("     php rows: %s" % left["rows"])
            print("     py  rows: %s" % right["rows"])
    return failures


def main() -> int:
    env = _env()
    config = mysql_config_from_env(env)

    cases_path = os.path.join(SCRATCH, "_dailyclose_write_cases.json")
    with open(cases_path, "w", encoding="utf-8") as handle:
        json.dump({"daily": DAILY_CASES, "calibration": CAL_CASES}, handle, indent=2)

    php_result = _run_php(env, cases_path)

    connection = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **config)
    try:
        py_daily = _run_python(connection, TABLE, DAILY_CASES, False, "")
        py_cal = _run_python(
            connection, TABLE_CAL, CAL_CASES, True, ", `time` time NOT NULL, `num` int(10) unsigned NOT NULL"
        )
    finally:
        cursor = connection.cursor()
        cursor.execute("DROP TABLE IF EXISTS `%s`" % TABLE)
        cursor.execute("DROP TABLE IF EXISTS `%s`" % TABLE_CAL)
        cursor.close()
        connection.close()

    failures = _report("DailyCloseSql", DAILY_CASES, php_result["daily"], py_daily)
    failures += _report("CalibrationSql", CAL_CASES, php_result["calibration"], py_cal)

    total = len(DAILY_CASES) + len(CAL_CASES)
    print("\n%d 例，%d 处不一致" % (total, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
