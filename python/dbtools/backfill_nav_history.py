"""给演示库补 ``netvaluehistory`` 的历史段（东方财富净值接口）。

## 为什么需要

``navcli`` 只写**最新一条**（"今天"的净值），没有回填能力。于是演示库里
QDII 基金的净值表是断的 —— 实测 SZ161125 只有 09-29 / 09-30 / 10-01 /
10-08 / 10-12 五行，缺 **10-09 / 10-13 / 10-14**。

断在哪一天，「基金溢价记录」（``fundhistoryparagraph``）就少哪一行 ——
那段是拿**价格日的前一个交易日**去查净值，净值没有就整行跳过
（``if ($strNetValue = $net_sql->GetClose(...))``）。生产端
``sz161125cn.php`` 那张表有七行，本地只有四行，差的就是这几天。

## 数据源与可信度

``source.navsource.EastmoneyNavSource``（``api.fund.eastmoney.com/f10/lsjz``），
与 ``navcli`` 的默认源**同一个**。实测与生产端逐格一致::

    10-09=3.1843  10-12=3.1817  10-13=3.1803  10-14=3.1448

## 只补不覆盖

已存在的**（日期, 净值）一律不动** —— 库里那些行可能是当时手工核准过的，
覆盖掉就再也回不来了。只写库里**没有那一天**的行。

## 别再手工维护符号清单（2026-11-09）

日常例程原来跑两条 ``--symbols <33 只美股族>`` / ``--symbols <28 只港股族>``，
**只覆盖 61 / 102 只**。剩下 41 只一次都没被回填过，净值就永远停在 10 月 ——
`/fund/qdiilof` 的溢价率拿旧净值当分母，看起来像"数据没更新"，其实是
**清单漏了基金**。清单唯一的出处是 ``core.symbolinventory.fund_symbols()``
（与 ``quotecli --all-qdii`` 同一份 102 只），所以用 ``--all-funds`` 取，
不要再往文档里抄逗号串。

``--report`` 是只读体检：列出每只基金的最新净值日、按落后天数标出断档，
并给出可直接粘进 ``--symbols`` 的补数清单（不联网、不写库）。

用法::

    python -B dbtools\\backfill_nav_history.py --report             # 只读断档体检
    python -B dbtools\\backfill_nav_history.py --all-funds --days 3 --apply   # 每日
    python -B dbtools\\backfill_nav_history.py --all-funds --days 40 --apply  # 断档追补
    python -B dbtools\\backfill_nav_history.py --symbols SZ161125 --days 40   # 预演
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

#: 同 ``seed_a_share_history.py``：import 期间把 stdout 换成没有 ``buffer``
#: 的对象，免得被 import 的脚本再包一层把底层 buffer 关掉。
_REAL_STDOUT = sys.stdout
sys.stdout = io.StringIO()

from core.stockdata import MySqlFundDataSource  # noqa: E402
from core.symbolinventory import fund_symbols  # noqa: E402
from source.navsource import EastmoneyNavSource  # noqa: E402

sys.stdout = (io.TextIOWrapper(_REAL_STDOUT.buffer, encoding="utf-8", errors="replace")
              if hasattr(_REAL_STDOUT, "buffer") else _REAL_STDOUT)

#: 不点名时的兜底清单（CK6 要对齐的是 ``/fund/SZ161125`` 那一页）。
#: **日常别靠它** —— 用 ``--all-funds``。
DEFAULT_SYMBOLS = ("SZ161125",)

#: 默认往回取多少条净值记录。
DEFAULT_DAYS = 40

#: ``--report`` 里"断档"的判定：比**基金**最新净值日落后多少**自然日**。
#: QDII 净值 T+1~T+2 披露、再摊上周末，落后 3~4 天仍属正常；> 5 天
#: 一定是漏回填（实测 10-30 断档 = 落后 7 天，11-03 正常 = 落后 3 天）。
DEFAULT_STALE_DAYS = 5


def _arg(name: str) -> str | None:
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


def resolve_symbols(raw_symbols: str | None, all_funds: bool = False):
    """把 ``--symbols`` 与 ``--all-funds`` 合成去重、保序的清单。

    ``--all-funds`` 取 ``core.symbolinventory.fund_symbols()``（102 只，
    与 ``quotecli --all-qdii`` 同源）；两者都给时取并集，方便临时点名补。
    都没给就退回 ``DEFAULT_SYMBOLS``（只补一只，保持老行为）。
    """
    result = []
    for symbol in (fund_symbols() if all_funds else ()):
        if symbol and symbol not in result:
            result.append(symbol)
    for part in (raw_symbols or "").split(","):
        symbol = part.strip()
        if symbol and symbol not in result:
            result.append(symbol)
    return tuple(result) if result else DEFAULT_SYMBOLS


def nav_state(source, symbols):
    """只读：``[(symbol, stock_id, latest, count), ...]``，不联网。"""
    cursor = source.connection.cursor()
    states = []
    for symbol in symbols:
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            states.append((symbol, None, None, 0))
            continue
        cursor.execute(
            "SELECT MAX(date) AS latest, COUNT(*) AS cnt "
            "FROM netvaluehistory WHERE stock_id = %s", (stock_id,))
        row = cursor.fetchone() or {}
        states.append((symbol, stock_id, row.get("latest"),
                       int(row.get("cnt") or 0)))
    cursor.close()
    return states


def print_report(source, symbols, stale_days: int) -> None:
    """--report：按净值落后天数找断档，并给出可直接粘的追补清单。

    锚点取**基金最新净值日**，不是写死的日期，也不是全表 MAX(date) ——
    后者会被 ``forexcli`` 写进同一张表的中间价行（USCNY 等）顶到今天，
    把所有基金都误判成断档。QDII 净值 T+1~T+2 披露，落后几天属正常。
    """
    from datetime import date

    states = nav_state(source, symbols)
    all_funds = fund_symbols()
    cursor = source.connection.cursor()
    cursor.execute(
        "SELECT MAX(n.date) AS latest FROM netvaluehistory n "
        "JOIN stock s ON s.id = n.stock_id WHERE s.symbol IN (%s)"
        % ",".join(["%s"] * len(all_funds)), tuple(all_funds))
    row = cursor.fetchone() or {}
    cursor.close()
    anchor_raw = row.get("latest")
    anchor = date.fromisoformat(str(anchor_raw)) if anchor_raw else None

    print("净值断档体检（只读，不联网、不写库）")
    print("清单 %d 只 | 基金最新净值日 %s | 断档判定：落后 > %d 个自然日"
          % (len(states), anchor or "-", stale_days))
    print("-" * 78)
    missing, stale, healthy = [], [], 0
    for symbol, stock_id, latest, count in sorted(
            states, key=lambda item: (str(item[2] or "0000-00-00"), item[0])):
        if stock_id is None:
            print("  %-10s stock 表里没有这个符号" % symbol)
        elif latest is None:
            missing.append(symbol)
            print("  %-10s 没有净值行" % symbol)
        else:
            lag = (anchor - date.fromisoformat(str(latest))).days if anchor else 0
            if lag > stale_days:
                stale.append(symbol)
                print("  %-10s %s  落后 %2d 天  (%d 行)  <== 断档"
                      % (symbol, latest, lag, count))
            else:
                healthy += 1
    print("-" * 78)
    print("正常 / 披露延迟 %d 只 | 断档 %d 只 | 无净值 %d 只"
          % (healthy, len(stale), len(missing)))
    todo = missing + stale
    if todo:
        print("追补清单（--symbols 直接粘）：")
        print(",".join(todo))
        print("追补命令（东财 pageSize 实测上限 20 条，约覆盖最近 20 个交易日）：")
        print("  python -B dbtools\\backfill_nav_history.py --symbols %s "
              "--days 40 --apply" % ",".join(todo))
    else:
        print("没有需要追补的基金。每日跑 --all-funds --days 3 --apply 即可。")


def fund_code_of(symbol: str) -> str:
    """库内符号 -> 净值接口的代码（``SZ161125`` -> ``161125``）。"""
    text = str(symbol).strip()
    for prefix in ("SZ", "SH", "BJ"):
        if text.upper().startswith(prefix):
            return text[len(prefix):]
    return text


def main() -> int:
    apply = "--apply" in sys.argv
    report = "--report" in sys.argv
    skip_backup = "--skip-backup" in sys.argv
    days = _arg_int("--days", DEFAULT_DAYS)
    stale_days = _arg_int("--stale-days", DEFAULT_STALE_DAYS)
    raw_symbols = _arg("--symbols")
    # --report 不带 --symbols 时也要看全量，否则只体检一只没有意义。
    all_funds = "--all-funds" in sys.argv or (report and not raw_symbols)
    symbols = resolve_symbols(raw_symbols, all_funds)

    if report and apply:
        print("--report 是只读体检，不能和 --apply 一起用。")
        return 2

    source = MySqlFundDataSource.from_env()
    connection = source.connection
    cursor = connection.cursor()

    if report:
        print_report(source, symbols, stale_days)
        cursor.close()
        connection.close()
        return 0

    if apply and not skip_backup:
        # 2026-11-09 起内置备份改走 backup_db.py（默认 --lock-tables）。
        # 旧实现调 seed_p0_symbols.backup_database()，用 --single-transaction，
        # 对演示库里的 MyISAM 表等于既没事务快照、又关掉了锁表
        # （见 dbtools/README_backup_db.md）。失败就停在这里，绝不带着
        # 一份坏备份继续写库。
        import backup_db
        code = backup_db.main([])
        if code != 0:
            print("备份失败（退出码 %s），停在这里，没有动库。" % code)
            cursor.close()
            connection.close()
            return 1
        print("")
    elif apply:
        print("**--skip-backup：本次不备份，出错只能手工恢复**")
        print("")

    print("回填清单 %d 只 | --days %d | %s"
          % (len(symbols), days, "写入" if apply else "预演"))

    nav_source = EastmoneyNavSource()

    for symbol in symbols:
        print("=" * 74)
        print(symbol)
        print("=" * 74)
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("  **stock 表里没有这个符号，跳过**")
            continue

        rows = nav_source.fetch_history(fund_code_of(symbol), limit=days)
        if not rows:
            print("  **抓不到净值历史，跳过**（东方财富接口不稳定，可重试）")
            continue
        print("  抓到 %d 条：%s ~ %s" % (len(rows), rows[-1]["date"], rows[0]["date"]))

        cursor.execute(
            "SELECT date, close FROM netvaluehistory WHERE stock_id = %s", (stock_id,))
        existing = {str(row["date"]) for row in cursor.fetchall()}

        planned = [row for row in rows if str(row["date"]) not in existing]
        print("  库里已有 %d 天，会补 %d 天" % (len(existing), len(planned)))
        for row in planned:
            print("     + %s  %s" % (row["date"], row["nav"]))

        if not apply:
            continue

        written = 0
        for row in planned:
            cursor.execute(
                "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE close = VALUES(close)",
                (stock_id, row["date"], row["nav"]),
            )
            written += 1
        connection.commit()
        print("  写入 %d 行" % written)

    cursor.close()
    connection.close()
    print("")
    print("完成。" if apply else "这是预演。真正执行请加 --apply。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
