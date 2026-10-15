"""CK4-D：把生产端 ``SH513050`` 的持仓明细做成演示库样本。

    python -B auto/_scratch/_ck4_sample.py            # 只解析 + 打印计划
    python -B auto/_scratch/_ck4_sample.py --apply    # 真的写库

## 为什么要造样本

演示库的 ``holdings`` 表是**空的**（``holdingsdate`` 有 11 行 2026-01-02 的
遗留），所以 ``StockGetHoldingsReference`` 的判据永远为假、Holdings 分支
在页面上从不出现 —— 迁完了也**验证不了**。生产端有真实持仓数据
（``holdingscn.php?symbol=SH513050`` 一页就把 input 与 output 都摆出来了），
把它搬一份过来就有了对照基准。

## 生产端那一页同时给了输入与输出

=======================  ================================================
表                        用途
=======================  ================================================
``holdingstable``         **输入**：成分股代码 / 持仓比例 / 持仓日的旧价
``referencetable``        **输入**：各成分股的当前价（实时快照）
``estimationtable``       **输出**：官方 EST / 参考 EST / 溢价
``netvaluehistorytable``  **输入+输出**：持仓日净值
=======================  ================================================

于是"搬样本"不需要猜：比例、旧价、当前价、净值全都从同一页取，
时刻一致，Python 页面算出来就该等于那一页的数。

## 写哪些表

* ``stock``        —— 成分股符号（港股 5 位数字、美股字母）大多还不在库里；
* ``realtimequote``—— 当前价快照（web 只读快照，不联网，所以必须写它）；
* ``stockhistory`` —— **两天的**收盘价：持仓日（估值式里的"旧价"）与
  官方 EST 日（港股/美股已收盘那天的价）；
* ``netvaluehistory`` —— 基金自己在持仓日的净值（估值式的基准）；
* ``holdingsdate`` / ``holdings`` —— 分支判据与持仓清单本身。

## 三个日期别搞混

生产端那一页抓于 2026-10-15 10:08（港股正在盘中）：

=================  ==========================================================
日期                 用途
=================  ==========================================================
2026-10-13         **持仓日**：净值（1.0115）与成分股旧价都取这天
2026-10-14         港股/美股**已收盘**那天 —— 官方 EST 用这天的收盘价
2026-10-15 10:08   **当前价**（``referencetable``）—— 参考 EST 用它
=================  ==========================================================

所以光搬 ``holdingstable``（旧价）+ ``referencetable``（当前价）只够算
**参考 EST**；官方 EST 要 10-14 的收盘价。那一页没直接给，但
``referencetable`` 有"涨幅"列 —— 港股是相对上一交易日（10-14）收盘的涨幅，
于是 ``10-14 收盘价 = 当前价 / (1 + 涨幅)`` 可以反推出来。

## ``realtimequote.id`` 不是自增（踩过的坑）

``SHOW CREATE TABLE realtimequote`` 显示 ``id int unsigned NOT NULL``
**没有 auto_increment**，主键就是 ``stock_id``。第一版没写 ``id``，
38 只成分股全插到 ``id = 0`` 那一行上互相覆盖，最后只剩一只 ——
页面自然算不出数。**写这张表必须显式给 ``id``。**
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource  # noqa: E402

SYMBOL = "SH513050"
PAGE_URL = "https://palmmicro.com/woody/res/holdingscn.php?symbol=%s" % SYMBOL
#: 中文页的显示名（库里 ``stock.name``）
DEFAULT_NAME = ""


def fetch_page(path: str) -> str:
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    result = subprocess.run(
        ["curl", "-s", "--max-time", "60", PAGE_URL, "-o", path],
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit("抓页面失败：%s" % result.stderr.decode("utf-8", "replace"))
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _table_rows(html: str, table_id: str) -> list:
    start = html.find('id="%s"' % table_id)
    if start < 0:
        return []
    end = html.find("</TABLE>", start)
    if end < 0:
        end = html.find("</table>", start)
    segment = html[start:end]
    rows = []
    for match in re.finditer(r"<tr[^>]*>(.*?)</tr>", segment, re.S):
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", match.group(1), re.S)
        ]
        if cells:
            rows.append(cells)
    return rows


def parse_holdings(html: str) -> tuple:
    """``holdingstable`` → (持仓日, [(符号, 比例, 旧价), ...])。

    末行是"全部"汇总（代码列不是符号），跳过。
    """
    holdings = []
    for cells in _table_rows(html, "holdingstable")[1:]:
        symbol = cells[0]
        if not symbol or symbol in ("全部",):
            continue
        try:
            ratio = float(cells[1])
            old_price = float(cells[2])
        except (ValueError, IndexError):
            continue
        holdings.append((symbol, ratio, old_price))
    return holdings


def parse_reference(html: str) -> dict:
    """``referencetable`` → {符号: (当前价, 涨幅%, 日期, 时间, 名称)}。

    涨幅列用来反推上一交易日（10-14）的收盘价，见模块 docstring。
    """
    result = {}
    for cells in _table_rows(html, "referencetable")[1:]:
        if len(cells) < 5:
            continue
        symbol = cells[0]
        try:
            price = float(cells[1])
        except ValueError:
            continue
        try:
            change = float(cells[2].rstrip("%"))
        except ValueError:
            change = 0.0
        result[symbol] = (price, change, cells[3], cells[4],
                          cells[5] if len(cells) > 5 else "")
    return result


def prev_close_of(price: float, change: float) -> float:
    """``涨幅`` 反推上一交易日收盘价：``price / (1 + change/100)``。"""
    return price / (1.0 + change / 100.0)


def est_close_of(item: tuple, est_date: str) -> float:
    """某只成分股在 ``est_date`` 的收盘价。

    ``referencetable`` 的"涨幅"基准日**不统一**，这是这个样本最容易错的地方：

    * 港股 10-15 还在盘中 → 报价日 10-15，涨幅相对 **10-14** 收盘，
      得反推 ``price / (1 + change)``；
    * 美股 10-15 还没开盘 → 报价日就是 10-14，当前价**就是** 10-14 收盘价，
      再反推一次会算出个不存在的价（PDD 反推出 81.958，其实 81.45 才是）。

    于是按报价日期分两路，而不是一律反推。
    """
    price, change, quote_date = item[0], item[1], str(item[2])
    if est_date and quote_date > est_date:
        return prev_close_of(price, change)
    return price


def parse_estimation(html: str) -> dict:
    """``estimationtable`` → 官方 EST / 参考 EST / 净值（生产端的**输出**）。"""
    rows = _table_rows(html, "estimationtable")
    output = {}
    for cells in rows[1:]:
        if len(cells) >= 5 and cells[0] == SYMBOL:
            output["official"] = cells[1]
            output["official_date"] = cells[2]
            output["official_premium"] = cells[3]
            output["fair"] = cells[4]
            output["fair_premium"] = cells[5] if len(cells) > 5 else ""
            break
    return output


def parse_netvalue(html: str) -> list:
    """``netvaluehistorytable`` → [(日期, 净值, 涨幅), ...]。"""
    rows = _table_rows(html, "netvaluehistorytable")[1:]
    return [(cells[0], cells[1], cells[2] if len(cells) > 2 else "") for cells in rows]


def holdings_date_of(html: str) -> str:
    match = re.search(r"基金持仓</font>更新于\s*([0-9-]+)", html)
    if match:
        return match.group(1)
    match = re.search(r"更新于\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", html)
    return match.group(1) if match else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", default="/tmp/h_SH513050.html", help="生产端页面（本地有就用，没有才抓）")
    parser.add_argument("--apply", action="store_true", help="真的写库（默认只打印计划）")
    args = parser.parse_args()

    html = fetch_page(args.page)
    holdings = parse_holdings(html)
    reference = parse_reference(html)
    estimation = parse_estimation(html)
    netvalue = parse_netvalue(html)
    date = holdings_date_of(html)

    print("生产端 %s" % PAGE_URL)
    print("  持仓日       %s" % date)
    print("  成分股       %d 只" % len(holdings))
    print("  参考价       %d 个符号" % len(reference))
    print("  生产端输出   官方EST %s（%s，溢价 %s） 参考EST %s（溢价 %s）" % (
        estimation.get("official"), estimation.get("official_date"),
        estimation.get("official_premium"), estimation.get("fair"),
        estimation.get("fair_premium")))
    print("  净值         %s" % (netvalue[:3],))
    print()
    est_date = estimation.get("official_date") or ""
    print("前 5 只成分股：")
    for symbol, ratio, old in holdings[:5]:
        now = reference.get(symbol)
        print("  %-8s 比例 %8.2f%%  旧价(10-13) %10s  当前价 %8s  %s收盘 %s" % (
            symbol, ratio, old,
            now[0] if now else "（无）",
            est_date,
            "%.4f" % prev_close_of(now[0], now[1]) if now else "（无）"))

    missing = [s for s, _r, _p in holdings if s not in reference]
    if missing:
        print()
        print("  [注] 参考表里没有当前价的成分股 %d 只：%s" % (len(missing), missing[:8]))

    #: 官方 EST 用的是"已收盘那天"的价，从生产端的 EST 日期拿，不写死
    est_date = estimation.get("official_date") or ""
    print()
    print("各符号的报价日期（决定 %s 收盘价怎么取）：" % est_date)
    by_date = {}
    for symbol, item in sorted(reference.items()):
        by_date.setdefault(item[2], []).append(symbol)
    for quote_date in sorted(by_date):
        print("  %s  %2d 只  %s" % (
            quote_date, len(by_date[quote_date]), by_date[quote_date][:8]))
    print()
    print("  -> 报价日 == %s 的，当前价**就是**那天收盘价；" % est_date)
    print("     报价日更新的（港股 10-15 盘中），才用涨幅反推。")

    print()
    print("%s 收盘价（前 8 只）：" % est_date)
    for symbol, _ratio, _old in holdings[:8]:
        item = reference.get(symbol)
        if item:
            print("  %-8s 当前 %10s @%s  涨幅 %6s%%  ->  %10.4f %s" % (
                symbol, item[0], item[2], item[1],
                est_close_of(item, est_date),
                "（反推）" if str(item[2]) > est_date else "（即当前价）"))

    if not args.apply:
        print()
        print("（dry-run）加 --apply 才写库。")
        return 0

    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()
    try:
        _apply(cursor, source, date, holdings, reference, netvalue, est_date)
        source.connection.commit()
    finally:
        cursor.close()
        source.connection.close()
    print()
    print("已写入。下一步：起服务看 /stock/%s，与生产端那一页对照。" % SYMBOL)
    return 0


def _apply(cursor, source, date, holdings, reference, netvalue, est_date="") -> None:
    """写库。每一步都打印条数 —— 静默少写几行最难查。"""

    def stock_id_of(symbol: str) -> int:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row:
            return int(row["id"] if isinstance(row, dict) else row[0])
        cursor.execute("INSERT INTO stock (symbol, name) VALUES (%s, %s)",
                       (symbol, (reference.get(symbol) or ("",))[3] or symbol))
        return int(cursor.lastrowid)

    fund_id = stock_id_of(SYMBOL)
    print("基金 %s -> stock_id %d" % (SYMBOL, fund_id))

    #: 持仓日与持仓清单（分支判据与基准日）
    cursor.execute(
        "INSERT INTO holdingsdate (id, `date`) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE `date` = %s", (fund_id, date, date))
    print("holdingsdate %s -> %s" % (fund_id, date))

    cursor.execute("DELETE FROM holdings WHERE stock_id = %s", (fund_id,))
    written = 0
    for symbol, ratio, _old in holdings:
        holding_id = stock_id_of(symbol)
        cursor.execute(
            "INSERT INTO holdings (stock_id, holding_id, ratio) VALUES (%s, %s, %s)",
            (fund_id, holding_id, ratio))
        written += 1
    print("holdings %d 行" % written)

    #: 成分股的旧价（估值式的分母）
    #:
    #: **只删本基金成分股那几行** —— 第一版写成
    #: ``stock_id IN (SELECT id FROM stock)``，那是"所有符号"，把 10-13 那天
    #: 中间层（^NDX / ^GSPC / QQQ / SPY）的历史行一起清了，
    #: 最后靠 ``_ck4_restore.py`` 从备份捞回来。**清数据前先想清楚 IN 的右值。**
    holding_ids = [stock_id_of(symbol) for symbol, _r, _p in holdings]
    if holding_ids:
        placeholders = ", ".join(["%s"] * len(holding_ids))
        cursor.execute(
            "DELETE FROM stockhistory WHERE stock_id IN (%s) AND `date` = %%s"
            % placeholders,
            tuple(holding_ids) + (date,))
    for symbol, _ratio, old in holdings:
        stock_id = stock_id_of(symbol)
        cursor.execute(
            "INSERT INTO stockhistory (stock_id, `date`, close, volume, adjclose) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE close = %s, adjclose = %s",
            (stock_id, date, str(old), "0", str(old), str(old), str(old)))
    print("stockhistory 旧价 %d 行 @ %s" % (len(holdings), date))

    #: 官方 EST 日（10-14）的收盘价 —— 由"当前价 / (1+涨幅)"反推，见模块 docstring
    if est_date:
        for symbol, _ratio, _old in holdings:
            item = reference.get(symbol)
            if not item:
                continue
            stock_id = stock_id_of(symbol)
            close = est_close_of(item, est_date)
            cursor.execute(
                "INSERT INTO stockhistory (stock_id, `date`, close, volume, adjclose) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE close = %s, adjclose = %s",
                (stock_id, est_date, str(close), "0", str(close),
                 str(close), str(close)))
        print("stockhistory 收盘价 %d 行 @ %s（反推）" % (
            len([s for s, _r, _p in holdings if s in reference]), est_date))

    #: 当前价快照（web 只读快照，不联网）
    #:
    #: **``id`` 必须显式给 ``stock_id``** —— 这张表的主键不是自增，
    #: 不给的话所有符号会挤到 ``id = 0`` 那一行上互相覆盖（踩过）。
    for symbol in reference:
        price, change, quote_date, quote_time, name = reference[symbol]
        stock_id = stock_id_of(symbol)
        cursor.execute(
            "INSERT INTO realtimequote "
            "(id, symbol, source_symbol, price, prev_price, name, "
            " quote_date, quote_time, source, fetched_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()) "
            "ON DUPLICATE KEY UPDATE price = %s, quote_date = %s, "
            "quote_time = %s, name = %s, fetched_at = NOW()",
            (stock_id, symbol, symbol.lower(), str(price), "0", name or symbol,
             quote_date, quote_time, "manual",
             str(price), quote_date, quote_time, name or symbol))
    print("realtimequote 快照 %d 行" % len(reference))

    #: 基金自己在持仓日的净值（估值式的基准）
    for symbol in (SYMBOL,):
        stock_id = stock_id_of(symbol)
        for row in netvalue:
            if len(row) >= 2:
                cursor.execute(
                    "INSERT INTO netvaluehistory (stock_id, `date`, close) VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE close = %s",
                    (stock_id, row[0], row[1], row[1]))
    print("netvaluehistory %d 行" % len(netvalue))


if __name__ == "__main__":
    sys.exit(main())
