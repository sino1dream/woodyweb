"""期货与指数的配对、自动校准与实时 EST 取值。

对应 PHP ``php/stock/fundpairref.php`` 里的三段逻辑：

===============================  ==========================================
PHP                              本模块
===============================  ==========================================
``GetPairSymbol($strSymbol)``     :meth:`FuturesPairStore.get_future_symbol`
``CheckAdjustFactorTime()`        :func:`check_adjust_time`
``AverageCalibraion()``           :meth:`FuturesCalibrator.calibrate`
``CalcFactor()``                  :meth:`FuturesCalibrator.calc_factor`
===============================  ==========================================

## 为什么需要"时刻对齐"才校准

``CheckAdjustFactorTime()``（``php/stock/stockref.php:235``）要求期货与指数两个
行情的**日期与时刻完全相同**：

```php
if ($strDate != $etf_ref->GetDate())        return false;
if (GetHM($strTime) != $etf_ref->GetTimeHM())  return false;
return true;
```

含义：只有"两个市场同时收着盘"的那一瞬间，`期货价 / 指数` 才是干净的比值。
作者 2024-04-19 那篇文档说得很直接：

> 在美股交易时自动校准一下当天的 NQ 期货和 ^NDX 指数就行了。
> 原来的做法就是画蛇添足，代码现成到只需要注释掉一行。

校准值用 ``WriteDailyAverage()`` 累加平均 —— 同一天多次校准不会互相覆盖，
而是取平均（对应 PHP ``CalibrationSql::WriteDailyAverage()``）。
"""

from __future__ import annotations

from typing import Any

from calc.fundcalc import qdii_get_calibration
from sql.historytable import CalibrationSql, format_hm


class FuturesPairStore:
    """``futurespair`` 表的读写。

    表形状与 PHP ``PairSql`` 一致：``id`` 是"本符号"，``stock_id`` 是"配对的符号"。
    这里 ``id`` = **指数**（``^NDX``），``stock_id`` = **期货**（``hf_NQ``）。
    """

    def __init__(self, connection: Any):
        self.connection = connection

    def _fetchone(self, sql: str, params: tuple = ()) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get_future_id(self, index_id: Any) -> Any:
        """按指数 id 取期货 id，对应 PHP ``ReadPair()``。"""
        if index_id is None:
            return None
        row = self._fetchone("SELECT stock_id FROM futurespair WHERE id = %s LIMIT 1", (index_id,))
        return row["stock_id"] if row else None

    def get_future_symbol(self, index_id: Any) -> Any:
        """按指数 id 取期货符号，对应 PHP ``GetPairSymbol()``。"""
        future_id = self.get_future_id(index_id)
        if future_id is None:
            return None
        row = self._fetchone("SELECT symbol FROM stock WHERE id = %s LIMIT 1", (future_id,))
        return row["symbol"] if row else None


#: 换算出来的中间层值，与它已知价格之间允许的偏离（其余情形按"量级不对"处理）。
#: 0.5 是因为期货与 ETF 快照可能不是同一时刻抓的，盘中波动加时差留 50% 足够宽松。
MIDDLE_VALUE_TOLERANCE = 0.5

#: PHP ``IsExtendedMarket()`` 认可的常规交易时段（``GetHourMinute()`` 的整数形式）。
#: 早于此开盘、晚于此收盘都算"延长时段"，作者的要求是**只在常规时段校准**。
REGULAR_SESSION_OPEN_HM = 930
REGULAR_SESSION_CLOSE_HM = 1600


def _time_zone_of(symbol: Any) -> Any:
    """符号所属市场的时区；取不到返回 ``None``（调用方会跳过换算）。"""
    if not symbol:
        return None
    try:
        from core.stocksymbol import StockSymbol

        return StockSymbol(symbol).get_time_zone()
    except Exception:
        return None


def hour_minute(hhmm: str) -> int:
    """``"15:42"`` -> ``1542``，对应 PHP ``GetHourMinute()``。"""
    try:
        hour, minute = str(hhmm).split(":")[:2]
        return int(hour) * 100 + int(minute)
    except (TypeError, ValueError):
        return -1


def check_adjust_time(future_date: Any, future_time: Any,
                      index_date: Any, index_time: Any,
                      future_time_zone: Any = None,
                      index_time_zone: Any = None) -> bool:
    """对应 ``CheckAdjustFactorTime()``：日期与时刻都相同才允许校准。

    PHP 原文（``php/stock/stockref.php:235``）里**三个条件**，此前只实现了两条：

    ```php
    if ($etf_ref->HasData() == false)        return false;
    if ($etf_ref->IsExtendedMarket())        return false;     // ← 漏了这条
    $strTimeZone = $etf_ref->GetTimeZone();
    if ($this->GetTimeZone() == $strTimeZone) { 原样比 }
    else { 先把本符号的时刻换算到 $strTimeZone 再比 }        // ← 漏了这条
    if ($strDate != $etf_ref->GetDate())            return false;
    if (GetHM($strTime) != $etf_ref->GetTimeHM())   return false;
    return true;
    ```

    **为什么必须补中间那条**：``期货价 / 指数`` 只有在两个市场**同时开着常规盘**
    的那一瞬间才是干净的比值。盘前盘后的期货价含隔夜跳空、指数却没动，
    算出来的比值会把跳空误当成基差 —— 演示库里 hf_NQ 快照是纽约时间 03:42
    （盘前），配上昨天的 ^NDX 收盘，校准值会偏到离谱。
    """
    if not future_date or not index_date:
        return False

    # 时区不同时先把期货那一刻换算到指数的时区再比（PHP 的 ConvertTick）
    fd, ft = str(future_date), future_time
    if future_time_zone and index_time_zone and future_time_zone != index_time_zone:
        converted = convert_quote_datetime(future_date, future_time, future_time_zone, index_time_zone)
        if converted is None:
            return False
        fd, ft = converted

    if fd != str(index_date):
        return False
    future_hm = format_hm(ft)
    index_hm = format_hm(index_time)
    if not future_hm or not index_hm:
        return False
    if future_hm != index_hm:
        return False

    # IsExtendedMarket()：只在常规时段校准
    hhmm = hour_minute(index_hm)
    if hhmm < REGULAR_SESSION_OPEN_HM or hhmm > REGULAR_SESSION_CLOSE_HM:
        return False
    return True


def convert_quote_datetime(date: Any, time: Any, from_time_zone: str, to_time_zone: str):
    """把 ``(日期, 时刻)`` 从 ``from_time_zone`` 换到 ``to_time_zone``。

    对应 PHP 里先 ``strtotime()`` 再 ``DebugGetDate/DebugGetTime($iTime, $tz)``。
    换算失败返回 ``None``（宁可不校准，也不拿错刻度的值去算比值）。
    """
    if not date or time in (None, ""):
        return None
    text = "%s %s" % (date, format_hm(time) or "00:00")
    try:
        from ref.stockref import convert_date_time

        return convert_date_time(text, from_time_zone, to_time_zone)
    except Exception:
        return None


class FuturesCalibrator:
    """把"期货 / 指数"的比价写成校准值，并据此算实时 EST。

    ``source`` 是 ``FundDataSource``（要 ``get_stock_id`` / ``get_calibration_now``
    / ``get_net_value_close`` 等）；``store`` 是 :class:`FuturesPairStore`。
    """

    def __init__(self, source: Any, store: FuturesPairStore = None,
                 connection: Any = None):
        self.source = source
        self.connection = connection if connection is not None else getattr(source, "connection", None)
        self.store = store or (FuturesPairStore(self.connection) if self.connection else None)

    # -- 配对查询 ---------------------------------------------------------
    def future_symbol_of(self, index_symbol: str) -> Any:
        """给定指数符号（``^NDX``）取出对应的期货符号（``hf_NQ``）。

        先按 ``futurespair`` 查；**查不到时退回内置映射表** ——
        否则演示库里少一行配置就整条实时 EST 静默失效，很难查。
        """
        if self.store is not None:
            index_id = self.source.get_stock_id(index_symbol)
            symbol = self.store.get_future_symbol(index_id)
            if symbol:
                return symbol
        return FALLBACK_FUTURES.get(index_symbol)

    # -- 校准 -------------------------------------------------------------
    def calc_factor(self, future_price: Any, index_price: Any, cny: Any) -> Any:
        """对应 ``CalcFactor()``：``校准值 = 期货价 × 汇率 / 指数价``。

        与 ``QdiiGetCalibration()`` 同一个式子，只是"源值"换成期货价。

        ``cny`` 为 ``None`` 时按 **1.0**（不乘汇率）：PHP 里是
        ``if ($this->cny_ref)`` 才取汇率，而美股指数/期货这一对
        ``cny_ref`` 恒为 false（两端都不是 A 股，见
        ``fundpairref.php:14-43``）。曾经这里跟 :meth:`calc_factor` 的调用方
        一样把 ``None`` 当"缺参数"直接 return None，结果时刻对齐了也**永远写不进**
        校准值 —— 实时 EST 因此一直回落到中间层快照。
        """
        if future_price in (None, "") or index_price in (None, ""):
            return None
        if cny in (None, ""):
            cny = 1.0
        try:
            index_value = float(index_price)
        except (TypeError, ValueError):
            return None
        if abs(index_value) <= 0.0000005:
            return None
        try:
            return qdii_get_calibration(float(future_price), float(cny), index_value)
        except (TypeError, ValueError):
            return None

    def implied_index_price(self, proxy_symbol: str) -> Any:
        """由**中间层 ETF** 反推指数点位：``指数 = ETF价 / 校准值``。

        依据本项目实测出的恒等式（见 ``fundcalc`` 与 QQQ 的校准值）：

        ```text
        est_quote = est_quote_ref × cal_m        （校准口径）
        ^NDX 30807.93 / QQQ净值 749.3773 = 41.111 = QQQ 校准值
        ⇒ 指数点位 = ETF 价 / ETF 的校准值
        ```

        为什么需要它：``CheckAdjustFactorTime()`` 要求期货与**指数**时刻对齐，
        但指数（``^NDX``）在本项目里没有行情快照（点位是从新浪直接抓的、
        不落 ``stockhistory``）。用 ETF 反推就不需要那一刻对齐 ——
        只要 ETF 与期货的日期相同即可（见 :meth:`calibrate_from_proxy`）。
        """
        proxy_id = self.source.get_stock_id(proxy_symbol)
        if proxy_id is None:
            return None
        calibration = self._calibration_of(proxy_symbol)
        if not calibration:
            return None
        price = self._proxy_price(proxy_symbol)
        if price in (None, ""):
            return None
        try:
            return float(price) / float(calibration)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def _proxy_price(self, proxy_symbol: str) -> Any:
        """中间层 ETF 的最新价：优先实时快照，再退到行情收盘价。"""
        quote = self._proxy_quote(proxy_symbol)
        if quote is not None:
            price = getattr(quote, "price", None)
            if price not in (None, ""):
                return price
        stock_id = self.source.get_stock_id(proxy_symbol)
        if stock_id is None:
            return None
        reader = getattr(self.source, "get_latest_history_close", None)
        if reader is None:
            return None
        try:
            return reader(stock_id)
        except Exception:
            return None

    def calibrate_from_proxy(self, index_symbol: str, future_quote: Any,
                             proxy_symbol: str = None, apply: bool = False) -> Any:
        """用**中间层 ETF 反推指数**来做期货校准（不需要时刻对齐）。

        与 :meth:`calibrate` 的差别：那个要求期货与指数时刻严格相同（PHP 原逻辑），
        这个只要求**日期相同** —— 因为指数点位是从 ETF 反推的。

        条件：

        * 期货有行情、日期非空；
        * 能反推出指数点位（ETF 有报价 + 有校准值）。

        返回校准值；条件不足返回 ``None``。
        """
        if future_quote is None:
            return None
        proxy = proxy_symbol or self.proxy_symbol_of(index_symbol)
        if not proxy:
            return None
        index_price = self.implied_index_price(proxy)
        if index_price is None:
            return None

        # 日期必须一致（否则是拿今天的期货配昨天的指数）
        proxy_date = self._proxy_date(proxy)
        future_date = getattr(future_quote, "date", None)
        if proxy_date and future_date and str(proxy_date) != str(future_date):
            return None

        factor = self.calc_factor(getattr(future_quote, "price", None), index_price, 1.0)
        if factor is None:
            return None
        if not apply:
            return factor

        index_id = self.source.get_stock_id(index_symbol)
        if index_id is None or self.connection is None:
            return factor
        table = CalibrationSql(self.connection)
        date = future_date or proxy_date
        existing = table.get_record(index_id, date)
        if existing is None:
            self._insert_calibration(index_id, date, factor, getattr(future_quote, "time", None))
        else:
            self._update_average(existing, factor)
        return factor

    def _proxy_date(self, proxy_symbol: str) -> Any:
        """中间层 ETF 最新数据的日期。

        优先取**快照**的 ``quote_date``（``get_stored_quote_date``）——
        期货校准要求"期货与指数同日"，而快照才是"今天"的行情；
        ``stockhistory`` 可能还没落当天数据。
        """
        reader = getattr(self.source, "get_stored_quote_date", None)
        if reader is not None:
            try:
                value = reader(proxy_symbol)
            except Exception:
                value = None
            if value:
                return value
        quote = self._proxy_quote(proxy_symbol)
        if quote is not None and getattr(quote, "date", None):
            return quote.date
        stock_id = self.source.get_stock_id(proxy_symbol)
        if stock_id is None:
            return None
        for name in ("get_latest_net_value_date", "get_latest_history_date"):
            reader = getattr(self.source, name, None)
            if reader is None:
                continue
            try:
                value = reader(stock_id)
            except Exception:
                value = None
            if value:
                return value
        return None

    def _proxy_quote(self, proxy_symbol: str) -> Any:
        """中间层 ETF 的快照（``RecentQuote``），没有返回 ``None``。"""
        reader = getattr(self.source, "get_stored_quote", None)
        if reader is None:
            return None
        try:
            return reader(proxy_symbol)
        except Exception:
            return None

    def proxy_symbol_of(self, index_symbol: str) -> Any:
        """指数对应的**中间层 ETF**（``^NDX`` → ``QQQ``）。

        查 ``fundpair``：该表的列语义（与 PHP ``PairSql`` 一致）是
        ``id`` = 基金/ETF，``stock_id`` = 它跟踪的标的。
        所以反过来找"跟踪这个指数的 ETF"就是 ``WHERE stock_id = 指数``，
        取那一行的 ``id``。

        查不到时用内置映射 :data:`FALLBACK_PROXIES`。
        """
        connection = self.connection
        if connection is not None:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT s.symbol FROM fundpair fp "
                    "JOIN stock s ON s.id = fp.id "
                    "WHERE fp.stock_id = (SELECT id FROM stock WHERE symbol = %s) LIMIT 1",
                    (index_symbol,),
                )
                row = cursor.fetchone()
                if row and row["symbol"]:
                    return str(row["symbol"])
            except Exception:
                pass
            finally:
                cursor.close()
        return FALLBACK_PROXIES.get(index_symbol)

    def calibrate(self, index_symbol: str, future_quote: Any, index_quote: Any,
                  cny: Any, apply: bool = False) -> Any:
        """时刻对齐时写校准值，对应 ``AverageCalibraion()``。

        ``apply=False``（默认）只算出校准值、不写库 —— 便于先核对。
        ``apply=True`` 时按**平均**写入 ``calibrationhistory``（同一天多次校准取平均，
        对应 PHP ``WriteDailyAverage()``）。

        返回算出的校准值；条件不满足返回 ``None``。
        """
        if future_quote is None or index_quote is None:
            return None
        # 时区不同时先换算到指数的时区再比，与 PHP 的 ConvertTick 一致
        index_tz = _time_zone_of(index_symbol)
        future_tz = _time_zone_of(getattr(future_quote, "symbol", None))
        if not check_adjust_time(
            getattr(future_quote, "date", None), getattr(future_quote, "time", None),
            getattr(index_quote, "date", None), getattr(index_quote, "time", None),
            future_time_zone=future_tz, index_time_zone=index_tz,
        ):
            return None

        factor = self.calc_factor(
            getattr(future_quote, "price", None),
            getattr(index_quote, "price", None),
            cny,
        )
        if factor is None:
            return None
        if not apply:
            return factor

        index_id = self.source.get_stock_id(index_symbol)
        if index_id is None or self.connection is None:
            return factor
        table = CalibrationSql(self.connection)
        existing = table.get_record(index_id, getattr(future_quote, "date"))
        if existing is None:
            self._insert_calibration(index_id, getattr(future_quote, "date"), factor,
                                     getattr(future_quote, "time"))
        else:
            self._update_average(existing, factor)
        return factor

    def _insert_calibration(self, stock_id: Any, date: Any, value: Any, time: Any) -> None:
        """插入一条校准记录（``time``/``num`` 是 NOT NULL 且无默认值，必须显式给）。"""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO calibrationhistory (stock_id, `date`, close, `time`, num) "
                "VALUES (%s, %s, %s, %s, 1)",
                (stock_id, date, value, self._normalize_time(time)),
            )
            commit = getattr(self.connection, "commit", None)
            if commit is not None:
                commit()
        finally:
            cursor.close()

    def _update_average(self, existing: dict, value: Any) -> None:
        """按平均更新，对应 PHP ``WriteDailyAverage()``。

        PHP 原文（``php/sql/sqldailytime.php:66``）：

        ```php
        $iNum = intval($record['num']);
        $fTotal = $iNum * $fOldAvg + $fVal;
        $iNum ++;
        return $this->UpdateDailyAverage($record['id'], strval($fTotal/$iNum), $iNum);
        ```
        """
        try:
            old_average = float(existing["close"])
            new_value = float(value)
        except (TypeError, ValueError, KeyError):
            return
        if abs(old_average - new_value) <= 0.0000005:
            # 与旧值相同就不动，避免 num 无意义地增长
            return
        count = int(existing.get("num") or 1)
        total = count * old_average + new_value
        count += 1
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "UPDATE calibrationhistory SET close = %s, num = %s WHERE id = %s",
                (total / count, count, existing["id"]),
            )
            commit = getattr(self.connection, "commit", None)
            if commit is not None:
                commit()
        finally:
            cursor.close()

    @staticmethod
    def _normalize_time(value: Any) -> str:
        """把期货行情里的 ``HH:MM:SS`` 落成 MySQL ``TIME`` 能接受的形式。"""
        import datetime as _datetime

        if isinstance(value, (_datetime.time, _datetime.datetime)):
            return value.strftime("%H:%M:%S")
        if isinstance(value, _datetime.timedelta):
            total = int(value.total_seconds()) % (24 * 3600)
            return "%02d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)
        text = str(value or "").strip()
        if len(text) == 5:            # HH:MM
            return text + ":00"
        return text[:8] if len(text) >= 8 else text

    # -- 实时 EST 的取值 --------------------------------------------------
    def realtime_source_value(self, index_symbol: str, future_quote: Any) -> Any:
        """算出"实时 EST 该用的中间层数值" —— **指数点位空间**。

        对应 PHP ``_QdiiReference::EstRealtimeNetValue()``
        （``php/stock/qdiiref.php:172-175``）：

        ```php
        if ($this->GetRealtimeRef())
        {
            $this->fRealtimeNetValue = $this->GetQdiiValue(strval($est_ref->EstFromPair()));
        }
        ```

        ``$est_ref`` 是 ``FundPairReference``，``EstFromPair()`` **无参**调用时
        （``fundpairref.php:86``）取 ``pair_ref->GetPrice()`` —— 即配对期货
        （hf_ES / hf_NQ）的实时价，走 ``($fPairVal / $fCny) / $this->fFactor``
        （``fundpairref.php:90``）。``fFactor`` 是 ``LoadCalibration()`` 载入的
        **期货校准值**（期货 / 指数，见 :meth:`_calibration_of`），``fCny`` 对
        美股这一对恒为 1.0（两端都不是 A 股），所以结果是**指数点位空间**：

        ```text
        指数点位 = 期货价 / cal_futures        （cal_futures = 期货 / 指数 ≈ 1.007）
        ```

        外层 ``GetQdiiValue()`` 再 ``× 汇率 / 基金校准值`` 换成净值空间。

        ## 为什么必须停在指数空间（2026-10-20 修过一次，10 倍的错）

        ``est_cal``（``fundestcalc._est_quote_ref``）是**中间层在指数空间的锚点**
        （^GSPC 的净值 / 当日收盘价，7690.73），``est / est_cal`` 才是
        "标的相对变化"。本方法曾经换算完指数点位又**多除了一次 ``cal_m``**
        （≈10.03，指数 / ETF 净值），把值压到 ETF 价空间
        （7700 / 10.03 = 767），于是 ``est / est_cal`` 退化成
        "ETF 价 / 指数点位" ≈ 0.0998 —— 实时 EST 从 3.12 掉到 0.45、
        溢价显示成 620%（对照 sz161125cn.php 实测）。
        **est 与 est_cal 必须同空间**，这是本方法唯一的硬约束。

        ## 一道量级保险

        换算完与**指数自身的实时快照价**对一下（``realtimequote`` 里
        ^GSPC / ^NDX 有 daemon 写的快照），偏离超过
        :data:`MIDDLE_VALUE_TOLERANCE` 就返回 ``None`` —— 调用方回落到
        中间层快照（值是对的，只是不带期货信息）。
        这把尺子同时能抓住上面那个 bug 的变体：767 对 7690 偏离 90%，
        一测就出来。
        """
        if future_quote is None:
            return None
        factor = self._calibration_of(index_symbol)
        if not factor:
            return None
        try:
            price = float(getattr(future_quote, "price", None))
        except (TypeError, ValueError):
            return None

        index_price = price / float(factor)                     # 期货 -> 指数点位

        reference = self._index_reference_price(index_symbol)
        try:
            reference = float(reference) if reference not in (None, "") else None
        except (TypeError, ValueError):
            reference = None
        if reference and abs(index_price / reference - 1.0) > MIDDLE_VALUE_TOLERANCE:
            # 量级不对就别用 —— 详见上面"2026-10-20 修过一次"
            return None
        return index_price

    def _index_reference_price(self, index_symbol: str) -> Any:
        """指数自身的实时快照价（量级保险的尺子），没有返回 ``None``。

        与 :meth:`_proxy_price` 同一套取数（快照优先、收盘价兜底），
        只是对象换成**指数自己** —— 尺子必须与被量的值同空间。
        """
        quote = self._proxy_quote(index_symbol)
        if quote is not None:
            price = getattr(quote, "price", None)
            if price not in (None, ""):
                return price
        stock_id = self.source.get_stock_id(index_symbol)
        if stock_id is None:
            return None
        reader = getattr(self.source, "get_latest_history_close", None)
        if reader is None:
            return None
        try:
            return reader(stock_id)
        except Exception:
            return None

    def _proxy_calibration(self, index_symbol: str) -> Any:
        """中间层 ETF 的校准值（``cal_m``）。

        ``cal_m`` 是"ETF / 指数"的比值，所以它同时充当
        "指数点位 → ETF 价"的换算系数（见 :meth:`realtime_source_value`）。
        """
        proxy = self.proxy_symbol_of(index_symbol)
        if not proxy:
            return None
        return self._calibration_of(proxy)

    def _calibration_of(self, index_symbol: str) -> Any:
        """指数自己的期货校准值。"""
        index_id = self.source.get_stock_id(index_symbol)
        if index_id is None:
            return None
        reader = getattr(self.source, "get_calibration_now", None)
        if reader is None:
            return None
        try:
            record = reader(index_id)
        except Exception:
            return None
        if record is None:
            return None
        return getattr(record, "close", None)


#: 内置兜底映射。``futurespair`` 查不到时用它 —— 避免少一行配置就静默失效。
#:
#: 港股与日经的配对是 2026-11-03 补的（此前只有美股两对，港日两族的
#: 「实时 EST」整列空着）。**恒生科技 ``^HSTECH`` 与国企 ``^HSCE`` 加不进来**：
#: 新浪期货只提供 ``hf_HSI`` / ``hf_NK``，``hf_HSTECH`` / ``hf_HSCEI`` /
#: ``hf_HTI`` / ``hf_HHI`` 全部返回空串（实测），生产端 ``sh513010cn.php``
#: 与 ``sh510900cn.php`` 也确实没有实时 EST 列 —— 这是数据源缺，不是漏配。
FALLBACK_FUTURES = {
    "^NDX": "hf_NQ",
    "^GSPC": "hf_ES",
    "^HSI": "hf_HSI",
    "znb_NKY": "hf_NK",
}

#: 指数 → 中间层 ETF 的兜底映射（反推指数点位用）。
FALLBACK_PROXIES = {
    "^NDX": "QQQ",
    "^GSPC": "SPY",
}


def build_futures_calibrator(source: Any) -> FuturesCalibrator:
    """按数据源创建校准器。"""
    return FuturesCalibrator(source, connection=getattr(source, "connection", None))
