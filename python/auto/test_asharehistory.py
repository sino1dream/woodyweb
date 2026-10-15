"""``updater.asharehistory`` 的解析测试（全离线，不打网络）。

钉住三点：

1. 新浪这条给的是**裸 JSON 数组**（不是美股那条 ``IO.XSRV2.CallbackList``
   的 JSONP 壳），别拿错解析器；
2. 成交量是**字符串**，要转 int；缺值给 0 而不是让整行写坏；
3. 抓不到 / 格式不对时**返回空列表**，不抛异常 —— 调用方按"没补到"处理。
"""

import unittest

from updater import asharehistory


#: 实测响应片段（``sz161125``，字段顺序与真实返回一致）
SAMPLE = (
    '[{"day":"2026-10-12","open":"3.337","high":"3.345","low":"3.320",'
    '"close":"3.337","volume":"6992720"},'
    '{"day":"2026-10-13","open":"3.335","high":"3.340","low":"3.325",'
    '"close":"3.330","volume":"3522546"}]'
)


class ParseDailyResponseTest(unittest.TestCase):
    def test_parses_json_array_not_jsonp(self):
        rows = asharehistory.parse_daily_response(SAMPLE)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "2026-10-12")
        self.assertEqual(rows[0]["close"], 3.337)
        self.assertEqual(rows[0]["volume"], 6992720)
        # 升序：最新在最后
        self.assertEqual(rows[-1]["date"], "2026-10-13")

    def test_empty_response_yields_empty_list(self):
        for text in ("", "[]", "null", "not json"):
            self.assertEqual(asharehistory.parse_daily_response(text), [])

    def test_missing_volume_defaults_to_zero(self):
        text = '[{"day":"2026-10-14","close":"3.34"}]'
        rows = asharehistory.parse_daily_response(text)
        self.assertEqual(rows[0]["volume"], 0)

    def test_zero_close_is_dropped(self):
        """停牌那天的 ``close`` 可能是 0，写进历史表会把溢价率算爆。"""
        text = '[{"day":"2026-10-14","close":"0"},{"day":"2026-10-15","close":"3.266"}]'
        rows = asharehistory.parse_daily_response(text)
        self.assertEqual([row["date"] for row in rows], ["2026-10-15"])


class QuoteCodeTest(unittest.TestCase):
    def test_lowercases_symbol(self):
        """新浪要小写（``SZ161125`` → ``sz161125``）。"""
        self.assertEqual(asharehistory.quote_code_of("SZ161125"), "sz161125")
        self.assertEqual(asharehistory.quote_code_of("SH513100"), "sh513100")


if __name__ == "__main__":
    unittest.main()
