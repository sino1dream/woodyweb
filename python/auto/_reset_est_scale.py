"""把演示数据重组成"归一化自洽"的口径（开发/验证用）。

## 为什么要这个脚本

`estquote` 接进估值公式时，`calibrationhistory` / `netvaluehistory` 必须满足
一组自洽关系，否则估值会差几个数量级（实测出现过 643194 与 0.228 两种错法）。
反复手改库里的单个值会把状态弄乱，所以用这个脚本**一次性重建**成已知自洽的
状态。

## 自洽关系（推导）

`fundcalc.calc_with_calibration()` 的完整路径是：

    ① 中间层反算（`reverse_calc_with_calibration`）：
         hedge = (est_m - (1 - p_m) * nv_m) * cal_m / p_m / cny

    ② 基金估算：
         est_f = (1 - p_f) * nv_f + p_f * hedge / cal_f

**注意 ① 不是齐次的**：`(1 - p_m) * nv_m` 是常数偏移项。当 `nv_m = 1.0` 且
`p_m = 1.0` 时它恰好为 0，公式才退化成 `hedge = est_m * cal_m`。

再要求"估值等于官方净值"：

    ③ est_f = nv_f   =>   hedge = nv_f * cal_f

联立 ① 与 ③（归一化 `nv_m = 1、p_m = 1、cny = 1`）：

    ④ est_m * cal_m = nv_f * cal_f

也就是 `est_source[中间层]` 必须等于 `nv_f * cal_f`。

            中间层 calibration = nv_f * cal_f / (cny * est_m * p_m)
            中间层 netvalue    = 1.0（归一化）
            基金层 calibration = 1.0

其中 ``cny`` 是汇率（USD/CNY，约 7.1）。**必须带上它**：估值公式里
``est_value`` 后面还要乘一次 ``cny``，所以校准值要先除掉，否则汇率会被算两遍
（实测估值会放大 7 倍左右）。

推导（`fundcalc.calc_with_calibration` 的完整链条）：
    ① 中间层反算（p_m = 1、nv_m = 1）:
           hedge = est_m * cal_m / cny
    ② 基金估算:
           est_f = (1-p_f) * nv_f + p_f * hedge * cny / cal_f
    ③ 要求 est_f = nv_f，代入 ① 得:
           p_f * (est_m * cal_m / cny) * cny / cal_f = p_f * nv_f
       =>  cal_m = nv_f * cal_f / (est_m * p_m)     …（与 cny 抵消）
    ④ 但 `StockDataArrayBuilder._attach_hedge_value()` 注入的是
           est_value = nv_f * cal_f / cny
       要让它满足 ①，需要 cal_m = nv_f * cal_f / (cny * est_m * p_m)

所以本脚本按 ④ 计算（含 cny），与构建器保持一致。

## 用法

    python -B auto\\_reset_est_scale.py            # 预演（只报告）
    python -B auto\\_reset_est_scale.py --apply    # 真正写库
    python -B auto\\_reset_est_scale.py --apply --date 2026-01-02
"""

from __future__ import annotations

import argparse
import datetime
import io
import os
import sys

# 注意：**不能**在模块顶层包 stdout。本模块的 `_plan()` / `_upsert_*()` 会被
# 别的脚本（如 _clean_stale_calibration.py）import，顶层替换 sys.stdout 会把
# 调用方的输出流关掉（实测 ValueError: I/O operation on closed file）。
# UTF-8 包装放在 `if __name__ == "__main__"` 分支里。

AUTO_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# 归一化常量：中间层净值与基金层校准值都取 1.0（见文件头推导）
NORMALIZED_NET_VALUE = 1.0
NORMALIZED_FUND_CALIBRATION = 1.0

# 演示用基金与其中间层标的（按 fundpair 的真实配对）
DEMO_FUNDS = ("SZ161130", "SZ161125", "SH513100")


def main() -> int:
    parser = argparse.ArgumentParser(description="重建归一化自洽的演示数据")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只报告）")
    parser.add_argument("--date", help="统一使用的日期；默认取已有汇率的最近一天")
    args = parser.parse_args()

    from stockdata import MySqlFundDataSource, load_mysql_env, mysql_config_from_env

    config = mysql_config_from_env(load_mysql_env(os.environ))
    print("目标库：%s@%s/%s\n" % (config["user"], config["host"], config["database"]))
    source = MySqlFundDataSource.from_env()
    connection = source.connection

    # 默认日期取"已有汇率的最近一天"。
    #
    # 为什么不是基金净值的日期：估值的官方日期必须与**汇率**同一天，
    # 否则 _get_default_cny_est() 只能往前回退 —— 实测基金官方日期
    # 2026-09-29 回退命中了 9 个月前的占位值。把三层（净值/校准值/汇率）
    # 对齐到同一天，口径才干净。
    date = args.date or _latest_forex_date(source)
    if date:
        print("统一日期：%s" % date)
    print("")
    try:
        plans = _plan(source, date)
        if not plans:
            print("没有可处理的数据（缺少 estquote 或净值）。")
            return 1

        print("%s %d 项：" % ("已重写" if args.apply else "将要重写", len(plans)))
        print("%-10s %-8s %-14s %-14s %-14s %s" % (
            "符号", "层", "日期", "est_quote/净值", "校准值", "中间层净值"))
        for item in plans:
            print("%-10s %-8s %-14s %-14s %-14s %s" % (
                item["symbol"], item["layer"], item["date"],
                item["value"], item["calibration"], item["middle_net_value"] or "-"))

        if not args.apply:
            print("\n这是预演。真正写库请加 --apply。")
            return 0

        cursor = connection.cursor()
        try:
            for item in plans:
                if item["layer"] == "middle":
                    _upsert_netvalue(cursor, item["stock_id"], item["date"], item["middle_net_value"])
                    _upsert_calibration(cursor, item["stock_id"], item["date"], item["calibration"])
                else:
                    _upsert_netvalue(cursor, item["stock_id"], item["date"], item["value"])
                    _upsert_calibration(cursor, item["stock_id"], item["date"], item["calibration"])
            connection.commit()
        finally:
            cursor.close()
        print("完成。用 python -B auto\\_verify_estimation.py 复核。")
    finally:
        connection.close()
    return 0


def _plan(source, date=None):
    """算出每只符号应当写成的净值与校准值。"""
    plans = []

    # 1) 中间层：netvalue = 1.0, calibration = 基金净值 / 源值
    #    需要先知道基金净值，所以先收集
    fund_net_values = {}
    for symbol in DEMO_FUNDS:
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            continue
        record = source.get_latest_net_value(stock_id)
        if record is not None:
            fund_net_values[symbol] = (stock_id, float(record.close), record.date)

    # 2) 中间层标的
    cursor = source.connection.cursor()
    try:
        cursor.execute("SELECT id, symbol FROM stock ORDER BY id")
        stocks = cursor.fetchall()
    finally:
        cursor.close()

    middle_ids = set()
    for row in stocks:
        stock_id, symbol = row["id"], row["symbol"]
        est = source.get_est_quote(stock_id)
        if est is None:
            continue
        middle_ids.add(stock_id)

        # 该中间层服务哪些基金？取用到它的第一只演示基金
        target = _fund_using(source, symbol, fund_net_values)
        if target is None:
            continue
        fund_symbol, fund_net_value, fund_date = target

        est_value = float(est.close)
        if est_value == 0:
            continue

        # 中间层仓位（p_m）与汇率（cny）
        middle_position = source.get_position(stock_id) or 1.0
        cny = _cny_for(source, fund_symbol, fund_date) or 1.0

        # 中间层校准值：使 hedge = 1（生产口径）
        #
        #   hedge = E_m * C_m / (cny * p_m) = 1   =>   C_m = cny * p_m / E_m
        #
        # 中间层 netvalue 取归一化的 1.0（元数据，不参与 hedge 推导）。
        calibration = cny * middle_position / est_value
        plans.append({
            "symbol": symbol,
            "stock_id": stock_id,
            "layer": "middle",
            "date": date or fund_date,
            "value": est.close,
            "calibration": calibration,
            "middle_net_value": NORMALIZED_NET_VALUE,
        })

    # 3) 基金层：netvalue = 官方净值, calibration = 生产口径值
    #
    # 校准值改用**生产口径**，不再固定为 1.0：
    #
    #     C_f = N_f * cny / p_f
    #
    # 它与生产页面上的 ``cal = est_value * cny / net_value`` 等价 ——
    # 因为完整链路里 ``est_value = E_m * C_m / cny / p_m``，代入后
    # ``N_f * C_f / p_f = E_m * C_m / (cny * p_m)`` 即 hedge = 1，
    # 于是 C_f = N_f*cny/p_f。生产数据可验证（官网 QQQ 页）：
    #   ^NDX 30807.93 / QQQ净值 749.3773 = 41.111 = QQQ 校准值 ✓
    #
    # 这样一来：
    #   * 估值仍等于官方净值（见 _verify_estimation）；
    #   * 校准值不再是恒定的 1.0，而是随净值与汇率变化的真实测量值；
    #   * hedge 恒等式 hedge = (C_f/p_f)/(C_m/p_m) 重新成立（≈1）。
    for symbol, (stock_id, net_value, net_date) in fund_net_values.items():
        fund_date = date or net_date
        position = source.get_position(stock_id) or 1.0
        cny = _cny_for(source, symbol, fund_date) or 1.0
        calibration = net_value * cny / position
        plans.append({
            "symbol": symbol,
            "stock_id": stock_id,
            "layer": "fund",
            "date": fund_date,
            "value": net_value,
            "calibration": calibration,
            "middle_net_value": None,
        })
    return plans


def _latest_forex_date(source) -> str:
    """取美元汇率里最近的一个日期，作为"统一日期"。

    这样重置后的净值/校准值/汇率都在同一天，`_get_default_cny_est()` 精确命中，
    不必回退。
    """
    connection = getattr(source, "connection", None)
    if connection is None:
        return None
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id FROM stock WHERE symbol = 'USCNY' LIMIT 1")
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute(
            "SELECT date FROM netvaluehistory WHERE stock_id = %s ORDER BY date DESC LIMIT 1",
            (row["id"],),
        )
        latest = cursor.fetchone()
        return str(latest["date"]) if latest else None
    finally:
        cursor.close()


def _cny_for(source, fund_symbol: str, date: str) -> float:
    """取该基金在该日期的汇率（USD/CNY）。

    直接复用数据源自己的取值逻辑，保证与估值链路用**同一个**汇率
    （对应 PHP ``qdiiref.php:135`` 的 ``$cny_ref->GetClose($strDate)``）。
    """
    try:
        return source._get_default_cny_est(fund_symbol, date)
    except Exception:
        return None


def _fund_using(source, middle_symbol, fund_net_values):
    """找出使用该中间层标的的演示基金（按 fundpair 的配对关系）。"""
    for fund_symbol, (stock_id, net_value, net_date) in fund_net_values.items():
        pair_id = source.get_fund_pair_id(stock_id)
        if pair_id is None:
            continue
        if source.get_stock_symbol(pair_id) == middle_symbol:
            return fund_symbol, net_value, net_date
    return None


def _upsert_netvalue(cursor, stock_id, date, value) -> None:
    cursor.execute(
        "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE close = VALUES(close)",
        (stock_id, date, value),
    )


def _upsert_calibration(cursor, stock_id, date, value) -> None:
    """写校准值；time / num 在表里是 NOT NULL 且无默认值，必须显式给。"""
    cursor.execute(
        "INSERT INTO calibrationhistory (stock_id, date, close, time, num) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE close = VALUES(close), time = VALUES(time), num = VALUES(num)",
        (stock_id, date, value, datetime.datetime.now().strftime("%H:%M:%S"), 1),
    )


if __name__ == "__main__":
    # 只有直接运行时才包 UTF-8 stdout（被 import 时不能动，见文件上方说明）
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.exit(main())
