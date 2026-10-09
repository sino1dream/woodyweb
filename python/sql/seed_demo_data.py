"""种子数据求解与写入：让种子数据在 `est_net_value` 下自洽。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B sql\\seed_demo_data.py            # 求解后写入
    python -B sql\\seed_demo_data.py --clear    # 清掉脚本写入的行

为什么要在写库前"求解校准值"？

    `stockdata.get_stock_hedge()` 是单位换算因子：
        hedge = (基金校准值 / 仓位) / (中间层校准值 / 中间层仓位)
    生产数据里它约等于 1，但它是从 calibrationhistory 现算的。手工拍校准值很容易
    让这个因子偏离 1，估值就被整体放大。所以这里不拍数字：
      * 中间层（QQQ/SPY）用「指数点位」做 calibration，netvalue 用「每份标的净值」；
      * 基金层先写一个候选值，用真实的 StockDataArrayBuilder + PalmmicroFundCalculator
        反复迭代，直到 hedge 因子落在 1 附近、且估值约等于该基金官方净值。

    结果是一组"自洽但非真实行情"的种子数据，只用于验证链路。
"""

from __future__ import annotations

import io
import os
import sys

# 注意：不能在这里包 stdout。本模块会被 sync_demo_netvalue.py 导入，
# 导入时替换 sys.stdout 会把调用方的输出流关掉（已踩过）。UTF-8 包装放在
# `if __name__ == "__main__"` 分支里。
if __name__ == "__main__" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SQL_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SQL_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from stockdata import (  # noqa: E402
    MySqlFundDataSource,
    StockDataArrayBuilder,
    load_mysql_env,
    mysql_config_from_env,
)
from web.bootstrap import install_paths  # noqa: E402

install_paths()

from fundcalc import PalmmicroFundCalculator  # noqa: E402

SEED_DATE = "2026-01-02"

# 符号 -> 显示名。覆盖两只 LOF、一只 ETF，以及估值链路里的中间层与指数层
#
# ⚠️ 这里曾经把 SZ161130 写成"易方达标普信息科技LOF"，**是错的**：
#    SZ161130 = 易方达纳斯达克100（跟踪 QQQ / 纳斯达克100）
#    SZ161128 = 易方达标普信息科技（另一只基金，与本演示无关）
# 现已按权威源核对（新浪 f_ 接口 / 深交所 CATALOGID=1785_child）：
#    161130 -> 易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)
#    161125 -> 易方达标普500指数(QDII-LOF)A(人民币份额)
#    513100 -> 国泰纳斯达克100(QDII-ETF)
# 下面的短名与分组列表的展示风格保持一致，与 fundpair 的配对也相符
# （SZ161130 跟踪 QQQ 即纳斯达克100）。
STOCKS = [
    ("SZ161130", "易方达纳斯达克100LOF"),
    ("SZ161125", "易方达标普500LOF"),
    ("SH513100", "国泰纳斯达克100ETF"),
    ("QQQ", "Invesco QQQ Trust"),
    ("SPY", "SPDR S&P 500 ETF"),
    ("^NDX", "纳斯达克100指数"),
    ("^GSPC", "标普500指数"),
    # 汇率符号。对应 PHP 的 CnyReference 用的 USCNY / HKCNY / EUCNY / JPCNY。
    # 为什么必须有：fundcalc 的估值公式里 `est_value * cny` 这一项要靠它把
    # 美元计价的中间层换成人民币；库里没有这些符号时 cny 会退化成 1.0，
    # 等于把美元当人民币用（QDII 估值差 7 倍左右）。
    # 这里的初值是占位，真实汇率由 auto/forexcli.py 抓取覆盖。
    ("USCNY", "美元人民币"),
    ("HKCNY", "港元人民币"),
    ("EUCNY", "欧元人民币"),
    ("JPCNY", "日元人民币"),
]

# 汇率初值。**注意**：这些只是占位，会被 auto/forexcli.py 抓到的真实汇率覆盖。
#
# 曾经这里填 7.1，被当成"2026-01-02 的真实汇率"用到了估值里 —— 而 7.1 并不是
# 任何一天的真实数据。所以：
#   * 初值按真实量级给（USD/CNY ≈ 6.7），不要再随手写 7.1 这种整数；
#   * 更靠谱的做法是先跑 `python -B auto\forexcli.py` 抓真实汇率，再跑本脚本。
FOREX_PLACEHOLDER = {
    "USCNY": 6.70,
    "HKCNY": 0.854,
    "EUCNY": 7.54,
    "JPCNY": 0.0425,
}

POSITIONS = {
    "SZ161130": 0.95,
    # SZ161125 也是 LOF，官网明写"仓位估算值使用 0.95"
    # （https://palmmicro.com/woody/res/sz161125cn.php）。
    # 这里曾写 1.0，属于种子数据错误 —— 与 PHP GetDefaultPosition() 的
    # ``IsLofA() ? 0.95 : 1.0`` 矛盾，也让估值口径和官网对不上。
    "SZ161125": 0.95,
    # SH513100 是 **ETF**（上海 51 号段），不是 LOF，所以默认仓位是 1.0。
    #
    # 这里曾写 0.98，是错的。反推验证：生产端 sh513100cn.php 校准记录显示
    #   2026-09-29  校准值 100358.446  净值 2.0379  对冲值 100358
    # 按 hedge = 校准值 / 仓位 反推 -> 仓位 = 100358.446 / 100358 = 1.000004，
    # 即生产用的就是 1.0。其余三只反推也都是 0.95（LOF）/ 1.0（ETF），
    # 与 GetDefaultPosition() 完全吻合。
    "SH513100": 1.00,
}

# 基金官方净值（netvaluehistory.close）。
#
# **必须是真实净值**：迁移「历史数据页面」（净值记录 / 基金溢价记录 /
# 净值价格比较）之后，这张表的内容会直接显示给用户，合成的占位值会被当成
# 真实历史 —— 实测旧占位值（1.5234）让「基金溢价记录」算出 214% 的溢价。
#
# 下面三个值取自生产端（官网可验证）：
#   https://palmmicro.com/woody/res/sz161130cn.php  校准记录 2026-09-29 净值 4.5452
#   https://palmmicro.com/woody/res/sz161125cn.php  校准记录 2026-09-29 净值 3.1328
#   三只基金在题目文档里也出现过：4.5452 / 3.1328 / 2.0379
NET_VALUES = {
    "SZ161130": 4.5452,
    "SZ161125": 3.1328,
    "SH513100": 2.0379,
}

# 中间层与指数层：calibration = 指数点位（也是传 source 时的尺度），
# netvalue = 每份标的净值。两者取同一尺度，估值才会随 source 线性变化。
#
# 关键点：`est_net_value` 在传了 source 时，会用「中间层净值」去反算 hedge_value，
# 所以中间层 netvalue 必须与 source 的尺度一致（source 给的是 QQQ 价格 ~560 这种）。
# 若把 netvalue 写成 0.42 这类"每份净值"，同一个 source 会被放大上千倍。
#
# **注意**：这里写的是 ``SEED_DATE``（2026-01-02，即"原点"）那一行。
# 真实行情行由 ``python/sql/`` 下那几个按实际日期写入的脚本负责，
# 不要为了对齐生产端的"真实净值"而改这里的数 —— 两者是不同日期的两行。
# 自动校准（``autocalibration.py``）对归一化尺度做了显式换算，见那里的说明。
MARKET_LAYERS = {
    "QQQ": {"index": "^NDX", "calibration": 560.0, "netvalue": 1.0},
    "SPY": {"index": "^GSPC", "calibration": 700.0, "netvalue": 1.0},
    "^NDX": {"index": "hf_ES", "calibration": 21000.0, "netvalue": 4.0},
    "^GSPC": {"index": "hf_ES", "calibration": 5900.0, "netvalue": 4.0},
}

# 基金 -> 跟踪的中间层符号
FUND_PAIRS = [
    ("SZ161130", "QQQ"),
    ("SZ161125", "SPY"),
    ("SH513100", "QQQ"),
]

DATES = dict((symbol, SEED_DATE) for symbol, _name in STOCKS)

# 行情历史（stockhistory：date, close, volume, adjclose）。
#
# **为什么要给 09-29 也留一行**：QDII 的「基金溢价记录」「净值价格比较」
# 都要把**当天的行情**和**前一交易日的净值**配起来。历史表里只存交易日，
# "前一交易日"是靠 `WHERE date < X ORDER BY date DESC LIMIT 1` 找的 ——
# 如果 09-29 没有行情行，这两个页面就一行都显示不出来（实测就是空表）。
#
# 价格与昨收取自生产端页面（官网可验证）：
#   SZ161130 4.796（昨收 4.727） / SZ161125 3.275（昨收 3.240）
#   SH513100 2.352（昨收 2.324）
HISTORY = [
    ("SZ161130", "2026-09-29", 4.727000, 5800000, 4.727000),
    ("SZ161130", "2026-09-30", 4.796000, 6369249, 4.796000),
    ("SZ161125", "2026-09-29", 3.240000, 3100000, 3.240000),
    ("SZ161125", "2026-09-30", 3.275000, 3380000, 3.275000),
    ("SH513100", "2026-09-29", 2.324000, 12000000, 2.324000),
    ("SH513100", "2026-09-30", 2.352000, 13500000, 2.352000),
    # 中间层的"净值"在本项目里是归一化的 1.0（见 MARKET_LAYERS 的说明），
    # 所以这里也写 1.0，保持一致；它们的真实价格在 realtimequote/estquote 里。
    ("QQQ", "2026-09-29", 1.0, 41000000, 1.0),
    ("QQQ", "2026-09-30", 1.0, 42000000, 1.0),
    ("SPY", "2026-09-29", 1.0, 55000000, 1.0),
    ("SPY", "2026-09-30", 1.0, 56000000, 1.0),
]


# ---------------------------------------------------------------------------
# 写库
# ---------------------------------------------------------------------------
def upsert_stock(cursor, symbol, name):
    cursor.execute(
        "INSERT INTO stock (symbol, name) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE name = VALUES(name)",
        (symbol, name),
    )
    cursor.execute("SELECT id FROM stock WHERE symbol = %s LIMIT 1", (symbol,))
    return cursor.fetchone()["id"]


def upsert_simple(cursor, table, key_id, value):
    cursor.execute(
        "INSERT INTO `%s` (id, `close`) VALUES (%%s, %%s) "
        "ON DUPLICATE KEY UPDATE `close` = VALUES(`close`)" % table,
        (key_id, value),
    )


def upsert_daily(cursor, table, stock_id, value, extra=None):
    columns = ["stock_id", "date", "close"]
    values = [stock_id, SEED_DATE, value]
    updates = ["close = VALUES(close)"]
    for column, column_value in (extra or {}).items():
        columns.append(column)
        values.append(column_value)
        updates.append("`%s` = VALUES(`%s`)" % (column, column))
    cursor.execute(
        "INSERT INTO `%s` (%s) VALUES (%s) ON DUPLICATE KEY UPDATE %s"
        % (table, ", ".join("`%s`" % c for c in columns), ", ".join(["%s"] * len(columns)), ", ".join(updates)),
        tuple(values),
    )


def write_calibration(cursor, stock_id, value):
    upsert_daily(cursor, "calibrationhistory", stock_id, value, {"time": "15:00:00", "num": 1})


def write_all(cursor, ids):
    """写入除"基金校准值"之外的全部数据。"""
    for symbol, layer in MARKET_LAYERS.items():
        write_calibration(cursor, ids[symbol], layer["calibration"])
    for symbol, value in FUND_PAIRS:
        cursor.execute(
            "INSERT INTO fundpair (id, stock_id) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE stock_id = VALUES(stock_id)",
            (ids[symbol], ids[value]),
        )
    for symbol, date in DATES.items():
        for table in ("holdingsdate", "stockhistorydate"):
            cursor.execute(
                "INSERT INTO `%s` (id, date) VALUES (%%s, %%s) "
                "ON DUPLICATE KEY UPDATE date = VALUES(date)" % table,
                (ids[symbol], date),
            )
    for symbol, date, close, volume, adjclose in HISTORY:
        cursor.execute(
            "INSERT INTO stockhistory (stock_id, date, close, volume, adjclose) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE close = VALUES(close), volume = VALUES(volume), adjclose = VALUES(adjclose)",
            (ids[symbol], date, close, volume, adjclose),
        )
    # 汇率占位值：写进 netvaluehistory，因为 _get_default_cny_est() 就是从那里
    # 按官方日期读汇率的（对应 PHP CnyReference 的 GetClose）。
    for symbol, value in FOREX_PLACEHOLDER.items():
        if symbol in ids:
            upsert_daily(cursor, "netvaluehistory", ids[symbol], value)


# ---------------------------------------------------------------------------
# 求解基金校准值，使估值收敛到官方净值
# ---------------------------------------------------------------------------
def probe(source, symbol):
    """返回 (估值, hedge 因子, config)。"""
    data = StockDataArrayBuilder(source).build(symbol)
    own = data.get(symbol, {})
    if not own:
        return None, None, data
    estimate = PalmmicroFundCalculator(data).est_net_value(symbol)
    return estimate, own.get("hedge"), data


def solve(source, cursor, ids, symbol, netvalue, tolerance=1e-4, max_rounds=60):
    """迭代求解基金校准值。

    估值与基金校准值近似成反比（校准越大估值越小），所以用"按比例缩放"迭代，
    比盲目二分收敛快；每轮都通过真实构建器重新取数，保证用的是线上同一条路径。
    """
    calibration = netvalue  # 初值：与净值同量级
    estimate = None
    for _round in range(max_rounds):
        write_calibration(cursor, ids[symbol], calibration)
        cursor.connection.commit()
        estimate, hedge, _data = probe(source, symbol)
        if estimate is None or estimate <= 0:
            calibration = calibration / 2.0 if calibration > 1e-6 else 1.0
            continue
        relative = abs(estimate - netvalue) / netvalue
        if relative < tolerance:
            break
        # 估值 ~ k / calibration  =>  新校准 = 旧校准 * 估值 / 目标
        calibration = calibration * estimate / netvalue
    return calibration, estimate, hedge


def clear(cursor, ids):
    stock_ids = list(ids.values())
    if not stock_ids:
        return
    placeholders = ", ".join(["%s"] * len(stock_ids))
    for table in ("calibrationhistory", "netvaluehistory", "fundposition",
                  "lastcalibration", "holdingsdate", "stockhistorydate", "stockhistory"):
        cursor.execute("DELETE FROM `%s` WHERE stock_id IN (%s)" % (table, placeholders), tuple(stock_ids))
    for table in ("fundposition", "lastcalibration"):
        cursor.execute("DELETE FROM `%s` WHERE id IN (%s)" % (table, placeholders), tuple(stock_ids))
    cursor.execute(
        "DELETE FROM fundpair WHERE id IN (%s) OR stock_id IN (%s)" % (placeholders, placeholders),
        tuple(stock_ids) + tuple(stock_ids),
    )
    cursor.execute("DELETE FROM stock WHERE id IN (%s)" % placeholders, tuple(stock_ids))


def main() -> int:
    do_clear = "--clear" in sys.argv
    config = mysql_config_from_env(load_mysql_env(os.environ))
    print("目标库：%s@%s:%s/%s" % (config["user"], config["host"], config["port"], config["database"]))

    import pymysql

    connection = pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor,
    )
    source = MySqlFundDataSource(connection)

    try:
        with connection.cursor() as cursor:
            ids = {}
            for symbol, _name in STOCKS:
                cursor.execute("SELECT id FROM stock WHERE symbol = %s LIMIT 1", (symbol,))
                row = cursor.fetchone()
                if row:
                    ids[symbol] = row["id"]

            if do_clear:
                clear(cursor, ids)
                connection.commit()
                print("已清除种子数据（%d 个符号）。" % len(ids))
                return 0

            # 1) 主表与基础数据
            for symbol, name in STOCKS:
                ids[symbol] = upsert_stock(cursor, symbol, name)
            for symbol, position in POSITIONS.items():
                upsert_simple(cursor, "fundposition", ids[symbol], position)
            for symbol, value in NET_VALUES.items():
                upsert_daily(cursor, "netvaluehistory", ids[symbol], value)
            for symbol in NET_VALUES:
                upsert_simple(cursor, "lastcalibration", ids[symbol], NET_VALUES[symbol])
            # 中间层与指数层的净值
            for symbol, layer in MARKET_LAYERS.items():
                upsert_daily(cursor, "netvaluehistory", ids[symbol], layer["netvalue"])
            write_all(cursor, ids)
            connection.commit()
            print("基础数据已写入（%d 个符号）。" % len(STOCKS))

            # 2) 求解基金校准值
            print("\n求解基金校准值（让估值收敛到官方净值）：")
            print("%-10s %-12s %-16s %-12s %s" % ("基金", "目标净值", "求得校准值", "实际估值", "hedge 因子"))
            for symbol, hedge_symbol in FUND_PAIRS:
                netvalue = NET_VALUES[symbol]
                calibration, estimate, hedge = solve(source, cursor, ids, symbol, netvalue)
                print("%-10s %-12.4f %-16.4f %-12.6f %s" % (
                    symbol, netvalue, calibration, estimate, hedge))

            connection.commit()
    finally:
        connection.close()

    print("\n完成。接着验证：")
    print("  python -B sql\\check_schema.py")
    print("  python -B sql\\verify_pages.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
