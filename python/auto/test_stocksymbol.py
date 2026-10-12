import datetime
import unittest

from core.stocksymbol import (
    BJ_PREFIX,
    SH_PREFIX,
    SINA_CN_FUTURE_PREFIX,
    SINA_FOREX_PREFIX,
    SINA_FUND_PREFIX,
    SINA_HK_PREFIX,
    SINA_INDEX_PREFIX,
    SINA_US_PREFIX,
    SZ_PREFIX,
    StockSymbol,
    build_china_fund_symbol,
    build_china_stock_symbol,
    build_hongkong_stock_symbol,
    build_yahoo_net_value_symbol,
    china_future_get_symbol_array,
    china_index_get_symbol_array,
    get_all_symbol_array,
    get_hk_mix_symbol_array,
    get_lof_mix_symbol_array,
    get_msci_us50_symbol_array,
    get_over_night_symbol_array,
    get_qdii_gold_symbol_array,
    get_qdii_oil_symbol_array,
    get_secondary_listing_array,
    in_array_all,
    in_array_china_future,
    in_array_china_index,
    in_array_lof_mix,
    in_array_qdii,
    in_array_qdii_hk,
    is_china_stock_digit,
    qdii_get_spy_match_array,
    qdii_get_symbol_array,
    qdii_hk_get_index_symbol_array,
    qdii_hk_get_symbol_array,
    stock_get_symbol,
    str_has_prefix,
)


class StrHasPrefixTest(unittest.TestCase):
    def test_returns_remainder_on_match(self):
        self.assertEqual(str_has_prefix("fx_susdcnh", SINA_FOREX_PREFIX), "usdcnh")
        self.assertEqual(str_has_prefix("hf_ES", "hf_"), "ES")

    def test_returns_none_without_match(self):
        self.assertIsNone(str_has_prefix("AAPL", SINA_FOREX_PREFIX))
        self.assertIsNone(str_has_prefix(None, "hf_"))

    def test_empty_remainder_is_falsy_like_php(self):
        # PHP strncmp() matched, so IsSinaForex() would get '' and treat it as false.
        self.assertEqual(str_has_prefix("fx_s", SINA_FOREX_PREFIX), "")
        self.assertFalse(StockSymbol("fx_s").is_sina_forex())


class SymbolArrayTest(unittest.TestCase):
    def test_secondary_listing_array(self):
        mapping = get_secondary_listing_array()
        self.assertEqual(mapping["BABA"], "09988")
        self.assertEqual(mapping["TCOM"], "09961")
        self.assertEqual(len(mapping), 6)

    def test_qdii_group_merges_match_arrays(self):
        symbols = qdii_get_symbol_array()
        self.assertIn("SZ162411", symbols)
        self.assertIn("SH513300", symbols)
        self.assertIn("SZ159655", symbols)
        self.assertEqual(len(symbols), len(set(symbols)))

    def test_spy_match_is_subset_of_spy_group(self):
        for symbol in qdii_get_spy_match_array():
            self.assertTrue(in_array_qdii(symbol))

    def test_all_symbol_array_contains_every_group(self):
        symbols = get_all_symbol_array()
        for group in (
            qdii_get_symbol_array(),
            qdii_hk_get_symbol_array(),
            china_index_get_symbol_array(),
            china_future_get_symbol_array(),
        ):
            for symbol in group:
                self.assertIn(symbol, symbols)
        self.assertTrue(in_array_all("SH513180"))
        self.assertFalse(in_array_all("AAPL"))

    def test_hk_index_array(self):
        self.assertEqual(qdii_hk_get_index_symbol_array(), ["^HSI", "^HSCE", "^HSTECH"])

    def test_lof_mix_includes_oil_and_gold(self):
        symbols = get_lof_mix_symbol_array()
        for symbol in get_qdii_oil_symbol_array() + get_qdii_gold_symbol_array():
            self.assertIn(symbol, symbols)
        self.assertTrue(in_array_lof_mix("SZ160644"))
        self.assertFalse(in_array_lof_mix("AAPL"))

    def test_mix_arrays_are_disjoint_enough_to_be_useful(self):
        self.assertTrue(set(get_hk_mix_symbol_array()).isdisjoint(get_msci_us50_symbol_array()))

    def test_over_night_symbol_array(self):
        symbols = get_over_night_symbol_array()
        self.assertIn("SZ162719", symbols)
        self.assertIn("SZ161226", symbols)
        self.assertIn("SZ159502", symbols)

    def test_membership_helpers(self):
        self.assertTrue(in_array_qdii("SZ162411"))
        self.assertTrue(in_array_qdii_hk("SH513180"))
        self.assertTrue(in_array_china_index("SH510300"))
        self.assertTrue(in_array_china_future("SH518880"))
        self.assertFalse(in_array_china_index("SZ162411"))


class DigitHelperTest(unittest.TestCase):
    def test_is_china_stock_digit(self):
        self.assertEqual(is_china_stock_digit("162411"), "162411")
        self.assertIsNone(is_china_stock_digit("1624"))
        self.assertIsNone(is_china_stock_digit("1624111"))
        self.assertIsNone(is_china_stock_digit("16241X"))
        self.assertIsNone(is_china_stock_digit(" 16241"))
        self.assertIsNone(is_china_stock_digit(162411))

    def test_build_china_fund_symbol(self):
        self.assertEqual(build_china_fund_symbol("162411"), SZ_PREFIX + "162411")
        self.assertEqual(build_china_fund_symbol("513180"), SH_PREFIX + "513180")
        self.assertEqual(build_china_fund_symbol("501043"), SH_PREFIX + "501043")
        self.assertIsNone(build_china_fund_symbol("600000"))
        self.assertIsNone(build_china_fund_symbol("000001"))
        self.assertIsNone(build_china_fund_symbol("16241"))

    def test_build_china_stock_symbol_market_split(self):
        # Shenzhen: below 100000, and 200000..399999
        self.assertEqual(build_china_stock_symbol("000001"), SZ_PREFIX + "000001")
        self.assertEqual(build_china_stock_symbol("300750"), SZ_PREFIX + "300750")
        # Beijing: 400000..499999 and 800000..899999
        self.assertEqual(build_china_stock_symbol("430047"), BJ_PREFIX + "430047")
        self.assertEqual(build_china_stock_symbol("830799"), BJ_PREFIX + "830799")
        # Shanghai: 600000 and above (the PHP test is a bare `>= 600000`, so the
        # 700000..799999 gap also resolves to Shanghai)
        self.assertEqual(build_china_stock_symbol("600000"), SH_PREFIX + "600000")
        self.assertEqual(build_china_stock_symbol("688981"), SH_PREFIX + "688981")
        self.assertEqual(build_china_stock_symbol("700000"), SH_PREFIX + "700000")
        # 900000..999999 are Shanghai B shares, still matched by `>= 600000`
        self.assertEqual(build_china_stock_symbol("900000"), SH_PREFIX + "900000")
        # Unassigned gaps keep returning PHP false
        self.assertIsNone(build_china_stock_symbol("500000"))

    def test_build_hongkong_stock_symbol(self):
        self.assertEqual(build_hongkong_stock_symbol("700"), "00700")
        self.assertEqual(build_hongkong_stock_symbol("09988"), "09988")
        self.assertIsNone(build_hongkong_stock_symbol(None))

    def test_build_yahoo_net_value_symbol(self):
        self.assertEqual(build_yahoo_net_value_symbol("SZ162411"), "^SZ162411-IV")
        self.assertEqual(build_yahoo_net_value_symbol("SZ162411", "NV"), "^SZ162411-NV")
        self.assertIsNone(build_yahoo_net_value_symbol(""))


class StockGetSymbolTest(unittest.TestCase):
    def test_digit_input_gets_market_prefix(self):
        self.assertEqual(stock_get_symbol("162411"), "SZ162411")
        self.assertEqual(stock_get_symbol("513180"), "SH513180")
        self.assertEqual(stock_get_symbol(" 600000 "), "SH600000")

    def test_other_symbols_are_upper_cased(self):
        self.assertEqual(stock_get_symbol("aapl"), "AAPL")
        self.assertEqual(stock_get_symbol("^hsi"), "^HSI")

    def test_underscore_symbols_keep_their_case(self):
        self.assertEqual(stock_get_symbol("fx_susdcnh"), "fx_susdcnh")
        self.assertEqual(stock_get_symbol("znb_NKY"), "znb_NKY")


class MarketClassificationTest(unittest.TestCase):
    def test_china_a_share(self):
        sym = StockSymbol("SZ162411")
        self.assertEqual(sym.is_symbol_a(), "162411")
        self.assertEqual(sym.get_digit_a(), "162411")
        self.assertFalse(sym.is_symbol_h())
        self.assertFalse(sym.is_symbol_us())
        self.assertTrue(sym.is_shenzhen_a())
        self.assertFalse(sym.is_shanghai_a())
        self.assertTrue(sym.is_lof_a())
        self.assertFalse(sym.is_etf_a())
        self.assertEqual(sym.is_fund_a(), "162411")
        self.assertFalse(sym.is_index())

    def test_lower_case_prefix_is_accepted(self):
        sym = StockSymbol("sz162411")
        self.assertEqual(sym.is_symbol_a(), "162411")
        self.assertTrue(sym.is_shenzhen_a())

    def test_shanghai_etf_and_star(self):
        self.assertTrue(StockSymbol("SH513350").is_etf_a())
        self.assertFalse(StockSymbol("SH513350").is_lof_a())
        self.assertTrue(StockSymbol("SH510300").is_etf_a())
        # 588000 is a STAR-market ETF on paper, but PHP only treats
        # 510000..569999 as a Shanghai ETF, so it is not an ETF there either.
        self.assertFalse(StockSymbol("SH588000").is_etf_a())
        self.assertTrue(StockSymbol("SH688981").is_shanghai_star())
        self.assertFalse(StockSymbol("SH600000").is_shanghai_star())

    def test_shenzhen_gem_and_b_share(self):
        self.assertTrue(StockSymbol("SZ300750").is_shenzhen_gem())
        self.assertFalse(StockSymbol("SZ000001").is_shenzhen_gem())
        self.assertEqual(StockSymbol("SZ200011").is_shenzhen_b(), "200011")
        self.assertEqual(StockSymbol("SH900901").is_shanghai_b(), "900901")
        self.assertTrue(StockSymbol("SH900901").is_stock_b())
        self.assertFalse(StockSymbol("SH600000").is_stock_b())

    def test_beijing_share(self):
        sym = StockSymbol("BJ430047")
        self.assertEqual(sym.is_symbol_a(), "430047")
        self.assertTrue(sym.is_beijing_a())
        self.assertTrue(sym.is_tradable())
        self.assertFalse(sym.is_index_a())

    def test_index_a(self):
        self.assertEqual(StockSymbol("SZ399001").is_index_a(), "399001")
        self.assertEqual(StockSymbol("SH000300").is_index_a(), "000300")
        self.assertFalse(StockSymbol("SZ162411").is_index_a())

    def test_hongkong_stock(self):
        sym = StockSymbol("00700")
        self.assertTrue(sym.is_symbol_h())
        self.assertFalse(sym.is_symbol_a())
        self.assertFalse(sym.is_symbol_us())

    def test_hongkong_index_symbols(self):
        for symbol in ("^HSI", "^HSCE", "^HSTECH"):
            sym = StockSymbol(symbol)
            self.assertTrue(sym.is_index())
            self.assertTrue(sym.is_symbol_h())
            self.assertFalse(sym.is_symbol_us())

    def test_unknown_index_is_not_hongkong(self):
        sym = StockSymbol("^GSPC")
        self.assertTrue(sym.is_index())
        self.assertFalse(sym.is_symbol_h())
        self.assertTrue(sym.is_symbol_us())

    def test_us_stock(self):
        sym = StockSymbol("XOP")
        self.assertTrue(sym.is_symbol_us())
        self.assertFalse(sym.is_symbol_a())
        self.assertFalse(sym.is_symbol_h())


class SinaFamilyTest(unittest.TestCase):
    def test_sina_fund(self):
        self.assertEqual(StockSymbol("f_240019").is_sina_fund(), "240019")
        self.assertFalse(StockSymbol("f_24001").is_sina_fund())
        self.assertFalse(StockSymbol("SZ162411").is_sina_fund())

    def test_sina_global_index(self):
        self.assertEqual(StockSymbol("znb_NKY").is_sina_global_index(), "NKY")
        self.assertFalse(StockSymbol("AAPL").is_sina_global_index())

    def test_sina_future(self):
        sym = StockSymbol("hf_ES")
        self.assertEqual(sym.is_sina_future_us(), "ES")
        self.assertFalse(sym.is_sina_future_cn())
        self.assertTrue(sym.is_sina_future())

        cn = StockSymbol("nf_AU0")
        self.assertEqual(cn.is_sina_future_cn(), "AU0")
        self.assertTrue(cn.is_sina_future())
        self.assertFalse(cn.is_sina_future_except_gold_cn())

        cn2 = StockSymbol("nf_AG0")
        self.assertTrue(cn2.is_sina_future_except_gold_cn())

    def test_forex(self):
        self.assertTrue(StockSymbol("USCNY").is_east_money_forex())
        self.assertTrue(StockSymbol("EUCNY").is_forex())
        self.assertEqual(StockSymbol("fx_susdcnh").is_sina_forex(), "USDCNH")
        self.assertTrue(StockSymbol("fx_susdcnh").is_forex())
        self.assertFalse(StockSymbol("AAPL").is_forex())

    def test_yahoo_net_value(self):
        self.assertEqual(StockSymbol("^SZ162411-IV").is_yahoo_net_value(), "SZ162411")
        self.assertFalse(StockSymbol("^HSI").is_yahoo_net_value())
        self.assertFalse(StockSymbol("SZ162411").is_yahoo_net_value())


class SymbolConversionTest(unittest.TestCase):
    def test_get_sina_symbol_china_a(self):
        self.assertEqual(StockSymbol("SZ162411").get_sina_symbol(), "sz162411")
        self.assertEqual(StockSymbol("SH600000").get_sina_symbol(), "sh600000")

    def test_get_sina_symbol_us(self):
        self.assertEqual(StockSymbol("XOP").get_sina_symbol(), "gb_xop")

    def test_get_sina_symbol_global_index_passthrough(self):
        self.assertEqual(StockSymbol("znb_NKY").get_sina_symbol(), "znb_NKY")

    def test_get_sina_symbol_hongkong_index(self):
        self.assertEqual(StockSymbol("^HSI").get_sina_symbol(), "rt_hkHSI")
        self.assertEqual(StockSymbol("^HSCE").get_sina_symbol(), "rt_hkHSCEI")
        self.assertEqual(StockSymbol("^HSTECH").get_sina_symbol(), "rt_hkHSTECH")

    def test_get_sina_symbol_us_index(self):
        self.assertEqual(StockSymbol("^DJI").get_sina_symbol(), "gb_dji")
        self.assertEqual(StockSymbol("^GSPC").get_sina_symbol(), "gb_inx")
        self.assertEqual(StockSymbol("^NDX").get_sina_symbol(), "gb_ndx")

    def test_get_sina_symbol_unknown_index_returns_false(self):
        self.assertFalse(StockSymbol("^FTSE").get_sina_symbol())

    def test_get_sina_symbol_hongkong_stock(self):
        self.assertEqual(StockSymbol("00700").get_sina_symbol(), "rt_hk00700")

    def test_get_sina_symbol_dollar_substitution(self):
        # PHP replaces '.' with '$' before lower-casing
        self.assertEqual(StockSymbol("BRK.B").get_sina_symbol(), "gb_brk$b")

    def test_get_sina_fund_symbol(self):
        self.assertEqual(StockSymbol("SZ162411").get_sina_fund_symbol(), "f_162411")
        self.assertEqual(StockSymbol("SH513350").get_sina_fund_symbol(), "f_513350")
        self.assertFalse(StockSymbol("SH600000").get_sina_fund_symbol())

    def test_get_yahoo_symbol_china(self):
        self.assertEqual(StockSymbol("SH600000").get_yahoo_symbol(), "600000.ss")
        self.assertEqual(StockSymbol("SZ162411").get_yahoo_symbol(), "162411.sz")
        self.assertEqual(StockSymbol("BJ430047").get_yahoo_symbol(), "430047.bj")

    def test_get_yahoo_symbol_index(self):
        self.assertEqual(StockSymbol("^HSI").get_yahoo_symbol(), "%5EHSI")
        self.assertEqual(StockSymbol("^HSTECH").get_yahoo_symbol(), "HSTECH.hk")

    def test_get_yahoo_symbol_hongkong_stock(self):
        self.assertEqual(StockSymbol("00700").get_yahoo_symbol(), "0700.hk")

    def test_get_yahoo_symbol_futures_and_forex(self):
        self.assertEqual(StockSymbol("hf_CL").get_yahoo_symbol(), "CL%3DF")
        self.assertEqual(StockSymbol("hf_CHA50CFD").get_yahoo_symbol(), "XIN9.FGI")
        self.assertEqual(StockSymbol("fx_sUSDCNH").get_yahoo_symbol(), "CNH%3DF")

    def test_get_yahoo_symbol_global_index_mapping(self):
        self.assertEqual(StockSymbol("znb_CAC").get_yahoo_symbol(), "%5EFCHI")
        self.assertEqual(StockSymbol("znb_DAX").get_yahoo_symbol(), "%5EGDAXI")
        self.assertEqual(StockSymbol("znb_NKY").get_yahoo_symbol(), "%5EN225")
        self.assertEqual(StockSymbol("znb_SENSEX").get_yahoo_symbol(), "%5EBSESN")

    def test_get_yahoo_symbol_passthrough_for_us_stock(self):
        self.assertEqual(StockSymbol("XOP").get_yahoo_symbol(), "XOP")


class DisplayAndPrecisionTest(unittest.TestCase):
    def test_precision(self):
        self.assertEqual(StockSymbol("SZ162411").get_precision(), 3)
        self.assertEqual(StockSymbol("f_240019").get_precision(), 3)
        self.assertEqual(StockSymbol("SH900901").get_precision(), 3)
        self.assertEqual(StockSymbol("USCNY").get_precision(), 4)
        self.assertEqual(StockSymbol("fx_susdcnh").get_precision(), 4)
        self.assertEqual(StockSymbol("XOP").get_precision(), 2)
        self.assertEqual(StockSymbol("SH600000").get_precision(), 2)

    def test_default_position(self):
        self.assertEqual(StockSymbol("SZ162411").get_default_position(), 0.95)
        self.assertEqual(StockSymbol("SH513350").get_default_position(), 1.0)
        self.assertEqual(StockSymbol("SH600000").get_default_position(), 1.0)

    def test_tradable(self):
        self.assertFalse(StockSymbol("znb_NKY").is_tradable())
        self.assertFalse(StockSymbol("^HSI").is_tradable())
        self.assertFalse(StockSymbol("SH000300").is_tradable())
        self.assertTrue(StockSymbol("SH600000").is_tradable())
        self.assertTrue(StockSymbol("00700").is_tradable())
        self.assertTrue(StockSymbol("XOP").is_tradable())

    def test_display(self):
        self.assertEqual(StockSymbol("hf_ES").get_display(), "ES")
        self.assertEqual(StockSymbol("nf_AU0").get_display(), "AU0")
        self.assertEqual(StockSymbol("fx_sUSDCNH").get_display(), "USDCNH")
        self.assertEqual(StockSymbol("znb_NKY").get_display(), "NKY")
        self.assertEqual(StockSymbol("SZ162411").get_display(), "SZ162411")
        self.assertEqual(StockSymbol("^HSI").get_display(), "^HSI")

    def test_get_symbol(self):
        self.assertEqual(StockSymbol("SZ162411").get_symbol(), "SZ162411")


class MarketHoursTest(unittest.TestCase):
    def test_china_a_share_before_and_after(self):
        sym = StockSymbol("SZ162411")
        self.assertTrue(sym.is_before_stock_market(900))
        self.assertFalse(sym.is_before_stock_market(915))
        self.assertFalse(sym.is_after_stock_market(1505))
        self.assertTrue(sym.is_after_stock_market(1506))

    def test_star_and_gem_keep_trading_later_than_main_board(self):
        # PHP structure: `> 1505` enters the branch, then STAR/GEM wait for
        # `> 1535` while every other A share returns true immediately.
        star = StockSymbol("SH688981")
        gem = StockSymbol("SZ300750")
        main = StockSymbol("SH600000")
        for sym in (star, gem):
            self.assertFalse(sym.is_after_stock_market(1505))
            self.assertFalse(sym.is_after_stock_market(1506))
            self.assertFalse(sym.is_after_stock_market(1535))
            self.assertTrue(sym.is_after_stock_market(1536))
        self.assertFalse(main.is_after_stock_market(1505))
        self.assertTrue(main.is_after_stock_market(1506))

    def test_hongkong_hours(self):
        sym = StockSymbol("00700")
        self.assertTrue(sym.is_before_stock_market(859))
        self.assertFalse(sym.is_before_stock_market(900))
        self.assertFalse(sym.is_after_stock_market(1615))
        self.assertTrue(sym.is_after_stock_market(1616))

    def test_us_hours(self):
        sym = StockSymbol("XOP")
        self.assertTrue(sym.is_before_stock_market(359))
        self.assertFalse(sym.is_before_stock_market(400))
        self.assertTrue(sym.is_after_stock_market(2006))

    def test_global_index_only_names_five_markets(self):
        # Only the five named indices have limits. The A/H/US fallbacks sit
        # inside the `znb_` branch, so every other global index (and every
        # other A/H/US symbol) reports "not before / not after" and therefore
        # counts as trading whenever the day is a weekday.
        self.assertTrue(StockSymbol("znb_NKY").is_before_stock_market(100))
        self.assertFalse(StockSymbol("znb_UKX").is_before_stock_market(100))
        self.assertFalse(StockSymbol("znb_UKX").is_after_stock_market(2359))
        self.assertFalse(StockSymbol("znb_UKX").is_before_stock_market(2359))

    def test_global_index_limits(self):
        for symbol in ("CAC", "DAX", "NKY", "TPX", "SENSEX"):
            self.assertTrue(StockSymbol("znb_" + symbol).is_before_stock_market(800))
            self.assertFalse(StockSymbol("znb_" + symbol).is_before_stock_market(900))
        self.assertTrue(StockSymbol("znb_DAX").is_after_stock_market(1806))
        self.assertFalse(StockSymbol("znb_DAX").is_after_stock_market(1805))
        self.assertTrue(StockSymbol("znb_NKY").is_after_stock_market(1536))
        self.assertTrue(StockSymbol("znb_SENSEX").is_after_stock_market(1606))

    def test_trading_on_weekend(self):
        saturday = datetime.datetime(2026, 1, 3, 10, 0)
        self.assertEqual(saturday.weekday(), 5)
        self.assertFalse(StockSymbol("SH600000").is_stock_market_trading(saturday))

    def test_trading_on_weekday_with_noon_break(self):
        sym = StockSymbol("SH600000")
        # 10:00 -> open
        self.assertTrue(sym.is_stock_market_trading(datetime.datetime(2026, 1, 5, 10, 0)))
        # 12:00 -> Chinese noon break
        self.assertFalse(sym.is_stock_market_trading(datetime.datetime(2026, 1, 5, 12, 0)))
        # 11:40 boundary keeps trading, 11:41 stops
        self.assertTrue(sym.is_stock_market_trading(datetime.datetime(2026, 1, 5, 11, 40)))
        self.assertFalse(sym.is_stock_market_trading(datetime.datetime(2026, 1, 5, 11, 41)))
        # 15:05 is still trading, 15:06 is after the close
        self.assertTrue(sym.is_stock_market_trading(datetime.datetime(2026, 1, 5, 15, 5)))
        self.assertFalse(sym.is_stock_market_trading(datetime.datetime(2026, 1, 5, 15, 6)))

    def test_noon_break_can_be_disabled(self):
        sym = StockSymbol("SH600000")
        noon = datetime.datetime(2026, 1, 5, 12, 0)
        self.assertFalse(sym.is_stock_market_trading(noon))
        self.assertTrue(sym.is_stock_market_trading(noon, False))

    def test_hongkong_noon_break_skips_indices(self):
        noon = datetime.datetime(2026, 1, 5, 12, 30)
        self.assertFalse(StockSymbol("00700").is_stock_market_trading(noon))
        # ^HSI and ^HSCE keep trading through the Hongkong lunch break
        self.assertTrue(StockSymbol("^HSI").is_stock_market_trading(noon))

    def test_date_object_is_treated_as_midnight(self):
        # Midnight is before every market, so nothing trades.
        self.assertFalse(StockSymbol("SH600000").is_stock_market_trading(datetime.date(2026, 1, 5)))

    def test_invalid_argument_fails_loudly(self):
        with self.assertRaises(TypeError):
            StockSymbol("SH600000").is_stock_market_trading("2026-01-05")

    def test_us_trading_window(self):
        sym = StockSymbol("XOP")
        self.assertTrue(sym.is_stock_market_trading(datetime.datetime(2026, 1, 5, 10, 0)))
        self.assertFalse(sym.is_stock_market_trading(datetime.datetime(2026, 1, 5, 21, 0)))
        self.assertFalse(sym.is_stock_market_trading(datetime.datetime(2026, 1, 5, 3, 0)))


class TimeZoneTest(unittest.TestCase):
    def test_time_zones(self):
        self.assertEqual(StockSymbol("SZ162411").get_time_zone(), "Asia/Shanghai")
        self.assertEqual(StockSymbol("00700").get_time_zone(), "Asia/Shanghai")
        self.assertEqual(StockSymbol("f_240019").get_time_zone(), "Asia/Shanghai")
        self.assertEqual(StockSymbol("USCNY").get_time_zone(), "Asia/Shanghai")
        self.assertEqual(StockSymbol("XOP").get_time_zone(), "America/New_York")
        self.assertEqual(StockSymbol("hf_ES").get_time_zone(), "America/New_York")
        self.assertEqual(StockSymbol("fx_sUSDCNH").get_time_zone(), "America/New_York")
        self.assertEqual(StockSymbol("nf_AU0").get_time_zone(), "Asia/Shanghai")
        self.assertEqual(StockSymbol("znb_CAC").get_time_zone(), "Europe/Paris")
        self.assertEqual(StockSymbol("znb_DAX").get_time_zone(), "Europe/Berlin")
        self.assertEqual(StockSymbol("znb_NKY").get_time_zone(), "Asia/Tokyo")
        self.assertEqual(StockSymbol("znb_TPX").get_time_zone(), "Asia/Tokyo")
        self.assertEqual(StockSymbol("znb_SENSEX").get_time_zone(), "Asia/Kolkata")
        self.assertEqual(StockSymbol("znb_UKX").get_time_zone(), "Asia/Shanghai")

    def test_set_time_zone_sets_process_zone(self):
        import os
        import time
        from unittest import mock

        original = os.environ.get("TZ")
        tzset_supported = hasattr(time, "tzset")
        try:
            with mock.patch.object(time, "tzset", create=True) as tzset_call:
                self.assertEqual(StockSymbol("XOP").set_time_zone(), "America/New_York")
                self.assertEqual(os.environ["TZ"], "America/New_York")
                if tzset_supported:
                    tzset_call.assert_called_once_with()

            with mock.patch.object(time, "tzset", create=True):
                self.assertEqual(StockSymbol("SZ162411").set_time_zone(), "Asia/Shanghai")
                self.assertEqual(os.environ["TZ"], "Asia/Shanghai")
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            if tzset_supported:
                time.tzset()


if __name__ == "__main__":
    unittest.main()
