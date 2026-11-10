"""``dbtools/backfill_nav_history.py`` 的 ``--all-funds`` / ``--report`` 单测。

## 为什么钉住这几条

2026-11-09 之前，日常例程是两条**手写**的 ``--symbols <33 只>`` /
``--symbols <28 只>``，只覆盖 61 / 102 只。剩下 41 只的 ``netvaluehistory``
从来没被回填过，净值永远停在 10 月 —— 页面表现为「溢价率离谱 / 假数据」，
很容易误判成公式或持仓问题。修法是让清单只有一个出处：
``core.symbolinventory.fund_symbols()``（与 ``quotecli --all-qdii`` 同源）。

## 为什么用路径加载

``dbtools`` 是维护脚本目录、不是包（``test_layout`` 钉住它没有
``__init__.py``），所以按文件路径加载；加载期间要把 ``sys.stdout`` 换成
没有 ``buffer`` 的对象，手法同 ``test_bootstrap.py``。
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

AUTO_DIR = Path(__file__).resolve().parent
PYTHON_DIR = AUTO_DIR.parent
DBTOOLS_DIR = PYTHON_DIR / "dbtools"
SCRIPT = DBTOOLS_DIR / "backfill_nav_history.py"


def _load():
    saved = sys.stdout
    sys.stdout = io.StringIO()
    try:
        spec = importlib.util.spec_from_file_location(
            "backfill_nav_history", str(SCRIPT))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.stdout = saved
    return module


backfill = _load()


class _Cursor:
    """只认脚本会发的三条 SQL，其余返回空。"""

    def __init__(self, source):
        self.source = source
        self._rows = []

    def execute(self, sql, params=()):
        states = self.source.states
        if "JOIN stock" in sql:
            # --report 的锚点：基金最新净值日（不含 forex 写进来的中间价行）
            dates = [latest for latest, _ in states.values() if latest]
            self._rows = [{"latest": max(dates) if dates else None}]
        elif "MAX(date) AS latest" in sql:
            symbol = self.source.by_id.get(params[0])
            latest, count = states.get(symbol, (None, 0))
            self._rows = [{"latest": latest, "cnt": count}]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class _Connection:
    def __init__(self, source):
        self.source = source

    def cursor(self):
        return _Cursor(self.source)

    def close(self):
        pass


class _Source:
    """``states``：``{symbol: (latest_date_or_None, row_count)}``。"""

    def __init__(self, states):
        self.states = states
        self.by_id = {index: symbol
                      for index, symbol in enumerate(sorted(states), 1)}
        self.by_symbol = {symbol: index for index, symbol in self.by_id.items()}
        self.connection = _Connection(self)

    def get_stock_id(self, symbol):
        return self.by_symbol.get(symbol)


class ResolveSymbolsTest(unittest.TestCase):
    """清单只能来自 symbolinventory，别再抄逗号串。"""

    def test_all_funds_is_the_symbolinventory_list(self):
        from core.symbolinventory import fund_symbols

        expected = tuple(fund_symbols())
        self.assertEqual(backfill.resolve_symbols(None, True), expected)
        self.assertEqual(len(expected), 102)
        self.assertEqual(len(set(expected)), 102)

    def test_no_flags_keeps_the_single_symbol_fallback(self):
        self.assertEqual(backfill.resolve_symbols(None, False),
                         ("SZ161125",))

    def test_symbols_split_trim_and_dedup(self):
        self.assertEqual(
            backfill.resolve_symbols(" SZ161125 ,SZ161226,SZ161125, ", False),
            ("SZ161125", "SZ161226"))

    def test_all_funds_union_appends_extra_without_duplicates(self):
        result = backfill.resolve_symbols("SZ161125,QQQ", True)
        # SZ161125 本来就在 102 只里，不该被追加第二次；QQQ 是额外点名的。
        self.assertEqual(result[-1], "QQQ")
        self.assertEqual(result.count("SZ161125"), 1)
        self.assertEqual(len(result), 103)

    def test_every_fund_symbol_maps_to_a_six_digit_code(self):
        for symbol in backfill.resolve_symbols(None, True):
            self.assertTrue(symbol.startswith(("SH", "SZ")), symbol)
            self.assertEqual(len(backfill.fund_code_of(symbol)), 6, symbol)


class NavStateTest(unittest.TestCase):
    def test_reads_latest_date_and_row_count(self):
        source = _Source({"SZ161125": (date(2026, 11, 5), 37),
                          "SZ161226": (None, 0)})
        states = dict((symbol, (latest, count))
                      for symbol, _, latest, count in backfill.nav_state(
                          source, ("SZ161125", "SZ161226")))
        self.assertEqual(states["SZ161125"], (date(2026, 11, 5), 37))
        self.assertEqual(states["SZ161226"], (None, 0))

    def test_unknown_symbol_has_no_stock_id(self):
        source = _Source({"SZ161125": (date(2026, 11, 5), 1)})
        state = backfill.nav_state(source, ("NOPE",))[0]
        self.assertEqual(state, ("NOPE", None, None, 0))


class ReportTest(unittest.TestCase):
    def _report(self, states, stale_days=5):
        output = io.StringIO()
        with redirect_stdout(output):
            backfill.print_report(_Source(states), tuple(sorted(states)), stale_days)
        return output.getvalue()

    def test_anchor_comes_from_funds_not_forex_rows(self):
        text = self._report({"SZ161125": (date(2026, 11, 6), 10)})
        self.assertIn("基金最新净值日 2026-11-06", text)
        self.assertIn("没有需要追补的基金", text)

    def test_stale_fund_is_flagged_and_printed_as_pasteable_list(self):
        text = self._report({"SZ161125": (date(2026, 11, 6), 10),
                             "SZ161226": (date(2026, 10, 30), 20)})
        self.assertIn("SZ161226", text)
        self.assertIn("落后  7 天", text)
        self.assertIn("断档 1 只", text)
        self.assertIn("--symbols SZ161226 --days 40 --apply", text)

    def test_t_plus_two_disclosure_is_not_a_false_positive(self):
        text = self._report({"SZ161125": (date(2026, 11, 6), 10),
                             "SZ161226": (date(2026, 11, 3), 10)})
        self.assertIn("断档 0 只", text)

    def test_threshold_can_be_tightened(self):
        text = self._report({"SZ161125": (date(2026, 11, 6), 10),
                             "SZ161226": (date(2026, 11, 3), 10)},
                            stale_days=2)
        self.assertIn("断档 1 只", text)


class NavSymbolsTest(unittest.TestCase):
    """2026-11-10：``--all-nav`` 的 109 只边界与商品基金的可发现性。

    ``fund_symbols()`` 只管 QDII 行情/归档（102 只），不含 A 股商品基金。
    净值链路更宽：``navcli`` 的 ``collect_targets()`` 取 ``stock`` 表里所有
    6 位代码，6 只商品基金都在里面。2026-11-10 之前 ``--all-funds`` /
    ``--report`` 只认 102 只，这 6 只停在 11-03 却报「断档 0 只」。
    当天又补了第 109 只 ``SZ160632``（A 股指数 LOF），见
    ``dbtools/patch_china_index_fund_data.py``。
    """

    COMMODITY = ("SH518800", "SH518880", "SZ159934", "SZ159937",
                 "SZ159985", "SZ161226")

    def test_nav_symbols_is_109_and_extends_fund_symbols(self):
        from core.symbolinventory import fund_symbols, nav_symbols

        funds = fund_symbols()
        nav = nav_symbols()
        self.assertEqual(len(funds), 102)
        self.assertEqual(len(nav), 109)
        self.assertEqual(len(set(nav)), 109)
        self.assertEqual(nav[:len(funds)], funds)
        for symbol in self.COMMODITY:
            self.assertIn(symbol, nav)
            self.assertNotIn(symbol, funds)
        # 补齐净值链路的那一只必须在，否则它会停在补齐当天。
        self.assertIn("SZ160632", nav)
        self.assertNotIn("SZ160632", funds)
        self.assertEqual(backfill.fund_code_of("SZ160632"), "160632")

    def test_other_china_index_symbols_stay_out_of_report(self):
        """分类页其余 26 只只有 stock 行，纳入会把空壳全报成断档。"""
        from core.symbolinventory import nav_symbols
        from core.stocksymbol import china_index_get_symbol_array

        nav = set(nav_symbols())
        others = [s for s in china_index_get_symbol_array() if s != "SZ160632"]
        self.assertEqual(len(others), 26)
        for symbol in others:
            self.assertNotIn(symbol, nav, symbol)

    def test_all_nav_resolves_the_109_symbol_list(self):
        self.assertEqual(len(backfill.resolve_symbols(None, False, True)), 109)
        self.assertEqual(len(backfill.resolve_symbols(None, True, True)), 109)
        self.assertEqual(len(backfill.resolve_symbols(None, True, False)), 102)

    def test_every_nav_symbol_maps_to_a_six_digit_code(self):
        for symbol in backfill.resolve_symbols(None, False, True):
            self.assertEqual(len(backfill.fund_code_of(symbol)), 6, symbol)

    def test_report_flags_a_stale_commodity_fund(self):
        source = _Source({"SZ161125": (date(2026, 11, 9), 10),
                          "SH518800": (date(2026, 11, 3), 20)})
        output = io.StringIO()
        with redirect_stdout(output):
            backfill.print_report(source, tuple(sorted(source.states)), 5)
        text = output.getvalue()
        self.assertIn("基金最新净值日 2026-11-09", text)
        self.assertIn("SH518800", text)
        self.assertIn("2026-11-03  落后  6 天", text)
        self.assertIn("断档 1 只", text)
        self.assertIn("--symbols SH518800 --days 40 --apply", text)


class BackupDbEnvKeyTest(unittest.TestCase):
    """内置备份必须与 ``core.stockdata`` 认同一个库名键。"""

    def test_database_key_wins_over_legacy_name(self):
        if str(DBTOOLS_DIR) not in sys.path:
            sys.path.insert(0, str(DBTOOLS_DIR))
        spec = importlib.util.spec_from_file_location(
            "backup_db_for_test", str(DBTOOLS_DIR / "backup_db.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False, encoding="utf-8")
        handle.write("PALMMICRO_DB_DATABASE=real_db\n"
                     "PALMMICRO_DB_NAME=legacy_db\n"
                     "PALMMICRO_DB_PASSWORD=x\n")
        handle.close()
        # --dry-run 之前也会找 mysqldump；换机器没装时别让测试挂在这上面。
        original_finder = module.find_mysqldump
        module.find_mysqldump = lambda _="": "mysqldump"
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                code = module.main(["--env", handle.name, "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("目标库     : real_db", output.getvalue())
        finally:
            module.find_mysqldump = original_finder
            os.remove(handle.name)


if __name__ == "__main__":
    unittest.main()
