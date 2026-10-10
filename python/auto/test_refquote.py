"""``refquote`` 的单元测试：**先读库快照，再兜底联网**。

对应 PHP ``MysqlReference`` 构造流程里那次行情加载（``LoadSinaData()``）。
本项目部署形态不同 —— 有 ``quoteupdatercli --daemon`` 往 ``realtimequote``
写快照，所以页面优先读库。

## 这个文件要盯住的两条纪律

1. **构造期不联网**：``QdiiReference`` 构造里还会算实时 EST，
   若构造期联网，每个页面请求都会去抓一遍新浪。
2. **快照优先**：库里有时绝不能联网（既慢又给站方添压力）。
"""

from __future__ import annotations

import unittest

from refquote import (
    NetworkQuoteSource,
    SnapshotQuoteSource,
    apply_network,
    apply_snapshot,
    build_quote_loader,
    load_quote_into,
    refresh_quotes,
    stem_of,
)


class FakeQuote:
    """``quotestore.RecentQuote`` 形状。"""

    def __init__(self, price="4.884", prev_price="4.814", date="2026-10-09",
                 time="15:00:00", name="纳指LOF"):
        self.price = price
        self.prev_price = prev_price
        self.date = date
        self.time = time
        self.name = name


class FakeRef:
    """``stockref.StockReference`` 形状的最小替身。"""

    def __init__(self, symbol="SZ161130", sina_symbol="sz161130", has_data=False):
        self.str_symbol = symbol
        self._sina_symbol = sina_symbol
        self.str_price = "0"
        self.str_prev_price = None
        self.str_date = None
        self.str_time = None
        self.str_name = ""
        self.has_data = has_data
        self.quote_loader = None
        self.loaded = 0

    def get_symbol(self):
        return self.str_symbol

    def get_sina_symbol(self):
        return self._sina_symbol

    def set_has_data(self, value=True):
        self.has_data = value

    def load_sina_data(self):
        """模仿 stockref：从 quote_loader 取文本后装填。"""
        self.loaded += 1
        text = self.quote_loader("f") if self.quote_loader else None
        if not text:
            self.has_data = False
            return
        fields = text.split(",")
        self.str_price = fields[0]
        self.str_date = fields[1] if len(fields) > 1 else None
        self.has_data = True


class FakeSource:
    def __init__(self, quotes=None):
        self.quotes = quotes or {}
        self.calls = []

    def get_stored_quote(self, symbol):
        self.calls.append(symbol)
        return self.quotes.get(symbol)


class FakeNetwork:
    """假的联网源：记录被调了几次。"""

    def __init__(self, text="4.884,2026-10-09"):
        self.text = text
        self.calls = []

    def fetch_text(self, sina_symbol):
        self.calls.append(sina_symbol)
        return self.text


class StemTest(unittest.TestCase):
    def test_strips_path_and_extension(self):
        self.assertEqual(stem_of("debug/sina/sz161130.txt"), "sz161130")
        self.assertEqual(stem_of("debug\\sina\\sz161130.txt"), "sz161130")

    def test_accepts_bare_stem(self):
        self.assertEqual(stem_of("sz161130"), "sz161130")

    def test_handles_empty(self):
        self.assertEqual(stem_of(""), "")
        self.assertEqual(stem_of(None), "")


class ApplySnapshotTest(unittest.TestCase):
    def test_fills_every_quote_field(self):
        ref = FakeRef()
        self.assertTrue(apply_snapshot(ref, FakeQuote()))

        self.assertEqual(ref.str_price, "4.884")
        self.assertEqual(ref.str_prev_price, "4.814")
        self.assertEqual(ref.str_date, "2026-10-09")
        self.assertEqual(ref.str_time, "15:00:00")
        self.assertTrue(ref.has_data)

    def test_zero_price_is_not_success(self):
        """``'0'`` 等同于"没有行情" —— 与 ``is_zero_string()`` 口径一致。"""
        ref = FakeRef()
        for price in (None, "", "0", "0.0", "0.00"):
            self.assertFalse(apply_snapshot(FakeRef(), FakeQuote(price=price)), price)
        self.assertFalse(ref.has_data)

    def test_none_quote_is_not_success(self):
        self.assertFalse(apply_snapshot(FakeRef(), None))


class LoadQuoteTest(unittest.TestCase):
    """``load_quote_into`` 的路径选择。"""

    def test_snapshot_hit_does_not_touch_network(self):
        """**库里有时绝不联网。**"""
        source = FakeSource({"SZ161130": FakeQuote()})
        network = FakeNetwork()

        path = load_quote_into(FakeRef(), source=source, network=network,
                               allow_network=True)

        self.assertEqual(path, "snapshot")
        self.assertEqual(network.calls, [], "读了快照就不该再联网")

    def test_construction_does_not_go_to_network_by_default(self):
        """**默认（构造期口径）不联网** —— 这是本模块最重要的一条约束。"""
        source = FakeSource()          # 库里没有
        network = FakeNetwork()

        path = load_quote_into(FakeRef(), source=source, network=network)

        self.assertEqual(path, "none")
        self.assertEqual(network.calls, [], "默认不该联网")

    def test_request_time_falls_back_to_network(self):
        """请求期显式打开时，快照没有才联网。"""
        source = FakeSource()
        network = FakeNetwork()

        path = load_quote_into(FakeRef(), source=source, network=network,
                               allow_network=True)

        self.assertEqual(path, "network")
        self.assertEqual(network.calls, ["sz161130"])
        self.assertEqual(source.calls, ["SZ161130"], "应当先试过快照")

    def test_no_source_does_not_crash(self):
        self.assertEqual(load_quote_into(FakeRef(), source=None), "none")

    def test_source_without_reader_does_not_crash(self):
        class Bare:
            pass

        self.assertEqual(load_quote_into(FakeRef(), source=Bare()), "none")


class ApplyNetworkTest(unittest.TestCase):
    def test_uses_stockref_parser(self):
        """联网拿到的原始文本要交给 ``stockref`` 既有解析器，不在这里重写字段映射。"""
        ref = FakeRef()
        self.assertTrue(apply_network(ref, FakeNetwork("9.99,2026-01-01")))
        self.assertEqual(ref.str_price, "9.99")
        self.assertEqual(ref.loaded, 1, "应当走 load_sina_data()")

    def test_restores_quote_loader(self):
        """临时替换的 loader 必须还原，否则会污染后续调用。"""
        ref = FakeRef()
        original = object()
        ref.quote_loader = original

        apply_network(ref, FakeNetwork())

        self.assertIs(ref.quote_loader, original)

    def test_no_sina_symbol_returns_false(self):
        network = FakeNetwork()
        self.assertFalse(apply_network(FakeRef(sina_symbol=None), network))
        self.assertEqual(network.calls, [])

    def test_empty_text_returns_false(self):
        self.assertFalse(apply_network(FakeRef(), FakeNetwork(text=None)))


class RefreshQuotesTest(unittest.TestCase):
    """请求期兜底入口。"""

    def test_only_refs_without_data_are_refreshed(self):
        filled = FakeRef(symbol="SZ161130", has_data=True)
        empty = FakeRef(symbol="SZ161125", has_data=False)
        source = FakeSource()
        network = FakeNetwork()

        # refresh_quotes 内部自建 network，这里直接验证 snapshot 侧行为
        result = refresh_quotes([filled, empty], source=source)

        self.assertNotIn("SZ161130", result, "已有行情的对象不该被处理")
        self.assertIn("SZ161125", result)

    def test_skips_none_entries(self):
        self.assertEqual(refresh_quotes([None], source=FakeSource()), {})


class BuildQuoteLoaderTest(unittest.TestCase):
    def test_loader_reads_snapshot(self):
        source = FakeSource({"SZ161130": FakeQuote()})
        loader = build_quote_loader(source)

        ref = FakeRef()
        path = loader(ref)

        self.assertEqual(path, "snapshot")
        self.assertEqual(ref.str_price, "4.884")

    def test_loader_never_uses_network_by_default(self):
        """``build_quote_loader`` 交给构造用，因此默认不联网。"""
        loader = build_quote_loader(FakeSource())
        self.assertEqual(loader(FakeRef()), "none")


if __name__ == "__main__":
    unittest.main()
