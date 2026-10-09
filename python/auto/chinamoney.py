from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping

from httpjson import fetch_json
from stockdata import load_mysql_env, mysql_config_from_env, mysql_connection_error_message


CHINA_MONEY_URL = "https://www.chinamoney.com.cn/"
CHINA_MONEY_JSON_URL = CHINA_MONEY_URL + "r/cms/www/chinamoney/data/fx/ccpr.json"

CURRENCY_SYMBOL_MAP = {
    "USD/CNY": "USCNY",
    "EUR/CNY": "EUCNY",
    "100JPY/CNY": "JPCNY",
    "HKD/CNY": "HKCNY",
}

CHINA_TIME_OFFSET = datetime.timedelta(hours=8)
CHINA_MONEY_OPEN_HOUR_MINUTE = 915
FLOAT_TOLERANCE = 0.0000005


@dataclass(frozen=True)
class ChinaMoneySnapshot:
    remote_date: str
    last_date_raw: str
    records: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ChinaMoneyUpdateSummary:
    reference_date: str | None
    hour_minute: int | None
    remote_date: str | None
    records: int = 0
    mapped: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_existing: int = 0
    skipped_missing: int = 0
    missing_symbols: tuple[str, ...] = ()
    skipped_reason: str | None = None
    dry_run: bool = False
    forced: bool = False
    overwrite: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference_date": self.reference_date,
            "hour_minute": self.hour_minute,
            "remote_date": self.remote_date,
            "records": self.records,
            "mapped": self.mapped,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped_existing": self.skipped_existing,
            "skipped_missing": self.skipped_missing,
            "missing_symbols": list(self.missing_symbols),
            "skipped_reason": self.skipped_reason,
            "dry_run": self.dry_run,
            "forced": self.forced,
            "overwrite": self.overwrite,
        }


class ChinaMoneyClient:
    """Client for the China Money central parity JSON feed."""

    def __init__(self, session: Any = None, timeout: float = 20.0):
        self.session = session
        self.timeout = timeout

    def fetch(self) -> ChinaMoneySnapshot:
        data = fetch_json(
            CHINA_MONEY_JSON_URL,
            session=self.session,
            timeout=self.timeout,
            headers={"Referer": CHINA_MONEY_URL},
        )
        if not isinstance(data, dict):
            raise RuntimeError("China Money response is not a JSON object.")

        data_block = data.get("data")
        if not isinstance(data_block, dict):
            raise RuntimeError("China Money response has no data object.")
        last_date_raw = str(data_block.get("lastDate", "")).strip()
        remote_date = last_date_raw[:10]
        _validate_date(remote_date)

        records = data.get("records", [])
        if records is None:
            records = []
        if not isinstance(records, list):
            raise RuntimeError("China Money response records is not a JSON array.")

        clean_records = tuple(dict(row) for row in records if isinstance(row, dict))
        return ChinaMoneySnapshot(remote_date=remote_date, last_date_raw=last_date_raw, records=clean_records)


class ChinaMoneyStore:
    """Storage interface used by :class:`ChinaMoneyUpdater`."""

    def get_stock_id(self, symbol: str) -> str | None:
        raise NotImplementedError

    def get_netvalue(self, stock_id: str, date: str) -> float | None:
        raise NotImplementedError

    def insert_netvalue(self, stock_id: str, date: str, price: float) -> bool:
        raise NotImplementedError

    def update_netvalue(self, stock_id: str, date: str, price: float) -> bool:
        raise NotImplementedError


class ChinaMoneyUpdater:
    """Python port of php/stock/chinamoney.php."""

    def __init__(
        self,
        client: ChinaMoneyClient,
        store: ChinaMoneyStore,
        overwrite: bool = False,
        force: bool = False,
    ):
        self.client = client
        self.store = store
        self.overwrite = overwrite
        self.force = force

    def update(
        self,
        reference_date: str | None = None,
        hour_minute: int | datetime.datetime | datetime.time | None = None,
        now: datetime.datetime | None = None,
        overwrite: bool | None = None,
        force: bool | None = None,
        dry_run: bool = False,
    ) -> ChinaMoneyUpdateSummary:
        overwrite = self.overwrite if overwrite is None else bool(overwrite)
        force = self.force if force is None else bool(force)

        china_now = _to_china_time(now)
        reference_date = reference_date or china_now.strftime("%Y-%m-%d")
        _validate_date(reference_date)
        hour_minute = _normalize_hour_minute(hour_minute) if hour_minute is not None else _hour_minute(china_now)

        us_cny_id = self.store.get_stock_id("USCNY")
        if (
            not force
            and not overwrite
            and us_cny_id is not None
            and self.store.get_netvalue(us_cny_id, reference_date) is not None
        ):
            return ChinaMoneyUpdateSummary(
                reference_date=reference_date,
                hour_minute=hour_minute,
                remote_date=None,
                skipped_reason="reference date already exists",
                dry_run=dry_run,
                forced=force,
                overwrite=overwrite,
            )

        if not force and hour_minute < CHINA_MONEY_OPEN_HOUR_MINUTE:
            return ChinaMoneyUpdateSummary(
                reference_date=reference_date,
                hour_minute=hour_minute,
                remote_date=None,
                skipped_reason="before 09:15",
                dry_run=dry_run,
                forced=force,
                overwrite=overwrite,
            )

        snapshot = self.client.fetch()
        mapped = _mapped_records(snapshot.records)
        inserted = 0
        updated = 0
        unchanged = 0
        skipped_existing = 0
        skipped_missing = 0
        missing_symbols: list[str] = []

        for symbol in CURRENCY_SYMBOL_MAP.values():
            if symbol not in mapped:
                continue
            price = mapped[symbol]
            stock_id = self.store.get_stock_id(symbol)
            if stock_id is None:
                missing_symbols.append(symbol)
                skipped_missing += 1
                continue

            existing = self.store.get_netvalue(stock_id, snapshot.remote_date)
            if existing is None:
                if not dry_run:
                    self.store.insert_netvalue(stock_id, snapshot.remote_date, price)
                inserted += 1
            elif overwrite:
                if _price_changed(existing, price):
                    if not dry_run:
                        self.store.update_netvalue(stock_id, snapshot.remote_date, price)
                    updated += 1
                else:
                    unchanged += 1
            else:
                skipped_existing += 1

        return ChinaMoneyUpdateSummary(
            reference_date=reference_date,
            hour_minute=hour_minute,
            remote_date=snapshot.remote_date,
            records=len(snapshot.records),
            mapped=len(mapped),
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
            skipped_existing=skipped_existing,
            skipped_missing=skipped_missing,
            missing_symbols=tuple(missing_symbols),
            dry_run=dry_run,
            forced=force,
            overwrite=overwrite,
        )


class MySqlChinaMoneyStore(ChinaMoneyStore):
    """DB-API backed store for the legacy stock and netvaluehistory tables."""

    def __init__(self, connection: Any):
        self.connection = connection

    @classmethod
    def from_env(cls) -> "MySqlChinaMoneyStore":
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("Install PyMySQL or pass an existing DB-API connection to MySqlChinaMoneyStore.") from exc

        config = mysql_config_from_env(load_mysql_env(os.environ))
        try:
            connection = pymysql.connect(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
                charset="utf8",
                autocommit=True,
                cursorclass=pymysql.cursors.DictCursor,
            )
        except Exception as exc:
            raise RuntimeError(mysql_connection_error_message(config, exc)) from exc
        return cls(connection)

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            _close_cursor(cursor)

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            count = int(getattr(cursor, "rowcount", 0) or 0)
        finally:
            _close_cursor(cursor)
        commit = getattr(self.connection, "commit", None)
        if commit:
            commit()
        return count

    def get_stock_id(self, symbol: str) -> str | None:
        row = self._fetchone("SELECT id FROM stock WHERE symbol = %s LIMIT 1", (symbol,))
        return str(row["id"]) if row else None

    def get_netvalue(self, stock_id: str, date: str) -> float | None:
        row = self._fetchone(
            "SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s LIMIT 1",
            (stock_id, date),
        )
        return float(row["close"]) if row else None

    def insert_netvalue(self, stock_id: str, date: str, price: float) -> bool:
        self._execute(
            "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s)",
            (stock_id, date, price),
        )
        return True

    def update_netvalue(self, stock_id: str, date: str, price: float) -> bool:
        self._execute(
            "UPDATE netvaluehistory SET close = %s WHERE stock_id = %s AND date = %s LIMIT 1",
            (price, stock_id, date),
        )
        return True


@dataclass
class MemoryChinaMoneyStore(ChinaMoneyStore):
    """In-memory store for unit tests and dry-run examples."""

    stocks: dict[str, str] = field(default_factory=dict)
    netvalue: dict[tuple[str, str], float] = field(default_factory=dict)
    inserted: list[tuple[str, str, float]] = field(default_factory=list)
    updated: list[tuple[str, str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.stock_ids_by_symbol = {symbol: stock_id for stock_id, symbol in self.stocks.items()}

    def get_stock_id(self, symbol: str) -> str | None:
        return self.stock_ids_by_symbol.get(symbol)

    def get_netvalue(self, stock_id: str, date: str) -> float | None:
        return self.netvalue.get((str(stock_id), date))

    def insert_netvalue(self, stock_id: str, date: str, price: float) -> bool:
        key = (str(stock_id), date)
        if key in self.netvalue:
            return False
        self.netvalue[key] = price
        self.inserted.append((str(stock_id), date, price))
        return True

    def update_netvalue(self, stock_id: str, date: str, price: float) -> bool:
        key = (str(stock_id), date)
        self.netvalue[key] = price
        self.updated.append((str(stock_id), date, price))
        return True


def build_updater_from_mysql_env(overwrite: bool = False, force: bool = False, timeout: float = 20.0) -> ChinaMoneyUpdater:
    return ChinaMoneyUpdater(ChinaMoneyClient(timeout=timeout), MySqlChinaMoneyStore.from_env(), overwrite, force)


def _validate_date(value: str) -> None:
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeError("Invalid China Money date: %r" % value) from exc


def _to_china_time(now: datetime.datetime | None = None) -> datetime.datetime:
    if now is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now_utc = now.replace(tzinfo=datetime.timezone.utc)
    else:
        now_utc = now.astimezone(datetime.timezone.utc)
    return (now_utc + CHINA_TIME_OFFSET).replace(tzinfo=None)


def _hour_minute(value: datetime.datetime | datetime.time) -> int:
    return value.hour * 100 + value.minute


def _normalize_hour_minute(value: int | datetime.datetime | datetime.time) -> int:
    if isinstance(value, (datetime.datetime, datetime.time)):
        result = _hour_minute(value)
    else:
        result = int(value)
    if result < 0 or result > 2359 or (result % 100) >= 60:
        raise ValueError("hour_minute must be HHMM between 0000 and 2359.")
    return result


def _mapped_records(records: tuple[dict[str, Any], ...]) -> dict[str, float]:
    result: dict[str, float] = {}
    for record in records:
        pair = str(record.get("vrtEName", "")).strip()
        symbol = CURRENCY_SYMBOL_MAP.get(pair)
        if symbol is None:
            continue
        price = _parse_price(record.get("price"), pair)
        result[symbol] = price
    return result


def _parse_price(value: Any, pair: str) -> float:
    try:
        text = str(value).strip().replace(",", "")
        price = float(text)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Invalid price for %s: %r" % (pair, value)) from exc
    if math.isnan(price) or math.isinf(price):
        raise RuntimeError("Invalid price for %s: %r" % (pair, value))
    return price


def _price_changed(existing: float, price: float) -> bool:
    return abs(float(existing) - float(price)) > FLOAT_TOLERANCE


def _close_cursor(cursor: Any) -> None:
    close = getattr(cursor, "close", None)
    if close:
        close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update USD/EUR/JPY/HKD CNY central parity rates from China Money.")
    parser.add_argument("--check", action="store_true", help="Fetch and print the mapped remote rows without touching MySQL.")
    parser.add_argument("--peek", action="store_true", help="Fetch and print the first mapped remote rows without touching MySQL.")
    parser.add_argument("--check-db", action="store_true", help="Read local stock IDs and existing values without writing.")
    parser.add_argument("--dry-run", action="store_true", help="Compute actions without writing to MySQL.")
    parser.add_argument("--force", action="store_true", help="Bypass the 09:15 and reference-date pre-checks.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Update existing rows and bypass the reference-date pre-check.",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")
    parser.add_argument("--ascii", action="store_true", help="Escape non-ASCII output for diagnostics.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        client = ChinaMoneyClient(timeout=args.timeout)
        if args.peek:
            snapshot = client.fetch()
            mapped = _mapped_records(snapshot.records)
            output = {
                "remote_date": snapshot.remote_date,
                "last_date_raw": snapshot.last_date_raw,
                "records": len(snapshot.records),
                "mapped": mapped,
            }
        elif args.check:
            snapshot = client.fetch()
            output = {
                "remote_date": snapshot.remote_date,
                "last_date_raw": snapshot.last_date_raw,
                "records": len(snapshot.records),
                "mapped": _mapped_records(snapshot.records),
            }
        elif args.check_db:
            snapshot = client.fetch()
            store = MySqlChinaMoneyStore.from_env()
            output = {
                "remote_date": snapshot.remote_date,
                "mapped": _mapped_records(snapshot.records),
                "stock": {},
            }
            for symbol in CURRENCY_SYMBOL_MAP.values():
                stock_id = store.get_stock_id(symbol)
                output["stock"][symbol] = {
                    "stock_id": stock_id,
                    "existing": store.get_netvalue(stock_id, snapshot.remote_date) if stock_id else None,
                }
        else:
            updater = build_updater_from_mysql_env(args.overwrite, args.force, args.timeout)
            output = updater.update(dry_run=args.dry_run).as_dict()
        print(json.dumps(output, ensure_ascii=args.ascii, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    except (RuntimeError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
