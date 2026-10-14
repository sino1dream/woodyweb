"""美股 ETF「净值」的派生源（中间层净值推进器）。

## 为什么是"派生"而不是"真实 NAV"

本项目实测过一圈免费信源，**没有一个能稳定给出美股 ETF 的官方 NAV**：

==============================================  ==========================================
信源                                              实测结果
==============================================  ==========================================
Invesco ``dng-api``（页面 JS 调的那个）            **HTTP 406**（需要 JS 挑战）
Invesco product 页 HTML                           200，但 NAV 是**前端 JS 后填**的，
                                                  初始 HTML 里没有 NAV 数字
Invesco 持仓 csv 下载                              **HTTP 406**
新浪美股 ``gb_qqq``                               200，**36 个字段里没有 NAV**
                                                  （``[30]`` 净资产恒为 0）
东财 ``push2.eastmoney.com`` 美股                 **RemoteDisconnected**（反爬）
东财 ``push2his`` kline                           200（但只有价格，没有 NAV）
``yfinance``（0.2.28，本机已装）                  **网络层被拦**（No price data found）
AKShare ``fund_*``                                全是中国基金（东财/新浪/深交所/雪球蛋卷）
AKShare ``fund_xq``（雪球 ``unit_nav``）          ``danjuanfunds.com``，中国基金
AKShare ``qdii_jsl``（集思录）                    中国**上市** QDII 基金（161130 那种），
                                                  不是它跟踪的美股 ETF
==============================================  ==========================================

所以**没有免费可用的真实 NAV 源**（生产端能用 Invesco，说明它有我们能用的
合作/带 JS 的通道）。

## 派生方案：用"当日收盘价"当净值（P0.5 定稿）

```text
净值(t) = 当日收盘价(t)            日期(t) = 该价格的行情日期
```

ETF 的净值与交易价格只差千分位 —— 做市商的一级申购赎回套利会把折溢价
抹平，生产端自己也是这么用的（``netvalueclosecn.php`` 上美股 ETF 的
"单位净值"就是收盘价）。

## 为什么不用「汇率 / 校准值」

calibration 那条恒等式 ``净值 = 汇率 / cal_m`` **只在种子尺度成立**
（``6.705 / 0.008945 = 749.58``，与生产端 QQQ 真实 NAV ``749.378``
吻合到 0.03%），因为那条种子校准值**本来就是按真实 NAV 解出来的**。
把校准口径重算成生产尺度（QQQ 的 ``cal_m = 41.5793``）后，同一个式子
给出 ``6.69 / 41.58 = 0.161`` —— 差 4648 倍，而且会被 ``sync-nav``
直接写进 ``netvaluehistory``。

根因：``cal_m`` 随部署尺度漂移，净值不漂移。**任何把 ``cal_m`` 当分母的
做法都会随尺度坏掉**，所以新口径里一个 ``cal_m`` 都不出现。

这样 ``DailyCalibration()`` 的闸门（净值日期 == 校准日期）就能自然前进。

## 必须说清的局限

* 这是**价格驱动**的净值，不是官方 NAV：误差来自 ETF 的**折溢价**
  （千分位级；分红除息时 NAV 与价格同额下降，两者同步，不像开放式基金
  那样错位）；
* 所以**不能**用它做精确到分位以下的对账，只能用来"让校准闸门前进"；
* 接到真实 NAV 源后应立刻替换（把本模块的 ``fetch_latest`` 换成真实实现即可）。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

#: 中间层符号 → 它的"兜底净值"。
#:
#: 只在**行情表里查不到价格**时用（没有真实数据也不让链路断掉）。
#: 数值取自生产端 QQQ/SPY 的真实 NAV 量级，不是按某个恒等式算出来的 ——
#: 之前这里放的是"汇率 / 校准值"，那条式子会随部署尺度漂移（见模块 docstring）。
FALLBACK_BASE_NAV = {
    "QQQ": 749.58,
    "SPY": 769.63,
}


@dataclass
class MiddleNav:
    """一条中间层净值。"""

    symbol: str
    date: str
    nav: float
    base_date: Any = None
    base_nav: Any = None
    price: Any = None
    derived: bool = True

    def describe(self) -> str:
        if self.derived:
            return "%s %s 净值=%s（派生：基准 %s×%s 的价格 %s）" % (
                self.symbol, self.date, round(self.nav, 6),
                self.base_nav, self.base_date, self.price)
        return "%s %s 净值=%s" % (self.symbol, self.date, round(self.nav, 6))


class DerivedMiddleNavSource:
    """从"基准净值 + 价格变化"派生出中间层的每日净值。

    刻意实现成 ``navsource.NavSource`` 的形状（``name`` / ``fetch_latest(code)``），
    这样以后换成真实 NAV 源时，上层代码一行都不用改。
    """

    #: 数据源标识（与 ``navsource`` 的约定一致）
    name = "us_etf_derived"

    def __init__(self, source: Any = None, base_nav: Any = None):
        self.source = source
        self._base_nav_override = base_nav or {}
        #: 派生过程中被"量级自检"挡下的记录（只读，给运维看）。
        #: 挡下后用常量表兜底，值是对的，但**说明净值口径与价格不同源**，
        #: 要查 ``_implied_nav`` 上游（见 README_p0_data_layer.md §四.3）。
        self.warnings: list = []

    def describe(self):
        return {
            "name": self.name,
            "kind": "派生（当日收盘价当净值，非官方 NAV）",
            "limitation": "误差来自 ETF 折溢价（千分位）；不可用于精确对账，"
                          "仅用于推进校准闸门",
            "supported": sorted(FALLBACK_BASE_NAV),
        }

    # -- 供 autocalibrationcli 使用 ---------------------------------------
    def fetch_middle_nav(self, middle_symbol: str) -> Any:
        """算出该中间层在**最新行情日期**上的净值与日期。

        ## 口径（P0.5 定稿，README §四.3 坑②）

        ```text
        净值(t) = 当日收盘价(t)        日期(t) = 该价格的行情日期
        ```

        之所以直接用价格：ETF 的净值与交易价只差千分位（一级申赎套利抹平
        折溢价），而**任何把 ``cal_m`` 当分母的式子都会随部署尺度漂移** ——
        旧口径 ``汇率 / cal_m`` 在种子尺度下（``6.705/0.008945 = 749.58``）
        恰好等于真实净值，改成生产尺度（QQQ 的 ``cal_m = 41.5793``）后给出
        ``0.161``，差 4648 倍。价格是尺度无关的，所以这条式子不会坏。

        **不要再乘价格比**：第一版写成 ``基准净值 × 价格(t)/价格(基准日)``，
        那是把"已经是对的价格"又按基准日的比例缩放了一遍。
        """
        if self.source is None:
            return None
        price, price_date = self._latest_price(middle_symbol)
        if not price_date:
            return None

        # 把价格传进去当量级自检的尺子（见 _implied_nav 的说明）
        nav = self._implied_nav(middle_symbol, price=price)
        if nav in (None, "") or abs(float(nav)) <= 0.0000005:
            return None

        return MiddleNav(symbol=middle_symbol, date=str(price_date),
                         nav=float(nav), base_date=str(price_date),
                         base_nav=float(nav), price=price)

    def _implied_nav(self, middle_symbol: str, price: Any = None) -> Any:
        """该中间层的每份净值 —— **当日收盘价**（详见 :meth:`fetch_middle_nav`）。

        优先 ``realtimequote`` 实时快照，再退 ``stockhistory`` 收盘价
        （与 ``AutoCalibrator._implied_middle_nav`` 同一优先级）。

        ## 量级自检（P0.5 的第一道护栏）

        价格来自行情表，正常情况下自检恒真（净值就是它自己）。留着是因为
        ``DerivedMiddleNavSource`` 还可能被喂 ``base_nav`` 覆盖值，以及后续
        换成真实 NAV 源后，这里就是挡"量级差几百倍还不报错"的最后一道闸门。
        取不到价格时退回常量表（``749.58`` / ``769.63``，真实量级），
        绝不把错量级的值交出去。
        """
        fallback = FALLBACK_BASE_NAV.get(middle_symbol)
        if price is None:
            price, _date = self._latest_price(middle_symbol)
        if self.source is None:
            return fallback

        try:
            # 惰性导入：autocalibration 反过来也会用到本模块（避免循环 import）
            from updater.autocalibration import AutoCalibrator, nav_matches_price
        except ImportError:
            return fallback

        stock_id = self.source.get_stock_id(middle_symbol)
        if stock_id is None:
            return fallback
        calibrator = AutoCalibrator(self.source)
        implied = calibrator._implied_middle_nav(stock_id, None,
                                                 symbol=middle_symbol)
        if implied in (None, "") or abs(float(implied)) <= 0.0000005:
            return fallback

        if not nav_matches_price(implied, price):
            self.warnings.append(
                "%s：派生净值 %s 与当日价格 %s 不同量级（尺度假设不成立），"
                "已退回常量表 %s —— 见 README §四.3" % (
                    middle_symbol, implied, price, fallback))
            return fallback
        return implied

    # -- 取数 -------------------------------------------------------------
    def _latest_price(self, symbol: str) -> Any:
        """最新的（价格, 日期）：优先实时快照，再退行情收盘价。"""
        quote_reader = getattr(self.source, "get_stored_quote", None)
        if quote_reader is not None:
            try:
                quote = quote_reader(symbol)
            except Exception:
                quote = None
            if quote is not None and getattr(quote, "price", None):
                return quote.price, getattr(quote, "date", None)

        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            return None, None
        price_reader = getattr(self.source, "get_latest_history_close", None)
        date_reader = getattr(self.source, "get_latest_history_date", None)
        price = price_reader(stock_id) if price_reader else None
        date = date_reader(stock_id) if date_reader else None
        return price, date

    def _price_on(self, symbol: str, date: Any) -> Any:
        """某个日期的收盘价（用来算价格变化）。"""
        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            return None
        reader = getattr(self.source, "get_history_close", None)
        if reader is None:
            return None
        try:
            return reader(stock_id, date)
        except Exception:
            return None

    def _base(self, middle_symbol: str):
        """基准（净值, 日期）。

        基准净值现在就是**当日收盘价**（见 :meth:`fetch_middle_nav`）；
        取不到时退回常量表。``_base_nav_override`` 优先，方便手工指定。
        """
        override = self._base_nav_override.get(middle_symbol)
        if override is not None:
            return override, None

        # 走 _implied_nav 而不是再抄一遍取价逻辑：那里带量级自检
        if self.source is not None:
            implied = self._implied_nav(middle_symbol)
            if implied not in (None, "") and abs(float(implied)) > 0.0000005:
                return implied, self._base_date_of(middle_symbol)

        return FALLBACK_BASE_NAV.get(middle_symbol), self._base_date_of(middle_symbol)

    def _base_date_of(self, middle_symbol: str) -> Any:
        """基准日 = 校准值所在的那一天。"""
        if self.source is None:
            return None
        stock_id = self.source.get_stock_id(middle_symbol)
        if stock_id is None:
            return None
        reader = getattr(self.source, "get_calibration_now", None)
        if reader is None:
            return None
        try:
            record = reader(stock_id)
        except Exception:
            return None
        return getattr(record, "date", None) if record is not None else None



def build_middle_nav_source(source: Any) -> DerivedMiddleNavSource:
    """按数据源创建派生净值源。"""
    return DerivedMiddleNavSource(source)
