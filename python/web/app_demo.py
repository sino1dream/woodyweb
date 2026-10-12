"""演示服务器：不连数据库，用示例数据展示分组列表与基金详情页。

用途
----
想在导入真实库之前先看页面形态时用它。真实数据请改用 ``python -B web\\app.py``
（走 ``get_db()``，需要 ``python/auto/.env`` 配置正确）。

用法
----
    cd F:\\woody\\woodyweb\\python
    python -B web\\app_demo.py

然后访问：

* http://127.0.0.1:5001/fund/qdiilof       （QDII-LOF，26 只）
* http://127.0.0.1:5001/fund/qdiietf       （QDII-ETF，21 只）
* http://127.0.0.1:5001/fund/qdiilof/SZ161130
* http://127.0.0.1:5001/fund/qdiietf/SH513100

加实时数据源参数可以看到估值变化（对应 PHP 页面里由行情层自动填入的部分）：

* http://127.0.0.1:5001/fund/qdiilof/SZ161130?source=QQQ=560&source=CNY=7.1

示例数据的名称与价格都是占位值，只用于验证页面链路。
"""

from __future__ import annotations

import os
import sys

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(WEB_DIR)
for _path in (PYTHON_DIR, WEB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from web.app import create_app
from web.bootstrap import install_paths  # noqa: E402
from web.services.fund_list_service import FundListService  # noqa: E402
from web.services.fund_service import FundService  # noqa: E402

install_paths()

from core.stockdata import DailyRecord, FundProfile, MemoryFundDataSource  # noqa: E402


def build_demo_source() -> MemoryFundDataSource:
    """构造示例数据。

    三个符号分别覆盖两类分组和一条两级估值链路：

    * SZ161130（QDII-LOF，深圳 16 号段）：SZ161130 -> QQQ -> ^NDX；
    * SZ161125（QDII-LOF，深圳 16 号段）：SZ161125 -> SPY -> ^GSPC；
    * SH513100（QDII-ETF，上海 51 号段）：SH513100 -> QQQ。

    中间层（QQQ / SPY）与指数层的行情不用准备，``StockDataArrayBuilder`` 会按
    ``get_leverage_hedge_symbol()`` 自动补出来。
    """
    return MemoryFundDataSource(
        stocks={"SZ161130": "1", "SZ161125": "2", "SH513100": "3"},
        names={
            # 注意：SZ161130 是**易方达纳斯达克100**，不是"标普信息科技"
            # （标普信息科技是 SZ161128）。这里曾写错，已在
            # auto/README_nav_migration.md 里记录并加了名称核对机制。
            "SZ161130": "易方达纳斯达克100LOF",
            "SZ161125": "易方达标普500LOF",
            "SH513100": "国泰纳斯达克100ETF",
        },
        profiles={
            "SZ161130": FundProfile(
                symbol="SZ161130",
                stock_id="1",
                position=0.95,
                official_date="2026-01-02",
                cny_est=7.10,
                cny_current=7.19,
                is_lof=True,
                est_symbol="QQQ",
                est_netvalue=560.0,
            ),
            "SZ161125": FundProfile(
                symbol="SZ161125",
                stock_id="2",
                position=1.0,
                official_date="2026-01-02",
                cny_est=7.10,
                cny_current=7.19,
                is_lof=True,
                est_symbol="SPY",
                est_netvalue=700.0,
            ),
            "SH513100": FundProfile(
                symbol="SH513100",
                stock_id="3",
                position=0.98,
                official_date="2026-01-02",
                cny_est=7.10,
                cny_current=7.19,
                is_lof=False,
                est_symbol="QQQ",
                est_netvalue=560.0,
            ),
        },
        calibration={
            "1": [DailyRecord("2026-01-02", 21000.0)],
            "2": [DailyRecord("2026-01-02", 5900.0)],
            "3": [DailyRecord("2026-01-02", 480.0)],
        },
        netvalue={
            ("1", "2026-01-02"): 1.5234,
            ("2", "2026-01-02"): 1.8765,
            ("3", "2026-01-02"): 1.2,
        },
        hedges={
            ("SZ161130", None): 1.0,
            ("SZ161125", None): 1.0,
            ("SH513100", None): 1.0,
        },
    )


class _StaticFactory:
    """Flask 扩展要求工厂可调用，这里固定返回同一个服务实例。"""

    def __init__(self, service):
        self.service = service

    def __call__(self):
        return self.service


def main() -> None:
    source = build_demo_source()
    app = create_app(
        {"TESTING": False},
        fund_service_factory=_StaticFactory(FundService(source)),
        fund_list_service_factory=_StaticFactory(FundListService(source)),
    )

    host = os.getenv("PALMMICRO_DEMO_HOST", "127.0.0.1")
    port = int(os.getenv("PALMMICRO_DEMO_PORT", "5001"))
    print("演示服务（示例数据，未连数据库）已启动：")
    print("  QDII-LOF 列表：http://%s:%d/fund/qdiilof" % (host, port))
    print("  QDII-ETF 列表：http://%s:%d/fund/qdiietf" % (host, port))
    print("  基金详情：")
    print("    http://%s:%d/fund/qdiilof/SZ161130" % (host, port))
    print("    http://%s:%d/fund/qdiilof/SZ161125" % (host, port))
    print("    http://%s:%d/fund/qdiietf/SH513100" % (host, port))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
