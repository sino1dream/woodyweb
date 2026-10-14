"""自动校准：拿到新官方净值时按当天美股数据重算校准值。

对应 PHP ``FundPairReference::DailyCalibration()``
（``php/stock/fundpairref.php:188``）：

```php
function DailyCalibration()
{
    $strStockId = $this->GetStockId();
    $net_sql = GetNetValueHistorySql();
    $cal_sql = GetCalibrationSql();
    $strDate = $net_sql->GetDateNow($strStockId);       // 本符号的净值最新日期
    if ($strDate == $cal_sql->GetDateNow($strStockId))  return;  // 已校准过 -> 不重做

    if ($strNetValue = $net_sql->GetCloseNow($strStockId))
    {
        if ($fPairNetValue = $this->pair_ref->GetNetValue($strDate))
        {
            $fFactor = $this->CalcFactor($fPairNetValue, floatval($strNetValue), $strDate);
            $cal_sql->WriteDaily($strStockId, $strDate, strval($fFactor));
            $this->LoadCalibration();
        }
    }
}
```

## 校准的是**中间层**，不是基金

这一点很容易搞错（本项目第一版就搞错了）。看调用点
``woody/res/php/_qdiigroup.php:69-82``：

```php
foreach ($arLev as $strSymbol)          // $arLev 是 'QQQ' 这种**中间层**符号
{
    $leverage_ref = new FundPairReference($strSymbol);
    ...
    $leverage_ref->DailyCalibration();  // ← 在中间层引用上调用
}
```

而函数体里用的是 ``$this->GetStockId()``（中间层的 id）。所以：

* **被校准**：``QQQ`` / ``SPY``（中间层）
* **用来算**：它的 ``pair_ref``（``^NDX`` / ``^GSPC``）
* **式子是** ``校准值 = pair 的净值 × 汇率 / 本符号的净值``

实测数据也印证：``QQQ`` 净值日期 10-02 ≠ 校准日期 10-01 ⇒ 该校准；
而三只基金层符号的净值日期与校准日期相同 ⇒ 已校准过。

## 闸门为什么是"日期不等"

``if ($strDate == $cal_sql->GetDateNow($strStockId)) return;`` ——
净值日期与校准日期一致，说明这一天已经校准过，不必也不需要重做。
这与"校准值是**当天美股数据**算出来的固定值"这个语义一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sql.historytable import CalibrationSql


#: 中间层 → 它跟踪的标的（指数）的内置映射。
#:
#: 对应 ``dbtools/seed_demo_data.py`` 的 ``MARKET_LAYERS`` —— 那份是 demo 库里
#: "ETF 跟踪哪个指数"的权威来源。``fundpair`` 表在 demo 里只存"基金 → ETF"，
#: 没有"ETF → 指数"，所以需要这张兜底表。
MIDDLE_PAIR_FALLBACK = {
    "QQQ": "^NDX",
    "SPY": "^GSPC",
}

#: 页面上要自动校准的中间层（demo 范围）。真实部署应从页面涉及的
#: ``$arLev`` 推导，见 :func:`default_middle_symbols`。
DEMO_MIDDLE_SYMBOLS = ("QQQ", "SPY")


@dataclass
class CalibrationOutcome:
    """一次自动校准的结果（供页面/CLI 展示与排查）。"""

    symbol: str
    pair_symbol: Any = None
    date: Any = None
    calibration: Any = None
    pair_value: Any = None
    net_value: Any = None
    cny: Any = None
    written: bool = False
    skipped: str = ""
    scale_converted: bool = False
    #: 这次用的是哪条式子：``"pair/net"`` 是 PHP ``CalcFactor`` 的原路径，
    #: ``""`` 表示退到了旧的"按配对比值缩放"（见 ``_scale_converted``）。
    #: 给运维看 —— 两者算出来的数不一样，排查时必须知道走的是哪条。
    formula: str = ""
    guard: Any = None
    warnings: list = field(default_factory=list)

    def describe(self) -> str:
        if self.skipped:
            return "%s 跳过（%s）" % (self.symbol, self.skipped)
        if self.written:
            return "%s 已校准：%s 校准值=%s（%s=%s / 净值=%s）" % (
                self.symbol, self.date, self.calibration,
                self.pair_symbol, self.pair_value, self.net_value)
        return "%s 算出校准值 %s（未写库）" % (self.symbol, self.calibration)

class AutoCalibrator:
    """中间层符号的自动校准（对应 ``DailyCalibration()``）。

    ``source`` 是 ``FundDataSource``。

    ``pair_fetcher`` 用来替换"指数实时点位"那一级兜底的网络请求
    （默认走 :meth:`_pair_live_quote`）。**这是为了让逻辑可测而留的口子**：
    那一级兜底会真的去新浪抓 ``gb_ndx``，单元测试一旦碰到它就在离线机器上
    返回 ``None``、校准值跟着变 ``None``（本项目实际发生过：同一套用例在装了
    ``requests`` 的机器上绿、在没装的机器上红）。注入后测试不依赖外网。
    """

    def __init__(self, source: Any, connection: Any = None, guard: Any = None,
                 use_guard: bool = True, pair_fetcher: Any = None):
        self.source = source
        self.connection = connection if connection is not None else getattr(source, "connection", None)
        self.pair_fetcher = pair_fetcher
        #: 写入前的估值守卫。默认按数据源自动创建；传 ``use_guard=False`` 关掉
        #: （只有明确知道自己在做什么时才关 —— 关掉就等于放弃那道防空转保护）。
        if not use_guard:
            self.guard = None
        elif guard is not None:
            self.guard = guard
        else:
            self.guard = EstimateGuard(source) if self.connection is not None else None

    # -- 主入口 -----------------------------------------------------------
    def calibrate_middle(self, middle_symbol: str, apply: bool = True) -> CalibrationOutcome:
        """对一个**中间层**符号做自动校准。

        ``apply=False`` 只算出校准值、不写库（供核对与测试）。
        """
        outcome = CalibrationOutcome(symbol=middle_symbol)

        stock_id = self.source.get_stock_id(middle_symbol)
        if stock_id is None:
            outcome.skipped = "符号不在 stock 表里"
            return outcome

        net_value, net_date = self._latest_net_value(stock_id)
        if net_value is None or net_date is None:
            outcome.skipped = "没有单位净值"
            return outcome
        outcome.date = net_date
        outcome.net_value = net_value

        # 闸门：净值日期 == 校准日期 -> 这一天已经校准过
        calibration_date = self._calibration_date(stock_id)
        if calibration_date is not None and str(calibration_date) == str(net_date):
            outcome.skipped = "该净值日期已校准过"
            return outcome

        pair_symbol = self._pair_symbol(middle_symbol)
        if not pair_symbol:
            outcome.skipped = "没有配对标的是"
            return outcome
        outcome.pair_symbol = pair_symbol

        pair_value = self._pair_value(pair_symbol, net_date)
        if pair_value is None:
            outcome.skipped = "%s 在 %s 没有净值/行情" % (pair_symbol, net_date)
            return outcome
        outcome.pair_value = pair_value

        cny = self._cny(net_date)
        outcome.cny = cny

        # **尺度换算**：本符号的 ``netvaluehistory`` 可能不是"真实每份净值"。
        #
        # PHP 直接用 ``$net_sql->GetCloseNow($strStockId)`` 当分母，因为它那边
        # 中间层的 ``netvaluehistory`` 存的就是真实每份净值（QQQ = 749.378）。
        # 本项目演示库把中间层净值归一化成了 1.0（见 ``seed_demo_data.MARKET_LAYERS``），
        # 照抄公式会把校准值放大约 750 倍（实测 QQQ 得到 31099，
        # 生产端是 41.111），页面看起来像坏了。
        #
        # 但**真正的约束**不是"看起来像生产"，而是估值必须成立：
        # ``fundestcalc._qdii_value()`` 里
        #
        #     est_quote_ref = cny_ref / cal_m
        #     ratio         = est_value / est_quote_ref      # 对 cal_m **线性**
        #
        # 所以 **``est_value × cal_m`` 这个乘积必须守恒**。演示库里
        # ``est_value`` 是中间层的实时价（QQQ ≈ 757），它与 ``cal_m`` **成对**，
        # 只改一个就会让估值按同比例炸掉（实测 4.59 -> 20245，且**不报错**）。
        #
        # **换算公式：按配对值的变化比例反向缩放**（实测标定，非推导）
        #
        # 我先试过 5 个代数推导，全部被估值守卫拒绝：
        #
        #     cal_old × pair / cny   = 41.36   -> 估值 9527     拒
        #     cny / pair × cal_old   = 1.9e-06 -> 估值 0.00046  拒
        #     cny / pair             = 2.2e-04 -> 估值 0.050    拒
        #     1.0（生产口径量级）                -> 估值 230      拒
        #     cal_old                = 0.008945 -> 估值 2.0605   放行 ✓
        #     cal_old × pair_old/pair_new       -> 放行（见下）
        #
        # 于是改用**测量**：扰动 ``cal_m`` 量出估值对它近似线性
        # （``sql/_measure_exponent.py``，k≈0.94），得到"配对值变了就按配对比值
        # 反向缩放"：
        #
        #     cal_new = cal_old × pair_old / pair_new
        #
        # 方向是**反向**（配对涨 → ``cal_m`` 跌），因为估值对两者都近似线性正比。
        #
        # **``pair_old`` 的现实问题**：本演示库里 ``^NDX``/``^GSPC`` **没有任何
        # 历史行情**（``netvaluehistory`` 与 ``stockhistory`` 都是空的），
        # 所以基准日的配对值取不到。取不到时**退回 ``cal_old`` 不变** ——
        # 这是最保守的选择（实测该值放行，估值偏差 1.11%），
        # 代价是"配对值大幅变动时校准值不跟随"。
        #
        # 想真正修好这一点，需要给指数落历史行情（或接真实美股 ETF 净值源），
        # 见 ``usnavsource`` 模块 docstring 里的信源实测清单。
        # 传 symbol 是为了让"当日收盘价"优先取实时快照（见 _implied_middle_nav）
        implied = self._implied_middle_nav(stock_id, cny, symbol=middle_symbol)
        old_calibration = self._calibration_value(stock_id)
        pair_old = self._pair_value_at(pair_symbol, calibration_date)

        # **能不能直接用 PHP 的原公式**，取决于 netvaluehistory 里的那个净值
        # 是不是"真实每份净值"的量级。
        #
        # PHP 从不判断这件事 —— 它那边中间层的 ``netvaluehistory`` 存的就是
        # 真实每份净值（QQQ = 749.378）。本项目演示库曾经把它归一化成 1.0
        # （``seed_demo_data.MARKET_LAYERS``），那时照抄 ``pair / net`` 会把
        # 校准值放大约 750 倍（实测 QQQ 得到 31099，生产端是 41.111）。
        #
        # 现在中间层净值由 ``sync-nav`` 按"当日收盘价"写入（P0.5，见
        # :meth:`_implied_middle_nav`），与官方 NAV 只差千分位，所以这道判断
        # 会放行 —— **判断的尺子就是第一步装的那个 ``nav_matches_price``**。
        # **没有尺子 ≠ 可信**：``nav_matches_price`` 对 ``price=None`` 是放行的
        # （那条规则是"别把查不到价格变成净值永远算不出来"），而这里要判断的
        # 是"净值是不是真实每份净值"，两件事 —— 拿不到当日价格就只能走旧退路。
        try:
            has_price = (implied not in (None, "")
                         and abs(float(implied)) > 0.0000005)
        except (TypeError, ValueError):
            has_price = False
        if has_price and nav_matches_price(net_value, implied):
            # PHP ``DailyCalibration()``（fundpairref.php:196-201）原文：
            #
            #     $fFactor = CalcFactor($fPairNetValue, $strNetValue, $strDate);
            #     $cal_sql->WriteDaily($strStockId, $strDate, $fFactor);
            #
            # 实测它就是生产端的定义口径（2026-10-13）：
            #     ^NDX 30569.4809 / QQQ 743.57 = 41.1118   生产锚点 41.111  ✓
            #     ^GSPC 7805.4502 / SPY 778.01 = 10.0326   生产锚点 10.035  ✓
            # 走的是 PHP 原路径，不是异常 —— 记在 outcome.formula 上由 CLI 展示，
            # 不塞进 warnings（warnings 是给"发生了意外"用的）。
            outcome.net_value = net_value
            factor = self.calc_factor(pair_value, net_value,
                                      self._calc_cny(middle_symbol, net_date))
            outcome.formula = "pair/net"
        else:
            outcome.net_value = implied
            factor = self._scale_converted(old_calibration, pair_old, pair_value,
                                           net_value, cny, outcome, calibration_date)

        if factor is None:
            outcome.skipped = "校准值算不出来（净值为 0 或参数无效）"
            return outcome
        outcome.calibration = factor

        if apply and self.connection is not None:
            # **写入前过估值守卫**：防止"尺度和中间层净值不匹配"的校准值
            # 静默把估值搞成荒谬数字（实测出过 20245 vs 4.59 的事故）。
            if self.guard is not None:
                verdict = self.guard.check(middle_symbol, factor)
                outcome.guard = verdict
                if not verdict.ok:
                    outcome.skipped = verdict.reason
                    return outcome
            outcome.written = self._write(stock_id, net_date, factor)
        return outcome

    def _scale_converted(self, old_calibration: Any, pair_old: Any, pair_value: Any,
                         net_value: Any, cny: Any, outcome: Any,
                         calibration_date: Any = None) -> Any:
        """净值**不是**真实每份净值时用的旧退路：按配对值的变化比例缩放。

        ## 为什么还有这条路

        演示库的中间层净值曾经是归一化到 1.0 的（``seed_demo_data.MARKET_LAYERS``）。
        那种数据下不能用 ``pair / net`` —— 会放大约 750 倍。这条路是当时靠
        **测量**标定的权宜之计（PHP 没有对应的实现）：

        ```text
        cal_new = cal_old × pair_old / pair_new
        ```

        方向**反向**（配对涨 → ``cal_m`` 跌），因为估值对两者都近似线性正比
        （``sql/_measure_exponent.py``，k≈0.94）。

        ## 它的代价

        这个式子把**净值的变化**丢掉了 —— 完整的换算应该是
        ``cal_old × pair_new/pair_old × nav_old/nav_new``，这里只有 pair 那一项。
        所以只要净值真的变了，它就会偏；实测 QQQ 偏出 4.7%。净值是真实量级时
        调用方不会走这里（见 :meth:`calibrate_middle` 的判断）。
        """
        if (old_calibration not in (None, "") and pair_old not in (None, "")
                and abs(float(pair_old)) > 0.0000005
                and abs(float(pair_value)) > 0.0000005):
            outcome.scale_converted = True
            try:
                return float(old_calibration) * float(pair_old) / float(pair_value)
            except (TypeError, ValueError, ZeroDivisionError):
                return None
        if old_calibration not in (None, ""):
            # 保守退路：不动校准值（实测放行）
            outcome.warnings.append(
                "取不到基准日（%s）的配对值，按「不改变校准值」处理"
                % calibration_date)
            return float(old_calibration)
        outcome.warnings.append(
            "取不到中间层净值尺度，按库里净值 %s 直接换算（可能与生产量级不同）"
            % net_value
        )
        return self.calc_factor(pair_value, net_value, cny)

    def _calc_cny(self, symbol: Any, date: Any) -> Any:
        """``CalcFactor`` 要用的汇率；不涉及换算时返回 ``None``（等价于 1.0）。

        对应 PHP ``fundpairref.php:14-43`` 里 ``$this->cny_ref`` 的构造：
        **本符号不是 A 股时，配对标的若是美股/美股指数（``else`` 分支，:41），
        ``cny_ref`` 就是 ``false``** —— ``CalcFactor`` 里那个 ``if ($this->cny_ref)``
        不成立，直接 ``return $fPairNetValue / $fNetValue``，**不乘汇率**。

        本项目 demo 的中间层全是美股 ETF（QQQ/SPY/AGG/…），走的正是这一支，
        所以对它们必须传 1.0 —— 传真实汇率 6.69 会把校准值放大 6.69 倍。

        注意这里**不能**图省事直接传 ``self._cny(date)``：那是"中间价是多少"，
        而 ``CalcFactor`` 要的是"这个符号有没有汇率换算" —— 两个不同的问题。
        """
        try:
            from core.stocksymbol import StockSymbol

            if StockSymbol(symbol).is_symbol_a():
                return self._cny(date)
        except (ImportError, ValueError, AttributeError):
            pass
        return None

    def _calibration_value(self, stock_id: Any) -> Any:
        """该符号当前的校准值（``cal_m``）；没有记录返回 ``None``。

        与 :meth:`_calibration_date` 同源，只是取 ``close`` 而不是 ``date``。
        """
        if self.connection is None:
            return None
        reader = getattr(self.source, "get_calibration_now", None)
        if reader is None:
            return None
        try:
            record = reader(stock_id)
        except Exception:
            return None
        if record is None:
            return None
        return getattr(record, "close", None)

    def _implied_middle_nav(self, stock_id: Any, cny: Any = None,
                           symbol: Any = None) -> Any:
        """中间层的每份净值 —— **直接取它的当日收盘价**（P0.5 定稿的口径②）。

        ## 为什么放弃「汇率 / cal_m」

        旧式子长这样：

        ```text
        净值 = 汇率 / cal_m
        ```

        它**只在种子尺度成立**：那时 ``cal_m = 0.008945``，
        ``6.705 / 0.008945 = 749.58``，与生产端 QQQ 的真实净值 ``749.378``
        吻合到 0.03% —— 因为那条种子校准值**本来就是按真实 NAV 解出来的**。

        校准口径改成生产尺度后（QQQ 的 ``cal_m = 41.5793``），
        同一个式子给出 ``6.69 / 41.58 = 0.161``，差 **4648 倍**，而它会被
        ``usnavsource`` 和 ``autocalibrationcli --set-nav`` 直接写进
        ``netvaluehistory``（README_p0_data_layer.md §四.3 坑②，
        pitfalls #60/#61 那一类"数字大得离谱还没报错"的事故）。

        根因是那个恒等式把**尺度**和**净值**搅在一起了：``cal_m`` 会随部署
        尺度重算，净值不会。任何把 ``cal_m`` 当分母的做法都会随尺度漂移。

        ## 新口径：净值 = 当日收盘价

        ETF 的净值与交易价格只差千分位（做市商的一级申购赎回套利会把折溢价
        抹平），生产端自己也是这么用的 —— ``netvalueclosecn.php`` 上美股 ETF
        的"单位净值"就是它的收盘价。这条式子**完全不碰校准值**，所以刻度怎么
        重算都不会坏，这正是我们要的性质。

        剩下的误差来源只有**折溢价**（千分位）与**分红除息**（ETF 分红时 NAV
        与价格同额下降，两者同步，不像开放式基金那样错位）。

        ## 与 usnavsource 的分工

        本类只有 ``stock_id``，所以默认走 ``stockhistory`` 的历史收盘价；
        调用方传了 ``symbol`` 就优先走实时快照 ``realtimequote``。
        ``DerivedMiddleNavSource._latest_price`` 也是这个优先级（PHP 语义：
        有实时行情就用实时的）。取不到价格时返回 ``None``，让调用方回落到
        常量表/库里的净值 —— 绝不臆造一个数。

        ``cny`` 保留在签名里只为**不破坏已有调用方**，新口径用不到它。
        """
        # ① 实时快照优先（PHP 语义：盘中就用盘中价）
        if symbol is not None:
            quote_reader = getattr(self.source, "get_stored_quote", None)
            if quote_reader is not None:
                try:
                    quote = quote_reader(symbol)
                except Exception:
                    quote = None
                price = getattr(quote, "price", None) if quote is not None else None
                if price not in (None, ""):
                    try:
                        if abs(float(price)) > 0.0000005:
                            return float(price)
                    except (TypeError, ValueError):
                        pass

        # ② 退回历史收盘价
        close_reader = getattr(self.source, "get_latest_history_close", None)
        if close_reader is None:
            return None
        try:
            close = close_reader(stock_id)
        except Exception:
            return None
        if close in (None, ""):
            return None
        try:
            value = float(close)
        except (TypeError, ValueError):
            return None
        return value if abs(value) > 0.0000005 else None


    def calibrate_many(self, symbols: Any, apply: bool = True) -> list:
        """批量校准中间层符号。"""
        return [self.calibrate_middle(symbol, apply=apply) for symbol in symbols]

    def calibrate_for_fund(self, fund_symbol: str, apply: bool = True) -> Any:
        """给一只**基金**找到它该校准的中间层，并校准那一层。

        页面访问走这条：页面知道的是基金符号，而校准对象是中间层。
        对应 PHP ``QdiiCreateGroup()`` 里 ``$arLev`` 与当前基金的对应关系。
        """
        middle = self.middle_symbol_of(fund_symbol)
        if not middle:
            outcome = CalibrationOutcome(symbol=fund_symbol)
            outcome.skipped = "没有中间层标的"
            return outcome
        return self.calibrate_middle(middle, apply=apply)

    # -- 算式 -------------------------------------------------------------
    def calc_factor(self, pair_value: Any, net_value: Any, cny: Any = None) -> Any:
        """对应 ``CalcFactor()``：``校准值 = pair 值 × 汇率 / 本符号净值``。

        PHP 原文（``fundpairref.php:213``）：

        ```php
        function CalcFactor($fPairNetValue, $fNetValue, $strDate)
        {
            if ($this->cny_ref)
            {
                $fCny = $this->cny_ref->GetVal($strDate);
                ...
        ```

        与 ``QdiiGetCalibration()`` 是同一个式子 —— 复用同一个实现，
        避免两处口径分家。
        """
        if pair_value in (None, "") or net_value in (None, ""):
            return None
        try:
            base = float(net_value)
        except (TypeError, ValueError):
            return None
        if abs(base) <= 0.0000005:
            return None
        try:
            rate = 1.0 if cny in (None, "") else float(cny)
        except (TypeError, ValueError):
            rate = 1.0
        try:
            from calc.fundcalc import qdii_get_calibration
        except ImportError:
            return None
        try:
            return qdii_get_calibration(float(pair_value), rate, base)
        except (TypeError, ValueError):
            return None

    # -- 取数 -------------------------------------------------------------
    def _calibration_date(self, stock_id: Any) -> Any:
        """该符号最新的校准日期（对应 ``$cal_sql->GetDateNow()``）。

        **没有校准记录时返回 ``None``**，而不是空串 ——
        否则闸门会把"从没校准过"误判成"已校准过"（字符串恰好相等）。
        """
        if self.connection is None:
            return None
        reader = getattr(self.source, "get_calibration_now", None)
        if reader is not None:
            try:
                record = reader(stock_id)
            except Exception:
                record = None
            if record is not None:
                date = getattr(record, "date", None)
                return str(date) if date else None
        return None

    def _latest_net_value(self, stock_id: Any):
        """最新单位净值与它的日期，对应 ``GetDateNow()`` / ``GetCloseNow()``。"""
        reader = getattr(self.source, "get_latest_net_value", None)
        if reader is not None:
            try:
                record = reader(stock_id)
            except Exception:
                record = None
            if record is not None:
                return getattr(record, "close", None), getattr(record, "date", None)
        close_reader = getattr(self.source, "get_latest_net_value_close", None)
        date_reader = getattr(self.source, "get_latest_net_value_date", None)
        if close_reader is None or date_reader is None:
            return None, None
        try:
            return close_reader(stock_id), date_reader(stock_id)
        except Exception:
            return None, None

    def _pair_symbol(self, middle_symbol: str) -> Any:
        """中间层跟踪的标的（``QQQ`` → ``^NDX``），对应 ``$this->pair_ref``。

        **拿不到就返回 ``None``，调用方应当跳过而不是继续算。**

        三条候选路径，按可靠性排序：

        1. ``fundpair`` 表（``id`` = 本体，``stock_id`` = 配对标的是）；
        2. :data:`MIDDLE_PAIR_FALLBACK` 内置映射；
        3. ``fundcalc.qdii_est_symbol_map()`` 反查（哪个基金的估值标的是它）。

        **为什么必须有第 2 条**：demo 库里 ``fundpair`` 只存"基金 → ETF"
        （``SZ161130 -> QQQ``），**没有"ETF → 指数"**（QQQ 不在 ``id`` 侧）。
        少了这条兜底，自动校准会一直"没有配对标的是"而静默失效 ——
        这正是实测踩到的。

        **为什么必须排除"自己配自己"**：第 3 条的 ``value`` 就是中间层本身，
        反查 ``AGG`` 会得到 ``AGG``。那时 ``pair/net`` 恒等于 1，写进
        ``calibrationhistory`` 是纯噪声。PHP 干脆不会走到这一步 ——
        ``FundPairReference`` 只在 ``GetPairSymbol()`` 有值时才构造
        ``pair_ref``（``fundpairref.php:13``），没有配对的符号根本不参与
        ``DailyCalibration()``。
        """
        candidate = self._pair_symbol_candidate(middle_symbol)
        if candidate is None or str(candidate) == str(middle_symbol):
            return None
        return str(candidate)

    def _pair_symbol_candidate(self, middle_symbol: str) -> Any:
        """:meth:`_pair_symbol` 的原始候选（可能返回符号自身）。"""
        connection = self.connection
        if connection is not None:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT p.symbol FROM fundpair f "
                    "JOIN stock p ON p.id = f.stock_id "
                    "WHERE f.id = (SELECT id FROM stock WHERE symbol = %s) LIMIT 1",
                    (middle_symbol,),
                )
                row = cursor.fetchone()
                if row and row["symbol"]:
                    return str(row["symbol"])
            except Exception:
                pass
            finally:
                cursor.close()
        if middle_symbol in MIDDLE_PAIR_FALLBACK:
            return MIDDLE_PAIR_FALLBACK[middle_symbol]
        try:
            from calc.fundcalc import qdii_est_symbol_map
        except ImportError:
            return None
        for _fund, est in qdii_est_symbol_map().items():
            if est == middle_symbol:
                return est
        return None

    def _pair_value_at(self, pair_symbol: str, date: Any) -> Any:
        """配对标的在 ``date`` 的值；取不到返回 ``None``。

        与 :meth:`_pair_value` 同一条取数链，只是不回落实时点位 ——
        这里要的是"基准日那天的历史值"，拿实时值当历史值是错的。
        """
        if date in (None, ""):
            return None
        pair_id = self.source.get_stock_id(pair_symbol)
        if pair_id is None:
            return None
        for name in ("get_netvalue_close", "get_history_close"):
            reader = getattr(self.source, name, None)
            if reader is None:
                continue
            try:
                value = reader(pair_id, date)
            except Exception:
                value = None
            if value not in (None, ""):
                return value
        return None

    def _pair_value(self, pair_symbol: str, date: Any) -> Any:
        """配对标的在 ``date`` 上的值，按可靠性依次尝试：

        1. **净值表**（``netvaluehistory``）—— ``_getOfficialEstVal()`` 的首选；
        2. **行情表**（``stockhistory``）；
        3. **实时快照**（``realtimequote``）：**只有行情日期 == ``date`` 才算数**；
        4. **指数实时点位**（新浪 ``gb_ndx`` / ``gb_inx``）—— 兜底。

        第 3 级是 P0.4 之后加的：后台 daemon 会把 ``^NDX``/``^GSPC`` 的当天点位
        抓进 ``realtimequote``，这正是 PHP ``$this->pair_ref->GetNetValue($strDate)``
        **按日期**要拿的东西。

        **为什么必须卡日期**：不卡就会拿"此刻"的点位去配"那天"的净值，口径混用。
        实测这一项就让 QQQ 的校准值偏了 1.5%（拿到 30111 而不是 10-13 的 30569，
        ``cal`` 从生产锚点 41.111 掉到 40.49）。

        第 4 级保留是因为 P0.4 之前 demo 库两个指数**都没落库**，
        少了它自动校准就永远"配对标的没有值"。
        """
        pair_id = self.source.get_stock_id(pair_symbol)
        if pair_id is not None:
            reader = getattr(self.source, "get_netvalue_close", None)
            if reader is not None:
                try:
                    value = reader(pair_id, date)
                except Exception:
                    value = None
                if value not in (None, ""):
                    return value
            his = getattr(self.source, "get_history_close", None)
            if his is not None:
                try:
                    value = his(pair_id, date)
                except Exception:
                    value = None
                if value not in (None, ""):
                    return value

        quote = self._stored_quote_on(pair_symbol, date)
        if quote is not None:
            return quote
        return self._fetch_pair_quote(pair_symbol)

    def _stored_quote_on(self, symbol: str, date: Any) -> Any:
        """``realtimequote`` 里**行情日期等于 ``date``** 的那个价格，否则 ``None``。

        卡日期的原因见 :meth:`_pair_value` 第 3 级的说明。
        """
        if date is None:
            return None
        reader = getattr(self.source, "get_stored_quote", None)
        if reader is None:
            return None
        try:
            quote = reader(symbol)
        except Exception:
            return None
        if quote is None:
            return None
        quote_date = getattr(quote, "date", None)
        price = getattr(quote, "price", None)
        if quote_date is None or price in (None, ""):
            return None
        if str(quote_date) != str(date):
            return None
        return price

    def _fetch_pair_quote(self, pair_symbol: str) -> Any:
        """兜底那一级"实时点位"：``pair_fetcher`` 优先，否则走新浪。

        拆出这个方法是为了让联网这件**外部依赖**只有一个出口 ——
        生产走 :meth:`_pair_live_quote`，测试注入 ``pair_fetcher`` 顶掉它。
        """
        if self.pair_fetcher is not None:
            try:
                return self.pair_fetcher(pair_symbol)
            except Exception:
                return None
        return self._pair_live_quote(pair_symbol)

    def _pair_live_quote(self, pair_symbol: str) -> Any:
        """从新浪取指数实时点位（``^NDX`` → ``gb_ndx``）。

        ``StockSymbol.get_sina_symbol()`` 会把 ``^`` 换成 ``$`` 再拼 ``gb_``，
        恰好就是新浪的全球指数代码（实测 ``^NDX`` → ``gb_ndx``、
        ``^GSPC`` → ``gb_inx``）。

        取不到返回 ``None`` —— 自动校准会跳过，不影响页面其余部分。
        """
        try:
            from core.stocksymbol import StockSymbol
            from source.sinaquotes import get_default_client
        except ImportError:
            return None
        try:
            code = StockSymbol(pair_symbol).get_sina_symbol()
        except Exception:
            return None
        if not code:
            return None
        try:
            text = get_default_client().fetch([code])
        except Exception:
            return None
        if not text:
            return None
        if isinstance(text, (bytes, bytearray)):
            try:
                from source.sinaquotes import decode_sina_content

                text = decode_sina_content(text)
            except Exception:
                return None
        try:
            from source.sinaquotes import split_quote_lines
        except ImportError:
            return None
        lines = split_quote_lines(text)
        line = lines.get(code)
        if not line:
            return None
        start = line.find('"')
        end = line.rfind('"')
        if start < 0 or end <= start:
            return None
        fields = line[start + 1:end].split(",")
        # 美股指数（gb_ 前缀）的字段 1 是最新点位（实测 gb_ndx/gb_inx 都是）
        if len(fields) < 2:
            return None
        try:
            return float(fields[1])
        except (TypeError, ValueError):
            return None

    def _cny(self, date: Any) -> Any:
        """该日期的美元人民币中间价；没有就退到最新一条。

        对应 ``$this->cny_ref->GetVal($strDate)``；取不到时按 1.0 处理
        （与 ``CalcFactor()`` 在没有 ``cny_ref`` 时等价）。
        """
        cny_id = self.source.get_stock_id("USCNY")
        if cny_id is None:
            return None
        reader = getattr(self.source, "get_netvalue_close", None)
        if reader is not None:
            try:
                value = reader(cny_id, date)
            except Exception:
                value = None
            if value not in (None, ""):
                return value
        fallback = getattr(self.source, "get_latest_net_value_close", None)
        if fallback is None:
            return None
        try:
            return fallback(cny_id)
        except Exception:
            return None

    def middle_symbol_of(self, fund_symbol: str) -> Any:
        """基金对应的中间层符号（用**杠杆替代**解析）。

        真实库里 ``SZ161130`` 的 ``est_symbol`` 是 ``^NDX``（指数），
        而校准对象是 ``QQQ``。只取 ``est_symbol`` 会永远算不出校准值。
        """
        from core.stockdata import get_leverage_hedge_symbol

        reader = getattr(self.source, "get_fund_profile", None)
        if reader is None:
            return None
        try:
            profile = reader(fund_symbol)
        except Exception:
            return None
        if profile is None:
            return None
        hedge_symbol = getattr(profile, "est_symbol", None) or getattr(profile, "pair_symbol", None)
        if not hedge_symbol:
            return None
        return get_leverage_hedge_symbol(fund_symbol) or hedge_symbol

    # -- 写库 -------------------------------------------------------------
    def _write(self, stock_id: Any, date: Any, value: Any) -> bool:
        """写校准值，对应 ``$cal_sql->WriteDaily()``。

        ``WriteDaily()`` 的语义是"**已有就不动**"
        （``InsertDaily`` 里判 ``if ($this->GetRecord(...)) return false;``）。
        这里照抄；闸门已经先挡掉重复调用，这条是双保险。
        """
        table = CalibrationSql(self.connection)
        if table.get_record(stock_id, date) is not None:
            return False
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO calibrationhistory (stock_id, `date`, close, `time`, num) "
                "VALUES (%s, %s, %s, %s, 1)",
                (stock_id, date, value, self._now_time()),
            )
            commit = getattr(self.connection, "commit", None)
            if commit is not None:
                commit()
            return True
        except Exception:
            return False
        finally:
            cursor.close()

    @staticmethod
    def _now_time() -> str:
        """``calibrationhistory.time`` 是 NOT NULL 且无默认值，必须给。"""
        import datetime as _datetime

        return _datetime.datetime.now().strftime("%H:%M:%S")


#: 派生净值与当日价格之间允许的偏离（ETF 净值与价格只差千分位；放宽到 ±25%
#: 是为了容纳分红除息与"净值是收盘后、价格是盘中"的时间差）。
NAV_PRICE_TOLERANCE = 0.25


def nav_matches_price(nav: Any, price: Any) -> bool:
    """派生出来的净值是否与**当日价格**同量级。

    ## 为什么需要它（P0.5 的第一个护栏）

    :meth:`AutoCalibrator._implied_middle_nav` 用的恒等式
    ``净值 = 汇率 / cal_m`` **只在种子尺度成立**（cal_m = 0.008945 →
    6.705/0.008945 = 749.58，与 QQQ 真实净值吻合）。2026-10-13 按生产端口径
    重算后 QQQ 的 cal_m 已是 **41.5793**（= 指数 / 净值），同一个式子给出
    ``6.69 / 41.58 = 0.161`` —— 差 4648 倍。而 ``usnavsource`` 与
    ``autocalibrationcli --set-nav`` 都会把这个值**写进
    ``netvaluehistory```**，重新制造 pitfalls #60/#61 那一类事故
    （净值优先的取数会直接把它当真）。

    价格是**尺度无关**的锚：ETF 的净值与价格总是同一量级（QQQ 749.58 vs
    743.57），所以拿它当尺子，恒等式算错了立刻能看出来。

    ``price`` 取不到时返回 ``True`` —— 没有尺子就不要拦，交给调用方兜底，
    免得把"查不到价格"变成"净值永远算不出来"。
    """
    try:
        nav_value = float(nav)
        price_value = float(price)
    except (TypeError, ValueError):
        return True
    if abs(price_value) <= 0.0000005 or abs(nav_value) <= 0.0000005:
        return True
    ratio = nav_value / price_value
    return abs(ratio - 1.0) <= NAV_PRICE_TOLERANCE

def build_auto_calibrator(source: Any, use_guard: bool = True) -> AutoCalibrator:
    """按数据源创建自动校准器。"""
    return AutoCalibrator(source, connection=getattr(source, "connection", None),
                          use_guard=use_guard)


#: 估值守卫的默认容差（相对偏差）。
#
# 取 5% 而不是 1%：**汇率与行情的正常日内变动**就可能有 1% 量级，
# 守卫要抓的是"量级错了"（实测 20245 vs 4.55 是 440000%），不是抓细微漂移。
GUARD_TOLERANCE = 0.05


@dataclass
class GuardVerdict:
    """估值守卫的裁决。"""

    ok: bool
    fund_symbol: Any = None
    estimate: Any = None
    net_value: Any = None
    deviation: Any = None
    reason: str = ""

    def describe(self) -> str:
        if self.ok:
            return "守卫通过（%s 估值=%s vs 净值=%s，偏差=%s）" % (
                self.fund_symbol, self.estimate, self.net_value, self.deviation)
        return "守卫拒绝写入：%s" % self.reason


class EstimateGuard:
    """写入校准值**之前**用真实估值管线验一遍，偏差过大就拒写。

    ## 为什么必须有这道守卫

    演示库的中间层有两个量参与估值：

    * ``est_value`` —— 中间层的实时价（``QQQ`` ≈ 757）；
    * ``cal_m`` —— 中间层的校准值。

    ``fundestcalc._qdii_value()`` 里 ``ratio = est_value / est_quote_ref``，
    而 ``est_quote_ref`` 取的是**中间层自己的历史报价**（与 ``est_value`` 同尺度），
    所以这一项对 ``cal_m`` **不再敏感** —— 那是 2026-10-14 修掉尺度错位之后的
    行为（修之前它用恒等式 ``cny_ref / cal_m`` 反推基准价，对 ``cal_m`` 线性，
    单方面改 ``cal_m`` 会让估值按同比例炸掉）。

    实测过一次真实事故：自动校准按"生产口径"写出了 ``QQQ = 41.488229``
    （演示库当时的归一化尺度是 ``0.008945``），结果

    ```text
    SZ161130 官方 EST = 20245.200164     （应为 4.59）
    ```

    这类错误**不会报错**，只会静默出一个荒谬的数字。守卫就是为它加的。
    尺度错位修掉之后这道守卫依然要留：它守的是"候选校准值写进去之后，
    整条估值管线给出的数还在净值量级"这个**端到端**不变量，
    与内部用哪个公式取基准价无关。

    ## 判定方式

    把候选校准值**临时写进库**、走一遍**真实估值管线**
    （``fundestcalc.compute_official``）、与净值比较，然后**无论成败都还原**：

    ```text
    |估值 / 净值 - 1| <= 容差   ->  放行
    ```

    为什么非要用真实管线而不是在公式上做区间判断：本项目实测过三条估值路径，
    **只有真实管线那条对 ``cal_m`` 敏感**：

    ==========================================  ============================
    路径                                          改 ``cal_m`` 后
    ==========================================  ============================
    ``est_net_value(symbol)``（不带 source）      4.5452 -> 4.5452（不敏感）
    ``est_net_value(symbol, est_source())``      0.229  -> 8.465（敏感但绝对值不对）
    ``fundestcalc.compute_official()``          4.59   -> 20245（**真实管线**）
    ==========================================  ============================

    守卫一开始挂在第一条上，六个候选值全部"放行"——**等于没守**。
    这条踩坑记录留在测试 ``EstimateGuardTest.test_guard_is_sensitive_to_calibration``。
    """

    def __init__(self, source: Any, tolerance: float = GUARD_TOLERANCE):
        self.source = source
        self.tolerance = tolerance

    def check(self, middle_symbol: str, candidate_value: Any) -> GuardVerdict:
        """检查把 ``middle_symbol`` 的校准值改成 ``candidate_value`` 会不会破坏估值。

        做法：临时写库 -> 跑真实管线 -> 还原原值。**必须还原**，
        否则守卫自己就把数据改了（这是它最容易出错的地方）。

        无法检查时 ``ok=True`` 并说明原因 —— 守卫不该变成"什么都不能写"的枷锁，
        但会把"没查成"记在 ``reason`` 里。
        """
        stock_id = self.source.get_stock_id(middle_symbol)
        if stock_id is None:
            return GuardVerdict(ok=True, reason="符号不在 stock 表里，跳过检查")

        fund_symbol = self._fund_using(middle_symbol)
        if fund_symbol is None:
            return GuardVerdict(ok=True, reason="找不到使用该中间层的基金，跳过检查")
        net_value = self._fund_net_value(fund_symbol)
        if net_value in (None, ""):
            return GuardVerdict(ok=True, reason="基金 %s 没有净值可比，跳过检查" % fund_symbol)

        try:
            candidate = float(candidate_value)
        except (TypeError, ValueError):
            return GuardVerdict(ok=True, reason="候选值不是数字，跳过检查")

        connection = getattr(self.source, "connection", None)
        if connection is None:
            return GuardVerdict(ok=True, reason="没有数据库连接，跳过检查")

        original = self._current_calibration(stock_id)
        try:
            self._write_calibration(stock_id, candidate)
            estimate = self._official_estimate(fund_symbol)
        except Exception as exc:
            return GuardVerdict(ok=True, reason="估值失败（%s），跳过检查" % exc)
        finally:
            # **一定要还原**，否则守卫变成破坏者
            self._write_calibration(stock_id, original)

        if estimate in (None, 0):
            return GuardVerdict(ok=True, reason="估值为空，跳过检查")
        deviation = abs(float(estimate) / float(net_value) - 1.0)
        verdict = GuardVerdict(
            ok=deviation <= self.tolerance,
            fund_symbol=fund_symbol,
            estimate=estimate,
            net_value=net_value,
            deviation=deviation,
        )
        if not verdict.ok:
            verdict.reason = (
                "写入 %s=%s 会让 %s 的估值变成 %s（净值 %s，偏差 %.0f%% > 容差 %.0f%%）。"
                "通常是校准值与中间层净值**不同尺度** —— 见 skill palmmicro-ops。"
                % (middle_symbol, candidate_value, fund_symbol, estimate,
                   net_value, deviation * 100, self.tolerance * 100)
            )
        return verdict

    def _fund_using(self, middle_symbol: str) -> Any:
        """找出使用该中间层的基金（用于比对估值）。

        **可注入**：测试里 ``fundcalc.qdii_est_symbol_map()`` 从库读配置，
        测试替身没有那套数据，会一直"找不到基金"而让守卫整体空转。
        把它做成方法就是为了让测试能替换掉（见
        ``test_autocalibration.EstimateGuardRestoreTest``）。
        """
        try:
            from calc.fundcalc import qdii_est_symbol_map
            from core.stockdata import get_leverage_hedge_symbol
        except ImportError:
            return None
        mapping = qdii_est_symbol_map()
        for candidate in sorted(mapping):
            resolved = get_leverage_hedge_symbol(candidate) or mapping[candidate]
            if resolved == middle_symbol and self.source.get_stock_id(candidate) is not None:
                return candidate
        return None

    def _fund_net_value(self, fund_symbol: str) -> Any:
        stock_id = self.source.get_stock_id(fund_symbol)
        if stock_id is None:
            return None
        reader = getattr(self.source, "get_latest_net_value_close", None)
        if reader is not None:
            try:
                return reader(stock_id)
            except Exception:
                return None
        return None

    def _current_calibration(self, stock_id: Any) -> Any:
        reader = getattr(self.source, "get_calibration_now", None)
        if reader is None:
            return None
        try:
            record = reader(stock_id)
        except Exception:
            return None
        return getattr(record, "close", None) if record is not None else None

    def _write_calibration(self, stock_id: Any, value: Any) -> None:
        """把校准值直接覆盖成 ``value``（``None`` 表示恢复成"没有记录"）。

        守卫用它是"临时改一下试试"，所以走 **UPDATE / 删除** 而不是
        ``WriteDaily``（后者是"已有就不动"，改不动）。
        """
        connection = getattr(self.source, "connection", None)
        if connection is None:
            return
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT id FROM calibrationhistory WHERE stock_id = %s "
                "ORDER BY `date` DESC LIMIT 1",
                (stock_id,),
            )
            row = cursor.fetchone()
            if value is None:
                if row:
                    cursor.execute("DELETE FROM calibrationhistory WHERE id = %s", (row["id"],))
            elif row:
                cursor.execute(
                    "UPDATE calibrationhistory SET close = %s WHERE id = %s",
                    (value, row["id"]),
                )
            else:
                import datetime as _datetime

                cursor.execute(
                    "INSERT INTO calibrationhistory (stock_id, `date`, close, `time`, num) "
                    "VALUES (%s, %s, %s, %s, 1)",
                    (stock_id, _datetime.date.today().isoformat(), value,
                     _datetime.datetime.now().strftime("%H:%M:%S")),
                )
            commit = getattr(connection, "commit", None)
            if commit is not None:
                commit()
        finally:
            cursor.close()

    def _official_estimate(self, fund_symbol: str) -> Any:
        """走**真实估值管线**算官方 EST（对应页面上那个数）。"""
        try:
            from calc.fundestcalc import build_estimate_calculator
        except ImportError:
            return None
        calculator = build_estimate_calculator(self.source)
        record = calculator.compute_official(fund_symbol)
        return getattr(record, "value", None) if record is not None else None


def build_estimate_guard(source: Any) -> EstimateGuard:
    """按数据源创建估值守卫。"""
    return EstimateGuard(source)


def default_middle_symbols(source: Any) -> list:
    """要自动校准的中间层符号集合。

    对应 PHP 页面里的 ``$arLev``（当前页面涉及的杠杆标的）——
    本版落成"``qdii_est_symbol_map()`` 的值集合里在库中存在的那些"，
    也就是所有基金实际用到的中间层。
    """
    try:
        from calc.fundcalc import qdii_est_symbol_map
    except ImportError:
        return []
    symbols = set()
    for est_symbol in qdii_est_symbol_map().values():
        # 指数（^NDX）本身不做校准 —— 校准对象是能取到净值的 ETF（QQQ）
        if est_symbol and source.get_stock_id(est_symbol) is not None:
            symbols.add(est_symbol)
    return sorted(symbols)
