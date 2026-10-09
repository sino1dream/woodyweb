"""股票/基金实时行情引用层。

对应 PHP 源码：``php/stock/stockref.php``（``StockReference``、
``ExtendedTradingReference``），以及它依赖的若干纯函数：

* ``php/debug.php`` 的 ``rtrim0()``、``IsZeroString()``、``GetHM()``、
  ``DebugGetDate()``、``DebugGetTime()``；
* ``php/stock.php`` 的 ``explodeQuote()``、``RemoveDoubleQuotationMarks()``、
  ``StockGetPercentage()``。

本模块只负责「把新浪行情字符串解析成结构化数据」这一层，刻意不做两件事：

1. **不生成 HTML**。PHP 的 ``GetPercentageDisplay()`` / ``GetPriceDisplay()``
   会返回带颜色的 ``<font>`` 片段，属于渲染层，留给 ``php/ui/*`` 的迁移步骤。
   这里提供 ``get_percentage_text()`` 和 ``format_price()`` 这两个不含标记的
   文本版本，渲染时再包标签。
2. **不直接访问数据库和缓存文件**。行情文本的获取（``LoadSinaData()`` 里对
   ``PrefetchLoadSinaData()`` 的调用）改为注入 ``quote_loader`` 回调，
   文件命名规则由 ``sina_quote_file_name()`` 提供，让解析逻辑可以独立测试。

时间处理说明：PHP 用 ``date_default_timezone_set()`` 改进程默认时区。Python
没有这个概念，所以时区换算统一走 ``tzutil``：有 ``zoneinfo``（Python 3.9+ 标准库，
或已安装 ``tzdata``）时用真实时区库，没有时回退到带夏令时规则的固定偏移实现，
因此 Python 3.7 上也能得到正确结果，不会静默按本地时区算错。
"""

from __future__ import annotations

import datetime as _datetime
import os
from typing import Any, Callable, Mapping, Sequence

from stocksymbol import StockSymbol
import tzutil

# ---------------------------------------------------------------------------
# 常量（对应 PHP define()）
# ---------------------------------------------------------------------------
SECONDS_IN_MIN = 60
SECONDS_IN_HOUR = 3600
SECONDS_IN_DAY = 86400

# 13.6 在 MySQL 里的表示，PHP debug.php 用它判断“是不是零”
MIN_FLOAT_VAL = 0.0000005
FLOAT_PRECISION = 6

TIME_ZONE_SHANGHAI = "Asia/Shanghai"
TIME_ZONE_US = "America/New_York"

# PHP 符号文件名里会被替换掉的字符（见 php/debug.php DebugGetSymbolFile()）
_UNSAFE_FILE_NAME_CHARS = ("/", "+", ",", "^", ".", ":", "%")


# ---------------------------------------------------------------------------
# 数字与字符串格式化（对应 php/debug.php 与 php/stock.php）
# ---------------------------------------------------------------------------
def _php_strval_float(value: float) -> str:
    """把 float 转成 PHP ``strval()`` 的字符串形式。

    PHP 的 ``strval(float)`` 用 ``precision`` ini 设置（默认 14）格式化，
    因此是**14 位有效数字**而不是 Python ``repr()`` 的 17 位。例如::

        PHP:    strval(0.1234567890123456) === '0.12345678901235'
        Python: repr(0.1234567890123456)   ==  '0.1234567890123456'

    差异会直接反映到 ``GetPrice()`` 的返回值上，所以这里显式按 14 位有效数字
    重新格式化，并把指数写法统一成 PHP 的 ``1.0E-7`` 形式（大写 E、无前导零）。
    """
    if value != value:
        # PHP 对 NAN 输出 'NAN'
        return "NAN"
    if value == float("inf"):
        return "INF"
    if value == float("-inf"):
        return "-INF"

    shortest = repr(value)
    # 用 repr() 的最短往返表示来数有效数字，避免 '%.14G' 把 0.1 这类本来
    # 只有一位有效数字的值写成 '0.10000000000000'
    digits = shortest.split("e")[0].lstrip("-").replace(".", "").lstrip("0")
    if "e" not in shortest and len(digits) < 14:
        text = shortest
    else:
        text = "%.14G" % value

    if "E" in text or "e" in text:
        mantissa, exponent = text.replace("E", "e").split("e")
        # 负数尾数是 '-1'，isdigit() 为假，所以按绝对值判断；PHP 写的是
        # '1.0E-7' / '-1.0E-7'，尾数必须带 '.0'
        if mantissa.lstrip("-").isdigit():
            mantissa += ".0"
        # 指数按整数重新格式化：PHP 写 '1.0E-7'，而 %.14G 产出的是 '1E-07'，
        # 注意 lstrip('+-') 去不掉前导零，必须转成 int 再拼
        return "%sE%s%d" % (mantissa, "-" if exponent.startswith("-") else "+", abs(int(exponent)))

    # PHP 的 strval() 不给浮点数补 '.0'：strval(3.0) === '3'
    if text.endswith(".0"):
        text = text[:-2]
    if text == "-0":
        return "-0"
    return text


def _php_floatval(value: Any) -> float:
    """对应 PHP ``floatval()``：无法解析的输入一律得到 0.0。

    Python 的 ``float()`` 会抛 ``ValueError``，而 PHP 的 ``floatval('')``、
    ``floatval('abc')``、``floatval('1,234')`` 都是 0.0（只解析开头合法的数字
    部分，解析不到就是 0）。行情文件里偶尔会有空字段，所以必须保持这种宽松语义。
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    # 先尝试直接解析失败后的「前缀数字」形式，例如 '1,234' -> 1
    text = str(value).strip()
    for length in range(len(text), 0, -1):
        try:
            return float(text[:length])
        except ValueError:
            continue
    return 0.0


def rtrim0(value: Any) -> str:
    """对应 PHP ``rtrim0()``：``strval(floatval($str))``。

    名字叫 rtrim0，但 PHP 实现是用 ``strval(floatval())`` 去掉尾部多余的零，
    所以这里也走 float 再格式化，而不是简单地去掉字符 '0'（否则 '100' 会变成 '1'）。
    """
    return _php_strval_float(_php_floatval(value))


def is_zero_string(value: Any) -> bool:
    """对应 PHP ``IsZeroString()``：绝对值小于 ``MIN_FLOAT_VAL`` 即为零。

    PHP 的 ``floatval('')``、``floatval('abc')`` 都是 0.0，因此这里也把无法
    解析的输入当作零处理，保持与 PHP 一致。
    """
    return abs(_php_floatval(value)) < MIN_FLOAT_VAL


def get_hm(time_hms: Any) -> str:
    """对应 PHP ``GetHM()``：``substr($strHMS, 0, 5)``，'06:56:22' -> '06:56'。"""
    if time_hms is None:
        return ""
    return str(time_hms)[0:5]


def stock_get_percentage(divisor: float, dividend: float) -> float:
    """对应 PHP ``StockGetPercentage()``：涨跌幅百分比。

    除数绝对值小于 ``MIN_FLOAT_VAL`` 时 PHP 直接返回 0.0，避免除零。
    """
    if abs(divisor) > MIN_FLOAT_VAL:
        return (dividend / divisor - 1.0) * 100.0
    return 0.0


def remove_double_quotation_marks(text: str) -> str:
    """对应 PHP ``RemoveDoubleQuotationMarks()``。

    新浪返回的是 ``var hq_str_gb_xop="XOP,1.23,...";`` 这种整行，需要先截取
    两个双引号之间的内容。PHP 的 ``strstr($str, '"')`` 返回从第一个引号开始的
    子串（没有引号时返回 false，即原字符串原样返回），这里保持同样行为。
    """
    start = text.find('"')
    if start < 0:
        return text
    rest = text[start:].lstrip('"')
    end = rest.find('"')
    if end >= 0:
        return rest[:end]
    return rest


def explode_quote(text: str) -> list:
    """对应 PHP ``explodeQuote()``：去掉外层引号后按逗号切分。"""
    return remove_double_quotation_marks(text).split(",")


def format_price(price: float, prev_price: float, precision: int) -> str:
    """对应 PHP ``StockGetPriceDisplay()`` 的文本部分（不含颜色标签）。

    PHP 用 ``number_format($fDisp, $iPrecision)`` 后去掉尾部零和小数点；
    ``$fDiff`` 阈值只用于决定颜色，颜色属于渲染层，这里不处理。

    兼容性注意：PHP 的 ``sprintf('%.2f', -0.0)`` 会规范化成 ``'0.00'``，
    而 Python 的 ``'%.2f' % -0.0`` 得到 ``'-0.00'``。价格恰好是负零时（跌到零
    或浮点误差产生 -0.0）两者会显示成不同文本，所以这里显式去掉负号。
    """
    if price == 0.0:
        price = 0.0  # 把 -0.0 归一成 0.0
    text = "%.*f" % (precision, price)
    if precision > 0:
        text = text.rstrip("0")
    return text.rstrip(".")


def _sina_symbol_file_stem(symbol: str) -> str:
    """对应 PHP ``DebugGetSymbolFile()`` 的文件名拼装规则。"""
    stem = symbol.lower()
    for char in _UNSAFE_FILE_NAME_CHARS:
        stem = stem.replace(char, "_")
    return stem


def sina_quote_file_name(symbol: str, root: str = "debug", section: str = "sina") -> str:
    """对应 ``DebugGetSinaFileName()``：``debug/sina/<符号>.txt``。

    PHP 里这些路径基于站点根目录推导；Web 化之后由调用方给出 ``root``，
    默认沿用 PHP 的 ``debug`` 目录名。
    """
    return os.path.join(root, section, _sina_symbol_file_stem(symbol) + ".txt")


# ---------------------------------------------------------------------------
# 时间与时区
# ---------------------------------------------------------------------------
def _parse_datetime(date_time: str, time_zone_name: str) -> _datetime.datetime:
    """按指定时区解析时间字符串。

    覆盖新浪实际返回的几种形态：

    * ``2026-06-08 15:00:00`` —— A 股/基金
    * ``2016/03/02 10:30:00`` —— 港股（日期用斜杠）
    * ``Jun 08 11:36AM EDT`` —— 美股 ``$ar[24]`` 盘前/盘后时间（不带年份，
      带美东时区缩写）

    PHP 用 ``strtotime()`` 通吃这些格式，Python 的 ``strptime`` 不认识月份英文
    缩写 + AM/PM + 时区缩写，所以最后一种单独处理。
    """
    text = str(date_time).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return tzutil.localize(_datetime.datetime.strptime(text, fmt), time_zone_name)
        except ValueError:
            continue

    parsed = _parse_us_style_datetime(str(date_time).strip())
    if parsed is not None:
        return tzutil.localize(parsed, time_zone_name)

    raise ValueError("无法解析时间字符串：%r" % (date_time,))


# 美股盘前/盘后时间：'Jun 08 11:36AM EDT'
_US_TIME_PATTERN = None


def _parse_us_style_datetime(text: str):
    """解析 ``'Jun 08 11:36AM EDT'`` 这类不带年份的美股时间。

    年份缺失是新浪的格式决定的：调用方会用行情里的 ``$ar[29]`` 补年份，
    所以这里用当前年份占位，月日时分本身不受影响。
    """
    global _US_TIME_PATTERN
    if _US_TIME_PATTERN is None:
        import re

        _US_TIME_PATTERN = re.compile(
            r"^(?P<month>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+"
            r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)"
            r"(?:\s+(?P<tz>[A-Z]{2,4}))?$",
            re.IGNORECASE,
        )
    match = _US_TIME_PATTERN.match(text)
    if match is None:
        return None

    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    month = months.get(match.group("month").lower())
    if month is None:
        return None

    hour = int(match.group("hour"))
    if match.group("ampm").upper() == "PM" and hour != 12:
        hour += 12
    elif match.group("ampm").upper() == "AM" and hour == 12:
        hour = 0

    return _datetime.datetime(
        _datetime.datetime.now().year,
        month,
        int(match.group("day")),
        hour,
        int(match.group("minute")),
    )


def format_date(moment: _datetime.datetime, time_zone_name: str) -> str:
    """对应 PHP ``DebugGetDate()``：按目标时区输出 ``Y-m-d``。"""
    return moment.astimezone(tzutil.get_time_zone(time_zone_name)).strftime("%Y-%m-%d")


def format_time(moment: _datetime.datetime, time_zone_name: str) -> str:
    """对应 PHP ``DebugGetTime()``：按目标时区输出 ``H:i:s``。"""
    return moment.astimezone(tzutil.get_time_zone(time_zone_name)).strftime("%H:%M:%S")


def convert_date_time(date_time: str, from_time_zone: str, to_time_zone: str):
    """把 ``date_time`` 从 ``from_time_zone`` 换到 ``to_time_zone``。

    返回 ``(日期, 时间)``，对应 PHP 里成对出现的 ``DebugGetDate()`` /
    ``DebugGetTime()``。
    """
    moment = _parse_datetime(date_time, from_time_zone)
    return format_date(moment, to_time_zone), format_time(moment, to_time_zone)


def _item(array: Sequence, index: int) -> Any:
    """按下标取值，越界返回 None。

    PHP 访问不存在的数组下标会得到 null 且不报错；Python 会抛 IndexError。
    解析外部行情时必须保持“缺字段就是 None”的宽松行为。
    """
    if index < 0 or index >= len(array):
        return None
    return array[index]


# ---------------------------------------------------------------------------
# StockReference 类
# ---------------------------------------------------------------------------
class StockReference(StockSymbol):
    """对应 PHP ``php/stock/stockref.php`` 的 ``StockReference``。

    构造时只需要符号；行情数据的填充通过 ``load_sina_data()`` 等方法完成。
    ``quote_loader`` 用于注入“取行情原始文本”的回调（对应 PHP 的
    ``PrefetchLoadSinaData()``），不传则表示没有缓存可用。
    """

    def __init__(self, symbol: str, quote_loader: Callable[[str], Any] = None):
        super().__init__(symbol)

        self.quote_loader = quote_loader
        # PHP 里由 DebugGetConfigFileName() 生成，纯逻辑层不做路径拼接
        self.config_name = sina_quote_file_name(symbol, section="config")
        self.file_name = None
        self.external_link = ""

        # 原始行情字段
        self.str_price = None
        self.str_prev_price = None
        self.str_date = None
        self.str_time = None

        self.str_name = ""
        self.str_chinese_name = None

        self.str_open = None
        self.str_high = None
        self.str_low = None
        self.str_volume = "0"

        self.str_settle_price = None
        self.str_vwap = None

        self.bid_prices = []
        self.bid_quantities = []
        self.ask_prices = []
        self.ask_quantities = []

        self.has_data = True
        # 美股盘前/盘后交易引用，对应 PHP 的 extended_ref
        self.extended_ref = None

    # -- 数据状态 ---------------------------------------------------------
    def has_quote_data(self) -> bool:
        """对应 PHP ``HasData()``。

        注意不能叫 ``has_data()``：那会和下面保存状态的 ``has_data`` 属性重名。
        """
        return self.has_data

    def set_has_data(self, value: bool = True) -> None:
        """对应 PHP ``SetHasData()``。"""
        self.has_data = value

    # -- 价格与量 ---------------------------------------------------------
    def get_price(self) -> str:
        """对应 PHP ``GetPrice()``：当前价，去掉尾部零后返回字符串。"""
        return rtrim0(self.str_price)

    def get_prev_price(self) -> str:
        """对应 PHP ``GetPrevPrice()``：昨收价。"""
        return rtrim0(self.str_prev_price)

    def get_volume(self) -> str:
        """对应 PHP ``GetVolume()``：成交量。"""
        return self.str_volume

    def get_ask_price(self, index: int = 0) -> Any:
        """对应 PHP ``GetAskPrice()``：卖价，缺省第一档。"""
        return _item(self.ask_prices, index)

    def get_bid_price(self, index: int = 0) -> Any:
        """对应 PHP ``GetBidPrice()``：买价，缺省第一档。"""
        return _item(self.bid_prices, index)

    def get_ask_quantity(self, index: int = 0) -> Any:
        """对应 PHP ``GetAskQuantity()``：卖量，缺省档位缺失时返回 ``False``。"""
        return _item(self.ask_quantities, index)

    def get_bid_quantity(self, index: int = 0) -> Any:
        """对应 PHP ``GetBidQuantity()``：买量，缺省档位缺失时返回 ``False``。"""
        return _item(self.bid_quantities, index)

    def get_available_quantity(self, is_sell: bool, index: int = 0) -> Any:
        """对应 PHP ``GetAvailableQuantity()``。

        PHP 里 ``if ($strQuantity = ...)`` 会把空字符串也当成“没有”，这里用
        ``is not None`` 更严格；行情里的量都是非空字符串，行为一致。
        """
        if is_sell:
            return self.get_bid_quantity(index)
        return self.get_ask_quantity(index)

    def get_available_price(self, is_sell: bool, index: int = 0) -> Any:
        """对应 PHP ``GetAvailablePrice()``：卖出看买价，买入看卖价。"""
        return self.get_bid_price(index) if is_sell else self.get_ask_price(index)

    # -- 外部链接 ---------------------------------------------------------
    def get_external_link(self) -> str:
        """对应 PHP ``GetExternalLink()``。"""
        return self.external_link

    def set_external_link(self, link: str) -> None:
        """对应 PHP ``SetExternalLink()``。"""
        self.external_link = link

    # -- 涨跌幅 -----------------------------------------------------------
    def get_percentage(self, divisor: float = None, dividend: float = None) -> float:
        """对应 PHP ``GetPercentage()``：默认用昨收/现价计算涨跌幅。"""
        if divisor is None:
            divisor = float(self.str_prev_price)
        if dividend is None:
            dividend = float(self.str_price)
        return stock_get_percentage(divisor, dividend)

    def get_percentage_string(self, divisor: float = None, dividend: float = None) -> str:
        """对应 PHP ``GetPercentageString()``。

        涨跌绝对值小于「半个最小价格单位」时归零，避免把浮点噪声显示成涨跌。
        """
        percentage = self.get_percentage(divisor, dividend)
        if divisor is None:
            divisor = float(self.str_prev_price)
        if abs(percentage * divisor) < (50.0 / pow(10, self.get_precision())):
            return "0"
        return str(percentage)

    def get_percentage_text(self, divisor: float = None, dividend: float = None) -> str:
        """对应 PHP ``GetPercentageText()``：两位小数、去掉多余零、带 % 号。

        PHP 的分支判断是 ``$str != '' && $str != '0'``，此时 ``$str`` 还是
        ``GetPercentageString()`` 的结果、尚未做 ``number_format``。因为 PHP 对数字
        字符串用数值比较，``'-0'``、``'0.00'`` 都等于 0，所以这里用数值判零。
        """
        text = self.get_percentage_string(divisor, dividend)
        if text != "" and _php_floatval(text) != 0.0:
            text = "%.2f" % float(text)
            text = text.rstrip("0").rstrip(".")
            text += "%"
        return text

    # -- 价格显示 ---------------------------------------------------------
    def get_price_display(self, disp: float = None, prev: float = None, precision: int = None) -> str:
        """对应 PHP ``GetPriceDisplay()`` 的文本部分（不含颜色标签）。

        PHP 的三个参数默认值都是 ``false``，语义是“没传就用现价/昨收”；
        这里用 ``None`` 表达同一件事。
        """
        if disp is None:
            disp = float(self.str_price)
            prev = float(self.str_prev_price)
        elif prev is None:
            prev = float(self.str_price)
        if not precision:
            precision = self.get_precision()
        return format_price(disp, prev, precision)

    # -- 日期时间 ---------------------------------------------------------
    def debug_link(self) -> str:
        """对应 PHP ``DebugLink()``：调试文件链接，纯逻辑层返回文件名。"""
        return self.file_name

    def debug_config_link(self) -> str:
        """对应 PHP ``DebugConfigLink()``。"""
        return self.config_name

    def get_date(self) -> str:
        """对应 PHP ``GetDate()``：行情日期 ``Y-m-d``。"""
        return self.str_date

    def get_time(self) -> str:
        """对应 PHP ``GetTime()``：行情时间 ``H:i:s``。"""
        return self.str_time

    def get_date_time(self) -> str:
        """对应 PHP ``GetDateTime()``：日期和时间拼接。"""
        return "%s %s" % (self.str_date, self.str_time)

    def set_time(self, time_text: str) -> None:
        """对应 PHP ``SetTime()``。"""
        self.str_time = time_text

    def get_time_hm(self) -> str:
        """对应 PHP ``GetTimeHM()``：``06:56:22`` -> ``06:56``。"""
        return get_hm(self.str_time)

    def get_hour(self) -> int:
        """对应 PHP ``GetHour()``：小时部分。"""
        return int(str(self.str_time)[0:2])

    def get_minute(self) -> int:
        """对应 PHP ``GetMinute()``：``substr(GetTimeHM(), -2, 2)`` 取分钟两位。

        ``GetTimeHM()`` 已经是 ``HH:MM``，所以取第 3、4 个字符。PHP 里写成
        ``-2`` 是因为它从字符串末尾数，效果一样；Python 写 ``[-2:2]`` 会得到
        空串，必须显式取 ``[3:5]``。
        """
        return int(self.get_time_hm()[3:5])

    def get_hour_minute(self) -> int:
        """对应 PHP ``GetHourMinute()``：``13:05`` -> ``1305``。"""
        return int(self.get_time_hm().replace(":", ""))

    def convert_tick(self) -> int:
        """对应 PHP ``ConvertTick()``：按本符号的时区把日期时间转成 Unix 时间戳。"""
        moment = _parse_datetime(self.get_date_time(), self.get_time_zone())
        return int(moment.timestamp())

    def is_extended_market(self) -> bool:
        """对应 PHP ``IsExtendedMarket()``：是否处于美股盘前/盘后时段。

        PHP 判断 ``< 930 || > 1600``，即 09:29 及之前、16:01 及之后都算盘外。
        """
        if self.extended_ref:
            return True

        value = self.get_hour_minute()
        if value < 930 or value > 1600:
            return True
        return False

    def check_adjust_factor_time(self, etf_ref) -> bool:
        """对应 PHP ``CheckAdjustFactorTime()``：ETF 与自身是否为同一时刻的数据。

        两个引用时区不同时，先把本引用的时间换算到对方时区再比较。
        """
        if etf_ref is None:
            return False
        if etf_ref.has_quote_data() is False:
            return False
        if etf_ref.is_extended_market():
            return False

        time_zone = etf_ref.get_time_zone()
        if self.get_time_zone() == time_zone:
            date_text = self.str_date
            time_text = self.str_time
        else:
            moment = _parse_datetime(self.get_date_time(), self.get_time_zone())
            date_text = format_date(moment, time_zone)
            time_text = format_time(moment, time_zone)

        if date_text != etf_ref.get_date():
            return False
        if get_hm(time_text) != etf_ref.get_time_hm():
            return False
        return True

    def _convert_date_time_from_us(self, date_time: str, year: str) -> None:
        """对应 PHP ``_convertDateTimeFromUS()``。

        PHP 用 ``strtotime()``（进程默认时区）解析，再按纽约时区取值；日期部分
        只取传入的年份 + 月日，所以年份以 ``$strYear`` 为准（新浪的
        ``"Jun 08 11:36AM EDT"`` 不带年份，正好用得上）。

        新浪美股时间有两种形态，都要能解析：
        * ``2026-06-08 23:36:45``（``gb_`` 行情里的完整时间）
        * ``Jun 08 11:36AM EDT``（``$ar[24]`` 盘前/盘后时间，不带年份）
        """
        moment = _parse_datetime(date_time, self.get_time_zone())
        self.str_time = format_time(moment, TIME_ZONE_US)
        self.str_date = "%s-%s" % (year, moment.strftime("%m-%d"))

    def _convert_date_time_to_us(self, date_text: str, time_text: str) -> None:
        """对应 PHP ``_convertDateTimeToUS()``：按上海时间解读，再换到本符号时区。"""
        time_zone = self.get_time_zone()
        moment = _parse_datetime("%s %s" % (date_text, time_text), TIME_ZONE_SHANGHAI)
        self.str_date = format_date(moment, time_zone)
        self.str_time = format_time(moment, time_zone)

    # -- 新浪行情字段映射 -------------------------------------------------
    def _on_sina_data_us(self, array: Sequence) -> None:
        """对应 ``_onSinaDataUS()``：美股 ``gb_`` 行情字段。

        字段含义按 PHP 原有下标：0 名称、1 现价、26 昨收、25 时间、29 年份、
        5/6/7 开高低、10 成交量、24 盘外时间（存在时建立 extended_ref）。
        """
        self.str_name = _item(array, 0)
        self.str_price = _item(array, 1)
        self.str_prev_price = _item(array, 26)
        self._convert_date_time_from_us(_item(array, 25), _item(array, 29))
        self.str_open = _item(array, 5)
        self.str_high = _item(array, 6)
        self.str_low = _item(array, 7)
        self.str_volume = _item(array, 10)

        extended_time = _item(array, 24)
        if extended_time:
            self.extended_ref = ExtendedTradingReference(array, self.get_symbol())
            self.extended_ref.file_name = self.file_name

    def _on_sina_data_hk(self, array: Sequence) -> None:
        """对应 ``_onSinaDataHK()``：港股 ``rt_hk`` 行情字段。"""
        self.str_prev_price = _item(array, 3)
        self.str_price = _item(array, 6)
        self.str_date = str(_item(array, 17)).replace("/", "-")  # 新浪返回 2016/03/02
        self.str_time = _item(array, 18)

        self.str_open = _item(array, 2)
        self.str_high = _item(array, 4)
        self.str_low = _item(array, 5)
        self.str_volume = _item(array, 12)

        self.str_name = _item(array, 0)
        self.str_chinese_name = _item(array, 1)

    def _on_sina_data_cn(self, array: Sequence) -> None:
        """对应 ``_onSinaDataCN()``：A 股行情字段，含十档买卖盘。"""
        self.str_prev_price = _item(array, 2)
        self.str_price = _item(array, 3)
        self.str_date = _item(array, 30)
        self.str_time = _item(array, 31)
        self.str_name = _item(array, 0)

        self.str_open = _item(array, 1)
        self.str_high = _item(array, 4)
        self.str_low = _item(array, 5)
        self.str_volume = _item(array, 8)

        # PHP: for ($i = 10; $i < 20; $i += 2)，买盘在 10..18，卖盘顺延 10 位
        for index in range(10, 20, 2):
            self.bid_quantities.append(_item(array, index))
            self.bid_prices.append(_item(array, index + 1))
            self.ask_quantities.append(_item(array, index + 10))
            self.ask_prices.append(_item(array, index + 11))

    def _on_sina_global_index(self, array: Sequence, count: int) -> None:
        """对应 ``_onSinaGlobalIndex()``：全球指数字段，两种长度分开处理。

        ``count == 6`` 的短格式只给涨跌值和时间戳，昨收要现算；长格式直接给昨收
        和日期时间。最后按符号时区把时间从上海时间换算过去。
        """
        self.str_name = _item(array, 0)
        self.str_price = _item(array, 1)
        if count == 6:
            self.str_prev_price = str(float(self.str_price) - float(_item(array, 2)))
            timestamp = int(_item(array, 5))
            moment = _datetime.datetime.fromtimestamp(
                timestamp, tz=tzutil.get_time_zone(TIME_ZONE_SHANGHAI)
            )
            self.str_date = moment.strftime("%Y-%m-%d")
            self.str_time = moment.strftime("%H:%M:%S")
        else:
            self.str_prev_price = _item(array, 9)
            self.str_date = _item(array, 6)
            self.str_time = _item(array, 7)

            self.str_open = _item(array, 8)
            self.str_high = _item(array, 10)
            self.str_low = _item(array, 11)

        time_zone = self.get_time_zone()
        if time_zone != TIME_ZONE_SHANGHAI:
            date_text, time_text = convert_date_time(
                self.get_date_time(), TIME_ZONE_SHANGHAI, time_zone
            )
            self.str_date = date_text
            self.str_time = time_text

    def _load_quote(self, file_name: str) -> Any:
        """取行情原始文本；没有注入 loader 时视为没有缓存。"""
        if self.quote_loader is None:
            return None
        return self.quote_loader(file_name)

    def load_sina_data(self) -> None:
        """对应 PHP ``LoadSinaData()``：按符号类型分派解析。

        PHP 里 ``strExternalLink`` 由 ``GetSinaStockLink()`` 生成，那属于页面层，
        这里不设置；``strFileName`` 由 ``sina_quote_file_name()`` 给出。
        """
        sina_symbol = self.get_sina_symbol()
        if sina_symbol:
            self.file_name = sina_quote_file_name(sina_symbol)
            text = self._load_quote(self.file_name)
            if text:
                array = explode_quote(text)
                count = len(array)
                if count >= 6:
                    if self.is_sina_global_index():
                        self._on_sina_global_index(array, count)
                        return
                    elif self.is_symbol_a():
                        self._on_sina_data_cn(array)
                        return
                    elif self.is_symbol_h():
                        self._on_sina_data_hk(array)
                        return
                    elif count >= 29:
                        self._on_sina_data_us(array)
                        return
        self.has_data = False

    def _on_sina_future(self, array: Sequence) -> None:
        """对应 ``_onSinaFuture()``：外盘期货字段。"""
        self.str_price = _item(array, 0)
        self.str_prev_price = _item(array, 7)
        self._convert_date_time_to_us(_item(array, 12), _item(array, 6))

        self.str_name = _item(array, 13)

        self.str_open = _item(array, 8)
        self.str_high = _item(array, 4)
        self.str_low = _item(array, 5)
        # PHP 里 9 是持仓量，注释明确不用它当成交量

    def _on_sina_future_cn(self, array: Sequence) -> None:
        """对应 ``_onSinaFutureCN()``：国内期货字段。

        PHP 把 ``$ar[1]`` 的 6 位数字拆成 ``HH:MM:SS``；结算价为 0 时置为
        ``None``（对应 PHP 的 ``false``），供调用方区分“有结算价”和“没有”。
        """
        self.str_price = _item(array, 8)
        time_digits = str(_item(array, 1))
        self.str_time = "%s:%s:%s" % (time_digits[0:2], time_digits[2:4], time_digits[4:6])
        self.str_prev_price = _item(array, 10)
        self.str_date = _item(array, 17)
        self.str_name = "%s-%s" % (_item(array, 15), _item(array, 0))

        self.str_open = _item(array, 2)
        self.str_high = _item(array, 3)
        self.str_low = _item(array, 4)
        self.str_volume = _item(array, 14)

        settle = _item(array, 9)
        self.str_settle_price = None if is_zero_string(settle) else settle
        self.str_vwap = _item(array, 27)

    def load_sina_future_data(self) -> None:
        """对应 PHP ``LoadSinaFutureData()``：期货行情，至少 13 个字段。"""
        symbol = self.get_symbol()
        self.file_name = sina_quote_file_name(symbol)
        text = self._load_quote(self.file_name)
        if text:
            array = explode_quote(text)
            if len(array) >= 13:
                if self.is_sina_future_us():
                    self._on_sina_future(array)
                else:
                    self._on_sina_future_cn(array)
                return
        self.has_data = False

    def load_sina_fund_data(self) -> None:
        """对应 PHP ``LoadSinaFundData()``：场外基金净值，至少 4 个字段。"""
        if self.is_sina_fund():
            fund_symbol = self.get_symbol()
        else:
            fund_symbol = self.get_sina_fund_symbol()

        self.file_name = sina_quote_file_name(fund_symbol)
        text = self._load_quote(self.file_name)
        if text:
            array = explode_quote(text)
            if len(array) >= 4:
                self.str_price = _item(array, 1)  # 单位净值
                self.str_prev_price = _item(array, 3)
                self.str_date = _item(array, 4)
                self.str_name = _item(array, 0)
                return
        self.has_data = False

    def load_sina_forex_data(self) -> None:
        """对应 PHP ``LoadSinaForexData()``：外汇行情，至少 10 个字段。

        ``fx_sjpycny`` 特殊：新浪给的是 100 日元兑人民币，PHP 会除以 100 之前先
        乘 100，即放大到“每 100 日元”的口径，这里照搬。
        """
        symbol = self.get_symbol()
        self.file_name = sina_quote_file_name(symbol)
        text = self._load_quote(self.file_name)
        if text:
            array = explode_quote(text)
            if len(array) >= 10:
                self.str_prev_price = _item(array, 3)
                self.str_price = _item(array, 8)
                if symbol == "fx_sjpycny":
                    self.str_price = str(float(self.str_price) * 100.0)
                    self.str_prev_price = str(float(self.str_prev_price) * 100.0)

                self.str_name = _item(array, 9)
                # PHP 用 end($ar) 取最后一个字段作为日期
                self._convert_date_time_to_us(array[-1], _item(array, 0))
                self.str_open = _item(array, 5)
                self.str_high = _item(array, 6)
                self.str_low = _item(array, 7)
                return
        self.has_data = False

    # -- PHP 原名别名 -----------------------------------------------------
    HasQuoteData = has_quote_data
    HasData = has_quote_data
    SetHasData = set_has_data
    GetPrice = get_price
    GetPrevPrice = get_prev_price
    GetVolume = get_volume
    GetAskPrice = get_ask_price
    GetBidPrice = get_bid_price
    GetAskQuantity = get_ask_quantity
    GetBidQuantity = get_bid_quantity
    GetAvailableQuantity = get_available_quantity
    GetAvailablePrice = get_available_price
    GetExternalLink = get_external_link
    SetExternalLink = set_external_link
    GetPercentage = get_percentage
    GetPercentageString = get_percentage_string
    GetPercentageText = get_percentage_text
    GetPriceDisplay = get_price_display
    DebugLink = debug_link
    DebugConfigLink = debug_config_link
    GetDate = get_date
    GetTime = get_time
    GetDateTime = get_date_time
    SetTime = set_time
    GetTimeHM = get_time_hm
    GetHour = get_hour
    GetMinute = get_minute
    GetHourMinute = get_hour_minute
    ConvertTick = convert_tick
    IsExtendedMarket = is_extended_market
    CheckAdjustFactorTime = check_adjust_factor_time
    LoadSinaData = load_sina_data
    LoadSinaFutureData = load_sina_future_data
    LoadSinaFundData = load_sina_fund_data
    LoadSinaForexData = load_sina_forex_data


class ExtendedTradingReference(StockReference):
    """对应 PHP ``ExtendedTradingReference``：美股盘前/盘后的一段行情。

    它复用美股行情数组的另外几个下标（21 盘外价、24 盘外时间、27 盘外量），
    并额外提供 ``get_market_session()`` 判断是盘前还是盘后。
    """

    def __init__(self, array: Sequence, symbol: str):
        super().__init__(symbol)

        self.external_link = ""
        self.str_price = _item(array, 21)
        self._convert_date_time_from_us(_item(array, 24), _item(array, 29))
        self.str_prev_price = _item(array, 26)
        # PHP: strpos($ar[27], '.') ? strstr($ar[27], '.', true) : $ar[27]
        # 有小数点时取小数点前的整数部分（成交量的万股/亿股写法）
        volume = _item(array, 27)
        if volume is not None and "." in str(volume):
            self.str_volume = str(volume).split(".", 1)[0]
        else:
            self.str_volume = volume

    def get_market_session(self) -> str:
        """对应 PHP ``GetMarketSession()``：9 点及之前算盘前，之后算盘后。"""
        return "盘前交易" if self.get_hour() <= 9 else "盘后交易"

    # -- PHP 原名别名 -----------------------------------------------------
    GetMarketSession = get_market_session
