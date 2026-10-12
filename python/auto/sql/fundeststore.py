"""官方 EST 的存储（``fundest`` 表）。

对应 PHP ``php/sql/sqlstocksymbol.php:117`` 的
``new DailyTimeSql('fundest')`` 与 ``php/sql/sqldailytime.php`` 的
``DailyTimeSql`` 接口。

## 这张表存的是什么（很重要）

**不是官方净值**，而是「**官方净值出来之前**那一刻算出来的估值」。

依据 PHP ``StockUpdateEstResult()``（``php/stock.php:148``）的注释与判断：

```php
function StockUpdateEstResult($strStockId, $fNetValue, $strDate)
{
    if ($fNetValue)
    {
        $net_sql = GetNetValueHistorySql();
        if ($net_sql->GetRecord($strStockId, $strDate) == false)
        {   // Only update when net value is NOT ready
            $fund_est_sql = GetFundEstSql();
            $fund_est_sql->WriteDaily($strStockId, $strDate, strval($fNetValue));
        }
    }
}
```

原注释 "Only update when net value is NOT ready" 就是全部要点：
**真实净值一旦到货，就再也不写这一天的估值了**。这样事后拿真实净值一比，
"当时算得准不准"就一目了然 —— 这就是「官方 EST + 时间 + 误差」三列的用途。

写入时机是**用户访问页面**（PHP 的估值在构造函数里算），
所以记录里的时间是"第一次访问的时刻"，没有访问就没有记录。

## 与 PHP 的接口对应

===========================  ==================================================
PHP ``DailyTimeSql``         本模块
===========================  ==================================================
``WriteDaily($id,$date,$v)``  :meth:`FundEstStore.write`
``GetRecord($id,$date)``      :meth:`FundEstStore.get_record`
``GetRecordNow($id)``         :meth:`FundEstStore.get_latest`
``GetClose($id,$date)``       :meth:`FundEstStore.get_close`
``GetDateNow($id)``           :meth:`FundEstStore.get_latest_date`
===========================  ==================================================
"""

from __future__ import annotations

import datetime
from typing import Any

from sql.historytable import DailyTimeSql


def hour_minute_of(value: Any) -> Any:
    """把时间值转成 ``HHMM`` 整数，用于时刻比较。

    对应 PHP 的 ``GetHourMinute()``：把 ``'20:55:00'`` 变成 ``2055``，
    这样比大小就知道"是否已过某个时刻"。

    **注意要处理 MySQL ``TIME`` 列**：PyMySQL 取回来是 ``datetime.timedelta``
    （实测 ``17:30:00`` -> ``timedelta(seconds=63000)``），
    直接 ``str()`` 虽然看着对，但跨天会变成 ``1 day, 0:00:00`` 而算错。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime.timedelta):
        total = int(value.total_seconds()) % (24 * 3600)
        return (total // 3600) * 100 + (total % 3600) // 60
    if isinstance(value, (datetime.time, datetime.datetime)):
        return value.hour * 100 + value.minute

    text = str(value).strip()
    # 允许 'HH:MM'、'HH:MM:SS'、'HHMM' 三种写法
    if ":" in text:
        parts = text.split(":")
    elif text.isdigit() and len(text) in (3, 4):
        # '0900' -> ['09', '00']；'900' -> ['9', '00']
        parts = [text[:-2], text[-2:]]
    else:
        parts = text.split()

    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 100 + minute


class FundEstStore:
    """``fundest`` 表的读写。

    ``connection`` 是任意 DB-API 连接（与数据源共用同一个请求级连接即可）。
    """

    #: 对应 PHP ``fundpairref.php:252`` 的那个 ``20:55``：
    #: 只有该中间层标的的时刻早于 20:55 才记录官方 EST。
    #: 依据：20:55 之后的美股行情已经接近收盘，此时算出的"官方 EST"与
    #: 当晚公布的净值重合度高，记下来没有比较意义（作者原话见
    #: ``StockUpdateEstResult`` 的用法）。
    DEFAULT_GATE_HHMM = 2055

    def __init__(self, connection: Any):
        self.connection = connection
        self._table = DailyTimeSql(connection, "fundest")

    # -- 读 ---------------------------------------------------------------
    def get_record(self, stock_id: Any, date: Any) -> Any:
        """取某一天的记录；没有返回 ``None``。对应 ``GetRecord()``。"""
        return self._table.get_record(stock_id, date)

    def get_close(self, stock_id: Any, date: Any) -> Any:
        """取某一天的估值；没有返回 ``None``。对应 ``GetClose()``。"""
        return self._table.get_close(stock_id, date)

    def get_latest(self, stock_id: Any) -> Any:
        """取**最新**一条记录（按日期倒序）。

        对应 PHP ``GetRecordNow()``/``GetDateNow()``：当天中间价还没到货时，
        ``_QdiiReference::EstNetValue()`` 就是用它回退，把上一次记录的
        日期与估值当成当前的官方 EST。
        """
        cursor = self.connection.cursor()
        try:
            columns = ", ".join("`%s`" % name for name in self._table.columns)
            cursor.execute(
                "SELECT %s FROM `fundest` WHERE stock_id = %%s "
                "ORDER BY `date` DESC, `time` DESC LIMIT 1" % columns,
                (stock_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get_latest_date(self, stock_id: Any) -> Any:
        """取最新一条记录的日期。对应 ``GetDateNow()``。"""
        record = self.get_latest(stock_id)
        return record["date"] if record else None

    def count(self, stock_id: Any) -> int:
        """该符号有多少条记录。对应 ``Count()``。"""
        return self._table.count(stock_id)

    def get_all(self, stock_id: Any, start: int = 0, num: int = 0) -> list:
        """按日期倒序取一页，供后台查看。对应 ``GetAll()``。"""
        return self._table.get_all(stock_id, start, num)

    # -- 写 ---------------------------------------------------------------
    def write(self, stock_id: Any, date: Any, value: Any, time: Any = None) -> bool:
        """写一条官方 EST，**已存在则不覆盖**。

        对应 PHP ``DailyTimeSql::InsertDaily()``：

        ```php
        if ($this->GetRecord($strKeyId, $strDate))    return false;   // 已有就不动
        $ar['time'] = DebugGetTime();
        ```

        「已有就不动」是必需的 —— 这一列的价值在于"**第一次算出来的**那个数"，
        如果每次访问都覆盖，就变成"最后一次访问时的收盘值"，失去比较意义。

        ``time`` 不给时取当前时刻。
        """
        if stock_id is None or date is None or value is None or value == "":
            return False
        if self.get_record(stock_id, date) is not None:
            return False

        stamp = time or datetime.datetime.now().strftime("%H:%M:%S")
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO `fundest` (stock_id, `date`, close, `time`) VALUES (%s, %s, %s, %s)",
                (stock_id, date, value, stamp),
            )
            commit = getattr(self.connection, "commit", None)
            if commit is not None:
                commit()
            return True
        finally:
            cursor.close()

    def write_if_ready(self, stock_id: Any, date: Any, value: Any, time: Any = None,
                       gate_hhmm: Any = None) -> bool:
        """带"是否该记"判断的写入。

        两个条件（都对应 PHP）：

        1. **当天真实净值还没到货** —— ``netvaluehistory`` 里没有 ``(stock_id, date)``
           这一行。原注释 "Only update when net value is NOT ready"。
        2. **中间层时刻早于 20:55** —— ``php/stock/fundpairref.php:252``
           的 ``if ($this->pair_ref->GetHourMinute() < 2055)``。

        第 2 条传 ``gate_hhmm=None`` 时跳过（用于没有中间层时刻的场合）。
        """
        if stock_id is None or date is None or value is None or value == "":
            return False

        # 条件 1：真实净值还没到
        from sql.historytable import NetValueHistorySql

        netvalue = NetValueHistorySql(self.connection)
        if netvalue.get_record(stock_id, date) is not None:
            return False

        # 条件 2：时刻闸门
        if gate_hhmm is not None:
            limit = self.DEFAULT_GATE_HHMM if gate_hhmm is True else int(gate_hhmm)
            current = hour_minute_of(time) if time is not None else None
            if current is None:
                current = datetime.datetime.now().hour * 100 + datetime.datetime.now().minute
            if current >= limit:
                return False

        return self.write(stock_id, date, value, time)


def default_gate_hhmm() -> int:
    """默认的时刻闸门（20:55）。"""
    return FundEstStore.DEFAULT_GATE_HHMM
