"""Plan A（fundest 日期口径对齐生产端）动手前的备份。

按 AGENTS.md 约定：**动演示库数据前先 mysqldump**，落到
``dbtools/_snapshots/``（该目录已 gitignore）。

本次 Plan A 会写这些表：

* ``netvaluehistory`` —— 33 只基金 NAV 回填 + 中间层派生净值（sync-nav）
  + USCNY 汇率回填；
* ``stockhistory`` / ``stockhistorydate`` —— A 股/场内基金价格补历史 +
  中间层行情归档；
* ``fundest`` —— 官方 EST 记录（fundestcli）。

    python -B auto/_scratch/_plan_a_backup.py
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

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402

#: 与 ``bootstrap_fund_data.py`` 同一张表清单，另加 ``stockhistorydate``
#: （``--archive-snapshot`` 会写它）。
TABLES = ("stock", "fundpair", "netvaluehistory", "stockhistory",
          "stockhistorydate", "calibrationhistory", "fundest",
          "realtimequote", "estquote", "lastcalibration")


def main() -> int:
    config = mysql_config_from_env(load_mysql_env(os.environ))
    snapshot_dir = os.path.join(PYTHON_DIR, "dbtools", "_snapshots")
    os.makedirs(snapshot_dir, exist_ok=True)
    output = os.path.join(snapshot_dir, "before_20261016_plan_a.sql")
    command = [
        "mysqldump",
        "-h", str(config["host"]),
        "-P", str(config["port"]),
        "-u", str(config["user"]),
        "-p%s" % config["password"],
        "--skip-lock-tables",
        str(config["database"]),
    ] + list(TABLES)
    with open(output, "w", encoding="utf-8") as handle:
        result = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print("备份失败：%s" % result.stderr.decode("utf-8", "replace"))
        return 1
    print("已备份到 %s（%d 字节）" % (output, os.path.getsize(output)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
