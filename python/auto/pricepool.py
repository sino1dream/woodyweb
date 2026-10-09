"""价格分布统计表 —— 对应 ``php/ui/pricepoolparagraph.php``（136 行）。

PHP 这一份**几乎不用** ``table.php`` 的列体系，而是手写 HEREDOC 拼 ``<tr>/<td>``：

.. code-block:: php

    echo <<<END
    <TABLE borderColor=#cccccc cellSpacing=0 width=570 border=1 class="text" id="pricepool">
    <tr>
        <td class=c1 width=150 align=center>{$arColumn[0]}</td>
        <td class=c1 width=60  align=center>{$arColumn[1]}</td>
        <td class=c1 width=120 align=center>{$strTradingSymbol}{$arColumnEx[0]}</td>
        ...
END;

所以它是整个 ``php/ui`` 里**最接近模板**的一个段落 —— 数据部分是两个纯计数类
（``PriceGoal`` / ``PricePool``），表现部分是固定五列。迁移成本几乎为零。

## 它在统计什么

``PricePool::OnData($fVal, $fCompare)`` 把每个交易日按**两个维度**分类，
产出一个 3×3 的计数矩阵：

* ``$fVal``     —— **溢价**：``>0`` 溢价 / 空 平价 / ``<0`` 折价，决定落在哪个
  ``PriceGoal``（``h_goal`` / ``u_goal`` / ``l_goal``）；
* ``$fCompare`` —— **涨跌**：``>0`` 涨 / 空 不变 / ``<=0`` 跌，决定 ``PriceGoal``
  里哪个计数器（``iHigher`` / ``iUnchanged`` / ``iLower``）加一。

两个调用方喂的都是 **(溢价, 涨跌)** 这个顺序：

* ``woody/res/netvalueclosecn.php:12``   —— ``OnData(arWord[1], arWord[2])``
* ``woody/res/php/_thanousparadox.php:13`` —— ``OnData(arWord[2], arWord[1])``

后者看起来参数反了，但它 ``Write`` 时的列序本来就不同（第 1 列写的是
``est_ref`` 的涨跌幅、第 2 列才是溢价），传到 ``OnData`` 时同样是溢价在前。

**``empty()`` 的语义要照抄**：PHP 的 ``empty(0.0)`` 为真，所以溢价恰好为 0
算"平价"而不是"折价"；涨跌为 0 也算"不变"。Python 里 ``not 0.0`` 同样为真，
语义一致 —— 这里不能写成 ``value is None``。

## ⚠️ PHP 的 ``$bTranspose`` 是个 bug，本版不照抄

``EchoPricePoolParagraph()`` 的 ``$bTranspose`` **默认为 true**，而它做的事是
把「行标签数组」和「列标签数组」互换着传进渲染函数：

.. code-block:: php

    $arColumnEx = array('涨', '不变', '跌');          // 涨跌轴
    $arRow      = array('溢价', '平价', '折价');       // 溢价轴

    if ($bTranspose)  _echoPricePoolParagraph(..., $arRow, $arColumnEx);
    else              _echoPricePoolParagraph(..., $arColumnEx, $arRow);

而渲染函数内部**行索引永远对应 ``h/u/l_goal``（溢价轴）、列索引永远对应
``higher/unchanged/lower``（涨跌轴）** —— 这两条从不改变。也就是说
``$bTranspose`` **只换了标签，没有换数据**。

后果：

* ``transpose=false``：行标溢价轴、列标涨跌轴，**与数据一致，正确**；
* ``transpose=true`` ：行标涨跌轴、列标溢价轴，**与数据错位**，
  9 格里只有对角线 3 格语义正确，其余 6 格是张冠李戴。

唯一显式传 ``false`` 的调用方是 ``_thanousparadox.php:23``；
``netvalueclosecn.php:21`` 用的是默认值，**正在吃这个 bug**。

本版的三个取值：

* ``transpose=False``（默认）—— 等价 PHP 的 ``false``，行=溢价、列=涨跌；
* ``transpose=True``  —— **真的转置数据**，行列标签跟着一起换，语义正确；
* ``legacy_labels=True`` —— 复刻 PHP「只换标签」的行为，**仅用于逐格对照排查**，
  正常页面不要用。

## 刻意未迁的部分

``PricePoolCsvFile extends PageCsvFile``（``php/ui/pricepoolparagraph.php:69``）
没有搬：``PageCsvFile`` 属于 ``php/csvfile.php``（文件 I/O + 分页写盘，未迁移），
且它在这里唯一的作用就是给子类挂一个 ``$pool``。Python 侧调用方直接持有一个
:class:`PricePool` 即可 —— 少一层继承，多的那层没有行为。
"""

from __future__ import annotations

from typing import Any, Sequence

from uicolumn import column_base, columns_to_dicts

#: PHP 表头那五个固定宽度：150 + 60 + 120*3 = 570。
PRICE_POOL_WIDTH_SYMBOL = 150
PRICE_POOL_WIDTH_TOTAL = 60
PRICE_POOL_WIDTH_ITEM = 120

#: PHP ``<TABLE ... width=570 ... id="pricepool">``。
PRICE_POOL_TABLE_WIDTH = 570
PRICE_POOL_TABLE_ID = "pricepool"

#: 涨跌轴（PHP 的 ``$arColumnEx``）。顺序对应 ``higher / unchanged / lower``。
PRICE_POOL_CHANGE_LABELS = ("涨", "不变", "跌")

#: 溢价轴（PHP 的 ``$arRow``）。顺序对应 ``h_goal / u_goal / l_goal``。
#: 首项在 PHP 里取自 ``TableColumnGetPremium()``，即 :data:`uicolumn.STOCK_DISP_PREMIUM`。
PRICE_POOL_PREMIUM_LABELS = ("溢价", "平价", "折价")

#: ``PriceGoal`` 的三个计数器在"涨跌轴"上的顺序，与 :data:`PRICE_POOL_CHANGE_LABELS` 一一对应。
_CHANGE_FIELDS = ("higher", "unchanged", "lower")


class PriceGoal:
    """一组的计数，对应 PHP ``PriceGoal``（``pricepoolparagraph.php:4``）。

    四个计数器：``total`` / ``higher``（涨）/ ``unchanged``（不变）/ ``lower``（跌）。

    判定照抄 PHP 的 ``empty()`` 语义 —— ``0``、``0.0``、``None`` 都算"不变"：

    .. code-block:: php

        if (empty($fVal))          $this->iUnchanged ++;
        else if ($fVal > 0.0)      $this->iHigher ++;
        else                       $this->iLower ++;
    """

    def __init__(self) -> None:
        self.total = 0
        self.higher = 0
        self.unchanged = 0
        self.lower = 0

    def add_data(self, value: Any) -> None:
        """按涨跌分档计数，对应 ``PriceGoal::AddData()``。"""
        if not value:
            self.unchanged += 1
        elif value > 0:
            self.higher += 1
        else:
            self.lower += 1
        self.total += 1

    def counts(self) -> tuple:
        """取 ``(涨, 不变, 跌)`` 三元组，顺序与 :data:`PRICE_POOL_CHANGE_LABELS` 对齐。"""
        return (self.higher, self.unchanged, self.lower)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "PriceGoal(total=%d, higher=%d, unchanged=%d, lower=%d)" % (
            self.total,
            self.higher,
            self.unchanged,
            self.lower,
        )

    #: PHP 方法名别名，方便按 PHP 源码逐行核对。
    AddData = add_data


class PricePool:
    """按溢价分三组的计数池，对应 PHP ``PricePool``（``pricepoolparagraph.php:39``）。

    三个成员与溢价三档一一对应：

    ==============  ==========  ==========================
    成员             溢价档       条件
    ==============  ==========  ==========================
    ``h_goal``      溢价         ``$fVal > 0``
    ``u_goal``      平价         ``empty($fVal)``（含 0）
    ``l_goal``      折价         ``$fVal < 0``
    ==============  ==========  ==========================
    """

    def __init__(self) -> None:
        self.h_goal = PriceGoal()
        self.u_goal = PriceGoal()
        self.l_goal = PriceGoal()

    @property
    def goals(self) -> tuple:
        """溢价轴上的三个 :class:`PriceGoal`，顺序与 :data:`PRICE_POOL_PREMIUM_LABELS` 对齐。"""
        return (self.h_goal, self.u_goal, self.l_goal)

    def on_data(self, value: Any, compare: Any) -> None:
        """投一条数据，对应 ``PricePool::OnData()``。

        ``value`` 是溢价、``compare`` 是涨跌 —— **顺序不能反**，
        两个调用方都是溢价在前（见模块 docstring）。
        """
        if not value:
            self.u_goal.add_data(compare)
        elif value > 0:
            self.h_goal.add_data(compare)
        else:
            self.l_goal.add_data(compare)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "PricePool(h=%r, u=%r, l=%r)" % (self.h_goal, self.u_goal, self.l_goal)

    #: PHP 方法名别名。
    OnData = on_data


def build_price_pool_columns(
    symbol: str,
    trading_symbol: str = "",
    premium_first: bool = False,
) -> list:
    """五列表头，对应 ``_echoPricePoolParagraph()`` 里那段手写 ``<tr>``。

    ``premium_first=False``（默认）时后三列是涨跌轴（``涨/不变/跌``），
    对应 PHP 的 ``$bTranspose = false``；``True`` 时后三列是溢价轴。

    ``trading_symbol`` 直接拼在列头前面，中间没有分隔符 —— 这是照抄 PHP 的
    ``{$strTradingSymbol}{$arColumnEx[0]}``，页面上的效果类似 ``QQQ涨``。
    """
    labels = PRICE_POOL_PREMIUM_LABELS if premium_first else PRICE_POOL_CHANGE_LABELS
    return columns_to_dicts(
        [
            column_base("%s交易" % _text(symbol), PRICE_POOL_WIDTH_SYMBOL),
            column_base("天数", PRICE_POOL_WIDTH_TOTAL),
        ]
        + [
            column_base("%s%s" % (_text(trading_symbol), label), PRICE_POOL_WIDTH_ITEM)
            for label in labels
        ]
    )


def build_price_pool_rows(
    pool: PricePool,
    transpose: bool = False,
    legacy_labels: bool = False,
) -> list:
    """三行数据（纯文本，无颜色）。

    ``transpose=False``（默认）：行是溢价轴，每行 ``[溢价档, 该档总天数, 涨, 不变, 跌]``。
    ``transpose=True``         ：行是涨跌轴，每行 ``[涨跌档, 该档总天数, 溢价, 平价, 折价]``
    —— **数据真的被转置了**，与 PHP 的同名参数行为不同（见模块 docstring）。

    ``legacy_labels=True`` 时复刻 PHP「只换标签不换数据」的行为，
    用于和线上页面逐格对照，正常渲染不要用。
    """
    goals = pool.goals
    if not transpose:
        return [
            [PRICE_POOL_PREMIUM_LABELS[index], goal.total] + list(goal.counts())
            for index, goal in enumerate(goals)
        ]

    if legacy_labels:
        # PHP 的 $bTranspose=true：标签换了，数据没换 —— 9 格里 6 格是错的。
        return [
            [PRICE_POOL_CHANGE_LABELS[index], goal.total] + list(goal.counts())
            for index, goal in enumerate(goals)
        ]

    # 真转置：cell(涨跌 i, 溢价 j) = goals[j].counts()[i]
    rows = []
    for measure_index, change_label in enumerate(PRICE_POOL_CHANGE_LABELS):
        cells = [goal.counts()[measure_index] for goal in goals]
        total = sum(cells)
        rows.append([change_label, total] + cells)
    return rows


def build_price_pool_table(
    pool: PricePool,
    symbol: str,
    trading_symbol: str = "",
    transpose: bool = False,
    legacy_labels: bool = False,
    title: str = "",
) -> dict:
    """一步到位给出 ``{"columns", "rows", ...}``，供模板渲染。

    ============================  ==========================================
    返回键                         含义
    ============================  ==========================================
    ``columns``                    五列表头（含宽度）
    ``rows``                       三行，每行五个**纯文本**格
    ``width``                      PHP 的 ``width=570``
    ``id``                         PHP 的 ``id="pricepool"``
    ``title``                      表格标题，可为空
    ============================  ==========================================

    与参考数据表（``reference_service``）不同，这里**没有颜色也没有外链**，
    所以行直接给字符串列表而不是 ``TableRow`` —— 没有需要承载的额外信息。
    """
    return {
        "title": title,
        "id": PRICE_POOL_TABLE_ID,
        "width": PRICE_POOL_TABLE_WIDTH,
        "columns": build_price_pool_columns(
            symbol, trading_symbol, premium_first=transpose
        ),
        "rows": build_price_pool_rows(
            pool, transpose=transpose, legacy_labels=legacy_labels
        ),
    }


def price_pool_from_rows(rows: Sequence[Sequence[Any]], value_index: int = 0, compare_index: int = 1) -> PricePool:
    """从一批 ``(溢价, 涨跌)`` 记录建 :class:`PricePool`。

    ``value_index`` / ``compare_index`` 对应 CSV 的两个列号：两个调用方写盘时的
    列序不一样（``netvalueclosecn`` 是溢价在前、``thanousparadox`` 是涨跌在前），
    所以顺序由调用方显式给出，而不是写死。
    """
    pool = PricePool()
    for row in rows:
        if len(row) <= max(value_index, compare_index):
            continue
        pool.on_data(_float(row[value_index]), _float(row[compare_index]))
    return pool


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _float(value: Any) -> float:
    """转成 float；转不出来按 0 处理（PHP 的 ``floatval()`` 也是这个语义）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# PHP 函数/类名别名：本模块是唯一同时有类又有顶层函数的一批，
# 别名让按 PHP 源码逐行核对时不用在两种命名间来回切换。
# ---------------------------------------------------------------------------
PriceGoalAddData = PriceGoal.add_data
PricePoolOnData = PricePool.on_data
EchoPricePoolParagraph = build_price_pool_table
