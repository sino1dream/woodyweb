"""用真实 PHP 逐例对照 `editstockamount`（"基金申购金额"）的写入口。

    cd F:\\woody\\woodyweb\\python\\auto\\_scratch
    D:\\python\\python.exe -B _php_amount_compare.py

对端：`_php_amount_check.php`（拦掉 `CREATE TABLE` 之后整份加载
`sqlmember.php` / `sqlstockgroup.php` / `sqlstocksymbol.php`，跑**真身**
`SqlGetIdByEmail()` / `SqlGetMyStockGroupItemId()` / `GroupItemAmountSql`）。

## 它要回答的问题

这一项的难点不在算术，而在**定位**：`email → member.id → 分组名 = 符号 →
stockgroup.id → 组内那一行 → stockgroupitem.id → groupitemamount.num`。
任何一环断了 PHP 都**静默什么都不做**，页面上看起来却是"提交成功"。
所以对照的重点是"六个用例里，两边**各写了什么、各没写什么**是否一致"。

## 靶子怎么造

演示库里的分组是 ``演示分组A`` / ``演示分组B`` / ``标普500`` —— 没有一个是
**以符号命名**的（生产端"我的自选"才那样），所以第一件事是造一个靶子：

1. 给会员 1（`demo@palmmicro.com`）建分组 ``SPY``；
2. 组里挂 ``SPY``（stock id 5）那一行 —— `stockgroupitem` 的 id 由库自增；
3. 跑完删掉这两行、把 `groupitemamount` 复原到基线。

脚本**先查后造**：`stockgroup` 里已经有同名分组就直接退出（不覆盖真数据）。

## 为什么不用临时表

`GroupItemAmountSql` 的表名写死 `groupitemamount`，`SqlGetMyStockGroupItemId`
查的也是 `stockgroup` / `stockgroupitem`。换表名等于把"真身"换掉，对照就没意义。
所以走"真表 + 快照 + 复原"，与分红/拆股那一组同一套路子。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(os.path.dirname(SCRATCH_DIR))
for _path in (os.path.join(PYTHON_DIR, "auto"), PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pymysql  # noqa: E402

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402
from ref.stockref import _php_strval_float  # noqa: E402
from web.services.admin_auth import ENV_ADMIN_EMAIL  # noqa: E402
from web.services.stock_option_service import StockOptionService  # noqa: E402

PHP_SCRIPT = os.path.join(SCRATCH_DIR, "_php_amount_check.php")
CASE_FILE = os.path.join(SCRATCH_DIR, "_amount_case.json")

#: 演示会员与靶子符号。``SPY`` 没有净值/校准的连带逻辑，只当"分组名"用。
MEMBER_EMAIL = "demo@palmmicro.com"
TARGET_SYMBOL = "SPY"
GROUP_NAME = TARGET_SYMBOL
NO_MEMBER_EMAIL = "nobody@example.com"


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
def connect():
    config = mysql_config_from_env(load_mysql_env(os.environ))
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **config)


def one(connection, sql, params=()):
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        cursor.close()


def execute(connection, sql, params=()):
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.rowcount
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# 靶子（分组名为符号的那一行）
# ---------------------------------------------------------------------------
def prepare(connection):
    """造靶子；返回 ``(member_id, group_id, item_id)``。已有同名分组就退出。"""
    member = one(connection, "SELECT id FROM member WHERE email = %s", (MEMBER_EMAIL,))
    if not member:
        print("演示库里没有 %s —— 先跑 dbtools/seed_stock_group_tables.py" % MEMBER_EMAIL)
        raise SystemExit(2)
    stock = one(connection, "SELECT id FROM stock WHERE symbol = %s", (TARGET_SYMBOL,))
    if not stock:
        print("演示库里没有符号 %s" % TARGET_SYMBOL)
        raise SystemExit(2)
    existing = one(
        connection,
        "SELECT id FROM stockgroup WHERE member_id = %s AND groupname = %s",
        (member["id"], GROUP_NAME))
    if existing:
        print("stockgroup 里已经有 %s / %s（id=%s）—— 不动真数据，退出"
              % (member["id"], GROUP_NAME, existing["id"]))
        raise SystemExit(2)
    group_id = None
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO stockgroup (member_id, groupname) VALUES (%s, %s)",
            (member["id"], GROUP_NAME))
        group_id = cursor.lastrowid
    finally:
        cursor.close()
    item_id = None
    cursor = connection.cursor()
    try:
        #: 与 `StockGroupItemSql::Insert()` 同形：quantity/cost/record 三个默认值。
        cursor.execute(
            "INSERT INTO stockgroupitem (stockgroup_id, stock_id, quantity, cost, record) "
            "VALUES (%s, %s, '0', '0.0', '0')",
            (group_id, stock["id"]))
        item_id = cursor.lastrowid
    finally:
        cursor.close()
    connection.commit()
    return member["id"], group_id, item_id


def cleanup(connection, group_id, baseline):
    execute(connection, "DELETE FROM stockgroupitem WHERE stockgroup_id = %s", (group_id,))
    execute(connection, "DELETE FROM stockgroup WHERE id = %s", (group_id,))
    restore(connection, baseline)


# ---------------------------------------------------------------------------
# groupitemamount 的基线 / 复原 / 快照
# ---------------------------------------------------------------------------
def export_baseline(connection):
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id, num FROM groupitemamount ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()


def restore(connection, baseline):
    execute(connection, "DELETE FROM groupitemamount")
    if baseline:
        cursor = connection.cursor()
        try:
            cursor.executemany(
                "INSERT INTO groupitemamount (id, num) VALUES (%s, %s)",
                [(row["id"], row["num"]) for row in baseline])
        finally:
            cursor.close()
    connection.commit()


def _cell(value):
    if value is None:
        return ""
    try:
        return _php_strval_float(float(value))
    except (TypeError, ValueError):
        return str(value)


def snapshot(connection):
    """与 PHP 侧 ``MakeSnapshot()`` 同构同序。"""
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id, num FROM groupitemamount ORDER BY id")
        return [{"id": str(row["id"]), "num": _cell(row["num"])} for row in cursor.fetchall()]
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# 两侧各跑一遍
# ---------------------------------------------------------------------------
class _Source:
    """`StockOptionService` 只需要 `.connection`（没有快照时）。"""

    def __init__(self, connection):
        self.connection = connection


def run_php(case):
    with open(CASE_FILE, "w", encoding="utf-8") as handle:
        json.dump(case, handle, ensure_ascii=False)
    #: 库口令在 `.env` 里（`load_mysql_env` 读它），子进程默认看不到 ——
    #: 不显式带上就会 `Access denied for user 'root'`。
    env = {**os.environ, **load_mysql_env(os.environ)}
    result = subprocess.run(
        ["php", "-d", "display_errors=stderr", PHP_SCRIPT, CASE_FILE],
        cwd=SCRATCH_DIR, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if result.returncode != 0 or not result.stdout.strip():
        return {"outcome": "php-failed", "error": result.stderr.strip()[-400:],
                "snapshot": []}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"outcome": "bad-json", "error": result.stdout[:400], "snapshot": []}


def run_python(case, connection):
    os.environ[ENV_ADMIN_EMAIL] = case["email"]
    service = StockOptionService(source=_Source(connection))
    messages = []
    for value in case["values"]:
        result = service.submit(case["symbol"], "editstockamount", "", value)
        messages.append(("ok" if result.get("ok") else "refused") + ":" +
                        str(result.get("message")))
    return {"messages": messages, "snapshot": snapshot(connection)}


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def build_cases():
    """十个用例。``values`` 是**按顺序**提交的值（重复提交靠它）。"""
    return [
        {"name": "write_50000", "email": MEMBER_EMAIL, "symbol": TARGET_SYMBOL,
         "values": ["50000"]},
        {"name": "write_zero", "email": MEMBER_EMAIL, "symbol": TARGET_SYMBOL,
         "values": ["0"]},
        {"name": "write_negative", "email": MEMBER_EMAIL, "symbol": TARGET_SYMBOL,
         "values": ["-1"]},
        #: 小数写进 `int` 列：MySQL 自己四舍五入（50000.7 → 50001），
        #: 两边都交给库做，结果必须一样。
        {"name": "write_decimal", "email": MEMBER_EMAIL, "symbol": TARGET_SYMBOL,
         "values": ["50000.7"]},
        #: PHP 不 strip、Python strip —— `' 12 '` 与 `'12'` 在 int 列上同值。
        {"name": "write_spaces", "email": MEMBER_EMAIL, "symbol": TARGET_SYMBOL,
         "values": [" 12 "]},
        #: 同值重提：`WriteString` 字符串比较返回 false，一行都不 UPDATE。
        {"name": "repeat_same", "email": MEMBER_EMAIL, "symbol": TARGET_SYMBOL,
         "values": ["50000", "50000"]},
        #: 同一天两次不同的值：最后一次赢。
        {"name": "repeat_differs", "email": MEMBER_EMAIL, "symbol": TARGET_SYMBOL,
         "values": ["50000", "60000"]},
        {"name": "write_non_numeric", "email": MEMBER_EMAIL, "symbol": TARGET_SYMBOL,
         "values": ["abc"]},
        {"name": "write_empty", "email": MEMBER_EMAIL, "symbol": TARGET_SYMBOL,
         "values": [""]},
        #: 没有「分组名 = 符号」的分组项 → 两边都不写。
        {"name": "no_item_symbol", "email": MEMBER_EMAIL, "symbol": "QQQ",
         "values": ["50000"]},
        #: member 表里没这个邮箱 → 两边都不写。
        {"name": "no_member", "email": NO_MEMBER_EMAIL, "symbol": TARGET_SYMBOL,
         "values": ["50000"]},
    ]


def main():
    connection = connect()
    baseline = export_baseline(connection)
    member_id, group_id, item_id = prepare(connection)
    print("靶子：member=%s group=%s item=%s（分组名 %s）"
          % (member_id, group_id, item_id, GROUP_NAME))
    print("基线：groupitemamount %d 行；符号 %s = stock id %s"
          % (len(baseline), TARGET_SYMBOL,
             one(connection, "SELECT id FROM stock WHERE symbol = %s",
                 (TARGET_SYMBOL,))["id"]))

    matched = []
    failures = []
    try:
        for case in build_cases():
            restore(connection, baseline)
            php = run_php(case)
            restore(connection, baseline)
            py = run_python(case, connection)
            restore(connection, baseline)

            name = case["name"]
            if php["snapshot"] == py["snapshot"]:
                matched.append(name)
                print("= %-18s 一致（%s / %s）"
                      % (name, php["outcome"], py["messages"][-1][:46]))
            else:
                failures.append(name)
                print("! %-18s 差异" % name)
                print("    outcome  PHP %s / PY %s" % (php["outcome"], py["messages"]))
                if php.get("error"):
                    print("    PHP 错误 %s" % php["error"])
                print("    amount   PHP %s" % php["snapshot"])
                print("    amount   PY  %s" % py["snapshot"])
    finally:
        cleanup(connection, group_id, baseline)
        left = one(connection,
                   "SELECT COUNT(*) AS n FROM stockgroup WHERE groupname = %s",
                   (GROUP_NAME,))
        rows = one(connection, "SELECT COUNT(*) AS n FROM groupitemamount")
        print("复原：靶子分组剩 %s 个、groupitemamount %s 行（基线 %d）"
              % (left["n"], rows["n"], len(baseline)))
        connection.close()

    print()
    print("共 %d 例：%d 例一致，%d 例失败%s"
          % (len(matched) + len(failures), len(matched), len(failures),
             ("（" + "、".join(failures) + "）") if failures else ""))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
