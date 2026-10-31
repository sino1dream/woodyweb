"""``etfdividendcn.php`` 的 PHP ↔ Python 逐例对照。

## 为什么需要造数据

演示库 ``stockdividend`` 只有 4 行，``stock_id`` 是 182 / 184 / 186 / 214 ——
全部是孤儿（``stock`` 表最大 id 181），任何符号按 id 都查不到。所以不插几行
干净数据，"有行 / 跳过行 / rtrim0 / 比例舍入"这四条分支一条都验不到。

## 怎么保证不弄脏库

1. 先记下插入前的 ``COUNT(*)`` 与 ``MAX(id)``；
2. 插三行（都挂在 QQQ 上，id=4）；
3. 跑 PHP 探针 + Python 服务比对；
4. ``finally`` 里按 id 删掉刚插的三行，再校验 ``COUNT(*)`` 与
   ``MAX(id)`` 都回到原值 —— 中途异常也照样恢复。

改库之前另有一份 ``mysqldump`` 落在系统临时目录
（``stockdividend_before_20261031.sql``），真出事可以整表还原。

## 三行数据分别验什么

===========  ==========  ================================================
日期          分红         验证点
===========  ==========  ================================================
2026-09-21    0.751474    QQQ 那天有收盘价 → 正常出四格
2026-09-03    0.500       有收盘价；分红过 ``rtrim0`` 应显示 ``0.5``（不是 0.500）
2020-01-01    0.300       当天没有收盘价 → **整行跳过**，只进 skipped
===========  ==========  ================================================

用法：

    D:/python/python.exe -B python/auto/_scratch/_php_etfdividend_compare.py

stdout 打对照明细；退出码 0 表示全一致。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_AUTO = _ROOT / "python" / "auto"
sys.path.insert(0, str(_AUTO))
sys.path.insert(0, str(_ROOT / "python"))

from core.stockdata import MySqlFundDataSource, load_mysql_env  # noqa: E402
from ref.refobj import MysqlReference  # noqa: E402
from web.services.etf_dividend_service import build_etf_dividend  # noqa: E402


SYMBOL = "QQQ"
PROBE_ROWS = [
    ("2026-09-21", "0.751474"),
    ("2026-09-03", "0.500"),
    ("2020-01-01", "0.300"),
]


def _php_rows(env: dict) -> dict:
    """跑 PHP 探针，拿 JSON。"""
    #: 必须 resolve()：本脚本常以相对路径被调用，``__file__`` 也是相对的，
    #: 而子进程的 cwd 又被改成探针目录，相对路径会解析不到。
    script = str(Path(__file__).resolve().with_name("_php_etfdividend_check.php"))
    proc = subprocess.run(
        ["php", "-d", "display_errors=stderr", script, SYMBOL],
        cwd=str(Path(script).parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        # 探针失败时把两端输出都摊开 —— 只写 stderr 的话会被调用方的 tail 截掉。
        print("PHP 探针 rc=%d" % proc.returncode)
        print("STDOUT>>>")
        print(proc.stdout.decode("utf-8", "replace"))
        print("STDERR>>>")
        print(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP 探针失败 rc=%d" % proc.returncode)
    text = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    return json.loads(text[-1])


def _insert_probe_rows(connection, stock_id: int) -> list:
    """插入探针行，返回它们的 id（用于 finally 里精确删除）。"""
    cursor = connection.cursor()
    ids = []
    try:
        for date, close in PROBE_ROWS:
            cursor.execute(
                "INSERT INTO `stockdividend` (`stock_id`, `date`, `close`) VALUES (%s, %s, %s)",
                (stock_id, date, close),
            )
            ids.append(cursor.lastrowid)
    finally:
        cursor.close()
    connection.commit()
    return ids


def _delete_probe_rows(connection, ids: list) -> None:
    if not ids:
        return
    cursor = connection.cursor()
    try:
        cursor.execute(
            "DELETE FROM `stockdividend` WHERE `id` IN (%s)" % ",".join(["%s"] * len(ids)),
            tuple(ids),
        )
    finally:
        cursor.close()
    connection.commit()


def _snapshot(connection) -> tuple:
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT COUNT(*) AS n, COALESCE(MAX(`id`), 0) AS m FROM `stockdividend`")
        row = cursor.fetchone()
        return int(row["n"]), int(row["m"])
    finally:
        cursor.close()


def main() -> int:
    env = {**os.environ, **load_mysql_env(os.environ)}
    if env.get("PALMMICRO_DB_PASSWORD"):
        # mysqldump / mysql 系客户端认 MYSQL_PWD，不认 PALMMICRO_DB_*
        env["MYSQL_PWD"] = env["PALMMICRO_DB_PASSWORD"]

    source = MySqlFundDataSource.from_env()
    connection = source.connection
    before = _snapshot(connection)

    ref = MysqlReference(SYMBOL, source=source)
    stock_id = ref.get_stock_id()
    if not stock_id:
        raise SystemExit("演示库里没有 %s" % SYMBOL)

    php = {"rows": [], "skipped": [], "net_value_count": 0}
    ids: list = []
    try:
        ids = _insert_probe_rows(connection, stock_id)
        php = _php_rows(env)

        page = build_etf_dividend(SYMBOL, source=source, ref=ref)
        if page is None or page["table"] is None:
            raise SystemExit("Python 侧没有出表：page=%r" % (page,))
        py_rows = [row.cells for row in page["table"]["rows"]]
        py_table_id = page["table"]["id"]
        py_link = page["dividend_link"]
    finally:
        _delete_probe_rows(connection, ids)
        after = _snapshot(connection)

    ok = True

    def _check(label, got, expected):
        nonlocal ok
        flag = got == expected
        if not flag:
            ok = False
        print("%-28s %s  %r %s %r" % (label, "OK  " if flag else "FAIL", got,
                                      "==" if flag else "!=", expected))

    print("== 行内容 ==")
    #: 期望值写死：演示库 QQQ 这两天收盘价是 741.470 / 717.670（``double(10,3)``，
    #: mysqli 给的是补零到三位的字符串），比例按 ``number_format(x, 2)`` 舍入
    #: → 0.10 / 0.07。
    #: 「分红」那一格 0.500 必须显示成 0.5 —— `rtrim0()` 生效；
    #: 「价格」那一格原样三位，不 trim。
    expected = [
        ["2026-09-21", "0.751474", "741.470", "0.10"],
        ["2026-09-03", "0.5", "717.670", "0.07"],
    ]
    _check("PHP rows", [list(r) for r in php["rows"]], expected)
    _check("PHP 跳过 2020-01-01", php["skipped"], ["2020-01-01"])
    _check("Python rows 数", len(py_rows), len(expected))
    _check("Python rows", py_rows, expected)
    _check("表 id", py_table_id, SYMBOL + "etfdividend")
    _check("外链（QQQ 是美股，应为空）", py_link, None)
    _check("净值条数", php["net_value_count"], ref.count_net_value())

    print("== 库已恢复 ==")
    _check("stockdividend 行数", after[0], before[0])
    _check("stockdividend max(id)", after[1], before[1])

    print("\n%s" % ("全部一致" if ok else "有差异，见上"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
