"""历史数据页面服务。

对应 PHP 的 6 个历史数据段落（``php/ui/*paragraph.php``）：

===============================  =========================  ==========================
页面（生产端）                    本模块 kind                 对应 PHP
===============================  =========================  ==========================
历史价格                          ``stockhistory``           stockhistoryparagraph.php
基金溢价记录                      ``fundhistory``            fundhistoryparagraph.php
净值记录                          ``netvaluehistory``        netvaluehistoryparagraph.php
净值价格比较                      ``netvalueclose``          netvaluecloseparagraph.php
校准记录                          ``calibrationhistory``     calibrationhistoryparagraph.php
基金场内份额                      ``shareshistory``          fundshareparagraph.php
===============================  =========================  ==========================

PHP 里这些页面各自独立、各自算数；这里把"取某张表的某几行"统一成
``HistoryService.get_table()``，列定义与单位换算集中在 :data:`HISTORY_KINDS`，
取数与显示口径复用 ``historytable.py``。

**净值记录还有第二种形态**：CK4 的持仓分支会在估值表之后摆一段
``net_value_paragraph()``（``EchoNetValueHistoryParagraph($ref, false, 0, 1)``，
只一行）。它和 ``get_table("netvaluehistory")`` 是**同一个 PHP 函数**的两种
参数 —— 列选型、行算法全部共用，差别只在行数和"没有数据时整段不出现"。
见 :meth:`HistoryService.net_value_paragraph`。

**与 PHP 的关键差异**：PHP 直接 ``echo`` HTML；这里返回 ``(列, 行)`` 数据结构，
由模板渲染。颜色不再是 ``<font color>``，而是随单元格一起返回颜色名，
模板映射成 CSS 类。
"""

from __future__ import annotations

from typing import Any

from web.bootstrap import install_paths

install_paths()

from calc.fundgroups import get_symbol_group  # noqa: E402
from sql.historytable import (  # noqa: E402
    CalibrationSql,
    DailyTimeSql,
    FundEstSql,
    MIN_FLOAT_VAL,
    NetValueHistorySql,
    TABLE_COMMON_DISPLAY,
    SharesHistorySql,
    StockHistorySql,
    TableRow,
    calibration_hedge_multiplier,
    format_hm,
    format_number_display,
    format_percentage_display,
    format_price_display,
    format_ratio_display,
    format_turnover_display,
    percentage_change,
    php_number_format,
    stock_calc_hedge,
    use_same_day_net_value,
)
from core.stockdata import FundDataSource, MySqlFundDataSource  # noqa: E402
from core.stocksymbol import (  # noqa: E402
    StockSymbol,
    in_array_hk_mix,
    in_array_lof_mix,
    in_array_qdii,
    in_array_qdii_eu,
    in_array_qdii_hk,
    in_array_qdii_jp,
    in_array_qdii_mix,
)
from ref.holdingsref import StockGetHoldingsReference, ratio_of  # noqa: E402
from ref.refobj import NETVALUE_PRECISION, QDII_REGIONS, QdiiReference  # noqa: E402
from ui.uicolumn import POS_NETVALUE_DIFF  # noqa: E402
from web.db import get_db  # noqa: E402


# ---------------------------------------------------------------------------
# 仓位反算（对应 php/ui/netvaluehistoryparagraph.php 顶部两个函数）
# ---------------------------------------------------------------------------

#: 仓位公式的说明文字。写成常量是为了让**页面上的说明**与
#: :func:`qdii_get_stock_position` 的实现保持同一份口径 —— 改公式时必须同时改这里，
#: 否则页面会开始撒谎（这跟 EST 那三档公式的做法一致，见
#: :data:`~web.services.fundest_service.EST_FORMULA`）。
#: 四句分别对应 ``php/stock.php:121`` 与 ``netvaluehistoryparagraph.php:7-13``。
POSITION_PERCENTAGE_DEF = (
    "StockGetPercentage(divisor, dividend) = (dividend ÷ divisor − 1) × 100"
)
POSITION_NET_PERCENT = "净值涨跌% = StockGetPercentage(上期净值, 净值)"
POSITION_EST_PERCENT = (
    "标的涨跌% = StockGetPercentage(上期汇率 ÷ (标的 ÷ 上期标的), 汇率)"
    " = (标的 ÷ 上期标的 × 汇率 ÷ 上期汇率 − 1) × 100"
)
POSITION_FORMULA = "仓位 = 净值涨跌% ÷ 标的涨跌%"
#: PHP 源码里那行注释（``netvaluehistoryparagraph.php:4``）—— 公式就是它解出来的
POSITION_DERIVATION = "(标的 × 汇率 ÷ 上期标的 × 上期汇率 − 1) × 仓位 = 净值 ÷ 上期净值 − 1"

#: 逐步算法。第 1 条是本页与「净值记录」页唯一的差别，必须写在最前面。
POSITION_STEPS = (
    "① 取相邻两个净值拐点（GetSwitchDates）：本期的「上期」是**上一个拐点**，"
    "不是上一交易日 —— 一段跨好几个交易日，累计涨跌才有信噪比；",
    "② 净值、上期净值（netvaluehistory 的单位净值）；",
    "③ 标的（est）与上期标的：est 有净值记录取净值，否则取行情价；",
    "④ 汇率与上期汇率（中间价 netvaluehistory）；",
    "⑤ 两个涨跌%相除，number_format 保留 2 位；与库里登记的仓位"
    "（同样 2 位）字符串相等时整行加黄。",
)

#: 三个闸门，逐条对应 PHP ``_QdiiGetStockPosition()`` 的 if。
POSITION_GATES = (
    "|净值涨跌%| > 阈值（默认 4%，页面 ?input= 可改）",
    "|标的涨跌%| > MIN_FLOAT_VAL",
    "仓位 > MIN_FLOAT_VAL（负数仓位也不出）",
)


def _plain(value: Any) -> str:
    """取 ``(文本, 颜色)`` 二元组里的文本；本来就是字符串则原样返回。

    说明段只关心数字本身 —— 涨跌染色是表格的事，且 ``|safe`` 之外的渲染
    会把二元组整个打印出来（``('4.6176', 'black')``）。
    """
    if isinstance(value, tuple) and value:
        return str(value[0])
    return "" if value is None else str(value)


def _chart_number(value: Any) -> Any:
    """把净值列的原始值转成 float（``ReadColumn`` 的 ``floatval``）。

    MySQL 的 DECIMAL 列经 pymysql 出来是 ``Decimal`` —— **JSON 序列化不了**，
    而 ``chart`` 会随 ``/api/history/...`` 一起 ``jsonify``。所以这里统一转
    float；转不动的（None / 空串）返回 ``None``，调用方把那一行从图数据里
    剔掉（PHP 的 ``floatval`` 会给 0.0，但净值列为空的记录本就病态，
    剔掉与画一个假深谷之间，前者对页面更诚实）。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_text(value: Any, places: int = 2) -> str:
    """把涨跌百分比排成 ``"-5.35%"``；算不出来（None / 非数字）返回空串。

    只给说明段用：表里的百分比走 ``format_percentage_display``（带阈值归零
    与涨跌染色），说明段要的是**原始值**，两个口径别混用。
    """
    if value is None:
        return ""
    try:
        return "%.*f%%" % (places, float(value))
    except (TypeError, ValueError):
        return ""


def qdii_get_stock_position(
    proportion: Any,
    prev: Any,
    net_value: Any,
    cny_prev: Any,
    cny: Any,
    f_input: Any = POS_NETVALUE_DIFF,
) -> Any:
    """``_QdiiGetStockPosition()``（``netvaluehistoryparagraph.php:5``）。

    PHP 原文注释给出了推导::

        (est * cny / estPrev * cnyPrev - 1) * position = (nv / nvPrev - 1)

    即**净值涨幅 = 仓位 × 标的涨幅**（标的涨幅里含汇率变动）。把汇率按标的
    涨幅归一后算汇率自身的变动，两者一除就是仓位。波动小于 ``f_input``
    （默认 4%）时不算 —— 噪声比信号大。

    ``proportion`` 是"标的（已换汇）的涨跌比例"，两条路给它的方式不同：

    * QDII（有 est 标的）：``est / estPrev``；
    * 持仓型 LOF（无 est 标的）：各成分股按持仓比例加权后的合计
      （见 :func:`qdii_mix_position`）。
    """
    percent = percentage_change(prev, net_value)
    if percent is None or abs(percent) <= f_input:
        return None

    # PHP: StockGetPercentage($fCnyPrev / $fEstProportion, $fCny)
    # proportion 为 0 时 PHP 是裸除法 —— PHP 8 下直接 DivisionByZeroError
    # fatal。这里返回 None（页面留空），记在 known deviation 里。
    try:
        divisor = float(cny_prev) / float(proportion)
    except (TypeError, ValueError, ZeroDivisionError):
        return None

    est_percent = percentage_change(divisor, cny)
    if est_percent is None or abs(est_percent) <= MIN_FLOAT_VAL:
        return None
    value = percent / est_percent
    if value <= MIN_FLOAT_VAL:
        return None
    return php_number_format(value, 2)


def qdii_mix_position(
    holdings_ref: Any,
    date: Any,
    prev_date: Any,
    prev: Any,
    net_value: Any,
    cny_prev: Any,
    cny: Any,
    f_input: Any = POS_NETVALUE_DIFF,
) -> Any:
    """``_QdiiMixGetPosition()``（``netvaluehistoryparagraph.php:23``）。

    持仓型基金没有单一 est 标的，改用**各成分股按持仓比例加权**的涨跌
    当 ``proportion``：::

        Σ[第 i 只成分股的涨跌 × 第 i 只的持仓比例 / 100]

    单个成分股的两日涨跌由 ``GetProportionArray()`` 给（里面已按 A/H/美股
    分别还原汇率），**任一只缺数据整段返回 false** —— 那时仓位格留空。

    与 PHP 的一处刻意差别：PHP 用 ``foreach`` 的下标把 ``arHoldingsRatio``
    和 ``arProportion`` 配对（``:29-32``）。Python 侧
    :meth:`HoldingsReference._build_holding_refs` 在"成分股符号查不到"时会
    **跳过**那只（PHP 不跳，照样建一个引用），两个列表于是错位。
    所以这里按 ``stock_id`` 回查比例（用 :func:`ref.holdingsref.ratio_of`，
    它同时治 int/str 键不一致）：正常数据下结果与 PHP 完全相同，
    错位时也不会把 A 股的比例安到港股头上。
    """
    proportions = holdings_ref.get_proportion_array(date, prev_date)
    if not proportions:
        return None

    ratios = holdings_ref.get_holdings_ratio_array()
    refs = holdings_ref.get_holdings_ref_array()
    total = 0.0
    for index, ref in enumerate(refs):
        if index >= len(proportions):
            #: PHP 这里会 Notice 后按 null 继续（贡献 0）；截断是等价且不吵的
            break
        ratio = ratio_of(ratios, ref.get_stock_id())
        total += proportions[index] * ratio / 100.0
    return qdii_get_stock_position(total, prev, net_value, cny_prev, cny, f_input)


class HistoryNotFoundError(LookupError):
    """未知的历史数据类型，或该符号没有相应数据。"""


def _column(text: str, color: str = "", width: int = 0) -> dict[str, Any]:
    """一列表头：文本 + 颜色名（可选）+ 宽度（可选）。"""
    return {"text": text, "color": color, "width": width}


# ---------------------------------------------------------------------------
# 列定义（文本与颜色对应 PHP TableColumn* 类，见 php/ui/stocktable.php）
# ---------------------------------------------------------------------------
_COL_DATE = _column("日期", "", 100)
_COL_PRICE = _column("价格", "blue", 70)
_COL_QUANTITY = _column("数量", "indigo", 120)
_COL_SETTLE_PRICE = _column("结算价", "blue", 70)
_COL_ADJCLOSE = _column("复权价格", "blue", 70)
_COL_NETVALUE = _column("净值", "olive", 90)
_COL_PREMIUM = _column("溢价", "orange", 70)
_COL_CHANGE = _column("涨幅", "", 70)
_COL_POSITION = _column("仓位", "", 60)
_COL_CALIBRATION = _column("校准值", "", 140)
_COL_TIME = _column("时间", "", 60)
_COL_HEDGE = _column("对冲值", "navy", 80)
_COL_COUNT = _column("次数", "", 50)
_COL_SHARE = _column("份额(万)", "", 110)
_COL_SHARE_DIFF = _column("场内新增(万)", "", 110)
_COL_TURNOVER = _column("换手", "green", 100)
_COL_TURNOVER_NEW = _column("新增换手", "green", 120)
_COL_OFFICIAL_EST = _column("官方EST", "magenta", 80)
_COL_ERROR = _column("误差", "", 70)
_COL_RATIO = _column("比价", "", 100)


def _stock_column(symbol: str, width: int = 80) -> dict[str, Any]:
    """对照标的列（如 ``^NDX``），对应 PHP ``TableColumnStock``：maroon 色。"""
    return _column(symbol, "maroon", width)


# ---------------------------------------------------------------------------
# 各页面的列组成
# ---------------------------------------------------------------------------
def _columns_stockhistory(source: FundDataSource, symbol: str, compare: Any, forex: Any) -> list:
    columns = [_COL_DATE, _COL_PRICE, _COL_QUANTITY]
    # PHP: $ref->IsSinaFuture() ? 结算价 : 复权价格
    # 期货没有"复权价"概念，PHP 显示「结算价」；用 StockSymbol 判定而不是数据源，
    # 因为这个判断只跟符号本身有关（hf_CL / hf_CHA50CFD 之类）。
    is_future = False
    try:
        from core.stocksymbol import StockSymbol

        is_future = bool(StockSymbol(symbol).is_sina_future())
    except Exception:
        is_future = False
    columns.append(_COL_SETTLE_PRICE if is_future else _COL_ADJCLOSE)
    if compare:
        columns.append(_stock_column(compare))
        if forex:
            columns.append(_stock_column(forex))
        columns.append(_COL_RATIO)
    return columns


def _est_netvalue_column(est_symbol: Any, has_netvalue: bool) -> dict[str, Any]:
    """fundhistory 第 8 列，对应 PHP ``RefGetTableColumnNetValue()``
    （``php/ui/stocktable.php:320``）的三分支：::

        $strStockDisplay = TableColumnGetStock($ref);   // 符号 display
        if ($ref->CountNetValue() > 0)           return new TableColumnNetValue($strStockDisplay);  // <sym>净值
        if ($ref->IsSinaFutureExceptGoldCN())    return new TableColumnSettlePrice($strStockDisplay);// <sym>结算价
        return new TableColumnPrice($strStockDisplay, 90);                                          // <sym>价格

    生产端 sz161125cn.php 上 SZ161125 的 est 是 ^GSPC（``QdiiGetEstSymbol``
    映射到指数而非 ETF，``qdiiref.php:26``），^GSPC 没有净值记录，所以走
    第三支显示「^GSPC价格」；映射到 ETF 的基金（XOP/KWEB 等）有净值记录，
    显示「XOP净值」。两条分支宽度都是 90。
    """
    text = str(est_symbol)
    if has_netvalue:
        return _column(text + "净值", "olive", 90)
    try:
        from core.stocksymbol import StockSymbol

        if StockSymbol(text).is_sina_future_except_gold_cn():
            return _column(text + "结算价", "blue", 90)
    except Exception:
        pass
    return _column(text + "价格", "blue", 90)


def _columns_fundhistory(est_symbol: Any, est_has_netvalue: bool = False) -> list:
    """对应 PHP ``fundhistoryparagraph.php:74-82`` 的列组装。

    后三列（官方EST/时间/误差）只在 ``fundest`` 有记录时才加；第 8 列
    「标的净值」还要 est_ref 存在才加（``:81`` 的 ``if ($est_ref)``）——
    **表头级**判断，不是逐行：PHP 里某行没有 fundest 记录时该行只出前
    四格，表头仍摆八列（生产端实测如此）。

    ``est_symbol`` 为 ``None`` 表示 fundest 无记录，只出前四列。
    """
    columns = [_COL_DATE, _COL_PRICE, _COL_NETVALUE, _COL_PREMIUM]
    if est_symbol:
        # PHP 只在 fundest 表有记录时才加这几列；这里由调用方判断后传入
        columns += [_COL_OFFICIAL_EST, _COL_TIME, _COL_ERROR]
        columns.append(_est_netvalue_column(est_symbol, est_has_netvalue))
    return columns


def _columns_netvaluehistory(cny_symbol: Any, est_symbol: Any) -> list:
    """对应 PHP ``GetNetValueTableColumn()``（netvaluehistoryparagraph.php:39）。

    PHP 在这里复用同一个 ``TableColumnNetValue``（"净值"）给基金净值和中间层净值，
    于是页面上会出现两个同名的「净值」列。这在 PHP 的表格里靠位置区分，
    但迁移到独立页面后会让读者分不清哪个是基金自己的净值。

    所以这里把中间层那列标成「标的净值」—— 语义相同、但不再有歧义。
    """
    columns = [_COL_DATE, _COL_NETVALUE, _COL_CHANGE]
    if cny_symbol:
        columns.append(_stock_column(cny_symbol))
        columns.append(_COL_CHANGE)
        if est_symbol:
            # 中间层净值（如 QQQ 的净值），加前缀避免与基金净值的「净值」列混淆
            columns.append(_column("标的净值", "olive", 90))
            columns.append(_COL_CHANGE)
        columns.append(_COL_POSITION)
    return columns


def _columns_calibrationhistory(is_fund: bool) -> list:
    columns = [_COL_DATE, _COL_CALIBRATION, _COL_TIME, _COL_COUNT]
    if is_fund:
        columns += [_COL_NETVALUE, _COL_HEDGE]
    return columns


def _columns_shareshistory() -> list:
    return [
        _COL_DATE,
        _COL_SHARE,
        _COL_SHARE_DIFF,
        _COL_QUANTITY,
        _COL_TURNOVER,
        _COL_TURNOVER_NEW,
    ]


# 页面 kind -> 标题与说明
HISTORY_KINDS: dict[str, dict[str, str]] = {
    "stockhistory": {
        "title": "历史价格",
        "description": "该符号的每日行情：收盘价、成交量与复权价。",
    },
    "fundhistory": {
        "title": "基金溢价记录",
        "description": "场内价格相对单位净值的溢价。**净值取前一日** —— 当天的净值要等当晚美股收盘后才公布。",
    },
    "netvaluehistory": {
        "title": "净值记录",
        "description": "单位净值、汇率与中间层净值的逐日对照，并据此反算实际仓位。",
    },
    "netvalueclose": {
        "title": "净值价格比较",
        "description": "同一天的场内价格与单位净值并排比较，看两者是否有稳定偏差。",
    },
    "calibrationhistory": {
        "title": "校准记录",
        "description": "每次拿到官方净值后做自动校准的结果，用来观察长期趋势。",
    },
    "shareshistory": {
        "title": "基金场内份额",
        "description": "场内份额变化与成交量的比值（换手率）。份额单位是万份。",
    },
    "fundposition": {
        "title": "仓位估算",
        "description": (
            "只挑净值出现**局部拐点**的交易日，用相邻两个拐点之间的累计涨跌反算实际仓位。"
            "找的是净值连续几天累计涨跌超过阈值（默认 4%）的机会。"
        ),
        #: PHP 的 ``fundpositioncn.php`` **没有翻页**：``$acct->GetNum()`` 只截行数，
        #: 不生成菜单链接（对比 netvaluehistorycn.php 有 ``GetMenuLink``）。这里显式关掉，
        #: 免得模板渲染出一个点了没反应的"下一页"。
        "paged": False,
    },
}


#: 只有这几个 builder 多收一个"浓度阈值"参数。放集合里比在 ``get_table``
#: 里写 ``if kind == 'fundposition'`` 好扩展，也不会让其它 builder 被迫收参数。
_BUILDERS_WITH_INPUT = frozenset({"fundposition"})


class HistoryService:
    """把某符号的历史数据整理成可渲染的表格。

    ``source`` 提供符号/名称/仓位等参考数据（``FundDataSource``）；
    ``connection`` 是请求级数据库连接（与 ``source`` 复用同一个即可）。
    """

    def __init__(self, source: FundDataSource, connection: Any = None):
        self.source = source
        self.connection = connection if connection is not None else getattr(source, "connection", None)

    # -- 对外 -------------------------------------------------------------
    def get_table(
        self,
        kind: str,
        symbol: str,
        start: int = 0,
        num: int = TABLE_COMMON_DISPLAY,
        f_input: Any = POS_NETVALUE_DIFF,
    ) -> dict[str, Any]:
        """取一个历史数据表格。

        ``start`` / ``num`` 与 PHP 的 ``stockhistorycn.php?symbol=X&start=&num=``
        语义一致：``num=0`` 表示"全部记录"。

        ``f_input`` 只有 **fundposition** 用得到 —— 它是页面上"净值涨跌%阈值"
        输入框的值（``POS_NETVALUE_DIFF`` 是 PHP 的默认 4%，``stockdisp.php:32``）。
        其余页面在 PHP 里写死这个常量，这里也就跟着不传。
        """
        if kind not in HISTORY_KINDS:
            raise HistoryNotFoundError(kind)
        if self.connection is None:
            raise HistoryNotFoundError("没有可用的数据库连接")

        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            raise HistoryNotFoundError(symbol)

        builder = getattr(self, "_build_" + kind)
        if kind in _BUILDERS_WITH_INPUT:
            built = builder(symbol, stock_id, start, num, f_input)
        else:
            built = builder(symbol, stock_id, start, num)
        columns, rows, total = built[0], built[1], built[2]

        meta = HISTORY_KINDS[kind]
        output = {
            "kind": kind,
            "symbol": symbol,
            "name": self.source.get_stock_name(symbol) or symbol,
            "group": get_symbol_group(symbol),
            "title": meta["title"],
            "description": meta["description"],
            "paged": meta.get("paged", True),
            "columns": columns,
            "rows": rows,
            "total": total,
            "start": start,
            "num": num,
            "other_kinds": [k for k in HISTORY_KINDS if k != kind],
        }
        #: 只有仓位估算页有算法说明：这一页的"仓位"是**反算**出来的，
        #: 不像其它历史页那样直接读库，不摊开没法验算。没有一行算出仓位
        #: 时整段不显示（返回 None），与详情页 ``_build_est_formula`` 一致。
        if kind == "fundposition":
            output["formula"] = self._position_formula_note(rows, f_input)
            #: 走势图数据（PHP 的 CSV 两列）；没有净值记录时为 None。
            output["chart"] = built[3]
        return output

    # -- 净值记录段落（``EchoNetValueHistoryParagraph``） -------------------
    def net_value_paragraph(
        self, symbol: str, ref: Any = None, start: int = 0, num: int = 1
    ) -> dict[str, Any] | None:
        """``EchoNetValueHistoryParagraph()``（``netvaluehistoryparagraph.php:138``）。

        CK4 持仓分支在估值表之后调它（``fundestparagraph.php:171``）::

            EchoNetValueHistoryParagraph($ref, false, 0, 1);

        即 **csv 关、从第 0 行起、只要 1 行** —— 页面上只摆最新一天的净值。
        独立页 ``netvaluehistorycn.php`` 用的是同一函数、``TABLE_COMMON_DISPLAY``
        行数，那一页依赖账号体系（``SymbolAccount``），在本轮范围之外，
        所以这里只实现段落本身。

        与 :meth:`get_table` 的 ``netvaluehistory`` 是**同一个 PHP 函数**的两种
        参数：列选型、行算法全部共用（``_net_value_refs`` /
        ``_row_netvaluehistory``），差别只在行数和"没有数据时整段不出现"。

        ``ref`` 是调用方手里的引用（CK4 传 ``HoldingsReference``）。
        PHP 在 LofMix 支会重新构造一个覆盖它，这里只有传进来的不是持仓引用时
        才重建（见 :meth:`_net_value_refs`）。

        **没有净值记录时返回 ``None``**（PHP ``if (($iTotal =
        $ref->CountNetValue()) == 0) return;``）—— 连表头都不要，
        不是摆一个空表。
        """
        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            return None

        netvalue = NetValueHistorySql(self.connection)
        total = netvalue.count(stock_id)
        if total == 0:
            return None

        cny_symbol, est_symbol, holdings_ref = self._net_value_refs(symbol, ref)
        position = self.source.get_position(stock_id)
        est_has_netvalue = self._est_has_netvalue(netvalue, est_symbol)

        rows = []
        for record in netvalue.get_all(stock_id, start, num):
            date = str(record["date"])
            prev_date = netvalue.get_date_prev(stock_id, date)
            rows.append(
                self._row_netvaluehistory(symbol, netvalue, record, prev_date,
                                          cny_symbol, est_symbol, position, holdings_ref,
                                          est_has_netvalue)
            )

        #: PHP 的 ``IsTableCommonDisplay($iStart, $iNum)`` 为真时
        #: （``table.php:6``：``iStart == 0 && iNum <= 10``）链接走
        #: ``GetNetValueHistoryLink()``、菜单链接为空。CK4 传的 (0, 1) 恒为真，
        #: 所以只实现这一支；分页那一支（``StockGetMenuLink``）依赖账号体系。
        return {
            "kind": "netvaluehistory",
            "symbol": symbol,
            "title": HISTORY_KINDS["netvaluehistory"]["title"],
            "columns": _columns_netvaluehistory(cny_symbol, est_symbol),
            "rows": rows,
            "total": total,
            "start": start,
            "num": num,
        }

    # -- 历史价格 ---------------------------------------------------------
    def _build_stockhistory(self, symbol: str, stock_id: Any, start: int, num: int):
        table = StockHistorySql(self.connection)
        # PHP 只在传入了 compare_ref 时才加对照列；这里按该符号是否有估值标的决定，
        # 这样 /history/stockhistory/XOP 这样的标的页也能显示比价
        compare = self._compare_symbol(symbol)
        forex = self._forex_symbol() if compare else None

        rows = []
        for record in table.get_all(stock_id, start, num):
            rows.append(self._row_stockhistory(table, record, compare, forex))
        return _columns_stockhistory(self.source, symbol, compare, forex), rows, table.count(stock_id)

    def _row_stockhistory(self, table: StockHistorySql, record: Any, compare: Any, forex: Any) -> TableRow:
        date = str(record["date"])
        stock_id = record["stock_id"]

        # PHP 用 $ref->GetPrevPrice()（= 上一交易日的收盘价）作为涨跌基准
        prev = self.source.get_prev_history_close(stock_id, date)
        close = record["close"]

        row = TableRow()
        row.add(date)
        row.add(format_price_display(close, prev))
        row.add(record["volume"])

        adjclose = record["adjclose"]
        row.add(format_price_display(adjclose, prev))

        if compare:
            compare_id = self.source.get_stock_id(compare)
            compare_adjclose = table.get_adjclose(compare_id, date) if compare_id else None
            if compare_adjclose is not None:
                row.add(format_price_display(compare_adjclose))
                # PHP 原文（stockhistoryparagraph.php:24-26）：把复权价从"每盎司美元"
                # 换成"每克人民币"口径再跟对照标的比价。
                # 1000 = 盎司→毫盎司的换算，31.1035 = 金衡盎司克数。
                # 这段只在黄金类比价时才有意义，照抄以保持数值一致。
                ratio_left = float(adjclose) / 1000.0 * 31.1035
                foreign = None
                if forex:
                    forex_id = self.source.get_stock_id(forex)
                    foreign = table.get_adjclose(forex_id, date) if forex_id else None
                    if foreign is not None:
                        row.add(format_price_display(foreign))
                if foreign:
                    ratio = ratio_left / float(compare_adjclose) / float(foreign)
                else:
                    ratio = ratio_left / float(compare_adjclose)
                row.add(format_ratio_display(ratio))
        return row

    # -- 基金溢价记录 -----------------------------------------------------
    def _build_fundhistory(self, symbol: str, stock_id: Any, start: int, num: int):
        history = StockHistorySql(self.connection)
        netvalue = NetValueHistorySql(self.connection)
        # **列集必须包含 time**：`DailyCloseSql` 的默认列只有
        # (id, stock_id, date, close)，用它取 fundest 会拿不到时间，
        # 于是「时间」列永远空着、异常还会被行处理的 try 吞掉（实测踩过）。
        fundest = DailyTimeSql(self.connection, "fundest")
        est_symbol = self._est_symbol(symbol)
        has_est = fundest.count(stock_id) > 0

        # 对应 PHP UseSameDayNetValue()：QDII 用**前一交易日**净值。
        # 判断依据是该符号属于哪个符号组（QDII / 混合 / 港股混合）。
        same_day = use_same_day_net_value(
            symbol,
            is_qdii=in_array_qdii(symbol),
            is_mix=in_array_qdii_mix(symbol),
            is_hk_mix=in_array_hk_mix(symbol),
        )

        # 第 8 列的文本分支要看 est 标的有没有净值记录（PHP 的
        # ``CountNetValue()``，``mysqlref.php:31`` 数 netvaluehistory 行数）。
        est_has_netvalue = self._est_has_netvalue(netvalue, est_symbol) if has_est else False

        rows = []
        for record in history.get_all(stock_id, start, num):
            date = str(record["date"])
            net_date = date if same_day else history.get_date_prev(stock_id, date)
            if not net_date:
                continue
            net_value = netvalue.get_close(stock_id, net_date)
            if net_value is None:
                continue
            # 官方 EST 按**净值日期**查 —— 严格对应 PHP
            # `fundhistoryparagraph.php:42`：
            #     $strDate = $bSameDay ? $arHistory['date'] : $his_sql->GetDatePrev(...);
            #     if ($strNetValue = $net_sql->GetClose($strStockId, $strDate))
            #         $arFundEst = $fund_est_sql->GetRecord($strStockId, $strDate);
            # 即 netvalue 与 fundest 用**同一个** $strDate（QDII 就是前一交易日）。
            # 换成行情日期会一行都查不到（实测踩过）。
            estimate = fundest.get_record(stock_id, net_date) if has_est else None
            rows.append(self._row_fundhistory(record, net_value, estimate, est_symbol,
                                              netvalue, est_has_netvalue, symbol))
        return (_columns_fundhistory(est_symbol if has_est else None, est_has_netvalue),
                rows, history.count(stock_id))

    def _row_fundhistory(self, record: Any, net_value: Any, estimate: Any, est_symbol: Any,
                         netvalue: Any, est_has_netvalue: bool = False,
                         symbol: str = "") -> TableRow:
        date = str(record["date"])
        close = record["close"]

        #: 溢价/误差两格的阈值精度 = **基金符号自身**的 GetPrecision()（基金 3），
        #: 对应 PHP ``$ref->GetPercentageString()`` 里的 ``$this->GetPrecision()``
        #: （stockref.php:129）。与净值列的 NETVALUE_PRECISION(4) 是两码事，
        #: 别混用 —— 传错会让 10-19 / 10-08 那类小误差该归零的不归零
        #: （2026-10-20 对照生产端修掉，见 format_percentage_display docstring）。
        fund_precision = StockSymbol(symbol).get_precision() if symbol else None

        row = TableRow()
        row.add(date)
        row.add(format_price_display(close, net_value))
        #: PHP: $ref->GetNetValueDisplay()（``mysqlref.php:75``）—— **有净值记录
        #: 时精度取 NETVALUE_PRECISION(4)**，否则退回符号自身精度。这一行能出来
        #: 就意味着 ``netvaluehistory`` 命中了（``get_close`` 非空才会 append），
        #: 即 PHP 的 ``iNetValueCount > 0`` 必然成立，所以恒 4 位。
        #:
        #: 生产端 ``sz161125cn.php`` 的「基金溢价记录」净值列是四位
        #: （3.1548 / 3.1448 / 3.1803 / 3.1817）；用默认的 3 位会四舍五入成
        #: 3.155 / 3.145 / 3.18 / 3.182 —— 2026-10-19 对照生产端发现，
        #: 库里存的就是 3.1548（东财/新浪同值），纯显示层取位错。
        row.add(format_price_display(net_value, precision=NETVALUE_PRECISION))
        row.add(format_percentage_display(net_value, close, fund_precision))

        if estimate is not None:
            estimate_close = estimate.get("close")
            #: PHP 的守卫是 ``if ($strEstValue = $arFundEst['close'])``
            #: （``fundhistoryparagraph.php:17``）—— ``""`` 与 ``"0"`` 都是**假值**，
            #: 那三格（官方EST / 时间 / 误差）整段不出现。
            #:
            #: Python 里非空字符串 ``"0"`` 是**真值**，直接写 ``if estimate_close:``
            #: 会给 0 值的 EST 摆出三格 —— 这是 PHP 逐例对照（case.4）实测抓到的。
            #: ``"0.0"`` 在 PHP 里反而是真值，所以只排除这两个字符串，
            #: 不能用 ``float(...) == 0`` 近似。
            if not _php_val_is_false(estimate_close):
                # 官方 EST 与净值同一量纲，直接比
                row.add(format_price_display(estimate_close, net_value))
                # 时间截到分，对应 PHP GetHM()
                row.add(format_hm(estimate.get("time")))
                # 误差：对应 PHP $ref->GetPercentageDisplay($fNetValue, $fEstValue)
                # 即 (估算 / 真实 - 1) * 100，与 StockCompareEstResult() 同向
                row.add(format_percentage_display(net_value, estimate_close, fund_precision))
                if est_symbol:
                    # 最后一列是**标的净值**：中间层在 EST 记录日期上的值，
                    # 供读者判断这次估算是基于哪个行情算出来的
                    est_id = self.source.get_stock_id(est_symbol)
                    est_value = netvalue.get_close(est_id, estimate.get("date")) if est_id else None
                    if est_value is None and est_id and not est_has_netvalue:
                        #: PHP ``GetNetValue()`` 第三支（``mysqlref.php:72``）：est
                        #: **没有**净值记录时才退回行情价（``GetVal() -> GetClose()``）；
                        #: 有净值记录但缺该日期 -> ``false`` -> 这一格为空。
                        #: （原实现不区分两种"取不到"，有净值记录时也会摆出行情价，
                        #: 与 PHP 相反。）
                        est_value = self.source.get_history_close(est_id, estimate.get("date"))
                    #: PHP: $est_ref->GetNetValueDisplay() —— 与基金自己的净值列
                    #: 同一套两档精度：est 有净值记录 4 位，否则用 est 符号自身的
                    #: 精度（^GSPC / QQQ / SPY 这类指数与美股是 2 位）。
                    est_precision = (NETVALUE_PRECISION if est_has_netvalue
                                     else StockSymbol(str(est_symbol)).get_precision())
                    row.add(format_price_display(est_value, precision=est_precision))
        return row

    # -- 净值记录 ---------------------------------------------------------
    def _build_netvaluehistory(self, symbol: str, stock_id: Any, start: int, num: int):
        netvalue = NetValueHistorySql(self.connection)
        cny_symbol, est_symbol, holdings_ref = self._net_value_refs(symbol)
        #: est 有没有净值记录决定「标的净值」列的精度档（PHP 的
        #: ``$est_ref->GetNetValueDisplay()``，``mysqlref.php:75``），
        #: 一次表渲染只查一回，不逐行 COUNT。
        est_has_netvalue = self._est_has_netvalue(netvalue, est_symbol)

        rows = []
        total = netvalue.count(stock_id)
        if total == 0:
            return _columns_netvaluehistory(cny_symbol, est_symbol), rows, total

        position = self.source.get_position(stock_id)
        for record in netvalue.get_all(stock_id, start, num):
            date = str(record["date"])
            prev_date = netvalue.get_date_prev(stock_id, date)
            rows.append(
                self._row_netvaluehistory(symbol, netvalue, record, prev_date,
                                          cny_symbol, est_symbol, position, holdings_ref,
                                          est_has_netvalue)
            )
        return _columns_netvaluehistory(cny_symbol, est_symbol), rows, total

    # -- 仓位估算（``fundpositioncn.php``） ---------------------------------
    def _build_fundposition(self, symbol: str, stock_id: Any, start: int, num: int,
                            f_input: Any = POS_NETVALUE_DIFF):
        """``_echoFundPositionData()``（``woody/res/fundpositioncn.php:8-41``）。

        与 ``netvaluehistory`` 用的是**同一个** ``EchoNetValueItem()``，唯一区别是
        **每一行的"上期"不是上一交易日，而是上一个净值拐点** —— 由
        :meth:`get_switch_dates` 挑出来。这样一段跨越好几个交易日，
        净值累计涨跌才有信噪比（单日涨跌常被 4% 闸门挡掉），
        混合型也才能拿这段区间去问 ``GetProportionArray()`` 要区间持仓比例。

        逐句对应 PHP 的行选择::

            if ($strDate == $arDate[$iIndex]) {
                $iIndex ++;
                if (isset($arDate[$iIndex])) {
                    $bWritten = EchoNetValueItem(...);
                    $iTotal ++;
                    if ($iTotal == $iNum)	break;
                } else break;
            }

        三个容易改错的地方：

        * **``$iTotal`` 数的是"候选行"，不是"写出了仓位的行"**。PHP 的
          ``EchoNetValueItem()`` 结尾无条件调 ``EchoMatchTableColumn()``
          （``netvaluehistoryparagraph.php:114``），所以没过阈值的
          ``$iTotal`` 照样 +1、整行照样显示，只是仓位格空着。
          早按"写出行数"实现会让页面少掉一半的行。
        * **拐点用完就 break**：``$arDate`` 最后一个成员之后没有更早的拐点可配对，
          PHP 直接退出。数组里虽然还有它，页面上却没有那一行。
        * **``$iNum`` 是行数不是 SQL 的 LIMIT**：PHP 先把 ``GetAll()`` 全取出来再截断，
          所以这里也全量取。净值表一个符号通常几十行，代价可以忽略；
          换成 SQL LIMIT 会变成"按原始记录数截断"，显示的候选行数不再受控。

        ``start`` 在 PHP 侧不存在（这一页没有翻页参数），这里同样忽略。

        **顺手收集走势图数据**（PHP 的 ``PageCsvFile``，``fundpositioncn.php:47``）。
        逐句照抄那两列的口径：

        * base（第 1 列净值）= **所有被迭代到的记录**，不只是拐点行 ——
          PHP 的 ``if ($bWritten === false) $csv->Write($strDate, $strNetValue);``
          （``:37``）把没算出仓位的拐点行、乃至两个拐点之间的**非拐点日**
          （``if`` 不进，直接落到 :37）都写进了 CSV；
        * focus（第 2 列仓位）= 只含**算出仓位**的行 —— ``EchoNetValueItem()``
          在算出仓位时写三列（``netvaluehistoryparagraph.php:94,107``），
          ``ReadColumn(2)`` 的 ``count($arWord) > $iColumn`` 只收三列的行；
        * 两个 break 都发生在 :37 **之前**：最后一个拐点日（没有更早的拐点
          可配对）与被 ``num`` 截断的那行。后者特殊 —— 它若算出了仓位，
          三列写入在 ``EchoNetValueItem()`` 里已完成，所以**在** base 里；
          没算出仓位则连 base 都进不去。

        于是图的横轴是净值记录本身（含非拐点日），红线才是稀疏的断线。
        """
        netvalue = NetValueHistorySql(self.connection)
        total = netvalue.count(stock_id)
        cny_symbol, est_symbol, holdings_ref = self._net_value_refs(symbol)
        columns = _columns_netvaluehistory(cny_symbol, est_symbol)
        if total == 0:
            #: 没有净值记录 → 没有图（PHP 的 ``HasFile()`` 为假，``<img>``
            #: 压根不出现）。chart 给 None 而不是空列表，模板与路由据此区分
            #: "这一页没有数据"与"有数据但画不出来"。
            return columns, [], total, None

        est_has_netvalue = self._est_has_netvalue(netvalue, est_symbol)
        position = self.source.get_position(stock_id)
        #: 拐点日期统一转 str —— MySQL 可能返回 date 对象，而下面要用 ``==`` 跟
        #:  ``record["date"]`` 比；PHP 那边两边都是字符串，这里得自己对齐类型。
        switch_dates = [str(date) for date in netvalue.get_switch_dates(stock_id)]

        rows = []
        #: 走势图两列（PHP 的 CSV）：base = 净值列、focus = 仓位列。
        #: 语义见本方法 docstring —— base 含非拐点日，focus 只含算出仓位的行。
        chart_base: list = []
        chart_focus: list = []
        shown = 0
        index = 0
        for record in netvalue.get_all(stock_id):
            date = str(record["date"])
            net_value = record["close"]
            written = False
            if index < len(switch_dates) and date == switch_dates[index]:
                index += 1
                if index >= len(switch_dates):
                    break
                row = self._row_netvaluehistory(symbol, netvalue, record, switch_dates[index],
                                                cny_symbol, est_symbol, position, holdings_ref,
                                                est_has_netvalue, f_input)
                rows.append(row)
                inputs = getattr(row, "position_inputs", None)
                if inputs:
                    #: PHP 在 EchoNetValueItem() 里写三列（date, netvalue, position），
                    #: 所以这一行同时进 base 与 focus。仓位取 number_format 后的
                    #: 两位字符串转 float —— PHP 的 ReadColumn 也是
                    #: floatval($arWord[2])，两边同一个数。
                    written = True
                    chart_base.append((date, net_value))
                    chart_focus.append((date, float(inputs["position"])))
                shown += 1
                if num and shown >= num:
                    break
            if not written:
                #: PHP fundpositioncn.php:37 —— 没写出三列的行照样写两列。
                #: 非拐点日（上面的 if 不进）也走这里，于是 base 的横轴是
                #: 净值记录本身而不是拐点子集。
                chart_base.append((date, net_value))
        chart = {"base": [], "focus": [[date, value] for date, value in chart_focus]}
        for date, value in chart_base:
            number = _chart_number(value)
            if number is not None:
                chart["base"].append([date, number])
        return columns, rows, total, chart

    # -- 净值记录段落的引用选型（``EchoNetValueHistoryParagraph``） ----------
    def _est_has_netvalue(self, netvalue: NetValueHistorySql, est_symbol: Any) -> bool:
        """est 标的有没有净值记录（PHP ``$est_ref->CountNetValue() > 0``）。

        决定「标的净值」列的**精度档**（4 位还是 est 符号自身精度）与
        **取数路径**（净值还是行情价回退）。查不到 stock_id 时按"没有"处理。
        """
        if not est_symbol:
            return False
        est_id = self.source.get_stock_id(est_symbol)
        return bool(est_id) and netvalue.count(est_id) > 0
    def _net_value_refs(self, symbol: str, ref: Any = None):
        """``EchoNetValueHistoryParagraph()`` 的三支选路（``:157-172``）。

        PHP 按"这个符号属于哪一类"决定摆几列，**与估值走哪条路是两套判据**：

        1. QDII 家族（``StockGetQdiiReference``：``in_arrayQdii/Hk/Jp/Eu``）
           → 汇率列 + 中间层净值列 + 仓位列。``cny`` 与 ``est`` **各自独立**
           取：没有 est 标的的 QDII（``README_p0_data_layer.md`` §1.3 那 8 只）
           照样有汇率列，只是没有中间层净值列；
        2. ``in_arrayLofMix``（14 只持仓型 LOF）→ 汇率列 + 仓位列，
           **没有**中间层净值列（``$est_ref = false``），仓位改走
           :func:`qdii_mix_position`；
        3. 其它 → 只有日期/净值/涨幅三列。

        返回 ``(cny_symbol, est_symbol, holdings_ref)``。``holdings_ref`` 只在
        第 2 支用得到。

        **为什么不真去建 ``QdiiReference``**：这里只需要两个符号（列头文字 +
        ``netvaluehistory`` 查值），而建引用会连库算 EST，失败时还会把列整个
        吞掉。PHP 那边 ``GetCnyRef()`` 返回的就是 ``SetForex($strCny)`` 建的
        ``CnyReference``，``$strCny`` 是四个子类硬编码的
        ``USCNY``/``HKCNY``/``JPCNY``/``EUCNY``（``qdiiref.php:239/256/271/286``），
        查表即可；``GetEstRef()`` 来自 ``QdiiGetEstSymbol()``。
        """
        region = self._qdii_region(symbol)
        if region is not None:
            cny_symbol = QDII_REGIONS[region][0]
            est_symbol = QdiiReference._est_symbol_of(symbol, region)
            #: ``$est_ref`` 可能为 false —— ``QdiiGetEstSymbol()`` 查不到中间层
            #: 符号时（持仓型 QDII 正是如此，``qdiiref.php:241`` 那个 if 不进）
            #: PHP 的 ``est_ref`` 保持默认的 false。这时
            #: ``EchoNetValueItem()`` 走 ``else if ($cny_ref)`` 支，用
            #: **传进来的 ``$ref``** 调 ``_QdiiMixGetPosition()``。
            #: CK4 传的 ``$ref`` 就是 HoldingsReference，所以 holdings_ref
            #: 在这一支也要往下传（第一版漏了，仓位格会整个空掉）。
            holdings = ref if _is_holdings_ref(ref) else None
            return cny_symbol, est_symbol, holdings

        if in_array_lof_mix(symbol):
            #: PHP: $ref = new HoldingsReference($strSymbol) —— **重新构造一个**，
            #: 不用传进来的那个。CK4 传进来的本来就是 HoldingsReference，
            #: 重复构造只是多一次同样的查询，所以这里复用（见下方 _is_holdings_ref）。
            holdings = ref if _is_holdings_ref(ref) else self._build_holdings_ref(symbol)
            if holdings is None:
                return None, None, None
            return _symbol_of(holdings.get_cny_ref()), None, holdings

        return None, None, None

    @staticmethod
    def _qdii_region(symbol: str):
        """``StockGetQdiiReference()`` 的分派顺序（``php/stock.php:306``）。

        顺序照抄 PHP：先 ``in_arrayQdii``，再 Hk/Jp/Eu。**不能**换成
        :func:`build_qdii_reference` 里那个"先试 hk/jp/eu 再归 us"的顺序 ——
        两边对"同时在两个数组里"的符号会给出不同地区。
        """
        if in_array_qdii(symbol):
            return "us"
        if in_array_qdii_hk(symbol):
            return "hk"
        if in_array_qdii_jp(symbol):
            return "jp"
        if in_array_qdii_eu(symbol):
            return "eu"
        return None

    def _build_holdings_ref(self, symbol: str) -> Any:
        """建持仓引用；建不出来返回 ``None``（那一节不显示，而不是整页 500）。"""
        try:
            return StockGetHoldingsReference(symbol, source=self.source)
        except Exception:
            return None

    def _row_netvaluehistory(
        self, symbol: str, netvalue: NetValueHistorySql, record: Any, prev_date: Any,
        cny_symbol: Any, est_symbol: Any, position: Any, holdings_ref: Any = None,
        est_has_netvalue: bool = False, f_input: Any = POS_NETVALUE_DIFF,
    ) -> TableRow:
        """``EchoNetValueItem()``（``netvaluehistoryparagraph.php:62``）的一行。

        ``holdings_ref`` 只在"持仓型 LOF"那条路上用得到（``_QdiiMixGetPosition``
        要读持仓明细反算仓位），其余两路是 ``None``。

        ``f_input`` 是"净值涨跌%阈值"（PHP ``$fInput``）：仓位反算式只在
        波动大于它时才出数。**只有 fundposition 页面会把这个值开放给用户**
        （``fundpositioncn.php:70`` 的输入框），其余调用点写死
        ``POS_NETVALUE_DIFF``，所以设成默认参数而不是必填。
        """
        date = str(record["date"])
        net_value = record["close"]

        #: 三个涨幅格的阈值精度各按**自己的 ref** 取 GetPrecision()：
        #: 基金 3 / 汇率 4（外汇）/ est 符号 2（^NDX、QQQ 这类）。
        #: PHP 侧分别是 $ref / $cny_ref / $est_ref 的 GetPercentageDisplay()
        #: （netvaluehistoryparagraph.php:72/78/90），谁调用就用谁的精度。
        fund_precision = StockSymbol(symbol).get_precision()

        row = TableRow()
        row.add(date)
        # PHP: $ref->GetNetValueDisplay() —— 有净值历史时精度取
        # NETVALUE_PRECISION(4)，否则退回引用自己的精度。
        # 生产端 SH513050 那一格是 "1.0115"，四位；用默认的 3 位会成 "1.012"。
        row.add(format_price_display(net_value, precision=4))
        prev_net = netvalue.get_close(record["stock_id"], prev_date) if prev_date else None
        row.add(format_percentage_display(prev_net, net_value, fund_precision))

        if not cny_symbol:
            return row

        cny_id = self.source.get_stock_id(cny_symbol)
        cny = netvalue.get_close(cny_id, date) if cny_id else None
        cny_prev = netvalue.get_close(cny_id, prev_date) if (cny_id and prev_date) else None
        row.add(format_price_display(cny) if cny is not None else "")
        #: PHP: $cny_ref->GetPercentageDisplay(...) —— 汇率符号是外汇，精度 4
        row.add(format_percentage_display(cny_prev, cny, StockSymbol(str(cny_symbol)).get_precision())
                if cny is not None else "")

        #: PHP 的 ``$strOrgPos``：库里记的仓位，用来判断"反算值和登记值是否吻合"
        #: （吻合就把整行标黄，见 ``EchoMatchTableColumn``）。
        org_pos = php_number_format(position, 2)
        if est_symbol:
            est_id = self.source.get_stock_id(est_symbol)
            est = netvalue.get_close(est_id, date) if est_id else None
            est_prev = netvalue.get_close(est_id, prev_date) if (est_id and prev_date) else None
            if not est_has_netvalue:
                #: PHP ``GetNetValue()`` 第三支（``mysqlref.php:72``）：est **没有**
                #: 净值记录时取行情价（``GetVal() -> GetClose()``）。演示库里
                #: ^GSPC / ^NDX 这类**指数** est 正是如此 —— 不加这条回退，
                #: 「标的净值」列对它们永远是空的，而生产端有值（指数收盘价，
                #: 精度 2 位）。有净值记录的 est（QQQ/SPY/XOP/KWEB）不走这里。
                if est is None and est_id:
                    est = self.source.get_history_close(est_id, date)
                if est_prev is None and est_id and prev_date:
                    est_prev = self.source.get_history_close(est_id, prev_date)
            #: PHP: $est_ref->GetNetValueDisplay() —— 两档精度：est 有净值记录
            #: 取 NETVALUE_PRECISION(4)，否则用 est 符号自身精度（指数/美股 2 位）。
            est_precision = (NETVALUE_PRECISION if est_has_netvalue
                             else StockSymbol(str(est_symbol)).get_precision())
            row.add(format_price_display(est, precision=est_precision) if est is not None else "")
            if est is not None:
                #: PHP: $est_ref->GetPercentageDisplay(...) —— 阈值精度恒用 est
                #: **符号自身**的 GetPrecision()（^NDX/QQQ=2），与上面价格列的
                #: 两档精度（有净值 4 位 / 无净值 2 位）是两码事，别复用 est_precision。
                row.add(format_percentage_display(est_prev, est,
                                                  StockSymbol(str(est_symbol)).get_precision()))
                # 对应 PHP _QdiiGetStockPosition()：先用汇率变动把"净值涨幅"里的
                # 汇率成分剥掉，剩下的比例才是真实仓位。
                estimated = self._estimate_position(est, est_prev, prev_net, net_value,
                                                    cny_prev, cny, f_input)
                row.add(estimated if estimated is not None else "")
                if estimated is not None:
                    row.position_inputs = self._position_inputs(
                        date, prev_date, net_value, prev_net, cny, cny_prev,
                        f_input, estimated, est=est, est_prev=est_prev,
                        est_symbol=est_symbol, est_precision=est_precision,
                        cny_symbol=cny_symbol)
                    if estimated == org_pos:
                        row.match = True
            else:
                row.add("")
                row.add("")
        else:
            # 混合型：用持仓比例加权，见 PHP _QdiiMixGetPosition()
            estimated = self._mixed_position(symbol, holdings_ref, date, prev_date,
                                             prev_net, net_value, cny_prev, cny, f_input)
            row.add(estimated if estimated is not None else "")
            if estimated is not None:
                #: 混合型没有单一 est 标的（``est_precision`` 那一路走不到），
                #: 页面说明段据此把"标的涨跌%"写成加权合计。
                row.position_inputs = self._position_inputs(
                    date, prev_date, net_value, prev_net, cny, cny_prev,
                    f_input, estimated, mixed=True, cny_symbol=cny_symbol)
                if estimated == org_pos:
                    row.match = True
        return row

    def _position_inputs(
        self,
        date: Any,
        prev_date: Any,
        net_value: Any,
        prev_net: Any,
        cny: Any,
        cny_prev: Any,
        f_input: Any,
        position: Any,
        est: Any = None,
        est_prev: Any = None,
        est_symbol: Any = None,
        est_precision: Any = None,
        cny_symbol: Any = None,
        mixed: bool = False,
    ) -> dict:
        """把这一行算仓位用的量留一份，供页面「算法说明」逐格代入。

        ``est`` 为空（混合型）时 ``est_percent`` 也算不出来 —— 那时页面
        把这一格写成"成分股加权合计"，不再摆数字。
        """
        net_percent = percentage_change(prev_net, net_value)
        est_percent = (None if est is None
                       else self._est_percent(est, est_prev, cny_prev, cny))
        return {
            "date": str(date),
            "prev_date": str(prev_date) if prev_date else "",
            #: ``format_price_display`` 返回 ``(文本, 颜色)`` —— 说明段只取文本，
            #: 颜色留给表格（那里才有涨跌染色的语义）。
            "net": _plain(format_price_display(net_value, precision=NETVALUE_PRECISION)),
            "prev_net": _plain(format_price_display(prev_net, precision=NETVALUE_PRECISION)),
            "net_percent": _percent_text(net_percent),
            "est": ("" if est is None
                    else _plain(format_price_display(est, precision=est_precision))),
            "est_prev": ("" if est_prev is None
                         else _plain(format_price_display(est_prev, precision=est_precision))),
            "est_percent": _percent_text(est_percent),
            "cny": "" if cny is None else _plain(format_price_display(cny)),
            "cny_prev": "" if cny_prev is None else _plain(format_price_display(cny_prev)),
            "position": str(position),
            "threshold": _percent_text(f_input),
            "est_symbol": "" if est_symbol is None else str(est_symbol),
            "cny_symbol": "" if cny_symbol is None else str(cny_symbol),
            "mixed": mixed,
        }

    @staticmethod
    def _est_percent(est: Any, est_prev: Any, cny_prev: Any, cny: Any) -> Any:
        """标的（含汇率）涨跌%，即 PHP ``StockGetPercentage($fCnyPrev / $fEstProportion, $fCny)``。

        展开后就是 ``(est ÷ est_prev × cny ÷ cny_prev − 1) × 100``
        —— PHP 把它写成"汇率 ÷ (上期汇率 ÷ 标的比例)"，绕一圈是因为
        ``proportion`` 在混合型那条路上是加权合计，不是两个价格的比。
        """
        try:
            proportion = float(est) / float(est_prev)
            divisor = float(cny_prev) / proportion
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        return percentage_change(divisor, cny)

    def _estimate_position(self, est: Any, est_prev: Any, prev_net: Any, net_value: Any,
                           cny_prev: Any, cny: Any, f_input: Any = POS_NETVALUE_DIFF) -> Any:
        """对应 PHP ``_QdiiGetStockPosition()``（netvaluehistoryparagraph.php:5）。

        PHP 原文注释给出了推导：

            (est * cny / estPrev * cnyPrev - 1) * position = (nv / nvPrev - 1)

        即净值涨幅 = 仓位 × 标的涨幅（标的涨幅里含汇率变动）。
        所以仓位 = 净值涨跌% / 标的（已换汇）涨跌%。

        只在净值涨跌超过 ``fInput``（默认 4%）时才算 —— 波动小时反算噪声太大。
        fundposition 页面把这个值开放给用户改，其余调用点走默认值。
        """
        if est_prev is None:
            return None
        try:
            proportion = float(est) / float(est_prev)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        return qdii_get_stock_position(proportion, prev_net, net_value, cny_prev, cny, f_input)

    def _position_formula_note(self, rows: Any, f_input: Any = POS_NETVALUE_DIFF) -> Any:
        """仓位估算页的「算法说明」：公式 + 逐步算法 + 闸门 + 一行代入示例。

        示例取**第一行算出仓位的行**；一行都没有时 ``sample`` 为 ``None``，
        但公式与算法照样给 —— 说明本身不依赖有没有算出行（默认 4% 阈值下
        SZ161125 就是一整页都过不了闸门，把公式也一起藏掉反而让人以为坏了）。
        """
        sample = None
        for row in rows:
            if getattr(row, "position_inputs", None):
                sample = row.position_inputs
                break
        return {
            "percentage_def": POSITION_PERCENTAGE_DEF,
            "net_percent": POSITION_NET_PERCENT,
            "est_percent": POSITION_EST_PERCENT,
            "formula": POSITION_FORMULA,
            "derivation": POSITION_DERIVATION,
            "steps": list(POSITION_STEPS),
            "gates": list(POSITION_GATES),
            "sample": sample,
        }

    def _mixed_position(self, symbol: str, holdings_ref: Any, date: Any, prev_date: Any,
                        prev_net: Any, net_value: Any, cny_prev: Any, cny: Any,
                        f_input: Any = POS_NETVALUE_DIFF) -> Any:
        """对应 PHP ``_QdiiMixGetPosition()``：按持仓比例加权后反算仓位。

        需要 ``holdings`` 表（持仓明细）。**CK4 之前演示库里 holdings 是空的**，
        所以这里一度直接返回 ``None``；``4d681a0f`` 迁完 ``HoldingsReference``
        并从生产端搬了 ``SH513050`` 的真实样本后才真算。
        """
        if holdings_ref is None:
            return None
        return qdii_mix_position(holdings_ref, date, prev_date, prev_net, net_value,
                                 cny_prev, cny, f_input)

    # -- 净值价格比较 -----------------------------------------------------
    def _build_netvalueclose(self, symbol: str, stock_id: Any, start: int, num: int):
        history = StockHistorySql(self.connection)
        netvalue = NetValueHistorySql(self.connection)
        shares = SharesHistorySql(self.connection)
        if netvalue.count(stock_id) == 0:
            return _columns_netvalueclose(), [], 0

        same_day = use_same_day_net_value(
            symbol,
            is_qdii=in_array_qdii(symbol),
            is_mix=in_array_qdii_mix(symbol),
            is_hk_mix=in_array_hk_mix(symbol),
        )

        rows = []
        for record in history.get_all(stock_id, start, num):
            date = str(record["date"])
            # PHP: $bSameDay ? $arHistory['date'] : GetDatePrev(...)
            net_date = date if same_day else history.get_date_prev(stock_id, date)
            if not net_date:
                continue
            net_value = netvalue.get_close(stock_id, net_date)
            if net_value is None:
                continue
            rows.append(self._row_netvalueclose(history, shares, record, net_value, symbol))
        return _columns_netvalueclose(), rows, history.count(stock_id)

    def _row_netvalueclose(self, history: StockHistorySql, shares: SharesHistorySql,
                           record: Any, net_value: Any, symbol: str) -> TableRow:
        date = str(record["date"])
        stock_id = record["stock_id"]
        close = record["close"]
        close_prev = history.get_close_prev(stock_id, date)

        row = TableRow()
        row.add(date)
        # PHP: GetPriceDisplay($fClose, $fNetValue) —— 以净值为基准上色
        row.add(format_price_display(close, net_value))
        #: PHP: $ref->GetNetValueDisplay()（``mysqlref.php:75``）—— 整表有
        #: ``CountNetValue() == 0`` 的守卫（``netvaluecloseparagraph.php:45``），
        #: 行能出来就必有净值记录，精度恒取 NETVALUE_PRECISION(4)。
        #: 3 位会把 3.1548 显示成 3.155（与「基金溢价记录」同一个坑）。
        row.add(format_price_display(net_value, precision=NETVALUE_PRECISION))
        #: 溢价/涨幅两格都走 $ref（基金自己）的精度 ——
        #: PHP ``$ref->GetPercentageDisplay()``（netvaluecloseparagraph.php:16/17）
        fund_precision = StockSymbol(symbol).get_precision()
        # PHP: GetPercentageDisplay($fNetValue, $fClose) —— 溢价（价格相对净值）
        row.add(format_percentage_display(net_value, close, fund_precision))
        # PHP: GetPercentageDisplay($fClosePrev, $fClose) —— 涨幅
        row.add(format_percentage_display(close_prev, close, fund_precision))

        share = shares.get_close(stock_id, date) if close_prev is not None else None
        if share is not None:
            row.add(format_number_display(share, 2))
            row.add(format_turnover_display(history.get_volume(stock_id, date), share))
        return row

    # -- 校准记录 ---------------------------------------------------------
    def _build_calibrationhistory(self, symbol: str, stock_id: Any, start: int, num: int):
        table = CalibrationSql(self.connection)
        # PHP ``$ref->IsFundA()``（``php/stock/stocksymbol.php:733``）：
        # 只有 **A 股 LOF/ETF** 才补「净值 / 对冲值」两列。
        #
        # 曾经用 ``get_fund_profile()`` 当谓词、再兜底 ``in_array_qdii`` ——
        # 两个都不等于 IsFundA：演示库里 ^GSPC / ^NDX / QQQ / SPY 这些
        # **中间层标的同样有 profile 行**（position 默认 1.0），于是期货均线表
        # 前那张校准表凭空多出两列，而生产端 ^GSPC 那张只有
        # 「日期 校准值 时间 次数」4 列。实测 37 个有校准记录的符号里只有这
        # 4 个非基金会被修掉，33 只真基金列头不变。
        is_fund = bool(StockSymbol(symbol).is_fund_a())
        multiplier = calibration_hedge_multiplier(symbol)

        netvalue = NetValueHistorySql(self.connection)
        position = self.source.get_position(stock_id) if is_fund else None
        #: PHP ``GetNetValueDisplay()`` 的两档精度 + ``GetNetValue()`` 的取值
        #: 三支（``mysqlref.php:60-78``）：有净值记录 -> 净值（4 位）；
        #: 没有净值记录 -> ``GetVal()`` 取行情价（符号自身精度，A 股基金 3 位）。
        #: A 股基金都有净值回填，正常走 4 位那一档。
        fund_has_netvalue = netvalue.count(stock_id) > 0
        net_precision = (NETVALUE_PRECISION if fund_has_netvalue
                         else StockSymbol(symbol).get_precision())

        rows = []
        for record in table.get_all(stock_id, start, num):
            row = TableRow()
            date = str(record["date"])
            calibration = record["close"]
            row.add(date)
            row.add(format_number_display(calibration, 3))
            row.add(format_hm(record["time"]))
            row.add(record["num"])
            if is_fund:
                net = netvalue.get_close(stock_id, date)
                if net is None and not fund_has_netvalue:
                    #: PHP GetNetValue() 第三支：没有净值记录 -> 退回行情价
                    net = self.source.get_history_close(stock_id, date)
                row.add(format_price_display(net, precision=net_precision)
                        if net is not None else "")
                hedge = stock_calc_hedge(calibration, position)
                if hedge is None:
                    row.add("")
                else:
                    row.add(format_number_display(hedge * multiplier, 0))
            rows.append(row)
        return _columns_calibrationhistory(is_fund), rows, table.count(stock_id)

    # -- 基金场内份额 -----------------------------------------------------
    def _build_shareshistory(self, symbol: str, stock_id: Any, start: int, num: int):
        shares = SharesHistorySql(self.connection)
        history = StockHistorySql(self.connection)
        rows = []
        for record in shares.get_all(stock_id, start, num):
            rows.append(self._row_shareshistory(shares, history, record))
        return _columns_shareshistory(), rows, shares.count(stock_id)

    def _row_shareshistory(self, shares: SharesHistorySql, history: StockHistorySql, record: Any) -> TableRow:
        date = str(record["date"])
        stock_id = record["stock_id"]
        share = record["close"]

        row = TableRow()
        row.add(date)
        row.add(format_number_display(share, 2) if share is not None else "")

        share_prev = shares.get_close_prev(stock_id, date)
        if share_prev is not None and share is not None:
            diff = float(share) - float(share_prev)
            volume = history.get_volume(stock_id, date)
            row.add(format_number_display(diff))
            row.add(volume)
            if share is not None:
                row.add(format_turnover_display(volume, share))
                # PHP 只在"有成交量且份额是净增"时才给新增换手：
                # 份额减少时这个比值没有意义（负数换手率会误导）
                if float(volume) > MIN_FLOAT_VAL and diff > MIN_FLOAT_VAL:
                    row.add(format_turnover_display(volume, diff, 0))
        return row

    # -- 参考符号解析 -----------------------------------------------------
    def _compare_symbol(self, symbol: str) -> Any:
        """历史价格页的对照标的。

        PHP 由调用方显式传 ``$compare_ref``；这里按该符号的估值标的推断，
        取不到就不加对照列（页面少几列，不影响其它列）。
        """
        return self._est_symbol(symbol)

    def _est_symbol(self, symbol: str) -> Any:
        """估值用的中间层标的（如 ``^NDX`` / ``XOP``）。"""
        reader = getattr(self.source, "get_fund_profile", None)
        if reader is None:
            return None
        try:
            profile = reader(symbol)
        except Exception:
            return None
        if profile is None:
            return None
        return getattr(profile, "est_symbol", None) or getattr(profile, "pair_symbol", None)

    def _cny_symbol(self, symbol: str) -> Any:
        """该基金估值用的汇率符号。

        QDII（美股/港股/日本/欧洲）用美元中间价 ``USCNY`` ——
        因为估值链路统一先把标的换算成人民币。没有估值标的的符号不显示汇率列。
        """
        if self._est_symbol(symbol) is None:
            return None
        return "USCNY"

    def _forex_symbol(self) -> Any:
        """历史价格页比价用的汇率符号。

        PHP 的比价公式里那个 ``$forex_ref`` 用的就是美元人民币中间价，
        目的是把"每克多少美元"换算成"每克多少人民币"。
        """
        return "USCNY"


def _symbol_of(ref: Any) -> Any:
    """从引用对象取符号；``None`` 或取不到都回 ``None``。"""
    if ref is None:
        return None
    getter = getattr(ref, "get_symbol", None)
    if getter is None:
        getter = getattr(ref, "GetSymbol", None)
    if getter is None:
        return None
    try:
        return getter()
    except Exception:
        return None


def _is_holdings_ref(ref: Any) -> bool:
    """ Duck-typing 判断是不是持仓型引用（有 ``get_proportion_array``）。

    ``EchoNetValueHistoryParagraph()`` 的 LofMix 支会 ``new
    HoldingsReference($strSymbol)`` 覆盖传进来的 ``$ref``；CK4 传进来的本来
    就是持仓引用，重复构造只是多一次同样的查询，所以这里认了就复用。
    """
    return getattr(ref, "get_proportion_array", None) is not None


def _php_val_is_false(value: Any) -> bool:
    """PHP 的 ``== false`` 松散比较（``fundhistoryparagraph.php:17``）。

    库里取出来的 ``close`` 是字符串，PHP 只有 ``""`` 与 ``"0"`` 是假值 ——
    ``"0.0"``、``"0.000"`` 都是真值。数字 0 同样算假。
    """
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return value.strip() in ("", "0")
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _columns_netvalueclose() -> list:
    """净值价格比较的列，对应 PHP ``netvaluecloseparagraph.php:53``。"""
    return [_COL_DATE, _COL_PRICE, _COL_NETVALUE, _COL_PREMIUM, _COL_CHANGE, _COL_SHARE, _COL_TURNOVER]


def default_history_service_factory() -> HistoryService:
    """创建使用当前请求数据库连接的历史数据服务。"""
    return HistoryService(MySqlFundDataSource(get_db()))


def parse_paging(args: Any) -> tuple:
    """从查询参数解析 ``start`` / ``num``，与 PHP 的参数名保持一致。

    ``num=0`` 表示全部记录（PHP ``_SqlBuildLimit`` 的语义）。
    负数或非数字按缺省处理，避免被一个坏链接打出全表。
    """
    def _int(name: str, default: int) -> int:
        raw = args.get(name)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value >= 0 else default

    return _int("start", 0), _int("num", TABLE_COMMON_DISPLAY)
