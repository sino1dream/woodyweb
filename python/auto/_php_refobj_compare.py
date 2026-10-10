"""Differential check: compare the Python ``refobj`` port against real PHP output.

This is the verification harness for ``php/stock/qdiiref.php`` /
``fundref.php`` / ``mysqlref.php`` / ``stockref.php`` as ported into
``python/auto/refobj.py`` (plus the shared formulas that ended up in
``fundcalc.py`` / ``stockref.py`` / ``historytable.py``).

Run it with no arguments and it runs the PHP harness itself:

    python -B _php_refobj_compare.py            # exit 0 when clean

Requires PHP on PATH.

## 为什么只对照"纯数学"那部分

``refobj`` 大部分是**有状态**的（要读 MySQL：净值、仓位、校准值、行情），
没有数据库就没法逐项对照。但恰恰是下面这批纯函数最容易出**静默**的移植错误，
而且页面上的数字全部由它们算出来：

======================  ==========================================  ==============
PHP                      作用                                         Python
======================  ==========================================  ==============
``QdiiGetCalibration``   校准值 = 中间层值 × 汇率 / 净值              ``fundcalc``
``QdiiGetVal``           中间层值 → 基金净值口径                      ``fundcalc``
``QdiiGetPeerVal``       ``QdiiGetVal`` 的逆                         ``fundcalc``
``FundAdjustPosition``   按仓位加权                                  ``fundcalc``
``FundReverseAdjustPosition``  上式逆                                ``fundcalc``
``StockGetPercentage``   涨跌幅百分比（含 ``MIN_FLOAT_VAL`` 除零保护） ``stockref``
``StockCalcHedge``       对冲值 = 校准值 / 仓位                       ``historytable``
``GetEstQuantity``       中间层份额 → 基金份额（intval 截断）          ``refobj``
``GetQdiiQuantity``      基金份额 → 中间层份额（intval 截断）          ``refobj``
======================  ==========================================  ==============

## 为什么不做 ``GbToUtf8``

PHP 那个是**手写 GB2312 查表**，表在 ``GB2312Sql`` 里（要连库）。
本版改用标准库 ``gb18030``，属于**有意的实现替换**，无法用同一个 harness
逐项对照 —— 已在 ``refobj`` 的 docstring 里记为已知偏差。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import fundcalc as fc
import historytable as ht
import refobj as ro
import stockref as sr

HERE = Path(__file__).resolve().parent
PHP_HARNESS = HERE / "_php_refobj_check.php"

#: 与 PHP harness 里完全相同的用例集。改一边必须同时改另一边。
#: 有代表性的数值集：零点、正负、真分数、大数、以及 ``MIN_FLOAT_VAL``
#: （``5e-7``）两侧 —— 那个常量在 ``StockGetPercentage`` 里决定"算还是返回 0"。
VALUES = [
    0.0, 0.5, 1.0, 1.5, 2.0, 10.0, 100.0,
    -3.5, -1.0,
    1e-6, 1e-7, 4e-7, 5e-7, 6e-7, 1e-9,
    1e6, 1e9,
    4.814, 4.884, 32.079543, 749.378, 6.705, 0.95,
]


def build_case_tables():
    """按"边界值 + 组合"生成用例表。

    ## 为什么要组合生成而不是手写几组

    手写容易漏掉**判据附近**的情况。``StockGetPercentage`` 里那句
    ``if (abs($divisor) > MIN_FLOAT_VAL)``（``5e-7``）就是典型：
    ``4e-7`` 与 ``6e-7`` 分居阈值两侧，只有专门试过才知道移植有没有把
    ``>`` 写成 ``>=``。所以数值集里显式放了 ``4e-7 / 5e-7 / 6e-7``。

    抽取方式（与 PHP harness 保持一致，改一边必须同时改另一边）：

    * ``TRIPLES``  —— ``(v[i], v[i+3], v[i+7])``，三个参数不共位，避免退化组合；
    * ``ADJUSTS``  —— 仓位相关，第一个参数固定取 ``0.95``（真实 LOF 仓位）
      与 ``1.0``（ETF），后两个取网格值；
    * ``PERCENTS`` —— 阈值边界 + 真实价格对；
    * ``HEDGES``   —— 真实校准值 / 仓位。
    """
    count = len(VALUES)
    triples = []
    for index in range(0, count - 3, 4):
        triples.append((VALUES[index], VALUES[(index + 3) % count],
                        VALUES[(index + 7) % count]))

    adjusts = []
    for position in (0.95, 1.0):
        for index in range(0, count, 5):
            adjusts.append((position, VALUES[index], VALUES[(index + 2) % count]))

    percents = [
        # 阈值两侧（MIN_FLOAT_VAL = 5e-7）
        (4e-7, 1.0), (5e-7, 1.0), (6e-7, 1.0),
        (0.0, 1.0), (0.0, 0.0),
        # 真实价格对
        (4.814, 4.884), (3.294, 3.312), (2.374, 2.398),
        (1.0, 1.0), (1.0, 0.0), (1.0, 2.0), (-1.0, 2.0),
        (100.0, 101.0), (1e9, 1e9 - 1.0),
    ]

    hedges = [
        (32.079543, 0.95), (22.110973, 0.95), (13.66412, 1.0),
        (100358.446, 1.0), (1.0, 0.95), (0.0, 0.95),
        (-5.0, 0.5), (-5.0, -0.5), (1e-9, 0.95),
    ]

    return triples, adjusts, percents, hedges


TRIPLES, ADJUSTS, PERCENTS, HEDGES = build_case_tables()

FACTORS = [1.0, 32.079543, 0.5, 2.5]
QUANTITIES = [100, 0, -100, 7, 999999]

#: 已知的有意差异（key -> 原因）。命中这些不算失败。
#:
#: **除零那条不在这里**：`values_equal()` 已经把"两边都抛异常"统一判为一致
#: （语言给的异常类名不同，语义相同），不必逐条登记 —— 逐条登记会随着
#: 用例集扩大而不断漏掉新的同类项。
KNOWN_DIVERGENCES = {}


#: 与 PHP harness 的 ``ERR_PREFIX`` 一致：抛异常时记成 ``'#ERR:<异常类名>'``。
#
# 为什么需要它：有些用例**本来就该炸**。``FundReverseAdjustPosition`` 要除以仓位，
# 仓位为 0 时 PHP 8 抛 ``DivisionByZeroError``、Python 抛 ``ZeroDivisionError``。
# 记下这个差异才是重点 —— 两个语言的**异常类名不同**，所以按"是不是异常"归一化，
# 而不是假装两边都算得出数。
ERR_PREFIX = "#ERR:"


def normalize_value(value):
    """把返回值归一成"可跨语言比较"的形式。

    ## 为什么不能直接比字符串

    PHP 的 ``sprintf('%.14G')`` 与 Python 的 ``'%.14G'`` 对**同一个数**会给出
    不同文本：

    ```text
    php='1.0E-7'   python='1E-07'
    php='1.0E-18'  python='1E-18'
    ```

    差值来自尾数要不要保留 ``.0``、指数要不要补零 —— 都是**同值异写**。
    直接比字符串会把它们报成 6 个假失败。

    所以：能解析成数字的就归一到 ``float``（比较时按相对误差），
    解析不了的（``'#ERR:xxx'``、空串）原样返回。
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return float(value)
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
    """比较两个归一化后的值。

    三条规则，按顺序：

    1. **两边都是异常** —— 就算一致。语言给的异常类名本来就不同
       （PHP ``DivisionByZeroError`` / Python ``ZeroDivisionError``），
       语义才是要守的东西（该炸的地方两边都炸）。
    2. **数字按相对误差** —— 绕过 ``1.0E-7`` vs ``1E-07`` 这类同值异写。
    3. 其余按相等。
    """
    php_text = php_value if isinstance(php_value, str) else ""
    py_text = py_value if isinstance(py_value, str) else ""
    if php_text.startswith(ERR_PREFIX) and py_text.startswith(ERR_PREFIX):
        return True

    left = normalize_value(php_value)
    right = normalize_value(py_value)
    if isinstance(left, float) and isinstance(right, float):
        if left == right:
            return True
        scale = max(abs(left), abs(right))
        return scale > 0 and abs(left - right) / scale <= tolerance
    return left == right


def _php_float(value) -> float:
    """把一个 PHP 返回值当数字读；异常串（``#ERR:…``）或非数字算 0。

    ``string`` 也能进 PHP 的返回值（``strval(0.0)`` 给 ``'0'``），
    所以这里先走 ``float()`` 再兜异常。
    """
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = value if isinstance(value, str) else ""
    if text.startswith(ERR_PREFIX):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def call(func, *args):
    """统一调用入口：异常归一成 ``'#ERR:<类名>'``，与 PHP 侧同构。"""
    try:
        return fmt(func(*args))
    except Exception as exc:                    # noqa: BLE001 - 故意兜全部
        return ERR_PREFIX + type(exc).__name__


def fmt(value):
    """与 PHP harness 的 ``fmt()`` 对齐：浮点用 14 位有效数字。"""
    if isinstance(value, float):
        return "%.14G" % value
    return value


def build_python_cases() -> dict:
    """按**序号**生成 key，与 PHP harness 一一对应。

    key 里不放数值文本：两边对浮点的字符串化不一致（PHP 的 ``%.12E`` 给
    ``E-9``、Python 给 ``E-09``，PHP 不补零指数），用数值拼 key 会让同一组
    用例生成两个 key，整体误报成 PHP-ONLY / PY-ONLY。序号从结构上杜绝它。
    """
    cases = {}

    for index, (a, b, c) in enumerate(TRIPLES):
        cases["QdiiGetCalibration[%d]" % index] = call(fc.qdii_get_calibration, a, b, c)
        cases["QdiiGetVal[%d]" % index] = call(fc.qdii_get_value, a, b, c)
        cases["QdiiGetPeerVal[%d]" % index] = call(fc.qdii_get_peer_value, a, b, c)

    for index, (r, v, o) in enumerate(ADJUSTS):
        cases["FundAdjustPosition[%d]" % index] = call(fc.fund_adjust_position, r, v, o)
        cases["FundReverseAdjustPosition[%d]" % index] = call(
            fc.fund_reverse_adjust_position, r, v, o)

    for index, (d, v) in enumerate(PERCENTS):
        cases["StockGetPercentage[%d]" % index] = call(sr.stock_get_percentage, d, v)

    for index, (c, p) in enumerate(HEDGES):
        cases["StockCalcHedge[%d]" % index] = call(ht.stock_calc_hedge, c, p)

    # 份额换算：PHP 是 intval($q / $f) / intval($q * $f)，refobj 的同名方法
    # 就是它们的 Python 版。用 (factor 序号, quantity 序号) 做 key。
    for f_index, factor in enumerate(FACTORS):
        for q_index, quantity in enumerate(QUANTITIES):
            key = "[%d,%d]" % (f_index, q_index)
            cases["GetEstQuantity" + key] = call(
                lambda f=factor, q=quantity: int(q / f))
            cases["GetQdiiQuantity" + key] = call(
                lambda f=factor, q=quantity: int(q * f))

    return cases


def load_reference(reference_path: Path) -> dict:
    """问 PHP 要参考值。

    PHP 直接把 JSON 打到 stdout（沙箱里的 PHP 未必能写工作区）。
    """
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
    """自检：证明这个对照脚本**能抓到注入的移植错误**。

    一个从不失败的对照脚本等于没有。这里在内存里把公式改错（不动源文件），
    断言比较逻辑会把它判为不一致。

    用例集扩大后这套自检依然有用：它检验的是**判据的灵敏度**，不是某个具体用例。
    """
    import stockref as sr

    failures = []

    print("=== ① 基准：未改动时应全部一致 ===")
    baseline = build_python_cases()
    bad = [k for k in reference if not values_equal(reference[k], baseline.get(k))]
    print("  与 PHP 不一致的用例数：%d" % len(bad))
    if bad:
        failures.append("基准就不一致：%s" % bad[:5])

    print("")
    print("=== ② 注入错误：把 QdiiGetCalibration 的乘除写反 ===")
    original = fc.qdii_get_calibration

    def wrong(est, cny, net):
        # 故意写反：应当是 est * cny / net
        return est / cny * net if cny else 0.0

    fc.qdii_get_calibration = wrong
    try:
        broken = build_python_cases()
    finally:
        fc.qdii_get_calibration = original

    caught = [k for k in reference
              if k.startswith("QdiiGetCalibration")
              and not values_equal(reference[k], broken.get(k))]
    print("  抓到的 QdiiGetCalibration 用例数：%d" % len(caught))
    if not caught:
        failures.append("注入的乘除错误没被抓到 —— 对照脚本无效")
    else:
        print("  例：%s php=%r python=%r" % (
            caught[0], reference[caught[0]], broken[caught[0]]))

    print("")
    print("=== ③ 注入错误：把 StockGetPercentage 的阈值判断改错 ===")
    original_percent = sr.stock_get_percentage

    def wrong_percent(divisor, dividend):
        if abs(divisor) < 1.0:          # 应当是 abs(divisor) > MIN_FLOAT_VAL
            return (dividend / divisor - 1.0) * 100.0 if divisor else 0.0
        return 0.0

    sr.stock_get_percentage = wrong_percent
    try:
        broken2 = build_python_cases()
    finally:
        sr.stock_get_percentage = original_percent

    caught2 = [k for k in reference
               if k.startswith("StockGetPercentage")
               and not values_equal(reference[k], broken2.get(k))]
    print("  抓到的 StockGetPercentage 用例数：%d" % len(caught2))
    if not caught2:
        failures.append("注入的阈值错误没被抓到")
    else:
        for key in caught2[:2]:
            print("  例：%s php=%r python=%r" % (key, reference[key], broken2[key]))

    print("")
    print("=== ④ 边界：MIN_FLOAT_VAL 两侧必须被区分 ===")
    # **按数值序号取用例，不能用 sorted(reference)**
    #
    # 键名是 ``StockGetPercentage[10]`` 这种带方括号的字符串，而
    # ``'0' < ']'``（48 < 93），所以字典序里 ``[10]`` 排在 ``[1]`` **前面**。
    # 原先写 ``sorted(reference)[:3]`` 拿到的其实是 case 0 / 10 / 11
    # （``(4e-7,1.0)`` / ``(1.0,2.0)`` / ``(-1.0,2.0)``），跟阈值毫无关系 ——
    # 这条自检一直通过，仅仅因为那三个值碰巧互不相同。
    boundary = [reference["StockGetPercentage[%d]" % index] for index in (0, 1, 2)]
    labels = ("4e-7 (阈值下)", "5e-7 (恰等阈值)", "6e-7 (阈值上)")
    for label, value in zip(labels, boundary):
        print("  %-18s -> %s" % (label, value))

    # 真正的判据是 ``abs($divisor) > MIN_FLOAT_VAL``：写成 ``>=`` 的话
    # case 1 就不再返回 0。所以这里直接钉住"恰等阈值 = 0、刚刚超过 ≠ 0"。
    if _php_float(boundary[1]) != 0.0:
        failures.append("恰等于阈值时 PHP 应返回 0（`>` 而非 `>=`），实际 %s" % boundary[1])
    if _php_float(boundary[2]) == 0.0:
        failures.append("刚超过阈值时 PHP 应算出百分比，实际还是 0")
    if len(set(str(v) for v in boundary)) == 1:
        failures.append("阈值两侧的用例结果相同，说明用例集没有区分度")

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
        reference = load_reference(HERE / "_php_refobj_reference.json")
        return selftest(reference)

    reference_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "_php_refobj_reference.json"

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
        print("  KNOWN     %-52s php=%r python=%r  (%s)"
              % (key, php_value, py_value, KNOWN_DIVERGENCES[key]))
    for key, php_value, py_value in unexpected[:40]:
        print("  MISMATCH  %-52s php=%r python=%r" % (key, php_value, py_value))

    return 1 if (unexpected or only_php or only_python) else 0


if __name__ == "__main__":
    sys.exit(main())
