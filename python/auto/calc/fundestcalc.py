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

from calc.fundcalc import PalmmicroFundCalculator
from sql.fundeststore import FundEstStore, hour_minute_of
from calc.futurescalib import build_futures_calibrator


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
        ``est_symbol``      实际取中间层行情的符号（= 基金档案的 ``est_symbol``，
                            如 ``^NDX``，**不做杠杆替代**，见 :meth:`_est_symbol`）
        ``est_id``          上述符号的 stock_id
        ``mid_date``        中间层最新数据的日期（对应 ``$est_ref->GetDate()``）
        ``mid_quote``       中间层当前报价（对应 ``$pair_ref->GetVal()``）
        ``mid_net_value``   中间层在 ``mid_date`` 上的**净值**（对应 ``$est_ref->GetNetValue()``）
        ``cny_date``        汇率最新日期（对应 ``$cny_ref->GetDate()``）
        ``cny_mid``         ``mid_date`` 当天的中间价（可能为 ``None``）
        ``cny_current``     最新中间价（对应 ``$cny_ref->GetVal()`` 兜底）
        ``est_quote_ref``   中间层报价的**校准基准值**（``est_cal``，三个 EST 共用的锚点）
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

        mid_date = self._latest_date(est_id, est_symbol)
        if mid_date is None:
            return None

        #: 汇率**跟随 est 标的的地区**：美股 USCNY、港股 HKCNY、日股 JPCNY、欧股 EUCNY。
        #: PHP 是"按地区选 Reference 类"（``QdiiHkReference`` 用 ``fx_shkdcny``），
        #: 我们只有一条通用路径，所以从 est 标的反查。写死 USCNY 会让港股基金
        #: 的"基准汇率"显示 6.71 而不是 0.856，EST 直接差一个数量级。
        from calc.fundcalc import qdii_est_cny_symbol

        cny_symbol = qdii_est_cny_symbol(est_symbol)
        cny_id = self.source.get_stock_id(cny_symbol)
        cny_date = self.source.get_latest_net_value_date(cny_id) if cny_id else None
        cny_mid = self._net_value(cny_id, mid_date) if cny_id else None
        cny_current = self._latest_net_value(cny_id) if cny_id else None
        if cny_current is None:
            # 汇率完全取不到就没法算任何一个 EST
            return None

        cal_date = self._calibration_date_of(stock_id)
        est_ref_value, est_ref_date = self._est_quote_ref_with_date(
            est_symbol, cal_date, mid_date)

        return {
            "stock_id": stock_id,
            "fund_symbol": symbol,
            "data": data,
            "calculator": PalmmicroFundCalculator(data),
            "est_symbol": est_symbol,
            "est_id": est_id,
            "mid_date": mid_date,
            "is_etf": self._is_etf_a(symbol),
            # 实时快照价（实时 EST 用）
            "realtime_quote": self._middle_quote(est_symbol, data),
            # 当日行情价（参考 EST 用，对应 $pair_ref->GetVal()）
            "day_quote": self._fair_quote(est_symbol, mid_date),
            # 中间层净值（官方 EST 优先用，对应 $est_ref->GetNetValue()）
            "mid_net_value": self._net_value(est_id, mid_date),
            # **中间层报价的基准值**（三个 EST 共用的校准锚点，见 :meth:`_est_quote_ref`）。
            # 它必须与传进 ``_qdii_value()`` 的 ``est_value`` **同尺度** ——
            # 曾经用恒等式 ``cny_ref / cal_m`` 反推，那条恒等式只对旧尺度的中间层
            # 校准值成立；2026-10-13 按生产端口径重算后（QQQ 41.58）基准价被算成
            # 0.16，比值放大 4648 倍，/qdii 与 /fund 页官方 EST 因此显示成 9625。
            "est_quote_ref": est_ref_value,
            #: 基准价**实际**取到的那天（可能是兜底后的日期，见
            #: :meth:`_est_quote_ref_with_date`）。页面算法说明要显示它。
            "est_quote_ref_date": est_ref_date,
            "cny_symbol": cny_symbol,
            "cny_date": cny_date,
            "cny_mid": cny_mid,
            "cny_current": cny_current,
            # 参考/实时 EST 该用的汇率（对应 ``GetForexRef()``：ETF 即期、LOF 中间价）
            "cny_fair": self._fair_cny(symbol, cny_current, cny_symbol),
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
        value = self._qdii_value(ctx["calculator"], ctx["data"], est_value, rate,
                                est_quote_ref=ctx["est_quote_ref"])
        if not value:
            return None
        return EstRecord(ctx["mid_date"], value, None)

    def compute_fair(self, symbol: str) -> Any:
        """算该基金的**参考 EST**（``FairEst``），不写库。

        **PHP 里这一档有两份实现，闸门不一样**，本方法按"是不是场内 ETF"分派：

        1. ``_QdiiReference::EstRealtimeNetValue()``（``php/stock/qdiiref.php:161``）
           —— 基金详情页 / 分类页走的就是这一份：

           ```php
           if ($this->IsEtfA() || ($cny_ref->GetDate() != $this->strOfficialDate)
                              || ($strDate != $this->strOfficialDate))
               $this->fFairNetValue = $this->GetQdiiValue($this->_getFairEstVal($strDate));
           ```

           **ETF 无条件算**。原因是 ETF 的参考 EST 与官方 EST 的汇率口径本来就不同
           （``GetQdiiValue($strEst)`` 不带汇率 → 用 ``GetForexRef()``，ETF 是即期），
           即使三个日期全一致，两个值也不相等、仍然有信息量。

        2. ``FundPairReference::GetFairNetValue()``（``php/stock/fundpairref.php:256``）
           —— 配对交易那条路（本方法不实现它，那套语义在
           ``refobj.FundPairReference.get_fair_net_value()`` 里）：

           ```php
           if ($this->cny_ref && ($strOfficialDate != $this->cny_ref->GetDate()))
               return $this->EstFromPair($this->pair_ref->GetVal(), $this->cny_ref->GetVal());
           if ($this->pair_ref->IsSinaFutureExceptGoldCN() || ($strOfficialDate != $this->pair_ref->GetDate()))
               return $this->EstFromPair($this->pair_ref->GetVal());
           return false;      // 两者都一致 -> 不显示（与官方 EST 相同，没有信息量）
           ```

        ## 2026-11-03 修正：只把"日期一致就隐藏"用在 LOF 上

        本方法原来**只有**第 2 份的闸门（``cny_date == mid_date`` → 返回 ``None``），
        于是港股 / 日股 ETF 的参考 EST 整列消失：恒生、日经的**指数行情日与
        本地汇率中间价日天然是同一天**（都在北京时间当天收盘后更新），
        条件天天成立；美元基金的中间层日期比北京晚一天，反而天天不成立，
        所以只有港日两族出问题（欧股因时差侥幸躲过）。

        生产端实测（2026-11-03）``sh513600cn.php``：官方 EST 2.553、参考 EST 2.527；
        ``sh513000cn.php``：官方 2.113、参考 2.10 —— 差额全部来自
        "即期汇率 vs 中间价"（HKDCNY 0.8473 vs HKCNY 0.8560），
        中间层价两边是同一个数。所以 ETF 这一档绝不能按日期一致隐藏。

        用途（作者原话）：验证算法准确度看**官方 EST**；
        按溢价折价决定套利与配对交易的看**参考 EST**
        （A 股或美股休市的日子尤其要看它）。
        """
        ctx = self._resolve(symbol)
        if ctx is None:
            return None

        # LOF 才按"汇率日期 == 中间层日期"隐藏：那时参考 EST 与官方 EST 同为
        # 中间价口径、中间层价也相同，两个值一模一样。ETF 见 docstring 第 1 条。
        if (not ctx["is_etf"]
                and ctx["cny_date"] is not None
                and str(ctx["cny_date"]) == str(ctx["mid_date"])):
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
        value = self._qdii_value(ctx["calculator"], ctx["data"], quote, ctx["cny_fair"],
                                est_quote_ref=ctx["est_quote_ref"])
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
        value = self._qdii_value(ctx["calculator"], ctx["data"], quote, ctx["cny_fair"],
                                est_quote_ref=ctx["est_quote_ref"])
        if not value:
            return None
        return EstRecord(ctx["mid_date"], value, None)

    # -- 公式说明（供页面核对，不参与计算） -------------------------------
    def explain(self, symbol: str) -> Any:
        """把三档估值的**公式与代入值**摊开，给页面上「算法说明」那一段用。

        纯读、不写库；任何一项取不到就返回 ``None``（页面整段不显示）。

        这里**不复用** ``compute_*`` 的返回值 —— 那些只给结果，而说明要的是
        "每个中间量分别是什么、从哪张表哪一天来的"，所以重新走一遍同样的
        取数路径并记录口径标签。公式本身见 :meth:`_qdii_value` 的推导。
        """
        ctx = self._resolve(symbol)
        if ctx is None:
            return None

        data = ctx["data"]
        est_symbol = ctx["est_symbol"]
        mid_date = ctx["mid_date"]
        cny_cal = data.get("CNY")

        official_est = self._official_est_value(est_symbol, mid_date, data)
        official_label = self._official_est_label(est_symbol, mid_date, data)
        official_cny = ctx["cny_mid"] if ctx["cny_mid"] is not None else ctx["cny_current"]
        official_cny_label = ("%s 中间价 %s" % (ctx["cny_symbol"], mid_date) if ctx["cny_mid"] is not None
                              else "%s 中间价 %s（最新，%s 无报价）" % (
                                  ctx["cny_symbol"], ctx["cny_date"], mid_date))

        fair_est = ctx["day_quote"]
        fair_label = "%s %s 当日收盘价" % (est_symbol, mid_date)
        if fair_est is None:
            fair_est = self._middle_day_quote(est_symbol, mid_date, data)
            fair_label = "%s 当前报价（快照 / 最新收盘）" % est_symbol

        realtime_est, realtime_label = self._realtime_quote_with_label(ctx)

        return {
            "symbol": symbol,
            "mid_date": mid_date,
            "position": data.get("position"),
            "netvalue": data.get("netvalue"),
            "calibration": data.get("calibration"),
            "calibration_date": self._calibration_date_of(ctx["stock_id"]),
            "est_symbol": est_symbol,
            "est_cal": ctx["est_quote_ref"],
            "est_cal_date": ctx.get("est_quote_ref_date") or self._calibration_date_of(ctx["stock_id"]),
            "cny_cal": cny_cal,
            "cny_symbol": ctx["cny_symbol"],
            "cny_cal_date": data.get("CNY_date") or self._calibration_date_of(ctx["stock_id"]),
            "price": self._market_price(symbol, ctx["stock_id"]),
            "tiers": [
                {
                    "key": "official",
                    "name": "官方EST",
                    "est": official_est,
                    "est_label": official_label,
                    "cny": official_cny,
                    "cny_label": official_cny_label,
                    "value": self._value_or_none(official_est, ctx, official_cny),
                },
                {
                    "key": "fair",
                    "name": "参考EST",
                    "est": fair_est,
                    "est_label": fair_label,
                    "cny": ctx["cny_fair"],
                    "cny_label": self._fair_cny_label(symbol, ctx),
                    "value": self._value_or_none(fair_est, ctx, ctx["cny_fair"]),
                },
                {
                    "key": "realtime",
                    "name": "实时EST",
                    "est": realtime_est,
                    "est_label": realtime_label or "%s 实时快照" % est_symbol,
                    "cny": ctx["cny_fair"],
                    "cny_label": self._fair_cny_label(symbol, ctx),
                    "value": self._value_or_none(realtime_est, ctx, ctx["cny_fair"]),
                },
            ],
        }

    def _value_or_none(self, est_value: Any, ctx: dict, cny: Any) -> Any:
        """``_qdii_value()`` 的包装：任一入参缺失就返回 ``None``。"""
        if est_value is None or cny is None:
            return None
        try:
            value = self._qdii_value(ctx["calculator"], ctx["data"], est_value, cny,
                                     est_quote_ref=ctx["est_quote_ref"])
        except Exception:
            return None
        return value if value else None

    def _official_est_label(self, est_symbol: str, date: Any, data: dict) -> str:
        """官方 EST 用的中间层值到底是什么口径（净值 / 持仓 / 行情价）。

        顺序与 :meth:`_official_est_value` 一致，只是把"取到了哪一种"说出来 ——
        用户核对数值时最想知道的就是这个。
        """
        est_id = self.source.get_stock_id(est_symbol)
        if est_id is not None and self._net_value(est_id, date) is not None:
            return "%s %s 净值" % (est_symbol, date)
        if self._holdings_est_value(est_symbol, date) is not None:
            return "%s %s 持仓加权估值" % (est_symbol, date)
        return "%s %s 行情价" % (est_symbol, date)

    def _fair_cny_label(self, symbol: str, ctx: dict) -> str:
        """参考/实时 EST 用的汇率口径（ETF 即期、LOF 中间价）。"""
        cny_symbol = ctx.get("cny_symbol") or "USCNY"
        if not ctx.get("is_etf"):
            return "%s 中间价 %s" % (cny_symbol, ctx["cny_date"])
        spot_symbol = self._spot_symbol_of(cny_symbol)
        if self._spot_cny(cny_symbol) not in (None, ""):
            return "%s 即期（ETF 按盘中汇率申赎）" % spot_symbol
        return "%s 中间价 %s（即期缺失，退回中间价）" % (cny_symbol, ctx["cny_date"])

    def _realtime_quote_with_label(self, ctx: dict) -> tuple:
        """实时 EST 用的中间层值 + 它的口径标签。

        与 :meth:`compute_realtime` 同一套优先级（期货 → 快照），
        只是额外把"用的哪一个"说出来。
        """
        value = self._futures_realtime_quote(ctx)
        if value is not None:
            future_symbol = self._future_symbol_of(ctx)
            index_symbol = self._index_symbol(ctx)
            return value, "%s 期货实时价 → %s 同口径" % (future_symbol or "期货", index_symbol or "标的")
        if ctx.get("realtime_quote") is not None:
            return ctx["realtime_quote"], "%s 实时快照价" % ctx["est_symbol"]
        return None, None

    def _future_symbol_of(self, ctx: dict) -> Any:
        """该基金配对的期货符号（只为说明文字服务）。"""
        calibrator = getattr(self, "futures_calibrator", None)
        if calibrator is None:
            return None
        index_symbol = self._index_symbol(ctx)
        if not index_symbol:
            return None
        try:
            return calibrator.future_symbol_of(index_symbol)
        except Exception:
            return None

    def _market_price(self, symbol: str, stock_id: Any) -> Any:
        """基金当前的场内价（算溢价用）。快照优先，退回最新收盘。"""
        reader = getattr(self.source, "get_stored_quote", None)
        if reader is not None:
            try:
                quote = reader(symbol)
            except Exception:
                quote = None
            price = getattr(quote, "price", None) if quote is not None else None
            if price not in (None, ""):
                try:
                    return float(price)
                except (TypeError, ValueError):
                    pass
        reader = getattr(self.source, "get_latest_history_close", None)
        if reader is None or stock_id is None:
            return None
        try:
            value = reader(stock_id)
        except Exception:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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

        # 期货挂在**指数**（^NDX ← hf_NQ / ^GSPC ← hf_ES）上，
        # 键就是基金档案里的 est_symbol（_est_symbol 已不再做杠杆替代，
        # ctx["est_symbol"] 本身就是指数；这里走 _index_symbol 取，
        # 语义上与"估值符号"分开，两处当前等价）。
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

        取法与 :meth:`_est_symbol` 相同（profile 的 ``est_symbol`` /
        ``pair_symbol``）。历史上 ``_est_symbol`` 曾做 QQQ/SPY 杠杆替代、
        与此处不等价，所以单独立了这个口子；替代去掉后两者一致，
        保留它是为了"期货挂在指数上"这一语义标注。
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
        from sql.historytable import NetValueHistorySql

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

        另外还会放 ``CNY`` = **校准发生那天**的汇率（``cny_cal``）——
        ``_qdii_value()`` 用它算汇率相对变化 ``cny / cny_cal``。缺了它就会
        回落到"传进来的当前汇率"，汇率那一项被约掉，参考 EST 反而与官方 EST
        同向变化（实测把 4.4776 算成 4.5452）。

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
        # **校准时的汇率**（``cny_cal``）：``_qdii_value()`` 用它算汇率相对变化
        # ``cny / cny_cal``（贡献值那一项除的是**当前**汇率，见该方法 docstring）。
        # 缺了它就会回落到"传进来的当前汇率"，汇率那一项被约掉，参考 EST 反而与
        # 官方 EST 同向变化（实测把 4.4776 算成 4.5452）。
        calibration_cny, cny_cal_date = self._calibration_cny_with_date(stock_id, symbol)
        if calibration_cny is not None:
            config["CNY"] = float(calibration_cny)
            #: 基准汇率**实际**取到的那天（三级兜底后可能与校准日不同），
            #: 页面算法说明要显示它，否则用户按校准日去查汇率会对不上。
            config["CNY_date"] = str(cny_cal_date) if cny_cal_date else ""
        if est_symbol:
            est_id = self.source.get_stock_id(est_symbol)
            middle_calibration = self._calibration_of(est_id) if est_id else None
            # **中间层那一行无论如何都要建**，不能因为没有校准值就不建。
            #
            # 曾经这里写成 ``if middle_calibration:`` —— 于是所有 est 标的是
            # 无配对 ETF（AGG / IBB / XBI / VNQ ...）的基金，``data`` 里根本
            # 没有中间层行，``_qdii_value()`` 再校验一轮后返回 ``None``，
            # 官方 EST 一只都算不出来。**生产端证明这是错的**：
            # ``https://palmmicro.com/woody/res/qdiicn.php`` 上
            # SH501300（AGG）照常显示官方 EST 0.914、SH513290（IBB）1.646、
            # SZ159502（XBI）1.453 —— 一条都不缺。
            #
            # 中间层有没有校准值，决定不了"能不能估值"：
            # ``fundcalc.calc_with_calibration()`` 用的是 ``hedge_value``
            # （由 :meth:`_qdii_value` 算好后塞进 data），``cal_m`` 在整条
            # 式子里**不出现**。它只在历史上充当过"防止算出离谱结果"的闸门，
            # 而 ``est_quote_ref`` 早已不再用恒等式反推（见 :meth:`_est_quote_ref`），
            # 那道闸门保护的东西已经不在了，留下来的只是"静默算不出来"。
            config[est_symbol] = {
                "calibration": float(middle_calibration) if middle_calibration else 0.0,
                "position": self._position_of(est_id) if est_id else 1.0,
            }
        return config

    def _calibration_date_of(self, stock_id: Any) -> Any:
        """基金**最近一条校准记录的日期**（``calibrationhistory`` 那天）。

        取不到返回 ``None``（连 ``lastcalibration`` 兜底都没有日期信息）。
        用途：中间层报价的基准日 —— 基金的 ``cal_f`` 是用那天的标的价算的，
        所以"标的涨了多少"必须拿同一天的报价做分母（见 :meth:`_qdii_value`）。
        """
        if stock_id is None:
            return None
        reader = getattr(self.source, "get_calibration_now", None)
        if reader is None:
            return None
        try:
            record = reader(stock_id)
        except Exception:
            record = None
        if record is None:
            return None
        return getattr(record, "date", None)

    def _calibration_cny_with_date(self, stock_id: Any, symbol: str) -> tuple:
        """:meth:`_calibration_cny` 的带日期版本：``(值, 实际取到的日期)``。

        三级兜底后真正取到的可能不是校准日 —— 页面算法说明要显示实际那天。

        **汇率符号跟着 est 标的的地区走**（港股 ``HKCNY``）：``cal_f`` 是用
        那个地区的中间价算出来的，这里取错地区，页面上"基准汇率 cny_cal"
        就会显示 6.71 而校准值是按 0.856 算的，EST 直接差一个数量级。
        """
        from calc.fundcalc import qdii_est_cny_symbol

        cny_symbol = qdii_est_cny_symbol(self._est_symbol(symbol))
        cny_id = self.source.get_stock_id(cny_symbol)
        if cny_id is None:
            return None, None

        calibration_date = self._calibration_date_of(stock_id)
        if calibration_date is None:
            return None, None

        value = self._net_value(cny_id, calibration_date)
        if value is not None:
            return value, calibration_date
        # 该日期没有汇率（周末/假期）-> 退到之前最近一条。
        # **取不到也要继续往下走**：这里曾经直接 `return fallback(...)`，
        # 而汇率只写 netvaluehistory、stockhistory 里没有行，于是兜底永远
        # 返回 None，最后那条"最新汇率"根本走不到 —— 校准时汇率因此恒为
        # None，``cny/cny_cal`` 被约成 1，汇率相对变化整项消失。
        fallback = getattr(self.source, "get_prev_history_close", None)
        if fallback is not None:
            try:
                value = fallback(cny_id, calibration_date)
            except Exception:
                value = None
            if value not in (None, ""):
                return value, "%s 前" % calibration_date
        # 最后退到"当前汇率"（与旧行为一致，作为兜底）
        return self._latest_net_value(cny_id), self.source.get_latest_net_value_date(cny_id)

    def _calibration_cny(self, stock_id: Any, symbol: str) -> Any:
        """**校准发生那一刻**用的汇率（``cny_ref``）。

        依据：``data["CNY"]`` 在 PHP 里是校准记录写下来时的汇率 ——
        ``QdiiGetCalibration(est, cny, netvalue)`` 用的就是它，
        所以它必须与 ``calibrationhistory`` 那条记录**同时期**。

        取数顺序：

        1. 校准日期（``get_calibration_now()`` 的 date）当天的 ``USCNY``；
        2. 该日期之前最近一条（假期/时差兜底；汇率只写 ``netvaluehistory``、
           ``stockhistory`` 里没有行，所以这一级对汇率通常落空）；
        3. 最新一条 ``USCNY``（与旧行为一致的兜底）。

        三级都取不到才返回 ``None``，由调用方回落到"当前汇率"。

        取数逻辑在 :meth:`_calibration_cny_with_date` 里（那份还返回实际取到的日期）。
        """
        return self._calibration_cny_with_date(stock_id, symbol)[0]

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
        """实际用来取中间层行情的符号 = 基金档案里的 ``est_symbol``（**不做杠杆替代**）。

        ## 曾经做 QQQ/SPY 杠杆替代，是相对 PHP 的偏离（已修）

        本方法旧实现拿 ``get_leverage_hedge_symbol(symbol)`` 把 ``^NDX`` 换成
        ``QQQ``、``^GSPC`` 换成 ``SPY`` 再取数。**PHP 不是这样**：

        * ``php/stock/qdiiref.php:243`` ``QdiiGetEstSymbol('SZ161130')`` 返回
          **``^NDX``**（``QdiiGetQqqSymbolArray()`` 那一支，行尾注释
          ``// 'QQQ'`` 只说明"对冲标的是 QQQ"），``SetEstRef(new
          FundPairReference('^NDX'))``；
        * 官方 EST 全链（``_getOfficialEstVal`` / ``EstNetValue`` /
          ``AdjustFactor``）都走这个 ``^NDX`` 的 ``GetEstRef()``；
        * QQQ 只出现在 ``stockdataarray.php`` 的 ``symbol_hedge``（对冲展示）
          与 ``GetStockHedge()``，**不进估值式子**。

        实测铁证（2026-10-20，SZ161130）：本地 ``fundest[10-19] = 4.41972``
        只有 QQQ 模型（718.36 / QQQ 净值[10-16] 709.8）能精确复现；
        ^NDX 模型给 4.41813 → 显示 **4.418 = 生产端估值表官方 EST**
        （生产端校准记录 cal=44892.619 是 ``^NDX × 汇率 / 净值``，
        与 QQQ 尺度不匹配，用 QQQ 做比值分子分母必然偏离）。

        杠杆替代唯一该出现的地方是 ``stockdata.StockDataArrayBuilder``
        （``symbol_hedge``，对应 ``GetLeverageHedgeSymbol()``）与
        ``GetStockHedge()``；本类的 ``est_symbol`` 语义是"估值标的"，
        与 ``autocalibration.calibrate_fund``（取 ``qdii_est_symbol`` 原始值）、
        ``history_service._est_symbol``（溢价表 ^NDX 价格列）保持一致。

        ## est_symbol 与 pair_symbol 的取值顺序

        1. ``est_symbol``（如 ``^NDX`` / ``XOP`` / ``KWEB``）；
        2. ``pair_symbol``（配对型基金的配对标的，无 est_symbol 时才用）。

        取不到返回 ``None``，调用方跳过该基金。
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
        return getattr(profile, "est_symbol", None) or getattr(profile, "pair_symbol", None)

    def _is_etf_a(self, symbol: str) -> bool:
        """该标的是不是场内 ETF（``is_etf_a()``，``stocksymbol.py:803``）。

        只用来决定"参考/实时 EST 用即期汇率还是中间价"（见 :meth:`_fair_cny`）。
        取不到符号对象时按 ``False``（LOF 口径）处理 —— 宁可少算一点汇率差，
        也不要因为判不出类别就让整张表空掉。
        """
        try:
            from core.stocksymbol import StockSymbol
        except ImportError:
            return False
        try:
            return bool(StockSymbol(str(symbol)).is_etf_a())
        except Exception:
            return False

    def _fair_cny(self, symbol: str, cny_current: Any, cny_symbol: str = "USCNY") -> Any:
        """参考/实时 EST 该用的汇率（对应 ``GetForexRef()``）。

        ## ETF 用即期、LOF 用中间价

        ``php/stock/fundref.php`` 的 ``GetForexRef()``::

            return $this->IsEtfA() ? $this->forex_ref : $this->cny_ref;

        原因（PHP 原注释）：**ETF 可以按盘中汇率实时申赎，LOF 只能按当天中间价**。
        所以两者的"当下汇率"不是同一个数：

        * ``USCNY``（中间价，货币网每天 09:15 发布）≈ ``6.72``；
        * ``fx_susdcny``（即期，新浪实时）≈ ``6.679``。

        差 0.6% —— 正好是之前参考 EST 对不上生产端的那 0.6%。实测
        ``SH513100`` 我们 1.9778 / 生产端 1.965（ETF，该用即期）；
        ``SZ161125`` 我们 3.1323 / 生产端 3.132（LOF，本来就用中间价）。

        ## 官方 EST 不用它

        官方 EST 走 ``strOfficialCNY = $cny_ref->GetClose($strDate)``，
        **恒用中间价**，与 ETF/LOF 无关。别把这条规则套到官方那一档上。

        即期取不到（快照缺失）时退回 ``cny_current`` —— 与 PHP
        ``$cny_ref->GetPrice()`` 兜底同向。

        ``cny_symbol`` 决定"哪个地区的即期"：港股 ETF 要 ``fx_shkdcny``，
        配 ``fx_susdcny`` 会让参考 EST 差一个汇率。
        """
        if not self._is_etf_a(symbol):
            return cny_current

        spot = self._spot_cny(cny_symbol)
        return spot if spot not in (None, "") else cny_current

    def _spot_symbol_of(self, cny_symbol: str) -> Any:
        """中间价符号 -> 对应的即期外汇符号（``USCNY`` -> ``fx_susdcny``）。

        符号取自 ``refobj.QDII_REGIONS`` —— 它把四个地区的中期价与即期外汇
        配在一起，是"哪个即期配哪个中间价"的唯一出处。
        """
        try:
            from ref.refobj import QDII_REGIONS
        except ImportError:
            return None

        for mid_symbol, spot_symbol in QDII_REGIONS.values():
            if mid_symbol == cny_symbol:
                return spot_symbol
        return None

    def _spot_cny(self, cny_symbol: str = "USCNY") -> Any:
        """即期外汇（``fx_susdcny`` / ``fx_shkdcny`` …）的当前值。

        第 1 优先行情快照（daemon 写的，角色同 PHP 的 sinajs 缓存），
        没有快照再退回净值表 —— 即期外汇正常情况下只有快照。
        """
        spot_symbol = self._spot_symbol_of(cny_symbol)
        if not spot_symbol:
            return None

        reader = getattr(self.source, "get_stored_quote", None)
        if reader is not None:
            try:
                quote = reader(spot_symbol)
            except Exception:
                quote = None
            price = getattr(quote, "price", None) if quote is not None else None
            if price not in (None, ""):
                try:
                    return float(price)
                except (TypeError, ValueError):
                    pass

        spot_id = self.source.get_stock_id(spot_symbol)
        if spot_id is None:
            return None
        return self._latest_net_value(spot_id)

    def _latest_date(self, stock_id: Any, symbol: Any = None) -> Any:
        """中间层最新一条数据的日期（对应 ``$est_ref->GetDate()``）。

        ## 必须**行情优先**，不能净值优先

        PHP 的 ``MyStockReference::GetDate()`` 返回的是引用对象**最后一次取到的
        行情日期** —— 也就是新浪缓存里的那一天，与"净值表有没有那一天"无关。

        所以取数顺序是：

        1. ``realtimequote`` 快照日期 —— daemon 写的跨进程缓存，与 PHP 的
           ``debug/sina/*.txt`` 同一个角色；
        2. ``stockhistory`` 最新日期；
        3. ``netvaluehistory`` 最新日期。

        ## 曾经先取净值表，导致 EST 日期卡在旧的那天

        实测 ``SZ161125``（2026-10-20）：中间层 ``SPY`` 的行情快照已经是
        ``2026-10-19``，但它的**净值**只到 ``2026-10-16``。先取净值表就把
        ``mid_date`` 定成 ``10-16``，于是：

        * 页面「EST日期」显示 ``10-16``，而生产端是 ``10-19``
          （PHP 取的就是 ``$est_ref->GetDate()`` = 行情日期）；
        * 更糟的是 ``est_value``（取自 ``mid_date`` 的净值）与
          ``est_quote_ref``（校准日的净值，恰好也是 10-16）**撞成同一个值**，
          比值退化成 1.0，官方 EST 于是**恒等于基金净值**（3.1206），
          而不是生产端的 3.132。

        ``stock_id`` 与 ``symbol`` 都给：前者查两张历史表，后者查行情快照。
        ``symbol`` 省略时跳过第 1 步（老调用方仍然可用）。
        """
        if symbol:
            reader = getattr(self.source, "get_stored_quote_date", None)
            if reader is not None:
                try:
                    date = reader(symbol)
                except Exception:
                    date = None
                if date:
                    return date

        for reader_name in ("get_latest_history_date", "get_latest_net_value_date"):
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
        """取某日净值（对应 ``cny_ref->GetClose($date)`` 与中间层净值）。

        **净值表优先、行情表兜底** —— 与 PHP ``_getEstNetValue()`` /
        ``_getEstPrice()`` 的顺序一致。

        ⚠️ 接口方法名是 ``get_netvalue_close``（``netvalue`` 连写，见
        ``core.stockdata.FundDataSource``）。本方法曾经查
        ``get_net_value_close``（多个下划线），真实数据源上永远取不到，
        于是**净值表从来没被读过**：中间层净值恒为 ``None``、
        校准时汇率也恒为 ``None``（``cny/cny_cal`` 被约成 1）。
        测试替身照着错名定义，所以一直没暴露 —— 现在替身与真实接口对齐，
        并由 ``test_fundest.InterfaceNameTest`` 盯着这类漂移。
        """
        if stock_id is None or date is None:
            return None
        reader = getattr(self.source, "get_netvalue_close", None)
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
        # 持仓型中间层（qdiiref.php:114 的第二行）：净值取不到时用**持仓加权估值**
        holdings = self._holdings_est_value(est_symbol, date)
        if holdings is not None:
            return holdings
        # _getEstPrice()：用**当日行情价**（含退到上一交易日的兜底）。
        # 注意不要在这里用归一化净值 —— 它与行情价差 ``cal_m`` 倍（实测 749 倍）。
        return self._middle_day_quote(est_symbol, date, data or {})

    def _holdings_est_value(self, est_symbol: str, date: Any) -> Any:
        """持仓型中间层：``$est_ref->_estNetValue($strDate)``（``qdiiref.php:114``）。

        PHP 的判据是 ``method_exists($est_ref, 'GetHoldingsDate')`` —— 问的是
        "引用对象是不是持仓型"，而不是"库里有没有数据"。本版用
        :func:`ref.refobj.count_holdings` 判断（与 :func:`ref.refobj.build_est_reference`
        同一判据），**不是持仓型就返回 ``None``**，让调用方继续走行情价分支。

        **刻意不缓存引用对象**：持仓清单会被后台改，缓存住了页面就会一直用旧比例，
        而这种错不报错（读数看着也合理）。构造一次要读一张几十行的表，
        相对"估值算错"这笔账是划算的。
        """
        try:
            from ref.refobj import count_holdings
        except ImportError:
            return None
        if count_holdings(self.source, est_symbol) <= 0:
            return None
        try:
            from ref.holdingsref import HoldingsReference
        except ImportError:
            return None
        try:
            ref = HoldingsReference(est_symbol, source=self.source)
            value = ref._est_net_value(date)
        except Exception:
            return None
        if value in (None, ""):
            return None
        return value

    def _est_quote_ref_with_date(self, est_symbol: str, cal_date: Any, mid_date: Any) -> tuple:
        """:meth:`_est_quote_ref` 的带日期版本：``(值, 实际取到的日期)``。

        ``cal_date`` 只是"想取哪天"，四级兜底后真正取到的可能是另一天 ——
        页面上的算法说明要显示**实际那天**（用户按标签去核对数值），
        所以把这个信息一起带出来。
        """
        est_id = self.source.get_stock_id(est_symbol)
        if est_id is None:
            return None, None
        if cal_date is not None:
            value = self._net_value(est_id, cal_date)
            if value not in (None, ""):
                return value, cal_date
            reader = getattr(self.source, "get_prev_history_close", None)
            if reader is not None:
                try:
                    value = reader(est_id, cal_date)
                except Exception:
                    value = None
                if value not in (None, ""):
                    # 退到上一交易日 —— 这一级拿不到具体日期；但若"最新行情日"
                    # 不晚于校准日，那它就是这个兜底取到的那天（演示库常见：
                    # 校准日 10-20 而行情只到 10-19），标出确切日期好核对。
                    latest = getattr(self.source, "get_latest_history_date", None)
                    if latest is not None:
                        try:
                            day = latest(est_id)
                        except Exception:
                            day = None
                        if day and str(day) <= str(cal_date):
                            return value, day
                    return value, "%s 前" % cal_date
        if mid_date is not None:
            value = self._net_value(est_id, mid_date)
            if value not in (None, ""):
                return value, mid_date
        return None, None

    def _est_quote_ref(self, est_symbol: str, cal_date: Any, mid_date: Any) -> Any:
        """中间层报价的**校准基准值**（``est_cal``）—— 基金 ``cal_f`` 建起来那天的中间层值。

        三个 EST 共用它当分母（``ratio = est_value / est_quote_ref``），
        所以它必须与 ``est_value`` **同尺度**：官方 EST 传中间层净值时就取净值，
        传行情价时就取行情价。这里按 :meth:`_official_est_value` 的顺序
        （净值优先、行情价兜底）取 —— 两者对这些 ETF 只差千分位
        （净值 vs 收盘价，见生产端 ``netvalueclosecn.php``）。

        取数顺序：

        1. **校准日当天**的中间层值（净值优先，再行情价）；
        2. 校准日之前最近一条行情价（PHP ``_getEstPrice()`` 的
           "SH000869 bug fix" 兜底）；
        3. **中间层最新日期**（``mid_date``）的值 —— 演示库里中间层行情常常
           只到 ``mid_date`` 附近、校准日更早，这时退而求其次；
        4. 都没有 -> ``None``，调用方按比例 1.0 处理（"标的没动"）。

        **为什么不能用恒等式 ``est_quote_ref = cny_ref / cal_m`` 反推**（本类曾经的
        实现）：那条恒等式只对**旧尺度**的中间层校准值成立 —— 演示库早期把 QQQ
        的校准值播种成 ``0.008945``（= 汇率/报价），恰能满足
        ``est_m × cal_m = cny_ref``。2026-10-13 按生产端口径重算后，QQQ 的校准值
        变成 ``41.58``（= 指数/净值，与生产端 QQQ 41.111 同口径），恒等式失效：
        基准价被算成 ``6.69/41.58 = 0.16``，比值放大 **4648 倍**，
        ``/qdii`` 与 ``/fund`` 页的官方 EST 因此显示成 9625（实测，应约 2.05）。

        取数逻辑在 :meth:`_est_quote_ref_with_date` 里（那份还返回实际取到的日期）。
        """
        return self._est_quote_ref_with_date(est_symbol, cal_date, mid_date)[0]

    def _qdii_value(self, calculator: PalmmicroFundCalculator, data: dict,
                    est_value: Any, cny: Any, est_quote_ref: Any = None) -> Any:
        """对应 ``GetQdiiValue()``：``est * cny / 校准值``，再按仓位调整。

        用 ``fundcalc.calc_with_calibration`` 的静态路径（``source=None``），
        把这一天的中间层贡献值注入 ``data["hedge_value"]``。

        ## 公式（与 PHP 逐字对应）

        PHP ``qdiiref.php``：

        ```php
        GetQdiiValue($strEst, $strCNY):
            $fVal = QdiiGetVal($strEst, $strCNY, $this->fFactor);   // est * cny / cal_f
            return $this->AdjustPosition($fVal);                    // p*fVal + (1-p)*nv_cal
        ```

        而 ``cal_f``（``fFactor``）是 ``AdjustFactor()`` 用**校准那天**的中间层值
        与汇率算的（``QdiiGetCalibration($strEst, $strCNY, $this->GetPrice())``），
        所以把 ``cal_f`` 代回去，汇率与标的各出现**一次**：

        ```text
        est_f = (1 - p) * nv + p * nv * (est / est_cal) * (cny / cny_cal)
        ```

        Python 侧 ``calc_with_calibration`` 的式子是：

        ```text
        est_f = (1 - p) * netvalue + p * hedge_value * cny / calibration
        ```

        两者相等要求

        ```text
        hedge_value = nv * cal_f / cny * (est / est_cal) * (cny / cny_cal)
        ```

        —— 这正是本函数的算法。注意 ``nv * cal_f / cny`` 除的是**当前汇率**，
        对应 ``stockdata._attach_hedge_value()`` 的 (A) 式：估值公式里
        ``hedge_value`` 后面还要乘一次 ``cny``，先除掉汇率才不会被算两遍
        （``README_name_and_forex.md`` §2.4①：不除的话估值放大 7 倍）。
        本函数曾经的实现除的是 ``cny_ref``，于是汇率被算了两遍 ——
        人民币升贬 1% 会让估值动 2%（实测参考 EST 4.4110 而非 4.4776）。

        ## 三个 EST 的分界线

        调用方决定用哪个口径的中间层值（``est_value``）与哪天的汇率（``cny``）：

        * 官方 EST —— 中间层**净值**（取不到时当日行情价）+ 中间层日期那条汇率；
        * 参考 EST —— 中间层**当日行情价** + 当前汇率；
        * 实时 EST —— 中间层**实时快照价** + 当前汇率。

        ``est_quote_ref``（``est_cal``）由 :meth:`_est_quote_ref` 算好传进来，
        三个 EST 共用同一个锚点，差别只在分子。曾经这里写死
        `self._middle_quote(...)`（实时快照优先），结果官方 EST 也被实时价影响、
        不再等于基金净值（实测 4.5452 变 4.5833），而且三个 EST 恒等。

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
        # 中间层的校准值（``cal_m``）**不参与这条式子** —— 它只对某些 est 标的
        # 存在（QQQ / SPY 有配对 ^NDX / ^GSPC），AGG / XBI 这类没有配对的 ETF
        # 就没有。**曾经拿它当闸门**（没有就 return None），于是那些基金的官方
        # EST 永远算不出来；生产端页面上它们明明都有值（SH501300 = 0.914）。
        # 现在 :meth:`_build` 无条件建中间层那一行，这里也不再拿 ``cal_m`` 挡路。
        #
        # 校准时用的汇率（cny_cal）：_build() 按校准日期写进 data["CNY"]
        try:
            cny_ref = float(data.get("CNY") or data.get("CNYest") or cny)
        except (TypeError, ValueError):
            cny_ref = float(cny)

        # 贡献值：nv * cal_f / **当前汇率**（见上面 (A) 式的说明）。
        # ``_build()`` 不写 ``data["hedge_value"]``（那是页面构建器
        # ``stockdata._attach_hedge_value()`` 的字段），所以这里自己算。
        try:
            contribution = (float(data["netvalue"]) * float(data["calibration"])
                            / float(cny))
        except (TypeError, ValueError, ZeroDivisionError, KeyError):
            return None

        # 标的相对变化：est / est_cal。取不到基准值按 1.0（= 标的没动），
        # 这与 _middle_quote() "取不到按 1.0" 的约定一致，也远好过旧恒等式
        # 在错误尺度下给出的 4648 倍放大。
        if est_quote_ref in (None, "", 0):
            ratio = 1.0
        else:
            try:
                ratio = float(est_value) / float(est_quote_ref)
            except (TypeError, ValueError, ZeroDivisionError):
                ratio = 1.0
        # 汇率相对变化（只出现一次，见上面从 PHP 推导的过程）
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
        from calc.fundcalc import qdii_est_symbol_map

        return sorted(qdii_est_symbol_map())


def build_estimate_calculator(source: Any) -> EstimateCalculator:
    """按数据源创建计算器（自带 ``fundest`` 存储）。"""
    connection = getattr(source, "connection", None)
    if connection is None:
        raise RuntimeError("数据源没有可用的数据库连接，无法读写 fundest")
    return EstimateCalculator(source, FundEstStore(connection))
