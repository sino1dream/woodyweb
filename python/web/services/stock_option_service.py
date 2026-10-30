"""通用股票编辑页（PHP edit 族 16 个选项）的服务层。

对应 PHP：

* ``woody/res/php/_editstockoption.php`` —— 分派壳子（4 行），16 个
  ``edit*.php`` 页面共用；
* ``woody/res/php/_editstockoptionform.php`` —— 表单（日期 / 值 / 说明），
  以及 ``_getStockOptionDate()`` / ``_getStockOptionVal()`` /
  ``_getStockOptionMemo()`` 三张 switch 表；
* ``woody/res/php/_submitstockoptions.php`` —— POST 目标，``_updateStockOption*``
  一族写函数。

## 这一层在做什么

PHP 那边 16 个页面 = 16 个 4 行壳子 + 1 张分派表 + 1 张表单 + 1 个 POST。
壳子之间唯一的差别是 URL 里的页面名，它决定"当前是哪个选项"。所以本版用一个
路由 ``/stock/<symbol>/edit/<option>`` 顶掉 16 个页面，把差异收进下面这张
:data:`STOCK_OPTIONS` 表：**选项 = (中文动作名, 说明, 日期口径, 读函数, 写函数)**。

## 为什么有些选项没迁

16 项里迁了 10 项。没迁的 6 项分成三类，每类的原因写在 ``note`` 里，页面上
也照原样显示 —— **不装作能写**（半截的写入口比没有更危险）：

1. **已有专门页**：``editquarterreport``（``/holdings/quarter/<symbol>``）、
   ``editstockholdings``（穷举持仓页的「确认更新持仓」）；
2. **依赖账号体系**：``editstockamount`` 要用 member id + 分组名
   （``SqlGetMyStockGroupItemId``）才能找到 ``stockgroupitem`` 行；
3. **缺表 / 要连带重算**：``editsharesdiff`` 缺 ``sharesdiff`` 表（已补 DDL 种子）；
   ``editstockdividend`` / ``editstocksplit`` 除了缺表，写一次还要重算
   **该日之后的全部复权价与校准值**（``_updateStockHistoryAdjCloseBySplit``
   / ``ByDividend``），那属于估值链路，不能在这里顺手做。

## 一处刻意偏离 PHP（会毁数据的那条）

``_updateStockOptionFund()``（``_submitstockoptions.php:183``）::

    $strPos = false;            // 用户只填了 INDEX 时
    ...
    if ($strPos == '0')         // ← PHP 8 里 false == '0' 是 TRUE
    {   ...删校准、删配对、删仓位... }

于是"只填 ``SH513100``、想把配对改成它"会被当成"删除配对"。照抄等于把
生产端的坑搬过来，而这页面的说明（``输入INDEX*0删除对应关系``）明明写的是
要带 ``*0`` 才删。本版按说明实现：**``pos`` 缺失（``None``）不算 0**，
只有显式写了 ``*0`` / ``0`` 才删。见 :meth:`StockOptionService._write_fund`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.stocksymbol import StockSymbol, stock_get_symbol
from sql.historytable import (
    MIN_FLOAT_VAL,
    NetValueHistorySql,
    SharesHistorySql,
    StockHistorySql,
)
from sql.optionstore import (
    AdrPairStore,
    AhPairStore,
    FuturePremiumSql,
    FundPairStore,
    LastCalibrationStore,
    SharesDiffSql,
    StockEmaSql,
)
from sql.positiontable import FundPositionSql
from sql.stocksql import StockSql


#: 日期栏的三种状态。对应 ``_getStockOptionDate()`` 的 switch
#: （``_editstockoptionform.php:5``）：有日期、没有日期（``''`` → 输入框 disabled）。
DATE_NONE = "none"
DATE_HISTORY = "history"        # 行情表最新一天
DATE_PREV_CLOSE = "prev_close"  # 行情表里"昨收"那一天
DATE_NETVALUE = "netvalue"      # 净值表最新一天（没有就退行情表）
DATE_PREMIUM = "premium"        # futurepremium 最新一天
DATE_SHARES = "shares"          # shareshistory/sharediff 最新一天


@dataclass(frozen=True)
class StockOptionSpec:
    """一个编辑选项的静态描述（对应 PHP 的 ``GetStockOptionArray()`` 一项）。"""

    #: URL 片段，就是 PHP 页面名去掉 ``cn.php``
    key: str
    #: ``STOCK_OPTION_*`` 常量的值（``php/stocklink.php:31-46``），
    #: 同时充当表单按钮文字
    submit: str
    #: ``_getStockOptionMemo()`` 的说明（``_editstockoptionform.php:307``）
    memo: str
    #: 日期口径，见 ``DATE_*``
    date_mode: str = DATE_NONE
    #: 已迁为 ``True``；未迁的写进 ``note``
    migrated: bool = False
    #: 未迁原因 / 该去哪儿用
    note: str = ""


#: 16 项，**顺序与 ``GetStockOptionArray()`` 一致**（``php/stocklink.php:255``）。
#: 顺序不是随便排的：PHP 用 ``array_search()`` 反查页面名，编辑器里改顺序会让
#: 分派错位；本版靠 key 直接查，但保持原序便于逐行对照。
STOCK_OPTIONS: tuple = (
    StockOptionSpec(
        key="editcalibration",
        submit="手工校准值",
        memo="清空输入删除对应日期校准值。",
        date_mode=DATE_NETVALUE,
        migrated=False,
        note=(
            "写入要把「手工输入的估算值」换算成校准值"
            "（QdiiGetCalibration + 地区中间价 + INDA/ASHR 的 Yahoo 净值特例），"
            "属于估值链路，随 fundestcalc 的校准写入口一起做。"
        ),
    ),
    StockOptionSpec(
        key="editfund",
        submit="修改对应配对代码和仓位",
        memo="输入INDEX*0删除对应关系和全部校准历史，输入0删除仓位。",
        migrated=True,
    ),
    StockOptionSpec(
        key="editnetvalue",
        submit="修改净值",
        memo="清空输入删除对应日期净值。",
        date_mode=DATE_NETVALUE,
        migrated=True,
    ),
    StockOptionSpec(
        key="editpremium",
        submit="期货升水",
        memo="期货升水年化百分比",
        date_mode=DATE_PREMIUM,
        migrated=True,
    ),
    StockOptionSpec(
        key="editquarterreport",
        submit="修改季报持仓",
        memo="清空输入删除对应日期季报数据。",
        migrated=False,
        note="已有专门页：/holdings/quarter/<symbol>（日期取季报最新一天）。",
    ),
    StockOptionSpec(
        key="editsharesdiff",
        submit="场内新增(万)",
        memo="清空输入删除对应日期新增。",
        date_mode=DATE_SHARES,
        migrated=True,
    ),
    StockOptionSpec(
        key="editstock",
        submit="修改股票说明",
        memo="改 stock 表的 name 列（生产端显示名就是它）。",
        migrated=True,
    ),
    StockOptionSpec(
        key="editstockadr",
        submit="修改H股对应ADR代码",
        memo="输入SYMBOL/0删除对应ADR。",
        migrated=True,
    ),
    StockOptionSpec(
        key="editstockah",
        submit="修改A股对应H股代码",
        memo="清空输入删除对应H股。",
        migrated=True,
    ),
    StockOptionSpec(
        key="editstockamount",
        submit="基金申购金额",
        memo="分组项里的申购金额。",
        migrated=False,
        note=(
            "依赖账号体系：要先由 member 邮箱 + 分组名定位 stockgroupitem 行"
            "（SqlGetMyStockGroupItemId），账号体系整体暂缓。"
        ),
    ),
    StockOptionSpec(
        key="editstockclose",
        submit="更新收盘价",
        memo="只改已有那一天的收盘价与复权价，不会新增行。",
        date_mode=DATE_PREV_CLOSE,
        migrated=True,
    ),
    StockOptionSpec(
        key="editstockdividend",
        submit="分红",
        memo="清空输入删除对应分红。",
        date_mode=DATE_HISTORY,
        migrated=False,
        note=(
            "演示库缺 stockdividend 表；且写一次要连带重算校准值与"
            "该日之后的全部复权价（_updateStockHistoryAdjCloseByDividend）。"
        ),
    ),
    StockOptionSpec(
        key="editstockema",
        submit="修改200/50日EMA",
        memo="股票收盘后的第2天修改才会生效，同时删除以往全部EMA记录。",
        date_mode=DATE_HISTORY,
        migrated=True,
    ),
    StockOptionSpec(
        key="editstockha",
        submit="修改H股对应A股代码",
        memo="清空输入删除对应A股。",
        migrated=True,
    ),
    StockOptionSpec(
        key="editstockholdings",
        submit="修改持仓",
        memo="清空输入删除对应持仓。",
        migrated=False,
        note=(
            "已有入口：穷举持仓页 /holdings/exhaustive/<symbol> 的「确认更新持仓」"
            "（POST /holdings/submit → process_admin_holdings）。"
            "SH/SZ ETF 支线在 PHP 里是抓交易所申赎清单文件，未迁。"
        ),
    ),
    StockOptionSpec(
        key="editstocksplit",
        submit="拆股或合股",
        memo="输入1:10表示10股合1股，10:1表示1股拆10股，0:0删除对应日期数据。",
        date_mode=DATE_HISTORY,
        migrated=False,
        note=(
            "演示库缺 stocksplit 表；且要连带改交易流水、校准值与复权序列"
            "（_updateStockOptionSplitTransactions / AdjCloseBySplit）。"
        ),
    ),
)

#: ``key`` → spec
STOCK_OPTION_MAP = {spec.key: spec for spec in STOCK_OPTIONS}


def get_stock_option(key: str) -> Optional[StockOptionSpec]:
    """按 URL 片段取选项描述；没有返回 ``None``（路由据此 404）。"""
    return STOCK_OPTION_MAP.get(key)


# ---------------------------------------------------------------------------
# PHP 口径的小工具
# ---------------------------------------------------------------------------


def _php_empty(text: Any) -> bool:
    """PHP ``empty($strVal)`` 的字符串口径：``''`` 与 ``'0'`` **都算空**。

    ``_updateOptionDailySql()``（``_submitstockoptions.php:211``）靠它决定
    "删一行"还是"写一行"，所以管理员把净值填成 ``0`` 时 PHP 是**删行**。
    照抄 —— 别"修正"成"只有空串才删"。

    注意 ``'0.0'`` **不算空**（非空字符串），与 ``_is_zero_text()`` 是两套语义；
    PHP 那边同一文件里也确实是两套（``empty()`` 与 ``== '0'``）。

    本文件自带一份而不是复用 ``routes/holdings.py`` 的：仓库里已有三份同款
    （``core/stocktrans.py`` / ``routes/holdings.py`` / ``services/trading_service.py``），
    沿用的是"每个模块一处"的既有做法 —— 它是三行的口径函数，跨模块 import
    反而会让依赖关系变复杂。
    """
    if text is None:
        return True
    text = str(text)
    return text == "" or text == "0"


def _is_zero_text(text: Any) -> bool:
    """PHP ``$x == '0'`` 的数字串口径：``'0'`` / ``'0.0'`` / ``'0.00'`` 都算零。

    PHP 8 把两个**数字串**按数值比较，所以 ``'0.0' == '0'`` 为真；
    非数字串（``'abc'``）按字符串比，为假。``None`` 不在这里判 ——
    "没填"与"填了 0"是两个意思，调用方要分开处理（见模块 docstring 的偏离说明）。
    """
    if text is None:
        return False
    try:
        return float(text) == 0.0
    except (TypeError, ValueError):
        return str(text) == "0"


def _intval(text: Any) -> int:
    """PHP ``intval()``：字符串按前缀解析，解析不了给 0。

    ``intval('1.5')`` 是 **1**，不是 2 —— ``_updateStockOptionAdr()`` 用它判断
    ``!= 1`` 决定"要不要写仓位"，所以 ``ADR/1.5`` 在 PHP 里**不会**写仓位。
    照抄这个截断行为（见 pitfalls 里那条 ADR 记录）。
    """
    if text is None:
        return 0
    if isinstance(text, bool):
        return 1 if text else 0
    try:
        return int(float(str(text).strip()))
    except (TypeError, ValueError):
        return 0


def _number_text(value: Any, precision: int = 6) -> str:
    """把库里的浮点值写成"能直接编辑"的文本：四舍五入到 6 位再去掉尾零。

    PHP 那边靠 mysqli 把 ``double(13,6)`` 读成 ``"1.434200"`` 直接塞进
    ``<textarea>``，管理员看到一串尾零。这里去掉尾零（``1.4342``）——
    只影响**显示**：提交时 ``WriteDaily`` 按 ``MIN_FLOAT_VAL`` 比数值，
    ``1.4342`` 与库里的 ``1.434200`` 视为相等，不会被误写。
    """
    if value is None:
        return ""
    try:
        number = round(float(value), precision)
    except (TypeError, ValueError):
        return str(value)
    text = "%.*f" % (precision, number)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _today() -> str:
    """``GetNowYMD()->GetYMD()``：服务器本地日期（``Y-m-d``）。"""
    import datetime

    return datetime.date.today().strftime("%Y-%m-%d")


def _date_text(value: Any) -> str:
    """把库里的 ``date`` 列值统一成 ``'YYYY-MM-DD'`` 字符串。

    **必须转**：pymysql 对 ``date`` 列给的是 ``datetime.date``，直接塞进模板
    能渲染（``str(date)`` 就是 ISO 格式），但 POST 回来的是字符串 ——
    比较/落库时两种类型混在一起迟早出事（``daily`` 表的写方法按字符串比较）。
    统一在入口处转成字符串，后面全线只有一种类型。
    """
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else text


def _connection_of(source: Any) -> Any:
    connection = getattr(source, "connection", None)
    if connection is None:
        raise ValueError("编辑页需要一个带 connection 的数据源")
    return connection


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------


class StockOptionService:
    """一个符号 + 一个选项的读默认值 / 写回。

    构造要 ``source``（只有 ``.connection`` 被用到，外加 ``get_stored_quote``
    可选 —— 有快照时"更新收盘价"的默认值取快照里的昨收，与 PHP
    ``GetPrevPrice()`` 同源）；表对象都可以注入，测试不必连库。
    """

    def __init__(self, source: Any, stock_sql: Any = None, net_value_sql: Any = None,
                 history_sql: Any = None):
        self.source = source
        self.connection = _connection_of(source)
        self.stock_sql = stock_sql if stock_sql is not None else StockSql(self.connection)
        self.net_value_sql = (
            net_value_sql if net_value_sql is not None else NetValueHistorySql(self.connection)
        )
        self.history_sql = (
            history_sql if history_sql is not None else StockHistorySql(self.connection)
        )

    # -- 懒加载的小表 -------------------------------------------------------
    def _position(self) -> FundPositionSql:
        return FundPositionSql(self.connection)

    def _last_calibration(self) -> LastCalibrationStore:
        return LastCalibrationStore(self.connection)

    def _ah_pair(self) -> AhPairStore:
        return AhPairStore(self.connection, stock_sql=self.stock_sql)

    def _adr_pair(self) -> AdrPairStore:
        return AdrPairStore(self.connection, stock_sql=self.stock_sql)

    def _fund_pair(self) -> FundPairStore:
        return FundPairStore(self.connection, stock_sql=self.stock_sql)

    # -- 页面装配 -----------------------------------------------------------
    def list_options(self) -> list:
        """索引页用：16 项 + 是否已迁。"""
        return [
            {
                "key": spec.key,
                "submit": spec.submit,
                "memo": spec.memo,
                "migrated": spec.migrated,
                "note": spec.note,
            }
            for spec in STOCK_OPTIONS
        ]

    def get_form(self, symbol: str, key: str) -> Optional[dict]:
        """取表单初值。``key`` 不认识返回 ``None``；符号不在库也一样返回表（附提示）。

        对应 ``_editstockoption.php`` 的 ``EchoAll()``：先 ``EchoStockGroup()``
        拿到引用，再 ``StockOptionEditForm($_getEditStockOptionSubmit(...))``，
        表单里 ``date`` 与 ``val`` 两个框的初值分别来自
        ``_getStockOptionDate()`` / ``_getStockOptionVal()``。
        """
        spec = get_stock_option(key)
        if spec is None:
            return None
        stock_id = self._stock_id(symbol)
        context = {
            "symbol": symbol,
            "stock_id": stock_id,
            "option": {
                "key": spec.key,
                "submit": spec.submit,
                "memo": spec.memo,
                "migrated": spec.migrated,
                "note": spec.note,
            },
            "date": "",
            "value": "",
            "date_enabled": spec.date_mode != DATE_NONE,
            "notice": "",
        }
        if not stock_id:
            context["notice"] = "stock 表里没有符号 %s。" % symbol
            return context
        if not spec.migrated:
            context["notice"] = "「%s」还没迁：%s" % (spec.submit, spec.note)
            return context
        #: 日期统一成字符串再往下走（见 :func:`_date_text`）。
        date = _date_text(self._default_date(spec, symbol, stock_id))
        context["date"] = date
        context["value"] = self._read_value(spec, symbol, stock_id, date)
        return context
    def submit(self, symbol: str, key: str, date: Any, value: Any) -> dict:
        """执行写回。返回 ``{"ok": bool, "message": str}``。

        与 ``_SubmitOptionsAccount::Process()``（``_submitstockoptions.php:411``）
        同构：按选项分派到各自的写函数。权限闸门**不在这里** ——
        与 ``process_admin_holdings`` 的做法一致，由路由调 ``is_admin()`` 后
        才允许进来（闸门放在最外层，越权调用连符号合法性都探不到）。
        """
        spec = get_stock_option(key)
        if spec is None:
            return {"ok": False, "message": "不认识的编辑选项 %s。" % key}
        if not spec.migrated:
            return {"ok": False, "message": "「%s」还没迁：%s" % (spec.submit, spec.note)}
        stock_id = self._stock_id(symbol)
        if not stock_id:
            return {"ok": False, "message": "stock 表里没有符号 %s，没有写入。" % symbol}

        date = "" if date is None else str(date).strip()
        value = "" if value is None else str(value)
        writer = getattr(self, "_write_" + spec.key[len("edit"):], None)
        if writer is None:
            return {"ok": False, "message": "「%s」的写入口还没实现。" % spec.submit}
        try:
            return writer(symbol, stock_id, date, value)
        except Exception as exc:  # pragma: no cover - 只在演示库缺表/断连时触发
            #: **不吞掉错误**：把驱动的话原样带到页面上，管理员能看出是
            #: "表不存在"还是"连接断了"。演示库比生产端少几张表
            #: （``sharesdiff`` / ``stockdividend`` / ``stocksplit``），
            #: 缺表时应该给一句可操作的话，而不是 500 白屏。
            return {"ok": False, "message": "写库失败：%s" % exc}

    # -- 内部：符号 ---------------------------------------------------------
    def _stock_id(self, symbol: str) -> Any:
        try:
            return self.stock_sql.get_id(symbol)
        except Exception:
            return None

    def _symbol_is_lof(self, symbol: str) -> bool:
        try:
            return bool(StockSymbol(symbol).is_lof_a())
        except Exception:
            return False

    # -- 内部：日期（``_getStockOptionDate``）--------------------------------
    def _default_date(self, spec: StockOptionSpec, symbol: str, stock_id: Any) -> str:
        if spec.date_mode == DATE_NONE:
            return ""
        if spec.date_mode == DATE_PREV_CLOSE:
            #: ``$his_sql->GetRecordPrev($strStockId, $ref->GetDate())`` ——
            #: 取"引用当天"之前最近一个交易日。引用日期优先取实时快照的
            #: ``quote_date``（本地没有快照就用今天），与 PHP 拿新浪行情的
            #: ``GetDate()`` 等价。
            reference_date = self._quote_date(symbol) or _today()
            return self.history_sql.get_date_prev(stock_id, reference_date) or _today()
        if spec.date_mode == DATE_HISTORY:
            return self.history_sql.get_date_now(stock_id) or _today()
        if spec.date_mode == DATE_NETVALUE:
            #: ``editcalibration`` / ``editnetvalue``：先净值表、再行情表。
            #: KWEB 那一支（取行情前一日、抓 Krane 净值）没迁 —— 整个 KWEB
            #: 特例都在 ``_updateStockOptionNetValue`` 里，见选项 note。
            return (
                self.net_value_sql.get_date_now(stock_id)
                or self.history_sql.get_date_now(stock_id)
                or _today()
            )
        if spec.date_mode == DATE_PREMIUM:
            return FuturePremiumSql(self.connection).get_date_now(stock_id) or _today()
        if spec.date_mode == DATE_SHARES:
            return SharesHistorySql(self.connection).get_date_now(stock_id) or _today()
        return _today()

    def _quote_date(self, symbol: str) -> Any:
        """实时快照的行情日期；没有快照返回 ``None``。"""
        reader = getattr(self.source, "get_stored_quote_date", None)
        if reader is None:
            return None
        try:
            return reader(symbol)
        except Exception:
            return None

    def _prev_price(self, symbol: str, stock_id: Any) -> str:
        """``$ref->GetPrevPrice()``：昨收。

        优先取 ``realtimequote`` 快照里的 ``prev_price``（与 PHP 同源：那边
        的引用对象也是从行情行里解析出 ``strPrevPrice``）；没有快照就退回
        ``stockhistory`` 最新一条收盘价。两处都取不到给空串 ——
        PHP 的 ``rtrim0('')`` 会得到 ``'0'``，那个值提交上去等于把收盘价改成 0，
        所以这里宁可不给（页面会显示空框）。
        """
        reader = getattr(self.source, "get_stored_quote", None)
        quote = None
        if reader is not None:
            try:
                quote = reader(symbol)
            except Exception:
                quote = None
        if quote is not None:
            price = getattr(quote, "prev_price", None)
            if price not in (None, ""):
                return _number_text(price)
        close = self.history_sql.get_close_now(stock_id)
        return _number_text(close) if close is not None else ""

    # -- 内部：读初值（``_getStockOptionVal``）------------------------------
    def _read_value(self, spec: StockOptionSpec, symbol: str, stock_id: Any, date: str) -> str:
        reader = getattr(self, "_read_" + spec.key[len("edit"):], None)
        if reader is None:
            return ""
        try:
            return reader(symbol, stock_id, date)
        except Exception:
            #: PHP 这些取数都在 try 之外，表缺了会 500。本版给空值 ——
            #: 页面还能打开，管理员能看见"取不到"而不是白屏。
            return ""

    def _read_stock(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionName()``（``_editstockoptionform.php:68``）。

        PHP 还会把行情里抓到的中文名/英文名**接在后面**当对照
        （``_getStockOptionNewName``），那需要联网取名字。本地离线，只显示
        ``stock.name``。这不影响写入语义：这一栏是 ``stock.name`` 的**整份替换**，
        看到什么就写什么。
        """
        return self.stock_sql.get_name(symbol) or ""

    def _read_stockclose(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionVal`` 的 CLOSE 分支：``$ref->GetPrevPrice()``。"""
        return self._prev_price(symbol, stock_id)

    def _read_netvalue(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionNetValue()``：``SqlGetNetValueByDate()``。

        KWEB 那一支（``GetKraneNetValue``）没迁，见选项 note。
        """
        if not date:
            return ""
        return _number_text(self.net_value_sql.get_close(stock_id, date))

    def _read_premium(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionPremium()``：没有记录给 ``'4.5'``（PHP 的示例值）。"""
        if not date:
            return "4.5"
        close = FuturePremiumSql(self.connection).get_close(stock_id, date)
        return _number_text(close) if close is not None else "4.5"

    def _read_stockema(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionEma()``：``round(ema200, 2)/round(ema50, 2)``。

        ``round(false, 2)`` 在 PHP 里是 ``0.0``，而 ``if ($str200 && $str50)``
        把 0 当假 → 回落到 ``'EMA200/50'`` 这个格式提示。照抄：任一侧取不到
        或为 0，显示提示串而不是 ``0/0``。
        """
        if not date:
            return "EMA200/50"
        ema200 = StockEmaSql(self.connection, 200).get_close(stock_id, date)
        ema50 = StockEmaSql(self.connection, 50).get_close(stock_id, date)
        if not ema200 or not ema50:
            return "EMA200/50"
        return "%s/%s" % (_number_text(ema200, 2), _number_text(ema50, 2))

    def _read_sharesdiff(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionSharesDiff()``：先查 ``sharesdiff``，再从份额序列算差。"""
        if not date:
            return ""
        shares_diff = SharesDiffSql(self.connection)
        value = shares_diff.get_close(stock_id, date)
        if value is not None:
            return _number_text(value)
        shares = SharesHistorySql(self.connection)
        close = shares.get_close(stock_id, date)
        if close is None:
            return ""
        prev = shares.get_close_prev(stock_id, date)
        if prev is None:
            return ""
        #: ``strval(floatval($strClose) - floatval($strClosePrev))``
        return _number_text(float(close) - float(prev))

    def _read_stockah(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionAh()``：A 股 → H 股；没有配对给格式提示 ``'H'``。"""
        return self._ah_pair().get_pair_symbol(symbol) or "H"

    def _read_stockha(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionHa()``：H 股 → A 股；没有配对给 ``'A'``。"""
        return self._ah_pair().get_symbol(symbol) or "A"

    def _read_stockadr(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionAdr()``：H 股 → ``ADR`` 或 ``ADR/比例``。

        比例取自 ADR 自己的 ``fundposition`` 行（不是 H 股的）。没有配对时给
        格式提示 ``'ADR/100'``。
        """
        adr = self._adr_pair().get_symbol(symbol)
        if not adr:
            return "ADR/100"
        adr_id = self._stock_id(adr)
        ratio = self._position().read_val(adr_id) if adr_id else None
        if ratio:
            return "%s/%s" % (adr, _number_text(ratio))
        return adr

    def _read_fund(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionFund()``：``INDEX*仓位`` / ``INDEX`` / ``仓位`` / 提示串。"""
        position = self._position().read_val(stock_id)
        pair = self._fund_pair().get_pair_symbol(symbol)
        if pair:
            return "%s*%s" % (pair, _number_text(position)) if position else pair
        if position:
            return _number_text(position)
        #: ``'INDEX*'.($ref->IsLofA() ? '0.95' : '1')`` —— LOF 的默认仓位是 0.95，
        #: ETF 是 1。这个 0.95 与 ``StockSymbol.get_default_position()`` 同一口径。
        return "INDEX*0.95" if self._symbol_is_lof(symbol) else "INDEX*1"

    # -- 内部：写回（``_updateStockOption*`` / ``_updateOptionDailySql``）----
    def _update_daily(self, sql: Any, stock_id: Any, date: str, value: Any) -> dict:
        """``_updateOptionDailySql()``（``_submitstockoptions.php:208``）。

        **空值（含 ``'0'``）→ 按日期删；否则 → ``WriteDaily``**。
        日频表的统一写入口，六张表共用（净值 / 升水 / 场内新增 / 季报 / …）。
        """
        if not date:
            return {"ok": False, "message": "日期不能为空。"}
        if _php_empty(value):
            deleted = sql.delete_by_date(stock_id, date)
            return {"ok": True, "message": "%s：已删除该日记录（%d 行）。" % (date, deleted)}
        written = sql.write_daily(stock_id, date, value)
        if written:
            return {"ok": True, "message": "%s：已写入 %s。" % (date, value)}
        #: 写不进去只有两种原因：值没变（MIN_FLOAT_VAL 内）或被周末闸门挡下。
        return {"ok": True, "message": "%s：值没有变化（或落在周末闸门），未写入。" % date}

    def _write_stock(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateStockDescription()``：``StockSql->WriteSymbol()``。"""
        name = value.strip()
        if not name:
            #: PHP 会把空串直接写进 name（列 NOT NULL 但允许空串）。改空等于
            #: 抹掉显示名，页面全靠它认标的 —— 这里挡住。
            return {"ok": False, "message": "名称不能为空，没有写入。"}
        self.stock_sql.write_symbol(symbol, name)
        return {"ok": True, "message": "%s：名称已改为「%s」。" % (symbol, name)}

    def _write_stockclose(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateStockHistoryClose()``（``_submitstockoptions.php:50``）。

        **只改已有行，不新增**：``if ($record = $his_sql->GetRecord(...))``。
        改的是 ``close`` 与 ``adjclose`` 两列（见
        :meth:`sql.historytable.StockHistorySql.update_close`）。
        """
        if not date:
            return {"ok": False, "message": "日期不能为空。"}
        text = value.strip()
        if not text:
            #: PHP 没有这道闸（会把空串写成 0）。收盘价被写成 0 会让当天的
            #: 涨幅变成 -100%，所有依赖它的对比都失真 —— 挡住。
            return {"ok": False, "message": "收盘价不能为空，没有写入。"}
        record = self.history_sql.get_record(stock_id, date)
        if record is None:
            return {
                "ok": False,
                "message": "%s %s 没有行情记录，本页只改已有行、不新增。" % (symbol, date),
            }
        self.history_sql.update_close(record.get("id"), text)
        return {"ok": True, "message": "%s %s：收盘价与复权价已改为 %s。" % (symbol, date, text)}

    def _write_netvalue(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateOptionDailySql(GetNetValueHistorySql())``。"""
        return self._update_daily(self.net_value_sql, stock_id, date, value)

    def _write_premium(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateOptionDailySql(new FuturePremiumSql())``。"""
        return self._update_daily(FuturePremiumSql(self.connection), stock_id, date, value)

    def _write_sharesdiff(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateOptionDailySql(new SharesDiffSql())``。"""
        return self._update_daily(SharesDiffSql(self.connection), stock_id, date, value)

    def _write_stockema(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateStockOptionEma()``（``_submitstockoptions.php:148``）。

        顺序照抄：**先清空这只标的在两表里的全部 EMA**，再按 ``200/50`` 回填。
        清空是必须的 —— EMA 是递推量，换一个起点就与旧序列不一致，留着旧的
        会让均线段新旧混排。值为 ``'EMA200/50'``（或任何不含 ``/`` 的串）时
        只清空不回填，等价于"删除 EMA"。

        ## 一处刻意偏离：先校验再清空

        PHP 是"先 ``SqlDeleteStockEma()`` 清空，再看有没有 ``/``"，而**占位提示串
        本身就是 ``'EMA200/50'``** —— 里面正好有 ``/``。于是管理员不动那一栏、
        直接提交时，PHP 会把 ``'EMA200'`` 与 ``'50'`` 当数字写进
        ``double`` 列（非 strict 模式下 ``'EMA200'`` 变 0），并把真正的 EMA
        全删了。本版把校验提到清空之前：两段都必须是数字，否则一句
        错误、一个字节都不写。

        这是"顺序"上的偏离，**变量语义没变** —— 合法输入的两条路径与 PHP 完全
        一致（清空 → 写 200 → 写 50）。
        """
        if not date:
            return {"ok": False, "message": "日期不能为空。"}
        ema200: Any = None
        ema50: Any = None
        if "/" in value:
            parts = value.split("/")
            if len(parts) < 2 or not _is_number(parts[0]) or not _is_number(parts[1]):
                return {
                    "ok": False,
                    "message": "写法要像 5.12/4.98（200日EMA/50日EMA，两段都要是数字），"
                               "没有写入，原有 EMA 也没动。",
                }
            ema200, ema50 = parts[0].strip(), parts[1].strip()
        for days in (50, 200):
            StockEmaSql(self.connection, days).delete_all(stock_id)
        if ema200 is None:
            return {"ok": True, "message": "%s：EMA 记录已全部删除（值里没有 200/50）。" % symbol}
        StockEmaSql(self.connection, 200).write_daily(stock_id, date, ema200)
        StockEmaSql(self.connection, 50).write_daily(stock_id, date, ema50)
        return {
            "ok": True,
            "message": "%s %s：EMA200=%s、EMA50=%s 已写入。" % (symbol, date, ema200, ema50),
        }

    def _write_stockah(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateStockOptionAh()``：清空 → ``DeleteBySymbol(A)``。

        注意**本体是 A 股**：删的是"以 A 股为主体的那一行"。
        """
        pair = self._ah_pair()
        text = value.strip()
        if not text:
            deleted = pair.delete_by_symbol(symbol)
            return {"ok": True, "message": "%s：已删除 H 股配对（%d 行）。" % (symbol, deleted)}
        target = stock_get_symbol(text)
        if not pair.write_pair_symbol(symbol, target):
            return {"ok": False, "message": "写配对失败：%s 或 %s 不在 stock 表里。" % (symbol, target)}
        return {"ok": True, "message": "%s：H 股已改为 %s。" % (symbol, target)}

    def _write_stockha(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateStockOptionHa()``：清空 → ``DeleteByPairSymbol(H)``。

        与 :meth:`_write_stockah` 的差别只在**删除方向** —— 这里是"以 H 股为
        配对侧"删，因为表单里的符号是 H 股。搞反会删掉别的 A 股的配对。
        """
        pair = self._ah_pair()
        text = value.strip()
        if not text:
            deleted = pair.delete_by_pair_symbol(symbol)
            return {"ok": True, "message": "%s：已删除 A 股配对（%d 行）。" % (symbol, deleted)}
        target = stock_get_symbol(text)
        if not pair.write_pair_symbol(target, symbol):
            return {"ok": False, "message": "写配对失败：%s 或 %s 不在 stock 表里。" % (target, symbol)}
        return {"ok": True, "message": "%s：对应的 A 股已改为 %s。" % (symbol, target)}

    def _write_stockadr(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateStockOptionAdr()``（``_submitstockoptions.php:95``）。

        值形如 ``ADR`` 或 ``ADR/比例``，比例缺失按 ``'1'`` 处理。
        ``比例 == 0`` → 删配对 + 删 ADR 的仓位行；否则写配对，比例 ``intval``
        不为 1 时再写仓位。

        **``intval($strRatio) != 1`` 是照抄的**：``ADR/1.5`` 的 ``intval`` 是 1，
        所以仓位那一步会跳过 —— 生产端就是这个行为（1.5 只能靠先存 2 再改，
        或者直接动库）。见 pitfalls 那条 ADR 记录。
        """
        text = value.strip()
        if not text:
            return {"ok": False, "message": "ADR 代码不能为空（要删除请写 SYMBOL/0）。"}
        if "/" in text:
            adr_text, ratio = text.split("/", 1)
        else:
            adr_text, ratio = text, "1"
        adr = stock_get_symbol(adr_text)
        adr_id = self._stock_id(adr)
        if not adr_id:
            return {"ok": False, "message": "stock 表里没有 %s，没有写入。" % adr}
        pair = self._adr_pair()
        position = self._position()
        if _is_zero_text(ratio):
            pair.delete_by_id(adr_id)
            position.delete_by_id(adr_id)
            return {"ok": True, "message": "%s：已删除对应的 ADR %s。" % (symbol, adr)}
        pair.write_pair(adr_id, stock_id)
        if _intval(ratio) != 1:
            position.write_val(adr_id, ratio.strip())
        return {"ok": True, "message": "%s：ADR 已改为 %s（比例 %s）。" % (symbol, adr, ratio.strip())}

    def _write_fund(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateStockOptionFund()``（``_submitstockoptions.php:159``）。

        解析三支（顺序照抄）：

        * 含 ``*`` → ``INDEX*仓位``；
        * 不含 ``*`` 且是**小于 3** 的数字 → 只有仓位（``$strIndex = false``）；
        * 否则 → 只有 INDEX。

        然后：

        * 仓位为 0 → 有 INDEX 时先删校准历史 + ``lastcalibration`` + 配对，
          再删仓位行；
        * 否则 → 写配对（有 INDEX 时）、写仓位（有仓位时）。

        ## 与 PHP 的唯一一处刻意偏离

        PHP 的 ``if ($strPos == '0')`` 在 ``$strPos === false`` 时**也成立**
        （``false == '0'`` 为真），于是"只填 INDEX"会走删除分支。本版用
        :func:`_is_zero_text` 判 —— ``None``（没写仓位）不算零。理由见模块
        docstring 末段。
        """
        text = value.strip()
        index: Any = None
        position_text: Any = None
        if "*" in text:
            index_text, position_text = text.split("*", 1)
            index = index_text.strip() or None
        else:
            numeric = _is_number(text)
            if numeric and float(text) < 3.0 + MIN_FLOAT_VAL:
                index, position_text = None, text
            else:
                index, position_text = (text or None), None

        pair = self._fund_pair()
        position = self._position()
        if _is_zero_text(position_text):
            if index:
                self._calibration_sql().delete_all(stock_id)
                self._last_calibration().delete_by_id(stock_id)
                pair.delete_by_symbol(symbol)
            position.delete_by_id(stock_id)
            return {"ok": True, "message": "%s：配对关系与仓位已删除。" % symbol}

        if index:
            pair_symbol = stock_get_symbol(index)
            if not pair.write_pair_symbol(symbol, pair_symbol):
                return {"ok": False, "message": "写配对失败：%s 或 %s 不在 stock 表里。" % (
                    symbol, pair_symbol)}
        if position_text:
            position.write_val(stock_id, position_text.strip())
        return {"ok": True, "message": "%s：配对=%s，仓位=%s。" % (
            symbol, index or "（未改）", position_text or "（未改）")}

    def _calibration_sql(self) -> Any:
        """``calibrationhistory`` 的写对象（``GetCalibrationSql()``）。

        只在 ``_write_fund`` 的删除分支里用到 —— ``DeleteAll($stockId)`` 把这只
        标的的全部校准记录清掉，因为配对换了以后旧校准值全部失去意义。
        """
        from sql.historytable import CalibrationSql

        return CalibrationSql(self.connection)


def _is_number(text: Any) -> bool:
    """PHP ``is_numeric()`` 的近似：去掉首尾空白后能解析成浮点。"""
    if text is None:
        return False
    try:
        float(str(text).strip())
    except (TypeError, ValueError):
        return False
    return True
