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
from typing import Any, Callable, Dict, Iterable, List, Sequence

from core import tzutil

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


def ignore_current_trading_data(date: Any, now_ymd: Any) -> bool:
    """对应 ``_ignoreCurrentTradingData()``（``php/stockhis.php:10``）。

    .. code-block:: php

        $ymd = GetNowYMD();
        if ($ymd->GetYMD() == $strDate)
        {
            if ($ymd->IsStockTradingHourEnd() == false)
                return true;      // market still trading, do not use today's data
        }
        return false;

    "今天这条数据"在收盘前不能算数（当天的收盘价还没定）。
    ``IsStockTradingHourEnd()`` 是 ``GetHour() > 16``
    （``php/class/year_month_day.php:217``）—— 注意是**当地 16 点**，
    不是 15:00 收盘价就出来了。

    ``now_ymd`` 必须是**该标的所在市场**的当地时间（PHP 靠
    ``SetTimeZone()`` 把进程默认时区改掉；本版用 ``tzutil`` 显式折算）。

    ``now_ymd`` 可以是 ``datetime``（带时刻，推荐）或纯日期串。**纯日期串
    等价于"当天 00:00"**，于是恒判为"未收盘"—— 这在调用方只关心日期时
    是正确的保守行为（见 ``_hour_of``）。
    """
    if str(date)[:10] != str(now_ymd)[:10]:
        return False
    return not is_stock_trading_hour_end(now_ymd)


def is_stock_trading_hour_end(now: Any) -> bool:
    """对应 ``IsStockTradingHourEnd()``：当地 16 点之后为收盘。

    .. code-block:: php

        function IsStockTradingHourEnd()
        {
            if ($this->GetHour() <= 16)  return false;
            return true;
        }

    **边界是 ``<= 16``**：16:59 仍算"未收盘"，17:00 才算。照抄。
    ``now`` 可以是 ``datetime``，也可以是带小时的字符串（``"2026-10-09 16:30"``）。
    """
    hour = _hour_of(now)
    if hour <= 16:
        return False
    return True


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


def _hour_of(value: Any) -> int:
    """取小时。``datetime`` 直接用；``"2026-10-09 16:30"`` / ``"16:30"`` 解析。

    **纯日期串（``2026-10-09``）给 0** —— 它没有时刻信息，等于"当天 00:00"。
    这在 :func:`ignore_current_trading_data` 那条路径上会用到：那边只需要
    "今天"这个事实，小时取 0 会让 :func:`is_stock_trading_hour_end` 返回
    ``False``（未收盘），正是"还没到收盘时间"的正确语义。
    """
    import datetime

    if isinstance(value, datetime.datetime):
        return value.hour
    if isinstance(value, datetime.date):
        return 0
    text = str(value).strip()
    if " " in text or "T" in text:
        text = text.replace("T", " ").split(" ")[-1]
    parts = text.split(":")
    try:
        return int(parts[0])
    except (ValueError, IndexError):
        raise ValueError("无法从 %r 取出小时" % (value,))


# ---------------------------------------------------------------------------
# 查库：与 php/sql/sqldailyclose.php 的 GetFromDate / GetAll 对齐
# ---------------------------------------------------------------------------
class StockHistorySql:
    """``stockhistory`` 表的访问器（对应 PHP ``GetStockHistorySql()``）。

    只加一个 :meth:`get_from_date` —— 其余 ``GetAll`` / ``GetRecordPrev``
    沿用 :class:`historytable.DailyCloseSql`，避免两套实现。

    ``adjclose`` 是**复权**收盘价，均线要用它（不是 ``close``）。
    PHP 的 ``_getDayWeekMonthData()`` 读的就是 ``$record['adjclose']``。
    """

    def __init__(self, connection: Any, table: str = "stockhistory") -> None:
        self.connection = connection
        self.table = table

    def _fetchall(self, sql: str, params: tuple = ()) -> list:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_all(self, stock_id: Any, start: int = 0, num: int = 0) -> list:
        """按日期**倒序**取一页。对应 PHP ``GetAll()``。

        limit 语义照 ``_SqlBuildLimit()``（``php/sql/_sqlcommon.php:63``）：
        ``num == 0`` 不加 limit；``start == 0`` 用 ``LIMIT num``。
        """
        sql = ("SELECT `date`, `adjclose` FROM `%s` WHERE stock_id = %%s "
               "ORDER BY `date` DESC" % self.table)
        if start == 0:
            if num != 0:
                sql += " LIMIT %d" % int(num)
        else:
            sql += " LIMIT %d, %d" % (int(start), int(num))
        return self._fetchall(sql, (stock_id,))

    def get_from_date(self, stock_id: Any, date: Any, num: int = 0) -> list:
        """取 ``date` **当天及之前**的最近 ``num`` 行，按日期倒序。

        对应 PHP ``GetFromDate()``（``php/sql/sqldailyclose.php:46``）::

            function _buildWhereFromDate($strKeyId, $strDate)
            {
                return $this->BuildWhere_key($strKeyId)." AND date <= '$strDate'";
            }

        ## ⚠️ 是 ``<=`` 不是 ``>=``

        这里我一开始写成了 ``date >= %s``，结果**只取回一行**（起始日那天），
        所有均线退化成同一个价格 —— 而它看起来"有数"、不报错。
        正确语义是"**截至** ``start_date`` 往前数 ``num`` 天"：
        之所以先算 ``start_date``（最新可用日期）再按它往回取，
        是为了把"今天还没收盘"那条排除掉。

        所以 ``start_date`` 是**上界**而不是下界。
        没有 ``start_date``（``None``）时调用方不该查库。

        ## 为什么要带 ``id``

        PHP 的 ``GetFromDate()`` 走 ``TableSql::GetData()`` → ``SELECT *``，
        回来的是整行，所以 ``_updateStockHistoryAdjCloseBySplit()`` 能拿
        ``$record['id']`` 逐条 ``UpdateAdjClose($id, ...)``。本方法原来只
        ``SELECT `date`, `adjclose```（SMA 只要这两列），拿不到 id ——
        拆股/分红重算要用它当主键改复权价，缺了就只能静默改不动。
        """
        sql = ("SELECT `id`, `date`, `adjclose` FROM `%s` "
               "WHERE stock_id = %%s AND `date` <= %%s "
               "ORDER BY `date` DESC" % self.table)
        if num:
            sql += " LIMIT %d" % int(num)
        return self._fetchall(sql, (stock_id, str(date)))

    def get_close(self, stock_id: Any, date: Any) -> Any:
        """取**指定日期**的 ``close``，取不到返回 ``None``。

        对应 PHP ``DailyCloseSql::GetClose()``（``php/sql/sqldailyclose.php:31``）::

            function GetClose($strKeyId, $strDate)
            {
                return $this->_getCloseString('GetRecord', $strKeyId, $strDate);
            }

        注意它是**按日期精确取**、不是"取最近一条"—— 那个日期没有记录就返回
        ``false``（本版 ``None``）。``_get_EMA()`` 用的就是这个。

        列名是 ``close`` 而不是 ``adjclose``：``stockema50`` / ``stockema200``
        两张表只有 ``id`` / ``stock_id`` / ``date`` / ``close``
        （见 ``dbtools/schema.sql``），没有复权列。
        """
        row = self._fetchone(
            "SELECT `close` FROM `%s` WHERE stock_id = %%s AND `date` = %%s LIMIT 1"
            % self.table,
            (stock_id, str(date)),
        )
        return None if not row else row.get("close")

    def get_close_text(self, stock_id: Any, date: Any) -> Any:
        """取 ``close`` 的**字符串**，形态与 mysqli 返回的一致。

        为什么要单独开一个方法（2026-10-31 踩到）：``close`` 列是
        ``double(10,3)``。mysqli 把 DOUBLE 当字符串交出来，而且是**按列定义
        补零**的 ``'741.470'``；驱动层却把它转成 Python ``float``
        （``741.47``）。凡是"把价格原样 echo 出去"的页面（``etfdividend`` 的
        价格列就是），直接 ``str(close)`` 会少一位尾零。

        解决办法是让**数据库自己**转字符串 —— ``CAST(close AS CHAR)`` 得到的
        就是 ``'741.470'``。不硬编码 3 位小数：哪天列定义从 ``(10,3)`` 改成
        ``(10,4)``，这里自动跟着走。

        参与计算的调用方继续用 :meth:`get_close`（要 ``float``）；
        这个方法只给"展示原样"的页面用。
        """
        row = self._fetchone(
            "SELECT CAST(`close` AS CHAR) AS `close_text` FROM `%s` "
            "WHERE stock_id = %%s AND `date` = %%s LIMIT 1" % self.table,
            (stock_id, str(date)),
        )
        return None if not row else row.get("close_text")

    def _fetchone(self, sql: str, params: tuple = ()) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()


class ConfigSmaStore:
    """SMA 结果的存放处 —— 对应 PHP 那套 ``INIFile`` 的 ``SMA`` 段落。

    ## 为什么默认用内存

    PHP 的 ``_configSMA()`` 会 ``$cfg->save_data()`` **回写配置文件**。
    直译过来就是"渲染一个页面会写工作区文件"：测试会污染工作区、
    并发请求会互相覆盖。所以本版把它抽成接口，默认实现是**纯内存**。

    另有一个实际好处：现在 `_configSMA()` 里的**缓存判断失效**
    （每次进程重启都重算）等价于"永远算"，逻辑与结果不变，
    只是少一次文件读写 —— 而文件读写的正确性本来就要单独评审。

    要换成文件后端时，实现这两个方法即可（``php/class/ini_file.php``
    的 ``group_exists`` / ``read_var`` / ``set_var`` / ``save_data``）。

    键的形状与 PHP 一致：``(section, name)``，section 固定 ``'SMA'``；
    ``name`` 是 ``'Date'`` / ``'D5'`` / ``'D5Next'`` / ``'BOLLUP'`` 这种。
    """

    def __init__(self) -> None:
        self._values: Dict[tuple, str] = {}

    def has_section(self, section: str) -> bool:
        return any(key[0] == section for key in self._values)

    def read_var(self, section: str, name: str) -> Any:
        return self._values.get((section, name))

    def set_var(self, section: str, name: str, value: Any) -> None:
        self._values[(section, name)] = str(value)

    def save_data(self) -> None:
        """内存实现无需落盘；保留这个方法是为了对齐 PHP 的调用点。"""

    def snapshot(self) -> Dict[tuple, str]:
        """调试/测试用：看一眼里面存了什么。"""
        return dict(self._values)

    # -- PHP 原名 ---------------------------------------------------------
    def group_exists(self, section: str) -> bool:
        return self.has_section(section)

    GroupExists = group_exists
    ReadVar = read_var
    SetVar = set_var
    SaveData = save_data


# ---------------------------------------------------------------------------
# StockHistory：对应 php/stockhis.php:207
# ---------------------------------------------------------------------------
class StockHistory:
    """对应 ``StockHistory``（``php/stockhis.php:207``）。

    ## 构造里四件事做了三件

    PHP 的 ``__construct`` 是::

        $ref->SetTimeZone();
        $this->strStartDate = $this->_calcStartDate();
        $this->_configSMA();                  // ← 会回写配置文件
        ... _onTest() / _getColorAndOrderArray()

    本版：

    * **不做 ``SetTimeZone()``** —— 那个在 Windows 上直接抛错
      （``time.tzset()`` 不存在，见 ``stocksymbol.set_time_zone``）。
      改用 :func:`market_now` 把时间点**显式折算**到市场时区；
    * ``_configSMA()`` 照做，但配置落到 :class:`ConfigSmaStore`（默认内存）；
    * ``_onTest()`` / ``_getColorAndOrderArray()`` 照做。

    ## 依赖是"鸭子类型"的，便于测试注入

    ``ref`` 需要 ``get_symbol()`` / ``get_stock_id()`` / ``get_price()``；
    时钟与配置都可以注入，所以测试**不需要数据库、不依赖真实时钟**。
    """

    def __init__(self, ref: Any, source: Any = None, after_hour: bool = False,
                 now: Any = None, config: Any = None,
                 history_sql: Any = None, table: str = "stockhistory",
                 ema_sql_factory: Any = None) -> None:
        self.stock_ref = ref
        #: 日/周/月三套均线的周期。对应 PHP ``$aiNum = array(5, 10, 20)``。
        self.ai_num = AI_NUM

        self.sma: Dict[str, Any] = {}
        self.next_values: Dict[str, Any] = {}
        self.after_hour_values: Dict[str, Any] = {}
        self.colors: Dict[str, Any] = {}
        self.order: List[str] = []

        self.source = source if source is not None else ref
        self.config = config if config is not None else ConfigSmaStore()
        self._now = now
        self._history_sql = history_sql
        self._table = table
        #: 建``stockema*`` 表访问器的工厂；测试可注入假的
        self._ema_sql_factory = ema_sql_factory
        #: 是否把当天现价并入序列（``_onTest()`` 期间为真）
        self._prepend_price = False

        self.start_date = self._calc_start_date()
        self._config_sma()

        if after_hour and self.need_after_hour_est():
            self._on_test()
            self.colors, self.order = get_color_and_order_array(self.next_values)
        else:
            self.colors, self.order = get_color_and_order_array(self.sma)

    # -- 时钟与时区 -------------------------------------------------------
    def market_now(self) -> Any:
        """**市场当地**的当前时间。对应 PHP ``SetTimeZone()`` 之后的 ``GetNowYMD()``。

        PHP 靠改进程默认时区让 ``GetNowYMD()`` 返回当地时间；本版把时间点
        折算到 :func:`tzutil.get_time_zone` 给出的时区，不动进程状态。
        """
        import datetime

        moment = self._now() if callable(self._now) else (
            self._now if self._now is not None else datetime.datetime.now())
        if isinstance(moment, datetime.date) and not isinstance(moment, datetime.datetime):
            moment = datetime.datetime(moment.year, moment.month, moment.day)
        if moment.tzinfo is not None:
            return moment

        name = self._time_zone_name()
        if name is None:
            return moment
        return tzutil.localize(moment, name)

    def _time_zone_name(self) -> Any:
        """取标的时区名；拿不到就返回 ``None``（退回本机时间）。"""
        for target in (self.stock_ref, self.source):
            getter = getattr(target, "get_time_zone", None)
            if callable(getter):
                try:
                    return getter()
                except Exception:
                    continue
        return None

    def _market_ymd(self) -> str:
        """市场当地的今天（``YYYY-MM-DD``）。"""
        return _to_date(self.market_now()).isoformat()

    # -- 查库 -------------------------------------------------------------
    def history_sql(self) -> Any:
        if self._history_sql is not None:
            return self._history_sql
        connection = getattr(self.source, "connection", None)
        if connection is None:
            return None
        return StockHistorySql(connection, self._table)

    def _calc_start_date(self) -> Any:
        """对应 ``_calcStartDate()``（``php/stockhis.php:459``）。

        PHP 取**最近 2 条**（``GetAll($stockId, 0, 2)``）然后逐条问
        ``_ignoreCurrentTradingData()``，返回第一条"可以用"的日期::

            foreach ($record as ...)
            {
                if (_ignoreCurrentTradingData($strDate))  continue;
                return $strDate;
            }
            return false;

        **为什么取 2 条而不是 1 条**：若最新一条就是今天、而市场还没收盘
        （16 点前），它会被跳过，于是起点落到**上一个交易日**。
        返回 ``False`` → 本版 ``None``（调用方要能处理"没有可用历史"）。

        取的是 PHP 的 ``GetAll``（字段是 ``date``/``adjclose``），
        不是 ``GetFromDate``。
        """
        sql = self.history_sql()
        if sql is None:
            return None
        stock_id = self._stock_id()
        if stock_id is None:
            return None

        now_ymd = self._market_ymd()
        for record in sql.get_all(stock_id, 0, 2):
            date = record.get("date")
            if date is None:
                continue
            if ignore_current_trading_data(date, self.market_now()):
                continue
            return str(date)
        return None

    def _stock_id(self) -> Any:
        getter = getattr(self.stock_ref, "get_stock_id", None)
        return getter() if callable(getter) else None

    # -- 取数并切日/周/月三套 ---------------------------------------------
    def get_day_week_month_data(self) -> tuple:
        """对应 ``_getDayWeekMonthData()``（``php/stockhis.php:298``）。

        返回 ``(日线, 周线, 月线)`` 三套**复权**收盘价，全部**最新在前**。

        ## 遍历方向与切片方向

        ``GetFromDate()`` 给的是 ``ORDER BY date DESC``，所以循环是**从新到旧**。
        ``$strNextDayYMD`` 保存"上一条处理过的日期"，也就是**时间上更晚**的那条。

        于是 ``_isWeekEnd($strYMD, $strNextDayYMD)`` 的实际含义是
        "``strYMD`` 是不是它后面那条（更晚的）之前最后一个交易日" ——
        判据落在 ``GetDayOfWeek() >= next`` 上（见 :func:`is_week_end`）。
        这正是判据已经在第一刀里逐项对照过 360 例的原因：**最容易搞反**。

        ``prepend``（``_onTest`` 用它）会在取数**之前**先塞进当天的现价，
        这样均线里包含"还没收盘的今天"。

        ## "今天"必须走市场时区

        最前面那条没有"下一天"，``_isWeekEnd`` / ``_isMonthEnd`` 会退到
        ``GetNowYMD()`` —— PHP 那是 ``SetTimeZone()`` **之后**的时间，
        而 :func:`_today_ywd` 取的是**本机**日期。两者在跨时区或周末时会分叉
        （典型：本机周六而市场还在周五，``IsWeekend()`` 让月末判据整体偏移）。
        所以这里显式传 :func:`_market_ymd`，既对齐 PHP 也让结果可注入。
        """
        quotes = self._prepended_quotes()
        daily = list(quotes)
        weekly = list(quotes)
        monthly = list(quotes)

        sql = self.history_sql()
        if sql is not None and self.start_date is not None:
            stock_id = self._stock_id()
            if stock_id is not None:
                now_ymd = self._market_ymd()
                next_day = None
                for record in sql.get_from_date(stock_id, self.start_date, MAX_QUOTES_DAYS):
                    close = _as_number(record.get("adjclose"))
                    date = record.get("date")
                    daily.append(close)
                    if date is not None:
                        text = str(date)
                        if is_week_end(text, next_day, now_ymd):
                            weekly.append(close)
                        if is_month_end(text, next_day, now_ymd):
                            monthly.append(close)
                        next_day = text
        return daily, weekly, monthly

    def _prepended_quotes(self) -> list:
        """``_onTest()`` 那种"先把当天现价塞到最前面"的处理。

        PHP ``_onTest()`` 是先 ``$afClose = array($fPrice)`` 再调
        ``_getDayWeekMonthData()``，所以取回来的历史**接在现价之后**
        （也就是时间上更早）。本方法只返回那个"起头"的现价。
        """
        if not self._prepend_price:
            return []
        getter = getattr(self.stock_ref, "get_price", None)
        price = getter() if callable(getter) else None
        if price in (None, ""):
            return []
        return [_as_number(price)]

    # -- 配置（缓存 SMA） -------------------------------------------------
    def _build_next_name(self, name: str) -> str:
        """对应 ``_buildNextName()``：``'D5'`` → ``'D5Next'``。"""
        return name + "Next"

    def _cfg_set_sma(self, name: str, sma: Any, next_value: Any = None) -> None:
        """对应 ``_cfg_set_SMA()``：写内存 + 写配置。"""
        self.sma[name] = sma
        self.next_values[name] = next_value
        self.config.set_var(SMA_SECTION, name, sma)
        if next_value:
            self.config.set_var(SMA_SECTION, self._build_next_name(name), next_value)

    def _cfg_get_sma(self, name: str) -> None:
        """对应 ``_cfg_get_SMA()``：从配置读回。

        配置里存的是**字符串**（INI 文件本来就是文本），读回来要转成数值 ——
        否则 ``self.sma['D5']`` 会是 ``'98.2'`` 而不是 ``98.2``，
        调用方一做减法就 ``TypeError``（实测在缓存命中的路径上踩到过）。
        """
        value = self.config.read_var(SMA_SECTION, name)
        self.sma[name] = None if value in (None, "") else _as_number(value)
        next_value = self.config.read_var(SMA_SECTION, self._build_next_name(name))
        self.next_values[name] = (None if next_value in (None, "")
                                  else _as_number(next_value))

    def _cfg_set_smas(self, prefix: str, quotes: Sequence[float]) -> None:
        """对应 ``_cfg_set_SMAs()``。

        每个周期存**两个**值：当日的 ``_estSma(quotes, i)`` 与 T+1 的
        ``_estSma(quotes, i - 1)``。照抄这个 ``i - 1``。
        """
        for period in self.ai_num:
            self._cfg_set_sma(prefix + str(period),
                              est_sma(quotes, period),
                              est_sma(quotes, period - 1))

        bands = est_bollinger_bands(quotes, BOLL_DAYS)
        next_bands = est_next_bollinger_bands(quotes, BOLL_DAYS)
        if bands is None:
            up = down = None
        else:
            up, down = bands
        if next_bands is None:
            up_next = down_next = None
        else:
            up_next, down_next = next_bands
        self._cfg_set_sma(prefix + "BOLLUP", up, up_next)
        self._cfg_set_sma(prefix + "BOLLDN", down, down_next)

    def _cfg_get_smas(self, prefix: str) -> None:
        for period in self.ai_num:
            self._cfg_get_sma(prefix + str(period))
        self._cfg_get_sma(prefix + "BOLLUP")
        self._cfg_get_sma(prefix + "BOLLDN")

    def _load_config_sma(self) -> None:
        for prefix in ("D", "W", "M"):
            self._cfg_get_smas(prefix)

    def _save_config_sma(self) -> None:
        daily, weekly, monthly = self.get_day_week_month_data()
        self._cfg_set_smas("D", daily)
        self._cfg_set_smas("W", weekly)
        self._cfg_set_smas("M", monthly)
        self.config.save_data()

    def _config_sma(self) -> None:
        """对应 ``_configSMA()``（``php/stockhis.php:365``）。

        PHP 的判断是"配置里的 ``Date`` 是否等于本次的 ``strStartDate``"：

        * 相等 → 读缓存（``_loadConfigSMA``）；
        * 不等或没有该段落 → 重算并写回（``_saveConfigSMA``）。

        两种情况都会再取 EMA50 / EMA200。

        ## 一处有意偏离：拿不到起始日期时**不写缓存**

        ``_calcStartDate()`` 在没有可用历史时返回 ``None``（PHP 是 ``false``）。
        此时 PHP 照样写：``Date=``（空）+ 一串 0，而下次请求 ``'' == ''``
        判定为**命中**，于是把这张全是 0 的表当缓存读回去。

        本版在这个分支直接跳过缓存：没有日期就没有 key，写下去的东西既不能
        命中也没有意义。落到文件后端上，这条尤其重要 —— 否则**每个请求都会
        往磁盘写一份垃圾**（演示库里 ``^NDX`` 正好没有历史，一开开关就中招）。
        """
        current = self.start_date
        if not current:
            self._get_ema(50)
            self._get_ema(200)
            return

        if self.config.has_section(SMA_SECTION):
            stored = self.config.read_var(SMA_SECTION, "Date")
            if stored == current:
                self._load_config_sma()
            else:
                self.config.set_var(SMA_SECTION, "Date", current)
                self._save_config_sma()
        else:
            self.config.set_var(SMA_SECTION, "Date", current)
            self._save_config_sma()

        self._get_ema(50)
        self._get_ema(200)

    def _get_ema(self, days: int) -> None:
        """对应 ``_get_EMA()``（``php/stockhis.php:258``）。

        .. code-block:: php

            $sql = GetStockEmaSql($iDays);
            $this->arSMA[$strName] = $sql->GetClose($this->GetStockId(), $this->strStartDate);
            $this->arNext[$strName] = false;
            $this->arAfterHour[$strName] = false;

        三个要点：

        * EMA 直接查 ``stockema50`` / ``stockema200`` 两表，**不是**算出来的；
        * ``GetClose()`` 是**按 ``strStartDate`` 精确取**，不是取最近一条；
        * ``next`` 与 ``after_hour`` 都是 ``False``（本版 ``None``）——
          EMA 不做 T+1 推算，也不参与盘后估值。照抄。
        """
        name = "EMA" + str(days)
        value = None
        if self.start_date is not None:
            sql = self._ema_sql(days)
            if sql is not None:
                close = sql.get_close(self._stock_id(), self.start_date)
                if close not in (None, ""):
                    value = _as_number(close)
        self.sma[name] = value
        self.next_values[name] = None
        self.after_hour_values[name] = None

    def _ema_sql(self, days: int) -> Any:
        """``stockema50`` / ``stockema200`` 的访问器（对应 ``GetStockEmaSql()``）。

        注入 ``ema_sql_factory`` 时用它建，否则用数据源上的连接。
        没有连接就返回 ``None`` —— EMA 为空不会让整张表挂掉。
        """
        table = "stockema%d" % days
        if callable(self._ema_sql_factory):
            return self._ema_sql_factory(table)
        if self._ema_sql_factory is not None:
            return None
        connection = getattr(self.source, "connection", None)
        if connection is None:
            return None
        return StockHistorySql(connection, table)

    # -- 盘后估值 ---------------------------------------------------------
    def need_after_hour_est(self) -> bool:
        """对应 ``NeedAfterHourEst()``（``php/stockhis.php:491``）。

        .. code-block:: php

            if ($this->GetStartDate() == $ref->GetDate())  return false;
            $ref->SetTimeZone();
            $now_ymd = GetNowYMD();
            return ($now_ymd->GetHourMinute() > 1520) ? true : false;

        两个条件：**起始日不等于最新行情日**（否则今天的数据已经在序列里了，
        不需要盘后补），且**当地时刻晚于 15:20**。
        """
        if str(self.start_date) == str(self._ref_date()):
            return False
        return self._hour_minute() > 1520

    def _ref_date(self) -> Any:
        getter = getattr(self.stock_ref, "get_date", None)
        return getter() if callable(getter) else None

    def _hour_minute(self) -> int:
        """对应 ``GetHourMinute()``：当地 ``HHMM`` 整数（``15:20`` → ``1520``）。"""
        moment = self.market_now()
        return moment.hour * 100 + moment.minute

    def _on_test(self) -> None:
        """对应 ``_onTest()``：把当天现价并进三套序列后重算。"""
        self._prepend_price = True
        try:
            daily, weekly, monthly = self.get_day_week_month_data()
        finally:
            self._prepend_price = False
        self._on_test_data("D", daily)
        self._on_test_data("W", weekly)
        self._on_test_data("M", monthly)

    def _on_test_data(self, prefix: str, quotes: Sequence[float]) -> None:
        for period in self.ai_num:
            self.after_hour_values[prefix + str(period)] = est_sma(quotes, period)
        bands = est_bollinger_bands(quotes, BOLL_DAYS)
        if bands is None:
            up = down = None
        else:
            up, down = bands
        self.after_hour_values[prefix + "BOLLUP"] = up
        self.after_hour_values[prefix + "BOLLDN"] = down

    # -- 对外 -------------------------------------------------------------
    def get_ref(self) -> Any:
        return self.stock_ref

    def get_start_date(self) -> Any:
        return self.start_date

    def get_sma(self) -> Dict[str, Any]:
        return self.sma

    def get_order_array(self) -> List[str]:
        return self.order

    def get_color(self, key: str) -> Any:
        return self.colors.get(key)

    def get_bull_bear(self) -> Any:
        """对应 ``GetBullBear()``：``EMA50 > EMA200`` 为牛市。

        PHP 返回的是带颜色的 HTML 片段（``GetFontElement('牛市')`` /
        ``GetRemarkElement('熊市')``），那属于渲染层 —— 本版返回
        ``('bull'|'bear', 文案)``，HTML 由调用方拼。

        没有 EMA50 时返回 ``None``（PHP 返回 ``false``）。
        """
        if "EMA50" not in self.sma:
            return None
        left = self.sma.get("EMA50")
        right = self.sma.get("EMA200")
        if left is None or right is None:
            return None
        if _as_number(left) > _as_number(right):
            return ("bull", "牛市")
        return ("bear", "熊市")

    # -- PHP 原名 ---------------------------------------------------------
    GetStartDate = get_start_date
    GetRef = get_ref
    GetSMA = get_sma
    GetOrderArray = get_order_array
    GetColor = get_color
    GetBullBear = get_bull_bear
    NeedAfterHourEst = need_after_hour_est
    _calcStartDate = _calc_start_date
    _getDayWeekMonthData = get_day_week_month_data
    _buildNextName = _build_next_name
    _cfg_set_SMA = _cfg_set_sma
    _cfg_get_SMA = _cfg_get_sma
    _cfg_set_SMAs = _cfg_set_smas
    _cfg_get_SMAs = _cfg_get_smas
    _loadConfigSMA = _load_config_sma
    _saveConfigSMA = _save_config_sma
    _configSMA = _config_sma
    _get_EMA = _get_ema
    _onTest = _on_test
    _onTestData = _on_test_data


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md 第 3 条）
# ---------------------------------------------------------------------------
_estSma = est_sma
GetQuadraticEquationRoot = get_quadratic_equation_root
_estBollingerBands = est_bollinger_bands
_estNextBollingerBands = est_next_bollinger_bands
_isWeekEnd = is_week_end
_isMonthEnd = is_month_end
_ignoreCurrentTradingData = ignore_current_trading_data
IsStockTradingHourEnd = is_stock_trading_hour_end
GetStockHistorySql = StockHistorySql
