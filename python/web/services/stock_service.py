"""股票列表表 —— 对应 ``php/ui/stockparagraph.php``（45 行）。

这是 ``php/ui`` 里**最短**的一个段落：两列（代码 / 名称）+ 一个翻页控件。
它的价值在于把 PHP 里两件"顺手就写在页面上的事"拆成了数据：

1. **翻页控件**：PHP 的 ``GetMenuLink(false, $iTotal, $iStart, $iNum)`` 直接把
   链接 HTML 拼成一串字符串塞给 ``EchoTableParagraphBegin()`` 当标题。
   本版返回一个 dict，由模板用 ``url_for`` 生成链接（见 :func:`menu_link_paging`）。
2. **每行的链接**：代码格是站内链接、名称格是雪球外链。服务层只产出 URL，
   不产 ``<a>`` —— 见 :class:`~historytable.TableRow` 的 ``hrefs`` 字段。

## 三条必须照抄的细节

1. **管理员列的表头是空串、宽 270**：``new TableColumn('', 270)``。
   它只在 ``$bAdmin`` 为真时出现，内容是"编辑/删除"两个链接。
2. **列仅三列**，而管理员列的宽 270 是**全表最宽** —— 那一列留给后台操作，
   宽度在 PHP 里写死。
3. ``GetMenuLink()`` 的最后一轮 ``for`` 是"每页显示 N 条"的页码方块，
   起点 ``DEFAULT_PAGE_NUM``（100）、上限 500，见 :func:`menu_page_sizes`。

## 没迁的部分：管理员那一列的操作链接

``GetStockEditDeleteLink()`` 属于 🔴 membership 域 —— 它要登录 + 管理员角色，
本项目连账号体系都还没迁。所以：

* 参数为假时的普通用户视图（**绝大多数请求**）与 PHP 完全一致；
* 真时的列也**照抄加出来**（空表头 + 270 宽），链接通过 ``admin_link_builder``
  注入；不注入就只有空表头 —— 与 PHP 在"没有可选操作"时的表现一致。

把它做成注入回调而不是写死 URL，是因为那一列到底有几个操作、权限怎么判，
都得等账号那一档定下来。**现在先别编 URL。**
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from web.bootstrap import install_paths

install_paths()

from sql.historytable import TableRow  # noqa: E402
from sql.stocksql import StockSql, stock_paging_columns  # noqa: E402
from ui.uicolumn import column_base, column_name, column_symbol, columns_to_dicts  # noqa: E402


#: ``EchoTableParagraphBegin($ar, 'stock', $strMenuLink)`` 的 ``$strId``。
STOCK_TABLE_ID = "stock"

#: 管理员列：``new TableColumn('', 270)`` —— 空表头、宽 270。
STOCK_ADMIN_WIDTH = 270

#: ``php/httplink.php:9``：``define('DEFAULT_PAGE_NUM', 100)``。
DEFAULT_PAGE_NUM = 100

#: ``GetMenuLink()`` 最后一轮的上限 ``min(…, 500)``。
MAX_PAGE_NUM = 500

#: 翻页控件的中文词组（``GetMenuLink()`` 的 ``$bChinese`` 分支）。
MENU_TOTAL_TEXT = "总数"
MENU_CURRENT_TEXT = "当前显示"
MENU_FIRST_TEXT = "首页"
MENU_PREV_TEXT = "上一页"
MENU_NEXT_TEXT = "下一页"
MENU_LAST_TEXT = "末页"


class StockService:
    """把 ``stock`` 表的一页记录整理成两列表格 + 翻页数据。

    ==========================  =============================================
    构造参数                      用途
    ==========================  =============================================
    ``stock_sql``               取数对象（``auto/stocksql.StockSql``）。必填。
    ``link_builder``            由符号生成**站内** URL（代码格用）。
    ``external_link_builder``   由符号生成**站外**行情 URL（名称格用）。
    ``admin_link_builder``      由符号生成管理员操作列表，形如
                                ``[{"text": "编辑", "url": ...}, ...]``。
    ==========================  =============================================
    """

    def __init__(self, stock_sql: Any,
                 link_builder: Callable[[str], Any] = None,
                 external_link_builder: Callable[[str], Any] = None,
                 admin_link_builder: Callable[[str], Any] = None):
        self.stock_sql = stock_sql
        self.link_builder = link_builder
        self.external_link_builder = external_link_builder
        self.admin_link_builder = admin_link_builder

    # -- 对外 -------------------------------------------------------------
    def get_table(self, start: int = 0, num: int = 0, admin: bool = False) -> Any:
        """对应 ``EchoStockParagraph($iStart, $iNum, $bAdmin)``。

        **没有数据时仍然返回表格 dict**（``rows`` 为空）—— 与 trading 那段不同：
        股票列表页是一张目录表，摆一张空表配"总数 0"的翻页说明是对的，
        不像盘口那样"摆空表会让人以为数据丢了"。模板据此显示提示文案。
        """
        total = self._count()
        records = list(self._records(start, num))

        return {
            "id": STOCK_TABLE_ID,
            "columns": columns_to_dicts(build_stock_columns(admin)),
            "rows": build_stock_rows(
                records,
                admin=admin,
                link_builder=self.link_builder,
                external_link_builder=self.external_link_builder,
                admin_link_builder=self.admin_link_builder,
            ),
            "total": total,
            "count": len(records),
            "start": start,
            "num": num,
            #: PHP 把 ``GetMenuLink()`` 的结果当表格标题；这里把它数据化。
            "paging": menu_link_paging(total, start, num),
        }

    # -- 内部 -------------------------------------------------------------
    def _count(self) -> int:
        try:
            return self.stock_sql.count_data()
        except Exception:
            # 列表页不该因为一次查询失败整体 500 —— PHP 那边 $sql 是全局单例，
            # 取不到会让整页挂掉；这里宁可显示"总数 0"。
            return 0

    def _records(self, start: int, num: int) -> Sequence:
        try:
            return self.stock_sql.get_all(start, num)
        except Exception:
            return []


# ---------------------------------------------------------------------------
# 列
# ---------------------------------------------------------------------------
def build_stock_columns(admin: bool = False) -> list:
    """``array(new TableColumnSymbol(), new TableColumnName())`` + 可选管理员列。"""
    columns = [column_symbol(), column_name()]
    if admin:
        columns.append(column_base("", STOCK_ADMIN_WIDTH))
    return columns


# ---------------------------------------------------------------------------
# 行
# ---------------------------------------------------------------------------
def build_stock_rows(records: Iterable[Any], admin: bool = False,
                     link_builder: Callable[[str], Any] = None,
                     external_link_builder: Callable[[str], Any] = None,
                     admin_link_builder: Callable[[str], Any] = None) -> list:
    """对应 ``_echoStockTableItem($strSymbol, $strName, $bAdmin)``。

    行的结构：``[代码, 名称]``，管理员模式再追加一格占位给操作链接。
    链接本身走 :attr:`TableRow.href` / :attr:`TableRow.hrefs`，
    **不在 cells 里放 HTML**。
    """
    rows = []
    for record in records:
        symbol = _field(record, "symbol")
        if not symbol:
            continue
        row = TableRow()
        row.add(symbol)
        if link_builder is not None:
            row.href = _text(link_builder(symbol))

        if external_link_builder is not None:
            url = external_link_builder(symbol)
            if url:
                row.hrefs[1] = url
        row.add(_field(record, "name"))

        if admin:
            # PHP 这一格是 GetStockEditDeleteLink() 产出的两个 <a>；
            # 本版给一串 {"text","url"}，让模板去渲染。没有 builder 就是空格。
            links = admin_link_builder(symbol) if admin_link_builder is not None else None
            row.add("")
            if links:
                row.hrefs[2] = list(links)

        rows.append(row)
    return rows


#: ``StockSql.get_all()`` 的 ``SELECT`` 顺序。tuple 行靠它按下标取值。
STOCK_COLUMN_ORDER = ("id", "symbol", "name")


def _field(record: Any, key: str) -> str:
    """取一行记录里的字段。

    ``pymysql`` 换游标类型会让行从 dict 变成 tuple，所以两种都要认 ——
    只认 dict 时的表现是"整列空白"而不是报错，最难查。

    这条原则在 ``reference_service._row_symbol()`` 里已经立过一次
    （见 ``_LEVERAGE_SQL`` 那段的注释），这里照做。
    """
    try:
        return _text(record[key])
    except Exception:
        pass
    try:
        index = STOCK_COLUMN_ORDER.index(key)
    except ValueError:
        return ""
    try:
        return _text(record[index])
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 翻页（GetMenuLink 的数据版）
# ---------------------------------------------------------------------------
def menu_link_paging(total: int, start: int, num: int) -> dict:
    """对应 ``GetMenuLink(false, $iTotal, $iStart, $iNum)``。

    ==========================  ============================================
    返回键                       含义
    ==========================  ============================================
    ``show``                    是否需要显示控件。``$iTotal <= 0`` 或
                                ``$iStart == 0 && $iNum == 0`` 时为假 ——
                                后者正是 PHP 里"直接返回总数"那个分支。
    ``first_index``/``last_index``  "当前显示 X-Y"的两个端点（1 起）。
    ``prev_start``/``first_start``  上一页/首页的 start；没有就是 ``None``。
    ``next_start``/``last_start``   下一页/末页的 start。
    ``page_sizes``              "每页 N 条"的候选，见 :func:`menu_page_sizes`。
    ==========================  ============================================
    """
    if total <= 0 or (start == 0 and num == 0):
        return {
            "show": False,
            "total": total,
            "first": MENU_FIRST_TEXT,
            "prev": MENU_PREV_TEXT,
            "next": MENU_NEXT_TEXT,
            "last": MENU_LAST_TEXT,
        }

    prev_start, next_start = stock_paging_columns(start, num, total)

    first_start = None
    if prev_start is not None and prev_start != 0:
        first_start = 0

    last_start = None
    if next_start is not None and next_start + num < total:
        last_start = total - num

    return {
        "show": True,
        "total": total,
        "total_text": MENU_TOTAL_TEXT,
        "current_text": MENU_CURRENT_TEXT,
        "first": MENU_FIRST_TEXT,
        "prev": MENU_PREV_TEXT,
        "next": MENU_NEXT_TEXT,
        "last": MENU_LAST_TEXT,
        "start": start,
        "num": num,
        "first_index": start + 1,
        "last_index": min(start + num, total),
        "prev_start": prev_start,
        "first_start": first_start,
        "next_start": next_start,
        "last_start": last_start,
        "page_sizes": menu_page_sizes(total),
    }


def menu_page_sizes(total: int) -> list:
    """``GetMenuLink()`` 最后一轮 ``for``：每页 N 条的候选。

    .. code-block:: php

        for ($i = DEFAULT_PAGE_NUM; $i <= min(($iTotal + DEFAULT_PAGE_NUM - 1), 500);
             $i += DEFAULT_PAGE_NUM)

    注意两处容易改错的地方：

    * 上界是 ``总数 + 步长 - 1`` 而不是总数 —— 于是**总数比步长少时也会有一个候选**
      （总数 30 仍然给出 100），删掉它会让"每页 100 条"这一项在小库里消失；
    * 候选会**超出总数**：总数 250 时给出 100/200/300。看着别扭，
      但 PHP 就是这样，改掉会让"想一次看完"这个入口消失。
    """
    stop = min(total + DEFAULT_PAGE_NUM - 1, MAX_PAGE_NUM)
    return list(range(DEFAULT_PAGE_NUM, stop + 1, DEFAULT_PAGE_NUM))


def default_stock_service_factory() -> StockService:
    """创建使用当前请求数据库连接的股票列表服务。"""
    from web.db import get_db

    # get_db() 已经是 pymysql 连接本身（DictCursor），直接交给 StockSql
    return StockService(StockSql(get_db()))


def _text(value: Any) -> str:
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名）
# ---------------------------------------------------------------------------
EchoStockParagraph = StockService.get_table
