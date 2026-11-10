# -*- coding: utf-8 -*-
"""补齐「A股指数」分类（``/category/chinaindex``）的 ``stock`` 符号行。

## 为什么页面全是「库里无此符号」

``/category/chinaindex`` 的 27 只符号来自
``core.stocksymbol.china_index_get_symbol_array()``
（PHP ``php/stock/stocksymbol.php:88`` 的 ``ChinaIndexGetSymbolArray()``）：

* A股宽基 9 只（``china_index_get_ashr_array()``）
* 科创 50 四只（``china_index_get_star_array()``，本地 PHP 快照没有、按生产端补）
* 深证 100 两只 / 煤炭两只
* 尾部 10 只 LOF

演示库 ``stock`` 表里**只有 ``SZ160632``（id=296，酒LOF）一行**，其余 26 只
全缺，于是 ``reference_service.quote_state_of()`` 只能标成「库里无此符号」，
``classify_symbols()`` 也建不出引用对象，EST 表整段不出现。

## 名称为什么照抄生产端、不抓新浪

``seed_p0_symbols.py`` 那套「名称从新浪抓」适用于 QDII —— 新浪对这批 A 股
LOF/ETF 返回的是**简称**（``SZ160706 -> 嘉实300A``、``SZ161005 -> 富国天惠``），
而生产端 ``chinaindexcn.php`` 的表格与 ``<td title=...>`` 用的是 LOF 全称
（``沪深300LOF`` / ``富国天惠LOF`` / ``兴全沪深300LOF``）。页面「名称」列读的是
``stock.name``，要逐位对齐生产端就得用全称。

名称表取自生产端快照
``python/auto/_scratch/_prod_category_cache/chinaindexcn.php.html``
（2026-11-10 抓取，27 行齐全）。**``_scratch/`` 被 gitignore**，所以把这份
映射就地写进本脚本，并与 ``china_index_get_symbol_array()`` 逐一校验顺序 ——
序号对不上就报错，避免以后数组增删时名称与符号错位。

## 不写什么

只补 ``stock`` 行（``symbol`` + ``name``）。**不碰** ``fundpair`` / ``calibration`` /
``netvaluehistory`` / 行情快照：A 股指数的配对链路（基金 → ``SZ399987`` 这类
中证指数）尚未整体迁移，逐只手写校准值会把"没迁的功能"伪装成"已迁的数据"。
``SZ160632`` 的空壳数据由 ``patch_china_index_fund_data.py`` 单独处理。

## 安全约定

* 默认 dry-run，``--apply`` 才写库；
* ``--apply`` 前走 ``backup_db.py --lock-tables``（MyISAM 安全），失败即停；
* 已有行**一律不动**（``SZ160632`` 的「酒LOF」保持原样），幂等；
* ``--symbols`` 可点名补少数几只，缺省按整个数组补齐。

用法::

    python -B dbtools\\patch_china_index_symbols.py                 # 预演
    python -B dbtools\\patch_china_index_symbols.py --apply         # 备份 + 真写
    python -B dbtools\\patch_china_index_symbols.py --symbols SH501043 --apply
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DBTOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(DBTOOLS_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR, DBTOOLS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource  # noqa: E402
from core.stocksymbol import china_index_get_symbol_array  # noqa: E402

#: 生产端 ``chinaindexcn.php`` 的「名称」列，顺序与
#: :func:`china_index_get_symbol_array` 完全一致（27 行）。
PRODUCTION_NAMES = (
    "沪深300LOF",              # SH501043
    "沪深300ETF华泰柏瑞",       # SH510300
    "沪深300ETF易方达",         # SH510310
    "沪深300ETF华夏",           # SH510330
    "沪深300ETF嘉实",           # SZ159919
    "沪深300LOF",              # SZ160706
    "富国天惠LOF",             # SZ161005
    "兴全沪深300LOF",           # SZ163407
    "红土创新精选LOF",          # SZ168401
    "科创50ETF华夏",            # SH588000
    "科创50ETF工银",            # SH588050
    "科创50ETF易方达",          # SH588080
    "科创50ETF华泰柏瑞",        # SH588090
    "国投深证100LOF",           # SZ161227
    "深证100LOF",              # SZ161812
    "煤炭龙头LOF",             # SZ161032
    "煤炭LOF",                 # SZ168204
    "500增强LOF",              # SH502000
    "新能源汽车LOF",            # SZ160225
    "酒LOF",                   # SZ160632
    "高铁LOF",                 # SZ160639
    "白酒基金LOF",             # SZ161725
    "生物医药LOF",             # SZ161726
    "医疗基金LOF",             # SZ162412
    "申万深成LOF",             # SZ163109
    "申万证券LOF",             # SZ163113
    "保险主题LOF",             # SZ167301
)


def stock_rows():
    """``[(symbol, name), ...]``，单一事实源是分类页数组。

    数组顺序与生产端名称表必须逐位对应，任一处增删都会在这里炸出来 ——
    宁可停手也不要把「煤炭LOF」写到「白酒基金LOF」头上。
    """
    symbols = list(china_index_get_symbol_array())
    if len(symbols) != len(PRODUCTION_NAMES):
        raise RuntimeError(
            "分类页数组 %d 只，但生产端名称表 %d 行；请同步更新 PRODUCTION_NAMES"
            % (len(symbols), len(PRODUCTION_NAMES)))
    return list(zip(symbols, PRODUCTION_NAMES))


def resolve_rows(raw_symbols=None):
    """``--symbols`` 点名时取子集（保持数组顺序），否则全量。"""
    rows = stock_rows()
    if not raw_symbols:
        return rows
    wanted = [part.strip() for part in raw_symbols.split(",") if part.strip()]
    known = {symbol for symbol, _name in rows}
    unknown = [symbol for symbol in wanted if symbol not in known]
    if unknown:
        raise SystemExit("不在 A股指数分类页数组里：%s" % ",".join(unknown))
    return [(symbol, name) for symbol, name in rows if symbol in wanted]


def _arg(name):
    if name not in sys.argv:
        return None
    index = sys.argv.index(name)
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else ""


def main() -> int:
    apply = "--apply" in sys.argv
    skip_backup = "--skip-backup" in sys.argv
    rows = resolve_rows(_arg("--symbols"))

    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== ① stock 表（A股指数分类页 %d 只） ===" % len(rows))
    planned = []
    for symbol, name in rows:
        cursor.execute("SELECT id, name FROM stock WHERE symbol = %s", (symbol,))
        existing = cursor.fetchone()
        if existing is not None:
            print("  %-10s 已在库（id=%-4s %s），不动" % (symbol, existing["id"], existing["name"]))
            continue
        print("  %-10s 待插入  %s" % (symbol, name))
        planned.append((symbol, name))
    print("  小计：待插入 %d 行" % len(planned))

    if not apply:
        print("")
        print("这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    if not planned:
        print("")
        print("无需改动。")
        cursor.close()
        source.connection.close()
        return 0

    if not skip_backup:
        import backup_db
        code = backup_db.main([])
        if code != 0:
            print("备份失败（退出码 %s），停在这里，没有动库。" % code)
            cursor.close()
            source.connection.close()
            return 1
        print("")
    else:
        print("**--skip-backup：本次不备份**")
        print("")

    for symbol, name in planned:
        cursor.execute(
            "INSERT INTO stock (symbol, name) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE name = VALUES(name)",
            (symbol, name),
        )
    source.connection.commit()
    print("已插入 %d 行 stock。" % len(planned))

    print("")
    print("=== 写入后核对 ===")
    cursor.execute(
        "SELECT symbol, name FROM stock WHERE symbol IN (%s) ORDER BY id"
        % ", ".join(["%s"] * len(rows)),
        tuple(symbol for symbol, _name in rows),
    )
    found = {row["symbol"]: row["name"] for row in cursor.fetchall()}
    print("  数组 %d 只，库里现有 %d 只" % (len(rows), len(found)))
    for symbol, name in rows:
        if symbol not in found:
            print("  **仍缺**：%s" % symbol)
        elif found[symbol] != name:
            print("  %-10s 名称与生产端不同：库里 %s / 生产端 %s" % (symbol, found[symbol], name))
    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
