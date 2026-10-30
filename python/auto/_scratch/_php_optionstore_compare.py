"""``sql/optionstore.py`` 的真实 PHP 逐例对照。

真 PHP 那边：``_php_optionstore_check.php``（include 真 ``sqlval/sqlint/sqlpair``
与 ``sqldailyclose``，只在最底层的四个执行函数上用等价实现）。
本脚本：生成用例 → 跑 Python 版 → 两边逐操作比"返回值 + 整表快照"。

三张表各自用同一份种子，操作序列一致：

* ``val``   —— ``(id, close)``：``WriteVal`` 的"值没变就不写"闸门；
* ``pair``  —— ``(id, stock_id)``：``WritePair`` 的字符串比较 + 两种删除方向；
* ``daily`` —— 日频表：``DeleteAll($strKeyId)`` 的 ``!== false`` 闸门。

用法::

    cd F:\\woody\\woodyweb\\python\\auto
    D:\\python\\python.exe -B _scratch\\_php_optionstore_compare.py
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (AUTO_DIR, os.path.dirname(AUTO_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from ref.stockref import _php_strval_float  # noqa: E402
from sql.optionstore import PairStore, ValStore  # noqa: E402
from sql.historytable import DailyCloseSql  # noqa: E402
from core.stockdata import load_mysql_env  # noqa: E402


def _env() -> dict:
    """数据库连接参数（与其它对照脚本同一套：``auto/.env`` 打底 + 进程环境覆盖）。"""
    return load_mysql_env(os.environ)

CASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_optionstore_cases.json")

#: ``val`` 段：先播种两行，再走"改值 / 改回同值 / 值差小于阈值 / 新增 /
#: 删不存在 / 删存在"。
#:
#: **这里不测 ``WriteString``** —— PHP 把它放在 ``IntSql`` 上
#: （``php/sql/sqlint.php:27``），``ValSql`` 根本没有这个方法。字符串比较那条
#: 语义在 ``pair`` 段测（``PairSql extends IntSql``）。
VAL_OPS = [
    {"op": "seed", "rows": [[59, "1.500000"], [60, "0.000000"]]},
    {"op": "write_val", "id": 59, "value": "2.5"},          # 改值
    {"op": "write_val", "id": 59, "value": "2.5"},          # 值没变 → 不写
    {"op": "write_val", "id": 59, "value": "2.5000001"},    # 差 < MIN_FLOAT_VAL → 不写
    {"op": "write_val", "id": 61, "value": "7"},            # 新增
    {"op": "delete_by_id", "id": 61},
    {"op": "delete_by_id", "id": 999},                      # 不存在 → 0 行
]

#: ``pair`` 段：``WritePair`` 用字符串比较；``Delete`` 删的是"配对侧"。
PAIR_OPS = [
    {"op": "seed", "rows": [[1, 10], [2, 10], [3, 30]]},
    {"op": "write_pair", "id": 1, "pair": 11},              # 改配对
    {"op": "write_pair", "id": 1, "pair": 11},              # 没变 → 不写
    {"op": "write_string", "id": 1, "value": "11"},         # 字符串模式：同值不写
    {"op": "delete_by_id", "id": 2},                        # 按本体删
    {"op": "delete", "pair": 10},                           # 按配对侧删（只剩 2 号，已删）
    {"op": "delete", "pair": 999},                          # 不存在
]

#: ``daily`` 段：``DeleteAll`` 只删一只标的；``false`` 一行都不删。
DAILY_OPS = [
    {"op": "seed", "rows": [[59, "2026-10-01", "1.0"], [59, "2026-10-02", "1.1"], [60, "2026-10-01", "2.0"]]},
    {"op": "write_daily", "stock_id": 59, "date": "2026-10-02", "value": "1.2"},   # 改值
    {"op": "write_daily", "stock_id": 59, "date": "2026-10-02", "value": "1.2"},   # 没变
    {"op": "write_daily", "stock_id": 59, "date": "2026-10-05", "value": "1.3"},   # 新增（周一）
    {"op": "delete_all", "stock_id": 59},
]


class SqliteCursor:
    def __init__(self, cursor, queries):
        self._cursor = cursor
        self._queries = queries
        self.rowcount = 0

    def execute(self, sql, params=()):
        if sql.lstrip().upper().startswith("UPDATE"):
            sql = sql.replace(" LIMIT 1", "")
        self._queries.append((sql, params))
        self._cursor.execute(sql.replace("%s", "?"), tuple(params))
        self.rowcount = self._cursor.rowcount

    def fetchone(self):
        row = self._cursor.fetchone()
        return None if row is None else dict(row)

    def fetchall(self):
        return [dict(row) for row in self._cursor.fetchall()]

    def close(self):
        pass


class SqliteConnection:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self.queries = []

    def cursor(self):
        return SqliteCursor(self._conn.cursor(), self.queries)

    def commit(self):
        pass

    def execute(self, sql, params=()):
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        try:
            rows = [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
        return rows


def _simple(value):
    """与 PHP 侧 ``SnapshotRows`` 同一个规整：数值统一成最简文本。

    PHP 那边是 ``strval(floatval($value))`` —— ``0`` 打印成 ``"0"`` 而不是
    ``"0.0"``，所以这里复用 ``_php_strval_float``（同一个口径的唯一实现），
    不用 Python 的 ``str(float)``。
    """
    if isinstance(value, (int, float)):
        return _php_strval_float(value)
    text = str(value)
    try:
        return _php_strval_float(float(text))
    except ValueError:
        return text


def _snapshot(connection, columns, table, order):
    rows = connection.execute("SELECT %s FROM `%s` ORDER BY %s" % (columns, table, order))
    return [{key: _simple(value) for key, value in row.items()} for row in rows]


def build_python_connection():
    connection = SqliteConnection()
    connection.execute(
        "CREATE TABLE `os_probe_val` (`id` INTEGER PRIMARY KEY, `close` REAL NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE `os_probe_pair` (`id` INTEGER PRIMARY KEY, `stock_id` INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE `os_probe_daily` (`id` INTEGER PRIMARY KEY AUTOINCREMENT, "
        "`stock_id` INTEGER NOT NULL, `date` TEXT NOT NULL, `close` REAL NOT NULL)"
    )
    return connection


def run_python():
    connection = build_python_connection()
    val = ValStore(connection, table="os_probe_val")
    pair = PairStore(connection, table="os_probe_pair")
    daily = DailyCloseSql(connection, "os_probe_daily")
    return {
        "val": _run_ops(connection, val, VAL_OPS, "`id`, `close`", "os_probe_val", "`id`"),
        "pair": _run_ops(connection, pair, PAIR_OPS, "`id`, `stock_id`", "os_probe_pair", "`id`"),
        "daily": _run_ops(
            connection, daily, DAILY_OPS,
            "`stock_id`, `date`, `close`", "os_probe_daily", "`date`, `stock_id`",
        ),
    }


def _run_ops(connection, obj, ops, columns, table, order):
    out = []
    for op in ops:
        name = op["op"]
        result = None
        if name == "seed":
            connection.execute("DELETE FROM `%s`" % table)
            marks = ", ".join(["?"] * len(columns.split(",")))
            for row in op["rows"]:
                connection.execute(
                    "INSERT INTO `%s` (%s) VALUES (%s)" % (table, columns, marks), tuple(row)
                )
            result = True
        elif name == "write_val":
            result = obj.write_val(str(op["id"]), op["value"])
        elif name == "write_string":
            result = obj.write_string(str(op["id"]), op["value"])
        elif name == "delete_by_id":
            result = obj.delete_by_id(str(op["id"]))
        elif name == "write_pair":
            result = obj.write_pair(str(op["id"]), str(op["pair"]))
        elif name == "delete":
            result = obj.delete_by_pair_id(str(op["pair"]))
        elif name == "write_daily":
            result = obj.write_daily(str(op["stock_id"]), op["date"], op["value"])
        elif name == "delete_all":
            result = obj.delete_all(str(op["stock_id"]))
        out.append({
            "op": name,
            "result": (1 if result else 0) if isinstance(result, bool) else result,
            "rows": _snapshot(connection, columns, table, order),
        })
    return out


def run_php():
    with io.open(CASE_FILE, "w", encoding="utf-8") as handle:
        json.dump({"val": VAL_OPS, "pair": PAIR_OPS, "daily": DAILY_OPS},
                  handle, ensure_ascii=False)
    php_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "_php_optionstore_check.php")
    completed = subprocess.run(
        ["php", "-d", "display_errors=stderr", php_script, CASE_FILE],
        cwd=os.path.dirname(php_script),
        env={**os.environ, **_env()},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        print("PHP 探针失败（returncode=%s）" % completed.returncode)
        print(completed.stderr.decode("utf-8", "replace")[-3000:])
        return None
    return json.loads(completed.stdout.decode("utf-8"))


def compare(php, python):
    """逐操作比"返回值 + 整表快照"，返回不一致的条目。"""
    diffs = []
    for section in ("val", "pair", "daily"):
        php_ops = php.get(section) or []
        py_ops = python.get(section) or []
        if len(php_ops) != len(py_ops):
            diffs.append((section, "-", "操作数不同 php=%d py=%d" % (len(php_ops), len(py_ops))))
            continue
        for index, (php_op, py_op) in enumerate(zip(php_ops, py_ops), 1):
            if php_op["result"] != py_op["result"]:
                diffs.append((section, "#%d %s" % (index, php_op["op"]),
                              "返回值 php=%r py=%r" % (php_op["result"], py_op["result"])))
            if php_op["rows"] != py_op["rows"]:
                diffs.append((section, "#%d %s" % (index, php_op["op"]),
                              "快照 php=%r py=%r" % (php_op["rows"], py_op["rows"])))
    return diffs


def main():
    python_result = run_python()
    php_result = run_php()
    if php_result is None:
        return 1
    diffs = compare(php_result, python_result)
    total = sum(len(php_result.get(key) or []) for key in ("val", "pair", "daily"))
    if diffs:
        print("不一致 %d 处（共 %d 个操作）：" % (len(diffs), total))
        for section, label, detail in diffs:
            print("  [%s] %s -> %s" % (section, label, detail))
        return 1
    print("[通过] %d 个操作全一致（val / pair / daily 三段）" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
