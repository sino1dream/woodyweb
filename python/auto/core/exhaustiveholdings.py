"""穷举持仓页的纯逻辑层。

对应 PHP：``woody/res/exhaustiveholdingscn.php``（375 行）里的全部计算部分。

## 这个页面在算什么

一只 QDII/LOF 基金只持有 2–4 只成分股时，可以用"连续几天净值涨跌 + 那几天
各成分股的涨跌 + 汇率变化"组一组方程，**反推**出成分股的实际比例与基金仓位：

```
已知：净值涨跌 f、汇率变化 n、各成分股涨跌 a/b/c…
未知：比例 x/y/z 与仓位 w
每天一个方程：a·x + b·y + c·z - (f/n)·w = 1/n - c
```

方程数多于未知数时用最小二乘（``SolveOverdetermined``），刚好够数时用
克莱姆法则（``CramersRule``）—— 两个函数在 :mod:`calc.linearsolve`。

## 三层拆分（为什么这么切）

PHP 把"取数 / 算 / 渲染"全挤在一个文件里，靠 ``static $arEq`` 跨行累积状态。
本版拆成：

* **本模块** —— 行级纯逻辑，唯一有状态的是 :class:`ExhaustiveHoldingsCalculator`
  的 ``_eq``（正是 PHP 那两个 ``static`` 变量的对应物，但**显式归属**给一次
  页面渲染，不会像 static 那样串到下一个请求）；
* :mod:`web.services.exhaustive_holdings_service` —— 四张表的装配与列定义；
* 模板 —— 只管摆格子。

## 数字口径：``number_format`` 而不是 ``round``

页面里四处比例/仓位显示都走 ``number_format($x, N)``（PHP 的 half-up，
且 ``-0.4`` 显示成 ``0`` 不是 ``-0``）。本版一律走
:func:`sql.historytable.php_number_format` 与 ``_trim_number_text``，
理由是这些值要拿去和"库里已有的比例"做**字符串相等**判定来决定整行是否标黄
（``__getHoldingRatioDisplay``）—— 舍入口径差一位，整张表的黄底就全乱。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from calc.linearsolve import LinearSolveError, cramers_rule, solve_overdetermined
from sql.historytable import format_price_display, php_number_format

#: ``HOLDINGS_NETVALUE_DIFF``（``php/stockdisp.php:33``）：净值涨跌阈值，单位 %。
#: 低于它的日子不进方程 —— 涨跌比这还小的日子，净值变化会被舍入噪声吃掉，
#: 解出来的比例是垃圾。页面允许在表单里改（``?input=``），1.0 只是默认值。
HOLDINGS_NETVALUE_DIFF = 1.0

#: ``DEFAULT_PAGE_NUM``（``php/httplink.php:9``）。PHP 用
#: ``$acct->GetNum() / 4`` 决定穷举表最多算几个季度 —— 100/4 = 25。
#: 为什么除以 4：这个页面原本是"每页 100 行"的账号体系里的一个标签，
#: 四个表平分页容量。本地没有账号体系，直接写死等价值。
DEFAULT_EXHAUSTIVE_ROWS = 25

#: ``GetPercentageDisplay()`` 在没有可比价格时给的颜色（注意是 ``gray``
#: 不是 ``grey``：PHP 的 ``StockGetPriceDisplay`` 用 ``grey``，
#: ``GetPercentageDisplay`` 用 ``gray``，两套拼写并存，照抄）。
SESSION_COLOR = "gray"

#: PHP ``__getPosDisplay`` 认的合理仓位区间（开区间）。落在外面的仓位
#: 说明方程解崩了，页面上标红提示。
POSITION_MIN = 0.25
POSITION_MAX = 1.25


# ---------------------------------------------------------------------------
# 显示助手
# ---------------------------------------------------------------------------
def percentage_cell(text: Any) -> tuple:
    """把涨跌幅文本转成 ``(文本, 颜色)`` 单元格。

    **全站唯一实现**：``web/services/reference_service.percentage_cell``
    转调这里。区别在于颜色语义 —— PHP ``stockref.php:147`` 是"跌红涨黑"
    （红=跌），本站点统一成 A 股习惯的**涨红跌绿**：

    ================================  ===================  ==================
    情形                                PHP 原文             本实现
    ================================  ===================  ==================
    带 ``%`` 且以 ``-`` 开头（跌）      ``red``              ``green``
    带 ``%`` 其它（涨或持平）           ``black``            ``red``
    不带 ``%``（无昨收可比）            ``gray``             ``gray``
    ================================  ===================  ==================

    最后一行的 ``gray`` 与 PHP 一致：那时 ``GetPercentageText()`` 返回空串，
    表示该标的压根没有昨收可比，灰色用来和"真的是 0%"区分开。
    """
    displayed = "" if text is None else str(text)
    if not displayed.endswith("%"):
        return displayed, SESSION_COLOR
    return displayed, ("green" if displayed.startswith("-") else "red")


def number_display(value: Any, precision: int = 2) -> tuple:
    """``GetNumberDisplay($fVal, $iPrecision)``（``php/stock.php:111``）。

    = ``StockGetPriceDisplay($fVal, 0.0, $precision, 'black', 'red')``，
    即**与 0 比**决定颜色：正数黑、负数红、容差内灰。

    返回 ``(文本, 颜色)``；颜色为 ``""`` 表示 PHP 那边不包 ``<font>``
    （``$strColor == 'black'`` 时 PHP 直接返回裸文本）。

    **文本用 ``format_price_display``（削尾零），不是 ``php_number_format``**：
    这条路径经 ``StockGetPriceDisplay`` 会 ``rtrim('0')`` 再把 ``-0`` 归成
    ``0``；而本模块另外两处（``holding_ratio_cell`` / ``position_cell``）
    是**直接** ``number_format`` 且要与库里值比字符串，必须保留尾零。
    两套格式各有各的用处，混用会让列宽与匹配判定同时出错。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ("" if value is None else str(value), "")

    #: PHP 的容差循环：``$iDiff = 0.5; while (precision--) $iDiff /= 10``
    tolerance = 0.5
    for _ in range(max(precision, 0)):
        tolerance /= 10.0

    if number > tolerance:
        color = ""
    elif number < -tolerance:
        color = "red"
    else:
        color = "grey"
    text, _ = format_price_display(number, 0.0, precision)
    return (text, color)


#: 判断一段文本是不是 PHP 眼中的"数字字符串"（无千分位、无空格）。
#: 为什么要区分：PHP 8 比较两个**数字字符串**时按数值比（``'12' == '12.0'``），
#: 而带千分位的（``'1,235'``）按字符串比。``number_format`` 输出正好两种都有。
_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _php_number_equal(left: str, right: str) -> bool:
    """PHP 8 的 ``!=`` 语义，用于 ``__getHoldingRatioDisplay`` 的匹配判定。"""
    if _NUMERIC_RE.match(left or "") and _NUMERIC_RE.match(right or ""):
        return float(left) == float(right)
    return left == right


def holding_ratio_cell(value: Any, ratio: Any, match: bool) -> tuple:
    """``__getHoldingRatioDisplay($fVal, $strRatio, &$bMatch)``。

    返回 ``((文本, 颜色), match)`` —— PHP 用引用参数改 ``$bMatch``，这里
    把新值返回去（调用方负责把 ``match`` 传给下一格，与 PHP 的引用同效果）。

    两件事：

    1. 用 ``number_format($fVal, 0)`` 与 ``number_format(floatval($strRatio), 0)``
       比较，不等就把 match 打成 ``False``（整行因此不标黄）；
    2. 文本以 ``-`` 开头包红色 ``<font>``（负比例说明算出来的比例方向反了，
       是作者用来一眼看出"解崩了"的信号）。
    """
    text = php_number_format(float(value), 0)
    if not _php_number_equal(text, php_number_format(float(ratio or 0.0), 0)):
        match = False
    return ((text, "red") if text.startswith("-") else (text, "")), match


def position_cell(
    ref_position: Any,
    position: Any,
    ratios: dict,
    match: bool,
    admin: bool = False,
    update_href: Optional[Callable[[dict, str], str]] = None,
) -> tuple:
    """``__getPosDisplay($ref, $fPos, $arRatio, &$bMatch, $bAdmin)``。

    返回 ``((文本, 颜色), match)``。

    ## 三个分支照抄

    1. ``number_format($fPos, 2)`` 与 ``number_format($ref->GetPosition(), 2)``
       不等 → match False；
    2. ``0.25 < fPos < 1.25`` 是"合理仓位"区间：正常显示；**管理员且
       不匹配**时 PHP 给一个"确认更新持仓"的 ``onclick`` 链接（把人算出来的
       比例写回库里）；
    3. 区间外（解崩了）→ 红字。

    ## 与 PHP 的一处刻意差异：写入链接默认不给

    ``update_href`` 为 ``None``（默认）时**不生成那个链接**，只显示数字。
    原因：写回仓位走 ``submitholdings.php``，需要管理员校验，而本项目的
    账号体系暂缓（``process_admin_holdings`` 目前无权限检查，见
    ``README_holdings_updater_migration.md``）。函数保留注入口，等权限补上
    就能原样接回，不用改这里的判断。
    """
    text = php_number_format(float(position), 2)
    if not _php_number_equal(text, php_number_format(float(ref_position or 0.0), 2)):
        match = False

    if POSITION_MIN < float(position) < POSITION_MAX:
        #: PHP 是 ``$bMatch === false``（严格比较）—— 只有本行**确实**被打成
        #: False 才给链接；一进来就 False 的（上一格没匹配）也算。
        if admin and match is False and update_href is not None:
            return ((update_href(ratios, text), ""), match)
        return ((text, ""), match)
    return ((text, "red"), match)


# ---------------------------------------------------------------------------
# 取数助手
# ---------------------------------------------------------------------------
def get_quarter_holdings_extra(ref: Any, quarter_sql: Any) -> dict:
    """``_getQuarterHoldingsExtra($ref, $quarter_sql)``。

    季报里出现过、但**当前持仓里没有**的标的 —— 它们要额外占几列，否则
    季报那几行会少格（PHP 里少格会让后面的仓位串位）。

    返回 ``{stock_id: ratio}``，顺序 = 季报出现顺序。PHP 用
    ``array_diff_key($arRatio, $arHoldingsRatio + $arExtra)``，即"去掉当前
    持仓已覆盖的、也去掉前面几季已经收进来的"；``+=`` 保留先出现的那个值。
    """
    extra: dict = {}
    if quarter_sql is None:
        return extra
    holdings_ratio = _call(ref, "get_holdings_ratio_array") or {}
    source = getattr(ref, "source", None)
    rows = quarter_sql.get_all(_call(ref, "get_stock_id")) or []
    for record in rows:
        ratio_map = decode_holdings(record.get("close"), source)
        if not ratio_map:
            continue
        known = dict(holdings_ratio)
        known.update(extra)
        for stock_id, value in ratio_map.items():
            if stock_id not in known:
                extra[stock_id] = value
    return extra


def decode_holdings(text: Any, source: Any = None) -> dict:
    """``StockOptionDecodeHoldings()`` 的薄封装：只要比例表，不要仓位。

    真正的解码在 :func:`updater.holdingsupdater.stock_option_decode_holdings`
    （那边还要写库，顺带返回归一化后的仓位）。这里只借它的解码部分。

    ``source`` 是**必需**的（虽然给了默认 ``None``）：解码要把持仓里的**符号**
    换成 ``stock`` 表的 id，没有数据源就查不到 id，比例表会是空的。
    页面调用时传 ``ref.source``。解码失败一律当"这条季报没有数据"。
    """
    try:
        from updater.holdingsupdater import stock_option_decode_holdings
    except Exception:                                    # pragma: no cover - 循环导入兜底
        return {}
    try:
        position, ratios = stock_option_decode_holdings(text, source=source)
    except Exception:
        return {}
    #: 注意返回的是**归一化后**的比例（PHP 同一个函数也归一化），
    #: 所以季报行上的比例天然是 100 的分配，与 ``_getQuarterHoldingsExtra``
    #: 收集的键顺序无关。
    return ratios if position else {}


def switch_date_pairs(net_sql: Any, stock_id: Any) -> list:
    """把净值记录与"反转日"配成 ``[(date, close, prev_date), ...]``。

    对应 ``_echoExhaustiveHoldingsData`` / ``_echoVerifyHoldingsData`` 里那两段
    **一模一样**的游标推进（``$iIndex`` 与 ``GetSwitchDates()`` 逐条对日期）：

    .. code-block:: php

        $arDate = $net_sql->GetSwitchDates($strStockId);
        while ($record = getAll())
        {
            if ($record['date'] == $arDate[$iIndex])
            {
                $iIndex ++;
                if (isset($arDate[$iIndex]))  → 用 ($record['date'], $record['close'], $arDate[$iIndex])
                else break;
            }
        }

    也就是"某个反转日 A 的记录，配它的**下一个**反转日 B"——
    ``GetSwitchDates`` 返回 DESC 序，所以 B 比 A 早，正好是"从 B 到 A 的涨跌"。

    **为什么把它抽出来**：两段游标逻辑一字不差，只有"算多少行 / 什么条件停"
    不同。抄两遍的话，以后修一个漏一个，两张表就会错位。
    """
    dates = net_sql.get_switch_dates(stock_id) or []
    records = net_sql.get_all(stock_id) or []
    pairs = []
    index = 0
    for record in records:
        if index >= len(dates):
            break
        date = record.get("date")
        if str(date) != str(dates[index]):
            continue
        index += 1
        if index >= len(dates):
            break
        pairs.append((date, record.get("close"), dates[index]))
    return pairs


def _call(target: Any, name: str) -> Any:
    """调 snake_case 方法；没有就返回 ``None``（替身对象常常只实现一部分）。"""
    method = getattr(target, name, None)
    if not callable(method):
        return None
    return method()


# ---------------------------------------------------------------------------
# 行级计算
# ---------------------------------------------------------------------------
class ExhaustiveHoldingsCalculator:
    """一次页面渲染用的行计算器。

    持有 PHP 那两个 ``static $arEq``（本类里是 :attr:`_exhaustive_eq` 与
    :attr:`_verify_eq`）与 ref 的当前状态。

    ## 为什么两个方程表要分开

    PHP 里它们分属两个函数的 static，天然独立。本版若合成一个，穷举表先跑
    （累积 20 多行）会让验证表一上来就 ``count($arEq) == $iCount`` 不成立 ——
    验证表就永远出不来结果。
    """

    def __init__(
        self,
        ref: Any,
        net_sql: Any,
        threshold: float,
        count: int = 0,
        admin: bool = False,
        update_href: Optional[Callable[[dict, str], str]] = None,
    ):
        self.ref = ref
        self.net_sql = net_sql
        #: ``$fInput`` —— 已经除以 100 的阈值（PHP 在 EchoAll 里除）
        self.threshold = float(threshold)
        self.count = int(count) if count else _call(ref, "count_holdings") or 0
        self.admin = admin
        self.update_href = update_href
        self._exhaustive_eq: list = []
        self._verify_eq: list = []

    # -- 表 1：当前使用的持仓数据 ------------------------------------------
    def current_row(self) -> tuple:
        """``_echoCurrentHoldingsItem($ref)``。

        返回 ``(cells, background)``；PHP 那句 ``EchoMatchTableColumn($ar)``
        **不传第二个参数**（默认 true）→ 整行黄底。所以这里 background 恒
        ``"yellow"``。
        """
        cells = []
        for _, ratio in (self.ref.get_holdings_ratio_array() or {}).items():
            cells.append(number_display(float(ratio), 0))
        cells.append(number_display(float(_call(self.ref, "get_position") or 0.0), 2))
        return cells, "yellow"

    # -- 表 2：季报持仓 ----------------------------------------------------
    def quarter_row(self, date: Any, close_text: Any, extra: dict) -> Optional[list]:
        """``_echoQuarterHoldingsItem($ref, $strDate, $strClose, $arExtraHoldings, $bAdmin)``。

        解不出季报（``StockOptionDecodeHoldings`` 返回假值）时返回 ``None`` ——
        PHP 那边是"整行只有日期一格都不输出"，因为进入条件就没进去，
        ``$ar`` 里只有最初那个日期…… 实际上 PHP 会输出一个只有日期的行。
        这里照抄：解码失败时返回 ``[date]`` 单格行。

        返回 ``(cells, match)``。
        """
        decoded = decode_holdings(close_text, getattr(self.ref, "source", None))
        if not decoded:
            return ([(date, "")], False)

        position, ratio_map = _decode_position_and_ratios(
            close_text, getattr(self.ref, "source", None))
        match = True
        cells = [(date, "")]

        columns = dict(extra)
        columns.update(self.ref.get_holdings_ratio_array() or {})
        for stock_id, ratio in columns.items():
            if stock_id in ratio_map:
                cell, match = holding_ratio_cell(ratio_map[stock_id], ratio, match)
                cells.append(cell)
            else:
                match = False
                cells.append(("", ""))
        cell, match = position_cell(
            _call(self.ref, "get_position"), position, ratio_map, match,
            self.admin, self.update_href,
        )
        cells.append(cell)
        return (cells, match)

    # -- 表 3：穷举 --------------------------------------------------------
    def exhaustive_row(self, date: Any, net_value: Any, prev_date: Any) -> Optional[list]:
        """``_echoExhaustiveHoldingsItem(...)``。

        返回 ``(cells, match)``。三格恒有（日期/净值/涨幅），进了条件才多两格
        （估值涨幅）与各持仓格 —— PHP 就是这个形状，模板不用补齐。

        ## 两个副作用照抄

        ``SetHoldingsDate($strPrevDate)`` 与 ``SetNetValueString($prevValue)``
        **会改 ref 的状态**，且不只这一行用到（后面的同样在被逐步改成更早的
        日子）。看起来像 bug，其实是作者的手动游标 —— 改成本地参数会让
        ``_est_net_value`` 里"持仓日那天的价格"取成另一个日子，比例就全错。
        """
        ref = self.ref
        stock_id = _call(ref, "get_stock_id")
        net_value = float(net_value or 0.0)

        prev_value = self.net_sql.get_close(stock_id, prev_date)
        cells = [
            (date, ""),
            (ref.get_net_value_display(net_value), ""),
            percentage_cell(ref.get_percentage_text(float(prev_value or 0.0), net_value)),
        ]

        match = False
        proportion = self.net_sql.get_proportion(stock_id, date, prev_date)
        percent = float(proportion) - 1.0 if proportion else None
        if percent is None or abs(percent) <= self.threshold:
            return (cells, match)
        ratios = ref.get_proportion_array(date, prev_date)
        if not ratios:
            return (cells, match)

        ref.set_holdings_date(prev_date)
        ref.set_net_value_string(prev_value)
        cells.append(percentage_cell(ref.get_percentage_text(net_value, ref._est_net_value(date))))

        cny_ref = _call(ref, "get_cny_ref")
        cny_proportion = self.net_sql.get_proportion(
            _call(cny_ref, "get_stock_id"), date, prev_date)
        if not cny_proportion:
            #: PHP 这里会 1/false → DivisionByZeroError（PHP 8 抛异常）。
            #: 本版当"这天算不了"，只出三格 —— 页面不能因为一天缺汇率就 500。
            return (cells, match)

        last = ratios[-1]
        line = [ratios[i] - last for i in range(max(self.count - 1, 0))]
        line.append(-percent / float(cny_proportion))
        line.append(1.0 / float(cny_proportion) - last)
        self._exhaustive_eq.append(line)

        if len(self._exhaustive_eq) >= self.count:
            try:
                solution = solve_overdetermined(self._exhaustive_eq)
            except LinearSolveError:
                return (cells, match)
            cells, match = self._ratio_cells(cells, solution)
        return (cells, match)

    # -- 表 4：验证最激进 --------------------------------------------------
    def verify_row(self, date: Any, net_value: Any, prev_date: Any) -> tuple:
        """``_echoVerifyHoldingsItem(...)``。

        返回 ``(cells, match, done)``；``done`` 对应 PHP 的 ``$bReturn``
        —— 调用方见到它就 ``break``（验证表只为"最激进的那一季"出一行）。
        """
        ref = self.ref
        stock_id = _call(ref, "get_stock_id")
        net_value = float(net_value or 0.0)

        prev_value = self.net_sql.get_close(stock_id, prev_date)
        cells = [
            (date, ""),
            (ref.get_net_value_display(net_value), ""),
            percentage_cell(ref.get_percentage_text(float(prev_value or 0.0), net_value)),
        ]

        done = False
        match = False
        proportion = self.net_sql.get_proportion(stock_id, date, prev_date)
        percent = float(proportion) - 1.0 if proportion else None
        if percent is None or abs(percent) <= self.threshold:
            return (cells, match, done)
        ratios = ref.get_proportion_array(date, prev_date)
        if not ratios:
            return (cells, match, done)

        cny_ref = _call(ref, "get_cny_ref")
        cny_proportion = self.net_sql.get_proportion(
            _call(cny_ref, "get_stock_id"), date, prev_date)
        if not cny_proportion:
            return (cells, match, done)

        line = list(ratios)
        line.append(-percent / float(cny_proportion))
        line.append(1.0 / float(cny_proportion))
        self._verify_eq.append(line)

        if len(self._verify_eq) == self.count:
            done = True
            #: 追加"比例合计 = 1"那一行（系数全 1、常数 1）。
            #: 这一步让方程组从"iCount 个方程 / iCount+1 个未知数"变成方阵。
            total = [1.0] * self.count
            total.append(0.0)
            total.append(1.0)
            self._verify_eq.append(total)
            try:
                solution = cramers_rule(self._verify_eq)
            except LinearSolveError:
                return (cells, match, done)
            cells, match = self._ratio_cells(cells, solution, extra_unknown=True)
        return (cells, match, done)

    # -- 共用：按解出比例填格 ----------------------------------------------
    def _ratio_cells(self, cells: list, solution: list, extra_unknown: bool = False) -> tuple:
        """``foreach ($ref->GetHoldingsRatioArray() as ...)`` 那一段。

        **往传入的 ``cells`` 里追加**（PHP 是往同一个 ``$ar`` 里 push），
        返回 ``(cells, match)``。不能新建列表 —— 那样日期/净值/涨幅那几格
        会被丢掉，行只剩比例（实测踩过：第 2 行起整行变成 3 格）。

        **最后一个比例是"1 减前面之和"**，不是解出来的 —— 这是穷举法的收敛
        做法：只解前 ``iCount-1`` 个未知数，剩下的由"合计为 1"补齐。

        ``extra_unknown`` 对应两张表的差别（**差一位，不是笔误**）：

        * 穷举表 ``1.0 / $arXY[$iIndex - 1]`` —— $arXY 长度 = iCount；
        * 验证表 ``1.0 / $arXY[$iIndex]``   —— CramersRule 那边多解了一个
          未知数（追加的"合计=1"那一行把维度抬高 1），$arXY 长度 = iCount+1。

        合成一个函数时最容易在这里出错：位置取错一位，页面上仓位会从
        ``1.00`` 变成 ``0.003`` 之类的荒数，而且不报错。
        """
        match = True
        new_ratios: dict = {}
        total = 0.0
        index = 0
        for stock_id, ratio in (self.ref.get_holdings_ratio_array() or {}).items():
            if index == self.count - 1:
                value = 1.0 - total
            else:
                value = solution[index]
                total += value
            value *= 100.0
            cell, match = holding_ratio_cell(value, ratio, match)
            cells.append(cell)
            new_ratios[stock_id] = value
            index += 1

        position_index = index if extra_unknown else index - 1
        if index == 0 or position_index < 0 or position_index >= len(solution):
            return (cells, match)
        divisor = solution[position_index]
        cell, match = position_cell(
            _call(self.ref, "get_position"),
            1.0 / divisor if divisor else 0.0,
            new_ratios, match, self.admin, self.update_href,
        )
        cells.append(cell)
        return (cells, match)


def _decode_position_and_ratios(text: Any, source: Any = None) -> tuple:
    """``StockOptionDecodeHoldings()`` 的完整返回：``(仓位, 比例表)``。

    季报行要用**归一化后**的比例与解出来的仓位，所以要原样拿到两者
    （:func:`decode_holdings` 只要比例，这一支还要仓位）。
    """
    try:
        from updater.holdingsupdater import stock_option_decode_holdings
    except Exception:                                    # pragma: no cover
        return (0.0, {})
    try:
        position, ratios = stock_option_decode_holdings(text, source=source)
    except Exception:
        return (0.0, {})
    return (float(position or 0.0), ratios)


# ---------------------------------------------------------------------------
# 阈值输入
# ---------------------------------------------------------------------------
def parse_threshold(raw: Any) -> float:
    """``floatval($strInput) / 100.0``（``exhaustiveholdingscn.php:346-350``）。

    ``GetEditInput()`` 取不到时 PHP 用 ``HOLDINGS_NETVALUE_DIFF``（1.0）；
    取到空串时 **``$strInput != ''`` 为假 → 整段不渲染**（页面只剩输入框）。
    这个"空串 = 不算"的行为由调用方判断，本函数只管换算。

    返回**百分数**（1.0 表示 1%）；换算成比例（0.01）由调用方做 —— 因为
    PHP 那边除以 100 的地方在 ``EchoAll``，与阈值本身的语义无关。
    """
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0
