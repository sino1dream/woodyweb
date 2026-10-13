"""历史数据页面的共享查询与显示口径。

对应 PHP：

* ``php/ui/stockhistoryparagraph.php``      —— 历史价格
* ``php/ui/fundhistoryparagraph.php``       —— 基金溢价记录
* ``php/ui/netvaluehistoryparagraph.php``   —— 净值记录
* ``php/ui/netvaluecloseparagraph.php``     —— 净值价格比较
* ``php/ui/calibrationhistoryparagraph.php``—— 校准记录
* ``php/ui/fundshareparagraph.php``         —— 基金场内份额

这些页面在 PHP 里各自独立，但它们共享同一套 SQL 取数语义与显示口径。
本模块把两部分集中起来：

1. **显示口径**（``format_price_display`` / ``format_number_display`` /
   ``format_ratio_display`` / ``format_turnover_display`` / ``format_hm``）——
   一一对应 PHP ``php/stock.php`` 与 ``php/ui/stockcomm.php`` 的原函数；
2. **取数语义**（``DailyCloseSql`` / ``StockHistorySql``）——
   对应 PHP ``php/sql/sqldailyclose.php`` 与 ``php/sql/sqlstocksymbol.php``。

放在 ``auto`` 侧而不是 ``web`` 侧，是因为这些口径属于"业务口径"，
以后后台脚本（如导出 CSV）也要用；``web`` 侧只负责把结果渲染成表格。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from sql.quotestore import stock_get_percentage
from core.stockdata import rtrim0


# 对应 PHP ``php/ui/table.php:4`` 的 ``define('TABLE_COMMON_DISPLAY', 10)``。
# 每页默认 10 行；start=0 且 num<=10 视为"常见显示"，此时页面不显示翻页菜单。
TABLE_COMMON_DISPLAY = 10

# 对应 PHP ``MIN_FLOAT_VAL``：小于这个值就当 0 处理，避免除零与噪声。
MIN_FLOAT_VAL = 0.0000005


# ---------------------------------------------------------------------------
# 显示口径（对应 PHP php/stock.php 与 php/ui/stockcomm.php）
# ---------------------------------------------------------------------------
def is_table_common_display(start: int, num: int) -> bool:
    """对应 PHP ``IsTableCommonDisplay()``（``php/ui/table.php:6``）。

    仅当 ``start == 0`` 且 ``num <= TABLE_COMMON_DISPLAY`` 时为真；
    为真时页面不显示翻页菜单（当前就是最常见的展示）。
    """
    return start == 0 and num <= TABLE_COMMON_DISPLAY


def format_hm(value: Any) -> str:
    """把时间字段取成 ``HH:MM``，对应 PHP ``GetHM()``。

    PHP 是 ``substr($strHMS, 0, 5)``（``php/debug.php:119``），即截掉秒。
    这里先处理 MySQL ``TIME`` 列经 PyMySQL 返回的 ``timedelta``
    （见 ``quoteupdater._normalize_quote_time()`` 的同类问题），再截取。
    """
    if value is None or value == "":
        return ""
    text = _time_to_text(value)
    return text[:5] if len(text) >= 5 else text


def _time_to_text(value: Any) -> str:
    """把可能是 ``timedelta`` / ``time`` / 字符串的时间值统一成 ``HH:MM:SS``。"""
    import datetime as _datetime

    if isinstance(value, _datetime.timedelta):
        total = int(value.total_seconds()) % (24 * 3600)
        return "%02d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)
    if isinstance(value, (_datetime.time, _datetime.datetime)):
        return value.strftime("%H:%M:%S")
    return str(value).strip()


def format_price_display(value: Any, prev: Any = None, precision: int = 3) -> tuple:
    """价格显示，对应 PHP ``StockGetPriceDisplay()``（``php/stock.php:91``）。

    返回 ``(文本, 颜色)``。颜色取值与 PHP 的 ``$strGreater``/``$strLess`` 一致：

    * ``red``   —— 涨（大于 ``prev + 容差``）
    * ``green`` —— 跌（小于 ``prev - 容差``）
    * ``grey``  —— 平（容差之内）

    容差 = ``0.5 / 10^precision``，是 PHP 里那个 ``$iDiff`` 循环的效果：
    ``precision=3`` 时容差 0.0005，避免把浮点噪声显示成涨跌。

    注意 PHP 默认 ``$strGreater='red'``、``$strLess='green'``；但
    ``GetNumberDisplay()`` 传的是 ``'black'``/``'red'``，所以调用方要自己选，
    这里把首尾色作为参数暴露出来。
    """
    if value is None or value == "":
        return ("", "")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return (str(value), "")

    color = "black"
    if prev is not None and prev != "":
        try:
            previous = float(prev)
        except (TypeError, ValueError):
            previous = None
        if previous is not None:
            tolerance = 0.5
            for _ in range(max(precision, 0)):
                tolerance /= 10.0
            if number > previous + tolerance:
                color = "red"
            elif number < previous - tolerance:
                color = "green"
            else:
                color = "grey"

    return (_trim_number_text(number, precision), color)


def _trim_number_text(number: float, precision: int) -> str:
    """按 PHP 的 ``number_format`` + ``rtrim('0')`` + ``rtrim('.')`` 出文本。

    **负零要归零**：PHP 的 ``number_format(-0.004, 2)`` 是 ``'0.00'`` ——
    **不带负号**，而 Python 的 ``'%.2f' % -0.004`` 是 ``'-0.00'``。
    于是 ``(-0.005, 0)`` 这一小段区间里的值（比如 -0.001 的手续费、
    -0.002 的盈亏）在 PHP 里显示成 ``0``，在 Python 里会是 ``-0``。
    是 PHP 逐例对照（``_php_transaction_compare.py`` 的 ``number_display``
    组）抓出来的，见那个脚本 docstring 的"这个脚本抓到的真 bug"一节
    与 pitfalls 第 51 条。
    """
    if precision <= 0:
        # PHP 的 number_format(x, 0) 会四舍五入成整数（无小数点）
        text = "%.0f" % number
        # number_format(-0.4) 同样是 '0' 而不是 '-0'
        return text if text != "-0" else "0"
    text = "%.*f" % (precision, number)
    text = text.rstrip("0").rstrip(".")
    return text if text not in ("", "-", "-0") else "0"


def format_number_display(value: Any, precision: int = 2) -> str:
    """数字显示，对应 PHP ``GetNumberDisplay()``（``php/stock.php:111``）。

    注意它用的是 ``StockGetPriceDisplay($fVal, 0.0, ...)`` —— 以 **0** 为基准，
    所以正数走 ``$strGreater``（默认 ``black``）、负数走 ``$strLess``（默认 ``red``）。
    本函数只返回文本，颜色由调用方按需决定（页面里用 CSS 类）。
    """
    if value is None or value == "":
        return ""
    try:
        return _trim_number_text(float(value), precision)
    except (TypeError, ValueError):
        return str(value)


def format_ratio_display(value: Any, precision: int = 4) -> str:
    """比例显示，对应 PHP ``GetRatioDisplay()``（``php/stock.php:116``）。

    与 :func:`format_number_display` 的唯一差别是基准取 **1.0**
    （``StockGetPriceDisplay($fVal, 1.0, $iPrecision)``），
    所以大于 1 走红、小于 1 走绿。
    """
    if value is None or value == "":
        return ""
    try:
        return _trim_number_text(float(value), precision)
    except (TypeError, ValueError):
        return str(value)


def format_int_display(value: Any) -> str:
    """对应 PHP ``number_format($fVal)``（**只传一个参数**的那一种）。

    PHP 的 ``number_format()`` 只传一个参数时：小数位 0、**带千分位逗号**。
    这与本文件里其它 ``format_*`` 都不一样（它们都显式传了
    ``'.'`` / ``''`` 把分隔符关掉），所以单独立一个函数，避免混用。

    用在两处（都是 ``php/ui/portfolioparagraph.php``）：

    * ``GetArbitrageQuantity()`` —— ``number_format($fQuantity / GetStockHedge(...))``；
    * ``_getArbitrageTestStr()`` —— ``number_format($iArbitrageQuantity + ...)``。

    负数的逗号在**负号之后**（PHP 行为）：``number_format(-1234567)`` 得
    ``"-1,234,567"``，不是 ``"-,123,456"``。
    """
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    # PHP 的 number_format(x) 先 round-half-up 到整数再插逗号。
    # Python 的 round() 是银行家舍入（2.5 → 2），PHP 是 2.5 → 3，
    # 所以这里按"四舍五入远离零"显式做一次。
    if number >= 0:
        rounded = int(number + 0.5)
    else:
        rounded = int(number - 0.5)
    negative = rounded < 0
    digits = str(-rounded if negative else rounded)
    parts = []
    while len(digits) > 3:
        parts.insert(0, digits[-3:])
        digits = digits[:-3]
    parts.insert(0, digits)
    text = ",".join(parts)
    return ("-" + text) if negative else text


def format_turnover_display(volume: Any, shares: Any, precision: int = 2) -> str:
    """换手率显示，对应 PHP ``GetTurnoverDisplay()``（``php/ui/stockcomm.php:70``）。

    PHP 原文：

        GetNumberDisplay(100.0 * $fVolume / ($fShare * 10000.0), $iPrecision).'%'

    ``shares`` 的单位是**万份**，所以先乘 10000 还原成份，再算百分比 ——
    漏掉这个 10000 会算出大 10000 倍的数字（实测国泰商品换手会显示成 5992222%）。
    """
    if volume is None or volume == "" or shares is None or shares == "":
        return ""
    try:
        volume_value = float(volume)
        shares_value = float(shares) * 10000.0
    except (TypeError, ValueError):
        return ""
    if abs(shares_value) <= MIN_FLOAT_VAL:
        return ""
    return format_number_display(100.0 * volume_value / shares_value, precision) + "%"


def format_percentage_display(prev: Any, value: Any) -> str:
    """涨跌幅文本（不带符号前缀），对应 PHP ``GetPercentageDisplay()``。

    公式复用 ``quotestore.stock_get_percentage()``（即 PHP ``StockGetPercentage()``），
    只有一处实现，避免各页面各写一遍。
    """
    change = percentage_change(prev, value)
    if change is None:
        return ""
    text = "%.2f" % change
    text = text.rstrip("0").rstrip(".")
    if text in ("", "-0", "0"):
        return "0"
    return text + "%"


def percentage_change(prev: Any, value: Any) -> Any:
    """``(value / prev - 1) * 100``；任一无效或 prev≈0 时返回 ``None``。

    与 PHP ``StockGetPercentage()`` 一致：``prev`` 绝对值小于 ``MIN_FLOAT_VAL``
    时 PHP 返回 0.0，这里返回 ``None`` 让页面留空 —— 页面留空比显示 "0%"
    更诚实（0% 会被误读成"真的没涨跌"）。
    """
    if prev is None or prev == "" or value is None or value == "":
        return None
    try:
        previous = float(prev)
        current = float(value)
    except (TypeError, ValueError):
        return None
    if abs(previous) <= MIN_FLOAT_VAL:
        return None
    return stock_get_percentage(previous, current)


# ---------------------------------------------------------------------------
# 表格行
# ---------------------------------------------------------------------------
@dataclass
class TableRow:
    """一行表格数据。

    ``cells`` 每项要么是 ``str``（普通文本），要么是 ``(文本, 颜色类)`` 二元组。
    颜色类沿用 PHP 的颜色名（``red``/``green``/``grey``/``black``），
    由模板映射成 CSS 类 —— 页面层不做业务判断。

    ``match`` 对应 PHP ``EchoMatchTableColumn()``：当"实测仓位"与库里仓位一致时，
    整行加高亮；这是作者用来一眼看出仓位是否吻合的做法。
    """

    cells: list = field(default_factory=list)
    match: bool = False
    #: 第一格的 tooltip。对应 ``EchoTableColumn($ar, $strColor, $strFirstHint)``
    #: 的第三个参数：参考数据表把股票名挂在这一行的链接上（见 ``referenceparagraph.php``）。
    title: str = ""
    #: **整行**底色名，对应 ``EchoTableColumn($ar, $strColor)`` 的第二个参数。
    #:
    #: PHP 那里是 ``style="background-color:xxx"``，即**底色**而不是文字色；
    #: 五档交易表用它区分涨停/跌停（橙）与买卖一档（黄）。与 :attr:`cells`
    #: 里那一格一格的文字色是两回事，所以单独一个字段，由模板渲染成
    #: ``<tr class="bg-xxx">``。空串表示不染色。
    background: str = ""
    #: 第一格的链接地址；非空时模板把它渲染成 ``<a href>``。
    #:
    #: 对应 PHP 首格恒为 ``GetMyStockLink()`` / ``GetStockLink()`` 产出的 ``<a>``。
    #: 这里只存 URL、不存 HTML：拼链接属于模板的职责（它才知道 ``url_for``），
    #: 服务层通过注入的回调拿到 URL，见 ``web/services/stock_service.py``。
    href: str = ""
    #: **非首格**的链接。键是 :attr:`cells` 的下标（从 0 起）；
    #: 值可以是 **URL 字符串**（把该格文本包成 ``<a>``），也可以是
    #: **``[{"text", "url"}, ...]``**（直接渲染成一串链接、忽略文本）。
    #:
    #: 为什么需要它：PHP 的行里不止首格有链接 —— ``stockparagraph`` 的名称格是
    #: 雪球外链、管理员列还要塞"编辑/删除"两个链接。只有 :attr:`href` 一个口子时，
    #: 剩下的格子只能由服务层自己拼 HTML，那与"数据与渲染分离"相悖。
    #:
    #: 例：``row.hrefs[1] = "https://xueqiu.com/S/SZ161130"``
    #:     ``row.hrefs[2] = [{"text": "编辑", "url": "/stock/edit/SZ161130"}]``
    hrefs: dict = field(default_factory=dict)
    #: 需要**透传 HTML** 的格子下标（0 起）。模板对下标在列表里的格子
    #: 不做转义，其余格子照常转义。
    #:
    #: 为什么需要它：``hrefs`` 只能表达"纯链接"，而交易明细段落的备注格是
    #: ``清空链接 + 备注原文 + 校准链接`` —— 文本夹在两个链接**中间**，
    #: 且链接带 ``onclick`` 确认。PHP 的 ``EchoTableColumn($ar)`` 收到的
    #: 本来就是拼好的 HTML 串，这里保留同样形态，由模板按 ``|safe`` 渲染。
    #:
    #: 与 :attr:`hrefs` 是互斥的两套方案：一个格子要么走 ``hrefs``（数据），
    #: 要么出现在 ``html_indexes`` 里（HTML）。不要两个都给。
    html_indexes: list = field(default_factory=list)

    def add(self, value: Any, color: str = "") -> "TableRow":
        """追加一格。``color`` 为空表示普通文本。"""
        if isinstance(value, tuple) and len(value) == 2:
            text, color = value
        else:
            text = "" if value is None else str(value)
        self.cells.append((text, color) if color else text)
        return self


# ---------------------------------------------------------------------------
# 取数语义（对应 PHP php/sql/sqldailyclose.php）
# ---------------------------------------------------------------------------
class DailyCloseSql:
    """``(stock_id, date, close)`` 形状的日频表访问。

    对应 PHP ``DailyCloseSql``（``php/sql/sqldailyclose.php``）：所有历史表
    （``stockhistory`` / ``netvaluehistory`` / ``calibrationhistory`` /
    ``shareshistory`` / ``fundest``）都是这个形状，只是多几个列。

    排序统一 ``date DESC``（PHP ``_SqlOrderByDate()``）。
    """

    def __init__(self, connection: Any, table: str, columns: Sequence[str] = ("id", "stock_id", "date", "close")):
        self.connection = connection
        self.table = table
        # **必须带上 stock_id**：调用方拿到行以后要用它去查"上一交易日"、
        # 成交量、复权价等同类数据。PHP 那边是对象自己知道 stock_id，
        # 这里行是纯 dict，所以得显式带出来。
        self.columns = tuple(columns)

    # -- 内部 ---------------------------------------------------------------
    def _fetchone(self, sql: str, params: tuple = ()) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def _fetchall(self, sql: str, params: tuple = ()) -> list:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def count(self, stock_id: Any) -> int:
        """对应 PHP ``Count()``。"""
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM `%s` WHERE stock_id = %%s" % self.table, (stock_id,)
        )
        return int(row["n"]) if row else 0

    def get_all(self, stock_id: Any, start: int = 0, num: int = 0) -> list:
        """按日期倒序取一页，对应 PHP ``GetAll()`` + ``_SqlBuildLimit()``。

        PHP 的 limit 语义（``php/sql/_sqlcommon.php:63``）：

        * ``start == 0`` 且 ``num != 0`` → ``LIMIT num``
        * ``start != 0``                 → ``LIMIT start, num``
        * ``num == 0``                   → **不加 limit**（全部记录）

        注意 ``start != 0`` 且 ``num == 0`` 时 PHP 会拼出 ``LIMIT start, 0``，
        结果是 0 行；这里照抄这个行为（传 start 就必须给 num）。
        """
        columns = ", ".join("`%s`" % name for name in self.columns)
        sql = "SELECT %s FROM `%s` WHERE stock_id = %%s ORDER BY `date` DESC" % (columns, self.table)
        params: list = [stock_id]
        if start == 0:
            if num != 0:
                sql += " LIMIT %d" % int(num)
        else:
            sql += " LIMIT %d, %d" % (int(start), int(num))
        return self._fetchall(sql, tuple(params))

    def get_record(self, stock_id: Any, date: Any) -> Any:
        """取某一天的行；没有返回 ``None``。对应 PHP ``GetRecord()``。"""
        columns = ", ".join("`%s`" % name for name in self.columns)
        return self._fetchone(
            "SELECT %s FROM `%s` WHERE stock_id = %%s AND `date` = %%s LIMIT 1"
            % (columns, self.table),
            (stock_id, date),
        )

    def get_record_prev(self, stock_id: Any, date: Any) -> Any:
        """取 ``date`` **之前**最近一行，对应 PHP ``GetRecordPrev()``。

        这就是"上一个交易日"的来源 —— 历史表只存交易日，所以按日期倒序取第一条
        比按日历减一天可靠。
        """
        columns = ", ".join("`%s`" % name for name in self.columns)
        return self._fetchone(
            "SELECT %s FROM `%s` WHERE stock_id = %%s AND `date` < %%s ORDER BY `date` DESC LIMIT 1"
            % (columns, self.table),
            (stock_id, date),
        )

    def get_close(self, stock_id: Any, date: Any) -> Any:
        """对应 PHP ``GetClose()``。"""
        row = self.get_record(stock_id, date)
        return row["close"] if row else None

    def get_close_prev(self, stock_id: Any, date: Any) -> Any:
        """对应 PHP ``GetClosePrev()``。"""
        row = self.get_record_prev(stock_id, date)
        return row["close"] if row else None

    def get_date_prev(self, stock_id: Any, date: Any) -> Any:
        """对应 PHP ``GetDatePrev()``：上一个有记录的日期。"""
        row = self.get_record_prev(stock_id, date)
        return row["date"] if row else None


class StockHistorySql(DailyCloseSql):
    """``stockhistory`` 表，对应 PHP ``StockHistorySql``。

    比其他日频表多 ``volume`` 与 ``adjclose`` 两列。
    """

    def __init__(self, connection: Any):
        super().__init__(
            connection,
            "stockhistory",
            columns=("id", "stock_id", "date", "close", "volume", "adjclose"),
        )

    def get_volume(self, stock_id: Any, date: Any) -> Any:
        """对应 PHP ``GetVolume()``：取不到时返回 ``'0'``（不是 ``None``）。"""
        row = self.get_record(stock_id, date)
        return row["volume"] if row else "0"

    def get_adjclose(self, stock_id: Any, date: Any, strict: bool = False) -> Any:
        """对应 PHP ``GetAdjClose()``（``php/sql/sqlstocksymbol.php:83``）。

        ``strict=False``（默认）时，若当天没有记录就**回落到上一个交易日** ——
        原注释写着 "when hongkong market on holiday"：
        港股与 A 股假期不同，比对时允许用最近一条复权价，
        否则页面会因港股休市整列空白。
        """
        row = self.get_record(stock_id, date)
        if row is None and not strict:
            row = self.get_record_prev(stock_id, date)
        return row["adjclose"] if row else None


class DailyTimeSql(DailyCloseSql):
    """带 ``time`` 列的日频表（``calibrationhistory`` / ``fundest``）。

    对应 PHP ``DailyTimeSql``（``php/sql/sqldailytime.php``）。
    ``calibrationhistory`` 还多一个 ``num`` 列（当日取样次数）。
    """

    def __init__(self, connection: Any, table: str, extra: Sequence[str] = ()):
        columns = ("id", "stock_id", "date", "close", "time") + tuple(extra)
        super().__init__(connection, table, columns=columns)

    def count(self, stock_id: Any) -> int:
        return super().count(stock_id)


class CalibrationSql(DailyTimeSql):
    """``calibrationhistory`` 表（含 ``num`` 列）。"""

    def __init__(self, connection: Any):
        super().__init__(connection, "calibrationhistory", extra=("num",))


class FundEstSql(DailyTimeSql):
    """``fundest`` 表（官方 EST 记录，无 ``num`` 列）。"""

    def __init__(self, connection: Any):
        super().__init__(connection, "fundest")


class NetValueHistorySql(DailyCloseSql):
    """``netvaluehistory`` 表。"""

    def __init__(self, connection: Any):
        super().__init__(connection, "netvaluehistory")


class SharesHistorySql(DailyCloseSql):
    """``shareshistory`` 表（场内份额，单位万份）。

    **注意**：PHP 里表名是 ``shareshistory`` 不是 ``fundshares``
    （``php/sql/sqldailyclose.php:303``）；Python 侧原来没有这张表，
    见 ``python/dbtools/schema.sql`` 与本模块配套的建表说明。
    """

    def __init__(self, connection: Any):
        super().__init__(connection, "shareshistory")


# ---------------------------------------------------------------------------
# 组合样式：给页面描述"这一页有哪些列"
# ---------------------------------------------------------------------------
def stock_calc_hedge(calibration: Any, position: Any) -> Any:
    """对应 PHP ``StockCalcHedge()``（``php/stock.php:389``）：``cal / pos``。"""
    if calibration is None or calibration == "":
        return None
    if position is None or position == "":
        return None
    try:
        divisor = float(position)
    except (TypeError, ValueError):
        return None
    if abs(divisor) <= MIN_FLOAT_VAL:
        return None
    return float(calibration) / divisor


# 校准值记录里"对冲值"要乘的倍数（对应 PHP calibrationhistoryparagraph.php:45-65）。
# 这几个符号的校准值量纲特殊：黄金类基金按 1000、白银按 10、沪银按 15。
# 这是作者按各品种标的报价单位逐个调出来的经验值，不是通用公式 ——
# 新增品种若对冲值明显不对，先检查是否漏了这里。
CALIBRATION_HEDGE_MULTIPLIERS = {
    "SH518800": 1000,
    "SH518880": 1000,
    "SZ159934": 1000,
    "SZ159937": 1000,
    "SZ159985": 10,
    "SZ161226": 15,
}
DEFAULT_HEDGE_MULTIPLIER = 1


def calibration_hedge_multiplier(symbol: str) -> int:
    """取该符号的对冲值倍数，缺省 1。"""
    return CALIBRATION_HEDGE_MULTIPLIERS.get(symbol, DEFAULT_HEDGE_MULTIPLIER)


def use_same_day_net_value(symbol: str, is_qdii: bool, is_mix: bool, is_hk_mix: bool) -> bool:
    """对应 PHP ``UseSameDayNetValue()``（``php/stock.php:381``）。

    PHP 原文：

        if (in_arrayQdii($strSymbol))            return false;
        else if (in_arrayQdiiMix($strSymbol))    return in_arrayHkMix($strSymbol);
        return true;

    含义：**QDII 基金要用前一天的净值**（当天净值要等美股收盘后才出），
    混合型里只有港股混合才用当天净值，其余（A 股类）用当天净值。
    判断所需的三个布尔值由调用方传入，避免本模块反向依赖 fundgroups。
    """
    if is_qdii:
        return False
    if is_mix:
        return is_hk_mix
    return True
