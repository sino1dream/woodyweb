# -*- coding: utf-8 -*-
"""给「跟踪代码 / 官方 EST 整组为空」的 LOF 补上 est 映射（``fundestmap`` 表）。

## 为什么需要这个补丁

「跟踪代码」列（``web/services/fund_list_service.py:447``）取的是
``profile.est_symbol or profile.pair_symbol``，而 ``est_symbol`` 来自
**硬编码表** —— ``calc/fundcalc.py`` 的 ``qdii_est_symbol_map()`` 等，
逐行照抄 PHP ``php/stock/qdiiref.php:19-77``。

代码里没配的基金，``est`` 为空，后果不止"少一格显示"：
``_QdiiReference`` 构造时 ``if ($strEstSymbol = QdiiGetEstSymbol($strSymbol))``
不成立就不 ``SetEstRef()``，于是 ``GetOfficialNetValue()`` 拿不到数 ——
**官方EST / EST日期 / 溢价 整组全空**，页面上看像"没数据"，其实是没映射。

2026-11-03 的实际盘点（``/fund/qdiilof`` 26 只，12 只有值、14 只空）：

===============  ========================================  ============================
类别             基金                                      为什么空
===============  ========================================  ============================
有对标、没建表    三只黄金                                    PHP 没给它们建 est 表
另有估值路径      全球油气能源、港美互联网                     生产端走**持仓加权**，不是单只 ETF
有对标、库和源都没有  全球芯片（SOXX/SMH）、印度（INDA）、海外科技  标的符号连 stock 行都没有
没有 ETF 对标     原油 4 只 + 商品 3 只                       跟踪原油期货 / 一篮子商品
===============  ========================================  ============================

**本脚本只补第一类**（3 只黄金，标的数据齐、能算出校准值，且数值与生产端
逐位对齐）。第二类**不要硬配** —— 详见 :data:`EXCLUDED` 的实测数字；
第三、四类要先把标的符号入库并回填历史。

## 配了之后不等于立刻有估值

估值 = ``est × cny ÷ cal_f``，还要三样：锚点日的 est 价、当天汇率、
以及一条 ``calibrationhistory`` 的 ``cal_f``。前两样本脚本只做**体检**并打印，
``cal_f`` 要跑 ``autocalibrationcli.py --funds`` 才写进去。

用法::

    python -B dbtools/patch_fund_est_map.py            # 预演（默认）
    python -B dbtools/patch_fund_est_map.py --apply    # 真写
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

#: 建表语句。与 ``dbtools/schema.sql`` 里的定义保持一致（那边是权威来源，
#: 这里复制一份是为了让补丁能自己跑起来，不依赖先执行整个 schema）。
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS `fundestmap` (
  `id` int(10) unsigned NOT NULL COMMENT '基金在 stock 表的 id',
  `est_symbol` varchar(16) NOT NULL COMMENT '跟踪标的符号，如 GLD / KWEB / ^NDX',
  PRIMARY KEY (`id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci
"""

#: 待补的「基金 → 跟踪标的」。只收**生产端确实是单只标的对标**、且本地
#: 数据齐、能算出 ``cal_f`` 的 3 只黄金。
#:
#: 三只黄金都跟踪金价 → ``GLD``（``stock`` 里已有 id=181）。数值已与生产端
#: 逐位对齐（2026-11-03）：``SZ160719`` 1.742/生产 1.743、``SZ161116``
#: 1.446/1.447、``SZ164701`` 1.514/1.514。
EST_OVERRIDES = [
    ("SZ160719", "GLD", "嘉实黄金LOF"),
    ("SZ161116", "GLD", "黄金主题LOF"),
    ("SZ164701", "GLD", "黄金LOF"),
]

#: **有意不配**的两只，别再往这里加（2026-11-03 实测结论）：
#:
#: * ``SZ163208``（全球油气能源LOF）：生产端「参考数据」列的是 ``USO``
#:   （油价ETF），但按 ``USO`` 算出的 EST 差 3.1%、按 ``XOP`` 差 0.6% ——
#:   两个都对不上生产端的 1.482，说明它的估值不是"单只 ETF × 比例"，
#:   而是 PHP 的 ``HoldingsReference``（持仓加权）。本地 ``holdings``
#:   一行都没有，硬配只会给出看起来正常、实际偏 0.6%–3% 的估值。
#: * ``SZ160644``（港美互联网LOF）：生产端「参考数据」**只有汇率**，
#:   没有任何标的符号，官方 EST 1.558 同样来自持仓加权路径。
#:   本地配 ``KWEB`` 会算出 1.609（差 3.3%），并把溢价从生产端的 -0.9%
#:   显示成 -4.1%，明显误导。
#:
#: 要覆盖这两只，缺的不是映射，是 ``holdings`` 数据 + ``HoldingsReference``。
EXCLUDED = [
    ("SZ163208", "USO", "生产端为持仓加权，单 ETF 对标差 0.6%(XOP)/3.1%(USO)"),
    ("SZ160644", "KWEB", "生产端无标的，官方EST 来自持仓加权；硬配差 3.3%"),
]

DEFAULT_CNY_SYMBOL = "USCNY"


def _ensure_table(cursor) -> bool:
    """建表；返回是否**新**建（已存在返回 False）。"""
    try:
        cursor.execute("SELECT 1 FROM fundestmap LIMIT 1")
        return False
    except Exception:
        pass
    print("  fundestmap 表不存在，待创建")
    cursor.execute(CREATE_SQL)
    return True


def _check_target(cursor, est_symbol: str) -> tuple:
    """体检标的数据：返回 ``(stock_id, 收盘价条数, 净值条数, 有无 stock 行问题说明)``。"""
    cursor.execute("SELECT id FROM stock WHERE symbol = %s", (est_symbol,))
    row = cursor.fetchone()
    if row is None:
        return (None, 0, 0, "stock 表里没有这个符号")
    est_id = row["id"]
    cursor.execute("SELECT COUNT(*) AS n FROM stockhistory WHERE stock_id = %s", (est_id,))
    close_n = cursor.fetchone()["n"]
    cursor.execute("SELECT COUNT(*) AS n FROM netvaluehistory WHERE stock_id = %s", (est_id,))
    nav_n = cursor.fetchone()["n"]
    note = ""
    if not close_n and not nav_n:
        note = "标的一条行情/净值都没有，配了也算不出"
    return (est_id, close_n, nav_n, note)


def _dry_calibration(cursor, fund_id: str, est_id: str) -> str:
    """按 PHP 口径试算 ``cal_f``：锚点 = 基金最新净值日。

    三量同日：基金净值、当天 est 价（净值优先、收盘价兜底）、当天汇率。
    任何一样缺就返回"算不出"并说明缺什么 —— 这正是"配了映射还要不要
    补数据"的判断依据。
    """
    cursor.execute(
        "SELECT `date`, close FROM netvaluehistory WHERE stock_id = %s ORDER BY `date` DESC LIMIT 1",
        (fund_id,),
    )
    nav = cursor.fetchone()
    if nav is None:
        return "基金没有净值，校准无从锚起"
    date = nav["date"]
    cursor.execute("SELECT close FROM netvaluehistory WHERE stock_id = %s AND `date` = %s", (est_id, date))
    est_val = cursor.fetchone()
    source = "净值"
    if est_val is None:
        cursor.execute("SELECT close FROM stockhistory WHERE stock_id = %s AND `date` = %s", (est_id, date))
        est_val = cursor.fetchone()
        source = "收盘价"
    if est_val is None:
        return "净值日 %s 那天标的没有行情" % date
    cursor.execute("SELECT id FROM stock WHERE symbol = %s", (DEFAULT_CNY_SYMBOL,))
    cny_id = cursor.fetchone()["id"]
    cursor.execute(
        "SELECT close FROM netvaluehistory WHERE stock_id = %s AND `date` <= %s ORDER BY `date` DESC LIMIT 1",
        (cny_id, date),
    )
    cny = cursor.fetchone()
    if cny is None:
        return "净值日 %s 之前没有 %s" % (date, DEFAULT_CNY_SYMBOL)
    value = float(est_val["close"]) * float(cny["close"]) / float(nav["close"])
    return "净值日 %s  est(%s)=%s  %s=%s  净值=%s  ->  cal_f=%.4f" % (
        date, source, est_val["close"], DEFAULT_CNY_SYMBOL, cny["close"],
        nav["close"], value,
    )


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== ① fundestmap 表 ===")
    created = _ensure_table(cursor)
    if created and not apply:
        print("  （预演不建表，真写时建）")

    print("")
    print("=== ② 待补的映射 ===")
    planned = []
    for fund_symbol, est_symbol, name in EST_OVERRIDES:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (fund_symbol,))
        fund_row = cursor.fetchone()
        if fund_row is None:
            print("  %-9s %-16s 跳过（基金不在 stock 表）" % (fund_symbol, name))
            continue
        fund_id = fund_row["id"]
        est_id, close_n, nav_n, note = _check_target(cursor, est_symbol)
        if est_id is None:
            print("  %-9s -> %-6s 跳过（%s）" % (fund_symbol, est_symbol, note))
            continue
        cursor.execute("SELECT est_symbol FROM fundestmap WHERE id = %s", (fund_id,))
        existing = cursor.fetchone()
        state = "待插入"
        if existing is not None:
            if existing["est_symbol"] == est_symbol:
                print("  %-9s -> %-6s 已有同样配置，跳过" % (fund_symbol, est_symbol))
                continue
            state = "待改正（原 %s）" % existing["est_symbol"]
        print("  %-9s -> %-6s %-16s %s  标的行情 %d 条 / 净值 %d 条"
              % (fund_symbol, est_symbol, name, state, close_n, nav_n))
        if note:
            print("       ⚠ %s" % note)
        planned.append((fund_id, est_symbol))

    print("")
    print("=== ③ 校准值试算（缺什么会直接说出来）===")
    for fund_symbol, est_symbol, _name in EST_OVERRIDES:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (fund_symbol,))
        fund_row = cursor.fetchone()
        if fund_row is None:
            continue
        est_id, _close_n, _nav_n, _note = _check_target(cursor, est_symbol)
        if est_id is None:
            continue
        print("  %-9s <- %-6s %s" % (fund_symbol, est_symbol,
                                     _dry_calibration(cursor, fund_row["id"], est_id)))

    total = len(planned)
    print("")
    print("=== ④ 有意不配的（缺的是数据与算法，不是映射）===")
    for fund_symbol, est_symbol, why in EXCLUDED:
        print("  %-9s 若配 %-5s %s" % (fund_symbol, est_symbol, why))

    print("")
    print("合计 %d 条映射待写" % total)

    if not apply:
        print("这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0
    if not total and not created:
        print("无需改动。")
        cursor.close()
        source.connection.close()
        return 0

    for fund_id, est_symbol in planned:
        cursor.execute(
            "INSERT INTO fundestmap (id, est_symbol) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE est_symbol = VALUES(est_symbol)",
            (fund_id, est_symbol),
        )
    source.connection.commit()

    print("")
    print("=== 写入后核对 ===")
    cursor.execute(
        "SELECT s.symbol AS fund, m.est_symbol AS est FROM fundestmap m "
        "JOIN stock s ON s.id = m.id ORDER BY s.symbol"
    )
    for row in cursor.fetchall():
        print("  %-10s -> %s" % (row["fund"], row["est"]))

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
