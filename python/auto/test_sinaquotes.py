"""`python/auto/sinaquotes.py` 与 `python/auto/quotestore.py` 的单元测试。

对应 PHP：``php/stock.php`` 的 ``GetSinaQuotes()``/``explodeQuote()``、
``php/url.php`` 的 ``url_get_contents()``、``php/stock/mystockref.php`` 的
``_updateStockHistory()``、``php/stock/sqlstocksymbol.php`` 的 ``WriteHistory()``。

抓取部分全部用固定样本离线测试（样本取自真实响应），不打网络。
"""

import unittest

from sql import quotestore
from source import sinaquotes
from ref.stockref import StockReference

# 真实新浪响应样本（与远端分支 captured 样本一致），用于离线验证
LINE_CN = (
    'var hq_str_sz162411="华宝油气,0.891,0.891,0.913,0.915,0.889,'
    '0.912,0.913,450669050,406234599.722,1495300,0.912,1017200,0.911,'
    '2230574,0.910,960700,0.909,717800,0.908,605171,0.913,1059900,0.914,'
    '1285002,0.915,163602,0.916,139500,0.917,2026-06-08,15:00:00,00";'
)
LINE_HK = (
    'var hq_str_rt_hk00700="TENCENT,腾讯控股,380.000,385.000,390.000,378.000,'
    '388.000,0,0,0,0,0,12345678,0,0,0,0,2026/01/05,16:08:00";'
)
LINE_US = (
    'var hq_str_gb_xop="XOP NAME,168.2300,1.35,2026-06-08 23:36:45,2.2400,'
    '168.1000,170.6350,167.8100,190.3600,118.9550,1679373,3195734,0,0.00,'
    '--,0.00,0.00,0.00,0.00,0,0,0.0000,0.00,0.00,,Jun 08 11:36AM EDT,'
    '165.9900,0,1,2026,283871768.0000,0.0000,0.0000,0.0000,0.0000,'
    '165.9900";'
)


def make_cn_line(fields=32):
    """按 A 股/基金的真实下标造一行，保证 date(30)/time(31) 落在正确位置。

    `_onSinaDataCN()` 用到的下标：0 名称、1 开、2 昨收、3 现价、4 高、5 低、
    8 量、30 日期、31 时间。真实响应比这更长，缺失字段用空串占位即可。
    """
    values = [""] * fields
    values[0] = "华宝油气"
    values[1] = "0.891"
    values[2] = "0.891"
    values[3] = "0.913"
    values[4] = "0.915"
    values[5] = "0.889"
    values[8] = "450669050"
    values[30] = "2026-06-08"
    values[31] = "15:00:00"
    return 'var hq_str_sz162411="%s";' % ",".join(values)


class SplitQuoteLinesTest(unittest.TestCase):
    def test_splits_by_sina_symbol(self):
        text = "\n".join([LINE_CN, LINE_HK, LINE_US])
        lines = sinaquotes.split_quote_lines(text)

        self.assertEqual(sorted(lines), ["gb_xop", "rt_hk00700", "sz162411"])
        self.assertEqual(lines["sz162411"], LINE_CN)

    def test_ignores_blank_and_unrelated_lines(self):
        text = "\n".join(["", "   ", "some unrelated text", LINE_CN, "//comment"])
        lines = sinaquotes.split_quote_lines(text)

        self.assertEqual(list(lines), ["sz162411"])

    def test_decodes_gb18030_content(self):
        # 新浪的 A 股/基金响应是 GBK，中文名需要能正确解出来
        content = LINE_CN.encode("gb18030")
        text = sinaquotes.decode_sina_content(content)

        self.assertIn("华宝油气", text)

    def test_decodes_utf8_content(self):
        content = LINE_HK.encode("utf-8")
        self.assertIn("腾讯控股", sinaquotes.decode_sina_content(content))

    def test_data_url_matches_php_format(self):
        # PHP: 'http://hq.sinajs.cn/list='.$strSinaSymbols
        self.assertEqual(
            sinaquotes.get_sina_data_url(["sz162411", "gb_xop"]),
            "http://hq.sinajs.cn/list=sz162411,gb_xop",
        )


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        return None


class FakeSession:
    """记录请求并按预置响应返回，用于离线测试抓取路径。"""

    def __init__(self, content, fail=False):
        self.content = content
        self.fail = fail
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        if self.fail:
            raise RuntimeError("boom")
        return FakeResponse(self.content)


class SinaQuoteClientTest(unittest.TestCase):
    def test_fetch_sends_referer_and_user_agent(self):
        session = FakeSession(LINE_CN.encode("gb18030"))
        client = sinaquotes.SinaQuoteClient(session=session)

        text = client.fetch(["sz162411"])

        self.assertIn("hq_str_sz162411", text)
        self.assertEqual(len(session.calls), 1)
        call = session.calls[0]
        self.assertEqual(call["url"], "http://hq.sinajs.cn/list=sz162411")
        # Referer 必需，否则新浪返回 403
        self.assertEqual(call["headers"]["Referer"], sinaquotes.SINA_FINANCE_URL)
        self.assertIn("User-Agent", call["headers"])

    def test_fetch_returns_lines_in_requested_order(self):
        session = FakeSession((LINE_CN + "\n" + LINE_US).encode("gb18030"))
        client = sinaquotes.SinaQuoteClient(session=session)

        text = client.fetch(["sz162411", "gb_xop"])
        lines = text.split("\n")

        self.assertEqual(len(lines), 2)
        self.assertIn("sz162411", lines[0])
        self.assertIn("gb_xop", lines[1])

    def test_fetch_failure_returns_none_like_php(self):
        session = FakeSession(b"", fail=True)
        client = sinaquotes.SinaQuoteClient(session=session)

        self.assertIsNone(client.fetch(["sz162411"]))
        self.assertIsNotNone(client.last_error)

    def test_empty_symbol_list_returns_none(self):
        client = sinaquotes.SinaQuoteClient(session=FakeSession(b""))
        self.assertIsNone(client.fetch([]))

    def test_cache_avoids_second_request(self):
        session = FakeSession(LINE_CN.encode("gb18030"))
        client = sinaquotes.SinaQuoteClient(session=session, cache_seconds=60)

        client.fetch(["sz162411"])
        client.fetch(["sz162411"])

        self.assertEqual(len(session.calls), 1)

    def test_cache_expiry_triggers_request(self):
        session = FakeSession(LINE_CN.encode("gb18030"))
        client = sinaquotes.SinaQuoteClient(session=session, cache_seconds=0)

        client.fetch(["sz162411"])
        client.fetch(["sz162411"])

        self.assertEqual(len(session.calls), 2)

    def test_load_uses_registered_file_mapping(self):
        # stockref 的 quote_loader 接口：传缓存文件名，返回该符号那一行
        session = FakeSession(LINE_CN.encode("gb18030"))
        client = sinaquotes.SinaQuoteClient(session=session)
        client.cache_quote_files({"debug/sina/sz162411.txt": "sz162411"})

        line = client.load("debug/sina/sz162411.txt")

        self.assertIn("hq_str_sz162411", line)
        # 未登记的文件名等于"没有缓存"
        self.assertIsNone(client.load("debug/sina/unknown.txt"))


class QuoteStoreTest(unittest.TestCase):
    def _reference(self, session_content, symbol):
        """用离线响应构造一个解析好的 StockReference。

        注意缓存文件名是用**新浪符号**拼的（SZ162411 -> sz162411），
        与 ``load_sina_data()`` 内部 ``GetSinaSymbol()`` 的行为一致，
        所以这里要先转换再登记，否则 loader 查不到映射。
        """
        from ref.stockref import sina_quote_file_name
        from core.stocksymbol import StockSymbol

        sina_symbol = StockSymbol(symbol).get_sina_symbol()
        client = sinaquotes.SinaQuoteClient(session=FakeSession(session_content))
        client.cache_quote_files({sina_quote_file_name(sina_symbol): sina_symbol})
        reference = StockReference(symbol, quote_loader=client.load)
        reference.load_sina_data()
        return reference

    def test_write_history_upserts(self):
        store = quotestore.MemoryQuoteStore()

        self.assertTrue(store.write_history("1", "2026-06-08", "0.913", "450669050"))
        store.write_history("1", "2026-06-08", "0.920", "450669050")

        record = store.history[("1", "2026-06-08")]
        self.assertEqual(record["close"], "0.920")
        # adjclose 缺省时取 close
        self.assertEqual(record["adjclose"], "0.920")

    def test_write_history_rejects_empty_date(self):
        store = quotestore.MemoryQuoteStore()
        self.assertFalse(store.write_history("1", "", "1.0"))

    def test_write_net_value_rejects_weekend(self):
        store = quotestore.MemoryQuoteStore()

        # 2026-01-03 是周六，PHP 的 WriteDaily 会拒绝
        self.assertFalse(store.write_net_value("1", "2026-01-03", "1.2"))
        self.assertTrue(store.write_net_value("1", "2026-01-05", "1.2"))

    def test_save_reference_writes_history(self):
        reference = self._reference(make_cn_line().encode("gb18030"), "SZ162411")
        store = quotestore.MemoryQuoteStore()

        result = store.save_reference("1", reference)

        self.assertTrue(result["history"])
        record = store.history[("1", reference.get_date())]
        self.assertEqual(record["close"], reference.get_price())
        self.assertEqual(store.dates["1"], reference.get_date())

    def test_save_reference_skips_incomplete_quote(self):
        # open/high/low 缺一个就整体跳过，避免写入半截数据
        reference = StockReference("SZ162411")
        reference.str_date = "2026-06-08"
        reference.str_price = "0.913"
        reference.str_open = ""
        store = quotestore.MemoryQuoteStore()

        result = store.save_reference("1", reference)

        self.assertFalse(result["history"])
        self.assertEqual(store.history, {})

    def test_save_reference_rejects_missing_date(self):
        reference = StockReference("SZ162411")
        reference.str_open = "1"
        reference.str_high = "1"
        reference.str_low = "1"
        reference.str_price = "1"
        store = quotestore.MemoryQuoteStore()

        self.assertFalse(store.save_reference("1", reference)["history"])


class PremiumRateTest(unittest.TestCase):
    def test_premium_when_price_above_net_value(self):
        self.assertAlmostEqual(quotestore.get_premium_rate("1.05", "1.00"), 5.0)

    def test_discount_when_price_below_net_value(self):
        self.assertAlmostEqual(quotestore.get_premium_rate("0.95", "1.00"), -5.0)

    def test_missing_values_return_none(self):
        self.assertIsNone(quotestore.get_premium_rate("", "1.00"))
        self.assertIsNone(quotestore.get_premium_rate("1.05", None))
        self.assertIsNone(quotestore.get_premium_rate("N/A", "1.00"))
        # 净值为 0 时不做除法
        self.assertIsNone(quotestore.get_premium_rate("1.05", "0"))

    def test_format_premium_rate(self):
        self.assertEqual(quotestore.format_premium_rate(1.5), "1.5%")
        self.assertEqual(quotestore.format_premium_rate(-2.345), "-2.35%")
        self.assertEqual(quotestore.format_premium_rate(0.0), "0")
        self.assertEqual(quotestore.format_premium_rate(None), "")

    def test_is_invalid_field(self):
        self.assertTrue(quotestore.is_invalid_field(None))
        self.assertTrue(quotestore.is_invalid_field(""))
        self.assertTrue(quotestore.is_invalid_field("N/A"))
        self.assertFalse(quotestore.is_invalid_field("0"))


if __name__ == "__main__":
    unittest.main()
