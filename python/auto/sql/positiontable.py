"""基金仓位（``fundposition`` 表）的读写 —— 对应 PHP ``php/sql/sqlval.php``。

## 这张表是什么

``fundposition`` 是 ``ValSql`` 的一张实例表（``php/sql/sqlval.php:89`` 的
``FundPositionSql``），形状是 ``(id, close)`` 两列：

==========  ============  ==========================================
列           含义          说明
==========  ============  ==========================================
``id``       **就是** stock_id  主键；没有自增含义（PHP ``ValSql`` 的键）
``close``    仓位          小数，1.0 = 100%；``ReadPos`` 取不到时回落 1.0
==========  ============  ==========================================

「仓位」在估值式里的位置（``holdingsref.php`` 的 ``_estNetValue``）::

    新净值 = 持仓日净值 × (1 + 加权涨幅 × 汇率调整 × 仓位)

默认 1.0 表示"满仓估算"。SH 这类反向 ETF 在表里存 ``-1``。

## 与 PHP 的接口对应

===================================  ==========================================
PHP ``ValSql`` / ``FundPositionSql``  本模块
===================================  ==========================================
``WriteVal($id, $val, $bString)``   :meth:`FundPositionSql.write_val`
``ReadVal($id, $bString)``          :meth:`FundPositionSql.read_val`
``ReadPos($strStockId)``            :meth:`FundPositionSql.read_pos`
``WritePos($strStockId, $fPos)``    :meth:`FundPositionSql.write_pos`
===================================  ==========================================

## 两个必须照抄的细节

1. **``WriteVal`` 的"值没变就不写"闸门**（``sqlval.php:41-56``）：行已存在且
   新旧值之差小于 ``MIN_FLOAT_VAL`` 时**直接返回不写**。这是有意的 ——
   持仓链路每次页面渲染都可能顺手写一次仓位，没有这道闸门会把
   ``fundposition`` 的行翻来覆去地 UPDATE。
2. **``ReadVal`` 的两种返回**（``$bString``）：字符串模式给库里的原值
   （``holdingsdate`` 的日期就走这条），数值模式给 ``floatval`` 后的值。
   PHP 取不到时返回 ``false``，本模块对应 ``None``。
"""

from __future__ import annotations

from typing import Any

#: ``sql → ref`` 是允许的依赖方向（见 ``sql/__init__.py`` 的说明），
#: 这两个私有助手是 PHP ``floatval`` / ``strval(float)`` 口径的唯一实现处，
#: 不重复造。
from ref.stockref import _php_floatval, _php_strval_float  # noqa: E402

#: 与 historytable 同一个常量（PHP ``php/stock.php`` 的 ``MIN_FLOAT_VAL``）。
#: 不 import 是为了让本模块可以单独被 sql 层之外的测试引用而不拖依赖；
#: 值是"比它小的差就当没变"的阈值，两边必须一致。
MIN_FLOAT_VAL = 0.0000005

#: 表名与值列名。PHP 是 ``parent::__construct('fundposition')``，
#: 默认值列叫 ``close``（``ValSql`` 构造函数的第二个参数）。
POSITION_TABLE = "fundposition"
POSITION_VALUE_COLUMN = "close"


class FundPositionSql:
    """``fundposition`` 表：一只基金的估算仓位。

    对应 PHP ``php/sql/sqlval.php:89`` 的 ``FundPositionSql extends ValSql``。
    """

    def __init__(self, connection: Any):
        self.connection = connection
        self.table = POSITION_TABLE
        self.column = POSITION_VALUE_COLUMN

    # -- 内部 ---------------------------------------------------------------
    def _fetchone(self, sql: str, params: tuple = ()) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def _record(self, stock_id: Any) -> Any:
        return self._fetchone(
            "SELECT `id`, `%s` FROM `%s` WHERE `id` = %%s LIMIT 1"
            % (self.column, self.table),
            (stock_id,),
        )

    # -- 读 -----------------------------------------------------------------
    def read_val(self, stock_id: Any, as_string: bool = False) -> Any:
        """对应 PHP ``ReadVal($strId, $bString)``。

        没有这一行返回 ``None``（PHP 返回 ``false``）—— 注意**不是** 0.0：
        调用方要靠这个区分"没设过仓位"和"仓位是 0"。
        """
        record = self._record(stock_id)
        if record is None:
            return None
        value = record[self.column]
        return value if as_string else _php_floatval(value)

    def read_pos(self, stock_id: Any) -> float:
        """对应 PHP ``ReadPos()``：读不到回落 **1.0**（满仓）。

        这个 1.0 是估值式的默认仓位，全站（``GetPosition()`` 一族）都认。
        """
        value = self.read_val(stock_id)
        return 1.0 if value is None else float(value)

    # -- 写 -----------------------------------------------------------------
    def write_val(self, stock_id: Any, value: Any, as_string: bool = False) -> Any:
        """对应 PHP ``WriteVal($strId, $strVal, $bString)``。

        行已存在时先过"值没变就不写"的闸门（见模块 docstring 第 1 条），
        再 UPDATE；没有行才 INSERT。返回 ``True`` 表示真的写了，
        ``False`` 表示被闸门挡下（PHP 同款三态）。
        """
        record = self._record(stock_id)
        if record is not None:
            old = record[self.column]
            if as_string:
                if str(value) == str(old):
                    return False
            else:
                if abs(_php_floatval(old) - _php_floatval(value)) < MIN_FLOAT_VAL:
                    return False
            cursor = self.connection.cursor()
            try:
                cursor.execute(
                    "UPDATE `%s` SET `%s` = %%s WHERE `id` = %%s"
                    % (self.table, self.column),
                    (value, stock_id),
                )
                return True
            finally:
                cursor.close()
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO `%s` (`id`, `%s`) VALUES (%%s, %%s)"
                % (self.table, self.column),
                (stock_id, value),
            )
            return True
        finally:
            cursor.close()

    def write_pos(self, stock_id: Any, position: Any) -> Any:
        """对应 PHP ``WritePos()``：``strval($fPos)`` 后走 ``WriteVal``。

        持仓写入链路用它把"比例之和 / 100"存进去 —— 管理员提交持仓
        （``holdingsupdater.update_stock_option_holdings``）与交易所清单
        两条路都经过这里。
        """
        return self.write_val(stock_id, _php_strval_float(position))

    def delete_by_id(self, stock_id: Any) -> int:
        """对应 PHP ``DeleteById($strId)``（``php/sql/sqltable.php:212``）。

        编辑页的两条路要用它（都来自 ``_submitstockoptions.php``）：

        * ``_updateStockOptionFund()``：仓位填 0 时删掉仓位行（回落 1.0 = 满仓）；
        * ``_updateStockOptionAdr()``：ADR 比例填 0 时删掉 ADR 的仓位行。

        返回删除行数（PHP 返回布尔，这里给行数以便 flash 报"删了几条"）。
        **MyISAM 删了回不来**，真要保护数据靠 ``dbtools/backup_db.py``。
        """
        if stock_id is None or str(stock_id) == "":
            return 0
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "DELETE FROM `%s` WHERE `id` = %%s" % self.table, (stock_id,)
            )
            rowcount = cursor.rowcount
            commit = getattr(self.connection, "commit", None)
            if commit is not None:
                commit()
            return rowcount
        finally:
            cursor.close()


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名）
# ---------------------------------------------------------------------------
ReadVal = FundPositionSql.read_val
WriteVal = FundPositionSql.write_val
ReadPos = FundPositionSql.read_pos
WritePos = FundPositionSql.write_pos
