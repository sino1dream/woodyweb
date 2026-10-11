"""均线（SMA）表 —— 对应 ``php/ui/smaparagraph.php``（225 行）。

数值与取数都在 ``auto/stockhistory.py``（``php/stockhis.php``），本模块只做
**把 ``StockHistory`` 的结果摆成一张表**这一件事。

## 两个 PHP 用过的全局变量，这里改成注入

.. code-block:: php

    function EchoSmaParagraph($ref, $str = false, $cb_ref = false, $callback = false, $callback2 = false)
    {
        $bAfterHour = LayoutUseWide();                       // ← 布局层
        ...
        $bAdmin = DebugIsAdmin();                            // ← 🔴 membership 域
    }

* ``LayoutUseWide()`` 属于未迁移的 ``php/layout.php``。它决定要不要多一列
  "盘后"，所以做成构造参数 ``wide``；
* ``DebugIsAdmin()`` 要有账号体系才有意义（本项目还没迁），默认 ``False``，
  需要时由调用方注入 —— 与 ``trading_service`` 的 ``link_builder``、
  ``stock_service`` 的 admin 列同款处理：**先别编，也不要假装能判定**。

## 三种"回调"是 PHP 的核心抽象，照搬

``$callback`` 一个 callable 承担两件事：

* **不传价格** → 返回"这一对照行要显示的价格该用谁的口径"（一个引用对象）；
* **传价格** → 返回按该口径换算后的**数值**。

``$callback2`` 则是"每行再补一格"的可选钩子（调用时传 ``($strVal, $strNext)``）。

Python 里就是两个 callable，语义完全一致 —— 四个回调
（``_callbackQdiiSma`` / ``_callbackFundPairSma`` / ``_callbackAhPairSma`` /
``_callbackFutureSma``）逐个落在本模块里。

## ⚠️ 又一次遇到 PHP 的 ``'0'`` 假值

``_getSmaCallbackPriceDisplay()`` 的 ``if ($strVal)`` 与 trading 那处是**同一个坑**：
Python 的 ``'0'`` 是真值。本模块一律走 :func:`_php_truthy`。

## 标题里的链接做成了结构化数据

PHP 的 ``_getSmaParagraphMemo()`` 把四条链接和"肝胆/牛熊"拼成一句 HTML。
本版返回 dict（``memo``），由**路由层**负责拼 —— 站内链接需要 ``url_for``，
而服务层在没有请求上下文时也要能跑（脚本导出、测试）。这与第三轮把
``GetMenuLink()`` 拆成 ``paging`` dict 是同一个做法。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from web.bootstrap import install_paths

install_paths()

from historytable import TableRow  # noqa: E402
from futurepremium import future_sma_value, ref_get_future_premium  # noqa: E402
from pairestimate import est_from_pair, est_to_pair  # noqa: E402
import smaconfig  # noqa: E402
from stockhistory import StockHistory  # noqa: E402
from uicolumn import (  # noqa: E402
    STOCK_DISP_PREMIUM,
    TableColumn,
    column_base,
    column_est,
    columns_to_dicts,
)

from web.services.reference_service import percentage_cell  # noqa: E402

#: ``EchoTableParagraphBegin($arColumn, 'smatable', $str)`` 的 ``$strId``。
SMA_TABLE_ID = "smatable"

#: ``new TableColumn('均线', 90)``。
SMA_LABEL_TEXT = "均线"
SMA_LABEL_WIDTH = 90

#: ``new TableColumnEst('T+1')`` / ``new TableColumnEst('盘后')`` 的前缀。
NEXT_PREFIX = "T+1"
AFTER_HOUR_PREFIX = "盘后"

#: ``_getSmaRow()`` 那张字面对照表（``php/ui/smaparagraph.php:7``）。
#: key 只有单个字母：''D''/''W''/''M'' 是周期，''BOLLUP''/''BOLLDN'' 是布林，
#: ''EMA50''/''EMA200'' 是牛熊分界 —— 这两行走的是"整串查表"那条分支。
SMA_ROW_TEXTS = {
    "D": "日",
    "W": "周",
    "M": "月",
    "BOLLUP": "布林上轨",
    "BOLLDN": "布林下轨",
    "EMA50": "小牛熊分界",
    "EMA200": "牛熊分界",
}


def sma_row_text(key: Any) -> str:
    """``_getSmaRow($strKey)``：把均线的 key 翻成中文行标题。

    PHP 那段是三层字符串切割，本版拆开写但**分支顺序一模一样**：

    .. code-block:: php

        $strFirst = substr($strKey, 0, 1);
        if ($strFirst == 'E')   return $arRow[$strKey];      // EMA50 / EMA200
        $strRest = substr($strKey, 1);
        if (substr($strKey, 1, 1) == 'B')                    // DBOLLUP / WBOLLUP…
            return ($strFirst == 'D') ? $arRow[$strRest] : $arRow[$strFirst].$arRow[$strRest];
        return $strRest.$arRow[$strFirst];                   // D5 → '5日'

    三种形态要分清：

    ==================  ====================  ==============================
    key                 走的分支              结果
    ==================  ====================  ==============================
    ``D5`` / ``W20``    最后一行              数字在前**单位在后**：``5日``/``20周``
    ``DBOLLUP``         第二条、且是日线      **只**给布林名（不加"日"）
    ``WBOLLUP``         第二条、非日线        单位在前：``周布林上轨``
    ``EMA50``           第一条               整串查表
    ==================  ====================  ==============================

    日线的布林为什么特殊：**生产端那一格就叫"布林上轨"**，不叫"日布林上轨"。
    这条看着像 PHP 疏漏，但改了页面上就多两个字。

    未知 key（比如哪天多了个 ``EMA100``）**返回原 key** 而不是抛异常 ——
    表格少一个字总比整段 500 强。
    """
    text = str(key or "")
    if not text:
        return ""
    first = text[0]
    if first == "E":
        return SMA_ROW_TEXTS.get(text, text)

    rest = text[1:]
    # PHP 判的是 substr($strKey, 1, 1) == 'B'，等价于"第二位是 B"
    if len(text) > 1 and text[1] == "B":
        if rest not in SMA_ROW_TEXTS:
            return text
        if first == "D":
            return SMA_ROW_TEXTS[rest]
        return "%s%s" % (SMA_ROW_TEXTS.get(first, first), SMA_ROW_TEXTS[rest])
    return "%s%s" % (rest, SMA_ROW_TEXTS.get(first, first))


class SmaService:
    """均线表服务（一个符号一张 ``smatable``）。

    ``history_factory`` 允许调用方替换 ``StockHistory`` 的构造方式
    （测试里塞假实现、生产里换数据源），缺省用 :class:`StockHistory` 本体。
    """

    def __init__(self, wide: bool = False, is_admin: bool = False,
                 history_factory: Callable[..., Any] = None,
                 warning_source: Any = None,
                 config_factory: Callable[..., Any] = None) -> None:
        self.wide = bool(wide)
        #: 对应 ``DebugIsAdmin()``。默认 False —— 管理员专属的链接<｜hy_place▁holder▁no▁813｜>接线。
        self.is_admin = bool(is_admin)
        self._history_factory = history_factory
        self._warning_source = warning_source
        #: ``符号 -> 配置存放处``。``None`` 时 ``StockHistory`` 用自己的内存实现。
        #: 见 ``auto/smaconfig.py`` —— 文件后端**默认不启用**（要显式设目录），
        #: 这个口子的存在只是让"要不要落盘"变成一处可替换的决策。
        self._config_factory = config_factory

    # -- 对外：三种段落 ---------------------------------------------------
    def sma_table(self, ref: Any, memo: Any = False, cb_ref: Any = False,
                  callback: Callable[..., Any] = False,
                  callback2: Callable[..., Any] = False) -> Any:
        """``EchoSmaParagraph($ref, $str, $cb_ref, $callback, $callback2)``。

        返回给模板的 dict；``StockHistory`` 没有均线（该标的没历史数据时）
        返回 ``None``，让上层整段跳过 —— 与详情页其它表的处理一致。

        ## ``memo`` 用 ``is False`` 严格判断，不是"假值就替换"

        .. code-block:: php

            if ($str === false)	$str = _getSmaParagraphMemo($his);

        是 ``===`` 而不是 ``==``，所以 ``EchoFundPairSmaParagraphs()`` 传进来的
        **空串不会被替换成默认 memo** —— 那张表就是没有标题文字的。

        这在 PHP 里看着像笔误，但它决定了多个段落并排展示时的样子
        （一组配对标的会 echo 好几张均线表，只有第一张带标题更清楚）。
        照抄，并在 ``MemoTest`` 里钉住。
        """
        history = self._build_history(ref)
        if history is None:
            return None

        after_hour = bool(self.wide) and bool(getattr(history, "need_after_hour_est", lambda: False)())
        sma = _sma_of(history)
        if not sma:
            return None
        if not _has_any_level(sma):
            # 见 :func:`_has_any_level` —— 这是一处**有意偏离 PHP**的守卫
            return None

        columns = self._columns(after_hour, cb_ref, callback, callback2)
        rows = []
        for key in sma:
            rows.append(self._row(history, key, sma[key], cb_ref, callback, callback2, after_hour))

        return {
            "id": SMA_TABLE_ID,
            "columns": columns_to_dicts(columns),
            "rows": rows,
            "count": len(rows),
            "memo": self.memo(history) if memo is False else memo,
            "warning": self.warning(ref),
            "symbol": _text(_invoke(ref, "get_symbol", "GetSymbol")),
            #: 最大格数 / 列数：两者不等说明照抄时抄漏了一格
            "cell_count": max((len(row.cells) for row in rows), default=0),
            "column_count": len(columns),
            #: PHP 末尾 DebugIsAdmin() 时才 echo 的那串均线顺序
            "order": list(getattr(history, "order", ()) or ()) if self.is_admin else [],
        }

    def qdii_table(self, qdii_ref: Any, callback2: Callable[..., Any] = None) -> Any:
        """``EchoQdiiSmaParagraph($qdii_ref, $callback2)``。

        PHP 传的是 ``$qdii_ref->GetEstRef()``（中间层标的）当被算均线那个 ref，
        而 ``$cb_ref`` 是 ``$qdii_ref`` 自己 —— 回调要用它的估值口径。
        """
        est_ref = _invoke(qdii_ref, "get_est_ref", "GetEstRef")
        if est_ref is None:
            return None
        return self.sma_table(est_ref, False, qdii_ref, callback_qdii_sma, callback2)

    def ah_pair_table(self, ref: Any, memo: Any = False,
                      callback2: Callable[..., Any] = False) -> Any:
        """``EchoAhPairSmaParagraph($ref, $str, $callback2)``（``:174``）。

        PHP 传的是 ``EchoSmaParagraph($ref, $str, $ref, '_callbackAhPairSma', …)`` ——
        注意**被算均线的 ref 与 cb_ref 是同一个对象**，这点与 QDII 那处不同
        （那边算均线的是 ``GetEstRef()``）。
        """
        return self.sma_table(ref, memo, ref, callback_ah_pair_sma, callback2)

    def fund_pair_table(self, ref: Any, memo: Any = False,
                        callback2: Callable[..., Any] = False) -> Any:
        """``EchoFundPairSmaParagraph($ref, $str, $callback2)``（``:164``）。

        算均线的是 **``GetPairRef()``**（配对标的），显示/换算的 cb_ref 是自己。
        """
        pair_ref = _invoke(ref, "get_pair_ref", "GetPairRef")
        if pair_ref is None:
            return None
        return self.sma_table(pair_ref, memo, ref, callback_fund_pair_sma, callback2)

    def fund_pair_tables(self, ref: Any, pair_refs: Any = (),
                         callback2: Callable[..., Any] = False) -> list:
        """``EchoFundPairSmaParagraphs($ref, $arFundPairRef, $callback2)``（``:156``）。

        PHP 在循环里给每个配对标的 echo 一张表，且**传空串当标题**
        （``EchoSmaParagraph($ref, '', $fund_pair_ref, …)``）——
        于是只有循环外自己那个更有信息量的标题会留着。
        这正是 :meth:`sma_table` 里 ``memo is False`` 那条严格判断要保的行为。
        """
        tables = []
        for pair_ref in pair_refs or ():
            table = self.sma_table(ref, "", pair_ref, callback_fund_pair_sma, callback2)
            if table:
                tables.append(table)
        return tables

    def future_table(self, ref: Any, source: Any = None,
                     callback2: Callable[..., Any] = False) -> Any:
        """``EchoFutureSmaParagraph($ref, $callback2)``（``:212``）。

        .. code-block:: php

            if ($realtime_ref = $ref->GetRealtimeRef())
            {
                if ($fPremium = RefGetFuturePremium($realtime_ref))
                {
                    EchoCalibrationHistoryParagraph($ref->GetEstRef(), 0, 1);
                    $str = '理论溢价：'.number_format($fPremium, 4).' '.…;
                    EchoSmaParagraph($ref->GetEstRef(), $str, $realtime_ref, '_callbackFutureSma', $callback2);
                }
            }

        ## 两件 PHP 干了而本版**不做**的事

        1. ``EchoCalibrationHistoryParagraph()`` 会在这张均线表**之前**再输出
           一张校准历史表（``php/ui/calibrationhistoryparagraph.php``，未迁）。
           本版返回 ``calibration_history`` 这个标记位，由路由层决定怎么摆 ——
           服务层不该"顺手 echo 第二张表"，那是调用方的事；
        2. 标题末尾那个 ``GetStockOptionLink(STOCK_OPTION_PREMIUM, …)``
           是管理员专属设置链接（🔴 membership 域），本版只给纯文本前缀，
           链接等那一档迁完再补（与 ``trading_service`` 的 ``link_builder`` 同款）。

        没有配对期货、或 ``futurepremium`` 表里没有该符号时返回 ``None``
        —— 与 PHP 的 ``if`` 守卫一致，此时整段不渲染。
        """
        realtime_ref = _invoke(ref, "get_realtime_ref", "GetRealtimeRef")
        if not _php_truthy(realtime_ref):
            return None

        premium = ref_get_future_premium(source, realtime_ref)
        if not _php_truthy(premium):
            return None

        est_ref = _invoke(ref, "get_est_ref", "GetEstRef")
        if est_ref is None:
            return None

        memo = "理论溢价：%s" % _format_number(premium, 4)
        table = self.sma_table(est_ref, memo, realtime_ref,
                               self._future_callback(source), callback2)
        if not table:
            return None
        table["premium"] = premium
        table["premium_text"] = memo
        #: PHP 在这里先 echo 一张校准历史表；本版只报告"需要"，不自己输出
        table["calibration_history"] = {"ref": est_ref, "start": 0, "num": 1}
        return table

    @staticmethod
    def _future_callback(source: Any) -> Callable[..., Any]:
        """``_callbackFutureSma``（``:200``）：把估值按理论溢价换算成期货价。

        回调里**每次都去读一次** ``futurepremium`` —— PHP 也是这样
        （``RefGetFuturePremium($ref)`` 在回调体内），好处是表里的每一行
        都用同一份溢价，换成缓存反而要注意"什么时候失效"。
        """
        def callback(ref: Any, est: Any = False) -> Any:
            if not _php_truthy(est):
                return ref
            premium = ref_get_future_premium(source, ref)
            return future_sma_value(ref, premium, est)

        return callback

    # -- 内部 -------------------------------------------------------------
    def _build_history(self, ref: Any) -> Any:
        """``new StockHistory($ref, $bAfterHour)``。

        PHP 用 ``LayoutUseWide()`` 的初值去构造，**构造之后**再问一次
        ``NeedAfterHourEst()`` 收窄（见 :meth:`sma_table` 里那行
        ``if ($bAfterHour) $bAfterHour = $his->NeedAfterHourEst()``）。
        这两步不能合成一步 —— ``StockHistory`` 内部的分支依赖同一个判断。
        """
        factory = self._history_factory
        try:
            if factory is not None:
                return factory(ref, self.wide)
            return StockHistory(ref, after_hour=self.wide,
                                config=self._config_of(ref))
        except Exception:
            # 没有数据源/建不出历史：整段跳过，页面不该因此 500
            return None

    def _config_of(self, ref: Any) -> Any:
        """给这个符号找个配置存放处；没有 ``config_factory`` 就返回 ``None``。

        :attr:`_config_factory` 为 ``None`` 时不传 ``config``，由
        ``StockHistory`` 自己用内存实现 —— 于是"渲染页面不落盘"是**默认行为**，
        不是需要处处小心的约定。
        """
        if self._config_factory is None:
            return None
        try:
            return self._config_factory(ref)
        except Exception:
            # 找存放处失败不该让整段 500 —— 退回内存（StockHistory 的缺省）
            return None

    def _columns(self, after_hour: bool, cb_ref: Any,
                 callback: Callable[..., Any] = None,
                 callback2: Callable[..., Any] = None) -> list:
        """``EchoSmaParagraph()`` 里那段 ``$ar`` 的组装（``:113``）。

        .. code-block:: php

            $premium_col = new TableColumnPremium();
            $next_col    = new TableColumnEst('T+1');
            $ar = array(new TableColumn('均线', 90), new TableColumnEst(),
                        $premium_col, $next_col, $premium_col);
            if ($bAfterHour) $ar[] = $afterhour_col;
            if ($callback) { $ar[] = new TableColumnStock($est_ref, 90);
                             $ar[] = $next_col;
                             if ($bAfterHour) $ar[] = $afterhour_col; }
            if ($callback2) $ar[] = new TableColumn(call_user_func($callback2), 90);

        一个照抄的细节：**``$premium_col`` 是同一个对象放了两次**（第 3 与第 5 列）。
        列头因此长得一样，这是 PHP 的原样，不去去重。
        """
        premium = _premium_column()
        next_col = column_est(NEXT_PREFIX)
        after_col = column_est(AFTER_HOUR_PREFIX)

        columns = [
            column_base(SMA_LABEL_TEXT, SMA_LABEL_WIDTH),
            column_est(),
            premium,
            next_col,
            premium,
        ]
        if after_hour:
            columns.append(after_col)
        # PHP 是 if ($callback) / if ($callback2)，不是 !== null
        if _php_truthy(callback):
            columns.append(_stock_column(cb_ref, callback))
            columns.append(next_col)
            if after_hour:
                columns.append(after_col)
        if _php_truthy(callback2):
            columns.append(column_base(_text(callback2()), 90))
        return columns

    def _row(self, history: Any, key: str, value: Any, cb_ref: Any,
             callback: Callable[..., Any] = None,
             callback2: Callable[..., Any] = None,
             after_hour: bool = False) -> TableRow:
        """``_echoSmaTableItem()``（``:29``）：一行均线。

        格序列是**变长**的（``after_hour`` / ``callback`` / ``callback2``
        各自追加），与 PHP 一致，不做补齐 —— 少了哪一段一眼就能从
        ``cell_count`` vs ``column_count`` 看出来。
        """
        ref = _invoke(history, "get_ref", "GetRef")
        row = TableRow()
        row.add(sma_row_text(key))

        fval = _floatval(value)
        row.add(_text(_invoke(ref, "get_price_display", "GetPriceDisplay", fval)))
        row.add(percentage_cell(_text(
            _invoke(ref, "get_percentage_display", "GetPercentageDisplay", fval))))

        # PHP: if ($strNext = $his->arNext[$strKey]) —— 赋值发生在 if 里，
        # 所以 else 分支里 $strNext 是 false，后面 callback2 也收到 false。
        str_next = _values_of(history, "next_values").get(key)
        if _php_truthy(str_next):
            fnext = _floatval(str_next)
            row.add(_text(_invoke(ref, "get_price_display", "GetPriceDisplay", fnext)))
            row.add(percentage_cell(_text(
                _invoke(ref, "get_percentage_display", "GetPercentageDisplay", fnext))))
        else:
            str_next = False
            row.add("")
            row.add("")

        if after_hour:
            str_after = _values_of(history, "after_hour_values").get(key)
            if _php_truthy(str_after):
                row.add(_text(_invoke(ref, "get_price_display",
                                      "GetPriceDisplay", _floatval(str_after))))
            else:
                row.add("")

        if _php_truthy(callback):
            row.add(_callback_price_display(callback, cb_ref, value))
            row.add(_callback_price_display(callback, cb_ref, str_next))
            if after_hour:
                row.add(_callback_price_display(
                    callback, cb_ref, _values_of(history, "after_hour_values").get(key)))

        if _php_truthy(callback2):
            # PHP: call_user_func($callback2, $strVal, $strNext) —— strNext 可能是 false
            try:
                row.add(_text(callback2(value, str_next)))
            except TypeError:
                row.add("")

        return row

    # -- 标题与告警 -------------------------------------------------------
    def memo(self, history: Any) -> dict:
        """``_getSmaParagraphMemo($his)``（``:68``）的**结构化版本**。

        PHP 拼出来的是一句带链接的 HTML；本版返回 dict，让路由层去拼
        （站内链接要 ``url_for``，服务层不该有请求上下文）。
        字段与 PHP 那六段一一对应，方便核对。
        """
        ref = _invoke(history, "get_ref", "GetRef")
        return {
            "symbol": _text(_invoke(ref, "get_symbol", "GetSymbol")),
            "start_date": _text(_invoke(history, "get_start_date", "GetStartDate")),
            "bull_bear": _text(_invoke(history, "get_bull_bear", "GetBullBear")),
            "is_admin": self.is_admin,
        }

    def warning(self, ref: Any) -> str:
        """``_getSmaParagraphWarning($ref)``（``:81``）：收盘价冲突告警。

        PHP 的逻辑是拿 ``stockhistory`` 里**该日期的上一条**的 ``adjclose``
        去比 ref 的昨收，差超过 0.0005 就报一行。

        ## 为什么没有直接照抄 ``GetRecordPrev()``

        那条查询要用一个新的 SQL 类（``StockHistorySql::GetRecordPrev()``），
        而它对 gaia 的引用虞只有一个地方。本版把它收进
        ``auto/stockhistory.py`` 的 ``StockHistorySql`` 里统一管，
        这样 alarm 与历史取数用的是同一份实现。
        （如果没有数据源就返回空串 —— 告警缺失不该让页面上出现假警报。）
        """
        if self._warning_source is None:
            return ""
        try:
            return str(self._warning_source(ref) or "")
        except Exception:
            return ""


def callback_qdii_sma(qdii_ref: Any, est: Any = False) -> Any:
    """``_callbackQdiiSma($qdii_ref, $strEst = false)``（``:141``）。

    * 不给 ``est`` → 返回**基金自己的场内引用对象**（决定估值怎么显示）；
    * 给 ``est``  → 返回按 QDII 口径换算后的**数值**。

    这就是前面说的"一个 callable 两件事"。
    """
    if not _php_truthy(est):
        return _invoke(qdii_ref, "get_stock_ref", "GetStockRef")
    return _invoke(qdii_ref, "get_qdii_value", "GetQdiiValue", est)


def callback_fund_pair_sma(ref: Any, est: Any = False) -> Any:
    """``_callbackFundPairSma($ref, $strEst = false)``（``:151``）。

    * 不给 ``est`` → 返回 **ref 自己**（配对那一档用的就是它自己的显示口径）；
    * 给 ``est``  → ``$ref->EstFromPair($strEst)``，把配对标的的价格换成我的估值。
    """
    if not _php_truthy(est):
        return ref
    return est_from_pair(ref, est)


def callback_ah_pair_sma(ref: Any, est: Any = False) -> Any:
    """``_callbackAhPairSma($ref, $strEst = false)``（``:169``）。

    与上一处的差别只在"显示口径是谁"：配对回调返回 ref 自己，
    而 **AH 回调返回 ``GetPairRef()``** —— AH 比价那一列要显示的是对方
    （H 股）的价格，不是自己的。
    """
    if not _php_truthy(est):
        return _invoke(ref, "get_pair_ref", "GetPairRef")
    return est_to_pair(ref, est)


def _callback_price_display(callback: Callable[..., Any], cb_ref: Any, value: Any) -> str:
    """``_getSmaCallbackPriceDisplay()``（``:19``）的展开版。

    .. code-block:: php

        if ($strVal) {
            $display_ref = call_user_func($callback, $ref);
            return $display_ref->GetPriceDisplay(call_user_func($callback, $ref, $strVal));
        }
        return '';

    两步：先问回调"用谁的口径显示"，再问回调"这个值换算到那个口径是多少"，
    最后交给 ``display_ref.GetPriceDisplay()`` 生成文本。
    ``if ($strVal)`` 用的是 PHP 假值语义（``'0'`` 为假），见 :func:`_php_truthy`。
    """
    if not _php_truthy(value):
        return ""
    display_ref = callback(cb_ref)
    if display_ref is None:
        return ""
    est_value = callback(cb_ref, value)
    if not _php_truthy(est_value):
        return ""
    return _text(_invoke(display_ref, "get_price_display", "GetPriceDisplay",
                         _floatval(est_value)))


def _values_of(history: Any, attribute: str) -> dict:
    """取 ``StockHistory`` 的 next/after-hour 字典；拿不到当空字典。

    Python 侧叫 ``next_values`` / ``after_hour_values``（对应 PHP 的
    ``arNext`` / ``arAfterHour``）。用 ``getattr`` 是为了让**假的** history
    也能用 —— 测试里塞的对象往往只有其中一部分。
    """
    values = getattr(history, attribute, None)
    return values if isinstance(values, dict) else {}


def _sma_of(history: Any) -> dict:
    """``$his->GetSMA()``。"""
    sma = _invoke(history, "get_sma", "GetSMA")
    return sma if isinstance(sma, dict) else {}


def _has_any_level(sma: dict) -> bool:
    """有没有**任意一档**是非零的 —— 全 0 就当没有历史数据。

    ## 这是一处有意偏离 PHP

    PHP 碰到"该标的没有历史数据"时的表现很糟：``_calcStartDate()`` 拿不到日期，
    后面一串 ``_estSma()`` 全算出 ``0``，于是页面上**摆一整张全是 0 的均线表**。
    生产端每个标的都有历史，所以作者没被这个问题困扰过；演示库里
    ``^NDX`` 一行历史都没有，照抄就会给出一张误导性的表。

    所以这里加一道守卫：全部档位都是 PHP 假值（``0`` / ``'0'`` / ``''``）时
    返回 ``None``，让上层整段跳过 —— 与详情页其它"没有数据就不摆空表"的
    处理一致。等真有历史数据，表自然就出来了。
    """
    for value in sma.values():
        if _php_truthy(value) and _floatval(value) != 0.0:
            return True
    return False


def _premium_column() -> Any:
    """``new TableColumnPremium()`` —— 一行里会用到**两次**（同一个实例）。"""
    from uicolumn import column_premium

    return column_premium()


def _stock_column(cb_ref: Any, callback: Callable[..., Any]) -> Any:
    """``new TableColumnStock($est_ref, 90)``（``:124``）。

    列头是回调**不带价格**时返回的那个引用对象的显示名 —— PHP 里
    ``$est_ref = call_user_func($callback, $cb_ref);`` 就取在这一行之前。
    """
    from uicolumn import table_column_get_stock

    display_ref = callback(cb_ref)
    display = _invoke(display_ref, "get_display", "GetDisplay")
    if display is None:
        display = _text(_invoke(display_ref, "get_symbol", "GetSymbol"))
    return column_base(_text(display) or table_column_get_stock(display or ""), 90)


def _format_number(value: Any, digits: int = 4) -> str:
    """``number_format($fPremium, 4)``：千分位分隔的固定小数位。

    PHP 的 ``number_format`` 默认用 ``,`` 分千位、``.`` 做小数点；
    这里只做"固定小数位 + 千分位"，与页面其它数字保持同款。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return "{:,.{d}f}".format(number, d=int(digits))


def _invoke(ref: Any, snake: str, camel: str, *args: Any) -> Any:
    """按 snake_case 优先、camelCase 兜底调用；取不到返回 ``None``。"""
    if ref is None:
        return None
    method = getattr(ref, snake, None)
    if method is None:
        method = getattr(ref, camel, None)
    if method is None or not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def _text(value: Any) -> str:
    """``None`` / ``False`` → 空串，其余转 ``str``。"""
    if value is None or value is False:
        return ""
    return str(value)


def _floatval(value: Any) -> float:
    """``floatval()``：转不了就当 0.0（PHP 也是这个行为）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _php_truthy(value: Any) -> bool:
    """PHP 的布尔判定。

    ``'0'`` / ``'0.0'`` / ``''`` / ``None`` / ``False`` 都是**假**，
    而它们几个在 Python 里都是真的（除 ``None``/``False``）。
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip() not in ("", "0", "0.0")
    return bool(value)


def default_sma_service_factory() -> SmaService:
    """建立一个默认服务。

    **不带 ``is_admin``、不带链接构造器** —— 那两样要账号体系，
    属于 🔴 membership 域。等那一档迁完在这里补即可，本模块不用改。

    ``wide`` 默认 False：PHP 的 ``LayoutUseWide()`` 属于未迁移的布局层，
    在能判断"是不是宽屏"之前，保守地不加"盘后"那一列 ——
    少一列只是信息少，多一列则是编造。

    ## 配置存放处：默认内存

    ``smaconfig.file_store_factory()`` 返回"按符号给一个存放处"的可调用，**但
    它读不到目录时就恒给内存实现** —— 所以不开 ``PALMMICRO_SMA_CACHE_DIR``
    就等于没开文件后端，一个字节都不写盘。理由见 ``auto/smaconfig.py``
    模块 docstring 的三节。
    """
    return SmaService(wide=False, config_factory=smaconfig.file_store_factory())
