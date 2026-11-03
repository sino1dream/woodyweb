import unittest

from calc.fundcalc import (
    QDII_HK_EST_EXCLUDED,
    PalmmicroFundCalculator,
    extra_est_symbols,
    fund_adjust_position,
    fund_reverse_adjust_position,
    qdii_est_cny_symbol,
    qdii_est_symbol,
    qdii_est_symbol_map,
    qdii_fund_est_symbol,
    qdii_fund_est_symbol_map,
    qdii_get_calibration,
    qdii_get_peer_value,
    qdii_get_value,
    qdii_hk_est_symbol,
    set_extra_est_symbols,
)


class FundFormulaTest(unittest.TestCase):
    def test_adjust_position_is_reversible(self):
        ratio = 0.72
        current = 2.345
        old = 1.876

        adjusted = fund_adjust_position(ratio, current, old)

        self.assertAlmostEqual(fund_reverse_adjust_position(ratio, adjusted, old), current)

    def test_qdii_calibration_round_trip(self):
        est = 130.68
        cny = 7.18
        net_value = 1.234

        calibration = qdii_get_calibration(est, cny, net_value)
        qdii_value = qdii_get_value(est, cny, calibration)

        self.assertAlmostEqual(qdii_value, net_value)
        self.assertAlmostEqual(qdii_get_peer_value(qdii_value, cny, calibration), est)

    def test_symbol_mapping_matches_php_tables(self):
        self.assertEqual(qdii_est_symbol("SZ159518"), "XOP")
        self.assertEqual(qdii_est_symbol("SZ161125"), "^GSPC")
        self.assertEqual(qdii_hk_est_symbol("SH513180"), "^HSTECH")
        self.assertEqual(qdii_hk_est_symbol("SZ161124"), "^HSI")


class PalmmicroFundCalculatorTest(unittest.TestCase):
    def test_direct_calibration_estimation_and_reverse(self):
        api = PalmmicroFundCalculator(
            {
                "SZ159518": {
                    "position": "0.8",
                    "calibration": "700",
                    "netvalue": "1.2",
                    "symbol_hedge": "XOP",
                    "CNY": "7.0",
                }
            }
        )

        value = api.est_net_value("SZ159518", {"XOP": 140.0, "CNY": 7.1})

        self.assertAlmostEqual(value, (1.0 - 0.8) * 1.2 + 0.8 * 140.0 * 7.1 / 700.0)
        self.assertAlmostEqual(api.reverse_est({"SZ159518": value, "CNY": 7.1}), 140.0)

    def test_nested_hedge_estimation(self):
        api = PalmmicroFundCalculator(
            {
                "SZ161125": {
                    "position": "1",
                    "calibration": "5000",
                    "netvalue": "1.0",
                    "symbol_hedge": "SPY",
                },
                "SPY": {
                    "position": "1",
                    "calibration": "1",
                    "netvalue": "6000",
                    "symbol_hedge": "^GSPC",
                },
                "^GSPC": {
                    "position": "1",
                    "calibration": "1",
                    "netvalue": "6000",
                    "symbol_hedge": "hf_ES",
                },
            }
        )

        self.assertAlmostEqual(api.est_net_value("SZ161125", {"hf_ES": 6200.0}), 1.24)
        self.assertAlmostEqual(api.reverse_est({"SZ161125": 1.24}), 6200.0)

    def test_holdings_estimation(self):
        api = PalmmicroFundCalculator(
            {
                "SZ160723": {
                    "position": "0.9",
                    "netvalue": "1.5",
                    "CNY": "7.2",
                    "CNYholdings": "7.0",
                    "symbol_hedge": {
                        "AAA": {"ratio": "60", "price": "10", "est_price": "11"},
                        "BBB": {"ratio": "40", "price": "20", "est_price": "19"},
                    },
                }
            }
        )

        total = (60.0 * 11.0 / 10.0 + 40.0 * 19.0 / 20.0) / 100.0
        expected = 1.5 * (1.0 + 0.9 * (total * 7.2 / 7.0 - 1.0))

        self.assertAlmostEqual(api.est_net_value("SZ160723"), expected)

    def test_missing_and_empty_inputs_return_zero(self):
        api = PalmmicroFundCalculator({})

        self.assertEqual(api.est_net_value("UNKNOWN"), 0.0)
        self.assertEqual(api.reverse_est({}), 0.0)
        self.assertEqual(api.reverse_est({"CNY": 7.1}), 0.0)


class ProductionCaliberTest(unittest.TestCase):
    """A 股 QDII 基金的校准口径：**est × 汇率 / 净值**（``qdiiref.php:3``）。

    ``QdiiGetCalibration($strEst, $strCNY, $strNetValue)`` 就一行：
    ``$strEst * $strCNY / $strNetValue``。与 :class:`test_pairref.UsPairCaliberTest`
    那一条（美股配对**不含汇率**）是**两套口径**，别混。

    期望值取自生产端 ``sz161125cn.php``（2026-10-12 的校准记录）::

        校准值 16517.232   净值 3.1817
        est 标的 ^GSPC = 7807.24（``QdiiGetEstArray()``，标普系 → ^GSPC）
        USCNY = 6.7298

    ``7807.24 * 6.7298 / 3.1817 = 16517.2``，与页面一致到 0.01%。
    """

    #: 生产端 sz161125cn.php 上的四个数。
    EST = 7807.24
    CNY = 6.7298
    NET_VALUE = 3.1817
    PRODUCTION_CALIBRATION = 16517.232

    def test_qdii_calibration_matches_production_sz161125(self):
        calibration = qdii_get_calibration(self.EST, self.CNY, self.NET_VALUE)
        self.assertAlmostEqual(
            calibration, self.PRODUCTION_CALIBRATION,
            delta=self.PRODUCTION_CALIBRATION * 0.001,  # 0.1%
        )

    def test_qdii_value_gives_back_the_net_value(self):
        """用同一个 factor 反算回净值 —— 口径自洽的前提。

        这条同时也是"演示库 QQQ 那次 345 万"的镜像检查：那边是
        ``EstFromPair = 配对价 / factor``，这里是 ``est × cny / factor``，
        方向反了就会差一个 ``cny²`` 且不报错。
        """
        factor = qdii_get_calibration(self.EST, self.CNY, self.NET_VALUE)
        self.assertAlmostEqual(
            qdii_get_value(self.EST, self.CNY, factor), self.NET_VALUE, places=4
        )

    def test_est_symbol_is_the_index_not_the_etf(self):
        """标普系基金的 est 是 ``^GSPC`` 不是 ``SPY`` —— 差了约 10 倍。"""
        self.assertEqual(qdii_est_symbol("SZ161125"), "^GSPC")
        self.assertEqual(qdii_est_symbol("SZ161130"), "^NDX")


class RegionalEstTest(unittest.TestCase):
    """跨地区（港股/日股/欧股）的 est 与汇率分派。

    PHP 有四个入口函数、按 Reference 类各带一个汇率；我们合成一张表。
    这里钉住的是"**别只认美股**"——2026-11-02 之前 ``--funds`` 用的
    ``qdii_est_symbol_map()`` 只有 ``QdiiGetEstArray()`` 那张美股表，
    港股 28 只一条校准记录都没有，页面照样 200，看日志才发现。
    """

    def test_hk_indices_map_to_the_hk_rate(self):
        self.assertEqual(qdii_est_cny_symbol("^HSI"), "HKCNY")
        self.assertEqual(qdii_est_cny_symbol("^HSTECH"), "HKCNY")
        self.assertEqual(qdii_est_cny_symbol("^HSCE"), "HKCNY")

    def test_jp_and_eu_map_to_their_own_rates(self):
        self.assertEqual(qdii_est_cny_symbol("znb_NKY"), "JPCNY")
        self.assertEqual(qdii_est_cny_symbol("znb_TPX"), "JPCNY")
        self.assertEqual(qdii_est_cny_symbol("znb_DAX"), "EUCNY")
        self.assertEqual(qdii_est_cny_symbol("znb_CAC"), "EUCNY")

    def test_us_est_still_uses_usd(self):
        for est in ("^NDX", "^GSPC", "^DJI", "XOP", "XBI", "QQQ", None, ""):
            self.assertEqual(qdii_est_cny_symbol(est), "USCNY")

    def test_hk_funds_resolve_est_by_group(self):
        # 恒生 / 恒生科技 / 国企三族各取一只，est 必须是对应的指数
        self.assertEqual(qdii_fund_est_symbol("SZ159920"), "^HSI")     # 恒生ETF
        self.assertEqual(qdii_fund_est_symbol("SZ159740"), "^HSTECH")  # 恒生科技
        self.assertEqual(qdii_fund_est_symbol("SZ159850"), "^HSCE")    # H股50

    def test_hk_hang_seng_special_member(self):
        # PHP: ``in_arrayHangSengQdiiHk($s) || $s == 'SZ161124'``
        self.assertEqual(qdii_fund_est_symbol("SZ161124"), "^HSI")

    def test_us_funds_keep_their_est(self):
        self.assertEqual(qdii_fund_est_symbol("SZ161125"), "^GSPC")
        self.assertEqual(qdii_fund_est_symbol("SZ162719"), "IEO")

    def test_excluded_hk_fund_is_not_auto_calibrated(self):
        """``SH501025`` 的 est 是 A 股指数，汇率口径没核清 -> 有意排除。"""
        self.assertIn("SH501025", QDII_HK_EST_EXCLUDED)
        self.assertIsNone(qdii_fund_est_symbol("SH501025"))

    def test_combined_map_covers_more_than_the_us_table(self):
        us_only = qdii_est_symbol_map()
        combined = qdii_fund_est_symbol_map()
        self.assertGreater(len(combined), len(us_only))
        # 美股族必须一个不少，且值不变
        for symbol, est in us_only.items():
            self.assertEqual(combined.get(symbol), est)
        # 港股 28 只要全在里面
        hk = [s for s, est in combined.items() if est in ("^HSI", "^HSTECH", "^HSCE")]
        self.assertEqual(len(hk), 28)

    def test_unknown_symbol_has_no_est(self):
        self.assertIsNone(qdii_fund_est_symbol("SH999999"))


class ExtraEstSymbolTest(unittest.TestCase):
    """库里的 ``fundestmap`` 覆盖层（2026-11-03 加）。

    背景：est 映射一直是硬编码的（照抄 PHP ``QdiiGetEstArray()`` 一族），
    代码里没配的基金「跟踪代码」为空、官方 EST 整组为空 —— 而这类配置
    本质是数据，不该每次改都要发版。覆盖层因此放在库里，优先级高于硬编码。
    """

    def setUp(self):
        # 全量替换语义：每个用例都从"没有覆盖"开始
        set_extra_est_symbols(None)

    def tearDown(self):
        set_extra_est_symbols(None)

    def test_override_fills_a_gap(self):
        """硬编码表里没有的基金，靠覆盖层补上。"""
        self.assertIsNone(qdii_est_symbol("SZ160719"))
        set_extra_est_symbols({"SZ160719": "GLD"})
        self.assertEqual(qdii_est_symbol("SZ160719"), "GLD")
        self.assertEqual(qdii_fund_est_symbol("SZ160719"), "GLD")

    def test_override_can_replace_a_hard_coded_value(self):
        """已有映射也能覆盖 —— 但明确是"覆盖"，不是"仅补充"。"""
        self.assertEqual(qdii_est_symbol("SZ161125"), "^GSPC")
        set_extra_est_symbols({"SZ161125": "SPY"})
        self.assertEqual(qdii_est_symbol("SZ161125"), "SPY")

    def test_clearing_falls_back_to_hard_coded(self):
        set_extra_est_symbols({"SZ160719": "GLD"})
        set_extra_est_symbols(None)
        self.assertIsNone(qdii_est_symbol("SZ160719"))
        self.assertEqual(qdii_est_symbol("SZ161125"), "^GSPC")

    def test_injection_is_total_not_incremental(self):
        """第二次注入必须**清掉**上一次 —— 否则长驻进程会留下上个库的配置。"""
        set_extra_est_symbols({"SZ160719": "GLD", "SZ161116": "GLD"})
        set_extra_est_symbols({"SZ164701": "GLD"})
        self.assertIsNone(qdii_est_symbol("SZ160719"))
        self.assertEqual(qdii_est_symbol("SZ164701"), "GLD")

    def test_combined_map_includes_overrides(self):
        """自动校准名单（``qdii_fund_est_symbol_map``）要能看到新增基金。"""
        set_extra_est_symbols({"SZ160719": "GLD"})
        self.assertEqual(qdii_fund_est_symbol_map().get("SZ160719"), "GLD")

    def test_us_table_stays_a_mirror_of_php(self):
        """``qdii_est_symbol_map()`` 是 ``QdiiGetEstArray()`` 的镜像，**不含**覆盖。

        它被 ``qdii_symbols_for_est()`` 等按 est 反查基金的地方用到；掺进
        库里的配置会让"这张表到底对应 PHP 哪一段"变得说不清。要全表请用
        :func:`qdii_fund_est_symbol_map`。
        """
        set_extra_est_symbols({"SZ160719": "GLD"})
        self.assertNotIn("SZ160719", qdii_est_symbol_map())

    def test_blank_values_are_ignored(self):
        """空符号 / 空标的直接丢弃，不写进映射（脏数据不该让页面显示空格子以外的错）。"""
        set_extra_est_symbols({"SZ160719": "  ", "": "GLD", "  ": "GLD"})
        self.assertEqual(extra_est_symbols(), {})


if __name__ == "__main__":
    unittest.main()
