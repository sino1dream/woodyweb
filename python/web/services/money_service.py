"""资金汇总段落 —— 对应 ``_stockaccount.php:213`` + ``_stock.php:13-91``。

调用方两处::

    woody/res/php/_mystock.php        EchoMoneyParagraphs($arGroup, $uscny_ref, $hkcny_ref)
    woody/res/php/_mystockgroup.php   EchoMoneyParagraph($group, ...)   // 单分组的薄封装

一行 = 一个**分组的一种货币**：人民币行恒有，美元行与港币行只在持有量
非零时出现。六列（分组名称 / 全部盈亏 / 全部持有 / 单一货币盈亏 /
单一货币持有 / 测试）。

## 一、"单一货币"是什么意思

``MultiCurrency`` 有三个原始桶（人民币 / 港币 / 美元）与三个折算结果。
表的**右边两列**是"折成人民币之后"的总额（``fConvertCNY`` / ``fConvertUSD``
/ ``fConvertHKD``），**中间两列**是原始桶自己的数。于是人民币行显示
"我有多少 A 股、折成人民币是多少"，美元行显示"我有多少美股、折成人民币
是多少"—— 两行的"全部持有"是**同一个**折人民币总数，只是按不同币种
单位表示。

## 二、三处必须照抄的 PHP 语义

### 1. 美元 / 港币行用 ``empty()`` 判，不是判非零

.. code-block:: php

    if ((empty($group->multi_amount->fUSD) == false)
            || (empty($group->multi_profit->fUSD) == false))

``empty(0.0)`` 是**真** —— 于是"持有量为 0"与"盈亏为 0"都不出这一行。
用 Python 的 ``!= 0`` 等价（``empty()`` 对浮点只有 0.0 与非零两种结果），
但**不能**写成 ``is not None``：``None``（没折算过）在 PHP 里也是 empty，
Python 的 ``None != 0`` 会把那一行放出来。

### 2. "补空格子"的分支在 PHP 8 下走不到

.. code-block:: php

    if ($strConvertValue == '')
    {
        if ($strProfit != '' || $strValue != '')	$ar[] = '';
    }
    else	$ar[] = $strConvertValue;

``GetNumberDisplay()`` 走的是 ``StockGetPriceDisplay(..., 'black', 'red')``，
而**只有** ``black`` 分支返回纯文本，灰（≈0）与红（负）都返回带
``<font>`` 的字符串。于是 ``0.0`` 产出 ``<font color=grey>0</font>``，
**永不为空白板** —— 那两个分支是历史遗留的死代码。

本版**照抄分支结构**（哪天 ``GetNumberDisplay`` 改了口径，行为仍然跟着
PHP 走），但两侧有个已知差异：Python 的
:func:`sql.historytable.format_number_display` 对 ``None`` / ``''``
返回空串，于是"没折算过"（``f_convert_*`` 还是 ``None``）时会走进
补空格子的分支。生产路径上不存在这种情形 —— ``_EchoMoneyGroupData()``
**必定先** ``ConvertCurrency()`` —— 所以两条路产出的格子数一致，
对照脚本也只比生产路径。

### 3. 测试列只对"全部"这一行出现，且按会员邮箱分两种算法

.. code-block:: php

    if ($strGroup == DISP_ALL_CN)
    {
        if ($strMemberId = $acct->GetMemberId())
        {
            switch (SqlGetEmailById($strMemberId))
            {
            case ADMIN_EMAIL:            $ar[] = GetNumberDisplay($fConvertProfit - 1679328.22);  break;
            case 'mix@palmmicro.com':    $ar[] = GetNumberDisplay($fConvertProfit / 2 - $fProfit); break;
            }
        }
    }

两个魔法数字照抄。``1679328.22`` 是作者给自己记的一个基准（大概是某个
时点的累计盈亏），``mix`` 那个邮箱是"两人合账"的一半口径 ——
**不要去猜它的含义，改了作者会对不上自己的账本**。

账号体系（🔴 membership）暂缓，所以邮箱由 ``member_email_of`` 注入；
不注入就**没有测试列**（与"没登录"一致）。

## 三、命名

行首格在 PHP 里是"分组链接"（``$acct->GetGroupLink()``），由
``group_link_of`` 注入；未注入时用 ``group_id`` 当文本。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from web.bootstrap import install_paths

install_paths()

from core.stockgroup import php_truthy  # noqa: E402
from core.stocktrans import _php_floatval  # noqa: E402
from web.services.admin_auth import ADMIN_EMAIL as _ADMIN_EMAIL  # noqa: E402
from ref.stocklink import (  # noqa: E402
    STOCK_GROUP_DISPLAY,
    get_my_stock_group_link,
    get_transaction_link,
)
from sql.historytable import TableRow, format_number_display  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    column_group_name,
    column_hkd,
    column_holding,
    column_profit,
    column_test,
    columns_to_dicts,
)


#: ``EchoTableParagraphBegin($ar, 'money', ...)`` 的 ``$strId``。
MONEY_TABLE_ID = "money"

#: ``$strMoney = '单一货币'``。
SINGLE_CURRENCY_TEXT = "单一货币"

#: ``define('DISP_ALL_CN', '全部')``（``php/account.php:14``）。
DISP_ALL_CN = "全部"

#: ``define('ADMIN_EMAIL', 'woody@palmmicro.com')``（``php/email.php:2``）。
#:
#: **单一来源在 ``web/services/admin_auth.py``**（那边负责"谁是管理员"），
#: 这里只做再导出：``money_service`` 用到它纯粹是"测试列按邮箱分算法"，
#: 两处各写一份字符串迟早会改漏一处，而那个常量决定的是**管理员专属列**。
ADMIN_EMAIL = _ADMIN_EMAIL

#: ``case 'mix@palmmicro.com'`` —— PHP 里写死的字面量，没有常量。
MIX_EMAIL = "mix@palmmicro.com"

#: ``GetNumberDisplay($fConvertProfit - 1679328.22)`` 里那个基准值。
ADMIN_PROFIT_BASE = 1679328.22

#: 美元行的显示文本（``StockGetTransactionLink($strGroupId, false, '$')``）。
USD_ROW_DISPLAY = "$"


# ---------------------------------------------------------------------------
# 列与标题
# ---------------------------------------------------------------------------


def money_columns() -> list:
    """``EchoMoneyParagraphs()`` 的六列。

    .. code-block:: php

        new TableColumnGroupName(), new TableColumnProfit(DISP_ALL_CN),
        new TableColumnHolding(DISP_ALL_CN), new TableColumnProfit($strMoney),
        new TableColumnHolding($strMoney), new TableColumnTest()

    前两列带 ``全部`` 前缀（折过汇的口径），中间两列带 ``单一货币`` 前缀。
    """
    return [
        column_group_name(),
        column_profit(DISP_ALL_CN),
        column_holding(DISP_ALL_CN),
        column_profit(SINGLE_CURRENCY_TEXT),
        column_holding(SINGLE_CURRENCY_TEXT),
        column_test(),
    ]


def money_title() -> str:
    """标题的**纯文本**部分：``$profit_col->GetDisplay()`` → ``盈亏``。

    PHP 的标题是 ``GetMyStockGroupLink().$profit_col->GetDisplay()``，
    即"股票分组"链接 + 加粗带色的"盈亏"。按项目"标题纯文本、装饰归模板"
    的取舍（见 ``README_ui_column_migration.md``），这里只出文本，
    链接与加粗由模板处理 —— 与 :func:`portfolio_service.portfolio_title`
    同一套做法。
    """
    return column_profit().text


def money_title_link() -> str:
    """标题里的"股票分组"链接（``GetMyStockGroupLink()``）。"""
    return get_my_stock_group_link()


# ---------------------------------------------------------------------------
# 行（对应 _echoMoneyItem）
# ---------------------------------------------------------------------------


def build_money_row(group_text: Any, value: Any = 0.0, profit: Any = 0.0,
                    convert_value: Any = 0.0, convert_profit: Any = 0.0,
                    member_email_of: Callable[[], Any] = None) -> TableRow:
    """对应 ``_echoMoneyItem()``（``_stock.php:13``）。

    ========================  ==============================================
    参数                       含义
    ========================  ==============================================
    ``group_text``            首格（分组链接文本，或 ``全部`` / ``港币$``）。
    ``value``                 该币种**原始桶**的市值。
    ``profit``                该币种**原始桶**的盈亏。
    ``convert_value``         折成人民币之后的市值。
    ``convert_profit``        折成人民币之后的盈亏。
    ``member_email_of``       取会员邮箱；只有"全部"行会用。不传则无测试列。
    ========================  ==============================================

    格数**不固定**：4、5、6 都有可能（PHP 的 ``$ar`` 长度取决于那两个
    "补空格子"分支与测试列）。模板按 ``row.cells`` 的实际长度渲染。
    """
    row = TableRow()
    row.add(group_text)
    row.add(format_number_display(convert_profit))

    text_convert_value = format_number_display(convert_value)
    text_profit = format_number_display(profit)
    text_value = format_number_display(value)

    if text_convert_value == '':
        if text_profit != '' or text_value != '':
            row.add('')
    else:
        row.add(text_convert_value)

    if text_profit == '':
        if text_value != '':
            row.add('')
    else:
        row.add(text_profit)

    # PHP 注掉了外层的 if ($strValue != '') —— 市值格恒追加
    row.add(text_value)

    if group_text == DISP_ALL_CN:
        email = member_email_of() if member_email_of else None
        if email == ADMIN_EMAIL:
            row.add(format_number_display(
                _php_floatval(convert_profit) - ADMIN_PROFIT_BASE))
        elif email == MIX_EMAIL:
            row.add(format_number_display(
                _php_floatval(convert_profit) / 2 - _php_floatval(profit)))
    return row


# ---------------------------------------------------------------------------
# 一个分组的三行（对应 _EchoMoneyGroupData）
# ---------------------------------------------------------------------------


def build_money_group_rows(group: Any, usdcny: Any = False, hkdcny: Any = False,
                           group_link_of: Callable[[Any], Any] = None,
                           member_email_of: Callable[[], Any] = None) -> list:
    """对应 ``_EchoMoneyGroupData()``（``_stock.php:67``）。

    .. code-block:: php

        if ($strGroupId = $group->GetGroupId()) {
            $strLink    = $acct->GetGroupLink($strGroupId);
            $strLinkUSD = StockGetTransactionLink($strGroupId, false, '$');
        } else {
            $strLink    = DISP_ALL_CN;      // "全部"分组没有 id
            $strLinkUSD = '$';
        }
        $group->ConvertCurrency($strUSDCNY, $strHKDCNY);
        _echoMoneyItem($strLink,    …, fCNY, …, fConvertCNY);
        if (!(empty(fUSD)) || !(empty(fUSD_profit))) _echoMoneyItem($strLinkUSD, …);
        if (!(empty(fHKD)) || !(empty(fHKD_profit))) _echoMoneyItem(港币$, …);

    **先折算再出行** —— 顺序反了会读到 ``None``（见
    :mod:`core.stockgroup` 里"三个 ``fConvert*`` 起步是 None"那条）。

    返回 1~3 行：人民币行恒有，美元行与港币行按 ``empty()`` 判定。
    """
    group_id = group.get_group_id()
    if group_id:
        link = group_link_of(group_id) if group_link_of else str(group_id)
        link_usd = get_transaction_link(group_id, False, USD_ROW_DISPLAY)
    else:
        link = DISP_ALL_CN
        link_usd = USD_ROW_DISPLAY

    group.convert_currency(usdcny, hkdcny)
    amount = group.multi_amount
    profit = group.multi_profit

    rows = [build_money_row(link, amount.f_cny, profit.f_cny,
                            amount.f_convert_cny, profit.f_convert_cny,
                            member_email_of=member_email_of)]

    # empty(0.0) 为真 —— 持有量与盈亏都为 0 时整行不出
    if php_truthy(amount.f_usd) or php_truthy(profit.f_usd):
        rows.append(build_money_row(link_usd, amount.f_usd, profit.f_usd,
                                    amount.f_convert_usd, profit.f_convert_usd,
                                    member_email_of=member_email_of))

    if php_truthy(amount.f_hkd) or php_truthy(profit.f_hkd):
        rows.append(build_money_row(column_hkd().text, amount.f_hkd, profit.f_hkd,
                                    amount.f_convert_hkd, profit.f_convert_hkd,
                                    member_email_of=member_email_of))
    return rows


# ---------------------------------------------------------------------------
# 表（对应 EchoMoneyParagraphs）
# ---------------------------------------------------------------------------


def build_money_table(groups: Iterable[Any] = None,
                      usdcny_ref: Any = None, hkcny_ref: Any = None,
                      group_link_of: Callable[[Any], Any] = None,
                      member_email_of: Callable[[], Any] = None) -> dict:
    """对应 ``EchoMoneyParagraphs()``（``_stockaccount.php:213``）。

    .. code-block:: php

        $strUSDCNY = $uscny_ref ? $uscny_ref->GetPrice() : false;
        $strHKDCNY = $hkcny_ref ? $hkcny_ref->GetPrice() : false;
        if (EchoTableParagraphBegin($arColumn, 'money', GetMyStockGroupLink().$profit_col->GetDisplay()))
        {
            foreach ($arGroup as $group)  _EchoMoneyGroupData($this, $group, $strUSDCNY, $strHKDCNY);
            EchoTableParagraphEnd();
        }

    汇率引用为空时按 PHP 的 ``: false`` 处理 —— ``MultiCurrency::Convert()``
    于是把折美元/折港币直接给 0.0（见 :mod:`core.stockgroup` 第 3 条）。
    """
    usdcny = _invoke(usdcny_ref, "get_price", "GetPrice") if usdcny_ref else False
    hkcny = _invoke(hkcny_ref, "get_price", "GetPrice") if hkcny_ref else False

    rows = []
    for group in groups or ():
        rows.extend(build_money_group_rows(
            group, usdcny, hkcny,
            group_link_of=group_link_of, member_email_of=member_email_of))

    return {
        "id": MONEY_TABLE_ID,
        "columns": columns_to_dicts(money_columns()),
        "rows": rows,
        "title": money_title(),
        "title_link": money_title_link(),
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
