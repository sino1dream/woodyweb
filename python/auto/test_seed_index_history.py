"""``dbtools/seed_index_history.py`` 默认清单必须带 ``GLD`` 的单测。

## 为什么要钉住

四只黄金基金（``SZ160719`` / ``SZ161116`` / ``SZ164701`` / ``SZ165513``）
的 ``est_symbol`` 是 GLD，由 ``fundestmap`` 表动态注入，不在
``core.symbolinventory.est_symbols()`` 里 —— daemon 不抓它的快照、
``--archive-snapshot --all-qdii`` 也搬不到它。2026-11-10 现场：GLD 日线停在
11-02、基金净值已到 11-06，``calibrate_fund()`` 按 PHP ``AdjustFactor()``
语义取不到净值日的 est 就不写校准行，``calibrationhistory`` 永远停在 10-30，
页面「公共参数」的 ``est_cal`` / ``cny_cal`` 也就一直显示 10-30。

修法是把 GLD 放进每日 ``--days 5 --apply`` 的默认清单。这里钉住三件事：
默认带它、显式 ``--symbols`` 不受污染、以及"它确实不在抓取清单里"这个前提。

## 为什么用路径加载

``dbtools`` 不是包（``test_layout`` 钉住它没有 ``__init__.py``），按文件路径
加载；加载期间要把 ``sys.stdout`` 换成没有 ``buffer`` 的对象，手法同
``test_bootstrap.py`` / ``test_backfill_nav_history.py``。
"""

from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from pathlib import Path

AUTO_DIR = Path(__file__).resolve().parent
PYTHON_DIR = AUTO_DIR.parent
SCRIPT = PYTHON_DIR / "dbtools" / "seed_index_history.py"


def _load():
    saved = sys.stdout
    sys.stdout = io.StringIO()
    try:
        spec = importlib.util.spec_from_file_location(
            "seed_index_history", str(SCRIPT))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.stdout = saved
    return module


seed = _load()


class DefaultTargetsTest(unittest.TestCase):
    """默认清单与显式点名两条路径。"""

    def test_default_targets_keep_eight_indexes_and_add_gld(self):
        targets = seed.resolve_targets(["seed_index_history.py", "--days", "5"])

        self.assertEqual(8, len(seed.TARGETS))
        self.assertEqual(9, len(targets))
        self.assertEqual(seed.TARGETS, targets[:8])
        self.assertEqual("GLD", targets[-1])
        self.assertEqual(len(targets), len(set(targets)))

    def test_gld_is_not_duplicated_when_targets_are_extended(self):
        """GLD 只在附加常量里出现一次，不能既进 TARGETS 又进附加项。"""
        self.assertEqual(["GLD"], list(seed.DEFAULT_EXTRA_SYMBOLS))
        self.assertNotIn("GLD", seed.TARGETS)

    def test_explicit_symbols_do_not_get_the_default_extra(self):
        targets = seed.resolve_targets(
            ["seed_index_history.py", "--symbols", "IEO,QQQ", "--apply"])

        self.assertEqual(["IEO", "QQQ"], targets)
        self.assertNotIn("GLD", targets)
        self.assertNotIn("^NDX", targets)

    def test_explicit_symbols_accept_space_separated_values(self):
        targets = seed.resolve_targets(
            ["seed_index_history.py", "--symbols", "^HSI", "^HSTECH", "--apply"])

        self.assertEqual(["^HSI", "^HSTECH"], targets)


class WhyGldNeedsTheDefaultListTest(unittest.TestCase):
    """钉住根因：GLD 确实不在任何自动抓取清单里。"""

    def test_gld_is_absent_from_sina_quote_symbols(self):
        from core.symbolinventory import est_symbols, sina_quote_symbols

        self.assertNotIn("GLD", est_symbols())
        self.assertNotIn("GLD", sina_quote_symbols())

    def test_gold_funds_only_enter_the_fund_map_via_db_override(self):
        """硬编码的地区数组只有 69 只；连库注入 fundestmap 后才变成 73 只。"""
        from calc import fundcalc

        gold = ("SZ160719", "SZ161116", "SZ164701", "SZ165513")
        self.addCleanup(fundcalc.set_extra_est_symbols, None)
        fundcalc.set_extra_est_symbols(None)
        hardcoded = fundcalc.qdii_fund_est_symbol_map()
        self.assertEqual(69, len(hardcoded))
        for symbol in gold:
            self.assertNotIn(symbol, hardcoded)

        fundcalc.set_extra_est_symbols(dict.fromkeys(gold, "GLD"))
        with_override = fundcalc.qdii_fund_est_symbol_map()
        self.assertEqual(73, len(with_override))
        for symbol in gold:
            self.assertEqual("GLD", with_override[symbol])


if __name__ == "__main__":
    unittest.main()
