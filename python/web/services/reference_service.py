"""参考数据表服务。

对应 PHP ``php/ui/referenceparagraph.php`` 的 ``EchoReferenceParagraph()``。

PHP 那一份是"边拼 HTML 边 echo"：

.. code-block:: php

    $ar = array_merge(array($ref->GetExternalLink()), GetStockReferenceArray($ref));
    $strFirstHint = array_pop($ar);        // 把名称拎出来...
    $ar[] = $strDescription;               // ...换成说明文字
    EchoTableColumn($ar, false, $strFirstHint);   // 名称降级成首格的 title 提示

本版只产出 **数据**（``columns`` + ``rows``），渲染交给模板；颜色不再是
``<font color>`` 片段而是随单元格返回的颜色名。这样同一份数据既能给页面用，
也能给 JSON API 用 —— PHP 那边想复用到接口上就得先正则剥标签。

## 涨幅配色：**不**照抄 PHP，采用全站统一的"涨红跌绿"

``php/stock/stockref.php:147`` 的规则是这样的：

.. code-block:: php

    if (str_ends_with($strDisp, '%'))  $strColor = str_starts_with($strDisp, '-') ? 'red' : 'black';
    else                               $strColor = 'gray';

也就是 **跌红、涨黑**。但同一个 PHP 代码库里 ``StockGetPriceDisplay()``
（``php/stock.php:91``）的默认参数是 ``($strGreater='red', $strLess='green')``，
即 **涨红跌绿**；``GetRatioDisplay()`` 同样以 1.0 为基准走红/绿；
``python/web`` 的分组列表页（``fund_list_service``）用的也是后者。

**裁决结论（2026-10-09）：统一成涨红跌绿**，与分组列表页一致。
理由：跌红涨黑只在 ``GetPercentageDisplay()`` 这一处出现，而涨红跌绿是
``StockGetPriceDisplay()`` 的默认参数、被 ``GetRatioDisplay()`` /
``GetNumberDisplay()`` 等一大片函数共用，后者才是代码库的主流口径。
把孤例改成多数派，改动面更小也更不容易再冒出第二个口径。

因此 :func:`percentage_cell` 的返回值与 PHP 原文**不同**：
跌是 ``green`` 而不是 ``red``，涨是 ``red`` 而不是 ``black``。
这是**已知偏差第 1 条**（见 ``README_ui_column_migration.md``）。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from web.bootstrap import install_paths

install_paths()

from sql.historytable import TableRow  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    columns_to_dicts,
    column_base,
    column_change,
    column_date,
    column_name,
    column_price,
    column_symbol,
    column_time,
    ref_has_data,
    table_column_get_last_width,
)


#: PHP 最后一列的名称列宽 ``new TableColumnName(false, 270)``
#: —— 名称是这一屏的唯一参照物，所以给到 270px。
REFERENCE_NAME_WIDTH = 270

#: 行的说明文字（盘前/盘后）在 PHP 里是 ``<i><font color=gray>…</font></i>``，
#: 这里只保留文本，倾斜与灰色交给 CSS。
REFERENCE_SESSION_COLOR = "gray"

# -- 一行「为什么没有数据」的状态 ----------------------------------------
#
# PHP **完全没有这个区分** —— ``GetStockReferenceArray()`` 的 else 分支统一输出
# 四个空串，页面上「stock 表里没这个符号」和「表里有、但快照没抓到」长得一模一样。
# 演示库实测两边各有一个活样本：
#
#   ``^NDX``    stock 表有（id=6），``realtimequote`` 没有它 → 抓取进程没覆盖到
#   ``USDCNY``  stock 表里压根没有（符号来自 fundcalc 的地区配置）
#
# 前者是运维问题（后台抓取没跑到），后者是符号配置与库不一致。都写成一排空格会
# 让人以为是数据丢了，所以本版在「名称」那格标出来。

#: 有行情，正常渲染
REFERENCE_STATE_OK = "ok"
#: 库里有这个符号，但没抓到行情快照
REFERENCE_STATE_NO_QUOTE = "no-quote"
#: 库里没有这个符号
REFERENCE_STATE_NO_SYMBOL = "no-symbol"
#: 问不到（没给数据源，或查询抛异常）—— 不敢下结论，只说"没行情"
REFERENCE_STATE_UNKNOWN = "unknown"

#: 没数据时挂在「名称」格上的说明文字（灰色）
REFERENCE_STATE_NOTES = {
    REFERENCE_STATE_NO_QUOTE: "无行情快照",
    REFERENCE_STATE_NO_SYMBOL: "库里无此符号",
    REFERENCE_STATE_UNKNOWN: "无行情",
}


def quote_state_of(source: Any, ref: Any) -> str:
    """判定这一行为什么没有数据，返回 :data:`REFERENCE_STATE_*` 之一。"""
    if ref_has_data(ref):
        return REFERENCE_STATE_OK

    # MysqlReference 构造时已经存过 stock_id；有值就说明库里有这一行
    if getattr(ref, "stock_id", None):
        return REFERENCE_STATE_NO_QUOTE

    getter = getattr(source, "get_stock_id", None)
    if getter is None:
        # 没有数据源可问（纯离线测试），不下结论
        return REFERENCE_STATE_UNKNOWN
    try:
        known = bool(getter(ref.get_symbol()))
    except Exception:
        return REFERENCE_STATE_UNKNOWN
    return REFERENCE_STATE_NO_QUOTE if known else REFERENCE_STATE_NO_SYMBOL


def _named_note(name: str, note: str) -> str:
    """把说明挂在名称后面；连名称都没有时就只显示说明。

    写出来是这样两种：``纳斯达克100指数（无行情快照）`` / ``库里无此符号``。
    """
    return "%s（%s）" % (name, note) if name else note


def percentage_cell(text: Any) -> tuple:
    """把涨跌幅文本转成 ``(文本, 颜色)`` 单元格。

    形状照抄 ``php/stock/stockref.php:147`` 的三分支（带 ``%`` 且带 ``-`` /
    带 ``%`` 不带 ``-`` / 不带 ``%``），但**颜色改成全站统一的涨红跌绿**：

    ================================  ===================  ==================
    情形                                PHP 原文             本实现
    ================================  ===================  ==================
    带 ``%`` 且以 ``-`` 开头（跌）      ``red``              ``green``
    带 ``%`` 其它（涨或持平）           ``black``            ``red``
    不带 ``%``（无昨收可比）            ``gray``             ``gray``
    ================================  ===================  ==================

    最后一行的 ``gray`` 与 PHP 一致：那时 ``GetPercentageText()`` 返回空串，
    表示该标的压根没有昨收可比，灰色用来和"真的是 0%"区分开。

    与 :func:`_cell` 的区别见 :func:`get_stock_reference_cells` 的调用处 ——
    只有涨幅这一格需要染色，价格/日期/时间都是纯文本。
    """
    displayed = "" if text is None else str(text)
    if not displayed.endswith("%"):
        return displayed, REFERENCE_SESSION_COLOR
    return displayed, ("green" if displayed.startswith("-") else "red")


def _cell(value: Any) -> Any:
    """空值统一成空串，避免模板里出现 ``None``。"""
    return "" if value is None else value


def stock_name_of(source: Any, ref: Any) -> str:
    """对应 ``SqlGetStockName($ref->GetSymbol())``。

    **已知偏差**：宽屏（``$bWide``）时 PHP 会把名称做成雪球外链
    （``GetXueqiuLink()``，``php/externallink.php:131``）。外链函数族属于
    优先级列表第 5 步、未迁移，所以这里**始终返回纯文本名称**，
    由模板决定要不要加链接。

    抽成模块级函数是因为 ``ahparagraph`` / ``fundestparagraph`` 也要查名字，
    而它们不是 :class:`ReferenceService`。
    """
    if source is None:
        return ""
    try:
        return _cell(source.get_stock_name(ref.get_symbol()))
    except Exception:
        # 查名字失败不该让整张表挂掉
        return ""


def build_reference_columns(admin: bool = False, layout_width: int = None) -> list:
    """参考数据表的列定义。

    对应 PHP ``EchoReferenceParagraph()`` 里那段有点绕的组装：

    .. code-block:: php

        $ar = array_merge(array(new TableColumnSymbol()), GetStockReferenceColumn());
        array_pop($ar);                              // 丢掉默认的「名称」列
        $ar[] = new TableColumnName(false, 270);     // 换成更宽的名称列
        if ($bAdmin) $ar[] = new TableColumn('调试数据', TableColumnGetLastWidth($ar));

    **为什么要 pop 再 append**：``GetStockReferenceColumn()`` 返回的名称列宽是
    默认的 150，而参考数据表要 270（它是页面的主要参照物）。本版直接构造，
    不重复"先塞进去再扔掉"的动作 —— 结果一致。
    """
    columns = [
        column_symbol(),
        column_price(),
        column_change(),
        column_date(),
        column_time(),
        column_name(width=REFERENCE_NAME_WIDTH),
    ]
    if admin:
        # PHP：TableColumnGetLastWidth() 依赖全局 LayoutGetDisplayWidth()，
        # 这里改成显式入参（见 uicolumn.table_column_get_last_width）。
        width = table_column_get_last_width(columns, layout_width) if layout_width else 0
        columns.append(column_base("调试数据", width))
    return columns_to_dicts(columns)


def get_stock_reference_cells(ref: Any) -> list:
    """对应 ``GetStockReferenceArray()``：中间四格（价格 / 涨幅 / 日期 / 时间）。

    **没有行情时要输出四个空串而不是省略** —— PHP 原样走的 else 分支，
    因为表格必须列数对齐，少一格整行就会串位。
    """
    if not ref_has_data(ref):
        return ["", "", "", ""]
    return [
        _cell(ref.get_price_display()),
        percentage_cell(ref.get_percentage_text()),
        _cell(ref.get_date()),
        _cell(ref.get_time_hm()),
    ]


class ReferenceService:
    """把一批引用对象整理成"参考数据"表格。

    ``source`` 只用来查股票中文名（对应 PHP 的 ``SqlGetStockName()``）；
    可以传 ``None``，那时名称列为空。
    """

    def __init__(self, source: Any = None, layout_width: int = None):
        self.source = source
        #: 布局总宽度，管理员调试列要靠它算剩余宽度
        self.layout_width = layout_width

    # -- 对外 -------------------------------------------------------------
    def get_table(
        self,
        refs: Iterable[Any],
        wide: bool = False,
        admin: bool = False,
        title: str = None,
    ) -> dict[str, Any]:
        """整理成 ``{"columns", "rows", "title", "count"}``。

        ==========================  =======================================
        参数                         含义
        ==========================  =======================================
        ``refs``                     引用对象列表，**允许含 ``None``**（跳过）
        ``wide``                     PHP 的 ``$bWide``：宽屏时名称做成外链
        ``admin``                    追加"调试数据"列（只有超管看得到）
        ``title``                    表格标题，默认"参考数据"
        ==========================  =======================================
        """
        rows = []
        for ref in refs:
            if ref is None:
                continue
            rows.append(self._row(ref, None, admin, wide))
            extended = getattr(ref, "extended_ref", None)
            if extended is not None:
                rows.append(self._row(extended, self._session_of(extended), False, wide))

        return {
            "title": title or "参考数据",
            "columns": build_reference_columns(admin=admin, layout_width=self.layout_width),
            "rows": rows,
            "count": len(rows),
        }

    # -- 内部 -------------------------------------------------------------
    def _row(self, ref: Any, description: Any, admin: bool, wide: bool) -> TableRow:
        """对应 ``_echoReferenceTableItem()``。

        PHP 的三个动作这里一一对应：

        1. 首格永远是外链（``GetExternalLink()``）；
        2. 中间四格来自 :func:`get_stock_reference_cells`；
        3. 有 ``description`` 时，**名称被降级成首格的 tooltip**，
           最后一格换成说明文字 —— 这是 PHP ``array_pop`` 那段的效果。
        """
        row = TableRow()
        row.add(_cell(ref.get_external_link()))
        for cell in get_stock_reference_cells(ref):
            row.add(cell)

        name = self._name_of(ref)
        note = self._note_of(ref)
        if description:
            row.title = name
            row.add((_cell(description), REFERENCE_SESSION_COLOR))
        elif note:
            # 中间四格全是空格，唯一的解释机会就是「名称」这一格
            row.add((_named_note(name, note), REFERENCE_SESSION_COLOR))
        else:
            row.add(name)

        if admin:
            row.add(_cell(ref.debug_link()))
        return row

    def _name_of(self, ref: Any) -> str:
        """转调 :func:`stock_name_of`。"""
        return stock_name_of(self.source, ref)

    def _note_of(self, ref: Any) -> str:
        """没数据时挂在名称格上的说明；有行情时返回空串。"""
        state = quote_state_of(self.source, ref)
        if state == REFERENCE_STATE_OK:
            return ""
        return REFERENCE_STATE_NOTES.get(state, REFERENCE_STATE_NOTES[REFERENCE_STATE_UNKNOWN])

    @staticmethod
    def _session_of(ref: Any) -> str:
        """盘前/盘后说明文字。PHP 是 ``GetMarketSession()``。"""
        getter = getattr(ref, "get_market_session", None)
        if getter is None:
            return ""
        try:
            return _cell(getter())
        except Exception:
            return ""


def reference_symbols_for_fund(symbol: str, source: Any = None) -> list:
    """一只 QDII 基金详情页「参考数据」表要放的**全部标的**（有序、去重）。

    对应 PHP ``_qdiigroup.php:55-83`` 那段装配 + ``EchoCommonParagraphs()`` 的
    ``EchoFundListParagraph()``：

    .. code-block:: php

        $arRef = [$stock_ref, $est_ref];                      // 基金 + 估值标的
        if ($realtime_ref = $ref->GetRealtimeRef()) $arRef[] = $realtime_ref;
        foreach ($arLev as $strSymbol) {                      // 杠杆标的（查 fundpair）
            $leverage_ref = new FundPairReference($strSymbol);
            $this->ar_leverage_ref[] = $leverage_ref;
        }
        $this->CreateGroup(array_merge($arRef, $this->ar_leverage_ref));

    生产端 ``sz161130cn.php`` 的参考数据表实测长这样（8 行）::

        SZ161130  基金自己
        ^NDX      估值标的
        hf_NQ     实时标的（期货）
        QQQ       杠杆标的
        TQQQ      杠杆标的
        USDCNH    即期外汇
        USDCNY    即期外汇
        USCNY     中间价

    实时标的与杠杆标的都在 ``fundpair`` 这一张表上，但读法方向相反，
    对照说明见 :func:`_realtime_symbol`。

    ## 本版的取舍：**有什么放什么**

    缺的符号仍然进列表（不像以前那样先查一遍 stock 表再跳过），因为"这个符号
    应该存在但库里没有"本身就是一条诊断信息 —— 由
    :func:`quote_state_of` 在页面上标成「库里无此符号」，比悄悄少一行更诚实。

    顺序按生产端：基金 → 估值标的 → 实时标的 → 杠杆标的 → 外汇 → 中间价。
    """
    symbols = [symbol]
    if source is None:
        return symbols

    est_symbol, forex_symbol, cny_symbol = _fund_reference_symbols(symbol, source)
    _append(symbols, est_symbol)
    _append(symbols, _realtime_symbol(est_symbol, source))
    for leverage in _leverage_symbols(est_symbol, source):
        _append(symbols, leverage)
    for extra in _cnh_and_oil_symbols(symbol):
        _append(symbols, extra)
    _append(symbols, forex_symbol)
    _append(symbols, cny_symbol)
    return symbols


#: 离岸人民币。PHP ``_qdii.php:13`` 里 ``$strCNH = 'fx_susdcnh';`` 是**写死的**，
#: 与基金是哪个 QDII 地区无关 —— ``QdiiAccount::Create()`` 无条件
#: ``new MyStockReference($strCNH)``，再经 ``EchoAll()`` 塞进
#: ``array_merge($acct->GetStockRefArray(), [$acct->oil_ref, $acct->cnh_ref], ...)``。
#: 所以它在页面上的位置是"杠杆标的之后、外汇之前"，不是跟着地区表走。
CNH_SYMBOL = "fx_susdcnh"

#: 原油期货。``_qdii.php:17-26``：只有 XOP 组（华宝油气那一支）多带一个
#: ``hf_CL``，其余 QDII 是 ``$strOil = false``。
OIL_SYMBOL = "hf_CL"


def _cnh_and_oil_symbols(symbol: str) -> list:
    """``[$acct->cnh_ref, $acct->oil_ref]`` 这两个"页面级附加标的"。

    返回顺序与 PHP 一致（先 CNH 后原油），缺符号时照样给出 ——
    :func:`quote_state_of` 会在页面上标成「库里无此符号」，比悄悄少一行诚实。
    """
    result = [CNH_SYMBOL]
    try:
        from core.stocksymbol import in_array_xop_qdii
    except ImportError:
        return result
    try:
        if in_array_xop_qdii(symbol):
            result.append(OIL_SYMBOL)
    except Exception:
        pass
    return result


def _append(symbols: list, value: Any) -> None:
    """去重追加；空值跳过。"""
    if not value:
        return
    text = str(value)
    if text not in symbols:
        symbols.append(text)


def _fund_reference_symbols(symbol: str, source: Any) -> tuple:
    """取该基金的（估值标的, 即期外汇, 中间价）三个符号。

    走 ``refobj`` 的地区表 —— 港股 QDII 用 ``HKCNY``/``fx_shkdcny``、
    日本用 ``JPCNY``/``fx_sjpycny``，不能一律按美元处理。
    """
    try:
        from ref.refobj import build_qdii_reference
        from calc.fundcalc import qdii_est_symbol, qdii_hk_est_symbol, qdii_jp_est_symbol, qdii_eu_est_symbol
    except ImportError:
        return None, None, None

    est_symbol = qdii_est_symbol(symbol)
    for finder in (qdii_hk_est_symbol, qdii_jp_est_symbol, qdii_eu_est_symbol):
        if est_symbol:
            break
        try:
            est_symbol = finder(symbol)
        except Exception:
            est_symbol = None

    forex_symbol = None
    cny_symbol = None
    try:
        ref = build_qdii_reference(symbol, source=source)
        cny_symbol = getattr(ref, "cny_symbol", None)
        forex_symbol = getattr(ref, "forex_symbol", None)
    except Exception:
        pass
    return est_symbol, forex_symbol, cny_symbol


#: 「谁跟踪我」：``fundpair`` 里 ``stock_id`` = 估值标的的那些行的 ``id``。
#: 对应 ``GetFundPairSql()->GetSymbolArray($strEstSymbol)``
#: （``php/sql/sqlstockpair.php:8``，注意它最后 ``sort($arSymbol)``）。
_LEVERAGE_SQL = (
    "SELECT s.symbol FROM fundpair f JOIN stock s ON s.id = f.id "
    "WHERE f.stock_id = (SELECT id FROM stock WHERE symbol = %s) "
    "ORDER BY s.symbol"
)

#: 「我跟踪谁」：``fundpair`` 里 ``id`` = 估值标的那一行的 ``stock_id``。
#: 对应 ``GetFundPairSql()->GetPairSymbol($strSymbol)``
#: （``php/sql/sqlstockpair.php:38``）。
_PAIR_SYMBOL_SQL = (
    "SELECT s.symbol FROM fundpair f JOIN stock s ON s.id = f.stock_id "
    "WHERE f.id = (SELECT id FROM stock WHERE symbol = %s) LIMIT 1"
)


def _fundpair_query(source: Any, sql: str, params: tuple) -> list:
    """在 ``fundpair`` 上跑一条单列查询，返回符号字符串列表。

    抽出来是因为「实时标的」与「杠杆标的」读的是同一张表、同一个方向相反的坑，
    只留一处游标样板更容易保证两边一致。

    **这里为什么用兜底 return []**：``fundpair`` 在演示库里只有 3 行、且很多
    分支查不到东西，而这张表的作用只是"参考数据里多几行"。查不出来不该让整个
    详情页挂掉 —— PHP 那边 ``SqlGetStockSymbol()`` 查不到也只是返回 ``false``。
    """
    connection = getattr(source, "connection", None)
    if connection is None:
        return []
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    except Exception:
        return []
    finally:
        cursor.close()

    symbols = []
    for row in rows:
        symbol = _row_symbol(row)
        if symbol:
            symbols.append(symbol)
    return symbols


def _row_symbol(row: Any) -> str:
    """从一行里取第一个字段。

    pymysql 的 ``DictCursor`` 返回 dict，普通 cursor 返回 tuple，两种都得认 ——
    只认一种的话换个游标类型就会**静默全变空**，比直接报错还难查。
    """
    try:
        value = row["symbol"] if isinstance(row, dict) else row[0]
    except (KeyError, IndexError, TypeError):
        return ""
    return str(value) if value else ""


def _realtime_symbol(est_symbol: Any, source: Any) -> Any:
    """实时标的（通常是境外期货）。对应 ``php/stock/fundref.php:158``。

    PHP 那句很短，但套了两层：

    .. code-block:: php

        function GetRealtimeRef() {
            return method_exists($this->est_ref, 'GetPairRef') ? $this->est_ref->GetPairRef() : false;
        }

    ``est_ref`` 是 ``MyPairReference``（``php/stock/fundpairref.php:6``），
    它在**构造时**就去查配对表：

    .. code-block:: php

        if ($strPair = $pair_sql->GetPairSymbol($strSymbol))
            $this->pair_ref = new MyStockReference($strPair);

    而 ``GetPairSymbol()`` 落到 SQL 上就是「``fundpair`` 里 ``id`` = 该符号
    那一行的 ``stock_id``」。

    ## 与 :func:`_leverage_symbols` 的方向正好相反

    同一张表、两种读法：

    * 杠杆标的 —— **谁跟踪我**（``stock_id = 估值标的`` 的那些 ``id``）；
    * 实时标的 —— **我跟踪谁**（``id = 估值标的`` 的那个 ``stock_id``）。

    搞反了就会把"跟踪这个指数的 ETF"当成"这个指数跟踪的期货"，页面照样出得来、
    只是数据全错（``php/sql/sqlpair.php`` 的 ``ReadPair`` / ``GetIdArray``
    分别对应这两条 SQL，可以对照着看）。

    生产端 ``SZ161130`` 参考表第 3 行的 ``hf_NQ`` 就是这么来的 ——
    ``^NDX`` 配对到纳斯达克 100 期货。演示库的 ``fundpair`` 里没有
    ``id = ^NDX`` 的行，所以实时标的取不到，这一档暂时是空的。
    """
    if not est_symbol:
        return None
    found = _fundpair_query(source, _PAIR_SYMBOL_SQL, (str(est_symbol),))
    return found[0] if found else None


def _leverage_symbols(est_symbol: Any, source: Any) -> list:
    """查 ``fundpair``：跟踪同一个估值标的的**杠杆/替代 ETF**。

    对应 PHP ``GetFundPairSql()->GetSymbolArray($strEstSymbol)``。
    列语义见 :func:`_realtime_symbol` 里那段对照 —— 这边是"谁跟踪我"。
    """
    if not est_symbol:
        return []
    return _fundpair_query(source, _LEVERAGE_SQL, (str(est_symbol),))


def build_stock_references(symbols: Iterable[str], quote_loader: Any = None,
                           source: Any = None) -> list:
    """为每个符号建一个引用对象，并**先读库快照、再兜底联网**装填行情。

    对应 PHP 页面里的 ``new StockReference($strSymbol)`` —— PHP 那时会同步抓新浪。

    ## 两条与本版有关的取舍

    1. **行情来源**：本项目的部署形态是"``quoteupdatercli --daemon`` 抓、页面读"，
       所以优先读 ``realtimequote`` 快照；快照没有该符号才联网。
       详见 ``auto/refquote.py``。
    2. **加载失败不影响页面**。取不到行情时 ``has_data`` 为 ``False``，
       参考数据表按 PHP 的 else 分支输出空串，表格列数照样对齐。
       所以用 ``try`` 包住是保守做法 —— 页面不该因为某个标的没抓到就整体报错。

    ``source`` 是数据源（要 ``get_stored_quote`` / ``get_stock_id``）；
    ``quote_loader`` 保留给测试注入（传了就顶掉默认装填）。
    """
    refs = []
    for symbol in symbols:
        refs.append(_build_one(symbol, source=source, quote_loader=quote_loader))
    return refs


def _build_one(symbol: str, source: Any = None, quote_loader: Any = None) -> Any:
    """建一个引用对象并尽力装填行情。

    优先用 ``refobj.MysqlReference``（它带净值/仓位/校准值，是详情页需要的）；
    ``refobj`` 不可用时退回 ``stockref.StockReference``（只有行情，字段更少）。

    **中间价符号（USCNY/HKCNY/JPCNY/EUCNY）用 ``CnyReference``**：
    PHP 的参考数据表对它们就是 ``new CnyReference(...)``（生产端该行时间
    固定 09:15、取 ``netvaluehistory``）。统一用 ``MysqlReference`` 会让
    ``realtimequote`` 的旧快照盖住净值口径 —— 实测演示库 USCNY 被盖成
    10-14/6.6917，而净值表里明明是 10-16/6.722（2026-10-19 修）。
    """
    try:
        from ref.refobj import MysqlReference
    except ImportError:
        return _build_one_via_stockref(symbol, quote_loader)

    try:
        from ref.refobj import QDII_REGIONS, CnyReference
        mid_rate_symbols = {pair[0] for pair in QDII_REGIONS.values()}
        factory = CnyReference if symbol in mid_rate_symbols else MysqlReference
    except ImportError:
        factory = MysqlReference

    try:
        ref = factory(symbol, source=source)
    except Exception:
        return _build_one_via_stockref(symbol, quote_loader)

    # 注入的 loader 顶掉默认装填结果（测试用）
    if quote_loader is not None:
        try:
            ref.quote_source = quote_loader(ref)
        except Exception:
            pass
    return ref


def _build_one_via_stockref(symbol: str, quote_loader: Any = None) -> Any:
    """回退实现：只有行情的瘦引用对象。"""
    from ref.stockref import StockReference

    ref = StockReference(symbol, quote_loader=quote_loader)
    try:
        ref.load_sina_data()
    except Exception:
        try:
            ref.set_has_data(False)
        except Exception:
            pass
    return ref


def build_reference_table(
    refs: Sequence[Any],
    source: Any = None,
    wide: bool = False,
    admin: bool = False,
    layout_width: int = None,
    title: str = None,
) -> dict[str, Any]:
    """一步到位的便捷函数（不需要复用 service 实例时用这个）。"""
    return ReferenceService(source, layout_width=layout_width).get_table(
        refs, wide=wide, admin=admin, title=title
    )


def default_reference_service_factory() -> ReferenceService:
    """创建使用当前请求数据库连接的参考数据服务（名称从库里查）。"""
    from core.stockdata import MySqlFundDataSource
    from web.db import get_db

    return ReferenceService(MySqlFundDataSource(get_db()))
