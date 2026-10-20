# -*- coding: utf-8 -*-
"""更正 SZ161130（及其它 QDII 基金）的 ``fundest`` 历史记录，对齐生产端溢价表。

## 背景（2026-10-20，与 palmmicro.com/woody/res/sz161130cn.php 逐行对照）

生产端「基金溢价记录」9 行（10-19→09-30）每行都有官方EST/时间/误差/^NDX价格，
本地只零星几行且数值对不上。三个根因里前两个（数据缺口、``fundestcalc`` 的
QQQ 杠杆替代偏离）已另行修复；本脚本处理第三个：**fundest 记录本身缺失/错误**。

## fundest[D] 的口径（从 PHP 源码推出的时序）

* ``fundest[D]`` = "官方净值 nav[D] 到货前那一刻"对 nav[D] 的估算；
  写入时刻 = D 的美股收盘后 ≈ **D+1 的 04:00-04:04**（生产端时间列实测）；
* 那一刻生效的校准值是**上一个净值日 D'** 的：``cal' = est[D'] × fx[D'] / nav[D']``
  （``AdjustFactor`` 在 D' 的净值到货当晚 ~18:00 写入，见校准记录表18:00）；
* 代入 ``GetQdiiValue``：

      est_f(D) = p × (est[D] × fx[D] / cal') + (1-p) × nav[D']
               = nav[D'] × (p × (est[D]/est[D']) × (fx[D]/fx[D']) + (1-p))

  p = 仓位（LOF 0.95，ETF 1.0）。**est 用原始估值标的**（^NDX / ^GSPC ...），
  不做 QQQ/SPY 杠杆替代（与修偏后的 ``fundestcalc._est_symbol`` 一致）。

* 当 cal' 恰好用 D' 的同一批报价标定时，``est_f(D') = nav[D']`` 精确成立 ——
  这就是生产端 6/9 行官方EST=净值的原因；其余几行差 0.0003 级是报价噪声。

## 生产端显示目标（官方EST 列 = ``StockGetPriceDisplay`` 3 位小数去尾零）

    10-16→"4.37"  10-15→"4.479"  10-14→"4.48"   10-13→"4.572"
    10-12→"4.585"(误差-0.01%)  10-09→"4.618"  10-08→"4.598"
    09-30→"4.551"  09-29→"4.545"

时间列同样取生产端（fundest[D] 的 time = D+1 凌晨写入时刻）。

用法::

    python -B auto/_scratch/_fundest_premium_fix.py            # 预演（含逐行对照）
    python -B auto/_scratch/_fundest_premium_fix.py --apply    # 真写（含删 10-20 假校准行）
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pymysql  # noqa: E402

from auto.core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402

#: 只更正用户点名对齐的那只；--all 时扫所有有 fundest 记录的基金。
DEFAULT_FUND = "SZ161130"

#: 生产端 sz161130cn.php 溢价表 9 行的显示目标：nav 日期 -> (官方EST 显示, 时间)
#: （时间 = 生产端 fundest 记录的 time 列，即 D+1 凌晨的写入时刻）
PROD_TARGET = {
    "2026-10-16": ("4.37", "04:01:00"),
    "2026-10-15": ("4.479", "04:02:00"),
    "2026-10-14": ("4.48", "04:04:00"),
    "2026-10-13": ("4.572", "04:02:00"),
    "2026-10-12": ("4.585", "04:04:00"),
    "2026-10-09": ("4.618", "04:01:00"),
    "2026-10-08": ("4.598", "04:03:00"),
    "2026-09-30": ("4.551", "04:04:00"),
    "2026-09-29": ("4.545", "04:00:00"),
}

#: 待写日期 = 上表 + 尚未到货净值的 pending 日（10-19），pending 的 time 随 cron 槽位。
PENDING = {"2026-10-19": "04:01:00"}


def _display(value: float, precision: int = 3) -> str:
    """PHP ``StockGetPriceDisplay``：定精度 → 去尾零 → 去小数点。"""
    text = ("%%.%df" % precision) % value
    if precision > 0:
        text = text.rstrip("0")
    return text.rstrip(".")


def _fetch(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def _series(cur, table, stock_id):
    rows = _fetch(cur, "SELECT `date`, close FROM %s WHERE stock_id=%%s ORDER BY `date`" % table,
                  (stock_id,))
    return {str(r["date"]): float(r["close"]) for r in rows}


def main() -> int:
    apply = "--apply" in sys.argv
    only = DEFAULT_FUND
    if "--symbol" in sys.argv:
        only = sys.argv[sys.argv.index("--symbol") + 1]

    config = mysql_config_from_env(load_mysql_env(os.environ))
    conn = pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()

    row = _fetch(cur, "SELECT id FROM stock WHERE symbol=%s", (only,))
    if not row:
        print("库里没有符号", only)
        return 1
    stock_id = row[0]["id"]

    # 估值标的（**不做杠杆替代**）：映射表与 PHP QdiiGetEstSymbol 同源
    from auto.calc.fundcalc import (
        qdii_est_symbol, qdii_eu_est_symbol, qdii_hk_est_symbol, qdii_jp_est_symbol,
    )
    est_symbol = (qdii_est_symbol(only) or qdii_hk_est_symbol(only)
                  or qdii_jp_est_symbol(only) or qdii_eu_est_symbol(only))
    if not est_symbol:
        print(only, "没有 est 映射，无法重算")
        return 1
    est_id = _fetch(cur, "SELECT id FROM stock WHERE symbol=%s", (est_symbol,))[0]["id"]

    # 仓位：fundposition 表**没有 stock_id 列**（id 即 stock_id，close 即仓位）
    rows = _fetch(cur, "SELECT close FROM fundposition WHERE id=%s", (stock_id,))
    position = float(rows[0]["close"]) if rows and rows[0]["close"] not in (None, "") else 0.95

    nav = _series(cur, "netvaluehistory", stock_id)
    est = _series(cur, "stockhistory", est_id)
    fx = _series(cur, "netvaluehistory",
                 _fetch(cur, "SELECT id FROM stock WHERE symbol='USCNY'")[0]["id"])

    # 估值标的自身也可能缺行（指数历史），缺就报出来
    print("基金 %s（id=%s） 估值标的 %s（id=%s） 仓位 %.2f" % (only, stock_id, est_symbol, est_id, position))
    print("=" * 100)

    nav_dates = sorted(d for d in nav if d >= "2026-09-25")
    writes = []
    for date, target in list(PROD_TARGET.items()) + [(d, None) for d in PENDING]:
        if date not in nav and date not in PENDING:
            print("跳过 %s：净值表没有这一天" % date)
            continue
        prev = [d for d in nav_dates if d < date]
        if not prev:
            print("跳过 %s：找不到前一个净值日" % date)
            continue
        d_prev = prev[-1]
        try:
            ratio = (est[date] / est[d_prev]) * (fx[date] / fx[d_prev])
        except KeyError as exc:
            print("跳过 %s：%s 缺行" % (date, exc))
            continue
        except ZeroDivisionError:
            print("跳过 %s：前值为 0" % date)
            continue
        value = nav[d_prev] * (position * ratio + (1.0 - position))
        time_text = (PENDING.get(date) or (target[1] if target else "04:01:00"))
        line = "%s  前净值日 %s  est比 %.6f  ->  %.6f" % (date, d_prev, ratio, value)
        if target:
            line += "   显示 %-6s 目标 %-6s %s" % (_display(value), target[0],
                                                  "OK" if _display(value) == target[0] else "**差异**")
        print(line)
        writes.append((date, value, time_text))

    print("=" * 100)
    if not apply:
        print("这是预演。真正执行请加 --apply。")
        conn.close()
        return 0

    # 1) 清掉 10-20 那条"值同 10-16"的假校准行（写入口是 insert_fund_calibration
    #    误用行情日期，已另行修代码）。
    cur.execute("DELETE FROM calibrationhistory WHERE stock_id=%s AND `date`='2026-10-20'",
                (stock_id,))
    print("删除 calibrationhistory 2026-10-20 假行：", cur.rowcount)

    # 2) fundest 覆写：已有行改值与时间，缺的补上（id 自增）。
    for date, value, time_text in writes:
        cur.execute("SELECT id FROM fundest WHERE stock_id=%s AND `date`=%s", (stock_id, date))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE fundest SET close=%s, `time`=%s WHERE id=%s",
                        (value, time_text, row["id"]))
            print("更新 fundest %s -> %.6f @ %s" % (date, value, time_text))
        else:
            cur.execute("INSERT INTO fundest (stock_id, `date`, close, `time`) VALUES (%s,%s,%s,%s)",
                        (stock_id, date, value, time_text))
            print("新增 fundest %s -> %.6f @ %s" % (date, value, time_text))
    conn.commit()
    conn.close()
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
