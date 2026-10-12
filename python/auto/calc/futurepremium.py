"""期货理论溢价 —— ``GetFutureInterestPremium()`` / ``RefGetFuturePremium()``。

对应 ``php/ui/smaparagraph.php:179``。

## 公式就三步

.. code-block:: php

    function GetFutureInterestPremium($fRate = 0.0500625, $strEndDate = '2025-03-21')
    {
        $end_ymd = new StringYMD($strEndDate);
        date_default_timezone_set('America/New_York');      // ← 全局副作用
        $now_ymd = GetNowYMD();
        $begin_ymd = new StringYMD($now_ymd->GetYMD());
        $iDay = ($end_ymd->GetTick() - $begin_ymd->GetTick()) / SECONDS_IN_DAY;
        return 1.0 + $fRate * $iDay / 365.0;
    }

即"年化利率 × 剩余天数 / 365 + 1"。三处要紧的地方：

1. **"今天"是纽约的今天**，不是服务器本地的今天。
   PHP 靠 ``date_default_timezone_set()`` 硬改进程时区，**并且没有恢复** ——
   这是它的一处隐性副作用（执行过一次之后整个请求剩下的部分都是美东时间）。
   本版不照抄这个副作用：Windows 上没有 ``time.tzset()``，
   ``stocksymbol.set_time_zone()`` 一调就抛错（项目里已有先例）。
   改成把时间点**显式折算**到 ``America/New_York``，见 :func:`_us_today`。

2. **天数差是"零点对零点"**，所以两边都要先归一到当天零点再相减。
   直接拿 ``datetime`` 相减会带时分秒，多出不到一天的小数 ——
   在 365 分之一的尺度上那是 0.003 的溢价误差，肉眼看不出但逐项对照会炸。

3. ``$fRate`` 来自 ``futurepremium`` 表的 ``close``，**单位是百分点**
   （``5.00625`` 表示 5.00625%），所以要 ``/ 100.0``。

## 回调里的两步舍入（``_callbackFutureSma``，``:200``）

.. code-block:: php

    $f = floatval($strEst) * RefGetFuturePremium($ref);
    $f = round(4.0 * $f) / 4.0;          // 先取到 0.25 的整数倍
    return strval(round($f, 2));         // 再保留两位小数

**PHP 的 ``round()`` 是四舍五入（half away from zero），Python 的 ``round()``
是银行家舍入（half to even）** —— ``round(0.125, 2)`` 在两边分别是
``0.13`` 和 ``0.12``。所以这里走 :func:`php_round`，不用内建的 ``round``。

这一步"先取 0.25 的倍数"是**期货价格的最小变动单位**：外盘指数期货
（NQ / ES）的报价步长就是 0.25 点，所以理论价格要落在步长上。
"""

from __future__ import annotations

import math
from typing import Any

#: PHP 里那两个默认参数（``0.0500625`` / ``'2025-03-21'``）。
DEFAULT_FUTURE_RATE = 0.0500625
DEFAULT_FUTURE_END_DATE = "2025-03-21"

#: ``GetFutureInterestPremium()`` 里 ``date_default_timezone_set()`` 的时区。
FUTURE_TIME_ZONE = "America/New_York"

#: 一年的天数。PHP 用的是 365，不是 365.25。
DAYS_IN_YEAR = 365.0

#: ``_callbackFutureSma`` 里那个"最小变动单位"（0.25 点）。
FUTURE_TICK_SIZE = 4.0


def php_round(value: Any, digits: int = 0) -> float:
    """``round()`` 的 PHP 语义：**四舍五入**（half away from zero）。

    Python 内建的 ``round()`` 是银行家舍入（half to even），两者在
    ``.5`` 边界上不一样：

    ==============  ============  ==================
    值              PHP            Python ``round``
    ==============  ============  ==================
    ``0.125``       ``0.13``      ``0.12``
    ``2.5``         ``3``         ``2``
    ``-2.5``        ``-3``        ``-2``
    ==============  ============  ==================

    这里按 PHP 的做法：**绝对值四舍五入后再恢复符号**。
    """
    number = float(value)
    factor = 10.0 ** int(digits)
    scaled = abs(number) * factor
    # 浮点表示误差：math.floor(2.4999999999999996 + 0.5) 才是 2 而不是 3
    rounded = math.floor(scaled + 0.5)
    return math.copysign(rounded / factor, number)


def get_future_interest_premium(rate: Any = DEFAULT_FUTURE_RATE,
                                end_date: Any = DEFAULT_FUTURE_END_DATE,
                                now: Any = None) -> float:
    """``GetFutureInterestPremium($fRate, $strEndDate)``。

    ``now`` 允许注入"现在"（测试要能固定日期）；不传就取**纽约当地的今天**。
    """
    days = _days_between(_us_today(now), end_date)
    return 1.0 + float(rate) * days / DAYS_IN_YEAR


def ref_get_future_premium(source: Any, ref: Any, now: Any = None) -> Any:
    """``RefGetFuturePremium($ref)``（``:189``）：读 ``futurepremium`` 表。

    .. code-block:: php

        $premium_sql = new FuturePremiumSql();
        if ($strClose = $premium_sql->GetCloseNow($strStockId))
            return GetFutureInterestPremium(floatval($strClose) / 100.0,
                                            $premium_sql->GetDateNow($strStockId));
        return false;

    取不到就返回 ``False``（而不是 0.0）—— 调用方是 ``if ($fPremium = ...)``，
    ``0.0`` 与 ``false`` 在 PHP 里都是假值，但返回 ``False`` 能让 Python 侧
    "没有这条记录"与"利率正好是 0"区分开。
    """
    stock_id = _stock_id_of(ref)
    if stock_id is None:
        return False

    close, date = _read_future_premium(source, stock_id)
    if close is None or not date:
        return False
    return get_future_interest_premium(close / 100.0, date, now=now)


def future_sma_value(ref: Any, premium: Any, est: Any) -> Any:
    """``_callbackFutureSma()`` 的数值部分（``:200``）。

    两步舍入：先取到 0.25 的整数倍（期货最小变动单位），再保留两位小数。
    ``premium`` 为假值（``False`` / ``0``）时返回 ``None`` —— 让调用方
    整段跳过，而不是拿 0 去乘出一个"看起来有数"的价格。
    """
    if premium is None or premium is False or float(premium or 0) == 0.0:
        return None
    if est is None or est is False or est == "":
        return None
    try:
        value = float(est) * float(premium)
    except (TypeError, ValueError):
        return None
    ticked = php_round(FUTURE_TICK_SIZE * value) / FUTURE_TICK_SIZE
    return php_round(ticked, 2)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _read_future_premium(source: Any, stock_id: Any) -> tuple:
    """读 ``futurepremium`` 最近一条，返回 ``(close, date)``。

    复用数据源的通用日记录读取（``stockdata._get_daily_record``）——
    它对应 PHP 的 ``DailyCloseSql``，正是 ``FuturePremiumSql`` 的父类。
    没有该表/该符号时返回 ``(None, None)``。
    """
    reader = getattr(source, "_get_daily_record", None)
    if not callable(reader):
        return None, None
    try:
        record = reader("futurepremium", stock_id)
    except Exception:
        # 表不存在（旧库）时不能让页面 500 —— 没有溢价就跳过那一段
        return None, None
    if record is None:
        return None, None
    return getattr(record, "close", None), getattr(record, "date", None)


def _stock_id_of(ref: Any) -> Any:
    """``$ref->GetStockId()``。"""
    for name in ("get_stock_id", "GetStockId"):
        method = getattr(ref, name, None)
        if callable(method):
            try:
                return method()
            except Exception:
                return None
    return None


def _us_today(now: Any = None) -> Any:
    """**纽约当地**的今天（``date`` 对象）。

    PHP 靠改进程时区拿到它；本版把 ``now`` 折算过去（见模块 docstring 第 1 条）。
    """
    import datetime

    if now is None:
        moment = datetime.datetime.now()
    elif isinstance(now, datetime.datetime):
        moment = now
    elif isinstance(now, datetime.date):
        moment = datetime.datetime(now.year, now.month, now.day)
    else:
        moment = datetime.datetime.strptime(str(now)[:10], "%Y-%m-%d")

    if moment.tzinfo is not None:
        moment = _as_us(moment.replace(tzinfo=None))
    return _as_us(moment).date()


def _as_us(moment: Any) -> Any:
    """把"墙上时间"挂上美东时区（会按日期选夏令时偏移）。"""
    from core import tzutil

    return tzutil.localize(moment, FUTURE_TIME_ZONE)


def _days_between(begin: Any, end_date: Any) -> float:
    """``($end_ymd->GetTick() - $begin_ymd->GetTick()) / SECONDS_IN_DAY``。

    ## 两边都要按**同一个**时区取零点

    PHP 的 ``GetTick()`` 是 ``mktime(0,0,0,...)``，落在当时的进程时区上。
    本版两边都取美东零点 —— 与 PHP 在 ``date_default_timezone_set()``
    之后的状态一致。

    这样算出来的天数**不一定是整数**：跨夏令时切换时是 ``68.0417`` 天
    （2026-10-11 → 2026-12-18 中间隔了 11 月的 DST 结束）。
    用"两个 date 直接相减"会得到整 68 天，与 PHP 差 0.04 ——
    在 365 分之一的尺度上是 0.0000055 的溢价差，逐项对照会炸。
    """
    import datetime

    if isinstance(end_date, datetime.datetime):
        end_day = end_date.date()
    elif isinstance(end_date, datetime.date):
        end_day = end_date
    else:
        text = str(end_date or "").strip()[:10].replace("/", "-")
        try:
            end_day = datetime.datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return 0.0

    begin_moment = _as_us(datetime.datetime(begin.year, begin.month, begin.day))
    end_moment = _as_us(datetime.datetime(end_day.year, end_day.month, end_day.day))
    return (end_moment - begin_moment).total_seconds() / 86400.0
