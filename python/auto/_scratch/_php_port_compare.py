"""Differential check: compare the Python stocksymbol port against real PHP output.

This is the verification harness for the `php/stock/stocksymbol.php` port. It
builds one key set, asks PHP for the same keys through `_php_port_check.php`,
and fails loudly on any mismatch, so the port can be trusted case by case
instead of by eyeballing two source files.

Run it with no arguments and it runs the PHP harness itself:

    python -B _php_port_compare.py            # ~6300 cases, exit 0 when clean
    python -B _php_port_compare.py ref.json   # reuse a saved reference

Requires PHP on PATH. Intentional divergences are listed in KNOWN_DIVERGENCES
and are reported separately from real mismatches.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
# 脚本挪到 python/auto/_scratch/ 之后，sys.path[0] 只剩 _scratch，
# 所以要把 python/auto 与 python 两级目录显式补进来，才 import 得到 ref/、sql/ 这些子包。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import stocksymbol as ss

HERE = Path(__file__).resolve().parent

PHP_HARNESS = HERE / "_php_port_check.php"

SYMBOLS = [
    "^HSI", "^HSCE", "^HSTECH", "^DJI", "^GSPC", "^NDX", "^FTSE",
    "^SZ162411-IV", "^HSI-IV",
    "00700", "09988", "000001",
    "SZ162411", "SZ161227", "SZ159518", "SZ163208", "SZ160416", "SZ159502", "SZ160723",
    "SZ000001", "SZ200011", "SZ300750", "SZ399001",
    "SH600000", "SH513350", "SH501043", "SH688981", "SH900901", "SH510300", "SH513180",
    "SH000300", "SH518880", "SH513080",
    "BJ430047", "BJ830799",
    "f_240019", "f_24001",
    "znb_NKY", "znb_CAC", "znb_DAX", "znb_TPX", "znb_SENSEX", "znb_UKX",
    "hf_ES", "hf_CL", "hf_CHA50CFD",
    "nf_AU0", "nf_AG0",
    "fx_susdcnh", "fx_sUSDCNH", "fx_s",
    "USCNY", "EUCNY", "JPCNY", "HKCNY",
    "XOP", "AAPL", "BRK.B", "QQQ", "SPY",
    "sz162411",
]

METHODS0 = [
    "is_index", "is_yahoo_net_value", "is_symbol_us", "is_symbol_h", "get_digit_a",
    "is_symbol_a", "is_shanghai_a", "is_shenzhen_a", "is_beijing_a",
    "is_shanghai_etf", "is_shanghai_lof", "is_shanghai_star",
    "is_shanghai_b", "is_shenzhen_b", "is_shenzhen_etf", "is_shenzhen_lof",
    "is_shenzhen_gem", "is_lof_a", "is_etf_a", "is_fund_a", "is_index_a",
    "is_stock_b", "is_east_money_forex", "is_sina_forex", "is_forex",
    "is_sina_fund", "is_sina_global_index", "is_sina_future_cn",
    "is_sina_future_except_gold_cn", "is_sina_future_us", "is_sina_future",
    "get_sina_fund_symbol", "get_sina_index_h", "get_sina_index_us",
    "get_sina_symbol", "get_yahoo_symbol", "get_precision",
    "get_default_position", "is_tradable", "get_time_zone", "get_display",
    "get_symbol",
]

METHODS_HM = ["is_before_stock_market", "is_after_stock_market"]
HOUR_MINUTES = [100, 359, 400, 800, 859, 900, 914, 915, 1140, 1141, 1200, 1259, 1300,
                1505, 1506, 1535, 1536, 1605, 1606, 1615, 1616, 1805, 1806, 2005, 2006, 2359]

ARRAYS = [
    "get_secondary_listing_array", "china_index_get_ashr_array", "china_index_get_sz100_array",
    "china_index_get_coal_array", "china_index_get_symbol_array", "china_future_get_symbol_array",
    "qdii_get_oil_etf_symbol_array", "qdii_get_qqq_match_array", "qdii_get_qqq_symbol_array",
    "qdii_get_spy_match_array", "qdii_get_spy_symbol_array", "qdii_get_xop_symbol_array",
    "qdii_get_xbi_symbol_array", "qdii_get_symbol_array", "qdii_hk_get_tech_symbol_array",
    "qdii_hk_get_h_shares_symbol_array", "qdii_hk_get_hang_seng_symbol_array",
    "qdii_hk_get_index_symbol_array", "qdii_hk_get_symbol_array", "qdii_jp_get_nky_symbol_array",
    "qdii_jp_get_symbol_array", "qdii_eu_get_dax_symbol_array", "qdii_eu_get_symbol_array",
    "get_china_internet_symbol_array", "get_msci_us50_symbol_array", "get_hk_mix_symbol_array",
    "get_qdii_gold_symbol_array", "get_qdii_oil_symbol_array", "get_lof_mix_symbol_array",
    "qdii_mix_get_symbol_array", "get_all_symbol_array", "get_over_night_symbol_array",
]

IN_ARRAYS = [
    "in_array_ashr_symbol", "in_array_china_index", "in_array_china_future",
    "in_array_oil_etf_qdii", "in_array_qqq_match", "in_array_qqq_qdii", "in_array_spy_match",
    "in_array_spy_qdii", "in_array_xop_qdii", "in_array_xbi_qdii", "in_array_qdii",
    "in_array_tech_qdii_hk", "in_array_h_shares_qdii_hk", "in_array_hang_seng_qdii_hk",
    "in_array_qdii_hk", "in_array_nky_qdii_jp", "in_array_qdii_jp", "in_array_dax_qdii_eu",
    "in_array_qdii_eu", "in_array_hk_mix", "in_array_lof_mix", "in_array_qdii_mix",
    "in_array_all",
]

PROBE_SYMBOLS = ["SH501043", "SH510300", "SZ160416", "SH513100", "SH513500",
                 "SH513350", "SZ159502", "SZ162411", "SH513180", "SH510900",
                 "SH501302", "SH513000", "SH513030", "SH513090", "SZ160644",
                 "SH513360", "SZ161226", "SH518880", "SZ161725", "AAPL", "SH999999"]

# Known, intentional divergences. Each entry is (key, why).
#
#   IsChinaStockDigit(' 16241'): PHP counts the leading space in strlen() and
#   is_numeric() still accepts it, so PHP returns ' 16241' with the space. This
#   port strips before validating, so it returns None instead of handing a
#   whitespace-padded symbol back to its callers. Every real caller trims first
#   (see stock_get_symbol), so the two never disagree in practice.
KNOWN_DIVERGENCES = {
    "IsChinaStockDigit( 16241)": "PHP returns a whitespace-padded symbol; this port strips it and rejects.",
}

# PHP function name -> Python function name for the membership helpers.
PHP_IN_ARRAY_NAMES = {
    "in_arrayAshrSymbol": "in_array_ashr_symbol",
    "in_arrayChinaIndex": "in_array_china_index",
    "in_arrayChinaFuture": "in_array_china_future",
    "in_arrayOilEtfQdii": "in_array_oil_etf_qdii",
    "in_arrayQqqMatch": "in_array_qqq_match",
    "in_arrayQqqQdii": "in_array_qqq_qdii",
    "in_arraySpyMatch": "in_array_spy_match",
    "in_arraySpyQdii": "in_array_spy_qdii",
    "in_arrayXopQdii": "in_array_xop_qdii",
    "in_arrayXbiQdii": "in_array_xbi_qdii",
    "in_arrayQdii": "in_array_qdii",
    "in_arrayTechQdiiHk": "in_array_tech_qdii_hk",
    "in_arrayHSharesQdiiHk": "in_array_h_shares_qdii_hk",
    "in_arrayHangSengQdiiHk": "in_array_hang_seng_qdii_hk",
    "in_arrayQdiiHk": "in_array_qdii_hk",
    "in_arrayNkyQdiiJp": "in_array_nky_qdii_jp",
    "in_arrayQdiiJp": "in_array_qdii_jp",
    "in_arrayDaxQdiiEu": "in_array_dax_qdii_eu",
    "in_arrayQdiiEu": "in_array_qdii_eu",
    "in_arrayHkMix": "in_array_hk_mix",
    "in_arrayLofMix": "in_array_lof_mix",
    "in_arrayQdiiMix": "in_array_qdii_mix",
    "in_arrayAll": "in_array_all",
}


def norm(value):
    """PHP false -> None, PHP true -> True, everything else unchanged."""
    if value is False:
        return None
    return value


def build_python_cases():
    cases = {}
    for symbol in SYMBOLS:
        sym = ss.StockSymbol(symbol)
        for method in METHODS0:
            cases["%s|%s" % (symbol, method)] = norm(getattr(sym, method)())
        for method in METHODS_HM:
            for hm in HOUR_MINUTES:
                cases["%s|%s|%d" % (symbol, method, hm)] = norm(getattr(sym, method)(hm))

    for s in ["fx_susdcnh", "hf_ES", "znb_NKY", "AAPL", ""]:
        cases["StrHasPrefix(%s,fx_s)" % s] = norm(ss.str_has_prefix(s, "fx_s"))
    for d in ["162411", "1624", "16241X", " 16241", "600000"]:
        cases["IsChinaStockDigit(%s)" % d] = norm(ss.is_china_stock_digit(d))
    for d in ["162411", "513180", "501043", "600000", "000001", "16241"]:
        cases["BuildChinaFundSymbol(%s)" % d] = norm(ss.build_china_fund_symbol(d))
    for d in ["000001", "300750", "430047", "830799", "600000", "688981", "500000", "700000", "900000"]:
        cases["BuildChinaStockSymbol(%s)" % d] = norm(ss.build_china_stock_symbol(d))
    for d in ["700", "09988", "00001"]:
        cases["BuildHongkongStockSymbol(%s)" % d] = norm(ss.build_hongkong_stock_symbol(d))

    cases["BuildYahooNetValueSymbol(SZ162411)"] = norm(ss.build_yahoo_net_value_symbol("SZ162411"))
    cases["BuildYahooNetValueSymbol(SZ162411,NV)"] = norm(ss.build_yahoo_net_value_symbol("SZ162411", "NV"))
    cases["BuildYahooNetValueSymbol()"] = norm(ss.build_yahoo_net_value_symbol(""))

    php_array_names = {
        "get_secondary_listing_array": "GetSecondaryListingArray",
        "china_index_get_ashr_array": "ChinaIndexGetAshrArray",
        "china_index_get_sz100_array": "ChinaIndexGetSz100Array",
        "china_index_get_coal_array": "ChinaIndexGetCoalArray",
        "china_index_get_symbol_array": "ChinaIndexGetSymbolArray",
        "china_future_get_symbol_array": "ChinaFutureGetSymbolArray",
        "qdii_get_oil_etf_symbol_array": "QdiiGetOilEtfSymbolArray",
        "qdii_get_qqq_match_array": "QdiiGetQqqMatchArray",
        "qdii_get_qqq_symbol_array": "QdiiGetQqqSymbolArray",
        "qdii_get_spy_match_array": "QdiiGetSpyMatchArray",
        "qdii_get_spy_symbol_array": "QdiiGetSpySymbolArray",
        "qdii_get_xop_symbol_array": "QdiiGetXopSymbolArray",
        "qdii_get_xbi_symbol_array": "QdiiGetXbiSymbolArray",
        "qdii_get_symbol_array": "QdiiGetSymbolArray",
        "qdii_hk_get_tech_symbol_array": "QdiiHkGetTechSymbolArray",
        "qdii_hk_get_h_shares_symbol_array": "QdiiHkGetHSharesSymbolArray",
        "qdii_hk_get_hang_seng_symbol_array": "QdiiHkGetHangSengSymbolArray",
        "qdii_hk_get_index_symbol_array": "QdiiHkGetIndexSymbolArray",
        "qdii_hk_get_symbol_array": "QdiiHkGetSymbolArray",
        "qdii_jp_get_nky_symbol_array": "QdiiJpGetNkySymbolArray",
        "qdii_jp_get_symbol_array": "QdiiJpGetSymbolArray",
        "qdii_eu_get_dax_symbol_array": "QdiiEuGetDaxSymbolArray",
        "qdii_eu_get_symbol_array": "QdiiEuGetSymbolArray",
        "get_china_internet_symbol_array": "GetChinaInternetSymbolArray",
        "get_msci_us50_symbol_array": "GetMsciUs50SymbolArray",
        "get_hk_mix_symbol_array": "GetHkMixSymbolArray",
        "get_qdii_gold_symbol_array": "GetQdiiGoldSymbolArray",
        "get_qdii_oil_symbol_array": "GetQdiiOilSymbolArray",
        "get_lof_mix_symbol_array": "GetLofMixSymbolArray",
        "qdii_mix_get_symbol_array": "QdiiMixGetSymbolArray",
        "get_all_symbol_array": "GetAllSymbolArray",
        "get_over_night_symbol_array": "GetOverNightSymbolArray",
    }
    for fn in ARRAYS:
        cases["array(%s)" % php_array_names[fn]] = getattr(ss, fn)()

    for php_name, py_name in PHP_IN_ARRAY_NAMES.items():
        helper = getattr(ss, py_name)
        for symbol in PROBE_SYMBOLS:
            cases["inarray(%s,%s)" % (php_name, symbol)] = norm(helper(symbol))

    return cases


def load_reference(reference_path):
    """Return the PHP reference map.

    The PHP harness is asked to print JSON on stdout, because a sandboxed PHP
    process may not be allowed to write into the workspace.
    """
    if reference_path.exists():
        return json.loads(reference_path.read_text(encoding="utf-8"))

    print("reference missing, running the PHP harness...")
    completed = subprocess.run(
        # -n skips php.ini; otherwise a startup warning can land on stdout and
        # corrupt the JSON payload, which is the only thing stdout may carry.
        ["php", "-n", "-d", "display_errors=stderr", str(PHP_HARNESS), "-"],
        cwd=str(HERE),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.stderr:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
    payload = completed.stdout.decode("utf-8")
    # Defensive: keep only the JSON object, in case PHP still prints a notice.
    start = payload.find("{")
    if start < 0:
        raise SystemExit("PHP harness produced no JSON on stdout")
    return json.loads(payload[start:])


def main():
    reference_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "_php_reference.json"

    reference = load_reference(reference_path)
    python_cases = build_python_cases()

    only_php = sorted(set(reference) - set(python_cases))
    only_python = sorted(set(python_cases) - set(reference))

    mismatches = []
    for key in sorted(set(reference) & set(python_cases)):
        php_value = reference[key]
        py_value = python_cases[key]
        if php_value != py_value:
            mismatches.append((key, php_value, py_value))

    known = [item for item in mismatches if item[0] in KNOWN_DIVERGENCES]
    unexpected = [item for item in mismatches if item[0] not in KNOWN_DIVERGENCES]

    print("compared cases : %d" % len(set(reference) & set(python_cases)))
    print("php only       : %d" % len(only_php))
    print("python only    : %d" % len(only_python))
    print("mismatches     : %d (%d known, %d unexpected)"
          % (len(mismatches), len(known), len(unexpected)))

    for key in only_php[:20]:
        print("  PHP-ONLY  %s = %r" % (key, reference[key]))
    for key in only_python[:20]:
        print("  PY-ONLY   %s = %r" % (key, python_cases[key]))
    for key, php_value, py_value in known:
        print("  KNOWN     %-45s php=%r python=%r  (%s)"
              % (key, php_value, py_value, KNOWN_DIVERGENCES[key]))
    for key, php_value, py_value in unexpected[:40]:
        print("  MISMATCH  %-45s php=%r python=%r" % (key, php_value, py_value))

    return 1 if (unexpected or only_php or only_python) else 0


if __name__ == "__main__":
    sys.exit(main())
