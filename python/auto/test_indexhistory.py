# -*- coding: utf-8 -*-
"""``updater.indexhistory`` 的单测：两条信源的分派与解析。

重点钉住两件事：

1. **分派**：``^HSI`` / ``^HSTECH`` / ``^HSCE`` 必须走东财，其余仍走新浪。
   这条错了不会报错，只会安静地抓不到数据（新浪港股日线已下线）。
2. **东财 kline 的收盘是第 3 列**（``日期,开,收,高,低,量,额``）。取第 2 列
   会拿到开盘价 —— 价格量级看着完全正常，只有跟生产端对比才会露馅。
"""

from __future__ import annotations

import json
import unittest

from updater import indexhistory


EASTMONEY_TEXT = json.dumps({
    "rc": 0,
    "data": {
        "code": "HSI",
        "name": "恒生指数",
        "klines": [
            "2026-10-30,26346.29,23294.42,26489.79,26193.12,2581102,0",
            "2026-11-02,23301.74,23412.59,23543.30,23271.26,1999999,0",
        ],
    },
})

SINA_TEXT = ('/*<script>location.href=\'//sina.com\';</script>*/\n'
             'IO.XSRV2.CallbackList[]([{"d":"2026-10-29","o":"1","h":"2","l":"0.5",'
             '"c":"29402.51","v":"100","a":"0"},'
             '{"d":"2026-10-30","o":"1","h":"2","l":"0.5","c":"28384.85","v":"120","a":"0"}]);')


class RecordingOpener:
    """假的 opener：记下被请求的 URL，返回预设文本。"""

    def __init__(self, text=""):
        self.text = text
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return self.text


class ParseEastmoneyTest(unittest.TestCase):
    def test_close_is_the_third_column(self):
        rows = indexhistory.parse_eastmoney_response(EASTMONEY_TEXT)
        self.assertEqual([row["date"] for row in rows], ["2026-10-30", "2026-11-02"])
        # 第 2 列是开盘 26346.29，第 3 列才是收盘 23294.42
        self.assertAlmostEqual(rows[0]["close"], 23294.42)
        self.assertAlmostEqual(rows[1]["close"], 23412.59)

    def test_volume_is_fifth_column(self):
        rows = indexhistory.parse_eastmoney_response(EASTMONEY_TEXT)
        self.assertEqual(rows[0]["volume"], 2581102)

    def test_rows_are_sorted_ascending(self):
        payload = json.dumps({"data": {"klines": [
            "2026-11-02,1,300,1,1,1,0",
            "2026-10-30,1,200,1,1,1,0",
        ]}})
        rows = indexhistory.parse_eastmoney_response(payload)
        self.assertEqual([row["date"] for row in rows], ["2026-10-30", "2026-11-02"])

    def test_data_null_returns_empty(self):
        self.assertEqual(indexhistory.parse_eastmoney_response('{"rc":0,"data":null}'), [])

    def test_invalid_json_returns_empty(self):
        self.assertEqual(indexhistory.parse_eastmoney_response("<html>502</html>"), [])

    def test_zero_close_is_dropped(self):
        # 停牌/未开盘行收盘为 0，写进 stockhistory 会把均线带偏
        payload = json.dumps({"data": {"klines": [
            "2026-10-30,0,0,0,0,0,0",
            "2026-11-02,1,100,1,1,0,0",
        ]}})
        rows = indexhistory.parse_eastmoney_response(payload)
        self.assertEqual([row["date"] for row in rows], ["2026-11-02"])

    def test_short_line_is_dropped(self):
        payload = json.dumps({"data": {"klines": ["2026-10-30,1"]}})
        self.assertEqual(indexhistory.parse_eastmoney_response(payload), [])


class DispatchTest(unittest.TestCase):
    def test_hk_indices_go_eastmoney(self):
        for symbol, secid in (("^HSI", "100.HSI"),
                              ("^HSTECH", "124.HSTECH"),
                              ("^HSCE", "100.HSCEI")):
            opener = RecordingOpener(EASTMONEY_TEXT)
            indexhistory.fetch_daily_history(symbol, limit=10, opener=opener)
            self.assertEqual(len(opener.urls), 1, symbol)
            self.assertIn("push2his.eastmoney.com", opener.urls[0])
            self.assertIn("secid=" + secid, opener.urls[0])

    def test_us_index_still_uses_sina(self):
        opener = RecordingOpener(SINA_TEXT)
        indexhistory.fetch_daily_history("^NDX", opener=opener)
        self.assertIn("US_MinKService.getDailyK", opener.urls[0])
        self.assertIn("symbol=.NDX", opener.urls[0])

    def test_us_etf_still_uses_sina(self):
        opener = RecordingOpener(SINA_TEXT)
        indexhistory.fetch_daily_history("QQQ", opener=opener)
        self.assertIn("symbol=QQQ", opener.urls[0])

    def test_eastmoney_limit_is_passed_to_request(self):
        opener = RecordingOpener(EASTMONEY_TEXT)
        indexhistory.fetch_daily_history("^HSI", limit=500, opener=opener)
        self.assertIn("lmt=500", opener.urls[0])

    def test_eastmoney_limit_zero_asks_for_all(self):
        opener = RecordingOpener(EASTMONEY_TEXT)
        indexhistory.fetch_daily_history("^HSI", opener=opener)
        self.assertIn("lmt=%d" % indexhistory.EASTMONEY_ALL_LIMIT, opener.urls[0])

    def test_unknown_symbol_in_eastmoney_fetcher_returns_empty(self):
        opener = RecordingOpener(EASTMONEY_TEXT)
        self.assertEqual(indexhistory.fetch_eastmoney_history("^NDX", opener=opener), [])
        self.assertEqual(opener.urls, [])

    def test_opener_error_returns_empty(self):
        def boom(url):
            raise OSError("network down")

        for symbol in ("^HSI", "^NDX"):
            self.assertEqual(indexhistory.fetch_daily_history(symbol, opener=boom), [])

    def test_limit_keeps_the_most_recent_rows(self):
        rows = indexhistory.fetch_daily_history("^HSI", limit=1, opener=RecordingOpener(EASTMONEY_TEXT))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-11-02")


class SecidTableTest(unittest.TestCase):
    def test_hk_secids_cover_the_three_indices(self):
        self.assertEqual(set(indexhistory.EASTMONEY_HK_SECIDS),
                         {"^HSI", "^HSTECH", "^HSCE"})

    def test_hk_secids_do_not_overlap_us_indices(self):
        # 重叠会让 ^NDX 被悄悄改走东财（那边没有这条 secid，结果是抓不到）
        self.assertFalse(set(indexhistory.EASTMONEY_HK_SECIDS) & set(indexhistory.INDEX_CODES))


if __name__ == "__main__":
    unittest.main()
