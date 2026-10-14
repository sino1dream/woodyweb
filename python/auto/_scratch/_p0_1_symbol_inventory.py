"""P0.1 符号清单：从 PHP 权威映射生成基金 + est 符号全集，并与演示库对照。

回答三个问题（P0.2 的直接输入）：

1. QDII 各页共有多少只基金、分别属于哪个数组（对应 PHP 的
   ``QdiiGetSymbolArray()`` 等）；
2. 每只基金的 est 符号是什么（含**杠杆替代**：``qdii_qqq_symbols()``
   用 QQQ 而不是 ^NDX 取数，见 ``stockdata.py:279-284``）；
3. 演示库里缺哪些 ``stock`` 行、缺哪些 ``fundpair`` 行。

口径全部来自代码里已有的映射（``core/stocksymbol.py`` 的数组 +
``calc/fundcalc.py`` 的 ``qdii_est_symbol()`` 族），它们与 PHP
``qdiiref.php:19-37`` 的 ``QdiiGetEstArray()`` 逐条对照过。

用法：``python -B auto/_scratch/_p0_1_symbol_inventory.py``（只读，不写库）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pymysql  # noqa: E402
import pymysql.cursors  # noqa: E402

from calc.fundcalc import (  # noqa: E402
    qdii_eu_est_symbol,
    qdii_hk_est_symbol,
    qdii_jp_est_symbol,
    qdii_est_symbol,
)
from core.stockdata import get_leverage_hedge_symbol, load_mysql_env, mysql_config_from_env  # noqa: E402
from core.stocksymbol import (  # noqa: E402
    get_china_internet_symbol_array,
    get_hk_mix_symbol_array,
    get_lof_mix_symbol_array,
    get_msci_us50_symbol_array,
    get_qdii_gold_symbol_array,
    get_qdii_oil_symbol_array,
    in_array_qdii,
    in_array_qdii_eu,
    in_array_qdii_hk,
    in_array_qdii_jp,
    in_array_qdii_mix,
    qdii_eu_get_symbol_array,
    qdii_get_symbol_array,
    qdii_hk_get_symbol_array,
    qdii_jp_get_symbol_array,
    qdii_mix_get_symbol_array,
)


def est_of(symbol):
    """与 ``_est_symbol()`` 同顺序：映射 -> 杠杆替代。"""
    est = (qdii_est_symbol(symbol) or qdii_hk_est_symbol(symbol)
           or qdii_jp_est_symbol(symbol) or qdii_eu_est_symbol(symbol))
    return get_leverage_hedge_symbol(symbol) or est


def category_of(symbol):
    """符号属于哪个 QDII 数组（对应 PHP 的分发顺序）。"""
    if in_array_qdii(symbol):
        return "qdii"
    if in_array_qdii_hk(symbol):
        return "qdiihk"
    if in_array_qdii_jp(symbol):
        return "qdiijp"
    if in_array_qdii_eu(symbol):
        return "qdiieu"
    if in_array_qdii_mix(symbol):
        return "qdiimix"
    if symbol in get_china_internet_symbol_array():
        return "chinainternet"
    if symbol in get_msci_us50_symbol_array():
        return "mscius50"
    if symbol in get_hk_mix_symbol_array():
        return "hkmix"
    if symbol in get_qdii_gold_symbol_array():
        return "qdiigold"
    if symbol in get_qdii_oil_symbol_array():
        return "qdiioil"
    if symbol in get_lof_mix_symbol_array():
        return "lofmix"
    return "?"


def main():
    config = mysql_config_from_env(load_mysql_env(os.environ))
    config["cursorclass"] = pymysql.cursors.DictCursor
    connection = pymysql.connect(**config)
    cursor = connection.cursor()

    cursor.execute("SELECT id, symbol, name FROM stock")
    rows = cursor.fetchall()
    by_symbol = {row["symbol"]: row for row in rows}
    by_id = {str(row["id"]): row for row in rows}

    # fundpair：id = 基金 id，stock_id = 配对标的 id（PHP 的 pair_id）
    cursor.execute("SELECT id, stock_id FROM fundpair")
    pair_of = {str(row["id"]): str(row["stock_id"]) for row in cursor.fetchall()}

    # -- 1. 基金清单 ----------------------------------------------------
    groups = [
        ("qdii", qdii_get_symbol_array()),
        ("qdiihk", qdii_hk_get_symbol_array()),
        ("qdiijp", qdii_jp_get_symbol_array()),
        ("qdiieu", qdii_eu_get_symbol_array()),
        ("qdiimix", qdii_mix_get_symbol_array()),
    ]

    print("=" * 78)
    print("一、基金清单（按数组）")
    print("=" * 78)
    total = 0
    for label, symbols in groups:
        print("\n[%s] %d 只" % (label, len(symbols)))
        missing = []
        for symbol in symbols:
            total += 1
            row = by_symbol.get(symbol)
            est = est_of(symbol)
            # 实际取数用的符号（杠杆替代后的）
            est_row = by_symbol.get(est) if est else None
            pair_id = pair_of.get(str(row["id"])) if row else None
            pair_symbol = by_id.get(pair_id, {}).get("symbol") if pair_id else None
            flags = []
            if row is None:
                flags.append("缺stock")
            if est and est_row is None:
                flags.append("缺est:%s" % est)
            if row is not None and pair_id is None:
                flags.append("缺fundpair")
            if pair_symbol and est and pair_symbol != est:
                flags.append("fundpair=%s≠est" % pair_symbol)
            mark = "  <-- " + ",".join(flags) if flags else ""
            print("  %-10s est=%-10s %s%s" % (symbol, est or "-", row["name"] if row else "", mark))
            if flags:
                missing.append(symbol)
        print("  -> 有问题 %d 只" % len(missing))
    print("\n合计 %d 只基金" % total)

    # -- 2. est 符号全集 -------------------------------------------------
    print()
    print("=" * 78)
    print("二、est 符号全集（中间层，必须都在 stock 表里）")
    print("=" * 78)
    est_symbols = {}
    for _, symbols in groups:
        for symbol in symbols:
            est = est_of(symbol)
            if est:
                est_symbols.setdefault(est, []).append(symbol)
    for est in sorted(est_symbols):
        funds = est_symbols[est]
        row = by_symbol.get(est)
        print("  %-10s %-28s 服务 %2d 只：%s%s" % (
            est, row["name"] if row else "**缺**", len(funds),
            ",".join(funds[:4]) + ("..." if len(funds) > 4 else ""),
            "" if row else "  <-- 缺stock"))

    # -- 3. 辅助符号 -----------------------------------------------------
    print()
    print("=" * 78)
    print("三、辅助符号（汇率 / 期货）")
    print("=" * 78)
    for symbol in ("USCNY", "HKCNY", "EUCNY", "JPCNY",
                   "hf_NQ", "hf_ES", "hf_CL", "hf_NK",
                   "fx_susdcny", "fx_shkdcny", "fx_sjpycny", "fx_seurcny"):
        row = by_symbol.get(symbol)
        print("  %-12s %s" % (symbol, row["name"] if row else "**缺**"))

    # -- 4. 汇总 ---------------------------------------------------------
    print()
    print("=" * 78)
    print("四、汇总（P0.2 的写入量）")
    print("=" * 78)
    all_funds = []
    for _, symbols in groups:
        for symbol in symbols:
            if symbol not in all_funds:
                all_funds.append(symbol)
    missing_funds = [s for s in all_funds if s not in by_symbol]
    missing_est = [e for e in est_symbols if e not in by_symbol]
    # fundpair 缺口按**全部有 est 的基金**算：P0.2 会先补 stock 行，
    # 那时这些基金就都有 id 了，配对行要一次性补齐。
    need_pair = [(s, est_of(s)) for s in all_funds if est_of(s)]
    print("基金总数           ：%d 只（%s）" % (
        len(all_funds), " + ".join("%s %d" % (label, len(syms)) for label, syms in groups)))
    print("缺 stock 行的基金   ：%d 只" % len(missing_funds))
    print("已在库的基金       ：%d 只 -> %s" % (
        len(all_funds) - len(missing_funds),
        ",".join(s for s in all_funds if s in by_symbol)))
    print("缺 stock 行的 est   ：%d 个 -> %s" % (len(missing_est), ",".join(missing_est)))
    print("需要 fundpair 行    ：%d 对（有 est 的基金各一对）" % len(need_pair))
    print("其中库里已有的      ：%d 对" % len([
        s for s in all_funds
        if s in by_symbol and str(by_symbol[s]["id"]) in pair_of]))

    connection.close()


if __name__ == "__main__":
    main()
