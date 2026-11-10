"""持仓型基金的「基金持仓」页路由。

对应 PHP ``woody/res/holdingscn.php``（139 行）—— 页面本身只有 4 行有效
逻辑（``EchoAll()``），重活全在它拉的三个 ``php/ui`` 段落里：

* ``EchoHoldingsEstParagraph``（估值表 + 说明 + 净值记录）——
  与 ``/stock/<symbol>`` 第 3 分支（CK4）共用
  :func:`web.services.stock_detail_service.get_holdings_est_sections`；
* ``EchoReferenceParagraph``（参考数据表）；
* 七列持仓测算表 + ``EchoAdrhParagraph``（ADR/H 对比表）——
  本页独有，见 :mod:`web.services.holdings_service`。

## 与 PHP 的两处结构差异

1. **没有账号层**：PHP 整页藏在 ``$acct->EchoStockGroup()`` 后面
   （``HasData() || IsAdmin()``），没行情时整页只剩链接。本站点没有账号体系，
   守卫收进服务层（``get_page`` 返回 ``None``），路由照站点既有做法渲染
   "取不到数据"的提示而不是空白页；
2. **没有「分组名称」表**：PHP 的 ``EchoStockGroupParagraph()`` 会先摆一张
   分组表（生产端 ``holdingstable`` 之前那张），属于账号体系（暂缓）。

``/holdings/<symbol>`` 与 ``/stock/<symbol>`` 的第 3 分支内容重叠但不相同：
后者还有均线 / 历史价 / 净值价格比较 / 份额四段（``_mystock.php`` 的公共尾部），
前者按 ``holdingscn.php`` 只有估值 + 参考 + 测算 + ADR 四段。
"""

from __future__ import annotations

from typing import Any

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from web.bootstrap import install_paths

install_paths()

from calc.fundgroups import get_symbol_group  # noqa: E402
from core.exhaustiveholdings import (  # noqa: E402
    DEFAULT_EXHAUSTIVE_ROWS,
    HOLDINGS_NETVALUE_DIFF,
)
from web.services.admin_auth import denied_message, is_admin  # noqa: E402
from web.services.holdings_service import HoldingsService  # noqa: E402


holdings_bp = Blueprint("holdings", __name__)


def _get_reference_service():
    """参考数据服务；它的 ``source`` 是持仓页查名字/配对表要用到的。"""
    factory = current_app.extensions.get("reference_service_factory")
    return None if factory is None else factory()


def _get_fundest_service():
    """估值表服务；与 ``/stock`` / ``/fund`` 共用同一个工厂。"""
    factory = current_app.extensions.get("fundest_service_factory")
    return None if factory is None else factory()


def _get_history_service():
    """净值记录段落服务（``EchoNetValueHistoryParagraph``）。"""
    factory = current_app.extensions.get("history_service_factory")
    return None if factory is None else factory()


def _source():
    """写入口取数据源：``holdings_write_source_factory`` → ``admin_source_factory``
    → 请求级库连接。

    三级回落是为了两条路都能注入：管理页的测试注 ``admin_source_factory``，
    持仓写入口的测试注 ``holdings_write_source_factory``；都没有才连库。
    """
    for key in ("holdings_write_source_factory", "admin_source_factory"):
        factory = current_app.extensions.get(key)
        if factory is not None:
            return factory()
    from core.stockdata import MySqlFundDataSource
    from web.db import get_db

    return MySqlFundDataSource(get_db())


def _process_admin_holdings(source, symbol, params, *, admin):
    """薄包一层 ``updater.holdingsupdater.process_admin_holdings``。

    包一层的唯一理由是**可替换**：路由测试要验"非管理员一个字节都不写"，
    真实现会连库。测试 monkeypatch 本函数即可。
    """
    from updater.holdingsupdater import process_admin_holdings

    return process_admin_holdings(source, symbol, params, admin=admin)


def _get_service() -> HoldingsService:
    """持仓页服务；注入了就用注入的（测试里整个顶掉）。

    默认实现从**现有**工厂里取 ``source`` / 估值表 / 净值记录三个依赖 ——
    不新增 app 级的工厂注册，与 ``routes/stock.py::_get_detail_service``
    的路子一致（那边也是先查扩展、没有就现造）。
    """
    factory = current_app.extensions.get("holdings_service_factory")
    if factory is not None:
        return factory()
    return HoldingsService(
        getattr(_get_reference_service(), "source", None),
        fundest_service=_get_fundest_service(),
        history_service=_get_history_service(),
    )


def _detail_link(symbol: str) -> str:
    """测算表首格的链接：按符号类型分流到 ``/fund`` 或 ``/stock``。

    与 ``routes/stock.py::_link_builders`` 的 ``detail_link`` 同一判据 ——
    成分股里也可能有 QDII 基金（持仓表允许基金持仓基金），直接一律
    ``/stock`` 会把基金导到一个没有这一行的分组页。
    """
    if get_symbol_group(symbol) is None:
        return url_for("stock.stock_detail_page", symbol=symbol)
    return url_for("fund.fund_page", symbol=symbol)


def _note_links(symbol: str) -> dict:
    """估值表说明文字的三个链接（``get_holdings_est_sections`` 的 ``links``）。

    对应 PHP 那句里的三个站内链接：仓位估算页 / 基金持仓页（本页自己，
    PHP 那边同样是自链接）/ 穷举持仓比例页。三个**全都接上了**（2026-10-26：
    穷举页路由落地后补的第三个）—— 说明文字与生产端逐字对齐,不再有
    "退化成纯文字"的那一段。
    """
    return {
        "position": url_for("history.history_page", kind="fundposition", symbol=symbol),
        "holdings": url_for("holdings.holdings_page", symbol=symbol),
        "exhaustive": url_for("holdings.exhaustive_holdings_page", symbol=symbol),
    }


@holdings_bp.route("/holdings/<symbol>")
def holdings_page(symbol: str):
    """渲染「基金持仓」页（``holdingscn.php``）。

    **没有内容时仍返回 200**（服务层返回 ``None``），渲染"取不到数据"提示：
    旧链接里的符号可能只是暂时没有行情或持仓明细，直接 404 会让历史外链
    全部失效（与 ``/stock/<symbol>`` 同一条约定）。
    """
    page = _get_service().get_page(
        symbol,
        link_builder=_detail_link,
        note_links=_note_links(symbol),
    )
    return render_template("holdings.html", page=page, symbol=symbol)


# ---------------------------------------------------------------------------
# 穷举持仓页（woody/res/exhaustiveholdingscn.php）
# ---------------------------------------------------------------------------
def _get_exhaustive_service():
    """穷举页服务；注入了就用注入的（测试里整个顶掉）。

    与 ``_get_service`` 同一套约定：默认实现从**现有**工厂取 ``source``,
    不新增 app 级工厂注册。

    管理员写入控件的 HTML 构造器**在这里注入**（``update_form_builder``）——
    服务层不认识 ``url_for``，`<form action>` 只能在这一层拼。
    非管理员时也照传，是否真的用得上由服务层按 ``admin`` 判定。
    """
    factory = current_app.extensions.get("exhaustive_holdings_service_factory")
    if factory is not None:
        return factory()
    from web.services.exhaustive_holdings_service import ExhaustiveHoldingsService

    return ExhaustiveHoldingsService(
        getattr(_get_reference_service(), "source", None),
        update_form_builder=_update_holdings_form,
    )


def _update_holdings_form(symbol: str, payload: str, position_text: str,
                          question: str) -> str:
    """管理员那一格的 HTML：一个带确认弹窗的 POST 表单。

    对应 PHP ``__getPosDisplay`` 里那句（``exhaustiveholdingscn.php:43``）::

        GetOnClickLink(PATH_STOCK.'submitholdings.php?symbol=…&holdings='.urlencode($str),
                       '确认更新持仓'.$str.'和仓位'.$strPos.'？', $strPos)

    ## 为什么不是链接

    PHP 那个链接点了就 GET —— 一个查询串带副作用。本站点从后台分组页起
    就定了"写操作一律 POST"（``routes/admin.py`` 的取舍 1），这里照办：
    载荷进 hidden input，``onsubmit`` 里弹确认。

    ## 为什么按钮文本是仓位数字

    PHP 的链接显示文本就是 ``$strPos``（仓位），点之前看到的就是"1.01"这个
    数字。保持同形：数字本身就是"点它能把这个数写回去"的意思，旁边不加
    "更新"字样。``title`` 放完整说明，鼠标悬停才看到。

    ## 转义

    ``question`` 里已经去掉了双引号（``GetOnClickLink`` 的口径），但它还含
    单引号风险 —— 话术是拼出来的（``确认更新持仓GLD:101.00和仓位1.01？``），
    没有用户自由输入，所以照 PHP 只做"去双引号"。载荷与符号走 Jinja 自动
    转义前先 ``markupsafe.escape``，避免符号里出现 ``"`` 时截断属性。

    表单形态与 ``admin_groups.html`` 的"删除"按钮一致（
    ``style="display:inline"`` + ``class="link-button"``），站点里只有一套
    表格内按钮的样式。
    """
    from markupsafe import escape

    return (
        '<form method="post" action="%s" style="display:inline;"'
        ' onsubmit="return confirm(\'%s\')">'
        '<input type="hidden" name="symbol" value="%s">'
        '<input type="hidden" name="holdings" value="%s">'
        '<button type="submit" class="link-button"'
        ' title="把算出来的比例 %s 和仓位 %s 写回库（对应 PHP submitholdings.php）">'
        '%s</button>'
        '</form>'
    ) % (
        url_for("holdings.submit_holdings"),
        escape(question),
        escape(symbol),
        escape(payload),
        escape(payload),
        escape(position_text),
        escape(position_text),
    )


def _resolve_threshold(raw):
    """``GetEditInput()`` 的取值语义（``exhaustiveholdingscn.php:346``）。

    返回 ``(是否渲染, 阈值百分数, 输入框回填串)``。三个分支：

    * 没有 ``?input=``（``None``）—— 用默认阈值 ``HOLDINGS_NETVALUE_DIFF``，
      输入框回填 ``1.0``；
    * ``?input=`` 是空串 —— PHP ``$strInput = ''`` 时**整段不算**，
      页面只剩输入框（``if ($strInput != '')``），所以不渲染；
    * 有值 —— 原样换算，输入框回填原文。

    **不能用 ``if (x)`` 判空**：``'0' != ''`` 在 PHP 里是真，填 0 是合法输入
    （阈值 0 = 每个有涨跌的日子都进方程）。所以 ``None`` 与 ``""`` 必须分开判。
    """
    if raw is None:
        return (True, HOLDINGS_NETVALUE_DIFF, str(HOLDINGS_NETVALUE_DIFF))
    if raw == "":
        return (False, HOLDINGS_NETVALUE_DIFF, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        #: 填了非数字：PHP 的 floatval('abc') 是 0.0 —— 阈值 0，照样算
        value = 0.0
    return (True, value, raw)


@holdings_bp.route("/holdings/exhaustive/<symbol>")
def exhaustive_holdings_page(symbol: str):
    """渲染「穷举持仓比例」页。

    **没有账号层**：PHP 是 ``SymbolAccount`` + ``$acct->EchoStockGroup()``
    （登录 + 分组可见性）。本项目的账号体系暂缓，按 admin 分组页的既定做法
    收敛成"固定 demo 身份"—— 但"是不是管理员"是真判的
    （``web/services/admin_auth.is_admin()``，比对 ``ADMIN_EMAIL``），
    只是演示环境的默认身份就是管理员，所以不用登录也能看到写入控件。

    阈值走 ``?input=``（GET）：这个参数只影响读，没有副作用，保留 GET 是
    照抄 PHP 的表单形态（写操作才一律 POST，见 ``/transactions``）。
    """
    render, threshold, threshold_input = _resolve_threshold(request.args.get("input"))
    page = None
    if render:
        page = _get_exhaustive_service().get_page(
            symbol,
            threshold=threshold,
            num=DEFAULT_EXHAUSTIVE_ROWS,
            #: PHP 传 ``$acct->IsAdmin()`` —— 决定"确认更新持仓"控件给不给
            admin=is_admin(),
        )
    return render_template(
        "exhaustive_holdings.html",
        page=page,
        symbol=symbol,
        threshold_input=threshold_input,
        default_threshold=HOLDINGS_NETVALUE_DIFF,
        skipped_by_input=not render,
        is_admin=is_admin(),
    )


# ---------------------------------------------------------------------------
# 管理员写入口（woody/res/submitholdings.php）
# ---------------------------------------------------------------------------
@holdings_bp.route("/holdings/submit", methods=["POST"])
def submit_holdings():
    """把穷举表算出来的比例写回库（PHP ``submitholdings.php``）。

    对应 PHP ``_AdminHoldingsAccount::AdminProcess()``::

        if ($strHoldings = UrlGetQueryValue('holdings'))
        {
            $ref = new HoldingsReference($strSymbol);
            if ($strDate = $ref->GetHoldingsDate())
                UpdateStockOptionHoldings($strStockId, $strDate, $strHoldings);
        }
        if ($strPosition = UrlGetQueryValue('fundposition'))
            $pos_sql->WriteVal($strStockId, $strPosition);

    ## 与 PHP 的三处差异

    1. **POST 不是 GET**（本站点写操作一律 POST）；
    2. **权限闸门真的判**：PHP 的 ``AdminRun()`` 里 ``if ($this->IsAdmin())``
       是唯一一道闸；这里把判定结果当 ``admin=`` 传给
       ``process_admin_holdings``，非管理员一个字节都不写库；
    3. 表单里没有 ``login``/``email`` 字段：PHP 用它查会员，本地只写符号的
       持仓（``fundposition`` 分支同理），与账号体系暂缓一致。
    """
    symbol = (request.form.get("symbol") or "").strip()
    if not is_admin():
        return render_template(
            "error.html", message=denied_message(), status=403), 403
    if not symbol:
        return render_template(
            "error.html", message="缺少 symbol 参数。", status=400), 400

    params: dict = {}
    if request.form.get("holdings"):
        params["holdings"] = request.form["holdings"]
    if request.form.get("fundposition"):
        params["fundposition"] = request.form["fundposition"]

    result = _process_admin_holdings(_source(), symbol, params, admin=True)
    if result.get("updated"):
        flash("%s：持仓已更新（日期取库里现有的持仓日）。" % symbol)
    elif params.get("holdings"):
        flash("%s：没有写入 —— 编码串解析失败或取不到持仓日。" % symbol)
    return redirect(url_for("holdings.exhaustive_holdings_page", symbol=symbol))


# ---------------------------------------------------------------------------
# 季报持仓写入口（woody/res/editquarterreportcn.php + submitstockoptions.php）
# ---------------------------------------------------------------------------
#: PHP ``_getStockOptionVal()`` 的 REPORT 分支在没有历史数据时给的样例值
#: （``_editstockoptionform.php:293``）。照抄这个字面量：它同时充当"格式示例"
#: —— 管理员看到就知道要写 ``"符号":百分比`` 的串。
QUARTER_REPORT_SAMPLE = '"AAA":"60","BBB":"40"'

#: ``_getStockOptionMemo()`` 的 REPORT 分支（``_editstockoptionform.php:342``）。
QUARTER_REPORT_MEMO = "清空输入删除对应日期季报数据。"


# ---------------------------------------------------------------------------
# 季报持仓索引页（本版新增，PHP 没有"列出要维护哪些基金"的页面）
# ---------------------------------------------------------------------------

def _quarter_fund_code(symbol: str) -> str:
    """库内符号 -> 页面上点得动的基金代码（``SZ161815`` -> ``161815``）。

    只剥 ``SH`` / ``SZ`` 两个交易所前缀；清单里 32 只全是 A 股场内基金，
    所以这个转换不需要考虑 ``^NDX`` / ``hf_SI`` 之类的中间层符号。
    """
    text = str(symbol).strip()
    return text[2:] if text[:2].upper() in ("SH", "SZ") and len(text) > 2 else text


def _quarter_report_targets(source) -> list:
    """索引页的行：32 只持仓型 QDII + 库里的名称 / 季报最新日期。

    ## 清单只有一个出处

    ``updater.holdingsupdater.qdiimix_holdings_symbols()`` →
    ``core.stocksymbol.qdii_mix_holdings_symbol_array()``（**32 只**）。
    这 32 个符号详情页 ``require`` 了 ``_qdiimix.php``，页面要摆持仓明细，
    所以后台必须按季报逐只维护 ``quarterreport`` 表。**别用**
    ``qdii_mix_get_symbol_array()``：那是分类页 ``qdiimixcn.php`` 的数组，
    2026-10-30 起已收窄到 15 只，混用会静默漏掉 17 只。

    ## 为什么不查 quarterreport 反推清单

    演示库里 ``quarterreport`` 一条记录都没有（2026-11-09 实测）。季报是
    "还没录"也要能看见的待办清单，不是"已经录了什么"的报表 —— 反推会
    得到空页。名称 / 最新日期只是**附加信息**，两条查询各自 try/except，
    库挂了也照常把这 32 个代码列出来（后台索引页不该因为一条 SELECT 打不开）。
    """
    from updater.holdingsupdater import qdiimix_holdings_symbols

    symbols = list(qdiimix_holdings_symbols())
    ids, names, reports = {}, {}, {}
    connection = getattr(source, "connection", None)
    if connection is not None and symbols:
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT id, symbol, name FROM stock WHERE symbol IN (%s)"
                % ",".join(["%s"] * len(symbols)), tuple(symbols))
            for record in cursor.fetchall():
                symbol = str(record["symbol"])
                ids[symbol] = str(record["id"])
                names[symbol] = str(record.get("name") or "")
            cursor.execute(
                "SELECT stock_id, MAX(date) AS latest, COUNT(*) AS cnt "
                "FROM quarterreport GROUP BY stock_id")
            for record in cursor.fetchall():
                reports[str(record["stock_id"])] = (
                    str(record.get("latest") or ""), int(record.get("cnt") or 0))
        except Exception:
            # 表不存在 / 连接断了 —— 名称与日期留空，代码清单照给
            pass
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    rows = []
    for symbol in symbols:
        date_text, count = reports.get(ids.get(symbol, ""), ("", 0))
        rows.append({
            "symbol": symbol,
            "code": _quarter_fund_code(symbol),
            "name": names.get(symbol, ""),
            "in_stock": symbol in ids,
            "latest": date_text,
            "rows": count,
        })
    return rows


@holdings_bp.route("/holdings/quarter")
def quarter_report_index():
    """「修改季报持仓」的入口：列出要按季报维护的基金代码，点代码进编辑页。

    PHP 没有这一层：``editquarterreportcn.php`` 是**通用股票选项表单**的一张皮，
    必须先知道 ``?symbol=`` 才能打开，于是"哪些基金该录季报"只能靠人记。
    本版补一张索引，从后台控制台直接进；每行的代码链到既有的
    ``/holdings/quarter/<symbol>``，写入口只有那一处（不重复实现）。

    这一页**不判权限**：只是导航 + 只读概览，真正的闸门在编辑页的 POST。
    """
    try:
        source = _source()
    except Exception:
        source = None
    rows = _quarter_report_targets(source)
    for row in rows:
        row["href"] = url_for("holdings.quarter_report_page", symbol=row["symbol"])
    return render_template(
        "quarter_report_index.html",
        rows=rows,
        total=len(rows),
        admin=is_admin(),
        admin_href=url_for("admin.admin_index"),
    )


def _quarter_report_context(symbol: str) -> dict:
    """季报编辑页要的取数：当前最新日期 / 该日期的值 / 错误说明。

    对应 ``_getStockOptionDate()`` + ``_getStockOptionVal()`` 的 REPORT 分支
    （``_editstockoptionform.php:44-47`` / ``:290-293``）::

        $quarter_sql = new QuarterReportSql();
        if ($strDate = $quarter_sql->GetDateNow($strStockId))  return $strDate;
        return $strYMD;                                    // 今天

        if ($strClose = $quarter_sql->GetCloseNow($strStockId))  return $strClose;
        return '"AAA":"60","BBB":"40"';

    取不到符号时 `stock_id` 为 `None`，两个默认值仍按 PHP 的口径给
    （日期 = 今天、值 = 样例），页面能照常显示，只是写不进去 —— 与 PHP
    遇到不存在的符号时表现一致（那边 `SqlGetStockId` 为假，
    ``WriteDaily`` 会写进 ``stock_id = 0``；本版宁可不写，见路由的注释）。
    """
    from sql.historytable import QuarterReportSql
    from sql.stocksql import StockSql

    source = _source()
    stock_sql = StockSql(getattr(source, "connection", None))
    stock_id = None
    try:
        stock_id = stock_sql.get_id(symbol)
    except Exception:
        stock_id = None

    date_text = ""
    value_text = ""
    quarter_sql = QuarterReportSql(getattr(source, "connection", None))
    if stock_id:
        try:
            date_text = quarter_sql.get_date_now(stock_id) or ""
            value_text = quarter_sql.get_close_now(stock_id) or ""
        except Exception:
            date_text, value_text = "", ""
    return {
        "symbol": symbol,
        "stock_id": stock_id,
        "date": date_text or today_ymd(),
        "value": value_text or QUARTER_REPORT_SAMPLE,
        "memo": QUARTER_REPORT_MEMO,
    }


def today_ymd() -> str:
    """``GetNowYMD()->GetYMD()``：今天（``Y-m-d``，服务器本地时区）。"""
    import datetime

    return datetime.date.today().strftime("%Y-%m-%d")


def _php_empty(text: Any) -> bool:
    """PHP ``empty($strVal)`` 的字符串口径。

    ``''`` 与 ``'0'`` **都算空** —— 这是 PHP 里最容易踩的一条：管理员想
    把某天的季报改成 0（``'0'``）时，PHP 那边是**删除那一行**，不是写 0。
    照抄，别"修正"成"只有空串才删"。
    """
    if text is None:
        return True
    text = str(text)
    return text == "" or text == "0"


@holdings_bp.route("/holdings/quarter/<symbol>", methods=["GET", "POST"])
def quarter_report_page(symbol: str):
    """季报持仓编辑页（PHP ``editquarterreportcn.php`` → ``_editstockoption.php``）。

    PHP 那个页面是**通用股票选项表单**的一张皮：``GetStockOptionArray()``
    里十几个选项（分红 / 拆分 / EMA / 净值 / 校准 / 季报 …）共用同一个
    ``_editstockoptionform.php`` 与 ``submitstockoptions.php``，靠
    ``$_POST['submit']`` 的值分派。本版只迁 **REPORT（季报）** 这一支 ——
    其余选项各自还需要一整条写链路（拆分要改复权价、分红要重算校准…），
    不在这块范围里。

    于是形态也跟着收敛成"专属表单"：日期 + 值两个框，比通用表单少
    login/symbol 两个只读框（本地不写会员）。

    ## 写入语义（``_updateOptionDailySql``，``_submitstockoptions.php:208``）

    * 值 **empty**（含 ``'0'``）→ ``DeleteByDate(stock_id, date)``；
    * 否则 → ``WriteDaily``（值没变就不写，见
      :meth:`sql.historytable.DailyStringSql.write_daily`）。

    ## 权限

    与其它写入口一致：``is_admin()`` 为假直接 403，表单只读展示。
    """
    admin = is_admin()
    if request.method == "POST":
        if not admin:
            return render_template(
                "error.html", message=denied_message(), status=403), 403
        return _submit_quarter_report(symbol)

    context = _quarter_report_context(symbol)
    return render_template(
        "quarter_report.html",
        admin=admin,
        error="",
        **context,
    )


def _submit_quarter_report(symbol: str):
    """POST 分支：写或删一条季报记录，然后回到本页（POST-Redirect-GET）。"""
    from sql.historytable import QuarterReportSql
    from sql.stocksql import StockSql

    date = (request.form.get("date") or "").strip()
    value = request.form.get("value")
    value = "" if value is None else str(value)

    source = _source()
    stock_sql = StockSql(getattr(source, "connection", None))
    stock_id = None
    try:
        stock_id = stock_sql.get_id(symbol)
    except Exception:
        stock_id = None

    def _back(error: str, status: int = 200):
        context = _quarter_report_context(symbol)
        context.update({"date": date or context["date"], "value": value})
        return render_template(
            "quarter_report.html", admin=is_admin(), error=error, **context), status

    if not stock_id:
        #: PHP 那边 `SqlGetStockId` 取不到会走 `stock_id = 0`（写进脏行）；
        #: 本版拒绝 —— 季报本来就不该出现在一个库里没有的符号上。
        return _back("stock 表里没有符号 %s，没有写入。" % symbol, 400)
    if not date:
        #: PHP ``InsertDaily`` 的第一道闸：`if ($strDate == '') return false;`
        return _back("日期不能为空。", 400)

    quarter_sql = QuarterReportSql(getattr(source, "connection", None))
    if _php_empty(value):
        deleted = quarter_sql.delete_by_date(stock_id, date)
        flash("%s %s：已删除季报记录（%d 行）。" % (symbol, date, deleted))
    else:
        written = quarter_sql.write_daily(stock_id, date, value)
        if written:
            flash("%s %s：季报已写入。" % (symbol, date))
        else:
            flash("%s %s：值没有变化，未写入。" % (symbol, date))
    return redirect(url_for("holdings.quarter_report_page", symbol=symbol))
