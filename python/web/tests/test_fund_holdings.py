"""基金详情页的**持仓分支**（``/fund/<group>/<symbol>`` 走
``EchoHoldingsEstParagraph`` 那一支）。

## 这一支为什么单独存在

PHP 的 ``woody/res/php/_qdiimix.php`` 里 ``$this->ref = new
HoldingsReference($strSymbol)`` —— **qdiimix（QDII-LOF 混合 / QDII-ETF）这一族
页面用的是持仓引用**，不是 QDII 引用。所以生产端 ``sz164701cn.php`` 与
``sh513050cn.php`` 的估值表上方是::

    仓位估算值使用1.00。基金持仓更新于2026-10-22。穷举持仓比例

而 QDII 那一支（``EchoFundEstParagraph``）是另一句「实时EST xx 和 yy 关联程度
按照100%估算。」。本仓库改这里之前，**基金详情页恒走 QDII 分支**，
持仓型基金少了「基金持仓」链接与后面的净值记录段（只有 ``/stock`` 页有）。

## 测什么

1. 分流：``holdings`` 表有明细 → 持仓分支，没有 → QDII 分支；
2. 说明文字的三段结构与**两个链接的目标**（``/history/fundposition`` +
   ``/holdings/<sym>``）；
3. 「仓位估算」**无条件给链接** —— 这是最容易搞错的一处，见
   :func:`test_position_link_ignores_fund_position_gate`；
4. ``CountHoldings() <= 4`` 才补「穷举持仓比例」；
5. 取引用抛异常时整页不 500。

替身的构造思路沿用 ``tests/test_holdings.py``：用 ``holdings_ref_builder``
注入口顶掉连库的 ``StockGetHoldingsReference``，其余段落（参考数据 / 五档 /
历史）由各自的 ``try`` 兜住，取不到就整段不显示 —— 不影响本分支的断言。
"""

from __future__ import annotations

import os
import sys
import unittest

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(WEB_DIR)
for _path in (PYTHON_DIR, WEB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import create_app  # noqa: E402
from calc.fundgroups import get_symbol_group  # noqa: E402
from core.stocksymbol import in_array_lof_mix, in_array_qdii  # noqa: E402


class FakeHoldingsRef:
    """基金级持仓引用替身（``HoldingsReference`` 在说明文字那部分的表面积）。

    只实现 ``_holdings_est_note`` 会问到的四个方法；估值式与七列测算表由
    ``auto/test_holdingsref.py`` 与 ``tests/test_holdings.py`` 负责。
    """

    def __init__(self, symbol="SH513050", date="2026-10-22", count=36,
                 position=1.0):
        self._symbol = symbol
        self._date = date
        self._count = count
        self._position = position

    def get_symbol(self):
        return self._symbol

    def get_holdings_date(self):
        return self._date

    def count_holdings(self):
        return self._count

    def get_position(self):
        return self._position


class RecordingFundEst:
    """``FundEstService`` 替身：记下传进来的引用，给一个非空表。

    **``rows`` 必须非空**：``_build_holdings_est`` 里 ``sections.get("est")``
    为假就整段退回 QDII 分支，给空列表等于没有注入。
    """

    def __init__(self):
        self.refs_seen = []

    def get_table(self, refs):
        self.refs_seen = list(refs)
        return {"kind": "fundest", "rows": [{"cells": ["SH513050"]}]}


class FakeHistoryService:
    """``HistoryService`` 替身：净值记录段落（``EchoNetValueHistoryParagraph``）。"""

    def net_value_paragraph(self, symbol, ref, num=1):
        return {"kind": "netvaluehistory", "symbol": symbol, "num": num,
                "title": "净值记录", "rows": [{"cells": ["2026-10-22"]}]}


class FakeFundService:
    """``FundService`` 替身：详情页只要 ``symbol`` / ``name`` 两个键。"""

    def get_fund(self, symbol, sources=None):
        return {"symbol": symbol, "name": "测试基金", "config": {}}


class FundHoldingsBranchTest(unittest.TestCase):
    """持仓型 vs QDII 型的分流与说明文字。"""

    SYMBOL = "SH513050"

    def setUp(self):
        self.fundest = RecordingFundEst()
        group = get_symbol_group(self.SYMBOL) or "qdiietf"
        self.url = "/fund/%s/%s" % (group, self.SYMBOL)

    def _app(self, ref=None, **kwargs):
        """``ref=None`` 表示"不是持仓型"（``StockGetHoldingsReference`` 返回
        ``None``）；``ref`` 是异常实例时模拟连库失败。
        """
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FakeFundService(),
            status_service_factory=lambda: None,
            fundest_service_factory=lambda: self.fundest,
            history_service_factory=lambda: FakeHistoryService(),
            holdings_ref_builder=(lambda symbol: ref),
            **kwargs
        )
        return self.app.test_client()

    def _html(self, ref=None, **kwargs):
        return self._app(ref=ref, **kwargs).get(self.url).get_data(as_text=True)

    # -- 分流 ----------------------------------------------------------
    def test_holdings_branch_note_has_three_parts(self):
        """持仓型：说明文字是「仓位估算值使用X。基金持仓更新于Y。」两段。"""
        html = self._html(FakeHoldingsRef())
        self.assertIn('<a href="/history/fundposition/SH513050">仓位估算</a>'
                      "值使用1.00。", html)
        self.assertIn('<a href="/holdings/SH513050">基金持仓</a>'
                      "更新于2026-10-22。", html)

    def test_holdings_branch_renders_net_value_history(self):
        """持仓型的估值表之后跟净值记录（QDII 那支是校准记录表）。

        ``EchoHoldingsEstParagraph`` 末尾调
        ``EchoNetValueHistoryParagraph($ref, false, 0, 1)`` —— 只摆最新一天。
        """
        html = self._html(FakeHoldingsRef())
        self.assertIn("netvaluehistory-table", html)
        self.assertIn("净值记录", html)

    def test_holdings_branch_uses_holdings_ref_for_est_table(self):
        """估值表要用**持仓引用**建（不是 QDII 引用）。"""
        ref = FakeHoldingsRef()
        self._html(ref)
        self.assertEqual(len(self.fundest.refs_seen), 1)
        self.assertIs(self.fundest.refs_seen[0], ref)

    def test_qdii_branch_when_no_holdings(self):
        """没有持仓明细 → 不出现「基金持仓」链接，也不出净值记录段。"""
        html = self._html(None)
        self.assertNotIn("/holdings/SH513050", html)
        self.assertNotIn("netvaluehistory-table", html)

    def test_exception_falls_back_without_500(self):
        """取引用抛异常 → 退化成 QDII 分支，整页仍然 200。"""
        client = self._app()

        def boom(_symbol):
            raise RuntimeError("数据库连不上")

        self.app.extensions["holdings_ref_builder"] = boom
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("/holdings/SH513050", response.get_data(as_text=True))

    # -- 「仓位估算」链接的判据 ------------------------------------------
    def test_position_link_ignores_fund_position_gate(self):
        """「仓位估算」无条件给链接 —— 不能用 ``_has_fund_position`` 的判据。

        PHP 这句走 ``_getFundPositionStr()``（``fundestparagraph.php:129-135``），
        它调 ``GetFundPositionLink()`` **不带判据**；``GetFundLinks()`` 里那个
        ``$bQdii || ... || $bLofMix`` 的判据属于**页尾链接行**，是另一处。

        生产端 ``sh513050cn.php`` 上的「仓位估算」就是链接，而 SH513050 两个
        谓词都为假 —— 用判据会把这一处弄成死文字。
        """
        self.assertFalse(in_array_qdii(self.SYMBOL))
        self.assertFalse(in_array_lof_mix(self.SYMBOL))
        html = self._html(FakeHoldingsRef())
        self.assertIn('<a href="/history/fundposition/SH513050">仓位估算</a>', html)

    # -- 「穷举持仓比例」------------------------------------------------
    def test_exhaustive_part_only_when_few_holdings(self):
        """``CountHoldings() <= 4`` 才补第三段（``fundestparagraph.php:168``）。"""
        few = self._html(FakeHoldingsRef(count=4))
        many = self._html(FakeHoldingsRef(count=5))
        self.assertIn("穷举持仓比例", few)
        self.assertNotIn("穷举持仓比例", many)

    def test_exhaustive_part_is_plain_text(self):
        """穷举页本站点没有路由 → 退化成纯文字，不留死链。"""
        html = self._html(FakeHoldingsRef(count=3))
        self.assertIn("穷举持仓比例", html)
        self.assertNotIn('<a href="/exhaustive', html)

    # -- 渲染 ----------------------------------------------------------
    def test_note_is_not_escaped(self):
        """整句是 HTML，必须按原文渲染（CK2b 那个 ``first_cell_is_html`` 坑）。"""
        html = self._html(FakeHoldingsRef())
        self.assertNotIn("&lt;a href", html)


if __name__ == "__main__":
    unittest.main()
