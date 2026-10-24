"""持仓的更新编排与管理员写入口。

对应 PHP 两个文件：

* ``woody/res/php/_qdiimix.php`` 的 ``_QdiiMixAccount::_updateStockHoldings()``
  —— 页面渲染时顺带更新的编排：净值日期推进了就补持仓（LOF 只补日期，
  ETF 从交易所下载申赎清单整份替换）；
* ``woody/res/submitholdings.php`` 的 ``_AdminHoldingsAccount::AdminProcess()``
  + ``woody/res/php/_updateholdings.php`` 的 ``UpdateStockOptionHoldings()``
  —— 管理员提交编码串写回明细与仓位。

## 两条写入路径的分工（按基金类型，PHP 就是这么分的）

===========================  ==================================================
``in_arrayLofMix($symbol)``   QDII-LOF-FOF 一族（含黄金/原油 LOF）。交易所
                              不公布申赎清单，**只推进持仓日期**：
                              ``CheckHoldingsDate($strNetValueDate)`` 确认每只
                              成分股在净值日都有收盘价，就 ``WriteDate``。
                              明细本身靠管理员提交（见下）。
否则                          QDII-ETF。走 ``FundEstSql`` 的日期闸门与
                              9:30–14:55 的时间窗，从沪/深交易所下载申赎清单
                              （``updater/etfholdings.py``）。
===========================  ==================================================

## 架构偏差（有意的）

PHP 的 ``_updateStockHoldings()`` 挂在**页面渲染**里（``Create()`` 的第一步），
靠"分钟级下载缓存 + 时间窗"兜住频率。本项目的既定架构是
**realtime 抓取只归 daemon，web 只读快照**（见 ``README_p0_data_layer.md``），
所以这个编排不放路由里，由 ``cli/holdingscli.py`` 定时调用；
PHP 的日期/时间闸门原样保留（换个调用方不改变语义）。

管理员页（``submitholdings.php``）的**登录闸门**没有对应物 —— 账号体系
（``stockgroup`` 三张表）按用户要求暂缓，见
``python/auto/README_holdings_page_migration.md`` 的偏差清单。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.stocksymbol import (
    in_array_hk_mix,
    in_array_lof_mix,
    stock_get_symbol,
)
#: PHP 那边叫 ``FundEstSql``（``GetFundEstSql()``），本项目的落点叫
#: ``FundEstStore``（``sql/fundeststore.py``）；``GetDateNow()`` 对应
#: ``get_latest_date()``。
from sql.fundeststore import FundEstStore
from sql.holdingstable import HoldingsDateSql, HoldingsSql
from sql.historytable import NetValueHistorySql
from sql.positiontable import FundPositionSql
from sql.stocksql import StockSql
from updater.etfholdings import read_sse_holdings, read_szse_holdings

# ---------------------------------------------------------------------------
# 小工具（PHP 口径）
# ---------------------------------------------------------------------------


def _call(ref: Any, name: str, *args: Any) -> Any:
    """调 ref 的方法；没有该方法或抛异常时返回 ``None``。

    与 PHP 的行为对齐：PHP 里这些库调用取不到就是 ``false``，不会让页面挂。
    **异常在这里被吞掉是有意的**，但只用于"取数"，不用于"写库"。
    """
    method = getattr(ref, name, None)
    if method is None:
        return None
    try:
        return method(*args)
    except Exception:
        return None


def _connection_of(source: Any) -> Any:
    """从数据源取连接；没有就抛 —— 写路径不允许"静默不写"。"""
    connection = getattr(source, "connection", None)
    if connection is None:
        raise ValueError("持仓写入需要一个带 connection 的数据源")
    return connection


def debug_decode(text: Any) -> Optional[dict]:
    """对应 PHP ``DebugDecode()``（``php/debug.php:88``）：::

        return json_decode('{'.$strJson.'}', true);

    入参是**去掉花括号的 JSON 对象体**（``"GLD":100,"SPY":50``），包上一层
    再解。解析失败返回 ``None``（PHP 的 ``json_decode`` 给 ``null``，
    而调用方 ``if ($ar = ...)`` 把 null 当假值）。

    空对象体（``''``）在 PHP 里解出 ``[]`` —— **同样是假值**，调用方一样
    不进分支。所以这里空 dict 也返回 ``None``，两种"空"对调用方同形。
    """
    if text is None:
        return None
    try:
        value = json.loads("{%s}" % str(text))
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and value else None


def _php_floatval(value: Any) -> float:
    """PHP ``floatval`` 口径：解析不了给 0.0，不抛错。"""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _php_str_ne_zero(text: Any) -> bool:
    """PHP 8 的 ``$str != '0'`` 语义。

    * 两个**数字串**按数值比（``'0.0' != '0'`` 为假 —— 会被跳过）；
    * 非数字串按字符串比（``'abc' != '0'`` 为真 —— 会进来，随后
      ``floatval`` 成 0.0）。

    管理员表单里比例列偶尔会是空串或 ``'0.00'``，两种都要走对。
    """
    try:
        return float(text) != 0.0
    except (TypeError, ValueError):
        return str(text) != "0"


def _check_symbol(text: Any) -> Any:
    """对应 PHP ``StockAccount::StockCheckSymbol()``
    （``woody/res/php/_stockaccount.php:71``）：长度 <= 11、不含单引号、
    去掉尾部斜杠；不合法返回 ``None``（PHP 返回 ``false``）。
    """
    if text is None:
        return None
    text = str(text)
    if len(text) > 11:
        return None
    if "'" in text:
        return None
    return text.rstrip("/")


# ---------------------------------------------------------------------------
# 管理员写入口（``submitholdings.php`` + ``_updateholdings.php``）
# ---------------------------------------------------------------------------


def stock_option_decode_holdings(text: Any, stock_sql: Any = None,
                                 source: Any = None) -> Tuple[Optional[float], Dict[Any, float]]:
    """对应 PHP ``StockOptionDecodeHoldings($strVal, &$arRatio)``
    （``woody/res/php/_updateholdings.php:3``）。

    解出 ``(fPos, {holding_id: ratio})``：**比例被归一化成合计 100**，
    ``fPos`` 是归一化前的合计（百分数 / 100）。

    归一化口径照抄 PHP（``_updateholdings.php:21-25``）::

        $fPos /= 100.0;
        foreach ($arRatio as $strHoldingId => $fRatio)
            $arRatio[$strHoldingId] = $fRatio / $fPos;

    所以 ``"GLD":100,"SPY":50`` 进来，``fPos = 1.5``，两条比例变成
    ``66.666…`` 与 ``33.333…``（**不是原值**）。

    ==============================  ==========================================
    PHP 行为                        本版
    ==============================  ==========================================
    ``DebugDecode`` 失败            ``(None, {})``
    比例为 ``'0'`` / ``'0.0'``     跳过（PHP 8 数字串按数值比）
    符号不在 ``stock`` 表           **不写该行**，但仍计入 ``fPos``
    ==============================  =

    **与 PHP 的差异（两处，都朝安全方向）**：

    1. PHP 对 ``GetId()`` 取不到的符号会把 ``false`` 当数组键（转成 0），
       最后插一行 ``holding_id = 0`` 的明细。本版不写这行 —— 表单里的符号
       本来就该来自库内清单，真出现了也是数据错误。但 **``fPos`` 照算**
       （PHP 的 ``$fPos += $fRatio`` 在查 id 之后、与写行无关）。
    2. 一条有效比例都没有时 PHP 返回 ``0.0``（调用方 ``if ($fPos = ...)``
       当假值，同样不写库）；本版返回 ``(None, {})``。写库行为一致，
       只是返回值形制不同。另外 PHP 在 ``$fPos`` 为 0 时归一化会
       ``DivisionByZeroError``，本版按"没有数据"处理。
    """
    decoded = debug_decode(text)
    if decoded is None:
        return None, {}
    if stock_sql is None:
        stock_sql = StockSql(_connection_of(source))
    ratios: Dict[Any, float] = {}
    total = 0.0
    for holding, ratio in decoded.items():
        if not _php_str_ne_zero(ratio):
            continue
        symbol = stock_get_symbol(holding)
        value = _php_floatval(ratio)
        #: fPos 先加再查 id —— PHP 的累加在 ``$arRatio[...] = $fRatio``
        #: 之后，与 id 取没取到无关（见上方差异表第 1 条）。
        total += value
        holding_id = stock_sql.get_id(symbol)
        if holding_id is None:
            continue
        ratios[holding_id] = value
    if not ratios:
        return None, {}
    position = total / 100.0
    if position == 0.0:
        # PHP 在这里会 1/0 抛 DivisionByZeroError；全是 0 比例的表单没有意义，
        # 当作"没有数据"处理（调用方同样不会写库）。
        return None, {}
    for holding_id in list(ratios):
        ratios[holding_id] = ratios[holding_id] / position
    return position, ratios


def update_stock_option_holdings(source: Any, stock_id: Any, date: Any, text: Any,
                                 position_sql: Any = None, date_sql: Any = None,
                                 holdings_sql: Any = None, stock_sql: Any = None) -> bool:
    """对应 PHP ``UpdateStockOptionHoldings($strStockId, $strDate, $strVal)``
    （``woody/res/php/_updateholdings.php:26``）。

    顺序照抄：**先写仓位、再写持仓日期、最后整份替换明细**。顺序有意义 ——
    中途失败时留下"新仓位 + 旧明细"比"新明细 + 旧仓位"更容易被下一次
    更新纠正（仓位影响估值，明细影响测算表）。

    ``stock_sql`` 是给测试/对照脚本的注入口（解码要查符号 id），与
    ``position_sql`` 一族一致。

    返回 ``True`` 表示真的写了（PHP 无返回值，这是为测试加的口径）。
    """
    position, ratios = stock_option_decode_holdings(
        text, stock_sql=stock_sql, source=source
    )
    if position is None:
        return False
    connection = _connection_of(source)
    if position_sql is None:
        position_sql = FundPositionSql(connection)
    if date_sql is None:
        date_sql = HoldingsDateSql(connection)
    if holdings_sql is None:
        holdings_sql = HoldingsSql(connection)
    position_sql.write_pos(stock_id, position)
    date_sql.write_date(stock_id, date)
    holdings_sql.delete_all(stock_id)
    for holding_id, ratio in ratios.items():
        from ref.stockref import _php_strval_float

        holdings_sql.insert(stock_id, holding_id, _php_strval_float(ratio))
    return True


def process_admin_holdings(source: Any, symbol: Any, params: Any,
                           holdings_ref_builder: Callable[[Any], Any] = None) -> dict:
    """对应 PHP ``_AdminHoldingsAccount::AdminProcess()``
    （``woody/res/submitholdings.php:8``）。

    ``params`` 是查询参数映射（``{'holdings': ..., 'fundposition': ...}``）。
    返回 ``{"symbol", "stock_id", "updated", "position"}`` 便于调用方记账。

    **登录闸门没有迁**：PHP 的 ``AdminRun()`` 外层还有 ``IsAdmin()``
    （账号体系暂缓），所以这个方法本身**不做任何权限检查** —— 接入 Web
    路由前必须先补上管理员校验，否则就是一个未鉴权的写入口。
    """
    result = {"symbol": symbol, "stock_id": None, "updated": False, "position": None}
    symbol = _check_symbol(symbol)
    if not symbol:
        return result
    stock_sql = StockSql(_connection_of(source))
    stock_id = stock_sql.get_id(symbol)
    if not stock_id:
        return result
    result["stock_id"] = stock_id

    holdings = params.get("holdings") if hasattr(params, "get") else None
    if holdings:
        #: PHP 在这里**新建一个 HoldingsReference**（而不是复用页面那个普通
        #: 引用），为的是读 ``GetHoldingsDate()`` —— 写入的日期就是库里
        #: 现有的持仓日，表单只改比例不改日期。
        if holdings_ref_builder is not None:
            ref = holdings_ref_builder(symbol)
        else:
            from ref.holdingsref import StockGetHoldingsReference

            ref = StockGetHoldingsReference(symbol, source=source)
        date = _call(ref, "get_holdings_date")
        if date:
            result["updated"] = update_stock_option_holdings(source, stock_id, date, holdings)

    position = params.get("fundposition") if hasattr(params, "get") else None
    if position:
        FundPositionSql(_connection_of(source)).write_val(stock_id, position)
        result["position"] = position
    return result


# ---------------------------------------------------------------------------
# 页面/定时任务编排（``_QdiiMixAccount::_updateStockHoldings``）
# ---------------------------------------------------------------------------


def _trace(trace: Any, message: str) -> None:
    """往诊断列表里追一行（``trace`` 为 ``None`` 时静默）。"""
    if trace is not None:
        trace.append(message)


def update_stock_holdings(ref: Any, fetch: Callable[[str], Optional[bytes]] = None,
                          source: Any = None, dry_run: bool = False,
                          trace: Optional[List[str]] = None) -> bool:
    """对应 PHP ``_QdiiMixAccount::_updateStockHoldings($strSymbol)``
    （``woody/res/php/_qdiimix.php:46``）。

    ``ref`` 是**持仓型基金的引用**（``HoldingsReference``）。返回 ``True``
    表示明细或日期被更新过 —— 调用方据此重建引用（PHP 是
    ``unset($this->ref); $this->ref = new HoldingsReference(...)``）。

    ``dry_run=True`` 时**只诊断不写**（``cli/holdingscli.py`` 的默认模式）：
    闸门照算，但到了写库/下载那一步就停。``trace`` 传一个 list 进来会逐条
    录下闸门判定 —— 诊断与写库因此共用同一份闸门逻辑，不会两处漂移。

    四道闸门照抄（任何一道不过就直接返回，不写任何东西）：

    1. ``净值日 == 持仓日`` —— 已经最新；
    2. ``持仓日 == 官方日期``（``GetOfficialDate()``）—— 官方 EST 已经用了
       新净值，再补一次会是同一天重复写；
    3. ETF 支线还有：``est 日 == 净值日``（``fundest`` 还没到）、
       ``est 日 == 行情日``（"a day too early"，港股混合族豁免）、
       时间窗 9:30–14:55（沪 open 前不取、收盘后不取）；
    4. LOF 支线：``CheckHoldingsDate($strNetValueDate)`` —— 每只成分股在
       净值日都有收盘价才推进日期。
    """
    if source is None:
        source = getattr(ref, "source", None)
    if source is None:
        raise ValueError("update_stock_holdings 需要一个带 connection 的数据源")

    symbol = _call(ref, "get_symbol")
    stock_id = _call(ref, "get_stock_id")
    if not symbol or not stock_id:
        _trace(trace, "取不到符号或 stock_id，跳过")
        return False

    connection = _connection_of(source)
    netvalue_sql = NetValueHistorySql(connection)
    date_sql = HoldingsDateSql(connection)

    net_value_date = netvalue_sql.get_date_now(stock_id)
    holdings_date = date_sql.read_date(stock_id)
    if net_value_date == holdings_date:
        _trace(trace, "净值日 %s == 持仓日 %s，已最新" % (net_value_date, holdings_date))
        return False                                   # 闸门 1：已经最新
    if holdings_date == _call(ref, "get_official_date"):
        _trace(trace, "持仓日 %s == 官方日期 %s，官方 EST 已用新净值"
                % (holdings_date, _call(ref, "get_official_date")))
        return False                                   # 闸门 2：官方 EST 已用新净值

    updated = False
    if in_array_lof_mix(symbol):
        #: LOF 支线：交易所不公布申赎清单，只能推进持仓**日期**。
        #: 明细保持原样，等管理员在 submitholdings 页提交。
        if net_value_date != holdings_date:
            if _call(ref, "check_holdings_date", net_value_date):
                _trace(trace, "LOF 支线：成分股在净值日 %s 都有收盘价，将推进持仓日期"
                        "（当前 %s）" % (net_value_date, holdings_date))
                if dry_run:
                    return False
                updated = bool(date_sql.write_date(stock_id, net_value_date))
            else:
                _trace(trace, "LOF 支线：成分股在净值日 %s 缺收盘价，不推进日期"
                        % net_value_date)
    else:
        #: ETF 支线：从沪/深交易所下载申赎清单，整份替换 holdings。
        fund_est_store = FundEstStore(connection)
        est_date = fund_est_store.get_latest_date(stock_id)
        if est_date == net_value_date:
            _trace(trace, "est 日 %s == 净值日 %s，fundest 还没到"
                    % (est_date, net_value_date))
            return False                               # fundest 还没到
        quote_date = _call(ref, "get_date")
        if not in_array_hk_mix(symbol):
            if est_date == quote_date:
                _trace(trace, "est 日 %s == 行情日 %s，a day too early"
                        % (est_date, quote_date))
                return False                           # A day too early
        hour_minute = _call(ref, "get_hour_minute") or 0
        if int(hour_minute) < 930:
            _trace(trace, "行情时刻 %s < 9:30，数据还没更新" % hour_minute)
            return False                               # Data not updated until 9:30
        if int(hour_minute) > 1455:
            _trace(trace, "行情时刻 %s > 14:55，收盘后停止自动更新" % hour_minute)
            return False                               # Stop autoupdate after market close
        if _call(ref, "is_shanghai_etf"):
            _trace(trace, "沪市 ETF：将下载申赎清单整份替换持仓（行情日 %s）" % quote_date)
            if dry_run:
                return False
            updated = read_sse_holdings(source, symbol, stock_id, fetch=fetch)
        elif _call(ref, "is_shenzhen_etf"):
            _trace(trace, "深市 ETF：将下载申赎清单整份替换持仓（行情日 %s）" % quote_date)
            if dry_run:
                return False
            updated = read_szse_holdings(source, symbol, stock_id, quote_date, fetch=fetch)
        else:
            _trace(trace, "既不是沪市也不是深市 ETF，无处下载")
    return updated


def update_stock_holdings_for_symbol(source: Any, symbol: str,
                                     fetch: Callable[[str], Optional[bytes]] = None,
                                     dry_run: bool = False,
                                     trace: Optional[List[str]] = None) -> bool:
    """按符号跑 :func:`update_stock_holdings`（CLI 用的薄封装）。

    先建 ``HoldingsReference``（``holdings`` 表里没有明细的基金直接返回
    ``False`` —— PHP 那边 ``_qdiimix.php`` 只对 qdiimix 族建账，判据一致），
    更新后**重建引用**，与 PHP 的 ``unset + new`` 等价。
    """
    from ref.holdingsref import StockGetHoldingsReference

    ref = StockGetHoldingsReference(symbol, source=source)
    if ref is None:
        _trace(trace, " holdings 表里没有明细（或不在 qdiimix 族），不建账")
        return False
    updated = update_stock_holdings(ref, fetch=fetch, source=source,
                                    dry_run=dry_run, trace=trace)
    if updated:
        #: PHP: ``unset($this->ref); $this->ref = new HoldingsReference($strSymbol)``
        #: 后续 CreateGroup 用的就是新数据。CLI 侧没有"后续"，所以只重建不返回。
        StockGetHoldingsReference(symbol, source=source)
    return updated


def qdiimix_holdings_symbols(source: Any = None) -> List[str]:
    """需要跑持仓更新的符号清单：``qdii_mix`` 一族。

    对应 PHP 的覆盖面：``_qdiimix.php`` 只服务走 ``require('php/_qdiimix.php')``
    的那些页面（QDII-LOF 混合 / QDII-ETF / 中概 / 港股混合 / MSCI 美国50），
    即 ``core.stocksymbol.qdii_mix_get_symbol_array()``。
    """
    from core.stocksymbol import qdii_mix_get_symbol_array

    return list(qdii_mix_get_symbol_array())
