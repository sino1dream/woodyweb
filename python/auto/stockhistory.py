"""均线 / 布林带 / 牛熊分界的数值核心 —— 对应 ``php/stockhis.php``（586 行）。

## 本步只做第一刀：**纯数值，不碰配置与渲染**

``stockhis.php`` 的 586 行里，``StockHistory`` 一个类的构造就要做四件事：

1. **数值** —— `_estSma` / `_estBollingerBands` / `_estNextBollingerBands` /
   ``GetQuadraticEquationRoot`` / ``GetBullBear`` / ``NeedAfterHourEst``；
2. **查库** —— `_calcStartDate` / `_getDayWeekMonthData`（日/周/月三套收盘序列）；
3. **读写配置文件** —— `_configSMA` → ``$cfg->set_var(SMA_SECTION…)`` + ``save_data()``；
4. **渲染** —— ``smaparagraph.php``（225 行）。

第 3 件有**外部可见副作用**（回写工作区文件），第 4 件是显示层，
两者都留到后面单独评审。本模块只落第 1 件 + 第 2 件里可测的切片判定、
以及 ``_getColorAndOrderArray`` 那段纯计算。

## ⚠️ 输入顺序：数组是**最新在前**

``StockHistory::_getDayWeekMonthData()`` 用
``GetStockHistorySql()->GetFromDate()`` 取数，而
``SqlDailyClose::BuildOrderBy()`` 是 ``ORDER BY date DESC`` ——
**最新的收盘价在 ``afClose[0]``**。

``_estSma()`` 只累加**前 ``iAvg - 1`` 个**元素，所以它算的是
"最近 ``iAvg-1`` 天"的均值（而不是常规意义上前 N 天的 SMA）。
这一点照抄，因为下游的布林带与颜色判定都建立在这个约定上。

## ⚠️ ``GetDayOfWeek()`` 是 ``date('w')``，不是 ISO

``php/class/year_month_day.php:93`` 返回 ``$this->local[6]``，
而它来自 ``date('w')``：**周日 = 0，周一 = 1，… 周五 = 5，周六 = 6**。

不是 ISO-8601 的"周一 = 1 … 周日 = 7"。``_isWeekEnd`` / ``_isMonthEnd``
里的比较（``>=`` / ``>``）依赖这个 0 基映射，换成 ``weekday()`` 会静默错位。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

#: 最多用 20 个月的历史。对应 ``php/stockhis.php:5``。
MAX_QUOTES_DAYS = 620

#: 布林带的周期。对应 ``php/stockhis.php:6``。
BOLL_DAYS = 20

#: 配置文件里的段落名。对应 ``php/stockhis.php:8``。
SMA_SECTION = "SMA"

#: 日/周/月三套均线的周期。对应 ``StockHistory::$aiNum``。
AI_NUM = (5, 10, 20)


def est_sma(quotes: Sequence[float], avg: int) -> float:
    """对应 ``_estSma()``（``php/stockhis.php:24``）：最近 ``avg-1`` 个的均值。

    .. code-block:: php

        $iNum = $iAvg - 1;              // ← 注意是 avg - 1
        foreach ($arF as $fVal)
        {
            $f += $fVal;
            $iCount ++;
            if ($iCount == $iNum)	break;
        }
        return strval($f / $iNum);      // ← 除以 iNum，不是 iAvg

    两处都照抄：

    * **除数与元素个数都是 ``avg - 1``**，所以 ``est_sma(x, 5)`` 是"最近 4 个
      的均值"。看着像 off-by-one，但它是 PHP 的原意，改掉会让所有均线偏移；
    * 元素不足时 PHP 循环自然结束、仍**除以 ``iNum``**（不是实际累加个数），
      等于把缺的数据当 0 —— 本版照抄（见下面的测试）。

    PHP 返回 ``strval(...)``，本函数返回 ``float``：数值层保持数值，
    字符串化留给显示层（``_getSmaCallbackPriceDisplay`` 那些）。
    这样 ``est_sma`` 才能参与进一步计算。
    """
    count = avg - 1
    if count == 0:
        # PHP 8 会抛 DivisionByZeroError；这里显式拒绝，别让它变成 inf/NaN
        raise ValueError("est_sma() 的除数 avg-1 为 0（avg=1）")
    total = 0.0
    seen = 0
    for value in quotes:
        total += value
        seen += 1
        if seen == count:
            break
    return total / count


def get_quadratic_equation_root(a: float, b: float, c: float) -> Any:
    """对应 ``GetQuadraticEquationRoot()``（``php/stockhis.php:42``）。

    ``axx + bx + c = 0`` 的两根；判别式小于 0 时返回 ``None``（PHP 返回 ``false``）。

    PHP 不检查 ``a == 0``：那种情况下会抛 ``DivisionByZeroError``。
    本版显式抛 :class:`ValueError` —— 布林带那条路径里 ``a`` 与 ``iAvg``
    有关（``a = (iAvg-4) * (iAvg-1)^2 - 4*(iAvg-1)``），``iAvg`` 小到一定程度
    就会踩到，不想让它静默变成两个 ``inf``。
    """
    if a == 0.0:
        raise ValueError("GetQuadraticEquationRoot() 的 a 为 0，不是二次方程")
    delta = b * b - 4.0 * a * c
    if delta >= 0.0:
        root = math.sqrt(delta)
        return ((0.0 - b + root) / (2.0 * a), (0.0 - b - root) / (2.0 * a))
    return None


def est_bollinger_bands(quotes: Sequence[float], avg: int = BOLL_DAYS) -> Any:
    """对应 ``_estBollingerBands()``（``php/stockhis.php:61``）：当日布林上下轨。

    PHP 顶部那行注释给了方程来源::

        a = (n - 4) * (n - 1)^2 - 4 * (n - 1)
        b = (8 - 2 * (n - 4) * (n - 1)) * sum(Xn)
        c = (n - 4) * (sum(Xn))^2 - 4 * sum(Xn^2)

    其中 ``n = iAvg``、``iNum = n - 1``。返回 ``(上轨, 下轨)``；无实根返回 ``None``。

    ``GetQuadraticEquationRoot`` 回来的两个根对应"加上一天新价之后"标准差方程
    的两组解，再各减去 ``2*sigma`` 得到轨道。``sigma`` 用的是 ``iNum`` 与
    ``/2``，与 ``_estNextBollingerBands`` 的 ``/4`` 不同 —— 别混。
    """
    total = 0.0
    quadratic_total = 0.0
    count = avg - 1
    seen = 0
    for value in quotes:
        total += value
        quadratic_total += value * value
        seen += 1
        if seen == count:
            break

    factor = 1.0 * (avg - 4)
    a = factor * count * count - 4 * count
    b = (8 - 2 * factor * count) * total
    c = factor * total * total - 4 * quadratic_total

    roots = get_quadratic_equation_root(a, b, c)
    if roots is None:
        return None
    x1, x2 = roots
    sigma1 = (total - count * x1) / 2
    sigma2 = (total - count * x2) / 2
    return (x1 - 2 * sigma1, x2 - 2 * sigma2)


def est_next_bollinger_bands(quotes: Sequence[float], avg: int = BOLL_DAYS) -> Any:
    """对应 ``_estNextBollingerBands()``（``php/stockhis.php:91``）：T+1 轨道。

    与上一式的差别是"多推一天"：``iNum = avg - 2``、``factor = avg - 8``、
    系数里的 4 / 8 / 16 对应 16 / 32 / 16、``sigma`` 除以 4 而不是 2。

    两条式子不能互相替换 —— 实测同一组报价下结果相差明显。
    """
    total = 0.0
    quadratic_total = 0.0
    count = avg - 2
    seen = 0
    for value in quotes:
        total += value
        quadratic_total += value * value
        seen += 1
        if seen == count:
            break

    factor = 1.0 * (avg - 8)
    a = factor * count * count - 16 * count
    b = (32 - 2 * factor * count) * total
    c = factor * total * total - 16 * quadratic_total

    roots = get_quadratic_equation_root(a, b, c)
    if roots is None:
        return None
    x1, x2 = roots
    sigma1 = (total - count * x1) / 4
    sigma2 = (total - count * x2) / 4
    return (x1 - 2 * sigma1, x2 - 2 * sigma2)


def is_week_end(ymd: str, next_day_ymd: Any = None, now_ymd: Any = None) -> bool:
    """对应 ``_isWeekEnd()``（``php/stockhis.php:122``）：这天是否为本周最后交易日。

    PHP 走两条分支：

    * 有"下一天"时 —— ``$ymd->GetDayOfWeek() >= $next_ymd->GetDayOfWeek()``
      说明到下一天时星期数没增加，即跨过了周末（数据是**倒序**的，
      所以"下一天"其实是时间上更早的一条，见模块 docstring）；
    * 没有"下一天"时 —— 是周五就算（含"本周五不是交易日"的兜底：
      若现在是工作日且本日星期数大于今天，也算周内最后一条）。

    ``now_ymd`` 传 ``None`` 表示"取当前时间"；测试里显式传入以避免依赖真实时钟。
    ``GetDayOfWeek()`` 是 ``date('w')``（周日 = 0 … 周六 = 6），别换成
    ``datetime.weekday()``。
    """
    day = _day_of_week(ymd)
    if next_day_ymd:
        return day >= _day_of_week(str(next_day_ymd))

    if day == 5:            # 周五（date('w') = 5）
        return True

    current = now_ymd if now_ymd is not None else _today_ywd()
    current_day = _day_of_week(str(current))
    if current_day not in (0, 6):       # 现在是工作日
        return day > current_day
    return True


def is_month_end(ymd: str, next_day_ymd: Any = None, now_ymd: Any = None) -> bool:
    """对应 ``_isMonthEnd()``（``php/stockhis.php:150``）：这天是否为本月最后交易日。

    * 有"下一天"时直接用它比较月份；
    * 没有时用``今天``：若今天就是本日、或今天是周末（本月最后一段没交易日），
      用"下一个交易日"代替今天比月份。

    月份不同即为月末。``tick`` 那条分支要 ``GetNextTradingDayTick()``
    （节假日表），本版简化为"下一个工作日"—— 落差仅在**节假日**上，
    正常周末行为一致，已记在 ``README_stockhistory_migration.md``。
    """
    month = _month_of(ymd)
    if next_day_ymd:
        return month != _month_of(str(next_day_ymd))

    current = now_ymd if now_ymd is not None else _today_ywd()
    current_text = str(current)
    current_day = _day_of_week(current_text)
    if current_text == ymd or current_day in (0, 6):
        next_text = _next_weekday_ymd(current_text)
    else:
        next_text = current_text
    return month != _month_of(next_text)


class MaxMin:
    """对应 ``MaxMin``（``php/stockhis.php:174``）：一个"只初始化一次"的区间。

    .. code-block:: php

        function Init($fMax, $fMin)
        {
            if ($this->fMin == false && $this->fMax == false)
            {   $this->fMin = $fMin;  $this->fMax = $fMax;   }
        }
        function Set($fVal)
        {
            if ($fVal > $this->fMax)  $this->fMax = $fVal;
            if ($fVal < $this->fMin)  $this->fMin = $fVal;
        }

    ``Init`` 的守卫条件是"**两个都还是 ``false``** 才写入"，所以：

    * 只调一次 ``Init(0.0, 10000000.0)`` 之后再 ``Init(a, b)`` **不生效**；
    * ``Set`` 没有守卫，会一直撑大区间。

    PHP 用 ``== false`` 比较（``0.0 == false`` 为真），本版用
    :meth:`_is_unset` 显式复刻这个语义 —— ``fMax`` 初值是 ``False`` 而不是
    ``0.0``，用 ``is None`` 判断会把"初值 0.0"错当成"已初始化"。
    """

    def __init__(self) -> None:
        #: PHP 里初值是 ``false``，不是数字
        self.f_max: Any = False
        self.f_min: Any = False

    def init(self, f_max: float, f_min: float) -> None:
        if self._is_unset(self.f_min) and self._is_unset(self.f_max):
            self.f_min = f_min
            self.f_max = f_max

    def set(self, f_val: float) -> None:
        if f_val > _as_number(self.f_max):
            self.f_max = f_val
        if f_val < _as_number(self.f_min):
            self.f_min = f_val

    def fit(self, f_val: float) -> bool:
        """严格在区间内（两端不算）。"""
        return _as_number(self.f_min) < f_val < _as_number(self.f_max)

    @staticmethod
    def _is_unset(value: Any) -> bool:
        """``$value == false``：``False``、``0``、``0.0``、``''`` 都算未设置。"""
        if value is False or value is None:
            return True
        if isinstance(value, (int, float)):
            return value == 0
        return value == ""

    # -- PHP 原名 ---------------------------------------------------------
    Init = init
    Set = set
    Fit = fit


def get_color_and_order_array(sma: dict) -> tuple:
    """对应 ``StockHistory::_getColorAndOrderArray()``（``php/stockhis.php:504``）。

    两件事：

    1. **配色** —— 日线区间内的周线值染 ``silver``，日线区间内的月线值也染
       ``silver``、周线区间内的月线值染 ``gray``，EMA 落在日线区间内染
       ``silver``、否则 ``yellow``。区间外的值收集起来进入第 2 件事；
    2. **纵轴标尺** —— 收集到的值**数值排序**、``number_format(x, 2)``
       格式化后去重，得到 ``arOrder``（PHP 的 ``array_unique``，
       保留首次出现的顺序；因为已排序，等价于升序去重）。

    ## 键的首字母决定分组

    ``D``/``W``/``M``/``E``（EMA）。**遍历顺序即字典插入顺序**，而 ``mm``
    只在遇到 ``D`` 键时才 ``Init(0.0, 10000000.0)`` —— 所以如果调用方给的
    字典以 ``W`` 开头，``mm`` 还是未初始化状态（``fMax = fMin = False``），
    区间退化成"任何数都大于 False 且不小于 False"，颜色就全错。

    PHP 那边靠 ``_configSMA`` 里"先 D 后 W 后 M 再 EMA"的读取顺序保证正确。
    本版**不假设调用方顺序**：若字典里没有 ``D`` 键，就显式初始化区间
    （与 PHP 给 W 首键时的行为一致 —— 那时 ``$mm->fMax/fMin`` 是 ``false``，
    转换过来就是"没有下界、也没有上界"）。

    返回 ``(colors, order)``：``colors`` 是 ``键 -> 颜色名或 None``，
    ``order`` 是去重后的标尺列表（**字符串**，与 PHP ``number_format`` 对齐）。
    """
    daily = MaxMin()
    weekly = MaxMin()
    values = []
    colors: dict = {}

    has_daily = any(str(key)[:1] == "D" for key in sma)

    for key, raw in sma.items():
        value = _as_number(raw)
        color = None
        first = str(key)[:1]

        if first == "D":
            daily.init(0.0, 10000000.0)
            daily.set(value)
            values.append(value)
        elif first == "W":
            if has_daily:
                weekly.init(_as_number(daily.f_max), _as_number(daily.f_min))
            weekly.set(value)
            if daily.fit(value):
                color = "silver"
            else:
                values.append(value)
        elif first == "M":
            if daily.fit(value):
                color = "silver"
            elif weekly.fit(value):
                color = "gray"
            else:
                values.append(value)
        else:                       # 'E' —— EMA50 / EMA200
            if daily.fit(value):
                color = "silver"
            else:
                color = "yellow"
        colors[key] = color

    order = list(dict.fromkeys(_format_two(value) for value in sorted(values)))
    return colors, order


# ---------------------------------------------------------------------------
# 内部助手
# ---------------------------------------------------------------------------
def _as_number(value: Any) -> float:
    """``floatval()`` 语义：``False``/``None``/空串给 0.0，不给就抛。"""
    if value is False or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_two(value: float) -> str:
    """``number_format($fVal, 2, '.', '')``：两位小数、千分位为空。"""
    return "%.2f" % value


def _to_date(ymd: Any):
    """把 ``YYYY-MM-DD`` / ``datetime.date`` 统一成 ``datetime.date``。"""
    import datetime

    if isinstance(ymd, datetime.datetime):
        return ymd.date()
    if isinstance(ymd, datetime.date):
        return ymd
    text = str(ymd).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("无法解析日期：%r" % (ymd,))


def _day_of_week(ymd: Any) -> int:
    """对应 ``GetDayOfWeek()``：``date('w')`` —— **周日 = 0 … 周六 = 6**。

    ``datetime.weekday()`` 是周一 = 0，两者差一天，所以要先 ``+1`` 再取模。
    """
    return (_to_date(ymd).weekday() + 1) % 7


def _month_of(ymd: Any) -> int:
    return _to_date(ymd).month


def _today_ywd() -> str:
    """今天（``YYYY-MM-DD``）。单独抽出来便于测试替换。"""
    import datetime

    return datetime.date.today().isoformat()


def _next_weekday_ymd(ymd: Any) -> str:
    """下一个工作日（跳过周六周日）。PHP 这里用的是节假日表，本版简化。"""
    import datetime

    current = _to_date(ymd) + datetime.timedelta(days=1)
    while current.weekday() >= 5:       # 5 = 周六、6 = 周日
        current += datetime.timedelta(days=1)
    return current.isoformat()


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md 第 3 条）
# ---------------------------------------------------------------------------
_estSma = est_sma
GetQuadraticEquationRoot = get_quadratic_equation_root
_estBollingerBands = est_bollinger_bands
_estNextBollingerBands = est_next_bollinger_bands
_isWeekEnd = is_week_end
_isMonthEnd = is_month_end
