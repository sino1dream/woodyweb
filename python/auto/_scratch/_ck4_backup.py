"""CK4-D：往演示库写持仓样本之前的全库备份。

与 P0 阶段同一条约定（AGENTS.md）：**动演示库数据前先 mysqldump**。
这次要动的表比 P0 多（``stock`` / ``stockhistory`` / ``netvaluehistory`` /
``holdings`` / ``holdingsdate``），所以直接整库备份，省得漏。

    python -B auto/_scratch/_ck4_backup.py
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


def main() -> int:
    config = mysql_config_from_env(load_mysql_env(os.environ))
    output = os.path.join(
        PYTHON_DIR, "dbtools", "_snapshots", "before_20261015_ck4_holdings.sql")
    command = [
        "mysqldump",
        "-h", str(config["host"]),
        "-P", str(config["port"]),
        "-u", str(config["user"]),
        "-p%s" % config["password"],
        "--skip-lock-tables",
        str(config["database"]),
    ]
    with open(output, "w", encoding="utf-8") as handle:
        result = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print("备份失败：%s" % result.stderr.decode("utf-8", "replace"))
        return 1
    print("已备份到 %s（%d 字节）" % (output, os.path.getsize(output)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
