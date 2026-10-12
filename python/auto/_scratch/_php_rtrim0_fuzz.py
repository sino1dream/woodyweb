"""``rtrim0()`` 的随机模糊对照：用真实 PHP 结果批量校验 14 位有效数字格式化。

固定用例的对照见 ``_php_numfmt_compare.py``；这里补的是随机与边界值，
因为 PHP ``strval(float)`` 的 14 位有效数字规则在指数边界、次正规数、
负零等地方最容易写错，靠人工挑几个值不足以证明。

    cd python\\auto
    python -B _php_rtrim0_fuzz.py 3000      # 需要 php 在 PATH 中
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path
# 脚本挪到 python/auto/_scratch/ 之后，sys.path[0] 只剩 _scratch，
# 所以要把 python/auto 与 python 两级目录显式补进来，才 import 得到 ref/、sql/ 这些子包。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ref import stockref as sr

HERE = Path(__file__).resolve().parent



def build_values(count: int):
    """构造随机值：混合普通区间、指数边界、以及次正规数。"""
    rng = random.Random(20260105)  # 固定种子，失败可复现
    values = [
        "0", "-0.0", "0.0", "1e-323", "5e-324", "1.7976931348623157e308",
        "2.2250738585072014e-308", "9007199254740993", "1e15", "1e16", "1e-4", "1e-5",
        "99999999999999.9", "999999999999999.9", "0.1", "0.30000000000000004",
    ]
    for _ in range(count):
        mode = rng.randrange(5)
        if mode == 0:
            value = rng.uniform(-1000, 1000)
        elif mode == 1:
            value = rng.uniform(-1, 1)
        elif mode == 2:
            value = rng.uniform(-1, 1) * (10.0 ** rng.randint(-320, 308))
        elif mode == 3:
            value = float(rng.randint(-10 ** 15, 10 ** 15))
        else:
            value = rng.uniform(-1e6, 1e6) / (10.0 ** rng.randint(0, 12))
        values.append(repr(value))
    return values


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    values = build_values(count)

    # 通过 stdin 传值：Windows 命令行有长度上限，几千个值放不进参数列表
    completed = subprocess.run(
        ["php", "-n", "-d", "display_errors=stderr", str(HERE / "_php_rtrim0_dump.php"), "-"],
        cwd=str(HERE),
        input="\n".join(values).encode("utf-8"),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.stderr:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
    reference = json.loads(completed.stdout.decode("utf-8"))

    mismatches = []
    for value, php_value in zip(values, reference):
        python_value = sr.rtrim0(value)
        if php_value != python_value:
            mismatches.append((value, php_value, python_value))

    print("compared values : %d" % len(values))
    print("mismatches      : %d" % len(mismatches))
    for value, php_value, python_value in mismatches[:30]:
        print("  MISMATCH  %-26s php=%r python=%r" % (value, php_value, python_value))

    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
