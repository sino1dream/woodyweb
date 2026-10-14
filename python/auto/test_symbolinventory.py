"""``core.symbolinventory`` 的单元测试。

## 为什么值得单测

这份清单是 P0 数据层的**写入依据**（``dbtools/seed_p0_symbols.py`` 与探针
``_scratch/_p0_1_symbol_inventory.py`` 都引它）。数字写错不会报错，
只会在库里少一行、页面上少一只基金 —— 所以把"多少只、谁映射到谁"
钉在这里，与生产端页面（``README_p0_data_layer.md`` §一）互为见证。
"""

from __future__ import annotations

import unittest

from core.symbolinventory import (
    est_symbol_of,
    est_symbols,
    fund_pairs,
    fund_symbols,
    required_stock_symbols,
)


class SymbolInventoryTest(unittest.TestCase):
    def test_fund_count_matches_production_pages(self):
        """102 只唯一基金 = 五个数组 110 - 8 只跨 qdii/qdiimix。

        生产端四页合计 41 + 38 + 15 + 5 + 3 = 102，完全一致
        （港股页 38 = 数组 29 + 9 只 hkmix；qdiimix 页 15 见
        ``README_p0_data_layer.md`` §1.4 的漂移说明）。
        """
        symbols = fund_symbols()
        self.assertEqual(len(symbols), 102)
        self.assertEqual(len(symbols), len(set(symbols)), "有重复符号")

    def test_known_est_mappings(self):
        """逐条锚定（与 PHP ``QdiiGetEstArray()`` 及生产端页面一致）。"""
        cases = {
            # qdii：10 只基础基金各自的 est
            "SH501300": "AGG",
            "SH513290": "IBB",
            "SH513400": "^DJI",
            "SZ160140": "VNQ",
            "SZ160416": "IXC",
            "SZ161126": "RSPH",
            "SZ161128": "VGT",
            "SZ162415": "XLY",
            "SZ162719": "IEO",
            "SZ164906": "KWEB",
            # 分组：XOP / XBI / SPY(->^GSPC) / QQQ(->^NDX)
            "SZ162411": "XOP",
            "SZ159502": "XBI",
            "SZ161125": "SPY",
            "SH513100": "QQQ",
            "SZ161130": "QQQ",
            # qdiihk
            "SH501025": "SH000869",
            "SH513010": "^HSTECH",
            "SH510900": "^HSCE",
            "SZ164705": "^HSI",
            # qdiijp / qdiieu
            "SH513800": "znb_TPX",
            "SH513000": "znb_NKY",
            "SH513080": "znb_CAC",
            "SH513030": "znb_DAX",
        }
        for symbol, expected in cases.items():
            self.assertEqual(est_symbol_of(symbol), expected, symbol)

    def test_leverage_substitution_replaces_the_index(self):
        """杠杆替代：QQQ / SPY 组的基金拿 ETF 取数，不拿指数。

        指数 ``^NDX`` / ``^GSPC`` 不落 ``netvaluehistory`` /
        ``stockhistory``，直接映射过去会"取不到数 -> 静默算不出 EST"。
        """
        qqq_funds = [s for s in fund_symbols() if s in
                     ("SH513100", "SH513110", "SH513390", "SZ159501", "SZ161130")]
        for symbol in qqq_funds:
            self.assertEqual(est_symbol_of(symbol), "QQQ", symbol)
        self.assertEqual(est_symbol_of("SH513500"), "SPY")
        self.assertEqual(est_symbol_of("SZ159655"), "SPY")

    def test_qdii_mix_members_have_no_est(self):
        """QdiiMix 成员没有 est 符号 —— 估值走持仓口径（CK4，未迁）。"""
        for symbol in ("SH513360", "SZ159509", "SZ159529", "SH513850", "SZ159577",
                       "SH501312", "SZ160216", "SZ164701", "SZ163208", "SH513090",
                       "SZ159792", "SH501018", "SZ160723"):
            self.assertIsNone(est_symbol_of(symbol), symbol)

    def test_est_symbols_are_the_etfs_not_the_indices(self):
        """est 中间层清单里**没有** ^NDX / ^GSPC（已被杠杆替代掉）。"""
        symbols = est_symbols()
        self.assertNotIn("^NDX", symbols)
        self.assertNotIn("^GSPC", symbols)
        self.assertIn("QQQ", symbols)
        self.assertIn("SPY", symbols)
        self.assertEqual(len(symbols), 22)

    def test_fund_pairs_one_row_per_fund_with_est(self):
        """70 对 = 102 只减去 32 只 QdiiMix 成员。"""
        pairs = fund_pairs()
        self.assertEqual(len(pairs), 70)
        funds = [fund for fund, _ in pairs]
        self.assertEqual(len(funds), len(set(funds)), "同一只基金出现多次")
        # 每对的 est 都必须非空且与 est_symbol_of 一致
        for fund, est in pairs:
            self.assertEqual(est, est_symbol_of(fund), fund)

    def test_required_stock_symbols_covers_everything(self):
        """写入依据要覆盖基金、est、汇率、期货、即期外汇。"""
        required = required_stock_symbols()
        self.assertEqual(len(required), len(set(required)), "有重复符号")
        for symbol in fund_symbols():
            self.assertIn(symbol, required, symbol)
        for symbol in est_symbols():
            self.assertIn(symbol, required, symbol)
        for symbol in ("USCNY", "HKCNY", "EUCNY", "JPCNY",
                       "hf_NQ", "hf_ES", "hf_CL", "hf_NK",
                       "fx_susdcny", "fx_shkdcny", "fx_sjpycny", "fx_seurcny"):
            self.assertIn(symbol, required, symbol)

    def test_sina_quote_symbols_excludes_other_pipelines(self):
        """新浪行情链路只抓基金 + est；期货/外汇各有入口。"""
        from core.symbolinventory import sina_quote_symbols

        symbols = sina_quote_symbols()
        self.assertEqual(len(symbols), 124)
        self.assertEqual(len(symbols), len(set(symbols)), "有重复符号")
        for symbol in fund_symbols() + est_symbols():
            self.assertIn(symbol, symbols, symbol)
        for symbol in ("USCNY", "HKCNY", "EUCNY", "JPCNY",
                       "hf_NQ", "hf_ES", "hf_CL", "hf_NK",
                       "fx_susdcny", "fx_shkdcny", "fx_sjpycny", "fx_seurcny"):
            self.assertNotIn(symbol, symbols, symbol)


if __name__ == "__main__":
    unittest.main()
