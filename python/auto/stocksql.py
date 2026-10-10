"""``stock`` 表访问 —— 对应 PHP ``php/sql/sqlstocksymbol.php:94`` 的 ``StockSql``。

PHP 里 ``StockSql extends KeyNameSql extends TableSql``，构造函数里除了本表还
``new`` 了十来个子表的访问对象（``cal_sql`` / ``his_sql`` / ``net_sql`` /
``pos_sql`` / 三个 pair sql…），是一个"上帝对象"：全局只建一次（``InitGlobalStockSql()``），
靠 ``GetStockSql()`` 到处取。

**本版只迁 ``stock`` 表本身**，不复制那个上帝对象，理由有两条：

1. 那些子表在 Python 侧各有自己的类（``historytable`` 里的 ``CalibrationSql`` /
   ``StockHistorySql`` / ``NetValueHistorySql`` …），不需要靠一个对象串起来；
2. 上帝对象隐含"进程级全局单例"，而 Python 侧的连接是**请求级**的
   （见 ``web/db.py``）。照抄会引入 PHP 那套全局变量语义，与现有架构冲突。

## 只迁了页面真正会用的那几个方法

``KeyNameSql`` 的 ``GetAll($iStart, $iNum)`` 是股票列表页
（``php/ui/stockparagraph.php``）取数的全部依赖；``CountData()`` 给翻页控件；
``GetId()`` / ``GetName()`` / ``GetSymbol()`` 对应 ``SqlGetStockId()`` /
``SqlGetStockName()`` / ``SqlGetStockSymbol()``，是三个一行代码的查询，
留着避免调用方各写一遍裸 SQL。

**没有迁** ``InsertKey()`` / ``Create()`` 这类写操作：建表与种子数据归
``python/sql/``（见 ``build_schema.py`` / ``seed_demo_data.py``），
页面层不需要写 ``stock`` 表。

## 排序与 limit 都照抄 PHP

* 排序恒为 ``symbol ASC``（``KeyNameSql::GetAll()`` 里写死）；
* limit 走 PHP ``_SqlBuildLimit()``（``php/sql/_sqlcommon.php:63``）的三分支语义，
  与 ``historytable.DailyCloseSql.get_all()`` 完全一致 —— 包括
  ``start != 0 且 num == 0`` 时会拼出 ``LIMIT start, 0``（返回 0 行）这个
  看着像 bug 的行为。**照抄**是因为它是"传了 start 就必须给 num"的隐式约定，
  改掉会让某一天突然多出几千行。
"""

from __future__ import annotations

from typing import Any, Sequence

#: 表名与"键"列名。PHP 是 ``parent::__construct('stock', 'symbol')``。
STOCK_TABLE = "stock"
STOCK_KEY_NAME = "symbol"
STOCK_NAME_COLUMN = "name"


def build_limit(start: int = 0, num: int = 0) -> str:
    """对应 ``_SqlBuildLimit()``（``php/sql/_sqlcommon.php:63``）。

    ==============================  ==================================
    情形                             返回
    ==============================  ==================================
    ``start == 0`` 且 ``num != 0``   ``"num"``
    ``start == 0`` 且 ``num == 0``   ``""``（不加 limit，取全部）
    ``start != 0``                   ``"start , num"``
    ==============================  ==================================

    第三行在 ``num == 0`` 时拼出 ``LIMIT start, 0``，MySQL 会返回 0 行。
    这是 PHP 的真实行为，测试里钉住了。
    """
    if start == 0:
        if num != 0:
            return str(int(num))
        return ""
    return "%d , %d" % (int(start), int(num))


class StockSql:
    """``stock`` 表（``symbol`` + ``name``）的分页访问。"""

    def __init__(self, connection: Any):
        self.connection = connection

    # -- 读 ---------------------------------------------------------------
    def count_data(self) -> int:
        """对应 ``CountData()``（``php/sql/sqltable.php:174``）：表里的总行数。"""
        row = self._fetchone("SELECT COUNT(*) AS n FROM `%s`" % STOCK_TABLE)
        return int(row["n"]) if row else 0

    def get_all(self, start: int = 0, num: int = 0) -> list:
        """按 ``symbol ASC`` 取一页，对应 ``KeyNameSql::GetAll()``。

        返回 ``[{"id", "symbol", "name"}, ...]``。
        """
        limit = build_limit(start, num)
        sql = "SELECT `id`, `%s`, `%s` FROM `%s` ORDER BY `%s` ASC" % (
            STOCK_KEY_NAME,
            STOCK_NAME_COLUMN,
            STOCK_TABLE,
            STOCK_KEY_NAME,
        )
        if limit:
            sql += " LIMIT " + limit
        return self._fetchall(sql)

    def get_record(self, symbol: str) -> Any:
        """按符号取整行；没有返回 ``None``。对应 ``GetRecord()``。"""
        return self._fetchone(
            "SELECT `id`, `%s`, `%s` FROM `%s` WHERE `%s` = %%s LIMIT 1"
            % (STOCK_KEY_NAME, STOCK_NAME_COLUMN, STOCK_TABLE, STOCK_KEY_NAME),
            (symbol,),
        )

    def get_id(self, symbol: str) -> Any:
        """对应 ``GetId()`` / ``SqlGetStockId()``。"""
        row = self.get_record(symbol)
        return row["id"] if row else None

    def get_name(self, symbol: str) -> Any:
        """对应 ``GetKey()`` 的变体 / ``SqlGetStockName()``。"""
        row = self.get_record(symbol)
        return row[STOCK_NAME_COLUMN] if row else None

    def get_symbol(self, stock_id: Any) -> Any:
        """按 id 反查符号，对应 ``SqlGetStockSymbol()``。"""
        row = self._fetchone(
            "SELECT `%s` FROM `%s` WHERE `id` = %%s LIMIT 1" % (STOCK_KEY_NAME, STOCK_TABLE),
            (stock_id,),
        )
        return row[STOCK_KEY_NAME] if row else None

    # -- 内部 -------------------------------------------------------------
    def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()


def stock_paging_columns(start: int, num: int, total: int) -> tuple:
    """给翻页控件算上一页/下一页的起始位置。

    对应 ``php/ui/stockparagraph.php`` 里传给 ``GetMenuLink()`` 的 ``start``/
    ``num`` 与 ``php/httplink.php:130`` 里那几个 ``$iPrevStart`` / ``$iNextStart``。
    返回 ``(上一页 start 或 None, 下一页 start 或 None)``。
    """
    prev_start = None
    if start > 0:
        prev_start = start - num if start > num else 0
    next_start = start + num if start + num < total else None
    return prev_start, next_start


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名）
# ---------------------------------------------------------------------------
SqlBuildLimit = build_limit
