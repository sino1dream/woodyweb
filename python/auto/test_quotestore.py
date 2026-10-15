"""``sql.quotestore.QuoteStore`` 的落库**闸门**测试（全离线）。

只测"该不该写"，不测"写进去长什么样" —— 后者要真库，交给
``dbtools/`` 那批脚本和页面实测。

钉住的是这一条：**外汇不能进 ``stockhistory``**。
``StockReference.load_sina_forex_data()`` 也会填 open/high/low/close，
字段校验过得去，于是汇率会被当成股价写一行历史。实测踩到过：
``quotecli fx_susdcnh`` 之后库里多出 ``fx_susdcnh 2026-10-15 close=6.69``。
"""

import unittest

from ref.stockref import StockReference
from sql.quotestore import QuoteStore


def _forex_line() -> str:
    """新浪外汇行（字段布局与 ``forexsource.SinaForexSource`` 的一致）。"""
    fields = ["10:30:00", "6.6901", "6.6905", "6.6959", "95",
              "6.6961", "6.6975", "6.688", "6.6901", "离岸人民币（香港）",
              "2026-10-15"]
    return '"' + ",".join(fields) + '"'


class SaveReferenceGateTest(unittest.TestCase):
    """``save_reference()`` 的"不写"分支。"""

    def test_forex_does_not_reach_stockhistory(self):
        """外汇有价也不落行情历史 —— 汇率的历史走 ``forexupdater`` 那条路。

        去掉 ``save_reference`` 里那个 ``is_sina_forex()`` 提前返回时，
        本测试应失败（``history`` 变 ``True``）。
        """
        ref = StockReference("fx_susdcnh", quote_loader=lambda _: _forex_line())
        ref.load_sina_forex_data()
        self.assertTrue(ref.has_quote_data(), "前置条件：确实解析出了价")

        store = QuoteStore(None)    # 连库都不给 —— 真往下走会直接炸
        result = store.save_reference("180", ref)
        self.assertEqual(result, {"history": False, "netvalue": False})

    def test_plain_symbol_without_data_writes_nothing(self):
        """没有行情时同样什么都不写（对照片，确认上面那条不是靠"没数据"蒙对的）。"""
        ref = StockReference("SH", quote_loader=lambda _: "")
        ref.load_data()
        self.assertFalse(ref.has_quote_data())

        store = QuoteStore(None)
        self.assertEqual(store.save_reference("179", ref),
                         {"history": False, "netvalue": False})


if __name__ == "__main__":
    unittest.main()
