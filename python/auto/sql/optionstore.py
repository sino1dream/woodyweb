"""编辑页（edit 族）要用的几张"小表"。

对应 PHP：

* ``php/sql/sqlval.php``    —— ``ValSql``（``(id, close)`` 两列）
* ``php/sql/sqlint.php``    —— ``IntSql``（``(id, num)`` 两列）
* ``php/sql/sqlpair.php``   —— ``PairSql``（``(id, stock_id)``，语义是"A→B"）
* ``php/sql/sqlstockpair.php`` —— ``StockPairSql``（上面那张表 + 符号解析）
* ``php/sql/sqldailyclose.php:267-320`` —— 五个 ``DailyCloseSql`` 子类

## 为什么单独一个模块

这五张表（``futurepremium`` / ``stockema50`` / ``stockema200`` / ``sharesdiff`` /
``stockdividend`` / ``stocksplit``）与三种"键值表"原来都散在 PHP 的
``sqlval/sqlint/sqlpair`` 里，Python 侧一直没有落点 —— 因为**读页面用不到它们**
（``futurepremium`` 只是升水记录、EMA 只是均线缓存），只有**编辑页**要写。
现在补 ``/stock/<symbol>/edit/<option>``，它们才第一次被需要，所以集中放这里，
而不是继续往 ``historytable.py`` 里堆。

## 三种表的形状差异（踩过的坑）

============================  ==========  ==========  ==========================
表                            主键        值列        值列的类型与比较方式
============================  ==========  ==========  ==========================
``fundposition``               ``id``      ``close``   ``double``，``MIN_FLOAT_VAL``
``lastcalibration``            ``id``      ``close``   ``double``，``MIN_FLOAT_VAL``
``groupitemamount``            ``id``      ``num``     ``int``，**字符串比较**
``ahpair``/``abpair``/          ``id``      ``stock_id`` ``int``，**字符串比较**
``adrpair``/``fundpair``
``stockema50``/``stockema200``/ ``id``      ``close``   ``double``，但主键是自增
``futurepremium``/``sharesdiff``/
``stockdividend``/``stocksplit``
============================  ==========  ==========  ==========================

**左边两张"键值表"是"表名即语义"**：``fundposition`` 的 ``id`` **就是**
``stock_id``，一张表最多一只标的一行；``WriteVal`` 是 upsert 语义。
**右边的日频表**则是"一天一行"，``id`` 是自增代理键，写入口是
``WriteDaily``（见 :class:`sql.historytable.DailyCloseSql`）。

两套写入口的差别不是风格问题：``_updateOptionDailySql()``
（``woody/res/php/_submitstockoptions.php:208``）对所有日频表都是
"空值→按日期删、有值→WriteDaily"，而 ``WriteVal`` 那几张表根本没有日期概念。
混用会写成"同一天多行"或"把整只标的的仓位按日期删掉"。
"""

from __future__ import annotations

from typing import Any, Optional

# ``sql`` → ``ref`` 是允许的依赖方向（见 ``sql/__init__.py``）。
# ``_php_floatval`` 是 PHP ``floatval`` 的唯一口径实现处，``write_val`` 的
# "值没变就不写"闸门要对齐 PHP 的比较方式，所以复用而不是再抄一份。
from ref.stockref import _php_floatval

from sql.historytable import MIN_FLOAT_VAL, DailyCloseSql
from sql.stocksql import StockSql


# ---------------------------------------------------------------------------
# 一、``(id, 值)`` 两列的键值表 —— PHP ``ValSql`` / ``IntSql``
# ---------------------------------------------------------------------------


class ValStore:
    """``(id, close)`` 两列的表，``id`` **就是** ``stock_id``。

    对应 PHP ``ValSql``（``php/sql/sqlval.php:4``）。子类只改表名与值列名。
    """

    #: 子类覆写；``None`` 表示由构造器传入
    table: str = ""
    #: 值列名。``ValSql`` 默认 ``'close'``，``IntSql`` 改成 ``'num'``
    column: str = "close"

    def __init__(self, connection: Any, table: Optional[str] = None,
                 column: Optional[str] = None):
        self.connection = connection
        if table is not None:
            self.table = table
        if column is not None:
            self.column = column

    # -- 内部 ---------------------------------------------------------------
    def _fetchone(self, sql: str, params: tuple = ()) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def _execute(self, sql: str, params: tuple = ()) -> int:
        """执行一条写语句并提交，返回受影响行数。

        **MyISAM 没有事务**（本项目的表 28/30 是 MyISAM），``commit()`` 是空
        操作，写坏了回滚不回来。所以这里的写方法一律"先查后写"，
        批量改数据前先跑 ``dbtools/backup_db.py``。
        """
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            commit = getattr(self.connection, "commit", None)
            if commit is not None:
                commit()
            return cursor.rowcount
        finally:
            cursor.close()

    # -- 读 -----------------------------------------------------------------
    def get_record_by_id(self, record_id: Any) -> Any:
        """对应 PHP ``GetRecordById()``：``{"id": ..., <column>: ...}`` 或 ``None``。"""
        if record_id is None or str(record_id) == "":
            return None
        return self._fetchone(
            "SELECT `id`, `%s` FROM `%s` WHERE `id` = %%s LIMIT 1"
            % (self.column, self.table),
            (record_id,),
        )

    def read_val(self, record_id: Any, as_string: bool = False) -> Any:
        """对应 PHP ``ReadVal($strId, $bString)``。

        没有这一行返回 ``None``（PHP 返回 ``false``）—— **不是** 0.0：
        调用方要靠这个区分"没设过"和"值是 0"（``fundposition`` 里 0 是合法的
        "空仓"，``lastcalibration`` 里 0 却意味着没有校准）。
        """
        record = self.get_record_by_id(record_id)
        if record is None:
            return None
        value = record[self.column]
        return value if as_string else _php_floatval(value)

    def get_all_id_val(self) -> dict:
        """``GetAllIdVal()``：``{id: 原值}`` 全表。"""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT `id`, `%s` FROM `%s`" % (self.column, self.table)
            )
            return {row[0] if not isinstance(row, dict) else row["id"]:
                    (row[1] if not isinstance(row, dict) else row[self.column])
                    for row in cursor.fetchall()}
        finally:
            cursor.close()

    # -- 写 -----------------------------------------------------------------
    def insert(self, record_id: Any, value: Any) -> bool:
        """``Insert($strId, $strVal)``（``sqlval.php:26``）。"""
        return bool(self._execute(
            "INSERT INTO `%s` (`id`, `%s`) VALUES (%%s, %%s)" % (self.table, self.column),
            (record_id, value),
        ))

    def update(self, record_id: Any, value: Any) -> bool:
        """``Update($strId, $strVal)``（``sqlval.php:31``）：按主键改。"""
        if record_id is None or str(record_id) == "":
            return False
        return bool(self._execute(
            "UPDATE `%s` SET `%s` = %%s WHERE `id` = %%s" % (self.table, self.column),
            (value, record_id),
        ))

    def write_val(self, record_id: Any, value: Any, as_string: bool = False) -> bool:
        """``WriteVal($strId, $strVal, $bString)``（``sqlval.php:36``）：upsert。

        **两道闸门都照抄**：

        * 行已存在且**值没变**（数值模式用 ``MIN_FLOAT_VAL`` 容差，字符串模式
          用 ``==`` 比较）→ 直接返回 ``False``，不发 UPDATE。持仓链路每次渲染
          都可能顺手写一次仓位，没有这道闸门会把行翻来覆去地改；
        * 没有行 → ``INSERT``（不是 ``INSERT ... ON DUPLICATE KEY``，
          PHP 就是先查后插）。
        """
        if record_id is None or str(record_id) == "":
            return False
        record = self.get_record_by_id(record_id)
        if record is not None:
            old = record[self.column]
            if as_string:
                if str(value) == str(old):
                    return False
            else:
                if abs(_php_floatval(old) - _php_floatval(value)) < MIN_FLOAT_VAL:
                    return False
            return self.update(record_id, value)
        return self.insert(record_id, value)

    def delete_by_id(self, record_id: Any) -> int:
        """``DeleteById($strId)``：返回删除行数（PHP 返回布尔，这里给行数）。"""
        if record_id is None or str(record_id) == "":
            return 0
        return self._execute(
            "DELETE FROM `%s` WHERE `id` = %%s" % self.table, (record_id,)
        )


class IntStore(ValStore):
    """``(id, num)`` 两列的整数表 —— 对应 PHP ``IntSql``（``php/sql/sqlint.php:4``）。

    **字符串比较的读写方法都在这层**，不在父类 —— 与 PHP 的分层一致
    （``WriteString`` / ``ReadString`` / ``Add`` / ``Inc`` 全在 ``IntSql`` 上，
    ``ValSql`` 只有数值版的 ``WriteVal`` / ``ReadVal``）。
    ``PairSql`` 继承 ``IntSql`` 也是这个道理：配对表的比较方式是字符串。
    """

    column = "num"

    def read_string(self, record_id: Any) -> Any:
        """``ReadString()``：库里那串原值。"""
        return self.read_val(record_id, as_string=True)

    def read_int(self, record_id: Any) -> Any:
        """``ReadInt()``：读到空串/``'0'`` 时返回 ``None``（PHP ``false``）。

        PHP 原文是 ``if ($str = $this->ReadString($strId)) return intval($str);``
        —— ``'0'`` 在 PHP 里是**假值**，所以读回来 0 也走 ``return false``。
        这个"0 与没有同形"的口径照抄，调用方据此判断"要不要先 Add"。
        """
        text = self.read_string(record_id)
        if not text:
            return None
        try:
            return int(float(text))
        except (TypeError, ValueError):
            return None

    def write_string(self, record_id: Any, text: Any) -> bool:
        """``WriteString()``：字符串比较的 ``WriteVal``。"""
        return self.write_val(record_id, text, as_string=True)

    def write_int(self, record_id: Any, value: Any) -> bool:
        """``WriteInt()``：``WriteString(strval($iInt))``。"""
        return self.write_string(record_id, str(int(value)))

    def add(self, record_id: Any, delta: int) -> bool:
        """``Add($strId, $iInt)``：读-加-写；读不到当 0。"""
        current = self.read_int(record_id)
        if current is None:
            current = 0
        return self.write_int(record_id, current + int(delta))

    def inc(self, record_id: Any) -> bool:
        """``Inc()``。"""
        return self.add(record_id, 1)


class LastCalibrationStore(ValStore):
    """``lastcalibration``（``php/sql/sqlval.php:98``）：最近一次校准值。

    估值链路拿它当"没有当日校准值时的兜底"。编辑页清空校准值时
    （``_updateStockOptionCalibration``，``_submitstockoptions.php:343``）
    要**同时删掉这张表的行** —— 不删的话页面还会显示旧校准值。
    """

    table = "lastcalibration"


class GroupItemAmountStore(IntStore):
    """``groupitemamount``（``php/sql/sqlstockgroup.php:126``）：分组项申购金额。

    ``ReadAmount()`` 读不到回落 ``'100000'``。注意这张表的 ``id`` 是
    ``stockgroupitem.id``，**不是** ``stock_id``，所以要先用
    ``SqlGetMyStockGroupItemId($strMemberId, $strStockId)`` 换一次。
    """

    table = "groupitemamount"

    def read_amount(self, group_item_id: Any) -> str:
        """``ReadAmount($strGroupItemId)``：读不到给 ``'100000'``。"""
        text = self.read_string(group_item_id)
        return "100000" if not text else str(text)


# ---------------------------------------------------------------------------
# 二、配对表 —— PHP ``PairSql`` / ``StockPairSql``
# ---------------------------------------------------------------------------


class PairStore(IntStore):
    """``(id, stock_id)`` 的配对表：``id`` 是**本体**，``stock_id`` 是**配对标的**。

    对应 PHP ``PairSql``（``php/sql/sqlpair.php:4``）。四张表同一个形状，
    但**方向不能搞反** —— ``fundpair`` 里 ``id=SH513100, stock_id=^NDX``
    表示"SH513100 跟踪 ^NDX"，反过来查不到。

    ``PairSql`` 继承 ``IntSql`` 所以值列叫 ``stock_id`` 而不是 ``num``，
    且比较方式是**字符串**（``WritePair`` → ``WriteString``）—— 两边都是
    ``int`` 列，用字符串比较只是懒得转类型，效果一样。
    """

    column = "stock_id"

    def read_pair(self, record_id: Any) -> Any:
        """``ReadPair($strId)``：本体 → 配对标的 **stock_id**。"""
        return self.read_string(record_id)

    def write_pair(self, record_id: Any, pair_id: Any) -> bool:
        """``WritePair($strId, $strPairId)``。"""
        return self.write_string(record_id, pair_id)

    def delete_by_pair_id(self, pair_id: Any) -> int:
        """删掉所有"配对标的是它"的行（``Delete($strPairId)`` 的单条版）。

        PHP ``Delete($strPairId)`` 删的是 ``WHERE stock_id = pair_id`` 的**全部**
        行（``PairSql::Delete``，``sqlpair.php:27``）；一只 A 股只对应一只 H 股，
        实际最多一行。
        """
        if pair_id is None or str(pair_id) == "":
            return 0
        return self._execute(
            "DELETE FROM `%s` WHERE `stock_id` = %%s" % self.table, (pair_id,)
        )


class StockPairStore(PairStore):
    """配对表 + 符号解析 —— 对应 PHP ``StockPairSql``（``php/sql/sqlstockpair.php``）。

    符号与 ``stock_id`` 的互查要走 ``stock`` 表，所以构造时可以注入
    :class:`sql.stocksql.StockSql`（测试用假桩）；不注入就自己建一个。

    **与 PHP 的一处形制差异**：PHP ``GetSymbol`` / ``GetPairSymbol`` 取不到
    返回 ``false``，这里返回 ``None``（见 AGENTS.md 的"``false`` 映射为 ``None``"）。
    """

    def __init__(self, connection: Any, table: Optional[str] = None,
                 stock_sql: Any = None):
        super().__init__(connection, table=table)
        self.stock_sql = stock_sql if stock_sql is not None else StockSql(connection)

    def get_pair_symbol(self, symbol: str) -> Any:
        """``GetPairSymbol($strSymbol)``：本体符号 → 配对符号。"""
        stock_id = self.stock_sql.get_id(symbol)
        if not stock_id:
            return None
        pair_id = self.read_pair(stock_id)
        if not pair_id:
            return None
        return self.stock_sql.get_symbol(pair_id)

    def get_symbol(self, pair_symbol: str) -> Any:
        """``GetSymbol($strPairSymbol)``：配对符号 → 本体符号（反查）。"""
        pair_id = self.stock_sql.get_id(pair_symbol)
        if not pair_id:
            return None
        row = self._fetchone(
            "SELECT `id` FROM `%s` WHERE `stock_id` = %%s LIMIT 1" % self.table,
            (pair_id,),
        )
        if not row:
            return None
        return self.stock_sql.get_symbol(row["id"])

    def write_pair_symbol(self, symbol: str, pair_symbol: str) -> bool:
        """``WritePairSymbol($strSymbol, $strPairSymbol)``。

        任一符号不在 ``stock`` 表就返回 ``False``（PHP 同样返回 ``false``，
        调用方 ``_updateStockOptionFund`` 会 ``DebugString`` 一句"写失败"）。
        """
        stock_id = self.stock_sql.get_id(symbol)
        if not stock_id:
            return False
        pair_id = self.stock_sql.get_id(pair_symbol)
        if not pair_id:
            return False
        return self.write_pair(stock_id, pair_id)

    def delete_by_symbol(self, symbol: str) -> int:
        """``DeleteBySymbol($strSymbol)``：按**本体**删。"""
        stock_id = self.stock_sql.get_id(symbol)
        if not stock_id:
            return 0
        return self.delete_by_id(stock_id)

    def delete_by_pair_symbol(self, pair_symbol: str) -> int:
        """``DeleteByPairSymbol($strPairSymbol)``：按**配对侧**删。"""
        pair_id = self.stock_sql.get_id(pair_symbol)
        if not pair_id:
            return 0
        return self.delete_by_pair_id(pair_id)


class AhPairStore(StockPairStore):
    """``ahpair``：A 股 ↔ H 股。``id`` = A 股，``stock_id`` = H 股。"""

    table = "ahpair"


class AbPairStore(StockPairStore):
    """``abpair``：A 股 ↔ B 股。"""

    table = "abpair"


class AdrPairStore(StockPairStore):
    """``adrpair``：H 股 ↔ ADR。``id`` = **H 股**，``stock_id`` = ADR。

    方向容易搞反：``_updateStockOptionAdr($strSymbol /* H */, $strVal /* ADR */)``
    里写的是 ``WritePairSymbol($strAdr, $strSymbol)`` —— 本体是 ADR，
    配对才是 H 股。所以编辑页的值是 "ADR/比例"，而页面符号是 H 股。
    """

    table = "adrpair"


class FundPairStore(StockPairStore):
    """``fundpair``：基金 ↔ 它跟踪的指数 / 期货。``id`` = 基金。"""

    table = "fundpair"


# ---------------------------------------------------------------------------
# 三、日频表的子类 —— PHP ``sqldailyclose.php:267-320``
# ---------------------------------------------------------------------------


class FuturePremiumSql(DailyCloseSql):
    """``futurepremium``（``sqldailyclose.php:267``）：期货升水年化百分比。"""

    def __init__(self, connection: Any):
        super().__init__(connection, "futurepremium")


class StockEmaSql(DailyCloseSql):
    """``stockema50`` / ``stockema200``（``sqldailyclose.php:275``）。

    表名由天数拼出来（``'stockema'.strval($iDays)``）。**只有 50 与 200**
    两张表存在 —— PHP 的 ``GetStockEmaSql($iDays)`` 是 ``if ($iDays == 50)
    ... else ema200``，传别的天数会静默落到 200。这里照抄这个回落，
    并通过 :meth:`table_name_for` 把表名算出来，传错天数会得到一张不存在的表
    （写库时当场炸，比静默写错表诚实）。
    """

    @staticmethod
    def table_name_for(days: Any) -> str:
        """``'stockema' + strval($iDays)``。"""
        return "stockema" + str(int(days))

    def __init__(self, connection: Any, days: int = 200):
        super().__init__(connection, self.table_name_for(days))
        self.days = int(days)


class SharesDiffSql(DailyCloseSql):
    """``sharesdiff``（``sqldailyclose.php:307``）：场内份额新增（万）。

    **演示库里没有这张表** —— 生产端有，本地要写它得先建 DDL
    （``dbtools/seed_option_tables.py``）。读方法照常可用（表不存在会抛，
    服务层有 try 包住并显示"表不存在"）。
    """

    def __init__(self, connection: Any):
        super().__init__(connection, "sharesdiff")


class StockDividendSql(DailyCloseSql):
    """``stockdividend``（``sqldailyclose.php:291``）：每股分红。

    同 :class:`SharesDiffSql`，演示库里没有这张表。
    """

    def __init__(self, connection: Any):
        super().__init__(connection, "stockdividend")


class StockSplitSql(DailyCloseSql):
    """``stocksplit``（``sqldailyclose.php:283``）：拆股/合股比例。

    同 :class:`SharesDiffSql`，演示库里没有这张表。
    """

    def __init__(self, connection: Any):
        super().__init__(connection, "stocksplit")


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名便于对照源码）
# ---------------------------------------------------------------------------
ReadVal = ValStore.read_val
WriteVal = ValStore.write_val
ReadPair = PairStore.read_pair
WritePair = PairStore.write_pair
WritePairSymbol = StockPairStore.write_pair_symbol
GetPairSymbol = StockPairStore.get_pair_symbol
