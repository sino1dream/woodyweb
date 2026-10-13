"""投资组合页（``woody/res/myportfoliocn.php``）的路由。

对应 PHP ``woody/res/php/_myportfolio.php`` 的 ``EchoAll()``：先渲染参考数据
段落，再渲染**投资组合**段落（``EchoPortfolioParagraph``），最后是合并段落。
本页只迁中间那一段 —— 参考数据段落已有 ``/qdii`` 与详情页复用，
合并段落（``_transEchoMergeParagraph``）是同一分组下多笔交易的合并视图，
优先级更低。

## 为什么是"演示数据"而不是真实持仓

PHP 那边的数据来自三张表：``stockgroup``（按 member 的分组）、
``stockgroupitem``（分组内的标的与预统计量）、``stocktransaction``
（每一笔买卖）。**演示库里这三张表都不存在**，而账号登录体系
（``$acct->Auth()``）用户要求暂缓。所以这里用一份**写死的样例数据集**，
把 ``portfolioparagraph.php`` 的每个分支都走到：

* 六个 LOF 套利符号中的三个（演示库里只有这三个）；
* ``KWEB`` 的跨标的套利测试列；
* ``SH600104`` / ``TLT`` 的偏离量；
* ``hf_ES`` 的合约乘数；
* 一个没有测试列的普通标的（六格行）；
* 一个清仓标的（两格行）；
* ``fx_susdcnh`` 的美元折算行（必须排在 QDII 之后）。

价格是**样例值**，不对应任何一天的行情。页面顶部有明确标注。
"""

from __future__ import annotations

from flask import Blueprint, render_template

from web.bootstrap import install_paths

install_paths()

from core.stocktrans import MyStockTransaction  # noqa: E402
from web.services.portfolio_service import (  # noqa: E402
    PortfolioCnyTotal,
    build_portfolio_table,
    sort_trans_by_symbol,
)

portfolio_bp = Blueprint("portfolio", __name__)

#: 分组内条目（``stockgroupitem`` 的等价物）。KWEB 的套利测试列要跨标的取值，
#: 见 ``portfolio_service.get_arbitrage_test_str()``。
DEMO_GROUP_ITEMS = [
    {"stock_id": "1", "quantity": "10000"},   # SZ161125
    {"stock_id": "2", "quantity": "5000"},    # SZ161130
    {"stock_id": "3", "quantity": "20000"},   # SZ162411
    {"stock_id": "4", "quantity": "12000"},   # SH600104
    {"stock_id": "5", "quantity": "500"},     # TLT
    {"stock_id": "6", "quantity": "200"},     # KWEB
    {"stock_id": "7", "quantity": "25"},      # hf_ES
    {"stock_id": "8", "quantity": "1000"},    # SH513100
    {"stock_id": "9", "quantity": "0"},       # QQQ（已清仓）
    {"stock_id": "10", "quantity": "100000"}, # fx_susdcnh
]

#: 样例持仓。``price`` 用字符串，与 PHP 的 ``strPrice`` 一致
#: （用来验证 ``GetValue()`` 走 ``floatval()``）。
DEMO_HOLDINGS = [
    # 符号          持仓      均价      现价      仓位    名称
    ("SZ161125", 10000, "1.800", "2.100", 0.98, "易方达标普500LOF"),
    ("SZ161130", 5000, "1.500", "1.700", 0.95, "易方达纳斯达克100LOF"),
    ("SZ162411", 20000, "0.700", "0.650", 0.92, "华宝油气LOF"),
    ("SH600104", 12000, "15.00", "16.20", 1.00, "上汽集团"),
    ("TLT", 500, "95.00", "98.50", 1.00, "iShares 20+年期美国国债ETF"),
    ("KWEB", 200, "30.00", "33.80", 1.00, "KraneShares中概互联网ETF"),
    ("hf_ES", 25, "5000.0", "5200.0", 1.00, "标普500指数期货"),
    ("SH513100", 1000, "1.800", "2.100", 1.00, "国泰纳斯达克100ETF"),
    ("QQQ", 0, "0.0", "400.00", 1.00, "Invesco QQQ Trust"),
    ("fx_susdcnh", 100000, "7.100", "7.120", 1.00, "美元离岸人民币"),
]

#: ``GetStockHedge()`` 的演示替身。LOF 套利的对冲比率接近 1
#: （一份 LOF 换一篮子股票），这里统一按 1.0 算。
DEMO_HEDGE = 1.0


class _DemoRef:
    """样例引用对象 —— 只实现段落用到的几个取值方法。

    **不连库、不联网**：价格是上面 :data:`DEMO_HOLDINGS` 里写死的样例值。
    真实页面里这个角色由 ``MysqlReference`` 扮演（见 ``auto/ref/refobj.py``）。
    """

    def __init__(self, symbol, price, position, name):
        self._symbol = symbol
        self._price = price
        self._position = position
        self._name = name

    def get_symbol(self):
        return self._symbol

    def get_stock_id(self):
        return self._symbol

    def get_price(self):
        return self._price

    def get_position(self):
        return self._position

    def get_val(self):
        """``fx_susdcnh`` 那行的汇率（``GetVal()`` 即当前价）。"""
        return self._price

    def get_price_display(self, disp=None, prev=None, precision=None):
        from sql.historytable import format_number_display

        return format_number_display(disp)

    def get_percentage_text(self, divisor=None, dividend=None):
        # 样例：相对均价涨 12.5%
        return "12.5%"

    def is_symbol_a(self):
        return self._symbol.startswith(("SH", "SZ"))

    def is_symbol_h(self):
        return self._symbol.startswith("HK")


def _demo_transactions():
    """把 :data:`DEMO_HOLDINGS` 变成 ``MyStockTransaction`` 列表。"""
    trans_list = []
    for symbol, shares, cost, price, position, name in DEMO_HOLDINGS:
        ref = _DemoRef(symbol, price, position, name)
        trans = MyStockTransaction(ref, "demo")
        # 一笔买入 + 一笔手续费，让成本里含 fees（见 stocktrans.add_sql_transaction）
        trans.add_transaction(shares, float(cost) * shares + 5.0)
        trans_list.append(trans)
    return trans_list


def _demo_names():
    """符号 → 中文名，填首格 tooltip（``SqlGetStockName()`` 的替身）。"""
    return {symbol: name for symbol, _, _, _, _, name in DEMO_HOLDINGS}


@portfolio_bp.route("/portfolio")
def portfolio_page():
    """投资组合段落（``php/ui/portfolioparagraph.php``）。"""
    cny = PortfolioCnyTotal()
    table = build_portfolio_table(
        sort_trans_by_symbol(_demo_transactions()),
        cny=cny,
        name_of=lambda symbol: _demo_names().get(symbol, ""),
        hedge_of=lambda symbol, stock_id: DEMO_HEDGE,
        items_of=lambda group_id: DEMO_GROUP_ITEMS,
        symbol_of=lambda stock_id: stock_id,
    )
    return render_template(
        "portfolio.html",
        table=table,
        cny_total=cny.value,
        holding_count=len(DEMO_HOLDINGS),
    )
