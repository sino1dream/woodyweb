import unittest

from fundcalc import PalmmicroFundCalculator
from stockdata import DailyRecord, FundProfile, MemoryFundDataSource, MySqlFundDataSource, StockDataArrayBuilder, get_input_symbol_array


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        self.connection.queries.append((sql, params))
        self.rows = self.connection.dispatch(sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.queries = []
        self.stock = {
            "SZ159518": {"id": "1", "symbol": "SZ159518"},
            "XOP": {"id": "2", "symbol": "XOP"},
            "USCNY": {"id": "3", "symbol": "USCNY"},
            "SPY": {"id": "4", "symbol": "SPY"},
        }
        self.stock_by_id = {row["id"]: row for row in self.stock.values()}
        self.tables = {
            "fundposition": {"1": {"close": 0.8}, "4": {"close": 1.0}},
            "lastcalibration": {"2": {"close": 120.0}},
            "fundpair": {"2": {"stock_id": "4"}},
            "calibration": {
                "1": [{"date": "2026-06-10", "close": 700.0}, {"date": "2026-06-09", "close": 690.0}],
                "4": [{"date": "2026-06-10", "close": 1.0}],
            },
            "netvaluehistory": {
                ("1", "2026-06-10"): {"close": 1.2},
                ("2", "2026-06-10"): {"close": 130.68},
                ("4", "2026-06-10"): {"close": 610.0},
            },
            "stockhistory": {
                ("3", "2026-06-10"): {"close": 7.1, "adjclose": 7.1},
                ("2", "2026-06-10"): {"close": 130.68, "adjclose": 130.68},
            },
            "holdings": {},
            "holdingsdate": {},
        }

    def cursor(self):
        return FakeCursor(self)

    def dispatch(self, sql, params):
        if sql.startswith("SELECT id FROM stock WHERE symbol"):
            row = self.stock.get(params[0])
            return [{"id": row["id"]}] if row else []
        if sql.startswith("SELECT symbol FROM stock WHERE id"):
            row = self.stock_by_id.get(str(params[0]))
            return [{"symbol": row["symbol"]}] if row else []
        if sql.startswith("SELECT close FROM fundposition"):
            row = self.tables["fundposition"].get(str(params[0]))
            return [row] if row else []
        if sql.startswith("SELECT close FROM lastcalibration"):
            row = self.tables["lastcalibration"].get(str(params[0]))
            return [row] if row else []
        if sql.startswith("SELECT stock_id FROM fundpair"):
            row = self.tables["fundpair"].get(str(params[0]))
            return [row] if row else []
        if sql.startswith("SELECT date FROM holdingsdate"):
            row = self.tables["holdingsdate"].get(str(params[0]))
            return [row] if row else []
        if sql.startswith("SELECT date, close FROM calibration WHERE stock_id = %s ORDER BY"):
            return self.tables["calibration"].get(str(params[0]), [])[:1]
        if sql.startswith("SELECT date, close FROM calibration WHERE stock_id = %s AND date <= %s"):
            stock_id, date = str(params[0]), params[1]
            return [row for row in self.tables["calibration"].get(stock_id, []) if row["date"] <= date][:1]
        if sql.startswith("SELECT date, close FROM calibration WHERE stock_id = %s ORDER BY date DESC"):
            return self.tables["calibration"].get(str(params[0]), [])
        if sql.startswith("SELECT close FROM calibration"):
            row = next((item for item in self.tables["calibration"].get(str(params[0]), []) if item["date"] == params[1]), None)
            return [{"close": row["close"]}] if row else []
        if sql.startswith("SELECT close FROM netvaluehistory"):
            row = self.tables["netvaluehistory"].get((str(params[0]), params[1]))
            return [row] if row else []
        if sql.startswith("SELECT close FROM stockhistory"):
            row = self.tables["stockhistory"].get((str(params[0]), params[1]))
            return [{"close": row["close"]}] if row else []
        if sql.startswith("SELECT adjclose FROM stockhistory"):
            rows = [
                {"adjclose": row["adjclose"], "date": key[1]}
                for key, row in self.tables["stockhistory"].items()
                if key[0] == str(params[0]) and key[1] <= params[1]
            ]
            rows.sort(key=lambda row: row["date"], reverse=True)
            return rows[:1]
        if sql.startswith("SELECT holding_id, ratio FROM holdings"):
            return self.tables["holdings"].get(str(params[0]), [])
        raise AssertionError(f"Unhandled SQL: {sql!r} {params!r}")


class StockDataArrayBuilderTest(unittest.TestCase):
    def test_input_symbols_match_php_normalization_shape(self):
        self.assertEqual(get_input_symbol_array("sz159518, SH513100\nfx_susdcny"), ["SZ159518", "SH513100", "fx_susdcny"])

    def test_builds_direct_calibrated_fund_data(self):
        source = MemoryFundDataSource(
            stocks={"SZ159518": "1"},
            profiles={
                "SZ159518": FundProfile(
                    symbol="SZ159518",
                    stock_id="1",
                    position=0.8,
                    official_date="2026-06-10",
                    cny_est=7.1,
                    cny_current=7.2,
                    is_lof=True,
                    est_symbol="XOP",
                    est_netvalue=130.68,
                )
            },
            calibration={"1": [DailyRecord("2026-06-10", 700.0)]},
            netvalue={("1", "2026-06-10"): 1.2},
            hedges={("SZ159518", None): 875.0},
        )

        data = StockDataArrayBuilder(source).build("SZ159518")

        self.assertEqual(
            data["SZ159518"],
            {
                "position": "0.8",
                "est_date": "2026-06-10",
                "CNYest": "7.1",
                "CNY": 7.2,
                "calibration": "700.0",
                "date": "2026-06-10",
                "netvalue": "1.2",
                "est_netvalue": "130.68",
                "symbol_hedge": "XOP",
                "hedge": "875.0",
            },
        )
        self.assertAlmostEqual(PalmmicroFundCalculator(data).est_net_value("SZ159518"), 1.3003748571428573)

    def test_builds_leverage_intermediate_entries(self):
        source = MemoryFundDataSource(
            stocks={"SZ161125": "1", "SPY": "2", "^GSPC": "3", "hf_ES": "4"},
            profiles={
                "SZ161125": FundProfile(
                    symbol="SZ161125",
                    stock_id="1",
                    position=1.0,
                    official_date="2026-06-10",
                    est_symbol="^GSPC",
                    est_netvalue=6200.0,
                )
            },
            positions={"1": 1.0, "2": 1.0, "3": 1.0},
            calibration={
                "1": [DailyRecord("2026-06-10", 5000.0)],
                "2": [DailyRecord("2026-06-10", 1.0)],
                "3": [DailyRecord("2026-06-10", 1.0)],
            },
            netvalue={
                ("1", "2026-06-10"): 1.2,
                ("2", "2026-06-10"): 610.0,
            },
            last_calibration={"3": 6000.0},
            fund_pairs={"3": "4"},
            hedges={("SZ161125", None): 5000.0},
        )

        data = StockDataArrayBuilder(source).build("SZ161125")

        self.assertEqual(data["SZ161125"]["symbol_hedge"], "SPY")
        self.assertEqual(data["SPY"]["symbol_hedge"], "^GSPC")
        self.assertEqual(data["^GSPC"]["symbol_hedge"], "hf_ES")
        self.assertAlmostEqual(PalmmicroFundCalculator(data).est_net_value("SZ161125", {"hf_ES": 6200.0}), 1.24)

    def test_builds_holdings_fund_data_and_pair_helpers(self):
        source = MemoryFundDataSource(
            stocks={"SZ160723": "1", "AAA": "2", "BBB": "3", "PAIR": "4"},
            profiles={
                "SZ160723": FundProfile(
                    symbol="SZ160723",
                    stock_id="1",
                    position=0.9,
                    official_date="2026-06-10",
                    cny_est=7.0,
                    cny_current=7.2,
                    is_lof=True,
                    holdings_date="2026-06-09",
                    holdings_netvalue=1.5,
                )
            },
            positions={"2": 1.0},
            calibration={"2": [DailyRecord("2026-06-10", 2.0)]},
            last_calibration={"2": 10.0},
            fund_pairs={"2": "4"},
            holdings={"1": {"2": 60.0, "3": 40.0}},
            history_close={("2", "2026-06-09"): 10.0, ("3", "2026-06-09"): 20.0},
            history_adjclose={("2", "2026-06-10"): 11.0, ("3", "2026-06-10"): 19.0},
        )

        data = StockDataArrayBuilder(source).build("SZ160723")

        self.assertEqual(data["SZ160723"]["date"], "2026-06-09")
        self.assertEqual(data["SZ160723"]["CNY"], 7.2)
        self.assertEqual(data["SZ160723"]["CNYholdings"], "7.0")
        self.assertEqual(data["SZ160723"]["symbol_hedge"]["AAA"]["ratio"], "60.0")
        self.assertEqual(data["SZ160723"]["symbol_hedge"]["BBB"]["est_price"], "19.0")
        self.assertEqual(data["AAA"]["symbol_hedge"], "PAIR")
        self.assertAlmostEqual(PalmmicroFundCalculator(data).est_net_value("SZ160723"), 1.5941142857142858)

    def test_symbol_range_filters_input_like_php_array_intersect(self):
        source = MemoryFundDataSource(stocks={}, profiles={})

        data = StockDataArrayBuilder(source).build("SZ159518 SZ160723", {"SZ160723"})

        self.assertEqual(list(data.keys()), ["SZ160723"])


class MySqlFundDataSourceTest(unittest.TestCase):
    def test_reads_legacy_tables_and_builds_calibrated_stock_data(self):
        source = MySqlFundDataSource(FakeConnection())

        data = StockDataArrayBuilder(source).build("SZ159518")

        self.assertEqual(data["SZ159518"]["position"], "0.8")
        self.assertEqual(data["SZ159518"]["calibration"], "700.0")
        self.assertEqual(data["SZ159518"]["netvalue"], "1.2")
        self.assertEqual(data["SZ159518"]["CNYest"], "7.1")
        self.assertEqual(data["SZ159518"]["symbol_hedge"], "XOP")

    def test_reads_pair_and_hedge_helpers(self):
        source = MySqlFundDataSource(FakeConnection())

        self.assertEqual(source.get_fund_pair_id("2"), "4")
        self.assertEqual(source.get_stock_symbol("4"), "SPY")
        self.assertAlmostEqual(source.get_stock_hedge("SZ159518", "1"), 875.0)


if __name__ == "__main__":
    unittest.main()
