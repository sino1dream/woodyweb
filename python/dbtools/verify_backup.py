# -*- coding: utf-8 -*-
"""检验一份 mysqldump 备份**真能还原**：导入临时库 -> 逐表 CHECKSUM -> 删库。

``backup_db.py`` 只能保证"dump 跑完没报错"，不能保证"导回去是好的"。
这两件事的差别，只有真的导一次才知道 —— 所以这个脚本把备份导入一个临时库，
逐表比 ``CHECKSUM TABLE``，比完立刻 DROP。

三个刻意的设计：

* **临时库名带 pid**（``backup_verify_<pid>``），不会和手工建的库撞上；
* **``finally`` 里必 DROP**，绝不在这台库实例上留残库；最后还会查一次
  ``backup_verify*`` 的残留数，不为 0 就说明清理失败；
* **不用中文 SQL 字面量**。Windows 下命令行参数按 ANSI 传，中文进 ``mysql -e``
  会变乱码、永远匹配不到，看着像"备份丢了中文"（pitfalls #129）。
  中文一律用 ``HEX()`` 比字节。

用法::

    python -B dbtools/verify_backup.py E:\\mysqldata\\stock_data_20261026_152033.sql
    python -B dbtools/verify_backup.py <file>.sql --db otherdb
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # python/
sys.path.insert(0, HERE)

import backup_db as bd  # noqa: E402  同一个目录，复用 .env 解析与 cnf 写法


def mysql_runner(client, cnf):
    """返回一个 ``mysql(*args)``：跑一条 SQL，出错时把真实 stderr 抛出来。"""

    def mysql(*args):
        # 必须显式 --default-character-set=utf8mb4：mysql.exe 往管道写字节时
        # 默认跟握手字符集走，不指定拿到的不是 UTF-8，中文会变成问号。
        proc = subprocess.run(
            [client, "--defaults-extra-file=" + cnf,
             "--default-character-set=utf8mb4"] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            raise SystemExit("mysql 失败(%d): %s\n%s" % (
                proc.returncode, " ".join(args),
                proc.stderr.decode("utf-8", "replace").strip()))
        return proc.stdout.decode("utf-8", "replace").strip()

    return mysql


def table_names(mysql, schema):
    rows = mysql("-N", "-B", "-e",
                 "SELECT table_name FROM information_schema.tables "
                 "WHERE table_schema='%s' ORDER BY table_name" % schema)
    return [line for line in rows.splitlines() if line]


def checksum(mysql, schema, table):
    """``CHECKSUM TABLE`` 的返回值里第二列才是校验和。"""
    return mysql("-N", "-B", "-e",
                 "CHECKSUM TABLE `%s`.`%s`" % (schema, table)).split("\t")[-1]


def import_dump(client, cnf, schema, sql_path):
    """把备份灌进临时库，返回 ``(返回码, stderr)``。

    用 ``subprocess.run`` 而不是 ``check_call``：要拿到 stderr 才能说清
    "为什么坏"（半截文件通常报 ``ERROR ... at line N``），只丢一个
    CalledProcessError 的 traceback 等于没说。
    """
    with io.open(sql_path, "rb") as fp:
        proc = subprocess.run(
            [client, "--defaults-extra-file=" + cnf,
             "--default-character-set=utf8mb4", schema],
            stdin=fp, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return proc.returncode, proc.stderr.decode("utf-8", "replace").strip()


def compare(mysql, src_schema, new_schema):
    """比表数、逐表 CHECKSUM、中文 HEX。任何一项不符返回 False。"""
    ok = True
    src_tables = table_names(mysql, src_schema)
    new_tables = table_names(mysql, new_schema)
    print("  表数                 : 源库 %d / 还原 %d%s"
          % (len(src_tables), len(new_tables),
             "" if len(src_tables) == len(new_tables) else "   <== 不一致"))
    if len(src_tables) != len(new_tables):
        ok = False
        missing = sorted(set(src_tables) - set(new_tables))
        if missing:
            print("  还原库缺表           : %s" % missing)

    diff = []
    for name in src_tables:
        a, b = checksum(mysql, src_schema, name), checksum(mysql, new_schema, name)
        if a != b:
            diff.append((name, a, b))
    print("  CHECKSUM 不一致的表  : %s"
          % (diff or "无（%d 张表逐对比过）" % len(src_tables)))
    if diff:
        ok = False

    # 中文按 HEX 抽查：挑第一条中文名（UTF-8 首字节 E4–E9），比字节不比字符串。
    pick = ("SELECT HEX(name) FROM `%s`.stock "
            "WHERE HEX(name) REGEXP '^(E[4-9]|EA|EB)' ORDER BY symbol LIMIT 1")
    hex_src = mysql("-N", "-B", "-e", pick % src_schema)
    hex_new = mysql("-N", "-B", "-e", pick % new_schema)
    print("  中文 HEX 抽查        : %s"
          % ("一致" if hex_src and hex_src == hex_new
             else "不一致 源=%s 还原=%s" % (hex_src, hex_new)))
    if not hex_src or hex_src != hex_new:
        ok = False
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description="验证 mysqldump 备份能否还原")
    parser.add_argument("sql", help="备份文件路径")
    parser.add_argument("--db", default="stock_data", help="源库名（默认 stock_data）")
    parser.add_argument("--env", default=os.path.join(ROOT, "auto", ".env"),
                        help="连接配置（默认 python/auto/.env）")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.sql):
        raise SystemExit("备份文件不存在：%s" % args.sql)
    if not os.path.isfile(args.env):
        raise SystemExit("找不到配置文件：%s" % args.env)

    env = bd.read_env_file(args.env)
    host = env.get("PALMMICRO_DB_HOST") or "127.0.0.1"
    port = env.get("PALMMICRO_DB_PORT") or "3306"
    user = env.get("PALMMICRO_DB_USER") or "root"
    password = env.get("PALMMICRO_DB_PASSWORD", "")
    if not password:
        raise SystemExit("%s 里没有 PALMMICRO_DB_PASSWORD" % args.env)

    client = os.path.join(os.path.dirname(bd.find_mysqldump()), "mysql.exe")
    tmp_db = "backup_verify_%d" % os.getpid()
    cnf = bd.write_defaults_file(host, port, user, password)
    mysql = mysql_runner(client, cnf)
    ok = True

    try:
        mysql("-e", "DROP DATABASE IF EXISTS `%s`" % tmp_db)
        mysql("-e", "CREATE DATABASE `%s` DEFAULT CHARACTER SET utf8mb4" % tmp_db)
        code, err = import_dump(client, cnf, tmp_db, args.sql)
        if code != 0:
            # 半截文件在这一步就炸 —— 这正是要抓的情况，别让它以 traceback 收场。
            print("  导入                 : 失败（返回码 %d）" % code)
            for line in err.splitlines()[:3]:
                print("    " + line)
            ok = False
        else:
            print("导入完成 -> %s" % tmp_db)
            ok = compare(mysql, args.db, tmp_db) and ok
    finally:
        # 顺序要紧：先 DROP、再查残留、最后删 cnf。反过来会让"查残留"
        # 因为 defaults 文件已被删而 abort，掩盖真正的报错。
        try:
            mysql("-e", "DROP DATABASE IF EXISTS `%s`" % tmp_db)
            left = mysql("-N", "-B", "-e",
                         "SELECT COUNT(*) FROM information_schema.schemata "
                         "WHERE schema_name LIKE 'backup_verify%'")
        except SystemExit as exc:
            left = "查询失败(%s)" % str(exc).splitlines()[0]
            ok = False
        finally:
            if os.path.exists(cnf):
                os.unlink(cnf)
        print("  临时库已删除         : 残留 backup_verify* 库数 = %s" % left)

    print("[%s] %s" % ("通过" if ok else "不通过", args.sql))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
