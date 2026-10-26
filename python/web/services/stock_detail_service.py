"""非基金详情页（``/stock/<symbol>``）的段落组装。

对应 ``woody/res/php/_mystock.php:144-170`` 的 **else 分支** ——
``_echoMyStockData()`` 四个分支里的最后一个：

=====  ==========================================  ==================
分支    条件                                         承载页面
=====  ==========================================  ==================
1      ``$ref->IsFundA()``                          ``/fund/<symbol>``（CK1）
2      ``StockGetFundPairReference()``              ``/stock/<symbol>``（CK2b）
3      ``StockGetHoldingsReference()``              CK4
4      **else**（本模块）                            ``/stock/<symbol>``
=====  ==========================================  ==================

本模块现在装**三个**分支：

* :meth:`StockDetailService.get_fund_pair_detail` —— 第 2 分支（CK2b）
* :meth:`StockDetailService.get_holdings_detail` —— 第 3 分支（CK4，含 CK4-E 的净值记录段落）
* :meth:`StockDetailService.get_detail` —— 第 4 分支（CK3）

两者共用同一个 ``/stock/<symbol>`` 路由，由路由层按"有没有 ``fundpair``
记录"分流 —— 与 PHP 的 ``if / else if`` 顺序一致。

第 2 分支的源码照抄如下：

.. code-block:: php

    else if ($fund_pair_ref = StockGetFundPairReference($strSymbol))
    {
        EchoFundArrayEstParagraph(array($fund_pair_ref));
        EchoFundListParagraph(array($fund_pair_ref));
        EchoFundPairSmaParagraph($fund_pair_ref);
        EchoFundHistoryParagraph($fund_pair_ref);
    }

四段**都做了**。第三段（``EchoFundListParagraph``）与第四段
（``EchoFundHistoryParagraph``）落在 :meth:`get_fund_pair_detail` 的
``fundlist`` / ``fundhistory`` 两个键上 —— 它们各自是一张新表（跟踪表 /
净值估计对照表），取数分别复用 ``qdii_fund_list.fund_reference_table`` 与
``detail_sections.build_fund_history``，本模块只做装配。

第 4 分支的源码照抄如下：

.. code-block:: php

    list($ab_ref, $ah_ref, $adr_ref) = StockGetPairReferences($strSymbol);
    if ($ab_ref)                EchoAbParagraph(array($ab_ref));
    if ($ah_ref)                EchoAhParagraph(array($ah_ref));
    if ($adr_ref)               EchoAdrhParagraph(array($adr_ref));
    if ($ref->IsSymbolA())      EchoTradingParagraph($ref, $ah_ref, $adr_ref);

    if ($ah_ref)
    {
        EchoAhPairSmaParagraph($ah_ref);
        EchoFundPairSmaParagraph($ah_ref);
        if ($adr_ref)           EchoFundPairSmaParagraph($adr_ref, '');
    }
    else if ($adr_ref)          EchoFundPairSmaParagraph($adr_ref);
    else
    {
        if ($strSymbol == 'fx_susdcnh') EchoSmaParagraph($ref, false, $ref, '_callbackCnhSma');
        else                            EchoSmaParagraph($ref);
    }

    EchoNetValueCloseParagraph($ref);       // 三个分支走完都会落到这里
    EchoFundShareParagraph($ref);
    EchoStockHistoryParagraph($ref);

## 关于演示库里没有 AB/AH/ADR 数据这件事

``abpair`` / ``ahpair`` / ``adrpair`` 三张表在演示库里**是空的**
（只有 ``fundpair`` 有 8 行），所以 ``StockGetPairReferences()`` 会返回
``(None, None, None)``，三张对比表与 ``ah_ref`` / ``adr_ref`` 相关的均线表
都不会出现。

**这不是 bug**：PHP 那边同样靠 ``if ($ah_ref)`` 守卫，没数据就整段不输出。
等库里有了配对记录，这几段会自己冒出来，本模块不用改。

## A 股五档交易表（曾记为"刻意不做"，本轮补上）

``EchoTradingParagraph($ref, $ah_ref, $adr_ref)``（``_mystock.php:150``）当时
留在缺口里，两个理由都已过期：

1. ``trading_service.get_table()``（generic 版）早就写好，
   ``test_trading.py`` 有二十多例盯着它 —— 缺的只是**接线**；
2. "演示库里没有非基金 A 股"也过期了：P0.1 符号入库后 ``stock`` 表里有
   **52 只** A 股符号走这一分支（恒生科技 ETF / H 股 ETF 那一大批，
   它们不在 qdii 分组里所以详情页落在 ``/stock``），且快照表里带着真实五档。

本轮接上，守卫照 PHP 的 ``IsSymbolA()`` —— 外汇 / 指数 / 外盘期货不摆。

数据源与服务都由调用方注入，本模块不直接连库 —— 与
``routes/fund.py`` 里 ``_build_*`` 的套路一致，测试里可以整个顶掉。
"""

from __future__ import annotations

from typing import Any

from web.bootstrap import install_paths

install_paths()

from calc.futurepremium import get_future_interest_premium, php_round  # noqa: E402
from ref.holdingsref import StockGetHoldingsReference  # noqa: E402
from ref.refobj import StockGetFundPairReference, StockGetPairReferences  # noqa: E402
from ref.stocklink import (  # noqa: E402
    EXHAUSTIVE_HOLDINGS_DISPLAY,
    FUND_POSITION_DISPLAY,
    HOLDINGS_DISPLAY,
    get_exhaustive_holdings_link,
    get_fund_position_link,
    get_holdings_link,
)
from sql.historytable import php_number_format  # noqa: E402
from web.services.detail_sections import (  # noqa: E402
    build_fund_history,
    build_netvalue_close,
    build_shares,
    build_stock_history,
)
from web.services.pair_service import build_pair_table  # noqa: E402
from web.services.qdii_fund_list import fund_reference_table  # noqa: E402


#: ``_callbackCnhSma``（``_mystock.php:108``）里写死的两个常量。
#: 负利率与固定到期日都是当时那笔美元兑人民币掉期的合约参数，照抄。
CNH_SMA_RATE = -0.0190
CNH_SMA_END_DATE = "2025-01-13"

#: ``_mystock.php:161`` 只有这一个符号走 ``_callbackCnhSma`` 分支。
CNH_SYMBOL = "fx_susdcnh"

#: 三张对比表的标题，对应 PHP 的三个 ``*_COMPARE_DISPLAY`` 常量
#: （``php/stocklink.php`` 的 ``GetAbCompareLink`` / ``GetAhCompareLink`` /
#: ``GetAdrhCompareLink``）。PHP 那边标题里还带一个指向分类页的链接
#: （``ahcompare`` / ``adrhcompare`` 页面），本站点还没有那两页，
#: 所以这里只留文字。
PAIR_TITLES = {
    "ab": "AB对比",
    "ah": "AH对比",
    "adr": "ADR和H股对比",
}


def callback_cnh_sma(ref: Any, est: Any = False) -> Any:
    """``_callbackCnhSma($ref, $strEst)``（``_mystock.php:104``）。

    .. code-block:: php

        if ($strEst)
        {
            $f = round(2000.0 * floatval($strEst) * GetFutureInterestPremium(-0.0190, '2025-01-13'));
            return number_format($f / 2000.0, 4);
        }
        return $ref;

    两个容易记错的细节：

    1. **先乘 2000 再 round 到整数、最后才除回去** —— 等价于"保留到
       0.0005 的整倍数"，不是简单的四位小数。这个放大-取整-缩小的写法把
       汇率换算的精度钉在半厘上，改成就地 round 会差最后一分钱。
    2. ``$strEst`` 为假值时**返回 ref 自己**（不是空串），所以表头那一格
       显示的是标的价格而不是空格。

    取不到期货溢价时按 PHP 的 ``false`` 处理：返回 ``ref`` 而不是崩掉。
    """
    if not est:
        return ref
    premium = get_future_interest_premium(CNH_SMA_RATE, CNH_SMA_END_DATE)
    if premium is None:
        return ref
    try:
        scaled = php_round(2000.0 * float(est) * float(premium))
    except (TypeError, ValueError):
        return ref
    return _number_format(scaled / 2000.0, 4)


def _number_format(value: Any, digits: int = 4) -> str:
    """``number_format($fVal, $iDigits)``：固定小数位 + 千分位。

    与 ``sma_service._format_number`` 是同款（两处都对应 PHP 的
    ``number_format``）。**不要**换成
    ``historytable.format_number_display`` —— 那个会去掉尾随零，
    而 ``number_format`` 是**保留**位数的，``7.1200`` 不会被写成 ``7.12``。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return "{:,.{d}f}".format(number, d=int(digits))


def _is_symbol_a(ref: Any) -> bool:
    """鸭子类型版 ``IsSymbolA()``（``php/stock/stocksymbol.php:600``）。

    ``StockReference`` 继承 ``StockSymbol``，方法直接在引用对象上；
    两个名字都探（snake_case 主名 + camelCase 别名，见 AGENTS.md 命名约定）。
    探测不到时返回 ``False`` —— 与 PHP"不是 A 股"同效果：不摆那张表。

    注意返回值是**数字串或 False**（PHP 原样），所以必须 ``bool()`` 一下：
    ``'600000'`` 是真值，``False`` 是假值，直接 ``if`` 也用得对，
    但显式转换能让"这个方法返回什么"在调用处一目了然。
    """
    for name in ("is_symbol_a", "IsSymbolA"):
        method = getattr(ref, name, None)
        if callable(method):
            try:
                return bool(method())
            except Exception:
                return False
    return False


#: ``fundestparagraph.php:168``：``if ($ref->CountHoldings() <= 4)`` ——
#: 持仓超过这么多就不给"穷举持仓比例"链接（未知数太多，反推不出比例）。
#: 上限同时被穷举页自己用（``_echoExhaustiveHoldingsParagraph`` 的
#: ``if ($iCount <= 4)`` 决定后两张表算不算），两处必须一致。
EXHAUSTIVE_HOLDINGS_LIMIT = 4


def _invoke_ref(ref: Any, snake: str, camel: str) -> Any:
    """鸭子类型调引用对象的方法，先 snake_case 后 camelCase。

    持仓引用可能是 :class:`ref.holdingsref.HoldingsReference`，也可能是测试替身
    或别的实现，两种命名都得认（与 ``fundest_service._invoke`` 同一套路子）。
    """
    method = getattr(ref, snake, None) or getattr(ref, camel, None)
    return None if method is None else method()


def _note_link(href: Any, text: str) -> str:
    """有 href 就包 ``<a>``，没有就纯文字。

    PHP 那三个链接（``stocklink`` 一族）恒给得出 URL；Flask 站点上由路由注入
    ``url_for`` 结果（三个键 2026-10-26 起全部接上）。传 ``None`` 时退化成
    纯文字 —— 少一个链接只是少一处可点，**不能少一句文案**，PHP 的句子结构是固定的。
    """
    return '<a href="%s">%s</a>' % (href, text) if href else text


class StockDetailService:
    """把 ``_mystock.php`` 第 4 分支的段落整理成模板要的 dict。"""

    def __init__(self, source: Any = None, wide: bool = False, link_builder: Any = None):
        #: 数据源：``StockGetPairReferences`` 要用它查四张配对表
        self.source = source
        #: ``LayoutUseWide()`` —— 宽屏时表格多几列
        self.wide = wide
        #: 链接构造器：``link_builder(符号, 页面名) -> URL``。跟踪表里有两格要
        #: 挂链接（校准记录页 / 标的详情页），都得 ``url_for`` 才拼得出来，
        #: 而服务层跑在没有请求上下文的地方（脚本导出、单测），只能由装配层注入。
        #: 不给就是"没有链接"，表格照常渲染。
        self.link_builder = link_builder

    # -- 第 2 分支：StockGetFundPairReference（CK2b） ---------------------
    def get_fund_pair_detail(
        self,
        symbol: str,
        ref: Any = None,
        sma_service: Any = None,
        history_service: Any = None,
        fundest_service: Any = None,
    ) -> dict[str, Any] | None:
        """``_mystock.php:131-136`` 的 ``fund_pair_ref`` 分支。

        ``ref`` 是 :class:`~ref.refobj.FundPairReference`；**没有配对记录时返回
        ``None``**，由调用方决定是退化到第 4 分支还是整页空。

        ## 四段的顺序照抄 PHP

        1. ``est``        —— ``EchoFundArrayEstParagraph``（``:132``）；
        2. ``fundlist``   —— ``EchoFundListParagraph``（``:133``）；
        3. ``fund_pair_sma`` —— ``EchoFundPairSmaParagraph``（``:134``）；
        4. ``fundhistory``   —— ``EchoFundHistoryParagraph``（``:135``）。

        这一段能画出东西，靠的是 CK2b 迁过来的那四个方法 ——
        ``get_official_net_value`` / ``get_fair_net_value`` /
        ``get_realtime_net_value`` / ``get_official_date``。``fundest_service``
        正是靠 ``_invoke`` 探测前面两个来决定**要不要多画两列**（参考 EST /
        实时 EST）：只有官方一档时表格是 3 列，有参考/实时时是 5 或 7 列。
        换句话说，**这一层的迁移在页面上是肉眼可见的**，不是"测试绿了但页面没变"。

        ``fundlist`` 用的是 :func:`~web.services.qdii_fund_list.fund_reference_table`
        —— ``EchoFundListParagraph()`` **不带回调**那一版。``qdii_fund_list`` 里
        原有的 ``fund_list_table`` 并不是"接不上"：PHP 里它们本来就是同一个函数
        的两个分支（末列对冲值 / 末列参考值），本版把不带回调那支也补齐了。

        ``fundhistory`` 走 :func:`~web.services.detail_sections.build_fund_history`，
        与 ``/history/fundhistory/<sym>`` 同一张表 —— PHP 那边详情页与独立页
        也是共用 ``_echoHistoryTableData()`` 的，没有第二套取数。
        """
        if ref is None:
            ref = self._build_fund_pair_ref(symbol)
        if ref is None:
            return None

        return {
            "symbol": symbol,
            #: 分支标记。**必须有**：两个分支的 dict 键互不相交，而 Jinja 对
            #: 缺失键的**链式**访问（``detail.sma.ah_pair``）会抛
            #: ``UndefinedError`` 而不是返回假值 —— 模板没法靠 if 蒙混。
            "branch": "fundpair",
            "ref": ref,
            #: ``EchoFundArrayEstParagraph(array($fund_pair_ref))``
            "est": self._fund_est_table(fundest_service, ref),
            #: ``EchoFundListParagraph(array($fund_pair_ref))`` —— 末列是参考值
            "fundlist": self._fund_list_table(ref),
            #: ``EchoFundPairSmaParagraph($fund_pair_ref)``
            "fund_pair_sma": self._fund_pair_sma(sma_service, ref),
            #: ``EchoFundHistoryParagraph($fund_pair_ref)`` —— 净值 + 估计值对照
            "fundhistory": build_fund_history(history_service, symbol),
            "stockhistory": build_stock_history(history_service, symbol),
            "netvalueclose": build_netvalue_close(history_service, symbol),
            "shares": build_shares(history_service, symbol),
        }

    def _build_fund_pair_ref(self, symbol: str) -> Any:
        """``StockGetFundPairReference($strSymbol)``；没配对记录返回 ``None``。

        包 ``try``：连不上库时退化成"这一段不显示"，而不是整页 500。
        """
        try:
            return StockGetFundPairReference(symbol, source=self.source)
        except Exception:
            return None

    def _fund_list_table(self, ref: Any) -> Any:
        """``EchoFundListParagraph(array($ref))``（``_mystock.php:133``）。

        只有**一个** ref —— PHP 传的就是 ``array($fund_pair_ref)``，问的是
        "我跟踪谁、仓位多少、上一次校准是多少"，不是"谁跟踪了我"。

        取不到数据（数据库连不上、``lastcalibration`` 表里没有这一行）时
        ``fund_reference_table`` 仍然会摆出一行，只是末两格空 —— 与 PHP 一致，
        PHP 那边也没有"这行是空的就不摆"的守卫。
        """
        try:
            return fund_reference_table(
                [ref], source=self.source, link_builder=self.link_builder
            )
        except Exception:
            #: 一张装饰性的表不该让整页 500
            return None

    @staticmethod
    def _fund_est_table(service: Any, ref: Any) -> Any:
        """``EchoFundArrayEstParagraph(array($ref))``（``fundestparagraph.php:124``）。"""
        if service is None:
            return None
        try:
            return service.get_table([ref])
        except Exception:
            return None

    @staticmethod
    def _fund_pair_sma(service: Any, ref: Any) -> Any:
        """``EchoFundPairSmaParagraph($ref)``；拿不到就 ``None``。

        注意这里**不传标题**：PHP 的 ``EchoFundPairSmaParagraph`` 默认带一个
        ``GetFundPairSmaLink()`` 链接做标题，本站点还没有那一分类页，
        所以省略（与 :meth:`_sma_sections` 里给对比表传
        ``PAIR_TITLES`` 的处理不同 —— 那边的标题是纯文字，不带链接）。
        """
        if service is None:
            return None
        try:
            return service.fund_pair_table(ref)
        except Exception:
            return None

    # -- 第 3 分支：StockGetHoldingsReference（CK4） ----------------------
    def get_holdings_detail(
        self,
        symbol: str,
        ref: Any = None,
        holdings_ref: Any = None,
        sma_service: Any = None,
        history_service: Any = None,
        fundest_service: Any = None,
        links: dict = None,
    ) -> dict[str, Any] | None:
        """``_mystock.php:138-142`` 的 ``holdings_ref`` 分支。

        ## 三段照抄 PHP

        ```php
        else if ($holdings_ref = StockGetHoldingsReference($strSymbol))
        {
            EchoHoldingsEstParagraph($holdings_ref);   // 估值表 + 说明
            EchoSmaParagraph($ref);                    // 注意是 $ref 不是 $holdings_ref
            EchoFundHistoryParagraph($holdings_ref);
        }
        ```

        **``EchoSmaParagraph($ref)`` 用的是通用引用** ``$ref`` —— 不是持仓引用。
        均线要的是场内价格（``$ref`` 的行情），持仓引用算的是估算净值，
        两条路不是一回事。所以本方法同时要 ``ref`` 与 ``holdings_ref`` 两个。

        ## 这一段和配对分支（``fundpair``）长得像，但估值来源完全不同

        ``fundpair`` 是"一只基金跟踪**一个**标的"，估值 = 标的价 × 校准值；
        本分支是"一只基金持有**几十只**成分股"，估值 = 各成分股按持仓比例的
        加权涨幅（见 :mod:`ref.holdingsref`）。判断走哪条分支的依据是
        ``holdings`` 表有没有这只基金的持仓明细行。

        ## 净值记录段落在估值表之后

        ``EchoHoldingsEstParagraph()`` 末尾还调了
        ``EchoNetValueHistoryParagraph($ref, false, 0, 1)``
        （``fundestparagraph.php:171``）—— "净值 + 汇率 + 仓位"的历史表，
        只摆**最新一天**。PHP 页面上的顺序是：估值表 → 净值记录 → 均线。
        """
        if holdings_ref is None:
            holdings_ref = self._build_holdings_ref(symbol)
        if holdings_ref is None:
            return None
        if ref is None:
            ref = self._build_ref(symbol)

        return {
            "symbol": symbol,
            "branch": "holdings",
            "ref": holdings_ref,
            #: ``EchoHoldingsEstParagraph`` 的三段：估值表 + 说明 + 净值记录。
            #: 与 ``/holdings/<symbol>`` 页共用同一份装配（见
            #: :func:`get_holdings_est_sections`），``links`` 透传给说明文字。
            **get_holdings_est_sections(
                symbol,
                holdings_ref,
                fundest_service=fundest_service,
                history_service=history_service,
                links=links,
            ),
            #: ``EchoSmaParagraph($ref)`` —— 通用引用，不是持仓引用
            "sma": self._sma_sections(symbol, ref, None, None, sma_service),
            "stockhistory": build_stock_history(history_service, symbol),
            "netvalueclose": build_netvalue_close(history_service, symbol),
            "shares": build_shares(history_service, symbol),
        }

    @staticmethod
    def _net_value_paragraph(symbol: str, holdings_ref: Any, history_service: Any) -> Any:
        """``EchoNetValueHistoryParagraph($ref, false, 0, 1)``。

        ``num=1`` 照抄 PHP（``fundestparagraph.php:171``）—— 持仓型基金的估值
        本身就以"最新净值"为基准，摆一整页历史没有信息量。
        """
        if history_service is None:
            return None
        try:
            return history_service.net_value_paragraph(symbol, holdings_ref, num=1)
        except Exception:
            return None

    def _build_holdings_ref(self, symbol: str) -> Any:
        """``StockGetHoldingsReference($strSymbol)``；不是持仓型返回 ``None``。

        包 ``try``：连不上库时退化成"这一段不显示"，而不是整页 500
        （与 :meth:`_build_fund_pair_ref` 同一条约定）。
        """
        try:
            return StockGetHoldingsReference(symbol, source=self.source)
        except Exception:
            return None

    @staticmethod
    def _holdings_est_note(ref: Any, links: dict = None) -> str:
        """``EchoHoldingsEstParagraph()`` 里那句说明（``fundestparagraph.php:163-168``）。

        生产端 ``holdingscn.php?symbol=SH513050`` 上是这样一句::

            <a href=".../fundpositioncn.php?symbol=SH513050">仓位估算</a>值使用1.00。
            <font color=blue>基金持仓</font>更新于2026-10-13。

        两个链接 + 一个日期，其中"穷举持仓比例"那个**只在持仓数 <= 4 时出现**
        （``:168``）—— 成分股多了就解不出比例了，摆出来也没用。

        **链接是 HTML 字符串**，模板那头必须按原文渲染（不能转义），否则页面上
        会看到 ``<a href=...>`` 的字面文本。

        ## ``links`` 注入：两套链接来源

        * ``links=None``（默认）—— 走 :mod:`ref.stocklink` 的 **PHP 形 URL**
          （``/woody/res/xxx.php?symbol=…``）。那是迁移替身，Flask 站点上点不动，
          保留它是为了既有测试与调用方不变；
        * ``links={...}`` —— 路由层注入 Flask 的 ``url_for`` 结果，键为
          ``position`` / ``holdings`` / ``exhaustive``（三个键 2026-10-26 起
          都有路由）。**缺哪个键，哪一段就退化成纯文字** —— 这是给
          "某个页面暂时没有路由"留的降级口子。

        仓位走 :func:`sql.historytable.php_number_format`（PHP 的
        ``number_format`` 口径，保留尾零）—— 与 ``fundest_service`` 里同一句
        「值使用0.95。」用的是同一个函数，两处口径必须一致。
        """
        symbol = _invoke_ref(ref, "get_symbol", "GetSymbol")
        position = _invoke_ref(ref, "get_position", "GetPosition") or 1.0
        date = _invoke_ref(ref, "get_holdings_date", "GetHoldingsDate") or ""
        few_holdings = (_invoke_ref(ref, "count_holdings", "CountHoldings") or 0) <= EXHAUSTIVE_HOLDINGS_LIMIT

        if links is None:
            position_link = get_fund_position_link(symbol)
            holdings_link = get_holdings_link(symbol)
            exhaustive_link = get_exhaustive_holdings_link(symbol) if few_holdings else ""
        else:
            position_link = _note_link(links.get("position"), FUND_POSITION_DISPLAY)
            holdings_link = _note_link(links.get("holdings"), HOLDINGS_DISPLAY)
            exhaustive_link = (_note_link(links.get("exhaustive"), EXHAUSTIVE_HOLDINGS_DISPLAY)
                               if few_holdings else "")

        text = "%s值使用%s。" % (position_link, php_number_format(position, 2))
        text += "%s更新于%s。" % (holdings_link, date)
        if exhaustive_link:
            text += exhaustive_link
        return text

    # -- 第 4 分支：else（CK3） -------------------------------------------
    def get_detail(
        self,
        symbol: str,
        ref: Any = None,
        sma_service: Any = None,
        history_service: Any = None,
        trading_service: Any = None,
    ) -> dict[str, Any] | None:
        """组装一页；**引用对象建不出来时返回 ``None``** 让上层 404/整段跳过。

        ``ref`` / ``sma_service`` / ``history_service`` / ``trading_service``
        都可以由调用方注入（路由层从 ``current_app.extensions`` 取，测试里整个顶掉）。
        """
        if ref is None:
            ref = self._build_ref(symbol)
        if ref is None:
            return None

        ab_ref, ah_ref, adr_ref = self._pair_refs(symbol)

        return {
            "symbol": symbol,
            #: 分支标记，用途见 :meth:`get_fund_pair_detail`
            "branch": "generic",
            "ref": ref,
            "ab": self._pair_table(ab_ref, "ab"),
            "ah": self._pair_table(ah_ref, "ah"),
            "adr": self._pair_table(adr_ref, "adr"),
            #: ``_mystock.php:150`` —— 摆在三张对比表之后、均线之前。
            #: 非 A 股、或没有挂单量时是 ``None``，模板据此整段不渲染。
            "trading": self._trading_table(ref, ah_ref, adr_ref, trading_service),
            "sma": self._sma_sections(symbol, ref, ah_ref, adr_ref, sma_service),
            #: ``_mystock.php:167-169`` —— 三个分支走完都会落到这里，
            #: 与 ``/fund/<symbol>`` 共用同一批构建器。
            "stockhistory": build_stock_history(history_service, symbol),
            "netvalueclose": build_netvalue_close(history_service, symbol),
            "shares": build_shares(history_service, symbol),
        }

    # -- 内部 -------------------------------------------------------------
    def _build_ref(self, symbol: str) -> Any:
        """建通用引用对象（非基金），失败返回 ``None``。

        用 ``build_stock_references`` 而不是 QDII 那套：非基金符号没有
        "三档估值"，``build_qdii_reference`` 对它们会直接抛异常。
        """
        try:
            from web.services.reference_service import build_stock_references

            refs = build_stock_references([symbol], source=self.source)
        except Exception:
            return None
        return refs[0] if refs else None

    def _pair_refs(self, symbol: str) -> tuple:
        """``StockGetPairReferences($strSymbol)``；查表失败时全 ``None``。

        包 ``try`` 而非让它抛：配对表缺失/连不上库时不该让整页 500，
        退化成"三张对比表都不显示"即可（与 PHP 拿不到 false 的效果一致）。
        """
        try:
            return StockGetPairReferences(symbol, self.source)
        except Exception:
            return (None, None, None)

    def _pair_table(self, ref: Any, kind: str) -> Any:
        """``Echo{Ab,Ah,Adrh}Paragraph(array($ref))``；没 ref 就 ``None``。"""
        if ref is None:
            return None
        try:
            return build_pair_table(
                [ref],
                kind=kind,
                wide=self.wide,
                title=PAIR_TITLES.get(kind, ""),
                source=self.source,
            )
        except Exception:
            return None

    def _trading_table(self, ref: Any, ah_ref: Any, adr_ref: Any, service: Any) -> Any:
        """``_mystock.php:150`` ``if ($ref->IsSymbolA()) EchoTradingParagraph(...)``。

        两道守卫叠在一起：

        1. **``IsSymbolA()``** —— PHP 只给 A 股摆这张表：外汇（``fx_*``）、
           指数（``^NDX``）、外盘期货（``hf_*``）都不摆；
        2. 服务层自己的 ``_checkTradingQuantity``（没有挂单量就整段不摆）——
           在 ``trading_service._table`` 里，这里不重复判。

        ``ah_ref`` / ``adr_ref`` 原样传下去：PHP 那边它们决定要不要多两列
        "相对 H 股 / ADR 的溢价"。演示库三张配对表是空的，这两个参数恒为
        ``None``，列数退化成基础三列（交易 / 价格 / 数量）。
        """
        if service is None:
            return None
        if not _is_symbol_a(ref):
            return None
        try:
            return service.get_table(ref, ah_ref, adr_ref)
        except Exception:
            # 与其它段落一致：一张装饰性的盘口表不该让整页 500
            return None

    def _sma_sections(
        self,
        symbol: str,
        ref: Any,
        ah_ref: Any,
        adr_ref: Any,
        service: Any,
    ) -> dict[str, Any]:
        """``_mystock.php:152-165`` 那段三分支的均线逻辑。

        返回四个槽位，**用不上的保持 ``None``** —— 让模板按槽位判断，
        而不是把三种情况压成一个键。
        """
        empty = {"ah_pair": None, "fund_pair": None, "adr_extra": None, "plain": None}
        if service is None:
            return empty

        try:
            if ah_ref is not None:
                return {
                    "ah_pair": service.ah_pair_table(ah_ref),
                    "fund_pair": service.fund_pair_table(ah_ref),
                    #: PHP 这里传的是**空串标题**（``EchoFundPairSmaParagraph($adr_ref, '')``），
                    #: 与 :meth:`sma_table` 里 ``memo is False`` 那条严格判断配套。
                    "adr_extra": (
                        service.fund_pair_table(adr_ref, "") if adr_ref is not None else None
                    ),
                    "plain": None,
                }
            if adr_ref is not None:
                return {
                    "ah_pair": None,
                    "fund_pair": service.fund_pair_table(adr_ref),
                    "adr_extra": None,
                    "plain": None,
                }
            if symbol == CNH_SYMBOL:
                return {
                    "ah_pair": None,
                    "fund_pair": None,
                    "adr_extra": None,
                    #: 只有 ``fx_susdcnh`` 这个符号带 ``_callbackCnhSma``
                    "plain": service.sma_table(ref, False, ref, callback_cnh_sma),
                }
            return {
                "ah_pair": None,
                "fund_pair": None,
                "adr_extra": None,
                "plain": service.sma_table(ref),
            }
        except Exception:
            #: 某张均线表算不出来时按"整段跳过"处理，不影响页面其它段落
            return empty


def default_stock_detail_service_factory() -> StockDetailService:
    """默认工厂；``source`` 由路由层注入（连库那一步在 web 侧完成）。"""
    return StockDetailService()


def get_holdings_est_sections(
    symbol: str,
    holdings_ref: Any,
    fundest_service: Any = None,
    history_service: Any = None,
    links: dict = None,
) -> dict[str, Any]:
    """``EchoHoldingsEstParagraph($ref)``（``fundestparagraph.php:160-172``）的三段。

    ==============  ==========================================  ====================
    键              PHP                                        取不到时
    ==============  ==========================================  ====================
    ``est``         ``_echoFundEstParagraph``（估值表）            ``None``
    ``est_note``    表头那句说明（仓位 + 持仓日期）               恒有（纯文本也出）
    ``netvaluehistory``  ``EchoNetValueHistoryParagraph``       ``None``（整段不摆）
    ==============  ==========================================  ====================

    **两个页面共用**：``/stock/<symbol>`` 的第 3 分支（CK4）与
    ``/holdings/<symbol>``（holdingscn.php）。PHP 里也是同一个函数被两处调用，
    区别只在 ``/holdings`` 页在它之后还摆参考数据表与七列持仓测算表。

    ``links`` 透传给 :meth:`StockDetailService._holdings_est_note`：
    见那边关于"PHP 形链接 vs Flask 链接"的说明。
    """
    return {
        "est": StockDetailService._fund_est_table(fundest_service, holdings_ref),
        "est_note": StockDetailService._holdings_est_note(holdings_ref, links),
        #: ``EchoNetValueHistoryParagraph($holdings_ref, false, 0, 1)``。
        #: 没有净值记录时 PHP 整段不出现（``CountNetValue() == 0`` 就 return），
        #: 所以这里可能是 ``None``，模板要判空。
        "netvaluehistory": StockDetailService._net_value_paragraph(
            symbol, holdings_ref, history_service
        ),
    }
