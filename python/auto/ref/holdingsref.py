"""持仓型基金的引用对象 ``HoldingsReference``（CK4）。

对应 PHP ``php/stock/holdingsref.php``（441 行 / 24 方法）。

## 这个类和 QDII 那条路的差别

前面 P0 迁的是 **配对型** 基金：一只基金跟踪**一个**标的（``SZ161130`` 跟
``^NDX``），估值 = 标的价 × 校准值。本类是 **持仓型**：一只基金持有**几十只**
成分股（``SH513050`` 持有腾讯、美团、PDD…），估值 = 各成分股按持仓比例的
加权涨幅 × 汇率调整。

分支判据在 ``woody/res/php/_stock.php:133``：

```php
function StockGetHoldingsReference($strSymbol)
{
    if (SqlCountHoldings($strSymbol) > 0)   // holdings 表有明细行
    {
        return new HoldingsReference($strSymbol);
    }
    return false;
}
```

## 估值的锚：持仓日

构造时先读 ``holdingsdate`` 得到**持仓清单更新日** ``strHoldingsDate``，然后：

* **净值**取这一天的（``SqlGetNetValueByDate``）；
* 每只成分股的**旧价**取这一天的 ``adjclose``（:meth:`get_holding_date_history`）；
* 每只成分股的**新价**取当前价（或指定日期的 ``adjclose``，或配对基金的
  实时 EST）。

于是 ``新净值 = 持仓日净值 × (1 + 加权涨幅)`` —— 见 :meth:`_est_net_value`。

## 三个净值（与 ``FundPairReference`` 同构）

=========================  ==================================================
PHP                         含义
=========================  ==================================================
``GetOfficialNetValue()``   **官方 EST**：按成分股**收盘价**估（``strEstDate``）
``GetFairNetValue()``       **参考 EST**：按成分股**当前价**估 + 官方偏差补偿
``GetRealtimeNetValue()``   **实时 EST**：对**能用配对实时估**的成分股再估一次
=========================  ==================================================

``fOfficialEstAdjust`` 是"官方 EST 与当天真实净值的差"，后两个净值会把它加回去
（``holdingsref.php:420`` / ``:434``）—— 含义是"承认自己的估算有系统性偏差，
用最近一次的偏差做补偿"。

## 刻意与 PHP 不同的两处（known deviation）

1. **默认不写库**：PHP 的 ``GetOfficialNetValue()`` 里直接调
   ``StockUpdateEstResult()``（``holdingsref.php:390``），也就是**每次页面渲染
   都在写 fundest 表**。本项目架构是「写只跑 daemon / CLI，web 只读」
   （``auto/refquote.py``），所以写库收进 ``auto_write`` 开关，默认关；
   与 :class:`refobj.FundPairReference` 的处理一致。
2. **``NetValueReference`` 用 ``MysqlReference`` 近似**：Python 侧没有对应的类
   （``php/stock/netvalueref.php``），:class:`refobj.FundPairReference` 里也是
   这么近似的。``netvalue_ref`` 目前只被 :meth:`get_net_value_ref` 暴露出去，
   估值算法本身不用它（PHP 里同样只在别处被引用）。
3. **``_adjust_to_cny`` 加了零值保护**：PHP 的 ``return $fOldCNY / $fCNY;``
   是裸除法（``holdingsref.php:247``），``new_ref`` 没价时 PHP 直接
   ``DivisionByZeroError`` fatal。生产环境四个汇率引用永远有价，不会触发；
   但演示库缺 ``fx_susdcny`` / ``fx_shkdcny`` 时实测会把页面打挂。
   这里退回 ``1.0``（等价于"不调汇率"），宁可数不准也不要页面 500。
"""

from __future__ import annotations

from typing import Any

from ref.refobj import CnyReference, FundPairReference, MysqlReference, _lookup_pair_symbol
from ref.stockref import StockReference
from sql.holdingstable import HoldingsDateSql, HoldingsSql
from sql.historytable import StockHistorySql

#: ``php/debug.php:11`` 的 ``MIN_FLOAT_VAL``。
#: 用在这两处：``总持仓比例 > 它`` 才做归一化（``holdingsref.php:337``）、
#: ``GetProportion`` 的前值 > 它 才算得出比例（``sqldailyclose.php:95``）。
MIN_FLOAT_VAL = 0.0000005

#: PHP 里持仓比例是**百分数**（30.68 表示 30.68%），构造时不做换算。
#: 只有 :meth:`get_holdings_ratio_display` 展示时才四舍五入成整数。


def ref_adjust_forex(ref: Any, adjust_hkd: float, adjust_usd: float) -> float:
    """``RefAdjustForex($ref, $fAdjustHKD, $fAdjustUSD)``（``holdingsref.php:3``）。

    成分股**自己**的汇率调整系数：A 股不调（本来就是人民币），港股用 HKD，
    其余（美股等）用 USD。
    """
    if _flag(ref, "is_symbol_a"):
        return 1.0
    if _flag(ref, "is_symbol_h"):
        return float(adjust_hkd)
    return float(adjust_usd)


def pick_est_date(date_h: Any, date_us: Any) -> Any:
    """``:97-101``：从港股/美股两个市场日期里挑官方 EST 用的那个。

    PHP 的三条件是 ``($strDateUS === false) || ($strDateH == $strDateUS) ||
    (strtotime($strDateH) < strtotime($strDateUS))`` —— 取**更早**的那个市场
    日期：港股比美股早收盘，取晚的会漏掉还没收盘的市场。

    抽成纯函数是为了能单独测：构造期要真连库建出成分股引用才拿得到日期，
    而这条选择逻辑本身不需要那些。
    """
    if date_h is None:
        return date_us
    if date_us is None:
        return date_h
    if str(date_h) <= str(date_us):
        return date_h
    return date_us


def _add_holdings_ratio(total: Any, ratio: float) -> float:
    """``_add_holdings_ratio(&$fTotal, $fRatio)``（``holdingsref.php:11``）。

    PHP 用 ``false`` 表示"还没有这个分量"（因为 0 是合法值），这里用 ``None``。
    """
    if total is None:
        return float(ratio)
    return float(total) + float(ratio)


def _flag(ref: Any, name: str) -> bool:
    """鸭子类型调符号判定：``is_symbol_a`` / ``is_symbol_h`` / ``is_etf_a`` …

    与 ``refobj._ref_flag`` 同款，但那个是私有的（跨模块导入不进），这里
    就地实现一份：持仓引用的成分股可能来自不同实现
    （``MysqlReference`` / ``FundPairReference`` / 测试替身），只认方法名。
    """
    method = getattr(ref, name, None)
    if method is None:
        return False
    return bool(method())


def _call(ref: Any, name: str, *args: Any) -> Any:
    """鸭子类型调方法；不存在返回 ``None``（对应 PHP 的 ``false``）。"""
    method = getattr(ref, name, None)
    if method is None:
        return None
    return method(*args)


def ratio_of(ratio_map: dict, stock_id: Any) -> float:
    """按 ``stock_id`` 取持仓比例，**容忍 int / str 不一致**。

    这是照搬 PHP 时最容易踩的一个坑：``$arHoldingsRatio`` 的键来自
    ``SqlGetHoldingsArray()``（MySQL 取出的 ``stock_id``），而
    ``$ref->GetStockId()`` 返回的是字符串。PHP 里这不成问题 ——
    **数字字符串作数组键时会被自动转成整数**（``$a['142']`` 与 ``$a[142]``
    是同一个键），所以 ``$this->arHoldingsRatio[$strStockId]`` 永远命中。
    Python 的 dict 没有这层隐式转换，``{'142': 30.68}[142]`` 直接 KeyError。

    后果很隐蔽：比例全取成 0 → ``Σ比例 = 0`` → 加权涨幅 = ``0 - 1 = -1``
    → ``净值 × (1 + -1) = 0``，页面上官方 EST 与参考 EST 全是 **0**，
    且不报任何错。**见到持仓型基金的估值整列是 0，先怀疑这里。**
    """
    value = ratio_map.get(stock_id)
    if value is None:
        value = ratio_map.get(str(stock_id))
    if value is None:
        try:
            value = ratio_map.get(int(stock_id))
        except (TypeError, ValueError):
            value = None
    return 0.0 if value is None else float(value)


class HoldingsReference(MysqlReference):
    """持仓型基金的引用对象，对应 ``php/stock/holdingsref.php:17``。"""

    # -- 构造 ---------------------------------------------------------------
    def __init__(self, symbol: str, source: Any = None, quote_loader: Any = None,
                 auto_write: bool = False,
                 holdings_sql: Any = None, holdings_date_sql: Any = None):
        """``holdings_sql`` / ``holdings_date_sql`` 是测试注入位。

        默认从 ``source.connection`` 现造（与 :mod:`refobj` 里其它引用对象
        取连接的路子一致）；单测里传假对象就能不连库。

        ``auto_write`` 默认 ``False`` —— 见模块 docstring 的 known deviation：
        PHP 在 ``GetOfficialNetValue()`` 里无条件写 ``fundest``，本版默认只算不写。
        """
        #: 要在 ``super().__init__()`` **之前**设好：父类构造会走 ``load_data()``，
        #: 而本类的持仓装载在父类之后、用到 ``self.source``。
        self._auto_write = auto_write
        self._holdings_sql = holdings_sql
        self._holdings_date_sql = holdings_date_sql

        #: ``$netvalue_ref``（``:47``）—— 见模块 docstring 的 known deviation ②
        self.netvalue_ref = None
        #: ``$uscny_ref`` / ``$usdcny_ref`` / ``$hkcny_ref`` / ``$hkdcny_ref``（``:20-23``）
        self.uscny_ref = None
        self.usdcny_ref = None
        self.hkcny_ref = None
        self.hkdcny_ref = None
        #: ``$ar_holdings_ref`` / ``$ar_realtime_ref``（``:25-26``）
        self.holdings_refs: list = []
        self.realtime_refs: list = []
        #: ``$strNetValue`` / ``$strHoldingsDate``（``:28-29``）
        self.net_value: Any = None
        self.holdings_date: Any = None
        #: ``$arHoldingsDateHistory``（``:30``）—— 成分股在持仓日的 adjclose
        self._holdings_date_history: dict = {}
        #: ``$arHoldingsRatio``（``:31``）—— ``{成分股 stock_id: 比例}``
        self.holdings_ratio: dict = {}
        #: ``$strEstDate``（``:33``）—— 官方 EST 用的日期
        self.est_date: Any = None
        #: ``$fOfficialEstAdjust``（``:34``）
        self.official_est_adjust = 0.0
        #: 三个净值的缓存（``:36-38``），``None`` 对应 PHP 的 ``false``
        self._official_net_value: Any = None
        self._fair_net_value: Any = None
        self._realtime_net_value: Any = None
        #: ``$fRatioCN`` / ``$fRatioHK`` / ``$fRatioUS``（``:40-42``）
        self.ratio_cn: Any = None
        self.ratio_hk: Any = None
        self.ratio_us: Any = None

        super().__init__(symbol, source=source, quote_loader=quote_loader)
        self.netvalue_ref = MysqlReference(symbol, source=source)
        self._load_holdings()

    # -- 持仓装载（``:50-107``） -------------------------------------------
    def _load_holdings(self) -> None:
        """构造函数后半段：读持仓日 → 净值 → 成分股清单 → 汇率引用 → EST 日期。

        PHP 原样是一整段 ``if`` 嵌套，这里拆成一条主流程 + 两个早退条件。
        **任何一个环节缺失都会让本对象退化成一只普通股票引用**（持仓列表为空），
        分支判据（``SqlCountHoldings > 0``）只看表有没有行，不看这里成不成功 ——
        所以"页面没出持仓段落"时先查持仓日与净值这两样（PHP 那边有
        ``DebugString('Missing net value on ...')``，本版留在 :meth:`_load_holdings`
        的 ``missing_net_value`` 返回值里）。
        """
        stock_id = self.get_stock_id()
        if stock_id is None:
            return

        date = self._read_holdings_date(stock_id)
        if date is None:
            return
        self.holdings_date = date

        net_value = _call(self.source, "get_netvalue_close", stock_id, date)
        if net_value is None:
            #: PHP: ``DebugString(__CLASS__.'->'.__FUNCTION__.': Missing net value on '.date)``
            self.missing_net_value = True
            return
        self.net_value = net_value

        self.holdings_ratio = self._read_holdings_ratio(stock_id)
        self._build_holding_refs()

    def _read_holdings_date(self, stock_id: Any) -> Any:
        sql = self._holdings_date_sql
        if sql is None:
            connection = getattr(self.source, "connection", None)
            if connection is None:
                return None
            sql = HoldingsDateSql(connection)
        return sql.read_date(stock_id)

    def _read_holdings_ratio(self, stock_id: Any) -> dict:
        sql = self._holdings_sql
        if sql is None:
            connection = getattr(self.source, "connection", None)
            if connection is None:
                return {}
            sql = HoldingsSql(connection)
        return sql.get_holdings_array(stock_id)

    def _build_holding_refs(self) -> None:
        """``:62-101``：为每只成分股建引用对象，并顺带定 EST 日期与汇率引用。"""
        date_h = None
        date_us = None
        for holding_id in self.holdings_ratio:
            ratio = float(self.holdings_ratio[holding_id])
            holding_symbol = _call(self.source, "get_stock_symbol", holding_id)
            if holding_symbol is None:
                continue

            #: ``:65-73``：成分股自己也是配对基金时，用 ``FundPairReference``
            #: 建（这样它能给出实时 EST），并记进 ``ar_realtime_ref``。
            if _lookup_pair_symbol(self.source, holding_symbol):
                ref = FundPairReference(holding_symbol, source=self.source)
                self.realtime_refs.append(ref)
            else:
                ref = MysqlReference(holding_symbol, source=self.source)
            self.holdings_refs.append(ref)

            if _flag(ref, "is_symbol_a"):
                self.ratio_cn = _add_holdings_ratio(self.ratio_cn, ratio)
            elif _flag(ref, "is_symbol_h"):
                self.ratio_hk = _add_holdings_ratio(self.ratio_hk, ratio)
                if self.hkcny_ref is None:
                    self.hkcny_ref = CnyReference("HKCNY", source=self.source)
                if self.hkdcny_ref is None:
                    self.hkdcny_ref = MysqlReference("fx_shkdcny", source=self.source)
                if date_h is None:
                    date_h = _call(ref, "get_date")
            else:
                self.ratio_us = _add_holdings_ratio(self.ratio_us, ratio)
                if self.uscny_ref is None:
                    self.uscny_ref = CnyReference("USCNY", source=self.source)
                if self.usdcny_ref is None:
                    self.usdcny_ref = MysqlReference("fx_susdcny", source=self.source)
                if date_us is None:
                    date_us = _call(ref, "get_date")

        #: ``:97-101``：EST 日期取**更早**的那个市场日期 —— 详见 :func:`pick_est_date`。
        self.est_date = pick_est_date(date_h, date_us)

    # -- 取值的几条小路 -----------------------------------------------------
    def get_net_value_string(self) -> Any:
        """``GetNetValueString()``（``:110``）。"""
        return self.net_value

    def set_net_value_string(self, value: Any) -> None:
        """``SetNetValueString()``（``:115``）。"""
        self.net_value = value

    def get_net_value_ref(self) -> Any:
        """``GetNetValueRef()``（``:120``）。"""
        return self.netvalue_ref

    def get_cny_ref(self) -> Any:
        """``GetCnyRef()``（``:125``）。"""
        return self.uscny_ref

    def get_usdcny_ref(self) -> Any:
        """``GetUsdcnyRef()``（``:130``）。"""
        return self.usdcny_ref

    def get_hkcny_ref(self) -> Any:
        """``GetHkcnyRef()``（``:135``）。"""
        return self.hkcny_ref

    def get_hkdcny_ref(self) -> Any:
        """``GetHkdcnyRef()``（``:140``）。"""
        return self.hkdcny_ref

    def get_holdings_date(self) -> Any:
        """``GetHoldingsDate()``（``:145``）。"""
        return self.holdings_date

    def set_holdings_date(self, date: Any) -> None:
        """``SetHoldingsDate()``（``:150``）：改日期要**清空**缓存的历史价。

        PHP 里是 ``unset`` 再重建数组；这里同理 —— 旧的历史价是按旧日期取的，
        留着会让下一次估值用错基准。
        """
        self.holdings_date = date
        self._holdings_date_history = {}

    def get_holdings_ratio_array(self) -> dict:
        """``GetHoldingsRatioArray()``（``:158``）。"""
        return self.holdings_ratio

    def get_holdings_ref_array(self) -> list:
        """``GetHoldingsRefArray()``（``:201``）。"""
        return self.holdings_refs

    def count_holdings(self) -> int:
        """``CountHoldings()``（``:206``）。

        ``fundestparagraph.php:168`` 用它判断要不要给"穷举持仓"链接
        （``<= 4`` 只持仓才有意义）。
        """
        return len(self.holdings_refs)

    def get_realtime_ref_array(self) -> list:
        """``GetRealtimeRefArray()``（``:220``）。"""
        return self.realtime_refs

    def use_realtime_est(self) -> bool:
        """``UseRealtimeEst()``（``:225``）：有成分股能用配对实时估才算。"""
        return len(self.realtime_refs) > 0

    def get_holdings_ratio_display(self) -> str:
        """``GetHoldingsRatioDisplay()``（``:211``）：“A股31% 港股27% 美股42%”。

        PHP 用 ``number_format()``（0 位小数 + 千分位）后拼 ``'%'``，
        最后 ``rtrim($str, ' ')`` 去掉末尾空格。
        """
        text = ""
        if self.ratio_cn is not None:
            text += "A股%s%% " % format(float(self.ratio_cn), ",.0f")
        if self.ratio_hk is not None:
            text += "港股%s%% " % format(float(self.ratio_hk), ",.0f")
        if self.ratio_us is not None:
            text += "美股%s%% " % format(float(self.ratio_us), ",.0f")
        return text.rstrip(" ")

    # -- 成分股历史价 -------------------------------------------------------
    def check_holdings_date(self, date: Any) -> bool:
        """``CheckHoldingsDate($strDate)``（``:163``）：所有成分股这天都有收盘价。"""
        sql = self._history_sql()
        if sql is None:
            return False
        for holding_id in self.holdings_ratio:
            if sql.get_close(holding_id, date) is None:
                return False
        return True

    def get_holding_date_history(self, his_sql: Any = None) -> dict:
        """``GetHoldingDateHistory($his_sql)``（``:263``）。

        返回 ``{成分股 stock_id: 持仓日的 adjclose}``，就是估值式里的"旧价"。
        **用 ``adjclose`` 而不是 ``close``**：持仓日到现在若有分红送股，
        用 close 会把除权当成下跌。

        PHP 传 ``$his_sql`` 进来是为了复用同一个连接；这里允许不传（自己造），
        但结果会**缓存**在对象上（PHP 同样缓存），所以重复调用不查库。
        """
        if not self._holdings_date_history:
            sql = his_sql if his_sql is not None else self._history_sql()
            if sql is None:
                return {}
            for ref in self.holdings_refs:
                stock_id = ref.get_stock_id()
                value = sql.get_adjclose(stock_id, self.holdings_date)
                if value is not None:
                    self._holdings_date_history[stock_id] = float(value)
        return self._holdings_date_history

    def get_proportion_array(self, date: Any, prev_date: Any) -> Any:
        """``GetProportionArray($strDate, $strPrevDate)``（``:173``）。

        每个成分股在两日之间的涨跌比例（含汇率还原），**任一成分股缺数据就整段
        返回 ``None``** —— PHP 用 ``return false`` 提前退出，调用方
        （``netvaluehistoryparagraph.php:25``）据此决定要不要画那一列。

        A 股成分股不调汇率；港股用 ``uscny/hkcny`` 双汇率还原；美股用 ``uscny``。
        """
        sql = self._history_sql()
        if sql is None:
            return None
        result = []
        for ref in self.holdings_refs:
            proportion = sql.get_proportion(ref.get_stock_id(), date, prev_date)
            if not proportion:
                return None
            value = float(proportion)
            if _flag(ref, "is_symbol_a"):
                value /= float(_call(self.uscny_ref, "get_val", date) or 1.0)
                value *= float(_call(self.uscny_ref, "get_val", prev_date) or 1.0)
            elif _flag(ref, "is_symbol_h"):
                hk_now = float(_call(self.hkcny_ref, "get_val", date) or 1.0)
                hk_prev = float(_call(self.hkcny_ref, "get_val", prev_date) or 1.0)
                value /= float(_call(self.uscny_ref, "get_val", date) or 1.0) / hk_now
                value *= float(_call(self.uscny_ref, "get_val", prev_date) or 1.0) / hk_prev
            result.append(value)
        return result

    def _history_sql(self) -> Any:
        connection = getattr(self.source, "connection", None)
        if connection is None:
            return None
        return StockHistorySql(connection)

    # -- 汇率调整（``:230-261``） -------------------------------------------
    def _adjust_to_cny(self, old_ref: Any, new_ref: Any, date: Any = None) -> float:
        """``_adjustToCNY($old_ref, $new_ref, $strDate)``（``:230``）。

        **旧价取持仓日那天的汇率**（``GetClose($this->strHoldingsDate)``），
        取不到才回落到当前价 —— 因为整个估值都是以持仓日为基准的，
        混用"今天的汇率"会把基准日的汇率波动算进涨幅。

        两个与 PHP 对齐的细节：

        * PHP 是 ``if ($strOldCNY = $old_ref->GetClose(...))`` —— **真值判断**，
          所以 ``"0"`` / ``""`` 也会回落到 ``GetPrice()``，不只是 ``false``。
          这里同样用真值判断，而不是 ``is not None``。
        * PHP 结尾是裸的 ``return $fOldCNY / $fCNY;``（``holdingsref.php:247``），
          **没有零保护**。生产环境四个汇率引用永远有价，所以从不触发；
          但演示库 / 数据不全时 PHP 会 ``DivisionByZeroError`` 直接 fatal
          （实测踩到）。这里退回 ``1.0``（"不做汇率调整"）让页面还能出数 ——
          **这是刻意的 known deviation**，见模块 docstring 第 3 条。
        """
        if old_ref is None:
            return 1.0
        old = _call(old_ref, "get_close", self.holdings_date)
        old_value = float(old) if old else float(_call(old_ref, "get_price") or 0.0)
        new_value = float(_call(new_ref, "get_price") or 0.0)
        if date is not None:
            new_close = _call(new_ref, "get_close", date)
            if new_close:
                new_value = float(new_close)
        if new_value == 0.0:
            return 1.0
        return old_value / new_value

    def get_adjust_usd(self, date: Any = None) -> float:
        """``GetAdjustUSD($strDate)``（``:251``）。

        ``$strDate`` 为空**且**是 A 股 ETF 时，用"官方中间价 vs 市场价"两个汇率
        （``uscny_ref`` vs ``usdcny_ref``）；否则是同一个引用在两日之间的比值。
        """
        if date is None and _flag(self, "is_etf_a"):
            return self._adjust_to_cny(self.uscny_ref, self.usdcny_ref)
        return self._adjust_to_cny(self.uscny_ref, self.uscny_ref, date)

    def get_adjust_hkd(self, date: Any = None) -> float:
        """``GetAdjustHKD($strDate)``（``:257``）。"""
        if date is None and _flag(self, "is_etf_a"):
            return self._adjust_to_cny(self.hkcny_ref, self.hkdcny_ref)
        return self._adjust_to_cny(self.hkcny_ref, self.hkcny_ref, date)

    # -- 核心：估值（``:287-343``） ----------------------------------------
    def _est_net_value(self, date: Any = None, realtime: bool = False) -> float:
        """``_estNetValue($strDate, $bRealtime)``（``:287``）。

        PHP 原注释 ``// (x - x0) / x0 = sum{ r * (y - y0) / y0}`` 就是全部：

        ```
        新净值 = 持仓日净值 × (1 + 加权涨幅 × 自身汇率调整 × 仓位)
        加权涨幅 = Σ[比例 × (新价 / 旧价) / 成分股汇率调整] / Σ比例 - 1
        ```

        三个参数分支：

        * ``date``       —— 用成分股在那天的 ``adjclose`` 代替当前价（算官方 EST）；
        * ``realtime``   —— 对能实时估的成分股用 ``EstFromPair()`` 代替当前价；
        * 两者都不给     —— 用当前价（算参考 EST）。

        **注意 ``$fTotalRatio > MIN_FLOAT_VAL`` 那一句**（``:337``）：比例之和
        太小时不做归一化，``$fTotalChange`` 保持"加权新比例之和"。照抄 ——
        虽然正常持仓不会走到那条路，但除以 0 会抛异常而 PHP 不会。
        """
        adjust_hkd = self.get_adjust_hkd(date)
        adjust_usd = self.get_adjust_usd(date)
        history = self.get_holding_date_history()

        total_change = 0.0
        total_ratio = 0.0
        for ref in self.holdings_refs:
            stock_id = ref.get_stock_id()
            #: 键可能是 int 而 ``get_stock_id()`` 给 str —— 见 :func:`ratio_of`
            ratio = ratio_of(self.holdings_ratio, stock_id)
            total_ratio += ratio

            price = _call(ref, "get_price")
            if date is not None:
                sql = self._history_sql()
                if sql is not None:
                    adjclose = sql.get_adjclose(stock_id, date)
                    if adjclose is not None:
                        price = adjclose
            value = float(price or 0.0)
            if realtime:
                est = _call(ref, "est_from_pair")
                if est is not None:
                    value = float(est)

            if stock_id in history:
                change = ratio * (value / history[stock_id])
                change /= ref_adjust_forex(ref, adjust_hkd, adjust_usd)
                total_change += change

        if total_ratio > MIN_FLOAT_VAL:
            total_change /= total_ratio
        total_change -= 1.0
        total_change *= ref_adjust_forex(self, adjust_hkd, adjust_usd)
        total_change *= float(_call(self, "get_position") or 1.0)
        return float(self.net_value) * (1.0 + total_change)

    def get_net_value_change(self) -> float:
        """``GetNetValueChange()``（``:345``）：估算净值 / 持仓日净值。"""
        return self._est_net_value() / float(self.net_value)

    # -- 三个净值（``:352-438``） ------------------------------------------
    def _need_adjust_official_date(self, date: Any) -> bool:
        """``_needAdjustOfficialDate($strDate)``（``:352``）。

        该日汇率还没公布 → 官方 EST 用不上这天，退到上一个有记录的日期。
        """
        if self.uscny_ref is not None:
            return _call(self.uscny_ref, "get_close", date) is None
        if self.hkcny_ref is not None:
            return _call(self.hkcny_ref, "get_close", date) is None
        return False

    def get_official_date(self) -> Any:
        """``GetOfficialDate()``（``:365``）。"""
        date = self.get_date()
        if _flag(self, "is_fund_a"):
            if self.est_date:
                date = self.est_date
            if self._need_adjust_official_date(date):
                sql = self._fund_est_sql()
                if sql is not None:
                    date = sql.get_date_prev(self.get_stock_id(), date)
        return date

    def get_official_net_value(self) -> Any:
        """``GetOfficialNetValue()``（``:378``）：按成分股**收盘价**估的官方 EST。

        算出之后会记下 ``official_est_adjust`` = 当天真实净值 - 估算值，
        后面两个净值把它加回去做偏差补偿。

        PHP 在这一步无条件 ``StockUpdateEstResult()`` 写库；本版只在
        ``auto_write=True`` 时写（见模块 docstring）。
        """
        if self._official_net_value is None:
            stock_id = self.get_stock_id()
            date = self.get_official_date()
            self._official_net_value = self._est_net_value(date)
            real = _call(self.source, "get_netvalue_close", stock_id, date)
            if real is not None:
                self.official_est_adjust = float(real) - self._official_net_value
            if self._auto_write:
                self._write_est_result(stock_id, self._official_net_value, date)
        return self._official_net_value

    def _write_est_result(self, stock_id: Any, value: Any, date: Any) -> None:
        """``StockUpdateEstResult($strStockId, $fNetValue, $strDate)``（``php/stock.php:148``）。

        "Only update when net value is NOT ready" —— 净值已到货就不写。
        """
        if not value:
            return
        if _call(self.source, "get_netvalue_close", stock_id, date) is not None:
            return
        sql = self._fund_est_sql()
        if sql is None:
            return
        sql.write(stock_id, date, str(value))

    def _need_fair_net_value(self) -> bool:
        """``_needFairNetValue()``（``:395``）：A 股 ETF 永远要算参考 EST。"""
        if _flag(self, "is_etf_a"):
            return True
        date = self.get_official_date()
        if self.est_date != date:
            return True
        if self.uscny_ref is not None:
            return str(_call(self.uscny_ref, "get_date")) != str(date)
        if self.hkcny_ref is not None:
            return str(_call(self.hkcny_ref, "get_date")) != str(date)
        return False

    def get_fair_net_value(self) -> Any:
        """``GetFairNetValue()``（``:412``）：当前价估的参考 EST + 偏差补偿。

        **不需要算时返回 ``None``**（PHP 返回 ``false``）——
        ``fundest_service`` 靠这个决定要不要画"参考 EST"那一列。
        """
        if self._fair_net_value is None and self._need_fair_net_value():
            self.get_official_net_value()
            self._fair_net_value = self._est_net_value() + self.official_est_adjust
        return self._fair_net_value

    def get_realtime_net_value(self) -> Any:
        """``GetRealtimeNetValue()``（``:426``）：只有成分股能实时估时才有值。"""
        if self._realtime_net_value is None and self.use_realtime_est():
            self.get_official_net_value()
            self._realtime_net_value = self._est_net_value(None, True) + self.official_est_adjust
        return self._realtime_net_value

    def _fund_est_sql(self) -> Any:
        """``GetFundEstSql()``（``fundest`` 表）。"""
        connection = getattr(self.source, "connection", None)
        if connection is None:
            return None
        from sql.fundeststore import FundEstStore

        return FundEstStore(connection)


def StockGetHoldingsReference(symbol: str, source: Any = None, **kwargs: Any) -> Any:
    """``StockGetHoldingsReference($strSymbol)``（``woody/res/php/_stock.php:133``）。

    判据是 ``SqlCountHoldings($strSymbol) > 0`` —— **只看 holdings 表有没有行**，
    不看净值/行情齐不齐。返回 ``None``（对应 PHP 的 ``false``）表示这只符号
    不是持仓型，调用方接着走后面的分支。
    """
    if source is None:
        return None
    stock_id = _call(source, "get_stock_id", symbol)
    if stock_id is None:
        return None
    connection = getattr(source, "connection", None)
    if connection is None:
        return None
    if HoldingsSql(connection).count(stock_id) <= 0:
        return None
    return HoldingsReference(symbol, source=source, **kwargs)


#: camelCase 别名（AGENTS.md：snake_case 主名 + camelCase 类属性别名）
setattr(HoldingsReference, "GetHoldingsDate", HoldingsReference.get_holdings_date)
setattr(HoldingsReference, "CountHoldings", HoldingsReference.count_holdings)
setattr(HoldingsReference, "GetHoldingsRatioDisplay", HoldingsReference.get_holdings_ratio_display)
setattr(HoldingsReference, "GetOfficialNetValue", HoldingsReference.get_official_net_value)
setattr(HoldingsReference, "GetFairNetValue", HoldingsReference.get_fair_net_value)
setattr(HoldingsReference, "GetRealtimeNetValue", HoldingsReference.get_realtime_net_value)
setattr(HoldingsReference, "UseRealtimeEst", HoldingsReference.use_realtime_est)
setattr(HoldingsReference, "GetHoldingsRefArray", HoldingsReference.get_holdings_ref_array)
setattr(HoldingsReference, "GetRealtimeRefArray", HoldingsReference.get_realtime_ref_array)
