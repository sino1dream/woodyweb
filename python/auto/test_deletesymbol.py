"""``auto/deletesymbol.py`` 的单测（删标的，PHP ``deletesymbol.php``）。

假库的做法与 ``test_submitupdaters.py`` 一致：行以 ``dict`` 存在
``tables[表名]`` 里，**写语句真的改内存** —— 所以"删完还剩几行"能断言，
而不只是看 SQL 长什么样。这一点在这里比别处更重要：删标的的错法几乎全是
"删少了 / 删多了"，而两者的 SQL 都长得完全正确。

被显式钉住的几件事：

* 六张表的**顺序**与 PHP ``DeleteStock()`` 一致（``stock`` 最后 —— 它一删，
  后面就再也定位不到任何行了）；
* 五道闸门命中时**一行都不删**，尤其 ``calibrationhistory`` 不动
  （PHP 会先删再拒绝，算刻意偏离，见 ``README_deletesymbol_migration.md`` §5）；
* ``id`` 存 stock id 的表（``stockhistorydate`` / ``lastcalibration`` /
  ``fundposition``）与按符号字符串存的表（``realtimequote`` / ``estquote``）
  各用各的匹配列 —— 搞混的错法是"删了同一个 id 的别的行"，很安静；
* ``holdings`` / ``futurespair`` 有两个身份，两个方向都要清。
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

AUTO_DIR = Path(__file__).resolve().parent
if str(AUTO_DIR) not in sys.path:
    sys.path.insert(0, str(AUTO_DIR))

import deletesymbol  # noqa: E402


# ---------------------------------------------------------------------------
# 假库
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, connection: "FakeConnection"):
        self.connection = connection
        self.rows: List[Dict[str, Any]] = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        self.connection.queries.append((" ".join(sql.split()), tuple(params)))
        self.rows, self.rowcount = self.connection.dispatch(
            " ".join(sql.split()), tuple(params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    """够本模块用的假库：``SELECT`` 投影 / ``COUNT`` / ``COUNT(DISTINCT)`` / ``DELETE``。"""

    _DELETE = re.compile(r"^DELETE FROM `?(\w+)`?(?: WHERE (.+))?$", re.I)
    _COUNT = re.compile(
        r"^SELECT COUNT\(\*\) AS n(?:, COUNT\(DISTINCT `?(\w+)`?\) AS (\w+))?"
        r" FROM `?(\w+)`?(?: WHERE (.+))?$", re.I)
    _SELECT = re.compile(
        r"^SELECT (.+?) FROM `?(\w+)`?(?: WHERE (.+?))?(?: LIMIT \d+)?$", re.I)

    def __init__(self, tables: Dict[str, List[Dict[str, Any]]] = None):
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            name: [dict(row) for row in rows]
            for name, rows in (tables or {}).items()
        }
        self.queries: List[Tuple[str, tuple]] = []
        self.commits = 0

    # -- 连接接口 ---------------------------------------------------------
    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rows_of(self, table: str) -> List[Dict[str, Any]]:
        return self.tables.setdefault(table, [])

    # -- 分派 -------------------------------------------------------------
    def dispatch(self, sql: str, params: tuple) -> Tuple[List[Dict[str, Any]], int]:
        match = self._DELETE.match(sql)
        if match:
            table, where = match.group(1), match.group(2)
            keep = [row for row in self.rows_of(table)
                    if not self._match(row, where, iter(params))]
            removed = len(self.rows_of(table)) - len(keep)
            self.tables[table] = keep
            return [], removed

        match = self._COUNT.match(sql)
        if match:
            distinct_col, distinct_as, table, where = match.groups()
            rows = [row for row in self.rows_of(table)
                    if self._match(row, where, iter(params))]
            result = {"n": len(rows)}
            if distinct_col:
                result[distinct_as or "distinct"] = len(
                    {row.get(distinct_col) for row in rows})
            return [result], 0

        match = self._SELECT.match(sql)
        if match:
            columns, table, where = match.groups()
            rows = [row for row in self.rows_of(table)
                    if self._match(row, where, iter(params))]
            if columns.strip() == "*":
                return rows, 0
            names = [name.strip().strip("`") for name in columns.split(",")]
            return [{name: row.get(name) for name in names} for row in rows], 0

        raise AssertionError("假库不认识这条 SQL：%s" % sql)

    @staticmethod
    def _match(row: Dict[str, Any], where: Any, params_iter) -> bool:
        """``col = %s [OR col = %s …]``；``where`` 为 ``None`` 表示不过滤。

        **每一行传一个新迭代器**（调用方在推导式里 ``iter(params)``）：
        参数是逐子句消费的，共用迭代器会让第二行拿到第一行剩下的参数 ——
        症状是"只有第一行匹配得上"，看起来像 SQL 写错了。
        也**不能**命中就 break：后面的 ``%s`` 还得吃掉自己的参数。
        """
        if not where:
            return True
        hit = False
        for clause in where.split(" OR "):
            column = re.match(r"^`?(\w+)`?\s*=\s*%s$", clause.strip())
            if column is None:
                raise AssertionError("假库不认识的 WHERE 片段：%s" % clause)
            if str(row.get(column.group(1))) == str(next(params_iter)):
                hit = True
        return hit


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------
def base_tables(stock_id: int = 419, pair_id: int = 420):
    """一个"什么闸门都没命中"的符号：六张 PHP 表 + 六张残留表都有行。"""
    return {
        "stock": [
            {"id": stock_id, "symbol": "ZZTEST", "name": "测试"},
            {"id": pair_id, "symbol": "ZZPAIR", "name": "配对"},
        ],
        "calibrationhistory": [
            {"id": 1, "stock_id": stock_id, "date": "2026-10-21", "close": 1.0},
            {"id": 2, "stock_id": stock_id, "date": "2026-10-22", "close": 1.1},
            {"id": 3, "stock_id": stock_id, "date": "2026-10-23", "close": 1.2},
        ],
        "stockema50": [{"id": 1, "stock_id": stock_id, "date": "2026-10-23", "close": 1.5}],
        "stockema200": [{"id": 1, "stock_id": stock_id, "date": "2026-10-23", "close": 1.6}],
        "stockhistory": [
            {"id": 1, "stock_id": stock_id, "date": "2026-10-22", "close": 10.0},
            {"id": 2, "stock_id": stock_id, "date": "2026-10-23", "close": 10.5},
        ],
        "netvaluehistory": [
            {"id": 1, "stock_id": stock_id, "date": "2026-10-23", "close": 2.5},
        ],
        "stockhistorydate": [{"id": stock_id, "date": "2026-10-23"}],
        "lastcalibration": [{"id": stock_id, "close": 3.5}],
        "fundposition": [{"id": stock_id, "close": 0.6}],
        "quarterreport": [{"id": 1, "stock_id": stock_id, "date": "2026-09-30", "close": "100"}],
        "stockdividend": [{"id": 1, "stock_id": stock_id, "date": "2026-09-21", "close": 0.5}],
        "realtimequote": [{"id": stock_id, "symbol": "ZZTEST", "price": "1.0"}],
        "estquote": [{"id": stock_id, "symbol": "ZZTEST", "close": "1.0"}],
        "holdings": [
            {"id": 1, "stock_id": stock_id, "holding_id": 900, "ratio": 0.5},
            {"id": 2, "stock_id": 901, "holding_id": stock_id, "ratio": 0.1},
            {"id": 3, "stock_id": 902, "holding_id": 903, "ratio": 0.2},
        ],
        "futurespair": [
            {"id": stock_id, "stock_id": pair_id},
            {"id": 904, "stock_id": stock_id},
            {"id": 905, "stock_id": 906},
        ],
        "abpair": [],
        "adrpair": [],
        "ahpair": [],
        "fundpair": [],
        "stockgroupitem": [],
    }


def build(**overrides):
    tables = base_tables()
    for name, rows in overrides.items():
        tables[name] = rows
    return FakeConnection(tables)


# ---------------------------------------------------------------------------
class PlanTest(unittest.TestCase):
    def test_unknown_symbol(self):
        connection = build()
        result = deletesymbol.plan(connection, "NOPE")
        self.assertFalse(result["found"])
        self.assertIsNone(result["stock_id"])
        self.assertEqual([], result["deletes"])
        self.assertEqual([], result["orphans"])

    def test_empty_symbol_does_not_touch_db(self):
        connection = build()
        result = deletesymbol.plan(connection, "  ")
        self.assertEqual("", result["symbol"])
        self.assertFalse(result["found"])
        self.assertEqual([], connection.queries)

    def test_counts_and_order(self):
        connection = build()
        result = deletesymbol.plan(connection, "ZZTEST")
        self.assertEqual(419, result["stock_id"])
        self.assertEqual(
            [table for table, _column, _label in deletesymbol.PHP_DELETES],
            [item["table"] for item in result["deletes"]],
        )
        by_table = {item["table"]: item["rows"] for item in result["deletes"]}
        self.assertEqual(3, by_table["calibrationhistory"])
        self.assertEqual(1, by_table["stockema50"])
        self.assertEqual(1, by_table["stockema200"])
        self.assertEqual(2, by_table["stockhistory"])
        self.assertEqual(1, by_table["netvaluehistory"])
        self.assertEqual(1, by_table["stock"])
        self.assertEqual(9, result["deleted_rows"])

    def test_stock_matched_by_id_not_stock_id(self):
        """``stock`` 表里根本没有 ``stock_id`` 列 —— 匹配列写成 stock_id 会删不到。"""
        connection = build()
        result = deletesymbol.plan(connection, "ZZTEST")
        stock = [item for item in result["deletes"] if item["table"] == "stock"][0]
        self.assertEqual("id", stock["column"])
        self.assertEqual(1, stock["rows"])

    def test_orphan_counts(self):
        connection = build()
        result = deletesymbol.plan(connection, "ZZTEST")
        by_table = {item["table"]: item["rows"] for item in result["orphans"]}
        self.assertEqual(1, by_table["stockhistorydate"])
        self.assertEqual(1, by_table["lastcalibration"])
        self.assertEqual(1, by_table["fundposition"])
        self.assertEqual(1, by_table["quarterreport"])
        self.assertEqual(1, by_table["stockdividend"])
        self.assertEqual(1, by_table["realtimequote"])
        self.assertEqual(1, by_table["estquote"])
        # holdings 两个身份各一行（第三行与它无关）
        self.assertEqual(2, by_table["holdings"])
        # futurespair 同理
        self.assertEqual(2, by_table["futurespair"])
        self.assertEqual(
            sum(by_table.values()), result["orphan_rows"])

    def test_skipped_tables_are_explained(self):
        result = deletesymbol.plan(build(), "ZZTEST")
        names = [item["table"] for item in result["skipped"]]
        self.assertIn("stock_minute_data", names)
        self.assertIn("stocktransaction", names)
        for item in result["skipped"]:
            self.assertTrue(item["note"])


class GateTest(unittest.TestCase):
    def _blocked(self, **overrides):
        connection = build(**overrides)
        result = deletesymbol.plan(connection, "ZZTEST")
        self.assertTrue(result["blocked"], "这道闸门没拦住：%s" % overrides)
        return connection, result

    def test_abpair_as_owner(self):
        _, result = self._blocked(abpair=[{"id": 419, "stock_id": 420}])
        self.assertEqual("abpair", result["blocked_by"]["table"])
        self.assertIn("作为本体存在配对标的 ZZPAIR", result["blocked_by"]["detail"])

    def test_abpair_as_pair(self):
        _, result = self._blocked(abpair=[{"id": 420, "stock_id": 419}])
        self.assertEqual("abpair", result["blocked_by"]["table"])
        self.assertIn("是 ZZPAIR 的配对标的", result["blocked_by"]["detail"])

    def test_adrpair(self):
        _, result = self._blocked(adrpair=[{"id": 419, "stock_id": 420}])
        self.assertEqual("adrpair", result["blocked_by"]["table"])

    def test_ahpair(self):
        _, result = self._blocked(ahpair=[{"id": 420, "stock_id": 419}])
        self.assertEqual("ahpair", result["blocked_by"]["table"])

    def test_fundpair(self):
        _, result = self._blocked(fundpair=[{"id": 419, "stock_id": 420}])
        self.assertEqual("fundpair", result["blocked_by"]["table"])

    def test_group_item(self):
        _, result = self._blocked(
            stockgroupitem=[{"id": 1, "stockgroup_id": 7, "stock_id": 419}])
        self.assertEqual("stockgroupitem", result["blocked_by"]["table"])
        self.assertIn("1 个分组里有 1 行", result["blocked_by"]["detail"])

    def test_all_gates_reported_not_short_circuited(self):
        """PHP 是 if/else if，命中第一道就返回；预览页要看到全部原因。"""
        connection = build(
            abpair=[{"id": 419, "stock_id": 420}],
            fundpair=[{"id": 419, "stock_id": 420}],
            stockgroupitem=[{"id": 1, "stockgroup_id": 7, "stock_id": 419}],
        )
        result = deletesymbol.plan(connection, "ZZTEST")
        hits = {gate["table"] for gate in result["gates"] if gate["hit"]}
        self.assertEqual({"abpair", "fundpair", "stockgroupitem"}, hits)
        # 判定只认第一道，与 PHP 等价
        self.assertEqual("abpair", result["blocked_by"]["table"])

    def test_gate_order_matches_php(self):
        result = deletesymbol.plan(build(), "ZZTEST")
        self.assertEqual(
            ["abpair", "adrpair", "ahpair", "fundpair", "stockgroupitem"],
            [gate["table"] for gate in result["gates"]],
        )


class ApplyTest(unittest.TestCase):
    #: 被测符号的 stock_id（``base_tables`` 里那个）
    STOCK_ID = 419

    def assert_no_self_rows(self, connection, tables):
        """这些表里不能再有任何一行指向本符号（``id`` / ``stock_id`` / ``holding_id``）。

        不能直接断言"表空了"：``stock`` / ``futurespair`` 里还有**另一只**符号的行，
        那是对的 —— 删一只不能连带删别的。
        """
        columns = ("id", "stock_id", "holding_id")
        for table in tables:
            for row in connection.rows_of(table):
                for column in columns:
                    self.assertNotEqual(
                        self.STOCK_ID, row.get(column),
                        "%s 里还有指向本符号的行：%s" % (table, row))

    def test_clean_deletes_six_tables_only(self):
        connection = build()
        result = deletesymbol.apply(connection, "ZZTEST")
        self.assertTrue(result["applied"])
        self.assertEqual(9, result["applied_rows"])
        self.assert_no_self_rows(
            connection, [table for table, _column, _label in deletesymbol.PHP_DELETES])
        # 残留表照 PHP 的口径留着
        self.assertEqual(1, len(connection.rows_of("stockhistorydate")))
        self.assertEqual(1, len(connection.rows_of("lastcalibration")))
        self.assertEqual(1, len(connection.rows_of("fundposition")))
        self.assertEqual(3, len(connection.rows_of("holdings")))
        self.assertEqual(3, len(connection.rows_of("futurespair")))
        self.assertEqual(1, connection.commits)

    def test_stock_row_goes_last(self):
        """``stock`` 一删，``GetId`` 就再也定位不到 —— 它必须是最后一步。"""
        connection = build()
        deletesymbol.apply(connection, "ZZTEST")
        deletes = [(sql, params) for sql, params in connection.queries
                   if sql.startswith("DELETE")]
        self.assertEqual("DELETE FROM `stock` WHERE `id` = %s", deletes[-1][0])

    def test_cascade_clears_orphans(self):
        connection = build()
        result = deletesymbol.apply(connection, "ZZTEST", include_orphans=True)
        self.assertTrue(result["applied"])
        self.assert_no_self_rows(
            connection, [item[0] for item in deletesymbol.ORPHAN_TABLES])
        self.assertGreater(result["applied_orphans"], 0)

    def test_cascade_keeps_unrelated_rows(self):
        connection = build()
        deletesymbol.apply(connection, "ZZTEST", include_orphans=True)
        # holdings 第三行、futurespair 第三行都不属于本符号
        self.assertEqual([3], [row["id"] for row in connection.rows_of("holdings")])
        self.assertEqual([905], [row["id"] for row in connection.rows_of("futurespair")])
        # 对照组：另一只符号的 stock 行还在
        self.assertEqual([420], [row["id"] for row in connection.rows_of("stock")])

    def test_blocked_deletes_nothing(self):
        """**本版与 PHP 的关键差异**：被闸门拦下时校准历史一行都不动。"""
        connection = build(
            stockgroupitem=[{"id": 1, "stockgroup_id": 7, "stock_id": 419}])
        result = deletesymbol.apply(connection, "ZZTEST")
        self.assertFalse(result["applied"])
        self.assertEqual(0, result["applied_rows"])
        self.assertEqual(0, connection.commits)
        self.assertEqual(3, len(connection.rows_of("calibrationhistory")))
        self.assertEqual(2, len(connection.rows_of("stockhistory")))
        # 本符号那一行还在（``stock`` 表里另一行是配对符号，与本例无关）
        self.assertEqual(2, len(connection.rows_of("stock")))

    def test_blocked_never_issues_delete(self):
        connection = build(abpair=[{"id": 419, "stock_id": 420}])
        deletesymbol.apply(connection, "ZZTEST", include_orphans=True)
        self.assertEqual(
            [], [sql for sql, _params in connection.queries
                 if sql.startswith("DELETE")])

    def test_not_found_deletes_nothing(self):
        connection = build()
        result = deletesymbol.apply(connection, "NOPE")
        self.assertFalse(result["applied"])
        self.assertEqual(0, connection.commits)

    def test_commit_can_be_skipped(self):
        connection = build()
        deletesymbol.apply(connection, "ZZTEST", commit=False)
        self.assertEqual(0, connection.commits)

    def test_rerun_is_idempotent(self):
        connection = build()
        deletesymbol.apply(connection, "ZZTEST")
        second = deletesymbol.apply(connection, "ZZTEST")
        self.assertFalse(second["found"])
        self.assertFalse(second["applied"])


class OrphanKeyTest(unittest.TestCase):
    """残留表的匹配列：``id`` / ``stock_id`` / ``symbol`` 三种，混了就会删错行。"""

    def test_symbol_keyed_tables_use_symbol(self):
        connection = build()
        deletesymbol.apply(connection, "ZZTEST", include_orphans=True)
        sqls = [sql for sql, _params in connection.queries
                if sql.startswith("DELETE")]
        self.assertIn("DELETE FROM `realtimequote` WHERE `symbol` = %s", sqls)
        self.assertIn("DELETE FROM `estquote` WHERE `symbol` = %s", sqls)

    def test_symbol_keyed_does_not_match_by_id(self):
        """``realtimequote.id`` 存的是 stock_id，但按符号删才是对的 ——
        同一张表里 id 与本符号无关的行不能被带走。"""
        tables = base_tables()
        tables["realtimequote"].append(
            {"id": 419, "symbol": "OTHER", "price": "9.9"})
        connection = FakeConnection(tables)
        deletesymbol.apply(connection, "ZZTEST", include_orphans=True)
        self.assertEqual(["OTHER"], [row["symbol"] for row in connection.rows_of("realtimequote")])

    def test_holdings_two_identities(self):
        connection = build()
        deletesymbol.apply(connection, "ZZTEST", include_orphans=True)
        sqls = [sql for sql, _params in connection.queries
                if sql.startswith("DELETE")]
        self.assertIn(
            "DELETE FROM `holdings` WHERE `stock_id` = %s OR `holding_id` = %s", sqls)

    def test_futurespair_two_identities(self):
        connection = build()
        deletesymbol.apply(connection, "ZZTEST", include_orphans=True)
        sqls = [sql for sql, _params in connection.queries
                if sql.startswith("DELETE")]
        self.assertIn(
            "DELETE FROM `futurespair` WHERE `id` = %s OR `stock_id` = %s", sqls)

    def test_id_keyed_tables(self):
        connection = build()
        deletesymbol.apply(connection, "ZZTEST", include_orphans=True)
        sqls = [sql for sql, _params in connection.queries
                if sql.startswith("DELETE")]
        for table in ("stockhistorydate", "lastcalibration", "fundposition"):
            self.assertIn("DELETE FROM `%s` WHERE `id` = %%s" % table, sqls)


class DescribeTest(unittest.TestCase):
    def test_empty_symbol(self):
        self.assertIn("缺少 symbol", deletesymbol.describe({"symbol": ""}))

    def test_not_found(self):
        message = deletesymbol.describe({"symbol": "NOPE", "found": False})
        self.assertIn("没什么可删的", message)

    def test_blocked(self):
        result = deletesymbol.plan(
            build(fundpair=[{"id": 419, "stock_id": 420}]), "ZZTEST")
        message = deletesymbol.describe(result)
        self.assertIn("删不了", message)
        self.assertIn("fundpair", message)
        self.assertIn("作为本体存在配对标的 ZZPAIR", message)

    def test_dry_run(self):
        message = deletesymbol.describe(deletesymbol.plan(build(), "ZZTEST"))
        self.assertIn("dry-run", message)

    def test_applied_with_leftover_note(self):
        connection = build()
        result = deletesymbol.apply(connection, "ZZTEST")
        message = deletesymbol.describe(result)
        self.assertIn("主表 9 行", message)
        # 默认口径下必须提醒"还有残留没清"，否则管理员以为清干净了
        self.assertIn("残留", message)
        self.assertIn("PHP 也不清", message)

    def test_applied_with_cascade(self):
        connection = build()
        result = deletesymbol.apply(connection, "ZZTEST", include_orphans=True)
        message = deletesymbol.describe(result)
        self.assertIn("残留表", message)
        self.assertNotIn("PHP 也不清", message)


if __name__ == "__main__":
    unittest.main()
