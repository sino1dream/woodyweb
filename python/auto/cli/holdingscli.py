"""持仓更新命令行工具（QDII-LOF 混合 / QDII-ETF 一族）。

对应 PHP ``woody/res/php/_qdiimix.php`` 的 ``_QdiiMixAccount``：
页面渲染时顺带更新的持仓编排（``_updateStockHoldings``）。本项目把这类
"渲染时抓取"挪到了定时任务（realtime 抓取只归 daemon、web 只读快照，
见 ``README_p0_data_layer.md``），所以这里做成可独立运行的 CLI，
闸门逻辑与原版一字不差（``updater/holdingsupdater.py``）。

用法：

    cd F:\\woody\\woodyweb\\python

    python -B auto\\cli\\holdingscli.py                    # dry-run 全部 qdiimix 族
    python -B auto\\cli\\holdingscli.py --symbol SZ164701  # 只看一只
    python -B auto\\cli\\holdingscli.py --all-qdii         # 显式全部（同默认）
    python -B auto\\cli\\holdingscli.py --apply            # 真正写库/下载
    python -B auto\\cli\\holdingscli.py --api              # 口径说明

**默认 dry-run**：闸门照算、判定照打印，但到了写库/下载那一步就停 ——
先看判定再决定要不要 --apply。写库动作有两类：LOF 支线推进持仓日期、
ETF 支线从沪/深交易所下载申赎清单整份替换 holdings。

管理员写入口（对应 ``woody/res/submitholdings.php`` 的查询参数）：

    python -B auto\\cli\\holdingscli.py --admin SZ164701 --holdings '"GLD":100'
    python -B auto\\cli\\holdingscli.py --admin SZ164701 --fundposition 1.00

编码串是**去掉花括号的 JSON 对象体**（``"GLD":100,"SPY":50``），比例会被
归一化成合计 100。日期**不是参数** —— PHP 读的是基金现有的持仓日
（``$ref->GetHoldingsDate()``），表单只改比例不改日期。``--api`` 里有完整口径。
"""

from __future__ import annotations

import argparse
import io
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

from core.stockdata import MySqlFundDataSource  # noqa: E402
from sql.holdingstable import HoldingsDateSql  # noqa: E402
from sql.historytable import NetValueHistorySql  # noqa: E402
from sql.fundeststore import FundEstStore  # noqa: E402
from updater.holdingsupdater import (  # noqa: E402
    process_admin_holdings,
    qdiimix_holdings_symbols,
    update_stock_holdings_for_symbol,
)


def _source():
    return MySqlFundDataSource.from_env()


def _describe(source, symbol):
    """一行的状态摘要（只读，不参与闸门判定）。

    返回 ``(stock_id, 净值日, 持仓日, est 日)``；符号不在库里给 ``None``。
    """
    stock_id = source.get_stock_id(symbol)
    if stock_id is None:
        return None
    net_value_date = NetValueHistorySql(source.connection).get_date_now(stock_id)
    holdings_date = HoldingsDateSql(source.connection).read_date(stock_id)
    est_date = FundEstStore(source.connection).get_latest_date(stock_id)
    return stock_id, net_value_date, holdings_date, est_date


def cmd_run(args) -> int:
    """跑持仓更新（默认 dry-run）。"""
    source = _source()
    if args.symbol:
        symbols = [args.symbol]
    else:
        # 库里有符号的才跑（fundestcli 同款过滤：没建的符号报了也没意义）
        symbols = [symbol for symbol in qdiimix_holdings_symbols()
                   if source.get_stock_id(symbol) is not None]
    mode = "写入" if args.apply else "dry-run（不写库）"
    print("模式：%s；候选符号 %d 只" % (mode, len(symbols)))
    if not symbols:
        print("库中没有 qdiimix 族的符号。")
        source.connection.close()
        return 0

    updated = []
    blocked = []
    for symbol in symbols:
        print("")
        described = _describe(source, symbol)
        if described is None:
            print("== %s：stock 表里没有这个符号，跳过" % symbol)
            blocked.append(symbol)
            continue
        stock_id, net_value_date, holdings_date, est_date = described
        print("== %s（stock_id=%s）净值日 %s / 持仓日 %s / est 日 %s"
              % (symbol, stock_id, net_value_date or "-",
                 holdings_date or "-", est_date or "-"))
        trace = []
        ok = update_stock_holdings_for_symbol(
            source, symbol, dry_run=not args.apply, trace=trace
        )
        for line in trace:
            print("   %s" % line)
        if ok:
            updated.append(symbol)
            print("   → 已更新")
        elif args.apply:
            blocked.append(symbol)
            print("   → 未更新（判定见上）")
        else:
            blocked.append(symbol)

    print("")
    if updated:
        print("已更新 %d 只：%s" % (len(updated), " ".join(updated)))
    print("其余 %d 只未动。" % len(blocked))
    source.connection.close()
    return 0


def cmd_api(args) -> int:
    """打印口径说明，便于换数据源或排查。"""
    print("""持仓更新口径说明
================

## 两条写入路径（按基金类型分，PHP 就是这么分的）

**LOF 支线**（``in_arrayLofMix``：QDII-LOF-FOF 一族，含黄金/原油 LOF）
交易所不公布申赎清单，只能推进持仓**日期**：``CheckHoldingsDate``
确认每只成分股在净值日都有收盘价，就 ``WriteDate``。明细本身靠管理员
在 submitholdings 页提交（``updater/holdingsupdater.py`` 的
``update_stock_option_holdings``）。

**ETF 支线**（其余 qdiimix 族）从沪/深交易所下载申赎清单，整份替换
holdings：沪市走 XML（``query.sse.com.cn``），深市走 GB2312 的 PCF 文本
（``reportdocs.static.szse.cn``）。解析与落库在
``updater/etfholdings.py``。

## 四道闸门（``_qdiimix.php:46`` 的 ``_updateStockHoldings``，一字不差）

1. 净值日 == 持仓日 —— 已经最新；
2. 持仓日 == 官方日期（``GetOfficialDate()``）—— 官方 EST 已用新净值；
3. ETF 支线另有：est 日 == 净值日（fundest 还没到）、est 日 == 行情日
   （a day too early，港股混合族豁免）、时间窗 9:30–14:55；
4. LOF 支线：成分股在净值日都有收盘价才推进日期。

## 与 PHP 的架构差异（有意的）

PHP 把 ``_updateStockHoldings`` 挂在**页面渲染**里，靠"分钟级下载缓存 +
时间窗"兜频率。本项目 realtime 抓取只归 daemon、web 只读快照，所以编排
放 CLI 由定时任务调用（建议挂在每日例程里，与 fundestcli 同批）。
换调用方不改变闸门语义。

## 已知怪癖（照抄 PHP，勿"修正"）

* 沪市 XML 的 ``EstimatedCashComponent`` 字段 PHP 读不到（标签名少一个
  ``d`` 的笔误，``_sseholdings.php:12``）—— 沪市分母恒等于 NAVperCU；
* 深市 ``array_filter`` 连 ``'0'`` 一起滤掉，列索引会整体前移；
* ``CalcCurrency`` 按 **stock_id**（不是符号名）查汇率中间价。
详见 ``python/auto/README_holdings_updater_migration.md`` 与 pitfalls。""")
    return 0


def cmd_admin(args) -> int:
    """管理员写入口：对应 ``woody/res/submitholdings.php``。

    * ``--holdings`` → ``?holdings=<编码串>``：写明细 + 仓位 + 日期；
    * ``--fundposition`` → ``?fundposition=<值>``：只写仓位。

    两者都没有时什么都不做（PHP 的 ``if ($strHoldings = ...)`` 同样是
    "取到才动"）。**权限闸门在这里传 ``admin=True``**：能执行本命令的人
    已经等价于管理员（PHP 侧那个入口是 ``AdminRun()`` 里的
    ``IsAdmin()``，本命令绕开的是网页登录，不是权限本身 —— CLI 从来不
    对外暴露）。Web 入口传的是 ``admin=is_admin()``，见
    ``web/services/admin_auth.py``。
    """
    source = _source()
    symbol = args.admin
    stock_id = source.get_stock_id(symbol)
    if stock_id is None:
        print("%s：stock 表里没有这个符号。" % symbol)
        source.connection.close()
        return 1

    params = {}
    if args.holdings:
        params["holdings"] = args.holdings
    if args.fundposition is not None:
        params["fundposition"] = args.fundposition
    if not params:
        print("没有要写的参数（--holdings / --fundposition 二选一或都给）。")
        source.connection.close()
        return 1

    if not args.apply:
        print("dry-run：将处理 %s（stock_id=%s），参数 %s"
              % (symbol, stock_id, sorted(params)))
        if "holdings" in params:
            print("   holdings 写入顺序：仓位 → 持仓日 → 整份替换明细；"
                  "日期取基金现有的持仓日")
        source.connection.close()
        return 0

    result = process_admin_holdings(source, symbol, params, admin=True)
    print("== %s（stock_id=%s）" % (symbol, result["stock_id"]))
    if "holdings" in params:
        print("   holdings：%s" % ("已写入" if result["updated"] else "未写入（见下）"))
    if result["position"] is not None:
        print("   fundposition：%s" % result["position"])
    if not result["updated"] and result["position"] is None:
        print("   什么都没写 ——  holdings 取不到持仓日或编码串解析失败。")
    source.connection.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="持仓更新工具（QDII-LOF 混合 / QDII-ETF，默认 dry-run）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--symbol", help="只处理这一只基金")
    parser.add_argument("--all-qdii", action="store_true",
                        help="显式处理全部 qdiimix 族（与默认相同，便于 crontab 自明）")
    parser.add_argument("--apply", action="store_true",
                        help="真正写库/下载（默认只诊断不写）")
    parser.add_argument("--api", action="store_true", help="打印口径说明")
    parser.add_argument("--admin", metavar="SYMBOL",
                        help="管理员写入口（submitholdings.php），配 --holdings / --fundposition")
    parser.add_argument("--holdings", metavar="编码串",
                        help='去掉花括号的 JSON 对象体，如 \'"GLD":100,"SPY":50\'')
    parser.add_argument("--fundposition", metavar="值",
                        help="只写 fundposition（不碰明细）")
    args = parser.parse_args(argv)

    if args.api:
        return cmd_api(args)
    if args.admin:
        return cmd_admin(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
