"""执行建库之后的补丁 SQL（给已存在的表加列之类）。

用法：

    cd F:\\woody\\woodyweb\\python
    D:\\python\\python.exe -B sql\\apply_patch.py sql\\patch_realtimequote_orderbook.sql
    D:\\python\\python.exe -B sql\\apply_patch.py sql\\patch_xxx.sql --dry-run

为什么要有它：``schema.sql`` 全是 ``CREATE TABLE IF NOT EXISTS``，
``build_schema.py`` 重跑也不会改动已存在的表（这是刻意的 —— 建表脚本不许动数据）。
所以"给旧库补一列"这类变更得单独走补丁。

幂等的处理：MySQL **没有** ``ADD COLUMN IF NOT EXISTS``，重复执行会抛
``Duplicate column name 'xxx'``（1060）。这里把它当成"已经打过了"跳过，
于是补丁可以放心重复跑，跟其它 sql 脚本的"可重复执行"约定保持一致。
"""

from __future__ import annotations

import io
import os
import sys

# 命令行脚本会打印中文，Windows 控制台默认编码可能是 GBK，这里强制 UTF-8。
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SQL_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SQL_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402

#: MySQL 错误码 1060：列已存在。重复打补丁不算失败。
ER_DUP_FIELDNAME = 1060


def split_statements(text: str) -> list:
    """按 ``;`` 切分，丢掉整行 ``--`` 注释（与 build_schema.split_statements 同款）。"""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        lines.append(line)
    return [chunk.strip() for chunk in "\n".join(lines).split(";") if chunk.strip()]


def main() -> int:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    if not args:
        print("用法：python -B sql\\apply_patch.py <补丁.sql> [--dry-run]")
        return 2

    path = args[0]
    if not os.path.isabs(path):
        path = os.path.join(SQL_DIR, os.path.basename(path))
    with open(path, "r", encoding="utf-8") as handle:
        statements = split_statements(handle.read())

    print("%s 里共 %d 条语句" % (os.path.basename(path), len(statements)))
    if dry_run:
        for statement in statements:
            print("  " + statement.splitlines()[0])
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
    applied = 0
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                try:
                    cursor.execute(statement)
                except pymysql.err.OperationalError as exc:
                    code = exc.args[0] if exc.args else None
                    if code != ER_DUP_FIELDNAME:
                        raise
                    print("  已打过，跳过：%s" % statement.splitlines()[0])
                    continue
                print("  已执行：%s" % statement.splitlines()[0])
                applied += 1
        connection.commit()
    finally:
        connection.close()

    print("完成：本次执行 %d 条。" % applied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
