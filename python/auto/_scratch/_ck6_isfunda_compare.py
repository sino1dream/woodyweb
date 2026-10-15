"""CK6-1：校准记录列头谓词 IsFundA() 的 PHP 逐例对照。

``history_service._build_calibrationhistory`` 曾经用 ``get_fund_profile()``
判"是不是基金"，把 ^GSPC / ^NDX / QQQ / SPY 这些**中间层标的**误判成基金，
期货均线表前那张校准表于是多出「净值 / 对冲值」两列（生产端只有 4 列）。
PHP 的谓词是 ``$ref->IsFundA()``（``php/stock/stocksymbol.php:733``）——
本工具逐个符号比对该函数与 Python ``StockSymbol.is_fund_a()``。

    cd python\\auto
    python -B _scratch/_ck6_isfunda_compare.py     # 需要 php 在 PATH 中
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 与其它 _scratch 脚本同一套路径补齐（parents[2]=python，parents[1]=python/auto）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.stocksymbol import StockSymbol  # noqa: E402

HERE = Path(__file__).resolve().parent
PHP_HARNESS = HERE / "_php_isfunda_check.php"


def load_reference():
    """让 PHP 把参考结果打到 stdout；PHP 进程可能无权写入工作区。"""
    completed = subprocess.run(
        ["php", "-n", "-d", "display_errors=stderr", str(PHP_HARNESS)],
        cwd=str(HERE),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.stderr:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
    return json.loads(completed.stdout.decode("utf-8"))


def main() -> int:
    reference = load_reference()

    mismatches = []
    checked = 0
    for item in reference:
        symbol = item["symbol"]
        php_value = item["IsFundA"]
        # PHP 命中时返回 strDigitA（字符串），否则 false；
        # Python 的 is_fund_a() 同样是 str_digit_a 或 False。
        py_value = StockSymbol(symbol).is_fund_a()
        checked += 1
        if php_value != py_value:
            mismatches.append((symbol, php_value, py_value))
            continue
        # 顺带核对中间两步，错了好定位是哪一层偏了
        for key, py_method in (("IsLofA", "is_lof_a"), ("IsEtfA", "is_etf_a")):
            php_mid = bool(item[key])
            py_mid = bool(getattr(StockSymbol(symbol), py_method)())
            if php_mid != py_mid:
                mismatches.append((symbol, key, php_mid, py_mid))

    funds = [i["symbol"] for i in reference if i["IsFundA"]]
    non_funds = [i["symbol"] for i in reference if not i["IsFundA"]]

    print("共对照 %d 个符号：IsFundA 为真 %d 个、为假 %d 个" % (
        checked, len(funds), len(non_funds)))
    print("  为真：%s" % " ".join(funds))
    print("  为假：%s" % " ".join(non_funds))

    if mismatches:
        print()
        print("不一致 %d 处：" % len(mismatches))
        for row in mismatches:
            print("   ", row)
        return 1

    print()
    print("全部一致 —— IsFundA / IsLofA / IsEtfA 三层逐例相同。")
    print("CK6-1 换上的谓词与 PHP 同语义：只有 A 股 LOF/ETF 才补净值/对冲值两列。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
