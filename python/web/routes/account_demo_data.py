"""账户 / 分组 / 交易三个演示页共用的内存样例数据与引用对象。

真实页面（``woody/res/php/_mystockgroup.php`` 的 mystockgroup 分支、
``_mystocktransaction.php``、``_stockaccount.php``）的数据来自
``stockgroup`` / ``stockgroupitem`` / ``stocktransaction`` 三张表加上登录态
取出的会员与分组。演示库里这三张表都没有、账号体系也暂缓，所以这里写死
一份样例，让三个页面都能用**已迁的** ``transaction_service`` /
``money_service`` 把渲染逻辑演出来。

模块只放数据、引用对象与几个查询助手，**不碰 Flask** —— 三个路由各自负责
渲染。``install_paths()`` 照 ``account.py`` 的约定先调，保证随后能 import
``core``（``auto`` 目录）。
"""

from web.bootstrap import install_paths

install_paths()

from core.stockgroup import MyStockGroup, StockGroup  # noqa: E402


#: 演示用的引用对象参数：符号 → (现价, 精度, 名称, 是否 A 类基金)。
#:
#: **是否 A 类基金**这一位不能省：``_echoTransactionTableItem()`` 里
#: ``IsFundA() ? 4 : GetPrecision()`` 决定价格列几位小数，分组 2 里的
#: ``SZ161125`` 就是靠它显示成四位（基金净值口径）。
DEMO_REF_ARGS = {
    "SH600104": ("16.20", 2, "上汽集团", False),
    "HK00700": ("600.00", 2, "腾讯控股", False),
    "SZ161125": ("2.100", 4, "易方达标普500LOF", True),
    "TLT": ("98.50", 2, "iShares 20+年期美国国债ETF", False),
    "KWEB": ("33.80", 2, "KraneShares中概互联网ETF", False),
    # 资金汇总段落要的两个汇率（``new CnyReference('USCNY')`` / ``'HKCNY'``）
    "fx_susdcnh": ("7.120", 4, "美元离岸人民币", False),
    "fx_hkdcnh": ("0.920", 4, "港币离岸人民币", False),
}

#: 分组名（``stockgroup`` 表的等价物）。
DEMO_GROUP_NAMES = {
    "1": "A股与港股",
    "2": "QDII与美股",
}

#: 分组内条目（``stockgroupitem`` 的等价物）。预统计量的口径见
#: :mod:`core.stockgroup` 第三节 —— 只有 ``record`` 大于 0 才进表，
#: 所以"预留了标的但一笔没买"的分组不会出现那一行。
#:
#: 三个币种**刻意分散**在不同分组里：分组 1 是人民币 + 港币，
#: 分组 2 是人民币 + 美元（QDII 的 ``SZ`` 前缀按 A 股记，进人民币桶），
#: 于是"全部"那一行三个桶都非零、三行全出。
DEMO_GROUP_RECORDS = {
    "1": [
        {"stock_id": "SH600104", "record": 1, "quantity": 12000, "cost": 180018.00},
        {"stock_id": "HK00700", "record": 1, "quantity": 100, "cost": 55050.00},
    ],
    "2": [
        {"stock_id": "SZ161125", "record": 2, "quantity": 25000, "cost": 48481.50},
        {"stock_id": "TLT", "record": 1, "quantity": 500, "cost": 47507.00},
        {"stock_id": "KWEB", "record": 2, "quantity": 300, "cost": 9109.70},
    ],
}

#: 交易明细（``stocktransaction`` 的等价物）。
#:
#: ``group_id`` 这一个键是**演示页自己加的** —— 真实的表里只有
#: ``groupitem_id``，分组号要回头查 ``stockgroupitem`` 才知道。页面用它
#: 切分组（``?groupid=``），省掉一次连表。
#:
#: ``filled`` 带时分秒：``GetSqlTransactionDate()`` 取第一个空格之前的部分，
#: 留着时间是为了让那个 ``strstr(..., true)`` 真的有东西可切。
DEMO_TRANSACTION_RECORDS = [
    # -- 分组 1：A 股与港股，备注里没有"申购"，于是没有校准链接 ----
    {"id": "101", "group_id": "1", "groupitem_id": "1",
     "stock_id": "SH600104", "filled": "2026-10-09 09:35:00",
     "quantity": "12000", "price": "15.00", "fees": "18.00", "remark": "建仓"},
    {"id": "102", "group_id": "1", "groupitem_id": "2",
     "stock_id": "HK00700", "filled": "2026-10-10 10:02:00",
     "quantity": "100", "price": "550.00", "fees": "50.00", "remark": ""},
    # -- 分组 2：三条"申购"各走一支 --------------------------------
    {"id": "201", "group_id": "2", "groupitem_id": "3",
     "stock_id": "SZ161125", "filled": "2026-10-11 14:20:00",
     "quantity": "20000", "price": "1.8000", "fees": "5.00", "remark": "申购"},
    {"id": "202", "group_id": "2", "groupitem_id": "4",
     "stock_id": "TLT", "filled": "2026-10-12 21:31:00",
     "quantity": "500", "price": "95.00", "fees": "7.00", "remark": "申购"},
    {"id": "205", "group_id": "2", "groupitem_id": "3",
     "stock_id": "SZ161125", "filled": "2026-10-13 09:35:00",
     "quantity": "5000", "price": "2.0950", "fees": "1.50", "remark": "申购"},
    # 备注里没有"申购" → 只有清空链接
    {"id": "203", "group_id": "2", "groupitem_id": "5",
     "stock_id": "KWEB", "filled": "2026-10-13 09:45:00",
     "quantity": "200", "price": "30.00", "fees": "6.60", "remark": "打新"},
    # 空备注 → 一个链接都没有（``strlen($strRemark) > 0`` 守卫）
    {"id": "204", "group_id": "2", "groupitem_id": "5",
     "stock_id": "KWEB", "filled": "2026-10-13 10:05:00",
     "quantity": "100", "price": "31.00", "fees": "3.10", "remark": ""},
]

#: ``netvalue`` 表演示数据：``(stock_id, 日期) → 净值``。
#:
#: QDII 走 ``GetClosePrev``（上一交易日）、其余走 ``GetClose``（当日），
#: 但**演示数据里两者给同一个值** —— 那一段的分支由传入的 ``prev`` 参数
#: 决定，逐例对照脚本已经逐个比过，页面上不需要再靠数据区分。
#:
#: 注意最后一条：``SZ161125`` 在 ``2026-10-13`` 的净值与成交价**相同**
#: （``2.0950``），于是那一行**没有**校准链接 —— 这是 PHP 松散比较
#: ``$strNetValue != $strPrice`` 的现场。
DEMO_NET_VALUES = {
    ("SZ161125", "2026-10-11"): "2.0950",
    ("SZ161125", "2026-10-13"): "2.0950",
    ("TLT", "2026-10-12"): "98.50",
}


class _DemoRef:
    """样例引用对象 —— 只实现交易/资金两个段落用到的几个取值方法。

    **不连库、不联网**：价格是 :data:`DEMO_REF_ARGS` 里写死的值。真实
    页面里这个角色由 ``MysqlReference`` 扮演（见 ``auto/ref/refobj.py``）。
    """

    def __init__(self, symbol, price="0.00", precision=2, name="", fund_a=False):
        self._symbol = symbol
        self._price = price
        self._precision = precision
        self._name = name
        self._fund_a = fund_a

    def get_symbol(self):
        return self._symbol

    def get_stock_id(self):
        return self._symbol

    def get_display(self):
        return self._symbol

    def get_price(self):
        return self._price

    def get_precision(self):
        return self._precision

    def get_price_display(self, disp=None, prev=None, precision=None):
        from sql.historytable import format_number_display

        return format_number_display(
            disp, self._precision if precision is None else precision)

    def is_fund_a(self):
        return self._fund_a

    def is_symbol_a(self):
        return self._symbol.startswith(("SH", "SZ"))

    def is_symbol_h(self):
        return self._symbol.startswith("HK")

    def is_sina_future_cn(self):
        """演示数据里没有国内期货（``hf_AU0`` 那一族）。

        留着这个方法是因为 ``StockGroup.OnStockTransaction()`` 会调它
        —— 返回 ``False`` 时外盘期货（``hf_ES``）落美元桶，与 PHP 一致。
        """
        return False


def _ref_of(symbol):
    """``ref_builder``：``StockGetReference($strSymbol)`` 的替身。

    符号不在 :data:`DEMO_REF_ARGS` 里时给一份全零的引用对象 —— 与 PHP
    建不出引用对象时"这笔交易不出现在表里"不同，这里宁可显示一行 0，
    好让人一眼看出是数据缺了而不是代码跳过了。
    """
    price, precision, name, fund_a = DEMO_REF_ARGS.get(
        symbol, ("0.00", 2, "", False))
    return _DemoRef(symbol, price, precision, name, fund_a)


def _demo_net_value(stock_id, date, prev=False):
    """``net_value_of``：按 ``(stock_id, 日期)`` 查净值，查不到回 ``None``。

    PHP 的 ``$net_sql->GetClose()`` 查不到时返回 ``false``，于是备注格里
    **仍然**出校准链接、只是 ``netvalue=`` 后面是空串。``None`` 与
    ``false`` 在 :func:`web.services.transaction_service._php_str` 眼里
    是同一个东西，所以这里按 PHP 的形态给 ``None``。
    """
    return DEMO_NET_VALUES.get((stock_id, date))


def _demo_groups():
    """按 :data:`DEMO_GROUP_RECORDS` 建分组，末尾追加一个"全部"行。

    末尾那个"全部"是 PHP ``_MyPortfolio extends StockGroup`` 的等价物：
    ``_echoPortfolio()`` 对每个分组的每笔交易调一次
    ``$portfolio->OnStockTransaction($trans)``，于是它自己是各分组之和。

    用 :class:`~core.stockgroup.StockGroup`（而不是
    :class:`~core.stockgroup.MyStockGroup`）是因为它**没有**
    ``group_id`` —— ``get_group_id()`` 返回 ``False``，资金汇总段落
    据此把首格显示成 ``全部`` 而不是一个分组链接。
    """
    groups = []
    total = StockGroup()
    for group_id, records in DEMO_GROUP_RECORDS.items():
        group = MyStockGroup(group_id, records=records, ref_builder=_ref_of)
        groups.append(group)
        for trans in group.get_stock_transaction_array():
            total.on_stock_transaction(trans)
    groups.append(total)
    return groups


def _demo_single_group(group_id):
    """单个分组的 ``MyStockGroup``（不含"全部"行）。

    对应 ``_mystockgroup.php`` 的 mystockgroup 分支：那里只对**选中的那一个**
    分组调 ``EchoMoneyParagraph($group, ...)``，所以资金汇总表只有这一组、
    没有末尾的"全部"行。``_demo_groups()`` 才是带"全部"的版本（给
    ``/account`` 用）。
    """
    records = DEMO_GROUP_RECORDS.get(group_id, [])
    return MyStockGroup(group_id, records=records, ref_builder=_ref_of)


def _demo_transaction_records(group_id, symbol=None):
    """筛选交易明细样例：按分组（必选）+ 可选按标的。

    对应 ``_mystocktransaction.php`` 的两种形态 —— 带 ``?symbol=`` 时只看
    该标的的交易，否则看整组的交易。两组过滤都只是演示数据的切片，
    真实页面靠 ``SqlGetStocksArray`` / ``SqlGroupHasStock`` 连表得到。
    """
    records = [record for record in DEMO_TRANSACTION_RECORDS
               if record["group_id"] == group_id]
    if symbol:
        records = [record for record in records
                   if record["stock_id"] == symbol]
    return records
