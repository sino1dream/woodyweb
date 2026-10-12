"""``python/auto`` 目录分档的结构守卫测试。

为什么要写这个测试：分档靠的是"文件搬走 + import 改写"两件事同时成立，
任何一半回退（有人又把 ``quotestore.py`` 拷回根目录、或新代码照旧写
``from quotestore import ...``）在单个模块里看不出问题，要等运行到那一行才炸。
这里用 AST 全量扫描把这类回退变成"跑测试就红"。

对应文档：``python/auto/README_layout.md``。
"""

from __future__ import annotations

import ast
import importlib
import io
import sys
import unittest
from pathlib import Path

AUTO_DIR = Path(__file__).resolve().parent
PYTHON_DIR = AUTO_DIR.parent

#: 分档后的包目录，以及它们应有的定位（用于文档与测试的同一份事实来源）
PACKAGES = ("sql", "source", "updater", "calc", "ref", "ui", "cli", "core")

#: 已经搬进子包的模块名。根目录**不允许**再出现同名文件，
#: 代码里也不允许再有以它们为顶层名的 import。
MOVED_MODULES = {
    "stocksql", "quotestore", "fundeststore", "historytable", "stockhistory", "smaconfig",
    "sinaquotes", "chinamoney", "chinastock", "forexsource", "navsource", "futuresquotes",
    "usnavsource", "httpjson", "quotesource",
    "quoteupdater", "navupdater", "forexupdater", "stockprefetch", "indexhistory",
    "autocalibration", "estcalibration",
    "fundcalc", "fundestcalc", "futurescalib", "futurepremium", "pairestimate", "fundgroups",
    "refobj", "stockref", "refquote", "externallink",
    "uicolumn", "pricepool",
    "fundcli", "fundestcli", "futurescli", "navcli", "forexcli", "quotecli",
    "quoteupdatercli", "autocalibrationcli",
    "stocksymbol", "stockdata", "tzutil",
}


def _iter_py_files() -> list[Path]:
    """auto 目录下所有 .py（含子包与 _scratch，排除 __pycache__）。"""
    return [
        path
        for path in AUTO_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


class PackageLayoutTest(unittest.TestCase):
    """子包目录本身要成立。"""

    def test_every_package_has_init_with_docstring(self):
        for name in PACKAGES:
            init = AUTO_DIR / name / "__init__.py"
            self.assertTrue(init.is_file(), f"{name}/__init__.py 不存在")
            tree = ast.parse(init.read_text(encoding="utf-8"))
            self.assertTrue(ast.get_docstring(tree), f"{name}/__init__.py 缺中文说明")

    def test_moved_modules_are_inside_packages(self):
        flat = sorted(
            name for name in MOVED_MODULES if (AUTO_DIR / f"{name}.py").exists()
        )
        self.assertEqual([], flat, f"这些模块还在根目录平铺：{flat}")

    def test_scratch_is_not_a_package(self):
        """``_scratch`` 故意不加 ``__init__.py``：

        加了它就会被 ``unittest discover`` 当成包递归进去，而里面是一次性脚本；
        不加又能让 Python 的命名空间包规则不去干扰真正的包解析。
        """
        self.assertTrue((AUTO_DIR / "_scratch").is_dir())
        self.assertFalse((AUTO_DIR / "_scratch" / "__init__.py").exists())


class ImportRewriteTest(unittest.TestCase):
    """没有任何一处 import 还停留在分档前的平铺写法。"""

    def test_no_flat_import_of_moved_modules(self):
        offenders: list[str] = []
        for path in _iter_py_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as err:  # 语法错误由别的测试负责，这里只跳过
                offenders.append(f"{path.name}: 语法错误 {err}")
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    # ``from __future__ import annotations`` 这类没有 module
                    root = (node.module or "").split(".")[0]
                    if root in MOVED_MODULES:
                        offenders.append(f"{path.relative_to(AUTO_DIR)}:{node.lineno}: {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        if root in MOVED_MODULES:
                            offenders.append(f"{path.relative_to(AUTO_DIR)}:{node.lineno}: import {alias.name}")
        self.assertEqual([], offenders, "仍有平铺 import：\n" + "\n".join(offenders))

    def test_every_module_imports(self):
        """每个模块都能独立 import —— 抓的是"包内相互 import 写错包名"。

        ``cli`` 下的脚本会在导入时把 ``sys.stdout`` 换成 ``TextIOWrapper``
        （为了让 Windows 控制台输出 UTF-8）。连续导入多个脚本时，上一个包装器
        被回收会关掉底层 buffer，下一个脚本再取 ``sys.stdout.buffer`` 就会
        ``ValueError: I/O operation on closed file``。所以这里先把
        ``sys.stdout`` 换成没有 ``buffer`` 属性的 ``StringIO``：脚本里的
        ``hasattr(sys.stdout, "buffer")`` 判断会自然跳过包装分支。
        """
        failed: list[str] = []
        saved = sys.stdout
        sys.stdout = io.StringIO()
        try:
            for name in PACKAGES:
                for path in sorted((AUTO_DIR / name).glob("*.py")):
                    if path.name == "__init__.py":
                        continue
                    module = f"{name}.{path.stem}"
                    try:
                        importlib.import_module(module)
                    except Exception as err:  # noqa: BLE001 - 收集所有失败再一次性报
                        failed.append(f"{module}: {type(err).__name__}: {err}")
        finally:
            sys.stdout = saved
        self.assertEqual([], failed, "模块导入失败：\n" + "\n".join(failed))


class SqlPackageShadowTest(unittest.TestCase):
    """``sql`` 这个包名的解析必须落在 ``python/auto/sql``。

    ``python/sql``（建表 / 种子 / 备份脚本）与它是**同名目录**，且
    ``web.bootstrap.install_paths()`` 会先把 ``python`` 塞进 ``sys.path``。
    目前靠"``python/sql`` 没有 ``__init__.py``，而 ``python/auto/sql`` 有"
    这条规则让正确的那个胜出；一旦有人给 ``python/sql`` 补了 ``__init__.py``，
    ``import sql.xxx`` 会静默解析到错的目录，所以这里显式钉住。
    """

    def test_python_sql_has_no_init(self):
        self.assertFalse(
            (PYTHON_DIR / "sql" / "__init__.py").exists(),
            "python/sql 不能有 __init__.py：它会把 import sql.* 抢到维护脚本目录去",
        )

    def test_sql_package_resolves_to_auto(self):
        sql = importlib.import_module("sql")
        resolved = Path(sql.__file__).resolve().parent
        self.assertEqual(AUTO_DIR / "sql", resolved, f"sql 包解析到了 {resolved}")

    def test_stockdata_env_default_is_auto_root(self):
        """``stockdata`` 挪到 ``core/`` 后，``.env`` 默认路径仍要指向 ``python/auto``。"""
        module = importlib.import_module("core.stockdata")
        env_default = Path(module.__file__).resolve().parents[1] / ".env"
        self.assertEqual(AUTO_DIR / ".env", env_default)


if __name__ == "__main__":
    unittest.main()
