"""基金详情页里**嵌入**的那张「校准记录」表。

对应 ``php/ui/calibrationhistoryparagraph.php``（80 行）。

## 为什么不在这里重写一遍取数

``HistoryService`` 的 ``calibrationhistory`` 已经照着同一个 PHP 文件写好了：
``IsFundA`` 决定要不要"净值 / 对冲值"两列、``CalibrationSql`` 取数、
``StockCalcHedge`` 算对冲、``calibration_hedge_multiplier`` 按符号取倍数 ——
一行不差。那边服务的是 ``/history/calibrationhistory/<sym>`` 整页。

**PHP 里这两处本来就是同一个函数**：``EchoCalibrationHistoryParagraph()``
既被 ``stockhistorycn.php`` 那类独立页面调用，也被下面两个位置内联：

* ``php/ui/fundestparagraph.php:157`` —— ``($ref, 0, 1)``，基金自己，紧跟估值表；
* ``php/ui/smaparagraph.php:218``     —— ``($ref->GetEstRef(), 0, 1)``，
  中间层标的（``^GSPC``），摆在期货均线表**之前**。

所以本模块只做形状转换：把"整页"结果转成详情页统一的段落结构
（``{"id", "columns", "rows", "count"}``），交给 ``_data_table.html`` 渲染。

## 一处有意偏离：**没有记录就整段不画**

PHP 的 ``EchoTableParagraphBegin()`` 会先 echo 表头，没有数据行时页面上就留下
一张只有表头的空表。本版返回 ``None``，模板整段跳过 ——
与详情页其它四张表的处理一致（见 ``trading_service`` / ``sma_service``）。

另一个实际理由：``calibrationhistory`` 只有**自动校准跑过**才有数据，
演示库里多数符号是空的，一律画表头会让详情页挂一堆空框。

## DOM id 沿用 PHP

PHP 用 ``$strSymbol.'calibrationhistory'`` 当表格 id
（``EchoTableParagraphBegin($ar, $strSymbol.'calibrationhistory', …)``），
本版照抄，免得外部锚点与爬虫书签失效。
"""

from __future__ import annotations

from typing import Any, Callable

from web.bootstrap import install_paths

install_paths()


#: PHP ``EchoCalibrationHistoryParagraph($ref, $iStart = 0, $iNum = TABLE_COMMON_DISPLAY)``
#: 的两个调用点都传了 ``(0, 1)`` —— **只显示最近一条**校准记录。
#: 这是"最近一次校准是什么时候、值多少"的摘要，不是历史明细
#: （明细在那个独立页面里）。
#: 单独开一个常量是因为下面两个位置都要用，写死两处容易改成不一样。
DEFAULT_CALIBRATION_ROWS = 1

#: 表格 id 的后缀，对应 PHP 里那个字符串拼接。
CALIBRATION_ID_SUFFIX = "calibrationhistory"

#: 历史页的 kind —— 取数走 ``HistoryService``，这里只是名字。
CALIBRATION_KIND = "calibrationhistory"

#: 页面标题。PHP 里这张表**没有标题**，只有表上方一个指向完整记录的链接；
#: 详情页加了这一行，是为了在并排好几张表时能分清哪张是哪张。
CALIBRATION_TITLE = "校准记录"


class CalibrationService:
    """把 ``HistoryService`` 的结果摆成详情页的一段表格。

    ==============================  ========================================
    构造参数                          用途
    ==============================  ========================================
    ``history_factory``              生成取数服务对象。注入是为了测试不打库；
                                     缺省从 ``web.services.history_service``
                                     现场建一个（需要数据库连接）。
    ``link_builder``                 由符号与页面名生成站内 URL。服务层不认识
                                     ``url_for``，必须注入；没给就没有"查看
                                     全部记录"那个链接。
    ==============================  ========================================
    """

    def __init__(self, history_factory: Callable[..., Any] = None,
                 link_builder: Callable[..., Any] = None) -> None:
        self._history_factory = history_factory
        self.link_builder = link_builder

    # -- 对外 ---------------------------------------------------------------
    def table(self, symbol: Any, start: int = 0,
              num: int = DEFAULT_CALIBRATION_ROWS) -> Any:
        """取一个符号的校准记录；**没有记录返回 ``None``**（理由见模块 docstring）。

        ``symbol`` 既可以是基金自己（``SZ161125``），也可以是中间层标的
        （``^GSPC``）—— 两个调用点的差别只在这里。
        """
        symbol = str(symbol or "")
        if not symbol:
            return None

        rows = self._load_rows(symbol, start, num)
        if not rows:
            return None

        return {
            "id": "%s%s" % (symbol, CALIBRATION_ID_SUFFIX),
            "title": CALIBRATION_TITLE,
            "columns": rows["columns"],
            "rows": rows["rows"],
            "count": len(rows["rows"]),
            "symbol": symbol,
            #: 页面上只显示最近一条，完整记录另有独立页面
            "total": rows["total"],
            "link": self._history_link(symbol),
        }

    # -- PHP 原名 -----------------------------------------------------------
    EchoCalibrationHistoryParagraph = table

    # -- 内部 ---------------------------------------------------------------
    def _load_rows(self, symbol: str, start: int, num: int) -> Any:
        """向取数服务要一份记录；拿不到就返回 ``None``。

        异常一律吞掉并当作"没有记录" —— 校准记录是详情页的可选装饰，
        不该因为它让整页 500。这与本项目中其它四个段落的处理一致。
        """
        try:
            service = self._history_service()
            if service is None:
                return None
            result = service.get_table(CALIBRATION_KIND, symbol, start, num)
        except Exception:
            return None
        rows = result.get("rows") or []
        if not rows:
            return None
        return {
            "columns": result.get("columns") or [],
            "rows": rows,
            "total": result.get("total", len(rows)),
        }

    def _history_service(self) -> Any:
        if self._history_factory is not None:
            return self._history_factory()
        try:
            # 走 ``default_history_service_factory`` 而不是自己 new 一个数据源：
            # 那边会用**当前请求**的数据库连接（``web.db.get_db``），与其它四个
            # 段落同源。自己造一个会在每个请求上多开一条连接。
            from web.services.history_service import default_history_service_factory

            return default_history_service_factory()
        except Exception:
            # 连不上库 → 当作没有校准记录（详见 :meth:`_load_rows` 的说明）
            return None

    def _history_link(self, symbol: str) -> Any:
        """指向完整校准记录页面的链接。

        对应 PHP 的 ``GetCalibrationHistoryLink($strSymbol)``
        （只有 ``IsTableCommonDisplay()`` 为真时才挂，这里两个调用点都是真）。
        """
        if self.link_builder is None or not symbol:
            return None
        url = self.link_builder(symbol, CALIBRATION_KIND)
        if not url:
            return None
        return {"text": "校准记录", "url": url}


def default_calibration_service_factory(
    link_builder: Callable[..., Any] = None,
) -> CalibrationService:
    """建一个默认服务。

    ``link_builder`` 要由装配层给 —— 指向完整校准记录页的 URL 得靠 ``url_for``
    拼，而服务层刻意不认识 ``url_for``（认识的话每个测试都得先搭请求上下文）。
    不给就是"没有那个链接"，表格照常渲染。
    """
    return CalibrationService(link_builder=link_builder)
