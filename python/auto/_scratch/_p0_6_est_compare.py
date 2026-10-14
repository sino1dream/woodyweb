"""P0.6 对照：本地 ``fundest`` 的官方 EST vs 生产端 qdiicn.php 的官方 EST。

只读，不写库。生产端页面先抓到本地再用：

    curl -s https://palmmicro.com/woody/res/qdiicn.php -o /tmp/qdiicn.html
    python -B auto/_scratch/_p0_6_est_compare.py /tmp/qdiicn.html

**注意日期口径**：两边日期不同就直接标"跨日"，不比数值 ——
拿 10-13 的数去对 10-14 的数，差值里既有公式误差也有市场波动，说不清。
"""

from __future__ import annotations

import io
import json
import os
import re
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def parse_production(path: str) -> dict:
    """从生产端 HTML 里抠出 ``estimationtable`` 的「官方 EST / EST日期」两列。"""
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    start = text.find('id="estimationtable"')
    end = text.find("</tbody>", start)
    segment = text[start:end]
    result = {}
    for row in re.findall(r"<tr>(.*?)</tr>", segment, re.S):
        cells = re.findall(r"<td class=c1[^>]*>(.*?)</td>", row, re.S)
        if not cells:
            continue
        match = re.search(r">([A-Z]{2}\d{6})</a>", cells[0])
        if not match:
            continue
        values = [re.sub(r"<[^>]+>", "", cell).strip() for cell in cells[1:]]
        result[match.group(1)] = values
    return result


def main(argv: list) -> int:
    if len(argv) < 2:
        print("用法：python -B auto/_scratch/_p0_6_est_compare.py <qdiicn.html>")
        return 2
    production = parse_production(argv[1])

    from core.stockdata import MySqlFundDataSource
    from sql.fundeststore import FundEstStore

    source = MySqlFundDataSource.from_env()
    try:
        print("%-10s %-12s %-10s %-12s %-10s %s" % (
            "符号", "本地官方EST", "本地日期", "生产官方EST", "生产日期", "偏差"))
        print("-" * 76)
        same_day = 0
        cross_day = []
        for symbol in sorted(production):
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                print("%-10s %s" % (symbol, "不在 stock 表"))
                continue
            row = FundEstStore(source.connection).get_close(stock_id, None)
            local = None
            local_date = "-"
            cursor = source.connection.cursor()
            try:
                cursor.execute(
                    "SELECT `date`, close FROM fundest WHERE stock_id = %s "
                    "ORDER BY `date` DESC LIMIT 1",
                    (stock_id,),
                )
                record = cursor.fetchone()
            finally:
                cursor.close()
            if record:
                local, local_date = record["close"], str(record["date"])
            prod = production[symbol]
            prod_value, prod_date = prod[0], prod[1]
            if local is None or not prod_value:
                print("%-10s %-12s %-10s %-12s %-10s %s" % (
                    symbol, local or "-", local_date, prod_value or "-", prod_date, "-"))
                continue
            if local_date != prod_date:
                # 跨日不比精度，但**量级必须比**：口径错（差几十倍）要能一眼看出来，
                # 一个交易日的波动（±3% 内）则属正常。
                deviation = float(local) / float(prod_value) - 1.0
                cross_day.append((symbol, deviation))
                print("%-10s %-12s %-10s %-12s %-10s 跨日 %+.2f%%（仅量级）" % (
                    symbol, "%.6f" % float(local), local_date, prod_value, prod_date,
                    deviation * 100.0))
                continue
            same_day += 1
            deviation = float(local) / float(prod_value) - 1.0
            print("%-10s %-12s %-10s %-12s %-10s %+.2f%%" % (
                symbol, "%.6f" % float(local), local_date, prod_value, prod_date,
                deviation * 100.0))
        print("")
        print("同日可比 %d 只 / 生产端共 %d 只" % (same_day, len(production)))
        if cross_day:
            deviations = [abs(value) for _, value in cross_day]
            worst = max(cross_day, key=lambda item: abs(item[1]))
            print("跨日 %d 只：量级偏差 |max| = %.2f%%（%s），|min| = %.2f%%" % (
                len(cross_day), max(deviations) * 100.0, worst[0], min(deviations) * 100.0))
            outliers = [item for item in cross_day if abs(item[1]) > 0.05]
            if outliers:
                print("超过 ±5% 的（要查，不只是数据陈旧）：")
                for symbol, value in outliers:
                    print("  %-10s %+.2f%%" % (symbol, value * 100.0))
    finally:
        source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
