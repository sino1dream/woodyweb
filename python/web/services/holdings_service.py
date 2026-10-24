"""持仓型基金的「基金持仓」页 —— 对应 ``woody/res/holdingscn.php``（139 行）。

## 页面结构（PHP ``EchoAll()`` 的顺序）

.. code-block:: php

    if ($ref = $acct->EchoStockGroup())                 // ① 通用引用有行情（或管理员）
    {
        $ref = new HoldingsReference($strSymbol);
        if ($ref->GetHoldingsDate())                    // ② 持仓日必须有
        {
            EchoHoldingsEstParagraph($ref);             // ③ 估值表 + 说明 + 净值记录
            EchoReferenceParagraph(                    // ④ 参考数据表
                array_merge(array($ref), RefSort($arHoldingRef)), $acct->IsAdmin());
            if (EchoTableParagraphBegin(...七列..., $str))   // ⑤ 持仓测算表
            {
                foreach ($arHoldingRef as $holding_ref)
                {
                    _echoHoldingItem(...);              //     每个成分股一行
                    if ($holding_ref->IsSymbolH())      //     顺手收集 ADR 配对
                        $arAdrhRef[] = new AdrPairReference(SqlGetHadrPair(...));
                }
                _echoHoldingItem(false, ...);           //     合计尾行
            }
            EchoAdrhParagraph($arAdrhRef, LayoutUseWide());  // ⑥ ADR/H 对比表
        }
    }

本模块按这个顺序装配，产出**数据**（``columns`` + ``rows``），渲染交给模板。

## 七列持仓测算表（``_echoHoldingItem``）

每行七格：符号链接 / 旧比例 / 旧价格 / 此后涨幅 / 新比例 / 影响 / 汇率调整。
三个 **static 累计器**（``$fTotalOld`` / ``$fTotalNew`` / ``$fTotalChange``）
跨行累加，最后 ``_echoHoldingItem(false, ...)`` 输出合计尾行。

两处"看着像 bug 但是原文"的地方，都照抄：

1. **合计尾行只有 6 格**（缺「汇率调整」）—— PHP 原样就是 6 个元素的数组，
   生产端 ``holdingscn.php?symbol=SH513050`` 实测那一行也只有 6 个 ``<td>``；
2. 尾行第 4 格是 **``($fNetValueChange - 1) * 100`` 加百分号**，与数据行的
   「此后涨幅」不是一套数（数据行是各成分股自己的涨幅）。

## 与站点其它表的两点不同

* **行序不排序**：测算表按 ``GetHoldingsRefArray()`` 的**原始顺序**（持仓表的
  查询顺序）出行；``RefSort``（A → H → 美股）只作用于第 ④ 段的参考数据表；
* **汇率调整逐行不同**：数据行的 ``$fAdjust`` 是**成分股自己**的
  （``RefAdjustForex``：A 股 1.0 / 港股 HKD / 美股 USD），尾行用的是
  **基金自己**的那个 —— 同一个表里两种口径，照抄。

## 数字口径：PHP 的 ``number_format`` / ``mysql_round``

测算表的每一格都是算出来的比例与涨幅，且「旧价格」列入参是 MySQL 的三位
小数串（``"3.675"``），第 3 位为 5 时 Python 的二进制舍入与 PHP 的最短十进制
half-up **必然分叉**。所以本表全部走 :func:`sql.historytable.php_number_format`
与 :func:`sql.historytable.mysql_round`（已在本地 PHP 8.0.10 逐例核对）。

## 已知取舍（详见 ``README_holdings_page_migration.md``）

* ``LayoutUseWide()`` 未迁移（``php/layout.php``），所有表按窄屏渲染；
* ``EchoTableParagraphBegin`` 的"总宽超屏宽则不渲染"未建模（七列合计 620px，
  与站点其它表的一致处理）；
* 页首的地区文案沿用**仓库 PHP** 的「港股/美股」，生产端跑的是更新过的代码
  （「香港/美国」），以仓库为准。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from web.bootstrap import install_paths

install_paths()

from ref.holdingsref import (  # noqa: E402
    MIN_FLOAT_VAL,
    StockGetHoldingsReference,
    ratio_of,
    ref_adjust_forex,
)
from ref.refobj import (  # noqa: E402
    AdrPairReference,
    _lookup_pair_symbol_reverse,
)
from ref.stockref import _php_floatval, _php_strval_float  # noqa: E402
from sql.historytable import TableRow, mysql_round, php_number_format  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    columns_to_dicts,
    column_base,
    column_change,
    column_percentage,
    column_price,
    column_symbol,
    ref_sort_by_symbol,
)

from web.services.fundest_service import FundEstService  # noqa: E402
from web.services.pair_service import build_pair_table  # noqa: E402
from web.services.reference_service import (  # noqa: E402
    ReferenceService,
    percentage_cell,
    ref_has_data,
    stock_name_of,
)
from web.services.stock_detail_service import (  # noqa: E402
    PAIR_TITLES,
    get_holdings_est_sections,
)

#: PHP ``php/account.php:14`` 的 ``DISP_ALL_CN`` —— 合计尾行第一格。
DISP_ALL_CN = "全部"

#: 表格 id。PHP 的 ``EchoTableParagraphBegin($ar, $acct->GetPage(), $str)``
#: 第二参是页面名（``UrlGetPage()`` 对 ``holdingscn.php`` 得 ``holdings``），
#: id 末尾再拼 ``table``（``php/ui/table.php:115`` 的 ``id="{$strId}table"``）。
HOLDINGS_TABLE_ID = "holdingstable"

#: 测算表标题（PHP 拼在 ``EchoTableParagraphBegin`` 第三参的表格标题上）::
#:     '持仓和测算示意 ' . '总数' . strval($ref->CountHoldings()) . ' ' . $ref->GetHoldingsRatioDisplay()
HOLDINGS_INTRO = "持仓和测算示意"

#: 页面标题后缀，PHP ``GetTitle()`` = ``GetSymbolDisplay() . HOLDINGS_DISPLAY``。
#: ``HOLDINGS_DISPLAY`` 在 ``php/stocklink.php:28`` 是 ``'基金持仓'``。
PAGE_TITLE_SUFFIX = "基金持仓"

#: 页面说明，PHP ``GetMetaDescription()``（``holdingscn.php:120-127``）。
#: 站点模板没有 meta description 槽位（``base.html`` 只有 title），
#: 这里原样保留文本，供 API / 未来接 SEO 时使用。
PAGE_DESCRIPTION = (
    "页面. 用于显示ETF基金的成分股持仓情况, 以及各个成分股最新的价格. "
    "基于成分股价格测算基金的官方EST和实时EST."
)


# ---------------------------------------------------------------------------
# 纯函数：排序与列定义
# ---------------------------------------------------------------------------
def _flag(ref: Any, snake: str, camel: str) -> bool:
    """鸭子类型调符号判定（``IsSymbolA`` / ``IsSymbolH``）。

    与 :func:`ref.holdingsref._flag` 同款，但两个命名都探 —— 这里的 ref 可能是
    ``MysqlReference`` / ``FundPairReference`` / 测试替身，别名区（AGENTS.md
    的 camelCase 约定）不一定都挂齐。
    """
    for name in (snake, camel):
        method = getattr(ref, name, None)
        if method is None:
            continue
        try:
            return bool(method())
        except Exception:
            return False
    return False


def _invoke(ref: Any, snake: str, camel: str, *args: Any) -> Any:
    """按 snake_case 优先、camelCase 兜口调用；取不到返回 ``None``。"""
    for name in (snake, camel):
        method = getattr(ref, name, None)
        if method is None or not callable(method):
            continue
        try:
            return method(*args)
        except Exception:
            return None
    return None


def ref_sort_ahus(refs: Iterable[Any]) -> list:
    """对应 ``RefSort($arRef)``（``holdingscn.php:8-22``）。

    A 股 / H 股 / 美股（及其它）三组，各组内 ``RefSortBySymbol``
    （按符号去重后升序，见 :func:`ui.uicolumn.ref_sort_by_symbol`），
    再按 **A → H → 其它** 的顺序接起来。

    只服务第 ④ 段的参考数据表；测算表（第 ⑤ 段）按持仓表原始顺序出行，
    不要把这个函数用到那边去。
    """
    a_refs, h_refs, us_refs = [], [], []
    for ref in refs:
        if _flag(ref, "is_symbol_a", "IsSymbolA"):
            a_refs.append(ref)
        elif _flag(ref, "is_symbol_h", "IsSymbolH"):
            h_refs.append(ref)
        else:
            us_refs.append(ref)
    return (
        ref_sort_by_symbol(a_refs)
        + ref_sort_by_symbol(h_refs)
        + ref_sort_by_symbol(us_refs)
    )


def build_holdings_columns() -> list:
    """测算表七列（``holdingscn.php:87-93``）。

    ========  =========================  ======  ======
    PHP                        列头      宽     颜色
    ========  =========================  ======  ======
    ``TableColumnSymbol()``      代码       80     maroon
    ``TableColumnPercentage('旧')``  旧比例(%)  100    —
    ``TableColumnPrice('旧')``   旧价格     70     blue
    ``TableColumnChange('此后')`` 此后涨幅   70     —
    ``TableColumnPercentage('新')``  新比例(%)  100    —
    ``TableColumnPercentage('影响')`` 影响(%)    100    —
    ``TableColumn('汇率调整',100)``  汇率调整  100    —
    ========  =========================  ======  ======

    前缀（``'旧'`` / ``'此后'`` / ``'新'`` / ``'影响'``）直接拼在列头文本前，
    这是 PHP ``TableColumn`` 构造器的第四个参数，见
    :func:`ui.uicolumn.column_base`。
    """
    columns = [
        column_symbol(),
        column_percentage("旧"),
        column_price("旧"),
        column_change("此后"),
        column_percentage("新"),
        column_percentage("影响"),
        column_base("汇率调整", 100),
    ]
    return columns_to_dicts(columns)


def holdings_intro_text(holdings_ref: Any) -> str:
    """测算表标题：``'持仓和测算示意 ' . '总数' . N . ' ' . GetHoldingsRatioDisplay()``。

    ``GetHoldingsRatioDisplay()``（``holdingsref.php:211``）给的是
    「A股31% 港股27% 美股42%」—— 只列**有持仓**的三个地区，缺的地区不占位。
    """
    count = _invoke(holdings_ref, "count_holdings", "CountHoldings") or 0
    display = _invoke(holdings_ref, "get_holdings_ratio_display", "GetHoldingsRatioDisplay") or ""
    text = "%s 总数%s" % (HOLDINGS_INTRO, count)
    if display:
        text += " %s" % display
    return text


# ---------------------------------------------------------------------------
# 数字小工具（PHP 口径）
# ---------------------------------------------------------------------------
def _php_falsy(value: Any) -> bool:
    """PHP 的 ``== false``：``0`` / ``0.0`` / ``'0'`` / ``''`` / ``None`` 都为假。

    Python 里 ``'0'`` 是**真值**，所以 ``if ($fClose)`` 不能直译成 ``if close:``。
    """
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return value.strip() in ("", "0")
    if isinstance(value, (int, float)):
        return value == 0
    return False


def _history_value(history: dict, stock_id: Any) -> float:
    """按 ``stock_id`` 取持仓日收盘价；取不到按 ``0.0``。

    键容错（int / str）与 :func:`ref.holdingsref.ratio_of` 同一理由：
    PHP 的数字字符串键会被隐式转成 int，Python 不会。测算表里
    ``$arHistory`` 与 ``$arRatio`` 两张图的键来源不同（一个来自
    ``get_stock_id()``、一个来自持仓表的 ``holding_id``），必须两边都容。

    **取不到时是 0.0 而不是抛错**：PHP 那边 ``$arHistory[$strStockId]``
    缺键只是 Warning + ``null``，随后 ``$fClose > MIN_FLOAT_VAL`` 为假，
    涨幅按 0 算、旧价格按 ``mysql_round('', 2)`` = ``'0'`` 出。
    """
    for key in (stock_id, str(stock_id)):
        if key in history:
            return float(history[key])
    try:
        if int(stock_id) in history:
            return float(history[int(stock_id)])
    except (TypeError, ValueError):
        pass
    return 0.0


def _percentage_text(ref: Any, close: float, price: float) -> str:
    """``GetPercentageText($fClose, $fPrice)`` 的文本部分（``stockref.php:135-145``）。

    PHP 三步：

    1. ``GetPercentageString`` —— 阈值归零：``|涨幅 × 除数| < 50/10^精度``
       时返回 ``'0'``（不拼 ``%``）；
    2. 非 ``'0'`` 才 ``number_format(..., 2)``，再 ``rtrim('0')`` / ``rtrim('.')``；
    3. 拼 ``'%'``。

    两处照抄细节：

    * **除数为假时回落到昨收** —— ``GetPercentage($fDivisor = false, ...)``
      的默认值在 ``$fDivisor == false`` 时生效，而 ``0.0 == false`` 为真。
      持仓日为 0（有行但值是 0）时 PHP 拿**昨收**当除数，不是给 0 涨幅；
    * **舍入用 PHP 口径**（:func:`php_number_format`）—— 现成的
      ``get_percentage_text()`` 内部是 ``"%.2f"``，二进制舍入在 ``.xx5``
      边界与 PHP 分叉（``0.125`` → ``'0.12'`` vs PHP 的 ``'0.13'``）。
    """
    #: 除数为假（0 / 空）时传 None，让 get_percentage_string 走 PHP 的
    #: ``floatval($this->strPrevPrice)`` 回落 —— 与 PHP 的 == false 判断一致。
    divisor = None if _php_falsy(close) else close
    text = _invoke(ref, "get_percentage_string", "GetPercentageString", divisor, price)
    if text is None or text == "" or _php_floatval(text) == 0.0:
        return "0"
    formatted = php_number_format(_php_floatval(text), 2)
    formatted = formatted.rstrip("0").rstrip(".")
    return "%s%%" % formatted


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------
class HoldingsService:
    """把持仓引用整理成「基金持仓」页的各张表。

    ``source`` 用来查股票中文名（``SqlGetStockName()``）与配对表
    （``SqlGetHadrPair``），可以传 ``None``（名称列与 ADR 段为空）。
    """

    def __init__(
        self,
        source: Any = None,
        reference_service: Any = None,
        fundest_service: Any = None,
        history_service: Any = None,
    ):
        self.source = source
        #: 三个段落服务都由调用方注入（测试里可整个顶掉）；
        #: 不给就现造一个用当前 source 的默认实现。
        self.reference_service = reference_service
        self.fundest_service = fundest_service
        self.history_service = history_service

    # -- 对外：七列持仓测算表 ---------------------------------------------
    def get_table(self, holdings_ref: Any, link_builder: Callable[[str], str] = None) -> dict:
        """测算表（``holdingscn.php:87-112``）。

        返回 ``{"columns", "rows", "id", "title", "count", "cell_count",
        "adr_refs"}``。``adr_refs`` 是循环里顺手收集的 ADR 配对引用 ——
        PHP 的收集动作嵌在 ``_echoHoldingItem`` 的循环里，拆开会让
        "哪只 H 股配哪只 ADR"的逻辑散两处，所以随表一起返回。

        ``link_builder(符号) -> URL`` 由路由层注入（要 ``url_for``）；
        不给就首格退化成纯文本（没有链接）。
        """
        holding_refs = list(_invoke(holdings_ref, "get_holdings_ref_array", "GetHoldingsRefArray") or [])
        ratio_map = _invoke(holdings_ref, "get_holdings_ratio_array", "GetHoldingsRatioArray") or {}
        net_value_change = _invoke(holdings_ref, "get_net_value_change", "GetNetValueChange") or 0.0
        history = _invoke(holdings_ref, "get_holding_date_history", "GetHoldingDateHistory") or {}
        #: 两个汇率调整系数**全表共用**，先算一次（PHP 同样先算好再进循环）
        adjust_hkd = _invoke(holdings_ref, "get_adjust_hkd", "GetAdjustHKD") or 1.0
        adjust_usd = _invoke(holdings_ref, "get_adjust_usd", "GetAdjustUSD") or 1.0

        #: PHP 的三个 static 累计器。static 在 PHP 里跨调用存活，但一个请求
        #: 只渲染一张表，所以"每次建表清零"是等价语义。
        totals = {"old": 0.0, "new": 0.0, "change": 0.0}
        adr_refs = []
        rows = []
        for holding_ref in holding_refs:
            rows.append(
                self._row(
                    holding_ref,
                    ratio_map,
                    net_value_change,
                    history,
                    adjust_hkd,
                    adjust_usd,
                    totals,
                    link_builder,
                )
            )
            #: ``if ($holding_ref->IsSymbolH())`` —— 只有港股成分股才配 ADR
            if _flag(holding_ref, "is_symbol_h", "IsSymbolH"):
                adr_ref = self._adr_ref(holding_ref)
                if adr_ref is not None:
                    adr_refs.append(adr_ref)

        #: 合计尾行：PHP 的 ``_echoHoldingItem(false, ...)``，$fAdjust 取
        #: **基金自己**的汇率调整（不是成分股的）
        rows.append(
            self._total_row(
                net_value_change,
                ref_adjust_forex(holdings_ref, adjust_hkd, adjust_usd),
                totals,
            )
        )

        return {
            "id": HOLDINGS_TABLE_ID,
            "title": holdings_intro_text(holdings_ref),
            "columns": build_holdings_columns(),
            "rows": rows,
            "count": len(rows),
            #: 最长行 7 格；合计尾行只有 6 格（见 _total_row）
            "cell_count": max(len(row.cells) for row in rows) if rows else 0,
            "adr_refs": adr_refs,
        }

    # -- 对外：整页装配 ----------------------------------------------------
    def get_page(
        self,
        symbol: str,
        holdings_ref: Any = None,
        link_builder: Callable[[str], str] = None,
        note_links: dict = None,
        wide: bool = False,
        admin: bool = False,
        layout_width: int = None,
    ) -> Any:
        """装配整页（``EchoAll()``）；**没有内容可渲染时返回 ``None``**。

        两道 PHP 守卫（``$ref->GetHoldingsDate()`` 与
        ``EchoStockGroup()`` 的 ``HasData()``）在这里合成一个返回值：
        路由层据此渲染"取不到行情数据"的提示（站点既有做法）。

        ==========================  ====================================
        参数                         含义
        ==========================  ====================================
        ``holdings_ref``             持仓引用；不给就自己建
        ``link_builder``             测算表首格的链接构造器
        ``note_links``               估值表说明文字的三个链接（见
                                    :func:`get_holdings_est_sections`）
        ``wide``                     PHP 的 ``$bWide``（未迁移，恒 False）
        ``admin``                    参考数据表追加"调试数据"列
        ==========================  ====================================
        """
        if holdings_ref is None:
            holdings_ref = self._build_holdings_ref(symbol)
        if holdings_ref is None:
            return None
        #: PHP 的 ``if ($ref->GetHoldingsDate())`` —— 没有持仓日就整页不渲染
        if not _invoke(holdings_ref, "get_holdings_date", "GetHoldingsDate"):
            return None
        #: PHP 的 ``EchoStockGroup()`` 守卫（``HasData() || IsAdmin()``）。
        #: 站点没有账号体系，所以只判 HasData；判定对象用持仓引用而不是
        #: 另建一个通用引用 —— 两者读同一份行情快照，has_data 相同，
        #: 少建一个对象少一次全表查询。
        if not ref_has_data(holdings_ref):
            return None

        holdings = self.get_table(holdings_ref, link_builder=link_builder)
        adr_count = len(holdings.get("adr_refs") or [])
        adr = None
        if adr_count:
            adr = self._adr_table(holdings["adr_refs"], wide=wide)
        #: ``adr_refs`` 是给调用方的中间产物，页面数据里不带（避免模板
        #: 拿到一堆引用对象）
        holdings.pop("adr_refs", None)

        reference = self._reference_table(holdings_ref, admin=admin, layout_width=layout_width)

        return {
            "symbol": symbol,
            "title": "%s%s" % (symbol, PAGE_TITLE_SUFFIX),
            "description": PAGE_DESCRIPTION,
            #: ``EchoHoldingsEstParagraph`` 的三段（估值表 + 说明 + 净值记录）
            **get_holdings_est_sections(
                symbol,
                holdings_ref,
                fundest_service=self._fundest(),
                history_service=self._history(),
                links=note_links,
            ),
            #: ④ 参考数据表：基金自己 + A/H/US 排序后的成分股
            "reference": reference,
            #: ⑤ 七列持仓测算表
            "holdings": holdings,
            #: ⑥ ADR/H 对比表（没有 ADR 配对时整段不出现）
            "adr": adr,
            "counts": {
                "holdings": _invoke(holdings_ref, "count_holdings", "CountHoldings") or 0,
                "adr": adr_count,
            },
        }

    # -- 内部 --------------------------------------------------------------
    def _build_holdings_ref(self, symbol: str) -> Any:
        """``new HoldingsReference($strSymbol)`` 的入口守卫。

        包 ``try``：连不上库时退化成"这一段不显示"，而不是整页 500
        （与 ``stock_detail_service._build_holdings_ref`` 同一条约定）。
        """
        try:
            return StockGetHoldingsReference(symbol, source=self.source)
        except Exception:
            return None

    def _fundest(self) -> Any:
        if self.fundest_service is not None:
            return self.fundest_service
        return FundEstService(self.source)

    def _history(self) -> Any:
        return self.history_service

    def _reference_service(self, layout_width: int) -> Any:
        """参考数据服务；``layout_width`` 是**构造参数**不是 ``get_table`` 的。

        （踩过：把 ``layout_width`` 传给 ``get_table`` 会 TypeError，又被
        ``except`` 吞掉 → 整张参考表静默消失。）
        """
        if self.reference_service is not None:
            return self.reference_service
        return ReferenceService(self.source, layout_width=layout_width)

    def _reference_table(self, holdings_ref: Any, admin: bool, layout_width: int) -> Any:
        """④ 参考数据表：``EchoReferenceParagraph(array_merge(array($ref), RefSort(...)))``。

        顺序照 PHP：**基金自己排第一**，后面跟着 A → H → 美股排序的成分股。
        标题沿用站点既有的「参考数据」（PHP 那边还拼了个当前时间
        ``GetTimeDisplay()``，站点其它页面都不拼，这里保持一致）。
        """
        holding_refs = _invoke(holdings_ref, "get_holdings_ref_array", "GetHoldingsRefArray") or []
        refs = [holdings_ref] + ref_sort_ahus(holding_refs)
        try:
            table = self._reference_service(layout_width).get_table(
                refs, wide=False, admin=admin, title="参考数据"
            )
        except Exception:
            #: 一张参考表不该让整页挂掉
            return None
        return table if table.get("rows") else None

    def _adr_table(self, adr_refs: list, wide: bool) -> Any:
        """⑥ ADR/H 对比表：``EchoAdrhParagraph($arAdrhRef, LayoutUseWide())``。

        行组装在 :mod:`web.services.pair_service`（``kind="adr"``），
        本方法只负责把收集到的引用交过去。**不给 position_reader** ——
        PHP 的 ``if ($strPos = $pos_sql->ReadVal(...))`` 在库里没有
        ``fundposition`` 行时那一格本来就不出，而数据源的
        ``get_position()`` 带默认仓位回落（0.95 / 1.0），会把空格子
        填出一个值来。生产端 SH513050 的 ADR 表就是五行都少这一格。
        """
        try:
            return build_pair_table(
                adr_refs,
                kind="adr",
                wide=wide,
                title=PAIR_TITLES["adr"],
                source=self.source,
            )
        except Exception:
            return None

    def _row(
        self,
        holding_ref: Any,
        ratio_map: dict,
        net_value_change: float,
        history: dict,
        adjust_hkd: float,
        adjust_usd: float,
        totals: dict,
        link_builder: Callable[[str], str],
    ) -> TableRow:
        """对应 ``_echoHoldingItem`` 的非合计分支（``holdingscn.php:37-66``）。"""
        stock_id = _invoke(holding_ref, "get_stock_id", "GetStockId")
        close = _history_value(history, stock_id)
        price = _php_floatval(_invoke(holding_ref, "get_price", "GetPrice") or "")
        #: 成分股**自己**的汇率调整（A 股 1.0 / 港股 HKD / 美股 USD）
        adjust = ref_adjust_forex(holding_ref, adjust_hkd, adjust_usd)

        #: ``($fClose > MIN_FLOAT_VAL) ? $fPrice / $fClose : 0.0`` 再 ``/= $fAdjust``
        change = (price / close) if close > MIN_FLOAT_VAL else 0.0
        if adjust:
            change /= adjust

        ratio = ratio_of(ratio_map, stock_id)
        totals["old"] += ratio
        #: PHP 这里是裸除。``GetNetValueChange()`` = 1 + 加权涨幅，为 0 意味着
        #: 持仓全化成灰 —— PHP 8 会 DivisionByZeroError fatal。这里退回 0.0
        #: 让页面还能出数（known deviation，见 README）。
        new_ratio = ratio * change / net_value_change if abs(net_value_change) > MIN_FLOAT_VAL else 0.0
        totals["new"] += new_ratio
        ratio_change = ratio * (change - 1.0)
        totals["change"] += ratio_change

        row = TableRow()
        symbol = _invoke(holding_ref, "get_symbol", "GetSymbol") or ""
        #: 首格：PHP 是 ``RefGetMyStockLink($ref)`` 的 ``<a>``，锚文本即符号；
        #: 站点惯例是 row.href（URL）+ 纯符号文本，URL 由路由层注入
        if link_builder is not None:
            row.href = link_builder(symbol) or ""
        row.add(symbol)
        row.add(php_number_format(ratio, 2))
        #: ``mysql_round(strval($fClose), 2)`` —— strval 那一步必须在：
        #: PHP 先按 14 位有效数字成串再 floatval 回来，个别边界值会换档
        row.add(mysql_round(_php_strval_float(close), 2))
        #: ``$ref->GetPercentageDisplay($fClose, $fPrice)``；配色走站点统一的
        #: 涨红跌绿（与参考数据表同一个 percentage_cell）
        row.add(percentage_cell(_percentage_text(holding_ref, close, price)))
        row.add(php_number_format(new_ratio, 2))
        row.add(php_number_format(ratio_change, 4))
        row.add(php_number_format(adjust, 4))
        #: ``RefEchoTableColumn($ref, $ar)``：窄屏时把股票名挂在首格 title 上
        #: （``EchoTableColumn($ar, false, SqlGetStockName(...))``）
        row.title = stock_name_of(self.source, holding_ref)
        return row

    @staticmethod
    def _total_row(net_value_change: float, fund_adjust: float, totals: dict) -> TableRow:
        """合计尾行（``holdingscn.php:32-34``）—— **只有 6 格**。

        PHP 原样是 6 个元素的一维数组，比表头少一格「汇率调整」：
        生产端实测确认这不是脚本提取错误（浏览器里那一行也只有 6 个 td）。
        **照抄，不补第七格** —— 补了反而是"修正"了 PHP。

        尾行走普通 ``EchoTableColumn($ar)``，没有首格 hint（不挂股票名）。
        """
        row = TableRow()
        row.add(DISP_ALL_CN)
        row.add(php_number_format(totals["old"], 2))
        row.add("")
        #: ``number_format(($fNetValueChange - 1.0) * 100, 2).'%'``
        row.add("%s%%" % php_number_format((net_value_change - 1.0) * 100.0, 2))
        row.add(php_number_format(totals["new"], 2))
        #: ``$fTotalChange * $fAdjust``，$fAdjust 是**基金自己**的汇率调整
        row.add(php_number_format(totals["change"] * fund_adjust, 2))
        return row

    def _adr_ref(self, holding_ref: Any) -> Any:
        """``SqlGetHadrPair($holding_ref->GetSymbol())`` → ``new AdrPairReference(...)``。

        **方向是这张表最容易搞反的地方**：``adrpair`` 表里 ``id`` = ADR 侧、
        ``stock_id`` = H 侧（``PairSql`` 的 ``GetRecord`` 按 ``stock_id``
        查行、取行的 ``id``，见 ``php/sql/sqlpair.php:21``）。所以从
        **H 符号**找 ADR 是**反查**（``StockPairSql::GetSymbol()``，
        ``php/sql/sqlstockpair.php:25``），对应
        :func:`ref.refobj._lookup_pair_symbol_reverse`；正查
        （``GetPairSymbol``）是 ``SqlGetAdrhPair(ADR)→H`` 的方向，
        给 ``AdrPairReference.get_pair_ref()`` 用。
        """
        symbol = _invoke(holding_ref, "get_symbol", "GetSymbol")
        if not symbol:
            return None
        adr_symbol = _lookup_pair_symbol_reverse(self.source, str(symbol), "adrpair")
        if not adr_symbol:
            return None
        try:
            return AdrPairReference(adr_symbol, source=self.source)
        except Exception:
            #: 建不出配对引用不该让整表消失（PHP 那边 new 失败会 fatal）
            return None


def build_holdings_page(
    symbol: str,
    source: Any = None,
    reference_service: Any = None,
    fundest_service: Any = None,
    history_service: Any = None,
    link_builder: Callable[[str], str] = None,
    note_links: dict = None,
    wide: bool = False,
    admin: bool = False,
    layout_width: int = None,
) -> Any:
    """一步到位的便捷函数（不需要复用 service 实例时用这个）。"""
    return HoldingsService(
        source,
        reference_service=reference_service,
        fundest_service=fundest_service,
        history_service=history_service,
    ).get_page(
        symbol,
        link_builder=link_builder,
        note_links=note_links,
        wide=wide,
        admin=admin,
        layout_width=layout_width,
    )
