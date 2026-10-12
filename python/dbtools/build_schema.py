"""按 ``schema.sql`` 在目标库建出核心表。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B dbtools\\build_schema.py            # 建表（已存在则跳过）
    python -B dbtools\\build_schema.py --dry-run  # 只打印将要执行的语句

设计要点：

* schema.sql 里全是 ``CREATE TABLE IF NOT EXISTS``，所以本脚本可重复执行，
  **不会删除或覆盖已有数据**；
* 语句按 ``;`` 切分后逐条执行，跳过空行与 ``--`` 注释；
* 连接配置复用 ``python/auto/.env``，与 Web 层完全一致，避免两处配置漂移。
"""

from __future__ import annotations

import io
import os
import sys

# 命令行脚本会打印中文，Windows 控制台默认编码可能是 GBK，这里强制 UTF-8，
# 避免"建表进度"变成乱码（库模块不打印，不要照抄这段）。
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SQL_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SQL_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402

SCHEMA_FILE = os.path.join(SQL_DIR, "schema.sql")


def split_statements(text: str) -> list:
    """把 schema.sql 切成可执行语句。

    只在行首是 ``--`` 的整行注释会被丢掉；语句内部不再有注释，
    所以按 ``;`` 简单切分即可（建表语句里没有字符串字面量含 ``;``）。
    """
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        lines.append(line)
    body = "\n".join(lines)

    statements = []
    for chunk in body.split(";"):
        statement = chunk.strip()
        if statement:
            statements.append(statement)
    return statements


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    with open(SCHEMA_FILE, "r", encoding="utf-8") as handle:
        statements = split_statements(handle.read())

    print("schema.sql 里共 %d 条语句" % len(statements))
    if dry_run:
        for statement in statements:
            first_line = statement.splitlines()[0]
            print("  " + first_line)
        return 0

    config = mysql_config_from_env(load_mysql_env(os.environ))
    print("目标库：%s@%s:%s/%s" % (config["user"], config["host"], config["port"], config["database"]))

    import pymysql

    connection = pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        charset="utf8",
        cursorclass=pymysql.cursors.DictCursor,
    )
    created = 0
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                table = _table_name(statement)
                if table is None:
                    # 例如 SET NAMES utf8：直接执行，不参与建表统计
                    cursor.execute(statement)
                    continue
                cursor.execute(
                    "SELECT COUNT(*) AS n FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = %s",
                    (config["database"], table),
                )
                existed = cursor.fetchone()["n"] > 0
                cursor.execute(statement)
                if existed:
                    print("  已存在，跳过：%s" % table)
                else:
                    print("  已建立：%s" % table)
                    created += 1
        connection.commit()
    finally:
        connection.close()

    print("完成：新建 %d 张表，其余已存在。" % created)
    return 0


def _table_name(statement: str):
    """从 CREATE TABLE 语句里取出表名；不是建表语句时返回 ``None``。"""
    marker = "CREATE TABLE IF NOT EXISTS `"
    start = statement.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = statement.find("`", start)
    return statement[start:end]


if __name__ == "__main__":
    sys.exit(main())
