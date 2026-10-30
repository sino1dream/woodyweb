"""按符号抓历史日线并落库（``woody/res/submithistory.php``）。

## PHP 那一页在做什么

``_AdminHistoryAccount::AdminProcess()`` 只有一句 ``UpdateYahooHistoryChart($ref)``。
真正的逻辑在 ``php/stock/yahoostock.php:406``，四步：

1. **水位线**：``StockHistoryDateSql::ReadDate($stockId) == $ref->GetDate()``
   就直接返回 —— 今天抓过了，别再抓；
2. 抓 Yahoo chart JSON（``range=2y&interval=1d``），取
   ``timestamp`` / ``quote[0].{open,high,low,close,volume}`` / ``adjclose[0].adjclose``；
3. 逐行过四道闸门写 ``stockhistory``：
   * 早于 2014-01-01 → 跳过（``OldestYMD::IsTooOld``）；
   * 与前一条**同一天** → 跳过（期货那种 23 小时连续数据会重复日期）；
   * 收盘价是 ``'-'`` / ``'null'`` / 0 → 跳过（Yahoo 用 ``-`` 表示缺失）；
   * 成交量是 0 → 跳过（假期）；
   写完再 ``DeleteByZeroVolume($strStockId)`` **无条件**清一遍 0 成交量的行；
4. ``StockHistoryDateSql::WriteDate($stockId, $ref->GetDate())`` 推水位线，
   ``unlinkConfigFile($symbol)`` 让参考对象的缓存失效。

## 一处**必须**的换源：Yahoo → 新浪

Yahoo 的 chart 接口对本机**全面 403**（``query1`` / ``query2`` 都试过，
带 UA 也一样，2026-10-30 复测仍 403），照原样迁移一行数据也取不到。
``updater/indexhistory.py`` 早就为"补指数历史"做了同一件事：改用新浪的
``US_MinKService.getDailyK``，实测 ``^NDX 2026-10-09 = 30883.15`` 与生产端
``sz161130cn.php`` 参考数据表逐字一致。本模块**直接复用**它
（:func:`updater.indexhistory.fetch_daily_history`），不再写第二份抓取代码。

换源带来的字段差异：

======================  ==========================================
Yahoo 有的               新浪有的
======================  ==========================================
``open`` / ``high`` / ``low``  ——（不写库，PHP 只用来打日志）
``close``                  ``c``
``volume``                 ``v``
``adjclose``               ——（**没有**复权列）
======================  ==========================================

``adjclose`` 取 ``close``：指数与 ETF 的日线本来就按复权价发布，项目里
``stockhistory.adjclose`` 对指数就是这么用的（``dbtools/seed_index_history.py``
同一个口径）。**这是与 PHP 的唯一数据口径差异**，已记在 README。

## 另外两处偏离

1. **行情日期由调用方给**。PHP 用 ``$ref->GetDate()``（参考对象加载出来的
   最新行情日）；本模块收 ``quote_date`` 参数，由路由用
   ``MysqlReference(symbol).get_date()`` 取。取不到就**拒绝执行** ——
   PHP 在那种情况下会 ``WriteDate($id, '')``，把水位线写成 ``0000-00-00``。
2. **不删 config 缓存文件**。``unlinkConfigFile()`` 删的是
   ``debug/config/<符号>.txt``，那是 PHP 参考对象的磁盘缓存；本项目的参考对象
   每次都从库里现读，没有这个文件可删。照抄一句 ``unlink`` 只会删到别人的东西。
"""

from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, List, Optional

from sql.historytable import (
    MIN_FLOAT_VAL,
    StockHistoryDateSql,
    StockHistorySql,
    mysql_round,
)
from updater import indexhistory

#: ``OldestYMD`` 的界限（``php/class/year_month_day.php:164``）。
OLDEST_VALID_DATE = datetime.date(2014, 1, 1)


def update_history_chart(
    source: Any,
    symbol: str,
    quote_date: str,
    *,
    fetch: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    today: Any = None,
) -> Dict[str, Any]:
    """对应 ``UpdateYahooHistoryChart($ref)``。返回一份"干了什么"的说明。

    ``quote_date`` 就是 PHP 的 ``$ref->GetDate()`` —— 水位线要比的那个日期。
    ``fetch`` 是抓取函数（默认新浪日线），测试注入用。

    返回 ``{"symbol", "source_date", "rows", "written", "zero_volume_deleted",
    "skipped", "reason"}``。``reason`` 非空表示**没写任何东西**，取值：

    * ``no_symbol`` / ``no_quote_date`` / ``already_updated`` / ``no_data``。
    """
    symbol = str(symbol).strip()
    result: Dict[str, Any] = {
        "symbol": symbol,
        "source_date": quote_date,
        "rows": 0,
        "written": 0,
        "zero_volume_deleted": 0,
        "skipped": {"too_old": 0, "duplicate": 0, "empty_close": 0, "zero_volume": 0,
                    "invalid_date": 0},
        "reason": None,
    }

    connection = getattr(source, "connection", source)
    stock_id = source.get_stock_id(symbol)
    if stock_id is None:
        result["reason"] = "stock 表里没有 %s 这个符号" % symbol
        return result
    if not quote_date:
        result["reason"] = (
            "取不到 %s 的行情日期（水位线没法比）—— 先让行情 daemon 刷一次"
            % symbol
        )
        return result

    date_sql = StockHistoryDateSql(connection)
    watermark = date_sql.read_date(stock_id)
    if _same_day(watermark, quote_date):
        result["reason"] = "already_updated"
        return result

    reader = fetch or indexhistory.fetch_daily_history
    rows = reader(symbol)
    if not rows:
        result["reason"] = "no_data"
        return result

    his_sql = StockHistorySql(connection)
    reference = _today(today)
    last_date = None
    written = 0
    for row in rows:
        date = row.get("date")
        moment = _to_date(date)
        if moment is None:
            continue
        if moment < OLDEST_VALID_DATE:
            result["skipped"]["too_old"] += 1
            continue
        if date == last_date:
            #: "future have continue data 23 hours a day" —— 期货那种连续行情
            #: 一天里会给好几条，日期重复的最后一条才作数（PHP 取第一条）
            result["skipped"]["duplicate"] += 1
            continue
        last_date = date

        close = row.get("close")
        if _is_zero(close):
            result["skipped"]["empty_close"] += 1
            continue
        volume = row.get("volume")
        if _is_zero(volume):
            result["skipped"]["zero_volume"] += 1
            continue
        if moment.weekday() >= 5 or moment > reference:
            result["skipped"]["invalid_date"] += 1
            continue

        result["rows"] += 1
        if his_sql.write_history(
            stock_id, date, mysql_round(close), volume, mysql_round(close)
        ):
            written += 1

    #: 无条件清 0 成交量（PHP 里包着它的 if 被注释掉了，见 sql 层 docstring）
    result["zero_volume_deleted"] = his_sql.delete_by_zero_volume(stock_id)
    date_sql.write_date(stock_id, quote_date)
    connection.commit()

    result["written"] = written
    return result


def describe(result: Dict[str, Any]) -> str:
    """把结果翻成一句给管理员看的话（路由的 flash 用）。"""
    symbol = result.get("symbol", "")
    reason = result.get("reason")
    if reason == "already_updated":
        return "%s：今天已经更新过（水位线 %s），没有重复抓。" % (
            symbol, result.get("source_date"))
    if reason == "no_data":
        return "%s：没抓到日线数据（新浪接口没返回可用行）。" % symbol
    if reason:
        return "%s：%s" % (symbol, reason)

    skipped = result.get("skipped") or {}
    parts = [
        "写入 %d 行" % result.get("written", 0),
        "清掉 %d 行 0 成交量" % result.get("zero_volume_deleted", 0),
    ]
    notes = []
    if skipped.get("empty_close"):
        notes.append("%d 行缺收盘价" % skipped["empty_close"])
    if skipped.get("zero_volume"):
        notes.append("%d 行成交量 0" % skipped["zero_volume"])
    if skipped.get("duplicate"):
        notes.append("%d 行日期重复" % skipped["duplicate"])
    if skipped.get("too_old"):
        notes.append("%d 行早于 2014" % skipped["too_old"])
    text = "%s：%s" % (symbol, "，".join(parts))
    if notes:
        text += "（跳过：" + "、".join(notes) + "）"
    return text


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _same_day(left: Any, right: Any) -> bool:
    """水位线比较。库里 ``date`` 列读出来是 ``datetime.date``，参数是字符串。"""
    if left is None:
        return False
    return _to_date(left) == _to_date(right)


def _to_date(value: Any) -> Optional[datetime.date]:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_zero(value: Any) -> bool:
    """``IsZeroString()``（``php/debug.php:16``）：``abs(floatval($v)) < MIN_FLOAT_VAL``。

    同时覆盖 PHP 那两个判断：``$strClose == '-'`` / ``== 'null'``
    （``floatval('-')`` 与 ``floatval('null')`` 都是 0）。
    """
    if value is None or value == "":
        return True
    try:
        return abs(float(value)) < MIN_FLOAT_VAL
    except (TypeError, ValueError):
        return True


def _today(value: Any) -> datetime.date:
    if value is None:
        return datetime.date.today()
    moment = _to_date(value)
    return moment or datetime.date.today()
