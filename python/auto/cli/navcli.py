"""基金官方净值抓取 CLI。

替代 PHP ``yahoostock.php`` 的净值部分（Yahoo 已全面 403，改用国内源）。

用法：

    cd F:\\woody\\woodyweb\\python

    python -B auto\\cli\\navcli.py --check                    # 只看源能取到什么，不写库
    python -B auto\\cli\\navcli.py                            # 抓取并写入 netvaluehistory
    python -B auto\\cli\\navcli.py --code 161130 --force      # 强制重取某只
    python -B auto\\cli\\navcli.py --history 162411           # 看某只的历史（前 10 条）
    python -B auto\\cli\\navcli.py --api                      # 打印净值源接口说明（换源参考）
    python -B auto\\cli\\navcli.py --list-sources             # 列出已注册净值源
    python -B auto\\cli\\navcli.py --all-sources 161130       # 三个源交叉对比

环境变量：

* ``PALMMICRO_NAV_SOURCE``  净值源名字（默认 ``eastmoney``）
* ``PALMMICRO_NAV_FORCE``   设为 1 时强制重取
"""

from __future__ import annotations

import argparse
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

from source import navsource  # noqa: E402
from updater.navupdater import NavUpdater, build_default_updater, nav_code_of  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基金官方净值抓取")
    parser.add_argument("--check", action="store_true", help="只抓取并打印，不写数据库")
    parser.add_argument("--code", help="只处理这一个基金代码")
    parser.add_argument("--force", action="store_true", help="忽略已有数据判断，强制重取")
    parser.add_argument("--history", metavar="代码", help="打印该基金的历史净值")
    parser.add_argument("--all-sources", metavar="代码", help="用所有源取同一只基金做交叉对比")
    parser.add_argument("--source", help="净值源名字，默认取环境变量或 eastmoney")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少只（0 表示不限）")
    parser.add_argument("--api", action="store_true", help="打印净值源接口说明")
    parser.add_argument("--list-sources", action="store_true", help="列出已注册的净值源")
    parser.add_argument("--sync-names", action="store_true",
                        help="用权威源同步 stock.name 并报告不一致")
    parser.add_argument("--check-names", action="store_true",
                        help="只报告名称差异，不改库")
    return parser.parse_args()


def print_api() -> int:
    """打印"换净值源"需要的接口说明。"""
    print("基金净值数据源接口（换源只改这里，抓取层与分析层都不用动）")
    print("=" * 72)
    print("""
1) 抽象基类：python/auto/navsource.py 的 NavSource
   必须实现：
       name                      : str，数据源标识
       fetch_latest(code)        : {code,date,nav[,...]}，取不到返回 None
   可选覆写：
       fetch_history(code, limit): [{...}, ...] 按日期倒序（默认只返回最新一条）
       describe()                : 诊断信息

2) 入参与返回约定：
       * code 是**6 位纯数字**基金代码（如 161130），不带市场前缀；
       * fetch_latest 返回的 date 用 'YYYY-MM-DD'，nav 用字符串（保原始精度）；
       * 取不到数据返回 None，不要抛异常（调用方按"跳过该基金"处理）。

3) 注册与切换：
       from source.navsource import register_nav_source, get_nav_source
       register_nav_source("mysource", lambda **kw: MySource(**kw))
       # 然后设置 PALMMICRO_NAV_SOURCE=mysource，或 get_nav_source("mysource")

4) 已注册的信源：
       crosscheck  **默认**：东财为主 + 新浪对照 + 差异告警（见下）
       eastmoney   东方财富（首选单源，有历史分页）
       sina        新浪 f_ 接口（备选单源，抓取层已就绪）
       szse        深交所官方（兜底交叉校验，仅深市 LOF/ETF）

5) 默认的 crosscheck 行为（需求指定）：
       * 主信源 = 东财，对照信源 = 新浪；
       * 同一只基金两个源都抓 → 清洗（去空白/逗号/百分号、
         日期统一成 YYYY-MM-DD）→ 对比；
       * |净值差| > 容差 → 打 ALERT 到 stderr；
       * 数值一致但日期不同 → 只打 INFO（各源更新时点不同，属正常）；
       * 主源取不到 → 自动用对照源的数值，并打 WARN；
       * 要退回单源：PALMMICRO_NAV_SOURCE=eastmoney（或 sina / szse）。

6) 相关环境变量：
       PALMMICRO_NAV_SOURCE          信源名字（默认 crosscheck）
       PALMMICRO_NAV_TOLERANCE       净值容差，元（默认 0.001）
       PALMMICRO_NAV_ALERT_ON_MISSING 设 1 时"一侧无数据"也告警
       PALMMICRO_NAV_FORCE           设 1 时强制重取
""")
    print("当前已注册：%s" % ", ".join(navsource.list_nav_sources()))
    print("默认信源：%s" % navsource.DEFAULT_NAV_SOURCE)
    print("当前实例自述：%s" % json.dumps(navsource.get_nav_source().describe(), ensure_ascii=False))
    return 0


def show_history(code: str, source_name: str = None) -> int:
    """打印某只基金的历史净值，用于确认源的分页与字段。"""
    source = navsource.get_nav_source(source_name)
    rows = source.fetch_history(code, limit=10)
    if not rows:
        print("源 %s 没有返回 %s 的净值。" % (source.name, code))
        return 1
    print("源 %s 返回 %s 最近 %d 条：" % (source.name, code, len(rows)))
    print("%-10s %-12s %-12s %-10s" % ("代码", "日期", "单位净值", "累计净值"))
    for row in rows:
        print("%-10s %-12s %-12s %-10s" % (
            row.get("code"), row.get("date"), row.get("nav"), row.get("acc_nav") or "-"))
    return 0


def compare_sources(code: str) -> int:
    """用所有**单信源**取同一只基金，交叉验证数值是否一致。

    跳过 ``crosscheck`` 本身 —— 它只是"主源 + 对照源"的包装，
    放进来会把同一只基金抓两遍、还会重复打印。
    """
    print("交叉对比 %s（各源应当一致，不一致说明有一方口径不同）：\n" % code)
    print("%-12s %-12s %-12s %-12s %s" % ("源", "日期", "单位净值", "累计净值", "结果"))
    single_sources = [name for name in navsource.list_nav_sources() if name != "crosscheck"]
    for name in single_sources:
        try:
            record = navsource.create_source(name).fetch_latest(code)
        except Exception as exc:
            print("%-12s %s" % (name, "异常 %s" % type(exc).__name__))
            continue
        if not record:
            print("%-12s %-12s %-12s %-12s %s" % (name, "-", "-", "-", "无数据"))
            continue
        print("%-12s %-12s %-12s %-12s %s" % (
            name, record.get("date"), record.get("nav"),
            record.get("acc_nav") or "-", "OK"))
    return 0


def sync_names(apply: bool = False) -> int:
    """用权威源同步/核对 ``stock.name``。

    起因：库里曾把 SZ161130 写成"易方达标普信息科技LOF"（那是 SZ161128 的名字）。
    新浪 ``f_`` 接口会返回权威全称，这里用它来核对。
    """
    from updater.navupdater import build_default_updater

    updater = build_default_updater(source=navsource.get_nav_source("sina"))
    pairs = updater.collect_targets()
    if not pairs:
        print("库里没有可核对的基金（没有 6 位数字代码的符号）。")
        return 1

    results = updater.sync_names(pairs, apply=apply)
    if updater.connection is not None:
        updater.connection.commit()

    changed = [item for item in results if item.get("mismatch")]
    print("%s 共 %d 只基金，%s %d 只：" % (
        "名称同步" if apply else "名称核对", len(results),
        "已更新" if apply else "发现不一致", len(changed)))
    print("%-10s %-8s %-26s %-34s %s" % ("符号", "代码", "库里的名称", "权威名称", "结果"))
    for item in results:
        if item.get("error"):
            print("%-10s %-8s %s" % (item["symbol"], item["code"], "错误 " + item["error"]))
            continue
        result = "一致"
        if item["mismatch"]:
            result = "已更新为全称" if item["changed"] else "不一致"
        print("%-10s %-8s %-26s %-34s %s" % (
            item["symbol"], item["code"],
            (item["current"] or "")[:26],
            (item["authoritative"] or "(源无名称)")[:34],
            result))
    if not apply and changed:
        print("\n这是核对。真正写库（改成权威全称）请加 --sync-names。")
        print("注意：库里默认保留简洁短名（如 易方达纳斯达克100LOF），页面列窄放不下全称；")
        print("      核对用的是关键片段包含关系，短名对得上就不会报不一致。")
    return 0


def main() -> int:
    args = parse_args()

    if args.api:
        return print_api()
    if args.list_sources:
        print("已注册的净值源：%s" % ", ".join(navsource.list_nav_sources()))
        return 0
    if args.all_sources:
        return compare_sources(args.all_sources)
    if args.sync_names or args.check_names:
        return sync_names(apply=args.sync_names)
    if args.history:
        return show_history(args.history, args.source)
    if args.check and args.code:
        # --check --code：只看源返回什么，连库都不用
        return show_history(args.code, args.source)

    # 需要数据库：构造生产用更新器
    source = navsource.get_nav_source(args.source)
    print("净值源：%s  %s" % (source.name, json.dumps(source.describe(), ensure_ascii=False)))

    if args.check:
        # 只读库取符号列表，不写任何数据
        from core.stockdata import MySqlFundDataSource, load_mysql_env, mysql_config_from_env

        config = mysql_config_from_env(load_mysql_env(os.environ))
        print("目标库：%s@%s/%s" % (config["user"], config["host"], config["database"]))
        fund_source = MySqlFundDataSource.from_env()
        pairs = NavUpdater(store=fund_source, source=source).collect_targets()
        if args.limit:
            pairs = pairs[:args.limit]
        print("待处理 %d 只基金：%s\n" % (len(pairs), ", ".join(s for s, _ in pairs)))
        print("%-12s %-12s %-12s %s" % ("符号", "日期", "单位净值", "说明"))
        for symbol, _stock_id in pairs:
            code = nav_code_of(symbol)
            if not code:
                continue
            record = source.fetch_latest(code)
            if record:
                print("%-12s %-12s %-12s %s" % (
                    symbol, record.get("date"), record.get("nav"),
                    "已有" if _has_nav(code) else "待写入"))
            else:
                print("%-12s %-12s %-12s %s" % (symbol, "-", "-", "源无数据"))
        return 0

    updater = build_default_updater(source=source)
    pairs = updater.collect_targets()
    if args.code:
        pairs = [(symbol, stock_id) for symbol, stock_id in pairs
                 if nav_code_of(symbol) == args.code]
    if args.limit:
        pairs = pairs[:args.limit]

    print("待处理 %d 只基金：%s" % (len(pairs), ", ".join(s for s, _ in pairs)))
    if not pairs:
        print("没有可处理的基金（stock 表里没有 6 位数字代码的符号）。")
        return 1

    updated = updater.update(pairs, force=args.force)
    if updater.connection is not None:
        updater.connection.commit()
    print("已写入 %d 条净值。" % updated)
    if updater.last_error:
        print("注意：%s" % updater.last_error)
    return 0


def _has_nav(code: str) -> bool:
    """辅助：查库里是否已有该基金当天净值（只用于 --check 的提示列）。"""
    import datetime

    from core.stockdata import MySqlFundDataSource

    try:
        source = MySqlFundDataSource.from_env()
        connection = source.connection
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT id FROM stock WHERE symbol LIKE %s LIMIT 1", ("%" + code,))
            row = cursor.fetchone()
            if not row:
                return False
            cursor.execute(
                "SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s LIMIT 1",
                (row["id"], datetime.date.today().isoformat()),
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()
            connection.close()
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
