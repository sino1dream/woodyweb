import unittest

from calc.fundcalc import (
    PalmmicroFundCalculator,
    fund_adjust_position,
    fund_reverse_adjust_position,
    qdii_est_symbol,
    qdii_get_calibration,
    qdii_get_peer_value,
    qdii_get_value,
    qdii_hk_est_symbol,
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


if __name__ == "__main__":
    unittest.main()
