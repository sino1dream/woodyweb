import unittest
from unittest.mock import patch

from calc.fundcalc import PalmmicroFundCalculator
from cli.fundcli import build_output, main, parse_market_values


class FundCliTest(unittest.TestCase):
    def test_parse_market_values(self):
        self.assertEqual(parse_market_values(["XOP=130.68", "CNY=7.1"]), {"XOP": 130.68, "CNY": 7.1})

    def test_parse_market_values_rejects_bad_items(self):
        with self.assertRaises(ValueError):
            parse_market_values(["XOP"])
        with self.assertRaises(ValueError):
            parse_market_values(["=130.68"])

    def test_build_output_estimates_symbols_and_includes_data(self):
        calculator = PalmmicroFundCalculator(
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

        output = build_output(calculator, "sz159518", {"XOP": 140.0, "CNY": 7.1}, include_data=True)

        self.assertEqual(output["symbols"], ["SZ159518"])
        self.assertAlmostEqual(output["estimate"]["SZ159518"], 1.376)
        self.assertIn("SZ159518", output["data"])

    def test_build_output_without_sources_uses_official_estimate(self):
        calculator = PalmmicroFundCalculator(
            {
                "SZ159518": {
                    "position": "0.8",
                    "calibration": "700",
                    "netvalue": "1.2",
                    "est_netvalue": "130.68",
                    "CNYest": "7.1",
                    "CNY": "7.2",
                    "symbol_hedge": "XOP",
                }
            }
        )

        output = build_output(calculator, "SZ159518")

        self.assertAlmostEqual(output["estimate"]["SZ159518"], 1.3003748571428573)

    def test_build_output_reverse_estimates_peer_value(self):
        calculator = PalmmicroFundCalculator(
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

        output = build_output(calculator, "SZ159518", reverse_values={"SZ159518": 1.376, "CNY": 7.1})

        self.assertAlmostEqual(output["reverse"], 140.0)

    def test_main_check_db_uses_symbol_lookup_only(self):
        class Source:
            @classmethod
            def from_env(cls):
                return cls()

            def get_stock_id(self, symbol):
                return {"SZ159518": "1"}.get(symbol)

        with patch("cli.fundcli.MySqlFundDataSource", Source), patch("builtins.print") as mock_print:
            self.assertEqual(main(["SZ159518", "--check-db"]), 0)

        self.assertIn('"ok": true', mock_print.call_args[0][0])
        self.assertIn('"SZ159518": "1"', mock_print.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
