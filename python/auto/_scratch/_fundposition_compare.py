# -*- coding: utf-8 -*-
"""``fundposition`` 页的 PHP / Python 逐例对照。

PHP 侧由 ``_php_fundposition_check.php`` 出结果（里面跑的是从
``fundpositioncn.php`` / ``sqldailyclose.php`` / ``netvaluehistoryparagraph.php``
现场切下来的真身），Python 侧用 ``HistoryService`` 连**同一个演示库**取数，
两边逐行比对：

* 拐点数组（含顺序）；
* 每行的日期 —— 决定"哪些净值波动值得看"；
* 每行的"上期" —— 本页相对净值记录页的核心差别；
* 每行的仓位格 —— 过不过 4% 闸门、值是多少。

跑法::

    cd python/auto/_scratch
    D:/python/python.exe -B _fundposition_export.py        # 导出共同输入
    D:/python/python.exe -B _fundposition_compare.py       # 本文件
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
WEB_DIR = os.path.join(PYTHON_DIR, "web")
for _path in (AUTO_DIR, PYTHON_DIR, WEB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource  # noqa: E402
from sql.historytable import NetValueHistorySql  # noqa: E402
from web.services.history_service import HistoryService  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
IN_FILE = os.path.join(HERE, "_fundposition_input.json")
PHP_FILE = os.path.join(HERE, "_php_fundposition_check.php")
PHP_EXE = "php"


def php_side() -> dict:
    proc = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", PHP_FILE, IN_FILE],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP 探针失败，退出码 %s" % proc.returncode)
    if proc.stderr:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
    return json.loads(proc.stdout.decode("utf-8"))


def cell_text(cell) -> str:
    return cell[0] if isinstance(cell, tuple) else str(cell)


def main() -> int:
    with open(IN_FILE, encoding="utf-8") as handle:
        cases = json.load(handle)

    source = MySqlFundDataSource.from_env()
    service = HistoryService(source)
    php = php_side()

    bad = 0
    for symbol, case in cases.items():
        num = case["num"]
        theirs = php[symbol]
        mine = service.get_table("fundposition", symbol, 0, num)

        stock_id = source.get_stock_id(symbol)
        my_switch = [str(d) for d in NetValueHistorySql(source.connection).get_switch_dates(stock_id)]

        print("=== %s  est=%-8s 净值 %d 条" % (symbol, case["est_symbol"], len(case["nav"])))

        ok = my_switch == theirs["switch_dates"]
        bad += 0 if ok else 1
        print("    拐点 PHP : %s" % theirs["switch_dates"])
        print("    拐点 Py  : %s  %s" % (my_switch, "一致" if ok else "★不一致"))

        my_rows = [[cell_text(r.cells[0]), cell_text(r.cells[-1])] for r in mine["rows"]]
        their_rows = [[r["date"], r["position"] or ""] for r in theirs["rows"]]

        #: "上期"必须显式比。仓位格为空的那些行，数值本身证明不了 prev 是谁，
        #: 而 prev 恰恰是本页相对净值记录页的唯一差别。
        #: Python 侧不把 prev 暴露出来，用"拐点 i 的下一位"还原即可
        #: （``_build_fundposition`` 就是这么配对的）。
        my_prevs = my_switch[1:len(my_rows) + 1]
        their_prevs = [r["prev"] for r in theirs["rows"]]
        prev_ok = my_prevs == their_prevs
        bad += 0 if prev_ok else 1
        print("    上期配对：%s" % ("一致" if prev_ok else "★不一致 %s vs %s" % (my_prevs, their_prevs)))

        ok = my_rows == their_rows
        bad += 0 if ok else 1
        print("    行数 PHP %d / Py %d   %s" % (len(their_rows), len(my_rows),
                                                "一致" if ok else "★不一致"))
        print("    %-12s %-12s %-10s %-10s" % ("日期", "上期(PHP)", "仓位PHP", "仓位Py"))
        for i, row in enumerate(their_rows):
            prefix = "   " if i < len(my_rows) and my_rows[i] == row else " ★ "
            print("    %s%-10s %-12s %-10s %-10s" % (
                prefix, row[0], theirs["rows"][i]["prev"], row[1],
                my_rows[i][1] if i < len(my_rows) else "-"))
        if not ok:
            for i in range(min(len(my_rows), len(their_rows))):
                if my_rows[i] != their_rows[i]:
                    print("      ★第 %d 行 php=%s py=%s" % (i, their_rows[i], my_rows[i]))

    print("\n对照结果：%s（不一致 %d 处）" % ("全部一致" if bad == 0 else "有差异", bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
