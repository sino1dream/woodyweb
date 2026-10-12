"""维护工具：把中间层校准值改回种子约定的尺度（幂等）。

## 什么时候用

``calibrationhistory`` 里中间层（``QQQ``/``SPY``）的校准值出现了
**与演示库尺度不匹配**的数，估值就会炸掉（实测过 20245 与 9504）：

```text
错误：QQQ = 41.488229  ->  SZ161130 官方 EST = 20245.200164
正确：QQQ = 0.008945   ->  SZ161130 官方 EST = 4.586786
```

原因见 ``python/auto/README_autocalibration_migration.md``：
演示库把中间层净值归一化成 1.0，而"生产口径"的校准值是按真实净值算的，
两者**不能混用** —— ``_qdii_value`` 的 ``ratio`` 对 ``cal_m`` 是线性的，
``est_value × cal_m`` 这个乘积必须守恒。

## 做法

删掉中间层**除基准日以外**的校准行，并确保基准日那一行是种子值。
基准日与种子值写死在下面的常量里（来源 ``dbtools/seed_demo_data.py``
与 ``README_autocalibration_migration.md`` 的实测记录）。

用法：

    python -B dbtools\\fix_middle_calibration.py            # 预演
    python -B dbtools\\fix_middle_calibration.py --apply    # 真改
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

#: 基准日（种子写的那一天）与各中间层在该日的正确校准值。
#:
#: 来源：``seed_demo_data.MARKET_LAYERS``（初值）+ ``solve()``（反解后的落地值）。
BASELINE_DATE = "2026-10-01"
BASELINE = {
    "QQQ": 0.008945,
    "SPY": 0.008712,
}


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    connection = source.connection
    cursor = connection.cursor()

    print("基准：%s   QQQ=%s  SPY=%s" % (BASELINE_DATE, BASELINE["QQQ"], BASELINE["SPY"]))
    print("")

    planned_delete = []
    planned_insert = []

    for symbol, expected in sorted(BASELINE.items()):
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("  %-4s 不在 stock 表里，跳过" % symbol)
            continue

        cursor.execute(
            "SELECT id, `date`, close FROM calibrationhistory WHERE stock_id = %s "
            "ORDER BY `date`",
            (stock_id,),
        )
        rows = cursor.fetchall()
        print("  %-4s 现有 %d 行：" % (symbol, len(rows)))
        has_baseline = False
        for row in rows:
            date = str(row["date"])
            # **只有基准日那一行、且值正确**才保留；其余全删。
            #
            # 我第一版只"补写缺失的基准行"，结果别的日期的错行（自动校准按生产
            # 口径写的 41.3）留在库里 —— 而 ``get_calibration_now()`` 取的是
            # **最新一条**，于是"修复"完估值还是错的。必须删干净。
            keep = date == BASELINE_DATE and abs(float(row["close"]) - expected) <= 1e-9
            if keep:
                has_baseline = True
                print("       %s %-14s 保留（正是基准值）" % (date, row["close"]))
            else:
                planned_delete.append(row["id"])
                print("       %s %-14s 删除" % (date, row["close"]))
        if not has_baseline:
            planned_insert.append((symbol, stock_id, expected))
            print("       将补写基准行 %s = %s" % (BASELINE_DATE, expected))

    print("")
    print("计划：删 %d 行，补 %d 行" % (len(planned_delete), len(planned_insert)))

    if not apply:
        print("这是预演。真正执行请加 --apply。")
        cursor.close()
        connection.close()
        return 0

    for row_id in planned_delete:
        cursor.execute("DELETE FROM calibrationhistory WHERE id = %s", (row_id,))
    for symbol, stock_id, value in planned_insert:
        cursor.execute(
            "INSERT INTO calibrationhistory (stock_id, `date`, close, `time`, num) "
            "VALUES (%s, %s, %s, '00:00:00', 1)",
            (stock_id, BASELINE_DATE, value),
        )
    connection.commit()
    print("完成。")
    cursor.close()

    # 改完立刻用真实管线自证：估值必须回到净值附近
    print("")
    print("=== 自证：官方 EST 应接近净值 ===")
    from calc.fundestcalc import build_estimate_calculator

    calculator = build_estimate_calculator(source)
    nav = {"SZ161130": 4.5452, "SZ161125": 3.1328, "SH513100": 2.0379}
    ok = True
    for symbol, target in sorted(nav.items()):
        record = calculator.compute_official(symbol)
        value = getattr(record, "value", None)
        if value is None:
            print("  %-10s 估不出来" % symbol)
            ok = False
            continue
        deviation = abs(value / target - 1.0)
        flag = "OK" if deviation <= 0.05 else "**偏差过大**"
        if deviation > 0.05:
            ok = False
        print("  %-10s 估值=%-14.6f 净值=%-8s 偏差=%6.2f%%  %s" % (
            symbol, value, target, deviation * 100, flag))
    connection.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
