# -*- coding: utf-8 -*-
"""``dbtools/patch_china_index_*`` 两个补丁脚本的**纯逻辑**单测。

## 为什么只测纯函数

两个脚本的 ``main()`` 都连库 + 联网（东财净值 / 新浪行情），不能进单测。
但它们各自有几处「一改就静悄悄出事」的决策，必须钉住：

* ``patch_china_index_symbols.stock_rows()``：生产端名称表与
  ``china_index_get_symbol_array()`` **逐位对应**。数组增删而名称表没跟上时，
  报错好过把「煤炭LOF」写到「白酒基金LOF」头上；
* ``patch_china_index_fund_data.DEFAULT_SYMBOLS``：默认**只补
  ``SZ160632``**。用户只点名这一只，脚本不能顺手给分类页 26 只空壳灌数据；
* ``plan_nav_rows()``：净值**只补不覆盖**，与 ``backfill_nav_history.py``
  同口径，别把库里手工核准过的值改掉。

## 为什么用路径加载

``dbtools`` 不是包（``test_layout`` 钉住它没有 ``__init__.py``），按文件路径
加载；加载期间把 ``sys.stdout`` 换成没有 ``buffer`` 的对象，避免被 import 的
脚本在模块顶层包 ``TextIOWrapper`` 时把共用底层 buffer 关掉
（手法同 ``test_backfill_nav_history.py``）。
"""

from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from datetime import date
from pathlib import Path

AUTO_DIR = Path(__file__).resolve().parent
PYTHON_DIR = AUTO_DIR.parent
DBTOOLS_DIR = PYTHON_DIR / "dbtools"


def _load(name, filename):
    saved = sys.stdout
    sys.stdout = io.StringIO()
    try:
        spec = importlib.util.spec_from_file_location(
            name, str(DBTOOLS_DIR / filename))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.stdout = saved
    return module


symbols_patch = _load("patch_china_index_symbols", "patch_china_index_symbols.py")
data_patch = _load("patch_china_index_fund_data", "patch_china_index_fund_data.py")


class StockRowsTest(unittest.TestCase):
    """生产端 27 个名称必须与分类页数组逐位对应。"""

    def test_rows_match_the_category_array(self):
        from core.stocksymbol import china_index_get_symbol_array

        rows = symbols_patch.stock_rows()
        self.assertEqual(len(rows), 27)
        self.assertEqual([symbol for symbol, _name in rows],
                         list(china_index_get_symbol_array()))
        self.assertEqual(len({symbol for symbol, _name in rows}), 27)

    def test_sz160632_keeps_the_production_name(self):
        names = dict(symbols_patch.stock_rows())
        self.assertEqual(names["SZ160632"], "酒LOF")
        self.assertEqual(names["SH501043"], "沪深300LOF")
        self.assertEqual(names["SZ167301"], "保险主题LOF")

    def test_name_table_length_guard(self):
        """名称表与数组不等长时 ``stock_rows()`` 应炸，而不是错位配对。"""
        saved = symbols_patch.PRODUCTION_NAMES
        try:
            symbols_patch.PRODUCTION_NAMES = saved[:-1]
            with self.assertRaises(RuntimeError):
                symbols_patch.stock_rows()
        finally:
            symbols_patch.PRODUCTION_NAMES = saved

    def test_resolve_rows_subset_keeps_array_order(self):
        rows = symbols_patch.resolve_rows("SZ160639,SH501043")
        self.assertEqual([symbol for symbol, _name in rows],
                         ["SH501043", "SZ160639"])

    def test_resolve_rows_rejects_unknown_symbol(self):
        with self.assertRaises(SystemExit):
            symbols_patch.resolve_rows("QQQ")

    def test_resolve_rows_default_is_the_whole_array(self):
        self.assertEqual(len(symbols_patch.resolve_rows(None)), 27)


class FundDataScopeTest(unittest.TestCase):
    """默认只补用户点名的那一只，别把范围悄悄放大。"""

    def test_default_is_only_sz160632(self):
        self.assertEqual(data_patch.DEFAULT_SYMBOLS, ("SZ160632",))
        self.assertEqual(data_patch.resolve_symbols(None), ("SZ160632",))
        self.assertEqual(data_patch.resolve_symbols(""), ("SZ160632",))

    def test_resolve_symbols_dedupes_and_keeps_order(self):
        self.assertEqual(
            data_patch.resolve_symbols(" SZ160639 ,SZ160632,SZ160639,"),
            ("SZ160639", "SZ160632"))

    def test_default_does_not_include_the_other_26(self):
        from core.stocksymbol import china_index_get_symbol_array

        default = set(data_patch.DEFAULT_SYMBOLS)
        others = [s for s in china_index_get_symbol_array() if s != "SZ160632"]
        self.assertEqual(len(others), 26)
        self.assertTrue(set(others).isdisjoint(default))

    def test_days_defaults_cover_the_known_source_limits(self):
        # 东财 lsjz 的 pageSize 上限约 20 条；多给几天是为了覆盖缺页。
        self.assertEqual(data_patch.DEFAULT_NAV_DAYS, 40)
        self.assertEqual(data_patch.DEFAULT_HISTORY_DAYS, 30)


class PlanNavRowsTest(unittest.TestCase):
    """净值只补不覆盖：已有日期一律跳过。"""

    def test_skips_dates_already_in_db(self):
        rows = [
            {"date": "2026-11-09", "nav": "0.2800"},
            {"date": "2026-11-06", "nav": "0.2820"},
            {"date": "2026-11-05", "nav": "0.2837"},
        ]
        planned = data_patch.plan_nav_rows(rows, {"2026-11-09", "2026-11-05"})
        self.assertEqual([row["date"] for row in planned], ["2026-11-06"])

    def test_existing_dates_may_be_date_objects(self):
        rows = [{"date": "2026-11-09", "nav": "0.2800"},
                {"date": "2026-11-06", "nav": "0.2820"}]
        planned = data_patch.plan_nav_rows(rows, [date(2026, 11, 9)])
        self.assertEqual([row["date"] for row in planned], ["2026-11-06"])

    def test_nothing_planned_when_fully_populated(self):
        rows = [{"date": "2026-11-09", "nav": "0.2800"}]
        self.assertEqual(data_patch.plan_nav_rows(rows, ["2026-11-09"]), [])

    def test_does_not_mutate_input(self):
        rows = [{"date": "2026-11-09", "nav": "0.2800"}]
        existing = {"2026-11-09"}
        data_patch.plan_nav_rows(rows, existing)
        self.assertEqual(len(rows), 1)
        self.assertEqual(existing, {"2026-11-09"})


if __name__ == "__main__":
    unittest.main()
