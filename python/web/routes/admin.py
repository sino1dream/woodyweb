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
