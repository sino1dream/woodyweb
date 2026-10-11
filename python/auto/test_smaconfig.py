"""SMA 缓存的**文件**后端（``smaconfig.py``）的测试。

对应 ``php/stockhis.php:365 _configSMA()`` 里那个 ``INIFile``。
``stockhistory.py`` 被 ``ModuleIsImportCleanTest`` 钉着不许出现 ``open(``，
所以落盘单独成模块，测试也单独成文件。

分四块：

1. ``PathTest`` —— 路径规则与 PHP 的 ``DebugGetConfigFileName`` 一致；
2. ``RoundTripTest`` —— 与 ``php/class/ini_file.php`` 互通，且保留未知段；
3. ``ConcurrencyTest`` —— 原子替换：读不到半个文件、临时文件不留残渣；
4. ``NoWorkspacePollutionTest`` —— **最重要的一块**：不显式给路径就绝不落盘。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import smaconfig as sc
import stockhistory as sh
from test_stockhistory import _build as _build_history


def _read(path: str) -> str:
    """读文本文件（带关闭，免得 ResourceWarning 刷屏）。"""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class _TempDirCase(unittest.TestCase):
    """每个用例一个临时目录，用完删掉 —— 工作区里不留任何东西。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="smacfg-")
        self.addCleanup(shutil.rmtree, self.root, True)


class PathTest(unittest.TestCase):
    """``DebugGetConfigFileName()``：``<root>/config/<符号>.txt``。"""

    def test_symbol_is_lowercased_and_sanitized(self):
        """``str_replace(array('/', '+', ',', '^', '.', ':', '%'), '_', $str)``。"""
        from stockref import config_file_name

        self.assertEqual(config_file_name("^NDX", "root"),
                         os.path.join("root", "config", "_ndx.txt"))
        self.assertEqual(config_file_name("hf_NQ", "root"),
                         os.path.join("root", "config", "hf_nq.txt"))
        self.assertEqual(config_file_name("SZ161130", "root"),
                         os.path.join("root", "config", "sz161130.txt"))

    def test_unsafe_characters_all_become_underscore(self):
        from stockref import config_file_name

        for symbol, stem in (("A/B", "a_b"), ("A+B", "a_b"), ("A,B", "a_b"),
                             ("A^B", "a_b"), ("A.B", "a_b"), ("A:B", "a_b"),
                             ("A%B", "a_b")):
            self.assertEqual(config_file_name(symbol, "r"),
                             os.path.join("r", "config", stem + ".txt"))

    def test_factory_uses_the_same_rule(self):
        store = sc.config_sma_store("^NDX", root="root")
        self.assertEqual(store.path, os.path.join("root", "config", "_ndx.txt"))

    def test_symbol_can_be_a_reference_object(self):
        """``_symbol_of`` 认 ``get_symbol()`` —— 不需要 ``MyStockReference``。"""

        class Ref:
            def get_symbol(self):
                return "^GSPC"

        store = sc.config_sma_store(Ref(), root="root")
        self.assertEqual(store.path, os.path.join("root", "config", "_gspc.txt"))

    def test_explicit_path_wins(self):
        store = sc.config_sma_store(path="somewhere/out.ini")
        self.assertEqual(store.path, "somewhere/out.ini")


class RoundTripTest(_TempDirCase):
    """与 PHP ``INIFile`` 的文件格式互通。"""

    def test_written_file_is_php_readable(self):
        """输出必须是 ``[SMA]\\n键=值\\n`` —— PHP 那边直接能读。"""
        store = sc.ConfigSmaFileStore(os.path.join(self.root, "_ndx.txt"))
        store.set_var("SMA", "Date", "2026-10-09")
        store.set_var("SMA", "D5", "100.5")
        self.assertTrue(store.save_data())

        with open(store.path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "[SMA]\nDate=2026-10-09\nD5=100.5\n")

    def test_read_back_a_php_written_file(self):
        """拿 PHP 手写的格式喂进去，应当原样解析出来。"""
        path = os.path.join(self.root, "_ndx.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("[SMA]\nDate=2026-10-09\nD5=100.5\nDBOLLUP=101.25\n")

        store = sc.ConfigSmaFileStore(path)
        self.assertTrue(store.has_section("SMA"))
        self.assertEqual(store.read_var("SMA", "Date"), "2026-10-09")
        self.assertEqual(store.read_var("SMA", "DBOLLUP"), "101.25")

    def test_unknown_sections_survive_a_write(self):
        """PHP 的 ``save_data()`` 写**全部**段；丢掉别人写的段不是我们的权利。"""
        path = os.path.join(self.root, "_ndx.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("[OTHER]\nfoo=bar\n[SMA]\nDate=2026-10-09\n")

        store = sc.ConfigSmaFileStore(path)
        store.set_var("SMA", "D5", "9.9")
        store.save_data()

        again = sc.ConfigSmaFileStore(path)
        self.assertEqual(again.read_var("OTHER", "foo"), "bar")
        self.assertEqual(again.read_var("SMA", "D5"), "9.9")

    def test_missing_file_means_no_cache(self):
        """文件不存在 = 没有缓存（PHP 也是"第一次就重算"），**不抛**。"""
        store = sc.ConfigSmaFileStore(os.path.join(self.root, "nope.txt"))
        self.assertFalse(store.has_section("SMA"))
        self.assertIsNone(store.read_var("SMA", "D5"))

    def test_corrupt_file_is_treated_as_no_cache(self):
        """读不出/格式坏 → 当没有缓存，错误记在 ``last_error`` 上。"""
        path = os.path.join(self.root, "_ndx.txt")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe[SMA]\nDate=2026-10-09\n")

        store = sc.ConfigSmaFileStore(path)
        self.assertFalse(store.has_section("SMA"))
        self.assertIsNotNone(store.last_error, "坏了要留痕，不能静默")

    def test_lines_before_any_section_go_to_the_empty_section(self):
        """PHP 的 ``$CURRENT_GROUP`` 初值是 ``''``，段头之前的行归它。"""
        store = sc.ConfigSmaFileStore(os.path.join(self.root, "_ndx.txt"))
        self.assertFalse(store.has_section("SMA"))

    def test_snapshot_matches_the_memory_shape(self):
        store = sc.ConfigSmaFileStore(os.path.join(self.root, "_ndx.txt"))
        store.set_var("SMA", "D5", "1.0")
        memory = sh.ConfigSmaStore()
        memory.set_var("SMA", "D5", "1.0")
        self.assertEqual(store.snapshot(), memory.snapshot())

    def test_php_aliases_exist(self):
        """与内存实现同名的 camelCase 别名，方便按 PHP 原名调用。"""
        store = sc.ConfigSmaFileStore(os.path.join(self.root, "_ndx.txt"))
        store.SetVar("SMA", "D5", "1.0")
        self.assertTrue(store.GroupExists("SMA"))
        self.assertEqual(store.ReadVar("SMA", "D5"), "1.0")


class ConcurrencyTest(_TempDirCase):
    """原子替换：读者永远看不到半个文件。"""

    def test_write_is_atomic_replace(self):
        """目标文件在写的过程中始终存在且完整 —— 靠临时文件 + ``os.replace``。"""
        path = os.path.join(self.root, "_ndx.txt")
        store = sc.ConfigSmaFileStore(path)
        store.set_var("SMA", "Date", "2026-10-09")
        store.save_data()
        first = _read(path)

        seen = []
        original_replace = os.replace

        def spy(src, dst):
            # 替换发生**之前**读一次：此时磁盘上仍应是完整的第一版
            seen.append(_read(dst))
            original_replace(src, dst)

        store.set_var("SMA", "D5", "100.5")
        os.replace = spy
        try:
            store.save_data()
        finally:
            os.replace = original_replace

        self.assertEqual(seen, [first], "替换前磁盘上必须是完整的旧文件")
        self.assertIn("D5=100.5", _read(path))

    def test_no_temp_files_are_left_behind(self):
        path = os.path.join(self.root, "_ndx.txt")
        store = sc.ConfigSmaFileStore(path)
        store.set_var("SMA", "Date", "2026-10-09")
        store.save_data()

        self.assertEqual(os.listdir(self.root), [os.path.basename(path)])

    def test_replace_failure_is_recorded_not_raised(self):
        """Windows 上 ``os.replace`` 可能撞"目标被占用"。

        缓存写不成不该让页面 500，**但错误必须留痕**（本项目吃过静默吞异常的亏）。
        """
        path = os.path.join(self.root, "_ndx.txt")
        store = sc.ConfigSmaFileStore(path)
        store.set_var("SMA", "Date", "2026-10-09")

        original = os.replace

        def boom(src, dst):
            raise OSError("被别的进程占着（模拟 Windows）")

        os.replace = boom
        try:
            self.assertFalse(store.save_data())
        finally:
            os.replace = original

        self.assertIsInstance(store.last_error, OSError)
        self.assertEqual(os.listdir(self.root), [], "失败时临时文件要清掉")

    def test_concurrent_writes_only_cost_a_recompute(self):
        """丢更新是**无害**的：内容是 ``(symbol, start_date)`` 的纯函数。

        两个"线程"写同一份文件，后写的赢；但两边算出来的字节一样，
        所以最坏结果只是多算一遍，绝不会写出错数据。这条钉住这个推理。
        """
        path = os.path.join(self.root, "_ndx.txt")
        first = sc.ConfigSmaFileStore(path)
        first.set_var("SMA", "Date", "2026-10-09")
        first.set_var("SMA", "D5", "100.5")

        second = sc.ConfigSmaFileStore(path)     # 同一时刻的另一条请求
        second.set_var("SMA", "Date", "2026-10-09")
        second.set_var("SMA", "D5", "100.5")

        self.assertTrue(first.save_data())
        self.assertTrue(second.save_data())

        self.assertEqual(_read(path),
                         "[SMA]\nDate=2026-10-09\nD5=100.5\n")


class NoWorkspacePollutionTest(_TempDirCase):
    """**这块最关键**：不显式给路径，就一个字节都不写。"""

    def setUp(self):
        super().setUp()
        self._clean_env()

    def tearDown(self):
        self._clean_env()
        super().tearDown()

    def _clean_env(self):
        os.environ.pop(sc.ENV_CACHE_DIR, None)

    def test_factory_falls_back_to_memory_without_a_root(self):
        self.assertIsInstance(sc.config_sma_store("^NDX"), sh.ConfigSmaStore)

    def test_factory_falls_back_to_memory_with_an_empty_root(self):
        self.assertIsInstance(sc.config_sma_store("^NDX", root=""), sh.ConfigSmaStore)

    def test_factory_falls_back_to_memory_without_a_symbol(self):
        """给了 root 但没有符号也没法拼路径 —— 仍然退回内存。"""
        self.assertIsInstance(sc.config_sma_store(root=self.root), sh.ConfigSmaStore)

    def test_env_var_is_the_only_implicit_switch(self):
        os.environ[sc.ENV_CACHE_DIR] = self.root
        store = sc.config_sma_store("^NDX")
        self.assertIsInstance(store, sc.ConfigSmaFileStore)
        self.assertEqual(store.path, os.path.join(self.root, "config", "_ndx.txt"))

    def test_file_store_factory_is_memory_until_the_env_is_set(self):
        """``file_store_factory`` 只在一个地方判断"开不开"，默认恒给内存。"""
        factory = sc.file_store_factory()
        self.assertIsInstance(factory("^NDX"), sh.ConfigSmaStore)

    def test_file_store_factory_honours_the_env(self):
        os.environ[sc.ENV_CACHE_DIR] = self.root
        factory = sc.file_store_factory()
        self.assertIsInstance(factory("^NDX"), sc.ConfigSmaFileStore)

    def test_file_store_factory_takes_an_explicit_root(self):
        factory = sc.file_store_factory(self.root)
        self.assertIsInstance(factory("^NDX"), sc.ConfigSmaFileStore)

    def test_default_stockhistory_never_touches_the_disk(self):
        """端到端钉住：默认构造 ``StockHistory`` 后，**临时目录里一个文件都没有**。

        这条是整套设计的底线 —— 渲染页面不能在工作区里乱写。
        刻意把 CWD 切到临时目录再建：若哪天有人拿 CWD 当 root，
        这条会立刻炸。
        """
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            instance, _ = _build_history()
            self.assertIsInstance(instance.config, sh.ConfigSmaStore)
            self.assertTrue(instance.get_sma(), "确认真算过（不是空跑）")
        finally:
            os.chdir(cwd)
        self.assertEqual(os.listdir(self.root), [])

    def test_file_backend_really_writes_when_asked(self):
        """反面对照：显式给了路径就**确实**会落盘，别把上面那条理解成"永远不写"。"""
        store = sc.config_sma_store("^NDX", root=self.root)
        store.set_var("SMA", "Date", "2026-10-09")
        self.assertTrue(store.save_data())
        self.assertEqual(os.listdir(self.root), ["config"])
        self.assertEqual(_read(store.path), "[SMA]\nDate=2026-10-09\n")


if __name__ == "__main__":
    unittest.main()
