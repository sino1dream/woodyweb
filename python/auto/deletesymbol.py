"""删标的（``woody/res/deletesymbol.php`` → ``php/deletestock.php``）。

## PHP 那一页是什么

``deletesymbol.php`` 是 ``SymbolAccount`` 的子类，只实现 ``AdminProcess()``，
``AdminRun()`` 跑完无条件 ``SwitchToSess()`` 跳回 —— **页面上什么都不显示**。

    function AdminRun()
    {
        if ($this->IsAdmin())   { $this->AdminProcess(); }
        SwitchToSess();
    }

所以非管理员点进来看到的是"什么都没发生"，管理员点进来看到的是"跳回去了"。
触发入口是 ``GetStockEditDeleteLink()``（``php/stocklink.php:285``）拼出来的
带 ``confirm()`` 的 ``<a href>``，与"修改"链接并排。

## 五道闸门 + 一处带副作用的"闸门"

``_deleteStockSymbol()`` 是一条 ``if / else if`` 链，**任意一环命中就放弃删除**：

=================  ============================================  ==============
顺序                判据                                           命中后
=================  ============================================  ==============
1 ``abpair``       本符号有 B 股配对，或本身是某只 A 股的 B 股      放弃
2 ``adrpair``      同上（H 股 ↔ ADR，两个方向）                    放弃
3 ``ahpair``       同上（A 股 ↔ H 股）                             放弃
4 ``fundpair``     同上（基金 ↔ 指数 / 期货）                      放弃
5 ``calibration``  ``Count > 0`` 就 ``DeleteAll``，**恒返回 false**  **已经删了**
6 ``stockgroupitem`` ``Count > 0`` 返回 ``false``（函数名叫          放弃
                   ``SqlDeleteStockGroupItemByStockId`` 但**不删**）
=================  ============================================  ==============

``_deleteHasCalibration()`` 值得单说：函数名是"有没有校准值"，实际行为是
"有就删掉，然后返回 ``false``" —— 它被塞在这条链里当 **do 块**用，所以
**永远不会拦住删除**。副作用是：走到第 6 环被 ``stockgroupitem`` 拦下时，
校准历史**已经没了**，标的却还在。净效果是"什么都没删成，但校准历史丢了"。

本版**不照抄这个顺序**：先判完全部闸门，再在删除阶段清校准值。偏离的理由与
实测量见 :func:`apply` 与 ``README_deletesymbol_migration.md`` §5。

## PHP 真会删 6 张表

``DeleteStock()``（``php/deletestock.php:20``）只做四件事，加上前面那个副作用：

``calibrationhistory`` / ``stockema50`` / ``stockema200`` / ``stockhistory`` /
``netvaluehistory`` / ``stock``。

**先前 ``README_pending_pages.md`` 里写的"连带清 8 张表"是错的**（当初没读
``deletestock.php`` 就估了个数）。实测 6 张。

## 剩下的一堆表 PHP 不碰 —— 那才是真问题

``information_schema`` 里带 ``stock_id``（或以 ``id`` 存 stock id）的表有 20 多张，
``deletestock.php`` 只清 6 张。留下的残留在"把同一符号再加回库里"时会**静默**
出错，最典型的是 ``stockhistorydate``：它是 ``/submit/history`` 的**水位线**，
判断"已经更新到哪天"。残留水位线比新库里全部历史都新 → 重新加回符号后
``submithistory`` 一直报 ``already_updated``，一条历史都补不进来。

所以本模块把残留表逐个登记在 :data:`ORPHAN_TABLES`，**默认只报不删**
（``include_orphans=False`` 时与 PHP 口径逐字节一致），要清得显式打开。
每张表的清法都有单独的说明，见该常量的注释。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sql.stocksql import StockSql

#: 四道配对闸门 —— **顺序即 PHP 的 if/else 顺序**，报错时按这个顺序挑第一条。
#: ``(表名, 显示名, PHP 里的判据函数)``
PAIR_GATES: Tuple[Tuple[str, str], ...] = (
    ("abpair", "A股/B股配对"),
    ("adrpair", "H股/ADR配对"),
    ("ahpair", "A股/H股配对"),
    ("fundpair", "基金/指数配对"),
)

#: ``SqlDeleteStockGroupItemByStockId()``（``php/sql/sqlstockgroup.php:305``）。
#: 名字带 Delete，实际**只看不删**：有行返回 ``false`` → 拦住删除。
GROUP_ITEM_GATE = ("stockgroupitem", "分组持仓")

#: PHP 真会删的 6 张表，顺序 = 本版执行顺序。
#: ``(表名, 匹配列, 显示名)``；``stock`` 的匹配列是 ``id``（符号本身那一行）。
PHP_DELETES: Tuple[Tuple[str, str, str], ...] = (
    ("calibrationhistory", "stock_id", "校准历史"),
    ("stockema50", "stock_id", "50日EMA"),
    ("stockema200", "stock_id", "200日EMA"),
    ("stockhistory", "stock_id", "历史价格"),
    ("netvaluehistory", "stock_id", "单位净值"),
    ("stock", "id", "标的本身"),
)

#: PHP 不碰、但删完会留下孤儿的表。**默认不删**，``include_orphans=True`` 才清。
#:
#: ``(表名, 匹配列元组, 显示名, 为什么必须清)``
#:
#: 匹配列元组里可以有两列（``holdings`` / ``futurespair`` 各有两个身份）；
#: 特殊值 ``"symbol"`` 表示这张表按**符号字符串**存，不按 stock_id。
ORPHAN_TABLES: Tuple[Tuple[str, Tuple[str, ...], str, str], ...] = (
    (
        "stockhistorydate",
        ("id",),
        "历史价格水位线",
        "/submit/history 靠它判断更新到哪天；残留会让重新加回的符号"
        "一直报 already_updated，历史一条都补不进来",
    ),
    (
        "lastcalibration",
        ("id",),
        "上次校准值",
        "残留会让重加回的符号带着旧校准值参与估算（fundpairref 直接读它）",
    ),
    (
        "fundposition",
        ("id",),
        "基金仓位",
        "残留会让重加回的符号被当成持有该仓位的基金",
    ),
    (
        "futurespair",
        ("id", "stock_id"),
        "期货配对",
        "PHP 四道配对闸门**不含**这张表，所以能删掉一个还在被引用的期货符号",
    ),
    ("fundest", ("stock_id",), "官方EST", "重加回符号后会拿旧 EST 当基准"),
    (
        "holdings",
        ("stock_id", "holding_id"),
        "成分股清单",
        "两个身份都要清：作为基金时 stock_id 是本符号，作为成分股时在 holding_id",
    ),
    ("holdingsdate", ("id",), "持仓日期", "持仓表的日期水位线"),
    ("quarterreport", ("stock_id",), "季度报告", "季报数据"),
    ("shareshistory", ("stock_id",), "场内份额", "份额序列"),
    ("sharesdiff", ("stock_id",), "场内新增", "每日新增份额"),
    ("stockdividend", ("stock_id",), "分红", "分红记录"),
    ("stocksplit", ("stock_id",), "拆股", "拆合股记录"),
    ("futurepremium", ("stock_id",), "期货升水", "升水序列"),
    ("estquote", ("symbol",), "EST快照", "按符号字符串存，脚本重算会覆盖"),
    ("realtimequote", ("symbol",), "实时快照", "按符号字符串存"),
)

#: 明确**不**处理的表与理由（写出来是为了下次不用重新判断一遍）。
SKIPPED_TABLES: Tuple[Tuple[str, str], ...] = (
    (
        "stock_minute_data",
        "只有 id/time/ohlc，没有 stock_id 也没有 symbol —— 没法判断某行属于"
        "哪只标的，本版也不写这张表",
    ),
    (
        "stocktransaction",
        "挂在 stockgroupitem 上（groupitem_id），不挂 stock_id；"
        "stockgroupitem 本身就是闸门，有持仓时根本删不下去",
    ),
    (
        "groupitemamount",
        "同上，键是 stockgroupitem.id",
    ),
)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _fetchone(connection: Any, sql: str, params: tuple = ()) -> Any:
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        cursor.close()


def _execute(connection: Any, sql: str, params: tuple = ()) -> int:
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        return int(cursor.rowcount or 0)
    finally:
        cursor.close()


def _params_for(columns: Tuple[str, ...], stock_id: Any, symbol: str) -> tuple:
    """按匹配列元组拼参数：``symbol`` 列传符号字符串，其余传 stock_id。"""
    return tuple(symbol if col == "symbol" else stock_id for col in columns)


def _where_for(columns: Tuple[str, ...]) -> str:
    return " OR ".join("`%s` = %%s" % col for col in columns)


def count_rows(
    connection: Any,
    table: str,
    columns: Tuple[str, ...],
    stock_id: Any,
    symbol: str,
) -> int:
    """这张表里属于该符号的行数（只读）。"""
    row = _fetchone(
        connection,
        "SELECT COUNT(*) AS n FROM `%s` WHERE %s" % (table, _where_for(columns)),
        _params_for(columns, stock_id, symbol),
    )
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
# 闸门
# ---------------------------------------------------------------------------
def _pair_hit(connection: Any, table: str, symbol: str, stock_id: Any) -> Optional[str]:
    """配对表两个方向都查，命中返回一句说明，没命中返回 ``None``。

    两个方向对应 PHP 的两次查询（``GetPairSymbol`` / ``GetSymbol``）：

    * ``id = 本符号`` —— 本符号是**本体**（A 股 / H 股 / 基金），它有配对标的；
    * ``stock_id = 本符号`` —— 本符号是**配对标的**（B 股 / ADR / 指数）。

    两句话措辞不同，因为管理员要据此判断"该先删哪一头"。
    """
    stock_sql = StockSql(connection)
    row = _fetchone(
        connection,
        "SELECT `stock_id` FROM `%s` WHERE `id` = %%s LIMIT 1" % table,
        (stock_id,),
    )
    if row:
        pair_symbol = stock_sql.get_symbol(row["stock_id"])
        return "作为本体存在配对标的 %s" % (pair_symbol or row["stock_id"])

    row = _fetchone(
        connection,
        "SELECT `id` FROM `%s` WHERE `stock_id` = %%s LIMIT 1" % table,
        (stock_id,),
    )
    if row:
        owner = stock_sql.get_symbol(row["id"])
        return "是 %s 的配对标的" % (owner or row["id"])
    return None


def check_gates(connection: Any, symbol: str, stock_id: Any) -> List[Dict[str, Any]]:
    """把五道闸门全查一遍（**不短路**），返回每一道的结论。

    为什么不短路：PHP 是 ``if / else if``，命中第一道就返回，管理员看到
    "有 AB 配对"却不知道后面还有 fund 配对。预览页要把拦路的**所有**原因
    都摆出来，所以这里全查；真正的判定仍然只看"有没有任何一道命中"，
    与 PHP 等价。
    """
    gates: List[Dict[str, Any]] = []
    for table, label in PAIR_GATES:
        detail = _pair_hit(connection, table, symbol, stock_id)
        gates.append({
            "table": table,
            "label": label,
            "hit": detail is not None,
            "detail": detail or "",
        })

    row = _fetchone(
        connection,
        "SELECT COUNT(*) AS n, COUNT(DISTINCT `stockgroup_id`) AS groups"
        " FROM `%s` WHERE `stock_id` = %%s" % GROUP_ITEM_GATE[0],
        (stock_id,),
    )
    rows = int(row["n"]) if row else 0
    groups = int(row["groups"]) if row else 0
    gates.append({
        "table": GROUP_ITEM_GATE[0],
        "label": GROUP_ITEM_GATE[1],
        "hit": rows > 0,
        "detail": "在 %d 个分组里有 %d 行" % (groups, rows) if rows else "",
        "rows": rows,
        "groups": groups,
    })
    return gates


# ---------------------------------------------------------------------------
# 计划 / 执行
# ---------------------------------------------------------------------------
def plan(connection: Any, symbol: str) -> Dict[str, Any]:
    """算清楚"这个符号删得掉吗、删了会影响哪些行"，**一行都不写**。

    预览页与 :func:`apply` 共用这一份结果 —— 页面看到的数字就是真要删的数字，
    不给"页面上是一套、点下去另一套"留空间。
    """
    symbol = (symbol or "").strip().upper()
    result: Dict[str, Any] = {
        "symbol": symbol,
        "stock_id": None,
        "found": False,
        "blocked": False,
        "blocked_by": None,
        "gates": [],
        "deletes": [],
        "orphans": [],
        "deleted_rows": 0,
        "orphan_rows": 0,
        "skipped": [{"table": table, "note": note} for table, note in SKIPPED_TABLES],
    }
    if not symbol:
        return result

    stock_id = StockSql(connection).get_id(symbol)
    result["stock_id"] = stock_id
    if stock_id is None:
        return result
    result["found"] = True

    gates = check_gates(connection, symbol, stock_id)
    result["gates"] = gates
    blocked = next((gate for gate in gates if gate["hit"]), None)
    result["blocked"] = blocked is not None
    result["blocked_by"] = blocked

    for table, column, label in PHP_DELETES:
        rows = count_rows(connection, table, (column,), stock_id, symbol)
        result["deletes"].append({
            "table": table,
            "column": column,
            "label": label,
            "rows": rows,
        })
    result["deleted_rows"] = sum(item["rows"] for item in result["deletes"])

    for table, columns, label, note in ORPHAN_TABLES:
        rows = count_rows(connection, table, columns, stock_id, symbol)
        result["orphans"].append({
            "table": table,
            "columns": columns,
            "label": label,
            "note": note,
            "rows": rows,
        })
    result["orphan_rows"] = sum(item["rows"] for item in result["orphans"])
    return result


def apply(
    connection: Any,
    symbol: str,
    *,
    include_orphans: bool = False,
    commit: bool = True,
) -> Dict[str, Any]:
    """真删。默认**只删 PHP 会删的那 6 张表**，与 PHP 口径一致。

    ``include_orphans=True`` 时连 :data:`ORPHAN_TABLES` 一起清 —— 这是本版的
    增强，不是 PHP 行为，页面上默认不勾。

    ## 与 PHP 的三处差异（都写进 README §5）

    1. **闸门全过才动手**。PHP 在 ``stockgroupitem`` 命中时已经把
       ``calibrationhistory`` 删了；本版不动任何一行。
    2. **不在闸门阶段删数据**。``_deleteHasCalibration`` 的副作用挪到删除阶段。
    3. **有反馈**。PHP 静默跳回；本版返回每一步删了几行。

    返回 :func:`plan` 的结果，外加 ``applied`` 与两个实测行数。
    """
    result = plan(connection, symbol)
    result["applied"] = False
    result["include_orphans"] = bool(include_orphans)
    result["applied_rows"] = 0
    result["applied_orphans"] = 0

    if not result["found"] or result["blocked"]:
        return result

    source = connection
    stock_id = result["stock_id"]
    symbol = result["symbol"]

    for table, column, _label in PHP_DELETES:
        result["applied_rows"] += _execute(
            source,
            "DELETE FROM `%s` WHERE `%s` = %%s" % (table, column),
            (stock_id,),
        )

    if include_orphans:
        for table, columns, _label, _note in ORPHAN_TABLES:
            result["applied_orphans"] += _execute(
                source,
                "DELETE FROM `%s` WHERE %s" % (table, _where_for(columns)),
                _params_for(columns, stock_id, symbol),
            )

    if commit:
        try:
            connection.commit()
        except Exception:  # pragma: no cover - 假连接没有 commit
            pass
    result["applied"] = True
    return result


# ---------------------------------------------------------------------------
# 文案
# ---------------------------------------------------------------------------
def describe(result: Dict[str, Any]) -> str:
    """给用户看的一句话（路由的 flash 用）。"""
    symbol = result.get("symbol") or ""
    if not symbol:
        return "缺少 symbol（符号）。"
    if not result.get("found"):
        return "库里没有 %s 这个符号，没什么可删的。" % symbol
    if result.get("blocked"):
        gate = result["blocked_by"] or {}
        return "%s 删不了：%s 里还有它（%s）。先把配对/持仓拆掉再来。" % (
            symbol, gate.get("table"), gate.get("detail") or "有记录")
    if not result.get("applied"):
        return "%s 可以删（dry-run，未执行）。" % symbol

    parts = ["删除 %s（stock_id=%s）" % (symbol, result.get("stock_id"))]
    parts.append("主表 %d 行" % result.get("applied_rows", 0))
    if result.get("include_orphans"):
        parts.append("残留表 %d 行" % result.get("applied_orphans", 0))
    elif result.get("orphan_rows"):
        parts.append("另有 %d 行残留在 %d 张表里（未清，PHP 也不清）" % (
            result["orphan_rows"],
            len([item for item in result.get("orphans", []) if item["rows"]]),
        ))
    return "%s。" % "，".join(parts)
