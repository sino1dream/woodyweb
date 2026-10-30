"""三个"抓外部数据写库"的后台工具（``woody/res/submit*.php`` 一族）。

## 为什么它们共用一个服务

三个 PHP 页面形状完全一致 —— 都是 ``SymbolAccount`` 的子类，只实现
``AdminProcess()``，跑完 ``SwitchToSess()`` 跳回去，**页面上什么都看不到**：

===================  ==========================  ================================
PHP 页面              做什么                       写哪张表
===================  ==========================  ================================
``submitdividend``    SPDR / Proshares 分红 JSON   ``stockdividend``
``submitnetvalue``    Invesco JSON / SPDR xlsx     ``netvaluehistory``（+``shareshistory``）
``submithistory``     Yahoo chart（本版换新浪）    ``stockhistory``（+``stockhistorydate``）
===================  ==========================  ================================

而且这三个页面在生产端**几乎进不去**：``submitdividend`` / ``submitnetvalue``
的管理员链接分别挂在 ``etfdividendcn.php`` 与 ``netvalueclosecn.php`` 上（两页
都还没迁），``submithistory`` 的链接函数 ``GetUpdateStockHistoryLink()``
在整个 PHP 代码库里**没有任何调用点** —— 只能手敲 URL。所以本地做成一个
工具页（``/submit``）+ 三个 POST 端点，而不是三张空页面。

## 与 PHP 的差异（清单在 ``README_submit_pages_migration.md``）

1. **POST 不是 GET**：PHP 用 ``GetOnClickLink()`` 拼一个带 ``confirm()`` 的
   ``<a href>``，点下去就是 GET 副作用。本项目的写操作一律 POST
   （``routes/admin.py`` 的取舍 1、``routes/holdings.py`` 同理）。
2. **有反馈**：PHP 把结果写进 ``debug.txt``，页面上看不到。这里把
   "写了多少行 / 为什么没写"flash 出来。
3. **闸门真的判**：``is_admin()``（``web/services/admin_auth.py``），非管理员
   一个字节都不写库。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from updater import etfdividend, etfnav, historyupdate

#: 三个工具的注册表。``php`` 是源文件名，页面上一并显示（便于对着 PHP 查）。
#: ``label`` 取的常量见备注 —— 手抄容易少个「数据」两字。
TOOLS = {
    "dividend": {
        "key": "dividend",
        "php": "submitdividend.php",
        "title": "更新分红数据",
        #: ``ETF_DIVIDEND_DISPLAY`` = ``STOCK_OPTION_DIVIDEND.'数据'`` = 「分红数据」
        "label": "分红数据",
        "sources": "SPDR（SPY / XBI / XLE / XLY / XOP）或 Proshares（其余美股 ETF）",
        "writes": "stockdividend",
        "note": "生产端挂在 etfdividendcn.php 的管理员链接上。",
    },
    "netvalue": {
        "key": "netvalue",
        "php": "submitnetvalue.php",
        "title": "更新单位净值",
        #: ``NETVALUE_HISTORY_DISPLAY`` = 「净值记录」
        "label": "净值记录",
        "sources": "Invesco（只认 QQQ）或 SPDR 的 navhist xlsx（另五只）",
        "writes": "netvaluehistory + shareshistory",
        "note": "生产端挂在 netvalueclosecn.php 的管理员链接上。",
    },
    "history": {
        "key": "history",
        "php": "submithistory.php",
        "title": "更新历史价格",
        #: ``STOCK_HISTORY_DISPLAY`` = 「历史价格」
        "label": "历史价格",
        "sources": "PHP 用 Yahoo chart；本版换新浪美股日线（Yahoo 已 403）",
        "writes": "stockhistory + stockhistorydate",
        "note": "PHP 侧 GetUpdateStockHistoryLink() 没有调用点，只能手敲 URL。",
    },
}

#: 页面上的展示顺序（与 §模块 docstring 的表格一致）。
TOOL_ORDER = ("dividend", "netvalue", "history")


def get_tool(name: str) -> Optional[Dict[str, Any]]:
    """按名字取工具定义，未知名字返回 ``None``（路由据此 404）。"""
    return TOOLS.get(str(name).strip())


def tool_list() -> list:
    """三个工具的定义，按 :data:`TOOL_ORDER` 排。"""
    return [TOOLS[name] for name in TOOL_ORDER]


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def run_tool(
    name: str,
    source: Any,
    symbol: str,
    *,
    quote_date: Optional[str] = None,
) -> Dict[str, Any]:
    """跑一个工具，返回 ``{"ok", "message", "result"}``。

    ``quote_date`` 只给 ``history`` 用（水位线要拿它比），不传就现取参考对象
    的行情日期 —— 与 PHP ``$ref->GetDate()`` 同源。

    不抛异常：三个 updater 都把"抓不到"表达成 ``reason``，这里翻成一句话。
    """
    tool = get_tool(name)
    if tool is None:
        return {"ok": False, "message": "没有这个工具：%s" % name, "result": None}

    symbol = (symbol or "").strip().upper()
    if not symbol:
        return {"ok": False, "message": "缺少 symbol（符号）。", "result": None}

    if tool["key"] == "dividend":
        result = etfdividend.update_etf_dividend(source, symbol)
        return _wrap(result, _dividend_message(result))

    if tool["key"] == "netvalue":
        result = etfnav.update_etf_net_value(source, symbol)
        return _wrap(result, _netvalue_message(result))

    date = quote_date if quote_date is not None else quote_date_of(source, symbol)
    result = historyupdate.update_history_chart(source, symbol, date)
    return _wrap(result, historyupdate.describe(result))


def quote_date_of(source: Any, symbol: str) -> str:
    """取符号的最新行情日（PHP ``$ref->GetDate()``）。

    建不出参考对象（符号不在库里）时返回空串，由 updater 报
    ``no_quote_date`` —— 与"参考对象存在但没有行情"是两种不同的原因，
    这里不合并。
    """
    try:
        from ref.refobj import MysqlReference

        return MysqlReference(symbol, source=source).get_date() or ""
    except Exception:
        return ""


def _wrap(result: Dict[str, Any], message: str) -> Dict[str, Any]:
    return {"ok": not result.get("reason"), "message": message, "result": result}


def _dividend_message(result: Dict[str, Any]) -> str:
    if result.get("reason"):
        return "%s：%s" % (result.get("symbol"), result["reason"])
    return "%s：解析出 %d 条分红，写入 %d 条（其余值未变）。" % (
        result.get("symbol"), result.get("rows", 0), result.get("written", 0))


def _netvalue_message(result: Dict[str, Any]) -> str:
    if result.get("reason"):
        return "%s：%s" % (result.get("symbol"), result["reason"])
    parts = ["净值写入 %d 条" % result.get("written", 0)]
    if result.get("shares_written"):
        parts.append("流通股数 %d 条" % result["shares_written"])
    if result.get("calibration_deleted"):
        parts.append("顺带清掉 %d 条同日校准值" % result["calibration_deleted"])
    return "%s（%s）：%s。" % (
        result.get("symbol"), result.get("source"), "，".join(parts))


# ---------------------------------------------------------------------------
# 页面上下文
# ---------------------------------------------------------------------------
def describe_symbol(source: Any, symbol: str) -> Dict[str, Any]:
    """这个符号在三个工具各自的目标表里现在是什么状态。

    **只读**，取不到就当空（符号不存在时页面照样能看，好过 500）。
    """
    symbol = (symbol or "").strip().upper()
    status = {"symbol": symbol, "stock_id": None, "dividend": None,
              "netvalue": None, "history": None}
    if not symbol:
        return status

    stock_id = source.get_stock_id(symbol)
    status["stock_id"] = stock_id
    if stock_id is None:
        return status

    status["dividend"] = _daily_status(
        source, "stockdividend", stock_id, "分红")
    status["netvalue"] = _daily_status(
        source, "netvaluehistory", stock_id, "净值")
    #: 历史价格多一项：水位线（stockhistorydate，条件列是 ``id``）
    status["history"] = _daily_status(
        source, "stockhistory", stock_id, "历史")
    status["history"]["watermark"] = _read_watermark(source, stock_id)
    return status


def _daily_status(source: Any, table: str, stock_id: Any, label: str) -> Dict[str, Any]:
    """``(行数, 最新日期)`` —— 三个工具的目标表都是日频表，形态一致。"""
    row = _fetchone(
        source,
        "SELECT COUNT(*) AS n, MAX(`date`) AS latest FROM `%s` WHERE stock_id = %%s" % table,
        (stock_id,),
    )
    return {
        "label": label,
        "table": table,
        "count": int(row["n"]) if row else 0,
        "latest": _text(row["latest"]) if row else None,
    }


def _read_watermark(source: Any, stock_id: Any) -> Optional[str]:
    row = _fetchone(
        source,
        "SELECT `date` FROM stockhistorydate WHERE id = %s LIMIT 1",
        (stock_id,),
    )
    return _text(row["date"]) if row else None


def _fetchone(source: Any, sql: str, params: tuple) -> Any:
    connection = getattr(source, "connection", source)
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        cursor.close()


def _text(value: Any) -> Optional[str]:
    """``date`` 列取出来是 ``datetime.date``，模板里要字符串。"""
    if value is None:
        return None
    return str(value)
