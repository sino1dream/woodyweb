"""交易记录（``stocktransaction`` 表）的读写 —— 对应 PHP ``php/sql/sqlstocktransaction.php``。

## 这张表是什么

一笔交易 = 一个成员的**一次成交**。表挂在 ``stockgroupitem``（分组成员）
上，一只基金在一个分组里可以有多笔交易（买入+卖出+补仓……）。PHP 的
``StockTransactionSql extends TableSql``（``sqlstocktransaction.php:5``），
而 ``TableSql::Create()`` 只建 ``id`` 主键列 —— **其余列是生产库手工
ALTER 上去的，PHP 源码里没有完整 DDL**。本模块的列形状从用法反推
（见 ``dbtools/seed_stock_group_tables.py`` 的 docstring 与迁移 README）。

============  ========  ==========================================
列             类型       说明
============  ========  ==========================================
``id``        自增       交易主键
``groupitem_id`` INT    所属成员（``stockgroupitem.id``）
``quantity``  **有符号** INT  成交数量；**卖出是负数**
``price``     DOUBLE    成交价
``fees``      DOUBLE    手续费（含在成本里，见 ``AddSqlTransaction``）
``filled``    DATETIME  成交时间（``DebugGetDateTime()`` 的上海时区串）
``remark``    VARCHAR   备注
============  ========  ==========================================

## 与 PHP 的接口对应

====================================  ==========================================
PHP                                    本模块
====================================  ==========================================
``GetAll($arGroupItemId, $iStart, $iNum)``  :meth:`StockTransactionSql.get_all`
``GetRecord($strGroupItemId, ...)``          :meth:`StockTransactionSql.get_record`
``CountAll($arGroupItemId)``                 :meth:`StockTransactionSql.count_all`
``Count($strGroupItemId)``                   :meth:`StockTransactionSql.count`
``TableSql::GetRecordById($strId)``          :meth:`StockTransactionSql.get_record_by_id`
``Delete($strGroupItemId)``                  :meth:`StockTransactionSql.delete`
``TableSql::DeleteById($strId)``             :meth:`StockTransactionSql.delete_by_id`
``Insert(...)``                              :meth:`StockTransactionSql.insert`
``Update(...)``                              :meth:`StockTransactionSql.update`
``Merge($strSrc, $strDst)``                  :meth:`StockTransactionSql.merge`
====================================  ==========================================

## 三个必须照抄的细节

1. **``filled`` 只在 Insert 时生成，Update 不碰**（``sqlstocktransaction.php:74``
   的 ``unset($ar['filled'])``）—— 编辑一笔交易不改它的成交时间。
2. **``GetAll`` 排序是 ``filled`` DESC**（新成交在前）—— 不是 id DESC。
   同一成员的多笔交易按成交时间倒序给，页面列表直接照这个顺序渲染。
3. **三个写方法对"空键"的返回**：PHP 的 ``_SqlBuildWhere`` 对 ``false``
   给 ``false``，于是 ``Count`` / ``Delete`` / ``Merge`` 在空键时**根本不
   执行 SQL**（函数无 return，值为 ``null``）。本模块对应 ``None``，
   调用方不要把它当 0 / False 用。

## 已知偏差（与 PHP 的差异，都记在迁移 README）

* **INSERT 省掉 id 列**：PHP 的 ``InsertArray`` 无 id 键时拼
  ``INSERT INTO t(id, ...) VALUES('0', ...)``（MyISAM 的自增惯例），
  本模块直接不写 id 列让 MySQL 自增。同为 MyISAM 效果一样，且不依赖
  ``'0'`` 这个魔法值（与 ``core/stockgroupsql.py`` 的同一处偏差一致）。
* **SQL 参数化**：PHP 全程字符串拼接（``_SqlBuildWhere`` 直接内插），
  本模块用 ``%s`` 占位符。where 的语义（空数组→不查）保持一致。
"""

from __future__ import annotations

import datetime
from typing import Any, List, Optional

from core import tzutil
from core.stocksymbol import TIME_ZONE_SHANGHAI

#: 表名。PHP ``parent::__construct('stocktransaction')``。
TRANSACTION_TABLE = "stocktransaction"

#: ``GetAll`` 的排序（PHP ``sqlstocktransaction.php:26`` 写死的字符串）。
ORDER_BY_FILLED_DESC = "`filled` DESC"


def debug_get_date_time(moment: Any = None) -> str:
    """对应 PHP ``DebugGetDateTime()``（``php/debug.php:112``）。

    ``DebugFormat_date('Y-m-d', ...)`` + ``' '`` + ``DebugFormat_date('H:i:s', ...)``，
    时区是 ``DEBUG_TIME_ZONE`` = ``Asia/Shanghai``。``moment`` 为 ``None``
    时取当前时间（PHP 的 ``$iTime = false`` → ``GetNowTick()``）。

    PHP 的 ``DebugFormat_date`` 会 ``date_default_timezone_set`` 再 ``date()``，
    即**先转到上海时区再格式化**——入参带 tzinfo 时同样要转（UTC 12:00
    应显示成上海 20:00），不是原样 strftime。
    """
    if moment is None:
        moment = datetime.datetime.now()
    shanghai = tzutil.get_time_zone(TIME_ZONE_SHANGHAI)
    if getattr(moment, "tzinfo", None) is None:
        moment = tzutil.localize(moment, TIME_ZONE_SHANGHAI)
    else:
        moment = moment.astimezone(shanghai)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _php_build_limit(start: Any, num: Any) -> str:
    """对应 PHP ``_SqlBuildLimit($iStart, $iNum)``（``_sqlcommon.php:63``）。

    * ``start == 0`` 且 ``num != 0`` → ``'<num>'``（只 LIMIT）
    * ``start != 0`` → ``'<start> , <num>'``（OFFSET 写法）
    * 两者皆 0 → ``''``（无 LIMIT）

    注意 PHP 是**松散比较**：``'0' == 0`` 为真，字符串 ``'10'`` 也当 10。
    本模块先 ``int()`` 再比，等价（调用方传来的都是整数或数字串）。
    """
    start_int = int(start or 0)
    num_int = int(num or 0)
    if start_int == 0:
        return str(num_int) if num_int != 0 else ""
    return "%d , %d" % (start_int, num_int)


class StockTransactionSql:
    """``stocktransaction`` 表：一个成员的成交记录集合。"""

    TABLE = TRANSACTION_TABLE

    def __init__(self, connection: Any):
        self.connection = connection

    # -- 内部 ---------------------------------------------------------------
    def _fetchall(self, sql: str, params: tuple = ()) -> List[dict]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [dict(row) for row in (rows or [])]

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        finally:
            cursor.close()
        return dict(row) if row else None

    def _execute(self, sql: str, params: tuple = ()) -> int:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return int(cursor.rowcount or 0)
        finally:
            cursor.close()

    def _build_where_or_groupitem(self, group_item_ids: Any) -> Optional[str]:
        """对应 PHP ``_buildWhereOr_groupitem`` → ``_SqlBuildWhereOrArray``。

        空数组/``None`` → ``None``（PHP 给 ``false``，调用方据此不查）。
        数组元素是 ``None`` 的会被跳过（PHP 的 ``_SqlBuildWhere`` 对
        ``false`` 给 ``false``，拼不进去）。
        """
        if not group_item_ids:
            return None
        parts = []
        for item_id in group_item_ids:
            if item_id is None:
                continue
            parts.append("`groupitem_id` = %s")
        if not parts:
            return None
        return "(" + " OR ".join(parts) + ")"

    # -- 读 -----------------------------------------------------------------
    def get_all(self, group_item_ids: Any, start: Any = 0, num: Any = 0
                ) -> Optional[List[dict]]:
        """对应 PHP ``GetAll($arGroupItemId, $iStart, $iNum)``。

        排序 ``filled`` DESC（见模块 docstring 第 2 条）。空 id 数组返回
        ``None``（PHP 给 ``false``）——**不是**空列表，调用方要区分。
        """
        where = self._build_where_or_groupitem(group_item_ids)
        if where is None:
            return None
        sql = "SELECT * FROM `%s` WHERE %s ORDER BY %s" % (
            self.TABLE, where, ORDER_BY_FILLED_DESC)
        limit = _php_build_limit(start, num)
        if limit:
            sql += " LIMIT " + limit
        # 占位符顺序必须与 where 里的 %s 一致（按传入的 id 顺序）
        params = tuple(item_id for item_id in group_item_ids
                       if item_id is not None)
        return self._fetchall(sql, params)

    def get_record(self, group_item_id: Any, start: Any = 0, num: Any = 0
                   ) -> Optional[List[dict]]:
        """对应 PHP ``GetRecord($strGroupItemId, ...)`` = ``GetAll(array($id), ...)``。"""
        return self.get_all([group_item_id], start, num)

    def count_all(self, group_item_ids: Any) -> int:
        """对应 PHP ``CountAll($arGroupItemId)``：空数组给 **0**（不是 None）。"""
        where = self._build_where_or_groupitem(group_item_ids)
        if where is None:
            return 0
        params = tuple(item_id for item_id in group_item_ids
                       if item_id is not None)
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM `%s` WHERE %s" % (self.TABLE, where),
            params)
        return int(row["n"]) if row else 0

    def get_record_by_id(self, trans_id: Any) -> Optional[dict]:
        """对应 PHP ``TableSql::GetRecordById($strId)``（``sqltable.php:195``）。

        ``GetSingleData(_SqlBuildWhere_id($strId))`` = ``SELECT * ... WHERE
        id = 'x' LIMIT 1``。注意 PHP 的 ``SqlGetSingleTableData`` 要求
        **恰好 1 行**（``mysqli_num_rows($result) == 1``）——id 是主键所以
        等价；id 为 ``None`` 时 PHP 的 ``_SqlBuildWhere`` 给 ``false``
        → 无 WHERE（全表扫）→ 行数不为 1 → ``false``。这里照语义给
        ``None``（不查库）。
        """
        if trans_id is None:
            return None
        return self._fetchone(
            "SELECT * FROM `%s` WHERE `id` = %%s LIMIT 1" % self.TABLE,
            (trans_id,))

    def delete_by_id(self, trans_id: Any) -> Optional[int]:
        """对应 PHP ``TableSql::DeleteById($strId)``（``sqltable.php:243``）。

        ``DeleteData`` 先 COUNT 再 DELETE，返回**删除行数**（没有行给 0）。
        id 为 ``None`` → ``None``（PHP 的 ``_SqlBuildWhere_id(false)``
        给 ``false``，``DeleteById`` 直接 ``return false``）。
        """
        if trans_id is None:
            return None
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM `%s` WHERE `id` = %%s" % self.TABLE,
            (trans_id,))
        count = int(row["n"]) if row else 0
        if count <= 0:
            return 0
        return self._execute(
            "DELETE FROM `%s` WHERE `id` = %%s" % self.TABLE, (trans_id,))

    def count(self, group_item_id: Any) -> Optional[int]:
        """对应 PHP ``Count($strGroupItemId)``。

        键为 ``None`` 时 PHP **不执行 SQL**、函数无 return → ``null``，
        本模块给 ``None``（见模块 docstring 第 3 条）。
        """
        if group_item_id is None:
            return None
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM `%s` WHERE `groupitem_id` = %%s"
            % self.TABLE, (group_item_id,))
        return int(row["n"]) if row else 0

    # -- 写 -----------------------------------------------------------------
    def delete(self, group_item_id: Any) -> Optional[int]:
        """对应 PHP ``Delete($strGroupItemId)`` → ``DeleteData``。

        PHP 的 ``DeleteData`` 先 COUNT 再 DELETE，返回**删除行数**
        （没有行返回 0）。键为 ``None`` → ``None``（不执行 SQL）。
        """
        if group_item_id is None:
            return None
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM `%s` WHERE `groupitem_id` = %%s"
            % self.TABLE, (group_item_id,))
        count = int(row["n"]) if row else 0
        if count <= 0:
            return 0
        return self._execute(
            "DELETE FROM `%s` WHERE `groupitem_id` = %%s" % self.TABLE,
            (group_item_id,))

    def delete_all_by_ids(self, group_item_ids: Any) -> int:
        """按一批成员批量删交易 —— **批量化写法，语义同循环 :meth:`delete`**。

        PHP 的 ``SqlDeleteStockGroupItemByGroupId`` 是 ``foreach`` 里逐条
        ``trans_sql->Delete``（``sqlstockgroup.php:296-300``）；这里合成
        一条 ``groupitem_id IN (...)``，省掉 N 次往返。空列表 → 0（不执行）。
        """
        if not group_item_ids:
            return 0
        placeholders = ", ".join(["%s"] * len(group_item_ids))
        return self._execute(
            "DELETE FROM `%s` WHERE `groupitem_id` IN (%s)"
            % (self.TABLE, placeholders),
            tuple(group_item_ids))

    def _make_field_array(self, group_item_id: Any, quantity: Any,
                          price: Any, fees: Any, remark: Any,
                          with_filled: bool) -> dict:
        """对应 PHP ``_makePrivateFieldArray``（``sqlstocktransaction.php:61``）。

        PHP 的数组顺序是 groupitem_id/quantity/price/fees/filled/remark，
        ``Insert`` 带 filled、``Update`` unset 掉它（见模块 docstring 第 1 条）。
        """
        fields = {
            "groupitem_id": group_item_id,
            "quantity": quantity,
            "price": price,
            "fees": fees,
        }
        if with_filled:
            fields["filled"] = debug_get_date_time()
        fields["remark"] = remark
        return fields

    def insert(self, group_item_id: Any, quantity: Any, price: Any,
               fees: Any = "0.0", remark: Any = "") -> int:
        """对应 PHP ``Insert($strGroupItemId, $strQuantity, $strPrice, $strFees, $strRemark)``。

        默认 ``fees='0.0'`` / ``remark=''`` 是 PHP 的签名默认值。
        返回插入行数（PHP 返回 query 的 bool，本模块给 rowcount 更方便断言）。
        """
        fields = self._make_field_array(group_item_id, quantity, price,
                                        fees, remark, with_filled=True)
        columns = ", ".join("`%s`" % key for key in fields)
        placeholders = ", ".join(["%s"] * len(fields))
        return self._execute(
            "INSERT INTO `%s` (%s) VALUES (%s)"
            % (self.TABLE, columns, placeholders),
            tuple(fields.values()))

    def update(self, trans_id: Any, group_item_id: Any, quantity: Any,
               price: Any, fees: Any, remark: Any = "") -> int:
        """对应 PHP ``Update($strId, ...)`` → ``UpdateById``。

        **不写 filled**（成交时间不可改，见模块 docstring 第 1 条）。
        WHERE 用 ``id = %s LIMIT 1``（PHP ``UpdateById`` 的 ``'1'``）。
        """
        fields = self._make_field_array(group_item_id, quantity, price,
                                        fees, remark, with_filled=False)
        assignments = ", ".join("`%s` = %%s" % key for key in fields)
        return self._execute(
            "UPDATE `%s` SET %s WHERE `id` = %%s LIMIT 1"
            % (self.TABLE, assignments),
            tuple(fields.values()) + (trans_id,))

    def merge(self, src_group_item_id: Any, dst_group_item_id: Any) -> Optional[int]:
        """对应 PHP ``Merge($strSrcGroupItemId, $strDstGroupItemId)``。

        把源成员的全部交易**改挂到**目标成员（合并成员时用），记录本身
        不动（不并 quantity、不动 filled）。源为 ``None`` → ``None``。
        """
        if src_group_item_id is None:
            return None
        return self._execute(
            "UPDATE `%s` SET `groupitem_id` = %%s WHERE `groupitem_id` = %%s"
            % self.TABLE, (dst_group_item_id, src_group_item_id))


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名）
# ---------------------------------------------------------------------------
GetAll = StockTransactionSql.get_all
GetRecord = StockTransactionSql.get_record
GetRecordById = StockTransactionSql.get_record_by_id
CountAll = StockTransactionSql.count_all
Count = StockTransactionSql.count
Delete = StockTransactionSql.delete
DeleteById = StockTransactionSql.delete_by_id
Insert = StockTransactionSql.insert
Update = StockTransactionSql.update
Merge = StockTransactionSql.merge
