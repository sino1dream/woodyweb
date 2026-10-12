"""表格列定义 —— ``php/ui`` 渲染层的第一层地基。

对应 PHP 源文件：

* ``php/ui/stockdisp.php``  (34 行)  → :data:`STOCK_DISP_*` 显示常量
* ``php/ui/table.php``      (174 行) → :class:`TableColumn`、宽度辅助、
  :data:`TABLE_COMMON_DISPLAY` 与 :func:`is_table_common_display`
* ``php/ui/stocktable.php`` (328 行) → 30 个列预设工厂 + ``TableColumnGet*()`` +
  :func:`ref_get_table_column_net_value`
* ``php/ui/stockcomm.php``  (75 行)  → :func:`ref_has_data` /
  :func:`ref_sort_by_numeric` / :func:`ref_sort_by_symbol`

**刻意不迁** ``php/ui/htmlelement.php``（156 行）：那一整份文件是
``GetHtmlElement()`` / ``GetFontElement()`` 这类"直接产出 HTML 片段"的函数。
新架构里 HTML 由模板负责、颜色以数据形式随单元格返回，所以逐字翻译它等于把
已经拆开的渲染/数据再粘回去。详见 :mod:`README_ui_column_migration.md` 取舍 1。

## 这一层在消灭什么重复

迁移前每个 service 各写一份列定义，比如 ``web/services/history_service.py`` 里
是 ``_column("日期", "", 100)``、``web/services/fund_list_service.py`` 里又写一遍
相近的字符串。PHP 侧的单一来源是 ``stocktable.php`` 那 30 个类。**本模块把它
搬过来，让列定义只有一个出处。**

## 颜色为什么不再是 HTML

PHP 的 ``TableColumn::__construct()`` 第 31 行：

.. code-block:: php

    $this->strText = $strColor ? GetFontElement($strText, $strColor) : $strText;

即**颜色在构造期就被烧进 HTML 片段**里。本版改成 ``color`` 字段单独承载，
``text`` 保持纯文本：

* 模板用 Jinja2 自动转义，文本里夹裸标签会被转义成 ``&lt;font&gt;``；
* ``"NEW"`` 这类业务文本不会因为忘记转义而破坏布局；
* 同一个列定义既能给 HTML 模板用，也能给 JSON API / CSV 导出用
  （PHP 那边想导出就得先正则剥标签）。

颜色名沿用 PHP 的原名（``blue``/``olive``/``orange``/…），由 CSS 映射成可读色，
见 ``web/static/style.css`` 的 ``.col-*`` / ``.cell-*``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

# ---------------------------------------------------------------------------
# 显示常量（php/ui/stockdisp.php）
#
# 这一份文件在 PHP 里只有 define，没有逻辑。注释里标了作者原本打算用什么颜色
# （但 color 并没有出现在常量里，是在 stocktable.php 的列预置里才指定的）。
# ---------------------------------------------------------------------------
STOCK_DISP_ASHARES = "A股"
STOCK_DISP_BSHARES = "B股"
STOCK_DISP_CALIBRATION = "校准"
STOCK_DISP_CHANGE = "涨幅"
STOCK_DISP_EST = "EST"              # magenta 估值
STOCK_DISP_FAIR = "参考"
STOCK_DISP_HEDGE = "对冲值"          # navy
STOCK_DISP_HIGH = "最高"
STOCK_DISP_HOLDING = "持仓"
STOCK_DISP_HSHARES = "H股"
STOCK_DISP_LOW = "最低"
STOCK_DISP_NETVALUE = "净值"         # olive
STOCK_DISP_OFFICIAL = "官方"
STOCK_DISP_OPEN = "开盘"
STOCK_DISP_ORDER = "申购"
STOCK_DISP_POSITION = "仓位"
STOCK_DISP_PREMIUM = "溢价"          # orange
STOCK_DISP_PRICE = "价格"            # blue
STOCK_DISP_PROFIT = "盈亏"           # red
STOCK_DISP_QUANTITY = "数量"         # indigo
STOCK_DISP_RATIO = "比价"
STOCK_DISP_REALTIME = "实时"
STOCK_DISP_REMARK = "备注"
STOCK_DISP_SYMBOL = "代码"           # maroon
STOCK_DISP_TURNOVER = "换手"         # green

STOCK_DISP_DEBUG = "调试信息"

#: ``php/ui/stockdisp.php:32``：净值记录页只有在"当天净值涨跌超过 4%"时才反算
#: 仓位 —— 作者用它规避官方 EST 的其他误差来源（净值波动小时反算出的仓位噪声太大）。
POS_NETVALUE_DIFF = 4.0
#: 持仓型基金的对应阈值。
HOLDINGS_NETVALUE_DIFF = 1.0


# ---------------------------------------------------------------------------
# TableColumn（php/ui/table.php:23）
# ---------------------------------------------------------------------------
#: ``php/ui/table.php:4``：不显示翻页控件的行数上限。
TABLE_COMMON_DISPLAY = 10


@dataclass(frozen=True)
class TableColumn:
    """一列表头定义，对应 PHP ``TableColumn``（``table.php:23``）。

    PHP 的构造签名是 ``($strText = '', $iWidth = 80, $strColor = false,
    $strPrefix = false)``，语义是：``文本 = 前缀 + 文本``，颜色烧进 HTML。
    这里保留同样的四个字段，区别只有两个：

    * ``color`` 是**数据**而不是 HTML 片段（见模块 docstring）；
    * 用 ``frozen=True`` 让它可哈希、可当 dict 键，也避免"构造完再被改"的
      隐式共享状态 —— PHP 那 30 个子类本来就是常量预置，不该可变。

    ==================  ==================================================
    PHP 方法             本版
    ==================  ==================================================
    ``GetText()``       :attr:`text_with_prefix`
    ``GetWidth()``      :attr:`width`
    ``GetDisplay()``    :attr:`display`（散文里用，PHP 是 ``<b>`` 包裹）
    ``GetHead()``       :meth:`to_dict`（模板据此渲染 ``<th>``）
    ==================  ==================================================
    """

    text: str = ""
    width: int = 80
    color: str = ""
    prefix: str = ""

    @property
    def text_with_prefix(self) -> str:
        """对应 ``GetText()``：``前缀 + 文本``。"""
        return f"{self.prefix}{self.text}" if self.prefix else self.text

    @property
    def display(self) -> str:
        """对应 ``GetDisplay()``：散文里提到这一列时的叫法。

        PHP 返回 ``<b>文本</b>``。**加粗与着色是表现层的决定**，这里只给文本，
        由调用方（模板或文案）决定是否强调。
        """
        return self.text_with_prefix

    def to_dict(self) -> dict[str, Any]:
        """转成模板要的形状 ``{"text", "color", "width"}``。

        与 ``web/services/history_service._column()`` 的返回**完全一致**，
        所以两种写法可以在同一个 ``columns`` 列表里混用，迁移可以逐个替换。
        """
        return {"text": self.text_with_prefix, "color": self.color, "width": self.width}


def columns_to_dicts(columns: Iterable[TableColumn]) -> list[dict[str, Any]]:
    """一批 :class:`TableColumn` → 模板用的 dict 列表。"""
    return [column.to_dict() for column in columns]


def table_column_get_total_width(columns: Sequence[TableColumn]) -> int:
    """对应 ``TableColumnGetTotalWidth()``（``table.php:80``）。"""
    return sum(column.width for column in columns)


def table_column_get_last_width(columns: Sequence[TableColumn], layout_width: int) -> int:
    """对应 ``TableColumnGetLastWidth()``（``table.php:90``）。

    PHP 是 ``LayoutGetDisplayWidth() - TableColumnGetTotalWidth($ar)`` ——
    **最后一列吃掉剩余宽度**。``LayoutGetDisplayWidth()`` 属于布局层
    （``php/layout.php:107``，优先级列表第 5 步、未迁移），所以这里改成
    **由调用方传入**，本模块不去猜屏幕宽度。

    这也是本层唯一一个"PHP 有全局隐含依赖、这里显式化"的地方。
    """
    return layout_width - table_column_get_total_width(columns)


def is_table_common_display(start: int, num: int) -> bool:
    """对应 ``IsTableCommonDisplay()``（``table.php:6``）。

    ``start == 0`` 且 ``num <= 10`` 时不需要翻页控件。
    """
    return start == 0 and num <= TABLE_COMMON_DISPLAY


# ---------------------------------------------------------------------------
# table.php 自带的三个通用列
# ---------------------------------------------------------------------------
def column_base(text: str = "", width: int = 80, color: str = "", prefix: str = "") -> TableColumn:
    """对应 ``new TableColumn(...)`` 本身：PHP 里它也常被直接 new（如调试列）。"""
    return TableColumn(text=text, width=width, color=color, prefix=prefix)


def column_date(prefix: str = "", chinese: bool = True) -> TableColumn:
    """``TableColumnDate``（``table.php:56``）。"""
    return TableColumn(text="日期" if chinese else "Date", width=100, prefix=prefix)


def column_ip() -> TableColumn:
    """``TableColumnIP``（``table.php:64``）。"""
    return TableColumn(text="IP", width=140)


def column_time(chinese: bool = True) -> TableColumn:
    """``TableColumnTime``（``table.php:72``）。"""
    return TableColumn(text="时间" if chinese else "Time", width=50)


# ---------------------------------------------------------------------------
# stocktable.php 的 30 个列预置
#
# PHP 用 30 个子类表达"只有默认值不同"，本版用工厂函数：表达能力相同，
# 但能内联成 :data:`COLUMN_PRESETS` 查找表，也便于按 PHP 类名反查。
# 每个工厂的默认值逐条照抄 stocktable.php，包括那些看着奇怪的
# （例如 GroupName 是"分组名称"而不是"分组"—— 见下方注释）。
# ---------------------------------------------------------------------------
def column_amount(prefix: str = "") -> TableColumn:
    """``TableColumnAmount``（``stocktable.php:6``）：金额，120。"""
    return TableColumn(text="金额", width=120, prefix=prefix)


def column_calibration() -> TableColumn:
    """``TableColumnCalibration``（``:14``）：``校准值``，140。"""
    return TableColumn(text=f"{STOCK_DISP_CALIBRATION}值", width=140)


def column_change(prefix: str = "") -> TableColumn:
    """``TableColumnChange``（``:22``）：涨幅，70。"""
    return TableColumn(text=STOCK_DISP_CHANGE, width=70, prefix=prefix)


def column_error() -> TableColumn:
    """``TableColumnError``（``:30``）：误差，70。"""
    return TableColumn(text="误差", width=70)


def column_est(prefix: str = "") -> TableColumn:
    """``TableColumnEst``（``:38``）：EST，80，magenta。"""
    return TableColumn(text=STOCK_DISP_EST, width=80, color="magenta", prefix=prefix)


def column_official_est() -> TableColumn:
    """``TableColumnOfficialEst``（``:52``）：``官方EST``。

    PHP 是 ``extends TableColumnEst`` 且只传 ``STOCK_DISP_OFFICIAL`` 作前缀，
    所以继承来的宽度 80 与 magenta 都在。
    """
    return column_est(STOCK_DISP_OFFICIAL)


def column_fair_est() -> TableColumn:
    """``TableColumnFairEst``（``:66``）：``参考EST``。"""
    return column_est(STOCK_DISP_FAIR)


def column_realtime_est() -> TableColumn:
    """``TableColumnRealtimeEst``（``:81``）：``实时EST``。"""
    return column_est(STOCK_DISP_REALTIME)


def column_hedge() -> TableColumn:
    """``TableColumnHedge``（``:95``）：对冲值，80，navy。"""
    return TableColumn(text=STOCK_DISP_HEDGE, width=80, color="navy")


def column_holding(prefix: str = "") -> TableColumn:
    """``TableColumnHolding``（``:103``）：持仓，105。"""
    return TableColumn(text=STOCK_DISP_HOLDING, width=105, prefix=prefix)


def column_name(prefix: str = "", width: int = 150) -> TableColumn:
    """``TableColumnName``（``:111``）：名称，默认宽 150。"""
    return TableColumn(text="名称", width=width, prefix=prefix)


def column_group_name() -> TableColumn:
    """``TableColumnGroupName``（``:119``）：``分组名称``，宽 110。

    **注意这个文本是 PHP 的 prefix 拼接结果**：它调的是
    ``parent::__construct('分组', 110)``，而 ``TableColumnName`` 的第一个参数是
    ``$strPrefix``，所以最终文本是 ``'分组'.'名称'``。看着别扭，但必须照抄，
    否则列头会与生产端对不上。
    """
    return column_name("分组", width=110)


def column_net_value(prefix: str = "") -> TableColumn:
    """``TableColumnNetValue``（``:127``）：净值，90，olive。"""
    return TableColumn(text=STOCK_DISP_NETVALUE, width=90, color="olive", prefix=prefix)


def column_percentage(prefix: str = "", width: int = 100) -> TableColumn:
    """``TableColumnPercentage``（``:141``）：``比例(%)``，默认宽 100。"""
    return TableColumn(text="比例(%)", width=width, prefix=prefix)


def column_position() -> TableColumn:
    """``TableColumnPosition``（``:149``）：仓位，60。"""
    return TableColumn(text=STOCK_DISP_POSITION, width=60)


def column_premium(prefix: str = "") -> TableColumn:
    """``TableColumnPremium``（``:163``）：溢价，70，orange。"""
    return TableColumn(text=STOCK_DISP_PREMIUM, width=70, color="orange", prefix=prefix)


def column_price(prefix: str = "", width: int = 70) -> TableColumn:
    """``TableColumnPrice``（``:177``）：价格，默认宽 70，blue。"""
    return TableColumn(text=STOCK_DISP_PRICE, width=width, color="blue", prefix=prefix)


def column_profit(prefix: str = "") -> TableColumn:
    """``TableColumnProfit``（``:191``）：盈亏，105，red。

    **red 是唯一一个 CSS 里原先缺的 ``.col-*``**：写作时 ``style.css`` 只有
    blue/olive/orange/indigo/green/maroon/magenta/navy 八个，已补上 ``.col-red``。
    """
    return TableColumn(text=STOCK_DISP_PROFIT, width=105, color="red", prefix=prefix)


def column_quantity(prefix: str = "", width: int = 100) -> TableColumn:
    """``TableColumnQuantity``（``:199``）：数量，默认宽 100，indigo。"""
    return TableColumn(text=STOCK_DISP_QUANTITY, width=width, color="indigo", prefix=prefix)


def column_ratio(prefix: str = "") -> TableColumn:
    """``TableColumnRatio``（``:213``）：比价，100。"""
    return TableColumn(text=STOCK_DISP_RATIO, width=100, prefix=prefix)


def column_remark() -> TableColumn:
    """``TableColumnRemark``（``:221``）：备注，150。"""
    return TableColumn(text=STOCK_DISP_REMARK, width=150)


def column_settle_price(prefix: str = "", width: int = 90) -> TableColumn:
    """``TableColumnSettlePrice``（``:235``）：结算价，默认宽 90，blue。

    期货没有"复权价"概念，所以行列总是显示这一列而不是价格/净值。
    """
    return TableColumn(text="结算价", width=width, color="blue", prefix=prefix)


def column_share() -> TableColumn:
    """``TableColumnShare``（``:244``）：``份额(万)``，110。"""
    return TableColumn(text="份额(万)", width=110)


def column_stock(display: Any, width: int = 80) -> TableColumn:
    """``TableColumnStock``（``:252``）：对照标的列（``^NDX`` 这种），maroon。

    PHP 的入参是 ``$sym`` 对象，构造里调 ``$sym->GetDisplay()``。本版改成
    **直接收显示文本**，因为这个层不该知道 ref 对象长什么样；调用方传
    ``ref.get_display()`` 进来即可（见 :func:`ref_get_table_column_net_value`）。
    """
    return TableColumn(text="" if display is None else str(display), width=width, color="maroon")


def column_symbol(prefix: str = "", width: int = 80) -> TableColumn:
    """``TableColumnSymbol``（``:266``）：代码，默认宽 80，maroon。"""
    return TableColumn(text=STOCK_DISP_SYMBOL, width=width, color="maroon", prefix=prefix)


def column_test() -> TableColumn:
    """``TableColumnTest``（``:280``）：测试，110。"""
    return TableColumn(text="测试", width=110)


def column_turnover(prefix: str = "", width: int = 100) -> TableColumn:
    """``TableColumnTurnover``（``:288``）：换手，默认宽 100，green。"""
    return TableColumn(text=STOCK_DISP_TURNOVER, width=width, color="green", prefix=prefix)


def column_hkd(prefix: str = "") -> TableColumn:
    """``TableColumnHKD``（``:296``）：``港币$``，100，blue。"""
    return TableColumn(text="港币$", width=100, color="blue", prefix=prefix)


def column_rmb(prefix: str = "") -> TableColumn:
    """``TableColumnRMB``（``:304``）：``人民币￥``，100，blue。"""
    return TableColumn(text="人民币￥", width=100, color="blue", prefix=prefix)


def column_usd(prefix: str = "") -> TableColumn:
    """``TableColumnUSD``（``:312``）：``美元$``，100，blue。"""
    return TableColumn(text="美元$", width=100, color="blue", prefix=prefix)


#: PHP 类名 → 工厂函数。用于按 PHP 源码类名反查，方便逐页核对。
COLUMN_PRESETS: dict[str, Callable[..., TableColumn]] = {
    "TableColumn": column_base,
    "TableColumnDate": column_date,
    "TableColumnIP": column_ip,
    "TableColumnTime": column_time,
    "TableColumnAmount": column_amount,
    "TableColumnCalibration": column_calibration,
    "TableColumnChange": column_change,
    "TableColumnError": column_error,
    "TableColumnEst": column_est,
    "TableColumnOfficialEst": column_official_est,
    "TableColumnFairEst": column_fair_est,
    "TableColumnRealtimeEst": column_realtime_est,
    "TableColumnHedge": column_hedge,
    "TableColumnHolding": column_holding,
    "TableColumnName": column_name,
    "TableColumnGroupName": column_group_name,
    "TableColumnNetValue": column_net_value,
    "TableColumnPercentage": column_percentage,
    "TableColumnPosition": column_position,
    "TableColumnPremium": column_premium,
    "TableColumnPrice": column_price,
    "TableColumnProfit": column_profit,
    "TableColumnQuantity": column_quantity,
    "TableColumnRatio": column_ratio,
    "TableColumnRemark": column_remark,
    "TableColumnSettlePrice": column_settle_price,
    "TableColumnShare": column_share,
    "TableColumnStock": column_stock,
    "TableColumnSymbol": column_symbol,
    "TableColumnTest": column_test,
    "TableColumnTurnover": column_turnover,
    "TableColumnHKD": column_hkd,
    "TableColumnRMB": column_rmb,
    "TableColumnUSD": column_usd,
}


# ---------------------------------------------------------------------------
# TableColumnGet*()：PHP 里这些 helpers 返回 **加粗** 过的列文本，用于在
# 说明文字里提到某一列（例如"按 Premium 排序"）。本版返回纯文本。
# ---------------------------------------------------------------------------
def table_column_get_est() -> str:
    return column_est().display


def table_column_get_official_est() -> str:
    return column_official_est().display


def table_column_get_fair_est() -> str:
    return column_fair_est().display


def table_column_get_realtime_est() -> str:
    return column_realtime_est().display


def table_column_get_net_value() -> str:
    return column_net_value().display


def table_column_get_position() -> str:
    return column_position().display


def table_column_get_premium() -> str:
    return column_premium().display


def table_column_get_price() -> str:
    return column_price().display


def table_column_get_quantity() -> str:
    return column_quantity().display


def table_column_get_remark() -> str:
    return column_remark().display


def table_column_get_stock(display: Any) -> str:
    return column_stock(display).display


def table_column_get_symbol() -> str:
    return column_symbol().display


# ---------------------------------------------------------------------------
# 按引用对象选列（stocktable.php:320）
# ---------------------------------------------------------------------------
def ref_get_table_column_net_value(ref: Any) -> TableColumn:
    """对应 ``RefGetTableColumnNetValue()``（``stocktable.php:320``）。

    这是整个 ``stocktable.php`` 里**唯一的多态工厂**：同一个"净值"概念，在
    不同标的上列名不同 ——

    * 有净值记录（基金）        → ``<标的>净值``；
    * 新浪期货（黄金除外）      → ``<标的>结算价``；
    * 其它（股票/指数/汇率）    → ``<标的>价格``。

    即"这一列到底叫什么"取决于 **ref 自己的状态**，这也是 PHP 把它放在
    这里而不是调用方的原因。本版照抄三条分支的顺序（顺序有意义）。
    """
    display = _call(ref, "get_display", "GetDisplay", None)
    if display is None:
        # 连 GetDisplay 都没有时退回符号，而不是 str(ref) —— 后者会把对象地址
        # 拼进列头，这种错误只在页面上才看得见。
        display = _ref_symbol(ref)
    if _call(ref, "count_net_value", "CountNetValue", 0) > 0:
        return column_net_value(display)
    if _call(ref, "is_sina_future_except_gold_cn", "IsSinaFutureExceptGoldCN", False):
        return column_settle_price(display)
    return column_price(display, 90)


def _call(ref: Any, snake: str, camel: str, default: Any) -> Any:
    """按 snake_case 优先、camelCase 兜底取 ref 上的方法/属性。

    引用对象保留了 PHP 原名别名（见 ``refobj`` / ``stockref`` 底部），
    所以两种写法都可能遇到。取不到就按"没有"处理 —— 与 PHP 里
    ``CountNetValue()`` 返回 0 的行为一致。
    """
    target = getattr(ref, snake, None)
    if target is None:
        target = getattr(ref, camel, None)
    if target is None:
        return default
    value = target() if callable(target) else target
    return default if value is None else value


# ---------------------------------------------------------------------------
# php/ui/stockcomm.php
# ---------------------------------------------------------------------------
def ref_has_data(ref: Any) -> bool:
    """对应 ``RefHasData()``（``stockcomm.php:3``）：ref 为空也安全。"""
    if ref is None:
        return False
    return bool(_call(ref, "has_data", "HasData", False))


def ref_sort_by_numeric(refs: Iterable[Any], callback: Callable[[Any], Any]) -> list:
    """对应 ``RefSortByNumeric()``（``stockcomm.php:21``）：按回调结果数值升序。

    **用 ``sorted`` 而不是 PHP 的 ``asort``**：PHP 版本依赖 symbol 做键去重，
    所以 **同符号的重复项会被静默丢掉**。这里改成稳定的 ``sort``（Python 的
    ``sorted`` 保证稳定），同值保持原顺序、重复项保留 —— 行为差异见下：

    * PHP：``asort`` 也不是稳定排序，且同 symbol 覆盖；
    * 本版：稳定排序 + 不去重。

    不去重是对的：调用方（如分组列表）传进来的 ref 列表本就该是去重的，
    排序函数悄悄去重会让"少了两行"变成查不出来的 bug。
    """
    pairs = [(ref, callback(ref)) for ref in refs]
    pairs.sort(key=lambda pair: _as_float(pair[1]))
    return [ref for ref, _ in pairs]


def ref_sort_by_symbol(refs: Iterable[Any]) -> list:
    """对应 ``RefSortBySymbol()``（``stockcomm.php:42``）：按符号去重后升序。

    这个函数**本来就要去重**（PHP 用 ``ksort`` + 同键跳过），所以这里照做：
    同一个符号只保留**第一个**。
    """
    mapping: dict[str, Any] = {}
    for ref in refs:
        symbol = _ref_symbol(ref)
        if symbol not in mapping:
            mapping[symbol] = ref
    return [mapping[symbol] for symbol in sorted(mapping)]


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ref_symbol(ref: Any) -> str:
    symbol = _call(ref, "get_symbol", "GetSymbol", None)
    return "" if symbol is None else str(symbol)


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名，按 PHP 写法调用仍可用）
# ---------------------------------------------------------------------------
TableColumnGetTotalWidth = table_column_get_total_width
TableColumnGetLastWidth = table_column_get_last_width
IsTableCommonDisplay = is_table_common_display

TableColumnGetEst = table_column_get_est
TableColumnGetOfficialEst = table_column_get_official_est
TableColumnGetFairEst = table_column_get_fair_est
TableColumnGetRealtimeEst = table_column_get_realtime_est
TableColumnGetNetValue = table_column_get_net_value
TableColumnGetPosition = table_column_get_position
TableColumnGetPremium = table_column_get_premium
TableColumnGetPrice = table_column_get_price
TableColumnGetQuantity = table_column_get_quantity
TableColumnGetRemark = table_column_get_remark
TableColumnGetStock = table_column_get_stock
TableColumnGetSymbol = table_column_get_symbol

RefGetTableColumnNetValue = ref_get_table_column_net_value
RefHasData = ref_has_data
RefSortByNumeric = ref_sort_by_numeric
RefSortBySymbol = ref_sort_by_symbol
