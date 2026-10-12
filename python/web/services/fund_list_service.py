"""基金分组列表服务。

对应 PHP：``php/ui/fundlistparagraph.php`` 的 ``EchoFundListParagraph()`` ——
「基金指数对照表」，每行列出符号、跟踪标的、仓位、校准值、日期和参考值。

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

    def get_group(self, name: str) -> dict[str, Any]:
        """取一个分组的表头与全部数据行。

        列名对齐生产端 ``qdiicn.php`` / ``fundlistcn.php`` 的两张表：

        * 参考数据表：``代码 | 价格 | 涨幅 | 日期 | 时间 | 名称``
          → 对应本页的「基金代码 / 最新价 / 涨跌幅 / 行情日期 / 行情时间 / 名称」；
        * 基金指数对照表：``代码 | 跟踪代码 | 仓位 | 校准值 | 日期 | 参考值``
          → 对应本页的「跟踪代码 / 仓位 / 校准值 / 校准日期」。

        **日期列必须分两栏**：校准值那份日期是 ``calibrationhistory`` 的日期，
        行情那份是行情的日期，两者通常不同（行情每天更新，校准值只在重标定时变）。
        生产端把它们放在不同表里，本页合并成一张表，所以用列名区分开，
        避免把「校准日期」误读成「行情日期」。
        """
        definition = get_fund_group(name)
        if definition is None:
            raise FundGroupNotFoundError(name)

        symbols = definition["symbols"]()
        # 自动校准：对应 PHP ``_qdiigroup.php`` 里 ``foreach ($arLev ...)
        # $leverage_ref->DailyCalibration()`` —— 页面装配时顺手把中间层的
        # 校准值按最新净值重算一遍。
        #
        # **必须在 ``_build_row`` 之前做**：列表里的「校准值」列要显示刚算出来的值。
        # 整个分组只做一次（同一页可能有多只基金共用 QQQ，重复校准会白抓指数行情）。
        self._auto_calibrate(symbols)
        rows = [self._build_row(symbol) for symbol in symbols]
        return {
            "name": name,
            "title": definition["title"],
            "description": definition["description"],
            "columns": [
                "基金代码",
                "名称",
                "跟踪代码",
                "仓位",
                "校准值",
                "校准日期",
                "基金净值",
                "基金净值日期",
                "最新行情日期",
                "行情更新时间",
                "最新价",
                "涨跌幅",
                "溢价率",
                "数据源",
            ],
            "rows": rows,
            "count": len(rows),
        }

    def _build_row(self, symbol: str) -> dict[str, Any]:
        """组装一行；数据缺失时对应字段留空，不抛异常。

        列表页里个别符号缺数据很常见（新股、未校准、停牌），PHP 那边同样会显示
        空白而不是报错，所以这里逐字段容错。

        ``group`` 是符号在 URL 里应归属的分组名，模板据此决定详情链接带上哪个
        分组前缀。``price`` / ``premium`` 来自 stockhistory 与 netvaluehistory。
        """
        row = {
            "symbol": symbol,
            "group": get_symbol_group(symbol),
            "name": self.source.get_stock_name(symbol) or "",
            "est_symbol": "",
            "position": "",
            "calibration": "",
            "calibration_date": "",
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

        calibration = self.source.get_calibration_now(stock_id)
        if calibration is not None:
            row["calibration"] = _text(calibration.close)
            row["calibration_date"] = calibration.date
            # 兼容旧模板/旧测试对 "date" 的引用
            row["date"] = calibration.date

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
