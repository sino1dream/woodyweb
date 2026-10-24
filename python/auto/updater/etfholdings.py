"""ETF 持仓申赎清单的下载与落库（沪市 XML / 深市 PCF 文本）。

对应 PHP（``woody/res/php/`` 下三个文件，一个字不改地搬过来）：

* ``_holdingscsvfile.php`` —— ``_HoldingsCsvFile``：把一行持仓写进
  ``holdings`` 表的公共基类（建符号 → 查旧价 → 插明细）；
* ``_etfholdings.php`` —— ``_EtfHoldingsFile``：现金与比例的计算
  （``ratio = 100 × 市值 / 现金``）；
* ``_sseholdings.php`` / ``_szseholdings.php`` —— 两个交易所的下载入口
  ``ReadSseHoldingsFile()`` / ``ReadSzseHoldingsFile()``。

## 数据长什么样

**沪市**（``query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=513050``）
是一份 XML，一次给全：::

    <PreTradingDay>20261021</PreTradingDay>        ← 持仓日（T-1，要 ConvertYMD）
    <NAVperCU>977582.66</NAVperCU>                  ← 分母（AddCash）
    <EstimatedCashComponent>-15.43</EstimatedCashComponent>   ← 分子修正（**PHP 读不到它**，见 SseHoldingsFile.parse_xml 的笔误说明）
    <ComponentList><Component>
      <InstrumentID>ATHM</InstrumentID>            ← 成分股代码
      <InstrumentName>ATHM</InstrumentName>
      <SubstitutionCashAmount>1474.53</SubstitutionCashAmount>   ← 市值口径
    </Component>...

**深市**（``reportdocs.static.szse.cn/files/text/etf/ETF15960520250901.txt``）
是一份 GB2312 编码的申购赎回清单（PCF），按行扫：

* ``YYYY-MM-DD日 信息内容`` —— 日期行，**取第一张表（T-1）的日期**；
* ``现金差额：  820.72元`` / ``最小申购、赎回单位资产净值：  1100754.99元``
  —— 两个冒号键值行（``SubCash`` / ``AddCash``）；
* ``证券代码 证券简称 股份数量 …`` —— 表头行，见到它才认为进入持仓区
  （同时``DeleteAllHoldings()``）；
* 之后每行按空格切，市值 = ``股份数量 × 当日复权价 × 汇率``。

## 与 PHP 的结构差异（有意的）

PHP 是"下载到 ``debug/csv/`` 缓存文件 → ``CsvFile`` 逐行 ``fgets`` →
``OnLineArray`` 回调"。本版**没有文件中间层**：下载函数拿字节、解析函数
收文本，行循环原样保留（``trim`` → 跳过空行 → 切分 → 回调），所以
``_SzseHoldingsFile.on_line_array`` 可以与 PHP 的 ``OnLineArray`` 逐行对照。

PHP 的下载缓存（``StockSaveDebugFile`` 的分钟级间隔）也没有对应物 ——
那是给"每次页面渲染都可能触发下载"用的闸门；本版的调用方是
``cli/holdingscli.py``（定时任务），不构成热循环。
"""

from __future__ import annotations

import csv
import io
import random
from typing import Any, Callable, List, Optional

#: ``updater → sql / core / ref`` 都是允许的依赖方向（见各包 ``__init__``）。
from sql.holdingstable import HoldingsDateSql, HoldingsSql
from sql.historytable import NetValueHistorySql, StockHistorySql
from sql.stocksql import StockSql

# ---------------------------------------------------------------------------
# 交易所 URL（对应 ``php/externalurl.php:130-142``）
# ---------------------------------------------------------------------------

#: 沪市申赎清单：``https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=513050``
SSE_HOLDINGS_URL = (
    "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=%s"
)
#: 深市 PCF 目录：``https://reportdocs.static.szse.cn/files/text/etf/``
SZSE_HOLDINGS_URL = "https://reportdocs.static.szse.cn/files/text/etf/%s?random=%s"


def sse_holdings_url(symbol: str) -> str:
    """沪市申赎清单 URL。

    PHP（``_sseholdings.php:10``）是 ``GetSseUrl('query')
    .'etfDownload/downloadETF2Bulletin.do?fundCode='.substr($strSymbol, 2, 6)``
    —— 去掉 ``SH`` / ``SZ`` 前缀取 6 位数字。
    """
    return SSE_HOLDINGS_URL % symbol[2:8]


def convert_ymd(date: Any) -> Any:
    """对应 PHP ``ConvertYMD()``（``php/class/year_month_day.php:4``）：
    ``'20261021'`` -> ``'2026-10-21'``。

    就是按固定位切三刀，不做任何校验（PHP 也不做）—— 输入不够 8 位时
    PHP 的 ``substr`` 给短串，这里同样给短串，由调用方自己面对。
    """
    text = "" if date is None else str(date)
    if len(text) < 8:
        return text
    return "%s-%s-%s" % (text[0:4], text[4:6], text[6:8])


def szse_etf_file_name(digit: str, date: Any) -> str:
    """深市 PCF 文件名，对应 PHP ``GetSzseEtfFileName()``（``externalurl.php:130``）::

        'ETF'.$strDigitA.str_replace('-', '', $strDate).'.txt'

    ``date`` 为空时 PHP 返回 ``''``（调用方随后必然失败）—— 照抄。
    """
    if not date:
        return ""
    return "ETF%s%s.txt" % (digit, str(date).replace("-", ""))


def szse_holdings_url(file_name: str) -> str:
    """深市 PCF 下载 URL，对应 PHP ``GetSzseHoldingsUrl()``（``externalurl.php:136``）。

    ``random`` 查询参数是 PHP 的缓存破坏招（``1.0 * rand() / getrandmax()``），
    这里用 ``random.random()``，语义一样：让 CDN 不要给旧文件。
    """
    return SZSE_HOLDINGS_URL % (file_name, random.random())


def php_is_numeric(text: Any) -> bool:
    """PHP ``is_numeric()`` 的口径（仅覆盖本模块用到的形态）。

    PHP 认：可选符号 + 数字（允许前导零与千分位外的形式）+ 可选小数 +
    可选科学计数法，**允许开头空白**（PHP 8 起）。所以::

        is_numeric('01024')  === true    ← 港股代码补零后仍是"数字串"
        is_numeric('00700')  === true
        is_numeric('GLD')    === false
        is_numeric('')       === false

    这个判定直接决定 ``GetMarketVal`` 用港元还是美元汇率（见
    ``_HoldingsCsvFile::GetMarketVal``），所以必须与 PHP 一致。
    """
    if text is None:
        return False
    if isinstance(text, (int, float)):
        return True
    text = str(text)
    #: PHP 8 只允许**开头**空白，尾随空白直接判非数字（``is_numeric('12 ')``
    #: 为假）—— 交易所文件里字段常常带尾随空格，这里放行会把市值算错。
    lead = text.lstrip(" \t\n\r\v\f")
    if lead != text:
        return False
    if not lead:
        return False
    try:
        float(lead)
    except ValueError:
        return False
    # Python 的 float() 比 PHP 宽松（'inf'/'nan'/'1_0' 都认），逐个排掉
    lowered = lead.lower().lstrip("+-")
    if lowered.startswith(("inf", "nan", "infinity")):
        return False
    if "_" in lead:
        return False
    return True


def _php_floatval(value: Any) -> float:
    """PHP ``floatval`` 口径：解析不了给 0.0，不抛错。"""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _php_intval(value: Any) -> int:
    """PHP ``intval`` 口径：取字符串开头的整数部分，解析不了给 0。"""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "").strip().lstrip("+-")
    digits = ""
    for char in text:
        if char.isdigit():
            digits += char
        else:
            break
    if not digits:
        return 0
    sign = -1 if str(value).strip().startswith("-") else 1
    return sign * int(digits)


def _php_rtrim_chars(text: str, chars: str) -> str:
    """PHP ``rtrim($str, $chars)``：去掉尾部任意个 ``chars`` 里的字符。"""
    if not text:
        return text
    end = len(text)
    while end > 0 and text[end - 1] in chars:
        end -= 1
    return text[:end]


def _php_strval_float(value: Any) -> str:
    """PHP ``strval(float)``（14 位有效数字、削尾零）—— 落库的比例串走它。"""
    from ref.stockref import _php_strval_float as _impl

    return _impl(value)


def _str_getcsv(line: str, separator: str) -> List[str]:
    """PHP ``str_getcsv($line, $separator)`` 的最小对应。

    深市 PCF 用的分隔符是 ``'?'``（``_szseholdings.php`` 的
    ``SetSeparator('?')``）—— 文件里没有问号，所以整行就是**一个**元素，
    后面的解析全靠 ``explode`` 手工切。引号处理交给 ``csv`` 模块，
    与 PHP 一致（``str_getcsv`` 也会剥掉双引号）。
    """
    if len(separator) != 1:
        # PHP 的 str_getcsv 只收单字符分隔符；多字符时行为未定义，这里直接整体切
        return line.split(separator)
    try:
        rows = list(csv.reader([line], delimiter=separator))
    except csv.Error:
        return line.split(separator)
    return rows[0] if rows else [line]


def _php_array_filter(values: List[str]) -> List[str]:
    """PHP ``array_filter``（无回调）：剔掉**假值**元素。

    对字符串而言假值只有 ``''`` 和 ``'0'`` —— ``'0'`` 也会被剔掉，
    深市持仓行的"股份数量"列如果是 ``0``，后面的列索引会整体前移。
    这是 PHP 的真实行为，照抄（见 pitfalls 里"PHP 数组键/假值"系列）。
    """
    return [value for value in values if value not in ("", "0")]


# ---------------------------------------------------------------------------
# 基类：对应 ``_HoldingsCsvFile`` + ``_EtfHoldingsFile``
# ---------------------------------------------------------------------------

class EtfHoldingsFile:
    """申赎清单落库器：现金、比例、持仓行、持仓日都从这里写。

    对应 PHP 的 ``_HoldingsCsvFile``（``woody/res/php/_holdingscsvfile.php``）
    与 ``_EtfHoldingsFile``（``_etfholdings.php``）。两个类在本版合并成一个：
    PHP 拆开是因为中间隔着一个 ``CsvFile``（磁盘文件读取），本版没有那一层
    （见模块 docstring）。

    所有数据库访问都走**可注入的 sql 对象**，默认按 ``source.connection``
    现造 —— 与 ``holdingsref`` 的写法一致，测试里传假连接即可。
    """

    def __init__(self, source: Any, stock_id: Any,
                 stock_sql: Any = None, holdings_sql: Any = None,
                 date_sql: Any = None, history_sql: Any = None,
                 netvalue_sql: Any = None) -> None:
        self.source = source
        self.stock_id = stock_id
        self._stock_sql = stock_sql
        self._holdings_sql = holdings_sql
        self._date_sql = date_sql
        self._history_sql = history_sql
        self._netvalue_sql = netvalue_sql

        #: ``$fSum``：各行市值之和（``DebugVal`` 的对账用）
        self.f_sum = 0.0
        #: ``$fCash``：分母。沪市 = NAVperCU - EstimatedCashComponent；
        #: 深市 = 最小申购赎回单位资产净值 - 现金差额
        self.f_cash = 0.0
        #: ``$strDate``：持仓日（T-1）。深市还有 ``false`` 三态（没见到日期行），
        #: Python 用 ``None``
        self.str_date: Any = None
        #: ``CalcCurrency()`` 的三个汇率
        self.f_usdcny = 0.0
        self.f_hkdcny = 0.0
        self.f_usdhkd = 0.0
        #: ``DebugReport`` 收集的告警（PHP 最后 ``TriggerReport`` 一次性抛出）
        self.report: List[str] = []

    # -- sql 对象（懒造，与 holdingsref 同款） ------------------------------
    @property
    def stock_sql(self) -> Any:
        if self._stock_sql is None:
            self._stock_sql = StockSql(_connection_of(self.source))
        return self._stock_sql

    @property
    def holdings_sql(self) -> Any:
        if self._holdings_sql is None:
            self._holdings_sql = HoldingsSql(_connection_of(self.source))
        return self._holdings_sql

    @property
    def date_sql(self) -> Any:
        if self._date_sql is None:
            self._date_sql = HoldingsDateSql(_connection_of(self.source))
        return self._date_sql

    @property
    def history_sql(self) -> Any:
        if self._history_sql is None:
            self._history_sql = StockHistorySql(_connection_of(self.source))
        return self._history_sql

    @property
    def netvalue_sql(self) -> Any:
        if self._netvalue_sql is None:
            self._netvalue_sql = NetValueHistorySql(_connection_of(self.source))
        return self._netvalue_sql

    # -- 现金与合计 ---------------------------------------------------------
    def add_cash(self, value: Any) -> None:
        """对应 PHP ``AddCash()``：``fCash += fVal``。"""
        self.f_cash += _php_floatval(value)

    def sub_cash(self, value: Any) -> None:
        """对应 PHP ``SubCash()``：``fCash -= fVal``。"""
        self.f_cash -= _php_floatval(value)

    def get_cash(self) -> float:
        """对应 PHP ``GetCash()``。"""
        return self.f_cash

    def add_sum(self, value: Any) -> None:
        """对应 PHP ``AddSum()``。"""
        self.f_sum += _php_floatval(value)

    def get_sum(self) -> float:
        """对应 PHP ``GetSum()``。"""
        return self.f_sum

    # -- 日期 ---------------------------------------------------------------
    def get_date(self) -> Any:
        """对应 PHP ``GetDate()``：没设过返回 ``None``（PHP 是 ``false``）。"""
        return self.str_date

    def set_date(self, date: Any) -> None:
        """对应 PHP ``SetDate()``。

        深市子类在这里**顺带算汇率**（``CalcCurrency``）—— PHP 的多态写法，
        见 :meth:`SzseHoldingsFile.set_date`。
        """
        self.str_date = date

    # -- 汇率（``CalcCurrency``，``_holdingscsvfile.php:70``） ----------------
    def calc_currency(self, date: Any) -> None:
        """取 ``USCNY`` / ``HKCNY`` 在 ``date`` 的中间价，算 ``fUSDHKD``。

        PHP::

            $str = ($strUSDCNY = $net_sql->GetClose($strUscnyId, $strDate)) ? $strUSDCNY : $net_sql->GetCloseNow($strUscnyId);

        **取不到当天就退回最新一条**（``GetCloseNow``）—— 中间价偶尔晚一天
        公布，没有这个回落整个深市清单的市值都会按 0 汇率算。
        """
        uscny_id = self.stock_sql.get_id("USCNY")
        hkcny_id = self.stock_sql.get_id("HKCNY")
        usd = self.netvalue_sql.get_close(uscny_id, date)
        if not usd:
            usd = self.netvalue_sql.get_close_now(uscny_id)
        hkd = self.netvalue_sql.get_close(hkcny_id, date)
        if not hkd:
            hkd = self.netvalue_sql.get_close_now(hkcny_id)
        self.f_usdcny = _php_floatval(usd)
        self.f_hkdcny = _php_floatval(hkd)
        self.f_usdhkd = self.f_usdcny / self.f_hkdcny if self.f_hkdcny else 0.0

    # -- 市值（``GetMarketVal``，``_holdingscsvfile.php:115``） ---------------
    def get_market_val(self, holding: str, quantity: Any) -> float:
        """``数量 × 当日复权价 × 汇率``。

        汇率判据是 ``is_numeric($strHolding)``：**港股代码补零后仍是数字串**
        （``'01024'``），所以走港元；美股代码（``'GLD'``）走美元。
        这个判定在 PHP 里就是一行三元式，本版用 :func:`php_is_numeric`
        复刻（见那里的说明）。
        """
        stock_id = self.stock_sql.get_id(holding)
        if not stock_id:
            self.report.append("GetMarketVal failed with %s" % holding)
            return 0.0
        forex = self.f_hkdcny if php_is_numeric(holding) else self.f_usdcny
        adjclose = self.history_sql.get_adjclose(stock_id, self.str_date)
        return _php_floatval(quantity) * _php_floatval(adjclose) * forex

    # -- 写持仓（``InsertHolding``，``_holdingscsvfile.php:22``） -------------
    def insert_holding(self, symbol: str, name: Any, ratio: Any) -> Any:
        """把一只成分股写进 ``holdings`` 表。

        三步：``InsertSymbol``（没有这个符号就建行）→ ``GetId`` →
        ``InsertHoldingId``。**旧价缺失只告警不中断**：PHP 在
        ``GetRecord(...) == false`` 时调 ``DebugReport``，然后照样插 ——
        缺旧价那一行在测算表里会显示成 0，但清单本身是完整的。
        """
        self.stock_sql.insert_symbol(symbol, name)
        holding_id = self.stock_sql.get_id(symbol)
        if not holding_id:
            return None
        if self.history_sql.get_record(holding_id, self.str_date) is None:
            self.report.append("%s missing data on %s" % (symbol, self.str_date))
        return self.holdings_sql.insert(self.stock_id, holding_id, ratio)

    def delete_all_holdings(self) -> Any:
        """对应 PHP ``DeleteAllHoldings()``。"""
        return self.holdings_sql.delete_all(self.stock_id)

    # -- 收尾（``UpdateHoldingsDate`` / ``Done``） ---------------------------
    def update_holdings_date(self) -> bool:
        """对应 PHP ``UpdateHoldingsDate()``：有日期才写 ``holdingsdate``。"""
        if self.str_date:
            return bool(self.date_sql.write_date(self.stock_id, self.str_date))
        return False

    def done(self) -> bool:
        """对应 PHP ``Done()``：写了持仓日才算成功。

        PHP 在这里 ``DebugVal(round(GetSum() - fCash, 3))`` 对账 ——
        市值之和与分母应当相等（差是成分股停牌一类）。本版把对账值
        收进 :attr:`report`，由调用方决定要不要看。
        """
        if self.update_holdings_date():
            self.report.append("Done sum-cash=%s" % round(self.get_sum() - self.f_cash, 3))
            return True
        return False

    # -- ETF 口径（``_EtfHoldingsFile``） ------------------------------------
    def add_holding(self, holding: str, name: Any, value: Any) -> Any:
        """对应 PHP ``AddHolding($strHolding, $strName, $fVal)``。

        比例 = ``100 × 市值 / 现金``，**以字符串落库**（PHP ``strval``）——
        库里 ``ratio`` 列是 ``DOUBLE(13,6)``，存字符串让 MySQL 自己截断，
        与 PHP 走同一条路。
        """
        self.add_sum(value)
        ratio = _php_strval_float(100.0 * _php_floatval(value) / self.f_cash)
        return self.insert_holding(holding, name, ratio)

    def convert_holding(self, holding: str) -> str:
        """对应 PHP ``ConvertHolding()``（``_etfholdings.php:48``）。

        纯数字按长度分：``<= 5`` 位是港股（补零到 5 位，如 ``700`` ->
        ``00700``），否则按 A 股规则加市场前缀；非数字把 ``/`` 换成 ``.``
        （``BRK/B`` -> ``BRK.B``）。
        """
        if php_is_numeric(holding):
            if len(holding) <= 5:
                from core.stocksymbol import build_hongkong_stock_symbol

                return build_hongkong_stock_symbol(holding)
            from core.stocksymbol import build_china_stock_symbol

            return build_china_stock_symbol(holding)
        return str(holding).replace("/", ".")


def _connection_of(source: Any) -> Any:
    """从数据源取连接；没有就抛 —— 写路径不允许"静默不写"。"""
    connection = getattr(source, "connection", None)
    if connection is None:
        raise ValueError("持仓写入需要一个带 connection 的数据源")
    return connection


# ---------------------------------------------------------------------------
# 深市 PCF 解析（``_SzseHoldingsFile``）
# ---------------------------------------------------------------------------

class SzseHoldingsFile(EtfHoldingsFile):
    """深市申购赎回清单（PCF）解析器。

    对应 PHP ``_szseholdings.php`` 的 ``_SzseHoldingsFile``。行循环与
    ``OnLineArray`` 逐行照抄，两个入口差异：

    * 分隔符是 ``'?'``（PHP ``SetSeparator('?')``）—— 于是 ``str_getcsv``
      返回的"字段"其实是**整行**，真正的切分在 ``OnLineArray`` 里手工做；
    * ``SetDate`` 多一步 ``CalcCurrency``（深市要自己算市值，沪市不用）。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: ``$bUse``：见到"证券代码"表头后才认为进入持仓区
        self.b_use = False

    def set_date(self, date: Any) -> None:  # type: ignore[override]
        """PHP ``SetDate()`` 的重载：父类赋值后顺带 ``CalcCurrency($strDate)``。"""
        super().set_date(date)
        self.calc_currency(date)

    def on_line_array(self, words: List[str]) -> bool:
        """对应 PHP ``OnLineArray($arWord)``。

        入参 ``words`` 就是 ``str_getcsv(trimmed_line, '?')`` 的结果 ——
        正常只有**一个**元素（整行），多个元素说明行里真有问号（没见过）。
        """
        line = words[0] if words else ""
        #: PHP: ``explode(':', str_replace('：', ':', GbToUtf8($arWord[0])))``
        #: 全角冒号先替换成半角，再按冒号切
        parts = line.replace("：", ":").split(":")
        count = len(parts)
        if count == 2:
            value = _php_floatval(_php_rtrim_chars(parts[1].strip(), "元"))
            key = parts[0].strip()
            if key == "现金差额":
                self.sub_cash(value)
            elif key == "最小申购、赎回单位资产净值":
                self.add_cash(value)
        elif count == 1:
            fields = line.split(" ")
            if self.b_use:
                if len(fields) > 7:
                    #: PHP ``array_filter`` + ``array_values``：剔掉空串与 '0' 后重编号
                    fields = _php_array_filter(fields)
                    holding = fields[0]
                    if holding != "159900":
                        #: ``159900`` 是"申赎现金"占位行，不是真持仓
                        holding = self.convert_holding(holding)
                        quantity = _php_intval(str(fields[2]).replace(",", ""))
                        if quantity == 0:
                            value = _php_floatval(
                                str(fields[5]).replace(",", "") if len(fields) > 5 else "")
                        else:
                            value = self.get_market_val(holding, quantity)
                        self.add_holding(holding, fields[1], value)
            else:
                if fields and fields[0] == "证券代码":
                    self.b_use = True
                    self.delete_all_holdings()
                elif self.get_date() is None:
                    if len(fields) == 2 and fields[1] == "信息内容":
                        #: ``2025-08-28日 信息内容`` -> ``2025-08-28``
                        self.set_date(_php_rtrim_chars(fields[0], "日"))
        return True

    def parse_text(self, text: str) -> bool:
        """逐行喂给 :meth:`on_line_array`（对应 PHP ``CsvFile::Read()``）。

        行处理照抄：``trim`` -> 跳过空行 -> ``str_getcsv`` -> 回调。
        """
        for raw in io.StringIO(text):
            line = raw.strip()
            if not line:
                continue
            words = _str_getcsv(line, "?")
            if self.on_line_array(words) is False:
                break
        return True


# ---------------------------------------------------------------------------
# 沪市 XML 解析（``ReadSseHoldingsFile``）
# ---------------------------------------------------------------------------

class SseHoldingsFile(EtfHoldingsFile):
    """沪市申赎清单（XML）落库器。

    对应 PHP ``_sseholdings.php`` 的 ``ReadSseHoldingsFile()`` 函数体：
    ``SubCash`` -> ``SetDate`` -> ``DeleteAllHoldings`` -> ``AddCash`` ->
    逐 ``Component`` 调 ``AddHolding``。深市那套"行回调 + bUse 状态机"在
    沪市用不上（XML 是结构化数据），所以这个类只有 :meth:`parse_xml`。
    """

    def parse_xml(self, root: Any) -> bool:
        """把 ``simplexml`` 形状的根节点写进库。

        ``root`` 用 ``xml.etree.ElementTree`` 的 Element（与 PHP
        ``simplexml_load_string`` 的对象访问对应：``$xml->PreTradingDay``
        -> ``root.findtext('PreTradingDay')``）。子元素缺失时 PHP 给
        ``''`` / 报 warning，这里统一按缺失处理（``findtext`` 默认 ``None``）。
        """
        def child(name: str) -> Any:
            element = root.find(name)
            return None if element is None else (element.text or "")

        #: **照抄 PHP 的笔误**：``_sseholdings.php:12`` 读的是
        #: ``EstimateCashComponent``（少一个 ``d``），而沪市 XML 的实际标签是
        #: ``EstimatedCashComponent``。SimpleXML 取不到节点 -> 空串 ->
        #: ``floatval`` 成 0.0 -> ``SubCash(0)`` 空转 —— 也就是说**生产端
        #: 沪市持仓的分母从来就是 NAVperCU 原值**，EstimatedCashComponent
        #: 这个修正字段实际上被忽略（实测反推：PHP 侧 fCash = NAVperCU，
        #: 见 ``_scratch/_php_etfholdings_compare.py`` 的 sse_xml 用例）。
        #: 这里连标签名一起照抄：改"对"了反而与线上分叉。
        self.sub_cash(_php_floatval(child("EstimateCashComponent")))
        self.set_date(convert_ymd((child("PreTradingDay") or "").strip()))
        self.delete_all_holdings()
        self.add_cash(_php_floatval(child("NAVperCU")))

        component_list = root.find("ComponentList")
        components = [] if component_list is None else list(component_list.findall("Component"))
        for component in components:
            def sub(name: str, element: Any = component) -> Any:
                item = element.find(name)
                return None if item is None else (item.text or "")

            instrument = (sub("InstrumentID") or "").strip()
            #: PHP 对 InstrumentID / InstrumentName / SubstitutionCashAmount
            #: 都做了 trim；缺失字段按 0 / 空串处理
            self.add_holding(
                self.convert_holding(instrument),
                (sub("InstrumentName") or "").strip(),
                _php_floatval((sub("SubstitutionCashAmount") or "").strip()),
            )
        return self.done()


# ---------------------------------------------------------------------------
# 下载入口（``ReadSseHoldingsFile`` / ``ReadSzseHoldingsFile``）
# ---------------------------------------------------------------------------

#: 与 ``php/url.php:52`` 同一个 UA（``url_get_contents`` 的默认头）
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

#: PHP ``url_get_contents`` 的 curl 超时是 **2 秒**（``php/url.php:49``）——
#: 交易所内网端点，慢一点就当时取不到。这里放宽到 20 秒：Python 侧调用方是
#: 定时任务而不是页面渲染，且国内网络到 query.sse.com.cn 偶发更慢，
#: 2 秒会把大量本可成功的下载判失败。
DEFAULT_TIMEOUT = 20.0


def default_fetch(url: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[bytes]:
    """默认下载器：``requests`` + PHP 同款 UA。取不到返回 ``None``（不抛）。"""
    try:
        import requests

        response = requests.get(
            url, timeout=timeout, headers={"User-Agent": DEFAULT_USER_AGENT}
        )
        response.raise_for_status()
        return response.content
    except Exception:
        return None


def read_sse_holdings(source: Any, symbol: str, stock_id: Any,
                      fetch: Callable[[str], Optional[bytes]] = None,
                      holdings_file: Any = None) -> bool:
    """对应 PHP ``ReadSseHoldingsFile($strSymbol, $strStockId)``。

    下载沪市申赎清单 XML 并整份替换 ``holdings``。返回 ``True`` 表示
    ``Done()`` 成功（写了持仓日）—— 与 PHP 的 ``$csv->Done()`` 同义。

    ``holdings_file`` 是注入口：测试/对照脚本传一个造好的
    :class:`SseHoldingsFile`（sql 对象全是替身），省得连库。
    """
    fetcher = fetch or default_fetch
    content = fetcher(sse_holdings_url(symbol))
    if not content:
        return False
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(content)
    except Exception:
        return False
    csv_file = holdings_file if holdings_file is not None else SseHoldingsFile(source, stock_id)
    return csv_file.parse_xml(root)


def read_szse_holdings(source: Any, symbol: str, stock_id: Any, date: Any,
                       fetch: Callable[[str], Optional[bytes]] = None,
                       holdings_file: Any = None) -> bool:
    """对应 PHP ``ReadSzseHoldingsFile($strSymbol, $strStockId, $strDate)``。

    ``date`` 只用于**拼文件名**（``ETF15960520250901.txt``），持仓日以文件
    内容里的日期行为准（T-1）。深市文件是 GB2312 编码，用 ``gb18030`` 解
    （前者的超集，混进来的 GBK 字符也不会炸）。

    ``holdings_file`` 同 :func:`read_sse_holdings` 的注入口。
    """
    fetcher = fetch or default_fetch
    file_name = szse_etf_file_name(symbol[2:], date)
    if not file_name:
        return False
    content = fetcher(szse_holdings_url(file_name))
    if not content:
        return False
    try:
        text = content.decode("gb18030")
    except UnicodeDecodeError:
        return False
    csv_file = holdings_file if holdings_file is not None else SzseHoldingsFile(source, stock_id)
    csv_file.parse_text(text)
    return csv_file.done()
