"""五档盘口落库（``realtimequote.orderbook``）的单元测试。

对应 PHP：``php/stock/stockref.php:316`` 的 ``_onSinaDataCN()`` —— 那段
``for ($i = 10; $i < 20; $i += 2)`` 把新浪 A 股行情的第 10~29 个字段拆成
买一到买五、卖一到卖五，存进 ``arBidPrice`` / ``arBidQuantity`` 四个平行数组。

迁这一块要钉住三件事：

1. **缺档是有语义的**。PHP 的 ``GetAskQuantity($i)`` 在数组没那么长时返回
   ``false``，``php/ui/tradingparagraph.php`` 靠这个假值决定"这一档整行不渲染"。
   Python 侧用空串表达同一件事（``_php_truthy`` 同样判假）；
2. **档数是数据源说了算**。A 股恒为 5 档（数组恒为 5 项，缺的是 ``False``），
   所以落库时按 5 档取值，与 PHP 的数组逐位等价；
3. **老库没有这一列**。``schema.sql`` 是 ``CREATE TABLE IF NOT EXISTS``，
   已存在的表不会被它改动，所以读写都必须能在"列不存在"时退回原样 ——
   盘口是附加信息，不能因为它让价格都写不进去。

全部离线：行情行是手写的，数据库是假的。
"""

import unittest

from quoteupdater import RecentQuote, _quote_from_row, parse_symbol, quote_from_reference
from quotestore import (
    ER_BAD_FIELD_ERROR,
    RealtimeQuoteStore,
    decode_orderbook,
    encode_orderbook,
    orderbook_from_lists,
)
from refquote import apply_order_book


def make_book_line(sina_symbol="sz161130", name="纳指LOF", price="4.796", prev="4.727",
                   bid=(("4.790", "1200"), ("4.789", "3800")),
                   ask=(("4.800", "900"), ("4.801", "1500"))):
    """造一行**带盘口**的 A 股行情。

    字段下标照抄 PHP：买盘量在 ``10 + 2i``、价在 ``11 + 2i``；
    卖盘顺延 10 位（``20 + 2i`` / ``21 + 2i``）。
    没给的档位留空串 —— 真实行情里"没挂单"就是这个样子。
    """
    values = [""] * 32
    values[0] = name
    values[1] = "4.700"
    values[2] = prev
    values[3] = price
    values[4] = "4.800"
    values[5] = "4.690"
    values[8] = "1000000"
    values[30] = "2026-09-30"
    values[31] = "15:00:00"
    for index, (level_price, quantity) in enumerate(bid):
        values[10 + 2 * index] = str(quantity)
        values[11 + 2 * index] = str(level_price)
    for index, (level_price, quantity) in enumerate(ask):
        values[20 + 2 * index] = str(quantity)
        values[21 + 2 * index] = str(level_price)
    return 'var hq_str_%s="%s";' % (sina_symbol, ",".join(values))


# ---------------------------------------------------------------------------
# 假数据库：记录 SQL，并能在"列不存在"时抛出 1054
# ---------------------------------------------------------------------------
class DbError(Exception):
    """模仿 DB-API 异常：错码在 ``args[0]``。"""

    def __init__(self, code, message):
        super().__init__(code, message)


class FakeCursor:
    def __init__(self, rows=None, legacy=False):
        self.rows = rows or []
        self.legacy = legacy
        self.statements = []
        self.params = []
        self.closed = False

    def execute(self, sql, params=()):
        # 先记录再抛错：真实驱动也是"语句发出去了才知道列不存在"
        self.statements.append(sql)
        self.params.append(tuple(params))
        if self.legacy and "orderbook" in sql:
            raise DbError(ER_BAD_FIELD_ERROR, "Unknown column 'orderbook' in 'field list'")

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows=None, legacy=False):
        self.cursor_obj = FakeCursor(rows, legacy)
        self.rolled_back = False

    def cursor(self):
        return self.cursor_obj

    def rollback(self):
        self.rolled_back = True


class OrderbookCodecTest(unittest.TestCase):
    """编解码：值保持字符串、缺档保持"假"、全空视为没有盘口。"""

    def test_false_levels_become_empty_strings(self):
        """``False`` 是 PHP 数组越界的返回值，语义就是"没有这一档"。"""
        book = orderbook_from_lists([False, "4.789"], [False, "3800"], [False], [False])

        self.assertEqual(book["bid_price"], ["", "4.789"])
        self.assertEqual(book["bid_quantity"], ["", "3800"])
        self.assertEqual(book["ask_price"], [""])
        self.assertEqual(book["ask_quantity"], [""])

    def test_all_blank_levels_mean_no_orderbook(self):
        """美股/港股那几套解析不填盘口，取出来全是 ``False`` —— 那不是盘口。"""
        self.assertEqual(orderbook_from_lists([False] * 5, [False] * 5, [False] * 5, [False] * 5), {})

    def test_roundtrip_keeps_every_level(self):
        book = orderbook_from_lists(
            ["4.790", "4.789", "", "", ""],
            ["1200", "3800", "", "", ""],
            ["4.800", "4.801"],
            ["900", "1500"],
        )
        decoded = decode_orderbook(encode_orderbook(book))

        self.assertEqual(decoded["bid_price"], ["4.790", "4.789", "", "", ""])
        self.assertEqual(decoded["ask_quantity"], ["900", "1500"])

    def test_encode_returns_none_without_levels(self):
        """没有盘口就写 NULL —— "没抓到"与"抓到了但没挂单"在排查时是两回事。"""
        self.assertIsNone(encode_orderbook({}))
        self.assertIsNone(encode_orderbook(None))
        self.assertIsNone(encode_orderbook(orderbook_from_lists([False], [False], [False], [False])))

    def test_decode_tolerates_everything(self):
        """坏数据一律降级成"没有盘口"：盘口是附加信息，不该让读行情失败。"""
        for value in (None, "", "not json", "[1,2]", 3.5, b"\xff\xfe"):
            self.assertEqual(decode_orderbook(value), {}, "输入 %r 应当被当成没有盘口" % (value,))

    def test_decode_fills_missing_keys(self):
        """只存了一侧也不能缺 key —— 上层按四个 key 取值，缺了会 KeyError。"""
        decoded = decode_orderbook('{"bid_price":["4.79"]}')

        self.assertEqual(decoded["bid_price"], ["4.79"])
        for key in ("bid_quantity", "ask_price", "ask_quantity"):
            self.assertEqual(decoded[key], [])


class RealtimeQuoteStoreTest(unittest.TestCase):
    """写库/读库，含"老库没有 orderbook 列"的回退。"""

    def _row(self, **kwargs):
        row = {
            "symbol": "SZ161130", "source_symbol": "sz161130", "price": "4.796",
            "prev_price": "4.727", "name": "纳指LOF", "quote_date": "2026-09-30",
            "quote_time": "15:00:00", "source": "sina", "fetched_at": "2026-09-30 15:00:00",
            "orderbook": None,
        }
        row.update(kwargs)
        return row

    def test_save_writes_the_json_text(self):
        connection = FakeConnection()
        book = orderbook_from_lists(["4.790"], ["1200"], ["4.800"], ["900"])

        self.assertTrue(RealtimeQuoteStore(connection).save(
            1, "SZ161130", "sz161130", "4.796", "4.727", "纳指LOF",
            "2026-09-30", "15:00:00", "sina", 0.0, book))

        sql = connection.cursor_obj.statements[0]
        self.assertIn("orderbook", sql)
        self.assertEqual(decode_orderbook(connection.cursor_obj.params[0][-1]), book)

    def test_save_without_orderbook_writes_null(self):
        connection = FakeConnection()
        RealtimeQuoteStore(connection).save(
            1, "SZ161130", "sz161130", "4.796", "4.727", "纳指LOF",
            "2026-09-30", "15:00:00", "sina", 0.0)

        self.assertIsNone(connection.cursor_obj.params[0][-1])
        self.assertEqual(len(connection.cursor_obj.statements), 1)

    def test_save_falls_back_when_column_missing(self):
        """老库没打补丁：退回十个字段的写法，**价格照写**，不能静默失败。"""
        connection = FakeConnection(legacy=True)
        book = orderbook_from_lists(["4.790"], ["1200"], ["4.800"], ["900"])

        self.assertTrue(RealtimeQuoteStore(connection).save(
            1, "SZ161130", "sz161130", "4.796", "4.727", "纳指LOF",
            "2026-09-30", "15:00:00", "sina", 0.0, book))

        statements = connection.cursor_obj.statements
        self.assertEqual(len(statements), 2)
        self.assertIn("orderbook", statements[0])
        self.assertNotIn("orderbook", statements[1])
        self.assertEqual(len(connection.cursor_obj.params[1]), 10)

    def test_save_reports_failure_on_other_errors(self):
        """不是"列不存在"的错必须照常报失败 —— 回退只认 1054。"""
        class BrokenCursor(FakeCursor):
            def execute(self, sql, params=()):
                raise DbError(1146, "Table doesn't exist")

        class BrokenConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.cursor_obj = BrokenCursor()

        connection = BrokenConnection()
        self.assertFalse(RealtimeQuoteStore(connection).save(
            1, "SZ161130", "sz161130", "4.796", "4.727", "纳指LOF",
            "2026-09-30", "15:00:00", "sina", 0.0, {}))
        self.assertTrue(connection.rolled_back)

    def test_load_returns_the_raw_column(self):
        raw = encode_orderbook(orderbook_from_lists(["4.790"], ["1200"], ["4.800"], ["900"]))
        connection = FakeConnection([self._row(orderbook=raw)])

        row = RealtimeQuoteStore(connection).load("SZ161130")

        self.assertEqual(row["orderbook"], raw)
        self.assertEqual(decode_orderbook(row["orderbook"])["bid_price"], ["4.790"])

    def test_load_falls_back_when_column_missing(self):
        connection = FakeConnection([self._row()], legacy=True)

        row = RealtimeQuoteStore(connection).load("SZ161130")

        self.assertIsNone(row["orderbook"])
        self.assertEqual(row["price"], "4.796")

    def test_load_all_keeps_every_row(self):
        raw = encode_orderbook(orderbook_from_lists(["4.790"], ["1200"], ["4.800"], ["900"]))
        connection = FakeConnection([self._row(orderbook=raw), self._row(symbol="SZ162411")])

        rows = RealtimeQuoteStore(connection).load_all()

        self.assertEqual([row["symbol"] for row in rows], ["SZ161130", "SZ162411"])


class QuoteCarriesOrderBookTest(unittest.TestCase):
    """``RecentQuote`` 要能把盘口从抓取进程带到页面。"""

    def test_parsed_reference_gives_five_levels(self):
        """A 股恒为 5 档：只填了两档，剩下三档是空串（与 PHP 的 ``False`` 等价）。"""
        reference = parse_symbol("sz161130", make_book_line())

        self.assertEqual(reference.get_bid_price(0), "4.790")
        self.assertEqual(reference.get_bid_quantity(1), "3800")
        self.assertFalse(reference.get_bid_price(2))     # 空串：这一档没有挂单
        self.assertEqual(reference.get_ask_price(1), "4.801")

    def test_quote_from_reference_fills_the_book(self):
        reference = parse_symbol("sz161130", make_book_line())
        quote = quote_from_reference("SZ161130", "sz161130", reference, "fake")

        self.assertTrue(quote.has_order_book())
        self.assertEqual(quote.orderbook["bid_price"], ["4.790", "4.789", "", "", ""])
        self.assertEqual(quote.orderbook["ask_quantity"], ["900", "1500", "", "", ""])

    def test_quote_without_levels_has_no_book(self):
        """收市后没有挂单：价格照样要写进去，只是没有盘口。"""
        reference = parse_symbol("sz161130", make_book_line(bid=(), ask=()))
        quote = quote_from_reference("SZ161130", "sz161130", reference, "fake")

        self.assertEqual(quote.price, "4.796")
        self.assertFalse(quote.has_order_book())

    def test_read_back_from_the_row(self):
        raw = encode_orderbook(orderbook_from_lists(["4.790"], ["1200"], ["4.800"], ["900"]))
        row = {"symbol": "SZ161130", "source_symbol": "sz161130", "price": "4.796",
               "prev_price": "4.727", "name": "纳指LOF", "quote_date": "2026-09-30",
               "quote_time": "15:00:00", "source": "sina", "orderbook": raw}

        quote = _quote_from_row(row, "SZ161130", 1000.0)

        self.assertEqual(quote.orderbook["bid_quantity"], ["1200"])
        self.assertEqual(quote.orderbook["ask_price"], ["4.800"])

    def test_row_without_the_column_has_no_book(self):
        quote = _quote_from_row({"symbol": "SZ161130", "price": "4.796"}, "SZ161130", 1000.0)
        self.assertFalse(quote.has_order_book())

    def test_as_dict_adds_the_book_only_when_present(self):
        """列表页 JSON 有十几只符号，没必要每只都挂 20 个值。"""
        plain = RecentQuote(symbol="SZ161130", sina_symbol="sz161130", price="4.796")
        self.assertNotIn("order_book", plain.as_dict_with_order_book())

        with_book = RecentQuote(symbol="SZ161130", sina_symbol="sz161130", price="4.796",
                                orderbook={"bid_price": ["4.790"]})
        self.assertEqual(with_book.as_dict_with_order_book()["order_book"]["bid_price"], ["4.790"])


class ApplyOrderBookTest(unittest.TestCase):
    """把快照里的盘口装回引用对象（``refquote.apply_order_book``）。"""

    class Ref:
        """只有四个平行数组的引用对象，形状与 ``StockReference`` 一致。"""

        def __init__(self):
            self.bid_prices = []
            self.bid_quantities = []
            self.ask_prices = []
            self.ask_quantities = []

    def test_fills_the_four_arrays(self):
        ref = self.Ref()
        book = orderbook_from_lists(["4.790"], ["1200"], ["4.800"], ["900"])

        self.assertTrue(apply_order_book(ref, book))
        self.assertEqual(ref.bid_prices, ["4.790"])
        self.assertEqual(ref.ask_quantities, ["900"])

    def test_empty_book_does_not_clear_what_is_there(self):
        """``{}`` 是"这次没带盘口信息"，拿它去覆盖只会把已有的挂单弄丢。"""
        ref = self.Ref()
        ref.bid_prices = ["4.790"]

        self.assertFalse(apply_order_book(ref, {}))
        self.assertEqual(ref.bid_prices, ["4.790"])

    def test_object_without_the_arrays_is_left_alone(self):
        """只算估值的那一层引用本来也不回答"买一价是多少"，不该被凭空挂上属性。"""
        class Plain:
            pass

        ref = Plain()
        self.assertFalse(apply_order_book(ref, {"bid_price": ["4.790"]}))
        self.assertFalse(hasattr(ref, "bid_prices"))


if __name__ == "__main__":
    unittest.main()
