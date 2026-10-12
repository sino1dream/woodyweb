import unittest
from unittest.mock import patch

from source.chinastock import (
    ChinaStockUpdater,
    MemoryChinaStockStore,
    MySqlChinaStockStore,
    SinaMarketClient,
    china_stock_symbol_filter,
    main,
    sina_count_code,
    sina_data_code,
)
from source.httpjson import decode_json_content


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.raise_count = 0

    def raise_for_status(self):
        self.raise_count += 1

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.urls = []
        self.timeouts = []

    def get(self, url, timeout, headers=None):
        self.urls.append(url)
        self.timeouts.append(timeout)
        return FakeResponse(self.payloads.pop(0))


class FakeMarketClient:
    def __init__(self, count, pages):
        self.remote_count = count
        self.pages = pages
        self.calls = []

    def count(self, node):
        self.calls.append(("count", node))
        return self.remote_count

    def fetch_page(self, node, page, num):
        self.calls.append(("fetch_page", node, page, num))
        return self.pages.get(page, [])


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection
        self.closed = False

    def execute(self, sql, params=()):
        self.connection.queries.append((sql, params))

    def fetchone(self):
        if isinstance(self.connection.one, list):
            return self.connection.one.pop(0) if self.connection.one else None
        return self.connection.one

    def fetchall(self):
        return self.connection.all

    def close(self):
        self.closed = True


class RecordingConnection:
    def __init__(self, one=None, all_rows=None):
        self.queries = []
        self.one = one
        self.all = all_rows or []
        self.commits = 0

    def cursor(self):
        return RecordingCursor(self)

    def commit(self):
        self.commits += 1


class NodeMetadataTest(unittest.TestCase):
    def test_count_codes_match_php_switch(self):
        self.assertEqual(sina_count_code("hs_a"), "HQNodeStockCount")
        self.assertEqual(sina_count_code("hs_b"), "HQNodeStockCount")
        self.assertEqual(sina_count_code("hs_s"), "HQNodeStockCountSimple")
        self.assertEqual(sina_count_code("etf_hq_fund"), "HQNodeStockCountSimple")
        self.assertEqual(sina_count_code("lof_hq_fund"), "HQNodeStockCountSimple")
        self.assertEqual(sina_count_code("qbgg_hk"), "HKStockCount")
        self.assertEqual(sina_count_code("aplush"), "ANHCount")

    def test_data_codes_match_php_switch(self):
        self.assertEqual(sina_data_code("hs_a"), "HQNodeData")
        self.assertEqual(sina_data_code("hs_b"), "HQNodeData")
        self.assertEqual(sina_data_code("hs_s"), "HQNodeDataSimple")
        self.assertEqual(sina_data_code("etf_hq_fund"), "HQNodeDataSimple")
        self.assertEqual(sina_data_code("lof_hq_fund"), "HQNodeDataSimple")
        self.assertEqual(sina_data_code("qbgg_hk"), "HKStockData")
        self.assertEqual(sina_data_code("aplush"), "ANHData")

    def test_symbol_filters_match_php_sql(self):
        self.assertIn("SH6", china_stock_symbol_filter("hs_a"))
        self.assertIn("NOT REGEXP", china_stock_symbol_filter("hs_a"))
        self.assertEqual(china_stock_symbol_filter("hs_b"), "symbol REGEXP '^(SH9|SZ2)[0-9]{5}$'")
        self.assertEqual(china_stock_symbol_filter("qbgg_hk"), "symbol REGEXP '^[0-9]{5}$'")
        with self.assertRaises(ValueError):
            china_stock_symbol_filter("unknown")


class SinaMarketClientTest(unittest.TestCase):
    def test_count_uses_expected_url(self):
        session = FakeSession([42])
        client = SinaMarketClient(session=session, timeout=3)

        self.assertEqual(client.count("HS_A"), 42)
        self.assertEqual(
            session.urls,
            [
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                "Market_Center.getHQNodeStockCount?node=hs_a"
            ],
        )
        self.assertEqual(session.timeouts, [3])

    def test_fetch_page_uses_expected_url(self):
        session = FakeSession([[{"symbol": "sh600000", "name": "浦发银行"}]])
        client = SinaMarketClient(session=session)

        rows = client.fetch_page("hs_a", 2, num=50)

        self.assertEqual(rows[0]["symbol"], "sh600000")
        self.assertIn("page=2&num=50&sort=symbol&asc=1&node=hs_a", session.urls[0])

    def test_fetch_page_rejects_non_array(self):
        client = SinaMarketClient(session=FakeSession([{"symbol": "sh600000"}]))

        with self.assertRaises(RuntimeError):
            client.fetch_page("hs_a", 1)

    def test_request_failures_are_wrapped_for_cli(self):
        class RaisingSession:
            def get(self, url, timeout, headers=None):
                raise RuntimeError("network down")

        with self.assertRaises(RuntimeError) as raised:
            SinaMarketClient(session=RaisingSession()).count("hs_a")

        self.assertIn("Unable to fetch", str(raised.exception))

    def test_decode_json_content_accepts_utf8_and_gb18030(self):
        text = '[{"name": "安徽凤凰"}]'
        self.assertEqual(decode_json_content(text.encode("utf-8"))[0]["name"], "安徽凤凰")
        self.assertEqual(decode_json_content(text.encode("gb18030"))[0]["name"], "安徽凤凰")


class ChinaStockUpdaterTest(unittest.TestCase):
    def test_stock_node_writes_new_and_changed_symbols(self):
        store = MemoryChinaStockStore(stocks={"1": "SH600000"}, names={"SH600000": "旧名称"})
        client = FakeMarketClient(
            3,
            {
                1: [
                    {"symbol": "sh600000", "name": "浦发银行"},
                    {"symbol": "sz000001", "name": "平安银行"},
                    {"symbol": "sh513350", "name": "标普油气ETF"},
                ]
            },
        )

        summary = ChinaStockUpdater(client, store, page_size=2).update("hs_a")

        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.changed, 3)
        self.assertEqual(summary.deleted, 0)
        self.assertEqual(store.names["SH600000"], "浦发银行")
        self.assertEqual(store.get_stock_id("SZ000001"), "2")
        self.assertEqual(client.calls, [("count", "hs_a"), ("fetch_page", "hs_a", 1, 2)])

    def test_stock_node_deletes_unprotected_old_rows(self):
        store = MemoryChinaStockStore(
            stocks={"1": "SH600000", "2": "SZ399001"},
            names={"SH600000": "浦发银行", "SZ399001": "深证成指"},
            ab_pairs={"2": "1"},
        )
        client = FakeMarketClient(1, {1: [{"symbol": "sh600000", "name": "浦发银行"}]})

        summary = ChinaStockUpdater(client, store, page_size=100).update("hs_a")

        self.assertEqual(summary.deleted, 1)
        self.assertEqual(summary.protected, 0)
        self.assertEqual(store.deleted, ["2"])
        self.assertIsNone(store.get_stock_id("SZ399001"))
        self.assertNotIn("2", store.ab_pairs)

    def test_stock_node_keeps_rows_used_by_stock_groups(self):
        store = MemoryChinaStockStore(
            stocks={"1": "SH600000", "2": "SZ399001"},
            names={"SH600000": "浦发银行", "SZ399001": "深证成指"},
            group_items={"2": 1},
        )
        client = FakeMarketClient(1, {1: [{"symbol": "sh600000", "name": "浦发银行"}]})

        summary = ChinaStockUpdater(client, store, page_size=100).update("hs_a")

        self.assertEqual(summary.deleted, 0)
        self.assertEqual(summary.protected, 1)
        self.assertEqual(store.deleted, [])
        self.assertEqual(store.get_stock_id("SZ399001"), "2")

    def test_aplush_node_writes_and_removes_pairs(self):
        store = MemoryChinaStockStore(
            stocks={"1": "SH600000", "2": "00902", "3": "SH600001", "4": "00903", "5": "SH600002", "6": "00904"},
            ah_pairs={"1": "2", "5": "6"},
        )
        client = FakeMarketClient(
            2,
            {
                1: [
                    {"a": "sh600000", "h": "00902"},
                    {"a": "sh600001", "h": "00903"},
                ]
            },
        )

        summary = ChinaStockUpdater(client, store, page_size=1).update("aplush")

        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.pairs_changed, 1)
        self.assertEqual(summary.pairs_deleted, 1)
        self.assertEqual(store.ah_pairs, {"1": "2", "3": "4"})

    def test_remote_count_zero_does_not_access_store(self):
        class FailingStore(MemoryChinaStockStore):
            def list_symbol_ids(self, node):
                raise AssertionError("store should not be touched")

        summary = ChinaStockUpdater(FakeMarketClient(0, {}), FailingStore()).update("hs_a")

        self.assertEqual(summary.remote_count, 0)
        self.assertEqual(summary.total, 0)

    def test_max_records_limits_processed_rows(self):
        store = MemoryChinaStockStore()
        client = FakeMarketClient(
            3,
            {1: [{"symbol": "sh600000", "name": "浦发银行"}, {"symbol": "sz000001", "name": "平安银行"}]},
        )

        summary = ChinaStockUpdater(client, store, page_size=2).update("hs_a", max_records=1)

        self.assertEqual(summary.remote_count, 1)
        self.assertEqual(summary.total, 1)
        self.assertEqual(store.get_stock_id("SH600000"), "1")
        self.assertIsNone(store.get_stock_id("SZ000001"))
        self.assertEqual(client.calls, [("count", "hs_a"), ("fetch_page", "hs_a", 1, 2)])


class MySqlChinaStockStoreTest(unittest.TestCase):
    def test_list_symbol_ids_uses_php_regex_filter(self):
        connection = RecordingConnection(all_rows=[{"symbol": "SH600000", "id": "1"}])
        store = MySqlChinaStockStore(connection)

        rows = store.list_symbol_ids("hs_a")

        self.assertEqual(rows, {"SH600000": "1"})
        self.assertIn("SELECT symbol, id FROM stock WHERE symbol REGEXP", connection.queries[0][0])
        self.assertIn("ORDER BY symbol ASC", connection.queries[0][0])
        self.assertIn("NOT REGEXP", connection.queries[0][0])

    def test_delete_stock_removes_legacy_dependent_tables(self):
        connection = RecordingConnection()
        store = MySqlChinaStockStore(connection)

        store.delete_stock("7")

        sql = [query for query, params in connection.queries]
        self.assertIn("DELETE FROM stockema50 WHERE stock_id = %s", sql)
        self.assertIn("DELETE FROM stockema200 WHERE stock_id = %s", sql)
        self.assertIn("DELETE FROM stockhistory WHERE stock_id = %s", sql)
        self.assertIn("DELETE FROM netvaluehistory WHERE stock_id = %s", sql)
        self.assertIn("DELETE FROM stock WHERE id = %s LIMIT 1", sql)
        self.assertEqual(connection.queries[0][1], ("7",))
        self.assertGreaterEqual(connection.commits, 5)

    def test_delete_any_pair_checks_pair_side_before_host_side(self):
        connection = RecordingConnection(one={"total": 1})
        store = MySqlChinaStockStore(connection)

        self.assertEqual(store.delete_ab_pair_any("7"), 1)

        first_sql, first_params = connection.queries[0]
        self.assertEqual(first_sql, "SELECT COUNT(*) AS total FROM abpair WHERE stock_id = %s")
        self.assertEqual(first_params, ("7",))
        self.assertEqual(connection.queries[1][0], "DELETE FROM abpair WHERE stock_id = %s")

    def test_delete_any_pair_falls_back_to_host_side(self):
        connection = RecordingConnection(one=[{"total": 0}, {"total": 1}])
        store = MySqlChinaStockStore(connection)

        self.assertEqual(store.delete_ab_pair_any("7"), 1)

        sql = [query for query, params in connection.queries]
        self.assertIn("SELECT COUNT(*) AS total FROM abpair WHERE stock_id = %s", sql)
        self.assertIn("SELECT COUNT(*) AS total FROM abpair WHERE id = %s", sql)
        self.assertIn("DELETE FROM abpair WHERE id = %s LIMIT 1", sql)


class ChinaStockCliTest(unittest.TestCase):
    def test_check_db_reads_local_symbol_count_without_writing(self):
        class FakeStore:
            @classmethod
            def from_env(cls):
                return cls()

            def list_symbol_ids(self, node):
                return {"SH600000": "1"}

        with patch("source.chinastock.MySqlChinaStockStore", FakeStore), patch("builtins.print") as mock_print:
            self.assertEqual(main(["hs_a", "--check-db"]), 0)

        self.assertIn('"local_count": 1', mock_print.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
