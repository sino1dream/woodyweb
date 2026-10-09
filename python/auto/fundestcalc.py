"""官方 EST 的计算与记录。

对应 PHP：

* ``php/stock/qdiiref.php`` ``_QdiiReference::EstNetValue()`` —— 计算主体
* ``php/stock/qdiiref.php`` ``_getOfficialEstVal()`` / ``_getFairEstVal()``
* ``php/stock.php`` ``StockUpdateEstResult()`` —— 写入（含闸门）
* ``php/stock.php`` ``StockCompareEstResult()`` —— 事后误差检查与告警

## 官方 EST 的取数链路（与 PHP 逐步对应）

```text
官方 EST = GetQdiiValue(官方估值源, 当天中间价)
             │                │
             │                └─ cny_ref->GetClose(est_ref 的日期)
             └─ _getOfficialEstVal(est_ref 的日期)：
                  ① 中间层**净值表**里该日期的值（XOP 净值那种）
                  ② 否则用中间层**行情价**（_getEstPrice，含 SH000869 那种取昨收的兜底）
```

**关键分支**（对应 ``EstNetValue()`` 的 if/else）：

* 当天中间价**存在** → 正常算出官方 EST，并走 `UpdateEstNetValue()` 记录；
* 当天中间价**不存在**（美股还没收盘） → **回退读取 `fundest` 里最新一条**，
  把它的日期与估值当成当前的官方 EST。

回退这一支就是"官方 EST 在官方净值出来前是活的、出来后被固定"的实现方式。

## 为什么这层值得单独做

页面上「基金溢价记录」的「官方EST / 时间 / 误差」三列全部来自这里；
而"误差" = 真实净值 vs 当时记录的官方 EST —— 用来验证整套估算算法准不准。
"""

from __future__ import annotations

import datetime
from typing import Any

from fundcalc import PalmmicroFundCalculator
from fundeststore import FundEstStore, hour_minute_of
from futurescalib import build_futures_calibrator
from stockdata import get_leverage_hedge_symbol


# 对应 PHP ``StockCompareEstResult()``（php/stock.php:136）的阈值：
# 真实净值与官方 EST 的偏差超过 1.0% 就 trigger_error 告警。
ESTIMATE_ERROR_THRESHOLD = 1.0


class EstRecord:
    """一条官方 EST 记录。"""

    __slots__ = ("date", "value", "time")

    def __init__(self, date: Any, value: Any, time: Any = None):
        self.date = date
        self.value = value
        self.time = time

    def as_dict(self) -> dict:
        return {"date": self.date, "value": self.value, "time": self.time}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "EstRecord(date=%r, value=%r, time=%r)" % (self.date, self.value, self.time)


class EstimateCalculator:
    """按 PHP 的链路算出官方 EST，并负责记录与误差告警。

    ``source`` 是 ``FundDataSource``（提供校准值/净值/中间层数据），
    ``store`` 是 :class:`~fundeststore.FundEstStore`。
    """

    def __init__(self, source: Any, store: FundEstStore, quote_time_provider: Any = None,
                 now_provider: Any = None, futures_client: Any = None,
                 futures_calibrator: Any = None):
        self.source = source
        self.store = store
        #: 误差告警累积在这里（对应 PHP 的 trigger_error），由调用方打印。
        self.warnings: list = []
        #: 中间层行情时刻的取数口（返回 ``'20:30:00'`` 这类文本）。
        #: 默认从数据源的 ``get_quote_time()`` 取；测试可注入固定值。
        self.quote_time_provider = quote_time_provider
        #: 当前时刻（对应 PHP ``DebugGetTime()``），测试可注入。
        self.now_provider = now_provider
        #: 期货行情客户端（实时 EST 的数据源）。``None`` 时退回读快照表。
        self.futures_client = futures_client
        #: 期货配对与校准（``futurescalib.FuturesCalibrator``）。
        #: ``None`` 时按数据源的连接自动创建 —— 配对查不到还有内置兜底映射。
        if futures_calibrator is not None:
            self.futures_calibrator = futures_calibrator
        else:
            connection = getattr(source, "connection", None)
            self.futures_calibrator = (
                build_futures_calibrator(source) if connection is not None else None
            )

    # -- 时刻 -------------------------------------------------------------
    def _now_time(self) -> str:
        """当前时刻文本；对应 PHP ``DebugGetTime()``。"""
        if self.now_provider is not None:
            value = self.now_provider()
            if isinstance(value, str):
                return value
            if isinstance(value, datetime.datetime):
                return value.strftime("%H:%M:%S")
        return datetime.datetime.now().strftime("%H:%M:%S")

    # -- 定价上下文 -------------------------------------------------------
    def _resolve(self, symbol: str):
        """解析出算三个 EST 都要用的那一组上下文。

        三个 EST（官方 / 参考 / 实时）**同源**，差别只在"用哪个中间层值"与
        "用哪天的汇率"。所以把公共部分抽出来：

        ==================  ==========================================================
        返回项              含义与出处
        ==================  ==========================================================
        ``stock_id``        基金在 ``stock`` 表里的 id
        ``data``            基金行 + 中间层行的配置（见 :meth:`_build`）
        ``calculator``      对应的 ``PalmmicroFundCalculator``
        ``est_symbol``      实际取中间层行情的符号（**已做杠杆替代解析**）
        ``est_id``          上述符号的 stock_id
        ``mid_date``        中间层最新数据的日期（对应 ``$est_ref->GetDate()``）
        ``mid_quote``       中间层当前报价（对应 ``$pair_ref->GetVal()``）
        ``mid_net_value``   中间层在 ``mid_date`` 上的**净值**（对应 ``$est_ref->GetNetValue()``）
        ``cny_date``        汇率最新日期（对应 ``$cny_ref->GetDate()``）
        ``cny_mid``         ``mid_date`` 当天的中间价（可能为 ``None``）
        ``cny_current``     最新中间价（对应 ``$cny_ref->GetVal()`` 兜底）
        ==================  ==========================================================

        任一项缺失就返回 ``None``，调用方跳过。
        """
        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            return None

        data = self._build(symbol)
        if not data:
            return None

        est_symbol = self._est_symbol(symbol)
        if not est_symbol:
            return None
        est_id = self.source.get_stock_id(est_symbol)
        if est_id is None:
            return None

        mid_date = self._latest_date(est_id)
        if mid_date is None:
            return None

        cny_id = self.source.get_stock_id("USCNY")
        cny_date = self.source.get_latest_net_value_date(cny_id) if cny_id else None
        cny_mid = self._net_value(cny_id, mid_date) if cny_id else None
        cny_current = self._latest_net_value(cny_id) if cny_id else None
        if cny_current is None:
            # 汇率完全取不到就没法算任何一个 EST
            return None

        return {
            "stock_id": stock_id,
            "fund_symbol": symbol,
            "data": data,
            "calculator": PalmmicroFundCalculator(data),
            "est_symbol": est_symbol,
            "est_id": est_id,
            "mid_date": mid_date,
            # 实时快照价（实时 EST 用）
            "realtime_quote": self._middle_quote(est_symbol, data),
            # 当日行情价（参考 EST 用，对应 $pair_ref->GetVal()）
            "day_quote": self._fair_quote(est_symbol, mid_date),
            # 中间层净值（官方 EST 优先用，对应 $est_ref->GetNetValue()）
            "mid_net_value": self._net_value(est_id, mid_date),
            "cny_date": cny_date,
            "cny_mid": cny_mid,
            "cny_current": cny_current,
        }

    # -- 单个基金 ---------------------------------------------------------
    def compute_official(self, symbol: str) -> Any:
        """算该基金的官方 EST（不写库）。

        对应 ``EstNetValue()`` 的前半段：

        ```php
        $strDate = $est_ref->GetDate();
        if ($this->strOfficialCNY = $cny_ref->GetClose($strDate))
        {
            $this->fOfficialNetValue = $this->GetQdiiValue($this->_getOfficialEstVal($strDate), $this->strOfficialCNY);
            $this->strOfficialDate = $strDate;
            $this->UpdateEstNetValue();
        }
        else { /* 回退读 fundest 最新一条 */ }
        ```

        返回 ``EstRecord``；取不到时返回 ``None``。
        """
        ctx = self._resolve(symbol)
        if ctx is None:
            return None

        # 官方日期就是中间层日期；汇率优先用它当天那条，没有才退回最新
        rate = ctx["cny_mid"] if ctx["cny_mid"] is not None else ctx["cny_current"]

        est_value = self._official_est_value(ctx["est_symbol"], ctx["mid_date"], ctx["data"])
        if est_value is None:
            return None
        value = self._qdii_value(ctx["calculator"], ctx["data"], est_value, rate)
        if not value:
            return None
        return EstRecord(ctx["mid_date"], value, None)

    def compute_fair(self, symbol: str) -> Any:
        """算该基金的**参考 EST**（``FairEst``），不写库。

        对应 ``FundPairReference::GetFairNetValue()``
        （``php/stock/fundpairref.php:256``）：

        ```php
        $strOfficialDate = $this->GetOfficialDate();      // = pair_ref 的日期 = 中间层日期
        if ($this->cny_ref)
        {
            // 中间层日期与**汇率日期**不一致 -> 用当前汇率重算
            if ($strOfficialDate != $this->cny_ref->GetDate())
                return $this->EstFromPair($this->pair_ref->GetVal(), $this->cny_ref->GetVal());
        }
        if ($this->pair_ref->IsSinaFutureExceptGoldCN() || ($strOfficialDate != $this->pair_ref->GetDate()))
            return $this->EstFromPair($this->pair_ref->GetVal());
        return false;      // 两者都一致 -> 不显示（与官方 EST 相同，没有信息量）
        ```

        这里 ``GetOfficialDate()`` 对 ``FundPairReference`` 返回的是
        **``pair_ref`` 的日期**（即中间层日期），所以第二个条件里的
        ``$strOfficialDate != $this->pair_ref->GetDate()`` 恒为假 ——
        **真正决定参考 EST 是否出现的是"汇率日期"那一条**。

        **参考 EST 与官方 EST 同源，差别只在"用中间层当前价 + 当前汇率"。**
        汇率日期与中间层日期一致时不显示（PHP 返回 ``false``）——
        这不是算不出来，是没有意义。

        用途（作者原话）：验证算法准确度看**官方 EST**；
        按溢价折价决定套利与配对交易的看**参考 EST**
        （A 股或美股休市的日子尤其要看它）。
        """
        ctx = self._resolve(symbol)
        if ctx is None:
            return None

        # 汇率日期与中间层日期一致 -> 参考 EST 等于官方 EST，PHP 此时返回 false
        if ctx["cny_date"] is not None and str(ctx["cny_date"]) == str(ctx["mid_date"]):
            return None

        # 用中间层的**当日行情**（对应 `$pair_ref->GetVal()`），不用实时快照 ——
        # 两者混用会让参考 EST 与实时 EST 变成同一个值（实测踩过）。
        # `day_quote` 取不到时按 `_middle_day_quote()` 的顺序兜底，
        # 否则演示库里（中间层行情只到 09-30、最新日期是 10-02）会直接算出空值。
        quote = ctx["day_quote"]
        if quote is None:
            quote = self._middle_day_quote(ctx["est_symbol"], ctx["mid_date"], ctx["data"])
        if quote is None:
            return None
        value = self._qdii_value(ctx["calculator"], ctx["data"], quote, ctx["cny_current"])
        if not value:
            return None
        return EstRecord(ctx["mid_date"], value, None)

    def _fair_quote(self, est_symbol: str, date: Any) -> Any:
        """中间层在 ``date`` 上的**行情收盘价**，对应 ``$pair_ref->GetVal()``。

        与 :meth:`_middle_quote` 的区别：那个优先取实时快照，这个取指定日期的
        ``stockhistory`` 收盘价 —— 参考 EST 要的是"当日价"，不是"此刻的实时价"。
        """
        est_id = self.source.get_stock_id(est_symbol)
        if est_id is None:
            return None
        reader = getattr(self.source, "get_history_close", None)
        if reader is None:
            return None
        try:
            value = reader(est_id, date)
        except Exception:
            return None
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def compute_realtime(self, symbol: str) -> Any:
        """算该基金的**实时 EST**（``RealtimeEst``），不写库。

        对应 ``FundPairReference::GetRealtimeNetValue()``
        （``php/stock/fundpairref.php:267``）：

        ```php
        if ($this->realtime_callback)
        {
            $fCny = $this->cny_ref ? $this->cny_ref->GetVal() : false;
            return $this->EstFromPair(call_user_func($this->realtime_callback), $fCny);
        }
        ```

        即：**在参考 EST 的基础上叠加实时数据**（作者原话见 skill 第 1.4 节）。

        本版把"实时数据"取成**中间层在 ``estquote`` 快照里的最新价**
        （``realtimequote`` 也行，见 :meth:`_middle_quote` 的取数顺序）。
        它与参考 EST 的差别是：参考 EST 用中间层的**当日价**，
        实时 EST 用**实时数据** —— 在有实时行情时两者会分道扬镳。

        取数优先级：

        1. **配对期货的实时价**（``futurespair`` → ``hf_NQ`` / ``hf_ES``）——
           这是作者文档里实时 EST 的正解（"叠加 NQ 期货实时数据的影响"）；
        2. 中间层的**实时快照价**（``estquote`` / ``realtimequote``）——
           没有期货配对时退而求其次。

        两者都取不到返回 ``None``（不编造）。
        """
        ctx = self._resolve(symbol)
        if ctx is None:
            return None

        quote = self._futures_realtime_quote(ctx) or ctx["realtime_quote"]
        if quote is None:
            return None
        value = self._qdii_value(ctx["calculator"], ctx["data"], quote, ctx["cny_current"])
        if not value:
            return None
        return EstRecord(ctx["mid_date"], value, None)

    def _futures_realtime_quote(self, ctx: dict) -> Any:
        """配对期货的实时价（换算成"中间层同口径"的数值）。

        对应 PHP 的 ``realtime_callback`` + ``EstFromPair()``：

        ```php
        return $this->EstFromPair(call_user_func($this->realtime_callback), $fCny);
        ```

        ``EstFromPair($fPairVal)`` 是 ``(期货价 / 汇率) / 校准值``，
        而外层 ``GetQdiiValue()`` 会再乘一次汇率，两次约掉 ——
        所以这里返回 ``期货价 / 校准值``，得到"以期货计的中间层估值"。

        没有期货配对、没有期货行情、或没有校准值时返回 ``None``，
        由调用方回落到中间层的实时快照。
        """
        calibrator = getattr(self, "futures_calibrator", None)
        if calibrator is None:
            return None

        # 中间层是"杠杆替代"后的 ETF（QQQ），但期货挂在**指数**（^NDX）上，
        # 所以要先从 profile 拿原始 est_symbol，不能直接用 ctx["est_symbol"]。
        index_symbol = self._index_symbol(ctx)
        if not index_symbol:
            return None

        future_symbol = calibrator.future_symbol_of(index_symbol)
        if not future_symbol:
            return None

        quote = self._fetch_future_quote(future_symbol)
        if quote is None:
            return None

        value = calibrator.realtime_source_value(index_symbol, quote)
        if value is None:
            return None
        return value

    def _index_symbol(self, ctx: dict) -> Any:
        """该基金跟踪的**指数**符号（``^NDX``），即期货配对的键。

        ``ctx["est_symbol"]`` 是杠杆替代后的 ETF（``QQQ``）；
        期货配对表用的是指数（``^NDX``）。所以这里从 profile 重新取。
        """
        symbol = ctx.get("fund_symbol")
        if not symbol:
            return None
        reader = getattr(self.source, "get_fund_profile", None)
        if reader is None:
            return None
        try:
            profile = reader(symbol)
        except Exception:
            return None
        if profile is None:
            return None
        return getattr(profile, "est_symbol", None) or getattr(profile, "pair_symbol", None)

    def _fetch_future_quote(self, future_symbol: str) -> Any:
        """抓一条期货行情；抓不到返回 ``None``。

        客户端可注入（``futures_client``），便于测试与换源。
        """
        client = getattr(self, "futures_client", None)
        if client is not None:
            try:
                return client.fetch_one(future_symbol)
            except Exception:
                return None
        # 没有注入客户端时不在这里抓 —— 估算是热路径，抓取应交给后台进程。
        # 快照表里的值由 quoteupdater 写入，这里退回读库。
        return self._stored_future_quote(future_symbol)

    def _stored_future_quote(self, future_symbol: str) -> Any:
        """从 ``realtimequote`` 快照读期货价（后台抓取进程写的）。"""
        reader = getattr(self.source, "get_stored_quote", None)
        if reader is None:
            return None
        try:
            return reader(future_symbol)
        except Exception:
            return None

    def record_official(self, symbol: str, gate_hhmm: Any = None) -> bool:
        """算官方 EST 并按 PHP 的闸门记录进 ``fundest``。

        对应 ``_QdiiReference::EstNetValue()`` 里的 ``$this->UpdateEstNetValue()``
        → ``StockUpdateEstResult()``。

        三个条件都会检查（见 :meth:`~fundeststore.FundEstStore.write_if_ready`）：

        1. 当天真实净值还没到（"Only update when net value is NOT ready"）；
        2. 中间层时刻早于 20:55 —— 时刻来源优先级：
           注入的 ``quote_time_provider`` → 数据源的 ``get_quote_time()``
           → 当前时刻（``now_provider``，对应 PHP ``DebugGetTime()``）；
        3. 该日期还没有记录（有就不覆盖）。

        ``gate_hhmm=None``（默认）表示**跳过 20:55 闸门**，只保留"净值未到"与
        "已有不覆盖"两条。因为实时行情快照在演示环境里可能是几天前的，
        套上 20:55 会一行都写不进去；需要严格复刻 PHP 时传 ``2055``。
        """
        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            return False

        record = self.compute_official(symbol)
        # 回退分支拿到的本来就是库里已有的记录（time 非空），不必再写
        if record is None or record.time is not None:
            return False

        stamp = self._est_quote_time(symbol) or self._now_time()
        limit = None if gate_hhmm is None else (
            FundEstStore.DEFAULT_GATE_HHMM if gate_hhmm is True else int(gate_hhmm)
        )
        return self.store.write_if_ready(
            stock_id, record.date, record.value, time=stamp, gate_hhmm=limit
        )

    def record_official_all(self, symbols: Any = None, gate_hhmm: Any = None) -> dict:
        """批量为一批基金记录官方 EST，返回 ``{符号: 是否写入}``。

        放进定时任务用；每次访问页面的单只记录走 :meth:`record_official`。
        """
        result = {}
        for symbol in (symbols or self._fund_symbols()):
            try:
                result[symbol] = self.record_official(symbol, gate_hhmm)
            except Exception as exc:
                self.warnings.append("%s 记录官方 EST 失败：%s: %s" % (symbol, type(exc).__name__, exc))
                result[symbol] = False
        return result

    # -- 误差检查 ---------------------------------------------------------
    def compare_with_net_value(self, symbol: str, net_value: Any, date: Any) -> Any:
        """真实净值到货后，比对当时记录的官方 EST 并告警。

        对应 ``StockCompareEstResult()``（``php/stock.php:127``）：

        ```php
        $fPercentage = StockGetPercentage(floatval($strNetValue), floatval($strEstValue));
        if (abs($fPercentage) > 1.0)
        {
            trigger_error('Net value estimation error ...');
        }
        ```

        ``StockGetPercentage(a, b) = (b/a - 1) * 100``，即"真实值 → 估算值"的偏差。

        返回偏差百分比；没有记录或参数无效时返回 ``None``。
        超过 ``ESTIMATE_ERROR_THRESHOLD``（1%）会追加到 :attr:`warnings`。
        """
        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None or net_value is None or net_value == "" or date is None:
            return None
        estimate = self.store.get_close(stock_id, date)
        if estimate is None:
            return None
        try:
            base = float(net_value)
            if abs(base) <= 0.0000005:
                return None
            change = (float(estimate) / base - 1.0) * 100.0
        except (TypeError, ValueError):
            return None

        if abs(change) > ESTIMATE_ERROR_THRESHOLD:
            self.warnings.append(
                "%s 实际值 %s %s估值 %s 误差:%.2f%%" % (symbol, net_value, date, estimate, change)
            )
        return change

    def scan_errors(self, symbols: Any = None) -> list:
        """扫描一批基金，把有记录的日期与真实净值逐一比对。

        返回 ``[(symbol, date, net_value, estimate, change), ...]``。
        对应 PHP 在每次 ``UpdateOfficialNetValue()`` 时的即时检查 ——
        这里做成可批量跑的形式，便于放进定时任务。
        """
        from historytable import NetValueHistorySql

        netvalue = NetValueHistorySql(self.store.connection)
        result = []
        for symbol in (symbols or self._fund_symbols()):
            stock_id = self.source.get_stock_id(symbol)
            if stock_id is None:
                continue
            for record in self.store.get_all(stock_id, 0, 0):
                date = record["date"]
                real = netvalue.get_close(stock_id, date)
                if real is None:
                    continue
                change = self.compare_with_net_value(symbol, real, date)
                if change is None:
                    continue
                result.append((symbol, date, real, record["close"], change))
        return result

    # -- 内部：数据与口径 -------------------------------------------------
    def _build(self, symbol: str) -> dict:
        """组装算官方 EST 所需的最小配置：**基金行 + 中间层行**。

        只用 ``FundDataSource`` 上已有的接口（仓位 / 净值 / 校准值），
        **不经过 ``StockDataArrayBuilder``** —— 那个 builder 会连带去查
        中间层行情、汇率、持仓等一大堆东西，其中任何一项缺失都可能抛异常，
        而异常一旦被吞掉就变成"官方 EST 静默算不出来"，很难查。

        返回的 dict 里：

        * 键 ``symbol`` 是基金自己那一行（``position`` / ``netvalue`` / ``calibration``）；
        * 键 ``<中间层符号>``（如 ``QQQ``）是中间层那一行，**至少带 ``calibration``**，
          因为 ``_qdii_value()`` 要用它把中间层估值换算成贡献值。

        缺任何一项就返回空 dict 让调用方跳过，不抛异常。
        """
        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            return {}

        net_value = self._latest_net_value(stock_id)
        if net_value is None:
            return {}
        calibration = self._calibration_of(stock_id)
        if not calibration:
            return {}

        est_symbol = self._est_symbol(symbol)
        config = {
            "symbol_hedge": est_symbol or "",
            "position": self._position_of(stock_id),
            "netvalue": float(net_value),
            "calibration": float(calibration),
        }
        # **校准时的汇率**（``cny_ref``）：``_qdii_value()`` 用它当缩放基准。
        # 缺了它就会回落到"传进来的当前汇率"，于是算参考 EST 时
        # ``est_quote_ref = cny/cny_ref`` 被约掉、参考 EST 反而与官方 EST 同向变化
        # （实测把 4.4110 算成 4.6139）。
        calibration_cny = self._calibration_cny(stock_id, symbol)
        if calibration_cny is not None:
            config["CNY"] = float(calibration_cny)
        if est_symbol:
            est_id = self.source.get_stock_id(est_symbol)
            middle_calibration = self._calibration_of(est_id) if est_id else None
            if middle_calibration:
                config[est_symbol] = {
                    "calibration": float(middle_calibration),
                    "position": self._position_of(est_id),
                }
        return config

    def _calibration_cny(self, stock_id: Any, symbol: str) -> Any:
        """**校准发生那一刻**用的汇率（``cny_ref``）。

        依据：``data["CNY"]`` 在 PHP 里是校准记录写下来时的汇率 ——
        ``QdiiGetCalibration(est, cny, netvalue)`` 用的就是它，
        所以它必须与 ``calibrationhistory`` 那条记录**同时期**。

        取数顺序：

        1. 校准日期（``get_calibration_now()`` 的 date）当天的 ``USCNY``；
        2. 该日期之前最近一条 ``USCNY``（假期/时差兜底）；
        3. 都没有则返回 ``None``，由调用方回落到"当前汇率"。
        """
        cny_id = self.source.get_stock_id("USCNY")
        if cny_id is None:
            return None

        calibration_date = None
        reader = getattr(self.source, "get_calibration_now", None)
        if reader is not None:
            try:
                record = reader(stock_id)
            except Exception:
                record = None
            if record is not None:
                calibration_date = getattr(record, "date", None)

        if calibration_date is None:
            return None

        value = self._net_value(cny_id, calibration_date)
        if value is not None:
            return value
        # 该日期没有汇率（周末/假期）-> 退到之前最近一条
        fallback = getattr(self.source, "get_prev_history_close", None)
        if fallback is not None:
            try:
                return fallback(cny_id, calibration_date)
            except Exception:
                return None
        # 最后退到"当前汇率"（与旧行为一致，作为兜底）
        return self._latest_net_value(cny_id)

    def _latest_net_value(self, stock_id: Any) -> Any:
        """基金自己的最新单位净值。"""
        reader = getattr(self.source, "get_latest_net_value", None)
        if reader is not None:
            try:
                record = reader(stock_id)
            except Exception:
                record = None
            if record is not None:
                value = getattr(record, "close", None)
                if value is not None:
                    return value
        close_reader = getattr(self.source, "get_latest_net_value_close", None)
        if close_reader is None:
            return None
        try:
            return close_reader(stock_id)
        except Exception:
            return None

    def _calibration_of(self, stock_id: Any) -> Any:
        """取校准值：先看当天那条，再退回 ``lastcalibration``。"""
        if stock_id is None:
            return None
        reader = getattr(self.source, "get_calibration_now", None)
        if reader is not None:
            try:
                record = reader(stock_id)
            except Exception:
                record = None
            if record is not None:
                value = getattr(record, "close", None)
                if value:
                    return value
        fallback = getattr(self.source, "get_last_calibration", None)
        if fallback is None:
            return None
        try:
            return fallback(stock_id)
        except Exception:
            return None

    def _position_of(self, stock_id: Any) -> float:
        """仓位；取不到按 1.0（与 ``get_default_position()`` 的非 LOF 分支一致）。"""
        reader = getattr(self.source, "get_position", None)
        if reader is None or stock_id is None:
            return 1.0
        try:
            return float(reader(stock_id))
        except Exception:
            return 1.0

    def _est_symbol(self, symbol: str) -> Any:
        """实际用来取中间层行情的符号。

        **不能只用 ``est_symbol``**：真实库里 ``SZ161130`` 的 ``est_symbol``
        是 **``^NDX``（指数）**，而 ``^NDX`` 在 ``netvaluehistory`` /
        ``stockhistory`` 里都没有行 —— 指数点位是从新浪直接抓的、不落这两张表。
        所以必须和构建器同顺序解析：

        1. ``est_symbol or pair_symbol``（如 ``^NDX``）；
        2. 若该符号有**杠杆替代**（``get_leverage_hedge_symbol()``，
           即 QQQ / SPY 这类真能拿到净值的 ETF），**用替代的那个**。

        对应 ``stockdata.py:279-284``：

        ```python
        hedge_symbol = profile.est_symbol or profile.pair_symbol
        leverage_symbol = get_leverage_hedge_symbol(symbol)
        if hedge_symbol and leverage_symbol:
            ...
            hedge_symbol = leverage_symbol
        ```

        只取 ``est_symbol`` 会让 ``_latest_date()`` 返回 ``None``，
        官方 EST 就静默算不出来（实测踩过，表现为"0 只写入"）。
        """
        reader = getattr(self.source, "get_fund_profile", None)
        if reader is None:
            return None
        try:
            profile = reader(symbol)
        except Exception:
            return None
        if profile is None:
            return None
        hedge_symbol = getattr(profile, "est_symbol", None) or getattr(profile, "pair_symbol", None)
        if not hedge_symbol:
            return None
        leverage_symbol = get_leverage_hedge_symbol(symbol)
        return leverage_symbol or hedge_symbol

    def _latest_date(self, stock_id: Any) -> Any:
        """中间层最新一条数据的日期（对应 ``$est_ref->GetDate()``）。

        优先**净值表**（``netvaluehistory``），没有再退回**行情表**
        （``stockhistory``）—— 因为 ``_getOfficialEstVal()`` 的取值顺序就是
        "先净值、再行情"，日期也应与之同源，否则会拿行情日期去查不存在的净值。
        """
        for reader_name in ("get_latest_net_value_date", "get_latest_history_date"):
            reader = getattr(self.source, reader_name, None)
            if reader is None:
                continue
            try:
                date = reader(stock_id)
            except Exception:
                date = None
            if date:
                return date
        return None

    def _net_value(self, stock_id: Any, date: Any) -> Any:
        """取某日净值（对应 ``cny_ref->GetClose($date)`` 与中间层净值）。"""
        if stock_id is None or date is None:
            return None
        reader = getattr(self.source, "get_net_value_close", None)
        if reader is not None:
            try:
                value = reader(stock_id, date)
            except Exception:
                value = None
            if value is not None:
                return value
        return self.source.get_history_close(stock_id, date)

    def _official_est_value(self, est_symbol: str, date: Any, data: dict = None) -> Any:
        """对应 ``_getOfficialEstVal()``：优先中间层**净值**，否则用**行情价**。

        ```php
        if ($str = $this->_getEstNetValue($est_ref, $strDate))	return $str;   // 净值
        if (method_exists($est_ref, 'GetHoldingsDate')) ...                    // 持仓型
        return $this->_getEstPrice($est_ref, $strDate);                        // 行情价
        ```
        """
        est_id = self.source.get_stock_id(est_symbol)
        if est_id is None:
            return None
        value = self._net_value(est_id, date)
        if value is not None:
            return value
        # _getEstPrice()：用**当日行情价**（含退到上一交易日的兜底）。
        # 注意不要在这里用归一化净值 —— 它与行情价差 ``cal_m`` 倍（实测 749 倍）。
        return self._middle_day_quote(est_symbol, date, data or {})

    def _qdii_value(self, calculator: PalmmicroFundCalculator, data: dict,
                    est_value: Any, cny: Any) -> Any:
        """对应 ``GetQdiiValue()``：``est * cny / 校准值``，再按仓位调整。

        用 ``fundcalc.calc_with_calibration`` 的静态路径（``source=None``），
        把这一天的中间层贡献值注入 ``data["hedge_value"]``。

        ## 贡献值怎么算（这条恒等式是本项目实测出来的）

        ```text
        hedge_value = nv_f × cal_f / cny          （构建器给的口径）
                    = est_m × cal_m               （两者都等于中间层的人民币价值）
        ```

        实测库里的值（见 ``_debug_middle_relation.py`` 的输出）：
        ``SZ161130`` 的 ``hedge_value = 21.746 = 4.5452×32.079543/6.705``；
        而 ``QQQ`` 的 ``est_m × cal_m = 749.58×0.008945 = 6.705``。
        两者不相等（差一个 ``p_f``/量纲因子），但**各自是自洽的**：
        基金那条直接由自己的净值与校准值算出，不需要中间层参与。

        所以这里**不自己拼中间层口径**（试过 `est_value × cal_m`，
        得到 0.229 这种离谱结果），而是复用构建器给的同一条式子，
        用**中间层报价相对变化**做缩放：

            est_f = (nv_f × cal_f / cny_ref) × (est_quote / est_quote_ref) × (cny / cny_ref)

        其中 ``est_quote_ref × cal_m = cny_ref``（就是 ``QQQ`` 那条恒等式），
        所以 ``est_quote_ref = cny_ref / cal_m``，代入即得本函数的实现。

        **口径要一致**：``est_value`` 传进来的必须是**中间层报价**
        （如 QQQ 的 749.58），不能是它归一化后的净值（1.0）——
        两者差 749 倍，混用会算出 0.23 这种结果（实测踩过）。
        """
        if not data.get("calibration"):
            return None

        hedge_symbol = str(data.get("symbol_hedge") or "")
        if not hedge_symbol:
            return None
        middle_row = data.get(hedge_symbol)
        if not isinstance(middle_row, dict):
            return None
        try:
            cal_m = float(middle_row.get("calibration") or 0)
        except (TypeError, ValueError):
            cal_m = 0.0
        if not cal_m:
            return None
        # 校准时的汇率（构建器已把 hedge_value 按它算好）
        try:
            cny_ref = float(data.get("CNY") or data.get("CNYest") or cny)
        except (TypeError, ValueError):
            cny_ref = float(cny)

        contribution = float(data["hedge_value"]) if data.get("hedge_value") not in (None, "") else None
        if contribution is None:
            # 没有构建器的贡献值：退回直接按 own 口径算（``nv_f × cal_f / cny``）
            try:
                contribution = float(data["netvalue"]) * float(data["calibration"]) / cny_ref
            except (TypeError, ValueError, ZeroDivisionError):
                return None

        # 中间层报价的基准值：est_quote_ref × cal_m = cny_ref
        est_quote_ref = cny_ref / cal_m
        if not est_quote_ref:
            return None
        # **用调用方传进来的 est_value**，而不是在这里自己去取实时快照。
        #
        # 这是三个 EST 的分界线：调用方决定用哪个口径的中间层值 ——
        #   * 官方 EST 传中间层**净值**（取不到时传当日行情价）；
        #   * 参考 EST 传**当日行情价**；
        #   * 实时 EST 传**实时快照价**。
        # 曾经这里写死 `self._middle_quote(...)`（实时快照优先），
        # 结果官方 EST 也被实时价影响、不再等于基金净值（实测 4.5452 变 4.5833），
        # 而且三个 EST 恒等。
        ratio = (float(est_value) / est_quote_ref) if est_quote_ref else 1.0
        # 再用汇率相对变化缩放
        fx_ratio = float(cny) / cny_ref if cny_ref else 1.0

        merged = dict(data)
        merged["hedge_value"] = contribution * ratio * fx_ratio
        merged["CNYest"] = float(cny)
        return PalmmicroFundCalculator.calc_with_calibration(merged)
    def _middle_quote(self, hedge_symbol: str, data: dict) -> Any:
        """中间层当前报价（与 ``est_quote_ref`` 同口径）。

        来源优先级：

        1. 数据源的 ``get_est_quote(stock_id)``（读 ``estquote`` 快照）；
        2. 数据源的 ``get_latest_history_close(stock_id)``（读 ``stockhistory``）；
        3. ``data`` 里构建器放的 ``est_quote``。

        **不要在这里优先读 `data` 里的字段**：测试替身把 ``est_quote``
        塞进 data 时会让官方 EST 也用上实时快照，从而不再等于基金净值
        （实测踩过，5 个用例同时失败）。行情快照属于"实时"那条路，
        官方 EST 要用 :meth:`_middle_day_quote`。

        都取不到返回 ``None``，调用方按比例 1.0 处理 ——
        即"没有中间层行情时，官方 EST 就等于用它自己净值与校准值算出的值"。
        """
        est_id = self.source.get_stock_id(hedge_symbol)
        if est_id is None:
            return None
        reader = getattr(self.source, "get_est_quote", None)
        if reader is not None:
            try:
                record = reader(est_id)
            except Exception:
                record = None
            if record is not None:
                value = getattr(record, "close", None)
                if value not in (None, ""):
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        pass
        reader = getattr(self.source, "get_latest_history_close", None)
        if reader is not None:
            try:
                value = reader(est_id)
            except Exception:
                value = None
            if value not in (None, ""):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
        row = data.get(hedge_symbol)
        if isinstance(row, dict):
            for key in ("est_quote", "price", "close"):
                if row.get(key) not in (None, ""):
                    try:
                        return float(row[key])
                    except (TypeError, ValueError):
                        pass
        return None

    def _middle_day_quote(self, est_symbol: str, date: Any, data: dict) -> Any:
        """中间层在 ``date`` 上的**当日行情价**，对应 ``$pair_ref->GetVal()``。

        官方 EST 的取值顺序（``_getOfficialEstVal``）：

        1. 中间层**净值**（见 :meth:`_official_est_value` 的前一步）；
        2. :meth:`_middle_quote` —— 当前报价（``estquote`` / ``realtimequote``
           快照优先，再退到 ``stockhistory`` 最新一条）；
        3. :meth:`_fair_quote` —— 指定日期的行情收盘价；
        4. ``get_prev_history_close`` —— 上一交易日（PHP ``_getEstPrice()`` 的
           "SH000869 bug fix" 兜底）。

        **第 2 步不能省**：演示库里中间层的 ``stockhistory`` 只到 09-30，
        而它的最新日期是 10-02，跳过第 2 步就会一路退到昨收 742.03，
        官方 EST 因此偏 1%（实测 4.5017 而非 4.5452）。
        """
        value = self._middle_quote(est_symbol, data)
        if value is not None:
            return value
        value = self._fair_quote(est_symbol, date)
        if value is not None:
            return value
        est_id = self.source.get_stock_id(est_symbol)
        if est_id is None:
            return None
        reader = getattr(self.source, "get_prev_history_close", None)
        if reader is None:
            return None
        try:
            previous = reader(est_id, date)
        except Exception:
            return None
        if previous in (None, ""):
            return None
        try:
            return float(previous)
        except (TypeError, ValueError):
            return None

    def _est_quote_time(self, symbol: str) -> Any:
        """中间层最新的行情时刻，用于 20:55 闸门。

        来源优先级：注入的 ``quote_time_provider`` → 数据源的 ``get_quote_time()``。
        都取不到返回 ``None``，调用方回落到当前时刻。
        """
        est_symbol = self._est_symbol(symbol)
        if not est_symbol:
            return None
        if self.quote_time_provider is not None:
            try:
                return self.quote_time_provider(est_symbol)
            except Exception:
                return None
        reader = getattr(self.source, "get_quote_time", None)
        if reader is None:
            return None
        try:
            return reader(est_symbol)
        except Exception:
            return None

    def _fund_symbols(self) -> list:
        """库里有估值配置的基金符号（用于批量扫描）。"""
        from fundcalc import qdii_est_symbol_map

        return sorted(qdii_est_symbol_map())


def build_estimate_calculator(source: Any) -> EstimateCalculator:
    """按数据源创建计算器（自带 ``fundest`` 存储）。"""
    connection = getattr(source, "connection", None)
    if connection is None:
        raise RuntimeError("数据源没有可用的数据库连接，无法读写 fundest")
    return EstimateCalculator(source, FundEstStore(connection))
