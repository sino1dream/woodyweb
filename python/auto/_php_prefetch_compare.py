"""``stockprefetch`` 判定逻辑与 PHP 的逐项对照。

覆盖 ``NeedFile()``、``_checkBetweenMarketClose()``、``_isFutureMarketTrading()``
和 ``StockNeedNewQuotes()`` 的完整分支组合，共 3 万个用例。这些函数的结果取决于
「当前时刻 + 文件修改时间 + 星期几 + 市场类型」，分支密集，必须用真实 PHP 结果
批量校验。

    cd python\\auto
    python -B _php_prefetch_compare.py      # 需要 php 在 PATH 中
"""

from __future__ import annotations

import datetime as _datetime
import json
import subprocess
import sys
from pathlib import Path

import stockprefetch as sp
from stocksymbol import StockSymbol

HERE = Path(__file__).resolve().parent
PHP_HARNESS = HERE / "_php_prefetch_check.php"

SHANGHAI = _datetime.timezone(_datetime.timedelta(hours=8), "Asia/Shanghai")
BASE_TIMESTAMP = 1767225600  # 2026-01-01 00:00:00 UTC
STEP = 911                    # 与 PHP 侧一致
SAMPLE_COUNT = 400

MARKET_PARAMS = {"A": (2, 8), "H": (3, 8), "US": (16, 4)}
FILE_AGES = [60, 3600, 12 * 3600, 3 * 86400]
NEED_FILE_AGES = [0, 1, 59, 60, 61, 3600]
# StockNeedNewQuotes 只用时区固定为 UTC+8 的符号做 PHP 对照；美股涉及夏令时，
# PHP 侧脚手架无法在不依赖系统时区库的情况下忠实换算，改由单元测试覆盖。
STOCK_SYMBOLS = ["SH600000", "SZ300750", "SH000300", "00700", "^HSI", "^HSCE"]
STOCK_AGES = [0, 30, 60, 600, 7200, 6 * 3600, 24 * 3600, 3 * 86400]


def sample_timestamps():
    return [BASE_TIMESTAMP + index * STEP for index in range(SAMPLE_COUNT)]


def load_reference():
    completed = subprocess.run(
        ["php", "-n", "-d", "display_errors=stderr", str(PHP_HARNESS)],
        cwd=str(HERE),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.stderr:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
    data = json.loads(completed.stdout.decode("utf-8"))
    return {key: value for key, value in data.items() if not key.startswith("_")}


def _norm(result):
    """PHP 的 false 对齐为 Python 的 False，其余保持不变。"""
    if result is False:
        return False
    return result


def build_python_cases():
    cases = {}

    timestamps = sample_timestamps()

    for timestamp in timestamps:
        local_seconds = timestamp + 8 * 3600
        cases["futureTrading|%d" % local_seconds] = sp.is_future_market_trading(
            _datetime.datetime.fromtimestamp(local_seconds, tz=SHANGHAI)
        )

    for timestamp in timestamps:
        local_seconds = timestamp + 8 * 3600
        now = _datetime.datetime.fromtimestamp(local_seconds, tz=SHANGHAI)
        for name, (weekday, weekend) in MARKET_PARAMS.items():
            for delta in FILE_AGES:
                file_time = local_seconds - delta
                key = "betweenClose|%s|%d|%d" % (name, local_seconds, file_time)
                cases[key] = sp._check_between_market_close(now, file_time, weekday, weekend)

    for timestamp in timestamps:
        local_seconds = timestamp + 8 * 3600
        now = _datetime.datetime.fromtimestamp(local_seconds, tz=SHANGHAI)
        for age in NEED_FILE_AGES:
            file_time = local_seconds - age
            key = "needFile|%d|%d" % (local_seconds, file_time)
            # 用内存映射模拟文件修改时间，避免真的建文件
            mtimes = {"<quote>": file_time}
            cases[key] = _norm(sp.need_file(now, "<quote>", 60, mtimes))

    for symbol_text in STOCK_SYMBOLS:
        symbol = StockSymbol(symbol_text)
        for timestamp in timestamps:
            local_seconds = timestamp + 8 * 3600
            now = _datetime.datetime.fromtimestamp(local_seconds, tz=SHANGHAI)
            for age in STOCK_AGES:
                file_time = local_seconds - age
                key = "stockNeed|%s|%d|%d" % (symbol_text, local_seconds, file_time)
                mtimes = {"<quote>": file_time}
                cases[key] = sp.stock_need_new_quotes(symbol, "<quote>", now, 60, mtimes)

    return cases


def main():
    reference = load_reference()
    python_cases = build_python_cases()

    only_php = sorted(set(reference) - set(python_cases))
    only_python = sorted(set(python_cases) - set(reference))

    mismatches = []
    for key in sorted(set(reference) & set(python_cases)):
        if reference[key] != python_cases[key]:
            mismatches.append((key, reference[key], python_cases[key]))

    print("compared cases : %d" % len(set(reference) & set(python_cases)))
    print("php only       : %d" % len(only_php))
    print("python only    : %d" % len(only_python))
    print("mismatches     : %d" % len(mismatches))

    for key in only_php[:20]:
        print("  PHP-ONLY  %s = %r" % (key, reference[key]))
    for key in only_python[:20]:
        print("  PY-ONLY   %s = %r" % (key, python_cases[key]))
    for key, php_value, py_value in mismatches[:40]:
        print("  MISMATCH  %-48s php=%r python=%r" % (key, php_value, py_value))

    return 1 if (mismatches or only_php or only_python) else 0


if __name__ == "__main__":
    sys.exit(main())
