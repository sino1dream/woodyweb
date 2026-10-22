# -*- coding: utf-8 -*-
"""`GetSwitchDates()` 的 Python 复刻 + 与 PHP 探针对照（`_php_switchdates_check.php`）。

PHP 原文见 ``php/sql/sqldailyclose.php:215-264``，本文件把它的状态机原样写成
Python，再喂同一份真实净值序列（``_switchdates_nav.json``），逐例比对：

* 拐点数组必须逐个相等；
* 状态机的每一步（首条 / 定方向 / 反转）也要一致 —— 这样"哪一步理解错了"
  一眼就能定位，而不是只看结果对不对。

跑法::

    cd python/auto/_scratch
    D:/python/python.exe -B _php_switchdates_compare.py

需要先导出净值数据::

    D:/python/python.exe -B _scratch/_switchdates_export.py
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAV_FILE = os.path.join(HERE, "_switchdates_nav.json")
PHP_FILE = os.path.join(HERE, "_php_switchdates_check.php")
PHP_EXE = "php"


def switch_dates(records: list) -> list:
    """复刻 PHP ``GetSwitchDates()``（``sqldailyclose.php:215``）。

    ``records`` 是 ``[{"date":..., "close":...}, ...]``，**顺序必须是 date DESC**
    —— 与 ``DailyCloseSql::GetAll()`` 的 ``_SqlOrderByDate()`` 一致。

    返回**拐点日期数组**：第一条 + 每次方向反转时的"上一条"日期。
    注意返回的既有"最新那条"也有"更早的"，但**整体从新到旧**；
    最后一段（没闭合的尾巴）不在数组里。
    """
    dates = []
    first = True
    second = False           # PHP 里未初始化（第一次循环才赋值），这里显式给 False
    up = False
    old = 0.0
    old_date = ""
    for record in records:
        date = record["date"]
        cur = float(record["close"])
        if first:
            dates.append(date)
            second = True
            first = False
        else:
            if second:
                up = old > cur
                second = False
            else:
                if up:
                    if old < cur:
                        up = False
                        dates.append(old_date)
                else:
                    if old > cur:
                        up = True
                        dates.append(old_date)
        old = cur
        old_date = date
    return dates


def trace(records: list) -> list:
    """状态机推演（与 :func:`switch_dates` 同一套判断，只是把每步说出来）。"""
    steps = []
    first = True
    second = False
    up = False
    old = 0.0
    old_date = ""
    for record in records:
        date = record["date"]
        cur = float(record["close"])
        action = ""
        if first:
            action = "首条入组"
            second = True
            first = False
        else:
            if second:
                up = old > cur
                second = False
                action = "定初始方向 bUp=%s" % ("true" if up else "false")
            else:
                if up:
                    if old < cur:
                        up = False
                        action = "转跌，记 " + old_date + "（极小）"
                else:
                    if old > cur:
                        up = True
                        action = "转涨，记 " + old_date + "（极大）"
        steps.append({"date": date, "close": record["close"],
                      "action": action, "bUp": "true" if up else "false"})
        old = cur
        old_date = date
    return steps


def php_side(nav: dict) -> dict:
    """跑 PHP 探针，返回它的输出（stdout 只有 JSON）。"""
    with open(NAV_FILE, "w", encoding="utf-8") as handle:
        json.dump(nav, handle)
    proc = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", PHP_FILE, NAV_FILE],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP 探针失败，退出码 %s" % proc.returncode)
    if proc.stderr:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
    return json.loads(proc.stdout.decode("utf-8"))


def main() -> int:
    if not os.path.exists(NAV_FILE):
        raise SystemExit("缺少 %s，请先跑导出脚本" % NAV_FILE)
    with open(NAV_FILE, encoding="utf-8") as handle:
        nav = json.load(handle)

    php_out = php_side(nav)
    bad = 0
    for symbol, records in nav.items():
        mine = switch_dates(records)
        theirs = php_out[symbol]["switch_dates"]
        ok = mine == theirs
        bad += 0 if ok else 1
        print("=== %s  净值 %d 条" % (symbol, len(records)))
        print("    PHP 拐点 : %s" % theirs)
        print("    Py  拐点 : %s   %s" % (mine, "一致" if ok else "★不一致"))
        # 状态机逐步核对
        mine_trace = trace(records)
        theirs_trace = php_out[symbol]["trace"]
        for i, (a, b) in enumerate(zip(mine_trace, theirs_trace)):
            if a != b:
                bad += 1
                print("    ★第 %d 步不一致 py=%s php=%s" % (i, a, b))
        print("    推演（前 6 步）：")
        for step in mine_trace[:6]:
            print("      %s  %-22s bUp=%s" % (step["date"], step["action"] or "—", step["bUp"]))
        # 波段区间：拐点 i -> 拐点 i+1（prev 是更早的那个）
        print("    波段（当前 -> prev，净值涨跌%）：")
        for i in range(len(mine) - 1):
            cur, prev = mine[i], mine[i + 1]
            value = _close_of(records, cur)
            prev_value = _close_of(records, prev)
            if value is None or prev_value is None:
                continue
            change = (value / prev_value - 1.0) * 100.0
            print("      %s -> %s  %+.2f%%  %s" % (
                cur, prev, change, "过 4% 闸门" if abs(change) > 4.0 else "不过闸门"))
    print("\n对照结果：%s（不一致 %d 处）" % ("全部一致" if bad == 0 else "有差异", bad))
    return 1 if bad else 0


def _close_of(records: list, date: str):
    for record in records:
        if record["date"] == date:
            return float(record["close"])
    return None


if __name__ == "__main__":
    raise SystemExit(main())
