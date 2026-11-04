# -*- coding: utf-8 -*-
"""A股商品（``/category/chinafuture``）链路的单元测试。

覆盖三处容易回退的地方，都是 2026-11-04 接通时确认过的：

1. **汇率那一档必须是 ``None``**（``qdii_est_cny_symbol``）：国内商品期货与
   基金同为本币计价，PHP 对应 ``fundpairref.php:17`` 的**空分支**，
   ``GetDefaultCny()`` 给 1.0。退化成 ``USCNY`` 会让 ``cal`` 大 6.7 倍。
2. **均价的口子只对非黄金开放**（``is_cn_future_except_gold``）：PHP
   ``IsSinaFutureExceptGoldCN()`` 把 ``nf_AU0`` 排除在外，黄金按收盘价计。
3. **期货日线的 ``adjclose`` 是结算价**（``parse_cn_future_response``）：
   PHP ``_updateStockHistory()`` 把 ``strSettlePrice`` 写进那一列，
   ``DailyCalibration()`` 读的也是它 —— 写成收盘价会让 cal 偏 3%。

跑法::

    cd python/auto && D:/python/python.exe -B -m unittest test_china_future
"""

from __future__ import annotations

import unittest

from calc.fundcalc import qdii_est_cny_symbol
from calc.fundestcalc import EstimateCalculator, is_cn_future_except_gold
from core.stockdata import DailyRecord
from updater.asharehistory import (
    cn_future_code_of,
    parse_cn_future_response,
)


class CnySymbolTest(unittest.TestCase):
    """``nf_`` 前缀不配汇率。"""

    def test_cn_future_has_no_cny(self):
        for symbol in ("nf_AU0", "nf_AG0", "nf_M0"):
            self.assertIsNone(qdii_est_cny_symbol(symbol), symbol)

    def test_others_keep_default(self):
        # 美股族仍用美元中间价，港日欧各归各
        self.assertEqual(qdii_est_cny_symbol("^NDX"), "USCNY")
        self.assertEqual(qdii_est_cny_symbol("^HSI"), "HKCNY")
        self.assertEqual(qdii_est_cny_symbol("znb_NKY"), "JPCNY")
        self.assertEqual(qdii_est_cny_symbol("znb_DAX"), "EUCNY")
        # 空符号走默认（老行为不变）
        self.assertEqual(qdii_est_cny_symbol(None), "USCNY")

    def test_prefix_is_case_insensitive(self):
        """库里存的是大写，但 URL 传进来可能被规范化，别因为大小写漏判。"""
        self.assertIsNone(qdii_est_cny_symbol("NF_M0"))


class ExceptGoldTest(unittest.TestCase):
    """PHP ``IsSinaFutureExceptGoldCN()`` 的等价实现。"""

    def test_non_gold_futures_use_vwap(self):
        self.assertTrue(is_cn_future_except_gold("nf_M0"))
        self.assertTrue(is_cn_future_except_gold("nf_AG0"))

    def test_gold_is_excluded(self):
        # 黄金 ETF 的净值按收盘价计，用均价反而错（PHP 也是这样排除的）
        self.assertFalse(is_cn_future_except_gold("nf_AU0"))
        self.assertFalse(is_cn_future_except_gold("NF_AU0"))

    def test_non_futures_are_false(self):
        for symbol in ("^NDX", "GLD", "hf_GC", "SH518800", "", None):
            self.assertFalse(is_cn_future_except_gold(symbol), symbol)


class VwapPickTest(unittest.TestCase):
    """``_official_est_value()`` 何时取均价。"""

    class _Source:
        """只提供 ``get_est_quote`` 的最小数据源。"""

        def __init__(self, record, fallback_close=3112.0):
            self._record = record
            #: ``_official_est_value()`` 在均价不可用时会退到"净值 → 行情价"，
            #: 这里给的就是那一级的值（对黄金是收盘价，对豆粕是现价）。
            self._fallback_close = fallback_close

        def get_est_quote(self, stock_id):
            return self._record

        def get_stock_id(self, symbol):
            return "9"

        def get_netvalue_close(self, stock_id, date):
            return None

        def get_history_close(self, stock_id, date):
            return self._fallback_close

    def _calc(self, record, fallback_close=3112.0):
        return EstimateCalculator(self._Source(record, fallback_close), store=None)

    def test_vwap_wins_for_meal(self):
        record = DailyRecord(date="2026-11-04", close=3112.0, vwap=3119.003)
        value = self._calc(record)._official_est_value("nf_M0", "2026-11-04")
        self.assertAlmostEqual(value, 3119.003)

    def test_gold_ignores_vwap(self):
        """沪金照常走行情价（收盘），不受均价影响。"""
        record = DailyRecord(date="2026-11-04", close=848.5, vwap=848.383)
        value = self._calc(record, fallback_close=848.5)._official_est_value(
            "nf_AU0", "2026-11-04")
        self.assertAlmostEqual(value, 848.5)

    def test_stale_vwap_is_ignored(self):
        """快照停在昨天时不能拿它当今天的均价 —— 与 cal 混日同一类错误。"""
        record = DailyRecord(date="2026-11-03", close=3147.0, vwap=3140.0)
        value = self._calc(record)._official_est_value("nf_M0", "2026-11-04")
        self.assertAlmostEqual(value, 3112.0)   # 退回行情价

    def test_missing_vwap_falls_back(self):
        record = DailyRecord(date="2026-11-04", close=3112.0)
        value = self._calc(record)._official_est_value("nf_M0", "2026-11-04")
        self.assertAlmostEqual(value, 3112.0)


class FutureHistoryParseTest(unittest.TestCase):
    """新浪国内期货日线解析。"""

    SAMPLE = (
        '/*<script>location.href=\'//sina.com\';</script>*/var _AU0=(['
        '{"d":"2026-11-02","o":"849.080","h":"849.280","l":"831.000",'
        '"c":"836.760","v":"325596","p":"170202","s":"839.180"},'
        '{"d":"2026-11-03","o":"835.700","h":"851.400","l":"834.720",'
        '"c":"841.800","v":"271886","p":"160467","s":"846.120"}]);'
    )

    def test_parse_keeps_settle_as_adjclose(self):
        rows = parse_cn_future_response(self.SAMPLE)
        self.assertEqual(len(rows), 2)
        last = rows[-1]
        self.assertEqual(last["date"], "2026-11-03")
        self.assertAlmostEqual(last["close"], 841.8)
        # 结算价 → adjclose（PHP WriteHistory 的第 5 个参数）
        self.assertAlmostEqual(last["adjclose"], 846.12)
        self.assertEqual(last["volume"], 271886)

    def test_bad_input_returns_empty(self):
        self.assertEqual(parse_cn_future_response(""), [])
        self.assertEqual(parse_cn_future_response("no array here"), [])
        self.assertEqual(parse_cn_future_response("[]"), [])

    def test_code_of(self):
        self.assertEqual(cn_future_code_of("nf_AU0"), "AU0")
        self.assertEqual(cn_future_code_of("NF_M0"), "M0")
        # 没有前缀时原样大写
        self.assertEqual(cn_future_code_of("ag0"), "AG0")


if __name__ == "__main__":
    unittest.main()
