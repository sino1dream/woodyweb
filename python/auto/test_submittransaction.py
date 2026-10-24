"""``core/submittransaction.py``（交易写入口七动作）的单元测试。

对应 PHP ``woody/res/php/_submittransaction.php``。用内存假库（不连真库）：
本模块的 SQL 涉及 ``stockgroup`` / ``stockgroupitem`` /
``stocktransaction`` / ``stock`` **四张表**，fake 按"解释 SQL 子集"
实现（与 ``test_stocktransactiontable.py`` 同款思路，不逐个方法分派）。

重点验证的语义：

1. **权限闸门**：``IsGroupReadOnly`` 是"分组不属于当前登录用户"，
   且 group_id 为假值时**不**只读（PHP 的短路）；
2. **只有 delete 分支管理员绕过只读**（``IsAdmin()`` 的旁路）；
3. **合并方向由 ``type0`` 决定**，合并后**两边**成员都要重算；
4. **``UpdateStockGroupItem`` 重算整组**（item_id 只做第二个闸门）；
5. **成本含手续费**（``AddSqlTransaction`` 的公式）；
6. **adjust 保持成交额不变反推份数**、**empty 只清 remark**。
"""

import re
import unittest

from core.submittransaction import (
    DISP_ORDER,
    FUND_FEE_RATIO_DEFAULT,
    SUBMIT_EDIT,
    SUBMIT_MERGE_CN,
    SUBMIT_NEW,
    SubmitTransactionAccount,
    get_arbitrage_quantity,
    stock_get_fund_fee_ratio,
    update_stock_group_item,
    update_stock_group_item_transaction,
)
from core.stockgroupsql import StockGroupItemSql


# ---------------------------------------------------------------------------
# 内存假库
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.rowcount = 0
        self.lastrowid = 0

    def execute(self, sql, params=()):
        self.connection.queries.append(sql)
        self.rows = []
        self.rowcount = 0
        self.lastrowid = 0
        self.connection.dispatch(sql, tuple(params), self)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class FakeDB:
    """四张表的内存版 + SQL 子集解释。"""

    #: ``SELECT `symbol` FROM `stock` WHERE `id` = %s LIMIT 1``
    _COLUMN_RE = re.compile(r"^SELECT\s+`?(\w+)`?\s+FROM")
    _EQ_RE = re.compile(r"`?(\w+)`?\s*=\s*%s")
    _IN_RE = re.compile(r"`?(\w+)`?\s+IN\s+\(([^)]*)\)")
    _TABLE_RE = re.compile(r"FROM\s+`?(\w+)`?", re.I)
    _INSERT_RE = re.compile(r"^INSERT INTO\s+`?(\w+)`?\s*\(([^)]*)\)")
    #: UPDATE 没有 FROM：``UPDATE stockgroupitem SET ...``
    _UPDATE_RE = re.compile(r"^UPDATE\s+`?(\w+)`?", re.I)

    def __init__(self, groups=None, items=None, transactions=None,
                 stocks=None):
        self.tables = {
            "stockgroup": list(groups or []),
            "stockgroupitem": list(items or []),
            "stocktransaction": list(transactions or []),
            "stock": list(stocks or []),
        }
        self.queries = []
        self.next_id = {"stocktransaction": 100}

    def cursor(self):
        return FakeCursor(self)

    # PHP 的 ``SqlGetStockSymbol`` 优先用数据源的方法（见
    # ``core.stockgroupsql._stock_symbol_of``）；假库不提供，走 SQL。
    # -- 分派 ---------------------------------------------------------------
    def dispatch(self, sql, params, cursor):
        text = " ".join(sql.split())
        upper = text.upper()
        if upper.startswith("SELECT COUNT(*)"):
            self._count(text, params, cursor)
        elif upper.startswith("SELECT"):
            self._select(text, params, cursor)
        elif upper.startswith("DELETE FROM"):
            self._delete(text, params, cursor)
        elif upper.startswith("INSERT INTO"):
            self._insert(text, params, cursor)
        elif upper.startswith("UPDATE"):
            self._update(text, params, cursor)
        else:
            raise AssertionError("测试 fake 不认识的 SQL: %s" % sql)

    def _table_of(self, text):
        return self._TABLE_RE.search(text).group(1)

    # -- 各语句 -------------------------------------------------------------
    def _select(self, text, params, cursor):
        table = self._table_of(text)
        rows = list(self.tables[table])
        if " WHERE " in text:
            rows = [row for row in rows
                    if self._match(row, text.split(" WHERE ", 1)[1], params)]
        if " ORDER BY `filled` DESC" in text:
            rows.sort(key=lambda row: row["filled"], reverse=True)
        elif " ORDER BY id ASC" in text:
            rows.sort(key=lambda row: int(row["id"]))
        limit = self._limit_of(text)
        if limit:
            start, num = limit
            rows = rows[start:start + num] if num else rows[start:]
        column = self._COLUMN_RE.match(text)
        if column and column.group(1) != "*":
            rows = [{column.group(1): row[column.group(1)]} for row in rows]
        cursor.rows = rows

    def _count(self, text, params, cursor):
        table = self._table_of(text)
        rows = list(self.tables[table])
        if " WHERE " in text:
            rows = [row for row in rows
                    if self._match(row, text.split(" WHERE ", 1)[1], params)]
        cursor.rows = [{"n": len(rows)}]

    def _delete(self, text, params, cursor):
        table = self._table_of(text)
        rows = list(self.tables[table])
        where = text.split(" WHERE ", 1)[1] if " WHERE " in text else ""
        keep, drop = [], []
        for row in rows:
            (drop if self._match(row, where, params) else keep).append(row)
        self.tables[table] = keep
        cursor.rowcount = len(drop)

    def _insert(self, text, params, cursor):
        match = self._INSERT_RE.match(text)
        table = match.group(1)
        columns = [c.strip().strip("`") for c in match.group(2).split(",")]
        row = dict(zip(columns, params))
        if "id" not in row or row["id"] is None:
            row["id"] = self.next_id.get(table, 1)
            self.next_id[table] = row["id"] + 1
        self.tables[table].append(row)
        cursor.rowcount = 1
        cursor.lastrowid = row["id"]

    def _update(self, text, params, cursor):
        table = self._UPDATE_RE.match(text).group(1)
        body = text[text.index(" SET ") + 5:]
        if " WHERE " in body:
            set_part, where = body.split(" WHERE ", 1)
        else:
            set_part, where = body, ""
        columns = [piece.split("=")[0].strip().strip("`")
                   for piece in set_part.split(",")]
        where_params = params[len(columns):]
        touched = 0
        for row in self.tables[table]:
            if where and not self._match(row, where, where_params):
                continue
            for column, value in zip(columns, params[:len(columns)]):
                row[column] = value
            touched += 1
        cursor.rowcount = touched

    # -- 小工具 -------------------------------------------------------------
    @staticmethod
    def _limit_of(text):
        if " LIMIT " not in text:
            return None
        tail = text.split(" LIMIT ", 1)[1]
        if " , " in tail:
            start, num = tail.split(" , ")
            return int(start), int(num)
        return 0, int(tail)

    def _match(self, row, where, params):
        in_match = self._IN_RE.search(where)
        if in_match:
            column = in_match.group(1)
            count = in_match.group(2).count("%s")
            wanted = {str(value) for value in params[:count]}
            return str(row[column]) in wanted
        columns = self._EQ_RE.findall(where)
        if not columns:
            return True
        pairs = list(zip(columns, params))
        if " OR " in where:
            return any(str(row[column]) == str(value)
                       for column, value in pairs)
        return all(str(row[column]) == str(value)
                   for column, value in pairs)

    # -- 便捷断言 -----------------------------------------------------------
    def transactions(self):
        return self.tables["stocktransaction"]

    def items(self):
        return self.tables["stockgroupitem"]


# ---------------------------------------------------------------------------
# 样本
# ---------------------------------------------------------------------------

GROUP_ID = 4          # 演示分组（属于 DEMO_MEMBER_ID）
OTHER_GROUP_ID = 9    # 别人的分组（member_id 不同）
ITEM_161125 = 11      # SZ161125 的成员
ITEM_513100 = 12      # SH513100 的成员（同组另一个成员）
OTHER_ITEM = 21       # 别人分组里的成员

STOCK_ID_161125 = 501
STOCK_ID_513100 = 502


def _groups():
    return [
        {"id": GROUP_ID, "member_id": 1, "groupname": "QDII"},
        {"id": OTHER_GROUP_ID, "member_id": 2, "groupname": "别人的分组"},
    ]


def _items():
    return [
        {"id": ITEM_161125, "stockgroup_id": GROUP_ID,
         "stock_id": STOCK_ID_161125, "quantity": 1000, "cost": 1650.0,
         "record": 1},
        {"id": ITEM_513100, "stockgroup_id": GROUP_ID,
         "stock_id": STOCK_ID_513100, "quantity": 0, "cost": 0.0,
         "record": 0},
        {"id": OTHER_ITEM, "stockgroup_id": OTHER_GROUP_ID,
         "stock_id": STOCK_ID_161125, "quantity": 0, "cost": 0.0,
         "record": 0},
    ]


def _transactions():
    return [
        {"id": 1, "groupitem_id": ITEM_161125, "quantity": "1000",
         "price": "1.65", "fees": "0.165", "filled": "2026-10-15 10:30:00",
         "remark": "建仓"},
        {"id": 2, "groupitem_id": ITEM_161125, "quantity": "-500",
         "price": "1.72", "fees": "1.29", "filled": "2026-10-20 14:00:00",
         "remark": "波段减仓"},
    ]


def _stocks():
    return [
        {"id": STOCK_ID_161125, "symbol": "SZ161125"},
        {"id": STOCK_ID_513100, "symbol": "SH513100"},
    ]


def _db():
    return FakeDB(_groups(), _items(), _transactions(), _stocks())


def _account(db, member_id=1, is_admin=False):
    return SubmitTransactionAccount(db, member_id=member_id,
                                    is_admin=is_admin)


# ---------------------------------------------------------------------------
# 费率与折股
# ---------------------------------------------------------------------------

class FundFeeRatioTest(unittest.TestCase):
    """``StockGetFundFeeRatio()``（``woody/res/php/_stock.php:151``）。"""

    def test_table_values(self):
        self.assertEqual(stock_get_fund_fee_ratio("SZ160416"), 0.0)
        self.assertEqual(stock_get_fund_fee_ratio("SZ161226"), 0.01)
        self.assertEqual(stock_get_fund_fee_ratio("SZ161125"), 0.012)
        self.assertEqual(stock_get_fund_fee_ratio("SZ161815"), 0.016)

    def test_default_ratio(self):
        """switch 走完没匹配上 → 0.015（``_stock.php:177``）。"""
        self.assertEqual(stock_get_fund_fee_ratio("SZ164906"), 0.012)
        self.assertEqual(stock_get_fund_fee_ratio("SZ999999"),
                         FUND_FEE_RATIO_DEFAULT)

    def test_none_symbol(self):
        self.assertEqual(stock_get_fund_fee_ratio(None),
                         FUND_FEE_RATIO_DEFAULT)


class ArbitrageQuantityTest(unittest.TestCase):
    """``GetArbitrageQuantity()``（``php/ui/stockcomm.php:65``）。"""

    def test_hedge_default_one(self):
        self.assertEqual(get_arbitrage_quantity("SZ161125", "501", 1000.0),
                         "1,000")

    def test_divided_by_hedge(self):
        self.assertEqual(
            get_arbitrage_quantity("SZ161125", "501", 1000.0,
                                   hedge_of=lambda s, i: 2.0), "500")

    def test_zero_hedge_falls_back_to_one(self):
        """除零保护：PHP 的 ``GetStockHedge`` 理论上不回 0，这里兜住。"""
        self.assertEqual(
            get_arbitrage_quantity("SZ161125", "501", 1000.0,
                                   hedge_of=lambda s, i: 0.0), "1,000")


# ---------------------------------------------------------------------------
# 回填
# ---------------------------------------------------------------------------

class UpdateStockGroupItemTest(unittest.TestCase):
    """``php/stocktrans.php:134`` / ``:148`` 的两个回填函数。"""

    def setUp(self):
        self.db = _db()

    def test_transaction_recomputes_totals(self):
        """quantity 累加（卖出为负）、cost **含手续费**、record 是笔数。"""
        item_sql = StockGroupItemSql(self.db, GROUP_ID)
        update_stock_group_item_transaction(item_sql, ITEM_161125)
        item = [row for row in self.db.items()
                if row["id"] == ITEM_161125][0]
        self.assertEqual(item["quantity"], 500)           # 1000 - 500
        self.assertEqual(item["record"], 2)
        # 1000*1.65 + 0.165 + (-500)*1.72 + 1.29
        self.assertAlmostEqual(float(item["cost"]),
                               1650.165 - 860.0 + 1.29, places=6)

    def test_empty_member_is_zeroed(self):
        """没有交易时也要 UPDATE（把三列刷成 0）—— 删最后一笔靠它清干净。"""
        item_sql = StockGroupItemSql(self.db, GROUP_ID)
        update_stock_group_item_transaction(item_sql, ITEM_513100)
        item = [row for row in self.db.items()
                if row["id"] == ITEM_513100][0]
        self.assertEqual(item["quantity"], 0)
        self.assertEqual(item["record"], 0)
        self.assertEqual(float(item["cost"]), 0.0)

    def test_group_update_covers_all_members(self):
        """``UpdateStockGroupItem`` 重算**整组**，item_id 只做第二个闸门。"""
        count = update_stock_group_item(GROUP_ID, ITEM_161125, self.db)
        self.assertEqual(count, 2)      # 分组里两个成员都重算了
        quantities = {row["id"]: row["quantity"] for row in self.db.items()}
        self.assertEqual(quantities[ITEM_161125], 500)
        self.assertEqual(quantities[ITEM_513100], 0)
        self.assertEqual(quantities[OTHER_ITEM], 0)   # 别的分组不动

    def test_gates(self):
        self.assertEqual(update_stock_group_item(False, ITEM_161125, self.db), 0)
        self.assertEqual(update_stock_group_item(GROUP_ID, False, self.db), 0)
        self.assertEqual(update_stock_group_item("0", ITEM_161125, self.db), 0)


# ---------------------------------------------------------------------------
# 权限闸门
# ---------------------------------------------------------------------------

class PermissionTest(unittest.TestCase):
    def setUp(self):
        self.db = _db()

    def test_own_group_is_writable(self):
        self.assertFalse(_account(self.db).is_group_read_only(GROUP_ID))

    def test_other_group_is_read_only(self):
        self.assertTrue(_account(self.db).is_group_read_only(OTHER_GROUP_ID))

    def test_false_group_id_is_not_read_only(self):
        """PHP 的短路：``if ($strGroupId)`` 不成立直接回 false。"""
        self.assertFalse(_account(self.db).is_group_read_only(False))
        self.assertFalse(_account(self.db).is_group_read_only("0"))

    def test_member_id_loose_compare(self):
        """``GetGroupMemberId() == GetLoginId()`` 是 PHP 的 ``==``：
        库里是 int 1、登录态是 '1' 也要相等。"""
        self.assertFalse(_account(self.db, member_id="1").is_group_read_only(
            GROUP_ID))

    def test_can_modify_returns_group_id(self):
        """PHP 返回的是 ``$strGroupId``（库里读出来的字符串）。"""
        self.assertEqual(
            _account(self.db).can_modify_stock_transaction(ITEM_161125),
            str(GROUP_ID))

    def test_can_modify_refuses_read_only(self):
        self.assertFalse(
            _account(self.db).can_modify_stock_transaction(OTHER_ITEM))

    def test_can_modify_unknown_item(self):
        self.assertFalse(_account(self.db).can_modify_stock_transaction(9999))


# ---------------------------------------------------------------------------
# 七个动作
# ---------------------------------------------------------------------------

class NewTransactionTest(unittest.TestCase):
    def setUp(self):
        self.db = _db()
        self.acct = _account(self.db)

    def test_new_inserts_and_refills(self):
        result = self.acct.process({
            "submit": SUBMIT_NEW, "symbol": ITEM_161125,
            "quantity": "500", "price": "1.70", "type": "1",
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "new")
        row = [r for r in self.db.transactions()
               if r["remark"] == "" and r["quantity"] == "500"][0]
        self.assertEqual(row["price"], "1.70")
        # A 股基金默认费率：500*1.70*0.0001 = 0.085 → strval_round(...,3)
        self.assertEqual(row["fees"], "0.085")
        # 末尾的 UpdateStockGroupItem 把成员刷成 1500 份 / 3 笔
        item = [r for r in self.db.items() if r["id"] == ITEM_161125][0]
        self.assertEqual(item["quantity"], 1000)
        self.assertEqual(item["record"], 3)

    def test_new_sell_prefixes_minus(self):
        self.acct.process({
            "submit": SUBMIT_NEW, "symbol": ITEM_161125,
            "quantity": "200", "price": "1.70", "type": "0",
        })
        self.assertIn("-200", [r["quantity"] for r in self.db.transactions()])

    def test_new_on_read_only_group_is_refused(self):
        result = self.acct.process({
            "submit": SUBMIT_NEW, "symbol": OTHER_ITEM,
            "quantity": "100", "price": "1.70", "type": "1",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.db.transactions()), 2)   # 没写进去


class EditTransactionTest(unittest.TestCase):
    def setUp(self):
        self.db = _db()
        self.acct = _account(self.db)

    def test_edit_keeps_filled(self):
        result = self.acct.process({
            "submit": SUBMIT_EDIT, "edit": 1, "symbol": ITEM_161125,
            "quantity": "1200", "price": "1.66", "type": "1",
            "remark": "改数量",
        })
        self.assertTrue(result["ok"])
        row = [r for r in self.db.transactions() if r["id"] == 1][0]
        self.assertEqual(row["quantity"], "1200")
        self.assertEqual(row["remark"], "改数量")
        self.assertEqual(row["filled"], "2026-10-15 10:30:00")  # 不可改

    def test_edit_without_id_is_noop(self):
        """PHP：``if ($strId = UrlGetQueryValue('edit'))`` 不成立就跳过。"""
        result = self.acct.process({
            "submit": SUBMIT_EDIT, "symbol": ITEM_161125,
            "quantity": "1200", "price": "1.66", "type": "1",
        })
        self.assertFalse(result["ok"])

    def test_unknown_submit_is_noop(self):
        result = self.acct.process({
            "submit": "不是已知按钮", "symbol": ITEM_161125,
            "quantity": "1", "price": "1.0", "type": "1",
        })
        self.assertEqual(result["action"], "unknown")
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.db.transactions()), 2)


class MergeTransactionTest(unittest.TestCase):
    def setUp(self):
        self.db = _db()
        self.acct = _account(self.db)

    def test_type0_zero_moves_symbol0_to_symbol1(self):
        ok = self.acct.process({
            "submit": SUBMIT_MERGE_CN, "type0": "0",
            "symbol0": ITEM_161125, "symbol1": ITEM_513100,
        })
        self.assertTrue(ok["ok"])
        moved = [r for r in self.db.transactions()
                 if r["groupitem_id"] == ITEM_513100]
        self.assertEqual(len(moved), 2)
        self.assertEqual([r for r in self.db.transactions()
                          if r["groupitem_id"] == ITEM_161125], [])
        # 两边成员都重算了
        quantities = {r["id"]: r["quantity"] for r in self.db.items()}
        self.assertEqual(quantities[ITEM_161125], 0)     # 源清空
        self.assertEqual(quantities[ITEM_513100], 500)   # 目标接过来

    def test_type0_other_reverses_direction(self):
        self.acct.process({
            "submit": SUBMIT_MERGE_CN, "type0": "1",
            "symbol0": ITEM_513100, "symbol1": ITEM_161125,
        })
        # symbol1（161125）是源 → 交易挂到 symbol0（513100）
        self.assertEqual([r for r in self.db.transactions()
                          if r["groupitem_id"] == ITEM_513100][0]["id"], 1)

    def test_read_only_side_refuses(self):
        ok = self.acct.process({
            "submit": SUBMIT_MERGE_CN, "type0": "0",
            "symbol0": ITEM_161125, "symbol1": OTHER_ITEM,
        })
        self.assertFalse(ok["ok"])


class DeleteAdjustEmptyTest(unittest.TestCase):
    def setUp(self):
        self.db = _db()

    def test_delete(self):
        result = _account(self.db).process({"delete": 2})
        self.assertTrue(result["ok"])
        self.assertEqual([r["id"] for r in self.db.transactions()], [1])
        item = [r for r in self.db.items() if r["id"] == ITEM_161125][0]
        self.assertEqual(item["quantity"], 1000)

    def test_delete_missing_record(self):
        result = _account(self.db).process({"delete": 999})
        self.assertFalse(result["ok"])

    def test_delete_read_only_refused(self):
        """别人的分组里的交易删不掉（非管理员）。"""
        self.db.transactions().append(
            {"id": 5, "groupitem_id": OTHER_ITEM, "quantity": "100",
             "price": "1.0", "fees": "0.0",
             "filled": "2026-10-21 10:00:00", "remark": ""})
        result = _account(self.db).process({"delete": 5})
        self.assertFalse(result["ok"])
        self.assertIn(5, [r["id"] for r in self.db.transactions()])

    def test_delete_admin_bypasses_read_only(self):
        """**只有 delete 这一支**有 ``IsAdmin()`` 旁路。"""
        self.db.transactions().append(
            {"id": 5, "groupitem_id": OTHER_ITEM, "quantity": "100",
             "price": "1.0", "fees": "0.0",
             "filled": "2026-10-21 10:00:00", "remark": ""})
        result = _account(self.db, is_admin=True).process({"delete": 5})
        self.assertTrue(result["ok"])
        self.assertNotIn(5, [r["id"] for r in self.db.transactions()])

    def test_adjust_keeps_amount(self):
        """份额 = 数量 × 价 / 新净值（成交额不变）。"""
        result = _account(self.db).process({"adjust": 1, "netvalue": "1.10"})
        self.assertTrue(result["ok"])
        row = [r for r in self.db.transactions() if r["id"] == 1][0]
        self.assertEqual(row["price"], "1.10")
        # 1650 / 1.1 在二进制浮点里是 1499.9999999999998，但 PHP 的
        # strval(float) 按 precision=14 出串 → '1500'（见 _php_strval_float）
        self.assertEqual(row["quantity"], "1500")
        self.assertAlmostEqual(float(row["quantity"]), 1000 * 1.65 / 1.10,
                               places=6)
        self.assertEqual(row["remark"], "建仓")      # 备注原样带回

    def test_adjust_read_only_refused(self):
        self.db.transactions().append(
            {"id": 6, "groupitem_id": OTHER_ITEM, "quantity": "100",
             "price": "1.0", "fees": "0.0",
             "filled": "2026-10-21 10:00:00", "remark": ""})
        result = _account(self.db, is_admin=True).process(
            {"adjust": 6, "netvalue": "2.0"})
        self.assertFalse(result["ok"])     # 管理员旁路**不**覆盖 adjust

    def test_empty_clears_remark_only(self):
        result = _account(self.db).process({"empty": 2})
        self.assertTrue(result["ok"])
        row = [r for r in self.db.transactions() if r["id"] == 2][0]
        self.assertEqual(row["remark"], "")
        self.assertEqual(row["quantity"], "-500")
        self.assertEqual(row["price"], "1.72")

    def test_zero_id_is_treated_as_missing(self):
        """``UrlGetQueryValue`` 的 ``== false`` 把 ``'0'`` 一起判掉。"""
        self.assertFalse(_account(self.db).process({"delete": "0"})["ok"])
        self.assertFalse(_account(self.db).process({"adjust": "0",
                                                    "netvalue": "1.0"})["ok"])


class FundPurchaseTest(unittest.TestCase):
    """``_onAddFundPurchase()``（``_submittransaction.php:113``）。"""

    def setUp(self):
        self.db = _db()
        self.acct = _account(self.db)

    def test_quantity_and_remark(self):
        """份额 = 金额 / (1 + 费率) / 净值；备注是 ``}折股数 申购``。"""
        result = self.acct.process({
            "groupid": GROUP_ID, "fundid": STOCK_ID_161125,
            "amount": "12000", "netvalue": "1.5",
        })
        self.assertTrue(result["ok"])
        row = self.db.transactions()[-1]
        # 12000 / 1.012 / 1.5 = 7905.138… → intval 7905
        self.assertEqual(row["quantity"], "7905")
        self.assertEqual(row["price"], "1.5")
        # 12000 * 0.012 * 0.1 = 14.4（那个 0.1 是 PHP 原式）
        self.assertEqual(row["fees"], "14.4")
        self.assertTrue(row["remark"].startswith("}"))
        self.assertTrue(row["remark"].endswith(" " + DISP_ORDER))

    def test_missing_amount_is_refused(self):
        self.assertFalse(self.acct.process({
            "groupid": GROUP_ID, "fundid": STOCK_ID_161125,
            "netvalue": "1.5",
        })["ok"])

    def test_zero_netvalue_is_refused(self):
        """``'0' == false``：净值填 0 与没填一样被判掉。"""
        self.assertFalse(self.acct.process({
            "groupid": GROUP_ID, "fundid": STOCK_ID_161125,
            "amount": "12000", "netvalue": "0",
        })["ok"])

    def test_read_only_group_refused(self):
        self.assertFalse(self.acct.process({
            "groupid": OTHER_GROUP_ID, "fundid": STOCK_ID_161125,
            "amount": "12000", "netvalue": "1.5",
        })["ok"])

    def test_fund_not_in_group_refused(self):
        """``$sql->GetId($strFundId)`` 取不到成员（分组里没这只基金）。"""
        self.assertFalse(self.acct.process({
            "groupid": GROUP_ID, "fundid": 99999,
            "amount": "12000", "netvalue": "1.5",
        })["ok"])


class DispatchTest(unittest.TestCase):
    """``Process()`` 的分派顺序（``_submittransaction.php:188``）。"""

    def setUp(self):
        self.db = _db()
        self.acct = _account(self.db)

    def test_no_action(self):
        result = self.acct.process({})
        self.assertEqual(result["action"], "none")
        self.assertFalse(result["ok"])

    def test_submit_wins_over_delete(self):
        """PHP 是 ``if (isset($_POST['submit'])) ... else if (delete)``。"""
        result = self.acct.process({
            "submit": SUBMIT_NEW, "symbol": ITEM_161125,
            "quantity": "10", "price": "1.6", "type": "1",
            "delete": 2,
        })
        self.assertEqual(result["action"], "new")
        self.assertIn(2, [r["id"] for r in self.db.transactions()])


if __name__ == "__main__":
    unittest.main()
