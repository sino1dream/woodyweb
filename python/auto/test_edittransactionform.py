"""``core/edittransactionform.py``（交易表单数据）的单元测试。

对应 PHP ``woody/res/php/_edittransactionform.php``。假库复用
``test_submittransaction`` 的四表内存库（多塞一张 ``stockhistory``）。

重点验证的语义：

1. **成员下拉只收可交易标的**，且按 A 股 → 港股 → 美股三组各自排序后拼接；
2. **价格下拉是 ±5 个最小变动单位**（11 档，步长 10^-precision），
   ``IsTradable()`` 为假时没有这个下拉；
3. **数量默认取最近一笔成交**（负号去掉，方向由 type 单选框管）；
4. **建议备注的 ``{`` / ``}`` 取反**（对冲申购批次标记）；
5. **编辑分支只有备注含"申购"才回填手续费**。
"""

import unittest

from core.edittransactionform import (
    PRICE_OPTION_RANGE,
    build_transaction_form,
    get_first_group_item,
    get_group_item_price_array,
    get_group_item_quantity_array,
    get_price_option,
    get_price_option_js_array,
    get_suggested_remark,
    sql_get_stock_group_item_symbol_array,
)
from core.stockgroupsql import StockGroupItemSql
from core.stocksymbol import StockSymbol
from core.submittransaction import DISP_ORDER, SUBMIT_EDIT, SUBMIT_NEW
from test_submittransaction import (  # noqa: E402  （复用四表内存假库）
    GROUP_ID,
    ITEM_161125,
    ITEM_513100,
    STOCK_ID_161125,
    STOCK_ID_513100,
    FakeDB,
    _groups,
    _items,
    _stocks,
    _transactions,
)


class FakeHistorySql:
    """``StockHistorySql`` 的替身：``{stock_id: 最新收盘}``。"""

    def __init__(self, prices=None):
        self.prices = prices or {}

    def get_close_now(self, stock_id):
        return self.prices.get(str(stock_id))


def _db(transactions=None):
    db = FakeDB(_groups(), _items(), transactions or _transactions(), _stocks())
    db.tables["stockhistory"] = [
        {"id": 1, "stock_id": STOCK_ID_161125, "date": "2026-10-23",
         "close": "1.680", "volume": "0", "adjclose": "1.680"},
        {"id": 2, "stock_id": STOCK_ID_513100, "date": "2026-10-23",
         "close": "2.399", "volume": "0", "adjclose": "2.399"},
    ]
    return db


def _item_sql(db, group_id=GROUP_ID):
    return StockGroupItemSql(db, group_id)


class SymbolArrayTest(unittest.TestCase):
    """``SqlGetStockGroupItemSymbolArray()``（``sqlstockgroup.php:190``）。"""

    def setUp(self):
        self.db = _db()
        self.sql = _item_sql(self.db)

    def test_tradable_only(self):
        """不可交易的成员（如指数 ^GSPC）不出现在下拉里。"""
        self.db.tables["stock"].append({"id": 900, "symbol": "^GSPC"})
        self.db.tables["stockgroupitem"].append(
            {"id": 31, "stockgroup_id": GROUP_ID, "stock_id": 900,
             "quantity": 0, "cost": 0.0, "record": 0})
        symbols = sql_get_stock_group_item_symbol_array(self.sql, self.db)
        self.assertNotIn("31", symbols)

    def test_grouped_and_sorted(self):
        """A 股 → 港股 → 美股，组内按符号排序（不是按 item id）。"""
        self.db.tables["stock"] += [
            {"id": 901, "symbol": "00700"},      # 港股
            {"id": 902, "symbol": "GLD"},        # 美股
            {"id": 903, "symbol": "SZ000001"},   # A 股
        ]
        for stock_id in (902, 901, 903):         # 故意乱序插入
            self.db.tables["stockgroupitem"].append(
                {"id": int("4%d" % (stock_id % 10)),
                 "stockgroup_id": GROUP_ID, "stock_id": stock_id,
                 "quantity": 0, "cost": 0.0, "record": 0})
        symbols = list(sql_get_stock_group_item_symbol_array(
            self.sql, self.db).values())
        # A 股在前（组内按符号排序：SH < SZ），港股次之，美股最后
        self.assertEqual(symbols[:3], ["SH513100", "SZ000001", "SZ161125"])
        self.assertEqual(symbols[3], "00700")
        self.assertEqual(symbols[4], "GLD")

    def test_first_item(self):
        """A 股组里 ``SH513100`` 排在 ``SZ161125`` 前面（符号排序）。"""
        self.assertEqual(get_first_group_item(self.sql, self.db),
                         str(ITEM_513100))


class PriceArrayTest(unittest.TestCase):
    def setUp(self):
        self.db = _db()
        self.sql = _item_sql(self.db)

    def test_prices_from_history(self):
        """走真 SQL 路径（``StockHistorySql.get_close_now``）。"""
        prices = get_group_item_price_array(self.sql)
        self.assertEqual(str(prices[str(ITEM_161125)]), "1.680")
        self.assertEqual(str(prices[str(ITEM_513100)]), "2.399")

    def test_missing_close_is_none(self):
        prices = get_group_item_price_array(self.sql,
                                            history_sql=FakeHistorySql({}))
        self.assertIsNone(prices[str(ITEM_161125)])


class QuantityArrayTest(unittest.TestCase):
    def setUp(self):
        self.db = _db()
        self.sql = _item_sql(self.db)

    def test_last_transaction_without_sign(self):
        """最近一笔是卖出（``-500``）→ 表单里显示 ``500``。"""
        quantities = get_group_item_quantity_array(self.sql)
        self.assertEqual(quantities[str(ITEM_161125)], "500")

    def test_member_without_transaction_is_empty(self):
        quantities = get_group_item_quantity_array(self.sql)
        self.assertEqual(quantities[str(ITEM_513100)], "")


class PriceOptionTest(unittest.TestCase):
    """``_getPriceOption()``（``_edittransactionform.php:13``）。"""

    def test_eleven_steps(self):
        options = get_price_option(StockSymbol("SZ161125"), "1.68")
        self.assertEqual(len(options), len(list(PRICE_OPTION_RANGE)))
        self.assertEqual(options[5], "1.68")            # i == 0 居中

    def test_step_is_precision_scaled(self):
        """步长 = 10^-precision（基金的 ``GetPrecision()`` 是 3 → 0.001）。"""
        options = get_price_option(StockSymbol("SZ161125"), "1.68")
        self.assertEqual(options[6], "1.681")
        self.assertEqual(options[4], "1.679")

    def test_not_tradable_has_no_option(self):
        self.assertIsNone(get_price_option(StockSymbol("^GSPC"), "6800"))

    def test_js_array(self):
        prices = {str(ITEM_161125): "1.68", str(ITEM_513100): "2.399"}
        array = get_price_option_js_array(
            prices, lambda item_id: StockSymbol("SZ161125"))
        self.assertEqual(array[str(ITEM_161125)].count(","), 10)


class SuggestedRemarkTest(unittest.TestCase):
    """``_getSuggestedRemark()``（``_edittransactionform.php:81``）。"""

    def test_empty_when_no_history(self):
        db = _db(transactions=[])
        self.assertEqual(get_suggested_remark(_item_sql(db), ITEM_161125), "")

    def test_flip_brace(self):
        """上一条备注含 ``{`` → 建议 ``}``；含 ``}`` → 建议 ``{``。"""
        db = _db(transactions=[
            {"id": 7, "groupitem_id": ITEM_161125, "quantity": "1000",
             "price": "1.5", "fees": "0.0",
             "filled": "2026-10-21 10:00:00", "remark": "{100 申购"},
        ])
        self.assertEqual(get_suggested_remark(_item_sql(db), ITEM_161125), "}")

    def test_flip_back(self):
        db = _db(transactions=[
            {"id": 8, "groupitem_id": ITEM_161125, "quantity": "1000",
             "price": "1.5", "fees": "0.0",
             "filled": "2026-10-22 10:00:00", "remark": "}100 申购"},
        ])
        self.assertEqual(get_suggested_remark(_item_sql(db), ITEM_161125), "{")

    def test_whole_group_when_no_item(self):
        """``$strGroupItemId`` 为假 → 取**本分组**最近一笔。"""
        db = _db(transactions=[
            {"id": 9, "groupitem_id": ITEM_513100, "quantity": "100",
             "price": "2.4", "fees": "0.0",
             "filled": "2026-10-22 10:00:00", "remark": "{50 申购"},
        ])
        self.assertEqual(get_suggested_remark(_item_sql(db), None), "}")


class BuildFormTest(unittest.TestCase):
    """``StockEditTransactionForm()`` 的数据部分。"""

    def setUp(self):
        self.db = _db()
        self.history = FakeHistorySql({str(STOCK_ID_161125): "1.68",
                                       str(STOCK_ID_513100): "2.399"})

    def test_new_defaults(self):
        form = build_transaction_form(self.db, GROUP_ID, SUBMIT_NEW,
                                      history_sql=self.history)
        self.assertEqual(form["submit"], SUBMIT_NEW)
        self.assertEqual(form["type"], "1")
        #: A 股组按符号排序，SH513100 排第一（见 SymbolArrayTest）
        self.assertEqual(form["item_id"], str(ITEM_513100))
        self.assertEqual(str(form["price"]), "2.399")
        self.assertEqual(form["quantity"], "")           # 这个成员没成交过
        self.assertEqual(form["commission"], "")
        self.assertEqual(form["remark"], "")             # 样例备注不含 {}
        self.assertTrue(form["price_options"])

    def test_edit_sell_type_and_quantity(self):
        """卖出记录（quantity < 0）→ type '0'，数量去掉负号。"""
        form = build_transaction_form(self.db, GROUP_ID, SUBMIT_EDIT,
                                      trans_id=2, history_sql=self.history)
        self.assertEqual(form["type"], "0")
        self.assertEqual(form["quantity"], "500")
        self.assertEqual(str(form["price"]), "1.72")
        self.assertEqual(form["remark"], "波段减仓")
        self.assertEqual(form["commission"], "")   # 备注不含"申购"

    def test_edit_fills_commission_for_purchase(self):
        """备注含"申购"才回填 fees（``_edittransactionform.php:155``）。"""
        self.db.transactions().append(
            {"id": 10, "groupitem_id": ITEM_161125, "quantity": "7905",
             "price": "1.5", "fees": "14.4",
             "filled": "2026-10-22 10:00:00",
             "remark": "}7,905 " + DISP_ORDER})
        form = build_transaction_form(self.db, GROUP_ID, SUBMIT_EDIT,
                                      trans_id=10, history_sql=self.history)
        self.assertEqual(form["commission"], "14.4")

    def test_edit_resolves_group_from_record(self):
        """编辑时分组号是**从交易记录反查**的（``:144``），不是入参给的那个。

        入参给一个空分组（99）也要能出表单 —— 否则改一笔交易会因为
        ``?groupid=`` 传错而读不到成员列表。
        """
        form = build_transaction_form(self.db, 99, SUBMIT_EDIT, trans_id=2,
                                      history_sql=self.history)
        self.assertIsNotNone(form)
        self.assertEqual(form["quantity"], "500")
        self.assertTrue(form["symbols"])

    def test_edit_missing_record_returns_none(self):
        self.assertIsNone(build_transaction_form(
            self.db, GROUP_ID, SUBMIT_EDIT, trans_id=999,
            history_sql=self.history))

    def test_empty_group_no_form(self):
        """``if (count($arPrice) == 0) return;``（``:161``）。

        注意 PHP 的 ``$arPrice`` 是**按成员键的**（取不到价也给 false），
        所以这个闸门只挡"分组一个成员都没有"，不挡"取不到收盘价"。
        """
        self.assertIsNone(build_transaction_form(
            self.db, 77, SUBMIT_NEW, history_sql=self.history))

    def test_missing_close_is_empty_in_js_array(self):
        """取不到收盘价的成员在 JS 数组里是空串（PHP 的 false），不崩。"""
        form = build_transaction_form(self.db, GROUP_ID, SUBMIT_NEW,
                                      history_sql=FakeHistorySql({}))
        self.assertEqual(set(form["price_array"].values()), {""})

    def test_common_phrases_default_empty(self):
        """``CommonPhraseSql`` 未迁 → 常用备注恒空（known deviation）。"""
        form = build_transaction_form(self.db, GROUP_ID, SUBMIT_NEW,
                                      history_sql=self.history)
        self.assertEqual(form["common_phrases"], {})


if __name__ == "__main__":
    unittest.main()
