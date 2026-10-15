"""`python/auto/stockref.py` 的单元测试。

对应 PHP：``php/stock/stockref.php``、``php/debug.php``、``php/stock.php``。

数值格式化的边界值另有与真实 PHP 的逐项对照：
``python -B _php_numfmt_compare.py`` 与 ``python -B _php_rtrim0_fuzz.py``；
这里只覆盖能被独立断言的行为。
"""

import datetime
import os
import unittest

from ref import stockref as sr


class NumberFormatTest(unittest.TestCase):
    def test_rtrim0_strips_trailing_zeros_like_php(self):
        self.assertEqual(sr.rtrim0("1.50"), "1.5")
        self.assertEqual(sr.rtrim0("100"), "100")
        self.assertEqual(sr.rtrim0("0.000"), "0")
        self.assertEqual(sr.rtrim0("3.0"), "3")
        self.assertEqual(sr.rtrim0("-2.25"), "-2.25")

    def test_rtrim0_uses_php_fourteen_digit_precision(self):
        # PHP 的 strval() 按 precision=14 输出，比 Python repr() 的 17 位短
        self.assertEqual(sr.rtrim0("0.1234567890123456"), "0.12345678901235")
        self.assertEqual(sr.rtrim0("3.141592653589793"), "3.1415926535898")

    def test_rtrim0_exponent_form_matches_php(self):
        # PHP 写 '1.0E-7'：大写 E、尾数带 .0、指数不带前导零
        self.assertEqual(sr.rtrim0("1e-7"), "1.0E-7")
        self.assertEqual(sr.rtrim0("-1e-7"), "-1.0E-7")
        self.assertEqual(sr.rtrim0("1e20"), "1.0E+20")
        self.assertEqual(sr.rtrim0("0.00001"), "1.0E-5")

    def test_rtrim0_is_lenient_like_floatval(self):
        # PHP floatval('') / floatval('abc') / floatval('1,234') 分别得到 0/0/1
        self.assertEqual(sr.rtrim0(""), "0")
        self.assertEqual(sr.rtrim0("abc"), "0")
        self.assertEqual(sr.rtrim0("1,234"), "1")
        self.assertEqual(sr.rtrim0(" 42 "), "42")
        self.assertEqual(sr.rtrim0(".5"), "0.5")

    def test_is_zero_string_threshold(self):
        self.assertTrue(sr.is_zero_string("0"))
        self.assertTrue(sr.is_zero_string("0.0000004"))
        self.assertFalse(sr.is_zero_string("0.0000005"))
        self.assertFalse(sr.is_zero_string("1"))
        self.assertTrue(sr.is_zero_string("abc"))

    def test_get_hm(self):
        self.assertEqual(sr.get_hm("06:56:22"), "06:56")
        self.assertEqual(sr.get_hm(""), "")
        # PHP 的 substr 不补齐，短字符串原样返回
        self.assertEqual(sr.get_hm("9:5:3"), "9:5:3")

    def test_stock_get_percentage(self):
        self.assertAlmostEqual(sr.stock_get_percentage(100.0, 110.0), 10.0)
        self.assertAlmostEqual(sr.stock_get_percentage(100.0, 90.0), -10.0)
        # 除数为零（绝对值小于 MIN_FLOAT_VAL）时 PHP 直接返回 0.0
        self.assertEqual(sr.stock_get_percentage(0.0, 110.0), 0.0)

    def test_format_price_removes_trailing_zeros(self):
        self.assertEqual(sr.format_price(1.5, 1.0, 3), "1.5")
        self.assertEqual(sr.format_price(1.0, 1.0, 3), "1")
        self.assertEqual(sr.format_price(12.345, 1.0, 2), "12.35")

    def test_format_price_normalizes_negative_zero(self):
        # PHP 的 sprintf('%.2f', -0.0) 得到 '0.00'，Python 会得到 '-0.00'
        self.assertEqual(sr.format_price(-0.0, 1.0, 2), "0")


class QuoteSplitTest(unittest.TestCase):
    def test_remove_double_quotation_marks(self):
        line = 'var hq_str_gb_xop="XOP,1.23,4.56";'
        self.assertEqual(sr.remove_double_quotation_marks(line), "XOP,1.23,4.56")

    def test_remove_double_quotation_marks_without_quotes(self):
        self.assertEqual(sr.remove_double_quotation_marks("XOP,1.23"), "XOP,1.23")

    def test_explode_quote(self):
        line = 'var hq_str_sh600000="浦发银行,10.00,10.10";'
        self.assertEqual(sr.explode_quote(line), ["浦发银行", "10.00", "10.10"])


class TimeZoneTest(unittest.TestCase):
    def test_convert_date_time_shanghai_to_tokyo(self):
        # 上海 09:00 对应东京 10:00（同为 UTC+8/+9，无夏令时）
        date_text, time_text = sr.convert_date_time(
            "2026-01-05 09:00:00", sr.TIME_ZONE_SHANGHAI, "Asia/Tokyo"
        )
        self.assertEqual(date_text, "2026-01-05")
        self.assertEqual(time_text, "10:00:00")

    def test_convert_date_time_shanghai_to_us_winter(self):
        # 2026-01-05 是美东冬令时（EST，UTC-5），上海 21:00 即美东 08:00
        date_text, time_text = sr.convert_date_time(
            "2026-01-05 21:00:00", sr.TIME_ZONE_SHANGHAI, sr.TIME_ZONE_US
        )
        self.assertEqual(date_text, "2026-01-05")
        self.assertEqual(time_text, "08:00:00")

    def test_convert_date_time_shanghai_to_us_summer(self):
        # 2026-07-06 是美东夏令时（EDT，UTC-4），上海 20:00 即美东 08:00
        date_text, time_text = sr.convert_date_time(
            "2026-07-06 20:00:00", sr.TIME_ZONE_SHANGHAI, sr.TIME_ZONE_US
        )
        self.assertEqual(date_text, "2026-07-06")
        self.assertEqual(time_text, "08:00:00")

    def test_parse_datetime_accepts_slash_dates(self):
        # 新浪港股返回 2016/03/02 这种格式
        moment = sr._parse_datetime("2016/03/02 10:30:00", sr.TIME_ZONE_SHANGHAI)
        self.assertEqual(moment.strftime("%Y-%m-%d %H:%M:%S"), "2016-03-02 10:30:00")

    def test_sina_quote_file_name_matches_php_rule(self):
        # PHP DebugGetSymbolFile() 会把 / + , ^ . : % 替换成下划线并转小写
        self.assertEqual(sr.sina_quote_file_name("^HSI"), os.path.join("debug", "sina", "_hsi.txt"))
        self.assertEqual(
            sr.sina_quote_file_name("fx_sUSDCNH".lower()),
            os.path.join("debug", "sina", "fx_susdcnh.txt"),
        )


def _us_quote_array():
    """构造一份美股 gb_ 行情字段，下标与 PHP _onSinaDataUS() 一致。"""
    array = [""] * 30
    array[0] = "XOP"
    array[1] = "140.5"
    array[5] = "139.0"
    array[6] = "141.2"
    array[7] = "138.1"
    array[10] = "1234567"
    array[25] = "2026-01-05 10:30:00"
    array[26] = "139.0"
    array[29] = "2026"
    return array


def _cn_quote_array():
    """构造一份 A 股行情字段，下标与 PHP _onSinaDataCN() 一致。

    注意 PHP 的循环是 ``for ($i = 10; $i < 20; $i += 2)``，所以它只读取
    10/12/14/16/18 这 5 个「买量」下标，并顺延 10 位取 20/22/24/26/28 作为「卖量」。
    也就是说虽然叫十档，实际只解析 5 档；本夹具按同样的下标布局填充，
    奇数下标不参与解析，留空即可。
    """
    array = [""] * 40
    array[0] = "浦发银行"
    array[1] = "9.90"
    array[2] = "10.00"
    array[3] = "10.05"
    array[4] = "10.10"
    array[5] = "9.85"
    array[8] = "1000000"
    for level in range(0, 5):
        array[10 + level * 2] = str(100 + level)          # 买量：10, 12, 14, 16, 18
        array[11 + level * 2] = "%.2f" % (9.99 - level / 100.0)   # 买价：11, 13, 15, 17, 19
        array[20 + level * 2] = str(200 + level)          # 卖量：20, 22, 24, 26, 28
        array[21 + level * 2] = "%.2f" % (10.01 + level / 100.0)  # 卖价：21, 23, 25, 27, 29
    array[30] = "2026-01-05"
    array[31] = "10:30:00"
    return array


class StockReferenceTest(unittest.TestCase):
    def test_inherits_symbol_classification(self):
        ref = sr.StockReference("SZ162411")
        # 继承自 StockSymbol，市场判断可直接用
        self.assertEqual(ref.is_symbol_a(), "162411")
        self.assertEqual(ref.get_precision(), 3)

    def test_default_state(self):
        ref = sr.StockReference("XOP")
        self.assertTrue(ref.has_quote_data())
        self.assertEqual(ref.get_volume(), "0")
        self.assertEqual(ref.get_external_link(), "")

    def test_set_has_data(self):
        ref = sr.StockReference("XOP")
        ref.set_has_data(False)
        self.assertFalse(ref.has_quote_data())

    def test_on_sina_data_us(self):
        ref = sr.StockReference("XOP")
        ref._on_sina_data_us(_us_quote_array())

        self.assertEqual(ref.get_symbol(), "XOP")
        self.assertEqual(ref.str_name, "XOP")
        self.assertEqual(ref.get_price(), "140.5")
        self.assertEqual(ref.get_prev_price(), "139")
        self.assertEqual(ref.str_open, "139.0")
        self.assertEqual(ref.get_volume(), "1234567")
        # 美东冬令时：按纽约时区取值，日期只取传入的年份
        self.assertEqual(ref.str_date, "2026-01-05")
        self.assertEqual(ref.str_time, "10:30:00")
        self.assertIsNone(ref.extended_ref)

    def test_on_sina_data_us_builds_extended_reference(self):
        array = _us_quote_array()
        array[24] = "2026-01-05 08:00:00"  # 盘前时间，存在即建立盘外引用
        array[21] = "141.0"
        array[27] = "3000.0"

        ref = sr.StockReference("XOP")
        ref._on_sina_data_us(array)

        self.assertIsNotNone(ref.extended_ref)
        self.assertEqual(ref.extended_ref.get_price(), "141")
        self.assertEqual(ref.extended_ref.get_volume(), "3000")
        self.assertTrue(ref.extended_ref.is_extended_market())

    def test_extended_market_session_name(self):
        # GetHour() <= 9 算盘前，否则盘后
        pre_array = _us_quote_array()
        pre_array[24] = "2026-01-05 09:05:00"
        pre = sr.ExtendedTradingReference(pre_array, "XOP")
        self.assertEqual(pre.get_market_session(), "盘前交易")

        post_array = _us_quote_array()
        post_array[24] = "2026-01-05 16:30:00"
        post = sr.ExtendedTradingReference(post_array, "XOP")
        self.assertEqual(post.get_market_session(), "盘后交易")

    def test_on_sina_data_cn(self):
        ref = sr.StockReference("SH600000")
        ref._on_sina_data_cn(_cn_quote_array())

        self.assertEqual(ref.str_name, "浦发银行")
        self.assertEqual(ref.get_price(), "10.05")
        self.assertEqual(ref.get_prev_price(), "10")
        # str_open 是原始字段，不做 rtrim0
        self.assertEqual(ref.str_open, "9.90")
        self.assertEqual(ref.get_volume(), "1000000")
        self.assertEqual(ref.get_date(), "2026-01-05")
        self.assertEqual(ref.get_time(), "10:30:00")

        # 十档买卖盘：PHP 是 for ($i = 10; $i < 20; $i += 2)，只循环 5 次
        self.assertEqual(len(ref.bid_prices), 5)
        self.assertEqual(ref.get_bid_price(0), "9.99")
        self.assertEqual(ref.get_bid_quantity(0), "100")
        self.assertEqual(ref.get_ask_price(0), "10.01")
        self.assertEqual(ref.get_ask_quantity(0), "200")
        self.assertEqual(ref.get_bid_price(2), "9.97")
        # 越界档位返回 None，对应 PHP 访问不存在下标得到 null
        self.assertIsNone(ref.get_bid_price(99))

    def test_on_sina_data_hk(self):
        array = [""] * 20
        array[0] = "TENCENT"
        array[1] = "腾讯控股"
        array[2] = "380.0"
        array[3] = "385.0"
        array[4] = "390.0"
        array[5] = "378.0"
        array[6] = "388.0"
        array[12] = "12345678"
        array[17] = "2026/01/05"
        array[18] = "16:08:00"

        ref = sr.StockReference("00700")
        ref._on_sina_data_hk(array)

        self.assertEqual(ref.str_name, "TENCENT")
        self.assertEqual(ref.str_chinese_name, "腾讯控股")
        self.assertEqual(ref.get_price(), "388")
        self.assertEqual(ref.get_prev_price(), "385")
        # 新浪港股日期是 2026/01/05，需要转成 2026-01-05
        self.assertEqual(ref.get_date(), "2026-01-05")
        self.assertEqual(ref.get_time(), "16:08:00")

    def test_get_available_price_and_quantity(self):
        ref = sr.StockReference("SH600000")
        ref._on_sina_data_cn(_cn_quote_array())

        # 卖出看买盘，买入看卖盘
        self.assertEqual(ref.get_available_price(True), "9.99")
        self.assertEqual(ref.get_available_price(False), "10.01")
        self.assertEqual(ref.get_available_quantity(True), "100")
        self.assertEqual(ref.get_available_quantity(False), "200")

    def test_get_percentage_and_text(self):
        ref = sr.StockReference("XOP")
        ref.str_price = "110.0"
        ref.str_prev_price = "100.0"

        self.assertAlmostEqual(ref.get_percentage(), 10.0)
        self.assertEqual(ref.get_percentage_text(), "10%")

    def test_get_percentage_text_zero_change(self):
        ref = sr.StockReference("XOP")
        ref.str_price = "100.0"
        ref.str_prev_price = "100.0"

        # 零涨跌显示 '0' 而不是 '0%'
        self.assertEqual(ref.get_percentage_text(), "0")

    def test_get_percentage_string_rounds_tiny_change_to_zero(self):
        ref = sr.StockReference("XOP")
        ref.str_price = "100.0001"
        ref.str_prev_price = "100.0"
        # 美股精度 2，阈值 50/10^2 = 0.5，涨跌绝对值远小于它 -> '0'
        self.assertEqual(ref.get_percentage_string(), "0")

    def test_time_helpers(self):
        ref = sr.StockReference("SH600000")
        ref.str_date = "2026-01-05"
        ref.str_time = "13:05:22"

        self.assertEqual(ref.get_date_time(), "2026-01-05 13:05:22")
        self.assertEqual(ref.get_time_hm(), "13:05")
        self.assertEqual(ref.get_hour(), 13)
        self.assertEqual(ref.get_minute(), 5)
        self.assertEqual(ref.get_hour_minute(), 1305)

    def test_convert_tick_uses_symbol_time_zone(self):
        ref = sr.StockReference("SH600000")
        ref.str_date = "2026-01-05"
        ref.str_time = "00:00:00"

        # 上海 2026-01-05 00:00 即 UTC 2026-01-04 16:00
        expected = int(datetime.datetime(2026, 1, 4, 16, 0, tzinfo=datetime.timezone.utc).timestamp())
        self.assertEqual(ref.convert_tick(), expected)

    def test_is_extended_market(self):
        ref = sr.StockReference("XOP")
        ref.str_time = "10:00:00"
        self.assertFalse(ref.is_extended_market())

        ref.str_time = "09:00:00"
        self.assertTrue(ref.is_extended_market())

        ref.str_time = "16:01:00"
        self.assertTrue(ref.is_extended_market())

    def test_load_sina_data_for_us_without_loader(self):
        ref = sr.StockReference("XOP")
        ref.load_sina_data()
        # 没有注入 loader，等于没有缓存文件
        self.assertFalse(ref.has_quote_data())

    def test_load_sina_data_for_us_with_loader(self):
        array = _us_quote_array()
        line = '"' + ",".join(array) + '"'
        loaded = {}

        def loader(file_name):
            loaded["file"] = file_name
            return line

        ref = sr.StockReference("XOP", quote_loader=loader)
        ref.load_sina_data()

        self.assertTrue(ref.has_quote_data())
        self.assertEqual(ref.get_price(), "140.5")
        self.assertEqual(loaded["file"], os.path.join("debug", "sina", "gb_xop.txt"))

    def test_load_sina_data_dispatch_by_market(self):
        cn_array = _cn_quote_array()
        cn_line = '"' + ",".join(cn_array) + '"'
        ref = sr.StockReference("SH600000", quote_loader=lambda _: cn_line)
        ref.load_sina_data()
        self.assertEqual(ref.str_name, "浦发银行")

    def test_load_sina_data_short_quote_marks_no_data(self):
        ref = sr.StockReference("SH600000", quote_loader=lambda _: "a,b,c")
        ref.load_sina_data()
        self.assertFalse(ref.has_quote_data())

    def test_load_sina_future_data_us(self):
        array = [""] * 30
        array[0] = "1400.0"
        array[4] = "1410.0"
        array[5] = "1390.0"
        array[6] = "10:30:00"
        array[7] = "1395.0"
        array[8] = "1398.0"
        array[12] = "2026-01-05"
        array[13] = "标普期货"

        ref = sr.StockReference("hf_ES", quote_loader=lambda _: ",".join(array))
        ref.load_sina_future_data()

        self.assertEqual(ref.str_name, "标普期货")
        self.assertEqual(ref.get_price(), "1400")
        self.assertEqual(ref.get_prev_price(), "1395")
        # _onSinaFuture() 走的是 _convertDateTimeToUS()，按上海时间解读后换到美东
        self.assertEqual(ref.str_date, "2026-01-04")
        self.assertEqual(ref.str_time, "21:30:00")

    def test_load_sina_future_data_cn(self):
        array = [""] * 30
        array[0] = "沪金"
        array[1] = "103045"
        array[2] = "500.0"
        array[3] = "505.0"
        array[4] = "495.0"
        array[8] = "502.0"
        array[9] = "0.000"
        array[10] = "501.0"
        array[14] = "12345"
        array[15] = "AU"
        array[17] = "2026-01-05"
        array[27] = "503.5"

        ref = sr.StockReference("nf_AU0", quote_loader=lambda _: ",".join(array))
        ref.load_sina_future_data()

        self.assertEqual(ref.str_name, "AU-沪金")
        self.assertEqual(ref.get_price(), "502")
        self.assertEqual(ref.get_time(), "10:30:45")
        self.assertEqual(ref.get_date(), "2026-01-05")
        self.assertEqual(ref.get_volume(), "12345")
        # 结算价为 0 时 PHP 存 false，这里存 None
        self.assertIsNone(ref.str_settle_price)
        self.assertEqual(ref.str_vwap, "503.5")

    def test_load_sina_fund_data(self):
        array = ["华夏成长", "1.2345", "x", "1.2000", "2026-01-05"]
        ref = sr.StockReference("f_240019", quote_loader=lambda _: ",".join(array))
        ref.load_sina_fund_data()

        self.assertEqual(ref.str_name, "华夏成长")
        self.assertEqual(ref.get_price(), "1.2345")
        self.assertEqual(ref.get_prev_price(), "1.2")
        self.assertEqual(ref.get_date(), "2026-01-05")

    def test_load_sina_forex_data(self):
        array = ["10:30:00", "a", "b", "7.1000", "c", "7.05", "7.20", "7.00", "7.1500", "美元人民币", "2026-01-05"]
        ref = sr.StockReference("fx_susdcnh", quote_loader=lambda _: ",".join(array))
        ref.load_sina_forex_data()

        self.assertEqual(ref.get_price(), "7.15")
        self.assertEqual(ref.get_prev_price(), "7.1")
        self.assertEqual(ref.str_name, "美元人民币")
        self.assertEqual(ref.str_open, "7.05")
        # str_high/str_low 是原始字段，不做 rtrim0，保留新浪给的小数位
        self.assertEqual(ref.str_high, "7.20")
        self.assertEqual(ref.str_low, "7.00")

    def test_load_data_dispatches_forex_before_plain_sina(self):
        """``MyStockReference::LoadData()``（``mystockref.php:117-122``）三分支。

        外汇的字段布局跟普通股不一样，直接走 ``load_sina_data()`` 会被判成
        "没数据" —— 页面上 USDCNH / USDCNY 两行就整行空。改回"一律
        ``load_sina_data()``"时本测试应失败。
        """
        array = ["10:30:00", "a", "b", "7.1000", "c", "7.05", "7.20", "7.00",
                 "7.1500", "离岸人民币（香港）", "2026-01-05"]
        ref = sr.StockReference("fx_susdcnh", quote_loader=lambda _: ",".join(array))
        ref.load_data()
        self.assertTrue(ref.has_quote_data())
        self.assertEqual(ref.get_price(), "7.15")

        plain = sr.StockReference("XOP", quote_loader=lambda _: "")
        plain.load_data()
        self.assertFalse(plain.has_quote_data())

    def test_load_sina_forex_data_scales_jpy(self):
        array = ["10:30:00", "a", "b", "0.0500", "c", "d", "e", "f", "0.0510", "日元人民币", "2026-01-05"]
        ref = sr.StockReference("fx_sjpycny", quote_loader=lambda _: ",".join(array))
        ref.load_sina_forex_data()

        # PHP 对 fx_sjpycny 会乘 100：0.0510*100 -> 5.1，0.0500*100 -> 5
        self.assertEqual(ref.get_price(), "5.1")
        self.assertEqual(ref.get_prev_price(), "5")

    def test_on_sina_global_index_short_format(self):
        # count == 6：昨收 = 现价 - 涨跌，日期时间取自最后一个时间戳
        array = ["恒生指数", "20000.0", "100.0", "", "", "1767225600"]
        ref = sr.StockReference("^HSI")
        ref._on_sina_global_index(array, 6)

        self.assertEqual(ref.str_name, "恒生指数")
        self.assertEqual(ref.get_price(), "20000")
        self.assertEqual(ref.get_prev_price(), "19900")
        self.assertEqual(ref.str_date, "2026-01-01")
        self.assertEqual(ref.str_time, "08:00:00")

    def test_on_sina_global_index_long_format_converts_time_zone(self):
        # count != 6：日期时间按上海时间给，需换算到符号所在时区
        array = ["日经225", "40000.0", "", "", "", "", "2026-01-05", "09:00:00", "39900", "39500", "40100", "39800"]
        ref = sr.StockReference("znb_NKY")
        ref._on_sina_global_index(array, len(array))

        self.assertEqual(ref.get_prev_price(), "39500")
        self.assertEqual(ref.str_open, "39900")
        self.assertEqual(ref.str_high, "40100")
        self.assertEqual(ref.str_low, "39800")
        # 上海 09:00 -> 东京 10:00
        self.assertEqual(ref.str_date, "2026-01-05")
        self.assertEqual(ref.str_time, "10:00:00")

    def test_on_sina_global_index_keeps_shanghai_for_unknown_index(self):
        array = ["富时100", "8000.0", "", "", "", "", "2026-01-05", "09:00:00", "7990", "7950", "8010", "7980"]
        ref = sr.StockReference("znb_UKX")
        ref._on_sina_global_index(array, len(array))

        # znb_UKX 的时区是 Asia/Shanghai，因此时间不变
        self.assertEqual(ref.get_time_zone(), "Asia/Shanghai")
        self.assertEqual(ref.str_time, "09:00:00")

    def test_check_adjust_factor_time_same_time_zone(self):
        etf = sr.StockReference("SH510300")
        etf.str_date = "2026-01-05"
        etf.str_time = "15:00:00"

        ref = sr.StockReference("SH600000")
        ref.str_date = "2026-01-05"
        ref.str_time = "15:00:22"

        # 同一时区：日期和 HH:MM 一致即可（PHP 比较的是 GetTimeHM()，即 'HH:MM'）
        self.assertTrue(ref.check_adjust_factor_time(etf))

        ref.str_time = "14:59:00"
        self.assertFalse(ref.check_adjust_factor_time(etf))

    def test_check_adjust_factor_time_rejects_missing_data(self):
        etf = sr.StockReference("SH510300")
        etf.set_has_data(False)
        ref = sr.StockReference("SH600000")

        self.assertFalse(ref.check_adjust_factor_time(None))
        self.assertFalse(ref.check_adjust_factor_time(etf))


if __name__ == "__main__":
    unittest.main()
