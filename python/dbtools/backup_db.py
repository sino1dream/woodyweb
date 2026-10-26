# -*- coding: utf-8 -*-
"""一键备份本地数据库（mysqldump -> 带时间戳的 .sql）。

为什么不直接手敲 ``mysqldump``：

* **密码不进命令行**。写在参数里会同时出现在进程列表和
  ``[Warning] Using a password on the command line interface can be insecure``
  那句警告里。本脚本写一个临时 ``--defaults-extra-file``，dump 结束立刻删掉。
* **时间戳不依赖 Windows 的 ``%date%``**。中文 locale 下那是 ``2026/10/26 周一``，
  拼不出可排序的文件名，还要处理斜杠。Python 的 ``strftime`` 一步到位。
* **失败要删半截文件**。dump 中断留下的半截 sql 最危险 —— 目录里看着"有备份"，
  真出事时导入到一半才发现缺表。本脚本对返回码 + 结尾 ``Dump completed`` +
  ``CREATE TABLE`` 计数三道校验，任何一道不过就把文件删掉。
* **重备份不会淹掉目录**。可选的 ``--keep N`` 只保留最近 N 份，
  且只认本脚本自己的命名（``<库>_YYYYmmdd_HHMMSS.sql``），不碰你手动放在
  同一目录里的其它 sql。

实测环境（2026-10-26）：XAMPP 自带的 **MariaDB 10.4.21**，
mysqldump 位于 ``D:\\xampp\\mysql\\bin\\mysqldump.exe``（Distrib 10.19）。

**参数集是给 MariaDB 挑的**：MariaDB 的 mysqldump 没有 MySQL 8 的
``--no-tablespaces`` 和 ``--column-statistics``，照抄 MySQL 的教程会直接
``unknown option`` 报错退出。

**默认锁表而不是用 ``--single-transaction``**，因为演示库 30 张表里 28 张是
**MyISAM**（只有 2 张 InnoDB）：MyISAM 不支持事务，``--single-transaction``
对它无效，而且会顺带关掉 mysqldump 默认的 ``--lock-tables``，等于让这 28 张表
在导出期间既无快照也无锁 —— 拿到不一致的备份也不会报错。详见 ``run_dump``。

用法::

    python -B dbtools/backup_db.py                    # 备份 stock_data 到 E:\\mysqldata
    python -B dbtools/backup_db.py --keep 30          # 只保留最近 30 份
    python -B dbtools/backup_db.py --db otherdb       # 换个库
    python -B dbtools/backup_db.py --dry-run          # 只打印命令，不执行

也可以直接双击同目录的 ``backup_db.bat``。
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

#: 默认备份目录（用户指定）。
DEFAULT_OUT = r"E:\mysqldata"

#: .env 是唯一的口径来源，别在这里再抄一份密码。
DEFAULT_ENV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "python", "auto", ".env",
)

#: mysqldump 的候选位置。PATH 里找不到时按这个顺序找 —— 双击 .bat 启动时
#: 继承的是系统 PATH，和 git bash 里的 PATH 不一定一样，留个兜底。
DUMP_CANDIDATES = (
    r"D:\xampp\mysql\bin\mysqldump.exe",
    r"C:\xampp\mysql\bin\mysqldump.exe",
    r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe",
    r"C:\Program Files\MySQL\MySQL Server 5.7\bin\mysqldump.exe",
)

#: 本脚本产出文件的命名。``--keep`` 只删匹配这个模式的，别的文件一概不碰。
NAME_PATTERN = re.compile(r"^(?P<db>\w+)_(?P<stamp>\d{8}_\d{6})\.sql$")

#: 结尾标记。MariaDB / MySQL 都会在最后写一行 ``-- Dump completed on ...``，
#: 少了它说明 dump 中途被掐断（比如磁盘满、连接断）。
COMPLETE_MARK = b"Dump completed"


def read_env_file(path: str) -> dict:
    """解析 .env（``KEY=VALUE`` 一行一对）。

    不用 python-dotenv 是为了让脚本能单独跑起来 —— 它待在 dbtools 里，
    不该依赖 auto 那套环境。去掉引号、跳过注释和空行即可，够用了。
    """
    values = {}
    with io.open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[key.strip()] = value
    return values


def find_mysqldump(explicit: str = "") -> str:
    """定位 mysqldump.exe：显式参数 > PATH > 已知安装位置。"""
    if explicit:
        if not os.path.isfile(explicit):
            raise SystemExit("找不到 mysqldump：%s" % explicit)
        return explicit

    found = shutil.which("mysqldump")
    if found:
        return found

    for candidate in DUMP_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate

    raise SystemExit(
        "PATH 里没有 mysqldump，也没在常见位置找到。\n"
        "用 --mysqldump <完整路径> 指定，例如：\n"
        "    --mysqldump D:\\xampp\\mysql\\bin\\mysqldump.exe"
    )


def quote_option_value(value: str) -> str:
    """把值写进 MySQL option 文件时需要的引号。

    option 文件的规则比 shell 更怪：值里有 ``#``（注释）、空格、引号都要包起来，
    反斜杠和双引号要转义。密码里出现这些字符时，不处理就会被截断成半个密码，
    然后报一个跟真实原因毫无关系的 "Access denied"。
    """
    if not value:
        return '""'
    needs = any(ch in value for ch in ' #"\'\t')
    if not needs:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"%s"' % escaped


def write_defaults_file(host: str, port: str, user: str, password: str) -> str:
    """写临时 client 配置文件，返回路径（调用方负责删除）。"""
    content = (
        "[client]\n"
        "host=%s\n"
        "port=%s\n"
        "user=%s\n"
        "password=%s\n"
        % (quote_option_value(host), quote_option_value(port),
           quote_option_value(user), quote_option_value(password))
    )
    # delete=False：Windows 上 NamedTemporaryFile 打开期间别的进程读不了，
    # mysqldump 要单独读它，所以先写盘、关句柄、再交给 mysqldump。
    handle, path = tempfile.mkstemp(prefix=".mysqldump_", suffix=".cnf")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fp:
            fp.write(content)
    except Exception:
        os.unlink(path)
        raise
    return path


def count_create_table(path: str) -> int:
    """数一数导出的文件里有几张表。

    用来交叉验证“备份是否完整”：库里 30 张表，导出的文件也该有 30 个
    ``CREATE TABLE``。数字对不上就值得看一眼，而不是等恢复时才发现。
    """
    total = 0
    with io.open(path, "rb") as fp:
        for line in fp:
            if line.startswith(b"CREATE TABLE"):
                total += 1
    return total


def prune_old(out_dir: str, db: str, keep: int, verbose: bool) -> list:
    """只保留最近 ``keep`` 份本脚本产出的备份，返回被删掉的文件名。

    ``keep <= 0`` 表示不清理 —— 默认值。删文件这件事必须有明确的数字才做，
    而且只认 ``<db>_YYYYmmdd_HHMMSS.sql``；同一目录下手动放的 sql 一律无视。
    """
    if keep <= 0:
        return []

    names = []
    for name in os.listdir(out_dir):
        match = NAME_PATTERN.match(name)
        if match and match.group("db") == db:
            names.append(name)
    names.sort(reverse=True)  # 时间戳定宽，字典序倒排即新到旧

    removed = []
    for name in names[keep:]:
        os.remove(os.path.join(out_dir, name))
        removed.append(name)
        if verbose:
            print("  清理旧备份：%s" % name)
    return removed


def run_dump(dump: str, defaults: str, db: str, out_path: str,
             single_transaction: bool = False) -> tuple:
    """跑 mysqldump，返回 ``(返回码, stderr 文本)``。

    **一致性策略是本函数最要紧的一件事**，因为演示库 30 张表里
    **28 张是 MyISAM、只有 2 张 InnoDB**（2026-10-26 实测）：

    * MyISAM **不支持事务**，所以 ``--single-transaction`` 对它一点用都没有；
      更糟的是 mysqldump 的 ``--single-transaction`` 会**顺带关掉默认开启的
      ``--lock-tables``**，于是 28 张 MyISAM 表在导出期间既没事务快照、
      也没表锁 —— 期间有任何写入（daemon、手写脚本）都会拿到跨表不一致的
      备份，而且**不报错**。
    * 所以默认走 ``--lock-tables``：mysqldump 会一次性
      ``LOCK TABLES t1 READ, t2 READ, ...``，跨表停在同一个时刻。
      代价是导出期间表只读 —— 本地库 0.3 秒，可以忽略。
    * 纯 InnoDB 的库（或备份耗时长、不能阻塞写入的场景）用
      ``--single-transaction`` 更好，通过 ``--single-transaction`` 参数切换。

    其余参数：

    * ``--quick`` —— 逐行吐数据，不把整张表读进内存；
    * ``--default-character-set=utf8mb4`` —— 中文（基金名称）不变成 ``???``；
    * ``--routines --triggers --events`` —— 存储过程/触发器/事件一起带上，
      漏了它们恢复出来的库是"半残"的（本项目当前没有，但备份不该挑着做）；
    * ``--hex-blob`` —— 二进制列安全导出。

    输出走 stdout 重定向而不是 ``--result-file``：后者在 Windows 上会做
    ``\\n`` -> ``\\r\\n`` 转换，历史上还吃过这个亏。
    """
    cmd = [dump, "--defaults-extra-file=%s" % defaults]
    cmd.extend(_dump_options(single_transaction))
    cmd.append(db)

    with io.open(out_path, "wb") as fp:
        process = subprocess.Popen(
            cmd, stdout=fp, stderr=subprocess.PIPE,
            universal_newlines=False,
        )
        _, stderr = process.communicate()
    text = stderr.decode("utf-8", "replace") if stderr else ""
    return process.returncode, text


def _dump_options(single_transaction: bool) -> list:
    """mysqldump 的选项表（单独拎出来，``--dry-run`` 也能打印）。"""
    options = [
        "--quick",
        "--default-character-set=utf8mb4",
        "--routines",
        "--triggers",
        "--events",
        "--hex-blob",
    ]
    if single_transaction:
        options.insert(0, "--single-transaction")
    else:
        options.insert(0, "--lock-tables")
    return options


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%.0f %s" % (size, unit)) if unit == "B" else ("%.2f %s" % (size, unit))
        size /= 1024


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="mysqldump 一键备份，输出 <库名>_<时间戳>.sql",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="备份目录（默认 %s）" % DEFAULT_OUT)
    parser.add_argument("--db", default="",
                        help="库名（默认取 .env 的 PALMMICRO_DB_NAME，没配则是 stock_data）")
    parser.add_argument("--env", default=DEFAULT_ENV,
                        help="配置文件路径（默认 python/auto/.env）")
    parser.add_argument("--mysqldump", default="",
                        help="mysqldump.exe 完整路径（默认从 PATH 找）")
    parser.add_argument("--keep", type=int, default=0,
                        help="只保留最近 N 份备份；0（默认）= 不清理")
    parser.add_argument("--single-transaction", action="store_true",
                        dest="single_transaction",
                        help="用事务快照代替锁表。**只对纯 InnoDB 库有效** —— "
                             "演示库 28/30 张是 MyISAM，开了反而丢一致性")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要执行的命令，不真的导出")
    parser.add_argument("--quiet", action="store_true", help="少打印")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    verbose = not args.quiet

    # 1) 配置
    if not os.path.isfile(args.env):
        print("[错误] 找不到配置文件：%s" % args.env)
        print("       用 --env 指定，例如 --env F:\\woody\\woodyweb\\python\\auto\\.env")
        return 2
    env = read_env_file(args.env)
    host = env.get("PALMMICRO_DB_HOST") or "127.0.0.1"
    port = env.get("PALMMICRO_DB_PORT") or "3306"
    user = env.get("PALMMICRO_DB_USER") or "root"
    password = env.get("PALMMICRO_DB_PASSWORD", "")
    db = args.db or env.get("PALMMICRO_DB_NAME") or "stock_data"
    if not password:
        print("[错误] %s 里没有 PALMMICRO_DB_PASSWORD" % args.env)
        return 2

    # 2) 目录（E:\ 不存在时给出人话提示，别抛 WinError 3）
    out_dir = args.out
    try:
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
    except OSError as exc:
        print("[错误] 备份目录不可用：%s（%s）" % (out_dir, exc))
        return 2

    dump = find_mysqldump(args.mysqldump)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = "%s_%s.sql" % (db, stamp)
    out_path = os.path.join(out_dir, name)

    if verbose:
        print("mysqldump : %s" % dump)
        print("连接       : %s@%s:%s" % (user, host, port))
        print("目标库     : %s" % db)
        print("一致性     : %s" % ("single-transaction（事务快照，不锁表）"
                                   if args.single_transaction
                                   else "lock-tables（导出期间表只读，MyISAM 也安全）"))
        print("输出       : %s" % out_path)

    if args.dry_run:
        print("（--dry-run：不执行）")
        print("命令       : mysqldump %s %s"
              % (" ".join(_dump_options(args.single_transaction)), db))
        return 0

    # 3) 导出
    tmp_cnf = write_defaults_file(host, port, user, password)
    started = time.time()
    try:
        code, stderr = run_dump(dump, tmp_cnf, db, out_path,
                                args.single_transaction)
    finally:
        # 无论成败都删 —— 里面是明文密码。
        if os.path.exists(tmp_cnf):
            os.unlink(tmp_cnf)
    elapsed = time.time() - started

    def fail(message):
        """失败时把半截文件删掉：留着比没有更糟。"""
        if os.path.exists(out_path):
            os.unlink(out_path)
        print("[失败] %s" % message)
        if stderr:
            for line in stderr.strip().splitlines():
                print("        " + line)
        return 1

    if code != 0:
        return fail("mysqldump 返回码 %d" % code)

    size = os.path.getsize(out_path)
    if size == 0:
        return fail("导出文件是空的（0 字节）")

    tables = count_create_table(out_path)
    with io.open(out_path, "rb") as fp:
        fp.seek(max(0, size - 4096))
        tail = fp.read()
    if COMPLETE_MARK not in tail:
        return fail("文件结尾没有 %s，说明导出被中途截断"
                    % COMPLETE_MARK.decode("ascii"))
    if tables == 0:
        return fail("文件里没有任何 CREATE TABLE，库名对了吗？（%s）" % db)

    print("[完成] %s" % out_path)
    print("       %s / %d 张表 / 耗时 %.1f 秒"
          % (human_size(size), tables, elapsed))

    removed = prune_old(out_dir, db, args.keep, verbose)
    if args.keep > 0 and verbose:
        print("       保留策略：keep=%d，本次清理 %d 份"
              % (args.keep, len(removed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
