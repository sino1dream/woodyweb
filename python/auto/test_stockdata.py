import unittest
import tempfile
from pathlib import Path

from calc.fundcalc import PalmmicroFundCalculator
from core.stockdata import (
    DailyRecord,
    FundProfile,
    MemoryFundDataSource,
    MySqlFundDataSource,
    StockDataArrayBuilder,
    build_fund_calculator,
    get_input_symbol_array,
    load_mysql_env,
    mysql_connection_error_message,
    mysql_config_from_env,
    parse_mysql_env_file,
    stock_get_symbol,
)


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
        # 真实表名是 calibrationhistory（PHP CalibrationSql 的定义），
        # 调用方传的短名 "calibration" 会在 MySqlFundDataSource._daily_table 里解析。
        # 这里按真实表名匹配，同时兼容旧写法。
        if sql.startswith("SELECT date, close FROM calibrationhistory WHERE stock_id = %s AND date <= %s"):
            stock_id, date = str(params[0]), params[1]
            return [row for row in self.tables["calibration"].get(stock_id, []) if row["date"] <= date][:1]
        if sql.startswith("SELECT date, close FROM calibrationhistory WHERE stock_id = %s ORDER BY"):
            return self.tables["calibration"].get(str(params[0]), [])[:1]
        if sql.startswith("SELECT date, close FROM calibrationhistory"):
            return self.tables["calibration"].get(str(params[0]), [])[:1]
        if sql.startswith("SELECT close FROM calibrationhistory"):
            row = next((item for item in self.tables["calibration"].get(str(params[0]), []) if item["date"] == params[1]), None)
            return [{"close": row["close"]}] if row else []
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
        if sql.startswith("SELECT close, quote_date FROM estquote"):
            # 中间层估值表（Python 侧新增）。替身里没有时返回空，等价于"未抓过
            # 中间层行情"，此时 get_est_quote 返回 None、构建器跳过 est_quote。
            row = self.tables.get("estquote", {}).get(str(params[0]))
            return [{"close": row["close"], "quote_date": row["date"]}] if row else []
        # 汇率回退用的 as-of 查询（该日期或之前最近一天）
        if sql.startswith("SELECT date, close FROM netvaluehistory WHERE stock_id = %s AND date <= %s"):
            stock_id, date = str(params[0]), params[1]
            rows = [
                {"date": key[1], "close": value}
                for key, value in self.tables["netvaluehistory"].items()
                if key[0] == stock_id and key[1] <= date
            ]
            rows.sort(key=lambda item: item["date"], reverse=True)
            return rows[:1]
        if sql.startswith("SELECT date, close FROM stockhistory WHERE stock_id = %s AND date <= %s"):
            stock_id, date = str(params[0]), params[1]
            rows = [
                {"date": key[1], "close": value}
                for key, value in self.tables["stockhistory"].items()
                if key[0] == stock_id and key[1] <= date
            ]
            rows.sort(key=lambda item: item["date"], reverse=True)
            return rows[:1]
        if sql.startswith("SELECT close, date FROM netvaluehistory"):
            row = self.tables["netvaluehistory"].get((str(params[0]), params[1]))
            return [{"close": row["close"], "date": params[1]}] if row else []
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
        self.assertEqual(get_input_symbol_array("sz159518" + chr(0xFF0C) + "sh513100" + chr(0x3001) + "qqq"), ["SZ159518", "SH513100", "QQQ"])

    def test_bare_digits_get_a_market_prefix(self):
        # StockGetSymbol() runs the Chinese fund/stock builders before upper-casing.
        self.assertEqual(get_input_symbol_array("162411 513180"), ["SZ162411", "SH513180"])
        self.assertEqual(get_input_symbol_array("600000"), ["SH600000"])
        self.assertEqual(stock_get_symbol("162411"), "SZ162411")

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

    def test_build_fund_calculator_from_source(self):
        source = MemoryFundDataSource(
            stocks={"SZ159518": "1"},
            profiles={
                "SZ159518": FundProfile(
                    symbol="SZ159518",
                    stock_id="1",
                    position=0.8,
                    official_date="2026-06-10",
                    cny_est=7.1,
                    est_symbol="XOP",
                    est_netvalue=130.68,
                )
            },
            calibration={"1": [DailyRecord("2026-06-10", 700.0)]},
            netvalue={("1", "2026-06-10"): 1.2},
        )

        calculator = build_fund_calculator(source, "SZ159518")

        self.assertAlmostEqual(calculator.est_net_value("SZ159518"), 1.3003748571428573)

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
    def test_parse_mysql_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            path.write_text(
                "\n".join(
                    [
                        "# local secrets",
                        "PALMMICRO_DB_HOST=127.0.0.1",
                        "PALMMICRO_DB_PASSWORD='secret'",
                        "OTHER=value",
                    ]
                ),
                encoding="utf-8",
            )

            values = parse_mysql_env_file(path)

        self.assertEqual(values, {"PALMMICRO_DB_HOST": "127.0.0.1", "PALMMICRO_DB_PASSWORD": "secret"})

    def test_load_mysql_env_prefers_process_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            path.write_text("PALMMICRO_DB_USER=file_user\nPALMMICRO_DB_PASSWORD=file_secret\n", encoding="utf-8")

            values = load_mysql_env({"PALMMICRO_DB_PASSWORD": "process_secret"}, path)

        self.assertEqual(values["PALMMICRO_DB_USER"], "file_user")
        self.assertEqual(values["PALMMICRO_DB_PASSWORD"], "process_secret")

    def test_mysql_config_from_env_defaults_legacy_values(self):
        config = mysql_config_from_env({"PALMMICRO_DB_PASSWORD": "secret"})

        self.assertEqual(config["host"], "mysql")
        self.assertEqual(config["port"], 3306)
        self.assertEqual(config["user"], "n5gl0n39mnyn183l_woody")
        self.assertEqual(config["password"], "secret")
        self.assertEqual(config["database"], "n5gl0n39mnyn183l_camman")

    def test_mysql_config_from_env_accepts_local_host_and_port(self):
        config = mysql_config_from_env(
            {
                "PALMMICRO_DB_HOST": "127.0.0.1",
                "PALMMICRO_DB_PORT": "3307",
                "PALMMICRO_DB_USER": "woody",
                "PALMMICRO_DB_DATABASE": "camman",
                "PALMMICRO_DB_PASSWORD": "secret",
            }
        )

        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["port"], 3307)
        self.assertEqual(config["user"], "woody")
        self.assertEqual(config["database"], "camman")

    def test_mysql_config_from_env_requires_password_and_integer_port(self):
        with self.assertRaises(RuntimeError):
            mysql_config_from_env({})
        with self.assertRaises(RuntimeError):
            mysql_config_from_env({"PALMMICRO_DB_PASSWORD": "secret", "PALMMICRO_DB_PORT": "abc"})
        with self.assertRaises(RuntimeError):
            mysql_config_from_env({"PALMMICRO_DB_USER": "<root>", "PALMMICRO_DB_PASSWORD": "secret"})

    def test_mysql_connection_error_message_has_specific_hints(self):
        config = mysql_config_from_env({"PALMMICRO_DB_PASSWORD": "secret"})

        self.assertIn("rejected the login", mysql_connection_error_message(config, Exception(1045, "Access denied")))
        self.assertIn("database does not exist", mysql_connection_error_message(config, Exception(1049, "Unknown database")))
        self.assertIn("TCP connection failed", mysql_connection_error_message(config, Exception(2003, "Can't connect")))

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


class PositionDefaultTest(unittest.TestCase):
    """仓位默认值的回归测试。

    背景（真实 bug）：``get_position()`` 曾把默认值硬编码成 ``1.0``，而 PHP 是
    ``MysqlReference::__construct()`` 回落到 ``StockSymbol::GetDefaultPosition()``，
    即 ``IsLofA() ? 0.95 : 1.0``（``php/stock/stocksymbol.php:1001``）。

    后果：库里没有 ``fundposition`` 行的 LOF 基金会算成 1.0 —— 实测 SZ162411、
    SH501018 都受影响；SZ161125 则因为种子数据显式写了 1.0 而中招。
    PHP 官网 ``sz161125cn.php`` 明写"仓位估算值使用0.95"。
    """

    def test_memory_source_falls_back_to_symbol_default(self):
        from core.stockdata import MemoryFundDataSource

        source = MemoryFundDataSource(
            stocks={"SZ162411": "1", "SH513350": "2", "QQQ": "3"},
            # 故意不给 positions，触发回落到符号默认仓位
        )

        self.assertAlmostEqual(source.get_position("1"), 0.95)   # LOF
        self.assertAlmostEqual(source.get_position("2"), 1.0)    # ETF
        self.assertAlmostEqual(source.get_position("3"), 1.0)    # 美股标的

    def test_explicit_position_wins(self):
        from core.stockdata import MemoryFundDataSource

        source = MemoryFundDataSource(
            stocks={"SZ161125": "1"},
            positions={"1": 0.98},
        )
        self.assertAlmostEqual(source.get_position("1"), 0.98)

    def test_defaults_match_php_get_default_position(self):
        from core.stocksymbol import StockSymbol

        # 与 php/stock/stocksymbol.php:1001 对齐：LOF 0.95，其余 1.0
        for symbol in ("SZ161130", "SZ161125", "SZ162411", "SH501018", "SH501312"):
            self.assertAlmostEqual(StockSymbol(symbol).get_default_position(), 0.95, msg=symbol)
        for symbol in ("SH513100", "SH513350", "SH600000"):
            self.assertAlmostEqual(StockSymbol(symbol).get_default_position(), 1.0, msg=symbol)


if __name__ == "__main__":
    unittest.main()
