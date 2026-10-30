"""QDII 分类页（``woody/res/qdiicn.php``）—— 现在只是注册表里的一项。

## 为什么只剩下这么点

16 个分类页（``biotechcn.php`` / ``hstechcn.php`` / ``qdiihkcn.php`` ……）
在 PHP 里全部落到同一个 ``_mystockgroup.php::_echoStockGroupArray()``，
**页面之间的差别只有符号数组和两段文字**。所以通用部分（注册表、
``classify_symbols``、两张表的装配）统一搬到了
:mod:`web.services.category_page_service`，本模块只保留 qdii 的对外名字：

* :data:`PAGE_TITLE` / :data:`PAGE_DESCRIPTION` —— 值取自注册表，不另写一份
  （两处都写，改一处就是静默不一致）；
* :func:`build_qdii_page` —— ``build_category_page('qdii', ...)`` 的薄包装，
  签名保持原样，``routes/fund.py`` 与 ``tests/test_qdii_page.py`` 不用改调用；
* :func:`classify_symbols` —— 从新模块再导出，老的 import 继续能用。
  **注意**：它执行时的全局名在 ``category_page_service`` 里，单测要给
  ``MysqlReference`` / ``build_qdii_reference`` 打补丁得打那边 ——
  打在本模块的名字上不会生效，单测会"全绿但没测到真东西"。

## qdii 与其它 15 个分类唯一的实质差别

``qdii_get_symbol_array()`` 是**刻意领先本地 PHP 仓库**的：仓库里
``QdiiGetSymbolArray()`` 是 33 只，Python 按生产端（2026-10-12 抓取）
复现成 41 只，多出的 8 只的来龙去脉写在
``core/stocksymbol.py::qdii_get_symbol_array()`` 的 docstring 里。
其余 15 个数组与 PHP 逐例一致，见 ``README_category_pages_migration.md``。

顺带一个可对账的事实：非宽屏时全部符号都进参考数据表，所以**表1 与表2
行数相同**（生产端都是 41 行）。这句话原来写在本模块的装配说明里，装配
搬走之后留在这里，核对时仍然有效。
"""

from __future__ import annotations

from typing import Any

from web.services.category_page_service import (  # noqa: F401
    CATEGORY_PAGES,
    build_category_page,
    classify_symbols,
)

#: 页面标题。PHP ``_getTitleStr('qdii')`` = ``QDII_DISPLAY . '基金净值计算工具'``，
#: 而 ``QDII_DISPLAY`` 在 ``php/stocklink.php:22`` 定义为 ``'美股QDII'``。
PAGE_TITLE = CATEGORY_PAGES["qdii"]["title"]

#: 页面说明。PHP ``_getMetaDescriptionStr()`` 里 qdii 分支的原文。
PAGE_DESCRIPTION = CATEGORY_PAGES["qdii"]["description"]


def build_qdii_page(
    source: Any = None,
    reference_service: Any = None,
    fundest_service: Any = None,
    symbols: list = None,
    wide: bool = False,
    sort: str = None,
) -> dict[str, Any]:
    """QDII 分类页的两张表，参数与返回见 :func:`build_category_page`。

    ``symbols`` 是**测试注入用**的，生产路径走注册表里的
    ``qdii_get_symbol_array()``。
    """
    return build_category_page(
        "qdii",
        source=source,
        reference_service=reference_service,
        fundest_service=fundest_service,
        symbols=symbols,
        wide=wide,
        sort=sort,
    )
