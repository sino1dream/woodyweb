"""临时诊断：确认回补后的真实汇率被估值链路用上。"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fundcalc import PalmmicroFundCalculator  # noqa: E402
from stockdata import MySqlFundDataSource, StockDataArrayBuilder  # noqa: E402

source = MySqlFundDataSource.from_env()
data = StockDataArrayBuilder(source).build("SZ161130 SZ161125 SH513100 QQQ SPY")

row = data["SZ161130"]
print("SZ161130: 日期=%s CNYest=%s CNY=%s hedge_value=%s" % (
    row.get("date"), row.get("CNYest"), row.get("CNY"), row.get("hedge_value")))
print("汇率回退警告数: %d" % len(source.rate_warnings))
for warning in source.rate_warnings:
    print("   ", warning)
print("")

calculator = PalmmicroFundCalculator(data)
print("%-10s %-10s %-14s %-16s %s" % ("符号", "官方净值", "静态估值", "带中间层估值", "结论"))
for symbol in ("SZ161130", "SZ161125", "SH513100"):
    net_value = float(data[symbol]["netvalue"])
    static = calculator.est_net_value(symbol)
    realtime = calculator.est_net_value_with_est_quote(symbol)
    ok = abs(static - net_value) < 1e-3 and abs(realtime - net_value) < 1e-3
    print("%-10s %-10s %-14.6f %-16.6f %s" % (
        symbol, net_value, static, realtime, "一致" if ok else "不一致"))

print("")
cursor = source.connection.cursor()
cursor.execute(
    "SELECT s.symbol, n.date, n.close FROM netvaluehistory n "
    "JOIN stock s ON s.id = n.stock_id "
    "WHERE s.symbol = 'USCNY' AND n.date BETWEEN '2026-09-24' AND '2026-10-02' ORDER BY n.date"
)
print("USCNY 历史（2026-09-24 ~ 10-02，外汇管理局真实中间价）:")
for record in cursor.fetchall():
    print("   %s  %s" % (record["date"], record["close"]))

cursor.execute(
    "SELECT s.symbol, COUNT(*) AS n, MIN(n.date) AS first_date, MAX(n.date) AS last_date "
    "FROM netvaluehistory n JOIN stock s ON s.id = n.stock_id "
    "WHERE s.symbol IN ('USCNY','HKCNY','EUCNY','JPCNY') GROUP BY s.symbol ORDER BY s.symbol"
)
print("")
print("汇率历史总量:")
for record in cursor.fetchall():
    print("   %-8s %3d 条   %s ~ %s" % (
        record["symbol"], record["n"], record["first_date"], record["last_date"]))

source.connection.close()
