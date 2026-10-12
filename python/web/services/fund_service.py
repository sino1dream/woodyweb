"""基金估值 Web 服务。"""

from __future__ import annotations

from typing import Any, Mapping

from web.bootstrap import install_paths

install_paths()

from calc.fundcalc import PalmmicroFundCalculator
from core.stockdata import FundDataSource, MySqlFundDataSource, StockDataArrayBuilder, stock_get_symbol
from web.db import get_db


class FundNotFoundError(LookupError):
    """基金代码不存在。"""


class FundService:
    """把基金数据适配器封装成 Web 可用的服务。"""

    def __init__(self, source: FundDataSource):
        self.source = source

    def get_fund(self, symbol: str, sources: Mapping[str, float] | None = None) -> dict[str, Any]:
        """读取单个基金代码的估值数据。"""
        normalized = stock_get_symbol(symbol)
        profile = self.source.get_fund_profile(normalized)
        if profile is None:
            raise FundNotFoundError(normalized)

        data = StockDataArrayBuilder(self.source).build(normalized)
        config = data.get(normalized, {})
        estimate: float | None
        try:
            estimate = PalmmicroFundCalculator(data).est_net_value(normalized, sources or None)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            # 数据不完整时仍让页面可用，只是不显示估值。
            estimate = None

        return {
            "symbol": normalized,
            "name": self.source.get_stock_name(normalized) or normalized,
            "stock_id": profile.stock_id,
            "position": profile.position,
            "official_date": profile.official_date,
            "cny_est": profile.cny_est,
            "estimate": estimate,
            "config": config,
        }


def default_fund_service_factory() -> FundService:
    """创建使用当前请求数据库连接的基金服务。"""
    return FundService(MySqlFundDataSource(get_db()))
