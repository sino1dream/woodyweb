"""校验/修复基金层校准值：``est × cny ÷ 净值``（同锚一日）。

## 为什么需要

校准值必须用**基金单位净值**当分母，而不是场内成交价。PHP 的
``$this->GetPrice()`` 对基金引用对象返回的是净值；本版 ``get_price()``
拿到的是 LOF/ETF 的场内价，两者差 5% 上下。

实测 ``SZ161125`` 2026-10-16 被写成 ``15606.776298``，而正确值是
``16498.992399``（生产端 ``16498.992``，偏差 0.0000%）。差额正好是
``3.299/3.1206`` 那 5.7% —— 分母用了场内价。

**它不影响页面估值**（``fundestcalc`` 里 ``cal_f`` 会约掉），但会让
「校准记录」「对冲值」这两处展示出错，也会误导任何直接读 ``cal_f`` 的地方。

## 用法

    python -B sql\\verify_calibration_anchor.py            # 只报告
    python -B sql\\verify_calibration_anchor.py --apply    # 真修
    python -B sql\\verify_calibration_anchor.py --days 60  # 只看最近 60 天
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

#: 相对偏差超过这个比例才算"写歪了"。留 0.5% 是因为中间层净值与收盘价
#: 本身只差千分位，不该被当成错误。
TOLERANCE = 0.005

#: 中间层符号的取法：优先净值（与 ``_getOfficialEstVal`` 同序），
#: 没有净值就退回当天收盘价。
DEFAULT_DAYS = 400


def main() -> int:
    apply = "--apply" in sys.argv
    days = _arg_int("--days", DEFAULT_DAYS)

    source = MySqlFundDataSource.from_env()
    connection = source.connection
    cursor = connection.cursor()

    print("=== 逐条核对基金层校准值（只看最近 %d 天）===" % days)
    print("    判据：est(锚点日) × USCNY(锚点日) ÷ 基金净值(锚点日)")
    print("")

    cursor.execute(
        "SELECT c.stock_id, s.symbol, c.date, c.close AS stored "
        "FROM calibrationhistory c JOIN stock s ON s.id = c.stock_id "
        "WHERE s.symbol REGEXP '^(SZ16|SH5)' "
        "ORDER BY c.date DESC LIMIT %d" % int(days * 40))
    rows = cursor.fetchall()

    wrong = []
    checked = 0
    for row in rows:
        symbol = str(row["symbol"])
        date = str(row["date"])
        stored = row["stored"]
        if stored in (None, ""):
            continue

        est = _middle_value(source, cursor, symbol, date)
        cny = _cny_value(source, cursor, date)
        nav = _nav_value(source, cursor, row["stock_id"], date)
        if est is None or cny is None or nav in (None, 0):
            continue

        checked += 1
        expected = float(est) * float(cny) / float(nav)
        if expected == 0:
            continue
        diff = abs(float(stored) - expected) / abs(expected)
        if diff > TOLERANCE:
            wrong.append((symbol, date, float(stored), expected, diff))

    print("  可核对 %d 条；写歪 %d 条" % (checked, len(wrong)))
    for symbol, date, stored, expected, diff in wrong[:20]:
        print("    %-10s %s  库里 %-16.6f 应为 %-16.6f 差 %6.2f%%"
              % (symbol, date, stored, expected, diff * 100.0))

    if not wrong:
        print("  没有发现写歪的校准值。")
        cursor.close()
        connection.close()
        return 0

    if not apply:
        print("")
        print("这是报告模式。要修请加 --apply。")
        cursor.close()
        connection.close()
        return 0

    print("")
    for symbol, date, _stored, expected, _diff in wrong:
        cursor.execute(
            "UPDATE calibrationhistory c JOIN stock s ON s.id = c.stock_id "
            "SET c.close = %s WHERE s.symbol = %s AND c.date = %s",
            (expected, symbol, date))
    connection.commit()
    print("已修正 %d 条。" % len(wrong))

    cursor.close()
    connection.close()
    return 0


def _middle_value(source, cursor, symbol, date):
    """中间层在 ``date`` 的值：优先净值，其次收盘价。

    中间层符号由 ``fundpair`` 找（基金 → ETF），ETF 再找它跟踪的指数。
    找不到就返回 ``None``，跳过这条。
    """
    cursor.execute(
        "SELECT p.symbol AS pair_symbol FROM fundpair f "
        "JOIN stock a ON a.id = f.id JOIN stock p ON p.id = f.stock_id "
        "WHERE a.symbol = %s LIMIT 1", (symbol,))
    row = cursor.fetchone()
    if not row:
        return None
    pair = str(row["pair_symbol"])

    # ETF（如 SPY）自己跟踪指数（^GSPC）：校准值用的是**指数**点位
    cursor.execute(
        "SELECT i.symbol AS index_symbol FROM fundpair f "
        "JOIN stock a ON a.id = f.id JOIN stock i ON i.id = f.stock_id "
        "WHERE a.symbol = %s LIMIT 1", (pair,))
    index_row = cursor.fetchone()
    est_symbol = str(index_row["index_symbol"]) if index_row else pair

    est_id = source.get_stock_id(est_symbol)
    if est_id is None:
        return None
    value = source.get_netvalue_close(est_id, date)
    if value in (None, ""):
        value = source.get_history_close(est_id, date)
    return value


def _cny_value(source, cursor, date):
    cny_id = source.get_stock_id("USCNY")
    if cny_id is None:
        return None
    return source.get_netvalue_close(cny_id, date)


def _nav_value(source, cursor, stock_id, date):
    return source.get_netvalue_close(stock_id, date)


def _arg_int(name: str, fallback: int) -> int:
    if name not in sys.argv:
        return fallback
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        return fallback
    try:
        return int(sys.argv[index + 1])
    except ValueError:
        return fallback


if __name__ == "__main__":
    sys.exit(main())
