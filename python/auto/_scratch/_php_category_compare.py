#!/usr/bin/env python
"""分类页的 PHP 逐例对照：17 个分类 × 三样东西。

这条线要核对的只有三件事（页面渲染逻辑完全共享，没有算式可对）：

======================  ==================================  ==================================
对照项                    PHP 出处                             Python 出处
======================  ==================================  ==================================
符号数组                  ``_stocklink.php::GetCategoryArray``  ``category_page_service.CATEGORY_PAGES[*]["symbols"]``
标题                      ``_mystockgroup.php::_getTitleStr``   同名键 ``title``
说明                      ``_mystockgroup.php::_getMetaDescriptionStr``  同名键 ``description``
======================  ==================================  ==================================

跑法（工作目录无所谓，脚本自己切到本文件所在目录）::

    D:/python/python.exe -B auto/_scratch/_php_category_compare.py

两个 PHP 探针：

* ``_php_category_check.php`` —— 数组。它 require 的是真 ``stocksymbol.php``
  与 ``_stocklink.php``，所以数组不是抄的；
* ``_php_category_meta_check.php`` —— 标题/说明。这两段是常量拼接，所以探针
  从源文件里把 ``$ar = array(...)`` 字面量抠出来 ``eval``，让 PHP 自己拼。

**已知的唯一差异是 qdii 的 8 只**（本地 PHP 仓库是旧快照，Python 按生产端
2026-10-12 的 41 只复现），脚本把它单独标出来，不当失败。
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

from core.stockdata import load_mysql_env  # noqa: E402
from core.stocksymbol import get_hk_mix_symbol_array  # noqa: E402


#: qdii 里刻意多出来的 8 只（生产端有、本地 PHP 仓库没有）。
#: 来源见 ``core/stocksymbol.py::qdii_get_symbol_array()`` 的 docstring。
QDII_EXTRA = [
    "SZ159509",
    "SZ159529",
    "SH513850",
    "SZ159577",
    "SH501312",
    "SZ160216",
    "SZ164701",
    "SZ163208",
]

#: 集合相同、只有**排列**按生产端排的分类。本地 PHP 快照与生产端的行序不同，
#: 而页面行序是人工比对对象，所以 Python 跟生产端走（见
#: ``category_page_service.GOLD_DISPLAY_ORDER``）。集合必须一致，只放行顺序。
#:
#: ``overnight`` 是 2026-11-01 加注册表时发现的同一个坑：它内嵌的
#: ``GetQdiiGoldSymbolArray()`` 那一段的排列与生产端不同，处理方式照抄
#: ``commodity``。
#:
#: ``lof`` 也是这一条：``GetAllSymbolArray()`` 在本地快照里还是旧的合并顺序
#: （成员 53 只一致），生产端 ``lofcn.php`` 的顺序与 Python 完全一致。
ORDER_BY_PRODUCTION = {"commodity", "overnight", "lof"}

#: chinaindex 里刻意多出来的 4 只科创 50（生产端有、本地 PHP 仓库没有）。
#: 来源见 ``core/stocksymbol.py::china_index_get_star_array()``。
CHINA_INDEX_EXTRA = ["SH588000", "SH588050", "SH588080", "SH588090"]

#: qdiihk 里刻意多出来的 9 只港股混合（生产端已从 qdiimix 并入港股页）。
QDII_HK_EXTRA = list(get_hk_mix_symbol_array())

#: qdiimix 里刻意**少**掉的 17 只：8 只归 qdii、9 只归 qdiihk。
QDII_MIX_DROP = QDII_EXTRA + QDII_HK_EXTRA


def run_php(script: str) -> dict:
    """跑一个探针并解析它的 stdout（JSON）。

    ``env`` 必须显式合并 ``.env``：``subprocess`` 继承的是**启动解释器时**
    的环境，``load_mysql_env()`` 只改本进程的 ``os.environ``。
    这两个探针本身不连库，但探针一旦扩到 ``lof`` / 配对分类就得连，
    把坑提前堵上（pitfalls #150）。
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


def main() -> int:
    from web.bootstrap import install_paths

    install_paths()
    from web.services.category_page_service import CATEGORY_PAGES, get_category_names

    php_symbols = run_php("_php_category_check.php")
    php_meta = run_php("_php_category_meta_check.php")

    consistent, deviations, failed = [], [], []

    print("=" * 92)
    print("%-14s %-6s %-6s %-5s %-5s %s" % ("分类", "PHP", "PY", "数组", "文字", "备注"))
    print("-" * 92)

    for name in get_category_names():
        definition = CATEGORY_PAGES[name]
        py_symbols = list(definition["symbols"]())
        php_case = php_symbols.get(name, {})
        php_list = php_case.get("symbols", [])

        notes = []
        array_ok = py_symbols == php_list
        if not array_ok:
            only_py = [s for s in py_symbols if s not in php_list]
            only_php = [s for s in php_list if s not in py_symbols]
            if name == "qdii" and only_py and not only_php and only_py == QDII_EXTRA:
                # 按设计差异：Python 跟着生产端走，本地 PHP 是旧快照
                notes.append("数组差 8 只（按设计：生产端已扩充）")
                deviations.append(name)
            elif (name == "chinaindex" and only_py == CHINA_INDEX_EXTRA
                  and not only_php):
                notes.append("数组差 4 只科创50（按设计：生产端已扩充）")
                deviations.append(name)
            elif name == "qdiihk" and only_py == QDII_HK_EXTRA and not only_php:
                notes.append("数组差 9 只港股混合（按设计：生产端已并入本页）")
                deviations.append(name)
            elif (name == "qdiimix" and not only_py
                  and sorted(only_php) == sorted(QDII_MIX_DROP)):
                notes.append("数组少 17 只（按设计：已归入 qdii / qdiihk）")
                deviations.append(name)
            elif name in ORDER_BY_PRODUCTION and not only_py and not only_php:
                # 集合一致、只是排列按生产端
                notes.append("排列按生产端（成员一致）")
                deviations.append(name)
            else:
                notes.append("数组不一致 py=+%s php=+%s" % (only_py, only_php))
                failed.append(name)
        else:
            consistent.append(name)

        meta_case = php_meta.get(name, {})
        title_ok = definition["title"] == meta_case.get("title")
        desc_ok = definition["description"] == meta_case.get("description")
        if not title_ok:
            notes.append("标题不一致：PHP=%r" % (meta_case.get("title"),))
            if name not in failed:
                failed.append(name)
        if not desc_ok:
            notes.append("说明不一致：PHP=%r" % (meta_case.get("description"),))
            if name not in failed:
                failed.append(name)

        print(
            "%-14s %-6d %-6d %-5s %-5s %s"
            % (
                name,
                len(php_list),
                len(py_symbols),
                "一致" if array_ok else ("按设计" if name in deviations else "不一致"),
                "一致" if (title_ok and desc_ok) else "不一致",
                "；".join(notes),
            )
        )

    print("-" * 92)
    print(
        "共 %d 例：一致 %d，按设计差异 %d，失败 %d"
        % (len(get_category_names()), len(consistent), len(deviations), len(failed))
    )
    if failed:
        print("失败分类：%s" % ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
