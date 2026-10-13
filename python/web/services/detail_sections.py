"""详情页共用的「历史类」段落构建器。

## 为什么单独拆出来

``/fund/<symbol>``（CK1）与 ``/stock/<symbol>``（CK3）这两张详情页在
``_mystock.php`` 里共用同一批尾部段落：

.. code-block:: php

    EchoNetValueCloseParagraph($ref);      // _mystock.php:167
    EchoFundShareParagraph($ref);          // :168
    EchoStockHistoryParagraph($ref);       // :169

三个分支（基金 / FundPair / Holdings）走完之后都会落到这三行。原先这三段
只写在 ``routes/fund.py`` 的 ``_build_*`` 里，CK3 要复用就会变成复制粘贴，
所以抽到这里。

## 三段的共同结构

三个 PHP 段落都是「取一段历史 → 有行就摆表、没行就整段不出现」。PHP 侧的
守卫各不相同，但**效果一致**：

* ``EchoStockHistoryParagraph`` —— ``$result`` 为空时循环不执行，表自然没有；
* ``EchoNetValueCloseParagraph`` —— 显式 ``if ($ref->CountNetValue() == 0) return;``；
* ``EchoFundShareParagraph`` —— 同样是取不到记录就不 echo。

所以这里统一用 ``table.get("rows")`` 为空来触发隐藏，而不是摆一张空表。
**不要**给三段各写一份判断。

## 服务缺失与查询异常都按「整段跳过」处理

``service`` 为 ``None``（应用没注入历史服务，测试里常见）或 ``get_table``
抛异常（库里没这张表、符号没记录）时，一律返回 ``None`` 让上层跳过。
这与详情页其它段落的处理一致 —— 一张表取不到不该让整页 500。
"""

from __future__ import annotations

from typing import Any

#: 详情页里历史表默认取的行数。PHP 侧 ``EchoStockHistoryParagraph`` 等取的是
#: 最近若干条，这里跟随 ``/history/*`` 页面已经在用的 ``0, 10``。
DEFAULT_START = 0
DEFAULT_COUNT = 10


def build_history_table(
    service: Any,
    kind: str,
    symbol: str,
    start: int = DEFAULT_START,
    count: int = DEFAULT_COUNT,
) -> dict[str, Any] | None:
    """取一张历史表；取不到或没有行就返回 ``None``。

    :param service: 历史服务（``history_service``），为 ``None`` 时直接跳过。
    :param kind: 表种类，如 ``stockhistory`` / ``netvalueclose`` /
        ``shareshistory``；与 ``/history/<kind>/<symbol>`` 用的是同一套。
    """
    if service is None:
        return None
    try:
        table = service.get_table(kind, symbol, start, count)
    except Exception:
        #: 库里没这张表、符号没记录都走这里 —— 按「整段跳过」处理
        return None
    return table if table.get("rows") else None


def build_stock_history(service: Any, symbol: str) -> dict[str, Any] | None:
    """「历史价格」表（``php/ui/stockhistoryparagraph.php``）。

    与 ``/history/stockhistory/<sym>`` 是同一张表、同一个服务。基金符号会按
    估值标的自动带出比价列（见 ``history_service`` 内部的 ``_compare_symbol``）。
    """
    return build_history_table(service, "stockhistory", symbol)


def build_netvalue_close(service: Any, symbol: str) -> dict[str, Any] | None:
    """「净值价格比较」表（``php/ui/netvaluecloseparagraph.php``）。

    PHP 的守卫是 ``if ($ref->CountNetValue() == 0) return;`` —— 没有净值数据时
    整段不显示。这里用 ``rows`` 为空达到同样效果。
    """
    return build_history_table(service, "netvalueclose", symbol)


def build_shares(service: Any, symbol: str) -> dict[str, Any] | None:
    """「基金场内份额」表（``php/ui/fundshareparagraph.php``）。

    复用历史服务的 ``shareshistory`` kind —— 与
    ``/history/shareshistory/<sym>`` 是同一张表，没有第二套算法。

    **权限差异**：生产端的独立页面 ``fundsharecn.php`` 要登录，而详情页里
    这段不带鉴权。本版跟随详情页（不鉴权）。
    """
    return build_history_table(service, "shareshistory", symbol)
