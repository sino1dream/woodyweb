"""CK4-D：把被误删的 ``stockhistory`` 行从备份里捞回来。

## 事故经过

``_ck4_sample.py`` 里清旧价那句写成了::

    DELETE FROM stockhistory
     WHERE stock_id IN (SELECT id FROM stock) AND `date` = %s AND stock_id != %s

**``stock_id IN (SELECT id FROM stock)`` 等于没写条件** —— 于是把 2026-10-13
那天**所有**符号的历史行都清了（原本只想清 36 只成分股的），中间层
``^NDX`` / ``^GSPC`` / ``QQQ`` / ``SPY`` / ``hf_NQ`` 那几行一起没了。

## 为什么能捞回来

写库前按 AGENTS.md 的约定全库 dump 了一份
（``dbtools/_snapshots/before_20261015_ck4_holdings.sql``）。
做法是把备份导进一个临时库，再按日期把缺的行插回主库 ——
**只补缺失的**（``INSERT IGNORE``），成分股那 36 行不动。

    python -B auto/_scratch/_ck4_restore.py
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import (  # noqa: E402
    MySqlFundDataSource,
    load_mysql_env,
    mysql_config_from_env,
)

BACKUP = os.path.join(
    PYTHON_DIR, "dbtools", "_snapshots", "before_20261015_ck4_holdings.sql")
RESTORE_DB = "ck4_restore"
#: 被误删的那天
DAY = "2026-10-13"


def main() -> int:
    config = mysql_config_from_env(load_mysql_env(os.environ))
    mysql = [
        "mysql",
        "-h", str(config["host"]),
        "-P", str(config["port"]),
        "-u", str(config["user"]),
        "-p%s" % config["password"],
    ]

    print("== 1. 建临时库 %s 并导入备份 ==" % RESTORE_DB)
    subprocess.run(mysql + ["-e", "DROP DATABASE IF EXISTS `%s`" % RESTORE_DB], check=True)
    subprocess.run(mysql + ["-e", "CREATE DATABASE `%s`" % RESTORE_DB], check=True)
    with open(BACKUP, encoding="utf-8") as handle:
        result = subprocess.run(mysql + [RESTORE_DB], stdin=handle,
                                stderr=subprocess.PIPE)
    if result.returncode != 0:
        print("导入失败：%s" % result.stderr.decode("utf-8", "replace"))
        return 1
    print("   备份已导入")

    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    def count(sql, params=()):
        cursor.execute(sql, params)
        return list(cursor.fetchall())[0]

    print()
    print("== 2. 对比 %s 那天的行数 ==" % DAY)
    cursor.execute(
        "SELECT COUNT(*) AS n FROM `%s`.stockhistory WHERE `date` = %%s" % RESTORE_DB,
        (DAY,))
    before = cursor.fetchone()["n"]
    cursor.execute("SELECT COUNT(*) AS n FROM stockhistory WHERE `date` = %s", (DAY,))
    now = cursor.fetchone()["n"]
    print("   备份里 %d 行，主库现在 %d 行，缺 %d 行" % (before, now, before - now))

    print()
    print("== 3. 只补缺失的（INSERT IGNORE，已有的不动）==")
    cursor.execute(
        "INSERT IGNORE INTO stockhistory (stock_id, `date`, close, volume, adjclose) "
        "SELECT stock_id, `date`, close, volume, adjclose "
        "FROM `%s`.stockhistory WHERE `date` = %%s" % RESTORE_DB, (DAY,))
    print("   影响 %d 行" % cursor.rowcount)
    source.connection.commit()

    cursor.execute("SELECT COUNT(*) AS n FROM stockhistory WHERE `date` = %s", (DAY,))
    print("   主库现在 %d 行（备份 %d 行）" % (cursor.fetchone()["n"], before))

    cursor.execute(
        "SELECT s.symbol FROM stockhistory h JOIN stock s ON s.id = h.stock_id "
        "WHERE h.`date` = %s AND s.symbol IN ('^NDX','^GSPC','QQQ','SPY','hf_NQ')",
        (DAY,))
    back = [r["symbol"] for r in cursor.fetchall()]
    print("   中间层/期货回来没有：%s" % (back or "还是没有！"))

    cursor.close()
    source.connection.close()

    print()
    print("== 4. 删临时库 ==")
    subprocess.run(mysql + ["-e", "DROP DATABASE `%s`" % RESTORE_DB], check=True)
    print("   已删")
    return 0


if __name__ == "__main__":
    sys.exit(main())
