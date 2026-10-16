"""stockgroup Sql 层对照：``core.stockgroupsql`` vs PHP ``sqlstockgroup.php`` 家族。

跑法::

    D:/python/python.exe -B _scratch/_php_stockgroup_compare.py

左边是真实 PHP（``_php_stockgroup_check.php``：``_sqlcommon.php`` 整个
require，``UpdateArray`` / ``SqlGet*TableData`` 的拼装句从源码抠、执行句
换成 return），右边是 Python 迁移实现**实际发出的 SQL**（内存 fake 连接
记录 (sql, params)，再把 params 按 PHP 风格嵌回字符串）。

## 比对分两档

* **严格一致**（大多数用例）：归一化（折叠空白、去反引号、关键字小写、
  抹掉 COUNT 别名）后逐字节相等；
* **语义等价**（两条 INSERT + 两条 COUNT）：形态不同但功能一致，
  脚本里显式列在 ``EQUIVALENT`` 表并打印说明，不算差异：

  ==========================  ==========================================
  PHP                          Python
  ==========================  ==========================================
  ``INSERT ... (id, ...) VALUES('0', ...)``   省掉 id 列让 MySQL 自增
  ``SELECT count(*) as total``                ``SELECT COUNT(*) AS n``
  ==========================  ==========================================

  前者是 MyISAM 的自增惯例（``InsertArray`` 无 id 键时拼 ``id, '0'``），
  后者纯别名。都在模块 docstring 的"已知偏差"里有记。
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_AUTO = os.path.dirname(_HERE)
if _AUTO not in sys.path:
    sys.path.insert(0, _AUTO)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHECK_PHP = os.path.join(_HERE, "_php_stockgroup_check.php")
PHP_EXE = "D:/xampp/php/php.exe"

#: 形态不同但语义等价的用例（key 相同，值给说明）
EQUIVALENT = {
    "insert_group": "PHP 显式插 id='0'（MyISAM 自增惯例），Python 省 id 列",
    "insert_item": "PHP 显式插 id='0'（MyISAM 自增惯例），Python 省 id 列",
    "count_group_by_name": "别名 total vs n",
    "count_item_by_group": "别名 total vs n",
    "count_item_by_stock": "别名 total vs n",
}


# ---------------------------------------------------------------------------
# Python 侧：捕获 core.stockgroupsql 实际发出的 SQL
# ---------------------------------------------------------------------------

class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows = []
        self.rowcount = 0
        self.lastrowid = 0

    def execute(self, sql, params=()):
        self.connection.log.append((sql, tuple(params)))
        self._rows = []
        self.rowcount = 0
        self.lastrowid = 0
        key = " ".join(sql.split())
        handler = _PY_HANDLERS.get(key)
        if handler is None:
            raise AssertionError("compare fake 不认识的 SQL: %s" % sql)
        handler(self, params)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


def _py_none(cursor, params):
    pass


def _py_group_row(cursor, params):
    if _select_returns_empty(cursor):
        return
    cursor._rows = [{"id": 7, "member_id": 1, "groupname": "演示分组A"}]


def _py_item_row(cursor, params):
    if _select_returns_empty(cursor):
        return
    cursor._rows = [{"id": 9, "stockgroup_id": 1, "stock_id": 2,
                     "quantity": 0, "cost": 0.0, "record": 0}]


def _py_count_1(cursor, params):
    if _select_returns_empty(cursor):
        cursor._rows = [{"n": 0}]
        return
    cursor._rows = [{"n": 1}]


def _py_symbol(cursor, params):
    cursor._rows = [{"symbol": "SZ161125"}]


def _py_id(cursor, params):
    cursor._rows = [{"id": 2}]


_PY_HANDLERS = {
    " ".join("SELECT * FROM stockgroup WHERE member_id = %s AND groupname = %s LIMIT 1".split()):
        _py_group_row,
    " ".join("SELECT * FROM stockgroup WHERE id = %s LIMIT 1".split()):
        _py_group_row,
    " ".join("SELECT * FROM stockgroup WHERE member_id = %s ORDER BY groupname ASC".split()):
        _py_group_row,
    " ".join("SELECT COUNT(*) AS n FROM stockgroup WHERE groupname = %s".split()):
        _py_count_1,
    " ".join("INSERT INTO stockgroup (member_id, groupname) VALUES (%s, %s)".split()):
        _py_none,
    " ".join("UPDATE stockgroup SET groupname = %s WHERE id = %s LIMIT 1".split()):
        _py_none,
    " ".join("DELETE FROM stockgroup WHERE id = %s".split()):
        _py_none,
    " ".join("DELETE FROM stockgroup WHERE groupname = %s".split()):
        _py_none,
    " ".join("SELECT * FROM stockgroupitem WHERE stockgroup_id = %s ORDER BY id ASC".split()):
        _py_item_row,
    " ".join("SELECT * FROM stockgroupitem WHERE stockgroup_id = %s ORDER BY id ASC LIMIT 10".split()):
        _py_item_row,
    " ".join("SELECT * FROM stockgroupitem WHERE stockgroup_id = %s ORDER BY id ASC LIMIT 5 , 10".split()):
        _py_item_row,
    " ".join("SELECT * FROM stockgroupitem WHERE id = %s LIMIT 1".split()):
        _py_item_row,
    " ".join("SELECT * FROM stockgroupitem WHERE stockgroup_id = %s AND stock_id = %s LIMIT 1".split()):
        _py_item_row,
    " ".join("SELECT COUNT(*) AS n FROM stockgroupitem WHERE stockgroup_id = %s".split()):
        _py_count_1,
    " ".join("SELECT COUNT(*) AS n FROM stockgroupitem WHERE stock_id = %s".split()):
        _py_count_1,
    " ".join("INSERT INTO stockgroupitem (stockgroup_id, stock_id, quantity, cost, record) VALUES (%s, %s, 0, 0.0, 0)".split()):
        _py_none,
    " ".join("UPDATE stockgroupitem SET quantity = %s, cost = %s, record = %s WHERE id = %s LIMIT 1".split()):
        _py_none,
    " ".join("DELETE FROM stockgroupitem WHERE id = %s".split()):
        _py_none,
    " ".join("DELETE FROM stockgroupitem WHERE stockgroup_id = %s".split()):
        _py_none,
    " ".join("SELECT symbol FROM stock WHERE id = %s LIMIT 1".split()):
        _py_symbol,
    " ".join("SELECT id FROM stock WHERE symbol = %s LIMIT 1".split()):
        _py_id,
}


class RecordingConnection:
    """记录 (sql, params)；预置一行让读方法走完。

    ``empty_selects=True`` 时所有 SELECT 返回空行 —— ``insert_string``
    是"先查后插"，查有行就直接 return None、INSERT 发不出来。
    """

    def __init__(self, empty_selects: bool = False):
        self.log = []
        self.empty_selects = empty_selects

    def cursor(self):
        return RecordingCursor(self)

    def last_sql(self):
        sql, params = self.log[-1]
        return sql, params


def _select_returns_empty(cursor) -> bool:
    return cursor.connection.empty_selects

    def last_sql(self):
        sql, params = self.log[-1]
        return sql, params


def _embed(sql: str, params: tuple) -> str:
    """把 %s 参数按 PHP 字符串拼接风格嵌回去（PHP 也不转义）。"""
    out = sql
    for value in params:
        out = out.replace("%s", "'%s'" % value, 1)
    return out


def run_python_cases() -> dict:
    """逐用例调 core.stockgroupsql，返回 {case: 实际 SQL 字符串}。"""
    from core.stockgroupsql import (
        StockGroupItemSql,
        StockGroupSql,
        _get_stock_id,
        _stock_symbol_of,
    )

    cases = {}

    # Add_id / Remove_id：类常量就是 PHP 方法的结果
    cases["add_id_member"] = StockGroupSql.KEY
    cases["add_id_stockgroup"] = StockGroupItemSql.KEY
    cases["remove_id_stockgroup"] = "stockgroup"
    cases["remove_id_member"] = "member"

    # -- StockGroupSql ------------------------------------------------------
    conn = RecordingConnection()
    group_sql = StockGroupSql(conn)
    group_sql.get_record(1, "演示分组A")
    cases["select_group_record"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    group_sql = StockGroupSql(conn)
    group_sql.get_record_by_id(7)
    cases["select_group_by_id"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    group_sql = StockGroupSql(conn)
    group_sql.get_all(1)
    cases["select_group_all"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    group_sql = StockGroupSql(conn)
    group_sql.count_by_string("演示分组A")
    cases["count_group_by_name"] = _embed(*conn.last_sql())

    conn = RecordingConnection(empty_selects=True)
    group_sql = StockGroupSql(conn)
    group_sql.insert_string(1, "演示分组A")
    cases["insert_group"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    group_sql = StockGroupSql(conn)
    group_sql.update_string(7, "改名")
    cases["update_group"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    group_sql = StockGroupSql(conn)
    group_sql.delete_by_id(7)
    cases["delete_group_by_id"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    group_sql = StockGroupSql(conn)
    group_sql.delete_by_string("演示分组A")
    cases["delete_group_by_string"] = _embed(*conn.last_sql())

    # -- StockGroupItemSql --------------------------------------------------
    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.get_all()
    cases["select_item_all"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.get_record_by_id(9)
    cases["select_item_by_id"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.get_record(2)
    cases["select_item_by_group_stock"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.count()
    cases["count_item_by_group"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.count_by_stock_id(2)
    cases["count_item_by_stock"] = _embed(*conn.last_sql())

    conn = RecordingConnection(empty_selects=True)
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.insert(2)
    cases["insert_item"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.update(9, 5, 1.5, 3)
    cases["update_item"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.delete_by_id(9)
    cases["delete_item_by_id"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.delete_all()
    cases["delete_item_all"] = _embed(*conn.last_sql())

    # -- 符号替身 ------------------------------------------------------------
    conn = RecordingConnection()
    _stock_symbol_of(conn, 2)
    cases["select_symbol_by_id"] = _embed(*conn.last_sql())

    conn = RecordingConnection()
    _get_stock_id(conn, "SZ161125")
    cases["select_id_by_symbol"] = _embed(*conn.last_sql())

    # WHERE / LIMIT 拼装：Python 侧不暴露中间函数，用已捕获的 SQL 反推太绕，
    # 这几条在 PHP 侧本就是纯函数演示，Python 的对应物是"最终 SQL 里的
    # 片段"——取上面几条的 WHERE 片段即可。
    cases["where_member_group"] = _extract_where(cases["select_group_record"])
    cases["where_id"] = _extract_where(cases["select_group_by_id"])
    cases["where_stock"] = _extract_where(cases["count_item_by_stock"])
    cases["where_key_stock"] = _extract_where(cases["select_item_by_group_stock"])
    cases["where_string"] = _extract_where(cases["count_group_by_name"])
    # _SqlBuildLimit 的三个分支：真调 Python 侧的分页路径拿 LIMIT 片段
    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.get_all(0, 10)
    cases["limit_zero_num"] = _extract_limit(_embed(*conn.last_sql()), default="")

    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.get_all(5, 10)
    cases["limit_start_num"] = _extract_limit(_embed(*conn.last_sql()), default="")

    conn = RecordingConnection()
    item_sql = StockGroupItemSql(conn, group_id=1)
    item_sql.get_all(0, 0)
    cases["limit_both_zero"] = _extract_limit(_embed(*conn.last_sql()), default="")

    return cases


def _extract_where(sql: str) -> str:
    match = re.search(r"WHERE (.*?)(?: LIMIT| ORDER BY|$)", sql)
    return match.group(1) if match else ""


def _extract_limit(sql: str, default: str = "") -> str:
    match = re.search(r"LIMIT (.*)$", sql)
    return match.group(1) if match else default


# ---------------------------------------------------------------------------
# 归一化与比对
# ---------------------------------------------------------------------------

def normalize(sql) -> str:
    """归一化：折叠空白、去反引号、小写、抹 COUNT 别名。"""
    if sql is False or sql is None:
        return ""
    text = " ".join(str(sql).split())
    text = text.replace("`", "")
    text = text.lower()
    text = re.sub(r"count\(\*\) as (?:total|n)", "count(*)", text)
    return text


def main() -> int:
    if not os.path.exists(PHP_EXE):
        print("PHP 可执行文件不存在：%s" % PHP_EXE)
        return 2
    proc = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", CHECK_PHP],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print("PHP harness 失败：%s" % proc.stderr[:500])
        return 2
    php_cases = json.loads(proc.stdout)
    py_cases = run_python_cases()

    missing = sorted(set(php_cases) - set(py_cases))
    extra = sorted(set(py_cases) - set(php_cases))
    if missing or extra:
        print("用例集不一致：PHP 独有 %s / Python 独有 %s" % (missing, extra))
        return 2

    strict_fail = 0
    equivalent = 0
    for key in sorted(php_cases):
        php_value = php_cases[key]
        py_value = py_cases[key]
        if key in EQUIVALENT:
            equivalent += 1
            print("[等价] %-28s PHP=%r  Py=%r  （%s）"
                  % (key, php_value, py_value, EQUIVALENT[key]))
            continue
        if normalize(php_value) != normalize(py_value):
            strict_fail += 1
            print("[差异] %s\n   PHP: %s\n   Py : %s" % (key, php_value, py_value))

    print()
    print("PHP %d 例：严格一致 %d、语义等价 %d、差异 %d"
          % (len(php_cases), len(php_cases) - equivalent - strict_fail,
             equivalent, strict_fail))
    return 1 if strict_fail else 0


if __name__ == "__main__":
    sys.exit(main())
