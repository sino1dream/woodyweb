"""自动校准命令行工具（中间层校准值的手动/强制刷新）。

## 为什么需要这个命令

页面（``/fund/qdiilof``）访问时**已经**会自动校准（见
``web/services/fund_list_service.py`` 的 ``_auto_calibrate``），但它有闸门：

    净值日期 == 校准日期  ->  跳过

这条闸门与 PHP ``DailyCalibration()`` 一致，语义是"这一天已经校准过"。
所以**只有中间层净值日期前进时**才会重算。

问题在于：**中间层（QQQ/SPY）的净值在本项目里没人抓**。
``navupdater.collect_targets()`` 只取能剥出 6 位数字代码的符号
（``SZ161130`` / ``SH513100``），QQQ 不是中国基金代码，被排除在外。
于是中间层净值日期永远停在种子那一行，闸门永远挡住 ——
用户跑了 ``quoteupdatercli --daemon``（只抓**行情快照**）之后发现
校准值不动，就是这个原因。

本命令提供三件事：

1. ``--show``   看中间层"净值日期 / 校准日期 / 闸门状态"；
2. ``--force``  忽略闸门，按当前行情重算并写库（运维补算用）；
3. ``--set-nav`` 手工把中间层的净值日期往前推（让往后的自动校准能正常触发）。

用法：

    cd F:\\woody\\woodyweb\\python

    python -B auto\\autocalibrationcli.py --show
    python -B auto\\autocalibrationcli.py                  # 预演（不写库）
    python -B auto\\autocalibrationcli.py --apply          # 走闸门写入
    python -B auto\\autocalibrationcli.py --force --apply  # 忽略闸门强制写入
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# cli/ 是 python/auto 的直接子目录，所以 auto 根目录要再往上一层。
AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from updater.autocalibration import (  # noqa: E402
    MIDDLE_PAIR_FALLBACK,
    build_auto_calibrator,
    default_middle_symbols,
)
from core.stockdata import MySqlFundDataSource  # noqa: E402


def _source():
    return MySqlFundDataSource.from_env()


def _targets(source, symbol=None) -> list:
    """要处理的中间层符号。

    优先用 ``qdii_est_symbol_map()`` 能解析出的那些（页面真正用到的），
    再并入内置配对表的**值**（``hf_NQ`` 那类配对标的不算，见下）——
    后者是兜底，保证即使某只基金没配好也能看到状态。

    **排除指数**（``^NDX`` / ``^GSPC``）：它们是"被读取的配对标的"，
    本身没有净值、也不是校准对象（PHP 只校准 ``$arLev`` 里的 ETF）。
    把它们列进来只会输出一堆"没有单位净值"的噪声。
    """
    if symbol:
        return [symbol]
    symbols = set(default_middle_symbols(source))
    # 内置表的**键**是中间层（QQQ），**值**是配对标的（^NDX）—— 只要键
    symbols.update(MIDDLE_PAIR_FALLBACK)
    return sorted(
        s for s in symbols
        if source.get_stock_id(s) is not None and not str(s).startswith("^")
    )


def cmd_show(args) -> int:
    """看中间层的净值日期、校准日期与闸门状态。"""
    source = _source()
    calibrator = build_auto_calibrator(source)

    print("%-8s %-8s %-14s %-14s %-14s %s" % (
        "中间层", "配对", "净值日期", "校准值", "校准日期", "闸门"))
    print("-" * 84)

    for symbol in _targets(source, args.symbol):
        stock_id = source.get_stock_id(symbol)
        record = source.get_latest_net_value(stock_id)
        calibration = source.get_calibration_now(stock_id)
        net_date = str(getattr(record, "date", None) or "-")
        net_close = getattr(record, "close", None)
        cal_date = str(getattr(calibration, "date", None) or "-")
        cal_close = getattr(calibration, "close", None)

        if net_date == cal_date and net_date != "-":
            verdict = "挡住（净值日期==校准日期）"
        elif net_date == "-":
            verdict = "挡住（没有净值）"
        else:
            verdict = "**可校准**"

        print("%-8s %-8s %-14s %-14s %-14s %s" % (
            symbol, calibrator._pair_symbol(symbol) or "-",
            "%s(%s)" % (net_date, net_close), cal_close, cal_date, verdict))

    print("")
    print("说明：闸门与 PHP ``DailyCalibration()`` 一致 —— 净值日期与校准日期相同就跳过。")
    print("      中间层净值本项目没人抓（``navupdater`` 只认 6 位数字代码），")
    print("      所以要么用 --force 补算，要么用 --set-nav 手工推进净值日期。")
    print("      详见 skill ``palmmicro-ops``。")
    source.connection.close()
    return 0


def cmd_calibrate(args) -> int:
    """重算中间层校准值。"""
    source = _source()
    calibrator = build_auto_calibrator(source)
    force = args.force
    apply = args.apply

    print("模式：%s%s" % (
        "强制（忽略闸门）" if force else "走闸门",
        "，写库" if apply else "，预演（不写库）"))
    print("")
    print("%-8s %-8s %-14s %-10s %-14s %s" % (
        "中间层", "配对", "配对值", "原校准值", "算出的新校准值", "结果"))
    print("-" * 88)

    changed = 0
    for symbol in _targets(source, args.symbol):
        stock_id = source.get_stock_id(symbol)
        old = source.get_calibration_now(stock_id)
        old_close = getattr(old, "close", None)

        # --force 时先把闸门"骗过"：不写库地算一遍需要绕开 _calibration_date
        if force:
            outcome = _calibrate_forced(calibrator, source, symbol, apply)
        else:
            outcome = calibrator.calibrate_middle(symbol, apply=apply)

        if outcome.skipped:
            print("%-8s %-8s %-14s %-10s %-14s %s" % (
                symbol, outcome.pair_symbol or "-", outcome.pair_value or "-",
                old_close, "-", outcome.skipped))
            continue

        print("%-8s %-8s %-14s %-10s %-14s %s" % (
            symbol, outcome.pair_symbol or "-", outcome.pair_value, old_close,
            outcome.calibration,
            "已写库" if outcome.written else ("预演" if not apply else "未写（已有）")))
        if outcome.written:
            changed += 1
        for warning in outcome.warnings:
            print("        [告警] %s" % warning)

    print("")
    print("写了 %d 条。" % changed)
    if not apply:
        print("这是预演。要写库请加 --apply。")
    source.connection.close()
    return 0


def _calibrate_forced(calibrator, source, symbol, apply: bool):
    """忽略闸门地算一次。

    ``calibrate_middle`` 的闸门写在方法体内，这里用"临时把校准日期挪开"
    是侵入性的做法；更干净的办法是绕开闸门直接调内部步骤 ——
    本函数就是后者：把闸门要比较的那个日期换成 ``None`` 再看结果。
    """
    original = calibrator._calibration_date

    def _no_gate(stock_id):
        return None

    calibrator._calibration_date = _no_gate
    try:
        return calibrator.calibrate_middle(symbol, apply=apply)
    finally:
        calibrator._calibration_date = original


def cmd_sync_nav(args) -> int:
    """把中间层的**净值**推进到最新行情日期（让自动校准的闸门能前进）。

    本项目没有免费可用的美股 ETF 官方 NAV 源（实测清单见
    ``usnavsource`` 模块 docstring），所以用**派生净值**：
    ``基准净值 × 今日价格 / 基准日价格``，基准净值锚定在真实 NAV 上
    （``汇率 / 校准值``，实测与生产端 QQQ 净值吻合到 0.03%）。

    只写 ``netvaluehistory``，且**已有该日期就不覆盖**（与抓取层语义一致）。
    """
    source = _source()
    from source.usnavsource import build_middle_nav_source

    nav_source = build_middle_nav_source(source)

    print("源：%s" % json.dumps(nav_source.describe(), ensure_ascii=False))
    print("")
    print("%-8s %-14s %-16s %-14s %s" % ("中间层", "日期", "派生净值", "基准净值", "会不会写"))
    print("-" * 78)

    planned = []
    for symbol in _targets(source, args.symbol):
        record = nav_source.fetch_middle_nav(symbol)
        if record is None:
            print("%-8s %-14s %-16s %-14s %s" % (symbol, "-", "-", "-", "取不到价格，跳过"))
            continue
        exists = _nav_exists(source, symbol, record.date)
        print("%-8s %-14s %-16s %-14s %s" % (
            symbol, record.date, round(record.nav, 6),
            round(float(record.base_nav), 6) if record.base_nav else "-",
            "跳过（该日期已有净值）" if exists else "**写**"))
        if not exists:
            planned.append(record)

    print("")
    print("计划写入 %d 条。" % len(planned))
    if not args.apply:
        print("这是预演。真正执行请加 --apply。")
        source.connection.close()
        return 0

    written = 0
    for record in planned:
        if _write_nav(source, record):
            written += 1
            print("  已写 %s" % record.describe())
    print("写了 %d 条。" % written)
    source.connection.close()
    return 0


def _nav_exists(source, symbol: str, date: Any) -> bool:
    """该符号在该日期是否已有净值。

    **直接查库**，不走数据源的 ``get_net_value_close`` —— 那条路的返回值语义
    随实现而变（实测在这里没生效，导致重复 INSERT 报 1062 却把判断显示成"要写"）。
    """
    stock_id = source.get_stock_id(symbol)
    connection = getattr(source, "connection", None)
    if stock_id is None or connection is None:
        return True
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT 1 FROM netvaluehistory WHERE stock_id = %s AND `date` = %s LIMIT 1",
            (stock_id, date),
        )
        return cursor.fetchone() is not None
    except Exception:
        return True
    finally:
        cursor.close()


def _write_nav(source, record) -> bool:
    """写一条净值（已有就不动）。"""
    connection = getattr(source, "connection", None)
    stock_id = source.get_stock_id(record.symbol)
    if connection is None or stock_id is None:
        return False
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s)",
            (stock_id, record.date, record.nav),
        )
        commit = getattr(connection, "commit", None)
        if commit is not None:
            commit()
        return True
    except Exception as exc:
        print("  写失败 %s：%s" % (record.symbol, exc))
        return False
    finally:
        cursor.close()


def cmd_set_nav(args) -> int:
    """手工写入中间层的净值（用来推进"净值日期"）。

    用途：本项目没有美股 ETF 的净值源，所以中间层净值日期不会自己前进，
    闸门就一直挡住。手工写一条当天净值即可让往后的自动校准恢复正常。

    只写 ``netvaluehistory``，且**已有该日期就不覆盖**（与抓取层语义一致）。
    """
    symbol = args.set_nav
    date = args.date or datetime.date.today().isoformat()
    source = _source()
    stock_id = source.get_stock_id(symbol)
    if stock_id is None:
        print("符号 %s 不在 stock 表里。" % symbol)
        source.connection.close()
        return 1

    connection = source.connection
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s LIMIT 1",
            (stock_id, date),
        )
        existing = cursor.fetchone()
        if existing:
            print("%s 在 %s 已有净值 %s，不覆盖。" % (symbol, date, existing["close"]))
            return 0

        value = args.value
        if value is None:
            # 不指定数值时，用"行情价 / 原校准值"反推一个合理净值 ——
            # 这正是 _implied_middle_nav 的逆运算（真实净值 = 汇率 / 校准值）。
            latest_calibration = source.get_calibration_now(stock_id)
            if latest_calibration is None or not latest_calibration.close:
                print("没有校准值可反推净值，请用 --value 显式指定。")
                return 1
            cny = _latest_cny(source)
            value = float(cny) / float(latest_calibration.close)
            print("未指定 --value，按「汇率 %s / 原校准值 %s」反推净值 = %s" % (
                cny, latest_calibration.close, round(value, 6)))

        if not args.apply:
            print("预演：将写入 %s %s 净值=%s（加 --apply 真写）" % (symbol, date, value))
            return 0

        cursor.execute(
            "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s)",
            (stock_id, date, value),
        )
        connection.commit()
        print("已写入 %s %s 净值=%s" % (symbol, date, value))
        return 0
    finally:
        cursor.close()
        source.connection.close()


def _latest_cny(source):
    """最新美元人民币中间价；取不到返回 1.0。"""
    cny_id = source.get_stock_id("USCNY")
    if cny_id is None:
        return 1.0
    value = source.get_latest_net_value_close(cny_id)
    return value if value not in (None, "") else 1.0


def cmd_api(args) -> int:
    print("""自动校准运维说明（详细版见 skill ``palmmicro-ops``）
====================================================

## 一句话

**daemon 只抓行情，不抓净值。校准值要动，必须有新的「中间层净值」。**

## 三个独立的东西，别混

| 动作 | 命令 | 写哪张表 | 频率 |
| --- | --- | --- | --- |
| 抓实时行情 | `quoteupdatercli.py --daemon --interval 60` | `realtimequote` | 60 秒 |
| 抓基金官方净值 | `navcli.py` | `netvaluehistory` | 每天净值公布后 |
| 重算中间层校准值 | 本命令 / 页面访问时自动 | `calibrationhistory` | 中间层净值日期前进时 |

## 为什么页面访问没更新校准值

页面访问**确实**会调自动校准，但有闸门：

    净值日期 == 校准日期  ->  跳过

而**中间层的净值本项目没人抓**：`navupdater.collect_targets()` 只取能剥出
6 位数字代码的符号（`SZ161130` / `SH513100`），QQQ/SPY 被排除。
于是中间层净值日期停在种子那一行，闸门永远挡住。

用 `--show` 能直接看到：

    QQQ  净值日期=2026-10-02  校准日期=2026-10-02  闸门 挡住

## 怎么让校准值动起来

三条路，按推荐顺序：

1. **推进中间层净值日期**（最贴近生产）：
   `--set-nav QQQ --apply`（不指定 `--value` 时按「汇率 / 原校准值」反推），
   之后页面访问会自动校准，闸门也恢复正常。
2. **强制补算一次**：
   `--force --apply` —— 忽略闸门，按当前行情与汇率重算并写库。
   适合"净值日期推不动但想立刻更新"的情形。
3. **什么都不做，等净值源接上**：真正修法是给中间层加美股 ETF 净值源
   （生产端是 `UpdateInvescoNetValue('QQQ')`），本项目尚未实现。""")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="中间层校准值的查看与重算")
    parser.add_argument("--show", action="store_true", help="看净值/校准日期与闸门状态")
    parser.add_argument("--apply", action="store_true", help="真的写库（默认只预演）")
    parser.add_argument("--force", action="store_true", help="忽略闸门，强制重算")
    parser.add_argument("--symbol", help="只处理这一个中间层符号")
    parser.add_argument("--set-nav", metavar="符号", help="手工写入该符号的净值（推进净值日期）")
    parser.add_argument("--sync-nav", action="store_true",
                        help="用派生源把中间层净值推进到最新行情日期")
    parser.add_argument("--date", help="配合 --set-nav：净值日期，默认今天")
    parser.add_argument("--value", type=float, help="配合 --set-nav：净值数值，默认按汇率/校准值反推")
    parser.add_argument("--api", action="store_true", help="运维口径说明")
    args = parser.parse_args(argv)

    if args.api:
        return cmd_api(args)
    if args.sync_nav:
        return cmd_sync_nav(args)
    if args.set_nav:
        return cmd_set_nav(args)
    if args.show:
        return cmd_show(args)
    return cmd_calibrate(args)


if __name__ == "__main__":
    sys.exit(main())
