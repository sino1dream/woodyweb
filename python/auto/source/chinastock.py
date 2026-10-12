from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from source.httpjson import fetch_json
from core.stockdata import load_mysql_env, mysql_config_from_env, mysql_connection_error_message


SINA_VIP_STOCK_URL = "https://vip.stock.finance.sina.com.cn"
SINA_MARKET_JSON_URL = SINA_VIP_STOCK_URL + "/quotes_service/api/json_v2.php"

SINA_COUNT_CODES = {
    "hs_a": "HQNodeStockCount",
    "hs_b": "HQNodeStockCount",
    "hs_s": "HQNodeStockCountSimple",
    "etf_hq_fund": "HQNodeStockCountSimple",
    "lof_hq_fund": "HQNodeStockCountSimple",
    "qbgg_hk": "HKStockCount",
    "aplush": "ANHCount",
}

SINA_DATA_CODES = {
    "hs_a": "HQNodeData",
    "hs_b": "HQNodeData",
    "hs_s": "HQNodeDataSimple",
    "etf_hq_fund": "HQNodeDataSimple",
    "lof_hq_fund": "HQNodeDataSimple",
    "qbgg_hk": "HKStockData",
    "aplush": "ANHData",
}

CHINA_STOCK_SYMBOL_FILTERS = {
    "hs_a": "symbol REGEXP '^(SH6|BJ[48]|SZ[03])[0-9]{5}$' AND symbol NOT REGEXP '^SZ399'",
    "hs_b": "symbol REGEXP '^(SH9|SZ2)[0-9]{5}$'",
    "hs_s": "symbol REGEXP '^(SH000|SZ399|BJ899)[0-9]{3}$'",
    "etf_hq_fund": "symbol REGEXP '^(SH5[168]|SZ15)[0-9]{4}$'",
    "lof_hq_fund": "symbol REGEXP '^(SH50|SZ16)[0-9]{4}$'",
    "qbgg_hk": "symbol REGEXP '^[0-9]{5}$'",
}

DEFAULT_PAGE_SIZE = 100


def sina_count_code(node: str) -> str:
    return _node_lookup(SINA_COUNT_CODES, node)


def sina_data_code(node: str) -> str:
    return _node_lookup(SINA_DATA_CODES, node)


def china_stock_symbol_filter(node: str) -> str:
    return _node_lookup(CHINA_STOCK_SYMBOL_FILTERS, node)


def _node_lookup(mapping: Mapping[str, str], node: str) -> str:
    normalized = _normalize_node(node)
    if normalized not in mapping:
        raise ValueError("Unsupported China market node %r. Expected one of: %s" % (node, ", ".join(sorted(mapping))))
    return mapping[normalized]


def _normalize_node(node: str) -> str:
    return str(node).strip().lower()


@dataclass(frozen=True)
class ChinaStockUpdateSummary:
    node: str
    remote_count: int
    total: int = 0
    changed: int = 0
    deleted: int = 0
    protected: int = 0
    pairs_changed: int = 0
    pairs_deleted: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "remote_count": self.remote_count,
            "total": self.total,
            "changed": self.changed,
            "deleted": self.deleted,
            "protected": self.protected,
            "pairs_changed": self.pairs_changed,
            "pairs_deleted": self.pairs_deleted,
        }


class SinaMarketClient:
    """Client for the Market_Center JSON endpoints used by updatechinastock.php."""

    def __init__(self, session: Any = None, timeout: float = 20.0):
        self.session = session
        self.timeout = timeout

    def count(self, node: str) -> int:
        node = _normalize_node(node)
        code = sina_count_code(node)
        url = "%s/Market_Center.get%s?node=%s" % (SINA_MARKET_JSON_URL, code, node)
        data = self._get_json(url)
        try:
            return int(data)
        except (TypeError, ValueError):
            return 0

    def fetch_page(self, node: str, page: int, num: int = DEFAULT_PAGE_SIZE) -> list[dict[str, Any]]:
        node = _normalize_node(node)
        code = sina_data_code(node)
        url = (
            "%s/Market_Center.get%s?page=%d&num=%d&sort=symbol&asc=1&node=%s"
            % (SINA_MARKET_JSON_URL, code, page, num, node)
        )
        data = self._get_json(url)
        if data is None:
            return []
        if not isinstance(data, list):
            raise RuntimeError("Sina response for %s is not a JSON array." % node)
        return data

    def _get_json(self, url: str) -> Any:
        return fetch_json(url, session=self.session, timeout=self.timeout)


class ChinaStockStore:
    """Storage interface implemented by :class:`MySqlChinaStockStore`."""

    def get_stock_id(self, symbol: str) -> str | None:
        raise NotImplementedError

    def list_symbol_ids(self, node: str) -> Mapping[str, str]:
        raise NotImplementedError

    def write_symbol(self, symbol: str, name: str) -> bool:
        raise NotImplementedError

    def list_ah_pairs(self) -> Mapping[str, str]:
        raise NotImplementedError

    def write_ah_pair(self, stock_id: str, pair_id: str) -> bool:
        raise NotImplementedError

    def delete_ab_pair_any(self, stock_id: str) -> int:
        raise NotImplementedError

    def delete_ah_pair_by_id(self, stock_id: str) -> int:
        raise NotImplementedError

    def count_stock_group_items(self, stock_id: str) -> int:
        raise NotImplementedError

    def delete_stock(self, stock_id: str) -> None:
        raise NotImplementedError


class ChinaStockUpdater:
    """Python port of the non-web parts of php/test/updatechinastock.php."""

    def __init__(self, client: SinaMarketClient, store: ChinaStockStore, page_size: int = DEFAULT_PAGE_SIZE):
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero.")
        self.client = client
        self.store = store
        self.page_size = page_size

    def update(self, node: str, max_records: int | None = None) -> ChinaStockUpdateSummary:
        normalized_node = str(node).strip().lower()
        remote_count = self.client.count(normalized_node)
        strict_limit = max_records is not None
        if max_records is not None:
            remote_count = min(remote_count, max_records)
        if remote_count <= 0:
            return ChinaStockUpdateSummary(node=normalized_node, remote_count=remote_count)

        if normalized_node == "aplush":
            return self._update_ah_pairs(normalized_node, remote_count, strict_limit)
        return self._update_stocks(normalized_node, remote_count, strict_limit)

    def _update_stocks(self, node: str, remote_count: int, strict_limit: bool) -> ChinaStockUpdateSummary:
        remaining = {str(symbol): str(stock_id) for symbol, stock_id in self.store.list_symbol_ids(node).items()}
        total = 0
        changed = 0
        for row in self._iter_market_data(node, remote_count, strict_limit):
            symbol = str(row.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            total += 1
            name = str(row.get("name", "")).strip()
            if self.store.write_symbol(symbol, name):
                changed += 1
            remaining.pop(symbol, None)

        deleted, protected = delete_old_china_stock(self.store, remaining)
        return ChinaStockUpdateSummary(
            node=node,
            remote_count=remote_count,
            total=total,
            changed=changed,
            deleted=deleted,
            protected=protected,
        )

    def _update_ah_pairs(self, node: str, remote_count: int, strict_limit: bool) -> ChinaStockUpdateSummary:
        old_pairs = {str(stock_id): str(pair_id) for stock_id, pair_id in self.store.list_ah_pairs().items()}
        total = 0
        changed = 0
        for row in self._iter_market_data(node, remote_count, strict_limit):
            symbol_a = str(row.get("a", "")).strip().upper()
            symbol_h = str(row.get("h", "")).strip()
            total += 1
            if not symbol_a or not symbol_h:
                continue

            stock_id_a = self._stock_id(symbol_a)
            stock_id_h = self._stock_id(symbol_h)
            if stock_id_a is None or stock_id_h is None:
                continue

            if self.store.write_ah_pair(stock_id_a, stock_id_h):
                changed += 1
            old_pairs.pop(stock_id_a, None)

        deleted = 0
        for stock_id in old_pairs:
            if self.store.delete_ah_pair_by_id(stock_id):
                deleted += 1

        return ChinaStockUpdateSummary(
            node=node,
            remote_count=remote_count,
            total=total,
            pairs_changed=changed,
            pairs_deleted=deleted,
        )

    def _iter_market_data(self, node: str, remote_count: int, strict_limit: bool = False) -> Iterable[dict[str, Any]]:
        total = 0
        page = 1
        while total < remote_count:
            rows = self.client.fetch_page(node, page, self.page_size)
            if not rows:
                break
            page_count = 0
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                if strict_limit and total >= remote_count:
                    return
                yield dict(row)
                page_count += 1
                total += 1
            if page_count == 0:
                break
            page += 1

    def _stock_id(self, symbol: str) -> str | None:
        # The update flow only needs id lookups here, so keep this separate from
        # list_symbol_ids' node-specific regex filters.
        return self.store.get_stock_id(symbol)


def delete_old_china_stock(store: ChinaStockStore, symbol_ids: Mapping[str, str]) -> tuple[int, int]:
    deleted = 0
    protected = 0
    for symbol, stock_id in symbol_ids.items():
        store.delete_ab_pair_any(stock_id)
        store.delete_ah_pair_by_id(stock_id)
        if store.count_stock_group_items(stock_id) == 0:
            store.delete_stock(stock_id)
            deleted += 1
        else:
            protected += 1
    return deleted, protected


class MySqlChinaStockStore(ChinaStockStore):
    """DB-API backed store for the legacy stock tables."""

    def __init__(self, connection: Any):
        self.connection = connection

    @classmethod
    def from_env(cls) -> "MySqlChinaStockStore":
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("Install PyMySQL or pass an existing DB-API connection to MySqlChinaStockStore.") from exc

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

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
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

    def list_symbol_ids(self, node: str) -> Mapping[str, str]:
        where = china_stock_symbol_filter(node)
        rows = self._fetchall("SELECT symbol, id FROM stock WHERE %s ORDER BY symbol ASC" % where)
        return {str(row["symbol"]): str(row["id"]) for row in rows}

    def write_symbol(self, symbol: str, name: str) -> bool:
        clean_name = name.strip()
        row = self._fetchone("SELECT id, name FROM stock WHERE symbol = %s LIMIT 1", (symbol,))
        if row:
            if clean_name != str(row["name"]):
                self._execute("UPDATE stock SET name = %s WHERE id = %s LIMIT 1", (clean_name, row["id"]))
                return True
            return False

        self._execute("INSERT INTO stock (symbol, name) VALUES (%s, %s)", (symbol, clean_name))
        return True

    def list_ah_pairs(self) -> Mapping[str, str]:
        rows = self._fetchall("SELECT id, stock_id FROM ahpair")
        return {str(row["id"]): str(row["stock_id"]) for row in rows}

    def write_ah_pair(self, stock_id: str, pair_id: str) -> bool:
        row = self._fetchone("SELECT stock_id FROM ahpair WHERE id = %s LIMIT 1", (stock_id,))
        if row:
            if str(row["stock_id"]) != str(pair_id):
                self._execute("UPDATE ahpair SET stock_id = %s WHERE id = %s LIMIT 1", (pair_id, stock_id))
                return True
            return False

        self._execute("INSERT INTO ahpair (id, stock_id) VALUES (%s, %s)", (stock_id, pair_id))
        return True

    def delete_ab_pair_any(self, stock_id: str) -> int:
        return self._delete_any("abpair", stock_id)

    def delete_ah_pair_by_id(self, stock_id: str) -> int:
        return self._delete_by_id("ahpair", stock_id)

    def count_stock_group_items(self, stock_id: str) -> int:
        row = self._fetchone("SELECT COUNT(*) AS total FROM stockgroupitem WHERE stock_id = %s", (stock_id,))
        return int(row["total"]) if row else 0

    def delete_stock(self, stock_id: str) -> None:
        for table in ("stockema50", "stockema200", "stockhistory", "netvaluehistory"):
            self._execute("DELETE FROM %s WHERE stock_id = %%s" % table, (stock_id,))
        self._execute("DELETE FROM stock WHERE id = %s LIMIT 1", (stock_id,))

    def _delete_by_id(self, table: str, stock_id: str) -> int:
        row = self._fetchone("SELECT COUNT(*) AS total FROM %s WHERE id = %%s" % table, (stock_id,))
        count = int(row["total"]) if row else 0
        if count:
            self._execute("DELETE FROM %s WHERE id = %%s LIMIT 1" % table, (stock_id,))
        return count

    def _delete_any(self, table: str, stock_id: str) -> int:
        row = self._fetchone("SELECT COUNT(*) AS total FROM %s WHERE stock_id = %%s" % table, (stock_id,))
        count = int(row["total"]) if row else 0
        if count:
            self._execute("DELETE FROM %s WHERE stock_id = %%s" % table, (stock_id,))
            return count

        row = self._fetchone("SELECT COUNT(*) AS total FROM %s WHERE id = %%s" % table, (stock_id,))
        count = int(row["total"]) if row else 0
        if count:
            self._execute("DELETE FROM %s WHERE id = %%s LIMIT 1" % table, (stock_id,))
        return count


def _close_cursor(cursor: Any) -> None:
    close = getattr(cursor, "close", None)
    if close:
        close()


@dataclass
class MemoryChinaStockStore(ChinaStockStore):
    """In-memory store used for tests and dry analysis."""

    stocks: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    ah_pairs: dict[str, str] = field(default_factory=dict)
    ab_pairs: dict[str, str] = field(default_factory=dict)
    group_items: dict[str, int] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.stock_ids_by_symbol = {symbol: stock_id for stock_id, symbol in self.stocks.items()}

    def get_stock_id(self, symbol: str) -> str | None:
        return self.stock_ids_by_symbol.get(symbol)

    def list_symbol_ids(self, node: str) -> Mapping[str, str]:
        # Tests can subclass this when node-specific filtering matters.
        return dict(self.stock_ids_by_symbol)

    def write_symbol(self, symbol: str, name: str) -> bool:
        stock_id = self.stock_ids_by_symbol.get(symbol)
        if stock_id is None:
            numeric_ids = [int(item) for item in self.stocks if str(item).isdigit()]
            stock_id = str(max(numeric_ids) + 1 if numeric_ids else 1)
            self.stocks[stock_id] = symbol
            self.stock_ids_by_symbol[symbol] = stock_id
            self.names[symbol] = name
            return True
        if self.names.get(symbol) != name:
            self.names[symbol] = name
            return True
        return False

    def list_ah_pairs(self) -> Mapping[str, str]:
        return dict(self.ah_pairs)

    def write_ah_pair(self, stock_id: str, pair_id: str) -> bool:
        if self.ah_pairs.get(stock_id) == pair_id:
            return False
        self.ah_pairs[stock_id] = pair_id
        return True

    def delete_ab_pair_any(self, stock_id: str) -> int:
        if self.ab_pairs.pop(stock_id, None) is not None:
            return 1
        for key, value in list(self.ab_pairs.items()):
            if value == stock_id:
                del self.ab_pairs[key]
                return 1
        return 0

    def delete_ah_pair_by_id(self, stock_id: str) -> int:
        return 1 if self.ah_pairs.pop(stock_id, None) is not None else 0

    def count_stock_group_items(self, stock_id: str) -> int:
        return self.group_items.get(stock_id, 0)

    def delete_stock(self, stock_id: str) -> None:
        symbol = self.stocks.pop(stock_id, None)
        if symbol is not None:
            self.stock_ids_by_symbol.pop(symbol, None)
            self.names.pop(symbol, None)
        self.deleted.append(stock_id)


def build_updater_from_mysql_env(page_size: int = DEFAULT_PAGE_SIZE) -> ChinaStockUpdater:
    return ChinaStockUpdater(SinaMarketClient(), MySqlChinaStockStore.from_env(), page_size)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Chinese stock/ETF/LOF/HK reference rows from Sina market nodes.")
    parser.add_argument("node", help="Sina market node: hs_a, hs_b, hs_s, etf_hq_fund, lof_hq_fund, qbgg_hk, or aplush.")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Rows requested per Sina page.")
    parser.add_argument("--max-records", type=int, default=None, help="Limit processed rows for a partial or test run.")
    parser.add_argument("--check", action="store_true", help="Only fetch and print the remote row count.")
    parser.add_argument("--check-db", action="store_true", help="Only read and print the local database row count.")
    parser.add_argument("--peek", type=int, default=None, metavar="N", help="Fetch and print the first N rows without writing.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")
    parser.add_argument("--ascii", action="store_true", help="Escape non-ASCII output for diagnostics.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.peek is not None:
            if args.peek <= 0:
                raise ValueError("--peek must be greater than zero.")
            client = SinaMarketClient()
            rows = client.fetch_page(args.node, 1, min(args.page_size, args.peek))
            output = {"node": args.node, "count": len(rows), "rows": rows[: args.peek]}
        elif args.check:
            client = SinaMarketClient()
            output = {"node": args.node, "remote_count": client.count(args.node)}
        elif args.check_db:
            store = MySqlChinaStockStore.from_env()
            if str(args.node).strip().lower() == "aplush":
                local_count = len(store.list_ah_pairs())
            else:
                local_count = len(store.list_symbol_ids(args.node))
            output = {"node": args.node, "local_count": local_count}
        else:
            updater = build_updater_from_mysql_env(args.page_size)
            output = updater.update(args.node, args.max_records).as_dict()
        print(json.dumps(output, ensure_ascii=args.ascii, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    except (RuntimeError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
