import json
import unittest
from unittest.mock import patch

from source.chinamoney import (
    CURRENCY_SYMBOL_MAP,
    ChinaMoneyClient,
    ChinaMoneySnapshot,
    ChinaMoneyUpdater,
    MemoryChinaMoneyStore,
    MySqlChinaMoneyStore,
    main,
)


REMOTE_DATE = "2026-09-28"


def make_snapshot():
    return ChinaMoneySnapshot(
        remote_date=REMOTE_DATE,
        last_date_raw="2026-09-28 9:15",
        records=(
            {"vrtEName": "USD/CNY", "price": "6.7399"},
            {"vrtEName": "EUR/CNY", "price": "7.6564"},
            {"vrtEName": "100JPY/CNY", "price": "4.2685"},
            {"vrtEName": "HKD/CNY", "price": "0.85923"},
            {"vrtEName": "GBP/CNY", "price": "8.9007"},
        ),
    )


class FakeClient:
    def __init__(self, snapshot=None, timeout=20.0):
        self.snapshot = snapshot or make_snapshot()
        self.timeout = timeout
        self.calls = 0

    def fetch(self):
        self.calls += 1
        return self.snapshot


class FakeResponse:
    def __init__(self, content=None, payload=None):
        self.content = content
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, timeout, headers=None):
        self.calls.append((url, timeout, headers))
        return self.response


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 1

    def execute(self, sql, params=()):
        self.connection.queries.append((sql, params))

    def fetchone(self):
        return self.connection.one

    def close(self):
        pass


class RecordingConnection:
    def __init__(self, one=None):
        self.queries = []
        self.one = one
        self.commits = 0

    def cursor(self):
        return RecordingCursor(self)

    def commit(self):
        self.commits += 1


class ChinaMoneyClientTest(unittest.TestCase):
    def test_fetch_parses_last_date_and_records(self):
        payload = json.dumps(
            {
                "data": {"lastDate": "2026-09-28 9:15"},
                "records": [
                    {"vrtEName": "USD/CNY", "price": "6.7399"},
                    {"vrtEName": "EUR/CNY", "price": "7.6564"},
                ],
            }
        ).encode("utf-8")
        session = FakeSession(FakeResponse(content=payload))

        snapshot = ChinaMoneyClient(session=session).fetch()

        self.assertEqual(snapshot.remote_date, "2026-09-28")
        self.assertEqual(snapshot.last_date_raw, "2026-09-28 9:15")
        self.assertEqual(len(snapshot.records), 2)
        self.assertIn("User-Agent", session.calls[0][2])

    def test_fetch_rejects_non_object_payload(self):
        session = FakeSession(FakeResponse(content=b"[]"))

        with self.assertRaises(RuntimeError):
            ChinaMoneyClient(session=session).fetch()

    def test_fetch_rejects_invalid_remote_date(self):
        payload = b'{"data": {"lastDate": "bad"}, "records": []}'
        session = FakeSession(FakeResponse(content=payload))

        with self.assertRaises(RuntimeError):
            ChinaMoneyClient(session=session).fetch()


class ChinaMoneyUpdaterTest(unittest.TestCase):
    def test_maps_and_inserts_missing_currencies(self):
        store = MemoryChinaMoneyStore(
            stocks={"1": "USCNY", "2": "EUCNY", "3": "JPCNY", "4": "HKCNY"}
        )
        client = FakeClient()

        summary = ChinaMoneyUpdater(client, store).update(reference_date="2026-09-25", hour_minute=1000)

        self.assertEqual(summary.records, 5)
        self.assertEqual(summary.mapped, 4)
        self.assertEqual(summary.inserted, 4)
        self.assertEqual(summary.missing_symbols, ())
        self.assertEqual(store.netvalue[("1", REMOTE_DATE)], 6.7399)
        self.assertEqual(store.netvalue[("4", REMOTE_DATE)], 0.85923)

    def test_insert_only_keeps_existing_value(self):
        store = MemoryChinaMoneyStore(
            stocks={"1": "USCNY", "2": "EUCNY", "3": "JPCNY", "4": "HKCNY"},
            netvalue={("1", REMOTE_DATE): 6.0},
        )
        client = FakeClient()

        summary = ChinaMoneyUpdater(client, store).update(reference_date="2026-09-25", hour_minute=1000)

        self.assertEqual(summary.inserted, 3)
        self.assertEqual(summary.skipped_existing, 1)
        self.assertEqual(store.netvalue[("1", REMOTE_DATE)], 6.0)
        self.assertNotIn(("1", REMOTE_DATE, 6.7399), store.updated)

    def test_overwrite_updates_existing_value(self):
        store = MemoryChinaMoneyStore(
            stocks={"1": "USCNY", "2": "EUCNY", "3": "JPCNY", "4": "HKCNY"},
            netvalue={("1", REMOTE_DATE): 6.0},
        )
        client = FakeClient()

        summary = ChinaMoneyUpdater(client, store, overwrite=True).update(
            reference_date=REMOTE_DATE,
            hour_minute=1000,
        )

        self.assertEqual(summary.updated, 1)
        self.assertEqual(summary.inserted, 3)
        self.assertEqual(store.netvalue[("1", REMOTE_DATE)], 6.7399)
        self.assertEqual(store.updated, [("1", REMOTE_DATE, 6.7399)])

    def test_overwrite_counts_unchanged_values(self):
        store = MemoryChinaMoneyStore(
            stocks={"1": "USCNY", "2": "EUCNY", "3": "JPCNY", "4": "HKCNY"},
            netvalue={("1", REMOTE_DATE): 6.7399},
        )
        client = FakeClient()

        summary = ChinaMoneyUpdater(client, store, overwrite=True).update(
            reference_date=REMOTE_DATE,
            hour_minute=1000,
        )

        self.assertEqual(summary.updated, 0)
        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(summary.inserted, 3)

    def test_before_0915_skips_fetch(self):
        store = MemoryChinaMoneyStore(stocks={"1": "USCNY"})
        client = FakeClient()

        summary = ChinaMoneyUpdater(client, store).update(reference_date="2026-09-25", hour_minute=900)

        self.assertEqual(summary.skipped_reason, "before 09:15")
        self.assertEqual(client.calls, 0)

    def test_force_bypasses_time_gate(self):
        store = MemoryChinaMoneyStore(
            stocks={"1": "USCNY", "2": "EUCNY", "3": "JPCNY", "4": "HKCNY"}
        )
        client = FakeClient()

        summary = ChinaMoneyUpdater(client, store, force=True).update(reference_date="2026-09-25", hour_minute=900)

        self.assertEqual(summary.inserted, 4)
        self.assertTrue(summary.forced)
        self.assertEqual(client.calls, 1)

    def test_reference_date_existing_skips_fetch_without_overwrite(self):
        store = MemoryChinaMoneyStore(
            stocks={"1": "USCNY", "2": "EUCNY", "3": "JPCNY", "4": "HKCNY"},
            netvalue={("1", "2026-09-25"): 6.7},
        )
        client = FakeClient()

        summary = ChinaMoneyUpdater(client, store).update(reference_date="2026-09-25", hour_minute=1000)

        self.assertEqual(summary.skipped_reason, "reference date already exists")
        self.assertEqual(client.calls, 0)

    def test_overwrite_bypasses_reference_date_precheck(self):
        store = MemoryChinaMoneyStore(
            stocks={"1": "USCNY", "2": "EUCNY", "3": "JPCNY", "4": "HKCNY"},
            netvalue={("1", REMOTE_DATE): 6.0},
        )
        client = FakeClient()

        summary = ChinaMoneyUpdater(client, store, overwrite=True).update(
            reference_date=REMOTE_DATE,
            hour_minute=1000,
        )

        self.assertEqual(client.calls, 1)
        self.assertEqual(summary.updated, 1)

    def test_missing_stock_id_is_reported(self):
        store = MemoryChinaMoneyStore(stocks={"1": "USCNY", "2": "EUCNY"})
        client = FakeClient()

        summary = ChinaMoneyUpdater(client, store).update(reference_date="2026-09-25", hour_minute=1000)

        self.assertEqual(summary.inserted, 2)
        self.assertEqual(summary.skipped_missing, 2)
        self.assertEqual(summary.missing_symbols, ("JPCNY", "HKCNY"))

    def test_dry_run_does_not_write(self):
        store = MemoryChinaMoneyStore(
            stocks={"1": "USCNY", "2": "EUCNY", "3": "JPCNY", "4": "HKCNY"}
        )
        client = FakeClient()

        summary = ChinaMoneyUpdater(client, store).update(
            reference_date="2026-09-25",
            hour_minute=1000,
            dry_run=True,
        )

        self.assertEqual(summary.inserted, 4)
        self.assertEqual(store.netvalue, {})
        self.assertEqual(store.inserted, [])


class MySqlChinaMoneyStoreTest(unittest.TestCase):
    def test_get_stock_id_and_netvalue(self):
        connection = RecordingConnection(one={"id": "1"})
        store = MySqlChinaMoneyStore(connection)

        self.assertEqual(store.get_stock_id("USCNY"), "1")
        self.assertEqual(connection.queries[0][0], "SELECT id FROM stock WHERE symbol = %s LIMIT 1")

        connection.one = {"close": 6.7399}
        self.assertEqual(store.get_netvalue("1", REMOTE_DATE), 6.7399)
        self.assertIn("SELECT close FROM netvaluehistory", connection.queries[1][0])

    def test_insert_and_update_netvalue_sql(self):
        connection = RecordingConnection()
        store = MySqlChinaMoneyStore(connection)

        store.insert_netvalue("1", REMOTE_DATE, 6.7399)
        store.update_netvalue("1", REMOTE_DATE, 6.74)

        self.assertEqual(
            connection.queries[0],
            (
                "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s)",
                ("1", REMOTE_DATE, 6.7399),
            ),
        )
        self.assertEqual(
            connection.queries[1],
            (
                "UPDATE netvaluehistory SET close = %s WHERE stock_id = %s AND date = %s LIMIT 1",
                (6.74, "1", REMOTE_DATE),
            ),
        )
        self.assertEqual(connection.commits, 2)


class ChinaMoneyCliTest(unittest.TestCase):
    def test_check_prints_mapped_rows_without_mysql(self):
        with patch("source.chinamoney.ChinaMoneyClient", FakeClient), patch("builtins.print") as mock_print:
            self.assertEqual(main(["--check"]), 0)

        output = json.loads(mock_print.call_args[0][0])
        self.assertEqual(output["remote_date"], REMOTE_DATE)
        self.assertEqual(output["mapped"]["USCNY"], 6.7399)
        self.assertEqual(output["mapped"]["HKCNY"], 0.85923)

    def test_check_db_prints_stock_ids_without_writing(self):
        class FakeStore:
            @classmethod
            def from_env(cls):
                return cls()

            def get_stock_id(self, symbol):
                return {"USCNY": "1", "EUCNY": "2", "JPCNY": "3", "HKCNY": "4"}.get(symbol)

            def get_netvalue(self, stock_id, date):
                return None

        with patch("source.chinamoney.ChinaMoneyClient", FakeClient), patch("source.chinamoney.MySqlChinaMoneyStore", FakeStore), patch(
            "builtins.print"
        ) as mock_print:
            self.assertEqual(main(["--check-db"]), 0)

        output = json.loads(mock_print.call_args[0][0])
        self.assertEqual(output["stock"]["USCNY"]["stock_id"], "1")
        self.assertIsNone(output["stock"]["USCNY"]["existing"])


if __name__ == "__main__":
    unittest.main()
