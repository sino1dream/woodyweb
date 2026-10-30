"""三个 submit 页的 PHP 逐例对照。

跑法（在 ``python/`` 下）::

    D:/python/python.exe -B auto/_scratch/_php_submit_compare.py

## 比什么

``_php_submit_check.php`` 把 PHP 原函数（``_updateSpdrEtfDividend`` /
``_updateProsharesEtfDividend`` 从 ``woody/res/submitdividend.php`` 里抠出来
eval）对着**同一份 fixture** 跑一遍，吐出它要写进库的 ``(日期, 值)`` 列表；
本脚本拿 Python 的解析结果逐行比。另外比一组"原语"（日期解析、0 判定、
``mysql_round``、流通股数换算）。

## 不比什么

* **SPDR 的 xlsx 净值**：PHP 那条要 ``PHPExcel_IOFactory``，本机 PEAR 里
  没有 ``HTML/`` 也没有 PHPExcel，跑不起来（见 pitfalls #152）。改为对真实
  文件做结构核对（``--xlsx`` 那段：表头在第 4 行、第 5 行起降序、列位置），
  这些结论在 ``test_submitupdaters.SpdrXlsxParseTest`` 里已钉住；
* **submithistory**：PHP 的 ``UpdateYahooHistoryChart`` 依赖 Yahoo 接口（已
  403）与一整套参考对象；闸门顺序按源码逐行抄的，见
  ``README_submit_pages_migration.md`` §四 的对照表。
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import os
from pathlib import Path

SCRATCH_DIR = Path(__file__).resolve().parent
#: ``_scratch → auto → python``：``parents[1]`` 是 python 目录（3.7 的
#: ``parents`` 不支持切片，只能按下标取）。
PYTHON_DIR = SCRATCH_DIR.parents[1]
sys.path.insert(0, str(PYTHON_DIR / "auto"))

from updater import etfdividend, etfnav, historyupdate  # noqa: E402
from sql.historytable import mysql_round  # noqa: E402

FIXTURES = SCRATCH_DIR / "_fixtures"


def run_php() -> dict:
    """跑 PHP 探针，拿它吐的 JSON。"""
    completed = subprocess.run(
        ["php", "-d", "display_errors=stderr", "_php_submit_check.php"],
        cwd=str(SCRATCH_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP 探针失败")
    return json.loads(completed.stdout.decode("utf-8"))


def read(name: str) -> str:
    return io.open(str(FIXTURES / name), encoding="utf-8").read()


def compare_rows(label: str, php_rows, py_rows, results) -> None:
    """逐行比 ``(date, 值)``。

    ``值`` 按 ``float`` 比：PHP 给的是原始字符串、Python 也是原始字符串，
    但两边一个是 ``"0.757469"`` 另一个可能是 ``0.757469``（JSON 数字）。
    值的**字符串形状**不影响落库（``_prepare_close`` 统一处理），
    所以这里只比数值，并在差异里打印原文。

    Python 那边的键名按模块不同：分红是 ``dividend``，净值是 ``nav``。
    """
    php_pairs = [(str(row["date"]), row["close"]) for row in php_rows]
    py_pairs = [(str(row["date"]), _value_of(row)) for row in py_rows]
    if len(php_pairs) != len(py_pairs):
        results.append(("FAIL", label, "行数 php=%d py=%d" % (len(php_pairs), len(py_pairs))))
        return
    bad = []
    for (php_date, php_close), (py_date, py_close) in zip(php_pairs, py_pairs):
        if php_date != py_date or abs(float(php_close) - float(py_close)) > 1e-9:
            bad.append((php_date, php_close, py_date, py_close))
    if bad:
        results.append(("FAIL", label, "%d 行不一致，首个：%s" % (len(bad), bad[0])))
    else:
        results.append(("OK", label, "%d 行" % len(php_pairs)))


def _value_of(row: dict):
    """从 Python 的解析结果里取值（分民用 ``dividend``、净值用 ``nav``）。"""
    for key in ("dividend", "nav", "close"):
        if key in row:
            return row[key]
    raise KeyError("解析结果里没有值字段：%r" % (row,))


def compare_primitives(php: dict, results) -> None:
    """三个原语：日期、0 判定、取整。"""
    cases = []

    #: SPDR 的 m/d/Y
    cases.append(("spdr_date_09212026", php["spdr_date_09212026"],
                  etfdividend._parse_us_date("09/21/2026")))
    cases.append(("spdr_date_12222025", php["spdr_date_12222025"],
                  etfdividend._parse_us_date("12/22/2025")))
    #: xlsx 的 d-M-Y
    cases.append(("xls_date_29Oct2026", php["xls_date_29Oct2026"],
                  etfnav._parse_excel_date("29-Oct-2026")))
    cases.append(("xls_date_31Dec2013", php["xls_date_31Dec2013"],
                  etfnav._parse_excel_date("31-Dec-2013")))
    #: 表头 "Date"：PHP strtotime 返回 false（假值）→ 整行跳过；Python 返回 None
    cases.append(("xls_header_tick", _php_falsy(php["xls_header_tick"]),
                  etfnav._parse_excel_date("Date") is None))
    #: Proshares 的切片
    cases.append(("proshares_slice", php["proshares_slice"],
                  "2024-12-23T00:00:00"[:10]))
    #: IsZeroString
    for key, value in (("zero_dash", "-"), ("zero_null_text", "null"),
                       ("zero_0", "0"), ("zero_769", "769.36")):
        cases.append((key, bool(php[key]), historyupdate._is_zero(value)))
    #: mysql_round（PHP 的 round(floatval(x), 6)）
    cases.append(("round_769_506335", php["round_769_506335"],
                  mysql_round("769.506335")))
    cases.append(("round_769_36", php["round_769_36"], mysql_round("769.36")))
    #: 流通股除以 10000
    cases.append(("shares_div", php["shares_div"],
                  _php_strval(float("1057282116") / 10000.0)))

    for label, php_value, py_value in cases:
        same = _same(php_value, py_value)
        results.append(("OK" if same else "FAIL", "原语 " + label,
                        "%r vs %r" % (php_value, py_value)))


def _php_falsy(value) -> bool:
    """PHP 的假值：``false`` / ``0`` / ``''`` / ``null``。"""
    return not value


def _php_strval(value: float) -> str:
    """PHP ``strval(float)``：整数值不带 ``.0``。"""
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def _same(php_value, py_value) -> bool:
    if isinstance(php_value, bool) or isinstance(py_value, bool):
        return bool(php_value) == bool(py_value)
    try:
        return abs(float(php_value) - float(py_value)) < 1e-9
    except (TypeError, ValueError):
        return str(php_value) == str(py_value)


def check_xlsx_shape(results) -> None:
    """xlsx 结构核对（PHP 跑不了，改用"真实文件里的事实"）。

    三条都是 ``_readXlsFile`` 隐含假设的：表头在第 4 行、第 5 行起是数据、
    列序是 日期/净值/流通股数。
    """
    path = FIXTURES / "navhist-us-en-spy.xlsx"
    if not path.is_file():
        results.append(("SKIP", "xlsx 结构", "没有 fixture（先跑一次抓取）"))
        return
    from source import xlsxreader

    rows = xlsxreader.read_rows(path.read_bytes(), 0)
    header = rows[3]
    ok = (header[0] == "Date" and header[1] == "NAV"
          and str(header[2]).startswith("Shares"))
    results.append(("OK" if ok else "FAIL", "xlsx 表头在第 4 行", str(header[:3])))
    first = etfnav.parse_spdr_nav_xlsx(path.read_bytes())[0]
    ok = first["date"] == "2026-10-29" and abs(first["nav"] - 769.506335) < 1e-9
    results.append(("OK" if ok else "FAIL", "xlsx 首行（降序最新）",
                    "%s %s" % (first["date"], first["nav"])))
    ok = abs(first["shares"] - 105728.2116) < 1e-6
    results.append(("OK" if ok else "FAIL", "xlsx 流通股 /10000",
                    str(first["shares"])))


def main() -> int:
    php = run_php()
    results = []

    compare_rows(
        "SPDR 分红（XOP 80 条）",
        php["spdr_dividend"],
        etfdividend.parse_spdr_dividends(read("spdr-dividend-xop.json")),
        results,
    )
    compare_rows(
        "Proshares 分红（TQQQ 4 条）",
        php["proshares_dividend"],
        etfdividend.parse_proshares_dividends(read("proshares-dividend-sample.json")),
        results,
    )
    py_invesco = etfnav.parse_invesco_nav(read("invesco-nav-qqq.json"))
    compare_rows(
        "Invesco 净值（QQQ 1 条）",
        [{"date": php["invesco_nav"]["effectiveDate"],
          "close": php["invesco_nav"]["nav"]}],
        [{"date": py_invesco["date"], "close": py_invesco["nav"]}],
        results,
    )
    compare_primitives(php["primitives"], results)
    check_xlsx_shape(results)

    ok = sum(1 for status, _, _ in results if status == "OK")
    failed = [row for row in results if row[0] == "FAIL"]
    for status, label, detail in results:
        print("%-5s %-34s %s" % (status, label, detail))
    print("")
    print("共 %d 例：一致 %d，失败 %d" % (len(results), ok, len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
