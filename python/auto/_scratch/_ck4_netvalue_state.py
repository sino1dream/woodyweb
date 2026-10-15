"""真实库上验证 ``net_value_paragraph``（CK4 的净值记录段落）。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
sys.path.insert(0, "auto")

from core.stockdata import MySqlFundDataSource
from ref.holdingsref import StockGetHoldingsReference
from web.services.history_service import HistoryService

SYMBOL = "SH513050"

source = MySqlFundDataSource.from_env()
service = HistoryService(source)
holdings_ref = StockGetHoldingsReference(SYMBOL, source=source)

print("=== _net_value_refs(%s) ===" % SYMBOL)
print(service._net_value_refs(SYMBOL, holdings_ref))

table = service.net_value_paragraph(SYMBOL, holdings_ref, num=1)
print()
print("=== net_value_paragraph ===")
print("title  :", table["title"])
print("columns:", [c["text"] for c in table["columns"]])
print("total  :", table["total"], " rows:", len(table["rows"]))
for row in table["rows"]:
    print("  cells:", row.cells, " match:", row.match)

print()
print("=== 生产端对照 ===")
print("  期望 columns: ['日期', '净值', '涨幅']")
print("  期望 row    : ['2026-10-13', '1.0115', '-0.24%']")
