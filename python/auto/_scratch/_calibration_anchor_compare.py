# -*- coding: utf-8 -*-
"""``calibrate_fund`` 的锚点口径：Python vs **真实 PHP** 逐只对照。

改锚点（est 最新行情日 → **基金净值日**，对齐 PHP ``AdjustFactor()``）之后，
光看源码不够，得让两边跑同一个演示库、比同一组数。

* PHP 侧：``_php_calibration_anchor_check.php`` —— 把 ``AdjustFactor()`` /
  ``_getEstNetValue()`` / ``QdiiGetCalibration()`` 三段**真身**从
  ``php/stock/qdiiref.php`` 现场切出来 eval，只把"取数/写库"换成连库替身。
* Python 侧：``AutoCalibrator.calibrate_fund()``，顶掉闸门（等价 CLI 的
  ``--force``），不写库。

比四样：**锚点日期、est 值、汇率、算出的 cal**，以及"该不该跳过"。

跑法::

    cd python/auto/_scratch
    D:/python/python.exe -B _calibration_anchor_compare.py
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRATCH_DIR)
PYTHON_DIR = os.path.dirname(AUTO_DIR)
REPO_DIR = os.path.dirname(PYTHON_DIR)
for _path in (SCRATCH_DIR, AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource  # noqa: E402
from updater.autocalibration import AutoCalibrator  # noqa: E402

INPUT_FILE = os.path.join(SCRATCH_DIR, "_calibration_anchor_input.json")
PHP_SCRIPT = os.path.join(SCRATCH_DIR, "_php_calibration_anchor_check.php")

#: cal 值的相对容差。PHP 是 float（双精度），Python 也是，差别只应来自
#: 十进制字符串 → 浮点的那一步；1e-9 足够严。
REL_TOL = 1e-9


def _php_side() -> dict:
    """跑 PHP 探针，返回它给出的结果。"""
    if not os.path.exists(INPUT_FILE):
        raise SystemExit("缺少 %s，先跑 _calibration_anchor_export.py" % INPUT_FILE)
    proc = subprocess.run(
        ["php", "-d", "display_errors=stderr", PHP_SCRIPT, INPUT_FILE],
        cwd=SCRATCH_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise SystemExit("PHP 探针失败：%s" % proc.stderr.decode("utf-8", "replace"))
    return json.loads(proc.stdout.decode("utf-8"))


def _python_side(symbols) -> dict:
    """跑 Python 校准（顶掉闸门，不写库）。"""
    source = MySqlFundDataSource.from_env()
    calibrator = AutoCalibrator(source)
    #: 闸门会挡掉"锚点日已校准过"，这里要看**重算出来的值**，所以顶掉它
    #: （与 CLI 的 --force 同一手法）。
    calibrator._calibration_date = lambda stock_id: None

    result = {}
    for symbol in symbols:
        outcome = calibrator.calibrate_fund(symbol, apply=False)
        row = {
            "est_symbol": outcome.pair_symbol,
            "date": None if outcome.date is None else str(outcome.date),
            "nav": None if outcome.net_value in (None, "") else str(outcome.net_value),
            "skipped": bool(outcome.skipped),
        }
        if outcome.skipped:
            row["est"] = None
            row["cny"] = None
            row["cal"] = None
        else:
            row["est"] = None if outcome.pair_value in (None, "") else str(outcome.pair_value)
            row["cny"] = None if outcome.cny in (None, "") else str(outcome.cny)
            row["cal"] = float(outcome.calibration)
        result[symbol] = row
    source.connection.close()
    return result


def _close(left, right) -> bool:
    """两个数够不够近（都按浮点比，容差见 ``REL_TOL``）。"""
    if left is None or right is None:
        return left is None and right is None
    left_f = float(left)
    right_f = float(right)
    if left_f == right_f:
        return True
    scale = max(abs(left_f), abs(right_f))
    return abs(left_f - right_f) <= REL_TOL * max(scale, 1.0)


def main() -> int:
    php = _php_side()
    py = _python_side(sorted(php))

    agreed = 0
    diffs = []
    skipped = 0
    for symbol in sorted(php):
        p = php[symbol]
        y = py.get(symbol)
        if y is None:
            diffs.append((symbol, "Python 没这只基金", "", ""))
            continue
        if p["skipped"] or y["skipped"]:
            #: 跳过与否一致就算过：这正是新口径下"净值日 est 没数据"的表现
            if p["skipped"] == y["skipped"] and p["date"] == y["date"]:
                skipped += 1
                agreed += 1
                continue
            diffs.append((symbol, "跳过状态不一致",
                          "php skipped=%s date=%s" % (p["skipped"], p["date"]),
                          "py  skipped=%s date=%s" % (y["skipped"], y["date"])))
            continue
        for field in ("date", "nav", "est", "cny"):
            if str(p[field]) != str(y[field]) and not _close(p[field], y[field]):
                diffs.append((symbol, field, p[field], y[field]))
        if not _close(p["cal"], y["cal"]):
            diffs.append((symbol, "cal", p["cal"], y["cal"]))

    print("对照 %d 只基金（其中同日跳过 %d 只）" % (len(php), skipped))
    print("")
    print("%-10s %-8s %-12s %-12s %-10s %-14s %s" % (
        "基金", "锚点日", "PHP cal", "Python cal", "est", "基金净值", "结论"))
    print("-" * 84)
    for symbol in sorted(php):
        p, y = php[symbol], py[symbol]
        php_cal = "-" if p["cal"] is None else "%.6f" % p["cal"]
        py_cal = "-" if y["cal"] is None else "%.6f" % y["cal"]
        bad = [d for d in diffs if d[0] == symbol]
        print("%-10s %-8s %-12s %-12s %-10s %-14s %s" % (
            symbol, p["date"] or "-", php_cal, py_cal,
            p.get("est") or "-", p["nav"] or "-",
            "不一致：%s" % ",".join(d[1] for d in bad) if bad else "一致"))

    print("")
    if diffs:
        print("**有 %d 处不一致**" % len(diffs))
        for symbol, field, left, right in diffs:
            print("   %-10s %-10s php=%r python=%r" % (symbol, field, left, right))
        return 1
    print("全部一致（锚点日 / est / 汇率 / cal 逐只相同，跳过与否也一致）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
