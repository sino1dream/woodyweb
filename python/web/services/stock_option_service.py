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

## 16 项现在都有去处

* **14 项在本页可写** —— 其中 ``editstockamount``（基金申购金额）是**会员级**
  入口：PHP 的 ``Process()`` 里只有这一支没有 ``$bAdmin`` 判断，改的是登录会员
  自己名下那一行（``member`` → ``stockgroup`` → ``stockgroupitem`` →
  ``groupitemamount``），本版同样只要求"有登录身份"；
* **2 项跳到专门页** —— ``editquarterreport`` 去 ``/holdings/quarter/<symbol>``、
  ``editstockholdings`` 去 ``/holdings/exhaustive/<symbol>``（它的「确认更新持仓」
  走 ``POST /holdings/submit``）。这里**不重复实现**它们的写入口：季报那张表是
  ``DailyStringSql``（口径与日频表不同），持仓要连带 ``fundposition``——
  两处各写一份，迟早跑偏。

``StockOptionSpec.route`` 非空就是这个意思：索引页直接给专门页链接，
``/stock/<symbol>/edit/<option>`` 收到这一项时 302 过去。

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

import re
from dataclasses import dataclass
from typing import Any, Optional

from calc.fundcalc import (
    qdii_est_symbol,
    qdii_eu_est_symbol,
    qdii_get_calibration,
    qdii_hk_est_symbol,
    qdii_jp_est_symbol,
    qdii_symbols_for_est,
)
from core.stockgroupsql import (
    StockGroupItemSql,
    StockGroupSql,
    sql_get_id_by_email,
    sql_get_my_stock_group_item_id,
)
from core.stocksymbol import (
    StockSymbol,
    in_array_china_future,
    in_array_china_index,
    in_array_qdii,
    in_array_qdii_eu,
    in_array_qdii_hk,
    in_array_qdii_jp,
    stock_get_symbol,
)
from core.submittransaction import update_stock_group_item_transaction
from sql.historytable import (
    MIN_FLOAT_VAL,
    NetValueHistorySql,
    SharesHistorySql,
    StockHistorySql,
    php_text_equal,
)
from sql.optionstore import (
    AdrPairStore,
    AhPairStore,
    FuturePremiumSql,
    FundPairStore,
    GroupItemAmountStore,
    LastCalibrationStore,
    SharesDiffSql,
    StockDividendSql,
    StockEmaSql,
    StockSplitSql,
)
from sql.positiontable import FundPositionSql
from sql.smaconfig import unlink_config_file
from sql.stocksql import StockSql


#: 日期栏的三种状态。对应 ``_getStockOptionDate()`` 的 switch
#: （``_editstockoptionform.php:5``）：有日期、没有日期（``''`` → 输入框 disabled）。
DATE_NONE = "none"
DATE_HISTORY = "history"        # 行情表最新一天
DATE_PREV_CLOSE = "prev_close"  # 行情表里"昨收"那一天
DATE_NETVALUE = "netvalue"      # 净值表最新一天（没有就退行情表）
DATE_PREMIUM = "premium"        # futurepremium 最新一天
DATE_SHARES = "shares"          # shareshistory/sharediff 最新一天

#: ``STOCK_DISP_NETVALUE``（``php/ui/stockdisp.php:15``）：没有"对方"引用时
#: 表单里显示 ``'对方'.STOCK_DISP_NETVALUE``。
DISPLAY_NET_VALUE = "净值"

#: 地区 → 中间价符号。PHP 是四个 ``if`` 分支各写一个
#: ``SqlGetStockId('USCNY')``（``_submitstockoptions.php:373-376``），
#: 地区判定顺序与 ``StockGetQdiiReference()``（``php/stock.php:306``）相同。
QDII_CNY_SYMBOLS = {"us": "USCNY", "hk": "HKCNY", "jp": "JPCNY", "eu": "EUCNY"}


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
    #: 已有专门页时，Flask endpoint 名（``holdings.quarter_report_page`` 这类）。
    #: 本页**不重复实现**它的写入口，GET/POST 都 302 过去。
    route: str = ""
    #: 会员级写入口（PHP 只要求登录、不要求管理员）。目前只有
    #: ``editstockamount`` 一项 —— 它改的是登录会员**自己**名下那一行。
    member_level: bool = False


#: 16 项，**顺序与 ``GetStockOptionArray()`` 一致**（``php/stocklink.php:255``）。
#: 顺序不是随便排的：PHP 用 ``array_search()`` 反查页面名，编辑器里改顺序会让
#: 分派错位；本版靠 key 直接查，但保持原序便于逐行对照。
STOCK_OPTIONS: tuple = (
    StockOptionSpec(
        key="editcalibration",
        submit="手工校准值",
        memo="清空输入删除对应日期校准值。",
        date_mode=DATE_NETVALUE,
        migrated=True,
        note=(
            "写的是「估算值」不是校准值：服务层按 QdiiGetCalibration 换算成"
            "校准值再落库（估算值 × 地区中间价 / 基金净值）。"
            "INDA / ASHR / hf_CHA50CFD 三支要抓 Yahoo 净值或取期货 VWAP，未迁 —— "
            "这三支会明确拒绝写入，不会写错值。"
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
        note=(
            "在专门页里改：/holdings/quarter/<symbol>"
            "（QuarterReportSql(DailyStringSql)，填 0 = 删除，日期取季报最新一天）。"
        ),
        route="holdings.quarter_report_page",
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
        memo="分组项里的申购金额（你的自选分组里这只标的那一行）。",
        migrated=True,
        member_level=True,
        note=(
            "会员级写入口：按登录邮箱找 member，再按「分组名 = 符号」找"
            " stockgroupitem 行，写 groupitemamount.num。"
            "PHP 这一支不要求管理员（$_POST['submit'] == STOCK_OPTION_AMOUNT "
            "那一段没有 $bAdmin 判断），本版同样只要求登录身份。"
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
        migrated=True,
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
        note=(
            "在专门页里改：/holdings/exhaustive/<symbol> 的「确认更新持仓」"
            "（POST /holdings/submit → process_admin_holdings）。"
            "SH/SZ ETF 支线在 PHP 里是抓交易所申赎清单文件，未迁。"
        ),
        route="holdings.exhaustive_holdings_page",
    ),
    StockOptionSpec(
        key="editstocksplit",
        submit="拆股或合股",
        memo="输入1:10表示10股合1股，10:1表示1股拆10股，0:0删除对应日期数据。",
        date_mode=DATE_HISTORY,
        migrated=True,
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


#: ``is_numeric()`` 用的十进制数字串（见 :func:`_php_is_numeric`）。
_NUMERIC_TEXT = re.compile(r"^\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*$")


def _php_is_numeric(text: Any) -> bool:
    """PHP 7+ ``is_numeric($str)`` 的字符串口径。

    只接受十进制：可选**前后**空白（PHP 7.0 起两边都允许）、可选正负号、
    数字带可选小数点、可选指数。``''`` / ``'abc'`` / ``'1e'`` / ``'0x1A'``
    都不算数字（十六进制在 PHP 7 已经不算）。

    ``_updateFundPurchaseAmount()`` 用它当唯一的输入闸门 —— 不是数字就
    **整笔跳过**（连"写错了"都不报）。本版照抄判定，只把"跳过"换成一句话。
    """
    if text is None:
        return False
    return _NUMERIC_TEXT.match(str(text)) is not None


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
        """索引页用：16 项 + 是否已迁 + 是否有专门页。

        ``route`` 非空的两项（季报 / 持仓）**不**在索引页给 ``<option>``
        链接，直接指到专门页 —— 两边各写一份写入口，迟早会跑偏。
        """
        return [
            {
                "key": spec.key,
                "submit": spec.submit,
                "memo": spec.memo,
                "migrated": spec.migrated,
                "note": spec.note,
                "route": spec.route,
                "member_level": spec.member_level,
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
                "route": spec.route,
                "member_level": spec.member_level,
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
        if spec.member_level:
            #: 会员级的那一项读不到分组项时不摆表单 —— 空值写进去没有任何意义
            #: （PHP 会往 textarea 里填一句 ``出错了``，照样让人提交）。
            context["notice"] = self._member_notice(symbol, stock_id)
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
            #: ``editcalibration`` / ``editnetvalue`` 共用同一个 case
            #: （``_editstockoptionform.php:32``）。KWEB 是**例外**：它的净值来自
            #: Krane 官方 CSV，日期取"行情前一日"（净值比行情晚一天），
            #: 取不到才退今天 —— 注意它**不**退到净值表/行情表的最新一天。
            if symbol == "KWEB":
                reference_date = self._quote_date(symbol) or _today()
                return (
                    self.history_sql.get_date_prev(stock_id, reference_date)
                    or _today()
                )
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

    def _read_stockamount(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionAmount()``（``_editstockoptionform.php:82``）::

            if ($strGroupItemId = SqlGetMyStockGroupItemId($strLoginId, $strStockId))
            {   $amount_sql = new GroupItemAmountSql();
                return $amount_sql->ReadAmount($strGroupItemId);
            }
            return '出错了';

        读的是**当前登录会员**那一行：``member.email`` → ``member.id`` →
        分组名恰好等于符号的那个 ``stockgroup`` → 组里这只标的的
        ``stockgroupitem.id`` → ``groupitemamount.num``（读不到回落
        ``'100000'``）。

        与 PHP 的一处显示偏离：找不到分组项时 PHP 往输入框里填一句
        ``出错了``，照样让人提交（提交再被静默丢掉）。本版给空值 + 一段
        说明，并且**不摆表单** —— 见 :meth:`_member_notice`。
        """
        item_id = self._member_item_id(stock_id)
        if not item_id:
            return ""
        return GroupItemAmountStore(self.connection).read_amount(item_id)

    def _member_email(self) -> str:
        """当前身份（登录邮箱）。

        账号体系暂缓，身份来自 :func:`web.services.admin_auth.current_admin_email`
        —— 与其它写入口**同一个来源**，接登录态时只改那一处。
        """
        try:
            from web.services.admin_auth import current_admin_email

            return current_admin_email() or ""
        except Exception:  # pragma: no cover - 没有 flask 上下文之类
            return ""

    def _member_item_id(self, stock_id: Any) -> Any:
        """``SqlGetMyStockGroupItemId(SqlGetIdByEmail($email), $stockId)``。"""
        email = self._member_email()
        if not email:
            return None
        member_id = sql_get_id_by_email(self.source, email)
        if not member_id:
            return None
        return sql_get_my_stock_group_item_id(self.source, member_id, stock_id)

    def _member_notice(self, symbol: str, stock_id: Any) -> str:
        """会员级那一项为什么不能写 —— 三句话分开说，别混成一句"出错了"。"""
        email = self._member_email()
        if not email:
            return (
                "没有登录身份，不知道要改谁的申购金额。本地演示环境默认取"
                "管理员邮箱（web/services/admin_auth.py）。"
            )
        if self._member_item_id(stock_id):
            return ""
        member_id = sql_get_id_by_email(self.source, email)
        if not member_id:
            return (
                "member 表里没有 %s 这个邮箱，取不到会员 id。本地演示库的会员邮箱是"
                " demo@palmmicro.com（dbtools/seed_stock_group_tables.py 灌的），"
                "把 PALMMICRO_ADMIN_EMAIL 设成它就能试这一项。" % email
            )
        return (
            "%s 名下没有「分组名 = %s」的分组，或者那个分组里没有这只标的 —— "
            "申购金额挂在 stockgroupitem 那一行上，缺行就没有可写的对象"
            "（PHP 在这种情况下静默什么都不做）。" % (email, symbol)
        )

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

    def _read_calibration(self, symbol: str, stock_id: Any, date: str) -> str:
        """``_getStockOptionCalibration()``（``_editstockoptionform.php:212``）。

        **填的是"对方标的"的值，不是库里已存的校准值** —— PHP 就是这么做的：
        管理员看着对方的净值/点位，改一个数，提交后服务层再按
        ``估算值 × 中间价 / 基金净值`` 换算成校准值。照抄。

        三支来源按 PHP 顺序（``_editstockoptionform.php:213-227``）：

        1. ``fundpair`` 有记录 → 配对标的（``GetPairRef()``）；
        2. 否则 QDII 四地区 → est 标的（``GetEstRef()``）；
        3. 否则 ``INDA`` → ``znb_SENSEX``。

        三支都没有就给 ``'对方净值'``（``'对方'.STOCK_DISP_NETVALUE``）。
        """
        est_symbol = self._calibration_est_symbol(symbol)
        if not est_symbol:
            return "对方" + DISPLAY_NET_VALUE
        return self._best_est_net_value(est_symbol, date)

    def _calibration_est_symbol(self, symbol: str) -> Any:
        """「对方的符号」：配对标的 / est 标的 / INDA 的 ``znb_SENSEX``。"""
        #: PHP ``if ($ref = StockGetFundPairReference($strSymbol))`` ——
        #: 门禁是 ``fundpair`` 表里有没有这一行，取到的是**配对标的**。
        pair = self._fund_pair().get_pair_symbol(symbol)
        if pair:
            return pair
        region = self._qdii_region(symbol)
        if region == "us":
            return qdii_est_symbol(symbol)
        if region == "hk":
            return qdii_hk_est_symbol(symbol)
        if region == "jp":
            return qdii_jp_est_symbol(symbol)
        if region == "eu":
            return qdii_eu_est_symbol(symbol)
        #: ``else if ($strSymbol == 'INDA') $est_ref = new MyStockReference('znb_SENSEX');``
        if symbol == "INDA":
            return "znb_SENSEX"
        return None

    def _best_est_net_value(self, est_symbol: str, date: str) -> str:
        """``_getBestEstNetValue()``（``_editstockoptionform.php:199``）：三级兜底。

        净值（``netvaluehistory``）→ 该日收盘（``stockhistory``）→ 昨收。

        **不走引用对象**：``$ref->GetClose($strDate)`` 在"日期就是引用当天"时会
        返回实时价，那要先抓一次行情。表单初值不值得为此联网 —— 库里最近一条
        与实时价的差别只在"当天还没收盘"这一种情形，而这条路径只在日期恰好
        等于最新行情日时才会碰到。
        """
        est_id = self._stock_id(est_symbol)
        if not est_id or not date:
            return ""
        value = self.net_value_sql.get_close(est_id, date)
        if value is None:
            value = self.history_sql.get_close(est_id, date)
        if value is None:
            value = self.history_sql.get_close_prev(est_id, date)
        return "" if value is None else _number_text(value)

    @staticmethod
    def _qdii_region(symbol: str) -> Any:
        """四地区判定，**顺序照抄** ``StockGetQdiiReference()``（``php/stock.php:306``）。

        顺序是语义的一部分：先 ``in_arrayQdii`` 再 Hk/Jp/Eu。反过来写，
        同时落在两个数组里的符号会拿到不同地区的中间价（USCNY vs HKCNY）。
        """
        if in_array_qdii(symbol):
            return "us"
        if in_array_qdii_hk(symbol):
            return "hk"
        if in_array_qdii_jp(symbol):
            return "jp"
        if in_array_qdii_eu(symbol):
            return "eu"
        return None

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

    def _write_calibration(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateStockOptionCalibration()``（``_submitstockoptions.php:340``）。

        ## 两段完全不同的语义

        **空值**（含 ``'0'``，见 :func:`_php_empty`）不只是删那一天的校准行：
        PHP 先删 ``lastcalibration`` 那一行（``LastCalibrationSql::DeleteById``，
        **整只标的一条、不分日期**），再由 ``_updateOptionDailySql`` 按日期删
        ``calibrationhistory``。少删前者的话，估值链路会拿旧校准值继续兜底，
        页面上看着像"删了没生效"。

        **有值**时填的是**估算值**，要换算成校准值才落库：

        ======================================  ==================================
        符号                                     换算
        ======================================  ==================================
        境内指数基金（``in_arrayChinaIndex``）   ``估算值 ÷ 基金净值``（汇率取 1.0）
        QDII 四地区                               ``估算值 × 地区中间价 ÷ 基金净值``
        ======================================  ==================================

        ``QdiiGetCalibration($strEst, $strCNY, $strNetValue)`` 只有一行
        ``floatval($strEst) * floatval($strCNY) / floatval($strNetValue)``
        （``php/stock/qdiiref.php:3``）。

        ## 三支没迁的特例（一律拒绝写入，不猜）

        * ``INDA`` / ``ASHR`` —— PHP 先 ``YahooGetNetValue($ref)`` 抓官方净值，
          再用 ``CalcFactor`` 换算。抓取链路没迁，拿库里的旧净值算会得到错值；
        * ``hf_CHA50CFD`` —— 用期货 VWAP（``$ref->GetVal()``）换算，同理；
        * 商品期货基金（``in_arrayChinaFuture``）—— PHP 自己也是
          ``DebugString('unhandled China future symbol'); return;``，没有写入。

        这三支在本版都返回可读的错误，**不会**退化成"用别的数硬算"。

        ## 一处容错偏离：分母为 0 不写

        PHP 在基金净值取不到时 ``floatval(false)`` = 0，除零得到 ``INF``，
        ``strval(INF)`` 是 ``"INF"``，非 strict 模式下 ``double`` 列收下就变
        **0** —— 校准值变 0 等于把这只基金的估值整条打废，而且没有报错。
        本版直接拒绝并说清缺什么。
        """
        if not date:
            return {"ok": False, "message": "日期不能为空。"}
        calibration = self._calibration_sql()
        if _php_empty(value):
            self._last_calibration().delete_by_id(stock_id)
            deleted = calibration.delete_by_date(stock_id, date)
            return {
                "ok": True,
                "message": "%s：已删除校准记录（%d 行）与 lastcalibration。" % (date, deleted),
            }

        #: 先判符号能不能换算。PHP 是"先读净值、再按符号分支"，读净值没有副作用，
        #: 换成先分支只是为了缺什么就报什么（INDA 缺的是抓取链路，不是净值）。
        cny, error = self._calibration_cny(symbol, date)
        if error:
            return {"ok": False, "message": error}
        net_value = self.net_value_sql.get_close(stock_id, date)
        if not net_value:
            return {
                "ok": False,
                "message": "%s 在 %s 没有净值记录 —— 换算是「估算值 × 中间价 ÷ 净值」，"
                           "没有分母算不出来，没有写入。" % (symbol, date),
            }
        try:
            factor = qdii_get_calibration(float(value), float(cny), float(net_value))
        except (TypeError, ValueError, ZeroDivisionError):
            return {"ok": False, "message": "估算值或净值不是数字，没有写入。"}
        written = calibration.write_daily(stock_id, date, factor)
        if not written:
            return {
                "ok": True,
                "message": "%s：算出来的校准值是 %s，与库里那条相同（或落在周末闸门），未写入。"
                           % (date, _number_text(factor)),
            }
        return {
            "ok": True,
            "message": "%s：估算值 %s × 中间价 %s ÷ 净值 %s = 校准值 %s，已写入。"
                       % (date, value.strip(), _number_text(cny), _number_text(net_value),
                          _number_text(factor)),
        }

    def _calibration_cny(self, symbol: str, date: str) -> tuple:
        """换算校准值要乘的中间价；返回 ``(值, 错误信息)``。

        错误信息非空就表示"这一支不能写"，此时值恒为 ``None`` —— 不返回 1.0
        之类"看起来合理"的假值。

        对应 PHP 那段 if-else 链（``_submitstockoptions.php:362-390``）：
        ``in_arrayChinaFuture`` → 直接 return；``in_arrayChinaIndex`` → ``'1.0'``；
        四个 QDII 地区各取自己的中间价；其余 → unhandled。
        """
        if symbol in ("INDA", "ASHR"):
            return None, (
                "%s 的校准值要先用 Yahoo 官方净值换算（PHP 先 YahooGetNetValue 再 "
                "CalcFactor），那条抓取链路还没迁，本页不写。" % symbol
            )
        if symbol == "hf_CHA50CFD":
            return None, (
                "hf_CHA50CFD 的校准值用期货 VWAP 换算（CalcFactor 的另一支），"
                "行情源没迁，本页不写。"
            )
        if in_array_china_future(symbol):
            return None, (
                "%s 是商品期货基金，PHP 在这个页面上也是 unhandled China future "
                "symbol 直接 return，没有写入。" % symbol
            )
        if in_array_china_index(symbol):
            #: PHP ``$strCNY = '1.0';`` —— 境内指数基金不乘汇率。
            return 1.0, ""
        region = self._qdii_region(symbol)
        if region is None:
            return None, (
                "%s 既不是境内指数基金，也不在 QDII 四个地区清单里，"
                "PHP 也是 unhandled other symbol，没有写入。" % symbol
            )
        cny_symbol = QDII_CNY_SYMBOLS[region]
        cny_id = self._stock_id(cny_symbol)
        cny = self.net_value_sql.get_close(cny_id, date) if cny_id else None
        if not cny:
            #: PHP ``if ($strCNY == false) return;`` —— 静默返回、一行不写。
            #: 本版给一句能操作的话（中间价靠 ``forexcli`` 每天补）。
            return None, (
                "%s 在 %s 没有中间价记录，算不出校准值 —— 先跑 forexcli 补中间价，"
                "或换一个日期。" % (cny_symbol, date)
            )
        return cny, ""

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

    def _write_stockamount(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateFundPurchaseAmount()``（``_submitstockoptions.php:70``）::

            $strMemberId = SqlGetIdByEmail($strEmail);
            $strStockId = SqlGetStockId($strSymbol);
            if ($strMemberId && $strStockId && is_numeric($strVal))
            {   if ($strGroupItemId = SqlGetMyStockGroupItemId($strMemberId, $strStockId))
                {   $amount_sql = new GroupItemAmountSql();
                    $amount_sql->WriteString($strGroupItemId, $strVal);
                }
            }

        ## 三处与其它 15 项不一样的地方

        1. **不是管理员专属**。``Process()`` 的 switch 里只有这一支没有
           ``if ($bAdmin)`` —— 它改的是登录会员**自己**名下那一行
           （``stockgroupitem`` → ``groupitemamount``），越权不了别人。
           权限闸门因此放行到"有登录身份"这一层（见路由里的 ``member_level``）；
        2. **没有日期**（``DATE_NONE``），也没有"清空 = 删除"：``WriteVal``
           在字符串模式下**不做删除**，而 ``is_numeric('')`` 是假，空值
           在进门时就被挡掉了 —— 所以这一项**删不掉**记录，只能改数字；
        3. **找不到分组项就什么都不做**（不是报错）。PHP 的返回值是
           ``void``，页面看起来"提交成功了"，其实一个字节没写。本版把三种
           原因分开说清楚（没身份 / member 表里没这个邮箱 / 没有同名分组项）。

        ``'0'`` 是**合法值**（写 0），与日频表那套 ``empty('0')`` 为真的
        语义**不同** —— 金额表走的是 ``WriteString``，不是 ``_updateOptionDailySql``。
        """
        email = self._member_email()
        if not email:
            return {"ok": False, "message": "没有登录身份，没有写入。"}
        member_id = sql_get_id_by_email(self.source, email)
        if not member_id:
            return {"ok": False, "message": self._member_notice(symbol, stock_id)}
        text = value.strip()
        #: ``is_numeric()`` 的口径：可选前导空白、正负号、小数、指数都算数字，
        #: 空串与 ``'abc'`` 不算。**不**接受十六进制（PHP 7+ 已废弃）。
        if not _php_is_numeric(text):
            return {
                "ok": False,
                "message": "申购金额要是一个数字（PHP 那边 is_numeric 为假就整笔跳过），"
                           "没有写入。",
            }
        item_id = sql_get_my_stock_group_item_id(self.source, member_id, stock_id)
        if not item_id:
            return {"ok": False, "message": self._member_notice(symbol, stock_id)}
        #: ``WriteString`` 是字符串比较：库里 ``'100000'`` 与提交的 ``'100000'``
        #: 相同 → 返回 False（"没变"），不 UPDATE。``'0.0'`` 与 ``'0'`` 在
        #: ``int`` 列上都是 0，但字符串比较会认为变了 → 照样 UPDATE 一次。
        changed = GroupItemAmountStore(self.connection).write_string(item_id, text)
        if not changed:
            return {
                "ok": True,
                "message": "%s：申购金额已经就是 %s（分组项 %s），没有变化。"
                           % (symbol, text, item_id),
            }
        return {
            "ok": True,
            "message": "%s：申购金额（分组项 %s）写成 %s。" % (symbol, item_id, text),
        }

    def _write_stockdividend(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateStockOptionDividend()``（``_submitstockoptions.php:301``）。

        ## 三段副作用，次序照抄

        ``stockdividend`` 表只存"每股派现"一行，真正的负担在后面：

        1. **校准值**：把 ``calibrationhistory`` 里**该日之前**那一行的校准值
           按"净值下降"重算 —— 分红让净值掉了 ``D``，同一个配对关系下
           估值因子必须跟着变，否则一除就跳空；
        2. **自身净值**：``netvaluehistory`` 的 ``date_prev`` 减去 ``D``；
        3. **复权价**：``stockhistory`` 里 ``date <= 该日`` 的全部 ``adjclose``
           减去 ``D``，**当天那一行除外**（``if ($record['date'] != $strYMD)``）——
           当天已经是除权后的价格，再减一次等于重复扣。

        与拆股（:meth:`_write_stocksplit`）的次序相同，差别只有"减"与"除"，
        以及**要不要排除当天**：拆股连当天一起除，分红不碰当天。

        ## 只有"真的写了分红行"才重算

        PHP 把整段副作用包在 ``if (_updateOptionDailySql(...))`` 里，于是：

        * 值没变（``MIN_FLOAT_VAL`` 内）→ 不重算；
        * 落在周末闸门 → 不重算；
        * **清空（含 ``'0'``）删行 → 也不重算**。

        最后那条是照抄的硬伤：删掉分红以后复权价不会涨回去、净值也不会加回来。
        这一版修不了 —— 删行那一刻"当初减了多少"只剩 ``stockdividend`` 里那一行，
        而它正是要被删掉的东西。

        ## 一处刻意偏离：QDII 分支不写净值

        PHP::

            if ($strClosePrev = $cal_sql->GetClosePrev($strStockId, $strDate))
            {   // 本标的分支
                $fNetValue = floatval($net_sql->GetClose($strStockId, $strDatePrev));
                ...
            }
            else
            {   foreach ($arQdii as $strQdii)
                {   if ($strClosePrev = $cal_sql->GetClosePrev($strQdiiId, $strDate))
                    {   $strDatePrev = $cal_sql->GetDatePrev($strQdiiId, $strDate);
                        $fNetValue = floatval($net_sql->GetClose($strStockId, $strDatePrev));
                        ...
                    }
                }
            }
            if ($strClosePrev)  $net_sql->WriteDaily($strStockId, $strDatePrev, strval($fNewNetValue));

        最后一行用的是**循环变量**：走 QDII 分支时 ``$strDatePrev`` 与
        ``$fNewNetValue`` 来自最后一个命中的 QDII，却写进**本标的**的净值表。
        本标的（SPY / QQQ 这类 ETF）在 ``netvaluehistory`` 里经常没有那一天，
        ``GetClose`` 给 ``false`` → ``floatval`` 是 0 → 会凭空插一条
        ``-分红值`` 的**负净值**。本版只在本标的分支写净值。

        ## 另一处偏离：分母取不到时整笔不写

        与 :meth:`_write_calibration` 同一个理由（``floatval(false)`` = 0）：
        PHP 7 算出 ``INF`` 写进 ``double`` 列变 0，PHP 8 抛
        ``DivisionByZeroError`` 整页 500。本版把两处取数（本标的净值 /
        各 QDII 净值）提前到写库之前，缺了直接拒绝，一行都不动。

        注意 ``_dividend_plan`` 里两支的公式**分子分母正好相反**
        （``close × net ÷ new`` 与 ``close × new ÷ net``）—— 那是 PHP 原文的
        样子，照抄不动。
        """
        if not date:
            return {"ok": False, "message": "日期不能为空。"}
        dividend_sql = StockDividendSql(self.connection)
        if _php_empty(value):
            deleted = dividend_sql.delete_by_date(stock_id, date)
            return {
                "ok": True,
                "message": "%s：已删除分红记录（%d 行）。复权价与净值**没有**回滚 —— "
                           "PHP 的重算整段写在「写成功」分支里，删行不重算。"
                           % (date, deleted),
            }
        text = value.strip()
        if not _is_number(text):
            return {"ok": False, "message": "分红要是一个数字（每股派现），没有写入。"}
        dividend = float(text)

        #: 先算好要改哪些行（全是只读）。取不到分母就整笔不写 —— 见 docstring。
        plan, error = self._dividend_plan(symbol, stock_id, date, dividend)
        if error:
            return {"ok": False, "message": error}

        if not dividend_sql.write_daily(stock_id, date, text):
            return {
                "ok": True,
                "message": "%s：%s 与库里那条相同（或落在周末闸门），未写入，也没有重算。"
                           % (date, text),
            }

        cal = self._calibration_sql()
        changed = []
        for target_id, date_prev, factor in plan["calibration"]:
            if cal.write_daily(target_id, date_prev, _number_text(factor)):
                changed.append("%s=%s" % (date_prev, _number_text(factor)))
        net_date = new_net_value = None
        if plan["net"] is not None:
            net_date, new_net_value = plan["net"]
        net_written = False
        if net_date:
            net_written = self.net_value_sql.write_daily(
                stock_id, net_date, _number_text(new_net_value))
        adjusted = self._recalc_adjclose(stock_id, date, dividend, by_ratio=False)
        unlink_config_file(symbol)

        parts = ["%s：已写入分红 %s。" % (date, text)]
        if changed:
            parts.append("校准值改了 %d 行（%s）。" % (len(changed), "，".join(changed)))
        if net_date:
            parts.append("净值 %s 改为 %s%s。" % (
                net_date, _number_text(new_net_value), "" if net_written else "（无变化，未写）"))
        parts.append("复权价重算 %d 行（当天那一行按定义不减）。" % adjusted)
        return {"ok": True, "message": "".join(parts)}

    def _dividend_plan(self, symbol: str, stock_id: Any, date: str, dividend: float) -> tuple:
        """分红要改的校准行与净值行，全是只读；返回 ``(plan, 错误信息)``。

        ``plan`` 形如::

            {"calibration": [(stock_id, date_prev, factor), ...],
             "net": (date_prev, new_net_value) | None}

        ``net`` 只有**本标的分支**才给值 —— 见 :meth:`_write_stockdividend`
        里那段"QDII 分支不写净值"的说明。
        """
        cal = self._calibration_sql()
        close_prev = cal.get_close_prev(stock_id, date)
        if close_prev is not None:
            #: ``get_date_prev`` 给的是 ``datetime.date``（pymysql 原生类型），
            #: 统一成 ``'YYYY-MM-DD'`` 再往下传 —— 消息里、SQL 参数里、对照
            #: 脚本里就都是同一个形状。
            date_prev = _date_text(cal.get_date_prev(stock_id, date))
            net_value = self.net_value_sql.get_close(stock_id, date_prev)
            new_net_value = self._net_value_after(net_value, dividend)
            if new_net_value is None:
                return None, (
                    "%s 在 %s 没有基金净值，或分红把净值扣成了 0 —— 校准值是 "
                    "「昨收 × 原净值 ÷ 新净值」，分母不成立，没有写入。"
                    % (symbol, date_prev)
                )
            factor = float(close_prev) * float(net_value) / new_net_value
            return ({"calibration": [(stock_id, date_prev, factor)],
                     "net": (date_prev, new_net_value)}, "")

        #: QDII 分支：本标的自己没有校准行，说明它是别人的配对标的
        #: （``QdiiGetArray($strSymbol)`` 找出把本标的当 est 的基金）。
        changes = []
        for qdii_symbol in qdii_symbols_for_est(symbol):
            qdii_id = self._stock_id(qdii_symbol)
            if not qdii_id:
                continue
            qdii_close_prev = cal.get_close_prev(qdii_id, date)
            if qdii_close_prev is None:
                continue
            qdii_date_prev = _date_text(cal.get_date_prev(qdii_id, date))
            #: PHP 这里取的是 **$strStockId（本标的）** 的净值，不是 QDII 的。
            net_value = self.net_value_sql.get_close(stock_id, qdii_date_prev)
            new_net_value = self._net_value_after(net_value, dividend)
            if new_net_value is None:
                continue
            #: 分子分母与上面那一支**相反**（``new ÷ net`` 而不是 ``net ÷ new``）。
            #: 两支是分别写的，PHP 原文如此 —— 照抄。
            factor = float(qdii_close_prev) * new_net_value / float(net_value)
            changes.append((qdii_id, qdii_date_prev, factor))
        return ({"calibration": changes, "net": None}, "")

    @staticmethod
    def _net_value_after(net_value: Any, dividend: float) -> Any:
        """``$fNewNetValue = $fNetValue - floatval($strVal)``；取不到或扣成 0 给 ``None``。

        PHP 那边取不到是 ``floatval(false)`` = 0，减完是负数，再当分母除 ——
        ``INF`` / ``DivisionByZeroError`` 二选一。这里的 ``None`` 只是把
        "算不出来"与"算出来是 0"区分开，两个都拒绝。
        """
        if net_value is None:
            return None
        try:
            value = float(net_value)
        except (TypeError, ValueError):
            return None
        new_value = value - float(dividend)
        if not value or not new_value:
            return None
        return new_value

    def _write_stocksplit(self, symbol: str, stock_id: Any, date: str, value: str) -> dict:
        """``_updateStockOptionSplit()``（``_submitstockoptions.php:253``）。

        ## 比例与"删除"的判据

        ``$ar = explode(':', $strVal)``，只认**前两段**（``1:2:3`` 的第三段被
        丢掉）。``$ar[0] == '0' && $ar[1] == '0'`` 是删行，用的是 PHP 8 的
        松散比较 —— 两个数字串按**数值**比，所以 ``0.0:0``、``00:0`` 都算删除。
        本版用 :func:`sql.historytable.php_text_equal` 复刻这个双轨。

        ``0:0`` 之外的比例一律 ``floatval(a) / floatval(b)``：
        ``'abc'`` 的 ``floatval`` 是 0，于是 ``10:abc`` 会变成除零 —— 本版在
        算之前就拒绝（PHP 8 在这里抛 ``DivisionByZeroError`` 整页 500，
        而且那时 ``stocksplit`` 行**已经插进去了**）。

        ## 四段副作用

        1. **交易流水**：给每个持有该标的的成员补两笔 —— 按昨收价卖出全部股数、
           再按 ``昨收 ÷ 比例`` 买回 ``intval(比例 × 股数)`` 股，然后回填成员
           预统计（``quantity/cost/record``）。只在**真的插入**了拆股行时才做
           （``if ($sql->InsertDaily(...))``）；
        2. **校准值**：本标的有校准行 → ``昨收 × 比例`` 写回 ``date_prev``；
           否则遍历把它当 est 的 QDII 基金 → ``昨收 ÷ 比例``；
        3. **复权价**：``date <= 该日`` 的全部 ``adjclose`` 除以比例，
           **连当天一起除**（与分红相反）；
        4. 删掉 SMA 配置文件（``unlinkConfigFile``）。

        ## 重复提交同一天会**再除一次**

        ``InsertDaily`` 是"已有就返回 false"，但**校准与复权价的重算写在 if 外面**。
        于是同一天提交两次 ``10:1``，复权价会被除两次（1/100），校准值也被
        乘两次。流水倒是不会重复插。这是 PHP 的行为，照抄 —— 只在返回消息里
        说清"当天已有记录、本次没有新增"，免得管理员以为没生效又点一次。
        """
        if not date:
            return {"ok": False, "message": "日期不能为空。"}
        text = value.strip()
        if ":" not in text:
            #: PHP ``if (strpos($strVal, ':') === false) return;`` —— 静默返回，
            #: 页面上一点变化都没有。本版给一句话。
            return {"ok": False,
                    "message": "拆股/合股要写成「拆:合」（例如 10:1 表示 1 股拆 10 股），"
                               "值里没有冒号，没有写入。"}
        parts = text.split(":")
        split_sql = StockSplitSql(self.connection)
        if php_text_equal(parts[0], "0") and php_text_equal(parts[1], "0"):
            deleted = split_sql.delete_by_date(stock_id, date)
            return {
                "ok": True,
                "message": "%s：已删除拆股记录（%d 行）。复权价、校准值与交易流水"
                           "**都没有**回滚 —— PHP 的 0:0 分支只删这一行。"
                           % (date, deleted),
            }

        ratio, error = self._split_ratio(parts)
        if error:
            return {"ok": False, "message": error}

        existing = split_sql.get_record(stock_id, date)
        will_insert = existing is None
        #: 流水要先知道昨收当单价。PHP 是 ``floatval(GetClosePrev())`` ——
        #: 取不到就写 0 价交易。本版在写任何东西之前拒绝。
        items = self._split_transaction_items(stock_id) if will_insert else []
        price_text = None
        if items:
            price_text = self.history_sql.get_close_prev(stock_id, date)
            if price_text is None:
                return {
                    "ok": False,
                    "message": "%s 在 %s 之前没有行情记录，取不到昨收价 —— 拆股流水要按"
                               "昨收价成交，没有写入。" % (symbol, date),
                }

        inserted = bool(split_sql.insert_daily(stock_id, date, _number_text(ratio))) \
            if will_insert else False
        trans_count = 0
        if inserted and items:
            trans_count = self._insert_split_transactions(items, ratio, price_text)

        cal = self._calibration_sql()
        changed = []
        close_prev = cal.get_close_prev(stock_id, date)
        if close_prev is not None:
            date_prev = _date_text(cal.get_date_prev(stock_id, date))
            factor = float(close_prev) * ratio
            if cal.write_daily(stock_id, date_prev, _number_text(factor)):
                changed.append("%s=%s" % (date_prev, _number_text(factor)))
        else:
            for qdii_symbol in qdii_symbols_for_est(symbol):
                qdii_id = self._stock_id(qdii_symbol)
                if not qdii_id:
                    continue
                qdii_close_prev = cal.get_close_prev(qdii_id, date)
                if qdii_close_prev is None:
                    continue
                qdii_date_prev = _date_text(cal.get_date_prev(qdii_id, date))
                #: 与上一支的乘除方向相反，PHP 原文如此。
                factor = float(qdii_close_prev) / ratio
                if cal.write_daily(qdii_id, qdii_date_prev, _number_text(factor)):
                    changed.append("%s=%s" % (qdii_date_prev, _number_text(factor)))

        adjusted = self._recalc_adjclose(stock_id, date, ratio, by_ratio=True)
        unlink_config_file(symbol)

        head = ("%s：已写入拆股比例 %s（%s）。" % (date, _number_text(ratio), text)
                if inserted else
                "%s：当天已有拆股记录，没有新增（比例仍是库里的 %s）。"
                % (date, _number_text(existing.get("close")) if existing else "?"))
        parts = [head]
        if not will_insert:
            parts.append("注意：校准与复权价按 PHP 口径又重算了一遍。")
        if trans_count:
            parts.append("交易流水补了 %d 个成员。" % trans_count)
        if changed:
            parts.append("校准值改了 %d 行（%s）。" % (len(changed), "，".join(changed)))
        parts.append("复权价重算 %d 行（含当天）。" % adjusted)
        return {"ok": True, "message": "".join(parts)}

    @staticmethod
    def _split_ratio(parts: list) -> tuple:
        """``$fRatio = floatval($ar[0]) / floatval($ar[1])``；返回 ``(比例, 错误信息)``。

        两道 PHP 会在**写完之后**才炸的闸门都提到前面：分母为 0、比例为 0
        （后者会让"复权价 ÷ 比例"除零）。
        """
        try:
            numerator = float(parts[0])
            denominator = float(parts[1])
        except (TypeError, ValueError):
            return None, "比例的两段都要是数字（例如 10:1），没有写入。"
        if denominator == 0:
            return None, ("比例的分母是 0 —— PHP 8 在这里抛 DivisionByZeroError "
                          "整页 500，本页不写。")
        ratio = numerator / denominator
        if ratio == 0:
            return None, ("比例算出来是 0（%s:%s），后面「复权价 ÷ 比例」会除零，"
                          "PHP 也是 500，本页不写。" % (parts[0], parts[1]))
        return ratio, ""

    def _split_transaction_items(self, stock_id: Any) -> list:
        """``_updateStockOptionSplitTransactions()`` 的前半段：谁持有这只标的。

        PHP 先 ``new TableSql(TABLE_STOCK_GROUP)`` 取**全部**分组（不是当前
        登录会员那几组），再逐组 ``GetStockIdArray(true)``（跳过
        ``record == 0`` 的成员）看有没有这只标的。返回
        ``[(StockGroupItemSql, 该成员在组里的行)]``。
        """
        items = []
        for group in StockGroupSql(self.connection).get_data():
            item_sql = StockGroupItemSql(self.connection, group["id"])
            if str(stock_id) not in set(item_sql.get_stock_id_array(True).values()):
                continue
            record = item_sql.get_record(stock_id)
            if record is None:
                continue
            items.append((item_sql, record))
        return items

    def _insert_split_transactions(self, items: list, ratio: float, price_text: Any) -> int:
        """补"卖出 + 按新比例买回"两笔流水，再回填成员预统计。

        PHP（``_updateStockOptionSplitGroupTransactions``）::

            $iQuantity = intval($record['quantity']);
            $sql->trans_sql->Insert($strGroupItemId, strval(0 - $iQuantity), strval($fPrice));
            $sql->trans_sql->Insert($strGroupItemId, strval(intval($fRatio * $iQuantity)),
                                    strval($fPrice / $fRatio));
            UpdateStockGroupItemTransaction($sql, $strGroupItemId);

        两笔是**配平**的：卖出 ``Q`` 股、买回 ``intval(ratio × Q)`` 股，
        金额一侧差出来的部分由价格 ``price`` 与 ``price / ratio`` 承担，
        所以 ``cost`` 不变、``quantity`` 变成新股数。

        ``intval`` 是**向零截断**：3 股按 1.5 倍拆只给 4 股（0.5 股被吞）。
        交易表只记整数股，这是 PHP 的口径。
        """
        price = float(price_text)
        count = 0
        for item_sql, record in items:
            item_id = record["id"]
            quantity = _intval(record["quantity"])
            trans_sql = item_sql.trans_sql
            trans_sql.insert(item_id, str(0 - quantity), _number_text(price))
            trans_sql.insert(item_id, str(_intval(ratio * quantity)),
                             _number_text(price / ratio))
            update_stock_group_item_transaction(item_sql, item_id)
            count += 1
        return count

    def _recalc_adjclose(self, stock_id: Any, date: str, amount: float,
                         by_ratio: bool) -> int:
        """拆股/分红共用的复权序列重算；返回真的改了值的行数。

        ``GetFromDate($strKeyId, $strDate)`` 的 WHERE 是 ``date <= $strDate``
        （``sqldailyclose.php:43``）—— 取**该日及之前**的全部历史。拆股/分红
        之后要按新口径重算的是"过去"，不是"未来"：新价格是从除权日开始
        往后用的，历史价格要倒着折算。

        两种口径唯一的结构差异：**分红跳过当天那一行**
        （``if ($record['date'] != $strYMD)``），拆股不跳。
        """
        changed = 0
        for row in self.history_sql.get_from_date(stock_id, date):
            #: ``row['date']`` 是 ``datetime.date``（pymysql 对 ``date`` 列的
            #: 原生类型），直接与 ``'YYYY-MM-DD'`` 字符串比**永远不等** ——
            #: 分红那天会被多减一次。`str()` 一下的唯一原因。
            if not by_ratio and _date_text(row["date"]) == date:
                continue
            try:
                adjclose = float(row["adjclose"] or 0)
            except (TypeError, ValueError):
                continue
            value = adjclose / amount if by_ratio else adjclose - amount
            self.history_sql.update_adjclose(row["id"], _number_text(value))
            changed += 1
        return changed

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

        三处用到：``_write_fund`` 的删除分支（``DeleteAll($stockId)`` 把这只
        标的的全部校准记录清掉，因为配对换了以后旧校准值全部失去意义）、
        ``_write_stockdividend`` 与 ``_write_stocksplit``（改历史校准值）。
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
