"""临时工具：清掉种子占位日期（2026-01-02）留在历史表里的假数据。

## 为什么必须清

历史数据页面（本次迁移）第一次把 ``netvaluehistory`` / ``stockhistory`` /
``calibrationhistory`` 的内容直接显示出来。种子里 ``2026-01-02`` 那批占位值
（如 SZ161130 净值 1.5234、QQQ 净值 560）本来只是为了让估值链路跑起来的合成值，
一旦显示在页面上就会被误当成真实历史 —— 实测「基金溢价记录」因此算出 214% 溢价。

**真实净值**（官网可验证）：
    SZ161130 2026-09-29 净值 4.5452
    SZ161125 2026-09-29 净值 3.1328
    SH513100 2026-09-29 净值 2.0379

所以只清种子占位日期的行，**真实抓到的净值/汇率/行情一律保留**。
"""

from __future__ import annotations

import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, "auto")

from core.stockdata import MySqlFundDataSource  # noqa: E402

# 只清**基金与中间层**符号的旧行。
#
# **不能按日期一刀切**：`netvaluehistory` 里同时存着汇率历史
# （USCNY / HKCNY / EUCNY / JPCNY，由 forexcli.py 从国家外汇管理局回补，
# 从 2026-07 起共 160+ 行）。按日期清会把这批真实数据一起删掉 —— 实测预演
# 就会误删 160 行真实汇率。所以这里按符号白名单收窄。
PLACEHOLDER_SYMBOLS = (
    "SZ161130", "SZ161125", "SH513100",
    "QQQ", "SPY", "^NDX", "^GSPC",
)

# 这些符号在 2026-09 之前只有种子占位值（真实净值/行情都从 2026-09 开始）。
CUTOFF = "2026-09-01"

TABLES = ("stockhistory", "netvaluehistory", "calibrationhistory", "shareshistory")


def _placeholder_filter(table: str) -> tuple:
    """返回 ``(WHERE 片段, 参数)``：限定表 + 符号白名单 + 日期上界。"""
    placeholders = ", ".join(["%s"] * len(PLACEHOLDER_SYMBOLS))
    where = (
        "`date` < %%s AND stock_id IN "
        "(SELECT id FROM stock WHERE symbol IN (%s))" % placeholders
    )
    return where, (CUTOFF,) + PLACEHOLDER_SYMBOLS


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== 待清理的占位行（符号白名单 + 日期 < %s）===" % CUTOFF)
    print("  符号：%s" % ", ".join(PLACEHOLDER_SYMBOLS))
    print("  **汇率历史（USCNY/HKCNY/EUCNY/JPCNY）不在此列，不会被删**")
    print("")
    total = 0
    for table in TABLES:
        where, params = _placeholder_filter(table)
        cursor.execute("SELECT COUNT(*) AS n FROM `%s` WHERE %s" % (table, where), params)
        count = cursor.fetchone()["n"]
        total += count
        print("  %-22s %d 行" % (table, count))
    print("  合计 %d 行" % total)

    print("")
    print("=== 明细 ===")
    for table in TABLES:
        where, params = _placeholder_filter(table)
        cursor.execute(
            "SELECT s.symbol, t.`date`, t.close FROM `%s` t JOIN stock s ON s.id = t.stock_id "
            "WHERE %s ORDER BY s.symbol, t.`date`" % (table, where),
            params,
        )
        for row in cursor.fetchall():
            print("  %-22s %-10s %s  %s" % (table, row["symbol"], row["date"], row["close"]))

    if not apply:
        print("\n这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    print("")
    for table in TABLES:
        where, params = _placeholder_filter(table)
        cursor.execute("DELETE FROM `%s` WHERE %s" % (table, where), params)
        print("  已删除 %-22s %d 行" % (table, cursor.rowcount))
    source.connection.commit()

    print("")
    print("=== 核对：汇率历史必须完好 ===")
    for symbol in ("USCNY", "HKCNY", "EUCNY", "JPCNY"):
        stock_id = source.get_stock_id(symbol)
        cursor.execute(
            "SELECT COUNT(*) AS n, MIN(`date`) AS d FROM netvaluehistory WHERE stock_id = %s",
            (stock_id,),
        )
        row = cursor.fetchone()
        print("  %-10s %d 行，最早 %s" % (symbol, row["n"], row["d"]))

    print("")
    print("=== 清理后的基金净值历史 ===")
    for symbol in ("SZ161130", "SZ161125", "SH513100", "QQQ", "SPY"):
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            continue
        cursor.execute(
            "SELECT date, close FROM netvaluehistory WHERE stock_id = %s ORDER BY date DESC LIMIT 4",
            (stock_id,),
        )
        rows = cursor.fetchall()
        print("  %-10s %s" % (symbol, ", ".join("%s=%s" % (r["date"], r["close"]) for r in rows) or "无"))
    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
