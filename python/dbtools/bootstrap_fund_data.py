"""P0.8：一键重跑整条基金数据链路（**默认 dry-run**）。

把 P0.1–P0.6 那些"各跑一次"的命令串成一条，换机器 / 换演示库时用同一把钥匙：

    cd F:\\woody\\woodyweb\\python

    python -B dbtools/bootstrap_fund_data.py                 # 体检 + 打印计划
    python -B dbtools/bootstrap_fund_data.py --apply         # 备份 + 真跑一遍
    python -B dbtools/bootstrap_fund_data.py --apply --only quotes,archive
    python -B dbtools/bootstrap_fund_data.py --apply --from calib

## 步骤顺序（顺序就是依赖，不能随意换）

| # | 步骤 | 命令 | 为什么在这个位置 |
| --- | --- | --- | --- |
| 1 | ``symbols`` | ``seed_p0_symbols.py --apply`` | 抓取层**跳过库里没有的符号**，不先写符号后面全是空转 |
| 2 | ``forex`` | ``forexcli --backfill`` | 估值要汇率，先于净值/校准 |
| 3 | ``nav`` | ``navcli`` | 基金净值；校准的分母 |
| 4 | ``quotes`` | ``quotecli --all-qdii`` | 行情历史；中间层"当日收盘价"的来源 |
| 5 | ``middle-nav`` | ``autocalibrationcli --sync-nav --apply`` | 中间层净值 = 收盘价，闸门要它才能前进 |
| 6 | ``archive`` | ``quotecli --archive-snapshot`` | daemon 只写快照，**历史要单独归档**；归档后锚点才推得动 |
| 7 | ``calib`` | ``autocalibrationcli --apply`` 与 ``--funds --apply`` | 两层校准值 |
| 8 | ``fundest`` | ``fundestcli`` | 官方 EST 落库，最后一步 |

第 4 步之前还需要一次 ``quoteupdatercli --once`` 才有快照可归档 —— 本脚本**不**
代跑长驻进程（那是 ``--daemon`` 的事），只在计划里提示。

## 两条安全约定

* **默认 dry-run**：只做体检（查库，不联网）并把要跑的命令打出来；
  ``--apply`` 才真正执行每个子命令；
* ``--apply`` 前先 ``mysqldump`` **全库**到 ``dbtools/_snapshots/``。
  找不到 mysqldump 就拒绝执行，除非显式 ``--skip-backup``。
  与 ``seed_p0_symbols.py`` 同一套约定 —— 一把钥匙开一把锁，不搞第二套。

每步一个子进程，失败默认**停下**（``--keep-going`` 才继续），退出码非 0 时
把失败的那一步说清楚。子命令的 stdout 直接透传，不吞输出 ——
排查时要看的就是它们自己打的那张表。
"""

from __future__ import annotations

import datetime
import io
import os
import subprocess
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DBTOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(DBTOOLS_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
CLI_DIR = os.path.join(AUTO_DIR, "cli")
for _path in (PYTHON_DIR, AUTO_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource, load_mysql_env, mysql_config_from_env  # noqa: E402

SNAPSHOT_DIR = os.path.join(DBTOOLS_DIR, "_snapshots")

#: 会被本脚本写入的表（备份用；``mysqldump`` 直接给表名）
TABLES = ("stock", "fundpair", "netvaluehistory", "stockhistory",
          "calibrationhistory", "fundest", "realtimequote", "estquote",
          "lastcalibration")


class Step:
    """一个子命令。``argv`` 是传给 Python 的参数（不含解释器）。"""

    def __init__(self, name, title, script, argv=()):
        self.name = name
        self.title = title
        self.script = script
        self.argv = list(argv)

    def command(self) -> list:
        return [sys.executable, "-B", self.script] + self.argv

    def display(self) -> str:
        return " ".join(["python", "-B", os.path.relpath(self.script, PYTHON_DIR)] + self.argv)


def _cli(name: str) -> str:
    return os.path.join(CLI_DIR, name)


#: 顺序即依赖，见模块 docstring 那张表
STEPS = [
    Step("symbols", "写 stock / fundpair（缺哪只补哪只）",
         os.path.join(DBTOOLS_DIR, "seed_p0_symbols.py"), ["--apply"]),
    Step("forex", "汇率历史（USCNY / HKCNY / EUCNY / JPCNY）",
         _cli("forexcli.py"), ["--backfill"]),
    Step("nav", "基金官方净值（东财）",
         _cli("navcli.py"), []),
    Step("quotes", "行情历史（新浪，102 基金 + 22 中间层）",
         _cli("quotecli.py"), ["--all-qdii"]),
    Step("middle-nav", "中间层派生净值（净值 = 当日收盘价）",
         _cli("autocalibrationcli.py"), ["--sync-nav", "--apply"]),
    Step("archive", "把实时快照归档进 stockhistory",
         _cli("quotecli.py"), ["--archive-snapshot"]),
    Step("calib", "中间层校准值 cal_m",
         _cli("autocalibrationcli.py"), ["--apply"]),
    Step("fund-calib", "基金层校准值 cal_f",
         _cli("autocalibrationcli.py"), ["--funds", "--apply"]),
    Step("fundest", "官方 EST 落 fundest",
         _cli("fundestcli.py"), []),
]


def backup() -> str:
    """``mysqldump`` 全库到 ``_snapshots/``，返回文件路径。"""
    config = mysql_config_from_env(load_mysql_env(os.environ))
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = os.path.join(SNAPSHOT_DIR, "bootstrap_before_%s.sql" % stamp)
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
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
    return output


def _mysqldump_available() -> bool:
    try:
        result = subprocess.run(["mysqldump", "--version"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return result.returncode == 0


def preflight() -> None:
    """只读体检：把"现在库里有什么"打出来，dry-run 的价值就在这一张表。"""
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    def scalar(sql, params=()):
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return list(row.values())[0] if row else None

    try:
        from core import symbolinventory

        funds = symbolinventory.fund_symbols()
        ests = symbolinventory.est_symbols()
    except Exception:
        funds, ests = [], []
    stock_symbols = set()
    try:
        cursor.execute("SELECT symbol FROM stock")
        stock_symbols = {str(row["symbol"]) for row in cursor.fetchall()}
    except Exception:
        pass

    print("=== 体检（只读，不联网）===")
    print("%-10s %-8s %-8s %s" % ("符号", "应有", "在库", "缺"))
    print("-" * 46)
    for label, wanted in (("基金", funds), ("est 中间层", ests)):
        missing = [s for s in wanted if s not in stock_symbols]
        print("%-10s %-8d %-8d %s" % (
            label, len(wanted), len(wanted) - len(missing),
            ("%d 只" % len(missing)) if missing else "0"))

    print("")
    print("%-18s %-10s %s" % ("表", "行数", "最新日期"))
    print("-" * 46)
    for table, date_column in (
        ("netvaluehistory", "date"),
        ("stockhistory", "date"),
        ("calibrationhistory", "date"),
        ("fundest", "date"),
    ):
        try:
            rows = scalar("SELECT COUNT(*) FROM %s" % table)
            latest = scalar("SELECT MAX(`%s`) FROM %s" % (date_column, table))
        except Exception as exc:
            print("%-18s 读不到（%s）" % (table, type(exc).__name__))
            continue
        print("%-18s %-10s %s" % (table, rows, latest or "-"))
    for table in ("realtimequote", "estquote"):
        try:
            rows = scalar("SELECT COUNT(*) FROM %s" % table)
            latest = scalar("SELECT MAX(quote_date) FROM %s" % table)
        except Exception:
            continue
        print("%-18s %-10s %s" % (table, rows, latest or "-"))
    cursor.close()
    source.connection.close()


def run(steps: list, keep_going: bool) -> int:
    """依次执行子命令，返回失败数。"""
    failed = []
    for step in steps:
        print("")
        print("=" * 78)
        print("[%s] %s" % (step.name, step.title))
        print("$ %s" % step.display())
        print("=" * 78)
        result = subprocess.run(step.command(), cwd=PYTHON_DIR)
        if result.returncode != 0:
            failed.append(step.name)
            print("")
            print("!! %s 失败（退出码 %d）" % (step.name, result.returncode))
            if not keep_going:
                break
    print("")
    if failed:
        print("失败步骤：%s" % ", ".join(failed))
        print("从失败那一步重跑：--from %s" % failed[0])
    else:
        print("全部步骤完成。")
    return len(failed)


def select_steps(only: str, start: str, end: str) -> list:
    """按 ``--only`` / ``--from`` / ``--to`` 挑步骤（保持原顺序）。"""
    names = [step.name for step in STEPS]
    if only:
        picked = [name.strip() for name in only.split(",") if name.strip()]
        unknown = [name for name in picked if name not in names]
        if unknown:
            raise SystemExit("未知步骤：%s（可选：%s）" % (", ".join(unknown), ", ".join(names)))
        return [step for step in STEPS if step.name in picked]
    begin = names.index(start) if start else 0
    finish = names.index(end) if end else len(names) - 1
    return STEPS[begin:finish + 1]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    apply_changes = "--apply" in argv
    skip_backup = "--skip-backup" in argv
    keep_going = "--keep-going" in argv

    def option(name: str) -> str:
        if name in argv:
            index = argv.index(name)
            if index + 1 < len(argv):
                return argv[index + 1]
        for item in argv:
            if item.startswith(name + "="):
                return item.split("=", 1)[1]
        return ""

    only = option("--only")
    start = option("--from")
    end = option("--to")

    steps = select_steps(only, start, end)

    print("基金数据一键重跑（P0.8）")
    print("模式：%s" % ("**写库**" if apply_changes else "dry-run（只体检 + 打印计划）"))
    print("")
    print("=== 计划 ===")
    for index, step in enumerate(steps, 1):
        print("  %d. %-12s %s" % (index, step.name, step.display()))
    if "archive" in [step.name for step in steps]:
        print("")
        print("提示：归档之前最好先跑一次 quoteupdatercli --once，")
        print("      否则没有快照可归档（daemon 是常驻进程，本脚本不代跑）。")
    print("")

    try:
        preflight()
    except Exception as exc:
        print("体检失败（不影响 --apply，只是少一张表）：%s: %s" % (type(exc).__name__, exc))

    if not apply_changes:
        print("")
        print("这是 dry-run。真正执行请加 --apply。")
        return 0

    if not skip_backup:
        if not _mysqldump_available():
            print("")
            print("找不到 mysqldump，拒绝执行。确认要继续请显式加 --skip-backup。")
            return 2
        try:
            path = backup()
            print("")
            print("已备份到 %s" % path)
        except Exception as exc:
            print("")
            print("备份失败：%s" % exc)
            return 2
    else:
        print("")
        print("!! 按 --skip-backup 跳过了备份 !!")

    return 1 if run(steps, keep_going) else 0


if __name__ == "__main__":
    sys.exit(main())
