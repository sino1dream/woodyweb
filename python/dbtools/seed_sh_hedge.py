"""CK6-3：给演示库补 ``SH`` 这条对冲线（外加 ``fx_susdcnh`` 符号）。

## 为什么需要

生产端 ``sz161125cn.php`` 的「基金指数对照表」有 **SH / SPY 两行**、
「净值价格比较」有 **两张表**，参考数据表里也带 ``USDCNH``；
本地演示库一样都没有 —— 不是渲染逻辑缺，是**库里根本没有 SH**：

* ``stock`` 表没有 ``SH`` 行；
* ``fundpair`` 表没有 ``(SH, ^GSPC)`` 行 —— 而
  ``_LEVERAGE_SQL``（"谁跟踪估值标的"）正是靠它列出 ``$arLev``，
  少了这一行，对照表/净值价格比较/各杠杆均线三段全都没东西可摆；
* 于是 ``reference_symbols_for_fund()`` 也算不出 SH（它从杠杆清单来）。

``SH`` 是 ProShares Short S&P500（**反向** ETF），在生产端的位置是 -1，
所以对照表里它的仓位是 ``-1``、对冲值是负的 —— 这是它跟 SPY 唯一的区别，
本脚本只写 ``fundpair`` 指向关系，仓位由渲染层按符号自己算。

## 写什么

1. ``stock`` 行：``SH`` 与 ``fx_susdcnh``（名称从新浪抓，抓不到用兜底名）；
2. ``fundpair`` 行：``(SH, ^GSPC)`` —— **已有的行一律不动**；
3. ``stockhistory``：SH 的日线（新浪美股接口，与 ``seed_index_history.py``
   同一个源，实测 ``SH 2026-10-14 = 32.17`` 与生产端参考数据表逐字一致）；
4. ``stockema50`` / ``stockema200``：顺带用标准递推补齐，
   否则 SH 的均线表出不来「牛/熊」标记。

**净值 / 校准 / 快照不在本脚本里写**，留给既有工具按各自的闸门跑::

    python -B auto/cli/quotecli.py SH fx_susdcnh              # 行情快照
    python -B auto/cli/autocalibrationcli.py --sync-nav --apply --symbol SH
    python -B auto/cli/autocalibrationcli.py --apply --symbol SH

拆开是为了让每一步都能单独重跑 —— 净值日期推进、校准闸门这些是
``autocalibration`` 自己的事，塞进 seed 脚本会让它没法单独诊断。

## 安全约定

* 默认 dry-run，``--apply`` 才写库；
* ``--apply`` 前先 ``mysqldump`` 全库到 ``dbtools/_snapshots/``
  （与 ``seed_p0_symbols.py`` 同一套约定）；找不到 mysqldump 就拒绝执行，
  除非显式 ``--skip-backup``。

用法::

    python -B dbtools\\seed_sh_hedge.py                       # 预演
    python -B dbtools\\seed_sh_hedge.py --apply               # 备份 + 真写
    python -B dbtools\\seed_sh_hedge.py --apply --days 800
    python -B dbtools\\seed_sh_hedge.py --apply --symbols SH,SPY,QQQ
"""

from __future__ import annotations

import io
import os
import sys

DBTOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(DBTOOLS_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR, DBTOOLS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: 下面要 import 的两个 ``seed_*`` 脚本在模块顶层各自包一层
#: ``io.TextIOWrapper(sys.stdout.buffer)``。每包一层就把上一层顶成"没人引用"，
#: 随即被 GC —— ``__del__`` 会关掉**共用的那个底层 buffer**，于是后面所有
#: print 全是 ``ValueError: I/O operation on closed file``
#: （``_fund_page_state.py`` 踩过同一个坑）。
#:
#: 所以在 import 期间把 ``sys.stdout`` 换成一个**没有 ``buffer`` 属性**的
#: 对象：那两个脚本的 ``if hasattr(sys.stdout, "buffer")`` 判定为假，就不会
#: 再包了。import 完再换回真正的 utf-8 包装。
_REAL_STDOUT = sys.stdout
sys.stdout = io.StringIO()

from core.stockdata import MySqlFundDataSource  # noqa: E402
from core.stocksymbol import StockSymbol  # noqa: E402
from ref.stockref import StockReference, sina_quote_file_name  # noqa: E402
from source.sinaquotes import SinaQuoteClient  # noqa: E402
from sql.quotestore import RealtimeQuoteStore  # noqa: E402
from updater import indexhistory  # noqa: E402
import seed_index_history as seed_history  # noqa: E402
import seed_p0_symbols as seed_symbols  # noqa: E402

sys.stdout = (io.TextIOWrapper(_REAL_STDOUT.buffer, encoding="utf-8", errors="replace")
              if hasattr(_REAL_STDOUT, "buffer") else _REAL_STDOUT)

#: 默认补的中介层符号。``SH`` 是生产端标普组唯一的反向标的。
DEFAULT_SYMBOLS = ("SH",)

#: 只要 ``stock`` 行 + 快照、不要历史的符号。``fx_susdcnh`` 是外汇
#: （``_qdii.php:13`` 写死的页面级标的），日线历史没有意义，行情走
#: ``realtimequote`` 快照。``fx_susdcny`` 本来就有 stock 行，列在这里只为
#: 补它的快照 + 纠正名字（它现在叫"美元离岸人民币即期"，其实是**在岸**）。
EXTRA_SYMBOLS = ("fx_susdcnh", "fx_susdcny")

#: 新浪抓不到名称时的兜底名（``stock.name`` 是 NOT NULL，不能留空）。
#: 取自生产端 ``sz161125cn.php`` 参考数据表那一格的原文。
FALLBACK_NAMES = {
    "SH": "标普500指数ETF-ProShares",
    "fx_susdcnh": "离岸人民币（香港）",
}

#: 与 ``seed_index_history.py`` 一致：均线要 620 天、EMA200 要 200 天。
DEFAULT_DAYS = 800

#: 仓位（``fundposition``，``id`` 就是 ``stock_id``）。SH 是**反向** ETF：
#: 生产端 ``sz161125cn.php`` 的对照表里它的仓位是 ``-1``、对冲值是负的，
#: SPY 是 ``1``。缺这一行时 ``MysqlReference._read_position()`` 退回 1，
#: SH 就变成"正向"，对冲值也跟着少一个负号。
POSITIONS = {"SH": -1.0}

#: EMA 周期，与 ``StockHistory._get_EMA(50/200)`` 一致。
EMA_DAYS = (50, 200)


def _arg(name: str) -> str | None:
    """取 ``--name value`` 形式的命令行参数。"""
    if name not in sys.argv:
        return None
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        return None
    return sys.argv[index + 1]


def _arg_int(name: str, fallback: int) -> int:
    raw = _arg(name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def fetch_names(symbols) -> dict:
    """从新浪抓名称，返回 ``{符号: 名称}``；抓不到的缺席。

    与 ``seed_p0_symbols.fetch_names()`` 只有一处不同：这里调
    ``ref.load_data()``（PHP ``MyStockReference::LoadData()``）而不是
    ``load_sina_data()`` —— 后者不认外汇的字段布局，``fx_s*`` 会永远
    "取不到名称"。
    """
    client = SinaQuoteClient()
    mapping = {}
    sina_symbols = []
    for symbol in symbols:
        sina_symbol = StockSymbol(symbol).get_sina_symbol()
        if not sina_symbol:
            print("  跳过（没有新浪符号）：%s" % symbol)
            continue
        sina_symbols.append(sina_symbol)
        mapping[sina_symbol] = symbol

    text = client.fetch(sina_symbols)
    if text is None:
        print("  **抓取失败**：%s（%s）" % (
            ",".join(mapping.values()), getattr(client, "last_error", "unknown")))
        return {}

    client.cache_quote_files({sina_quote_file_name(s): s for s in sina_symbols})

    names = {}
    for sina_symbol in sina_symbols:
        symbol = mapping[sina_symbol]
        ref = StockReference(symbol, quote_loader=client.load)
        ref.load_data()
        if ref.str_name:
            names[symbol] = ref.str_name
        else:
            print("  **取不到名称**：%s（%s）" % (symbol, sina_symbol))
    return names


def ensure_stock(cursor, symbol: str, name: str, from_sina: bool = False) -> int | None:
    """确保 ``stock`` 表有这一行，返回它的 id。

    ``from_sina`` 为真且名字不一样时**顺手改成新浪给的名字** ——
    演示库里 ``fx_susdcny`` 的名字是手填的"美元离岸人民币即期"，但新浪与
    生产端都叫"在岸人民币"（"离岸"那个是 ``fx_susdcnh``）。名称手填就会出现
    "两个名字指同一个东西"的口径重影，``seed_p0_symbols.py`` 的文档里明确
    反对，所以这里一律以新浪为准。**只在真抓到名字时才改**，兜底名不覆盖。
    """
    cursor.execute("SELECT id, name FROM stock WHERE symbol = %s", (symbol,))
    row = cursor.fetchone()
    if row:
        stock_id = row["id"] if isinstance(row, dict) else row[0]
        old_name = row["name"] if isinstance(row, dict) else row[1]
        if from_sina and old_name != name:
            cursor.execute("UPDATE stock SET name = %s WHERE id = %s", (name, stock_id))
            print("     （名称 %s -> %s）" % (old_name, name))
        return stock_id
    cursor.execute("INSERT INTO stock (symbol, name) VALUES (%s, %s)", (symbol, name))
    cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
    row = cursor.fetchone()
    return row["id"] if isinstance(row, dict) else row[0]


def ensure_fundpair(cursor, symbol: str, pair_symbol: str) -> bool:
    """确保 ``fundpair`` 有 ``(symbol -> pair_symbol)``；返回是否新写。"""
    cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
    row = cursor.fetchone()
    if not row:
        return False
    self_id = row["id"] if isinstance(row, dict) else row[0]
    cursor.execute("SELECT id FROM stock WHERE symbol = %s", (pair_symbol,))
    row = cursor.fetchone()
    if not row:
        return False
    pair_id = row["id"] if isinstance(row, dict) else row[0]

    cursor.execute("SELECT id FROM fundpair WHERE id = %s", (self_id,))
    if cursor.fetchone():
        return False
    cursor.execute(
        "INSERT INTO fundpair (id, stock_id) VALUES (%s, %s)", (self_id, pair_id))
    return True


def main() -> int:
    apply = "--apply" in sys.argv
    skip_backup = "--skip-backup" in sys.argv
    days = _arg_int("--days", DEFAULT_DAYS)
    raw_symbols = _arg("--symbols")
    symbols = tuple(
        part.strip() for part in raw_symbols.split(",") if part.strip()
    ) if raw_symbols else DEFAULT_SYMBOLS

    source = MySqlFundDataSource.from_env()
    connection = source.connection
    cursor = connection.cursor()

    # -- 0. 备份（只在真写时做，与 seed_p0_symbols.py 同一套约定）--------
    if apply and not skip_backup:
        from core.stockdata import load_mysql_env, mysql_config_from_env
        config = mysql_config_from_env(load_mysql_env(os.environ))
        path = seed_symbols.backup_database(config)
        print("备份完成：%s" % path)
        print("")
    elif apply:
        print("**--skip-backup：本此不备份，出错只能手工恢复**")
        print("")

    print("要补的中介层符号：%s" % ", ".join(symbols))
    print("只补 stock 行的符号：%s" % ", ".join(EXTRA_SYMBOLS))
    print("每个最多取最近 %d 个交易日" % days)
    print("")

    # -- 1. 名称 --------------------------------------------------------
    print("=" * 74)
    print("从新浪抓名称")
    print("=" * 74)
    fetched = fetch_names(list(symbols) + list(EXTRA_SYMBOLS))
    names = {}
    from_sina = {}
    for symbol in list(symbols) + list(EXTRA_SYMBOLS):
        if symbol in fetched:
            names[symbol] = fetched[symbol]
            from_sina[symbol] = True
            print("  %-12s %s" % (symbol, names[symbol]))
        else:
            names[symbol] = FALLBACK_NAMES.get(symbol, symbol)
            from_sina[symbol] = False
            print("  %-12s **抓不到名称，用兜底名** %s" % (symbol, names[symbol]))
    print("")

    # -- 2. stock 行 ----------------------------------------------------
    print("=" * 74)
    print("stock 行")
    print("=" * 74)
    if not apply:
        for symbol in list(symbols) + list(EXTRA_SYMBOLS):
            cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
            exists = cursor.fetchone() is not None
            print("  %-12s %s" % (symbol, "已有" if exists else "会新增"))
    else:
        for symbol in list(symbols) + list(EXTRA_SYMBOLS):
            stock_id = ensure_stock(cursor, symbol, names[symbol], from_sina[symbol])
            connection.commit()
            print("  %-12s id=%s" % (symbol, stock_id))
    print("")

    # -- 3. fundpair ----------------------------------------------------
    print("=" * 74)
    print("fundpair 行（谁跟踪估值标的）")
    print("=" * 74)
    for symbol in symbols:
        # 生产端标普组：SH / SPY 都挂在 ^GSPC 上（``_LEVERAGE_SQL`` 方向）
        pair_symbol = "^GSPC" if symbol == "SH" else None
        if pair_symbol is None:
            continue
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if not row and not apply:
            print("  %-12s -> %s（预演：stock 行还没建）" % (symbol, pair_symbol))
            continue
        if apply:
            written = ensure_fundpair(cursor, symbol, pair_symbol)
            connection.commit()
            print("  %-12s -> %s %s" % (symbol, pair_symbol,
                                        "新写" if written else "已有，跳过"))
        else:
            print("  %-12s -> %s（会补）" % (symbol, pair_symbol))
    print("")

    # -- 3b. 仓位 -------------------------------------------------------
    print("=" * 74)
    print("fundposition（仓位）")
    print("=" * 74)
    for symbol, position in POSITIONS.items():
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("  %-12s **stock 表里没有**，跳过" % symbol)
            continue
        cursor.execute("SELECT close FROM fundposition WHERE id = %s", (stock_id,))
        row = cursor.fetchone()
        if row is not None:
            print("  %-12s 已有 %s" % (symbol, row["close"] if isinstance(row, dict) else row[0]))
            continue
        print("  %-12s 会写 %s" % (symbol, position))
        if apply:
            cursor.execute(
                "INSERT INTO fundposition (id, close) VALUES (%s, %s)", (stock_id, position))
            connection.commit()
            print("     已写")
    print("")

    # -- 4. 外汇快照 ----------------------------------------------------
    # 行情 daemon 的符号集合是**从 stockhistory 反推**的，外汇本来就没有
    # 日线历史，永远进不了那个集合（跟当年 ^NDX 的鸡生蛋一模一样），
    # 所以 ``quoteupdatercli --once`` 不会给它们写快照 —— 这里直接写。
    print("=" * 74)
    print("realtimequote 快照（外汇）")
    print("=" * 74)
    client = SinaQuoteClient()
    store = RealtimeQuoteStore(connection)
    for symbol in EXTRA_SYMBOLS:
        sina_symbol = StockSymbol(symbol).get_sina_symbol()
        text = client.fetch([sina_symbol])
        if text is None:
            print("  %-12s **抓取失败**（%s）" % (symbol, getattr(client, "last_error", "?")))
            continue
        client.cache_quote_files({sina_quote_file_name(sina_symbol): sina_symbol})
        ref = StockReference(symbol, quote_loader=client.load)
        ref.load_sina_forex_data()
        if not ref.has_quote_data():
            print("  %-12s **解析不出价**，跳过" % symbol)
            continue
        print("  %-12s 价=%-10s 昨收=%-10s %s %s 名称=%s" % (
            symbol, ref.get_price(), ref.get_prev_price(),
            ref.str_date, ref.str_time, ref.str_name))
        if apply:
            stock_id = source.get_stock_id(symbol)
            written = store.save(stock_id, symbol, sina_symbol, ref.get_price(),
                                 prev_price=ref.get_prev_price(), name=ref.str_name,
                                 quote_date=ref.str_date, quote_time=ref.str_time,
                                 source="sina")
            connection.commit()
            print("     %s快照" % ("已写" if written else "**没写进去**（stock_id=%s）" % stock_id))
    print("")

    # -- 5. 历史 + EMA --------------------------------------------------
    print("=" * 74)
    print("stockhistory 与 EMA")
    print("=" * 74)
    for symbol in symbols:
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("  %s：stock 表里没有，跳过" % symbol)
            continue

        rows = indexhistory.fetch_daily_history(symbol, limit=days)
        if not rows:
            print("  %s：**抓不到历史**，跳过" % symbol)
            continue
        print("  %s：抓到 %d 条 %s ~ %s" % (
            symbol, len(rows), rows[0]["date"], rows[-1]["date"]))

        if not apply:
            print("     （预演）会写入 %d 行历史 + EMA%s" % (
                len(rows), "/".join(map(str, EMA_DAYS))))
            continue

        written = seed_history._write_history(cursor, stock_id, rows)
        connection.commit()
        print("     写入 stockhistory %d 行" % written)
        for ema_days in EMA_DAYS:
            count = seed_history._write_ema(cursor, connection, stock_id, ema_days, rows)
            print("     EMA%d 写入 %d 行" % (ema_days, count))
    print("")

    if not apply:
        cursor.close()
        connection.close()
        print("这是预演。真正执行请加 --apply。")
        print("写完后还要跑：")
        print("  python -B auto/cli/quotecli.py SH fx_susdcnh")
        print("  python -B auto/cli/autocalibrationcli.py --sync-nav --apply --symbol SH")
        print("  python -B auto/cli/autocalibrationcli.py --apply --symbol SH")
        return 0

    cursor.close()
    connection.close()
    print("完成。接着跑：")
    print("  python -B auto/cli/quotecli.py SH fx_susdcnh")
    print("  python -B auto/cli/autocalibrationcli.py --sync-nav --apply --symbol SH")
    print("  python -B auto/cli/autocalibrationcli.py --apply --symbol SH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
