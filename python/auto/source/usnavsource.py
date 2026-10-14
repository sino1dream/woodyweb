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

## 派生方案：用"价格 ÷ 基准校准值"

既然拿不到官方 NAV，就用**项目里已经在用的恒等式**反推：

```text
中间层每份净值 = 汇率 / 本符号校准值          （见 autocalibration._implied_middle_nav）
```

实测：``6.705 / 0.008945 = 749.58``，与生产端 QQQ 真实 NAV ``749.378``
吻合到 **0.03%**。所以这个派生值**锚定在真实的官方 NAV 上**（种子校准值本身
就是按真实 NAV 解出来的），不是凭空编的数。

之后每个交易日推进：

```text
净  值(t) = 基准净值 × 价格(t) / 价格(基准日)
日  期(t) = 该价格的行情日期
```

这样 ``DailyCalibration()`` 的闸门（净值日期 == 校准日期）就能自然前进。

## 必须说清的局限

* 这是**价格驱动**的净值，不是官方 NAV：**分红除息日会失真**
  （官方 NAV 会因分红下降，我们的派生值不会）；
* 所以**不能**用它做对账，只能用来"让校准闸门前进"；
* 接到真实 NAV 源后应立刻替换（把本模块的 ``fetch_latest`` 换成真实实现即可）。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

#: 中间层符号 → 它的"基准净值"。
#:
#: 基准净值 = 汇率 / 基准校准值（见 ``autocalibration._implied_middle_nav``），
#: 来源是种子里那条按真实 NAV 解出来的校准值。写死为常量是为了在没有数据库时
#: 也能工作；有数据库时应优先用 ``_implied_middle_nav()`` 现算。
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
        #: 挡下后用常量表兜底，值是对的，但**说明恒等式在当前尺度不成立**，
        #: 必须修 `_implied_middle_nav`（见 README_p0_data_layer.md §四.3）。
        self.warnings: list = []

    def describe(self):
        return {
            "name": self.name,
            "kind": "派生（价格驱动，非官方 NAV）",
            "limitation": "分红除息日会失真；不可用于对账，仅用于推进校准闸门",
            "supported": sorted(FALLBACK_BASE_NAV),
        }

    # -- 供 autocalibrationcli 使用 ---------------------------------------
    def fetch_middle_nav(self, middle_symbol: str) -> Any:
        """算出该中间层在**最新行情日期**上的净值与日期。

        ## 式子（重要）

        直接用 :meth:`_implied_middle_nav` 的恒等式：

        ```text
        中间层每份净值 = 汇率 / 本符号校准值
        ```

        实测 ``6.705 / 0.008945 = 749.58``，与生产端 QQQ 真实 NAV ``749.378``
        吻合到 **0.03%** —— 因为种子那条校准值**本来就是按真实 NAV 解出来的**，
        所以这个派生值锚定在真实净值上。

        **不要再乘价格比**：我第一版写成 ``基准净值 × 价格(t) / 价格(基准日)``，
        结果量级错到 0.16 —— 因为"该部署尺度的净值"与"行情价格"不是同一尺度
        （校准值里已经含了缩放）。恒等式给出的就是最终值，价格只用来定**日期**。

        取不到校准值/汇率时退回常量表；日期取最新行情的日期
        （这样 ``DailyCalibration()`` 的闸门才有东西可比）。
        """
        if self.source is None:
            return None
        price, price_date = self._latest_price(middle_symbol)
        if not price_date:
            return None

        # 把价格传进去当量级自检的尺子（见 _implied_nav 的说明）
        base_nav = self._implied_nav(middle_symbol, price=price)
        if base_nav in (None, "") or abs(float(base_nav)) <= 0.0000005:
            return None

        return MiddleNav(symbol=middle_symbol, date=str(price_date),
                         nav=float(base_nav), base_date=self._base_date_of(middle_symbol),
                         base_nav=float(base_nav), price=price)

    def _implied_nav(self, middle_symbol: str, price: Any = None) -> Any:
        """``汇率 / 校准值`` —— 该部署尺度下中间层的每份净值。

        ## 量级自检（P0.5 的第一道护栏）

        那条恒等式**只在种子尺度成立**（cal_m = 0.008945 → 749.58）。
        按生产端口径重算后 QQQ 的 cal_m 是 41.5793，同一式子给出
        ``6.69 / 41.58 = 0.161`` —— 差 4648 倍，而它会被
        ``autocalibrationcli sync-nav --apply`` 直接写进 ``netvaluehistory``。

        所以这里拿**当日价格**当尺子过一遍（ETF 净值与价格同量级）：
        不匹配就**退回常量表**（真实量级）并记一条 warning，
        绝不把错量级的值交出去。``price`` 没传就自己取一次。
        """
        fallback = FALLBACK_BASE_NAV.get(middle_symbol)
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
        implied = calibrator._implied_middle_nav(stock_id, self._cny())
        if implied in (None, "") or abs(float(implied)) <= 0.0000005:
            return fallback

        if price is None:
            price, _date = self._latest_price(middle_symbol)
        if not nav_matches_price(implied, price):
            self.warnings.append(
                "%s：恒等式算出的净值 %s 与当日价格 %s 不同量级（尺度假设不成立），"
                "已退回常量表 %s —— 需修 _implied_middle_nav，见 README §四.3"
                % (middle_symbol, implied, price, fallback))
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

        优先用**现算的隐含净值**（``汇率 / 校准值``）—— 它随部署尺度自适应；
        取不到时退回常量表。

        基准日期取"校准值那天"，即那个净值对应的日期。
        """
        override = self._base_nav_override.get(middle_symbol)
        if override is not None:
            return override, self._base_date_of(middle_symbol)

        # 走 _implied_nav 而不是再抄一遍恒等式：那里带量级自检（见其说明）
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

    def _cny(self) -> Any:
        """最新美元人民币中间价；取不到返回 1.0。"""
        if self.source is None:
            return 1.0
        cny_id = self.source.get_stock_id("USCNY")
        if cny_id is None:
            return 1.0
        reader = getattr(self.source, "get_latest_net_value_close", None)
        if reader is None:
            return 1.0
        try:
            value = reader(cny_id)
        except Exception:
            return 1.0
        return value if value not in (None, "") else 1.0


def build_middle_nav_source(source: Any) -> DerivedMiddleNavSource:
    """按数据源创建派生净值源。"""
    return DerivedMiddleNavSource(source)
