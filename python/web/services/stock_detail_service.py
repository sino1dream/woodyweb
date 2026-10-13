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

本模块现在装**两个**分支：

* :meth:`StockDetailService.get_fund_pair_detail` —— 第 2 分支（CK2b）
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

本版做了前两段与第四段，**第三段（``EchoFundListParagraph``）与
``EchoFundHistoryParagraph`` 留作缺口** —— 见 :meth:`get_fund_pair_detail`
的说明：它们各自要一张新的表（跟踪表 / 净值估计对照表），属于"段落迁移"
而不是"引用层迁移"，不必堵在 CK2b 这条线上。

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

## 刻意不做的一段

``EchoTradingParagraph($ref, $ah_ref, $adr_ref)``（A 股带配对溢价的交易块）
**没做**：``trading_service`` 目前只有 ``fund_table``（基金版），没有 A 股
generic 版本；而且演示库的 ``stock`` 表里一个非基金 A 股都没有，做了也验证不了。
留作 CK3 的后续缺口。

数据源与服务都由调用方注入，本模块不直接连库 —— 与
``routes/fund.py`` 里 ``_build_*`` 的套路一致，测试里可以整个顶掉。
"""

from __future__ import annotations

from typing import Any

from web.bootstrap import install_paths

install_paths()

from calc.futurepremium import get_future_interest_premium, php_round  # noqa: E402
from ref.refobj import StockGetFundPairReference, StockGetPairReferences  # noqa: E402
from web.services.detail_sections import (  # noqa: E402
    build_netvalue_close,
    build_shares,
    build_stock_history,
)
from web.services.pair_service import build_pair_table  # noqa: E402


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


class StockDetailService:
    """把 ``_mystock.php`` 第 4 分支的段落整理成模板要的 dict。"""

    def __init__(self, source: Any = None, wide: bool = False):
        #: 数据源：``StockGetPairReferences`` 要用它查四张配对表
        self.source = source
        #: ``LayoutUseWide()`` —— 宽屏时表格多几列
        self.wide = wide

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

        这一段能画出东西，靠的是 CK2b 迁过来的那四个方法 ——
        ``get_official_net_value`` / ``get_fair_net_value`` /
        ``get_realtime_net_value`` / ``get_official_date``。``fundest_service``
        正是靠 ``_invoke`` 探测前面两个来决定**要不要多画两列**（参考 EST /
        实时 EST）：只有官方一档时表格是 3 列，有参考/实时时是 5 或 7 列。
        换句话说，**这一层的迁移在页面上是肉眼可见的**，不是"测试绿了但页面没变"。

        刻意不做的两段：

        * ``EchoFundListParagraph(array($fund_pair_ref))``（``:133``）—— 跟踪表
          要一行"符号 / 跟踪 / 仓位 / 校准值 / 日期 / 参考值"。现有的
          ``qdii_fund_list.fund_list_table`` 是**另一形态**（给"谁跟踪了我的
          中间层标的"那张杠杆表用的，末尾一列是对冲值），接不上；这里要的是
          ref 自己的持仓校准信息。
        * ``EchoFundHistoryParagraph($fund_pair_ref)``（``:135``）—— 净值 +
          估计值的历史对照表，Python 侧还没有这张表。它的数据来源倒是现成的：
          PHP 走 ``$ref->GetPairRef()``，而 ``FundPairReference.get_pair_ref()``
          CK2 就迁好了。
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
            #: ``EchoFundPairSmaParagraph($fund_pair_ref)``
            "fund_pair_sma": self._fund_pair_sma(sma_service, ref),
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

    # -- 第 4 分支：else（CK3） -------------------------------------------
    def get_detail(
        self,
        symbol: str,
        ref: Any = None,
        sma_service: Any = None,
        history_service: Any = None,
    ) -> dict[str, Any] | None:
        """组装一页；**引用对象建不出来时返回 ``None``** 让上层 404/整段跳过。

        ``ref`` / ``sma_service`` / ``history_service`` 都可以由调用方注入
        （路由层从 ``current_app.extensions`` 取，测试里整个顶掉）。
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
