"""行情预取调度层：判断哪些符号需要重新拉取新浪行情。

对应 PHP 源码：``php/stock/stockprefetch.php``，以及它依赖的时间判定函数
``php/class/year_month_day.php`` 的 ``NeedFile()``、``IsSunday()`` 等。

职责边界
--------
本模块只回答「**要不要**重新取行情」这个问题，不做实际的网络请求，也不解析
行情内容（那分别属于后续的抓取层和 ``python/auto/stockref.py``）。

因此三处外部依赖都做成可注入的：

* 文件修改时间：``file_mtime`` 回调，默认读真实文件，测试时传入内存字典；
* 数据库判断：``net_value_exists`` / ``hit_date_provider`` 回调，对应 PHP 里
  ``SqlGetNetValueByDate()`` 和 ``GetStockHistorySql()->GetDateNow()``；
* 行情文本的读写：``save_quote`` / ``load_quote`` 回调。

时区说明：PHP 在预取前会 ``$sym->SetTimeZone()`` 改进程默认时区，之后
``GetNowYMD()`` 取的是**该市场的当地时间**。Python 没有进程默认时区，所以这里
要求传入时区感知的 ``datetime``；只给朴素 ``datetime`` 时会按该符号的时区补上，
保持与 PHP 相同的语义。
"""

from __future__ import annotations

import datetime as _datetime
import os
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from stockref import SECONDS_IN_HOUR, SECONDS_IN_MIN
from stocksymbol import StockSymbol, build_china_fund_symbol
import tzutil

# 对应 PHP DebugGetSinaFileName() 所在目录；实际路径由调用方给出
DEFAULT_QUOTE_DIRECTORY = os.path.join("debug", "sina")


# ---------------------------------------------------------------------------
# 文件时间与时间点工具
# ---------------------------------------------------------------------------
def _default_file_mtime(path: str) -> Optional[int]:
    """默认的文件修改时间读取：不存在返回 ``None``（对应 PHP 的 ``false``）。"""
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return None


def _resolve_mtime(file_mtime, path: str) -> Optional[int]:
    """把 ``file_mtime`` 参数解析成修改时间。

    支持三种形式，方便测试与不同的存储后端：

    * ``None``：读真实文件系统；
    * ``dict``：内存映射（路径不存在即视为没有文件）；
    * 可调用对象：自定义查询。
    """
    if file_mtime is None:
        return _default_file_mtime(path)
    if isinstance(file_mtime, Mapping):
        value = file_mtime.get(path)
        return None if value is None else int(value)
    return file_mtime(path)


def need_file(now: _datetime.datetime, file_path: str, interval: int = SECONDS_IN_MIN, file_mtime=None) -> Any:
    """对应 PHP ``YearMonthDay::NeedFile()``。

    文件不存在、或已过期超过 ``interval`` 秒时，返回**文件修改时间**（PHP 用这个
    时间戳做后续判断）；否则返回 ``False`` 表示不需要重新拉取。

    注意返回值有两种含义：整数是「需要更新，旧文件时间是它」，``False`` 是
    「不需要」。这种 PHP 风格的三态在迁移时保留，调用方用 ``is_needs_update``
    判断更直观。
    """
    file_time = _resolve_mtime(file_mtime, file_path)
    if file_time is None:
        file_time = 1  # PHP: file_exists() ? filemtime() : 1
    if now.timestamp() < (file_time + interval):
        return False
    return file_time


def needs_update(result: Any) -> bool:
    """``need_file()`` 的三态返回值是否表示「需要更新」。"""
    return result is not False and result is not None


def _to_market_datetime(moment: _datetime.datetime, symbol: StockSymbol) -> _datetime.datetime:
    """把时间点折算到该符号所在时区。

    PHP 在预取前调用 ``SetTimeZone()``，于是 ``GetNowYMD()`` 返回市场当地时间。
    传入朴素 ``datetime`` 时按符号时区补上；传入带时区的时间点时换到该时区。
    时区对象由 ``tzutil`` 提供，Python 3.7 上也能正确切换夏令时。
    """
    time_zone_name = symbol.get_time_zone()
    if moment.tzinfo is None:
        return tzutil.localize(moment, time_zone_name)
    return moment.astimezone(tzutil.get_time_zone(time_zone_name))


# ---------------------------------------------------------------------------
# 各类「是否需要新行情」的判断
# ---------------------------------------------------------------------------
def _check_between_market_close(now: _datetime.datetime, file_time: int, weekday: int, weekend: int) -> bool:
    """对应 PHP ``_checkBetweenMarketClose()``。

    收盘后按「工作日等 N 小时、周末额外多等 M 小时」判断是否该重新取数，
    参数由调用方按市场给出（A 股 2/8、港股 3/8、其它 16/4）。
    """
    difference = int(now.timestamp()) - file_time
    weekday_index = now.weekday()  # Python: 周一 0 ... 周日 6
    if weekday_index == 6:  # 周日
        if difference > (weekday + weekend + 24) * SECONDS_IN_HOUR:
            return True
    elif weekday_index == 5:  # 周六
        if difference > (weekday + weekend) * SECONDS_IN_HOUR:
            return True
    else:
        if difference > weekday * SECONDS_IN_HOUR:
            return True
    return False


def stock_need_new_quotes(
    symbol: StockSymbol,
    file_name: str,
    now: _datetime.datetime,
    interval: int = SECONDS_IN_MIN,
    file_mtime=None,
) -> bool:
    """对应 PHP ``StockNeedNewQuotes()``：普通股票/指数是否需要新行情。

    先在「当前时刻」和「文件时间」两处判断是否处于交易时段，只要有一处是在
    交易时段就需要更新；否则按各市场的收盘等待时长判断。
    """
    file_time = need_file(now, file_name, interval, file_mtime)
    if file_time is False:
        return False  # 距离上次更新不足 interval

    market_now = _to_market_datetime(now, symbol)
    if symbol.is_stock_market_trading(market_now):
        return True

    file_moment = _datetime.datetime.fromtimestamp(int(file_time), tz=market_now.tzinfo)
    if symbol.is_stock_market_trading(file_moment):
        return True

    if market_now.day == file_moment.day:
        if symbol.is_before_stock_market(market_now.hour * 100 + market_now.minute):
            return False
        if symbol.is_after_stock_market(file_moment.hour * 100 + file_moment.minute):
            return False

    if symbol.is_symbol_a():
        return _check_between_market_close(market_now, int(file_time), 2, 8)
    elif symbol.is_symbol_h():
        return _check_between_market_close(market_now, int(file_time), 3, 8)
    return _check_between_market_close(market_now, int(file_time), 16, 4)


def is_future_market_trading(moment: _datetime.datetime) -> bool:
    """对应 PHP ``_isFutureMarketTrading()``：外盘期货的交易时段。

    只有 17:00 这一小时休息（周五从 17:00 起休、其它工作日 17 点整休），
    周六全天休、周日 18:00 之后才算开盘。
    """
    hour = moment.hour
    weekday_index = moment.weekday()
    if weekday_index == 6:  # 周日
        if hour < 18:
            return False
    elif weekday_index == 5:  # 周六
        return False
    elif weekday_index == 4:  # 周五
        if hour >= 17:
            return False
    else:
        if hour == 17:
            return False
    return True


def future_need_new_file(
    file_name: str,
    now: _datetime.datetime,
    interval: int = SECONDS_IN_MIN,
    file_mtime=None,
) -> bool:
    """对应 PHP ``FutureNeedNewFile()``：期货/外汇是否需要新行情。"""
    file_time = need_file(now, file_name, interval, file_mtime)
    if file_time is False:
        return False

    if is_future_market_trading(now):
        return True
    file_moment = _datetime.datetime.fromtimestamp(int(file_time), tz=now.tzinfo)
    if is_future_market_trading(file_moment):
        return True
    return _check_between_market_close(now, int(file_time), 23, 7)


def sina_fund_need_file(
    symbol: StockSymbol,
    file_name: str,
    now: _datetime.datetime,
    file_mtime=None,
    net_value_date: Callable[[StockSymbol], Any] = None,
    net_value_exists: Callable[[str, Any], bool] = None,
) -> bool:
    """对应 PHP ``SinaFundNeedFile()``：场外基金是否需要新净值。

    与股票不同，场外基金先看数据库里是否已经有那个日期的净值；已经有了就不需要
    再取。数据库访问做成两个回调：

    * ``net_value_date(symbol)``：对应 PHP 的
      ``$his_sql->GetDateNow()`` 配合 ``UseSameDayNetValue()``，给出应当核对的
      净值日期；
    * ``net_value_exists(symbol, date)``：对应 ``SqlGetNetValueByDate()``。

    回调缺省时退化为只按文件时间判断（30 分钟间隔），与 PHP 在无数据库时的
    行为一致。PHP 里 ``f_`` 前缀的场外基金代码会先还原成 ``SH/SZ`` 场内代码再查
    数据库，这一步也照搬。
    """
    if _resolve_mtime(file_mtime, file_name) is not None:
        check_symbol = symbol
        if symbol.is_sina_fund():
            built = build_china_fund_symbol(symbol.is_sina_fund())
            if not built:
                # PHP 在这里写日志后返回 false，表示符号无法识别
                return False
            check_symbol = StockSymbol(built)

        date_now = net_value_date(check_symbol) if net_value_date is not None else None
        if date_now is not None:
            if net_value_exists is not None and net_value_exists(check_symbol.get_symbol(), date_now):
                return False
            # 数据日期就是今天、且还没到 16:00 时，当天净值尚未公布
            if market_ymd(now, check_symbol) == date_now and hour_minute(now) < 1600:
                return False

    return needs_update(need_file(now, file_name, 30 * SECONDS_IN_MIN, file_mtime))


def market_ymd(moment: _datetime.datetime, symbol: StockSymbol) -> str:
    """取该符号所在市场的当地日期 ``Y-m-d``。"""
    return _to_market_datetime(moment, symbol).strftime("%Y-%m-%d")


def hour_minute(moment: _datetime.datetime) -> int:
    """``13:05`` -> ``1305``，对应 PHP ``TickYMD::GetHourMinute()``。"""
    return moment.hour * 100 + moment.minute


# ---------------------------------------------------------------------------
# 行情文本的保存与读取
# ---------------------------------------------------------------------------
def save_sina_data(file_name: str, data: str, directory: str = None) -> str:
    """对应 PHP ``PrefetchSaveSinaData()``：写入行情文本，返回完整路径。"""
    path = os.path.join(directory, file_name) if directory else file_name
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(data)
    return path


def load_sina_data(file_name: str, directory: str = None) -> Any:
    """对应 PHP ``PrefetchLoadSinaData()``：读行情文本，不存在返回 ``None``。"""
    path = os.path.join(directory, file_name) if directory else file_name
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 预取调度
# ---------------------------------------------------------------------------
def prefetch_sina_stock_data(
    symbols: Sequence[str],
    fetch_quotes: Callable[[Sequence[str]], Any],
    now: _datetime.datetime,
    save_quote: Callable[[str, str], Any] = None,
    file_mtime=None,
    interval: int = SECONDS_IN_MIN,
) -> list:
    """对应 PHP ``PrefetchSinaStockData()``。

    把待处理符号分成三组（场外基金 / 期货外汇 / 普通股票），挑选出真正需要更新
    的符号，一次性请求行情后按行回写缓存文件。返回无法识别的符号列表，
    与 PHP 的 ``$arUnknown`` 一致。

    ``fetch_quotes`` 对应 PHP ``GetSinaQuotes()``：接收新浪符号列表、返回按行
    分隔的行情文本（每行对应一个符号），返回假值表示本次抓取失败。
    """
    unknown = []
    prefetch: Dict[str, StockSymbol] = {}

    for text in symbols:
        if not text:
            continue
        symbol = StockSymbol(text)
        if symbol.is_sina_fund() or symbol.is_sina_forex():
            prefetch[text] = symbol
        elif symbol.is_sina_future():
            prefetch[text] = symbol
        elif symbol.is_east_money_forex():
            # 只有微信调用会走到这里，PHP 注释明确：什么都不用做，也不要并入 unknown
            continue
        else:
            sina_symbol = symbol.get_sina_symbol()
            if sina_symbol:
                prefetch[sina_symbol] = symbol
                if symbol.is_fund_a():
                    fund_symbol = symbol.get_sina_fund_symbol()
                    prefetch[fund_symbol] = StockSymbol(fund_symbol)
            else:
                unknown.append(text)

    _prefetch(symbols_to_fetch(prefetch, now, file_mtime, interval), fetch_quotes, save_quote)
    return unknown


def symbols_to_fetch(
    prefetch: Mapping[str, StockSymbol],
    now: _datetime.datetime,
    file_mtime=None,
    interval: int = SECONDS_IN_MIN,
) -> list:
    """对应 PHP ``_prefetchSinaData()`` 的第一段：筛出需要更新的符号与文件名。

    返回 ``[(文件路径, 符号), ...]``，只保留真正需要重新拉取的条目。
    """
    selected = []
    for text, symbol in prefetch.items():
        file_name = os.path.join(DEFAULT_QUOTE_DIRECTORY, _quote_file_stem(text) + ".txt")
        if symbol.is_sina_fund():
            if not sina_fund_need_file(symbol, file_name, now, file_mtime=file_mtime):
                continue
        elif symbol.is_sina_future() or symbol.is_sina_forex():
            if not future_need_new_file(file_name, now, interval, file_mtime):
                continue
        else:
            if not stock_need_new_quotes(symbol, file_name, now, interval, file_mtime):
                continue
        selected.append((file_name, text))
    return selected


def _quote_file_stem(symbol: str) -> str:
    """与 ``stockref.sina_quote_file_name()`` 共用同一套文件名替换规则。"""
    from stockref import _sina_symbol_file_stem

    return _sina_symbol_file_stem(symbol)


def _prefetch(selected: Sequence, fetch_quotes: Callable, save_quote: Callable = None) -> None:
    """对应 PHP ``_prefetchSinaData()`` 的第二段：抓取并按行回写。"""
    if len(selected) < 1:
        return

    quote_text = fetch_quotes([text for _, text in selected])
    if not quote_text:
        return

    lines = quote_text.split("\n")
    for index, (file_name, _) in enumerate(selected):
        if index >= len(lines):
            # PHP 没有做边界检查，越界会写入 null；这里显式跳过，避免写坏缓存
            break
        if save_quote is not None:
            save_quote(file_name, lines[index])


# ---------------------------------------------------------------------------
# PHP 原名别名
# ---------------------------------------------------------------------------
SinaFundNeedFile = sina_fund_need_file
StockNeedNewQuotes = stock_need_new_quotes
FutureNeedNewFile = future_need_new_file
PrefetchSaveSinaData = save_sina_data
PrefetchLoadSinaData = load_sina_data
PrefetchSinaStockData = prefetch_sina_stock_data
