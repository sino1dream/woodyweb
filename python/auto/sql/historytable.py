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

import datetime
import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Sequence

# ``_is_weekend`` 是 ``WriteDaily()`` 周末闸门的口径实现（``sql/quotestore.py``
# 里那份），这里复用而不是再抄一遍：闸门只有一处实现，改的时候不会漏。
from sql.quotestore import _is_weekend, stock_get_percentage
from core.stockdata import rtrim0
# ``sql`` → ``ref`` 是允许的依赖方向（见 ``sql/__init__.py`` 的依赖方向说明）。
# 这两个下划线函数是 PHP ``floatval`` / ``strval`` 的口径实现，持仓测算表的
# ``mysql_round`` 要原样复用，不在本文件再抄一份（抄了就会有两处需要同步）。
from ref.stockref import _php_floatval, _php_strval_float  # noqa: E402


# 对应 PHP ``php/ui/table.php:4`` 的 ``define('TABLE_COMMON_DISPLAY', 10)``。
# 每页默认 10 行；start=0 且 num<=10 视为"常见显示"，此时页面不显示翻页菜单。
TABLE_COMMON_DISPLAY = 10

# 对应 PHP ``MIN_FLOAT_VAL``：小于这个值就当 0 处理，避免除零与噪声。
MIN_FLOAT_VAL = 0.0000005

#: ``php/debug.php:12`` 的 ``FLOAT_PRECISION``，``mysql_round`` 的默认精度。
FLOAT_PRECISION = 6

#: ``OldestYMD`` 的构造日期（``php/class/year_month_day.php:160``）：
#: 早于这一天的记录算"太老"，由 ``DailyCloseSql.delete_invalid_dates()`` 清理。
OLDEST_VALID_DATE = datetime.date(2014, 1, 1)


def _parse_ymd(value: Any) -> datetime.date:
    """把 ``date`` 列的值解析成 :class:`datetime.date`；解析不出返回 ``None``。

    容忍 ``datetime`` / ``date`` / ``"YYYY-MM-DD"`` / ``"YYYY-MM-DD HH:MM:SS"``
    四种形态（MySQL 直连给 ``date``，假桩和 JSON 给字符串）。
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip().replace("/", "-")
    if len(text) < 10:
        return None
    try:
        return datetime.datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


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

    **先按 :func:`php_round` 的十进制 half-up 舍入，再格式化**。只在格式化时
    用 ``"%.*f"`` 会掉进 Python 的二进制 / 银行家舍入，与 PHP 在 *.5 边界上
    必然分叉：``-2.5`` 到 0 位，PHP 的 ``number_format`` 给 ``-3``，
    ``"%.0f" % -2.5`` 给 ``-2``（穷举持仓页的整数比例就踩在这个边界上）。

    **负零要归零**：PHP 的 ``number_format(-0.004, 2)`` 是 ``'0.00'`` ——
    **不带负号**，而 Python 的 ``'%.2f' % -0.004`` 是 ``'-0.00'``。
    于是 ``(-0.005, 0)`` 这一小段区间里的值（比如 -0.001 的手续费、
    -0.002 的盈亏）在 PHP 里显示成 ``0``，在 Python 里会是 ``-0``。
    是 PHP 逐例对照（``_php_transaction_compare.py`` 的 ``number_display``
    组）抓出来的，见那个脚本 docstring 的"这个脚本抓到的真 bug"一节
    与 pitfalls 第 51 条。
    """
    rounded = php_round(number, precision)
    if precision <= 0:
        text = "%.0f" % rounded
        # number_format(-0.4) 同样是 '0' 而不是 '-0'
        return text if text != "-0" else "0"
    text = "%.*f" % (precision, rounded)
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


def _to_float(value: Any) -> float:
    """按 PHP ``floatval`` 口径转 float；任何输入都得到数值（不抛错）。

    PHP 的 ``round()`` / ``number_format()`` 内部都会先 ``floatval``，所以
    ``round('abc', 2)`` 是 ``0.0`` 而不是错误 —— 这里照抄。
    """
    return _php_floatval(value)


def php_round(value: Any, digits: int) -> float:
    """PHP ``round($fVal, $iDigits)``：**最短十进制表示**上的 half-away-from-zero。

    PHP 的 ``round()`` / ``number_format()`` 不是按 double 的二进制精确值舍入，
    而是先把值化成最短往返十进制串（``repr`` 那一层语义，PHP 内部是预舍入到
    15 位有效数字，与 ``repr`` 的最短表示在实用范围内等价），再做十进制
    half-up。实测（``php -n`` 8.0.10）::

        round(2.675, 2)  === 2.68      Python round(2.675, 2)  === 2.67
        round(1.005, 2)  === 1.01      Python round(1.005, 2)  === 1.0
        round(0.125, 2)  === 0.13      Python round(0.125, 2)  === 0.12
        round(0.045, 2)  === 0.05      Python round(0.045, 2)  === 0.04

    **为什么必须照抄**：持仓测算表的「旧价格」列入参是 MySQL 的
    ``adjclose`` 串（``"3.675"`` 这种三位小数），第 3 位小数为 5 时两种
    口径必然分叉 —— 而 ``stockhistory`` 表里这类值到处都是。
    """
    quantum = Decimal(1).scaleb(-int(digits))
    # Decimal(repr(x))：repr 给出该 double 的最短往返十进制表示
    return float(Decimal(repr(_to_float(value))).quantize(quantum, rounding=ROUND_HALF_UP))


def mysql_round(value: Any, precision: int = FLOAT_PRECISION) -> str:
    """PHP ``mysql_round($str, $iPrecision)``（``php/debug.php:34``）。

    原式是 ``strval(round(floatval($str), $iPrecision))``，两个细节：

    * ``floatval`` 语义 —— 空串 / 非数字串得 0.0（:func:`php_round` 内部
      已按 ``floatval`` 口径转换，见 :func:`_to_float`）；
    * 结果走 **``strval``** 而不是定点格式 —— 尾随零被削掉
      （``mysql_round('2.3995', 2)`` = ``'2.4'``），**负零保留**
      （``mysql_round(-0.004, 2)`` = ``'-0'``，PHP 实测）。
    """
    return _php_strval_float(php_round(value, precision))


def php_number_format(value: Any, precision: int = 2) -> str:
    """PHP ``number_format($fVal, $precision)``：定点、**不削尾零**、不去负零。

    与 :func:`format_number_display` 是两套格式，别混用：

    * ``format_number_display`` 走 ``StockGetPriceDisplay()``
      （``php/stock.php:91``），末尾会 ``rtrim('0')`` 再把 ``-0`` 归成 ``0``；
    * ``number_format`` 原样出 ``"1.00"``，``-0.00`` 也原样出。

    两个调用点都用它：

    * ``_QdiiGetStockPosition()``（``php/ui/netvaluehistoryparagraph.php:16``）
      —— 仓位那一格，还要拿它跟 ``number_format($ref->GetPosition(), 2)``
        做**字符串比较**来决定行底色，所以尾零不能削；
    * ``_getFundPositionStr()``（``php/ui/fundestparagraph.php:135``）
      —— 估值表上方那行「值使用0.95。」；
    * 持仓测算表（``woody/res/holdingscn.php`` 的七列）—— 列值全是算出来的
      比例与涨幅，边界值密集，舍入口径必须与 PHP 逐格一致。

    ## 舍入口径：最短十进制 half-up，负零归正

    PHP 8 实测：``number_format(2.675, 2)`` = ``'2.68'``、``number_format(1.005, 2)``
    = ``'1.01'``、``number_format(0.125, 2)`` = ``'0.13'``（Python 的 ``'%.2f'``
    分别给 ``'2.67'`` / ``'1.00'`` / ``'0.12'``）；而结果为零时**负号被去掉**：
    ``number_format(-0.004, 2)`` = ``'0.00'``、``number_format(-0.00004, 4)``
    = ``'0.0000'``。所以这里按 :func:`php_round` 的十进制口径舍入后，再对
    ``== 0`` 的结果取绝对值。

    千分位逗号没有实现：三个调用点的入参（仓位、反算仓位、持仓比例）都远小于
    1000，用不到的语义不加。
    """
    rounded = Decimal(repr(_to_float(value))).quantize(
        Decimal(1).scaleb(-int(precision)), rounding=ROUND_HALF_UP
    )
    if rounded == 0:
        # PHP 的 number_format 在结果为零时不带负号（负零归正）
        rounded = abs(rounded)
    return str(rounded)


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


def format_percentage_display(prev: Any, value: Any, precision: Any = None) -> str:
    """涨跌幅文本（不带符号前缀），对应 PHP ``GetPercentageDisplay()``。

    公式复用 ``quotestore.stock_get_percentage()``（即 PHP ``StockGetPercentage()``），
    只有一处实现，避免各页面各写一遍。

    **阈值归零**（PHP ``GetPercentageString()``，``php/stock/stockref.php:129``）::

        if (abs($fPercentage * $fDivisor) < (50.0 / pow(10, $this->GetPrecision()])))
            return '0';

    即 ``|涨幅% × prev| < 50 / 10^precision`` 时整格显示 ``0``（不带 ``%``）。
    数学上等价于 ``|value − prev| < 0.5 × 10^-precision`` —— 因为
    ``涨幅% × prev = (value − prev) × 100``，两边同除 100 即得。含义是
    "差值小于半个最小显示单位就不值得占一格"。

    2026-10-20 对照生产端修掉的差异：SZ161130 的 10-19 / 10-08 两行误差
    （净值 4.3704 vs EST 4.37015、4.5515 vs 4.551192）本地显示 ``-0.01%``，
    生产端显示 ``0`` —— 就是缺这条阈值。

    ``precision`` 是 **被调用 ref 自己的** ``GetPrecision()``
    （``php/stock/stocksymbol.php:994`` / ``core/stocksymbol.py:981``）：
    基金 3 / 外汇 4 / 其余 2，**不是**净值列的 ``NETVALUE_PRECISION(4)``。
    传 ``None`` 表示不做阈值判定（旧调用兼容）；页面调用点都应显式传。

    PHP 那边 ``$fDivisor == false`` 时会回落 ``strPrevPrice`` 再比；
    这里 ``prev`` 恒由调用方显式传入，无效时 ``percentage_change()`` 已
    返回 ``None``（页面留空），不会走到阈值这一步。
    """
    change = percentage_change(prev, value)
    if change is None:
        return ""
    if precision is not None:
        try:
            divisor = float(prev)
        except (TypeError, ValueError):
            divisor = None
        if divisor is not None and abs(change * divisor) < 50.0 / (10.0 ** precision):
            return "0"
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
    #: **只有仓位估算页用**：这一行算出仓位时，把参与计算的那些量留一份，
    #: 供页面下方的「算法说明」逐格代入验算（见
    #: ``web/services/history_service.py::_position_formula_note``）。
    #:
    #: 为什么不另起一个返回通道：仓位是在 ``_row_netvaluehistory`` 内部算的，
    #: 那里已经把 est / cny 的两套取数（净值 vs 行情价回退）都走了一遍，
    #: 让调用方再取一次就会把这套分支抄成两份。空 dict = 这一行没算出仓位。
    position_inputs: dict = field(default_factory=dict)

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
#: ``php/debug.php:11`` 的 ``MIN_FLOAT_VAL``：判断"这个数算不算 0"。
#: 本模块用在 :meth:`DailyCloseSql.get_proportion` 的前值门槛上
#: （与 PHP ``sqldailyclose.php:95`` 同一道闸）。
MIN_FLOAT_VAL = 0.0000005


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

    def get_record_now(self, stock_id: Any) -> Any:
        """最新一行（按 ``date DESC``），对应 PHP ``GetRecordNow()``。

        PHP 的 ``KeySql::GetRecordNow()``（``php/sql/sqlkey.php:70``）就是
        ``GetSingleData(where_key, order_by)``，而 ``DailyCloseSql`` 的
        ``BuildOrderBy()`` 恒为 ``date DESC``（``php/sql/sqldailyclose.php:31``），
        所以语义是"这只标的最新一个交易日那一行"。

        ``_updateStockHoldings``（``woody/res/php/_qdiimix.php:51``）拿
        ``NetValueHistorySql::GetDateNow()`` 当"净值到哪天"的判据 —— 没有这个
        方法，持仓更新编排就不知道净值新不新鲜。
        """
        columns = ", ".join("`%s`" % name for name in self.columns)
        return self._fetchone(
            "SELECT %s FROM `%s` WHERE stock_id = %%s ORDER BY `date` DESC LIMIT 1"
            % (columns, self.table),
            (stock_id,),
        )

    def get_date_now(self, stock_id: Any) -> Any:
        """对应 PHP ``GetDateNow()``：最新一行的日期；一行都没有返回 ``None``。

        PHP 用 ``_getDateString('GetRecordNow', ...)``，取不到返回 ``false``。
        """
        row = self.get_record_now(stock_id)
        return row["date"] if row else None

    def get_close_now(self, stock_id: Any) -> Any:
        """对应 PHP ``GetCloseNow()``：最新一行的收盘值；没有返回 ``None``。

        ``_HoldingsCsvFile::CalcCurrency()``（``woody/res/php/_holdingscsvfile.php:70``）
        用 ``NetValueHistorySql::GetClose()`` 取不到时**回落**到它拿汇率中间价。
        """
        row = self.get_record_now(stock_id)
        return row["close"] if row else None

    def get_close_prev(self, stock_id: Any, date: Any) -> Any:
        """对应 PHP ``GetClosePrev()``。"""
        row = self.get_record_prev(stock_id, date)
        return row["close"] if row else None

    def get_date_prev(self, stock_id: Any, date: Any) -> Any:
        """对应 PHP ``GetDatePrev()``：上一个有记录的日期。"""
        row = self.get_record_prev(stock_id, date)
        return row["date"] if row else None

    def get_switch_dates(self, stock_id: Any) -> list:
        """净值序列的**拐点（局部极值）日期**，对应 PHP ``GetSwitchDates()``
        （``php/sql/sqldailyclose.php:215-264``）。

        一个三阶段状态机，没有阈值、不做平滑，纯方向反转检测：

        1. **第一条**（最新的一天）无条件入组 —— 它是序列端点，未必是极值；
        2. **第二条**只用 ``$bUp = ($fOld > $fCur)`` 定初始方向，不入组；
        3. 之后每读到一条，若方向与当前趋势相反，把**上一条**日期记为拐点并翻转方向。

        几个不改会出错的细节：

        * **输入顺序必须是 ``date DESC``**（``get_all`` 的排序）。从新往旧扫时
          ``$fOld``（上一条）是**日期更新**的那条，于是
          ``$bUp = ($fOld > $fCur)`` 的语义是"顺时间在涨"，反转条件才算得对。
          用升序喂进来会得到一串同样多但**含义全反**的日期。
        * **记的是上一条 ``$strOldDate``**（= 趋势里最后那条），不是当前这条。
          反转是"读到更旧那条才发现"的，而折返点本身属于旧趋势的末端。
          由此还有个干净的规律：``$bUp`` 支（涨势中反转）记的一定是**局部极小**，
          ``!$bUp`` 支记的一定是**局部极大**，两者严格交替。
        * **严格不等号**：相邻两天净值相等时不算反转，平台段整个被跳过。
        * PHP 里 ``$bSecond`` / ``$bUp`` 在循环外**没初始化**，靠 ``$bFirst``
          分支赋值；只有一条记录时返回仅含首条。这里的显式初值只为可读性。

        返回值为**字符串日期列表**（与 ``record["date"]`` 同类型），空表返回空列表。
        最老那段的尾巴不在里面 —— 调用方按 ``dates[i]`` / ``dates[i+1]`` 配对使用，
        见 :meth:`web.services.history_service.HistoryService._build_fundposition`。
        """
        dates: list = []
        first = True
        #: PHP 未初始化，这里显式给初值；语义等价（首条走 $bFirst 分支）
        second = False
        up = False
        old = 0.0
        old_date: Any = None
        for record in self.get_all(stock_id):
            date = record["date"]
            try:
                cur = float(record["close"])
            except (TypeError, ValueError):
                #: 照抄 PHP ``floatval($record['close'])``：空字符串得 **0.0** 而不是报错。
                #: 代价是净值缺失的那天会被当成一次深谷（PHP 同样如此），但换来自
                #: 家与 PHP 的取数完全一致；这类行在正常数据里不存在。
                cur = 0.0
            if first:
                dates.append(date)
                first = False
                second = True
            elif second:
                up = old > cur
                second = False
            elif up:
                if old < cur:
                    up = False
                    dates.append(old_date)
            else:
                if old > cur:
                    up = True
                    dates.append(old_date)
            old = cur
            old_date = date
        return dates

    def get_proportion(self, stock_id: Any, date: Any, prev_date: Any) -> Any:
        """对应 PHP ``GetProportion()``（``php/sql/sqldailyclose.php:88``）。

        ``close(date) / close(prev_date)``，**任一日缺失或前值过小都返回
        ``None``**（PHP 返回 ``false``）。调用方（``HoldingsReference.
        GetProportionArray``）拿到 ``None`` 就整段放弃那一列 —— 所以这里是
        "缺一天就不画"，不是"拿 0 顶上"。

        ``前值 > MIN_FLOAT_VAL`` 那道门槛是 PHP 原文就有的：持仓清单里偶尔会
        有比例 0.000000 的成分股，不挡住会算出除零。
        """
        value = self.get_close(stock_id, date)
        if value is None:
            return None
        prev = self.get_close(stock_id, prev_date)
        if prev is None:
            return None
        try:
            previous = float(prev)
        except (TypeError, ValueError):
            return None
        if previous <= MIN_FLOAT_VAL:
            return None
        return float(value) / previous

    # -- 写（对应 PHP sqldailyclose.php:143-213）----------------------------
    def _execute(self, sql: str, params: tuple = ()) -> Any:
        """执行一条写语句并提交，返回受影响行数。

        **这里没有事务可用**：本项目所有日频历史表都是 MyISAM
        （``stockhistory`` / ``netvaluehistory`` / ``calibrationhistory`` /
        ``fundest`` / ``shareshistory`` / ``futurepremium``），MyISAM 不支持
        事务，``commit()`` 只是空操作 —— 写完就落盘，写坏了回滚不回来。

        所以本类的写方法一律"先查后写"，不靠回滚兜底；批量改历史数据前
        先跑 ``dbtools/backup_db.py``。
        """
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            commit = getattr(self.connection, "commit", None)
            if commit is not None:
                commit()
            return cursor.rowcount
        finally:
            cursor.close()

    def _prepare_close(self, close: Any) -> Any:
        """把 ``close`` 规整成要绑定的参数（给子类覆写的钩子）。

        父类写的是 ``double`` 列，``None`` 给 ``0.0``：PHP 是把值直接拼进
        SQL（``sqltable.php:130`` 的 ``"'$strVal'"``），``null`` 会变成空串，
        MySQL 在非 strict 模式下把空串转成 ``0``。这里显式给 0，
        **不依赖服务器的 sql_mode**。``close`` 列是 VARCHAR 的表见
        :class:`DailyStringSql`。
        """
        if close is None:
            return 0.0
        return close

    def make_field_array(self, stock_id: Any, date: Any, close: Any) -> dict:
        """对应 PHP ``MakeFieldArray()``（``sqldailyclose.php:143``）：三列。

        单拎出来是因为 ``DailyTimeSql`` 那一路要在这三列上再加 ``time``
        （``sqldailytime.php:18``），子类覆写这一个方法比各写一遍可靠。
        """
        return {"stock_id": stock_id, "date": date, "close": self._prepare_close(close)}

    def insert_daily(self, stock_id: Any, date: Any, close: Any) -> bool:
        """``InsertDaily()``（``sqldailyclose.php:148``）。

        两道前置闸照抄：**日期为空不写**；**已有同日行不写**
        （``if ($this->GetRecord(...)) return false;``）—— 不是 upsert，
        重复插入交给 ``UNIQUE (date, stock_id)`` 抛异常，所以先查。

        PHP 的 ``InsertArray`` 会给没有 ``id`` 的数组补一列 ``id = '0'``
        （``sqltable.php:114-125``），对 AUTO_INCREMENT 主键来说等价于"让
        数据库分配"。这里不写 ``id``，效果相同。
        """
        if date is None or str(date) == "":
            return False
        if self.get_record(stock_id, date):
            return False
        fields = self.make_field_array(stock_id, date, close)
        names = ", ".join("`%s`" % name for name in fields)
        marks = ", ".join(["%s"] * len(fields))
        sql = "INSERT INTO `%s` (%s) VALUES (%s)" % (self.table, names, marks)
        return bool(self._execute(sql, tuple(fields.values())))

    def _update_by_id(self, record_id: Any, fields: dict) -> bool:
        """对应 PHP ``UpdateById($ar, $strId)``（``sqltable.php:165``）。

        按主键 id 改，``LIMIT 1`` 照抄（``UpdateArray($ar, $strWhere, '1')``）。
        ``fields`` 用 dict 传，是因为 ``DailyTimeSql`` / ``CalibrationSql``
        要在 ``close`` 之外再带 ``time`` / ``num``（``sqldailytime.php:48``）。
        """
        if record_id is None or str(record_id) == "":
            return False
        assignments = ", ".join("`%s` = %%s" % name for name in fields)
        sql = "UPDATE `%s` SET %s WHERE id = %%s LIMIT 1" % (self.table, assignments)
        return bool(self._execute(sql, tuple(fields.values()) + (record_id,)))

    def update_daily(self, record_id: Any, close: Any) -> bool:
        """``UpdateDaily($strId, $strClose)``（``sqldailyclose.php:156``）。

        只改 ``close`` 一列。带 ``time`` 的表（``calibrationhistory`` /
        ``fundest``）覆写本方法，见 :class:`DailyTimeSql`。
        """
        return self._update_by_id(record_id, {"close": self._prepare_close(close)})

    def delete_by_date(self, stock_id: Any, date: Any) -> int:
        """``DeleteByDate()``（``sqldailyclose.php:201``）：返回删除行数。

        PHP 返回 ``DeleteData()`` 的**行数**（它先 ``CountData`` 再删，
        ``sqltable.php:232``），不是布尔值 —— 这里同样给行数，路由的 flash
        文案要用。``WHERE`` 键是 ``(stock_id, date)``，靠 UNIQUE 保证最多一行。
        """
        return self._execute(
            "DELETE FROM `%s` WHERE stock_id = %%s AND `date` = %%s" % self.table,
            (stock_id, date),
        )

    def write_daily(self, stock_id: Any, date: Any, close: Any) -> bool:
        """``WriteDaily()``（``sqldailyclose.php:161``）：有行比数值，无行插入。

        分支顺序与判据都照抄：

        * **有行**：``abs(floatval(old) - floatval(new)) > MIN_FLOAT_VAL`` 才
          UPDATE。值没变返回 ``False``。注意这不是"已有就不动"—— 值变了要
          覆盖（校准值写错时靠这一支刷回来，见 pitfalls #95）；
        * **无行**：先过**周末闸门** ``if ($ymd->IsWeekend()) return false;``
          （源码注释写明是防新浪偶尔送来的周末假数据），再 ``InsertDaily``。

        返回 ``True`` 表示真的写了库。PHP 无返回值，这是为路由/CLI 的
        "写了几条"加的口径。``DailyStringSql`` 会覆写本方法
        （字符串比较 + **没有**周末闸门）。
        """
        record = self.get_record(stock_id, date)
        if record:
            diff = abs(_to_float(record.get("close")) - _to_float(close))
            if diff > MIN_FLOAT_VAL:
                return self.update_daily(record.get("id"), close)
            return False
        if _is_weekend(date):
            return False
        return self.insert_daily(stock_id, date, close)

    def write_daily_array(self, stock_id: Any, values: dict) -> int:
        """``WriteDailyArray()``（``sqldailyclose.php:181``）：逐日写一批。

        PHP 是 ``foreach ($ar as $strDate => $strClose) $this->WriteDaily(...)``，
        无返回值。这里返回**真正写进库的条数**（值未变/被周末闸门拦下的不算），
        便于回填脚本报"补了几天"。
        """
        written = 0
        for date, close in values.items():
            if self.write_daily(stock_id, date, close):
                written += 1
        return written

    def delete_close(self, close: Any = "0.000000", *, dry_run: bool = True) -> int:
        """``DeleteClose()``（``sqldailyclose.php:210``）：删掉 ``close`` 等于
        该值的所有行，返回行数。

        **默认 dry-run**：这是一条**不带 stock_id 的全表 DELETE**，PHP 侧唯一
        的调用点是调试脚本 ``php/test.php:129``（同一个文件第 132 行还自己写着
        ``DeleteInvalidDate()`` "can be very slow"）。真删要显式
        ``dry_run=False`` —— MyISAM 删了没有回滚。
        """
        params = (self._prepare_close(close),)
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM `%s` WHERE `close` = %%s" % self.table, params
        )
        count = int(row["n"]) if row else 0
        if dry_run or count == 0:
            return count
        self._execute("DELETE FROM `%s` WHERE `close` = %%s" % self.table, params)
        return count

    def is_invalid_date(self, record: Any, today: Any = None) -> bool:
        """``IsInvalidDate()``（``sqldailyclose.php:189``）：这行日期该不该清掉。

        PHP 原文是两个判定取或：

        * ``OldestYMD::IsTooOld()`` —— 早于 **2014-01-01**（``OldestYMD``
          的构造参数，``php/class/year_month_day.php:160``）；
        * ``OldestYMD::IsInvalid()`` —— 周末，或者**未来**日期。

        ``record`` 收 dict（取 ``record["date"]``）或直接的日期串。

        **一处刻意偏离**：日期解析不出来时判 ``True``（当作脏行报出来）。
        PHP 那边 ``StringYMD`` 遇到 ``explode('-')`` 不是三段会退回 ``time()``
        —— 即"当作今天"，于是非法串反而**不算** invalid。真实表里 ``date`` 是
        ``date`` 列，存不进畸形值，所以这只影响调用方手搓的输入；清理场景下
        宁可报出来也别悄悄放过。
        """
        value = record.get("date") if isinstance(record, dict) else record
        moment = _parse_ymd(value)
        if moment is None:
            return True
        reference = today or datetime.date.today()
        if moment < OLDEST_VALID_DATE:
            return True
        if moment.weekday() >= 5:
            return True
        return moment > reference

    def find_invalid_records(self, today: Any = None) -> list:
        """全表扫一遍，返回 ``[{"id": ..., "date": ...}]``（对应 ``DeleteInvalid``
        里那段 ``GetAll()`` + 回调过滤，``sqltable.php:248``）。

        PHP 的 ``GetAll()`` 不带 ``stock_id`` 就是全表；这里同样全表，**只读**。
        """
        rows = self._fetchall("SELECT id, `date` FROM `%s`" % self.table)
        return [row for row in rows if self.is_invalid_date(row, today)]

    def delete_invalid_dates(self, *, dry_run: bool = True) -> int:
        """``DeleteInvalidDate()``（``sqldailyclose.php:196``）：清掉脏日期行。

        **默认 dry-run**，返回命中的条数、不动数据。原因同
        :meth:`delete_close`：全表扫描 + 逐行删，PHP 侧只在 ``php/test.php``
        里用到，而且 MyISAM 删了回不来。先
        ``find_invalid_records()`` 看看命中什么再决定。
        """
        records = self.find_invalid_records()
        if dry_run or not records:
            return len(records)
        for record in records:
            self._execute(
                "DELETE FROM `%s` WHERE id = %%s LIMIT 1" % self.table, (record["id"],)
            )
        return len(records)


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

    def write_history(
        self, stock_id: Any, date: Any, close: Any, volume: Any = "", adjclose: Any = False
    ) -> bool:
        """``WriteHistory()``（``php/sql/sqlstocksymbol.php:27``）：行情三列一起写。

        **``stockhistory`` 不能用父类的 ``insert_daily`` / ``write_daily``**：
        它比日频表多 ``volume`` 与 ``adjclose``，两列都 ``NOT NULL`` 且无默认值，
        只给 ``(stock_id, date, close)`` 会撞 ``1364 Field doesn't have a default``。
        PHP 那边同样是另起一个方法（不覆写 ``WriteDaily``），这里保持一致。

        三个判据都与父类不同，照抄不动：

        * ``close`` 的容差是**硬编码 0.0005**，不是 ``MIN_FLOAT_VAL`` ——
          ``close`` 列是 ``double(10,3)``，第三位以外的抖动无意义；
        * ``volume`` 用 ``==`` 宽松比较（PHP 8 的两个数字串按数值比），
          由 :func:`php_text_equal` 复刻；
        * **没有周末闸门**（历史行情抓回来就是权威数据，不再二次过滤）。

        返回 ``True`` 表示真的发了写语句；三列都没变返回 ``False``
        （PHP 里 ``count($ar) > 0`` 不成立，直接 ``return false``）。
        """
        #: PHP ``if ($strAdjClose == false) $strAdjClose = $strClose;``
        #: —— ``''`` 与 ``0`` 在 PHP 里都 `== false`，所以用 falsy 判断。
        if not adjclose:
            adjclose = close
        record = self.get_record(stock_id, date)
        if record:
            fields: dict = {}
            if abs(_to_float(record.get("close")) - _to_float(close)) >= 0.0005:
                fields["close"] = close
            if not php_text_equal(record.get("volume"), volume):
                fields["volume"] = 0 if volume is None or volume == "" else volume
            if abs(_to_float(record.get("adjclose")) - _to_float(adjclose)) >= MIN_FLOAT_VAL:
                fields["adjclose"] = adjclose
            if not fields:
                return False
            return self._update_by_id(record.get("id"), fields)
        return bool(
            self._execute(
                "INSERT INTO `stockhistory` (stock_id, `date`, `close`, `volume`, `adjclose`) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    stock_id,
                    date,
                    self._prepare_close(close),
                    0 if volume is None or volume == "" else volume,
                    adjclose,
                ),
            )
        )


class DailyTimeSql(DailyCloseSql):
    """带 ``time`` 列的日频表（``calibrationhistory`` / ``fundest``）。

    对应 PHP ``DailyTimeSql``（``php/sql/sqldailytime.php:4``）。写方法和父类的
    差别只有一处：INSERT 的字段和 UPDATE 的 SET 子句都要多带一个 ``time``
    （``DebugGetTime()``）。``calibrationhistory`` 还多一个 ``num`` 列
    （当日取样次数），见 :class:`CalibrationSql`。
    """

    def __init__(self, connection: Any, table: str, extra: Sequence[str] = ()):
        columns = ("id", "stock_id", "date", "close", "time") + tuple(extra)
        super().__init__(connection, table, columns=columns)

    def count(self, stock_id: Any) -> int:
        return super().count(stock_id)

    @staticmethod
    def now_time() -> str:
        """``DebugGetTime()``（``php/debug.php:107``）：``Asia/Shanghai`` 的 ``H:i:s``。

        必须显式按上海时区算，不能用 ``datetime.now()``：数据库里的 ``time``
        列按 **``DEBUG_TIME_ZONE`` = Asia/Shanghai** 记录（``php/debug.php:4``），
        换台机器（或 CI 的 UTC 容器）就会写出差 8 小时的 ``time``。
        3.7 没有 ``zoneinfo``，用固定 +8 偏移 —— 上海没有夏令时，等价。
        """
        utc_now = datetime.datetime.utcnow()
        return (utc_now + datetime.timedelta(hours=8)).strftime("%H:%M:%S")

    def make_field_array(self, stock_id: Any, date: Any, close: Any) -> dict:
        """``MakeFieldArray()`` + ``time``（``sqldailytime.php:18-21``）。"""
        fields = super().make_field_array(stock_id, date, close)
        fields["time"] = self.now_time()
        return fields

    def update_daily(self, record_id: Any, close: Any) -> bool:
        """``UpdateDaily()`` 覆写（``sqldailytime.php:24-28``）：``close`` + ``time``。"""
        return self._update_by_id(
            record_id, {"close": self._prepare_close(close), "time": self.now_time()}
        )


class CalibrationSql(DailyTimeSql):
    """``calibrationhistory`` 表（含 ``num`` 列）。

    对应 PHP ``CalibrationSql``（``php/sql/sqldailytime.php:31``）。除了 ``time``，
    写方法还要管 ``num``（当日取样次数）：

    * ``InsertDaily`` 把 ``num`` 置 ``1``；
    * ``UpdateDaily`` 转到 ``UpdateDailyAverage($id, $close)``，即 **``num`` 重置
      为 1**（不是累加 —— 默认参数就是 1，见 ``sqldailytime.php:51``）；
    * ``WriteDailyAverage`` 才是**累加平均**：``close' = (num*old + new) / (num+1)``，
      ``num' = num + 1``。
    """

    def __init__(self, connection: Any):
        super().__init__(connection, "calibrationhistory", extra=("num",))

    def make_field_array(self, stock_id: Any, date: Any, close: Any) -> dict:
        """``MakeFieldArray()`` + ``time`` + ``num = 1``（``sqldailytime.php:40-46``）。"""
        fields = super().make_field_array(stock_id, date, close)
        fields["num"] = 1
        return fields

    def update_daily(self, record_id: Any, close: Any) -> bool:
        """``UpdateDaily()`` → ``UpdateDailyAverage($id, $close)``，即 ``num = 1``。"""
        return self.update_daily_average(record_id, close, 1)

    def update_daily_average(self, record_id: Any, close: Any, num: Any = 1) -> bool:
        """``UpdateDailyAverage($strId, $strClose, $iNum = 1)``
        （``sqldailytime.php:56``）：``close`` + ``time`` + ``num``。"""
        return self._update_by_id(
            record_id,
            {
                "close": self._prepare_close(close),
                "time": self.now_time(),
                "num": 1 if num is None else int(_to_float(num)),
            },
        )

    def write_daily_average(self, stock_id: Any, date: Any, close: Any) -> bool:
        """``WriteDailyAverage()``（``sqldailytime.php:65``）：按样本数累加平均。

        与 :meth:`write_daily` 的两处差别，都照抄：

        * 有行且值有变时算 ``(num*old + new) / (num+1)`` 并把 ``num`` 加一 ——
          这样多跑几次校准不会把同一个样本重复计数，而是取平均；
        * 无行时**没有周末闸门**。PHP 那三行是被注释掉的，注释写着
          "hf_CHA50CFD calibration may in Sunday, which is Monday for SH000300"
          —— 期货的"周日"在这边可能是标的的"周一"，挡掉就漏了。
        """
        record = self.get_record(stock_id, date)
        if record:
            old_avg = _to_float(record.get("close"))
            value = _to_float(close)
            if abs(old_avg - value) > MIN_FLOAT_VAL:
                num = int(_to_float(record.get("num")))
                total = num * old_avg + value
                num += 1
                return self.update_daily_average(record.get("id"), total / num, num)
            return False
        return self.insert_daily(stock_id, date, close)


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


#: PHP 8 认定的"数字字符串"：允许前后空白、正负号、小数点（``.5``）、指数。
#: 注意 PHP 8 比 PHP 7 严格 —— ``'abc'`` / ``'12abc'`` 都不算，而
#: ``' 12 '`` 算（两侧空白自 PHP 8 起被容忍）。
_NUMERIC_STRING_RE = re.compile(r"^\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*$")


def php_text_equal(left: Any, right: Any) -> bool:
    """PHP 8 的 ``$a == $b``，**两个操作数都是字符串**时的语义。

    用在 :meth:`DailyStringSql.write_daily` 里替代 PHP 的
    ``if ($record['close'] != $strClose)``。PHP 的规则是：

    * 两个**数字字符串** → 按数值比（``'100' == '100.00'`` 为真，
      ``'0' == '0.0'`` 为真）；
    * 否则 → 按字节比（``'{"GLD":100}' == '{"GLD":100.01}'`` 为假）。

    为什么要复刻这个双轨：季报的 ``close`` 列两种内容都有 —— 管理员手填的
    百分比数字串，以及 ``"GLD":100`` 那种编码串（外层不带花括号，见
    ``DebugEncode``）。如果一律按字节比，
    ``'100.00'`` 改 ``'100'`` 会白白 UPDATE 一次（php 那边不会）；如果一律
    按数值比，非数字串会被 ``floatval`` 成 0 而**永远相等** —— 症状是
    "改了季报却存不进去"，且不报错。

    ``None`` 按空串处理（PHP 的 ``null == ''`` 为真）。
    """
    a = "" if left is None else str(left)
    b = "" if right is None else str(right)
    if _NUMERIC_STRING_RE.match(a) and _NUMERIC_STRING_RE.match(b):
        return float(a) == float(b)
    return a == b


class DailyStringSql(DailyCloseSql):
    """``close`` 列存**字符串**的日频表。对应 PHP ``DailyStringSql``
    （``php/sql/sqldailystring.php:4``）。

    与父类的差异只有两处，都是**照抄不动**：

    * :meth:`write_daily` 的**比较方式**。父类（PHP ``sqldailyclose.php:161``）是

      .. code-block:: php

          if (abs(floatval($record['close']) - floatval($strClose)) > MIN_FLOAT_VAL)

      而 String 版（``sqldailystring.php:16``）是 ::

          if ($record['close'] != $strClose)

      ``'100.00'`` 与 ``'100'`` 在 PHP 8 里是数值相等的数字串 → 视为相同、
      不写库；``'{"GLD":100}'`` 与 ``'{"GLD":100.01}'`` 不是数字串 → 按字节
      比较。这个双轨语义由 :func:`php_text_equal` 复刻。
    * **没有周末闸门**。父类在 INSERT 分支有
      ``if ($ymd->IsWeekend()) return false;``（新浪偶尔给周末的假数据），
      String 版没有 —— 季报日期本来就可能是任意一天（季末），不能挡。

    ``close`` 是 VARCHAR，所以 :meth:`_prepare_close` 把值字符串化，
    ``insert_daily`` / ``update_daily`` / ``delete_by_date`` 全部沿用父类。
    """

    # -- 写 ---------------------------------------------------------------
    def _prepare_close(self, close: Any) -> Any:
        """``close`` 是 VARCHAR：``None`` 写空串（PHP ``null`` → ``''``）。"""
        return "" if close is None else str(close)

    def write_daily(self, stock_id: Any, date: Any, close: Any) -> bool:
        """``WriteDaily()``（``sqldailystring.php:16``）：有行比字符串，无行插入。

        返回 ``True`` 表示真的写了库（PHP 无返回值，这是为路由 flash 加的口径）。
        **值相同返回 ``False``** —— 与 PHP 的 ``return false;`` 同义。
        """
        record = self.get_record(stock_id, date)
        if record:
            if php_text_equal(record.get("close"), close):
                return False
            return self.update_daily(record.get("id"), close)
        return self.insert_daily(stock_id, date, close)


class QuarterReportSql(DailyStringSql):
    """``quarterreport`` 表：基金季报披露的持仓明细。

    对应 PHP ``QuarterReportSql``（``php/sql/sqldailystring.php:33``），
    而它自己是 ``DailyStringSql`` 的**空壳**——只定表名，方法全在基类里
    （``GetAll`` / ``GetClose`` / ``GetRecord`` / ``WriteDaily``）。所以这里
    同样只定表名，写方法继承自 :class:`DailyStringSql`。

    ``close`` 列存的是**字符串**不是数字：内容是
    ``{"GLD":50,"SPY":50}`` 这一族的持仓编码串（季报里各成分股的百分比），
    由 :func:`updater.holdingsupdater.stock_option_decode_holdings` 解回比例。
    这也是 ``DailyStringSql`` 存在的唯一理由 —— 表结构一样，只是 ``close``
    的 VARCHAR 语义（比较两个字符串是否相等来决定 update 还是 insert）。

    写入口见 ``/holdings/quarter/<symbol>``（对应 PHP
    ``editquarterreportcn.php`` + ``submitstockoptions.php`` 的 REPORT 分支）。
    """

    def __init__(self, connection: Any):
        super().__init__(connection, "quarterreport")


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
