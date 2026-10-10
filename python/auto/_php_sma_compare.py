"""``stockhistory`` 数值核心的 PHP 对照（第一刀）。

    cd python\\auto
    $env:PATH = "D:\\xampp\\php;" + $env:PATH
    python -B _php_sma_compare.py
    python -B _php_sma_compare.py --selftest

## 对照什么

``php/stockhis.php`` 里**不碰配置、不碰渲染**的那部分：

============================  ================================================
PHP                            说明
============================  ================================================
``_estSma``                    最近 ``avg-1`` 个的均值
``GetQuadraticEquationRoot``   二次方程求根（布林带要用）
``_estBollingerBands``         当日布林上下轨
``_estNextBollingerBands``     T+1 布林上下轨
``_isWeekEnd`` / ``_isMonthEnd``  日/周/月三套序列的**切片判据**
``MaxMin``                     配色用的"只初始化一次"区间
``_getColorAndOrderArray``     配色 + 纵轴标尺
============================  ================================================

## 为什么值得逐项对照

布林带那两式是**解二次方程**反推标准差，系数里全是 (n-4)/(n-1)/(n-8) 这类
常数。抄错一个下标不会崩，只会让轨道慢慢偏 —— 页面上"看起来还是个布林带"。
均线那个 ``avg - 1`` 更是典型的 off-by-one（除数与元素个数都是 ``avg-1``）。

## key 用序号，不用数值

两边对浮点的字符串化不一致（PHP 的 ``%.14G`` 给 ``E-7``、Python 给 ``E-07``），
用数值拼 key 会让同一组用例生成两个 key、整体误报。详见
``README_refobj_php_compare.md`` 第 5.2 节。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
PHP_HARNESS = HERE / "_php_sma_check.php"

import stockhistory as sh  # noqa: E402

#: 已知的有意差异（key -> 原因）。命中这些不算失败。
KNOWN_DIVERGENCES = {
    "_isMonthEnd[3,7]":
        "PHP 的 GetNextTradingDayTick() 走节假日表（新年等），本版简化为"
        "『下一个工作日』；仅节假日边界会差，已记在 README。",
}


# ---------------------------------------------------------------------------
# 用例集：必须与 _php_sma_check.php 完全一致
# ---------------------------------------------------------------------------
def _build_serie() -> list:
    """与 PHP 侧同一条伪随机序列（收敛型）。"""
    serie = []
    value = 100.0
    for index in range(64):
        value += ((index % 7) - 3) * 0.37 + 0.021 * index
        serie.append(round(value, 4))
    return serie


def _build_series() -> dict:
    serie = _build_serie()
    rising = [10.0 + index for index in range(40)]
    falling = [100.0 - 1.5 * index for index in range(40)]
    return {
        "serie": serie,
        "flat": [50.0] * 32,
        "rising": rising,
        "falling": falling,
        "short": [12.5, 11.0, 9.5],
        "negative": [-3.5, -1.25, 0.0, 2.75, -8.0, 4.5, -0.5, 1.0, 6.25, -2.0,
                     -3.5, -1.25, 0.0, 2.75, -8.0, 4.5, -0.5, 1.0, 6.25, -2.0],
    }


QUADRATICS = [
    (1.0, -3.0, 2.0),
    (1.0, 2.0, 1.0),
    (1.0, 0.0, 1.0),
    (-4.0, 8.0, 4.0),
    (2.5, -7.5, 1.25),
    (1e-6, 1e-3, 1.0),
]

#: 固定的"现在"，与 PHP 侧同一组
FIXED_NOW = ["2026-10-09", "2026-10-11", "2026-10-31", "2026-11-01"]

SMA_SETS = {
    "daily_first": {
        "D5": "101.25", "D10": "99.5", "D20": "97.0",
        "W5": "105.0", "W10": "95.0", "W20": "103.0",
        "M5": "98.0", "M10": "102.0", "M20": "106.0",
        "EMA50": "100.5", "EMA200": "90.0",
    },
    "only_ema": {"EMA50": "55.0", "EMA200": "44.0"},
    "all_inside": {
        "D5": "10.0", "D10": "20.0", "W5": "15.0", "M5": "12.0",
        "EMA50": "18.0",
    },
    "all_outside": {
        "D5": "1.0", "D10": "2.0", "W5": "100.0", "M5": "200.0",
        "EMA50": "300.0",
    },
}


def _build_dates() -> list:
    """与 PHP 侧同一段日期（2026-09-25 起 45 天）。"""
    import datetime

    start = datetime.date(2026, 9, 25)
    return [(start + datetime.timedelta(days=index)).isoformat() for index in range(45)]


def fmt(value):
    """与 PHP harness 的 ``fmt()`` 对齐。"""
    if isinstance(value, float):
        return "%.14G" % value
    return value


def build_python_cases() -> dict:
    cases = {}
    series = _build_series()

    for name, quotes in series.items():
        for avg in (5, 10, 20, 2):
            cases["_estSma(%s,%d)" % (name, avg)] = fmt(
                float(sh.est_sma(quotes, avg)))

    for index, (a, b, c) in enumerate(QUADRATICS):
        roots = sh.get_quadratic_equation_root(a, b, c)
        cases["GetQuadraticEquationRoot[%d]" % index] = (
            None if roots is None else [fmt(roots[0]), fmt(roots[1])])

    for name, quotes in series.items():
        bands = sh.est_bollinger_bands(quotes, 20)
        cases["_estBollingerBands(%s)" % name] = (
            None if bands is None else [fmt(bands[0]), fmt(bands[1])])
        next_bands = sh.est_next_bollinger_bands(quotes, 20)
        cases["_estNextBollingerBands(%s)" % name] = (
            None if next_bands is None else [fmt(next_bands[0]), fmt(next_bands[1])])

    dates = _build_dates()
    for now_index, now_text in enumerate(FIXED_NOW):
        for date_index, date_text in enumerate(dates):
            previous = dates[date_index + 1] if date_index + 1 < len(dates) else None
            key = "[%d,%d]" % (now_index, date_index)
            cases["_isWeekEnd" + key] = sh.is_week_end(date_text, previous, now_text)
            cases["_isMonthEnd" + key] = sh.is_month_end(date_text, previous, now_text)

    for name, sma in SMA_SETS.items():
        colors, order = sh.get_color_and_order_array(sma)
        for key, color in colors.items():
            cases["color(%s,%s)" % (name, key)] = color
        cases["order(%s)" % name] = order

    instance = sh.MaxMin()
    cases["MaxMin.initial"] = [None, None]
    instance.init(0.0, 10000000.0)
    cases["MaxMin.after_init"] = [fmt(instance.f_max), fmt(instance.f_min)]
    instance.init(5.0, 6.0)                      # 应当不生效
    cases["MaxMin.after_second_init"] = [fmt(instance.f_max), fmt(instance.f_min)]
    for value in (-1.0, 5.0, 20000000.0, 3.0):
        instance.set(value)
    cases["MaxMin.after_sets"] = [fmt(instance.f_max), fmt(instance.f_min)]
    cases["MaxMin.fit_inside"] = instance.fit(4.0)
    cases["MaxMin.fit_below"] = instance.fit(-10.0)
    cases["MaxMin.fit_above"] = instance.fit(1e9)

    return cases


# ---------------------------------------------------------------------------
# 比较
# ---------------------------------------------------------------------------
ERR_PREFIX = "#ERR:"


def normalize_value(value):
    """把返回值归一成可跨语言比较的形式。

    数字 -> ``float``（绕过 ``1.0E-7`` vs ``1E-07`` 的同值异写）；
    列表 -> 逐项归一；其余原样。
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return text
        try:
            return float(text)
        except ValueError:
            return text
    return value


def values_equal(php_value, py_value, tolerance: float = 1e-9) -> bool:
    """两边都是异常就算一致；数字按相对误差；其余按相等。"""
    php_text = php_value if isinstance(php_value, str) else ""
    py_text = py_value if isinstance(py_value, str) else ""
    if php_text.startswith(ERR_PREFIX) and py_text.startswith(ERR_PREFIX):
        return True

    left = normalize_value(php_value)
    right = normalize_value(py_value)

    if isinstance(left, list) and isinstance(right, list):
        return (len(left) == len(right)
                and all(values_equal(a, b, tolerance) for a, b in zip(left, right)))
    if isinstance(left, float) and isinstance(right, float):
        if left == right:
            return True
        scale = max(abs(left), abs(right))
        return scale > 0 and abs(left - right) / scale <= tolerance
    return left == right


def load_reference(reference_path: Path) -> dict:
    """问 PHP 要参考值（PHP 把 JSON 打到 stdout）。"""
    if reference_path.exists():
        return json.loads(reference_path.read_text(encoding="utf-8"))

    print("reference missing, running the PHP harness...")
    completed = subprocess.run(
        # -n 跳过 php.ini：否则启动告警会落到 stdout，而 stdout 只能有 JSON
        ["php", "-n", "-d", "display_errors=stderr", str(PHP_HARNESS), "-"],
        cwd=str(HERE),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.stderr:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
    payload = completed.stdout.decode("utf-8")
    start = payload.find("{")
    if start < 0:
        raise SystemExit("PHP harness produced no JSON on stdout")
    return json.loads(payload[start:])


def selftest(reference: dict) -> int:
    """自检：证明对照能抓到注入的错误。

    一个从不失败的对照脚本等于没有。这里在内存里把公式改错（不动源文件），
    断言比较逻辑抓得到。
    """
    failures = []

    print("=== ① 基准：未改动时应全部一致 ===")
    baseline = build_python_cases()
    bad = [key for key in reference if not values_equal(reference[key], baseline.get(key))]
    print("  与 PHP 不一致的用例数：%d" % len(bad))
    if bad:
        failures.append("基准就不一致：%s" % bad[:5])

    print("")
    print("=== ② 注入错误：把 _estSma 的除数从 avg-1 改成 avg ===")
    original = sh.est_sma

    def wrong(quotes, avg):
        count = avg                      # 应当是 avg - 1
        total = 0.0
        for value in quotes[:count]:
            total += value
        return total / count

    sh.est_sma = wrong
    try:
        broken = build_python_cases()
    finally:
        sh.est_sma = original

    caught = [key for key in reference
              if key.startswith("_estSma") and not values_equal(reference[key], broken.get(key))]
    print("  抓到的 _estSma 用例数：%d" % len(caught))
    if not caught:
        failures.append("注入的 off-by-one 没被抓到 —— 对照脚本无效")
    else:
        print("  例：%s php=%r python=%r" % (caught[0], reference[caught[0]], broken[caught[0]]))

    print("")
    print("=== ③ 注入错误：布林带把 sigma 的 /2 改成 /4 ===")
    original_bands = sh.est_bollinger_bands

    def wrong_bands(quotes, avg=20):
        total = 0.0
        quadratic_total = 0.0
        count = avg - 1
        for value in quotes[:count]:
            total += value
            quadratic_total += value * value
        factor = 1.0 * (avg - 4)
        a = factor * count * count - 4 * count
        b = (8 - 2 * factor * count) * total
        c = factor * total * total - 4 * quadratic_total
        roots = sh.get_quadratic_equation_root(a, b, c)
        if roots is None:
            return None
        x1, x2 = roots
        # 故意错：应当是 /2
        return (x1 - 2 * (total - count * x1) / 4, x2 - 2 * (total - count * x2) / 4)

    sh.est_bollinger_bands = wrong_bands
    try:
        broken2 = build_python_cases()
    finally:
        sh.est_bollinger_bands = original_bands

    caught2 = [key for key in reference
               if key.startswith("_estBollingerBands(")
               and not values_equal(reference[key], broken2.get(key))]
    print("  抓到的布林带用例数：%d" % len(caught2))
    if not caught2:
        failures.append("注入的 sigma 错误没被抓到")
    else:
        print("  例：%s php=%r python=%r" % (caught2[0], reference[caught2[0]], broken2[caught2[0]]))

    print("")
    print("=== ④ 两式必须给出不同结果（否则等于只迁了一式）===")
    bands = sh.est_bollinger_bands(_build_series()["serie"], 20)
    next_bands = sh.est_next_bollinger_bands(_build_series()["serie"], 20)
    print("  当日：%s" % (bands,))
    print("  T+1 ：%s" % (next_bands,))
    if bands == next_bands:
        failures.append("两条布林式子结果相同 —— 很可能只迁了一式")

    print("")
    if failures:
        print("**自检失败：**")
        for item in failures:
            print("  - %s" % item)
        return 1
    print("自检通过：对照脚本能抓到注入的错误。")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return selftest(load_reference(HERE / "_php_sma_reference.json"))

    reference_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "_php_sma_reference.json"
    reference = load_reference(reference_path)
    python_cases = build_python_cases()

    only_php = sorted(set(reference) - set(python_cases))
    only_python = sorted(set(python_cases) - set(reference))
    mismatches = []
    for key in sorted(set(reference) & set(python_cases)):
        if not values_equal(reference[key], python_cases[key]):
            mismatches.append((key, reference[key], python_cases[key]))

    known = [item for item in mismatches if item[0] in KNOWN_DIVERGENCES]
    unexpected = [item for item in mismatches if item[0] not in KNOWN_DIVERGENCES]

    print("compared cases : %d" % len(set(reference) & set(python_cases)))
    print("php only       : %d" % len(only_php))
    print("python only    : %d" % len(only_python))
    print("mismatches     : %d (%d known, %d unexpected)"
          % (len(mismatches), len(known), len(unexpected)))

    for key in only_php[:20]:
        print("  PHP-ONLY  %s = %r" % (key, reference[key]))
    for key in only_python[:20]:
        print("  PY-ONLY   %s = %r" % (key, python_cases[key]))
    for key, php_value, py_value in known:
        print("  KNOWN     %-46s php=%r python=%r" % (key, php_value, py_value))
        print("            (%s)" % KNOWN_DIVERGENCES[key])
    for key, php_value, py_value in unexpected[:40]:
        print("  MISMATCH  %-46s php=%r python=%r" % (key, php_value, py_value))

    return 1 if (unexpected or only_php or only_python) else 0


if __name__ == "__main__":
    sys.exit(main())
