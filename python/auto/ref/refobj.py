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
from calc.pairestimate import (
    est_from_pair as _est_from_pair,
    est_to_pair as _est_to_pair,
    get_default_cny as _pairestimate_default_cny,
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

#: 查配对表：``id``（= ``stock`` 列）= 本体、``stock_id`` = 被跟踪标的。
#:
#: 对应 PHP ``StockPairSql::GetPairSymbol()``（``php/sql/sqlstockpair.php:38``）::
#:
#:     if ($strStockId = $sql->GetId($strSymbol))
#:         if ($strPairId = $this->ReadPair($strStockId))
#:             return $sql->GetStockSymbol($strPairId);
#:
#: ``table`` 是 ``abpair`` / ``adrpair`` / ``ahpair`` / ``fundpair`` 之一，
#: 对应 PHP ``sqlstocksymbol.php:123-126`` 那四个 ``StockPairSql`` 实例。
_PAIR_SYMBOL_SQL = (
    "SELECT p.symbol FROM {table} f "
    "JOIN stock p ON p.id = f.stock_id "
    "WHERE f.id = (SELECT id FROM stock WHERE symbol = %s) LIMIT 1"
)


def _lookup_pair_symbol(source: Any, symbol: str, table: str = "fundpair") -> Any:
    """按符号查它在 ``table`` 里跟踪的标的（A→配对方向）；没有返回 ``None``。

    ``^NDX`` → ``hf_NQ``（纳斯达克 100 指数跟踪 NQ 期货）就是这么来的，
    也就是生产端 ``sz161130cn.php`` 参考数据表第 3 行那个 ``hf_NQ``。
    """
    connection = getattr(source, "connection", None)
    if connection is None:
        return None
    cursor = connection.cursor()
    try:
        cursor.execute(_PAIR_SYMBOL_SQL.format(table=table), (symbol,))
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


def _lookup_pair_symbol_reverse(source: Any, symbol: str, table: str = "fundpair") -> Any:
    """配对方向的反查（配对→A 方向）；对应 ``StockPairSql::GetSymbol()``。

    ``php/sql/sqlstockpair.php:25`` 用这个把"从 B/H/ADR 这一侧进来"的符号换成 A 侧
    符号，再构造配对引用（见 ``StockGetPairReferences``）。SQL 把 WHERE 条件从
    ``f.id = 本体`` 换成 ``f.stock_id = 配对侧``，取回的 ``f.id``（本体侧）再
    join 回 ``stock`` 得到 A 侧符号。
    """
    connection = getattr(source, "connection", None)
    if connection is None:
        return None
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT s.symbol FROM {table} f "
            "JOIN stock s ON s.id = f.id "
            "WHERE f.stock_id = (SELECT id FROM stock WHERE symbol = %s) LIMIT 1".format(
                table=table
            ),
            (symbol,),
        )
        row = cursor.fetchone()
    except Exception:
        return None
    finally:
        cursor.close()
    if not row:
        return None
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


def count_holdings(source: Any, symbol: str) -> int:
    """``SqlCountHoldings()``（``php/sql/sqlstocksymbol.php:313``）的本版。

    只用来回答"这个标的是不是**持仓型**"。**查不到一律返回 0**（= 走配对口径），
    不抛异常 —— 它只是选型判据，不该让整个引用对象构造失败。
    """
    connection = getattr(source, "connection", None)
    if connection is None:
        return 0
    getter = getattr(source, "get_stock_id", None)
    if getter is None:
        return 0
    try:
        stock_id = getter(symbol)
    except Exception:
        return 0
    if stock_id is None:
        return 0
    try:
        from sql.holdingstable import HoldingsSql
        return int(HoldingsSql(connection).count(stock_id) or 0)
    except Exception:
        return 0


def build_est_reference(symbol: str, source: Any) -> Any:
    """按"标的是不是持仓型"造中间层引用（``php/stock/qdiiref.php:245``）。

    ```php
    if (SqlCountHoldings($strEstSymbol) > 0)   $est_ref = new HoldingsReference($strEstSymbol);
    else                                       $est_ref = new FundPairReference($strEstSymbol);
    ```

    **``else`` 分支仍用 ``EstReference`` 而不是 ``FundPairReference``，是刻意偏差**：
    后者构造时还会查自己的配对（``^NDX → hf_NQ → hf_NQ 的配对 → …``），
    理由见 :func:`_build_pair_reference`。PHP 靠 ``IsSinaFutureUS()`` 打断递归，
    本版用更直白的"中间层引用不再找配对"。

    持仓那一半是**照实迁**的：``HoldingsReference`` 已具备
    ``GetHoldingsDate``/``_est_net_value``，下游
    （``calc.fundestcalc._official_est_value``）凭它切持仓口径。
    """
    if count_holdings(source, symbol) > 0:
        try:
            from ref.holdingsref import HoldingsReference
        except ImportError:
            return EstReference(symbol, source=source)
        return HoldingsReference(symbol, source=source)
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

    def get_my_stock_link(self) -> str:
        """对应 PHP ``GetMyStockLink()``：**账号体系下**的"我的股票"链接。

        PHP 原文（``php/stock/stocksymbol.php:1173`` 附近的 ``GetMyStockLink``）：:

            if ($str = GetGroupStockLink($this->GetSymbol())) return $str;
            return $this->GetStockLink();

        本项目账号体系（登录 / ``GetGroupSql()``）暂缓，没有"分组链接"，
        所以直接回落到站内详情页链接（与 ``get_stock_link`` 同形态）。
        配对比较表（``pair_service``）用它在首格放链接；
        ``/stock/<symbol>`` 详情页（CK3）落地后这些链接就能跳转。
        """
        return self.get_stock_link()

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

    #: 配对表名：默认 ``fundpair``（QDII 基金/指数期货那一类）。
    #: 子类 ``PairReference`` 的 Ab/Ah/Adr 会改写这一项。
    pair_table = "fundpair"

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
        """查 ``pair_table`` 取被跟踪标的的符号；没有返回 ``None``。"""
        if self.source is None:
            return None
        symbol = self.get_symbol()
        if not symbol:
            return None
        return _lookup_pair_symbol(self.source, str(symbol), self.pair_table)


class PairReference(MysqlReference):
    """对应 ``php/stock/fundpairref.php:3`` 的 ``MyPairReference``（不含 ``FundPairReference`` 那段）。

    它做三件事（与 PHP 构造 + ``LoadCalibration`` 一致）：

    1. 构造时按 ``pair_table`` 查出配对标的（``get_pair_ref``，惰性查找继承自
       :class:`EstReference`）；
    2. 按"本符号"和"配对符号"的类型关系推导用哪个汇率引用（``cny_ref``，
       :meth:`_build_cny_ref`）；
    3. 装载校准值（``LoadCalibration``），给 ``EstFromPair`` / ``EstToPair`` 提供
       ``fFactor`` 与 ``fLastCalibrationVal``。

    ``Ab`` / ``Ah`` / ``Adr`` / ``Fund`` 四种配对都继承它，只是 ``pair_table`` 不同
    （见各子类的类属性）。``EstFromPair`` / ``EstToPair`` / ``GetPriceRatio`` /
    ``GetCnyRef`` / ``GetDefaultCny`` 这几个方法一旦存在，``pair_service`` /
    ``sma_service`` / ``trading_service`` 就通过 ``_invoke`` 鸭子类型取数，
    **不用改**（见 ``README_pair_reference_migration.md``）。

    **刻意不含** EST 取值与期货自动校准 —— 那些是 :class:`FundPairReference`
    （``fundpairref.php:148``，CK2b 已迁）加的部分。拆开的理由：``PairReference``
    是 A/H/ADR 对比表那类**只读**用途的最小实现，而 ``FundPairReference``
    构造时还想写库（见它的 :meth:`~FundPairReference.__init__`），两者不该
    混在一个类里 —— 页面路径上构造引用对象不能顺带写库。
    """

    #: 配对表名：``Ab`` → ``abpair`` / ``Ah`` → ``ahpair`` / ``Adr`` → ``adrpair`` /
    #: ``Fund`` → ``fundpair``。对应 ``php/sql/sqlstocksymbol.php:123-126``。
    pair_table = "fundpair"

    #: ``$pair_ref``（``fundpairref.php:5``）。类级默认值，理由同 ``EstReference.pair_ref``：
    #: ``MysqlReference.__init__`` 会先调 ``load_data()``，那时实例属性还没赋值。
    pair_ref = None

    #: ``$cny_ref``（``fundpairref.php:6``）。类级默认值，理由同 ``pair_ref``。
    cny_ref = None
    #: ``$fLastCalibrationVal``（``fundpairref.php:8``）；没有为 ``None``，
    #: 由 ``pairestimate`` 回落到本次估值。
    last_calibration_val = None

    # -- 配对引用（与 :class:`EstReference` 同语义，这里**必须**就地实现） -----
    # PHP ``MyPairReference`` 自身就有 ``GetPairRef()``（``fundpairref.php:65``），
    # 而 :meth:`_build_cny_ref` 一开始就调 ``self.get_pair_ref()``。``PairReference``
    # 与 ``EstReference`` 是平行的两条 ``MysqlReference`` 子类，不互相继承，
    # 所以这里把同样的惰性查表逻辑再实现一遍 —— 表名用本类的 ``pair_table``
    # （``abpair`` / ``ahpair`` / ``adrpair`` / ``fundpair``）。
    def get_pair_ref(self) -> Any:
        """对应 ``MyPairReference::GetPairRef()``：**本符号跟踪的标的**。"""
        if self.pair_ref is not None:
            return self.pair_ref
        symbol = self._pair_symbol()
        if not symbol:
            return None
        self.pair_ref = _build_pair_reference(symbol, self.source)
        return self.pair_ref

    def _pair_symbol(self) -> Any:
        """查 ``pair_table`` 取被跟踪标的的符号；没有返回 ``None``。"""
        if self.source is None:
            return None
        symbol = self.get_symbol()
        if not symbol:
            return None
        return _lookup_pair_symbol(self.source, str(symbol), self.pair_table)

    def __init__(self, symbol: str, source: Any = None, quote_loader: Callable[[str], Any] = None):
        #: 先走 ``MysqlReference`` 的标准构造（装行情、取 stock_id、仓位），
        #: 再建汇率引用 + 装载校准值 —— 顺序与 PHP 一致：先有配对标的，再据此推导汇率。
        super().__init__(symbol, source=source, quote_loader=quote_loader)
        self._build_cny_ref()
        self.load_calibration()

    # -- 汇率引用（``fundpairref.php:16-43`` 的推导） ----------------------
    def _build_cny_ref(self) -> None:
        """对应 ``MyPairReference`` 构造里那一段 ``cny_ref`` 推导。

        ``GetPairRef()`` 拿不到时（``$pair_ref = false``）``cny_ref`` 也保持
        ``false``，与 PHP 一致。
        """
        pair_ref = self.get_pair_ref()
        if pair_ref is None:
            self.cny_ref = None
            return
        pair_symbol = pair_ref.get_symbol()
        #: ``$strCNY``（中间价符号）；只有走中间价分支才被赋值，
        #: 走离岸外汇（fx_）分支时直接建 ``cny_ref``、不碰这个变量。
        cny_symbol = False

        if pair_ref.is_sina_future_cn():
            # ``$strCNY`` 保持 false、``cny_ref`` 保持 false
            pass
        elif pair_ref.is_symbol_a():
            if self.is_symbol_a():
                # 用数值区间版本（``is_shanghai_b`` / ``is_shenzhen_b``）：PHP 的
                # ``_isDigitShangHaiB`` 判的是 ``900000 <= 数字 < 1000000``，
                # 不是字符串前缀 ``900`` —— 后者会把 901000~999999 漏掉。
                if pair_ref.is_shanghai_b():
                    cny_symbol = "USCNY"
                elif pair_ref.is_shenzhen_b():
                    cny_symbol = "HKCNY"
            # PHP 此处 ``else if ($this->IsSymbolH()) $strCNY = 'HKCNY';`` 被注释掉，
            # 属于死分支（进入本分支要求 pair 是 A，H 不可能是 A），照 PHP 保留注释。
            elif self.is_symbol_us() or self.is_sina_future_us():
                self.cny_ref = MysqlReference("fx_susdcnh", source=self.source)
        elif pair_ref.is_symbol_h():
            if self.is_symbol_a():
                cny_symbol = "HKCNY"
            elif self.is_symbol_us():
                self.cny_ref = MysqlReference("fx_susdhkd", source=self.source)
        elif pair_symbol == "znb_SENSEX":
            self.cny_ref = MysqlReference("fx_susdinr", source=self.source)
        else:
            if self.is_symbol_a():
                cny_symbol = "USCNY"

        if cny_symbol:
            # ``CnyReference`` 走中间价净值表 + 固定 09:15 时点（见其类定义），
            # 正是 PHP ``new CnyReference($strCNY)`` 的形态。
            self.cny_ref = CnyReference(cny_symbol, source=self.source)

    def get_cny_ref(self) -> Any:
        return self.cny_ref

    def get_default_cny(self, date: Any = False) -> float:
        """对应 ``GetDefaultCny($strDate)``（``fundpairref.php:75``）。

        复用 ``pairestimate.get_default_cny``：有汇率引用取它的值，否则 1.0。
        """
        return _pairestimate_default_cny(self, date)

    # -- 双向估算（``fundpairref.php:84`` / ``:94``） -----------------------
    def est_from_pair(self, pair_val: Any = False, cny: Any = False) -> Any:
        """``EstFromPair($fPairVal, $fCny)``：由**配对标的的价格**反推本标的估值。

        委托给 ``pairestimate.est_from_pair``（唯一实现），本方法只负责"把 ``self``
        作为 ref 传进去"。
        """
        return _est_from_pair(self, pair_val, cny)

    def est_to_pair(self, my_val: Any = False, cny: Any = False) -> Any:
        """``EstToPair($fMyVal, $fCny)``：由**本标的的价格**推配对标的的价格。"""
        return _est_to_pair(self, my_val, cny)

    # -- 比价（``fundpairref.php:104``） -----------------------------------
    def get_price_ratio(self, date: Any = False) -> float:
        """``GetPriceRatio($strDate)``：本价 / 由配对价反推的估值。

        ``empty()`` 判断用"空或取不到"口径；分母为 0（或估算失败）时回落 1.0，
        PHP 那边会得 ``INF`` 并画出来，本版宁可少一列也比摆个 ``INF`` 好。
        """
        pair_ref = self.get_pair_ref()
        if pair_ref is None:
            return 1.0
        if date:
            my_price = self.get_close(date)
            pair_price = pair_ref.get_close(date)
        else:
            my_price = self.get_price()
            pair_price = pair_ref.get_price()
        if my_price in (None, "") or pair_price in (None, ""):
            return 1.0
        try:
            my_val = float(my_price)
            pair_val = float(pair_price)
        except (TypeError, ValueError):
            return 1.0
        est = self.est_from_pair(pair_val, self.get_default_cny(date))
        if not est:
            return 1.0
        try:
            return my_val / float(est)
        except ZeroDivisionError:
            return 1.0

    def get_last_calibration_val(self) -> Any:
        return self.last_calibration_val

    # -- 校准装载（``fundpairref.php:48``） ---------------------------------
    def load_calibration(self) -> None:
        """对应 ``LoadCalibration()``。

        PHP 的 ``IsYahooNetValue()`` 分支对 A/H/ADR 恒为 ``false``，直接用自身
        ``stock_id``。有校准记录时取 ``close`` 当 ``fFactor``，并读
        ``lastcalibration`` 表（没有则回落校准日期那天的净值）当
        ``fLastCalibrationVal``；没有校准记录时 ``fFactor = 1.0 / 仓位``。
        """
        stock_id = self.get_stock_id()
        if stock_id is None:
            self.factor = self._fallback_factor()
            self.last_calibration_val = None
            return
        record = self._call("get_calibration_now", stock_id)
        close = getattr(record, "close", None)
        if close in (None, ""):
            self.factor = self._fallback_factor()
            self.last_calibration_val = None
            return
        try:
            self.factor = float(close)
        except (TypeError, ValueError):
            self.factor = self._fallback_factor()
            self.last_calibration_val = None
            return
        # ``fLastCalibrationVal``：优先 lastcalibration 表，没有则取校准日期那天净值
        # （对应 ``SqlGetNetValueByDate``）。
        last = self._call("get_last_calibration", stock_id)
        try:
            self.last_calibration_val = float(last) if last not in (None, "") else None
        except (TypeError, ValueError):
            self.last_calibration_val = None
        if self.last_calibration_val is None:
            record_date = getattr(record, "date", None)
            if record_date not in (None, ""):
                net = self._call("get_netvalue_close", stock_id, record_date)
                try:
                    self.last_calibration_val = float(net) if net not in (None, "") else None
                except (TypeError, ValueError):
                    self.last_calibration_val = None

    def _fallback_factor(self) -> float:
        """``1.0 / GetPosition()``；仓位为 0 时退化为 1.0（PHP 会得到 INF，本版不画）。"""
        position = self.get_position()
        if position:
            return 1.0 / float(position)
        return 1.0


class AbPairReference(PairReference):
    """对应 ``php/stock/fundpairref.php:124`` 的 ``AbPairReference``。"""

    pair_table = "abpair"


class AdrPairReference(PairReference):
    """对应 ``php/stock/fundpairref.php:132`` 的 ``AdrPairReference``。"""

    pair_table = "adrpair"


class AhPairReference(PairReference):
    """对应 ``php/stock/fundpairref.php:140`` 的 ``AhPairReference``。"""

    pair_table = "ahpair"


# ---------------------------------------------------------------------------
# FundPairReference 用到的小工具
# ---------------------------------------------------------------------------
def _ref_call(ref: Any, name: str, *args: Any) -> Any:
    """调引用对象上的方法；没有这个方法或抛异常都返回 ``None``。

    ``FundPairReference`` 的配对标的可能是任何一种引用对象
    （``MysqlReference`` / ``CnyReference`` / 联网抓取的那几种），
    **不能假设它一定有某个方法** —— PHP 那边靠 ``method_exists`` 兜着，
    这里 ``getattr`` 兜。
    """
    if ref is None:
        return None
    method = getattr(ref, name, None)
    if method is None:
        return None
    try:
        return method(*args)
    except Exception:
        return None


def _ref_flag(ref: Any, name: str) -> bool:
    """取引用对象上的布尔判定，取不到就算 ``False``。"""
    return bool(_ref_call(ref, name))


def _pair_val_is_false(value: Any) -> bool:
    """对应 PHP 的 ``== false`` 松散比较（``fundpairref.php:246`` 那两处）。

    PHP 里 ``if (($fEst = ...->GetNetValue($strDate)) == false)`` 用的是 ``==``，
    所以 ``0`` / ``"0"`` / ``""`` / ``false`` 都会被当成"没取到"而回落到下一路。
    Python 里必须**显式**把这些写出来，只判 ``None`` 会让净值为 0 的那一路
    意外通过（然后算出除零）。
    """
    if value is None or value is False:
        return True
    if isinstance(value, str):
        #: PHP 的字符串转布尔**只有** ``""`` 与 ``"0"`` 是假的 —— ``"0.0"`` 反而是真的。
        #: 所以这里照抄那个集合，不能用 ``float(...) == 0`` 近似：后者会把
        #: ``"0.0"`` 也当成"没取到"，与 PHP 走不同分支。
        return value.strip() in ("", "0")
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _fund_est_store(connection: Any) -> Any:
    """延迟取 ``FundEstStore``（``auto/sql/fundeststore.py``）。

    **延迟的原因**：``sql`` 包里的 ``fundeststore`` 会反向用到本模块的引用对象，
    模块级 ``import`` 会形成环，所以和 :meth:`QdiiReference._estimator` 里
    ``from calc.fundestcalc import ...`` 一样放在函数内。
    """
    from sql.fundeststore import FundEstStore
    return FundEstStore(connection)


class FundPairReference(PairReference):
    """对应 ``php/stock/fundpairref.php:148`` 的 ``FundPairReference``。

    相对 :class:`PairReference` 多三块：

    1. **四个 EST 取值** —— :meth:`get_official_date` / :meth:`get_official_net_value` /
       :meth:`get_fair_net_value` / :meth:`get_realtime_net_value`
       （``fundpairref.php:224``-``:275``）。``fundest_service`` 正是靠 ``_invoke``
       探测 ``get_fair_net_value`` / ``get_realtime_net_value`` 决定要不要多画
       两列（见 ``fundest_service.build_fund_est_columns``）；**没有这两个方法，
       EST 表就永远只有"官方"一档**。
    2. **``netvalue_ref``**（``:150`` / ``:208``）；
    3. **自动校准**（``:174`` / ``:188``）—— PHP 在**构造函数里**写库，本版收进
       ``auto_write`` 开关，理由见 :meth:`__init__`。

    .. note::

       PHP 里那个方法叫 ``AverageCalibraion``（**少一个 ``t``**，``:174``）。
       本版用正确拼写，``camelCase`` 别名两个拼写都给，免得对照时找不到。
    """

    #: ``$netvalue_ref``（``fundpairref.php:150``）；``None`` 对应 PHP 的 ``false``。
    netvalue_ref = None
    #: ``$realtime_callback``（``fundpairref.php:151``）。
    realtime_callback = None

    def __init__(self, symbol: str, source: Any = None, callback: Any = None,
                 quote_loader: Callable[[str], Any] = None,
                 auto_write: bool = False):
        """``callback`` 对应 PHP 构造的第二个参数 ``$callback``（实时估值回调）。

        **``auto_write`` 默认关是与 PHP 的刻意偏差**：PHP 在构造里无条件调
        ``AverageCalibraion()``，``GetOfficialNetValue()`` 里也直接调
        ``StockUpdateEstResult()`` —— 也就是说**每次页面渲染都在写库**。
        本项目的架构是「实时抓取只跑 daemon、web 只读」（``auto/refquote.py``），
        引用对象在 web 路径上会被反复构造，让它顺手写库既违背分层、
        也没办法在单测里关掉。写库交给 ``autocalibrationcli`` / ``fundestcli``
        统一负责；这里 ``auto_write=False`` 时**只算不写**。
        """
        #: 两个写相关开关要在 ``super().__init__()`` **之前**设好：父类构造会走
        #: ``load_calibration()``，那条路虽然不写库，但 ``_auto_write`` 读不到会
        #: 抛 ``AttributeError`` 而不是"默认不写"。
        self._auto_write = auto_write
        super().__init__(symbol, source=source, quote_loader=quote_loader)

        pair_ref = self.get_pair_ref()
        if pair_ref is None:
            return
        self.realtime_callback = callback

        # ``fundpairref.php:160-163``
        if _ref_flag(pair_ref, "is_sina_future_us"):
            if self._check_adjust_time(pair_ref, self):
                self.average_calibration()
        # ``fundpairref.php:165-170``
        if _ref_flag(self, "is_sina_future_us"):
            # PHP 原注释：``$this->netvalue_ref = $this; Can NOT do this in __construct``
            # —— 构造期间把自己赋给自己会形成循环引用太早，所以那一分支**不建
            # netvalue_ref**，只做校准。
            if self._check_adjust_time(self, pair_ref):
                self.average_calibration()
        else:
            #: ``new NetValueReference($strSymbol)``（``:170``）。Python 侧没有
            #: 对应的类，用 ``MysqlReference`` 近似 —— 见模块 docstring 的
            #: known deviation 一节。
            self.netvalue_ref = MysqlReference(symbol, source=source)

    # -- 自动校准（``fundpairref.php:174`` / ``:188``） ---------------------
    def _check_adjust_time(self, ref: Any, other: Any) -> bool:
        """``$ref->CheckAdjustFactorTime($other)``（``fundpairref.php:162`` / ``:168``）。

        ``auto_write`` 关时恒 ``False`` —— 闸门通过了也不写。
        """
        if not getattr(self, "_auto_write", False):
            return False
        return _ref_flag(ref, "check_adjust_factor_time") and _ref_call(
            ref, "check_adjust_factor_time", other) is True

    def average_calibration(self) -> Any:
        """对应 ``AverageCalibraion()``（``fundpairref.php:174``，PHP 拼写少了 ``t``）。

        算出校准因子 → 按**平均**写入 calibration 表 → 写 lastcalibration →
        :meth:`load_calibration` 重载。

        ``auto_write`` 关时只走到"算出因子"为止并返回它 —— 这样这个方法在页面
        路径上是纯读的，在 CLI 路径上（``auto_write=True``）才落库。

        **写库部分依赖数据源提供写入口**，本项目的 ``FundDataSource`` 还没有
        ``WriteDailyAverage`` / ``WriteVal`` 的对应方法（只有读），所以这里保留
        ``_calibration_writer`` 注入位：注入了才写，没注入就当作 dry-run。
        """
        pair_ref = self.get_pair_ref()
        stock_id = self.get_stock_id()
        if pair_ref is None or stock_id is None:
            return None
        factor = self.calc_factor(pair_ref.get_val(), self.get_val(), self.get_date())
        if factor is None:
            return None
        writer = getattr(self, "_calibration_writer", None)
        if writer is None or not self._auto_write:
            return factor
        try:
            writer(stock_id, self.get_date(), str(factor), self.get_price())
        except Exception:
            return factor
        self.load_calibration()
        return factor

    def calc_factor(self, pair_net_value: Any, net_value: Any, date: Any = False) -> Any:
        """对应 ``CalcFactor()``（``fundpairref.php:213``）。

        与 :meth:`est_from_pair` **严格互逆** —— 这是校准能自洽的前提：

        ============  ========================  ==============================
        本符号         ``EstFromPair``          ``CalcFactor``
        ============  ========================  ==============================
        A 股          ``pair * cny / factor``   ``pair * cny / netvalue``
        其它          ``pair / cny / factor``   ``pair / (netvalue * cny)``
        ============  ========================  ==============================

        **两个分支的汇率方向相反**，不要合并成一个式子。这也是它不能复用
        ``futurescalib.FuturesCalibrator.calc_factor`` 的原因 —— 后者只有 A 股
        那一式（``期货价 × cny / 指数价``，见 ``fundcalc.qdii_get_calibration``）。

        已知偏差：``$fCny`` 为 0 时 PHP 会得到 ``INF``，本版退回不含汇率的比值
        （宁可少一个汇率因子，也不把 ``INF`` 写进校准表）。
        """
        if pair_net_value in (None, "") or net_value in (None, ""):
            return None
        try:
            factor = float(pair_net_value) / float(net_value)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        cny_ref = self.get_cny_ref()
        if cny_ref is None:
            return factor
        cny = _ref_call(cny_ref, "get_val", date)
        try:
            cny = float(cny)
        except (TypeError, ValueError):
            return factor
        if cny == 0:
            return factor
        # PHP: ``if ($this->IsSymbolA()) $fNetValue /= $fCny; else $fNetValue *= $fCny;``
        return factor * cny if self.is_symbol_a() else factor / cny

    def get_net_value_ref(self) -> Any:
        """对应 ``GetNetValueRef()``（``fundpairref.php:208``）。

        PHP 的写法是 ``$this->netvalue_ref ? $this->netvalue_ref : $this`` ——
        **没有净值引用时返回自己**（``netvalue_ref`` 为 ``false``）。
        """
        return self.netvalue_ref if self.netvalue_ref is not None else self

    # -- 官方日期（``fundpairref.php:224``） --------------------------------
    def get_official_date(self) -> Any:
        """取配对标的的行情日期。

        中间这一层是 PHP 的 ``if ($this->cny_ref->GetClose($strOfficialDate) === false)``
        分支：汇率引用在**那一天**没有收盘价时（当天汇率还没公布），换用
        ``fundest`` 表里本标的的最新日期 —— PHP 原注释
        ``// Load last value from database``。
        """
        pair_ref = self.get_pair_ref()
        if pair_ref is None:
            return None
        date = _ref_call(pair_ref, "get_date")
        cny_ref = self.get_cny_ref()
        if cny_ref is None:
            return date
        close = _ref_call(cny_ref, "get_close", date)
        if not _pair_val_is_false(close):
            return date
        return self._official_date_from_est(date)

    def _official_date_from_est(self, fallback: Any) -> Any:
        """``GetFundEstSql()->GetDateNow($this->GetStockId())``（``:232``）。

        需要数据库连接；拿不到就退回传入的日期 —— 这比返回 ``None`` 好：
        官方 EST 那一列还能画出来，只是日期可能不是最新。
        """
        stock_id = self.get_stock_id()
        if stock_id is None:
            return fallback
        connection = getattr(self.source, "connection", None)
        if connection is None:
            return fallback
        try:
            date = _fund_est_store(connection).get_latest_date(stock_id)
        except Exception:
            return fallback
        return date if date else fallback

    # -- 三个 EST（``fundpairref.php:238`` / ``:256`` / ``:267``） ----------
    def get_official_net_value(self) -> Any:
        """对应 ``GetOfficialNetValue()``（``fundpairref.php:238``）。

        三路取中间层的值，**自上而下**回落：

        1. 境外期货（黄金除外）→ ``str_vwap``（``floatval($this->pair_ref->strVWAP)``）；
        2. 该日期的**净值**（``GetNetValue``）；
        3. 配对标的的**现价**（``GetVal``）。

        回落用 PHP 的 ``== false`` 口径（见 :func:`_pair_val_is_false`）——
        净值正好为 0 时也要继续往下试。

        最后的 ``StockUpdateEstResult`` 是**写库**行为，只在 ``auto_write``
        打开时发生；闸门是 PHP 的 ``GetHourMinute() < 2055``（``:252``）。
        """
        pair_ref = self.get_pair_ref()
        if pair_ref is None:
            return None
        date = self.get_official_date()
        cny_ref = self.get_cny_ref()
        cny = _ref_call(cny_ref, "get_val", date) if cny_ref is not None else False

        est = None
        if _ref_flag(pair_ref, "is_sina_future_except_gold_cn"):
            est = getattr(pair_ref, "str_vwap", None)
        if _pair_val_is_false(est):
            est = _ref_call(pair_ref, "get_net_value", date)
        if _pair_val_is_false(est):
            est = _ref_call(pair_ref, "get_val")
        if _pair_val_is_false(est):
            return None

        value = self.est_from_pair(est, cny)
        if self._auto_write:
            self._record_official(pair_ref, value, date)
        return value

    def _record_official(self, pair_ref: Any, value: Any, date: Any) -> bool:
        """``StockUpdateEstResult()``（``fundpairref.php:252``）。

        闸门是**配对标的的时刻**（``$this->pair_ref->GetHourMinute()``），
        不是当前时刻 —— 所以这里传 ``gate_hhmm=None`` 跳过 ``write_if_ready``
        自己的时刻判断，只留它的"净值未到货"那条。
        """
        stock_id = self.get_stock_id()
        connection = getattr(self.source, "connection", None)
        if stock_id is None or connection is None:
            return False
        hour_minute = _ref_call(pair_ref, "get_hour_minute")
        if hour_minute is None:
            return False
        try:
            store = _fund_est_store(connection)
            if int(hour_minute) >= store.DEFAULT_GATE_HHMM:
                return False
            return bool(store.write_if_ready(stock_id, date, value, gate_hhmm=None))
        except Exception:
            return False

    def get_fair_net_value(self) -> Any:
        """对应 ``GetFairNetValue()``（``fundpairref.php:256``）。

        两条 return，注意 PHP 的**控制流是穿过来的**而不是两个 ``if``：

        * 有汇率引用、且官方日期不等于汇率日期 → 返回带汇率的估值；
        * 否则（没有汇率引用，**或**日期相同）落到第二个判断：期货非黄金 /
          官方日期不等于配对标的日期 → 返回不带汇率的估值；
        * 都不满足 → ``False``（本版 ``None``）。

        第二条 Return 不传 cny 是因为 :meth:`est_from_pair` 内部会自己用
        :meth:`get_default_cny` 兜底（``fundpairref.php:87``）。
        """
        pair_ref = self.get_pair_ref()
        if pair_ref is None:
            return None
        official_date = self.get_official_date()
        cny_ref = self.get_cny_ref()
        if cny_ref is not None:
            cny_date = _ref_call(cny_ref, "get_date")
            if official_date != cny_date:
                return self.est_from_pair(pair_ref.get_val(), _ref_call(cny_ref, "get_val"))
        if _ref_flag(pair_ref, "is_sina_future_except_gold_cn") or (
                official_date != _ref_call(pair_ref, "get_date")):
            return self.est_from_pair(pair_ref.get_val())
        return None

    def get_realtime_net_value(self) -> Any:
        """对应 ``GetRealtimeNetValue()``（``fundpairref.php:267``）。

        **没有回调就没有实时估值**（PHP 返回 ``false``）—— 这一档靠
        ``$realtime_callback`` 存在与否决定，所以:meth:`fundest_service` 探测
        ``get_realtime_net_value`` 时，没建回调的对象应该让它**连列都不出现**；
        本方法返回 ``None`` 即可，列的出现与否由调用方按返回值判断。
        """
        if self.realtime_callback is None:
            return None
        cny_ref = self.get_cny_ref()
        cny = _ref_call(cny_ref, "get_val") if cny_ref is not None else False
        try:
            value = self.realtime_callback()
        except Exception:
            return None
        return self.est_from_pair(value, cny)


# ---------------------------------------------------------------------------
# 配对引用工厂（对应 php/stock.php:328-379）
# ---------------------------------------------------------------------------
def _get_ab_pair_reference(symbol: str, source: Any = None) -> Any:
    """对应 ``_getAbPairReference()``：A/B 配对引用，双向查找（A→B 或 B→A 都成）。"""
    if _lookup_pair_symbol(source, symbol, "abpair"):
        return AbPairReference(symbol, source=source)
    reversed_symbol = _lookup_pair_symbol_reverse(source, symbol, "abpair")
    if reversed_symbol:
        return AbPairReference(reversed_symbol, source=source)
    return None


def _get_ah_pair_reference(symbol: str, source: Any = None) -> Any:
    """对应 ``_getAhPairReference()``：A/H 配对引用，双向查找。"""
    if _lookup_pair_symbol(source, symbol, "ahpair"):
        return AhPairReference(symbol, source=source)
    reversed_symbol = _lookup_pair_symbol_reverse(source, symbol, "ahpair")
    if reversed_symbol:
        return AhPairReference(reversed_symbol, source=source)
    return None


def _get_adr_pair_reference(symbol: str, source: Any = None) -> Any:
    """对应 ``_getAdrPairReference()``：ADR/H 配对引用，双向查找。"""
    if _lookup_pair_symbol(source, symbol, "adrpair"):
        return AdrPairReference(symbol, source=source)
    reversed_symbol = _lookup_pair_symbol_reverse(source, symbol, "adrpair")
    if reversed_symbol:
        return AdrPairReference(reversed_symbol, source=source)
    return None


def StockGetPairReferences(symbol: str, source: Any = None) -> tuple:
    """对应 ``StockGetPairReferences($strSymbol)``（``php/stock.php:352``）。

    从入口符号出发，按"谁先命中"链式推导 ab / ah / adr 三个配对引用，
    每对可能为 ``None``（对应 PHP 的 ``false``）。返回 ``(ab_ref, ah_ref, adr_ref)``。
    """
    ab_ref = ah_ref = adr_ref = None
    ab_ref = _get_ab_pair_reference(symbol, source)
    if ab_ref is not None:
        ah_ref = _get_ah_pair_reference(ab_ref.get_symbol(), source)
        if ah_ref is not None:
            h_ref = ah_ref.get_pair_ref()
            h_symbol = h_ref.get_symbol() if h_ref is not None else None
            if h_symbol:
                adr_ref = _get_adr_pair_reference(h_symbol, source)
    else:
        ah_ref = _get_ah_pair_reference(symbol, source)
        if ah_ref is not None:
            h_ref = ah_ref.get_pair_ref()
            h_symbol = h_ref.get_symbol() if h_ref is not None else None
            if h_symbol:
                adr_ref = _get_adr_pair_reference(h_symbol, source)
            ab_ref = _get_ab_pair_reference(ah_ref.get_symbol(), source)
        else:
            adr_ref = _get_adr_pair_reference(symbol, source)
            if adr_ref is not None:
                h_ref = adr_ref.get_pair_ref()
                h_symbol = h_ref.get_symbol() if h_ref is not None else None
                if h_symbol:
                    ah_ref = _get_ah_pair_reference(h_symbol, source)
                    if ah_ref is not None:
                        ab_ref = _get_ab_pair_reference(ah_ref.get_symbol(), source)
    return (ab_ref, ah_ref, adr_ref)


def StockGetFundPairReference(symbol: str, source: Any = None, callback: Any = None,
                              auto_write: bool = False) -> Any:
    """对应 ``StockGetFundPairReference()``（``woody/res/php/_stock.php:142``）。

    门禁是 ``SqlGetFundPair($strSymbol)`` —— 也就是 ``fundpair`` 表里有没有这一行。
    这个查找 CK2 已经实现好了（:func:`_lookup_pair_symbol`，``pair_table`` 默认
    就是 ``fundpair``），所以这里**没有规则**，只是复用 + 包一层。

    没有配对记录返回 ``None``（对应 PHP 的 ``false``）—— 调用方用它决定
    ``_mystock.php:131`` 那个分支走不走。
    """
    if not _lookup_pair_symbol(source, symbol, "fundpair"):
        return None
    return FundPairReference(symbol, source=source, callback=callback,
                             auto_write=auto_write)


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
            #: ``qdiiref.php:245`` —— 持仓型标的一律走 ``HoldingsReference``
            self.set_est_ref(build_est_reference(est_symbol, self.source))
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
MysqlReference.GetMyStockLink = MysqlReference.get_my_stock_link

PairReference.GetCnyRef = PairReference.get_cny_ref
PairReference.GetDefaultCny = PairReference.get_default_cny
PairReference.EstFromPair = PairReference.est_from_pair
PairReference.EstToPair = PairReference.est_to_pair
PairReference.GetPriceRatio = PairReference.get_price_ratio
PairReference.GetLastCalibrationVal = PairReference.get_last_calibration_val

FundPairReference.GetNetValueRef = FundPairReference.get_net_value_ref
FundPairReference.CalcFactor = FundPairReference.calc_factor
FundPairReference.GetOfficialDate = FundPairReference.get_official_date
FundPairReference.GetOfficialNetValue = FundPairReference.get_official_net_value
FundPairReference.GetFairNetValue = FundPairReference.get_fair_net_value
FundPairReference.GetRealtimeNetValue = FundPairReference.get_realtime_net_value
FundPairReference.AverageCalibration = FundPairReference.average_calibration
#: PHP 原名的拼法是 ``AverageCalibraion``（**少一个 ``t``**，``fundpairref.php:174``），
#: 一并挂上，免得照着 PHP 源码搜过来时找不到。
FundPairReference.AverageCalibraion = FundPairReference.average_calibration

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
