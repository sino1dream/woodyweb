"""基金持仓明细（``holdings`` / ``holdingsdate`` 两张表）。

对应 PHP ``php/sql/sqlholdings.php`` 的 ``HoldingsSql`` 与
``php/sql/sqldate.php:37`` 的 ``HoldingsDateSql``。

这两张表是 **CK4 Holdings 分支**的数据底座：一只基金在 ``holdings`` 里有持仓
明细行，``/stock/<symbol>`` 才会走进 ``HoldingsReference`` 那条分支
（``woody/res/php/_stock.php:133`` 的 ``StockGetHoldingsReference``）：

```php
function StockGetHoldingsReference($strSymbol)
{
    if (SqlCountHoldings($strSymbol) > 0)   // 判据就是 holdings 表有没有行
    {
        return new HoldingsReference($strSymbol);
    }
    return false;
}
```

## 两张表的形状与一个命名陷阱

``holdings`` 是**明细表**，一行 = 基金持有的一只成分股：

==========  ============  ==========================================
列           含义          说明
==========  ============  ==========================================
``id``       自增主键      无业务含义
``stock_id`` **基金**的 id  外键指向 ``stock`` 表
``holding_id`` **成分股**的 id  同样指向 ``stock`` 表
``ratio``    持仓比例      ``DOUBLE(13,6)``，单位是**百分数**（30.68 表示 30.68%）
==========  ============  ==========================================

``holdingsdate`` 是**日期表**，一行 = 一只基金的持仓清单更新日：

==========  ============  ==========================================
列           含义          说明
==========  ============  ==========================================
``id``       **就是** stock_id  见下面的陷阱说明
``date``     持仓更新日     成分股与比例是这一天的快照
==========  ============  ==========================================

**陷阱**：``holdingsdate`` 长得像其它日频表（``netvaluehistory`` /
``stockhistorydate``），但**它没有 ``stock_id`` 列** —— PHP 的
``DateSql`` 基类（``php/sql/sqldate.php:4``）用 ``ComposePrimaryIdStr()``
把主键 ``id`` 建出来，``ReadDate($strId)`` 传进来的 ``$strId`` 就是
``stock_id`` 的值，**存在 ``id`` 列里**。所以本模块查询 holdingsdate 时
条件是 ``id = %s`` 而不是 ``stock_id = %s``。照抄 ``DailyCloseSql`` 的写法
会查出一个空表，而且不报错（这正是持仓分支"静默不生效"的一种形态）。

## 与 PHP 的接口对应

===================================  ==========================================
PHP ``HoldingsSql``                  本模块
===================================  ==========================================
``Count($strStockId)``               :meth:`HoldingsSql.count`
``GetAll($strStockId)``              :meth:`HoldingsSql.get_all`
``GetHoldingsArray($strStockId)``    :meth:`HoldingsSql.get_holdings_array`
``InsertHoldingId(...)``             :meth:`HoldingsSql.insert`
``BuildOrderBy()``（``ratio DESC``）  :meth:`HoldingsSql.get_all` 内建
===================================  ==========================================

===================================  ==========================================
PHP ``HoldingsDateSql``              本模块
===================================  ==========================================
``ReadDate($strId)``                 :meth:`HoldingsDateSql.read_date`
``WriteDate($strId, $strDate)``      :meth:`HoldingsDateSql.write_date`
===================================  ==========================================
"""

from __future__ import annotations

from typing import Any

#: 表里的 ``ratio`` 是百分数（30.68 = 30.68%），与 PHP 一致不做换算 ——
#: ``HoldingsReference`` 里的公式（``holdingsref.php:301,331``）直接用它，
#: 只有展示时才除以 100。
TABLE_RATIO_IS_PERCENT = True


class HoldingsSql:
    """``holdings`` 表：一只基金的成分股清单与比例。

    对应 PHP ``php/sql/sqlholdings.php:4`` 的 ``HoldingsSql extends KeySql``。
    """

    def __init__(self, connection: Any):
        self.connection = connection
        self.table = "holdings"
        self.columns = ("id", "stock_id", "holding_id", "ratio")

    # -- 内部 ---------------------------------------------------------------
    def _fetchone(self, sql: str, params: tuple = ()) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def _fetchall(self, sql: str, params: tuple = ()) -> list:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    # -- 读 -----------------------------------------------------------------
    def count(self, stock_id: Any) -> int:
        """对应 PHP ``Count()``：这只基金有几行持仓明细。

        ``StockGetHoldingsReference`` 就用它做分支判据（``> 0`` 才算持仓型），
        所以**返回 0 会让整条分支静默不生效** —— 排查"持仓页为什么不出现"
        时先看这里。
        """
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM `%s` WHERE stock_id = %%s" % self.table,
            (stock_id,),
        )
        return int(row["n"]) if row else 0

    def get_all(self, stock_id: Any) -> list:
        """对应 PHP ``GetAll()``，**按 ``ratio`` 倒序**（``BuildOrderBy()``）。

        比例大的排前面，页面上的持仓表就是从重仓到轻仓；``GetHoldingsArray``
        与它同序，所以后面按 id 取比例的循环顺序也跟着确定。
        """
        columns = ", ".join("`%s`" % name for name in self.columns)
        return self._fetchall(
            "SELECT %s FROM `%s` WHERE stock_id = %%s ORDER BY `ratio` DESC"
            % (columns, self.table),
            (stock_id,),
        )

    def get_holdings_array(self, stock_id: Any) -> dict:
        """对应 PHP ``GetHoldingsArray()``：``{holding_id: ratio}``。

        PHP 用 ``$ar[$strHoldingId] = $record['ratio']``，数字字符串键会被 PHP
        自动转成 int；这里直接用 **int 键**，调用方（``HoldingsReference``）拿
        成分股的 stock_id 来索引时不需要再转换。

        ``ratio`` 转成 ``float``：PHP 那边存的是字符串，取值处 ``floatval``；
        这里一步到位，免得调用方到处 ``float()``。
        """
        result: dict = {}
        for row in self.get_all(stock_id):
            result[int(row["holding_id"])] = float(row["ratio"])
        return result

    # -- 写 -----------------------------------------------------------------
    def insert(self, stock_id: Any, holding_id: Any, ratio: Any) -> Any:
        """对应 PHP ``InsertHoldingId()``。

        写入由 CK4-D 的建样本脚本使用（从生产端抓来的持仓明细）；
        业务运行时只读不写。
        """
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO `%s` (stock_id, holding_id, ratio) VALUES (%%s, %%s, %%s)"
                % self.table,
                (stock_id, holding_id, ratio),
            )
            return cursor.lastrowid
        finally:
            cursor.close()

    def insert_array(self, stock_id: Any, ratios: Any) -> int:
        """对应 PHP ``InsertHoldingsArray()``：整份 ``{holding_id: ratio}`` 一次写入。"""
        written = 0
        for holding_id in ratios:
            self.insert(stock_id, holding_id, ratios[holding_id])
            written += 1
        return written


class HoldingsDateSql:
    """``holdingsdate`` 表：持仓清单的更新日。

    对应 PHP ``php/sql/sqldate.php:37`` 的 ``HoldingsDateSql extends DateSql``。

    **条件列是 ``id`` 不是 ``stock_id``** —— 见本模块文档里的命名陷阱。
    """

    def __init__(self, connection: Any):
        self.connection = connection
        self.table = "holdingsdate"

    def _fetchone(self, sql: str, params: tuple = ()) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def read_date(self, stock_id: Any) -> Any:
        """对应 PHP ``ReadDate()``：持仓清单更新日；没有返回 ``None``。

        这个日期是 ``HoldingsReference`` 的**估值基准日** —— 净值取它、
        成分股的"旧价"也取它（``holdingsref.php:52-54,270``）。
        """
        row = self._fetchone(
            "SELECT `date` FROM `%s` WHERE id = %%s LIMIT 1" % self.table,
            (stock_id,),
        )
        return row["date"] if row else None

    def write_date(self, stock_id: Any, date: Any) -> Any:
        """对应 PHP ``WriteDate()``：``id`` 是主键，用 upsert 语义。"""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO `%s` (id, `date`) VALUES (%%s, %%s) "
                "ON DUPLICATE KEY UPDATE `date` = %%s" % self.table,
                (stock_id, date, date),
            )
            return cursor.lastrowid
        finally:
            cursor.close()


#: camelCase 别名 —— PHP 侧方法名是 camelCase，逐行对照时这样写能对齐
#: （AGENTS.md：snake_case 主名 + camelCase 类属性别名）
setattr(HoldingsSql, "GetHoldingsArray", HoldingsSql.get_holdings_array)
setattr(HoldingsSql, "GetAll", HoldingsSql.get_all)
setattr(HoldingsSql, "Count", HoldingsSql.count)
setattr(HoldingsDateSql, "ReadDate", HoldingsDateSql.read_date)
setattr(HoldingsDateSql, "WriteDate", HoldingsDateSql.write_date)
