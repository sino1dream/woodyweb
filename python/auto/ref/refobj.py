"""引用对象层：``MysqlReference`` / ``FundReference`` / ``QdiiReference``。

对应 PHP 源码：

* ``php/stock/mysqlref.php`` → :class:`MysqlReference`
* ``php/stock/fundref.php``  → :class:`FundReference`
* ``php/stock/qdiiref.php``  → :class:`QdiiReference`（PHP 里叫 ``_QdiiReference``）
  与四个地区子类；文件里的自由函数（``QdiiGetCalibration`` / ``QdiiGetVal`` /
  ``QdiiGetPeerVal`` / ``QdiiGetEstSymbol`` 等）**已经在 ``fundcalc`` 里**，
  本模块直接复用，不重复实现。
* ``php/stock/cnyref.php``   → :class:`CnyReference`（``FundReference`` 的
  ``SetForex()`` 要用它，只有 20 行，一并迁过来）

## 这一层在解决什么

PHP 的引用对象是"**一个有状态的符号**"：它同时持有行情（来自新浪）、
库里的 id/净值/仓位/校准值，以及三个估值。页面上几乎所有数字都从它身上取。
所以它不是"又一套算法"，而是**把已经迁好的那些能力按 PHP 的字段语义组织起来**。

## 三条刻意的设计取舍

1. **不在构造时联网**。PHP 的 ``FundReference::LoadData()`` 会调
   ``LoadSinaFundData()`` 抓基金名（还会把 GB2312 名字转成 UTF-8）。本版名称
   由数据源提供（``navsource`` 已实现名称抓取），构造函数只做纯组装，
   这样引用对象可以在没有网络的环境下被测试和使用。
2. **三个 EST 的算法不在这里实现**。`fundestcalc.EstimateCalculator` 是官方 /
   参考 / 实时三个 EST 的**唯一实现**，本模块的 ``est_net_value()`` 只负责
   "调闸门 + 把结果填进字段"。理由写在 :meth:`QdiiReference.est_net_value`
   的注释里 —— 本项目出过的最大一次事故（估值 4.59 变 20245）就是**两条实现
   并存、其中一条口径不同**造成的。
3. **外部依赖全部可注入**：数据源 ``source``、行情加载 ``quote_loader``、
   估算器 ``estimator``、基金场内行情引用 ``stock_ref``。数据源缺少某个方法时
   走 :meth:`MysqlReference._call` 的兜底返回 ``None``，与 PHP 里"取不到就是
   false"的行为对齐。

数据库读写只在**显式调用**（``update_est_net_value`` / ``insert_fund_calibration``）
时发生；构造对象本身不写库。
"""

from __future__ import annotations

from typing import Any, Callable

from calc.fundcalc import (
    fund_adjust_position,
    fund_reverse_adjust_position,
    qdii_est_symbol,
    qdii_eu_est_symbol,
    qdii_get_calibration,
    qdii_get_peer_value,
    qdii_get_value,
    qdii_hk_est_symbol,
    qdii_jp_est_symbol,
)
from ref.stockref import StockReference, is_zero_string

#: ``php/debug.php:13``
NETVALUE_PRECISION = 4

#: 四个地区的（中间价符号, 离岸/即期外汇符号）。对应 ``qdiiref.php`` 底部那四个
#: 子类的构造参数。
QDII_REGIONS = {
    "us": ("USCNY", "fx_susdcny"),
    "hk": ("HKCNY", "fx_shkdcny"),
    "jp": ("JPCNY", "fx_sjpycny"),
    "eu": ("EUCNY", "fx_seurcny"),
}

#: 查 ``fundpair``：``id`` = 本体、``stock_id`` = 被跟踪标的。
#:
#: 对应 PHP ``StockPairSql::GetPairSymbol()``（``php/sql/sqlstockpair.php:38``）::
#:
#:     if ($strStockId = $sql->GetId($strSymbol))
#:         if ($strPairId = $this->ReadPair($strStockId))
#:             return $sql->GetStockSymbol($strPairId);
_PAIR_SYMBOL_SQL = (
    "SELECT p.symbol FROM fundpair f "
    "JOIN stock p ON p.id = f.stock_id "
    "WHERE f.id = (SELECT id FROM stock WHERE symbol = %s) LIMIT 1"
)


def _lookup_pair_symbol(source: Any, symbol: str) -> Any:
    """按符号查它在 ``fundpair`` 里跟踪的标的；没有返回 ``None``。

    ``^NDX`` → ``hf_NQ``（纳斯达克 100 指数跟踪 NQ 期货）就是这么来的，
    也就是生产端 ``sz161130cn.php`` 参考数据表第 3 行那个 ``hf_NQ``。
    """
    connection = getattr(source, "connection", None)
    if connection is None:
        return None
    cursor = connection.cursor()
    try:
        cursor.execute(_PAIR_SYMBOL_SQL, (symbol,))
        row = cursor.fetchone()
    except Exception:
        return None
    finally:
        cursor.close()
    if not row:
        return None
    # 游标行有 tuple / dict 两种形状，都认（与 reference_service 的口径一致）
    if isinstance(row, dict):
        return row.get("symbol") or None
    return row[0] or None


def _build_pair_reference(symbol: str, source: Any) -> Any:
    """给配对标的建引用对象。

    PHP 这里是 ``new MyStockReference($strPair)``，而 ``MyStockReference``
    未迁移，所以用 :class:`EstReference` 顶上 —— 参考数据表只需要
    "价格 / 涨幅 / 日期 / 时间"，它够用。

    **刻意用 ``EstReference`` 而不是 ``FundPairReference``**：后者构造时还会
    查一次自己的配对，形成递归（``^NDX → hf_NQ → hf_NQ 的配对 → …``）。
    PHP 靠 ``IsSinaFutureUS()`` 判断打断这个环，本版用更直白的办法 ——
    配对引用本身不再找配对。
    """
    return EstReference(symbol, source=source)


def gb_to_utf8(text: Any) -> Any:
    """对应 PHP ``GbToUtf8()``（``php/gb2312.php``）。

    PHP 那边是**手写 GB2312 → Unicode 查表**再拼 UTF-8 字节。Python 标准库的
    ``gb18030`` 是 GB2312 的超集，直接解码即可，结果一致。

    入参有两种形态，都要处理：

    * ``bytes`` —— 抓回来的原始字节，直接解码；
    * ``str`` —— 字节流被当成单字节字符读进来的那种（每个字节一个码位），
      先按 latin-1 还原成字节再解码。**已经是中文的 str 会在这一步抛
      ``UnicodeEncodeError``**，此时原样返回（说明不需要转换）。
    """
    if text is None:
        return None
    if isinstance(text, (bytes, bytearray)):
        return bytes(text).decode("gb18030", errors="replace")
    try:
        return text.encode("latin-1").decode("gb18030")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


class MysqlReference(StockReference):
    """对应 ``php/stock/mysqlref.php`` 的 ``MysqlReference``。

    PHP 的构造函数依次做四件事，这里保持一致（顺序有意义）：

    ```php
    $this->SetTimeZone();
    $this->LoadData();                                  // 子类覆盖
    if (IsZeroString($this->strPrice)) $this->strPrice = $this->strPrevPrice;
    $this->_loadSqlId(...);                             // 净值条数 + 仓位
    ```
    """

    def __init__(self, symbol: str, source: Any = None, quote_loader: Callable[[str], Any] = None):
        super().__init__(symbol, quote_loader=quote_loader)
        self.source = source

        #: ``php`` 的 ``$bConvertGB2312``：新浪基金名是 GB2312 编码的
        self.convert_gb2312 = False
        #: ``$fFactor`` —— calibrationhistory 的 close，即校准值
        self.factor = 1.0
        #: ``$fRatio`` —— 仓位（fundposition），没有时回落默认仓位
        self.ratio = 1.0
        #: ``$iNetValueCount`` —— netvaluehistory 里该符号的条数
        self.net_value_count = 0
        #: ``$strSqlId``
        self.stock_id = None

        self.load_data()
        # 行情装填：**只读库快照，不联网**（见 :meth:`load_quote_data`）。
        # **必须在 ``load_data()`` 之后**：子类的 ``load_data`` 可能已经把
        # ``has_data`` 按"有没有净值"设过一次，这里以行情为准覆盖。
        # 快照没有该符号时留空，联网兜底由请求期显式触发
        # （``refquote.refresh_quotes``）—— 构造期联网会让每个请求都慢一拍。
        self.quote_source = self.load_quote_data()
        # 外链：PHP 在 ``LoadSinaFundData()`` 里 ``SetExternalLink(GetSinaFundLink())``。
        # **必须有内容** —— 参考数据表首格就是它，为空会让整行少一格、列错位。
        # 取不到外链时 ``get_sina_fund_link()`` 会退回纯符号文本（与 PHP 一致）。
        self.set_external_link(self._build_external_link())
        if is_zero_string(self.get_price()):
            self.str_price = self.get_prev_price()

        if not self.stock_id:
            self._load_sql_id(self.get_symbol())

        if self.stock_id:
            self.net_value_count = self._count_net_value(self.stock_id)
            self.ratio = self._read_position(self.stock_id)

    # -- 子类覆盖点 -------------------------------------------------------
    def load_data(self) -> None:
        """对应 ``LoadData()``。基类不加载任何东西（PHP 里也只 ``SetHasData(false)``）。"""
        self.set_has_data(False)

    # -- 行情装填 ---------------------------------------------------------
    def load_quote_data(self) -> str:
        """装填行情：**只读库快照**。

        对应 PHP ``MysqlReference`` 构造流程里那次行情加载（PHP 是
        ``LoadSinaData()``，每次请求同步抓新浪）。本项目部署形态不同 ——
        已有 ``quoteupdatercli --daemon`` 把行情写进 ``realtimequote``，
        所以构造期**只读快照**。

        **为什么不在这里联网**：``QdiiReference`` 构造里还会算一次实时 EST，
        若构造期联网，每个页面请求都会去抓一遍新浪（既慢又给站方添压力）。
        联网兜底做成请求期显式调用 ``refquote.refresh_quotes()``。

        返回 ``'snapshot'`` / ``'none'``，便于判断数据来源。
        """
        try:
            from ref.refquote import load_quote_into
        except ImportError:
            self.set_has_data(False)
            return "none"
        return load_quote_into(self, source=self.source)

    @property
    def connection(self) -> Any:
        """数据库连接（从数据源转发）。

        ## 为什么要有这个属性

        本项目里"引用对象 → 数据库连接"一直走``ref.source.connection``
        （``_count_net_value`` / ``load_daily_sql_data`` 都是这么写的）。
        但有些消费方（例如 ``StockHistory``）拿到的只是"一个引用对象"，
        并不知道数据源在哪，于是会写成 ``getattr(ref, "connection", None)``
        —— 那**永远拿到 ``None``**，查询被静默跳过。

        实测踩到过：``SmaService._build_history()`` 建 ``StockHistory`` 时
        没传 ``source``，``StockHistory`` 回退到 ``self.source = ref``，
        于是 ``ref.connection`` 为空 → 均线全是 0 → 页面上的均线表整段消失。
        **不报错**，只是没有表。

        所以在这里把连接转发出来：数据源挂在 ``self.source`` 上，
        引用对象就把 ``connection`` 暴露给"只拿到 ref"的调用方。
        数据源没有连接（比如纯内存的假源）时返回 ``None``。
        """
        return getattr(self.source, "connection", None)

    def _build_external_link(self) -> str:
        """首格那个外链；没有对应外链时退回符号文本。
        走 :func:`externallink.get_sina_stock_link` 的**通用分发**（对应 PHP
        ``GetSinaStockLink()``）：基金、A 股、港股、美股/美股指数、期货、外汇
        各有各的 URL 形状。参考数据表会把这几类混在一张表里，所以必须按类型分派，
        不能只认基金。

        **首格必须有内容**：为空会让整行少一格、后面所有列错位（实测踩过）。
        取不到外链时 ``get_sina_stock_link()`` 会退回纯符号文本（与 PHP 一致）。
        """
        symbol = self.get_symbol()
        try:
            from ref.externallink import get_sina_stock_link
        except ImportError:
            return symbol
        try:
            return get_sina_stock_link(self)
        except Exception:
            return symbol

    def get_stock_link(self) -> str:
        """对应 ``StockSymbol::GetStockLink()``：**站内**详情页链接。

        PHP 原文（``php/stock/stocksymbol.php:1173``）::

            if ($str = GetGroupStockLink($this->GetSymbol())) return $str;
            return $this->GetMyStockLink();

        产出形如 ``/woody/res/sh513100cn.php`` 的链接 —— 生产端
        ``qdiicn.php`` 估值表首格正是这个形状。

        **为什么不能复用 ``get_external_link()``**：那边是新浪行情页
        （外站、``target="_blank"``），这边是自己的详情页。两者在页面上
        是紧挨着的两张表（参考数据表用外链、估值表用站内链接），
        复用会让估值表首格跳去新浪。这个错曾经真实存在过：
        ``GetStockLink`` 被别名成 ``get_external_link``，详情页只有一张表
        所以没暴露，直到分类页把两张表摆到一起做逐格对照才抓出来。
        详见 :mod:`ref.stocklink` 的模块 docstring。
        """
        from ref.stocklink import get_stock_link

        try:
            return get_stock_link(self.get_symbol())
        except Exception:
            # 首格必须有内容：为空会让整行少一格、后面所有列错位
            return self.get_symbol()

    # -- 取数兜底 ---------------------------------------------------------
    def _call(self, name: str, *args: Any) -> Any:
        """调数据源的方法；数据源没有该方法或抛异常时返回 ``None``。

        与 PHP 的行为对齐：PHP 里这些库调用取不到就是 ``false``，不会让页面挂。
        **异常在这里被吞掉是有意的**，但只用于"取数"，不用于"写库"。
        """
        method = getattr(self.source, name, None)
        if method is None:
            return None
        try:
            return method(*args)
        except Exception:
            return None

    def _load_sql_id(self, symbol: str) -> None:
        """对应 ``_loadSqlId()``：有行情就把符号写进 stock 表，然后取 id。"""
        if self.has_quote_data():
            insert = getattr(self.source, "insert_symbol", None)
            if insert is not None:
                try:
                    insert(symbol, self.get_chinese_name())
                except Exception:
                    pass
        self.stock_id = self._call("get_stock_id", symbol)

    def _count_net_value(self, stock_id: Any) -> int:
        """对应 ``GetNetValueHistorySql()->Count()``。

        优先用数据源自己的 ``count_net_value``；没有就走已有的
        ``historytable.NetValueHistorySql``（需要数据源带 ``connection``）；
        再没有就返回 0。
        """
        own = getattr(self.source, "count_net_value", None)
        if own is not None:
            try:
                return int(own(stock_id))
            except Exception:
                return 0
        connection = getattr(self.source, "connection", None)
        if connection is None:
            return 0
        try:
            from sql.historytable import NetValueHistorySql
        except ImportError:
            return 0
        try:
            return NetValueHistorySql(connection).count(stock_id)
        except Exception:
            return 0

    def _read_position(self, stock_id: Any) -> float:
        """对应 ``$pos_sql->ReadVal()``，取不到回落 ``GetDefaultPosition()``。

        PHP：``($fRatio = $pos_sql->ReadVal($strSqlId)) ? $fRatio : $this->GetDefaultPosition()``
        —— 注意是 **PHP 的真值判断**，所以仓位写成 0 也会被当成没读到。这里照抄。
        """
        value = self._call("get_position", stock_id)
        if value:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
        return float(self.get_default_position())

    # -- 价格 / 净值 ------------------------------------------------------
    def get_close(self, date: Any) -> Any:
        """对应 ``GetClose()``：当天就用现价，否则去行情表取。"""
        if date is not None and str(date) == str(self.get_date()):
            return self.get_price()
        return self._call("get_history_close", self.stock_id, date)

    def get_val(self, date: Any = None) -> Any:
        """对应 ``GetVal()``：传日期就取那天（可能是字符串），不传就是现价。"""
        if date:
            close = self.get_close(date)
            if close:
                try:
                    return float(close)
                except (TypeError, ValueError):
                    return None
            return None
        price = self.get_price()
        try:
            return float(price)
        except (TypeError, ValueError):
            return None

    def get_net_value(self, date: Any) -> Any:
        """对应 ``GetNetValue()``：三条路，顺序不能改。

        1. 新浪期货（黄金除外）→ 取**复权**收盘价；
        2. 库里有净值记录 → 取净值；
        3. 都没有 → 退回 ``GetVal()``（拿行情当净值用）。
        """
        if self.is_sina_future_except_gold_cn():
            value = self._call("get_history_adjclose", self.stock_id, date)
            return float(value) if value else None
        if self.net_value_count > 0:
            value = self._call("get_netvalue_close", self.stock_id, date)
            return float(value) if value else None
        return self.get_val(date)

    def get_net_value_display(self, net_value: Any) -> str:
        """对应 ``GetNetValueDisplay()``。

        精度分两种：**有净值记录的用 4 位**（``NETVALUE_PRECISION``），
        没有的按符号本身的精度 —— 因为那时显示的是行情价，不是净值。
        """
        if net_value is False or net_value is None:
            return ""
        precision = NETVALUE_PRECISION if self.net_value_count > 0 else self.get_precision()
        return self.get_price_display(float(net_value), None, precision)

    # -- 三个估值（基类都是 false，由子类填） ------------------------------
    def get_official_net_value(self) -> Any:
        return None

    def get_fair_net_value(self) -> Any:
        return None

    def get_realtime_net_value(self) -> Any:
        return None

    def get_est_net_value(self) -> Any:
        """对应 ``GetEstNetValue()``：**实时 → 参考 → 官方**。

        顺序是"新鲜度优先"：实时最贴近当下，官方是昨天收盘那一刻的定格值。
        """
        for value in (self.get_realtime_net_value(),
                      self.get_fair_net_value(),
                      self.get_official_net_value()):
            if value:
                return value
        return None

    # -- 库数据加载 -------------------------------------------------------
    def load_daily_sql_data(self, kind: str = "history") -> Any:
        """对应 ``LoadDailySqlData($sql)``：把最新一条日线读进 price/date/prev。

        PHP 传的是 sql 对象（``GetNetValueHistorySql()`` 或 ``GetStockHistorySql()``），
        Python 用 ``kind`` 区分：``"netvalue"`` 或 ``"history"``。

        返回昨收（PHP 也是返回 ``$this->strPrevPrice``）。
        """
        if self.stock_id is None:
            return None
        if kind == "netvalue":
            close = self._call("get_latest_net_value_close", self.stock_id)
            date = self._call("get_latest_net_value_date", self.stock_id)
        else:
            close = self._call("get_latest_history_close", self.stock_id)
            date = self._call("get_latest_history_date", self.stock_id)
        if close in (None, "") or date in (None, ""):
            return None

        self.str_price = str(close)
        self.str_date = str(date)
        # 前收只有行情表有口子；净值表的前一日净值当前数据源没提供（见 README 的
        # 已知偏差）。它只用于显示涨跌幅，不影响估值。
        prev = self._call("get_prev_history_close", self.stock_id, str(date))
        self.str_prev_price = str(prev) if prev not in (None, "") else None
        return self.str_prev_price

    def load_sql_net_value_data(self) -> Any:
        """对应 ``LoadSqlNetValueData()``。"""
        return self.load_daily_sql_data("netvalue")

    def count_net_value(self) -> int:
        return self.net_value_count

    def is_fund(self) -> bool:
        """对应 ``IsFund()``：有净值记录，或者本身就是 A 股基金。"""
        if self.count_net_value() > 0:
            return True
        return bool(self.is_fund_a())

    # -- 名称 -------------------------------------------------------------
    def get_english_name(self) -> Any:
        if self.convert_gb2312:
            return gb_to_utf8(self.str_name)
        return self.str_name

    def get_chinese_name(self) -> Any:
        """对应 ``GetChineseName()``。

        PHP 注释说得很清楚：数据里常常**只有一个**名字（中文或英文），那种情况
        统一放在 ``strName`` 字段，所以没有专属中文名时回落到英文名。
        """
        if not self.str_chinese_name:
            return self.get_english_name()
        if self.convert_gb2312:
            return gb_to_utf8(self.str_chinese_name)
        return self.str_chinese_name

    # -- 与 PHP 同名的简单取值 --------------------------------------------
    def get_stock_id(self) -> Any:
        return self.stock_id

    def get_factor(self) -> float:
        return self.factor

    def get_position(self) -> float:
        return self.ratio


class CnyReference(MysqlReference):
    """对应 ``php/stock/cnyref.php`` 的 ``CnyReference``。

    两个特别之处：

    * 汇率**没有盘中价**，只有每天一个中间价，所以走 ``netvaluehistory``，
      并且把时间固定成 ``09:15:00``（中国外汇交易中心公布中间价的时点）；
    * ``GetClose()`` 覆盖成查**净值表**而不是行情表 —— 汇率压根不在行情表里。
    """

    def load_data(self) -> None:
        self.stock_id = self._call("get_stock_id", self.get_symbol())
        self.load_sql_net_value_data()
        self.set_time("09:15:00")
        # PHP 还设置了调试文件名与外部链接，属于渲染层，这里不迁。

    def get_close(self, date: Any) -> Any:
        if date is not None and str(date) == str(self.get_date()):
            return self.get_price()
        return self._call("get_netvalue_close", self.stock_id, date)


class EstReference(MysqlReference):
    """**够** ``QdiiReference`` 用的最小中间层引用。

    PHP 里这个位置是 ``FundPairReference`` / ``HoldingsReference``
    （``php/stock/fundpairref.php``、``holdingsref.php``）。本版拆成两半：

    * **配对查询**在这个类里就地实现（:meth:`get_pair_ref`）—— 它只读
      ``fundpair`` 一行，没必要为它多一层继承；
    * 需要**期货自动校准**的那种（``FundPairReference``）单独一个类。

    两者都实现 ``get_pair_ref()``，因为 ``FundReference.get_realtime_ref()``
    就是靠 ``method_exists`` / ``hasattr`` 探测这个方法来决定要不要算实时 EST
    （对应 ``php/stock/fundref.php:158``）。**没有它，实时 EST 那一档永远是空的**
    —— 这是实测踩过的：``EstReference`` 少了这个方法，页面 EST 表少两列。
    """

    #: ``$this->pair_ref``（``php/stock/fundpairref.php:5``）。
    #: 类级默认值，理由同 ``FundReference.stock_ref``：``MysqlReference.__init__``
    #: 会先调 ``load_data()``，那时实例属性还没赋值。
    pair_ref = None

    def load_data(self) -> None:
        self.set_has_data(False)
        if self.source is None:
            return
        stock_id = self._call("get_stock_id", self.get_symbol())
        if stock_id is None:
            return
        self.stock_id = stock_id

        close = self._call("get_latest_net_value_close", stock_id)
        date = self._call("get_latest_net_value_date", stock_id)
        if close in (None, ""):
            close = self._call("get_latest_history_close", stock_id)
            date = self._call("get_latest_history_date", stock_id)
        if close in (None, "") or date in (None, ""):
            return

        self.str_price = str(close)
        self.str_date = str(date)
        self.set_has_data(True)

    # -- 配对引用 ---------------------------------------------------------
    def get_pair_ref(self) -> Any:
        """对应 ``MyPairReference::GetPairRef()``：**本符号跟踪的标的**。

        ``php/stock/fundpairref.php:65``::

            function GetPairRef() { return $this->pair_ref; }

        而 ``pair_ref`` 是构造时查出来的（``GetFundPairSql()->GetPairSymbol()``）::

            if ($strPair = $pair_sql->GetPairSymbol($strSymbol))
                $this->pair_ref = new MyStockReference($strPair);

        ``fundpair`` 的列语义是 ``id`` = 本体、``stock_id`` = 被跟踪标的，
        所以 SQL 是「``id`` = 本符号那一行的 ``stock_id``」——
        与"谁跟踪我"（杠杆标的）**方向相反**，见
        ``web/services/reference_service._realtime_symbol`` 的对照说明。

        **惰性建**而不是构造时建：``QdiiReference`` 构造会算一次实时 EST，
        构造时建配对引用会让每个请求都去查一遍库。
        """
        if self.pair_ref is not None:
            return self.pair_ref
        symbol = self._pair_symbol()
        if not symbol:
            return None
        self.pair_ref = _build_pair_reference(symbol, self.source)
        return self.pair_ref

    def _pair_symbol(self) -> Any:
        """查 ``fundpair`` 取被跟踪标的的符号；没有返回 ``None``。"""
        if self.source is None:
            return None
        symbol = self.get_symbol()
        if not symbol:
            return None
        return _lookup_pair_symbol(self.source, str(symbol))


class FundReference(MysqlReference):
    """对应 ``php/stock/fundref.php`` 的 ``FundReference``。

    它比 ``MysqlReference`` 多三样东西：三个估值字段、仓位正反调整、
    以及"委托给 ``stock_ref``"的那几个方法。
    """

    # 类级默认值：**必须在这里给**，不能只在 ``__init__`` 里给。
    #
    # 因为 ``MysqlReference.__init__`` 会调 ``load_data()``，而 ``load_data()``
    # 又会走到 ``get_symbol()`` —— 那时 ``FundReference.__init__`` 还没执行到
    # 赋值那几行，访问 ``self.stock_ref`` 会 ``AttributeError``。
    # PHP 里访问未定义属性只是得到 ``null``，所以不会有这个问题。

    #: A 股基金的场内行情引用。``is_fund_a()`` 为真时构造时自动建
    #: （见 :meth:`_build_stock_ref`），调用方也可以用 ``stock_ref=`` 顶掉。
    stock_ref = None
    est_ref = None
    cny_ref = None
    forex_ref = None

    official_net_value = None
    fair_net_value = None
    realtime_net_value = None
    official_date = None

    def __init__(self, symbol: str, source: Any = None, stock_ref: Any = None,
                 quote_loader: Callable[[str], Any] = None):
        super().__init__(symbol, source=source, quote_loader=quote_loader)

        if stock_ref is not None:
            self.stock_ref = stock_ref
        elif self.is_fund_a():
            # ``php/stock/fundref.php:19``::
            #
            #     if ($this->IsFundA())
            #         $this->stock_ref = new MyStockReference($strSymbol);
            #
            # A 股基金（LOF / ETF）场内可交易，场内价才是"真价格"，
            # 所以 PHP 给基金对象挂一份场内行情引用，净值之外另有一套口径。
            # 兜底放在**调用处**而不是 ``_build_stock_ref`` 里面：这样无论
            # 建不出来的是哪一步（甚至将来换了实现），承诺都成立。
            try:
                self.stock_ref = self._build_stock_ref(symbol)
            except Exception:
                self.stock_ref = None

        # 校准值来自 calibrationhistory（PHP 在子类构造里读一次）
        if self.stock_id:
            record = self._call("get_calibration_now", self.stock_id)
            close = getattr(record, "close", None)
            if close not in (None, ""):
                try:
                    self.factor = float(close)
                except (TypeError, ValueError):
                    self.factor = 1.0

    def _build_stock_ref(self, symbol: str) -> Any:
        """``new MyStockReference($strSymbol)`` 在本项目里的等价物。

        ``MyStockReference``（``php/stock/mystockref.php``）未迁移 —— 它构造时
        联网抓新浪行情，而本项目的部署形态是"``quoteupdatercli --daemon`` 抓进
        ``realtimequote``、页面读快照"（见 ``auto/refquote.py``）。所以用
        :class:`MysqlReference` 顶上：它的构造已经包含"读快照装填行情"这一步，
        正是场内行情引用在本项目里的形态，``reference_service._build_one()``
        也是这么干的。

        **为什么要补这一行**：QDII 均线的换算列
        （``php/ui/smaparagraph.php:141 _callbackQdiiSma``）不带参数调用时返回
        的就是 ``GetStockRef()``，列头和格子格式都取自它。缺了它整列空白 ——
        生产端 ``sz161125cn.php`` 该列是 ``SZ161125 / 3.179``。

        建不出来时调用方会回退 ``None``（见 ``__init__``）—— 这列只是装饰，
        不该让整页 500；回退后行为与补这一行之前完全一致。

        参数用传入的 ``symbol`` 而不是 ``self.get_symbol()``：后者会委托给
        ``stock_ref``，而此刻 ``stock_ref`` 正在赋值 —— 绕开它更省心。
        """
        return MysqlReference(symbol, source=self.source)

    def load_data(self) -> None:
        """对应 ``FundReference::LoadData()``。

        PHP 是 ``LoadSinaFundData()``（联网抓基金名）+ ``bConvertGB2312 = true``。
        本版改为**从数据源取名字**，不在构造时联网（见模块 docstring 取舍 1）；
        取不到名字就 ``SetHasData(false)``，与 PHP 抓失败时的行为一致。
        """
        self.convert_gb2312 = True
        name = self._call("get_stock_name", self.get_symbol())
        if name:
            self.str_name = name
        self.set_has_data(name is not None)

    # -- 三个估值 ---------------------------------------------------------
    def get_official_net_value(self) -> Any:
        return self.official_net_value or None

    def get_fair_net_value(self) -> Any:
        return self.fair_net_value or None

    def get_realtime_net_value(self) -> Any:
        return self.realtime_net_value or None

    def get_official_date(self) -> Any:
        return self.official_date

    # -- 汇率引用 ---------------------------------------------------------
    def set_forex(self, cny_symbol: str) -> None:
        """对应 ``SetForex()``：建中间价引用（``USCNY`` 这种）。"""
        self.cny_ref = CnyReference(cny_symbol, source=self.source)

    def set_forex_ref(self, forex_symbol: str) -> None:
        """对应 ``SetForexRef()``：建离岸/即期外汇引用（``fx_susdcny`` 这种）。

        PHP 这里是 ``new MyStockReference($strSymbol)``；``MyStockReference``
        未迁移，本版用 :class:`EstReference` 顶上 —— 它同样只需要"最新价 + 日期"。
        """
        self.forex_ref = EstReference(forex_symbol, source=self.source)

    def get_cny_ref(self) -> Any:
        return self.cny_ref

    def get_forex_ref(self) -> Any:
        """对应 ``GetForexRef()``：**ETF 用即期外汇，LOF 用中间价**。

        差别在于 ETF 可以按盘中汇率实时申赎，LOF 只能按当天中间价。
        """
        return self.forex_ref if self.is_etf_a() else self.cny_ref

    def get_forex_ref_array(self) -> tuple:
        return (self.forex_ref, self.cny_ref)

    # -- 中间层引用 -------------------------------------------------------
    def get_est_ref(self) -> Any:
        return self.est_ref

    def set_est_ref(self, ref: Any) -> None:
        """设中间层引用。传**符号字符串**时自动包成 :class:`EstReference`。"""
        if isinstance(ref, str):
            ref = EstReference(ref, source=self.source)
        self.est_ref = ref

    def get_realtime_ref(self) -> Any:
        """对应 ``GetRealtimeRef()``：中间层引用带配对引用时才有实时值。"""
        getter = getattr(self.est_ref, "get_pair_ref", None)
        if getter is None:
            getter = getattr(self.est_ref, "GetPairRef", None)
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def get_stock_ref(self) -> Any:
        return self.stock_ref

    # -- 委托给 stock_ref（A 股基金的场内行情才是"真价格"） ---------------
    def get_symbol(self) -> Any:
        if self.stock_ref is not None:
            return self.stock_ref.get_symbol()
        return super().get_symbol()

    def get_stock_id(self) -> Any:
        if self.stock_ref is not None:
            return self.stock_ref.get_stock_id()
        return super().get_stock_id()

    def get_price_display(self, disp: float = None, prev: float = None, precision: int = None) -> str:
        if self.stock_ref is not None:
            return self.stock_ref.get_price_display(disp, prev, precision)
        return super().get_price_display(disp, prev, precision)

    def get_percentage_text(self, divisor: float = None, dividend: float = None) -> str:
        """对应 ``GetPercentageDisplay()`` 的**文本部分**。

        PHP 那边返回带颜色的 ``<font>`` 片段，属于渲染层（``php/ui`` 未迁移），
        这里只做同样的委托，返回不带标记的文本。
        """
        if self.stock_ref is not None:
            return self.stock_ref.get_percentage_text(divisor, dividend)
        return super().get_percentage_text(divisor, dividend)

    # -- 仓位正反调整 -----------------------------------------------------
    def _get_calibration_base_val(self) -> float:
        """对应 ``_getCalibrationBaseVal()``：**校准值那天**的净值。

        注意取的是**校准日期**而不是今天 —— 仓位调整是相对于"上一次校准时的
        净值"做的，这是 ``FundAdjustPosition`` 里 ``$fOldVal`` 的定义。
        """
        if self.stock_id is None:
            return 0.0
        record = self._call("get_calibration_now", self.stock_id)
        date = getattr(record, "date", None)
        if date in (None, ""):
            return 0.0
        value = self._call("get_netvalue_close", self.stock_id, date)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def adjust_position(self, value: float) -> float:
        """对应 ``AdjustPosition()``：按仓位把中间层值折算成基金口径。"""
        return fund_adjust_position(self.get_position(), float(value), self._get_calibration_base_val())

    def reverse_adjust_position(self, value: float) -> float:
        """对应 ``ReverseAdjustPosition()``：``AdjustPosition`` 的逆运算。"""
        return fund_reverse_adjust_position(self.get_position(), float(value), self._get_calibration_base_val())

    # -- 写库（只在显式调用时发生） ---------------------------------------
    def update_est_net_value(self) -> bool:
        """对应 ``UpdateEstNetValue()`` → ``StockUpdateEstResult()``。

        落到 :meth:`fundestcalc.EstimateCalculator.record_official`（已迁移的那条
        实现，含"净值未到才写"与 20:55 闸门）。需要数据库连接；没有就返回 ``False``。
        """
        estimator = getattr(self, "_estimator", lambda: None)()
        if estimator is None:
            return False
        try:
            return bool(estimator.record_official(self.get_symbol()))
        except Exception:
            return False

    def update_official_net_value(self) -> bool:
        """对应 ``UpdateOfficialNetValue()`` → ``StockCompareEstResult()``。

        PHP 的语义是"把当前价格当作官方净值写进 ``netvaluehistory``，
        **新写入**才返回 ``true``"（``InsertDaily`` 已有则不写、返回 false）。
        这个返回值直接决定 ``AdjustFactor()`` 会不会重算校准值 ——
        **只在新的官方净值到货那天重算**，这正是 PHP 的设计。

        本版数据源没有"写净值"的口子，所以用**该日期是否已有净值**等价判断：
        没有 → 视为新到货（``True``），已有 → 视为已处理（``False``）。
        顺带做 EST 误差比对（PHP 里那段 ``trigger_error``）。
        """
        stock_id = self.get_stock_id()
        date = self.get_date()
        if stock_id is None or date in (None, ""):
            return False

        writer = getattr(self.source, "write_net_value", None)
        if writer is not None:
            try:
                inserted = bool(writer(stock_id, date, self.get_price()))
            except Exception:
                inserted = False
        else:
            inserted = self._call("get_netvalue_close", stock_id, date) in (None, "")

        if not inserted:
            return False

        estimator = getattr(self, "_estimator", lambda: None)()
        if estimator is not None:
            try:
                estimator.compare_with_net_value(self.get_symbol(), self.get_price(), date)
            except Exception:
                pass
        return True

    def insert_fund_calibration(self) -> bool:
        """对应 ``InsertFundCalibration()``：把当前校准值写进 ``calibrationhistory``。

        走 ``WriteDaily`` 语义（**已有就不动**），与 ``autocalibration._write()``
        用的是同一条 SQL —— 那里已经踩过 ``time`` 列 NOT NULL 的坑，这里照抄。

        数据源自带 ``write_calibration`` 时优先用它（便于测试与替换存储）。
        """
        stock_id = self.get_stock_id()
        date = self.get_date()
        if stock_id is None or date in (None, ""):
            return False

        writer = getattr(self.source, "write_calibration", None)
        if writer is not None:
            try:
                return bool(writer(stock_id, date, self.factor))
            except Exception:
                return False

        connection = getattr(self.source, "connection", None)
        if connection is None:
            return False
        try:
            from sql.historytable import CalibrationSql
        except ImportError:
            return False

        try:
            if CalibrationSql(connection).get_record(stock_id, date) is not None:
                return False
        except Exception:
            return False

        import datetime as _datetime
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO calibrationhistory (stock_id, `date`, close, `time`, num) "
                "VALUES (%s, %s, %s, %s, 1)",
                (stock_id, date, self.factor, _datetime.datetime.now().strftime("%H:%M:%S")),
            )
            commit = getattr(connection, "commit", None)
            if commit is not None:
                commit()
            return True
        except Exception:
            return False
        finally:
            cursor.close()


class QdiiReference(FundReference):
    """对应 ``php/stock/qdiiref.php`` 的 ``_QdiiReference``。

    PHP 里这是个**抽象中间类**（下划线前缀），四个地区子类才真正被 new：

    ======================  ========================  ==========================
    PHP 子类                中间价符号                即期外汇符号
    ======================  ========================  ==========================
    ``QdiiReference``       ``USCNY``                 ``fx_susdcny``
    ``QdiiHkReference``     ``HKCNY``                 ``fx_shkdcny``
    ``QdiiJpReference``     ``JPCNY``                 ``fx_sjpycny``
    ``QdiiEuReference``     ``EUCNY``                 ``fx_seurcny``
    ======================  ========================  ==========================

    Python 里合成一个类 + ``region`` 参数（见 :func:`build_qdii_reference`），
    地区差异只是一个查表，没必要写四遍。
    """

    def __init__(self, symbol: str, source: Any = None, region: str = "us",
                 cny_symbol: str = None, forex_symbol: str = None,
                 stock_ref: Any = None, quote_loader: Callable[[str], Any] = None,
                 estimator: Any = None, auto_estimate: bool = True):
        super().__init__(symbol, source=source, stock_ref=stock_ref, quote_loader=quote_loader)

        self.region = region
        default_cny, default_forex = QDII_REGIONS.get(region, QDII_REGIONS["us"])
        self.cny_symbol = cny_symbol or default_cny
        self.forex_symbol = forex_symbol or default_forex

        #: ``$strOfficialCNY`` —— 官方净值那天用的汇率
        self.official_cny = None
        self._estimator_override = estimator

        self.set_forex(self.cny_symbol)

        est_symbol = self._est_symbol_of(self.get_symbol(), region)
        if est_symbol:
            self.set_est_ref(est_symbol)
        self.set_forex_ref(self.forex_symbol)

        if auto_estimate:
            self.est_net_value()

    # -- 组装辅助 ---------------------------------------------------------
    @staticmethod
    def _est_symbol_of(symbol: str, region: str) -> Any:
        """按地区取中间层符号（``fundcalc`` 里已经迁好了四个查表函数）。"""
        if region == "hk":
            return qdii_hk_est_symbol(symbol)
        if region == "jp":
            return qdii_jp_est_symbol(symbol)
        if region == "eu":
            return qdii_eu_est_symbol(symbol)
        return qdii_est_symbol(symbol)

    def _estimator(self) -> Any:
        """取估算器：优先注入的，否则按数据源现场建一个。

        建不出来（没有数据库连接）就返回 ``None`` —— 这时三个估值字段保持空，
        对象仍然可用，只是算不出 EST。**不抛异常**：引用对象是给页面用的，
        页面不能因为算不出 EST 就挂。
        """
        if self._estimator_override is not None:
            return self._estimator_override
        if getattr(self, "_estimator_built", False):
            return self._estimator_cache
        self._estimator_built = True
        self._estimator_cache = None
        if self.source is None:
            return None
        try:
            from calc.fundestcalc import build_estimate_calculator
            self._estimator_cache = build_estimate_calculator(self.source)
        except Exception:
            self._estimator_cache = None
        return self._estimator_cache

    # -- 取中间层的值 -----------------------------------------------------
    def _get_est_net_value(self, est_ref: Any, date: Any) -> Any:
        """对应 ``_getEstNetValue()``：中间层在 ``date`` 的**净值**。"""
        stock_id = getattr(est_ref, "get_stock_id", None)
        if stock_id is None:
            return None
        try:
            stock_id = stock_id()
        except TypeError:
            pass
        if stock_id is None:
            return None
        return self._call("get_netvalue_close", stock_id, date)

    def _get_est_price(self, est_ref: Any, date: Any) -> Any:
        """对应 ``_getEstPrice()``：中间层的**当前价**，取不到就退回该日前的收盘价。

        PHP 原注释写的是 ``// SH000869 bug fix`` —— 港股的 ``SH000869`` 经常抓不到
        实时价，必须回退。**注意是"该日之前"而不是"该日"**：官方净值还没出来时，
        当天那条行情是不能拿来算官方 EST 的。
        """
        price = None
        getter = getattr(est_ref, "get_price", None)
        if getter is not None:
            try:
                price = getter()
            except Exception:
                price = None
        if price not in (None, ""):
            return price
        stock_id = getattr(est_ref, "get_stock_id", None)
        if stock_id is None:
            return None
        try:
            stock_id = stock_id()
        except TypeError:
            pass
        if stock_id is None:
            return None
        return self._call("get_prev_history_close", stock_id, date)

    # -- 三个 EST ---------------------------------------------------------
    def est_net_value(self) -> None:
        """对应 ``_QdiiReference::EstNetValue()``。

        **算法委托给** ``fundestcalc.EstimateCalculator``，这里只做 PHP 这段流程：

        ```php
        $this->AdjustFactor();                    // 新净值到货 -> 重算校准值
        $strDate = $est_ref->GetDate();
        if ($this->strOfficialCNY = $cny_ref->GetClose($strDate))
        {
            $this->fOfficialNetValue = ...;       // 汇率齐了才算
            $this->strOfficialDate   = $strDate;
            $this->UpdateEstNetValue();           // 写 fundest
        }
        else { /* 汇率缺 -> 从 fundest 读最近一条 */ }
        $this->EstRealtimeNetValue();
        ```

        ## 为什么不在本模块再实现一遍这三个公式

        本项目出过一次代价不小的事故：``fundestcalc`` 之外还有一条"看起来等价"的
        估值路径（``est_net_value()`` 不带 source 那条），基于它做的受控实验得出
        "中间层校准值对估值零影响"的**错误结论**，写入后把 ``SZ161130`` 的官方 EST
        从 4.59 搞成 20245，而且**页面不报错**。

        所以这里的纪律是：**三个 EST 只有 ``fundestcalc`` 一个实现**，
        引用对象只负责承载结果与维持 PHP 的字段语义。
        """
        self.adjust_factor()

        est_ref = self.get_est_ref()
        if est_ref is None:
            return

        estimator = self._estimator()
        if estimator is None:
            return

        date = getattr(est_ref, "get_date", lambda: None)()
        cny_ref = self.get_cny_ref()
        official_cny = cny_ref.get_close(date) if cny_ref is not None else None

        if official_cny:
            self.official_cny = official_cny
            record = self._safe_compute(estimator.compute_official, self.get_symbol())
            if record is not None:
                try:
                    self.official_net_value = float(record.value)
                except (TypeError, ValueError):
                    self.official_net_value = None
                self.official_date = getattr(record, "date", date)
                self.update_est_net_value()
        else:
            self._fallback_official_from_store(estimator)

        self.est_realtime_net_value()

    def _fallback_official_from_store(self, estimator: Any) -> None:
        """PHP 的 else 分支：汇率缺那天，从 ``fundest`` 读最近一条。

        PHP 还会在最后把 ``strOfficialCNY`` 兜底成**当前**汇率，这样后面算
        FairEst 时不会因为除数为 0 而崩 —— 这里照抄。
        """
        store = getattr(estimator, "store", None)
        stock_id = self.get_stock_id()
        if store is not None and stock_id is not None:
            try:
                record = store.get_latest(stock_id)
            except Exception:
                record = None
            if record:
                self.official_date = record.get("date")
                cny_ref = self.get_cny_ref()
                if cny_ref is not None:
                    self.official_cny = cny_ref.get_close(self.official_date)
                try:
                    self.official_net_value = float(record.get("close"))
                except (TypeError, ValueError):
                    self.official_net_value = None
                return
        cny_ref = self.get_cny_ref()
        if cny_ref is not None:
            self.official_cny = cny_ref.get_price()

    def est_realtime_net_value(self) -> None:
        """对应 ``EstRealtimeNetValue()``：参考 EST 与实时 EST。"""
        est_ref = self.get_est_ref()
        if est_ref is None:
            return
        estimator = self._estimator()
        if estimator is None:
            return

        date = getattr(est_ref, "get_date", lambda: None)()
        cny_ref = self.get_cny_ref()
        cny_date = cny_ref.get_date() if cny_ref is not None else None

        # PHP：ETF 一律算；否则"汇率日期"或"中间层日期"与官方日期不同才算 ——
        # 都相同说明参考 EST 与官方 EST 是同一个值，没有信息量。
        if (self.is_etf_a()
                or str(cny_date) != str(self.official_date)
                or str(date) != str(self.official_date)):
            record = self._safe_compute(estimator.compute_fair, self.get_symbol())
            if record is not None:
                try:
                    self.fair_net_value = float(record.value)
                except (TypeError, ValueError):
                    self.fair_net_value = None

        # 实时 EST 只有"中间层带配对引用"时才有（对应 $est_ref->EstFromPair()）
        if self.get_realtime_ref() is not None:
            record = self._safe_compute(estimator.compute_realtime, self.get_symbol())
            if record is not None:
                try:
                    self.realtime_net_value = float(record.value)
                except (TypeError, ValueError):
                    self.realtime_net_value = None

    @staticmethod
    def _safe_compute(method: Any, *args: Any) -> Any:
        try:
            return method(*args)
        except Exception:
            return None

    # -- 校准值 -----------------------------------------------------------
    def adjust_factor(self) -> Any:
        """对应 ``AdjustFactor()``：新的官方净值到货时重算校准值。

        ```php
        $fFactor = QdiiGetCalibration($strEst, $strCNY, $this->GetPrice());
        ```

        即 ``校准值 = 中间层值 × 汇率 / 基金价格``。取中间层值时**净值优先**，
        净值没有才退回收盘价 —— 两条都取不到就返回 ``false``（不重算）。
        """
        if not self.update_official_net_value():
            return None

        date = self.get_date()
        cny_ref = self.get_cny_ref()
        if cny_ref is None:
            return None
        cny = cny_ref.get_close(date)
        if not cny:
            return None

        est_ref = self.get_est_ref()
        if est_ref is None:
            return None

        est = self._get_est_net_value(est_ref, date)
        if est in (None, ""):
            getter = getattr(est_ref, "get_close", None)
            est = getter(date) if getter is not None else None
        if est in (None, ""):
            return None

        try:
            self.factor = qdii_get_calibration(float(est), float(cny), float(self.get_price()))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        self.insert_fund_calibration()
        return self.factor

    # -- QDII 换算 --------------------------------------------------------
    def get_qdii_value(self, est: Any, cny: Any = None) -> float:
        """对应 ``GetQdiiValue()``：**中间层值 → 基金净值口径**。

        ``QdiiGetVal`` 之后再过一次仓位调整。``cny`` 不传时按 ETF/LOF 选汇率引用
        （见 :meth:`FundReference.get_forex_ref`）。
        """
        if cny is None:
            forex_ref = self.get_forex_ref()
            cny = forex_ref.get_price() if forex_ref is not None else None
        if not self.factor:
            return 0.0
        try:
            value = qdii_get_value(float(est), float(cny), float(self.factor))
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0
        return self.adjust_position(value)

    def get_est_value(self, qdii_value: Any) -> float:
        """对应 ``GetEstValue()``：**基金净值口径 → 中间层值**（``GetQdiiValue`` 的逆）。"""
        forex_ref = self.get_forex_ref()
        cny = forex_ref.get_price() if forex_ref is not None else None
        try:
            reversed_value = self.reverse_adjust_position(float(qdii_value))
            return qdii_get_peer_value(reversed_value, float(cny), float(self.factor))
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    def get_est_quantity(self, qdii_quantity: Any) -> int:
        """对应 ``GetEstQuantity()``：中间层份额 → 基金份额。"""
        try:
            return int(float(qdii_quantity) / float(self.factor))
        except (TypeError, ValueError, ZeroDivisionError):
            return 0

    def get_qdii_quantity(self, est_quantity: Any) -> int:
        """对应 ``GetQdiiQuantity()``：基金份额 → 中间层份额。"""
        try:
            return int(float(est_quantity) * float(self.factor))
        except (TypeError, ValueError):
            return 0


def build_qdii_reference(symbol: str, source: Any = None, region: str = None, **kwargs: Any) -> QdiiReference:
    """按符号**猜地区**，建一个 :class:`QdiiReference`。

    地区决定用哪个中间价（USCNY/HKCNY/JPCNY/EUCNY）与哪个即期外汇。
    判断顺序与 ``qdiiref.php`` 里四个子类的用法一致：先试hk/jp/eu 的
    ``est_symbol`` 查表（它们各自只认自己那批符号），剩下的归 ``us``。
    """
    if region is None:
        region = "us"
        for candidate in ("hk", "jp", "eu"):
            if QdiiReference._est_symbol_of(symbol, candidate):
                region = candidate
                break
    return QdiiReference(symbol, source=source, region=region, **kwargs)


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名，让按 PHP 写法调用的代码仍可用）
# ---------------------------------------------------------------------------
GbToUtf8 = gb_to_utf8

MysqlReference.GetClose = MysqlReference.get_close
MysqlReference.GetVal = MysqlReference.get_val
MysqlReference.GetNetValue = MysqlReference.get_net_value
MysqlReference.GetNetValueDisplay = MysqlReference.get_net_value_display
MysqlReference.GetOfficialNetValue = MysqlReference.get_official_net_value
MysqlReference.GetFairNetValue = MysqlReference.get_fair_net_value
MysqlReference.GetRealtimeNetValue = MysqlReference.get_realtime_net_value
MysqlReference.GetEstNetValue = MysqlReference.get_est_net_value
MysqlReference.LoadDailySqlData = MysqlReference.load_daily_sql_data
MysqlReference.LoadSqlNetValueData = MysqlReference.load_sql_net_value_data
MysqlReference.CountNetValue = MysqlReference.count_net_value
MysqlReference.IsFund = MysqlReference.is_fund
MysqlReference.GetEnglishName = MysqlReference.get_english_name
MysqlReference.GetChineseName = MysqlReference.get_chinese_name
MysqlReference.GetStockId = MysqlReference.get_stock_id
MysqlReference.GetFactor = MysqlReference.get_factor
MysqlReference.GetPosition = MysqlReference.get_position
#: ``GetStockLink()``：基金详情页「估值」表首格用的就是它
#: （``fundestparagraph.php`` 里 ``$ref->GetStockLink()``）。
#: 它是**站内**详情页链接（``/woody/res/<sym>cn.php``），与参考数据表首格的
#: 新浪外链不是一回事 —— 曾经错把它别名成 ``get_external_link``，
#: 导致估值表首格跳去新浪，详见 :meth:`MysqlReference.get_stock_link`。
MysqlReference.GetStockLink = MysqlReference.get_stock_link

FundReference.GetOfficialDate = FundReference.get_official_date
FundReference.SetForex = FundReference.set_forex
FundReference.SetForexRef = FundReference.set_forex_ref
FundReference.GetForexRef = FundReference.get_forex_ref
FundReference.GetForexRefArray = FundReference.get_forex_ref_array
FundReference.GetCnyRef = FundReference.get_cny_ref
FundReference.GetEstRef = FundReference.get_est_ref
FundReference.SetEstRef = FundReference.set_est_ref
FundReference.GetRealtimeRef = FundReference.get_realtime_ref
FundReference.GetStockRef = FundReference.get_stock_ref
FundReference.AdjustPosition = FundReference.adjust_position
FundReference.ReverseAdjustPosition = FundReference.reverse_adjust_position
FundReference.UpdateEstNetValue = FundReference.update_est_net_value
FundReference.UpdateOfficialNetValue = FundReference.update_official_net_value
FundReference.InsertFundCalibration = FundReference.insert_fund_calibration

QdiiReference.EstNetValue = QdiiReference.est_net_value
QdiiReference.EstRealtimeNetValue = QdiiReference.est_realtime_net_value
QdiiReference.AdjustFactor = QdiiReference.adjust_factor
QdiiReference.GetQdiiValue = QdiiReference.get_qdii_value
QdiiReference.GetEstValue = QdiiReference.get_est_value
QdiiReference.GetEstQuantity = QdiiReference.get_est_quantity
QdiiReference.GetQdiiQuantity = QdiiReference.get_qdii_quantity
