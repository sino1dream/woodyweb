#!/usr/bin/env python
"""AB / AH / ADR 对比四页的 PHP 逐例对照。

三张对比页的行完全是 ``GetCategoryArray($strPage)`` 决定的，而它的
``abcompare`` / ``ahcompare`` / ``adrhcompare`` 三个分支就是
``GetXxxPairSql()->GetSymbolArray()`` —— 所以这里对的是**三层**：

====================  ======================================  ==========================================
对照项                  PHP 出处                                 Python 出处
====================  ======================================  ==========================================
页面数组                ``_stocklink.php::GetCategoryArray``      ``compare_page_service.compare_symbols``
全表符号数组            ``StockPairSql::GetSymbolArray()``         ``StockPairStore.get_symbol_array()``
按配对侧反查            ``GetSymbolArray($strPairSymbol)``         同名方法带参数
====================  ======================================  ==========================================

跑法（工作目录无所谓，脚本自己切到本文件所在目录）::

    D:/python/python.exe -B auto/_scratch/_php_compare_compare.py

要点：

* ``abpair`` / ``ahpair`` / ``adrpair`` 在演示库是 **0 行**，只拿它们对"空"
  没有说服力，所以把 ``fundpair``（76 行）也拉进来 —— 它走的是**同一个**
  ``StockPairSql::GetSymbolArray()``，``sort()`` 与"id 查不到符号"两条分支
  只有它验得到。
* PHP ``GetSymbolArray()`` 把查不到符号的 ``id`` 原样塞进数组（值是 ``false``）
  再 ``sort()``；Python 选择**跳过**（``None`` 进 ``sort()`` 会炸，且下游
  ``StockGetPairReferences`` 也无法给它建引用）。演示库里不存在这种脏行，
  所以本对照**撞不到**这条分支，只在 README 里记为已知偏离。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SCRATCH_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRATCH_DIR.parents[1]       # …/python
AUTO_DIR = SCRATCH_DIR.parent             # …/python/auto

for path in (str(PYTHON_DIR), str(AUTO_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.stockdata import MySqlFundDataSource, load_mysql_env  # noqa: E402
from sql.optionstore import (  # noqa: E402
    AbPairStore,
    AdrPairStore,
    AhPairStore,
    FundPairStore,
)


#: 页面名 → (PHP ``GetCategoryArray`` 分支, Python 的 store 类)
PAGE_TABLES = {
    "abcompare": AbPairStore,
    "ahcompare": AhPairStore,
    "adrhcompare": AdrPairStore,
}

#: 与 PHP 探针里 ``$arPairSymbol`` 逐一对应，别只改一边。
PAIR_SYMBOLS = ["SZ161125", "SH513100", "QQQ", "^GSPC", "SH", "SZ161130",
                "NOSUCHSYMBOL", ""]


def run_php(script: str) -> dict:
    """跑一个探针并解析它的 stdout（JSON）。

    ``env`` 必须显式合并 ``.env``：``subprocess`` 继承的是**启动解释器时**
    的环境，``load_mysql_env()`` 只改本进程的 ``os.environ``（pitfalls #150）。
    """
    env = {**os.environ, **load_mysql_env(os.environ)}
    completed = subprocess.run(
        ["php", "-d", "display_errors=stderr", script],
        cwd=str(SCRATCH_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise SystemExit(
            "PHP 探针失败：%s\n%s" % (script, completed.stderr.decode("utf-8", "replace"))
        )
    return json.loads(completed.stdout.decode("utf-8"))


def _row(kind: str, name: str, php: list, py: list, notes: list,
         consistent: list, failed: list) -> None:
    ok = php == py
    if ok:
        consistent.append(name)
        verdict = "一致"
    else:
        failed.append(name)
        verdict = "不一致"
        only_py = [s for s in py if s not in php]
        only_php = [s for s in php if s not in py]
        if not only_py and not only_php:
            notes.append("顺序不同 php=%s py=%s" % (php[:6], py[:6]))
        else:
            notes.append("py 多 %s / php 多 %s" % (only_py[:6], only_php[:6]))
    print("%-12s %-14s %-5d %-5d %-6s %s"
          % (kind, name, len(php), len(py), verdict, "；".join(notes)))


def main() -> int:
    from web.bootstrap import install_paths

    install_paths()
    from web.services.compare_page_service import compare_symbols

    php = run_php("_php_compare_check.php")
    source = MySqlFundDataSource.from_env()
    connection = source.connection
    stores = {
        "abpair": AbPairStore(connection),
        "ahpair": AhPairStore(connection),
        "adrpair": AdrPairStore(connection),
        "fundpair": FundPairStore(connection),
    }

    consistent: list = []
    failed: list = []

    print("=" * 96)
    print("%-12s %-14s %-5s %-5s %-6s %s"
          % ("层次", "用例", "PHP", "PY", "判定", "备注"))
    print("-" * 96)

    # 1) 页面数组：GetCategoryArray() ←→ compare_symbols()
    for page in ("abcompare", "ahcompare", "adrhcompare"):
        php_case = php["categories"][page]
        py_list = list(compare_symbols(page, source))
        _row("页面数组", page, php_case["symbols"], py_list, [],
             consistent, failed)

    # 2) 全表符号数组
    for table, store in stores.items():
        php_case = php["tables"][table]
        _row("全表数组", table, php_case["symbols"], store.get_symbol_array(),
             [], consistent, failed)

    # 3) 按配对侧反查
    for symbol in PAIR_SYMBOLS:
        for table, store in stores.items():
            php_case = php["by_pair"][symbol][table]
            _row("反查", "%s@%s" % (symbol or "''", table),
                 php_case["symbols"], store.get_symbol_array(symbol),
                 [], consistent, failed)

    print("-" * 96)
    total = len(consistent) + len(failed)
    print("共 %d 例：一致 %d，失败 %d" % (total, len(consistent), len(failed)))
    if failed:
        print("失败用例：%s" % ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
