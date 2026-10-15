"""CK4-D：本地 ``/stock/SH513050`` 与生产端持仓页逐格对照。

    python -B auto/_scratch/_ck4_compare.py [生产端页面] [本地页面]

两边**同一个输入**（本地库里的比例 / 旧价 / 当前价 / 净值都是从生产端那一页
搬过去的，见 ``_ck4_sample.py``），所以算出来的数应该一致 ——
对不上就是迁移的问题，不是数据新鲜度的问题。

===============  ==================================================
列                 来自
===============  ==================================================
官方EST            ``_estNetValue($strEstDate)``（成分股收盘价）
EST日期            ``GetOfficialDate()``
溢价                ``GetPercentageDisplay($fOfficialPrice)``
参考EST            ``_estNetValue()`` + ``fOfficialEstAdjust``
溢价               同上
===============  ==================================================
"""

from __future__ import annotations

import io
import os
import re
import sys
import tempfile

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SYMBOL = "SH513050"
#: Windows 版 Python 不认 Git Bash 的 /tmp，统一走 tempfile.gettempdir()
TMP = tempfile.gettempdir()
DEFAULT_PROD = os.path.join(TMP, "h_SH513050.html")
DEFAULT_LOCAL = os.path.join(TMP, "local_SH513050.html")


def rows_of(html: str, table_id: str, header_rows: int = 1) -> list:
    start = html.find('id="%s"' % table_id)
    if start < 0:
        start = html.find('class="data-table est-table"')
    if start < 0:
        return []
    end = html.find("</table>", start)
    if end < 0:
        end = html.find("</TABLE>", start)
    segment = html[start:end]
    rows = []
    for match in re.finditer(r"<tr[^>]*>(.*?)</tr>", segment, re.S):
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", match.group(1), re.S)
        ]
        if cells:
            rows.append(cells)
    return rows[header_rows:]


def main() -> int:
    prod_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROD
    local_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LOCAL

    for path in (prod_path, local_path):
        if not os.path.exists(path):
            print("找不到 %s" % path)
            return 2

    with open(prod_path, encoding="utf-8", errors="replace") as handle:
        prod_html = handle.read()
    with open(local_path, encoding="utf-8", errors="replace") as handle:
        local_html = handle.read()

    prod_rows = [r for r in rows_of(prod_html, "estimationtable") if r and r[0] == SYMBOL]
    local_rows = [r for r in rows_of(local_html, "estimationtable") if r and r[0] == SYMBOL]

    print("生产端 estimationtable：%s" % (prod_rows or "（没找到）"))
    print("本地   estimationtable：%s" % (local_rows or "（没找到）"))

    if not prod_rows or not local_rows:
        return 1

    prod, local = prod_rows[0], local_rows[0]
    print()
    print("%-10s %-24s %-24s %s" % ("列", "生产端", "本地", "差"))
    print("-" * 72)
    names = ["代码", "官方EST", "EST日期", "溢价", "参考EST", "溢价"]
    bad = 0
    for index in range(1, min(len(prod), len(local))):
        a, b = prod[index], local[index]
        if index in (1, 4):
            try:
                diff = "差 %.4f" % (float(b) - float(a))
                same = abs(float(b) - float(a)) <= 0.0005
            except ValueError:
                diff, same = "", a == b
        else:
            diff, same = "", a == b
        if not same:
            bad += 1
        print("%-10s %-24s %-24s %s" % (
            names[index] if index < len(names) else index, a, b, diff))

    print("-" * 72)
    print("不一致 %d 项" % bad)

    #: 说明文字（仓位 + 持仓更新日）
    prod_note = re.search(r"值使用([0-9.]+)。.{0,40}?更新于\s*([0-9-]+)", prod_html, re.S)
    local_note = re.search(r"值使用([0-9.]+)。.{0,80}?更新于\s*([0-9-]+)", local_html, re.S)
    print()
    print("说明文字  生产端：%s" % (prod_note.group(0).replace("\n", " ")[:60] if prod_note else "-"))
    print("          本地：%s" % (local_note.group(0).replace("\n", " ")[:60] if local_note else "-"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
