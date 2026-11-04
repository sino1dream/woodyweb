# -*- coding: utf-8 -*-
"""补齐「A股商品」分类（``/category/chinafuture``）的基金与国内商品期货数据。

## 为什么页面全是「库里无此符号」

``/category/chinafuture`` 的符号表来自 ``ChinaFutureGetSymbolArray()``
（``php/stock/stocksymbol.php:119``）::

    SH518800 SH518880 SZ159934 SZ159937 SZ159985 SZ161226

这 6 只在本地 ``stock`` 表里**一行都没有**，于是
``reference_service.quote_state_of()`` 只能标成「库里无此符号」，
EST 表也整段不出现（``classify_symbols`` 建不出引用对象）。

它们跟踪的是**国内商品期货**，与 QDII 那套外盘期货（``hf_`` 前缀）是两族：
新浪给国内期货的符号前缀是 ``nf_``（``SINA_CN_FUTURE_PREFIX``），
``nf_AU0`` 黄金连续 / ``nf_AG0`` 白银连续 / ``nf_M0`` 豆粕连续。

## 链路：基金 → 国内期货（一级，与黄金 QDII 的两级不同）

::

    SH518800 ──fundpair──> nf_AU0     （黄金ETF  → 沪金连续）
    SZ159985 ──fundpair──> nf_M0      （豆粕ETF  → 连豆粕连续）
    SZ161226 ──fundpair──> nf_AG0     （白银LOF  → 沪银连续）

``FundPairReference``（``php/stock/fundpairref.php``）的汇率分支里
``if ($this->pair_ref->IsSinaFutureCN()) { }`` 是**空语句** ——
国内期货与 A 股基金同为人民币计价，``cny_ref`` 保持 false，
``GetDefaultCny()`` 返回 1.0。所以这里的 ``cal`` 就是纯粹的
「期货价 ÷ 基金净值」，不像 QDII 还要夹一道汇率。

## 校准值从哪来：生产端「校准记录」逐位照抄

生产端每只基金的详情页（``sh518800cn.php`` 等）直接列出::

    日期        校准       时间   次数  净值
    2026-11-03  101.3203   22:47  1     8.3693

「基金指数对照表」那行同时给出 ``跟踪代码 = AU0``、``仓位 = 1``。
六只全部抓到（2026-11-04），见 ``CALIBRATIONS``。

**注意这些 cal 里混了时段**：``DailyCalibration()`` 写的是
「写入时刻的期货价 ÷ 当天净值」。22:47 写的那条用的是**沪金夜盘价**
（848.02），而净值还是当天日盘的 8.3693 —— 所以 518800 的 cal
比「日盘收盘 ÷ 净值」高约 0.7%。作者那边就这样，照抄才能与生产端的
官方 EST 逐位对上（8.374）；换成"日盘收盘"会得到 8.436 = 现价，
溢价恒为 0，反而看不出套利空间。

## 只补 ``fundpair``，不碰 ``futurespair``

``futurespair`` 是外盘期货（``hf_``）的配对表，服务
``futurescalib.future_symbol_of()`` 与实时 EST 的期货那一跳。
国内期货没有"期货/指数"两层，``fundpair`` 一条就够；写进
``futurespair`` 会让 ``futurescli`` 去给 ``nf_AU0`` 算"期货/指数"校准值
（量级 1.0），纯属污染。

用法::

    python -B dbtools/patch_china_future.py            # 预演（默认）
    python -B dbtools/patch_china_future.py --apply    # 真写
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

#: 要补进 ``stock`` 的符号。名称照生产端详情页标题
#: （``黄金ETF国泰`` / ``豆粕ETF华夏`` / ``国投白银LOF``），与本地既有风格一致
#: （``SH513100 国泰纳斯达克100ETF``）。期货用``交易所+品种+连续``的读法，
#: 页面上的「沪-黄金连续」是行情原文拼的（``_on_sina_future_cn`` 用 ar[15]-ar[0]），
#: 与这里无关。
STOCK_ROWS = [
    ("nf_AU0", "黄金连续"),
    ("nf_AG0", "白银连续"),
    ("nf_M0", "豆粕连续"),
    ("SH518800", "黄金ETF国泰"),
    ("SH518880", "黄金ETF华安"),
    ("SZ159934", "黄金ETF易方达"),
    ("SZ159937", "黄金ETF博时"),
    ("SZ159985", "豆粕ETF华夏"),
    ("SZ161226", "国投白银LOF"),
]

#: 「基金 → 跟踪的国内期货」，写 ``fundpair``（``id`` = 基金，``stock_id`` = 期货）。
#: 顺序与 ``ChinaFutureGetSymbolArray()`` 一致，页面行序即由此而来。
PAIRS = [
    ("SH518800", "nf_AU0"),
    ("SH518880", "nf_AU0"),
    ("SZ159934", "nf_AU0"),
    ("SZ159937", "nf_AU0"),
    ("SZ159985", "nf_M0"),
    ("SZ161226", "nf_AG0"),
]

#: 校准值：``(基金符号, 日期, 时刻, 值, num)``。
#:
#: 值 = 期货价 ÷ 基金净值，直接照抄生产端「校准记录」那一行。
#: ``time`` 列 NOT NULL 且无默认值，必须显式给 —— **用生产端那个时刻**，
#: 它是"这条 cal 是怎么算出来的"的唯一线索（见模块 docstring 的混时段说明）。
CALIBRATIONS = [
    ("SH518800", "2026-11-03", "22:47:00", 101.3203, 1),
    ("SH518880", "2026-11-03", "20:37:00", 105.4478, 1),
    ("SZ159934", "2026-11-03", "21:22:00", 101.4101, 1),
    ("SZ159937", "2026-11-03", "21:11:00", 106.3608, 1),
    ("SZ159985", "2026-11-03", "21:10:00", 1518.7566, 1),
    ("SZ161226", "2026-11-03", "21:05:00", 8868.3006, 1),
]

#: 仓位（``fundposition.close``），**商品期货基金恒为 1.0**。
#:
#: ## 为什么非写不可（只有 ``SZ161226`` 会看得出来）
#:
#: PHP ``stocksymbol.php:1003`` 的默认仓位是 ``IsLofA() ? 0.95 : 1.0``，
#: 而 ``fundpairref.php:91`` 的 ``EstFromPair`` 最后一步是::
#:
#:     FundAdjustPosition($this->GetPosition(), $fVal, $fLastCalibrationVal)
#:
#: 也就是 ``last + (val - last) × 仓位``。商品基金持有的是**期货合约本身**
#: （不是股票篮子），仓位就是 100%，生产端 ``fundposition`` 里也是 1.0。
#:
#: 本地这六只原先一行仓位都没有，于是 ``SZ161226``（LOF）落到 0.95：
#:
#: * 官方 EST 被往"上一交易日净值"方向拉：``1.3546 + (1.3422-1.3546)×0.95 = 1.3429``
#:   → 页面 1.343，生产端 1.342；
#: * 参考 EST 同样被拉：1.33 vs 1.328，溢价 6.43% vs 6.53%。
#:
#: 五只 ETF 默认就是 1.0，写进去只是把"数据"和"默认值"对齐，数值不变 ——
#: 但下次谁再改默认仓位逻辑时，这六只不会跟着漂。
POSITIONS = ["SH518800", "SH518880", "SZ159934", "SZ159937", "SZ159985", "SZ161226"]

#: 校验 ``cal × 最新净值`` 是否落在期货价的合理区间用。
#: 这里**不能**用黄金 QDII 那种固定的 ``[5, 20]``：A 股商品的 cal 跨度极大
#: （黄金 ≈ 101、豆粕 ≈ 1519、白银 ≈ 8868，取决于期货报价与基金净值的量级）。
#: 所以改用"反推期货价"的方式体检 —— ``cal × 净值`` 应该等于库里的期货价
#: （若日线还没回填就只打印出来供人工核对）。
PRICE_RATIO_LOW = 0.5
PRICE_RATIO_HIGH = 2.0


def _ensure_stock(cursor) -> list:
    planned = []
    for symbol, name in STOCK_ROWS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row is not None:
            print("  %-10s 已在 stock 表（id=%s），跳过" % (symbol, row["id"]))
            continue
        print("  %-10s 待插入（%s）" % (symbol, name))
        planned.append((symbol, name))
    return planned


def _ensure_pairs(cursor, pending=frozenset()) -> list:
    planned = []
    for fund_symbol, future_symbol in PAIRS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (fund_symbol,))
        fund_row = cursor.fetchone()
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (future_symbol,))
        future_row = cursor.fetchone()
        if fund_row is None or future_row is None:
            missing = [s for s, row in ((fund_symbol, fund_row), (future_symbol, future_row))
                       if row is None]
            if set(missing).issubset(set(pending)):
                print("  %-10s -> %-8s 待创建 %s 后自动配对"
                      % (fund_symbol, future_symbol, "/".join(missing)))
            else:
                print("  %-10s -> %-8s 跳过（stock 表里缺符号）" % (fund_symbol, future_symbol))
            continue
        cursor.execute("SELECT stock_id FROM fundpair WHERE id = %s LIMIT 1", (fund_row["id"],))
        existing = cursor.fetchone()
        if existing is not None:
            if int(existing["stock_id"]) == int(future_row["id"]):
                print("  %-10s -> %-8s 已存在，跳过" % (fund_symbol, future_symbol))
            else:
                print("  %-10s -> %-8s 已存在但配的是 id=%s，将改正"
                      % (fund_symbol, future_symbol, existing["stock_id"]))
                planned.append((fund_row["id"], future_row["id"]))
            continue
        print("  %-10s -> %-8s 待插入" % (fund_symbol, future_symbol))
        planned.append((fund_row["id"], future_row["id"]))
    return planned


def _latest_net_value(cursor, stock_id):
    cursor.execute(
        "SELECT close FROM netvaluehistory WHERE stock_id = %s ORDER BY `date` DESC LIMIT 1",
        (stock_id,),
    )
    row = cursor.fetchone()
    return float(row["close"]) if row else None


def _latest_history_close(cursor, stock_id):
    cursor.execute(
        "SELECT close FROM stockhistory WHERE stock_id = %s ORDER BY `date` DESC LIMIT 1",
        (stock_id,),
    )
    row = cursor.fetchone()
    return float(row["close"]) if row else None


def _ensure_calibration(cursor) -> list:
    """补 ``calibrationhistory`` 行，并用 ``cal × 净值`` 反推期货价做体检。"""
    planned = []
    for symbol, date, clock, value, num in CALIBRATIONS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row is None:
            print("  %-10s 跳过（stock 表里缺符号）" % symbol)
            continue
        stock_id = row["id"]

        # 反推期货价：cal × 最新净值应当 ≈ 期货的收盘/实时价。
        net_value = _latest_net_value(cursor, stock_id)
        implied = None if net_value is None else value * net_value
        pair_symbol = dict(PAIRS).get(symbol)
        future_price = None
        if pair_symbol:
            cursor.execute("SELECT id FROM stock WHERE symbol = %s", (pair_symbol,))
            future_row = cursor.fetchone()
            if future_row is not None:
                future_price = _latest_history_close(cursor, future_row["id"])
        note = ""
        if implied is not None and future_price:
            ratio = implied / future_price
            note = "（反推期货 %.2f vs 库里 %.2f，比 %.3f）" % (implied, future_price, ratio)
            if not (PRICE_RATIO_LOW <= ratio <= PRICE_RATIO_HIGH):
                print("  %-10s %s = %s 反推期货价 %.2f 与库里 %.2f 差 %.1f 倍，拒绝写入"
                      % (symbol, date, value, implied, future_price, ratio))
                continue
        elif implied is not None:
            note = "（反推期货 %.2f，库里还没有期货日线）" % implied

        cursor.execute(
            "SELECT id, close FROM calibrationhistory WHERE stock_id = %s AND `date` = %s",
            (stock_id, date),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if abs(float(existing["close"]) - value) < 1e-6:
                print("  %-10s %s = %s 已存在，跳过%s" % (symbol, date, value, note))
            else:
                print("  %-10s %s 已存在但值 = %s，将改正为 %s%s"
                      % (symbol, date, existing["close"], value, note))
                planned.append((stock_id, date, clock, value, num))
            continue
        print("  %-10s %s 待插入 = %s%s" % (symbol, date, value, note))
        planned.append((stock_id, date, clock, value, num))
    return planned


def _ensure_position(cursor) -> list:
    """补 ``fundposition`` = 1.0（见 :data:`POSITIONS` 的说明）。"""
    planned = []
    for symbol in POSITIONS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row is None:
            print("  %-10s 跳过（stock 表里缺符号）" % symbol)
            continue
        stock_id = row["id"]
        cursor.execute("SELECT close FROM fundposition WHERE id = %s", (stock_id,))
        existing = cursor.fetchone()
        if existing is not None and abs(float(existing["close"]) - 1.0) < 1e-9:
            print("  %-10s 仓位已是 1.0，跳过" % symbol)
            continue
        note = "" if existing is None else "（原值 %s）" % existing["close"]
        print("  %-10s 待写仓位 1.0%s" % (symbol, note))
        planned.append((stock_id, 1.0))
    return planned


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== ① stock 表 ===")
    new_stock = _ensure_stock(cursor)

    # 配对要查 stock.id，所以补 stock 行必须**先落库**才能配对；
    # 预演时把待建的符号记为 pending，让输出能说清"最终会被补上"。
    if apply and new_stock:
        for symbol, name in new_stock:
            cursor.execute("INSERT INTO stock (symbol, name) VALUES (%s, %s)", (symbol, name))
        source.connection.commit()
        print("  已插入 %d 行 stock。" % len(new_stock))
        new_stock = []
    pending = {symbol for symbol, _name in new_stock}

    print("")
    print("=== ② fundpair（基金 → 国内商品期货）===")
    new_fundpair = _ensure_pairs(cursor, pending)

    print("")
    print("=== ③ 基金校准值（期货 ÷ 净值）===")
    new_calibration = _ensure_calibration(cursor)

    print("")
    print("=== ④ 仓位（商品基金 = 100%）===")
    new_position = _ensure_position(cursor)

    total = len(new_fundpair) + len(new_calibration) + len(new_position)
    print("")
    print("合计 %d 项改动（fundpair %d / 校准 %d / 仓位 %d）"
          % (total, len(new_fundpair), len(new_calibration), len(new_position)))

    if not apply:
        print("这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0
    if not total:
        print("无需改动。")
        cursor.close()
        source.connection.close()
        return 0

    for fund_id, future_id in new_fundpair:
        cursor.execute(
            "INSERT INTO fundpair (id, stock_id) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE stock_id = VALUES(stock_id)",
            (fund_id, future_id),
        )
    for stock_id, date, clock, value, num in new_calibration:
        cursor.execute(
            "INSERT INTO calibrationhistory (stock_id, `date`, close, `time`, num) "
            "VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE close = VALUES(close)",
            (stock_id, date, value, clock, num),
        )
    for stock_id, position in new_position:
        cursor.execute(
            "INSERT INTO fundposition (id, close) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE close = VALUES(close)",
            (stock_id, position),
        )
    source.connection.commit()

    print("")
    print("=== 写入后核对 ===")
    cursor.execute(
        "SELECT a.symbol AS fund_symbol, b.symbol AS pair_symbol, c.close, c.`date` "
        "FROM fundpair fp JOIN stock a ON a.id = fp.id JOIN stock b ON b.id = fp.stock_id "
        "LEFT JOIN calibrationhistory c ON c.stock_id = fp.id "
        "WHERE a.symbol IN (%s) ORDER BY a.symbol"
        % ", ".join(["%s"] * len(PAIRS)),
        tuple(symbol for symbol, _f in PAIRS),
    )
    for row in cursor.fetchall():
        print("  %-10s -> %-8s cal=%s @ %s"
              % (row["fund_symbol"], row["pair_symbol"], row["close"], row["date"]))

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
