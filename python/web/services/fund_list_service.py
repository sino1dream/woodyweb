"""基金分组列表服务。

对应 PHP：``php/ui/fundlistparagraph.php`` 的 ``EchoFundListParagraph()`` ——
「基金指数对照表」，每行列出符号、跟踪标的、仓位与参考值。

**2026-10-19 列调整**（用户要求，对齐生产端 ``qdiicn.php`` 第二张表）：
去掉「校准值 / 校准日期」两列，增加 EST 七列
（官方EST / EST日期 / 溢价 / 参考EST / 溢价 / 实时EST / 溢价）。
数据来自每行的 :class:`~ref.refobj.QdiiReference`，对应 PHP
``php/ui/fundestparagraph.php`` 的 ``_echoFundEstTableItem()`` ——
生产端把这些列放在**独立的第二张表**里，本页合并成一张。

与 PHP 的差别：PHP 里每行的引用对象（``FundPairReference`` 等）同时负责取数、
校准和渲染；这里把列数据摊平成 dict，由模板渲染，取数逻辑留在
``FundDataSource`` 里，页面层不碰 SQL。
"""

from __future__ import annotations

import os
from typing import Any

from web.bootstrap import install_paths

install_paths()

from calc.fundgroups import get_fund_group, get_fund_group_names, get_symbol_group  # noqa: E402
from sql.quotestore import (  # noqa: E402
    format_premium_rate,
    get_premium_rate,
    stock_get_percentage,
)
from core.stockdata import FundDataSource, MySqlFundDataSource, rtrim0  # noqa: E402
from web.db import get_db  # noqa: E402


class FundGroupNotFoundError(LookupError):
    """分组名不存在。"""


# ---------------------------------------------------------------------------
# 列定义与排序（2026-10-26 用户要求：19 个栏目全部支持升降序）
# ---------------------------------------------------------------------------
#: 列定义：(键, 显示名, 排序类型)。
#:
#: * **键** —— 同时是行字典里的字段名、URL 参数 ``?sort=`` 的取值、以及
#:   表头 ``data-sort`` 的值。三者共用一套名字，少一层映射就少一处能写错；
#: * **类型** —— 决定排序怎么比：``number`` 按浮点比（涨跌幅 / 溢价要剥掉
#:   ``%``），``date`` / ``time`` 按定宽文本比（``2026-10-22``、``13:05:00``
#:   字典序即时间序，不必解析成 datetime），``text`` 按字节序比。
#:
#: **顺序有意义**：表头、:meth:`FundListService.get_group` 返回的 ``columns``
#: 与排序键全部从这一份生成 —— 改列只改这里，别在模板里另抄一遍列名。
FUND_LIST_COLUMNS = (
    ("symbol", "基金代码", "text"),
    ("name", "名称", "text"),
    ("est_symbol", "跟踪代码", "text"),
    ("position", "仓位", "number"),
    ("net_value", "基金净值", "number"),
    ("net_value_date", "基金净值日期", "date"),
    ("quote_date", "最新行情日期", "date"),
    ("quote_time", "行情更新时间", "time"),
    ("price", "最新价", "number"),
    ("change", "涨跌幅", "number"),
    ("premium", "溢价率", "number"),
    ("quote_source", "数据源", "text"),
    ("official_est", "官方EST", "number"),
    ("est_date", "EST日期", "date"),
    ("official_premium", "溢价", "number"),
    ("fair_est", "参考EST", "number"),
    ("fair_premium", "溢价", "number"),
    ("realtime_est", "实时EST", "number"),
    ("realtime_premium", "溢价", "number"),
)

SORT_ASC = "asc"
SORT_DESC = "desc"

#: 键 -> 类型。
SORT_KINDS = {key: kind for key, _, kind in FUND_LIST_COLUMNS}

#: 键 -> 显示名（页面提示"当前按 X 排序"用）。
_LABEL_OF = {key: label for key, label, _ in FUND_LIST_COLUMNS}

#: 某一列**第一次**被点击时给的顺序。
#:
#: 数值列先给降序 —— 看涨跌幅 / 溢价 / 净值时人总是先想看最大那个；
#: 文本与日期列先给升序（时间正序、字典序）。已排序列再点就翻转。
_FIRST_ORDER = {"number": SORT_DESC}
_DEFAULT_FIRST_ORDER = SORT_ASC


def sort_value(displayed: Any, kind: str) -> Any:
    """把**页面显示的文本**解析成可比较的值；空值或解析不了返回 ``None``。

    刻意拿显示文本当排序依据，而不是另存一份原始数值：这样「看到什么就按什么
    排」，不会出现"显示 1.55 但按 1.5468 排"的错位。代价是显示精度一致的
    两个值会判成相等 —— 相等时由 ``sort_rows`` 的稳定性兜住（保持原顺序）。
    """
    text = "" if displayed is None else str(displayed).strip()
    if not text:
        return None
    if kind == "number":
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            return float(text)
        except (TypeError, ValueError):
            return None
    return text


def sort_rows(rows: Any, key: str, order: str = SORT_ASC) -> list:
    """按一列排序；**空值恒排最后**（升序降序都一样）。

    空值放最后是有意的：缺净值的基金不该因为升序就霸占第一屏。
    相等值保持原顺序（Python 的 ``sort`` 稳定），而原顺序是分组定义里
    手工排的顺序 —— 也就是说"没数据差别"时看到的是熟悉的顺序。
    """
    rows = list(rows)
    kind = SORT_KINDS.get(key)
    if kind is None:
        return rows

    filled = []
    empty = []
    for row in rows:
        value = sort_value(row.get(key, ""), kind)
        if value is None:
            empty.append(row)
        else:
            filled.append((value, row))
    # 只按第 0 项比，避免相等时退化成比较 dict（Python 3 会直接 TypeError）
    filled.sort(key=lambda pair: pair[0], reverse=(order == SORT_DESC))
    return [row for _, row in filled] + empty


def build_column_defs(sort_key: str = None, order: str = None) -> list:
    """表头用的列描述：标签 + 这一列的链接该带什么顺序 + 升/降箭头。

    ``next_order`` 在这里算好，模板只负责渲染 —— "点已排序列翻转、点新列用
    该类型的默认顺序"这条规则就只写了一遍，能被单测直接钉住。
    """
    defs = []
    for key, label, kind in FUND_LIST_COLUMNS:
        is_sorted = key == sort_key
        if is_sorted:
            next_order = SORT_ASC if order == SORT_DESC else SORT_DESC
        else:
            next_order = _FIRST_ORDER.get(kind, _DEFAULT_FIRST_ORDER)
        defs.append({
            "key": key,
            "label": label,
            "kind": kind,
            "is_sorted": is_sorted,
            "next_order": next_order,
            "order_hint": "降序" if next_order == SORT_DESC else "升序",
            "mark": ("▼" if order == SORT_DESC else "▲") if is_sorted else "",
        })
    return defs


def _text(value: Any) -> str:
    """把数值转成页面显示文本，沿用 PHP 的 rtrim0() 口径。"""
    if value is None:
        return ""
    if isinstance(value, float):
        return rtrim0(value)
    return str(value)


def _auto_calibrate_enabled() -> bool:
    """页面访问时是否自动校准中间层。

    默认**开启** —— 这是 PHP 的既有行为（``_qdiigroup.php`` 在页面装配时调
    ``DailyCalibration()``）。设 ``WOODY_AUTO_CALIBRATE=0`` 可关掉，
    用于只想看库里现有校准值、不希望页面触发写库的场景。
    """
    value = os.environ.get("WOODY_AUTO_CALIBRATE", "").strip().lower()
    if value in ("0", "false", "no", "off"):
        return False
    return True


def _to_float(value: Any) -> Any:
    """把可能是字符串的数值转成 float；无效时返回 ``None``。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _change_percent(price: Any, prev_price: Any) -> Any:
    """涨跌幅（%），复用 PHP ``StockGetPercentage()`` 的口径。

    ``price`` / ``prev_price`` 是数据源返回的**字符串**（``RecentQuote`` 里就是
    字符串，因为要保留 rtrim0 口径）。任一无效或昨收为 0 时返回 ``None``，
    让页面显示空白 —— 生产端在没有昨收时也是留空，而不是显示 0%。
    """
    current = _to_float(price)
    previous = _to_float(prev_price)
    if current is None or previous is None or previous == 0:
        return None
    return stock_get_percentage(previous, current)


def format_change_percent(change: Any, digits: int = 2) -> str:
    """把涨跌幅格式化成带符号的百分比文本，口径同 ``format_premium_rate()``。

    生产端显示形如 ``0.44%`` / ``1.8%`` / ``-0.17%``：两位小数后去掉多余的 0，
    所以 ``1.80`` 显示成 ``1.8``。正数不加 ``+``（与生产端一致）。
    """
    if change is None:
        return ""
    text = "%.*f" % (digits, float(change))
    text = text.rstrip("0").rstrip(".")
    if text in ("", "-0", "0"):
        return "0"
    return text + "%"


def _premium_class(text: Any) -> str:
    """溢价格的上色类名（涨红跌绿，与页内涨跌幅 / 溢价率同一套）。

    判据与 ``reference_service.percentage_cell`` 相同：带 ``%`` 且以 ``-``
    开头算跌。没有 ``%``（``GetPercentageText()`` 返回 ``"0"`` 或空串，
    即没有价格可比）时不上色 —— 与那一格留空是两回事，别把灰色逻辑搬过来。
    """
    displayed = "" if text is None else str(text)
    if not displayed.endswith("%"):
        return ""
    return "negative" if displayed.startswith("-") else "positive"


class FundListService:
    """把分组符号列表补齐成可渲染的表格行。

    ``quotes_provider`` 提供实时行情（``quoteupdater.QuotesUpdater``，只要
    有 ``get(symbol)`` 方法即可）。传 ``None`` 时退化为只读库里的收盘价，
    这样在没有实时行情进程时页面依然可用。

    ``auto_calibrate`` 控制页面访问时是否自动校准中间层（见
    :meth:`_auto_calibrate`）。上一轮校准的具体动作记在
    ``self.calibration_notes``，供页面/日志展示。
    """

    def __init__(self, source: FundDataSource, quotes_provider: Any = None,
                 auto_calibrate: bool = None):
        self.source = source
        self.quotes_provider = quotes_provider
        #: 是否在页面访问时自动校准中间层（对应 PHP 的 ``DailyCalibration()``）。
        #:
        #: 默认由环境变量 ``WOODY_AUTO_CALIBRATE`` 决定，**默认开启**：
        #: 这是 PHP 的既有行为（页面装配时顺手校准），关掉会让校准值永远停在上次手动运行。
        #: 测试替身没有 ``connection``，``_auto_calibrate`` 会因此静默跳过。
        self.auto_calibrate = _auto_calibrate_enabled() if auto_calibrate is None else bool(auto_calibrate)
        #: 最近一次自动校准的动作记录（写过库的才记），供页面/日志展示。
        self.calibration_notes: list = []

    def list_groups(self) -> list[dict[str, str]]:
        """返回全部分组，供首页/导航列出入口。"""
        groups = []
        for name in get_fund_group_names():
            definition = get_fund_group(name)
            groups.append(
                {
                    "name": name,
                    "title": definition["title"],
                    "description": definition["description"],
                    "count": str(len(definition["symbols"]())),
                }
            )
        return groups

    def _auto_calibrate(self, symbols: Any) -> list:
        """页面访问时自动校准本组用到的**中间层**。

        对应 PHP ``woody/res/php/_qdiigroup.php:81``：

        ```php
        foreach ($arLev as $strSymbol)
        {
            $leverage_ref = new FundPairReference($strSymbol);
            ...
            $leverage_ref->DailyCalibration();
        }
        ```

        ## 三条必须遵守的约束

        1. **校准对象是中间层**（``QQQ``），不是基金 —— 见
           ``autocalibration`` 模块 docstring 里对这个坑的说明；
        2. **不能影响页面**：任何异常都吞掉并记进 ``self.calibration_notes``，
           页面照常渲染（PHP 那边校准失败也只是不写库）；
        3. **同一分组只做一次**（按符号去重）—— 同页多只基金常共用 QQQ，
           重复校准会白抓指数行情。
        """
        notes = []
        if not self.auto_calibrate:
            self.calibration_notes = notes
            return notes
        connection = getattr(self.source, "connection", None)
        if connection is None:
            self.calibration_notes = notes
            return notes

        try:
            from updater.autocalibration import build_auto_calibrator
        except ImportError:
            self.calibration_notes = notes
            return notes

        try:
            calibrator = build_auto_calibrator(self.source)
        except Exception as exc:
            self.calibration_notes = ["自动校准不可用：%s: %s" % (type(exc).__name__, exc)]
            return self.calibration_notes

        seen = set()
        for symbol in symbols:
            try:
                middle = calibrator.middle_symbol_of(symbol)
            except Exception:
                continue
            if not middle or middle in seen:
                continue
            seen.add(middle)
            try:
                outcome = calibrator.calibrate_middle(middle, apply=True)
            except Exception as exc:
                notes.append("%s 自动校准失败：%s: %s" % (middle, type(exc).__name__, exc))
                continue
            if outcome.written:
                notes.append(outcome.describe())
        self.calibration_notes = notes
        return notes

    def get_group(self, name: str, sort: str = None, order: str = None) -> dict[str, Any]:
        """取一个分组的表头与全部数据行。

        列名对齐生产端 ``qdiicn.php`` 的**两张表**（本页合并成一张）：

        * 参考数据表：``代码 | 价格 | 涨幅 | 日期 | 时间 | 名称``
          → 对应本页的「基金代码 / 最新价 / 涨跌幅 / 最新行情日期 /
          行情更新时间 / 名称」；
        * 估值表（``fundestparagraph.php::_echoFundEstTableItem()``）：
          ``代码 | 官方EST | EST日期 | 溢价 | 参考EST | 溢价 |
          实时EST | 溢价`` → 对应本页末尾七列。

        **日期列分两栏**：EST 日期是估值标的的行情日期，行情日期是基金
          自己的，两者通常不同。基金净值的日期（T+1 公布）又与它们不同，
          所以三处各占一列，不合并。

        ~~原「校准值 / 校准日期」两列 2026-10-19 删除~~：那是
        ``fundlistparagraph.php`` 的旧列，生产端当前页面没有；校准值仍由
        ``_auto_calibrate()`` 在取数前算好（EST 要用它），只是不再显示。

        ## 排序（2026-10-26）

        ``sort`` 是 :data:`FUND_LIST_COLUMNS` 里的键，``order`` 是
        ``asc`` / ``desc``。**不认识的键直接忽略**（返回分组原顺序），
        不认识的 ``order`` 退化成该类型的默认顺序 —— 表头链接虽然是页面
        生成的，但 URL 是用户可编的，别让它变成 500。

        PHP 的列表页没有排序（``?sort=symbol`` / ``?sort=premium`` 是
        ``/qdii`` 那一页的估值表），所以这里是本地增强，不是迁移。
        """
        definition = get_fund_group(name)
        if definition is None:
            raise FundGroupNotFoundError(name)

        symbols = definition["symbols"]()
        # 自动校准：对应 PHP ``_qdiigroup.php`` 里 ``foreach ($arLev ...)
        # $leverage_ref->DailyCalibration()`` —— 页面装配时顺手把中间层的
        # 校准值按最新净值重算一遍。
        #
        # **必须在 ``_build_row`` 之前做**：EST 三档估值拿的是刚算出来的校准值。
        # 整个分组只做一次（同一页可能有多只基金共用 QQQ，重复校准会白抓指数行情）。
        self._auto_calibrate(symbols)
        rows = [self._build_row(symbol) for symbol in symbols]

        sort_key = sort if sort in SORT_KINDS else None
        sort_order = None
        if sort_key is not None:
            sort_order = order if order in (SORT_ASC, SORT_DESC) else None
            if sort_order is None:
                sort_order = _FIRST_ORDER.get(
                    SORT_KINDS[sort_key], _DEFAULT_FIRST_ORDER)
            rows = sort_rows(rows, sort_key, sort_order)

        return {
            "name": name,
            "title": definition["title"],
            "description": definition["description"],
            #: 纯标签列表（表头文字）。保留它是因为外界/测试按标签比对列；
            #: 模板用下面的 ``column_defs``（带排序状态）。
            "columns": [label for _, label, _ in FUND_LIST_COLUMNS],
            "column_defs": build_column_defs(sort_key, sort_order),
            #: 当前排序状态；都没设时是空串，模板据此决定是否显示"已排序"提示。
            "sort_key": sort_key or "",
            "sort_order": sort_order or "",
            #: 当前排序列的显示名（页面提示用；没排序时为空串）。
            "sort_label": _LABEL_OF.get(sort_key, "") if sort_key else "",
            "sort_order_text": "" if not sort_order else (
                "降序" if sort_order == SORT_DESC else "升序"),
            "rows": rows,
            "count": len(rows),
        }

    def _build_row(self, symbol: str) -> dict[str, Any]:
        """组装一行；数据缺失时对应字段留空，不抛异常。

        列表页里个别符号缺数据很常见（新股、未校准、停牌），PHP 那边同样会显示
        空白而不是报错，所以这里逐字段容错。

        ``group`` 是符号在 URL 里应归属的分组名，模板据此决定详情链接带上哪个
        分组前缀。``price`` / ``premium`` 来自 stockhistory 与 netvaluehistory。
        末尾十一个键（``official_est`` … ``realtime_premium_class``）来自
        :meth:`_build_est`，对应生产端估值表的七列。
        """
        row = {
            "symbol": symbol,
            "group": get_symbol_group(symbol),
            "name": self.source.get_stock_name(symbol) or "",
            "est_symbol": "",
            "position": "",
            "net_value": "",
            "net_value_date": "",
            "quote_date": "",
            "quote_time": "",
            "quote_source": "",
            "change": "",
            "change_class": "",
            "premium": "",
            "premium_class": "",
        }
        # EST 七列：放在最前面合并，这样下面 ``stock_id is None`` 的提前返回
        # 也照样带着这十个键（模板少判一层，未知符号整行留空）。
        row.update(self._build_est(symbol))

        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            return row

        # 跟踪代码：对应生产端「基金指数对照表」的"跟踪代码"列（如 ^NDX）。
        # 优先取 est_symbol（估值用的中间层标的），退回 pair_symbol（配对标的）。
        profile = None
        get_profile = getattr(self.source, "get_fund_profile", None)
        if get_profile is not None:
            try:
                profile = get_profile(symbol)
            except Exception:
                profile = None
        if profile is not None:
            row["est_symbol"] = getattr(profile, "est_symbol", None) or getattr(
                profile, "pair_symbol", None
            ) or ""

        position = self.source.get_position(stock_id)
        if position is not None:
            row["position"] = _text(position)

        # 基金净值与净值日期：对应生产端「基金净值比较」表的"净值 / 日期"两列。
        # 数据来自 netvaluehistory（官方公布的单位净值），不是实时行情 ——
        # 与「最新价」是两回事：净值一天一个数，价格盘中一直动。
        # 溢价率也是拿它算的（场内价 / 前一日净值 - 1）。
        net_value = self._read_latest_net_value(stock_id)
        if net_value not in (None, ""):
            row["net_value"] = _text(net_value)
        net_value_date = self._read_latest_net_value_date(stock_id)
        if net_value_date:
            row["net_value_date"] = str(net_value_date)

        price = None
        quote = self._read_quote(symbol)
        if quote is not None and quote.price:
            # 优先用最近一次行情快照（可能已过 TTL，见 _read_quote 说明）。
            # 把日期/时间/数据源都带上，页面能看出这是什么时候的行情。
            price = quote.price
            row["price"] = _text(quote.price)
            row["quote_date"] = quote.date
            row["quote_time"] = quote.time
            row["quote_source"] = quote.source
            change = _change_percent(quote.price, quote.prev_price)
            if change is not None:
                row["change"] = format_change_percent(change)
                # 涨红跌绿，与生产端 <font color="red"> / <font color="black"> 一致
                row["change_class"] = "negative" if change < 0 else "positive"
        else:
            # 连快照都没有时才退回库里的收盘价（quotecli.py 落库的那条）。
            # stockhistory 只有日期没有时间，所以时间列留空；
            # 涨跌幅此时也拿不到昨收，同样留空 —— 不编造。
            price = self._read_latest_price(stock_id)
            if price is not None:
                row["price"] = _text(price)
                previous = self._read_prev_price_before_latest(stock_id)
                change = _change_percent(price, previous)
                if change is not None:
                    row["change"] = format_change_percent(change)
                    row["change_class"] = "negative" if change < 0 else "positive"
                row["quote_date"] = self._read_latest_price_date(stock_id) or ""
                row["quote_source"] = "stockhistory"

        net_value = self._read_latest_net_value(stock_id)
        premium = get_premium_rate(price, net_value)
        if premium is not None:
            row["premium"] = format_premium_rate(premium)
            # 模板用来上色：溢价（正）红、折价（负）绿
            row["premium_class"] = "negative" if premium < 0 else "positive"

        return row

    def _build_est(self, symbol: str) -> dict[str, Any]:
        """EST 七列 —— 对应 PHP ``fundestparagraph.php::_echoFundEstTableItem()``。

        生产端 ``qdiicn.php`` 把这三档估值放在**第二张表**里
        （``EchoFundArrayEstParagraph``），本页合并成一张表，列名与那张表一致：
        官方EST / EST日期 / 溢价 / 参考EST / 溢价 / 实时EST / 溢价。

        三档的判据全部来自 :class:`~ref.refobj.QdiiReference`
        （``build_qdii_reference``，内部委托 ``fundestcalc`` 计算）：

        * **官方EST** —— ``GetOfficialNetValue()``。算不出来的基金 PHP 根本
          不进 EST 表，这里表现为七列全空（行还在：行情表里有它）；
        * **参考EST / 实时EST** —— 可能没有（PHP 的 ``GetFairNetValue()`` /
          ``GetRealtimeNetValue()`` 返回 false）。**留空、不补位**，
          与 PHP 行里的两个 ``if`` 一致。

        溢价 = ``(场内价 / 该档估值 - 1) × 100%``，即 PHP 的
        ``GetPercentageDisplay($fOfficialPrice)``（除数是估值、被除数默认是
        现价）—— 生产端实测 SH513100：2.345 / 1.989 - 1 = 17.9%，对得上。

        **全程容错**：建引用对象或算估值抛异常时七列全空。分组页是列表，
        一只基金的估值算错不该让整页 500（PHP 那边 fatal，我们不跟）。
        """
        est = {
            "official_est": "", "est_date": "",
            "official_premium": "", "official_premium_class": "",
            "fair_est": "", "fair_premium": "", "fair_premium_class": "",
            "realtime_est": "", "realtime_premium": "", "realtime_premium_class": "",
        }
        try:
            #: 延迟导入：``ref.refobj`` 会反向用到 ``web`` 之外的包，
            #: 模块级导入容易和 ``app`` 工厂的形成环，用到时再进。
            from ref.refobj import build_qdii_reference

            ref = build_qdii_reference(symbol, source=self.source)
        except Exception:
            return est

        official = ref.get_official_net_value()
        if not official:
            # PHP 的守卫 ``if ($fund->GetOfficialNetValue())``：官方估值没有的
            # 基金不进 EST 表。这里行还在（行情表里有它），只是七列全空。
            return est

        est["official_est"] = _text(ref.get_price_display(official))
        est["est_date"] = _text(ref.get_official_date())
        premium = ref.get_percentage_text(official)
        est["official_premium"] = _text(premium)
        est["official_premium_class"] = _premium_class(premium)

        fair = ref.get_fair_net_value()
        if fair:
            est["fair_est"] = _text(ref.get_price_display(fair))
            fair_premium = ref.get_percentage_text(fair)
            est["fair_premium"] = _text(fair_premium)
            est["fair_premium_class"] = _premium_class(fair_premium)

        realtime = ref.get_realtime_net_value()
        if realtime:
            est["realtime_est"] = _text(ref.get_price_display(realtime))
            realtime_premium = ref.get_percentage_text(realtime)
            est["realtime_premium"] = _text(realtime_premium)
            est["realtime_premium_class"] = _premium_class(realtime_premium)

        return est

    def _read_quote(self, symbol: str) -> Any:
        """取**最近一次**行情快照。

        顺序很重要：

        1. ``get_last_stored()`` —— 读 ``realtimequote`` 表里那条快照，**不判 TTL**。
           ``QuotesUpdater`` 的 TTL 默认只有 60 秒，是给"要不要重新抓"用的；页面要
           显示的是"最近一次行情是什么时候的"。若只用 ``get()``，只要抓取进程没在跑，
           行情列、时间列、数据源列就全变空白 —— 实测就是这个现象。
        2. ``get()`` —— 回落。用于没有配 store 的场景（只靠进程内存缓存，例如
           测试替身、或不想落库的部署），此前的行为不能因此退化。

        两者都取不到返回 ``None``，调用方再回退到 ``stockhistory`` 的收盘价。
        """
        if self.quotes_provider is None:
            return None

        stored_reader = getattr(self.quotes_provider, "get_last_stored", None)
        if stored_reader is not None:
            try:
                quote = stored_reader(symbol)
            except Exception:
                quote = None
            if quote is not None and getattr(quote, "price", ""):
                return quote

        cached_reader = getattr(self.quotes_provider, "get", None)
        if cached_reader is None:
            return None
        try:
            return cached_reader(symbol)
        except Exception:
            return None

    def _read_prev_price_before_latest(self, stock_id: str) -> Any:
        """回退路径下的昨收：取最新那条收盘价**之前**的一条。

        只在没有行情快照、价格来自 ``stockhistory`` 时用，这样涨跌幅至少能算出来，
        而不是留空。数据源没提供 ``get_latest_history_date`` 时无法定位最新日期，
        直接返回 ``None``（页面留空，不编造）。
        """
        latest_date = self._read_latest_price_date(stock_id)
        if not latest_date:
            return None
        reader = getattr(self.source, "get_prev_history_close", None)
        if reader is None:
            return None
        try:
            return reader(stock_id, latest_date)
        except Exception:
            return None

    def _read_latest_price(self, stock_id: str) -> Any:
        """取最新市价；数据源没有这个方法时返回 ``None``。"""
        reader = getattr(self.source, "get_latest_history_close", None)
        return reader(stock_id) if reader else None

    def _read_latest_price_date(self, stock_id: str) -> Any:
        """取"最新市价"对应的日期。

        只在没有实时行情、退回库里收盘价时才用 —— 有实时行情时日期以行情为准。
        数据源没提供这个方法（如测试替身）就返回空串，页面留白。
        """
        reader = getattr(self.source, "get_latest_history_date", None)
        if reader is None:
            return ""
        try:
            return reader(stock_id) or ""
        except Exception:
            return ""

    def _read_latest_net_value(self, stock_id: str) -> Any:
        """取最新单位净值：先查 netvaluehistory，再退回 stockhistory。

        基金靠净值、ETF 靠行情，两边都要能取到；取不到时溢价率留空。
        """
        netvalue_reader = getattr(self.source, "get_latest_net_value_close", None)
        if netvalue_reader:
            value = netvalue_reader(stock_id)
            if value is not None:
                return value
        return self._read_latest_price(stock_id)

    def _read_latest_net_value_date(self, stock_id: str) -> Any:
        """取最新单位净值对应的日期（页面「基金净值日期」列）。

        **必须与 :meth:`_read_latest_net_value` 走同一条回退链**，
        否则会出现"净值取自 netvaluehistory、日期取自 stockhistory"的错配 ——
        两个表的最后日期通常不同（净值 T+1 才公布），配错会让人以为数据串了。

        ``netvaluehistory`` 里没有该符号时退回行情日期
        （对应 ``_read_latest_net_value`` 的第二条路）。
        """
        date_reader = getattr(self.source, "get_latest_net_value_date", None)
        if date_reader:
            try:
                value = date_reader(stock_id)
            except Exception:
                value = None
            if value:
                return value
        return self._read_latest_price_date(stock_id)


def default_fund_list_service_factory() -> FundListService:
    """创建使用当前请求数据库连接与实时行情缓存的列表服务。"""
    from updater.quoteupdater import get_updater

    return FundListService(MySqlFundDataSource(get_db()), quotes_provider=get_updater())
