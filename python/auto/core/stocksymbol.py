"""股票符号分类、行情符号转换与市场时段判断层。

对应 PHP 源码：``php/stock/stocksymbol.php``。本模块是纯逻辑层：
不访问数据库、不发网络请求、不渲染 HTML。

迁移约定
--------
* PHP 返回 ``false`` 的“字符串类”方法统一返回 ``None``，“布尔类”方法统一返回
  ``False``，与 ``python/auto`` 下其它迁移模块保持一致。
* PHP 里“非空字符串为真”的判断直接映射成 Python 的非空字符串真值判断。
  ``StrHasPrefix()`` 仍然返回去掉前缀后的剩余部分（或 ``None``），这样
  ``if ($str = StrHasPrefix(...))`` 可以原样写成 ``if str_has_prefix(...)``。
* 只迁移 PHP 类中与逻辑相关的部分。两个链接辅助方法
  （``GetMyStockLink()`` / ``GetStockLink()``）依赖 PHP 的页面层与数据库层
  （``php/stocklink.php``、``php/sql/sqlstocksymbol.php``），留给后续迁移步骤；
  它们需要的输入由本模块的 ``get_symbol()`` 和 ``get_display()`` 提供。
* 另外保留一组 camelCase 别名，使远端分支
  ``origin/devin/1780933111-migrate-php-core-to-python`` 上的调用写法仍然可用。
"""

from __future__ import annotations

import os
import re
import time
import datetime as _datetime

# ---------------------------------------------------------------------------
# 常量（对应 PHP define()）
# ---------------------------------------------------------------------------
SINA_FOREX_PREFIX = "fx_s"
SINA_FUTURE_PREFIX = "hf_"
SINA_CN_FUTURE_PREFIX = "nf_"
SINA_FUND_PREFIX = "f_"
SINA_INDEX_PREFIX = "znb_"
SINA_HK_PREFIX = "rt_hk"
SINA_US_PREFIX = "gb_"

BJ_PREFIX = "BJ"
SH_PREFIX = "SH"
SZ_PREFIX = "SZ"

YAHOO_INDEX_CHAR = "^"

EAST_MONEY_FOREX_SYMBOLS = ("USCNY", "EUCNY", "JPCNY", "HKCNY")

TIME_ZONE_SHANGHAI = "Asia/Shanghai"
TIME_ZONE_EDT = "America/New_York"


# ---------------------------------------------------------------------------
# 通用辅助函数
# ---------------------------------------------------------------------------
def str_has_prefix(string, prefix):
    """对应 PHP ``StrHasPrefix()``。

    匹配时返回去掉 ``prefix`` 后的剩余部分，不匹配返回 ``None``（PHP 的 ``false``）。
    """
    if string is None or prefix is None:
        return None
    if string.startswith(prefix):
        return string[len(prefix):]
    return None


def _to_datetime(value):
    """归一化市场时段辅助函数接受的 ``$ymd`` 参数。

    ``datetime.datetime`` 原样返回；``datetime.date`` 按当天零点补齐。
    其它类型直接抛错，避免把错误的参数静默当成别的时间点来判断交易状态。
    """
    if isinstance(value, _datetime.datetime):
        return value
    if isinstance(value, _datetime.date):
        return _datetime.datetime(value.year, value.month, value.day)
    raise TypeError("expected datetime.datetime or datetime.date, got %r" % (type(value).__name__,))


def is_week_day(value):
    """对应 PHP ``YearMonthDay::IsWeekDay()``。"""
    # Python 的 weekday()：周一 0 ... 周日 6
    return _to_datetime(value).weekday() < 5


def get_hour_minute(value):
    """对应 PHP ``TickYMD::GetHourMinute()``：13:05 -> 1305。"""
    moment = _to_datetime(value)
    return moment.hour * 100 + moment.minute


# ---------------------------------------------------------------------------
# 符号表函数（对应 PHP 的 *GetSymbolArray() 与 in_array*()）
# ---------------------------------------------------------------------------
def get_secondary_listing_array():
    """美股符号 -> 港股二次上市代码。"""
    return {
        "BABA": "09988",
        "BIDU": "09888",
        "BILI": "09626",
        "JD": "09618",
        "NTES": "09999",
        "TCOM": "09961",
    }


def china_index_get_ashr_array():
    return [
        "SH501043",
        "SH510300",
        "SH510310",
        "SH510330",
        "SZ159919",
        "SZ160706",
        "SZ161005",
        "SZ163407",
        "SZ168401",
    ]


def in_array_ashr_symbol(symbol):
    return symbol in china_index_get_ashr_array()


def china_index_get_sz100_array():
    return ["SZ161227", "SZ161812"]


def china_index_get_coal_array():
    return ["SZ161032", "SZ168204"]


def china_index_get_symbol_array():
    return (
        china_index_get_ashr_array()
        + china_index_get_sz100_array()
        + china_index_get_coal_array()
        + [
            "SH502000",
            "SZ160225",
            "SZ160632",
            "SZ160639",
            "SZ161725",
            "SZ161726",
            "SZ162412",
            "SZ163109",
            "SZ163113",
            "SZ167301",
        ]
    )


def in_array_china_index(symbol):
    return symbol in china_index_get_symbol_array()


def china_future_get_symbol_array():
    return ["SH518800", "SH518880", "SZ159934", "SZ159937", "SZ159985", "SZ161226"]


def in_array_china_future(symbol):
    return symbol in china_future_get_symbol_array()


def qdii_get_oil_etf_symbol_array():
    return ["SZ160416", "SZ162719"]


def in_array_oil_etf_qdii(symbol):
    return symbol in qdii_get_oil_etf_symbol_array()


def qdii_get_qqq_match_array():
    return [
        "SH513100",
        "SH513110",
        "SH513390",
        "SH513870",
        "SZ159501",
        "SZ159513",
        "SZ159632",
        "SZ159659",
        "SZ159660",
        "SZ159696",
        "SZ159941",
        "SZ161130",
    ]


def in_array_qqq_match(symbol):
    return symbol in qdii_get_qqq_match_array()


def qdii_get_qqq_symbol_array():
    return qdii_get_qqq_match_array() + ["SH513300"]


def in_array_qqq_qdii(symbol):
    return symbol in qdii_get_qqq_symbol_array()


def qdii_get_spy_match_array():
    return ["SH513500", "SH513650", "SZ159612", "SZ161125"]


def in_array_spy_match(symbol):
    return symbol in qdii_get_spy_match_array()


def qdii_get_spy_symbol_array():
    return qdii_get_spy_match_array() + ["SZ159655"]


def in_array_spy_qdii(symbol):
    return symbol in qdii_get_spy_symbol_array()


def qdii_get_xop_symbol_array():
    return ["SH513350", "SZ159518", "SZ162411"]


def in_array_xop_qdii(symbol):
    return symbol in qdii_get_xop_symbol_array()


def qdii_get_xbi_symbol_array():
    return ["SZ159502", "SZ161127"]


def in_array_xbi_qdii(symbol):
    return symbol in qdii_get_xbi_symbol_array()


def qdii_get_symbol_array():
    """QDII 分组（``php/stock/stocksymbol.php:199`` 的 ``QdiiGetSymbolArray()``）。

    **本地 PHP 仓库是旧快照，生产端已经扩充过**：仓库里是 8 个硬编码 +
    XBI / XOP / OilEtf / QQQ / SPY 五组，共 33 个；而生产端
    ``https://palmmicro.com/woody/res/qdiicn.php``（2026-10-12 抓取）是
    **41 个**，多出下面这 8 个：:

        SZ159509 纳指科技ETF景顺   SH513850 美国50ETF易方达
        SZ159529 标普消费ETF景顺   SZ159577 美国50ETF汇添富
        SH501312 海外科技LOF      SZ160216 国泰商品LOF
        SZ164701 黄金LOF          SZ163208 全球油气能源LOF

    这 8 个的**归属子组无法从本地 PHP 确认**，所以按生产端的可观测输出复现
    —— 顺序上它们整块排在 SPY 组之后（不是插进 QQQ / SPY 组中间），
    因此作为一个独立的追加段放在最后。已经排除"从原分组移过来"这种解释：
    逐个抓了生产端的 ``qqqfundcn`` / ``spyfundcn`` / ``mscius50cn`` /
    ``commoditycn`` / ``oilfundcn``，那几个分组仍各自显示这 8 只，
    说明是**额外并入 qdii**，而非搬家。

    为什么连顺序都要对齐：参考数据表与估值表的行序就是数组顺序，而页面是
    人工比对的对象；顺序错了看不出公式问题，只会多出一堆无意义的差异。
    """
    return (
        [
            "SH501300",
            "SH513290",
            "SH513400",
            "SZ160140",
            "SZ161126",
            "SZ161128",
            "SZ162415",
            "SZ164906",
        ]
        + qdii_get_xbi_symbol_array()
        + qdii_get_xop_symbol_array()
        + qdii_get_oil_etf_symbol_array()
        + qdii_get_qqq_symbol_array()
        + qdii_get_spy_symbol_array()
        # 生产端 2026-10-12 新增的 8 个（本地 PHP 仓库里还没有，见 docstring）
        + [
            "SZ159509",
            "SZ159529",
            "SH513850",
            "SZ159577",
            "SH501312",
            "SZ160216",
            "SZ164701",
            "SZ163208",
        ]
    )


def in_array_qdii(symbol):
    return symbol in qdii_get_symbol_array()


def qdii_hk_get_tech_symbol_array():
    return [
        "SH513010",
        "SH513130",
        "SH513180",
        "SH513260",
        "SH513380",
        "SH513580",
        "SH513890",
        "SH520570",
        "SH520590",
        "SH520920",
        "SZ159740",
        "SZ159741",
        "SZ159742",
    ]


def in_array_tech_qdii_hk(symbol):
    return symbol in qdii_hk_get_tech_symbol_array()


def qdii_hk_get_h_shares_symbol_array():
    return ["SH510900", "SZ159850", "SZ159954", "SZ159960", "SZ160717", "SZ161831"]


def in_array_h_shares_qdii_hk(symbol):
    return symbol in qdii_hk_get_h_shares_symbol_array()


def qdii_hk_get_hang_seng_symbol_array():
    return [
        "SH501302",
        "SH513210",
        "SH513600",
        "SH513660",
        "SZ159312",
        "SZ159920",
        "SZ160924",
        "SZ164705",
    ]


def in_array_hang_seng_qdii_hk(symbol):
    return symbol in qdii_hk_get_hang_seng_symbol_array()


def qdii_hk_get_index_symbol_array():
    return ["^HSI", "^HSCE", "^HSTECH"]


def qdii_hk_get_symbol_array():
    return (
        ["SH501025", "SZ161124"]
        + qdii_hk_get_tech_symbol_array()
        + qdii_hk_get_h_shares_symbol_array()
        + qdii_hk_get_hang_seng_symbol_array()
    )


def in_array_qdii_hk(symbol):
    return symbol in qdii_hk_get_symbol_array()


def qdii_jp_get_nky_symbol_array():
    return ["SH513000", "SH513520", "SH513880", "SZ159866"]


def in_array_nky_qdii_jp(symbol):
    return symbol in qdii_jp_get_nky_symbol_array()


def qdii_jp_get_symbol_array():
    return ["SH513800"] + qdii_jp_get_nky_symbol_array()


def in_array_qdii_jp(symbol):
    return symbol in qdii_jp_get_symbol_array()


def qdii_eu_get_dax_symbol_array():
    return ["SH513030", "SZ159561"]


def in_array_dax_qdii_eu(symbol):
    return symbol in qdii_eu_get_dax_symbol_array()


def qdii_eu_get_symbol_array():
    return ["SH513080"] + qdii_eu_get_dax_symbol_array()


def in_array_qdii_eu(symbol):
    return symbol in qdii_eu_get_symbol_array()


def get_china_internet_symbol_array():
    return ["SH513050", "SH513220", "SZ159605", "SZ159607"]


def get_msci_us50_symbol_array():
    return ["SH513850", "SZ159577"]


def get_hk_mix_symbol_array():
    return [
        "SH513090",
        "SH513230",
        "SH513750",
        "SH513990",
        "SZ159567",
        "SZ159570",
        "SZ159615",
        "SZ159751",
        "SZ159792",
    ]


def in_array_hk_mix(symbol):
    return symbol in get_hk_mix_symbol_array()


def get_qdii_gold_symbol_array():
    return ["SZ160216", "SZ161815", "SZ160719", "SZ161116", "SZ164701", "SZ165513"]


def get_qdii_oil_symbol_array():
    return ["SZ163208", "SH501018", "SZ160723", "SZ161129"]


def get_lof_mix_symbol_array():
    return (
        ["SH501225", "SH501312", "SZ160644", "SZ164824"]
        + get_qdii_oil_symbol_array()
        + get_qdii_gold_symbol_array()
    )


def in_array_lof_mix(symbol):
    return symbol in get_lof_mix_symbol_array()


def qdii_mix_get_symbol_array():
    return (
        ["SH513360", "SZ159509", "SZ159529"]
        + get_lof_mix_symbol_array()
        + get_china_internet_symbol_array()
        + get_hk_mix_symbol_array()
        + get_msci_us50_symbol_array()
    )


def in_array_qdii_mix(symbol):
    return symbol in qdii_mix_get_symbol_array()


def get_all_symbol_array():
    return (
        qdii_get_symbol_array()
        + qdii_mix_get_symbol_array()
        + qdii_hk_get_symbol_array()
        + qdii_jp_get_symbol_array()
        + qdii_eu_get_symbol_array()
        + china_index_get_symbol_array()
        + china_future_get_symbol_array()
    )


def in_array_all(symbol):
    return symbol in get_all_symbol_array()


def get_over_night_symbol_array():
    return (
        qdii_get_xop_symbol_array()
        + ["SZ162719"]
        + get_qdii_oil_symbol_array()
        + get_qdii_gold_symbol_array()
        + [
            "SZ161226",
            "SZ161125",
            "SZ161126",
            "SZ161130",
            "SZ162415",
            "SZ164824",
            "SZ164906",
        ]
        + qdii_get_xbi_symbol_array()
    )


def in_array_over_night(symbol):
    """PHP 原文件只为这一组提供了数组，没有对应的 in_array* 函数。

    这里补上成员判断，只是为了与其它组保持一致的调用方式。
    """
    return symbol in get_over_night_symbol_array()


# ---------------------------------------------------------------------------
# 数字区间判断（对应 PHP 的 _isDigit*()）
# ---------------------------------------------------------------------------
_SIX_DIGIT_PATTERN = re.compile(r"[0-9]{6}\Z")


def is_china_stock_digit(digit):
    """对应 PHP ``IsChinaStockDigit()``。

    正好六位数字时返回该字符串，否则返回 ``None``。

    PHP 用的是 ``is_numeric()`` 加 ``strlen()``。这里不能用 ``str.isdigit()``：
    它会接受全角数字 ``'１２３４５６'``，而 PHP 的 ``is_numeric()`` 会拒绝。
    PHP 还容忍前导空白，因此 ``' 16241'`` 在那边算六位且是数字；这里会先
    ``strip()`` 再判断，既贴近 PHP 的接受范围，又不会把带空白的符号交回调用方。
    """
    if not isinstance(digit, str):
        return None
    value = digit.strip()
    if _SIX_DIGIT_PATTERN.match(value):
        return value
    return None


def _is_digit_shenzhen_etf(digit):
    return 150000 <= digit <= 159999


def _is_digit_shenzhen_lof(digit):
    return 160000 <= digit <= 169999


def _is_digit_shenzhen_b(digit):
    return 200000 <= digit < 300000


def _is_digit_shenzhen_gem(digit):
    # 创业板（growth enterprise market, GEM）
    return 300000 <= digit < 390000


def _is_digit_shenzhen_index(digit):
    return 390000 <= digit < 400000


def _is_digit_shanghai_index(digit):
    return 0 <= digit < 100000


def _is_digit_shanghai_etf(digit):
    # 518999
    return 510000 <= digit <= 569999


def _is_digit_shanghai_lof(digit):
    return 500000 <= digit <= 509999


def _is_digit_shanghai_star(digit):
    # 科创板（SSE STAR Market）
    return 688000 <= digit <= 688999


def _is_digit_shanghai_b(digit):
    return 900000 <= digit < 1000000


def _is_digit_shenzhen_fund(digit):
    if _is_digit_shenzhen_etf(digit):
        return True
    if _is_digit_shenzhen_lof(digit):
        return True
    return False


def _is_digit_shanghai_fund(digit):
    if _is_digit_shanghai_etf(digit):
        return True
    if _is_digit_shanghai_lof(digit):
        return True
    return False


# ---------------------------------------------------------------------------
# 符号构造
# ---------------------------------------------------------------------------
def build_china_fund_symbol(digit):
    """对应 PHP ``BuildChinaFundSymbol()``。"""
    if is_china_stock_digit(digit):
        value = int(digit)
        if _is_digit_shanghai_fund(value):
            prefix = SH_PREFIX
        elif _is_digit_shenzhen_fund(value):
            prefix = SZ_PREFIX
        else:
            return None
        return prefix + digit
    return None


def build_china_stock_symbol(digit):
    """对应 PHP ``BuildChinaStockSymbol()``。

    区间的划分照搬 PHP：注意最后一条是裸的 ``>= 600000``，所以 700000 这类
    未被单独分配的号段也会归到沪市。
    """
    if is_china_stock_digit(digit):
        value = int(digit)
        if value < 100000 or 200000 <= value < 400000:
            return SZ_PREFIX + digit
        elif 400000 <= value < 500000 or 800000 <= value < 900000:
            return BJ_PREFIX + digit
        elif value >= 600000:
            return SH_PREFIX + digit
    return None


def build_hongkong_stock_symbol(digit):
    """对应 PHP ``BuildHongkongStockSymbol()``：不足五位时左侧补零。"""
    if digit is None:
        return None
    if len(digit) != 5:
        return digit.rjust(5, "0")
    return digit


def build_yahoo_net_value_symbol(symbol, suffix="IV"):
    """对应 PHP ``BuildYahooNetValueSymbol()``：构造 ``^符号-IV`` 形式。"""
    if not symbol:
        return None
    return YAHOO_INDEX_CHAR + symbol + "-" + suffix


def stock_get_symbol(text):
    """对应 ``php/stock.php`` 里的 ``StockGetSymbol()``。

    先尝试按基金、股票代码补市场前缀，再对不含下划线的输入转大写。
    上游把这两个函数放在不同文件里，这里保留在本模块是因为它是唯一的
    「输入符号归一化」入口。
    """
    text = text.strip()
    symbol = build_china_fund_symbol(text)
    if symbol:
        return symbol
    symbol = build_china_stock_symbol(text)
    if symbol:
        return symbol
    if "_" not in text:
        text = text.upper()
    return text


# ---------------------------------------------------------------------------
# StockSymbol 类
# ---------------------------------------------------------------------------
class StockSymbol:
    """对应 PHP ``StockSymbol`` 类。

    PHP 类对每个市场判断都是「首次调用时计算并缓存在字段里」，缓存未命中用
    ``false`` 作哨兵。这里保留同样的哨兵（用 ``False`` 而不是 ``None``），
    这样缓存行为、以及 ``if ($this->strDigitA)`` 这类判断都能一一对应。
    """

    def __init__(self, symbol):
        self.str_symbol = symbol

        # 指数字符串以 '^' 开头
        self.str_first_char = False
        self.str_others = None

        # 中国市场
        self.str_digit_a = False     # 例如 '162411'
        self.i_digit_a = None
        self.str_prefix_a = False    # 'SH'、'SZ' 或 'BJ'

        # 香港市场
        self.i_digit_h = -1
        self.str_sina_index_h = False

        # 美国市场
        self.str_sina_index_us = False

    # -- 惰性取值辅助 ------------------------------------------------------
    def _get_first_char(self):
        if self.str_first_char is False:
            symbol = self.str_symbol
            self.str_first_char = symbol[0:1]
            self.str_others = symbol[1:]

    # -- 市场分类 ----------------------------------------------------------
    def is_index(self):
        self._get_first_char()
        return self.str_first_char == YAHOO_INDEX_CHAR

    def is_yahoo_net_value(self):
        if self.is_index():
            pos = self.str_others.find("-")
            if pos != -1:
                return self.str_others[0:pos]
        return False

    def is_symbol_us(self):
        if self.is_symbol_a():
            return False
        if self.is_symbol_h():
            return False
        return True

    def is_symbol_h(self):
        if self.i_digit_h >= 0:
            return True

        symbol = self.str_symbol
        if self.is_index():
            if symbol in qdii_hk_get_index_symbol_array():
                self.i_digit_h = 0
                return True
        elif self.str_first_char == "0":
            if symbol.isdigit() and len(symbol) == 5:
                self.i_digit_h = int(symbol)
                return True
        return False

    def get_digit_a(self):
        return self.str_digit_a

    def is_symbol_a(self):
        if self.str_digit_a:
            return self.str_digit_a

        symbol = self.str_symbol
        digit = symbol[2:]
        if is_china_stock_digit(digit):
            prefix = symbol[0:2].upper()
            if prefix == SH_PREFIX or prefix == SZ_PREFIX or prefix == BJ_PREFIX:
                self.str_prefix_a = prefix
                self.i_digit_a = int(digit)
                self.str_digit_a = digit
                return digit
        return False

    def is_shanghai_a(self):
        if self.is_symbol_a():
            if self.str_prefix_a == SH_PREFIX:
                return True
        return False

    def is_shenzhen_a(self):
        if self.is_symbol_a():
            if self.str_prefix_a == SZ_PREFIX:
                return True
        return False

    def is_beijing_a(self):
        if self.is_symbol_a():
            if self.str_prefix_a == BJ_PREFIX:
                return True
        return False

    def is_shanghai_etf(self):
        if self.is_shanghai_a():
            if _is_digit_shanghai_etf(self.i_digit_a):
                return True
        return False

    def is_shanghai_lof(self):
        if self.is_shanghai_a():
            if _is_digit_shanghai_lof(self.i_digit_a):
                return True
        return False

    def is_shanghai_star(self):
        if self.is_shanghai_a():
            if _is_digit_shanghai_star(self.i_digit_a):
                return True
        return False

    def is_shanghai_b(self):
        if self.is_shanghai_a():
            if _is_digit_shanghai_b(self.i_digit_a):
                return self.str_digit_a
        return False

    def is_shenzhen_b(self):
        if self.is_shenzhen_a():
            if _is_digit_shenzhen_b(self.i_digit_a):
                return self.str_digit_a
        return False

    def is_shenzhen_etf(self):
        if self.is_shenzhen_a():
            if _is_digit_shenzhen_etf(self.i_digit_a):
                return True
        return False

    def is_shenzhen_lof(self):
        if self.is_shenzhen_a():
            if _is_digit_shenzhen_lof(self.i_digit_a):
                return True
        return False

    def is_shenzhen_gem(self):
        if self.is_shenzhen_a():
            if _is_digit_shenzhen_gem(self.i_digit_a):
                return True
        return False

    def is_lof_a(self):
        if self.is_shenzhen_lof():
            return True
        if self.is_shanghai_lof():
            return True
        return False

    def is_etf_a(self):
        if self.is_shenzhen_etf():
            return True
        if self.is_shanghai_etf():
            return True
        return False

    def is_fund_a(self):
        if self.is_lof_a() or self.is_etf_a():
            return self.str_digit_a
        return False

    def is_index_a(self):
        if self.str_digit_a == False:
            if self.is_symbol_a() == False:
                return False

        if self.str_prefix_a == SZ_PREFIX and _is_digit_shenzhen_index(self.i_digit_a):
            return self.str_digit_a
        if self.str_prefix_a == SH_PREFIX and _is_digit_shanghai_index(self.i_digit_a):
            return self.str_digit_a
        return False

    def is_stock_b(self):
        if self.is_shanghai_b():
            return True
        if self.is_shenzhen_b():
            return True
        return False

    # -- 外汇 --------------------------------------------------------------
    def is_east_money_forex(self):
        return self.str_symbol in EAST_MONEY_FOREX_SYMBOLS

    def is_sina_forex(self):
        # PHP 写的是 strtoupper(StrHasPrefix(...))，所以不匹配时得到的是空字符串
        # 而不是 false。这里保留空字符串：两种语言里它都是假值，同时调用方还能
        # 看到确切返回值。
        remainder = str_has_prefix(self.str_symbol, SINA_FOREX_PREFIX)
        if remainder is not None:
            return remainder.upper()
        return ""

    def is_forex(self):
        if self.is_east_money_forex():
            return True
        if self.is_sina_forex():
            return True
        return False

    # -- 新浪各类行情 ------------------------------------------------------
    def is_sina_fund(self):
        # f_240019
        digit = str_has_prefix(self.str_symbol, SINA_FUND_PREFIX)
        if digit:
            return is_china_stock_digit(digit)
        return False

    def is_sina_global_index(self):
        return str_has_prefix(self.str_symbol, SINA_INDEX_PREFIX)

    def is_sina_future_cn(self):
        return str_has_prefix(self.str_symbol, SINA_CN_FUTURE_PREFIX)

    def is_sina_future_except_gold_cn(self):
        value = self.is_sina_future_cn()
        if value:
            if value != "AU0":
                return True
        return False

    def is_sina_future_us(self):
        return str_has_prefix(self.str_symbol, SINA_FUTURE_PREFIX)

    def is_sina_future(self):
        if self.is_sina_future_cn():
            return True
        if self.is_sina_future_us():
            return True
        return False

    # -- 行情符号转换 ------------------------------------------------------
    def get_sina_fund_symbol(self):
        if self.is_fund_a():
            return SINA_FUND_PREFIX + self.str_digit_a
        return False

    def get_sina_index_h(self):
        if self.str_sina_index_h is False:
            mapping = {"^HSI": "HSI", "^HSCE": "HSCEI", "^HSTECH": "HSTECH"}
            self.str_sina_index_h = mapping.get(self.str_symbol, False)
        return self.str_sina_index_h

    def get_sina_index_us(self):
        if self.str_sina_index_us is False:
            mapping = {"^DJI": "dji", "^GSPC": "inx", "^NDX": "ndx"}
            self.str_sina_index_us = mapping.get(self.str_symbol, False)
        return self.str_sina_index_us

    def get_sina_symbol(self):
        if self.is_sina_global_index():
            return self.str_symbol

        # PHP 先把 '.' 换成 '$'，再统一小写
        symbol = self.str_symbol.replace(".", "$")
        lower = symbol.lower()
        if self.is_index():
            if self.get_sina_index_h():
                return SINA_HK_PREFIX + self.str_sina_index_h
            elif self.get_sina_index_us():
                return SINA_US_PREFIX + self.str_sina_index_us
            else:
                return False
        elif self.is_symbol_h():
            # 香港市场
            return SINA_HK_PREFIX + symbol
        elif self.is_symbol_a():
            return lower
        return SINA_US_PREFIX + lower

    def get_yahoo_symbol(self):
        future_suffix = "%3DF"    # CL=F
        index_prefix = "%5E"      # ^HSI
        hk = ".hk"

        symbol = self.str_symbol.replace(".", "-")
        value = self.is_sina_future_us()
        if value:
            if value == "CHA50CFD":
                return "XIN9.FGI"
            # 外盘期货：CL=F
            return value + future_suffix
        elif self.is_sina_forex():
            if self.is_sina_forex() == "USDCNH":
                # CNH=F
                return "CNH" + future_suffix
        elif self.is_sina_global_index():
            mapping = {
                "CAC": index_prefix + "FCHI",
                "DAX": index_prefix + "GDAXI",
                "NKY": index_prefix + "N225",
                "SENSEX": index_prefix + "BSESN",
            }
            remainder = self.is_sina_global_index()
            if remainder in mapping:
                return mapping[remainder]
        elif self.is_index():
            if symbol == "^HSTECH":
                return self.str_others + hk
            # 普通指数：^HSI
            return index_prefix + self.str_others
        elif self.is_symbol_h():
            # 香港市场
            return self.str_others + hk
        elif self.is_symbol_a():
            if self.str_prefix_a == SH_PREFIX:
                # 沪市
                return self.str_digit_a + ".ss"
            elif self.str_prefix_a == SZ_PREFIX:
                # 深市
                return self.str_digit_a + ".sz"
            elif self.str_prefix_a == BJ_PREFIX:
                # 北交所
                return self.str_digit_a + ".bj"
        return symbol

    # -- 显示与交易判断 ----------------------------------------------------
    def get_precision(self):
        if self.is_fund_a() or self.is_sina_fund() or self.is_stock_b():
            return 3
        elif self.is_forex():
            return 4
        return 2

    def get_default_position(self):
        return 0.95 if self.is_lof_a() else 1.0

    def is_tradable(self):
        if self.is_sina_global_index():
            return False
        if self.is_index():
            return False
        if self.is_index_a():
            return False
        # PHP 原文件把外汇和期货判定为可交易，它注释掉的那两行检查在这里也只
        # 保留为说明，不启用。
        return True

    # 全球各市场交易时间参考：https://cn.investing.com/blog/article-193
    def is_before_stock_market(self, hour_minute):
        """对应 PHP ``IsBeforeStockMarket()``：是否还没开盘。

        注意控制流：A 股/港股/美股的判断是嵌在「全球指数」分支**内部**的
        （PHP 原文件这几行缩进不规则，容易误读为同级 else if）。因此
        ``znb_`` 开头的符号如果不在下面那五个指数里，会直接走到最后的
        ``return False``，即只要不是周末就视为在交易时段。
        """
        if self.is_sina_global_index():
            remainder = self.is_sina_global_index()
            if remainder in ("CAC", "DAX", "NKY", "TPX", "SENSEX"):
                if hour_minute < 900:
                    return True
        elif self.is_symbol_a():
            if hour_minute < 915:
                return True
        elif self.is_symbol_h():
            # 香港市场：9:00 到 16:10
            if hour_minute < 900:
                return True
        else:
            # 美股盘前盘后交易：凌晨 4 点到晚上 8 点
            if hour_minute < 400:
                return True

        return False

    def is_after_stock_market(self, hour_minute):
        """对应 PHP ``IsAfterStockMarket()``：是否已经收盘。

        与 ``is_before_stock_market()`` 同样的嵌套结构，说明见该方法的文档。
        """
        if self.is_sina_global_index():
            remainder = self.is_sina_global_index()
            if remainder in ("CAC", "DAX"):
                # DAX30 指数交易时间为 09:00 - 17:30（CET）
                if hour_minute > 1805:
                    return True
            elif remainder in ("NKY", "TPX"):
                # 東證所的交易時間從上午9點到上午11點30分及下午0點30分到下午3點
                if hour_minute > 1535:
                    return True
            elif remainder == "SENSEX":
                # 印度时间 18:35
                if hour_minute > 1605:
                    return True
        elif self.is_symbol_a():
            if hour_minute > 1505:
                # 科创板与创业板收盘更晚，15:35 之后才算收盘
                if self.is_shanghai_star() or self.is_shenzhen_gem():
                    if hour_minute > 1535:
                        return True
                else:
                    return True
        elif self.is_symbol_h():
            # 香港市场：9:00 到 16:10
            if hour_minute > 1615:
                return True
        else:
            # 美股盘前盘后交易：凌晨 4 点到晚上 8 点
            if hour_minute > 2005:
                return True

        return False

    def is_stock_market_trading(self, ymd, no_noon_break=True):
        """对应 PHP ``IsStockMarketTrading($ymd, $bNoonBreak = true)``。

        ``ymd`` 接受 ``datetime.datetime`` 或 ``datetime.date``；判断只用到星期
        几和时分，所以只传日期时按当天零点处理。
        """
        if not is_week_day(ymd):
            # 周末不交易
            return False

        hour_minute = get_hour_minute(ymd)
        if self.is_before_stock_market(hour_minute):
            return False
        elif self.is_after_stock_market(hour_minute):
            return False
        elif no_noon_break:
            if self.is_symbol_a():
                if 1140 < hour_minute < 1300:
                    # 午休：SH000300 会更新到 1135
                    return False
            elif self.is_symbol_h():
                # 香港市场：9:00 到 16:10
                if 1200 < hour_minute < 1300:
                    if not self.is_index():
                        # ^HSCE 与 ^HSI 在午休期间继续交易
                        return False

        return True

    def get_time_zone(self):
        """对应 PHP ``GetTimeZone()``：该符号所在市场的时区名。"""
        if self.is_sina_fund():
            pass
        elif self.is_sina_future():
            if self.is_sina_future_us():
                return TIME_ZONE_EDT
        elif self.is_sina_forex():
            return TIME_ZONE_EDT
        elif self.is_east_money_forex():
            pass
        elif self.is_sina_global_index():
            remainder = self.is_sina_global_index()
            if remainder == "CAC":
                return "Europe/Paris"
            elif remainder == "DAX":
                return "Europe/Berlin"
            elif remainder in ("NKY", "TPX"):
                return "Asia/Tokyo"
            elif remainder == "SENSEX":
                return "Asia/Kolkata"
        elif self.is_symbol_a() or self.is_symbol_h():
            pass
        else:
            return TIME_ZONE_EDT
        return TIME_ZONE_SHANGHAI

    def set_time_zone(self):
        """对应 PHP ``SetTimeZone()``。

        Python 没有「进程级默认时区」这个概念，所以这里设置 ``TZ`` 环境变量并调用
        ``time.tzset()``（该接口只在部分平台提供，Windows 上没有）。
        需要做日期运算的调用方应改用 ``get_time_zone()`` 配合 ``tzutil`` /
        ``zoneinfo`` 显式指定时区。
        """
        time_zone = self.get_time_zone()
        if not hasattr(time, "tzset"):
            raise RuntimeError("set_time_zone() 需要 time.tzset()，当前平台不提供")
        os.environ["TZ"] = time_zone
        time.tzset()
        return time_zone

    def get_display(self):
        """对应 PHP ``GetDisplay()``：去掉前缀后的展示名。"""
        value = self.is_sina_future_us()
        if value:
            return value
        value = self.is_sina_future_cn()
        if value:
            return value
        value = self.is_sina_forex()
        if value:
            return value
        value = self.is_sina_global_index()
        if value:
            return value
        return self.get_symbol()

    def get_symbol(self):
        return self.str_symbol

    # -- 兼容远端分支 API 的 camelCase 别名 --------------------------------
    # 未合并的分支 origin/devin/1780933111-migrate-php-core-to-python 用的是
    # camelCase 方法名。这里保留一层薄别名，让按那个分支写的调用代码继续可用。
    IsIndex = is_index
    IsYahooNetValue = is_yahoo_net_value
    IsSymbolUS = is_symbol_us
    IsSymbolH = is_symbol_h
    GetDigitA = get_digit_a
    IsSymbolA = is_symbol_a
    IsShangHaiA = is_shanghai_a
    IsShenZhenA = is_shenzhen_a
    IsBeiJingA = is_beijing_a
    IsShangHaiEtf = is_shanghai_etf
    IsShangHaiLof = is_shanghai_lof
    IsShangHaiStar = is_shanghai_star
    IsShangHaiB = is_shanghai_b
    IsShenZhenB = is_shenzhen_b
    IsShenZhenEtf = is_shenzhen_etf
    IsShenZhenLof = is_shenzhen_lof
    IsShenZhenGem = is_shenzhen_gem
    IsLofA = is_lof_a
    IsEtfA = is_etf_a
    IsFundA = is_fund_a
    IsIndexA = is_index_a
    IsStockB = is_stock_b
    IsEastMoneyForex = is_east_money_forex
    IsSinaForex = is_sina_forex
    IsForex = is_forex
    IsSinaFund = is_sina_fund
    IsSinaGlobalIndex = is_sina_global_index
    IsSinaFutureCN = is_sina_future_cn
    IsSinaFutureExceptGoldCN = is_sina_future_except_gold_cn
    IsSinaFutureUS = is_sina_future_us
    IsSinaFuture = is_sina_future
    GetSinaFundSymbol = get_sina_fund_symbol
    GetSinaIndexH = get_sina_index_h
    GetSinaIndexUS = get_sina_index_us
    GetSinaSymbol = get_sina_symbol
    GetYahooSymbol = get_yahoo_symbol
    GetPrecision = get_precision
    GetDefaultPosition = get_default_position
    IsTradable = is_tradable
    IsBeforeStockMarket = is_before_stock_market
    IsAfterStockMarket = is_after_stock_market
    IsStockMarketTrading = is_stock_market_trading
    GetTimeZone = get_time_zone
    SetTimeZone = set_time_zone
    GetDisplay = get_display
    GetSymbol = get_symbol


# ---------------------------------------------------------------------------
# 模块级 camelCase 别名（对应 PHP 原函数名）
# ---------------------------------------------------------------------------
StrHasPrefix = str_has_prefix
GetSecondaryListingArray = get_secondary_listing_array
ChinaIndexGetAshrArray = china_index_get_ashr_array
in_arrayAshrSymbol = in_array_ashr_symbol
ChinaIndexGetSz100Array = china_index_get_sz100_array
ChinaIndexGetCoalArray = china_index_get_coal_array
ChinaIndexGetSymbolArray = china_index_get_symbol_array
in_arrayChinaIndex = in_array_china_index
ChinaFutureGetSymbolArray = china_future_get_symbol_array
in_arrayChinaFuture = in_array_china_future
QdiiGetOilEtfSymbolArray = qdii_get_oil_etf_symbol_array
in_arrayOilEtfQdii = in_array_oil_etf_qdii
QdiiGetQqqMatchArray = qdii_get_qqq_match_array
in_arrayQqqMatch = in_array_qqq_match
QdiiGetQqqSymbolArray = qdii_get_qqq_symbol_array
in_arrayQqqQdii = in_array_qqq_qdii
QdiiGetSpyMatchArray = qdii_get_spy_match_array
in_arraySpyMatch = in_array_spy_match
QdiiGetSpySymbolArray = qdii_get_spy_symbol_array
in_arraySpyQdii = in_array_spy_qdii
QdiiGetXopSymbolArray = qdii_get_xop_symbol_array
in_arrayXopQdii = in_array_xop_qdii
QdiiGetXbiSymbolArray = qdii_get_xbi_symbol_array
in_arrayXbiQdii = in_array_xbi_qdii
QdiiGetSymbolArray = qdii_get_symbol_array
in_arrayQdii = in_array_qdii
QdiiHkGetTechSymbolArray = qdii_hk_get_tech_symbol_array
in_arrayTechQdiiHk = in_array_tech_qdii_hk
QdiiHkGetHSharesSymbolArray = qdii_hk_get_h_shares_symbol_array
in_arrayHSharesQdiiHk = in_array_h_shares_qdii_hk
QdiiHkGetHangSengSymbolArray = qdii_hk_get_hang_seng_symbol_array
in_arrayHangSengQdiiHk = in_array_hang_seng_qdii_hk
QdiiHkGetIndexSymbolArray = qdii_hk_get_index_symbol_array
QdiiHkGetSymbolArray = qdii_hk_get_symbol_array
in_arrayQdiiHk = in_array_qdii_hk
QdiiJpGetNkySymbolArray = qdii_jp_get_nky_symbol_array
in_arrayNkyQdiiJp = in_array_nky_qdii_jp
QdiiJpGetSymbolArray = qdii_jp_get_symbol_array
in_arrayQdiiJp = in_array_qdii_jp
QdiiEuGetDaxSymbolArray = qdii_eu_get_dax_symbol_array
in_arrayDaxQdiiEu = in_array_dax_qdii_eu
QdiiEuGetSymbolArray = qdii_eu_get_symbol_array
in_arrayQdiiEu = in_array_qdii_eu
GetChinaInternetSymbolArray = get_china_internet_symbol_array
GetMsciUs50SymbolArray = get_msci_us50_symbol_array
GetHkMixSymbolArray = get_hk_mix_symbol_array
in_arrayHkMix = in_array_hk_mix
GetQdiiGoldSymbolArray = get_qdii_gold_symbol_array
GetQdiiOilSymbolArray = get_qdii_oil_symbol_array
GetLofMixSymbolArray = get_lof_mix_symbol_array
in_arrayLofMix = in_array_lof_mix
QdiiMixGetSymbolArray = qdii_mix_get_symbol_array
in_arrayQdiiMix = in_array_qdii_mix
GetAllSymbolArray = get_all_symbol_array
in_arrayAll = in_array_all
GetOverNightSymbolArray = get_over_night_symbol_array
in_arrayOverNight = in_array_over_night

IsChinaStockDigit = is_china_stock_digit
BuildChinaFundSymbol = build_china_fund_symbol
BuildChinaStockSymbol = build_china_stock_symbol
BuildHongkongStockSymbol = build_hongkong_stock_symbol
BuildYahooNetValueSymbol = build_yahoo_net_value_symbol
StockGetSymbol = stock_get_symbol
