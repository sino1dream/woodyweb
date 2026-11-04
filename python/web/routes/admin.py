"""后台管理：分组（``stockgroup`` / ``stockgroupitem``）增删改查。

对应 PHP 两个页面 + 一个提交端点：

* ``woody/res/editstockgroupcn.php`` → ``php/_editstockgroup.php`` +
  ``_editgroupform.php``：新建（``?new=<symbol>``）与编辑
  （``?edit=<groupid>``）共用一个表单（组名 + 成员符号多行文本）；
* ``woody/res/submitgroup.php`` → ``_submitgroup.php`` 的
  ``_SubmitGroupAccount::Process()``：按 ``$_POST['submit']`` 的值分派
  新建 / 编辑，按 ``?delete=`` 删分组。

## 与 PHP 的差异（README §六 有完整清单）

1. **删除走 POST**（``/admin/group/<id>/delete``）。PHP 是
   ``?delete=<id>`` 的 GET 副作用；我们不在 GET 上做写操作。
2. **不接登录态**：PHP 未登录时 ``Process`` 不执行；演示环境固定
   ``DEMO_MEMBER_ID``（账号体系暂缓，页面有常驻说明）。
3. **符号解析不建 ref**：PHP 的 ``_getStockIdArray`` 对库里没有的符号
   会 ``StockGetReference`` 顺带入库；我们只认 ``stock`` 表里已有的符号，
   跳过的符号在结果里报数（不静默）。
4. **删成员/删分组会级联删交易**（2026-10-24 反填）：PHP 的
   ``SqlUpdateStockGroup`` 移除分支与 ``SqlDeleteStockGroupItemByGroupId``
   都是先 ``trans_sql->Delete`` 再删 item 行；本地把后者合成一条
   ``groupitem_id IN (...)``，语义相同（见
   ``README_stocktransaction_migration.md`` 第三节）。
"""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from web.bootstrap import install_paths

install_paths()

import deletesymbol  # noqa: E402  ``auto/deletesymbol.py``：删标的口径全在那里
from core.stockdata import MySqlFundDataSource  # noqa: E402
from core.stockgroupsql import (  # noqa: E402
    DEMO_MEMBER_ID,
    StockGroupItemSql,
    StockGroupSql,
    get_input_stock_id_array,
    is_group_name_reserved,
    prefix_reserved_group_name,
    sql_delete_stock_group,
    sql_get_stocks_array,
    sql_update_stock_group,
)
from web.db import get_db  # noqa: E402

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

#: 新建/编辑/删除三个提交值，照 PHP 的 ``DISP_NEW_CN`` / ``DISP_EDIT_CN``。
SUBMIT_NEW = "新建"
SUBMIT_EDIT = "编辑"
SUBMIT_DELETE = "删除"


def _source() -> MySqlFundDataSource:
    """取数据源：优先测试注入的工厂，缺省用请求级库连接。

    工厂注入的形态照 ``create_app`` 的既有模式（``fund_service_factory``
    那批）——测试传 ``admin_source_factory=lambda: FakeSource()``。
    """
    factory = current_app.extensions.get("admin_source_factory")
    if factory is not None:
        return factory()
    return MySqlFundDataSource(get_db())


def _group_rows(source: MySqlFundDataSource) -> list:
    """列表页的行数据：分组 + 成员符号 + 成员数。"""
    group_sql = StockGroupSql(source)
    rows = []
    for record in group_sql.get_all(DEMO_MEMBER_ID):
        group_id = record["id"]
        symbols = sql_get_stocks_array(source, group_id)
        rows.append({
            "id": group_id,
            "name": record["groupname"],
            "symbols": symbols,
            "count": len(symbols),
        })
    return rows


@admin_bp.route("")
@admin_bp.route("/")
def admin_index():
    """``/admin`` 与 ``/admin/`` 直接跳到分组列表。

    PHP 没有"/admin"这个入口（后台是 ``editstockgroupcn.php`` 一张页面），
    本地把 ``/admin/groups`` 当入口用。但页头导航的「后台」和页脚的
    「后台管理」都指这里，人手敲 ``/admin`` 才是最自然的猜法 ——
    给它 404 不合理，跳过去就完了（2026-10-26 加，起因是用户实测
    ``http://127.0.0.1:5000/admin`` 报 Not Found）。
    """
    return redirect(url_for("admin.groups"))


@admin_bp.route("/groups")
def groups():
    """分组列表（PHP 没有独立列表页——那边列表就是 mystockgroupcn 的
    账号段落；这里给后台一个入口，形态照它）。"""
    source = _source()
    return render_template(
        "admin_groups.html",
        rows=_group_rows(source),
        member_id=DEMO_MEMBER_ID,
    )


@admin_bp.route("/group/new")
def group_new():
    """新建表单。``?symbol=QQQ`` 时预填（照 PHP 的 ``?new=`` 分支：
    组名 ``@符号``、成员就那个符号）。"""
    symbol = (request.args.get("symbol") or "").strip()
    group_name = ("@" + symbol) if symbol else ""
    return render_template(
        "admin_group_form.html",
        mode="new",
        group_id=None,
        group_name=group_name,
        symbols=symbol,
        name_disabled=False,
    )


@admin_bp.route("/group/<int:group_id>")
def group_edit(group_id: int):
    """编辑表单：回显组名与成员符号（逗号分隔，照 PHP 的
    ``_getStocksString``）。"""
    source = _source()
    group_sql = StockGroupSql(source)
    record = group_sql.get_record_by_id(group_id)
    if record is None:
        flash("分组 %d 不存在" % group_id)
        return redirect(url_for("admin.groups"))
    symbols = sql_get_stocks_array(source, group_id)
    return render_template(
        "admin_group_form.html",
        mode="edit",
        group_id=group_id,
        group_name=record["groupname"],
        symbols=", ".join(symbols),
        # PHP：组名与股票符号重名的分组禁止改名（_editgroupform.php:29
        # 的 $strGroupNameDisabled）
        name_disabled=is_group_name_reserved(record["groupname"], _all_symbols(source)),
    )


@admin_bp.route("/group/submit", methods=["POST"])
def group_submit():
    """统一提交端点，照 ``_SubmitGroupAccount::Process()`` 的分派。"""
    source = _source()
    submit = request.form.get("submit") or ""
    group_name = (request.form.get("groupname") or "").strip()
    symbols_text = request.form.get("symbols") or ""

    if submit == SUBMIT_NEW:
        return _on_new(source, group_name, symbols_text)
    if submit == SUBMIT_EDIT:
        return _on_edit(source, group_name, symbols_text)
    flash("未知提交：%s" % submit)
    return redirect(url_for("admin.groups"))


def _on_new(source, group_name, symbols_text):
    """照 ``_SubmitGroupAccount::_onNew()``。"""
    if not group_name:
        # PHP：空组名用 '@'.md5(rand()) 占位；我们用固定占位串
        group_name = "@未命名"
    # 重名保护：组名撞上股票符号时加 '@'（PHP _submitgroup.php:72）
    if is_group_name_reserved(group_name, _all_symbols(source)):
        group_name = prefix_reserved_group_name(group_name)

    group_sql = StockGroupSql(source)
    group_id = group_sql.insert_string(DEMO_MEMBER_ID, group_name)
    if group_id is None:
        flash("分组「%s」已存在" % group_name)
        return redirect(url_for("admin.groups"))

    stock_ids, skipped = _resolve_stock_ids(source, symbols_text)
    item_sql = StockGroupItemSql(source, group_id)
    for stock_id in stock_ids:
        item_sql.insert(stock_id)
    flash("已新建分组「%s」（%d 个成员%s）"
          % (group_name, len(stock_ids), _skipped_note(skipped)))
    return redirect(url_for("admin.group_edit", group_id=group_id))


def _on_edit(source, group_name, symbols_text):
    """照 ``_SubmitGroupAccount::_onEdit()``。"""
    group_id = request.form.get("group_id")
    if not group_id:
        flash("编辑请求缺少 group_id")
        return redirect(url_for("admin.groups"))
    group_id = int(group_id)

    group_sql = StockGroupSql(source)
    record = group_sql.get_record_by_id(group_id)
    if record is None:
        flash("分组 %d 不存在" % group_id)
        return redirect(url_for("admin.groups"))

    # 不修改有单独页面的分组名称（PHP _submitgroup.php:59-60）：
    # 旧名与符号重名时丢弃提交的组名、保留旧名
    old_name = record["groupname"]
    if is_group_name_reserved(old_name, _all_symbols(source)):
        new_name = old_name
    else:
        new_name = group_name or old_name
        # 改名也不能撞符号（PHP 只管旧名，我们顺手防新名——记为有意偏差）
        if is_group_name_reserved(new_name, _all_symbols(source)):
            new_name = prefix_reserved_group_name(new_name)
    group_sql.update_string(group_id, new_name)

    stock_ids, skipped = _resolve_stock_ids(source, symbols_text)
    stats = sql_update_stock_group(source, group_id, stock_ids)
    flash("已保存分组「%s」（新增 %d、移除 %d、当前 %d 个成员%s）"
          % (new_name, len(stats["added"]), len(stats["removed"]),
             len(stock_ids), _skipped_note(skipped)))
    return redirect(url_for("admin.group_edit", group_id=group_id))


@admin_bp.route("/group/<int:group_id>/delete", methods=["POST"])
def group_delete(group_id: int):
    """删分组：先删成员再删组（MyISAM 无级联，照 PHP ``_onDelete``）。"""
    source = _source()
    stats = sql_delete_stock_group(source, group_id)
    if stats["group"]:
        flash("已删除分组 %d（含 %d 个成员）" % (group_id, stats["items"]))
    else:
        flash("分组 %d 不存在" % group_id)
    return redirect(url_for("admin.groups"))


# ---------------------------------------------------------------------------
# 符号管理（``stock`` 表增删改）
#
# 与分组管理并列的第二块后台能力。PHP 那边**没有**这个页面：符号是
# ``/woody/res/editstock.php`` 的「修改」（改名）与 ``deletesymbol.php``
# （删）两个散装入口，而"新增"只能靠访问某个页面时 ``StockGetReference``
# 顺手入库（``stock.php`` 的副作用）。本地没有那个副作用，所以缺一个
# 显式的增删入口 —— 这就是这一块存在的理由（2026-11-04，起因是
# ``/category/chinafuture`` 六只基金全部显示「库里无此符号」）。
# ---------------------------------------------------------------------------

#: 列表页最多显示多少行。演示库 300 行上下，一页足够；真的变多时用 ``?q=`` 收窄。
SYMBOL_LIST_LIMIT = 300

#: 列表页要显示行数的表：``(别名, 表名, 匹配列, 显示名)``。
#:
#: 匹配列写 ``symbol`` 的表按**符号字符串**存（``realtimequote`` / ``estquote``），
#: 其余按 ``stock.id`` 存。这与 ``auto/deletesymbol.py`` 的 ``ORPHAN_TABLES``
#: 是同一份知识，但这里只需要"看得见的几张大表"，不照搬全套。
SYMBOL_COUNT_TABLES = (
    ("n_history", "stockhistory", "stock_id", "历史价格"),
    ("n_netvalue", "netvaluehistory", "stock_id", "单位净值"),
    ("n_calibration", "calibrationhistory", "stock_id", "校准记录"),
    ("n_quote", "realtimequote", "symbol", "实时快照"),
    ("n_est", "estquote", "symbol", "EST快照"),
)


def _count_subquery(alias: str, table: str, column: str) -> str:
    """拼一个相关子查询：``(SELECT COUNT(*) FROM t WHERE t.col = s.<x>)``。

    为什么用子查询而不是 ``LEFT JOIN + GROUP BY``：``realtimequote`` /
    ``estquote`` 是按符号字符串关联的，混在同一个 ``JOIN`` 里既难读也容易
    把行数算错（一符号一行，多表 join 不会重复，但改成按 ``stock_id``
    关联时就会踩到）。子查询慢一点，后台页不在乎。
    """
    left = "s.symbol" if column == "symbol" else "s.id"
    return "(SELECT COUNT(*) FROM `%s` t WHERE t.`%s` = %s)" % (table, column, left)


def _symbol_rows(source: MySqlFundDataSource, query: str = "") -> list:
    """列表页的行：符号、名称，以及四张主要表的行数。

    ``query`` 非空时按符号 / 名称模糊匹配（``LIKE`` 两边都加 ``%``）。
    """
    columns = ", ".join(
        "%s AS %s" % (_count_subquery(alias, table, column), alias)
        for alias, table, column, _label in SYMBOL_COUNT_TABLES
    )
    sql = "SELECT s.id, s.symbol, s.name, %s FROM stock s" % columns
    params: tuple = ()
    if query:
        sql += " WHERE s.symbol LIKE %s OR s.name LIKE %s"
        params = ("%" + query + "%", "%" + query + "%")
    sql += " ORDER BY s.symbol LIMIT %d" % SYMBOL_LIST_LIMIT

    cursor = source.connection.cursor()
    try:
        cursor.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        _close(cursor)

    labels = [(alias, label) for alias, _table, _column, label in SYMBOL_COUNT_TABLES]
    for row in rows:
        row["counts"] = [
            {"label": label, "rows": int(row.get(alias) or 0)}
            for alias, label in labels
            if int(row.get(alias) or 0)
        ]
        row["total"] = sum(item["rows"] for item in row["counts"])
    return rows


def _close(cursor) -> None:
    close = getattr(cursor, "close", None)
    if close:
        close()


def _normalize_symbol(text: str) -> str:
    """符号归一：去掉 ``_`` 之外的空白，没有下划线就大写（``StockGetSymbol()``）。

    与 ``build_qdii_reference`` 里那段规范化同源。**``nf_AU0`` 必须原样保留大小写**
    —— 新浪对 ``nf_au0`` 返回空串，而 ``stock`` 的排序规则不区分大小写，
    查重查不出来，只有行情会静默失踪。
    """
    symbol = (text or "").strip()
    if not symbol:
        return ""
    if "_" not in symbol:
        symbol = symbol.upper()
    return symbol


@admin_bp.route("/symbols")
def symbols():
    """符号列表（``stock`` 表）。"""
    source = _source()
    query = (request.args.get("q") or "").strip()
    return render_template(
        "admin_symbols.html",
        rows=_symbol_rows(source, query),
        query=query,
        limit=SYMBOL_LIST_LIMIT,
    )


@admin_bp.route("/symbol/new")
def symbol_new():
    """新增符号表单（符号 + 名称）。"""
    return render_template(
        "admin_symbol_form.html",
        mode="new",
        symbol=_normalize_symbol(request.args.get("symbol") or ""),
        name=(request.args.get("name") or "").strip(),
        detail=None,
    )


@admin_bp.route("/symbol/<symbol>")
def symbol_detail(symbol: str):
    """编辑表单 + **关联数据清单**（删之前先看清楚要连带删什么）。"""
    source = _source()
    plan = _symbol_plan(source, symbol)
    if not plan["found"]:
        flash("库里没有符号 %s" % plan["symbol"])
        return redirect(url_for("admin.symbols"))
    return render_template(
        "admin_symbol_form.html",
        mode="edit",
        symbol=plan["symbol"],
        name=_symbol_name(source, plan["stock_id"]),
        detail=plan,
    )


@admin_bp.route("/symbol/submit", methods=["POST"])
def symbol_submit():
    """新增 / 改名提交。"""
    source = _source()
    mode = request.form.get("mode") or "new"
    symbol = _normalize_symbol(request.form.get("symbol"))
    name = (request.form.get("name") or "").strip()

    if not symbol:
        flash("符号不能为空")
        return redirect(url_for("admin.symbols"))

    if mode == "edit":
        return _rename_symbol(source, symbol, name)
    return _create_symbol(source, symbol, name)


def _create_symbol(source: MySqlFundDataSource, symbol: str, name: str):
    """插一行 ``stock``。**只写这张表** —— 行情 / 净值 / 校准值都不造。"""
    if source.get_stock_id(symbol) is not None:
        flash("符号 %s 已在库里" % symbol)
        return redirect(url_for("admin.symbol_detail", symbol=symbol))
    cursor = source.connection.cursor()
    try:
        cursor.execute("INSERT INTO stock (symbol, name) VALUES (%s, %s)",
                       (symbol, name or symbol))
        source.connection.commit()
    except Exception as exc:  # 唯一键冲突、连接断了等等
        try:
            source.connection.rollback()
        except Exception:
            pass
        flash("新增 %s 失败：%s: %s" % (symbol, type(exc).__name__, exc))
        return redirect(url_for("admin.symbol_new", symbol=symbol, name=name))
    finally:
        _close(cursor)

    flash("已新增符号 %s。行情 / 净值 / 校准值都还没有 —— "
          "参考数据会显示「无行情快照」，估值表要等这些数据到货后才出现。" % symbol)
    return redirect(url_for("admin.symbol_detail", symbol=symbol))


def _rename_symbol(source: MySqlFundDataSource, symbol: str, name: str):
    """改显示名。**不改符号本身** —— 符号是六张表的外键锚点，改了等于换了标的。"""
    stock_id = source.get_stock_id(symbol)
    if stock_id is None:
        flash("库里没有符号 %s" % symbol)
        return redirect(url_for("admin.symbols"))
    if not name:
        flash("名称不能为空（要清空就写符号本身）")
        return redirect(url_for("admin.symbol_detail", symbol=symbol))

    cursor = source.connection.cursor()
    try:
        cursor.execute("UPDATE stock SET name = %s WHERE id = %s", (name, stock_id))
        source.connection.commit()
    finally:
        _close(cursor)
    flash("已把 %s 的名称改为「%s」" % (symbol, name))
    return redirect(url_for("admin.symbol_detail", symbol=symbol))


@admin_bp.route("/symbol/<symbol>/delete", methods=["POST"])
def symbol_delete(symbol: str):
    """删符号。闸门与清表清单全部复用 ``auto.deletesymbol``。"""
    source = _source()
    include_orphans = bool(request.form.get("include_orphans"))
    result = deletesymbol.apply(source.connection, symbol, include_orphans=include_orphans)
    flash(deletesymbol.describe(result))
    if result.get("blocked"):
        return redirect(url_for("admin.symbol_detail", symbol=result["symbol"]))
    return redirect(url_for("admin.symbols"))


def _symbol_plan(source: MySqlFundDataSource, symbol: str) -> dict:
    """``deletesymbol.plan()`` 的包装：把 ``connection`` 换成数据源里的那条。"""
    return deletesymbol.plan(source.connection, symbol)


def _symbol_name(source: MySqlFundDataSource, stock_id) -> str:
    cursor = source.connection.cursor()
    try:
        cursor.execute("SELECT name FROM stock WHERE id = %s", (stock_id,))
        row = cursor.fetchone()
        return str(row["name"]) if row else ""
    finally:
        _close(cursor)


# ---------------------------------------------------------------------------
# 内部助手
# ---------------------------------------------------------------------------

def _all_symbols(source: MySqlFundDataSource) -> list:
    """``GetAllSymbolArray()`` 的替身：库里全部符号。

    PHP 的 ``in_arrayAll`` 查的是**全站符号表**；这里查 ``stock`` 表。
    每次请求全捞一遍在演示库规模（几百行）可接受，不做缓存 ——
    后台页不是热路径。
    """
    cursor = source.connection.cursor()
    try:
        cursor.execute("SELECT symbol FROM stock")
        return [str(row["symbol"]) for row in cursor.fetchall()]
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()


def _resolve_stock_ids(source: MySqlFundDataSource, symbols_text: str):
    """符号文本 → (stock_id 列表, 跳过的符号列表)。

    跳过的符号要报出来：PHP 对查不到的符号会尝试建引用对象入库，
    我们不写库，跳过即失败——不提示的话用户以为加成功了。
    """
    if not symbols_text.strip():
        return [], []
    # 直接数"文本里的符号"与"解析出的 id"的差：get_input_stock_id_array
    # 已做去重与大小写归一，重新切一遍拿期望集合
    expected = _split_symbols(symbols_text)
    stock_ids = get_input_stock_id_array(source, symbols_text)
    skipped = [sym for sym in expected if _lookup_id(source, sym) is None]
    return stock_ids, skipped


def _split_symbols(symbols_text: str) -> list:
    """与 ``get_input_stock_id_array`` 同规则的切分 + 归一（只为报数）。"""
    result = []
    seen = set()
    text = symbols_text
    for sep in ",;\t\r\n ":
        text = text.replace(sep, " ")
    for token in text.split(" "):
        symbol = token.strip()
        if not symbol:
            continue
        if "_" not in symbol:
            symbol = symbol.upper()
        if symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
    return result


def _lookup_id(source: MySqlFundDataSource, symbol: str):
    return source.get_stock_id(symbol)


def _skipped_note(skipped: list) -> str:
    if not skipped:
        return ""
    return "；跳过 %d 个 stock 表没有的符号：%s" % (len(skipped), ", ".join(skipped))
