"""P0.2：把 QDII 符号清单写进演示库的 ``stock`` 与 ``fundpair``。

## 为什么必须有这一步

``cli/quotecli.py`` 对**库里没有的符号直接跳过落库**（"库里没有 %s，
跳过落库"）—— 演示库 21 个符号里只有 4 只是 QDII 基金，其余 98 只基金
和 20 个 est 中间层都不在 ``stock`` 表里，于是整条抓取链路对它们全是空转。
本脚本先把符号补全，后面的 P0.3 历史回填、P0.4 实时链路才有着落。

## 写什么

清单来自 ``core.symbolinventory``（单一事实源，102 只基金 + 22 个 est
中间层 + 汇率/期货/即期外汇；与生产端四页逐行核对过，见
``README_p0_data_layer.md`` §一）：

1. ``stock`` 行：**名称从新浪行情抓**（不手填 —— 手填就会出现
   "两个名字指同一个东西"的口径重影，``seed_reference_symbols.py``
   的 ``REMOVE_STOCKS`` 就是擦这个屁股的）；
2. ``fundpair`` 行：每只"有 est 的基金"一对，``id`` = 基金 id、
   ``stock_id`` = 配对标的 id（演示库的简化形状，见
   ``dbtools/schema.sql:243-245``）。**已有的行一律不动** ——
   表里同时存在"基金→ETF"和"ETF→指数"两个方向，覆盖会毁掉后者。

## 安全约定

* 默认 dry-run，``--apply`` 才写库；
* ``--apply`` 前先 ``mysqldump`` 全库到 ``dbtools/_snapshots/``
  （该目录已 gitignore，先例 ``calib_before_20261013.sql``）；
  找不到 mysqldump 时拒绝执行，除非显式 ``--skip-backup``；
* 新浪抓不到名称的符号**不写**（``name`` 是 NOT NULL，写占位符会污染
  显示），只在报告里列出来等重试。

用法::

    python -B dbtools\\seed_p0_symbols.py                # 预演
    python -B dbtools\\seed_p0_symbols.py --apply        # 备份 + 真写
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from datetime import datetime

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto")
PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource, load_mysql_env, mysql_config_from_env  # noqa: E402
from core.stocksymbol import StockSymbol  # noqa: E402
from core.symbolinventory import fund_pairs, fund_symbols  # noqa: E402
from ref.stockref import StockReference, sina_quote_file_name  # noqa: E402
from source.sinaquotes import SinaQuoteClient  # noqa: E402

#: 每批抓取的符号数（新浪 list 接口一次别太多）。
BATCH_SIZE = 40

#: mysqldump 的常见位置（Windows/XAMPP 与 Linux 各自找一遍）。
MYSQLDUMP_CANDIDATES = (
    "mysqldump",
    "D:/xampp/mysql/bin/mysqldump",
    "D:/xampp/mysql/bin/mysqldump.exe",
    "C:/xampp/mysql/bin/mysqldump.exe",
    "/usr/bin/mysqldump",
    "/usr/local/mysql/bin/mysqldump",
)


def find_mysqldump():
    for candidate in MYSQLDUMP_CANDIDATES:
        found = shutil.which(candidate) if os.sep not in candidate else (candidate if os.path.exists(candidate) else None)
        if found:
            return found
    return None


def backup_database(config) -> str:
    """全库 mysqldump 到 ``dbtools/_snapshots/symbols_before_<日期>.sql``。"""
    mysqldump = find_mysqldump()
    if mysqldump is None:
        raise RuntimeError("找不到 mysqldump（找过：%s）" % ", ".join(MYSQLDUMP_CANDIDATES))

    snapshot_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_snapshots")
    os.makedirs(snapshot_dir, exist_ok=True)
    path = os.path.join(snapshot_dir, "symbols_before_%s.sql" % datetime.now().strftime("%Y%m%d_%H%M%S"))

    env = dict(os.environ)
    env["MYSQL_PWD"] = str(config.get("password") or "")
    command = [
        mysqldump,
        "--host=%s" % config.get("host", "127.0.0.1"),
        "--port=%s" % config.get("port", 3306),
        "--user=%s" % config.get("user", "root"),
        "--default-character-set=utf8",
        "--single-transaction",
        "--routines",
        config["database"],
    ]
    print("备份：%s" % path)
    print("命令：%s" % " ".join(command))
    with open(path, "w", encoding="utf-8") as handle:
        result = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE, env=env)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("mysqldump 失败（%d）：%s" % (result.returncode, message))
    size = os.path.getsize(path)
    print("备份完成：%s（%.1f KB）" % (path, size / 1024.0))
    if size == 0:
        raise RuntimeError("备份文件是空的，拒绝继续")
    return path


def fetch_names(symbols, timeout=15.0):
    """从新浪抓名称。返回 ``{符号: 名称}``，抓不到的缺席。"""
    names = {}
    client = SinaQuoteClient(timeout=timeout)

    for start in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[start:start + BATCH_SIZE]
        sina_symbols = []
        mapping = {}
        for symbol in batch:
            sina_symbol = StockSymbol(symbol).get_sina_symbol()
            if not sina_symbol:
                print("  跳过（没有新浪符号）：%s" % symbol)
                continue
            sina_symbols.append(sina_symbol)
            mapping[sina_symbol] = symbol
        if not sina_symbols:
            continue

        text = client.fetch(sina_symbols)
        if text is None:
            print("  **本批抓取失败**：%s（%s）" % (
                ",".join(mapping.values()), getattr(client, "last_error", "unknown")))
            continue

        file_map = {}
        for sina_symbol in sina_symbols:
            file_map[sina_quote_file_name(sina_symbol)] = sina_symbol
        client.cache_quote_files(file_map)

        for sina_symbol in sina_symbols:
            symbol = mapping[sina_symbol]
            ref = StockReference(symbol, quote_loader=client.load)
            ref.load_sina_data()
            if ref.str_name:
                names[symbol] = ref.str_name
            else:
                print("  **取不到名称**：%s（%s）" % (symbol, sina_symbol))
    return names


def main() -> int:
    apply = "--apply" in sys.argv
    skip_backup = "--skip-backup" in sys.argv

    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    # -- 1. 算清单 ------------------------------------------------------
    cursor.execute("SELECT id, symbol, name FROM stock")
    rows = cursor.fetchall()
    by_symbol = {row["symbol"]: row for row in rows}

    all_funds = fund_symbols()
    required = []
    for symbol in all_funds:
        if symbol not in required:
            required.append(symbol)
    # est 中间层：从配对清单反推（与探针的 22 个一致）
    for _, est in fund_pairs():
        if est not in required:
            required.append(est)

    missing = [s for s in required if s not in by_symbol]
    pairs = fund_pairs()

    print("=== 清单 ===")
    print("基金 %d 只 + est 中间层 %d 个 = 需要 %d 行 stock，库里已有 %d 行，缺 %d 行"
          % (len(all_funds), len(required) - len(all_funds), len(required),
             len(required) - len(missing), len(missing)))
    print("需要 fundpair 行 %d 对" % len(pairs))

    if not apply:
        print("\n=== 预演（不改数据）===")
        print("缺 stock 行的符号（名称将从新浪抓取）：")
        for symbol in missing:
            print("  %s" % symbol)
        cursor.close()
        source.connection.close()
        print("\n这是预演。真正执行请加 --apply。")
        return 0

    # -- 2. 备份 --------------------------------------------------------
    if not skip_backup:
        try:
            backup_database(mysql_config_from_env(load_mysql_env(os.environ)))
        except RuntimeError as error:
            print("**备份失败，拒绝写库**：%s" % error)
            print("确认要继续请显式加 --skip-backup。")
            cursor.close()
            source.connection.close()
            return 2
    else:
        print("!! 按 --skip-backup 跳过了备份 !!")

    # -- 3. 抓名称并写 stock --------------------------------------------
    print("\n=== 抓取名称（新浪，%d 个符号）===" % len(missing))
    names = fetch_names(missing)

    writable = [s for s in missing if s in names]
    unfetchable = [s for s in missing if s not in names]
    print("\n=== 写 stock 行 ===")
    for symbol in writable:
        cursor.execute(
            "INSERT INTO stock (symbol, name) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE name = VALUES(name)",
            (symbol, names[symbol]),
        )
        print("  %-10s %s" % (symbol, names[symbol]))
    source.connection.commit()
    if unfetchable:
        print("\n**%d 个符号没抓到名称，未写入**（等网络好的时候重跑）：%s"
              % (len(unfetchable), ",".join(unfetchable)))

    # -- 4. 写 fundpair --------------------------------------------------
    print("\n=== 写 fundpair 行 ===")
    written = 0
    skipped = 0
    for fund_symbol, est_symbol in pairs:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (fund_symbol,))
        fund_row = cursor.fetchone()
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (est_symbol,))
        est_row = cursor.fetchone()
        if fund_row is None or est_row is None:
            print("  跳过 %s -> %s（符号不全：fund=%s est=%s）"
                  % (fund_symbol, est_symbol, fund_row is not None, est_row is not None))
            skipped += 1
            continue
        cursor.execute("SELECT 1 FROM fundpair WHERE id = %s LIMIT 1", (fund_row["id"],))
        if cursor.fetchone() is not None:
            skipped += 1
            continue
        cursor.execute(
            "INSERT INTO fundpair (id, stock_id) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE stock_id = VALUES(stock_id)",
            (fund_row["id"], est_row["id"]),
        )
        written += 1
        print("  %-10s (id=%-3s) -> %-10s (id=%s)"
              % (fund_symbol, fund_row["id"], est_symbol, est_row["id"]))
    source.connection.commit()
    print("\n新建 fundpair %d 对，跳过 %d 对（已存在或符号不全）。" % (written, skipped))

    # -- 5. 汇报 ---------------------------------------------------------
    cursor.execute("SELECT COUNT(*) AS c FROM stock")
    total = cursor.fetchone()["c"]
    cursor.execute("SELECT COUNT(*) AS c FROM fundpair")
    total_pairs = cursor.fetchone()["c"]
    print("\n=== 写后 ===")
    print("stock 表 %d 行（原 %d 行，本次新增 %d 行）"
          % (total, len(rows), total - len(rows)))
    print("fundpair 表 %d 行" % total_pairs)
    if unfetchable:
        print("提醒：%d 个符号还没进库，重跑本脚本即可补齐。" % len(unfetchable))
    print("下一步：P0.3 历史回填（quotecli / navcli），现在它们对这些符号不再是空转了。")

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
