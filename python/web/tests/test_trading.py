"""五档交易表的测试（`php/ui/tradingparagraph.php` → `trading_service.py`）。

**全部离线**：引用对象是内存假实现，不联网、不连 MySQL、不需要 flask。
"""

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services.trading_service import (  # noqa: E402
    TRADING_CALLBACK_WIDTH,
    TRADING_COLOR_FIRST,
    TRADING_COLOR_LIMIT,
    TRADING_QUOTE_NUM,
    TRADING_TABLE_ID,
    TRADING_ZERO_QUANTITY,
    TradingService,
    _php_empty,
    _php_truthy,
    build_trading_rows,
    has_trading_quantity,
)


class FakeRef:
    """带完整五档行情的假引用对象。

    PHP 的 ``StockReference`` 用对象方法暴露五档；这里用数组，行为一致：
    下标越界返回 ``None``（对应 PHP 访问不存在下标得到 null）。
    """

    def __init__(self, symbol="SZ161130", ask_prices=None, ask_quantities=None,
                 bid_prices=None, bid_quantities=None, price="4.884", prev_price="4.814",
                 date="2026-10-09", precision=3):
        self.str_symbol = symbol
        self.str_price = price
        self.str_prev_price = prev_price
        self.str_date = date
        self._precision = precision
        self.ask_prices = list(ask_prices or [])
        self.ask_quantities = list(ask_quantities or [])
        self.bid_prices = list(bid_prices or [])
        self.bid_quantities = list(bid_quantities or [])

    # -- 五档 -------------------------------------------------------------
    def get_symbol(self):
        return self.str_symbol

    def GetSymbol(self):
        return self.get_symbol()

    def get_price(self):
        return self.str_price

    def get_prev_price(self):
        return self.str_prev_price

    def get_date(self):
        return self.str_date

    def get_precision(self):
        return self._precision

    def get_time_zone(self):
        return "Asia/Shanghai"

    def is_stock_market_trading(self, ymd, no_noon_break=True):
        """交易时段判定走**真实实现**（``stocksymbol.StockSymbol``）。

        这里刻意不自己写一套假的：涨停基准价那条三元表达式的分叉就在这个判定上，
        用假的实现等于把最要紧的那条逻辑摘出去测了。
        """
        from stocksymbol import StockSymbol

        try:
            return StockSymbol(self.str_symbol).is_stock_market_trading(ymd, no_noon_break)
        except Exception:
            return False

    def get_ask_price(self, index=0):
        return _item(self.ask_prices, index)

    def get_bid_price(self, index=0):
        return _item(self.bid_prices, index)

    def get_ask_quantity(self, index=0):
        return _item(self.ask_quantities, index)

    def get_bid_quantity(self, index=0):
        return _item(self.bid_quantities, index)

    # -- 显示口径：这里刻意做成"看得懂"的纯函数 ---------------------------
    def get_price_display(self, disp=None, prev=None, precision=None):
        precision = self._precision if not precision else precision
        return "%.*f" % (precision, 0.0 if disp is None else float(disp))

    def get_percentage_text(self, divisor=None, dividend=None):
        if divisor is None or dividend is None:
            return ""
        return "%.2f%%" % (float(dividend) / float(divisor) * 100.0 - 100.0)


def _item(array, index):
    return array[index] if 0 <= index < len(array) else None


def full_ref(**kwargs):
    """五档齐全的 ref：卖出 5.00…5.04，买入 4.90…4.94。"""
    return FakeRef(
        ask_prices=["5.00", "5.01", "5.02", "5.03", "5.04"],
        ask_quantities=["100", "200", "300", "400", "500"],
        bid_prices=["4.94", "4.93", "4.92", "4.91", "4.90"],
        bid_quantities=["90", "80", "70", "60", "50"],
        **kwargs
    )


class PhpTruthinessTest(unittest.TestCase):
    """``'0'`` 在 PHP 是假值、在 Python 是真值 —— 这是本模块最容易翻车的地方。"""

    def test_string_zero_is_false(self):
        self.assertFalse(_php_truthy("0"))
        self.assertTrue(_php_truthy("00"))
        self.assertTrue(_php_truthy("0.0"))

    def test_empty_string_is_false(self):
        self.assertFalse(_php_truthy(""))
        self.assertTrue(_php_empty(""))
        self.assertTrue(_php_empty("0"))

    def test_none_is_false(self):
        self.assertFalse(_php_truthy(None))
        self.assertTrue(_php_empty(None))

    def test_nonzero_string_is_true(self):
        self.assertTrue(_php_truthy("100"))

    def test_python_builtin_truthiness_would_differ(self):
        """把"照字面翻译会怎样"写出来，防止以后有人把它改回去。"""
        self.assertEqual(bool("0"), True)          # Python 判真
        self.assertEqual(_php_truthy("0"), False)  # PHP 判假


class HasTradingQuantityTest(unittest.TestCase):
    def test_all_zero_quantities_means_nothing_to_show(self):
        ref = FakeRef(ask_quantities=["0"] * 5, bid_quantities=["0"] * 5)
        self.assertFalse(has_trading_quantity(ref))

    def test_empty_arrays_means_nothing_to_show(self):
        self.assertFalse(has_trading_quantity(FakeRef()))

    def test_one_bid_is_enough(self):
        ref = FakeRef(bid_quantities=["10"], bid_prices=["4.9"])
        self.assertTrue(has_trading_quantity(ref))

    def test_zero_string_does_not_count_as_a_order(self):
        """PHP 里 ``'0'`` 进不了 ``if ($strQuantity = …)``，所以也不算有挂单。

        Python 直接写 ``if quantity:`` 会把这里判成"有"，从而多摆一张空盘口表。
        """
        ref = FakeRef(ask_quantities=["0"], ask_prices=["5.0"])
        self.assertFalse(has_trading_quantity(ref))


class BaseColumnTest(unittest.TestCase):
    def test_columns_are_transaction_price_quantity(self):
        table = TradingService().get_table(full_ref())
        self.assertEqual([c["text"] for c in table["columns"]],
                         ["交易", "价格", "数量"])

    def test_widths_and_colors_follow_stocktable_php(self):
        table = TradingService().get_table(full_ref())
        self.assertEqual([c["width"] for c in table["columns"]], [50, 70, 100])
        self.assertEqual([c["color"] for c in table["columns"]], ["", "blue", "indigo"])

    def test_table_id_is_trading(self):
        self.assertEqual(TradingService().get_table(full_ref())["id"], TRADING_TABLE_ID)


class RowOrderTest(unittest.TestCase):
    """``_echoTradingTableData()`` 的行序：涨停 → 卖5…卖1 → 买1…买5 → 跌停。"""

    def setUp(self):
        self.rows = TradingService().get_table(full_ref())["rows"]

    def test_twelve_rows_when_all_five_levels_present(self):
        self.assertEqual(len(self.rows), 2 * TRADING_QUOTE_NUM + 2)

    def test_labels_in_php_order(self):
        labels = [row.cells[0] for row in self.rows]
        self.assertEqual(labels, [
            "涨停",
            "卖5", "卖4", "卖3", "卖2", "卖1",
            "买1", "买2", "买3", "买4", "买5",
            "跌停",
        ])

    def test_limit_rows_have_no_quantity(self):
        self.assertEqual(self.rows[0].cells[2], "")
        self.assertEqual(self.rows[-1].cells[2], "")

    def test_limit_and_first_level_are_colored(self):
        backgrounds = [row.background for row in self.rows]
        self.assertEqual(backgrounds, [
            TRADING_COLOR_LIMIT,
            "", "", "", "", TRADING_COLOR_FIRST,   # 卖5…卖1，只有卖1染色
            TRADING_COLOR_FIRST, "", "", "", "",   # 买1…买5，只有买1染色
            TRADING_COLOR_LIMIT,
        ])

    def test_price_cell_uses_price_display(self):
        # 假 ref 的 get_price_display 是 "%.*f"，问2/3 位小数
        self.assertEqual(self.rows[1].cells[1], "5.040")
        self.assertEqual(self.rows[6].cells[1], "4.940")


class LimitPriceTest(unittest.TestCase):
    """涨停/跌停的基准价三元表达式。"""

    def _limit_row(self, ref, now):
        table = TradingService(now_provider=lambda _ref: now).get_table(ref)
        return table["rows"][0], table["rows"][-1]

    def test_outside_trading_hours_use_current_price(self):
        # 22:00 非交易时段 → 基准价 = 现价 4.884 → 涨停 5.3724
        ref = full_ref(price="4.884", prev_price="4.814")
        now = _dt("2026-10-09 22:00:00")
        up, down = self._limit_row(ref, now)
        self.assertEqual(up.cells[1], "5.372")     # 4.884 * 1.1
        self.assertEqual(down.cells[1], "4.396")   # 4.884 * 0.9

    def test_during_trading_hours_use_prev_close(self):
        # 10:30 交易时段且行情日期是今天 → 基准价 = 昨收 4.814
        ref = full_ref(price="4.884", prev_price="4.814")
        now = _dt("2026-10-09 10:30:00")
        up, down = self._limit_row(ref, now)
        self.assertEqual(up.cells[1], "5.295")     # 4.814 * 1.1
        self.assertEqual(down.cells[1], "4.333")   # 4.814 * 0.9

    def test_noon_break_still_counts_as_trading(self):
        """``IsStockMarketTrading($now_ymd, false)`` 的第二参数是"不剔除午休"。

        所以 12:15 仍按交易时段算 —— 若把它写成默认值 ``true``，A 股午休这一小时
        的涨跌停会换成"以现价为基准"。这条边界值得单独钉住：午休期间页面同样在刷。
        """
        ref = full_ref(price="4.884", prev_price="4.814")
        up, _ = self._limit_row(ref, _dt("2026-10-09 12:15:00"))
        self.assertEqual(up.cells[1], "5.295")

    def test_stale_quote_date_falls_back_to_current_price(self):
        """行情不是今天的（抓取挂了），闸门生效 → 用现价而不是昨收。"""
        ref = full_ref(date="2026-10-08")
        now = _dt("2026-10-09 10:30:00")
        up, _ = self._limit_row(ref, now)
        self.assertEqual(up.cells[1], "5.372")

    def test_weekend_uses_current_price(self):
        ref = full_ref()
        now = _dt("2026-10-10 10:30:00")  # 周六
        up, _ = self._limit_row(ref, now)
        self.assertEqual(up.cells[1], "5.372")


class PartialLevelTest(unittest.TestCase):
    def test_only_quote_levels_with_quantity_appear(self):
        ref = FakeRef(ask_prices=["5.00", "5.01"], ask_quantities=["10", "20"],
                      bid_prices=["4.94"], bid_quantities=["9"])
        rows = TradingService().get_table(ref)["rows"]
        labels = [row.cells[0] for row in rows]
        self.assertEqual(labels, ["涨停", "卖2", "卖1", "买1", "跌停"])

    def test_missing_level_does_not_shift_others(self):
        """第 2 档没有量 → 整档跳过，后面的标签不往前串。"""
        ref = FakeRef(ask_prices=["5.00", "5.01", "5.02"],
                      ask_quantities=["10", "", "30"])
        rows = TradingService().get_table(ref)["rows"]
        self.assertEqual([row.cells[0] for row in rows],
                         ["涨停", "卖3", "卖1", "跌停"])

    def test_no_quantity_at_all_returns_none(self):
        """PHP 的 ``_checkTradingQuantity()`` 为真时整段 return，页面不摆空盘口表。"""
        self.assertIsNone(TradingService().get_table(FakeRef()))


class FundTradingTest(unittest.TestCase):
    """``EchoFundTradingParagraph()``：溢价列是嵌套追加的。"""

    class FakeFund:
        def __init__(self, official=None, fair=None, realtime=None, symbol="SZ161130",
                     price="4.884", prev_price="4.814"):
            self._official = official
            self._fair = fair
            self._realtime = realtime
            self.ref = full_ref(symbol=symbol, price=price, prev_price=prev_price)

        def get_stock_ref(self):
            return self.ref

        def get_official_net_value(self):
            return self._official

        def get_fair_net_value(self):
            return self._fair

        def get_realtime_net_value(self):
            return self._realtime

    def test_without_any_est_title_is_just_the_sentence(self):
        table = TradingService().fund_table(self.FakeFund())
        self.assertEqual(table["title"], "SZ161130当前5档交易价格")
        self.assertEqual(len(table["columns"]), 3)

    def test_official_est_adds_one_premium_column(self):
        table = TradingService().fund_table(self.FakeFund(official=4.545))
        self.assertEqual([c["text"] for c in table["columns"]],
                         ["交易", "价格", "数量", "官方溢价"])
        self.assertEqual(table["title"],
                         "SZ161130当前5档交易价格相对于EST4.545的溢价")

    def test_fair_and_realtime_append_in_nested_order(self):
        table = TradingService().fund_table(self.FakeFund(
            official=4.545, fair=4.549, realtime=4.6))
        self.assertEqual([c["text"] for c in table["columns"]],
                         ["交易", "价格", "数量", "官方溢价", "参考溢价", "实时溢价"])
        self.assertEqual(table["title"],
                         "SZ161130当前5档交易价格相对于EST4.545|4.549|4.600的溢价")

    def test_realtime_without_fair_is_impossible(self):
        """PHP 里 realtime 的取值嵌在 fair 的 if 里，所以不会出现"跳过参考、只有实时"。"""
        table = TradingService().fund_table(self.FakeFund(official=4.545, realtime=4.6))
        self.assertEqual([c["text"] for c in table["columns"]],
                         ["交易", "价格", "数量", "官方溢价"])

    def test_premium_cells_are_aligned_with_columns(self):
        table = TradingService().fund_table(self.FakeFund(official=4.545, fair=4.549))
        self.assertEqual(table["cell_count"], len(table["columns"]))

    def test_premium_color_follows_the_up_red_down_green_ruling(self):
        """``GetPercentageDisplay()`` 在 PHP 里是"跌红涨黑"，全站已裁决为涨红跌绿。

        官方估值 5.5 时，卖5（5.04）的溢价是 5.04/5.5-1 = -8.36%，应为绿。
        """
        table = TradingService().fund_table(self.FakeFund(official=5.5))
        cell = table["rows"][1].cells[3]
        self.assertEqual(cell[0], "-8.36%")
        self.assertEqual(cell[1], "green")
        table = TradingService().fund_table(self.FakeFund(official=4.0))   # 正溢价
        self.assertEqual(table["rows"][1].cells[3][1], "red")


class PairTradingTest(unittest.TestCase):
    def test_pair_always_adds_the_premium_column(self):
        """即使没有官方估值，PHP 也无条件加了这一列 —— 于是行会少一格（照抄）。"""

        class Ref(FakeRef):
            def get_official_net_value(self):
                return None

            def get_pair_ref(self):
                return None

        ref = Ref(ask_prices=["5.00"], ask_quantities=["10"])
        table = TradingService().pair_table(ref)
        self.assertEqual([c["text"] for c in table["columns"]],
                         ["交易", "价格", "数量", "官方溢价"])
        self.assertEqual(table["cell_count"], 3)
        self.assertEqual(table["column_count"], 4)

    def test_pair_est_title(self):
        class PairRef:
            def get_symbol(self):
                return "^NDX"

            def get_my_stock_link(self):
                return "<a href='/fund/%5ENDX'>^NDX</a>"

        class Ref(FakeRef):
            def get_official_net_value(self):
                return 4.545

            def get_pair_ref(self):
                return PairRef()

        ref = Ref(ask_prices=["5.00"], ask_quantities=["10"])
        table = TradingService().pair_table(ref)
        self.assertEqual(table["title"],
                         "SZ161130当前5档交易价格相对于^NDX的溢价")
        self.assertEqual(table["cell_count"], table["column_count"])


class CallbackColumnTest(unittest.TestCase):
    """回调列：``call_user_func($callback)`` 出表头、带价参数出格子。"""

    @staticmethod
    def _callback(price=None):
        if price is None:
            return "自定义列"
        return "%.2f 的值" % float(price)

    def test_header_comes_from_zero_arg_call(self):
        table = TradingService().fund_table(FundTradingTest.FakeFund(),
                                            callback=self._callback)
        self.assertEqual(table["columns"][-1]["text"], "自定义列")
        self.assertEqual(table["columns"][-1]["width"], TRADING_CALLBACK_WIDTH)

    def test_cell_comes_from_the_raw_price_string(self):
        table = TradingService().fund_table(FundTradingTest.FakeFund(),
                                            callback=self._callback)
        self.assertEqual(table["rows"][1].cells[-1], "5.04 的值")

    def test_missing_price_drops_the_cell(self):
        """PHP 的 ``empty($strPrice)`` 判定 —— 缺价格时这一行少一格，**不补空**。"""

        def callback(price=None):
            return "自定义列" if price is None else "x"

        ref = FakeRef(ask_prices=[None], ask_quantities=["10"], bid_prices=[], bid_quantities=[])
        rows = build_trading_rows(ref, [], callback=callback)
        lengths = {len(row.cells) for row in rows}
        self.assertEqual(sorted(lengths), [3, 4])  # 缺价那一行只有 3 格


class RotationLinkTest(unittest.TestCase):
    def test_rotatable_symbol_gets_the_link(self):
        """``SZ162411``（华宝油气）在 PHP 的 ``in_arrayXopQdii()`` 里。"""
        builder = lambda symbol, kind: "/%s/%s" % (kind, symbol)
        table = TradingService(link_builder=builder).fund_table(
            FundTradingTest.FakeFund(symbol="SZ162411"))
        self.assertEqual(table["rotation_link"],
                         {"text": "轮动交易", "url": "/rotationtrading/SZ162411"})

    def test_other_symbols_get_nothing(self):
        """``SZ164906`` 不属于那四个轮动分组。

        注意 **``SZ161130`` 是有的**（``in_arrayQqqMatch()``），所以负例不能顺手
        拿它当"普通符号" —— 一开始就是这么踩到的。
        """
        builder = lambda symbol, kind: "/%s/%s" % (kind, symbol)
        table = TradingService(link_builder=builder).fund_table(
            FundTradingTest.FakeFund(symbol="SZ164906"))
        self.assertIsNone(table["rotation_link"])

    def test_without_link_builder_there_is_no_link(self):
        """默认工厂没有注入构造器 —— `/rotationtrading` 页面还没迁，不该编 URL。"""
        table = TradingService().fund_table(FundTradingTest.FakeFund(symbol="SZ162411"))
        self.assertIsNone(table["rotation_link"])


def _dt(text):
    """构造一个 Asia/Shanghai 时区的 tz-aware datetime。"""
    import datetime

    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Asia/Shanghai")
    except Exception:  # Python 3.7 没有 zoneinfo
        import tzutil

        zone = tzutil.get_time_zone("Asia/Shanghai")
    naive = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=zone)


if __name__ == "__main__":
    unittest.main()
