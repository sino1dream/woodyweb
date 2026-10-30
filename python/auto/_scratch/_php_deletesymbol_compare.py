"""删标的：PHP 真身 vs Python 的逐例对照（``deletesymbol.php``）。

    cd python && D:/python/python.exe -B auto/_scratch/_php_deletesymbol_compare.py

## 对照什么

不是"SQL 长得像不像"，而是**在真库上造一个一次性符号，让两边各删一次，
再数各表还剩几行**。所以：

* PHP 端跑的是 ``_php_deletesymbol_check.php``，它 eval 的是
  ``woody/res/deletesymbol.php`` 里 ``_deleteStockSymbol()`` 的**原文**，
  下面调的 ``DeleteStock()`` / ``GetAbPairSql()`` / ``SqlDeleteStockGroupItemByStockID()``
  全是真身（见那个文件头部对 pitfalls #145 的说明）；
* Python 端跑的是 ``auto/deletesymbol.py::apply()``，与页面走同一条路；
* 两边用**同一组 SQL** 数行数，数的是"删完还剩几行"，不是"删了几行" ——
  后者会被"本来就没有行"骗过去。

## 为什么要一次性符号

``deletestock.php`` 删的是真表，MyISAM 没事务。拿 SPY / SH513100 这种在用的
符号做对照，删错了就只能靠 dump 恢复。这里每例都现场建
``ZZPHP*`` / ``ZZPY*`` 两个**镜像**符号（连 fixture 行都一模一样），
跑完连残留一起 ``DELETE`` 掉 —— 真实数据一行都不碰。

## 九个用例的分布

无闸门命中 1 例（clean）、本版级联 1 例（cascade）、五道闸门各至少 1 例、
其中一个专门测"配对表反方向"。``gate_groupitem`` 是**已知偏离**那一例：
PHP 会把校准历史删掉再拒绝删标的，本版一行都不动 —— 它的差异是设计，
不是缺陷，所以单独标出来，不算失败。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRATCH_DIR = Path(__file__).resolve().parent
AUTO_DIR = SCRATCH_DIR.parent
for _path in (str(AUTO_DIR),):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import deletesymbol  # noqa: E402
from core.stockdata import MySqlFundDataSource  # noqa: E402

PHP_SCRIPT = SCRATCH_DIR / "_php_deletesymbol_check.php"


#: 与 ``_php_deletesymbol_check.php`` 末尾**同一组查询**（表名, 匹配列）。
#: ``symbol`` 表示这行按符号字符串存（``estquote`` / ``realtimequote``）。
COUNT_SPEC = (
    ("calibrationhistory", "stock_id"),
    ("stockema50", "stock_id"),
    ("stockema200", "stock_id"),
    ("stockhistory", "stock_id"),
    ("netvaluehistory", "stock_id"),
    ("stock", "id"),
    ("stockhistorydate", "id"),
    ("lastcalibration", "id"),
    ("fundposition", "id"),
    ("quarterreport", "stock_id"),
    ("stockdividend", "stock_id"),
    ("realtimequote", "symbol"),
)


def _execute(connection, sql, params=()):
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.rowcount
    finally:
        cursor.close()


def _fetchone(connection, sql, params=()):
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------
def _seed(connection, symbol, pair_symbol, gates):
    """给一个一次性符号铺满 fixture —— 六张 PHP 表 + 六张残留表 + 指定闸门。"""
    _execute(connection, "INSERT INTO `stock` (`symbol`, `name`) VALUES (%s, %s)",
             (symbol, "ZZ 测试 " + symbol))
    stock_id = _fetchone(
        connection, "SELECT `id` FROM `stock` WHERE `symbol` = %s", (symbol,))["id"]
    _execute(connection, "INSERT INTO `stock` (`symbol`, `name`) VALUES (%s, %s)",
             (pair_symbol, "ZZ 配对 " + pair_symbol))
    pair_id = _fetchone(
        connection, "SELECT `id` FROM `stock` WHERE `symbol` = %s", (pair_symbol,))["id"]

    # 六张 PHP 会删的表
    for offset, date in enumerate(("2026-10-21", "2026-10-22", "2026-10-23")):
        _execute(
            connection,
            "INSERT INTO calibrationhistory (stock_id, date, close, time, num)"
            " VALUES (%s, %s, %s, '09:00:00', 1)",
            (stock_id, date, str(1.0 + offset)),
        )
    for table in ("stockema50", "stockema200"):
        for date in ("2026-10-22", "2026-10-23"):
            _execute(
                connection,
                "INSERT INTO `%s` (stock_id, date, close) VALUES (%%s, %%s, 1.5)" % table,
                (stock_id, date),
            )
    for offset, date in enumerate(("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23")):
        _execute(
            connection,
            "INSERT INTO stockhistory (stock_id, date, close, volume, adjclose)"
            " VALUES (%s, %s, %s, 1000, %s)",
            (stock_id, date, str(10.0 + offset), str(10.0 + offset)),
        )
    for date in ("2026-10-22", "2026-10-23"):
        _execute(
            connection,
            "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, 2.5)",
            (stock_id, date),
        )

    # 六张 PHP 不删的残留表
    _execute(connection, "INSERT INTO stockhistorydate (id, date) VALUES (%s, '2026-10-23')",
             (stock_id,))
    _execute(connection, "INSERT INTO lastcalibration (id, close) VALUES (%s, 3.5)", (stock_id,))
    _execute(connection, "INSERT INTO fundposition (id, close) VALUES (%s, 0.6)", (stock_id,))
    _execute(connection, "INSERT INTO quarterreport (stock_id, date, close)"
                         " VALUES (%s, '2026-09-30', '100')", (stock_id,))
    _execute(connection, "INSERT INTO stockdividend (stock_id, date, close)"
                         " VALUES (%s, '2026-09-21', 0.5)", (stock_id,))
    _execute(
        connection,
        "INSERT INTO realtimequote (id, symbol, source_symbol, price, prev_price, name,"
        " quote_date, quote_time, source, fetched_at)"
        " VALUES (%s, %s, %s, '1.0', '0.9', 'ZZ', '2026-10-23', '15:00:00', 'zz', NOW())",
        (stock_id, symbol, symbol),
    )

    state = {"stock_id": stock_id, "pair_id": pair_id}

    # 闸门
    for table in ("abpair", "ahpair", "adrpair", "fundpair"):
        side = gates.get(table)
        if side == "self":
            _execute(connection, "INSERT INTO `%s` (id, stock_id) VALUES (%%s, %%s)" % table,
                     (stock_id, pair_id))
        elif side == "pair":
            _execute(connection, "INSERT INTO `%s` (id, stock_id) VALUES (%%s, %%s)" % table,
                     (pair_id, stock_id))
    if gates.get("stockgroupitem"):
        # stockgroup_id 用假 id：MyISAM 没有外键，不必真建一个分组
        _execute(
            connection,
            "INSERT INTO stockgroupitem (stockgroup_id, stock_id, quantity, cost, record)"
            " VALUES (999999, %s, 100, 1.0, 1)",
            (stock_id,),
        )
    return state


def _cleanup(connection, symbols, ids=()):
    """把这个符号在任何表里的痕迹全抹掉（``stock`` 最后删）。

    ``ids`` 必须由调用方**显式传进来**：跑完之后 ``stock`` 那一行已经没了，
    再靠 ``SELECT id FROM stock WHERE symbol = ?`` 反查会拿到空 ——
    于是按 ``id`` 匹配的三张表（``stockhistorydate`` / ``lastcalibration`` /
    ``fundposition``）一行都清不掉，每跑一例就往库里漏三行。
    （第一版就是这么错的，症状是"基线行数悄悄涨了 4 行"。）
    """
    for symbol in symbols:
        row = _fetchone(
            connection, "SELECT `id` FROM `stock` WHERE `symbol` = %s", (symbol,))
        values = list(ids) + ([row["id"]] if row else [])
        for table in ("abpair", "ahpair", "adrpair", "fundpair", "futurespair"):
            for value in values:
                _execute(connection, "DELETE FROM `%s` WHERE id = %%s OR stock_id = %%s"
                         % table, (value, value))
        for table, column in COUNT_SPEC:
            if column == "symbol":
                _execute(connection, "DELETE FROM `%s` WHERE `%s` = %%s" % (table, column),
                         (symbol,))
            else:
                for value in values:
                    _execute(connection,
                             "DELETE FROM `%s` WHERE `%s` = %%s" % (table, column),
                             (value,))
        _execute(connection, "DELETE FROM stockgroupitem WHERE stockgroup_id = 999999")
        _execute(connection, "DELETE FROM `stock` WHERE `symbol` = %s", (symbol,))


def _setup_pair(connection, case, suffix):
    """建一对镜像符号（PHP 一个、Python 一个），fixture 完全相同。"""
    php_symbol = "ZZPHP" + suffix
    py_symbol = "ZZPY" + suffix
    php_pair = php_symbol + "P"
    py_pair = py_symbol + "P"
    symbols = (php_symbol, py_symbol, php_pair, py_pair)
    _cleanup(connection, symbols)
    gates = case["gates"]
    php_state = _seed(connection, php_symbol, php_pair, gates)
    py_state = _seed(connection, py_symbol, py_pair, gates)
    all_ids = (php_state["stock_id"], php_state["pair_id"],
               py_state["stock_id"], py_state["pair_id"])
    return php_symbol, py_symbol, symbols, all_ids, py_state


# ---------------------------------------------------------------------------
# 数行数（两边同一组查询）
# ---------------------------------------------------------------------------
def _counts(connection, symbol, stock_id):
    counts = {}
    for table, column in COUNT_SPEC:
        value = symbol if column == "symbol" else stock_id
        row = _fetchone(
            connection,
            "SELECT COUNT(*) AS n FROM `%s` WHERE `%s` = %%s" % (table, column),
            (value,),
        )
        counts[table] = int(row["n"]) if row else 0
    for table in ("abpair", "adrpair", "ahpair", "fundpair"):
        row = _fetchone(
            connection,
            "SELECT COUNT(*) AS n FROM `%s` WHERE id = %%s OR stock_id = %%s" % table,
            (stock_id, stock_id),
        )
        counts["gate_" + table] = int(row["n"]) if row else 0
    row = _fetchone(
        connection,
        "SELECT COUNT(*) AS n FROM stockgroupitem WHERE stock_id = %s", (stock_id,))
    counts["gate_stockgroupitem"] = int(row["n"]) if row else 0
    return counts


def _run_php(symbol):
    env = dict(os.environ)
    env.setdefault("PALMMICRO_DB_PASSWORD", os.environ.get("PALMMICRO_DB_PASSWORD", ""))
    completed = subprocess.run(
        ["php", "-d", "display_errors=stderr", str(PHP_SCRIPT), symbol],
        capture_output=True, text=True, env=env, cwd=str(SCRATCH_DIR),
    )
    if completed.returncode != 0:
        raise RuntimeError("PHP 探针失败：%s" % completed.stderr.strip()[-2000:])
    return json.loads(completed.stdout.strip().splitlines()[-1]), completed.stderr


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
CASES = (
    {"name": "clean", "gates": {}, "include_orphans": False,
     "memo": "无闸门命中：PHP 删 6 张表，6 张残留不动"},
    {"name": "cascade", "gates": {}, "include_orphans": True,
     "memo": "本版增强：连 6 张残留一起清（PHP 没有这个行为）"},
    {"name": "gate_groupitem", "gates": {"stockgroupitem": 1}, "include_orphans": False,
     "memo": "分组持仓命中：PHP 已删校准值再拒绝；本版不动", "deviation": True},
    {"name": "gate_abpair", "gates": {"abpair": "self"}, "include_orphans": False,
     "memo": "abpair 正向（本符号是 A 股）"},
    {"name": "gate_abpair_rev", "gates": {"abpair": "pair"}, "include_orphans": False,
     "memo": "abpair 反向（本符号是 B 股）"},
    {"name": "gate_ahpair", "gates": {"ahpair": "self"}, "include_orphans": False,
     "memo": "ahpair 正向"},
    {"name": "gate_adrpair", "gates": {"adrpair": "pair"}, "include_orphans": False,
     "memo": "adrpair 反向"},
    {"name": "gate_fundpair", "gates": {"fundpair": "self"}, "include_orphans": False,
     "memo": "fundpair 正向（基金↔指数）"},
    {"name": "gate_all", "include_orphans": False,
     "gates": {"abpair": "self", "fundpair": "self", "stockgroupitem": 1},
     "memo": "三道同时命中：PHP 短路在第一道，本版三道全报；两边都不删"},
)


def _diff(php_counts, py_counts, expect_php_orphans):
    """返回差异列表；``expect_php_orphans`` 为真时忽略残留表（那是设计差异）。"""
    diffs = []
    orphan_tables = {table for table, _column in COUNT_SPEC[6:]}
    for key in sorted(php_counts):
        php_value = php_counts[key]
        py_value = py_counts.get(key)
        if php_value == py_value:
            continue
        if expect_php_orphans and key in orphan_tables:
            continue
        diffs.append("%s: PHP 剩 %s / Python 剩 %s" % (key, php_value, py_value))
    return diffs


def main():
    source = MySqlFundDataSource.from_env()
    connection = source.connection
    failures = 0
    rows = []

    for index, case in enumerate(CASES):
        suffix = "C%d" % index
        php_symbol, py_symbol, symbols, all_ids, py_state = _setup_pair(
            connection, case, suffix)
        try:
            php_out, _stderr = _run_php(php_symbol)
            php_counts = php_out["counts"]

            connection.commit()
            outcome = deletesymbol.apply(
                connection, py_symbol,
                include_orphans=case["include_orphans"], commit=True)
            py_counts = _counts(connection, py_symbol, py_state["stock_id"])

            diffs = _diff(php_counts, py_counts, case["include_orphans"])
            deviation = case.get("deviation", False)
            status = "OK  " if not diffs else ("偏离" if deviation else "FAIL")
            if diffs and not deviation:
                failures += 1

            rows.append((case, php_counts, py_counts, diffs, status))
            print("%s  %-16s %s" % (status, case["name"], case["memo"]))
            print("      PHP 端: %s" % _brief(php_counts))
            print("      Python: %s（%s）" % (
                _brief(py_counts),
                "applied" if outcome["applied"] else "未执行"))
            for diff in diffs:
                print("      · %s" % diff)
        finally:
            _cleanup(connection, symbols, all_ids)
            connection.commit()

    print()
    total = len(CASES)
    print("共 %d 例：一致 %d，按设计偏离 %d，失败 %d" % (
        total,
        sum(1 for item in rows if item[4] == "OK  "),
        sum(1 for item in rows if item[4] == "偏离"),
        failures,
    ))
    return 1 if failures else 0


def _brief(counts):
    """只打非零项，一行看完。"""
    parts = ["%s=%d" % (key, value) for key, value in counts.items() if value]
    return "，".join(parts) if parts else "（各表都已清空）"


if __name__ == "__main__":
    sys.exit(main())
