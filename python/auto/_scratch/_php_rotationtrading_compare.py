"""轮动交易页的 PHP ↔ Python 逐例对照。

跑 ``_php_rotationtrading_check.php``（真 ``in_array*`` 查表 + 真
``fundposition`` / ``calibrationhistory``），把结果与 Python 侧逐项比：

============================  =============================================
对照项                          Python 侧对应
============================  =============================================
``_getRotationSymbolArray()``  :func:`rotation_service.rotation_symbol_array`
``GetStockHedge()``            ``MySqlFundDataSource.get_stock_hedge``
``GetNumberDisplay($v, 0)``    :func:`rotation_service.number_display`
``abs(round(x/100)*100)``      :func:`rotation_service.hint_quantity`
============================  =============================================

**``SZ159655`` 是刻意差异**：PHP 的 ``_getRotationSymbolArray()`` 判
``in_arraySpyMatch()``（4 只，不含它自己），所以返回 ``false``、页面空白；
本版按 2026-10-31 的要求放宽到 ``in_arraySpyQdii()``。这一条不按"不一致"报错，
而是单独断言"PHP 为空、Python 有 4 只"—— 差异本身要被钉住，否则哪天有人
把它改回去也不会有人发现。

用法（仓库根目录）：

    D:/python/python.exe -B python/auto/_scratch/_php_rotationtrading_compare.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for path in (str(ROOT / "python"), str(ROOT / "python" / "auto")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.stockdata import MySqlFundDataSource, load_mysql_env  # noqa: E402
from web.services.rotation_service import (  # noqa: E402
    hint_quantity,
    number_display,
    rotation_symbol_array,
)

#: 已知的、**刻意**与 PHP 不同的一条。
DELIBERATE = {"SZ159655": None}

_failed = 0


def _check(name, got, want, note=""):
    global _failed
    if got == want:
        print("  OK   %-46s %s" % (name, _short(want)))
        return True
    _failed += 1
    print("  FAIL %-46s" % name)
    print("        PHP : %s" % _short(want))
    print("        PY  : %s" % _short(got))
    if note:
        print("        注  : %s" % note)
    return False


def _short(value, limit=90):
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _php() -> dict:
    env = {**os.environ, **load_mysql_env(os.environ)}
    script = str(HERE / "_php_rotationtrading_check.php")
    proc = subprocess.run(
        ["php", "-d", "display_errors=stderr", script],
        cwd=str(HERE), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace")[:4000])
        raise SystemExit("PHP 探针失败 rc=%d" % proc.returncode)
    return json.loads(proc.stdout.decode("utf-8"))


def main() -> int:
    php = _php()
    source = MySqlFundDataSource.from_env()

    print("== 1. 四族分派（_getRotationSymbolArray 真身）==")
    for symbol, payload in php["groups"].items():
        if symbol in DELIBERATE:
            continue
        want = None if payload is None else payload["symbols"]
        _check(repr(symbol), rotation_symbol_array(symbol), want)

    print("\n== 2. 刻意差异：SZ159655（PHP 判 SpyMatch，本版判 SpyQdii）==")
    php_value = php["groups"]["SZ159655"]
    py_value = rotation_symbol_array("SZ159655")
    _check("PHP 侧为 null（页面空白）", php_value, None)
    _check("Python 侧拿到 4 只候选", py_value,
           ["SH513500", "SH513650", "SZ159612", "SZ161125"])

    print("\n== 3. 对冲值（真 fundposition / calibrationhistory）==")
    for symbol, payload in php["hedges"].items():
        want = float(payload["hedge"])
        got = float(source.get_stock_hedge(symbol, int(payload["stock_id"])))
        #: 两边都是同一批 DECIMAL 除出来的，比值判等即可（不设 1e-9 绝对误差）。
        _check(symbol, round(got, 9), round(want, 9))

    print("\n== 4. GetNumberDisplay($v, 0) 文本与颜色 ==")
    for item in php["number_display"]:
        got_text, got_color = number_display(item["value"], 0)
        _check("%s 文本" % item["value"], got_text, item["text"])
        _check("%s 颜色" % item["value"], got_color, item["color"])

    print("\n== 5. 建议数量的两层取整 ==")
    for item in php["hints"]:
        got = hint_quantity(item["hedge"], item["est_quantity"])
        want = float(item["text"])
        _check("%s × %s" % (item["hedge"], item["est_quantity"]), got, want)

    print("\n%s" % ("全部一致" if _failed == 0 else "有 %d 项不一致" % _failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
