"""行情数据源抽象层：给实时行情预留可替换的接口。

为什么要这一层
--------------
PHP 那边实时行情是**写死新浪**的：``GetSinaQuotes()`` 直接拼 ``hq.sinajs.cn``
的 URL，没有抽象、也没有对外接口（详见 ``README_quotes_migration.md`` 的分析）。
Python 这边把"取行情"抽成 :class:`MarketQuoteSource`，换源时只需要新写一个子类
并注册，Web 层与解析层都不用改。

当前实现
--------
* ``sina``：新浪 ``hq.sinajs.cn``，由 :class:`~sinaquotes.SinaQuoteClient` 承载。
  原生响应是逐行的 ``var hq_str_符号="字段,字段,...";``，解析交给 ``stockref``。

新增一个数据源需要做什么
------------------------
1. 继承 :class:`MarketQuoteSource`，实现 ``name``、``fetch_lines()`` 两个成员；
2. 用 :func:`register_quote_source` 注册；
3. 设置环境变量 ``PALMMICRO_QUOTE_SOURCE=<名字>`` 即可切换。

``fetch_lines()`` 的返回约定（很重要）
-------------------------------------
返回 ``{请求时传入的符号: 该符号的原始文本}``，键必须是**调用方传入的符号**，
这样 ``stockref`` 的解析器不用关心数据源用什么内部代码。新浪这边调用方传的
就是新浪符号（``sz161130``、``gb_qqq``），所以键值一致。

返回 ``None`` 表示整批失败（对应 PHP 的 ``GetSinaQuotes()`` 返回 ``false``）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Mapping, Sequence


# 环境变量名。集中在这里，避免散落各处拼字符串。
ENV_SOURCE = "PALMMICRO_QUOTE_SOURCE"


class MarketQuoteSource:
    """行情数据源接口。

    子类必须实现 :meth:`fetch_lines`；:meth:`fetch_raw` 有默认实现（把各行拼起来），
    只有需要完整原始响应（例如保留响应头或整体结构）的数据源才需要覆写。
    """

    #: 数据源标识，用于日志与 ``PALMMICRO_QUOTE_SOURCE``
    name = "abstract"

    def fetch_lines(self, symbols: Sequence[str]) -> Any:
        """取一批符号的行情，返回 ``{符号: 原始文本}``；整批失败返回 ``None``。

        ``symbols`` 是**数据源自己的代码**（新浪就是 ``sz161130`` 这种），
        由 ``stocksymbol.StockSymbol.get_sina_symbol()`` 之类的方法转换好再传进来。
        """
        raise NotImplementedError

    def fetch_raw(self, symbols: Sequence[str]) -> Any:
        """取整批原始响应文本（按行）。默认把 :meth:`fetch_lines` 的结果拼起来。

        ``stockprefetch._prefetch()`` 需要"按行、与请求顺序一致"的文本，
        所以这里按传入顺序拼接，缺行时补空行以保持下标对应。
        """
        lines = self.fetch_lines(symbols)
        if lines is None:
            return None
        return "\n".join(lines.get(symbol, "") for symbol in symbols)

    def describe(self) -> Dict[str, Any]:
        """数据源自述，便于 ``/api/status`` 之类的诊断输出。"""
        return {"name": self.name}


class QuoteSourceRegistry:
    """数据源注册表。

    默认在模块导入时注册新浪源；用 :func:`register_quote_source` 追加其它源。
    """

    def __init__(self):
        self._factories: Dict[str, Any] = {}

    def register(self, name: str, factory) -> None:
        """注册一个工厂函数：``factory(**kwargs) -> MarketQuoteSource``。"""
        self._factories[name] = factory

    def names(self) -> List[str]:
        return sorted(self._factories)

    def create(self, name: str = None, **kwargs) -> MarketQuoteSource:
        """按名字创建数据源；``name`` 缺省取环境变量或 ``sina``。"""
        target = name or os.getenv(ENV_SOURCE) or DEFAULT_QUOTE_SOURCE
        if target not in self._factories:
            raise ValueError(
                "未知行情数据源 %r；可用的有：%s" % (target, ", ".join(self.names()))
            )
        return self._factories[target](**kwargs)


#: 默认数据源。与 PHP 行为一致（PHP 只支持新浪）。
DEFAULT_QUOTE_SOURCE = "sina"

registry = QuoteSourceRegistry()


def register_quote_source(name: str, factory) -> None:
    """注册行情数据源工厂。"""
    registry.register(name, factory)


def get_quote_source(name: str = None, **kwargs) -> MarketQuoteSource:
    """取一个行情数据源实例。

    ``name`` 缺省时依次看环境变量 ``PALMMICRO_QUOTE_SOURCE`` 与默认值，
    所以部署时只要改环境变量就能换源，不用改代码。
    """
    return registry.create(name, **kwargs)


def list_quote_sources() -> List[str]:
    """列出已注册的数据源名字。"""
    return registry.names()


def _create_sina(**kwargs) -> MarketQuoteSource:
    """新浪数据源的工厂（延迟导入，避免模块级循环依赖）。"""
    from source.sinaquotes import SinaMarketQuoteSource

    return SinaMarketQuoteSource(**kwargs)


register_quote_source("sina", _create_sina)
