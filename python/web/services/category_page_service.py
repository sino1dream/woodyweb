"""分类页服务（16 个"行业/指数分类"页共用的两张表）。

## 对应 PHP

``woody/res/biotechcn.php`` / ``hstechcn.php`` / ``qdiihkcn.php`` …… 这一族
**16 个页面文件都是 4 行壳子**：:

    <?php
    require('php/_mystockgroup.php');
    require('../../php/ui/_dispcn.php');
    ?>

真正的分派在 ``woody/res/php/_mystockgroup.php:154``：

.. code-block:: php

    _echoStockGroupArray(GetCategoryArray($strPage), LayoutUseWide());

也就是说，**页面之间的唯一差别是"符号数组"以及标题/说明两段文本**，
渲染逻辑完全共享。所以这里做成一个注册表 + 一个通用装配函数，
而不是 16 个服务 —— 与 ``/stock/<symbol>/edit/<option>`` 一个路由顶
16 个编辑页是同一个取舍。

三样数据各有出处，都不能凭印象：

=========================  ================================================================
数据                        出处
=========================  ================================================================
符号数组                    ``woody/res/php/_stocklink.php:3 GetCategoryArray()``
                            （内部再调 ``php/stock/stocksymbol.php`` 的各函数）
标题                        ``_mystockgroup.php::_getTitleStr()`` = 分组显示名 + '基金净值计算工具'
说明                        ``_mystockgroup.php::_getMetaDescriptionStr()``
=========================  ================================================================

标题与说明大量由常量拼接（``BIOTECH_GROUP_DISPLAY.'基金的'.STOCK_DISP_NETVALUE``
之类），手抄必错。本模块的值是用 ``auto/_scratch/_php_category_meta_check.php``
把源文件里的 ``$ar = array(...)`` 字面量抠出来 ``eval`` 得到的**展开结果**；
符号数组同样由 ``_php_category_check.php`` 逐例比对过（见
``README_category_pages_migration.md``）。

## 页面形状

与 ``/qdii`` 完全一致，顺序固定：

1. **参考数据** —— ``php/ui/referenceparagraph.php::EchoReferenceParagraph()``；
   列：代码(外链) / 价格 / 涨幅 / 日期 / 时间 / 名称。首格是**新浪外链**
   （``GetExternalLink()``）。
2. **估值** —— ``php/ui/fundestparagraph.php::EchoFundArrayEstParagraph()``；
   列：代码(站内链接) / 官方EST / EST日期 / 官方溢价 / [参考EST / 参考溢价] /
   [实时EST / 实时溢价]。后四列**动态出现**：``_getFundEstTableColumn()``
   扫遍整个列表，一只基金都没有参考（或实时）估值时，对应的两列就不加。

PHP 还会渲染 AB / AH / ADR 三个配对段落（``EchoAbParagraph`` 等），但**只对
非 A 股基金的符号**生效；这 16 个分类的数组清一色是 A 股场内基金
（SH/SZ 号段），那个分支一次都不会进。``qdii_page_service`` 的旧注释已经
为 qdii 论证过一次，这里对全部 16 个数组重新核对过：

* ``chinaindex`` 的说明里提到"美股 ASHR"，但 ``GetCategoryArray()`` 给的是
  ``ChinaIndexGetAshrArray()`` —— 数组里是**跟踪 ASHR 的国内基金**，不是
  ASHR 本身，所以仍然全是 A 股基金。

## 与 PHP 的差异（刻意）

* **建不出引用对象的符号跳过**，PHP 会直接 fatal。与基金详情页的既有取舍一致。
* **不渲染 ``ImgStockGroup($strPage)`` 的配图**，也不渲染 ``EchoLinks()`` 那串
  站内导航；页尾改成列出其它分类页（同一份注册表，不会与数组走散）。
"""

from __future__ import annotations

from typing import Any

from web.bootstrap import install_paths

install_paths()

from core.stocksymbol import (  # noqa: E402
    StockSymbol,
    china_future_get_symbol_array,
    china_index_get_symbol_array,
    get_all_symbol_array,
    get_china_internet_symbol_array,
    get_msci_us50_symbol_array,
    get_over_night_symbol_array,
    get_qdii_gold_symbol_array,
    get_qdii_oil_symbol_array,
    in_array_china_future,
    in_array_china_index,
    qdii_eu_get_symbol_array,
    qdii_get_oil_etf_symbol_array,
    qdii_get_qqq_symbol_array,
    qdii_get_spy_symbol_array,
    qdii_get_symbol_array,
    qdii_get_xbi_symbol_array,
    qdii_get_xop_symbol_array,
    qdii_hk_get_h_shares_symbol_array,
    qdii_hk_get_hang_seng_symbol_array,
    qdii_hk_get_symbol_array,
    qdii_hk_get_tech_symbol_array,
    qdii_jp_get_symbol_array,
    qdii_mix_get_symbol_array,
)
from ref.refobj import (  # noqa: E402
    FundPairReference,
    MysqlReference,
    build_qdii_reference,
)


def build_fund_reference(symbol: str, source: Any = None) -> Any:
    """按符号选基金引用类，对应 ``StockGetFundReference()``（``php/stock.php:317``）。

    ```php
    if ($ref = StockGetQdiiReference($strSymbol))                     return $ref;
    else if (in_arrayQdiiMix($strSymbol))                             return new HoldingsReference($strSymbol);
    else if (in_arrayChinaIndex($strSymbol) || in_arrayChinaFuture($strSymbol))  return new FundPairReference($strSymbol);
    return new FundReference($strSymbol);
    ```

    ## 为什么这里非分派不可（A股商品 / A股指数 两族）

    ``FundPairReference`` 与 ``QdiiReference`` 是**两套 EST 口径**，混用不会报错，
    只会把数字算歪或者整段消失：

    * ``FundPairReference`` 的官方 EST 直接拿**配对标的**算（``fundpairref.php:238``）：
      商品期货（黄金除外）取当日**均价** ``strVWAP``，其它取该日**结算价**，
      再 ``EstFromPair`` 除以校准值 —— 汇率引用为空时等于 1.0（本币计价）。
    * ``QdiiReference`` 走 ``fundestcalc`` 的估算器，官方 EST 依赖"中间层净值 / 指数收盘"，
      而且必须有汇率的收盘价才会算 —— 国内期货没有汇率，那条路直接折在
      "汇率缺失"兜底里读 ``fundest`` 表，读不到就 ``None``，
      于是分类页的「估值」表**整段不出现**（PHP 靠 ``GetFairNetValue()`` 的守卫跳过，
      表现一样，只是本地原本连参考数据都不全）。

    实测差异（2026-11-04 11:30，生产端 chinafuturecn.php 对照）：
    走 ``QdiiReference`` 时 6 只全部 ``None``；走 ``FundPairReference`` 后
    8.374 / 8.047 / 8.367 / 7.978 / 2.054 / 1.342 与生产端逐位一致。

    ``qdii_mix`` 那一支（``HoldingsReference``）本模块不涉及 —— 分类页里的
    QDII 混合组走的是 ``build_qdii_reference`` 里"持仓型"的既有分支，
    挪过来会改变既有页面的口径，不做。
    """
    if in_array_china_index(symbol) or in_array_china_future(symbol):
        return FundPairReference(symbol, source=source)
    return build_qdii_reference(symbol, source=source)


class CategoryNotFoundError(LookupError):
    """分类名不在注册表里。路由据此 404，不要静默退化成别的分类。"""


# ---------------------------------------------------------------------------
# 符号数组：16 个分类里有一半不是"直接调一个函数"，PHP 那边逐个查了
# ``GetCategoryArray()`` 的写法，这里照抄合并方式（**不去重**，PHP 的
# ``array_merge`` 也不去重；顺序就是页面行序）。
# ---------------------------------------------------------------------------


def _biotech_symbols():
    """``_stocklink.php``::

        array_merge(QdiiGetXbiSymbolArray(), array('SH513290'))

    SH513290 是纳指生物科技 ETF，与 XBI 组的另外两只不是同一条线，
    所以 PHP 是硬编码追加而不是放进 ``QdiiGetXbiSymbolArray()``。
    """
    return qdii_get_xbi_symbol_array() + ["SH513290"]


def _chinainternet_symbols():
    """``array_merge(GetChinaInternetSymbolArray(), array('SZ164906'))``。

    多出来的都是"没进那个数组但跟踪同一个指数"的基金。
    """
    return get_china_internet_symbol_array() + ["SZ164906"]


#: 黄金段的**显示顺序**（生产端 ``commoditycn.php`` 2026-10-30 抓取）。
#:
#: 本地 PHP 快照里 ``GetQdiiGoldSymbolArray()`` 的成员与生产端完全一致（6 只），
#: 只有排列不同 —— 生产端把 SZ164701 提到第二位、SZ161815 挪到最后。
#: 页面行序是人工比对的对象（``qdii_get_symbol_array()`` 的 docstring 里
#: 说过一遍同样的理由），所以按生产端排；**成员仍只从源函数取**，
#: 不在这里复制一份字面量。
GOLD_DISPLAY_ORDER = [
    "SZ160216",
    "SZ164701",
    "SZ160719",
    "SZ161116",
    "SZ165513",
    "SZ161815",
]


def _commodity_symbols():
    """``array_merge(ChinaFutureGetSymbolArray(), GetQdiiGoldSymbolArray())``。

    商品期货基金 + 黄金基金合成一页，两者不重叠（6 + 6 = 12）。

    黄金段按 :data:`GOLD_DISPLAY_ORDER` 排（生产端顺序）。上游数组增删时
    不抛异常：认识的按生产端序在前，新来的按源顺序追加在后面，删掉的自然消失
    —— 一个纯展示顺序不值得让整页 500。
    """
    gold = get_qdii_gold_symbol_array()
    known = [symbol for symbol in GOLD_DISPLAY_ORDER if symbol in gold]
    unknown = [symbol for symbol in gold if symbol not in GOLD_DISPLAY_ORDER]
    return china_future_get_symbol_array() + known + unknown


def _oilfund_symbols():
    """``array_merge(QdiiGetXopSymbolArray(), QdiiGetOilEtfSymbolArray(), GetQdiiOilSymbolArray())``。

    三段是三种不同的原油基金：跟踪 XOP 的、场内原油 ETF、以及 QDII 原油。
    """
    return (
        qdii_get_xop_symbol_array()
        + qdii_get_oil_etf_symbol_array()
        + get_qdii_oil_symbol_array()
    )


def _lof_symbols():
    """``_stocklink.php`` 的 ``lof`` 分支：:

        $ar = array();
        foreach (GetAllSymbolArray() as $strSymbol)
        {
            $sym = new StockSymbol($strSymbol);
            if ($sym->IsLofA())    $ar[] = $strSymbol;
        }

    与其它分类不同，这一支是**过滤**而不是直接取数组，所以结果是
    ``get_all_symbol_array()`` 保持原序的子序列。``GetAllSymbolArray()``
    在 PHP 那边也是一段 ``array_merge``（不是查库），本模块的函数与它逐项对应，
    所以两边过滤出来的成员与顺序一致。
    """
    return [symbol for symbol in get_all_symbol_array() if StockSymbol(symbol).is_lof_a()]


def _overnight_symbols():
    """``GetOverNightSymbolArray()``，但黄金那一段按生产端顺序排。

    PHP 侧就是 ``GetOverNightSymbolArray()``（``_stocklink.php`` 的 overnight
    分支），问题出在它内嵌的 ``GetQdiiGoldSymbolArray()``：本地 PHP 快照里
    6 只黄金基金的顺序是 ``SZ160216 SZ161815 SZ160719 SZ161116 SZ164701
    SZ165513``，生产端 ``overnightcn.php``（2026-11-01 抓取）是
    ``SZ160216 SZ164701 SZ160719 SZ161116 SZ165513 SZ161815`` —— **成员一致、
    只有顺序不同**，与 :func:`_commodity_symbols` 撞的是同一个坑（那一页
    2026-10-30 已经用 :data:`GOLD_DISPLAY_ORDER` 处理过一次）。

    所以这里复用同一份顺序：把黄金 6 只整体挪到"第一个黄金成员"所在的位置。
    不改 ``core.stocksymbol.get_qdii_gold_symbol_array()`` 本身 —— 那个数组是
    本地 PHP 快照的忠实移植，auto 层的对照脚本靠它；顺序是**展示**问题，
    在展示层解，与 commodity 的处置保持一致。
    """
    symbols = get_over_night_symbol_array()
    gold = get_qdii_gold_symbol_array()
    ordered = [s for s in GOLD_DISPLAY_ORDER if s in gold]
    ordered += [s for s in gold if s not in GOLD_DISPLAY_ORDER]

    result = []
    placed = False
    for symbol in symbols:
        if symbol in ordered:
            if not placed:
                result.extend(ordered)
                placed = True
            continue
        result.append(symbol)
    return result


# ---------------------------------------------------------------------------
# 注册表。键会出现在 URL 里：/group/<name>。
#
# ``display`` 是导航上显示的短名（PHP ``php/stocklink.php`` 的
# ``*_GROUP_DISPLAY`` 常量），``title`` 是 ``<h1>`` 与 ``<title>``，
# 两者不是一回事：title 恒为 display + '基金净值计算工具'，但 qdii 与
# 欧洲/港股/日本/混合 QDII 的 display 里已经带了"QDII"字样，读起来像重复，
# 这是 PHP 原文，照抄不改。
# ---------------------------------------------------------------------------

CATEGORY_PAGES = {
    "biotech": {
        "display": "生物科技",
        "title": "生物科技基金净值计算工具",
        "description": (
            "生物科技基金的净值估算。使用XBI的净值EST，目前包括"
            "SZ159502标普生物科技ETF和SZ161127标普生物科技LOF。"
        ),
        "symbols": _biotech_symbols,
        "php": "biotechcn.php",
    },
    "chinafuture": {
        "display": "A股商品",
        "title": "A股商品基金净值计算工具",
        "description": (
            "A股商品基金工具，使用国内市场黄金、白银和豆粕等大宗商品期货数据"
            "计算基金净值，同时测算无风险套利的对冲比例等数据。"
        ),
        "symbols": china_future_get_symbol_array,
        "php": "chinafuturecn.php",
    },
    "chinaindex": {
        "display": "A股指数",
        "title": "A股指数基金净值计算工具",
        "description": (
            "A股指数基金工具。计算基金净值，同时分析比较各种套利对冲方案。"
            "包括美股ASHR和多家国内基金公司的A股沪深300指数基金的配对交易等。"
        ),
        "symbols": china_index_get_symbol_array,
        "php": "chinaindexcn.php",
    },
    "chinainternet": {
        "display": "中丐互怜",
        "title": "中丐互怜基金净值计算工具",
        "description": (
            "跟踪几个不同中证海外中国互联网指数的中概互联基金们在2021年初"
            "疯狂见顶后几个月时间一路狂泻都跌成了中丐互怜，也因此跌出了QDII"
            "基金有史以来最为壮观的流动性。"
        ),
        "symbols": _chinainternet_symbols,
        "php": "chinainternetcn.php",
    },
    "commodity": {
        "display": "大宗商品和黄金",
        "title": "大宗商品和黄金基金净值计算工具",
        "description": (
            "大宗商品和黄金基金的净值估算。目前包括大致对应跟踪GSG的信诚商品"
            "(SZ165513)和银华通胀(SZ161815)。跟踪大宗商品期货的基金都有因为"
            "期货升水带来的损耗，不建议长期持有。"
        ),
        "symbols": _commodity_symbols,
        "php": "commoditycn.php",
    },
    "hangseng": {
        "display": "恒生指数",
        "title": "恒生指数基金净值计算工具",
        "description": (
            "恒生指数基金的净值估算。使用恒生指数【^HSI】计算官方EST和参考EST、"
            "使用恒生指数期货【hf_HSI】提供港股不开盘期间的实时EST。"
        ),
        "symbols": qdii_hk_get_hang_seng_symbol_array,
        "php": "hangsengcn.php",
    },
    "hshares": {
        "display": "H股中国企业指数",
        "title": "H股中国企业指数基金净值计算工具",
        "description": (
            "港交所在2017年后玩弄文字游戏把H股国企指数改成H股中国企业指数，"
            "大量加入非国有企业成分股，就是为了吸引迷信鹅厂的国内韭菜跨过香江"
            "去夺取港股定价权！"
        ),
        "symbols": qdii_hk_get_h_shares_symbol_array,
        "php": "hsharescn.php",
    },
    "hstech": {
        "display": "恒生科技指数",
        "title": "恒生科技指数基金净值计算工具",
        "description": (
            "厌倦了港交所加印花税的贪婪，本来没想跟踪恒生科技指数基金的净值。"
            "不过在无意中发现了KTEC后，觉得还是应该补上，为日后可能的跨市场"
            "套利机会做好准备。"
        ),
        "symbols": qdii_hk_get_tech_symbol_array,
        "php": "hstechcn.php",
    },
    # `lofcn.php` 与 `overnightcn.php` 是同一套壳子的另外两页，2026-10-30 那批
    # 16 个漏了它们（`GetCategoryArray()` 的 switch 里这两支一直在）。位置按
    # switch 的顺序插：lof 在 hstech 之后，overnight 在 oilfund 之后 ——
    # 字典顺序就是导航顺序，别图省事都塞到末尾。
    "lof": {
        "display": "LOF基金",
        # 注意 title **不带**"基金净值计算工具"后缀：PHP 的 `_getTitleStr()`
        # 那一栏写的是 `'lof' => LOF_ALL_DISPLAY`，与 biotech 那批拼 $strTool
        # 的写法不一样。overnight 同理。
        "title": "LOF基金",
        "description": (
            "本网站有EST的LOF基金汇总，方便申购不起ETF的穷人发现套利机会。"
            "使用拖拉机自动化软件前注意查询基金的限购状态和限购方式等具体内容。"
        ),
        "symbols": _lof_symbols,
        "php": "lofcn.php",
    },
    "mscius50": {
        "display": "MSCI美国50",
        "title": "MSCI美国50基金净值计算工具",
        "description": (
            "当易方达刚开始发这个中国特色指数基金时，我在雪球上嘲笑了这种领先"
            "美国的做法，等到汇添富继续发基金时，我发现MSCI官网上就查不到跟踪"
            "这个MSCI美国50指数的基金信息了！"
        ),
        "symbols": get_msci_us50_symbol_array,
        "php": "mscius50cn.php",
    },
    "oilfund": {
        "display": "原油",
        "title": "原油基金净值计算工具",
        "description": (
            "跟踪原油期货的基金有升水损耗，不建议长期持有。跨市场套利时，"
            "不要赌几个小时后A股QDII基金折价溢价转折点，只做连续折价或者"
            "连续溢价套利。"
        ),
        "symbols": _oilfund_symbols,
        "php": "oilfundcn.php",
    },
    "overnight": {
        "display": "义工群对比",
        # 同样不带"基金净值计算工具"后缀 —— `'overnight' => OVERNIGHT_DISPLAY`。
        "title": "义工群对比",
        "description": (
            "义工群对比LOF数据汇总，方便调试软件、对比两边软件的实时折价溢价"
            "信息以及在消息过多的时候快速筛选和定位有夜盘实时跨市场套利机会"
            "的基金。"
        ),
        "symbols": _overnight_symbols,
        "php": "overnightcn.php",
    },
    "qdii": {
        "display": "美股QDII",
        "title": "美股QDII基金净值计算工具",
        "description": (
            "美股QDII官方EST用来验证计算方式的准确性。以参考EST为准，"
            "折价不申购、溢价不赎回。上涨赚溢价、下跌赚净值，不要怂，就是干！"
        ),
        "symbols": qdii_get_symbol_array,
        "php": "qdiicn.php",
    },
    "qdiieu": {
        "display": "欧洲QDII",
        "title": "欧洲QDII基金净值计算工具",
        # 注意：这句 PHP 原文里用的是 QDII_JP_DISPLAY（'日本QDII'），
        # 不是笔误也不是我这边的转写错误 —— ``_getMetaDescriptionStr()``
        # 的 qdiieu 分支确实写着"加了日本QDII后"。照抄。
        "description": (
            "加了日本QDII后，我意识到华安基金的德国和法国指数ETF可以也用同样"
            "的模式EST，忍不住再加上这个欧洲QDII页面，看看能不能解决之前一直"
            "EST不准的问题。"
        ),
        "symbols": qdii_eu_get_symbol_array,
        "php": "qdiieucn.php",
    },
    "qdiihk": {
        "display": "港股QDII",
        "title": "港股QDII基金净值计算工具",
        "description": (
            "把港股QDII从其它QDII页面分出来，不光能够优化网站和代码结构，"
            "还能更好的体验股市三大幻觉：A股要涨、美股见顶、港股便宜！"
        ),
        "symbols": qdii_hk_get_symbol_array,
        "php": "qdiihkcn.php",
    },
    "qdiijp": {
        "display": "日本QDII",
        "title": "日本QDII基金净值计算工具",
        "description": (
            "四个跟踪日经225指数的日本QDII上市好几年来一直不愠不火，直到最近"
            "有人趁巴菲特光环和易方达因为中丐互怜缺QDII额度的机会拉场内溢价，"
            "让我觉得不能再观望下去了！"
        ),
        "symbols": qdii_jp_get_symbol_array,
        "php": "qdiijpcn.php",
    },
    "qdiimix": {
        "display": "混合QDII",
        "title": "混合QDII基金净值计算工具",
        "description": (
            "采用跟踪成分股变化的方式对同时有美股、港股和A股持仓的中丐互怜等"
            "混合QDII基金进行净值估算，这样参考EST可以反应白天港股和A股波动对"
            "净值的影响。"
        ),
        "symbols": qdii_mix_get_symbol_array,
        "php": "qdiimixcn.php",
    },
    "qqqfund": {
        "display": "纳斯达克100",
        "title": "纳斯达克100基金净值计算工具",
        "description": (
            "纳斯达克100基金的净值估算。使用纳斯达克100指数^NDX计算官方EST和"
            "参考EST，芝商所纳斯达克期货NQ计算实时EST，QQQ仅用于参考。"
        ),
        "symbols": qdii_get_qqq_symbol_array,
        "php": "qqqfundcn.php",
    },
    "spyfund": {
        "display": "标普500",
        "title": "标普500基金净值计算工具",
        "description": (
            "标普500基金的净值估算。使用标普500指数^GSPC计算官方EST和参考EST，"
            "芝商所标普500期货ES计算实时EST，SPY仅用于参考。"
        ),
        "symbols": qdii_get_spy_symbol_array,
        "php": "spyfundcn.php",
    },
}


def get_category(name: str) -> dict:
    """按分类名取定义；不存在抛 :class:`CategoryNotFoundError`。"""
    try:
        return CATEGORY_PAGES[name]
    except KeyError:
        raise CategoryNotFoundError(name)


def get_category_names() -> list:
    """全部分类名，顺序就是页面上的排列顺序（与 PHP 的 switch 同序）。"""
    return list(CATEGORY_PAGES)


def get_category_nav() -> list:
    """页尾导航用的 ``[{name, display, title, count}]``。

    只读注册表，**不连数据库** —— 数量来自符号数组本身的长度，
    与 ``routes/fund.py::_fund_group_nav()`` 同一个做法。
    """
    return [
        {
            "name": name,
            "display": definition["display"],
            "title": definition["title"],
            "count": len(definition["symbols"]()),
        }
        for name, definition in CATEGORY_PAGES.items()
    ]


def classify_symbols(symbols: list, source: Any = None) -> tuple:
    """按 PHP 的 foreach 把符号分成「参考数据」与「估值」两组。

    返回 ``(refs, funds, skipped)``：

    ================  ====================================================
    返回值            含义
    ================  ====================================================
    ``refs``          全部成功建出普通引用对象的符号（非宽屏时全进参考表）
    ``funds``         A 股基金里算得出官方净值的那批（进 EST 表）
    ``skipped``       建引用对象失败的符号，调用方可以选择记账
    ================  ====================================================

    建 ``fund`` 失败**不**影响它已经进 ``refs`` 的事实：PHP 里
    ``$arRef[] = $ref`` 在 ``IsFundA()`` 分支之外，两者互不牵连。

    注意 ``MysqlReference`` / ``build_qdii_reference`` 是**本模块的全局名**，
    单测用 ``mock.patch.object`` 打的就是这里 —— 挪动这个函数或这两个名字，
    打补丁的地方要跟着挪，否则单测会"全绿但没测到真东西"。
    """
    refs = []
    funds = []
    skipped = []
    for symbol in symbols:
        try:
            ref = MysqlReference(symbol, source=source)
        except Exception:
            skipped.append(symbol)
            continue
        refs.append(ref)

        if not ref.is_fund_a():
            continue
        try:
            fund = build_fund_reference(symbol, source=source)
        except Exception:
            # 官方净值这一档拿不到，PHP 的守卫 ``if ($fund->GetOfficialNetValue())``
            # 同样不会把它加进 $arFund；只是这里连对象都没建出来。
            continue
        if fund.get_official_net_value():
            funds.append(fund)
    return refs, funds, skipped


def build_category_page(
    name: str,
    source: Any = None,
    reference_service: Any = None,
    fundest_service: Any = None,
    symbols: list = None,
    wide: bool = False,
    sort: str = None,
) -> dict[str, Any]:
    """装配一个分类页的两张表。

    ==========================  ==========================================
    参数                         含义
    ==========================  ==========================================
    ``name``                    分类名（``CATEGORY_PAGES`` 的键）
    ``source``                  数据源，建引用对象要用
    ``reference_service``       :class:`~web.services.reference_service.ReferenceService`
    ``fundest_service``         :class:`~web.services.fundest_service.FundEstService`
    ``symbols``                 符号清单；**测试注入用**，默认取注册表
    ``wide``                    PHP 的 ``$bWide``（宽屏时名称做成外链）
    ``sort``                    EST 表的排序，见 ``fundest_service``
    ==========================  ==========================================

    任一张表的数据为空时对应键为 ``None``（模板据此整段跳过），
    这与详情页"缺数据就整段不摆"的既定做法一致。
    """
    definition = get_category(name)
    if symbols is None:
        symbols = definition["symbols"]()
    symbols = list(symbols)

    refs, funds, skipped = classify_symbols(symbols, source=source)

    reference = None
    if reference_service is not None and refs:
        try:
            table = reference_service.get_table(refs, wide=wide)
            reference = table if table.get("rows") else None
        except Exception:
            reference = None

    est = None
    if fundest_service is not None and funds:
        try:
            est = fundest_service.get_table(funds, wide=wide, sort=sort)
        except Exception:
            est = None

    return {
        "name": name,
        "display": definition["display"],
        "php": definition["php"],
        "title": definition["title"],
        "description": definition["description"],
        "reference": reference,
        "est": est,
        "counts": {
            "symbols": len(symbols),
            "reference": len(refs),
            "est": len(funds),
            "skipped": len(skipped),
        },
        "skipped": skipped,
    }
