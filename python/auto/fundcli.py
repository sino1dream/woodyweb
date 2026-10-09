from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Mapping

from fundcalc import PalmmicroFundCalculator
from stockdata import MySqlFundDataSource, build_fund_calculator_from_mysql_env, get_input_symbol_array


def parse_market_values(items: list[str]) -> dict[str, float]:
    values: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Expected SYMBOL=VALUE, got {item!r}")
        symbol, value = item.split("=", 1)
        symbol = symbol.strip()
        if not symbol:
            raise ValueError(f"Missing symbol in {item!r}")
        values[symbol] = float(value)
    return values


def build_output(
    calculator: PalmmicroFundCalculator,
    symbols: str,
    market_values: Mapping[str, float] | None = None,
    reverse_values: Mapping[str, float] | None = None,
    include_data: bool = False,
) -> dict[str, Any]:
    symbol_array = get_input_symbol_array(symbols)
    output: dict[str, Any] = {
        "symbols": symbol_array,
        "estimate": {},
    }

    for symbol in symbol_array:
        output["estimate"][symbol] = calculator.est_net_value(symbol, market_values or None)

    if reverse_values:
        output["reverse"] = calculator.reverse_est(reverse_values)
    if include_data:
        output["data"] = calculator.get_config()
    return output


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate fund net values from the migrated Python data adapter.")
    parser.add_argument("symbols", help="Fund symbols, separated by spaces, commas, Chinese comma, or Chinese dunhao.")
    parser.add_argument("--source", action="append", default=[], metavar="SYMBOL=VALUE", help="Market value used for realtime estimation.")
    parser.add_argument("--reverse", action="append", default=[], metavar="SYMBOL=VALUE", help="Reverse-estimate the peer value from a fund value.")
    parser.add_argument("--data", action="store_true", help="Include generated stock data in the JSON output.")
    parser.add_argument("--check-db", action="store_true", help="Only check MySQL connectivity and stock symbol lookup.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.check_db:
            source = MySqlFundDataSource.from_env()
            output = {
                "ok": True,
                "symbols": {symbol: source.get_stock_id(symbol) for symbol in get_input_symbol_array(args.symbols)},
            }
            print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
            return 0

        calculator = build_fund_calculator_from_mysql_env(args.symbols)
        output = build_output(
            calculator,
            args.symbols,
            parse_market_values(args.source),
            parse_market_values(args.reverse),
            args.data,
        )
        print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
