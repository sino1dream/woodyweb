"""中间层估值尺度自洽工具。

为什么需要它
------------
`estquote` 里存的是**源值**（新浪 QQQ 市价 ≈ 749.58），而
`fundcalc.calc_with_calibration()` 的公式是：

    fund_est = (1 - position) * fund_netvalue
             + position * est_value * cny / fund_calibration

其中 `est_value` 来自**中间层**的反算：

    hedge_value = (est_value - (1 - pos_m) * netvalue_m) * calibration_m / pos_m / cny

所以 **`calibration_m`（中间层校准值）必须与源值同尺度**。如果源从
Yahoo 的 `^QQQ-IV`（量级几十）换成新浪市价（量级几百）而校准值没跟着改，
估值就会被放大几十倍 —— 实测会出现 641 这种明显错误的数字。

对应 PHP 的角色是 `php/stock/fundpairref.php:188 DailyCalibration()`：

    $fFactor = $this->CalcFactor($fPairNetValue, floatval($strNetValue), $strDate);
    $cal_sql->WriteDaily($strStockId, $strDate, strval($fFactor));

本模块做同样的事，并额外处理"换源后一次性重标定"。

核心关系（务必记住）
--------------------
`php/stock/qdiiref.php:193`：

    fFactor = QdiiGetCalibration(est, cny, netvalue) = est * cny / netvalue

即**校准值是从"源值 + 净值"反算出来的派生量**，本身没有独立含义。
所以"重标定"只要保持 `calibration / est`（也就是 `cny / netvalue`）不变即可。

最简做法（本模块采用）：把中间层净值归一化为 **1.0**，于是：

    中间层 calibration = 1 / est_value      （使 est * cal = 1）
    基金层 calibration = 1.0                （使估值恒等于官方净值）

这样估算公式退化为 ``(1-p)*netvalue_f + p*netvalue_f``＝``netvalue_f``，
语义清楚、也便于核对。
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from typing import Any, Dict, List, Mapping, Optional

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# 中间层净值归一化基准。取 1.0 之后：calibration_m == 1 / est_value，最好核对。
NORMALIZED_NET_VALUE = 1.0

# 归一化口径下基金层校准值应当是 1.0（推导见文件头注释）。
NORMALIZED_CALIBRATION = 1.0


def middle_calibration_for(est_value: Any, middle_net_value: Any = NORMALIZED_NET_VALUE) -> float:
    """反算中间层校准值，使 ``est_value * calibration`` 等于中间层净值。

    也就是 ``calibration = netvalue_m / est_value``。

    **注意方向**（我在这里踩过坑）：不是 ``est / netvalue``。
    看 ``fundcalc.calc_with_calibration()`` 的中间层反算：

        hedge_value = (est - (1 - pos_m) * netvalue_m) * calibration_m / pos_m / cny

    ``pos_m = 1`` 时（中间层标的是满仓跟踪标的）化简为
    ``hedge_value = est * calibration_m``。要让中间层贡献 **等于它自己的净值**
    ``netvalue_m``（这正是静态路径的行为），必须

        est * calibration_m = netvalue_m   =>   calibration_m = netvalue_m / est

    如果写成 ``est / netvalue``，那 ``est_value * cal_m = est²`` ——
    实测会算出 643194 这种离谱数字。

    归一化净值（1.0）下就是 ``calibration_m = 1 / est_value``。
    """
    est = float(est_value)
    if est == 0:
        return 0.0
    net = float(middle_net_value if middle_net_value not in (None, "") else NORMALIZED_NET_VALUE)
    return net / est


def fund_calibration_for(est_value: Any, fund_net_value: Any) -> float:
    """反算**基金层**校准值，使估值等于基金官方净值。

    由 ``QdiiGetCalibration``（``php/stock/qdiiref.php:3``）的同一关系
    ``calibration = est * cny / netvalue`` 得来，只是这里的 "netvalue" 是
    **基金官方净值**，所以基金层校准值就等于官方净值本身（中间层已归一化到 1）。
    """
    net = float(fund_net_value)
    if net == 0:
        return 0.0
    # est_value * calibration_m 已归一化为 1，故基金层校准值 = 官方净值
    return net


def hedge_value_for(est_value: Any, calibration: Any, position: Any) -> float:
    """按 ``reverse_calc_with_calibration()`` 复算中间层 hedge_value，便于核对。

    ``hedge_value = (est - (1 - pos) * netvalue_m) * calibration / pos / cny``；
    归一化净值且 ``pos = 1`` 时退化为 ``est * calibration``，应当约等于 1。
    """
    p = float(position)
    if p == 0:
        return 0.0
    return float(est_value) * float(calibration) / p


def is_scale_consistent(est_value: Any, calibration: Any, tolerance: float = 0.05) -> bool:
    """判断中间层校准值是否与源值自洽。

    自洽条件是 ``est * calibration ≈ netvalue_m``（归一化时即 ≈ 1.0），
    而不是"两者相等" —— 这是我在第一版里搞反的地方。
    """
    try:
        est = float(est_value)
        cal = float(calibration)
    except (TypeError, ValueError):
        return False
    if est == 0 or cal == 0:
        return False
    return abs(est * cal / NORMALIZED_NET_VALUE - 1.0) <= tolerance


def diagnose(source, data: Mapping[str, Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    """检查中间层尺度是否自洽。

    与 :func:`diagnose_data` 的区别：本函数**直接查库**，逐只中间层标的比较
    ``estquote.close`` 与 ``calibrationhistory.close``。

    为什么不只看 build 出来的数据：中间层标的可能因为不是本次请求的输入符号而
    不在结果里（实测 QQQ 就有这种情况），只看数据会漏掉真正有问题的行。
    """
    problems = []
    connection = getattr(source, "connection", None)
    if connection is None:
        return problems

    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id, symbol FROM stock ORDER BY symbol")
        stocks = cursor.fetchall()
    finally:
        cursor.close()

    for row in stocks:
        stock_id, symbol = row["id"], row["symbol"]
        est = source.get_est_quote(stock_id)
        if est is None:
            continue
        calibration = source.get_calibration_now(stock_id)
        if calibration is None:
            problems.append({
                "symbol": symbol, "est_quote": est.close,
                "calibration": None, "ratio": None,
            })
            continue
        if is_scale_consistent(est.close, calibration.close):
            continue
        try:
            ratio = float(calibration.close) / float(est.close)
        except (TypeError, ValueError):
            ratio = None
        problems.append({
            "symbol": symbol,
            "est_quote": est.close,
            "calibration": calibration.close,
            "ratio": ratio,
        })
    return problems


def diagnose_data(data: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """检查一份 build 出来的数据里，中间层的尺度是否自洽。

    只覆盖本次构建涉及的符号；要全库体检请用 :func:`diagnose`。
    """
    problems = []
    for symbol, row in data.items():
        if not isinstance(row, Mapping):
            continue
        est = row.get("est_quote")
        calibration = row.get("calibration")
        # 只检查"有源值"的行；没有源值就无从判断尺度
        if est in (None, "") or calibration in (None, ""):
            continue
        if is_scale_consistent(est, calibration):
            continue
        problems.append({
            "symbol": symbol,
            "est_quote": est,
            "calibration": calibration,
            "ratio": float(calibration) / float(est) if float(est) else None,
        })
    return problems


def format_problems(problems: List[Dict[str, Any]]) -> str:
    """把诊断结果排版成可读文本。"""
    if not problems:
        return "中间层尺度自洽：所有带 est_quote 的行都满足 est * calibration ≈ 1。"
    lines = ["中间层尺度**不自洽**（est * calibration 偏离 1）："]
    lines.append("%-12s %-14s %-16s %s" % ("符号", "est_quote", "calibration", "est*cal"))
    for item in problems:
        try:
            product = float(item["est_quote"]) * float(item["calibration"])
            product_text = "%.6f" % product
        except (TypeError, ValueError):
            product_text = "-"
        lines.append("%-12s %-14s %-16s %s" % (
            item["symbol"], item["est_quote"],
            item["calibration"] if item["calibration"] is not None else "-",
            product_text))
    lines.append("")
    lines.append("处理办法：跑本脚本（不加 --apply 先预演）重标定，")
    lines.append("归一化口径下应满足 est_quote * calibration ≈ 1（见文件头注释）。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 重标定：把中间层的净值归一化为 1.0，校准值对齐源值
# ---------------------------------------------------------------------------
def _now_time() -> str:
    """当前时间 ``HH:MM:SS``，对应 PHP ``CalibrationSql::InsertDaily()`` 的 time 字段。"""
    import datetime as _datetime

    return _datetime.datetime.now().strftime("%H:%M:%S")


def recalibrate_middle_layer(source, apply: bool = False, date: str = None) -> List[Dict[str, Any]]:
    """把中间层与基金层的校准值重标定成"归一化"口径。

    重标定后的自洽关系（推导见本文件头注释）：

    ==================  ==========================================
    中间层 calibration   ``1 / est_value``（使 ``est * cal = 1``）
    中间层 netvalue      1.0（归一化）
    基金层 calibration  1.0（使估值恒等于官方净值）
    ==================  ==========================================

    验证：``est=749.58、cal_m=1/749.58、netvalue_m=1、p_m=1``
    → ``hedge_value = 749.58 * (1/749.58) = 1``
    → ``fund_est = (1-0.95)*4.8201 + 0.95*1/1 = 4.8201`` ＝ 官方净值 ✓

    对应 PHP 的角色是 `php/stock/fundpairref.php:188 DailyCalibration()`
    与 `php/stock/qdiiref.php:193 AdjustFactor()`。

    ``apply=False`` 时只报告将要做什么，不改库。返回变更清单。
    """
    changes = []
    connection = getattr(source, "connection", None)
    if connection is None:
        return changes

    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id, symbol FROM stock ORDER BY id")
        stocks = cursor.fetchall()
        for row in stocks:
            stock_id, symbol = row["id"], row["symbol"]
            est = source.get_est_quote(stock_id)
            if est is None:
                continue

            calibration = source.get_calibration_now(stock_id)
            current_cal = float(calibration.close) if calibration else None
            target_cal = middle_calibration_for(est.close)
            target_date = date or est.date

            if current_cal is not None and is_scale_consistent(est.close, current_cal):
                continue

            changes.append({
                "symbol": symbol,
                "stock_id": stock_id,
                "est_quote": est.close,
                "calibration_before": current_cal,
                "calibration_after": target_cal,
                "netvalue_after": NORMALIZED_NET_VALUE,
                "date": target_date,
            })
            if not apply:
                continue

            _upsert_netvalue(cursor, stock_id, target_date, NORMALIZED_NET_VALUE)
            _upsert_calibration(cursor, stock_id, target_date, target_cal)

        # 基金层：校准值置 1.0，估算即恒等于官方净值
        cursor.execute("SELECT id, symbol FROM stock ORDER BY id")
        for row in stocks:
            stock_id, symbol = row["id"], row["symbol"]
            if source.get_est_quote(stock_id) is not None:
                continue  # 中间层标的已经处理过

            calibration = source.get_calibration_now(stock_id)
            if calibration is None:
                continue
            # 必须写在与**校准值同一天**：StockDataArrayBuilder 是按校准值的日期
            # 去取 netvalue/est_quote 的，两者不在同一天会各取各的、对不上。
            fund_date = date or calibration.date
            net_record = source.get_latest_net_value(stock_id)
            if not fund_date or net_record is None:
                continue

            current_cal = float(calibration.close)
            if abs(current_cal - NORMALIZED_CALIBRATION) < 1e-9:
                continue

            changes.append({
                "symbol": symbol,
                "stock_id": stock_id,
                "est_quote": "-",
                "calibration_before": current_cal,
                "calibration_after": NORMALIZED_CALIBRATION,
                "netvalue_after": net_record.close,
                "date": fund_date,
            })
            if apply:
                _upsert_calibration(cursor, stock_id, fund_date, NORMALIZED_CALIBRATION)
                _upsert_netvalue(cursor, stock_id, fund_date, net_record.close)

        if apply:
            connection.commit()
    finally:
        cursor.close()
    return changes


def _upsert_netvalue(cursor, stock_id, date, value) -> None:
    """写净值。唯一键是 (date, stock_id)，用 ON DUPLICATE KEY 避免重复行。"""
    cursor.execute(
        "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE close = VALUES(close)",
        (stock_id, date, value),
    )


def _upsert_calibration(cursor, stock_id, date, value) -> None:
    """写校准值。

    ``calibrationhistory`` 的 ``time`` / ``num`` 都是 NOT NULL 且**没有默认值**，
    必须显式给 —— 对应 PHP ``CalibrationSql::InsertDaily()`` 里写
    ``time = DebugGetTime()``、``num = '1'``。
    """
    cursor.execute(
        "INSERT INTO calibrationhistory (stock_id, date, close, time, num) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE close = VALUES(close), time = VALUES(time), num = VALUES(num)",
        (stock_id, date, value, _now_time(), 1),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="中间层估值尺度自洽工具")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只报告）")
    parser.add_argument("--diagnose", action="store_true", help="检查当前数据尺度是否自洽")
    args = parser.parse_args()

    from stockdata import MySqlFundDataSource, load_mysql_env, mysql_config_from_env

    config = mysql_config_from_env(load_mysql_env(os.environ))
    print("目标库：%s@%s/%s\n" % (config["user"], config["host"], config["database"]))
    source = MySqlFundDataSource.from_env()

    try:
        if args.diagnose:
            print(format_problems(diagnose(source)))
            return 0

        changes = recalibrate_middle_layer(source, apply=args.apply)
        if not changes:
            print("中间层尺度已经自洽，无需重标定。")
            return 0

        print("%s %d 个中间层标的：" % ("已重标定" if args.apply else "将要重标定", len(changes)))
        print("%-10s %-14s %-16s %-16s %-12s %s" % (
            "符号", "源值(est_quote)", "校准值(改前)", "校准值(改后)", "净值改为", "日期"))
        for item in changes:
            print("%-10s %-14s %-16s %-16s %-12s %s" % (
                item["symbol"], item["est_quote"],
                "%.4f" % item["calibration_before"] if item["calibration_before"] is not None else "-",
                "%.4f" % item["calibration_after"],
                item["netvalue_after"], item["date"]))
        if not args.apply:
            print("\n这是预演。真正写库请加 --apply。")
    finally:
        connection = getattr(source, "connection", None)
        if connection is not None:
            connection.close()
    return 0


def _all_symbols(source) -> List[Mapping[str, Any]]:
    connection = source.connection
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT symbol FROM stock ORDER BY symbol")
        return cursor.fetchall()
    finally:
        cursor.close()


if __name__ == "__main__":
    sys.exit(main())
