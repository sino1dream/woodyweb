"""账户页（交易明细 + 资金汇总）的路由。

对应 PHP 两个调用现场::

    woody/res/php/_mystockgroup.php:143
        if ($acct->EchoStockTransaction($group))
            $acct->EchoMoneyParagraph($group, new CnyReference('USCNY'), new CnyReference('HKCNY'));

    woody/res/php/_myportfolio.php:182
        $portfolio->arStockGroup[] = $portfolio;      // "全部"这一行
        $acct->EchoMoneyParagraphs($portfolio->arStockGroup, USCNY, HKCNY);

``EchoStockTransaction()`` 内部（``_stockaccount.php:206``）先出交易明细
段落再出投资组合段落，所以**一页上两段是挨着的**。本页把交易明细与资金
汇总并在一起：前者按某一个分组显示最新十条，后者把每个分组各出一组行，
外加一个"全部"行。

## 为什么是演示数据

PHP 那边的数据来自三张表：``stockgroup``（按 member 的分组）、
``stockgroupitem``（分组内的标的与预统计量）、``stocktransaction``
（每一笔买卖）。**演示库里这三张表都不存在**，而账号登录体系
（``$acct->Auth()``）用户要求暂缓，所以这里用一份**写死的样例数据集**。

与 :mod:`web.routes.portfolio` 的取舍一致：列定义与行装配都在服务层，
路由只负责"把样例数据喂进去"。

## 演示数据覆盖的分支

**交易明细**按备注分五种形态（``_echoTransactionTableItem`` 里那三层
``if`` 的全部分支）：

==================  ====================================================
备注                 表现
==================  ====================================================
``申购`` + QDII      校准链接按**上一交易日**净值（``GetClosePrev``）
``申购`` + 非 QDII   校准链接按**当日**净值（``GetClose``）
``申购`` 且价等于净值  **不出**校准链接（PHP 的 ``$strNetValue != $strPrice``）
不含 ``申购``        只有清空链接
空备注               两个链接都没有
==================  ====================================================

**资金汇总**按 ``empty()`` 判定出 1~3 行（分组 1 只有港币行、分组 2
只有美元行、"全部"三行都有）—— 见 :mod:`web.services.money_service`
里"美元 / 港币行用 ``empty()`` 判，不是判非零"那条。

## 页面上的链接点得通吗

**分组名**是本页自己拼的（``?groupid=`` 切分组），能点。其余链接
（清空 / 校准 / 修改 / 删除、"股票分组"标题、"交易记录"标题）都指向
PHP 的页面，演示环境下点不通 —— 它们的作用是让形态与 PHP 一致，
逐例对照脚本比的就是这些串。
"""

from __future__ import annotations

from flask import Blueprint, render_template, request, url_for
from markupsafe import Markup

from web.bootstrap import install_paths

install_paths()

from core.stockgroup import MyStockGroup, StockGroup  # noqa: E402
from web.services.money_service import build_money_table  # noqa: E402
from web.services.transaction_service import (  # noqa: E402
    TABLE_COMMON_DISPLAY,
    build_transaction_table,
)

account_bp = Blueprint("account", __name__)

#: 默认显示哪个分组（没有 ``?groupid=`` 时）。
DEFAULT_GROUP_ID = "2"

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
    """样例引用对象 —— 只实现两个段落用到的几个取值方法。

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


def _group_link_of(group_id):
    """``group_link_of``：``$acct->GetGroupLink($strGroupId)`` 的替身。

    PHP 那边是"分组名 → 分组页面"的链接。演示页没有分组页面，于是
    指回自己（``?groupid=``），点一下换上面的交易明细 —— 这样这个
    注入口子是真的在点，而不是摆设。
    """
    name = DEMO_GROUP_NAMES.get(group_id, str(group_id))
    return Markup('<a href="%s">%s</a>'
                  % (url_for("account.account_page", groupid=group_id), name))


@account_bp.route("/account")
def account_page():
    """交易明细段落（``php/ui/transactionparagraph.php``）+ 资金汇总段落
    （``_stockaccount.php:213``）。"""
    group_id = request.args.get("groupid") or DEFAULT_GROUP_ID
    if group_id not in DEMO_GROUP_NAMES:
        # 演示页没有"分组不存在"这一说（PHP 那边会先 $acct->EchoStockGroup()
        # 判一次），给个兜底别摆一张空表
        group_id = DEFAULT_GROUP_ID
    records = [record for record in DEMO_TRANSACTION_RECORDS
               if record["group_id"] == group_id]

    # ``_stockaccount.php:206`` 的调用形状：ref=false（全部标的）、b_all=false
    # —— 于是标题是"交易记录"链接、只显示最新 TABLE_COMMON_DISPLAY 条。
    transaction_table = build_transaction_table(
        records,
        ref_builder=_ref_of,
        symbol_of=lambda stock_id: stock_id,
        net_value_of=_demo_net_value,
        b_all=False,
        group_id=group_id,
    )

    money_table = build_money_table(
        _demo_groups(),
        usdcny_ref=_ref_of("fx_susdcnh"),
        hkcny_ref=_ref_of("fx_hkdcnh"),
        group_link_of=_group_link_of,
        # 不注入 member_email_of —— 与"没登录"一致，于是没有测试列。
        # 那一列只对"全部"行出现，且按会员邮箱分两种算法（见 money_service）。
    )

    return render_template(
        "account.html",
        transaction_table=transaction_table,
        money_table=money_table,
        group_id=group_id,
        group_name=DEMO_GROUP_NAMES.get(group_id, group_id),
        groups=sorted(DEMO_GROUP_NAMES.items()),
        table_common_display=TABLE_COMMON_DISPLAY,
        record_count=len(records),
    )
