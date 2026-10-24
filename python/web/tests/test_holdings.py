"""「基金持仓」页（``woody/res/holdingscn.php``）的测试。

覆盖三层：

1. **纯函数**：``RefSort`` 的 A→H→US 分组排序、七列定义、说明文字标题；
2. **七列持仓测算表**（``_echoHoldingItem``）：每个成分股一行的七格怎么算、
   三个 static 累计器怎么累、合计尾行为什么只有 6 格；
3. **整页装配**（``EchoAll()``）：两道守卫（持仓日 / 有行情）合成一个
   ``None``，est 说明文字的 links 注入，ADR 配对收集的**方向**。

## 数字怎么来的

一组能手算的输入：汇率调整 0.9063（HKD）/ 1.0082（USD）、净值涨幅 2.34%。
期望值在测试里用**显式算术**写出来（不调用被测代码的公式），格式化统一走
``php_number_format`` / ``mysql_round`` —— 那两个函数的 PHP 口径由
``auto/test_historytable.py::PhpRoundingTest`` 与本地 PHP 8.0.10 逐例钉死
（23 个用例），这里不再重复验证舍入本身，只验证"哪个数进了哪个格子"。

## 两处"看着像 bug 但是原文"的地方

* **合计尾行只有 6 格**（缺「汇率调整」）—— PHP 原样就是 6 个元素的数组；
* 尾行第 4 格是 ``($fNetValueChange - 1) * 100`` 加百分号，与数据行的
  「此后涨幅」不是一套数。
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(WEB_DIR)
for _path in (PYTHON_DIR, WEB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from ref.stockref import StockReference  # noqa: E402
from sql.historytable import mysql_round, php_number_format  # noqa: E402
from web.services import holdings_service as hs  # noqa: E402
from web.services.holdings_service import (  # noqa: E402
    DISP_ALL_CN,
    HOLDINGS_TABLE_ID,
    HOLDINGS_INTRO,
    PAGE_DESCRIPTION,
    PAGE_TITLE_SUFFIX,
    HoldingsService,
    _history_value,
    _percentage_text,
    build_holdings_columns,
    build_holdings_page,
    holdings_intro_text,
    ref_sort_ahus,
)
from web.services.stock_detail_service import StockDetailService  # noqa: E402


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------
class FakeHoldingRef(StockReference):
    """成分股引用替身。

    **继承真 ``StockReference``** 而不是另写鸭子替身：「此后涨幅」那一格的
    口径（``GetPercentageString`` 的阈值归零 + ``GetPercentageText`` 的削零
    加 %）正是本表最容易与 PHP 分叉的地方，用真实现才能保证测的是**装配**
    而不是替身自己的算术。符号判定（A / H / US）同样用真实现 —— ``00700``
    是港股、``SH600519`` 是 A 股，都是 ``StockSymbol`` 按符号形状判的。

    ``stock_id`` 默认传**字符串**：PHP 的 ``GetStockId()`` 返回字符串，而
    持仓比例表的键是 MySQL 取出的整数 —— 两边类型不一致正是
    :func:`ref.holdingsref.ratio_of` 与 :func:`_history_value` 要容错的原因。
    """

    def __init__(self, symbol, stock_id, price, prev_price=None):
        super().__init__(symbol)
        self.stock_id = stock_id
        self.str_price = price
        #: 昨收默认等于现价（涨幅 0，走阈值归零那条路）；需要非零昨收的
        #: 用例显式传（见 PercentageTextTest 的回落用例）。
        self.str_prev_price = price if prev_price is None else prev_price

    def get_stock_id(self):
        return self.stock_id


class FakeHoldingsRef:
    """基金级持仓引用替身（``HoldingsReference`` 的页面侧表面）。

    只实现 ``holdings_service`` 会用到的那几个方法；估值式本身由
    ``auto/test_holdingsref.py`` 与 PHP 对照脚本负责，这里不重测。
    """

    def __init__(self, symbol="SH513050", date="2026-10-13", refs=(),
                 ratio=None, net_value_change=1.0234,
                 adjust_hkd=0.9063, adjust_usd=1.0082,
                 has_data=True, count=None,
                 ratio_display="A股5% 港股30.68% 美股7.31%"):
        self._symbol = symbol
        self._date = date
        self._refs = list(refs)
        self._ratio = RATIO if ratio is None else ratio
        self._net_value_change = net_value_change
        self._adjust_hkd = adjust_hkd
        self._adjust_usd = adjust_usd
        self._has_data = has_data
        self._count = len(self._refs) if count is None else count
        self._ratio_display = ratio_display

    def get_symbol(self):
        return self._symbol

    def get_holdings_date(self):
        return self._date

    def has_data(self):
        return self._has_data

    def count_holdings(self):
        return self._count

    def get_holdings_ratio_display(self):
        return self._ratio_display

    def get_holdings_ref_array(self):
        return list(self._refs)

    def get_holdings_ratio_array(self):
        return dict(self._ratio)

    def get_net_value_change(self):
        return self._net_value_change

    def get_holding_date_history(self):
        return dict(HISTORY)

    def get_adjust_hkd(self):
        return self._adjust_hkd

    def get_adjust_usd(self):
        return self._adjust_usd

    def get_position(self):
        return 1.0

    #: 符号判定要与真 ``StockSymbol`` 的形状规则一致 —— ``ref_adjust_forex``
    #: 靠它决定尾行乘哪个汇率调整（缺方法会静默落到 USD 分支！）。
    def is_symbol_a(self):
        return self._symbol[:2] in ("SH", "SZ", "BJ") and self._symbol[2:].isdigit()

    def is_symbol_h(self):
        return len(self._symbol) == 5 and self._symbol.isdigit()


class RecordingFundEst:
    """``FundEstService`` 替身：只记传进来的引用。"""

    def __init__(self):
        self.refs_seen = []

    def get_table(self, refs):
        self.refs_seen = list(refs)
        return {"kind": "fundest", "rows": []}


class FakeHistoryService:
    """``HistoryService`` 替身：净值记录段落。"""

    def net_value_paragraph(self, symbol, ref, num=1):
        return {"kind": "netvaluehistory", "symbol": symbol, "num": num}


class FakeReferenceService:
    """``ReferenceService`` 替身：只保证 ④ 参考数据表有个非空形状。

    参考数据表自己的口径（行状态机 / 名称列 / 五列宽度）由
    ``tests/test_reference.py`` 负责，这里不重测。**``rows`` 必须非空** ——
    ``holdings_service._reference_table`` 对空行返回 ``None``（"没有数据
    就不摆这张表"），给空列表等于没有注入。
    """

    def get_table(self, refs, wide=False, admin=False, title=""):
        from sql.historytable import TableRow

        row = TableRow()
        row.add("SH513050")
        return {"kind": "reference", "title": title,
                "refs_seen": list(refs), "rows": [row]}


class RecordingCursor:
    """记录 SQL 的假游标（``_lookup_pair_symbol_reverse`` 用）。"""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        self.connection.queries.append((sql, params))

    def fetchone(self):
        return self.connection.pair_row

    def close(self):
        pass


class PairSource:
    """只为配对反查供 ``connection`` 的假数据源。

    ``pair_row`` 是反查的返回值：``("BILI",)`` 表示 09626 配上 BILI；
    ``None`` 表示库里没有这一对（PHP 的 ``if ($strAdrSymbol = ...)`` 不成立）。
    """

    def __init__(self, pair_row=None):
        self.connection = self
        self.queries = []
        self.pair_row = pair_row

    def cursor(self):
        return RecordingCursor(self)

    def get_stock_name(self, symbol):
        return None


class RecordingAdrPair:
    """``AdrPairReference`` 替身：只记构造时拿到的符号。"""

    def __init__(self, symbol, source=None):
        self.symbol = symbol
        self.source = source


# ---------------------------------------------------------------------------
#  canonical 输入：三只成分股（A / H / US 各一），顺序即持仓表原始顺序
# ---------------------------------------------------------------------------
RATIO = {13: 5.0, 11: 30.68, 12: 7.31}            # int 键：MySQL 取出的形状
HISTORY = {"13": "1500.0", "11": "425.2", "12": "81.96"}
ADJUST_HKD = 0.9063
ADJUST_USD = 1.0082
NET_VALUE_CHANGE = 1.0234                          # 净值涨 2.34%


def default_refs():
    return [
        FakeHoldingRef("SH600519", "13", "1501.0"),
        FakeHoldingRef("00700", "11", "418.0"),
        FakeHoldingRef("PDD", "12", "81.96"),
    ]


def make_fund(**kwargs):
    kwargs.setdefault("refs", default_refs())
    return FakeHoldingsRef(**kwargs)


def _cells(row):
    """把一行格子统一成可断言的文本列表（颜色格子取文本部分）。"""
    return [cell[0] if isinstance(cell, tuple) else cell for cell in row.cells]


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
class RefSortAhTest(unittest.TestCase):
    """``RefSort($arRef)``（``holdingscn.php:8-22``）—— 只服务参考数据表。"""

    def test_groups_a_then_h_then_us_each_sorted(self):
        refs = [
            FakeHoldingRef("PDD", "12", "81.96"),        # 美股
            FakeHoldingRef("00700", "11", "418.0"),      # 港股
            FakeHoldingRef("SH600519", "13", "1501.0"),  # A 股
            FakeHoldingRef("BILI", "14", "106.2"),       # 美股
            FakeHoldingRef("09626", "15", "106.2"),      # 港股
        ]
        self.assertEqual([r.get_symbol() for r in ref_sort_ahus(refs)],
                         ["SH600519", "00700", "09626", "BILI", "PDD"])

    def test_duplicate_symbol_kept_once(self):
        """``RefSortBySymbol`` 用 ``ksort`` + 同键跳过：重复符号只留第一个。"""
        first = FakeHoldingRef("PDD", "12", "81.96")
        second = FakeHoldingRef("PDD", "12", "99.0")
        self.assertIs(ref_sort_ahus([first, second])[0], first)

    def test_non_a_non_h_all_land_in_us_group(self):
        """外汇 / 指数这类符号没有 A / H 判定，全落第三组（PHP 的 else）。"""
        refs = [FakeHoldingRef("^NDX", "6", "25000.0"),
                FakeHoldingRef("SH600519", "13", "1501.0"),
                FakeHoldingRef("fx_susdcny", "8", "7.1")]
        self.assertEqual([r.get_symbol() for r in ref_sort_ahus(refs)],
                         ["SH600519", "^NDX", "fx_susdcny"])

    def test_holdings_table_order_is_not_sorted(self):
        """测算表按持仓表**原始顺序**出行 —— ``RefSort`` 不掺和这一层。

        PHP 的 ``foreach ($arHoldingRef ...)`` 用的是
        ``GetHoldingsRefArray()`` 的原始返回，与第 ④ 段
        ``array_merge(array($ref), RefSort(...))`` 是两个不同的列表。
        """
        service = HoldingsService()
        table = service.get_table(make_fund())
        self.assertEqual([_cells(row)[0] for row in table["rows"][:3]],
                         ["SH600519", "00700", "PDD"])


class HoldingsColumnsTest(unittest.TestCase):
    """七列定义（``holdingscn.php:87-93``）与说明文字标题。"""

    def test_seven_columns_match_php(self):
        columns = build_holdings_columns()
        #: 「影响比例(%)」不是笔误 —— 生产端 ``holdingstable`` 表头逐字如此
        #: （``TableColumnPercentage`` 的文本本来就是「比例(%)」，前缀
        #: 『影响』拼在前面）。
        self.assertEqual([c["text"] for c in columns],
                         ["代码", "旧比例(%)", "旧价格", "此后涨幅",
                          "新比例(%)", "影响比例(%)", "汇率调整"])
        self.assertEqual([c["width"] for c in columns],
                         [80, 100, 70, 70, 100, 100, 100])
        self.assertEqual([c["color"] for c in columns],
                         ["maroon", "", "blue", "", "", "", ""])

    def test_intro_text_is_count_plus_ratio_display(self):
        """``'持仓和测算示意 ' . '总数' . N . ' ' . GetHoldingsRatioDisplay()``。"""
        self.assertEqual(holdings_intro_text(make_fund()),
                         "持仓和测算示意 总数3 A股5% 港股30.68% 美股7.31%")

    def test_intro_text_without_ratio_display(self):
        """比例展示为空串时结尾不留空格（PHP 的 ``$str .= ' '.$display``
        外面还有一层 ``$str`` 初始化，空 display 会多一个尾随空格 —— 这里
        按站既有的 ``GetHoldingsRatioDisplay`` 恒有值处理，空值只保证不炸）。"""
        fund = make_fund(ratio_display="")
        self.assertEqual(holdings_intro_text(fund), "持仓和测算示意 总数3")


# ---------------------------------------------------------------------------
# 七列测算表
# ---------------------------------------------------------------------------
class HoldingsTableTest(unittest.TestCase):
    """``_echoHoldingItem`` 的逐格口径 + 三个 static 累计器 + 尾行。"""

    def setUp(self):
        self.service = HoldingsService()
        self.table = self.service.get_table(make_fund())

    def test_table_metadata(self):
        self.assertEqual(self.table["id"], HOLDINGS_TABLE_ID)
        self.assertEqual(self.table["title"],
                         "持仓和测算示意 总数3 A股5% 港股30.68% 美股7.31%")
        # 3 只成分股 + 1 行合计
        self.assertEqual(self.table["count"], 4)
        self.assertEqual(self.table["cell_count"], 7)

    def test_a_share_row(self):
        """A 股：汇率调整 1.0，``$fChange = $fPrice / $fClose`` 不再除谁。"""
        row = self.table["rows"][0]
        change = (1501.0 / 1500.0) / 1.0
        self.assertEqual(_cells(row), [
            "SH600519",
            php_number_format(5.0, 2),                    # 旧比例
            mysql_round("1500.0", 2),                     # 旧价格
            "0.07%",                                       # 此后涨幅
            php_number_format(5.0 * change / NET_VALUE_CHANGE, 2),
            php_number_format(5.0 * (change - 1.0), 4),   # 影响
            php_number_format(1.0, 4),                    # 汇率调整
        ])

    def test_h_share_row_uses_hkd_adjust(self):
        """港股：``$fChange /= GetAdjustHKD()``，汇率调整格就是 HKD 系数。"""
        row = self.table["rows"][1]
        change = (418.0 / 425.2) / ADJUST_HKD
        self.assertEqual(_cells(row), [
            "00700",
            php_number_format(30.68, 2),
            mysql_round("425.2", 2),
            "-1.69%",
            php_number_format(30.68 * change / NET_VALUE_CHANGE, 2),
            php_number_format(30.68 * (change - 1.0), 4),
            php_number_format(ADJUST_HKD, 4),
        ])

    def test_us_share_row_uses_usd_adjust(self):
        """美股：``$fChange /= GetAdjustUSD()``。"""
        row = self.table["rows"][2]
        change = (81.96 / 81.96) / ADJUST_USD
        self.assertEqual(_cells(row), [
            "PDD",
            php_number_format(7.31, 2),
            mysql_round("81.96", 2),
            "0",                                            # 涨幅 0 → 阈值归零
            php_number_format(7.31 * change / NET_VALUE_CHANGE, 2),
            php_number_format(7.31 * (change - 1.0), 4),
            php_number_format(ADJUST_USD, 4),
        ])

    def test_percentage_cell_colors_follow_site_convention(self):
        """此后涨幅格是 ``(文本, 颜色)``：涨红跌绿、归零灰。

        站点统一口径（``reference_service.percentage_cell``）—— PHP 原文是
        涨红跌**红**都分色，本版按 A 股习惯统一成涨红跌绿。
        """
        cells = self.table["rows"]
        self.assertEqual(cells[0].cells[3], ("0.07%", "red"))
        self.assertEqual(cells[1].cells[3], ("-1.69%", "green"))
        self.assertEqual(cells[2].cells[3], ("0", "gray"))

    def test_total_row_has_six_cells(self):
        """合计尾行（``_echoHoldingItem(false, ...)``）**只有 6 格**。

        PHP 原样就是 6 个元素的数组（缺「汇率调整」），生产端页面那一行也
        只有 6 个 ``<td>``。照抄，不补第七格。
        """
        total = self.table["rows"][3]
        self.assertEqual(len(total.cells), 6)
        change_a = (1501.0 / 1500.0) / 1.0
        change_h = (418.0 / 425.2) / ADJUST_HKD
        change_u = (81.96 / 81.96) / ADJUST_USD
        total_new = (5.0 * change_a + 30.68 * change_h + 7.31 * change_u) / NET_VALUE_CHANGE
        total_change = (5.0 * (change_a - 1.0) + 30.68 * (change_h - 1.0)
                        + 7.31 * (change_u - 1.0))
        self.assertEqual(_cells(total), [
            DISP_ALL_CN,
            php_number_format(5.0 + 30.68 + 7.31, 2),
            "",                                              # PHP 的第 3 格恒空
            "%s%%" % php_number_format((NET_VALUE_CHANGE - 1.0) * 100.0, 2),
            php_number_format(total_new, 2),
            #: 基金自己是 A 股（SH513050）→ 汇率调整 1.0
            php_number_format(total_change * 1.0, 2),
        ])

    def test_total_row_is_not_old_plus_change(self):
        """尾行的「新比例」≠「旧比例」+「影响」—— 两列除数不同，照抄即如此。

        ``新比例`` 除了净值涨幅（``$fNetValueChange``），``影响`` 没有除。
        若有人"修正"成自洽，反而是偏离了 PHP。
        """
        total = _cells(self.table["rows"][3])
        self.assertNotEqual(float(total[1]) + float(total[5]), float(total[4]))

    def test_link_builder_fills_first_cell_href(self):
        """首格链接由路由层注入；不给就退化成纯文本。"""
        seen = []

        def link_builder(symbol):
            seen.append(symbol)
            return "/stock/%s" % symbol

        table = self.service.get_table(make_fund(), link_builder=link_builder)
        self.assertEqual(table["rows"][0].href, "/stock/SH600519")
        self.assertEqual(table["rows"][3].href, "")     # 尾行没有链接
        #: 三只成分股各调一次；尾行不调（PHP 那里 ``$ref == false`` 直接返回）
        self.assertEqual(seen, ["SH600519", "00700", "PDD"])

    def test_row_title_uses_stock_name(self):
        """窄屏时股票名挂在首格 title 上（``RefEchoTableColumn`` 的 hint）。"""
        service = HoldingsService(source=PairSource())
        table = service.get_table(make_fund())
        #: PairSource.get_stock_name 返回 None → 标题为空串
        self.assertEqual(table["rows"][0].title, "")

    def test_adr_refs_returned_with_table(self):
        """``adr_refs`` 随表返回（PHP 的收集动作嵌在同一个循环里）。"""
        with mock.patch.object(hs, "AdrPairReference", RecordingAdrPair):
            table = self.service.get_table(make_fund())
        self.assertEqual(table["adr_refs"], [])


# ---------------------------------------------------------------------------
# 舍入与边界（PHP 口径最容易错的地方）
# ---------------------------------------------------------------------------
class HoldingsRoundingTest(unittest.TestCase):
    """「旧价格」格的 ``mysql_round`` 与缺失历史的兜底。"""

    def setUp(self):
        self.service = HoldingsService()

    def test_close_with_half_cent_rounds_away_from_zero(self):
        """``mysql_round('3.675', 2)`` = ``'3.68'``（PHP half-up）。

        Python 的 ``"%.2f" % 3.675`` 给 ``'3.67'``（二进制值小于 3.675）——
        这一格入参正是 MySQL 的三位小数串，必踩。
        """
        refs = [FakeHoldingRef("SZ000001", "16", "3.7")]
        fund = FakeHoldingsRef(refs=refs, ratio={16: 1.0})
        fund.get_holding_date_history = lambda: {"16": "3.675"}
        table = self.service.get_table(fund)
        self.assertEqual(_cells(table["rows"][0])[2], "3.68")

    def test_missing_history_row_gives_zero_change(self):
        """持仓日没有收盘价：``$fChange = 0``、旧价格 ``'0'``、涨幅 ``'0'``。

        PHP 那里 ``$arHistory[$strStockId]`` 缺键只是 Warning + null，
        随后 ``$fClose > MIN_FLOAT_VAL`` 为假。照抄（不抛错）。
        """
        refs = [FakeHoldingRef("BILI", "14", "106.2")]
        fund = FakeHoldingsRef(refs=refs, ratio={14: 2.0})
        table = self.service.get_table(fund)   # HISTORY 里没有 "14"
        cells = _cells(table["rows"][0])
        self.assertEqual(cells[2], "0")        # mysql_round('', 2) 走 floatval('')
        self.assertEqual(cells[3], "0")
        self.assertEqual(cells[4], "0.00")     # new_ratio = 0
        self.assertEqual(cells[5], "-2.0000")  # ratio_change = 2.0 * (0 - 1)

    def test_zero_close_uses_prev_price_for_percentage(self):
        """持仓日有行但值是 0：``$fClose`` 假 → 涨幅除数回落到**昨收**。

        PHP 的 ``GetPercentage($fDivisor = false, ...)`` 默认值在
        ``$fDivisor == false`` 时生效，而 ``0.0 == false`` 为真。
        """
        refs = [FakeHoldingRef("PDD", "12", "101.0", prev_price="100.0")]
        fund = FakeHoldingsRef(refs=refs, ratio={12: 7.31})
        fund.get_holding_date_history = lambda: {"12": 0.0}
        table = self.service.get_table(fund)
        #: 101/100 - 1 = 1%
        self.assertEqual(_cells(table["rows"][0])[3], "1%")

    def test_history_value_tolerates_int_and_str_keys(self):
        """``$arHistory`` 与 ``$arRatio`` 的键类型两边可能不一致。"""
        self.assertEqual(_history_value({11: 425.2}, "11"), 425.2)
        self.assertEqual(_history_value({"11": 425.2}, 11), 425.2)
        self.assertEqual(_history_value({11: 425.2}, 11), 425.2)
        self.assertEqual(_history_value({}, "11"), 0.0)      # 缺键不抛错


class TotalRowTest(unittest.TestCase):
    """``_total_row`` 直接单测：尾行乘的是**基金自己**的汇率调整。"""

    def test_fund_adjust_multiplies_total_change(self):
        """同一张表里两种汇率口径：数据行用成分股的，尾行用基金的。

        基金是港股持仓（``00700`` 那一行收集 ADR 的那个场景）时，尾行的
        汇率调整就是 HKD 系数 —— 与成分行各调各的不矛盾，PHP 原文如此。
        """
        totals = {"old": 100.0, "new": 101.0, "change": 2.0}
        row = HoldingsService._total_row(1.0234, 0.9063, totals)
        self.assertEqual(len(row.cells), 6)
        self.assertEqual(_cells(row), [
            DISP_ALL_CN, "100.00", "", "2.34%", "101.00",
            php_number_format(2.0 * 0.9063, 2),
        ])

    def test_total_change_negative_zero_becomes_zero(self):
        """``number_format`` 负零归正（PHP 实测 ``-0.004`` → ``'0.00'``）。"""
        totals = {"old": 0.0, "new": 0.0, "change": -0.004}
        row = HoldingsService._total_row(1.0, 1.0, totals)
        self.assertEqual(_cells(row)[5], "0.00")


class PercentageTextTest(unittest.TestCase):
    """``_percentage_text``（``GetPercentageText`` 的文本部分）的阈值归零。"""

    def _text(self, price, close, prev=None):
        ref = FakeHoldingRef("PDD", "12", price, prev_price=prev)
        return _percentage_text(ref, close, float(price))

    def test_below_threshold_returns_bare_zero(self):
        """``|涨幅 × 除数| < 50/10^精度`` → ``'0'``，**不带 %**。

        生产端 01810 / 01060 / 02643 那几行就是 ``'0'``。
        """
        #: 涨 0.0004 元、昨收 1.0：|0.0004 × 1.0| = 0.0004 < 0.5
        self.assertEqual(self._text("1.0004", 1.0), "0")

    def test_above_threshold_has_percent(self):
        self.assertEqual(self._text("1.01", 1.0), "1%")

    def test_trim_trailing_zeros(self):
        """``number_format`` 后 ``rtrim('0')`` / ``rtrim('.')``。"""
        self.assertEqual(self._text("1.10", 1.0), "10%")
        self.assertEqual(self._text("1.006", 1.0), "0.6%")
        self.assertEqual(self._text("1.05", 1.0), "5%")

    def test_exactly_at_threshold_is_float_decided(self):
        """阈值边界 ``|涨幅 × 除数| == 50/10^精度`` 的行为由浮点决定，不钉。

        1.005 在两边都是 0.49999999999998934 < 0.5 → ``'0'``（PHP 同款
        二进制噪声）。这里只记录这个事实，防止后人误以为是公式错。
        """
        self.assertEqual(self._text("1.005", 1.0), "0")


# ---------------------------------------------------------------------------
# ADR 配对收集（方向是最容易搞反的地方）
# ---------------------------------------------------------------------------
class AdrCollectTest(unittest.TestCase):
    """``SqlGetHadrPair`` → ``new AdrPairReference``：H 符号找 ADR 是**反查**。"""

    def _table(self, source, refs=None):
        with mock.patch.object(hs, "AdrPairReference", RecordingAdrPair):
            return HoldingsService(source).get_table(
                FakeHoldingsRef(refs=refs if refs is not None else default_refs()))

    def test_h_share_triggers_reverse_lookup(self):
        """``adrpair`` 表 ``id`` = ADR 侧、``stock_id`` = H 侧。

        所以 SQL 是 ``WHERE f.stock_id = (SELECT id FROM stock WHERE symbol=...)``
        （反查），参数是 **H 符号** 09626，拿回来的才是 ADR 符号。
        """
        source = PairSource(pair_row=("BILI",))
        refs = [FakeHoldingRef("09626", "15", "106.2")]
        table = self._table(source, refs)
        self.assertEqual(len(source.queries), 1)
        sql, params = source.queries[0]
        self.assertIn("adrpair", sql)
        self.assertIn("f.stock_id =", sql)          # 反查方向，不是 f.id =
        self.assertEqual(params, ("09626",))
        self.assertEqual([ref.symbol for ref in table["adr_refs"]], ["BILI"])

    def test_no_pair_row_means_no_adr_ref(self):
        """PHP 的 ``if ($strAdrSymbol = SqlGetHadrPair(...))`` —— 查不到就不建。"""
        source = PairSource(pair_row=None)
        table = self._table(source, refs=[FakeHoldingRef("09626", "15", "106.2")])
        self.assertEqual(table["adr_refs"], [])

    def test_only_h_shares_look_up(self):
        """A / 美股成分股不查配对表：三只默认持仓里只有一只是港股。"""
        source = PairSource(pair_row=None)
        self._table(source)                      # default_refs()：SH600519 / 00700 / PDD
        self.assertEqual(len(source.queries), 1)
        self.assertEqual(source.queries[0][1], ("00700",))


# ---------------------------------------------------------------------------
# 整页装配（EchoAll）
# ---------------------------------------------------------------------------
class HoldingsPageTest(unittest.TestCase):
    """两道守卫 + 说明文字 links 注入。"""

    def _page(self, fund=None, **kwargs):
        service = HoldingsService(fundest_service=RecordingFundEst(),
                                  history_service=FakeHistoryService(),
                                  reference_service=FakeReferenceService())
        return service.get_page("SH513050", holdings_ref=fund or make_fund(),
                                **kwargs)

    def test_page_sections_in_php_order(self):
        """est 三段 → 参考数据 → 测算表 → ADR → counts。"""
        page = self._page()
        self.assertEqual(page["symbol"], "SH513050")
        self.assertEqual(page["title"], "SH513050" + PAGE_TITLE_SUFFIX)
        self.assertEqual(page["description"], PAGE_DESCRIPTION)
        for key in ("est", "est_note", "netvaluehistory", "reference",
                    "holdings", "adr"):
            self.assertIn(key, page)
        self.assertEqual(page["counts"], {"holdings": 3, "adr": 0})

    def test_no_holdings_date_returns_none(self):
        """``if ($ref->GetHoldingsDate())`` 不成立 → 整页不渲染。"""
        self.assertIsNone(self._page(make_fund(date="")))

    def test_no_quote_returns_none(self):
        """``EchoStockGroup()`` 的 ``HasData()`` 守卫（站点没有账号体系，
        只保留 HasData 这一半）。"""
        self.assertIsNone(self._page(make_fund(has_data=False)))

    def test_build_failure_returns_none(self):
        """持仓引用建不出来（连不上库等）→ ``None``，不让整页 500。"""
        service = HoldingsService()
        with mock.patch.object(hs, "StockGetHoldingsReference",
                               side_effect=RuntimeError("no db")):
            self.assertIsNone(service.get_page("SH513050"))

    def test_est_table_uses_injected_fundest_service(self):
        service = HoldingsService(fundest_service=RecordingFundEst(),
                                  history_service=FakeHistoryService())
        page = service.get_page("SH513050", holdings_ref=make_fund())
        self.assertEqual(page["est"], {"kind": "fundest", "rows": []})

    def test_note_links_are_injected(self):
        """links 注入：``/holdings`` 与 ``/stock`` 两个页面共用这句说明。"""
        page = self._page(note_links={
            "position": "/history/fundposition/SH513050",
            "holdings": "/holdings/SH513050",
            "exhaustive": None,
        })
        note = page["est_note"]
        self.assertIn('<a href="/history/fundposition/SH513050">仓位估算</a>', note)
        self.assertIn('<a href="/holdings/SH513050">基金持仓</a>', note)
        self.assertIn("值使用1.00。", note)
        self.assertIn("更新于2026-10-13。", note)

    def test_note_missing_key_degrades_to_plain_text(self):
        """缺键 → 那一段退化成纯文字（``_note_link`` 的 None 分支）。"""
        page = self._page(note_links={"position": "/p"})
        note = page["est_note"]
        self.assertIn('<a href="/p">仓位估算</a>', note)
        self.assertNotIn("<a href", note[len('<a href="/p">仓位估算</a>'):])
        self.assertIn("基金持仓更新于", note)

    def test_note_exhaustive_link_only_when_few_holdings(self):
        """``if ($ref->CountHoldings() <= 4)``（``fundestparagraph.php:168``）。"""
        few = make_fund(count=4)
        many = make_fund(count=37)
        links = {"position": "/p", "holdings": "/h", "exhaustive": "/e"}
        self.assertIn('<a href="/e">穷举持仓比例</a>',
                      self._page(few, note_links=links)["est_note"])
        self.assertNotIn("穷举持仓比例",
                         self._page(many, note_links=links)["est_note"])

    def test_note_without_links_uses_php_shaped_urls(self):
        """``links=None`` 保持旧行为（``ref.stocklink`` 的 PHP 形 URL）——
        既有测试与"直接用服务层"的调用方依赖这个默认值。"""
        page = self._page()
        self.assertIn("fundpositioncn.php", page["est_note"])

    def test_reference_table_precedes_holdings_table(self):
        """④ 参考数据表在 ⑤ 测算表之前（``EchoAll()`` 的顺序）。"""
        page = self._page()
        self.assertIsNotNone(page["reference"])
        self.assertIsNotNone(page["holdings"])

    def test_adr_table_absent_without_pairs(self):
        """没有 ADR 配对时 ⑥ 整段不出现（``EchoAdrhParagraph(array())``）。"""
        page = self._page()
        self.assertIsNone(page["adr"])


class BuildHoldingsPageTest(unittest.TestCase):
    """便捷函数 ``build_holdings_page``：自己建 service 再装配。"""

    def test_smoke(self):
        with mock.patch.object(hs, "StockGetHoldingsReference",
                               return_value=make_fund()):
            page = build_holdings_page("SH513050")
        self.assertEqual(page["title"], "SH513050" + PAGE_TITLE_SUFFIX)
        self.assertEqual(page["holdings"]["count"], 4)


if __name__ == "__main__":
    unittest.main()
