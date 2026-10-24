"""分组与分组成员的 Sql 访问层。

对应 PHP ``php/sql/sqlstockgroup.php``（338 行）里的四个类与一串模块级函数：

* :class:`StockGroupSql` —— ``StockGroupSql extends KeyStringSql``
  （``sqlkeystring.php``），管 ``stockgroup`` 表：会员 id + 分组名 →
  分组 id。语义是"字符串键表"：``(member_id, groupname)`` 唯一。
* :class:`StockGroupItemSql` —— ``StockGroupItemSql extends KeyTableSql``
  （``sqlkeytable.php``），管 ``stockgroupitem`` 表：一个分组下挂哪些
  ``stock_id``，外加预统计的 ``quantity / cost / record`` 三列。
* ``GroupItemAmountSql`` / ``GroupItemExtraSql``（``sqlint.php`` 的
  ``IntSql``）—— 分组成员的**金额**与**扩展记录**两张附表。
  后台管理页用不到，本模块不迁方法，只在建表 DDL 里保持对称
  （见 ``dbtools/seed_stock_group_tables.py``）。

## 一、两张表在 PHP 里的建表语义

PHP 的 ``TableSql::__construct()`` 会**当场跑** ``CREATE TABLE IF NOT
EXISTS``（``sqltable.php:11``）——构造即建表。本模块不照搬这一点：
web 是长进程，每次构造都发 DDL 是浪费。建表改由
``dbtools/seed_stock_group_tables.py`` 显式执行（幂等），数据层只做
CRUD。这是与 PHP 的一处**已知偏差**，记录在 README 里。

两张表的实际结构（PHP 注释版 ``SqlCreateStockGroupItemTable`` 与
``KeyStringSql::Create()`` 拼出来）：

.. code-block:: text

    stockgroup      id AUTO_INCREMENT PK
                    member_id INT UNSIGNED      -- Add_id(TABLE_MEMBER)
                    groupname VARCHAR(64) utf8
                    UNIQUE (groupname, member_id)

    stockgroupitem  id AUTO_INCREMENT PK
                    stockgroup_id INT UNSIGNED   -- Add_id(TABLE_STOCK_GROUP)
                    stock_id INT UNSIGNED
                    quantity INT
                    cost DOUBLE(10,3)
                    record INT
                    INDEX (record)
                    UNIQUE (stock_id, stockgroup_id)

PHP 的 DDL 是 ``ENGINE = MYISAM``，所以两条 ``FOREIGN KEY`` 声明其实
**不生效**（MyISAM 忽略 FK），删分组不会真的级联删 item —— PHP 靠
``SqlDeleteStockGroupItemByGroupId()`` 手工删。我们的建表脚本照建
MyISAM，行为与 PHP 一致；数据层的删除也照 PHP 手工先删 item 再删组。

## 二、字符串键表的两个"静默失败"点（pitfalls 家族）

``KeyStringSql`` 的查表全是 ``WHERE member_id=? AND groupname=?``：
* 分组名在 PHP 里可以带 ``'@'`` 前缀（``_submitgroup.php:72`` 的重名
  保护），大小写敏感由 MySQL 的 collation 决定（``utf8_unicode_ci``
  **不**区分大小写）——本模块不做额外归一化，与库的判定保持一致；
* ``InsertString()`` 先查后插，重复名返回 ``None``（PHP 返回 ``false``）。
  并发下两个请求可能都查到"没有"然后双双插入，``UNIQUE`` 约束会挡下
  第二个——调用方拿到的就是那条约束错误。PHP 同款行为，不额外加锁。

## 三、数量列的字符串口径

PHP 的 ``_makePrivateFieldArray()`` 默认值是**字符串** ``'0' / '0.0'``
（``sqlstockgroup.php:69``），``GetStockIdArray($bCheckTransaction)`` 读
``record`` 时也要 ``intval()`` 才敢比 0。pymysql 对 INT 列返回 Python
``int``，所以本模块直转 ``int()`` 即可；但 ``cost`` 是 DOUBLE，PHP 侧
``floatval``、本模块 ``float()``。这些转换写在读写两端，不让调用方
猜库列的类型。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

#: 演示会员 id。PHP 后台页的写操作挂在登录会员下（``_submitgroup.php``
#: 的 ``$strLoginId``）；Python 后台不接登录态（账号体系暂缓），
#: 所有分组都归到这个会员。``dbtools/seed_stock_group_tables.py`` 灌的
#: 演示会员同 id。见 README_stockgroupsql_migration.md §五。
DEMO_MEMBER_ID = 1


# ---------------------------------------------------------------------------
# 小工具：PHP 的字符串真假 / intval / floatval 语义
# ---------------------------------------------------------------------------

def _php_truthy(value: Any) -> bool:
    """PHP 真假判断：``''`` / ``'0'`` / ``False`` / ``None`` 都是假值。

    服务对象是 ``GetStockIdArray($bCheckTransaction)`` 的
    ``if ($bCheckTransaction)`` 与 ``intval($record['record']) == 0``
    这类判断 —— 前者是布尔参数（调用方不会传怪值），这里主要给后者兜底。
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value not in ("", "0")
    return bool(value)


def _int(value: Any, default: int = 0) -> int:
    """PHP ``intval()`` 的常用子集：None / '' / 'abc' 都落 default。

    PHP 的 ``intval('12abc')`` 得 12，本模块用不到那么细——库里的
    ``record`` / ``quantity`` 是 INT 列，pymysql 已给 int；这个函数主要
    挡"从表单字符串灌进来"的路径（后台页的 quantity 输入框）。
    """
    if value is None or value is False:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        # PHP intval('3.7') == 3：先按 float 截断再取整
        try:
            return int(float(text))
        except ValueError:
            return default


def _float(value: Any, default: float = 0.0) -> float:
    """PHP ``floatval()`` 的常用子集：None / '' / 非数字落 default。"""
    if value is None or value is False:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# stockgroup：会员 id + 分组名（KeyStringSql 语义）
# ---------------------------------------------------------------------------

class StockGroupSql:
    """对应 ``php/sql/sqlstockgroup.php:7`` 的 ``StockGroupSql``。

    构造参数即 ``parent::__construct(TABLE_STOCK_GROUP, TABLE_MEMBER,
    'groupname', 64)`` 四件套：表名、键前缀（决定 ``member_id`` 列名）、
    字符串列名、最大长度。

    PHP 的 ``KeyStringSql`` 继承 ``KeySql`` → ``TableSql``，方法全是
    "拼 WHERE 再查"。本模块把 WHERE 拼装内联到各方法里，语义逐条对齐，
    不保留拼 SQL 的中间层（Python 侧参数化查询更安全，且调用方只有
    后台页与种子脚本两处）。
    """

    TABLE = "stockgroup"
    #: ``Add_id(TABLE_MEMBER)``（sqltable.php:18）= ``TABLE_MEMBER.'_id'``
    KEY = "member_id"
    #: ``KeyStringSql`` 的 ``$strStringName``
    STRING_NAME = "groupname"
    #: ``KeyStringSql`` 的 ``$iMaxStringLen``
    MAX_LEN = 64

    def __init__(self, source: Any):
        # source 是带 .connection（DB-API）的对象，如 MySqlFundDataSource；
        # 直接传连接也行（web/routes 里两者都见过，这里只用到 cursor）。
        self._connection = getattr(source, "connection", source)

    # -- 内部小工具 -------------------------------------------------------
    def _fetchone(self, sql: str, params: tuple) -> dict | None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    def _fetchall(self, sql: str, params: tuple = ()) -> list:
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    def _execute(self, sql: str, params: tuple) -> int:
        """执行写语句，返回受影响行数。"""
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params)
            return int(cursor.rowcount or 0)
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    # -- 查 ----------------------------------------------------------------
    def get_record(self, member_id: Any, group_name: str) -> dict | None:
        """对应 ``GetRecord($strKeyId, $strString)``：按会员 + 组名取整行。"""
        return self._fetchone(
            "SELECT * FROM %s WHERE %s = %%s AND %s = %%s LIMIT 1"
            % (self.TABLE, self.KEY, self.STRING_NAME),
            (member_id, group_name),
        )

    def get_group_id(self, member_id: Any, group_name: str) -> str | None:
        """对应 ``GetGroupId()`` → ``GetRecordId()``：拿分组 id。

        找不到返回 ``None``（PHP 返回 ``false``）。调用方（后台新建
        分支）靠这个回读刚插入的分组 id。
        """
        record = self.get_record(member_id, group_name)
        return str(record["id"]) if record else None

    def get_string(self, group_id: Any) -> str | None:
        """对应 ``GetString($strId)``：按 id 拿组名（编辑表单回显用）。"""
        record = self.get_record_by_id(group_id)
        if not record:
            return None
        return str(record[self.STRING_NAME])

    def get_record_by_id(self, group_id: Any) -> dict | None:
        """对应 ``GetRecordById()``：按 id 取整行（含 member_id）。"""
        return self._fetchone(
            "SELECT * FROM %s WHERE id = %%s LIMIT 1" % self.TABLE, (group_id,)
        )

    def get_all(self, member_id: Any = False) -> list:
        """对应 ``GetAll($strKeyId)``：某会员的全部分组。

        ``KeyStringSql::BuildOrderBy()`` 是 ``groupname ASC`` —— 后台
        列表页照此排序。``member_id=False`` 时 PHP 会拼出
        ``WHERE member_id = ''`` 返回空，本模块同样传空串即可，
        不特判（保持与 PHP 一样的"调用方传对值"约定）。
        """
        sql = "SELECT * FROM %s WHERE %s = %%s ORDER BY %s ASC" % (
            self.TABLE, self.KEY, self.STRING_NAME)
        return self._fetchall(sql, (member_id,))

    def count_by_string(self, group_name: str) -> int:
        """对应 ``CountByString()``：重名检测（不限会员）。"""
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM %s WHERE %s = %%s"
            % (self.TABLE, self.STRING_NAME), (group_name,))
        return _int(row["n"]) if row else 0

    # -- 写 ----------------------------------------------------------------
    def insert_string(self, member_id: Any, group_name: str) -> int | None:
        """对应 ``InsertString()``：插新分组，返回自增 id。

        先查后插（PHP 同款）：已存在返回 ``None``。分组名超长时 PHP 的
        ``KeyNameSql::InsertKey`` 会截断——但那是 ``KeyNameSql`` 的语义，
        ``KeyStringSql::InsertString`` **不截断**（超长直接吃 MySQL 错误
        或严格模式报错）。本模块照 ``KeyStringSql``：不截断，让调用方
        （后台表单）先用 ``maxlength`` 挡一层。
        """
        if self.get_record(member_id, group_name):
            return None
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO %s (%s, %s) VALUES (%%s, %%s)"
                % (self.TABLE, self.KEY, self.STRING_NAME),
                (member_id, group_name),
            )
            return int(cursor.lastrowid or 0)
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    def update_string(self, group_id: Any, group_name: str) -> int:
        """对应 ``UpdateString()``：改组名，返回受影响行数。"""
        return self._execute(
            "UPDATE %s SET %s = %%s WHERE id = %%s LIMIT 1"
            % (self.TABLE, self.STRING_NAME),
            (group_name, group_id),
        )

    def delete_by_id(self, group_id: Any) -> int:
        """对应 ``DeleteById()``：删分组行本身。

        **不会**级联删 item —— MyISAM 的 FK 不生效，PHP 也是先调
        ``SqlDeleteStockGroupItemByGroupId()`` 再删组。后台页照此顺序，
        别只调这一个方法。

        无 ``LIMIT 1``：PHP 的 ``DeleteById → DeleteData(where, false)``
        不传 limit（``_SqlAddLimit(false)`` 得空串）。id 是主键，
        功能等价，但逐例对照要求形态一致，照 PHP。
        """
        return self._execute(
            "DELETE FROM %s WHERE id = %%s" % self.TABLE, (group_id,)
        )

    def delete_by_string(self, group_name: str) -> int:
        """对应 ``DeleteByString()``：按组名删（空名返回 0，PHP 同款守卫）。"""
        if not _php_truthy(group_name):
            return 0
        return self._execute(
            "DELETE FROM %s WHERE %s = %%s" % (self.TABLE, self.STRING_NAME),
            (group_name,),
        )


# ---------------------------------------------------------------------------
# stockgroupitem：一个分组下的成员（KeyTableSql 语义）
# ---------------------------------------------------------------------------

class StockGroupItemSql:
    """对应 ``php/sql/sqlstockgroup.php:21`` 的 ``StockGroupItemSql``。

    键是**分组 id**（``Add_id(TABLE_STOCK_GROUP)`` = ``stockgroup_id``），
    所以每个实例管一个分组的成员集合；PHP 的 ``new StockGroupItemSql()``
    不传键时是"无分组实例"（``DeleteAll`` 直接拒绝、``GetAll`` 拼出
    ``WHERE stockgroup_id = ''``）。本模块保留同一形状：``group_id=None``
    表示无分组，写操作照 PHP 拒绝或让调用方别那么用。

    PHP 的 ``$this->trans_sql = new StockTransactionSql()``
    （``sqlstockgroup.php:28``）在本模块对应 :attr:`trans_sql` 属性 ——
    **懒造 + 可注入**：PHP 两个 Sql 类同层互引，Python 的分层是
    ``sql → core``、反向禁止（``sql/__init__.py`` 的依赖方向），故不能
    顶部 import，只能延迟到第一次用到时。测试可预先给
    ``item_sql._trans_sql`` 注假冒身避免连库。
    """
    TABLE = "stockgroupitem"
    #: ``Add_id(TABLE_STOCK_GROUP)``
    KEY = "stockgroup_id"

    def __init__(self, source: Any, group_id: Any = None):
        self._connection = getattr(source, "connection", source)
        self.group_id = group_id
        #: PHP 的 ``$this->trans_sql = new StockTransactionSql()``
        #: （``sqlstockgroup.php:28``）—— 交易表访问入口。**懒造**：
        #: PHP 侧两个 Sql 类同层互引，而 Python 的分层是
        #: ``sql → core``、反向禁止（``sql/__init__.py`` 的依赖方向），
        #: 所以这里不能顶部 import，只能在第一次用到时延迟 import。
        #: 测试可注假冒身（``trans_sql_override``）避免连库。
        self._trans_sql = None

    @property
    def trans_sql(self) -> Any:
        """对应 PHP 的 ``GetTransSql()`` / 直接属性访问 ``$sql->trans_sql``。"""
        if self._trans_sql is None:
            from sql.stocktransactiontable import StockTransactionSql
            self._trans_sql = StockTransactionSql(self._connection)
        return self._trans_sql

    # -- 内部小工具（与 StockGroupSql 同款，不继承以避免暴露 SQL 拼装） --
    def _fetchone(self, sql: str, params: tuple) -> dict | None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    def _fetchall(self, sql: str, params: tuple = ()) -> list:
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    def _execute(self, sql: str, params: tuple) -> int:
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params)
            return int(cursor.rowcount or 0)
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    def _execute_lastrowid(self, sql: str, params: tuple) -> int | None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, params)
            return int(cursor.lastrowid or 0)
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    # -- 查 ----------------------------------------------------------------
    def get_all(self, start: int = 0, num: int = 0) -> list:
        """对应 ``GetAll($iStart, $iNum)``：本分组的全部成员。

        ``KeyTableSql::BuildOrderBy()`` 是 ``id ASC``（插入顺序）。
        LIMIT 语义照 ``_SqlBuildLimit``（``_sqlcommon.php:63``）三个分支：

        * ``(0, 0)`` → 无 LIMIT（``false``）；
        * ``(0, n)`` → ``LIMIT n``；
        * ``(s, n)`` → ``LIMIT s , n``（**逗号两侧有空格**，PHP 是
          ``strval($iStart).' , '.strval($iNum)``）。

        曾经写成"只有 start/num 都非零才加 LIMIT"——漏掉 ``(0, n)``
        分支，且 ``LIMIT s, n`` 少个空格。是 PHP 逐例对照
        （``_php_stockgroup_compare.py`` 的 ``limit_zero_num`` 例）抓出来的。
        """
        if self.group_id is None:
            return []
        sql = "SELECT * FROM %s WHERE %s = %%s ORDER BY id ASC" % (
            self.TABLE, self.KEY)
        params: list = [self.group_id]
        start_i, num_i = _int(start), _int(num)
        if start_i == 0:
            if num_i != 0:
                sql += " LIMIT %s" % num_i
        else:
            sql += " LIMIT %s , %s" % (start_i, num_i)
        return self._fetchall(sql, tuple(params))

    def get_stock_id_array(self, check_transaction: bool = False) -> dict:
        """对应 ``GetStockIdArray($bCheckTransaction)``：``{item_id: stock_id}``。

        ``$bCheckTransaction`` 为真时跳过 ``record == 0`` 的成员 ——
        "分组里预定了标的但一笔都没成交"的人不出现在交易集合里
        （``MyStockGroup`` 构造函数同款过滤）。注意 PHP 的键是
        ``$ar[$record['id']]``（字符串数字键），Python dict 不认
        ``'142'`` === ``142`` —— 本模块统一转成 ``str`` 键，
        调用方要按字符串取（pitfalls #71/#72 家族的老坑，别再踩）。
        """
        result = {}
        for record in self.get_all():
            if _php_truthy(check_transaction) and _int(record.get("record")) == 0:
                continue
            result[str(record["id"])] = str(record["stock_id"])
        return result

    def get_stock_id(self, item_id: Any) -> str | None:
        """对应 ``GetStockId($strId)``：按 item id 拿 stock_id。"""
        record = self.get_record_by_id(item_id)
        return str(record["stock_id"]) if record else None

    def get_record_by_id(self, item_id: Any) -> dict | None:
        """对应 ``GetRecordById()``：按 item id 取整行。"""
        return self._fetchone(
            "SELECT * FROM %s WHERE id = %%s LIMIT 1" % self.TABLE, (item_id,)
        )

    def get_record(self, stock_id: Any) -> dict | None:
        """对应 ``GetRecord($strStockId)``：本分组里某个 stock 的整行。

        WHERE 是 ``stockgroup_id=? AND stock_id=?``（``BuildWhere_key_stock``），
        所以**只在绑定了分组**的实例上有意义。
        """
        if self.group_id is None:
            return None
        return self._fetchone(
            "SELECT * FROM %s WHERE %s = %%s AND stock_id = %%s LIMIT 1"
            % (self.TABLE, self.KEY),
            (self.group_id, stock_id),
        )

    def get_id(self, stock_id: Any) -> str | None:
        """对应 ``TableSql::GetId($strVal, 'GetRecord')``：stock → item id。"""
        record = self.get_record(stock_id)
        return str(record["id"]) if record else None

    def get_id_array(self) -> list:
        """对应 ``GetIdArray()``：本分组全部 item id（``CountAllStockTransaction``
        等"整组扫一遍"的入口）。无分组实例返回空表——PHP 那里
        ``GetAll`` 拼出 ``WHERE stockgroup_id = ''`` 也是空。"""
        return [str(record["id"]) for record in self.get_all()]

    def count(self) -> int:
        """对应 ``KeyTableSql::Count()``：本分组成员数。"""
        if self.group_id is None:
            return 0
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM %s WHERE %s = %%s" % (self.TABLE, self.KEY),
            (self.group_id,))
        return _int(row["n"]) if row else 0

    def count_by_stock_id(self, stock_id: Any) -> int:
        """对应 ``CountByStockId()``：某 stock 出现在多少个分组里。

        注意 WHERE 只有 ``stock_id``（``_SqlBuildWhere_stock``）——
        与是否绑分组无关，用于删股票前的"还有分组在引用"检查。
        """
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM %s WHERE stock_id = %%s" % self.TABLE,
            (stock_id,))
        return _int(row["n"]) if row else 0

    # -- 交易查询（``sqlstockgroup.php:84-116`` 的四个方法） ------------------
    # 这一组是"按 stock_id 问交易"的便捷层：先把 stock_id 换成本分组的
    # groupitem_id，再转发给 trans_sql。PHP 取不到成员时给 false / 0。
    def count_stock_transaction(self, stock_id: Any) -> int:
        """对应 ``CountStockTransaction($strStockId)``。

        取不到成员（本分组没这只股票）→ **0**（PHP 同款，注意与
        :meth:`count` 的空键返回 ``None`` 不同：这里先过 ``GetId``）。
        """
        item_id = self.get_id(stock_id)
        if item_id is None:
            return 0
        count = self.trans_sql.count(item_id)
        return 0 if count is None else int(count)

    def get_stock_transaction(self, stock_id: Any, start: Any = 0,
                              num: Any = 0) -> Any:
        """对应 ``GetStockTransaction($strStockId, $iStart, $iNum)``。

        取不到成员 → ``None``（PHP 给 ``false``）。有成员但没交易时
        ``trans_sql.get_record`` 给空列表（PHP 的 ``GetData`` 无行也返回
        ``false`` —— 这里统一成空列表，调用方判空的方式一致）。
        """
        item_id = self.get_id(stock_id)
        if item_id is None:
            return None
        return self.trans_sql.get_record(item_id, start, num) or []

    def count_all_stock_transaction(self) -> int:
        """对应 ``CountAllStockTransaction()``：本分组全部成员的交易总数。"""
        item_ids = self.get_id_array()
        if not item_ids:
            return 0
        return self.trans_sql.count_all(item_ids)

    def get_all_stock_transaction(self, start: Any = 0, num: Any = 0) -> Any:
        """对应 ``GetAllStockTransaction($iStart, $iNum)``。

        分组无成员 → ``None``（PHP 给 ``false``）。排序同样是
        ``filled`` DESC（``trans_sql.get_all`` 的约定）。
        """
        item_ids = self.get_id_array()
        if not item_ids:
            return None
        return self.trans_sql.get_all(item_ids, start, num) or []

    # -- 写 ----------------------------------------------------------------
    def insert(self, stock_id: Any) -> int | None:
        """对应 ``Insert($strStockId)``：往本分组加一个成员。

        字段形状照 ``InsertArrays($this->MakeFieldKeyId(),
        array('stock_id' => ...), $this->_makePrivateFieldArray())``：
        ``stockgroup_id + stock_id + quantity/cost/record 全 0``。

        **已知偏差**：PHP 的 ``InsertArray`` 在数组无 ``id`` 键时会拼
        ``INSERT INTO t(id, ...) VALUES('0', ...)``（MyISAM 的自增惯例），
        本模块直接省掉 id 列让 MySQL 自增——同为 MyISAM，效果一样，
        且不依赖 ``'0'`` 这个魔法值。

        重复插入（同组同 stock）会被 ``UNIQUE (stock_id, stockgroup_id)``
        挡下；PHP 同款。后台页的编辑分支走 :func:`sql_update_stock_group`
        的 diff，不会到这里撞约束。
        """
        if self.group_id is None:
            return None
        return self._execute_lastrowid(
            "INSERT INTO %s (%s, stock_id, quantity, cost, record) "
            "VALUES (%%s, %%s, 0, 0.0, 0)" % (self.TABLE, self.KEY),
            (self.group_id, stock_id),
        )

    def update(self, item_id: Any, quantity: Any = "0", cost: Any = "0.0",
               record: Any = "0") -> int:
        """对应 ``Update($strId, $strQuantity, $strCost, $strRecord)``。

        默认值是 PHP ``_makePrivateFieldArray()`` 的字符串
        ``'0' / '0.0' / '0'``；这里收任何类型再转（``_int`` / ``_float``），
        因为后台表单传进来的是字符串。``record`` 是"预统计的成交笔数"，
        由交易体系维护——**后台页不暴露这一列**，只透传默认 0。
        """
        return self._execute(
            "UPDATE %s SET quantity = %%s, cost = %%s, record = %%s "
            "WHERE id = %%s LIMIT 1" % self.TABLE,
            (_int(quantity), _float(cost), _int(record), item_id),
        )

    def delete_by_id(self, item_id: Any) -> int:
        """对应 ``DeleteById()``：删一个成员。

        **只删 item 行，不碰交易** —— PHP 的 ``DeleteById`` 同样不删，
        交易清理在**调用方**（``SqlUpdateStockGroup`` 移除分支、
        ``SqlDeleteStockGroupItemByGroupId`` 先 ``trans_sql->Delete``
        再调本方法）。2026-10-24 交易表迁完后，那两个调用方已反填
        （见 :func:`sql_update_stock_group` 与
        :func:`sql_delete_stock_group_item_by_group_id`）。

        无 ``LIMIT 1``：PHP 的 ``DeleteById`` 不传 limit，同
        :meth:`StockGroupSql.delete_by_id` 的说明。
        """
        return self._execute(
            "DELETE FROM %s WHERE id = %%s" % self.TABLE, (item_id,)
        )

    def delete_all(self) -> int:
        """对应 ``DeleteAll()``：删本分组全部成员。无分组实例拒绝（PHP 同款）。"""
        if self.group_id is None:
            return 0
        return self._execute(
            "DELETE FROM %s WHERE %s = %%s" % (self.TABLE, self.KEY),
            (self.group_id,),
        )


# ---------------------------------------------------------------------------
# 模块级函数（对应 sqlstockgroup.php 179 行以后的自由函数）
# ---------------------------------------------------------------------------

def sql_get_stock_group_id(source: Any, item_id: Any) -> str | None:
    """对应 ``SqlGetStockGroupId($strGroupItemId)``：item id → 分组 id。

    PHP 用 ``GetRecordById`` 拿 ``stockgroup_id``。用于"从交易反查
    它属于哪个分组"（资金汇总段落），后台页暂不用。
    """
    item_sql = StockGroupItemSql(source)
    record = item_sql.get_record_by_id(item_id)
    return str(record["stockgroup_id"]) if record else None


def sql_get_stock_group_item_id(source: Any, group_id: Any,
                                stock_id: Any) -> str | None:
    """对应 ``SqlGetStockGroupItemId($strGroupId, $strStockId)``。"""
    item_sql = StockGroupItemSql(source, group_id)
    return item_sql.get_id(stock_id)


def sql_get_stocks_array(source: Any, group_id: Any,
                         check_transaction: bool = False) -> list:
    """对应 ``SqlGetStocksArray($strGroupId, $bCheckTransaction)``。

    把分组的 stock_id 逐个翻成符号（``SqlGetStockSymbol``）。顺序即
    ``GetAll`` 的 ``id ASC``——PHP 注释里明说**不能** ``sort()``：
    那会打乱用户自己排列的成员顺序。本模块同样不排序。
    """
    item_sql = StockGroupItemSql(source, group_id)
    stock_ids = item_sql.get_stock_id_array(check_transaction=check_transaction)
    if not stock_ids:
        return []
    symbols = []
    for _item_id, stock_id in stock_ids.items():
        symbol = _stock_symbol_of(source, stock_id)
        if symbol:
            symbols.append(symbol)
    return symbols


def _stock_symbol_of(source: Any, stock_id: Any) -> str | None:
    """``SqlGetStockSymbol()`` 的替身。

    source 可能带 ``get_stock_symbol``（``MySqlFundDataSource``），
    也可能只是裸连接——后者退回一条 SQL。测试里传假 source 即可。
    """
    getter = getattr(source, "get_stock_symbol", None)
    if getter is not None:
        return getter(stock_id)
    connection = getattr(source, "connection", source)
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT symbol FROM stock WHERE id = %s LIMIT 1", (stock_id,))
        row = cursor.fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            return str(row["symbol"])
        return str(row[0])
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()


def sql_update_stock_group(source: Any, group_id: Any,
                           new_stock_ids: Sequence[Any]) -> dict:
    """对应 ``SqlUpdateStockGroup($strGroupId, $arNew)``：把分组成员改成
    给定集合（diff 更新）。

    PHP 的算法（``sqlstockgroup.php:260``）：

    1. 读旧集合 ``arOld``；
    2. ``arNew`` 里不在 ``arOld`` 的 → ``Insert``；
    3. ``arOld`` 里不在 ``arNew`` 的 → 先 ``trans_sql->Delete`` 再
       ``DeleteById``。

    第 3 步的交易清理 2026-10-24 已反填（stocktransaction 表迁完）：
    删成员前先把它的成交记录清掉，否则会留下**孤儿交易**（MyISAM 的
    FK 被静默忽略，没有级联）。

    返回 ``{'added': [...], 'removed': [...]}`` 给后台页做结果提示
    （PHP 只 ``trigger_error`` 一行 debug，页面无反馈；我们给个反馈，
    这是**新增行为**，不算偏差）。
    """
    item_sql = StockGroupItemSql(source, group_id)
    old_map = item_sql.get_stock_id_array()          # {item_id: stock_id}
    old_stock_ids = set(old_map.values())
    new_set = {str(stock_id) for stock_id in new_stock_ids}

    added = []
    for stock_id in new_stock_ids:
        if str(stock_id) not in old_stock_ids:
            item_sql.insert(stock_id)
            added.append(str(stock_id))

    removed = []
    for item_id, stock_id in old_map.items():
        if stock_id not in new_set:
            # PHP: $sql->trans_sql->Delete($strId); $sql->DeleteById($strId);
            item_sql.trans_sql.delete(item_id)
            item_sql.delete_by_id(item_id)
            removed.append(stock_id)

    return {"added": added, "removed": removed}


def sql_delete_stock_group_item_by_group_id(source: Any, group_id: Any) -> int:
    """对应 ``SqlDeleteStockGroupItemByGroupId($strGroupId)``。

    删一个分组的全部成员。PHP 对每个 item 先 ``trans_sql->Delete``
    （``sqlstockgroup.php:296-300``）—— 2026-10-24 已反填。
    返回删掉的 item 数。
    """
    item_sql = StockGroupItemSql(source, group_id)
    # PHP 对每个 item 先删交易再 DeleteAll；本模块用一条
    # ``groupitem_id IN (...)`` 批量删交易，再删全部 item
    # （语义等价：MyISAM 无级联，交易必须走在 item 前面）。
    item_ids = item_sql.get_id_array()
    if item_ids:
        item_sql.trans_sql.delete_all_by_ids(item_ids)
    return item_sql.delete_all()


def sql_delete_stock_group(source: Any, group_id: Any) -> dict:
    """删分组（item 先行，再删组行）—— PHP 散落在
    ``_submitgroup.php:32`` 的 ``_onDelete`` 里，这里收成一个函数。

    顺序很重要：MyISAM 无级联，先删组会留下孤儿 item。
    返回 ``{'items': n, 'group': 0|1}``。
    """
    items = sql_delete_stock_group_item_by_group_id(source, group_id)
    group_sql = StockGroupSql(source)
    group = group_sql.delete_by_id(group_id)
    return {"items": items, "group": group}


def sql_delete_stock_group_by_member_id(source: Any, member_id: Any) -> int:
    """对应 ``SqlDeleteStockGroupByMemberId($strMemberId)``：删某会员的
    全部分组（注销账号时的清理）。逐组走 :func:`sql_delete_stock_group`。"""
    group_sql = StockGroupSql(source)
    deleted = 0
    for record in group_sql.get_all(member_id):
        sql_delete_stock_group(source, record["id"])
        deleted += 1
    return deleted


def sql_get_my_stock_group_item_id(source: Any, member_id: Any,
                                   stock_id: Any) -> str | None:
    """对应 ``SqlGetMyStockGroupItemId($strMemberId, $strStockId)``。

    "我的自选"路径：分组名恰好等于股票符号（``SqlGetStockSymbol``），
    在该会员下找到这个分组后再找成员。后台页用不到，保留只为对齐
    PHP 的自由函数全集（README 有清单）。
    """
    group_sql = StockGroupSql(source)
    symbol = _stock_symbol_of(source, stock_id)
    if not symbol:
        return None
    group_id = group_sql.get_group_id(member_id, symbol)
    if not group_id:
        return None
    return sql_get_stock_group_item_id(source, group_id, stock_id)


def get_input_stock_id_array(source: Any, symbols_text: str,
                             ref_builder=None) -> list:
    """对应 ``woody/res/php/_submitgroup.php:5`` 的 ``_getStockIdArray()``。

    后台表单的 ``symbols`` 多行文本 → stock_id 列表。PHP 的逻辑：

    1. ``GetInputSymbolArray($strSymbols)`` 把逗号/换行/空格混排的文本
       切成符号数组（含 ``StockGetSymbol`` 式的大小写归一）；
    2. 每个符号先 ``SqlGetStockId`` 查库；
    3. 查不到就 ``StockGetReference($strSymbol)`` 建引用对象，
       ``HasData()`` 为真才拿 ``GetStockId()``（顺带**入库**），
       否则 ``continue``（静默跳过）。

    第 3 步的"建引用顺带入库"依赖 reference 体系（``MysqlReference``
    构造即写 stock 表），Python 侧的替身是 ``ref_builder`` 回调：
    * 传了：``ref_builder(symbol)`` 返回带 ``has_data`` / ``get_stock_id``
      的对象，照 PHP 取 id；
    * 没传：查不到库就跳过（演示环境可接受，README 有记）。

    切分规则照 ``GetInputSymbolArray``：逗号、分号、空白（含换行）都算
    分隔符，空段忽略。
    """
    if not symbols_text:
        return []
    separators = ",;\t\r\n "
    normalized = symbols_text
    for sep in separators:
        normalized = normalized.replace(sep, " ")
    result = []
    seen = set()
    for token in normalized.split(" "):
        symbol = token.strip()
        if not symbol:
            continue
        # PHP GetInputSymbolArray 对 sz161125 这类小写做 strtoupper
        # （StockGetSymbol 语义：无下划线才大写，外汇符号 fx_susdcny 不动）
        if "_" not in symbol:
            symbol = symbol.upper()
        if symbol in seen:
            continue
        seen.add(symbol)
        stock_id = _get_stock_id(source, symbol)
        if stock_id is None and ref_builder is not None:
            ref = ref_builder(symbol)
            if ref is not None and _ref_has_data(ref):
                stock_id = _ref_stock_id(ref)
        if stock_id is None:
            continue
        result.append(str(stock_id))
    return result


def _get_stock_id(source: Any, symbol: str) -> str | None:
    """``SqlGetStockId()`` 的替身（同 :func:`_stock_symbol_of` 的注入风格）。"""
    getter = getattr(source, "get_stock_id", None)
    if getter is not None:
        return getter(symbol)
    connection = getattr(source, "connection", source)
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s LIMIT 1", (symbol,))
        row = cursor.fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            return str(row["id"])
        return str(row[0])
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()


def _ref_has_data(ref: Any) -> bool:
    """PHP ``$ref->HasData()``。

    引用对象的 ``has_data`` 在 Python 侧是**属性**（``refobj.py`` 的
    ``StockReference.has_data``），不是方法；PHP 才是方法调用。这里两种
    形状都接：能调就调，是值就直接用。
    """
    value = getattr(ref, "has_data", None)
    if value is None:
        value = getattr(ref, "HasData", None)
    if callable(value):
        value = value()
    return _php_truthy(value)


def _ref_stock_id(ref: Any) -> str | None:
    """PHP ``$ref->GetStockId()``（方法；值形状也接）。"""
    value = getattr(ref, "get_stock_id", None)
    if value is None:
        value = getattr(ref, "GetStockId", None)
    if callable(value):
        value = value()
    if value in (None, False, ""):
        return None
    return str(value)


def is_group_name_reserved(group_name: str, all_symbols: Iterable[str]) -> bool:
    """对应 ``in_arrayAll($strGroupName)``（``stocksymbol.php:377``）：
    分组名是否与某个**股票符号**重名。

    重名时 PHP 给组名加 ``'@'`` 前缀（``_submitgroup.php:72``），
    因为"符号同名的分组"是系统自动产生的自选分组，用户分组不能占。
    ``all_symbols`` 由调用方给符号全集（``GetAllSymbolArray`` 的替身）——
    本模块不自己拉全库符号，后台页按需传。
    """
    return group_name in set(all_symbols)


def prefix_reserved_group_name(group_name: str) -> str:
    """对应 ``_submitgroup.php:72``：重名组名加 ``'@'`` 前缀。

    编辑分支（``_submitgroup.php:59``）对**已有**的重名分组是反过来的：
    提交的组名被丢弃、保留旧名 —— 那个判断在路由层做（要读旧组名），
    这里只提供新建分支的前缀化。
    """
    if group_name.startswith("@"):
        return group_name
    return "@" + group_name


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：方法保留 camelCase 别名）
# ---------------------------------------------------------------------------

for _camel, _snake in (
    ("GetRecord", "get_record"),
    ("GetGroupId", "get_group_id"),
    ("GetString", "get_string"),
    ("GetRecordById", "get_record_by_id"),
    ("GetAll", "get_all"),
    ("CountByString", "count_by_string"),
    ("InsertString", "insert_string"),
    ("UpdateString", "update_string"),
    ("DeleteById", "delete_by_id"),
    ("DeleteByString", "delete_by_string"),
):
    if hasattr(StockGroupSql, _snake):
        setattr(StockGroupSql, _camel, getattr(StockGroupSql, _snake))

for _camel, _snake in (
    ("GetStockIdArray", "get_stock_id_array"),
    ("GetStockId", "get_stock_id"),
    ("GetId", "get_id"),
    ("GetIdArray", "get_id_array"),
    ("Count", "count"),
    ("CountByStockId", "count_by_stock_id"),
    ("Insert", "insert"),
    ("Update", "update"),
    ("DeleteAll", "delete_all"),
    #: 交易查询四件（``sqlstockgroup.php:84-116``）
    ("CountStockTransaction", "count_stock_transaction"),
    ("GetStockTransaction", "get_stock_transaction"),
    ("CountAllStockTransaction", "count_all_stock_transaction"),
    ("GetAllStockTransaction", "get_all_stock_transaction"),
):
    if hasattr(StockGroupItemSql, _snake):
        setattr(StockGroupItemSql, _camel, getattr(StockGroupItemSql, _snake))

del _camel, _snake
