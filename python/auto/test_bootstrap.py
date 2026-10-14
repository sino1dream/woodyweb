"""``dbtools/bootstrap_fund_data.py``（P0.8 一键重跑）的单元测试。

## 为什么要测一个"只是串命令"的脚本

它的价值全在**顺序**上：每一步都是已经单独验证过的命令，串错顺序就会静默
产出半成品（先校准后回填 = 校准值永远停在旧数据上）。所以这里钉的是
**依赖关系**，不是每条命令的行为。

## 为什么用路径加载而不是 import

``dbtools`` 是**维护脚本目录，不是包**（``test_layout.SqlPackageShadowTest``
钉住它没有 ``__init__.py`` —— 加了就会和 ``auto/sql`` 抢包名）。所以只能
按文件路径加载，不能用 ``import dbtools.bootstrap_fund_data``。
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

AUTO_DIR = Path(__file__).resolve().parent
PYTHON_DIR = AUTO_DIR.parent
SCRIPT = PYTHON_DIR / "dbtools" / "bootstrap_fund_data.py"


def _load():
    """按路径加载脚本（``dbtools`` 不是包）。

    **必须在加载期间把 ``sys.stdout`` 换成没有 ``buffer`` 属性的对象。**
    脚本（和 ``cli/*`` 一样）在导入时会把 ``sys.stdout`` 包成 ``TextIOWrapper``
    （为了让 Windows 控制台输出 UTF-8）。那个包装器一旦被垃圾回收，会关掉底层
    buffer；后续再 import 任何做同样包装的脚本就会
    ``ValueError: I/O operation on closed file`` —— 本文件加进来之后
    ``test_fundest.TimeTextTest`` 就是这样红的（它 import ``cli.fundestcli``）。
    ``test_layout.test_every_module_imports`` 用的是同一个规避手法。
    """
    saved = sys.stdout
    sys.stdout = io.StringIO()
    try:
        spec = importlib.util.spec_from_file_location("bootstrap_fund_data", str(SCRIPT))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.stdout = saved
    return module


bootstrap = _load()


class StepOrderTest(unittest.TestCase):
    """步骤的**顺序**就是依赖，这几条是整条链路的骨架。"""

    def names(self) -> list:
        return [step.name for step in bootstrap.STEPS]

    def test_symbols_comes_first(self):
        """抓取层跳过库里没有的符号 —— 不先写符号，后面全是空转。"""
        self.assertEqual(self.names()[0], "symbols")

    def test_fundest_comes_last(self):
        """官方 EST 要用到净值与校准值，只能排在最后。"""
        self.assertEqual(self.names()[-1], "fundest")

    def test_archive_comes_before_calibration(self):
        """快照归档要在校准之前：锚点日取的是**已收盘**的行情，
        不归档的话锚点推不动（P0.6 实测 29 只基金因此算不出净值）。"""
        names = self.names()
        self.assertLess(names.index("archive"), names.index("calib"))
        self.assertLess(names.index("archive"), names.index("fund-calib"))

    def test_middle_nav_comes_before_calibration(self):
        """中间层净值推进闸门，闸门不开就没有校准值。"""
        names = self.names()
        self.assertLess(names.index("middle-nav"), names.index("calib"))

    def test_quotes_comes_before_middle_nav(self):
        """中间层净值 = 当日收盘价，先有行情才推得出净值。"""
        names = self.names()
        self.assertLess(names.index("quotes"), names.index("middle-nav"))

    def test_fund_calib_comes_before_fundest(self):
        names = self.names()
        self.assertLess(names.index("fund-calib"), names.index("fundest"))


class StepDefinitionTest(unittest.TestCase):
    """每一步指向的脚本要真的存在，且写库的参数要显式。"""

    def test_every_script_exists(self):
        for step in bootstrap.STEPS:
            self.assertTrue(os.path.isfile(step.script),
                            "%s 指向的脚本不存在：%s" % (step.name, step.script))

    def test_steps_that_write_say_apply(self):
        """会写库的步骤必须**显式**带 ``--apply`` —— 靠"脚本默认是写库"这种
        隐式约定，一键脚本就会变成一把没有保险的枪。"""
        for step in bootstrap.STEPS:
            if step.name in ("symbols", "middle-nav", "calib", "fund-calib"):
                self.assertIn("--apply", step.argv, step.name)

    def test_read_only_steps_do_not_pass_apply(self):
        """只补数据的步骤（行情/净值/汇率/归档/EST）默认就是写库，
        再挂 ``--apply`` 反而说明脚本被改成了默认只读。"""
        for step in bootstrap.STEPS:
            if step.name in ("forex", "nav", "quotes", "archive", "fundest"):
                self.assertNotIn("--apply", step.argv, step.name)


class SelectStepsTest(unittest.TestCase):
    """``--only`` / ``--from`` / ``--to`` 的选步逻辑。"""

    def test_only_keeps_the_declared_order(self):
        """按声明顺序而不是参数顺序 —— 用户写 ``--only fundest,archive``
        也该先归档再落 EST。"""
        steps = bootstrap.select_steps("fundest,archive", "", "")
        self.assertEqual([step.name for step in steps], ["archive", "fundest"])

    def test_from_picks_a_tail(self):
        steps = bootstrap.select_steps("", "archive", "")
        self.assertEqual([step.name for step in steps][0], "archive")
        self.assertEqual([step.name for step in steps][-1], "fundest")

    def test_to_picks_a_head(self):
        steps = bootstrap.select_steps("", "", "forex")
        self.assertEqual([step.name for step in steps], ["symbols", "forex"])

    def test_unknown_step_is_rejected(self):
        """拼错的步骤名要**立刻报错**，而不是静默跑空。"""
        with self.assertRaises(SystemExit):
            bootstrap.select_steps("nope", "", "")


class DryRunTest(unittest.TestCase):
    """默认 dry-run：不写库、不要求连得上库。"""

    def test_default_is_dry_run_and_survives_without_a_database(self):
        """体检那一步连不上库也要能跑完 —— 换机器第一次跑时数据库可能还没有。"""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = bootstrap.main(["--only", "fundest"])
        self.assertEqual(code, 0)
        self.assertIn("dry-run", buffer.getvalue())
        self.assertIn("fundestcli", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
