"""交易明细段落 —— 对应 ``php/ui/transactionparagraph.php``（142 行）。

调用方两处（都在 🔴 membership 域里）::

    woody/res/php/_mystocktransaction.php   EchoTransactionParagraph($acct, $strGroupId, $ref, true)
    woody/res/php/_stockaccount.php:206     EchoTransactionParagraph($this, $strGroupId, false, false)

这是"我每一笔买卖"的流水表：一行 = ``stocktransaction`` 表里一条记录，
七列（日期 / 代码 / 数量 / 价格 / 费用 / 备注 / 操作）。

## 一、七列里只有前五列是数据

后两列（备注、操作）是**拼好的 HTML**，而且必须照拼：

* **备注格** = ``清空链接 + 备注原文 + 校准链接``。原文夹在两个链接
  **中间**，且链接都带 ``onclick`` 确认 —— 走不了 :attr:`TableRow.hrefs`
  （那只能表达纯链接），所以这两格用 :attr:`TableRow.html_indexes`
  透传 HTML。PHP 的 ``EchoTableColumn($ar)`` 收到的本来就是这个串。
* **操作格** = ``修改链接 + ' ' + 删除链接``。只读时没有修改链接，
  于是格子是 ``' '`` —— **一个空格**，不是空串。PHP 的
  ``$strEdit.' '.$strDelete`` 在两边都为空时留下的就是这一个空格。

## 二、三处必须照抄的 PHP 语义

### 1. ``strpos`` 判的是**拼过链接之后**的备注

.. code-block:: php

    $strRemark = GetOnClickLink(...).$strRemark;
    if (strpos($strRemark, STOCK_DISP_ORDER) !== false)

判的是拼接结果，不是原始备注。本版照抄（见 :func:`_remark_cell`）。
两者语义等价 —— 清空链接的确认文字里也有备注，所以"原始备注含申购"
与"拼接结果含申购"同真同假 —— 但**照抄更省一次解释**：
哪天有人往链接里塞了别的文字，行为仍然跟着 PHP 走。

### 2. ``$strNetValue != $strPrice`` 是 PHP 的松散比较

两个操作数都是数据库里的**字符串**。PHP 8 的规则是"两边都是数字串就按
数值比，否则按字符串比"：``'1.234' != '1.2340'`` 是 **false**（数值相等），
而 Python 的字符串比较会得 True。这里走 :func:`_php_ne`。

### 3. 净值取哪一列看符号，不看人

.. code-block:: php

    if (in_arrayQdii($strSymbol) || in_arrayQdiiMix($strSymbol))
        $strNetValue = $net_sql->GetClosePrev($strStockId, $strDate);
    else
        $strNetValue = $net_sql->GetClose($strStockId, $strDate);

QDII 基金当天净值要**收盘后才公布**，所以按"上一交易日"取值 ——
这正是 :mod:`web.services.calibration_service` 里那条"QDII 慢一天"的
同一件事，只是这里用于"把申购价校准到当天净值"。

## 三、没迁的部分

``EchoTransactionParagraph()`` 里那三行**取数**属于 🔴 数据库域
（``StockGroupItemSql`` 的 ``GetStockTransaction`` /
``CountAllStockTransaction`` 等），本版由调用方注入 ``records``；
``$acct->GetStart()`` / ``GetNum()`` / ``IsGroupReadOnly()`` / ``IsAdmin()``
属于 🔴 membership 域，本版落成参数。翻页控件复用
:func:`web.services.stock_service.menu_link_paging` —— 它与 PHP 的
``GetMenuLink()`` 是同一个函数，股票列表页已经迁过。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from web.bootstrap import install_paths

install_paths()

from core.stocksymbol import in_array_qdii, in_array_qdii_mix  # noqa: E402
from core.stocktrans import _php_floatval, get_sql_transaction_date  # noqa: E402
from ref.stocklink import (  # noqa: E402
    PATH_STOCK,
    STOCK_TRANSACTION_DISPLAY,
    get_delete_link,
    get_edit_link,
    get_on_click_link,
    stock_get_all_transaction_link,
)
from sql.historytable import TableRow, format_number_display  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    STOCK_DISP_CALIBRATION,
    STOCK_DISP_NETVALUE,
    STOCK_DISP_ORDER,
    STOCK_DISP_REMARK,
    column_base,
    column_date,
    column_price,
    column_quantity,
    column_remark,
    column_symbol,
    columns_to_dicts,
)
from web.services.stock_service import menu_link_paging  # noqa: E402


#: ``EchoTableParagraphBegin($ar, 'transaction', $str)`` 的 ``$strId``。
TRANSACTION_TABLE_ID = "transaction"

#: ``PATH_STOCK.'submittransaction.php'`` —— 清空 / 校准 / 删除都走这一页，
#: 用 ``?empty=`` / ``?adjust=`` / ``?delete=`` 区分动作。
SUBMIT_TRANSACTION_PAGE = PATH_STOCK + "submittransaction.php"

#: ``PATH_STOCK.'editstocktransaction'`` —— ``GetEditLink()`` 拼页面名，
#: 中文页后缀 ``cn.php`` 由 :func:`ref.stocklink.get_php_link` 补上。
EDIT_TRANSACTION_PAGE = PATH_STOCK + "editstocktransaction"

#: ``new TableColumn('费用', 60)``。
FEES_COLUMN_WIDTH = 60

#: ``new TableColumn('操作')`` —— 没给宽度，走 ``TableColumn`` 默认的 80。
OPERATION_COLUMN_TEXT = "操作"

#: ``define('TABLE_COMMON_DISPLAY', 10)``（``php/ui/table.php:4``）：
#: ``$bAll`` 为假时只显示最新 10 条。
TABLE_COMMON_DISPLAY = 10

#: 备注格里那个"清空"链接的显示文本。
#:
#: 动作名与显示文本不是一个词：URL 上是 ``?empty=``（清空备注），
#: 链接文字是 ``清空``。PHP 里两者都写死在 ``GetOnClickLink()`` 的调用处。
CLEAR_REMARK_TEXT = "清空"

#: 确认问句的前缀。PHP 的问句是 ``'确认清空'.'备注'.'：'.$strRemark.'？'``，
#: **确认**两个字属于问句、不属于动作名 —— 初版移植把它漏了，
#: 于是弹窗变成"清空备注：xxx？"，是 PHP 逐例对照抓出来的。
CONFIRM_TEXT = "确认"

#: 备注格与操作格的下标（见模块 docstring 第一节）。
REMARK_CELL_INDEX = 5
OPERATION_CELL_INDEX = 6


# ---------------------------------------------------------------------------
# 列（对应 EchoTransactionParagraph 的那七列）
# ---------------------------------------------------------------------------


def transaction_columns() -> list:
    """``EchoTransactionParagraph()`` 的七列。

    .. code-block:: php

        new TableColumnDate(), new TableColumnSymbol(), new TableColumnQuantity(),
        new TableColumnPrice(), new TableColumn('费用', 60),
        new TableColumnRemark(), new TableColumn('操作')

    注意后两个：费用列是**无名列**（``TableColumn`` 直接 new，不在
    ``TableColumnXxx`` 家族里），操作列连宽度都没给 —— 走构造函数默认的 80。
    """
    return [
        column_date(),
        column_symbol(),
        column_quantity(),
        column_price(),
        column_base("费用", FEES_COLUMN_WIDTH),
        column_remark(),
        column_base(OPERATION_COLUMN_TEXT),
    ]


# ---------------------------------------------------------------------------
# 行（对应 _echoTransactionTableItem）
# ---------------------------------------------------------------------------


def build_transaction_row(ref: Any, record: Any, read_only: bool = False,
                          admin: bool = False,
                          net_value_of: Callable[[Any, Any, bool], Any] = None
                          ) -> TableRow:
    """对应 ``_echoTransactionTableItem()``（``transactionparagraph.php:4``）。

    ======================  ==================================================
    参数                     含义
    ======================  ==================================================
    ``ref``                 引用对象（``MyStockReference``）。取展示名、
                            价格精度、``stock_id`` 都靠它。
    ``record``              一条 ``stocktransaction`` 记录
                            （``id`` / ``filled`` / ``quantity`` / ``price``
                            / ``fees`` / ``remark``）。
    ``read_only``           ``$bReadOnly``（别人的分组）：没有清空/校准/修改。
    ``admin``               ``$bAdmin``：只读时**管理员仍能看到删除**。
    ``net_value_of``        净值查询 ``(stock_id, date, prev)``，
                            ``prev`` 为真取"上一交易日"。不传则备注格
                            不出现校准链接（与 PHP 查不到净值时一致）。
    ======================  ==================================================
    """
    date = get_sql_transaction_date(record)
    price = record.get("price")
    quantity = record.get("quantity")
    record_id = record.get("id")

    row = TableRow()
    row.add(date)
    row.add(_invoke(ref, "get_display", "GetDisplay"))
    row.add(quantity)
    # GetPriceDisplay(floatval($strPrice), false, IsFundA() ? 4 : GetPrecision())
    precision = 4 if _call(ref, "is_fund_a", "IsFundA") else _invoke(ref, "get_precision", "GetPrecision")
    row.add(_invoke(ref, "get_price_display", "GetPriceDisplay",
                    _php_floatval(price), False, precision))
    row.add(format_number_display(_php_floatval(record.get("fees"))))
    row.add(_remark_cell(ref, record, date=date, price=price,
                         read_only=read_only, net_value_of=net_value_of))
    row.add(_operation_cell(record, date=date, quantity=quantity,
                            read_only=read_only, admin=admin))
    # 备注格与操作格是 PHP 同形态的 HTML，模板不转义
    row.html_indexes = [REMARK_CELL_INDEX, OPERATION_CELL_INDEX]
    return row


def _remark_cell(ref: Any, record: Any, date: Any = None, price: Any = None,
                 read_only: bool = False,
                 net_value_of: Callable[[Any, Any, bool], Any] = None) -> str:
    """对应 ``_echoTransactionTableItem()`` 里备注格的 30 行。

    .. code-block:: php

        if ($bReadOnly == false)
        {
            if (strlen($strRemark) > 0)
            {
                $strRemark = GetOnClickLink(..., '确认清空备注：'.$strRemark.'？', '清空').$strRemark;
                if (strpos($strRemark, STOCK_DISP_ORDER) !== false)
                {
                    if (in_arrayQdii($strSymbol) || in_arrayQdiiMix($strSymbol))
                        $strNetValue = $net_sql->GetClosePrev($strStockId, $strDate);
                    else
                        $strNetValue = $net_sql->GetClose($strStockId, $strDate);
                    if ($strNetValue != $strPrice)
                        $strRemark .= GetOnClickLink(..., '确认校准到净值: '.$strNetValue.'？', '校准');
                }
            }
        }

    三处照抄：

    * **只读时整段跳过** —— 别人的分组里连"清空"链接都不该出现；
    * ``strpos`` 判的是**拼过链接后**的备注（见模块 docstring 第 1 条）；
    * 净值与价格的比较是 PHP 松散比较（同前，第 2 条）。

    ``$strNetValue`` 为空时 PHP 会拼出 ``netvalue=``（null 转空串）；
    Python 的 ``str(None)`` 会得到 ``'None'``，所以这里统一走
    :func:`_php_str`。
    """
    remark = _php_str(record.get("remark"))
    if read_only:
        return remark
    if len(remark) == 0:
        return remark

    record_id = record.get("id")
    # '确认清空'.STOCK_DISP_REMARK.'：'.$strRemark.'？'
    # —— 注意**确认**两个字是问句的一部分，不属于动作名，别漏
    empty_link = get_on_click_link(
        "%s?empty=%s" % (SUBMIT_TRANSACTION_PAGE, _php_str(record_id)),
        CONFIRM_TEXT + CLEAR_REMARK_TEXT + STOCK_DISP_REMARK + "：" + remark + "？",
        CLEAR_REMARK_TEXT,
    )
    remark = empty_link + remark

    if STOCK_DISP_ORDER in remark:
        if net_value_of is None:
            # 没接净值表 —— 按项目约定"未注入 = 没有这一项"，
            # 与"接了但查不到"（走下面那支、照样出链接）不是一回事。
            return remark
        stock_id = _invoke(ref, "get_stock_id", "GetStockId")
        symbol = _invoke(ref, "get_symbol", "GetSymbol")
        prev = bool(in_array_qdii(symbol) or in_array_qdii_mix(symbol))
        net_value = net_value_of(stock_id, date, prev)
        if _php_ne(net_value, price):
            remark += get_on_click_link(
                "%s?adjust=%s&netvalue=%s" % (SUBMIT_TRANSACTION_PAGE,
                                              _php_str(record_id),
                                              _php_str(net_value)),
                "确认" + STOCK_DISP_CALIBRATION + "到" + STOCK_DISP_NETVALUE
                + ": " + _php_str(net_value) + "？",
                STOCK_DISP_CALIBRATION,
            )
    return remark


def _operation_cell(record: Any, date: Any = None, quantity: Any = None,
                    read_only: bool = False, admin: bool = False) -> str:
    """对应 ``_echoTransactionTableItem()`` 的操作格。

    .. code-block:: php

        $strEdit = '';
        $strDelete = GetDeleteLink(..., $strDate.' '.$strQuantity.STOCK_TRANSACTION_DISPLAY);
        if ($bReadOnly == false)      $strEdit = GetEditLink(...);
        else if ($bAdmin == false)    $strDelete = '';
        $ar[] = $strEdit.' '.$strDelete;

    两个容易改错的地方：

    * **删除链接是"日期 + 空格 + 数量 + 交易记录"**（``2026-10-13 100交易记录``），
      确认弹窗里靠它区分删的是哪一条 —— 同一天同数量会有两条；
    * **只读且非管理员时格子是 ``' '`` 一个空格**，不是空串。PHP 的
      ``$strEdit.' '.$strDelete`` 两边都清空后留下的就是那一个空格。
    """
    edit = ''
    delete = get_delete_link(
        "%s?delete=%s" % (SUBMIT_TRANSACTION_PAGE, _php_str(record.get("id"))),
        _php_str(date) + ' ' + _php_str(quantity) + STOCK_TRANSACTION_DISPLAY,
    )
    if not read_only:
        edit = get_edit_link(EDIT_TRANSACTION_PAGE, record.get("id"))
    elif not admin:
        delete = ''
    return edit + ' ' + delete


# ---------------------------------------------------------------------------
# 行集合（对应 _echoTransactionTableData 的两个分支）
# ---------------------------------------------------------------------------


def build_transaction_rows(records: Iterable[Any], ref: Any = None,
                           ref_builder: Callable[[Any], Any] = None,
                           symbol_of: Callable[[Any], Any] = None,
                           group_item_id_of: Callable[[Any], Any] = None,
                           read_only: bool = False, admin: bool = False,
                           net_value_of: Callable[[Any, Any, bool], Any] = None
                           ) -> list:
    """``_echoTransactionTableData()``：有 ``ref`` 走单标的，否则走全部。

    ``ref`` 非空时对应 ``_echoSingleTransactionTableData()`` —— 每一行都用
    同一个引用对象（PHP 里 ``$ref`` 是参数传进来的）。

    否则对应 ``_echoAllTransactionTableData()``，这里有个必须照抄的
    **引用对象缓存**（``php/ui/transactionparagraph.php:67``）::

        $ar = array();
        while ($record = mysqli_fetch_assoc($result)) {
            $strGroupItemId = $record['groupitem_id'];
            if (array_key_exists($strGroupItemId, $ar))  $ref = $ar[$strGroupItemId];
            else {
                $strStockId = $sql->GetStockId($strGroupItemId);
                $strSymbol  = SqlGetStockSymbol($strStockId);
                $ref = new MyStockReference($strSymbol);
                $ar[$strGroupItemId] = $ref;
            }
            _echoTransactionTableItem($ref, $record, ...);
        }

    同一个 ``groupitem_id`` 下的多笔交易**共用一份引用对象**。这不是单纯
    的性能优化：引用对象构造时会装填行情（本项目里是读快照），
    重复构造会让同一列出现不同时刻的价格。缓存按**首次出现**建对象，
    之后一律复用 —— 于是同一标的的几行价格口径一致。

    两个查询（``GetStockId`` / ``SqlGetStockSymbol``）合并成注入回调
    ``symbol_of``：由调用方把 ``groupitem_id`` 映射成符号，
    缺省时直接用 ``record['stock_id']``。
    """
    rows = []
    cache = {}
    for record in records or ():
        if ref is not None:
            row_ref = ref
        else:
            key = (group_item_id_of(record) if group_item_id_of
                   else record.get("groupitem_id"))
            if key in cache:
                row_ref = cache[key]
            else:
                stock_id = record.get("stock_id")
                symbol = symbol_of(stock_id) if symbol_of else stock_id
                row_ref = ref_builder(symbol) if ref_builder else None
                cache[key] = row_ref
        rows.append(build_transaction_row(
            row_ref, record, read_only=read_only, admin=admin,
            net_value_of=net_value_of))
    return rows


# ---------------------------------------------------------------------------
# 表（对应 EchoTransactionParagraph）
# ---------------------------------------------------------------------------


def build_transaction_table(records: Iterable[Any] = None, ref: Any = None,
                            ref_builder: Callable[[Any], Any] = None,
                            symbol_of: Callable[[Any], Any] = None,
                            group_item_id_of: Callable[[Any], Any] = None,
                            read_only: bool = False, admin: bool = False,
                            net_value_of: Callable[[Any, Any, bool], Any] = None,
                            b_all: bool = True, start: int = 0, num: int = 0,
                            total: int = 0, group_id: Any = None,
                            all_link: Any = None) -> dict:
    """对应 ``EchoTransactionParagraph()``（``transactionparagraph.php:102``）。

    ``$bAll`` 决定**标题与页脚各是什么**（PHP 里两者是同一个串）::

        if ($bAll) {
            $str = $strMenuLink;                 // 标题 = 翻页控件
            ...
        } else {
            $str = StockGetAllTransactionLink($strGroupId, $ref);  // 标题 = 链接
            $strMenuLink = '';                   // 页脚空
        }
        EchoTableParagraphBegin($arColumn, 'transaction', $str);
        ...
        EchoTableParagraphEnd($strMenuLink);

    于是返回两个键：``paging``（翻页控件数据，``$bAll`` 为真时非空）与
    ``title_link``（``$bAll`` 为假时的"交易记录"链接）。模板据此决定
    标题与页脚 —— 与 PHP 一样，**两者在同一时刻只有一个有内容**。

    ``$bAll`` 为假时 PHP 只取 ``TABLE_COMMON_DISPLAY``（10）条，
    这里对传入的 ``records`` 做同样的切片（对应 SQL 的 ``LIMIT``）。

    翻页控件复用 :func:`web.services.stock_service.menu_link_paging`：
    它与 ``GetMenuLink()`` 是同一个函数，股票列表页已迁过并有单测，
    不重复实现。
    """
    if not b_all:
        records = list(records or ())[:TABLE_COMMON_DISPLAY]

    rows = build_transaction_rows(
        records, ref=ref, ref_builder=ref_builder, symbol_of=symbol_of,
        group_item_id_of=group_item_id_of, read_only=read_only, admin=admin,
        net_value_of=net_value_of,
    )

    if b_all:
        paging = menu_link_paging(total, start, num)
        title_link = None
    else:
        paging = None
        title_link = all_link
        if title_link is None and group_id:
            title_link = stock_get_all_transaction_link(group_id, ref)

    return {
        "id": TRANSACTION_TABLE_ID,
        "columns": columns_to_dicts(transaction_columns()),
        "rows": rows,
        "paging": paging,
        "title_link": title_link,
    }


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _invoke(ref: Any, snake: str, camel: str, *args: Any) -> Any:
    """先试 snake_case 再试 camelCase（见 AGENTS.md 的别名约定）。"""
    if ref is None:
        return None
    method = getattr(ref, snake, None)
    if method is None:
        method = getattr(ref, camel, None)
    if method is None:
        return None
    return method(*args)


def _call(ref: Any, snake: str, camel: str, *args: Any) -> bool:
    return bool(_invoke(ref, snake, camel, *args))


def _php_str(value: Any) -> str:
    """PHP 的字符串拼接语义：``false`` / ``null`` 都是空串。

    ``str(None)`` 会得到 ``'None'`` —— 而 PHP 的 ``'netvalue='.null`` 是
    ``'netvalue='``。这个差别会让校准链接的参数变成 ``netvalue=None``。
    """
    if value is None or value is False:
        return ""
    return str(value)


def _php_ne(left: Any, right: Any) -> bool:
    """PHP 8 的 ``!=``：两边都是数字串按**数值**比，否则按字符串比。

    实测（PHP 8.0.10）::

        "1.234" != "1.2340"   → false   ← 数值相等，Python 的字符串比会得 True
        "0"     != "0.0"      → false
        " 1.234 " != "1.234"  → false   ← 两端空白会被忽略
        null    != ""         → false   ← 都当空串
        null    != "1.234"    → true
        "abc"   != "abc"      → false

    净值与价格都来自 MySQL 的 ``varchar``/``decimal`` 列，取值形如
    ``'1.2340'`` 与 ``'1.234'`` —— 用 Python 的字符串比较会把"没变"
    误判成"变了"，于是每一行都多出一个校准链接。
    """
    left_num = _as_number(left)
    right_num = _as_number(right)
    if left_num is not None and right_num is not None:
        return left_num != right_num
    return _php_str(left) != _php_str(right)


def _as_number(value: Any) -> Any:
    """能当数字看就返回 ``float``，否则 ``None``（``null`` / 空串 / 非数字）。"""
    if value is None or value is False:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return None
    return None
