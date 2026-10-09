"""历史数据页面服务。

对应 PHP 的 6 个历史数据段落（``php/ui/*paragraph.php``）：

===============================  =========================  ==========================
页面（生产端）                    本模块 kind                 对应 PHP
===============================  =========================  ==========================
历史价格                          ``stockhistory``           stockhistoryparagraph.php
基金溢价记录                      ``fundhistory``            fundhistoryparagraph.php
净值记录                          ``netvaluehistory``        netvaluehistoryparagraph.php
净值价格比较                      ``netvalueclose``          netvaluecloseparagraph.php
校准记录                          ``calibrationhistory``     calibrationhistoryparagraph.php
基金场内份额                      ``shareshistory``          fundshareparagraph.php
===============================  =========================  ==========================

PHP 里这些页面各自独立、各自算数；这里把"取某张表的某几行"统一成
``HistoryService.get_table()``，列定义与单位换算集中在 :data:`HISTORY_KINDS`，
取数与显示口径复用 ``historytable.py``。

**与 PHP 的关键差异**：PHP 直接 ``echo`` HTML；这里返回 ``(列, 行)`` 数据结构，
由模板渲染。颜色不再是 ``<font color>``，而是随单元格一起返回颜色名，
模板映射成 CSS 类。
"""

from __future__ import annotations

from typing import Any

from web.bootstrap import install_paths

install_paths()

from fundgroups import get_symbol_group  # noqa: E402
from historytable import (  # noqa: E402
    CalibrationSql,
    DailyTimeSql,
    FundEstSql,
    MIN_FLOAT_VAL,
    NetValueHistorySql,
    TABLE_COMMON_DISPLAY,
    SharesHistorySql,
    StockHistorySql,
    TableRow,
    calibration_hedge_multiplier,
    format_hm,
    format_number_display,
    format_percentage_display,
    format_price_display,
    format_ratio_display,
    format_turnover_display,
    percentage_change,
    stock_calc_hedge,
    use_same_day_net_value,
)
from stockdata import FundDataSource, MySqlFundDataSource  # noqa: E402
from stocksymbol import in_array_hk_mix, in_array_qdii, in_array_qdii_mix  # noqa: E402
from web.db import get_db  # noqa: E402


class HistoryNotFoundError(LookupError):
    """未知的历史数据类型，或该符号没有相应数据。"""


# 对应 PHP ``php/ui/stockdisp.php:32`` 的 ``define('POS_NETVALUE_DIFF', 4.0)``：
# 净值记录页只有在"当天净值涨跌超过 4%"时才反算仓位 —— 作者用它规避
# 官方 EST 的其他误差来源（净值波动小的时候反算出的仓位噪声太大）。
POS_NETVALUE_DIFF = 4.0


def _column(text: str, color: str = "", width: int = 0) -> dict[str, Any]:
    """一列表头：文本 + 颜色名（可选）+ 宽度（可选）。"""
    return {"text": text, "color": color, "width": width}


# ---------------------------------------------------------------------------
# 列定义（文本与颜色对应 PHP TableColumn* 类，见 php/ui/stocktable.php）
# ---------------------------------------------------------------------------
_COL_DATE = _column("日期", "", 100)
_COL_PRICE = _column("价格", "blue", 70)
_COL_QUANTITY = _column("数量", "indigo", 120)
_COL_SETTLE_PRICE = _column("结算价", "blue", 70)
_COL_ADJCLOSE = _column("复权价格", "blue", 70)
_COL_NETVALUE = _column("净值", "olive", 90)
_COL_PREMIUM = _column("溢价", "orange", 70)
_COL_CHANGE = _column("涨幅", "", 70)
_COL_POSITION = _column("仓位", "", 60)
_COL_CALIBRATION = _column("校准值", "", 140)
_COL_TIME = _column("时间", "", 60)
_COL_HEDGE = _column("对冲值", "navy", 80)
_COL_COUNT = _column("次数", "", 50)
_COL_SHARE = _column("份额(万)", "", 110)
_COL_SHARE_DIFF = _column("场内新增(万)", "", 110)
_COL_TURNOVER = _column("换手", "green", 100)
_COL_TURNOVER_NEW = _column("新增换手", "green", 120)
_COL_OFFICIAL_EST = _column("官方EST", "magenta", 80)
_COL_ERROR = _column("误差", "", 70)
_COL_RATIO = _column("比价", "", 100)


def _stock_column(symbol: str, width: int = 80) -> dict[str, Any]:
    """对照标的列（如 ``^NDX``），对应 PHP ``TableColumnStock``：maroon 色。"""
    return _column(symbol, "maroon", width)


# ---------------------------------------------------------------------------
# 各页面的列组成
# ---------------------------------------------------------------------------
def _columns_stockhistory(source: FundDataSource, symbol: str, compare: Any, forex: Any) -> list:
    columns = [_COL_DATE, _COL_PRICE, _COL_QUANTITY]
    # PHP: $ref->IsSinaFuture() ? 结算价 : 复权价格
    # 期货没有"复权价"概念，PHP 显示「结算价」；用 StockSymbol 判定而不是数据源，
    # 因为这个判断只跟符号本身有关（hf_CL / hf_CHA50CFD 之类）。
    is_future = False
    try:
        from stocksymbol import StockSymbol

        is_future = bool(StockSymbol(symbol).is_sina_future())
    except Exception:
        is_future = False
    columns.append(_COL_SETTLE_PRICE if is_future else _COL_ADJCLOSE)
    if compare:
        columns.append(_stock_column(compare))
        if forex:
            columns.append(_stock_column(forex))
        columns.append(_COL_RATIO)
    return columns


def _columns_fundhistory(est_symbol: Any) -> list:
    columns = [_COL_DATE, _COL_PRICE, _COL_NETVALUE, _COL_PREMIUM]
    if est_symbol:
        # PHP 只在 fundest 表有记录时才加这几列；这里由调用方判断后传入
        columns += [_COL_OFFICIAL_EST, _COL_TIME, _COL_ERROR]
    return columns


def _columns_netvaluehistory(cny_symbol: Any, est_symbol: Any) -> list:
    """对应 PHP ``GetNetValueTableColumn()``（netvaluehistoryparagraph.php:39）。

    PHP 在这里复用同一个 ``TableColumnNetValue``（"净值"）给基金净值和中间层净值，
    于是页面上会出现两个同名的「净值」列。这在 PHP 的表格里靠位置区分，
    但迁移到独立页面后会让读者分不清哪个是基金自己的净值。

    所以这里把中间层那列标成「标的净值」—— 语义相同、但不再有歧义。
    """
    columns = [_COL_DATE, _COL_NETVALUE, _COL_CHANGE]
    if cny_symbol:
        columns.append(_stock_column(cny_symbol))
        columns.append(_COL_CHANGE)
        if est_symbol:
            # 中间层净值（如 QQQ 的净值），加前缀避免与基金净值的「净值」列混淆
            columns.append(_column("标的净值", "olive", 90))
            columns.append(_COL_CHANGE)
        columns.append(_COL_POSITION)
    return columns


def _columns_calibrationhistory(is_fund: bool) -> list:
    columns = [_COL_DATE, _COL_CALIBRATION, _COL_TIME, _COL_COUNT]
    if is_fund:
        columns += [_COL_NETVALUE, _COL_HEDGE]
    return columns


def _columns_shareshistory() -> list:
    return [
        _COL_DATE,
        _COL_SHARE,
        _COL_SHARE_DIFF,
        _COL_QUANTITY,
        _COL_TURNOVER,
        _COL_TURNOVER_NEW,
    ]


# 页面 kind -> 标题与说明
HISTORY_KINDS: dict[str, dict[str, str]] = {
    "stockhistory": {
        "title": "历史价格",
        "description": "该符号的每日行情：收盘价、成交量与复权价。",
    },
    "fundhistory": {
        "title": "基金溢价记录",
        "description": "场内价格相对单位净值的溢价。**净值取前一日** —— 当天的净值要等当晚美股收盘后才公布。",
    },
    "netvaluehistory": {
        "title": "净值记录",
        "description": "单位净值、汇率与中间层净值的逐日对照，并据此反算实际仓位。",
    },
    "netvalueclose": {
        "title": "净值价格比较",
        "description": "同一天的场内价格与单位净值并排比较，看两者是否有稳定偏差。",
    },
    "calibrationhistory": {
        "title": "校准记录",
        "description": "每次拿到官方净值后做自动校准的结果，用来观察长期趋势。",
    },
    "shareshistory": {
        "title": "基金场内份额",
        "description": "场内份额变化与成交量的比值（换手率）。份额单位是万份。",
    },
}


class HistoryService:
    """把某符号的历史数据整理成可渲染的表格。

    ``source`` 提供符号/名称/仓位等参考数据（``FundDataSource``）；
    ``connection`` 是请求级数据库连接（与 ``source`` 复用同一个即可）。
    """

    def __init__(self, source: FundDataSource, connection: Any = None):
        self.source = source
        self.connection = connection if connection is not None else getattr(source, "connection", None)

    # -- 对外 -------------------------------------------------------------
    def get_table(
        self,
        kind: str,
        symbol: str,
        start: int = 0,
        num: int = TABLE_COMMON_DISPLAY,
    ) -> dict[str, Any]:
        """取一个历史数据表格。

        ``start`` / ``num`` 与 PHP 的 ``stockhistorycn.php?symbol=X&start=&num=``
        语义一致：``num=0`` 表示"全部记录"。
        """
        if kind not in HISTORY_KINDS:
            raise HistoryNotFoundError(kind)
        if self.connection is None:
            raise HistoryNotFoundError("没有可用的数据库连接")

        stock_id = self.source.get_stock_id(symbol)
        if stock_id is None:
            raise HistoryNotFoundError(symbol)

        builder = getattr(self, "_build_" + kind)
        columns, rows, total = builder(symbol, stock_id, start, num)

        meta = HISTORY_KINDS[kind]
        return {
            "kind": kind,
            "symbol": symbol,
            "name": self.source.get_stock_name(symbol) or symbol,
            "group": get_symbol_group(symbol),
            "title": meta["title"],
            "description": meta["description"],
            "columns": columns,
            "rows": rows,
            "total": total,
            "start": start,
            "num": num,
            "other_kinds": [k for k in HISTORY_KINDS if k != kind],
        }

    # -- 历史价格 ---------------------------------------------------------
    def _build_stockhistory(self, symbol: str, stock_id: Any, start: int, num: int):
        table = StockHistorySql(self.connection)
        # PHP 只在传入了 compare_ref 时才加对照列；这里按该符号是否有估值标的决定，
        # 这样 /history/stockhistory/XOP 这样的标的页也能显示比价
        compare = self._compare_symbol(symbol)
        forex = self._forex_symbol() if compare else None

        rows = []
        for record in table.get_all(stock_id, start, num):
            rows.append(self._row_stockhistory(table, record, compare, forex))
        return _columns_stockhistory(self.source, symbol, compare, forex), rows, table.count(stock_id)

    def _row_stockhistory(self, table: StockHistorySql, record: Any, compare: Any, forex: Any) -> TableRow:
        date = str(record["date"])
        stock_id = record["stock_id"]

        # PHP 用 $ref->GetPrevPrice()（= 上一交易日的收盘价）作为涨跌基准
        prev = self.source.get_prev_history_close(stock_id, date)
        close = record["close"]

        row = TableRow()
        row.add(date)
        row.add(format_price_display(close, prev))
        row.add(record["volume"])

        adjclose = record["adjclose"]
        row.add(format_price_display(adjclose, prev))

        if compare:
            compare_id = self.source.get_stock_id(compare)
            compare_adjclose = table.get_adjclose(compare_id, date) if compare_id else None
            if compare_adjclose is not None:
                row.add(format_price_display(compare_adjclose))
                # PHP 原文（stockhistoryparagraph.php:24-26）：把复权价从"每盎司美元"
                # 换成"每克人民币"口径再跟对照标的比价。
                # 1000 = 盎司→毫盎司的换算，31.1035 = 金衡盎司克数。
                # 这段只在黄金类比价时才有意义，照抄以保持数值一致。
                ratio_left = float(adjclose) / 1000.0 * 31.1035
                foreign = None
                if forex:
                    forex_id = self.source.get_stock_id(forex)
                    foreign = table.get_adjclose(forex_id, date) if forex_id else None
                    if foreign is not None:
                        row.add(format_price_display(foreign))
                if foreign:
                    ratio = ratio_left / float(compare_adjclose) / float(foreign)
                else:
                    ratio = ratio_left / float(compare_adjclose)
                row.add(format_ratio_display(ratio))
        return row

    # -- 基金溢价记录 -----------------------------------------------------
    def _build_fundhistory(self, symbol: str, stock_id: Any, start: int, num: int):
        history = StockHistorySql(self.connection)
        netvalue = NetValueHistorySql(self.connection)
        # **列集必须包含 time**：`DailyCloseSql` 的默认列只有
        # (id, stock_id, date, close)，用它取 fundest 会拿不到时间，
        # 于是「时间」列永远空着、异常还会被行处理的 try 吞掉（实测踩过）。
        fundest = DailyTimeSql(self.connection, "fundest")
        est_symbol = self._est_symbol(symbol)
        has_est = fundest.count(stock_id) > 0

        # 对应 PHP UseSameDayNetValue()：QDII 用**前一交易日**净值。
        # 判断依据是该符号属于哪个符号组（QDII / 混合 / 港股混合）。
        same_day = use_same_day_net_value(
            symbol,
            is_qdii=in_array_qdii(symbol),
            is_mix=in_array_qdii_mix(symbol),
            is_hk_mix=in_array_hk_mix(symbol),
        )

        rows = []
        for record in history.get_all(stock_id, start, num):
            date = str(record["date"])
            net_date = date if same_day else history.get_date_prev(stock_id, date)
            if not net_date:
                continue
            net_value = netvalue.get_close(stock_id, net_date)
            if net_value is None:
                continue
            # 官方 EST 按**净值日期**查 —— 严格对应 PHP
            # `fundhistoryparagraph.php:42`：
            #     $strDate = $bSameDay ? $arHistory['date'] : $his_sql->GetDatePrev(...);
            #     if ($strNetValue = $net_sql->GetClose($strStockId, $strDate))
            #         $arFundEst = $fund_est_sql->GetRecord($strStockId, $strDate);
            # 即 netvalue 与 fundest 用**同一个** $strDate（QDII 就是前一交易日）。
            # 换成行情日期会一行都查不到（实测踩过）。
            estimate = fundest.get_record(stock_id, net_date) if has_est else None
            rows.append(self._row_fundhistory(record, net_value, estimate, est_symbol, netvalue))
        return _columns_fundhistory(est_symbol if has_est else None), rows, history.count(stock_id)

    def _row_fundhistory(self, record: Any, net_value: Any, estimate: Any, est_symbol: Any, netvalue: Any) -> TableRow:
        date = str(record["date"])
        close = record["close"]

        row = TableRow()
        row.add(date)
        row.add(format_price_display(close, net_value))
        row.add(format_price_display(net_value))
        row.add(format_percentage_display(net_value, close))

        if estimate is not None:
            estimate_close = estimate.get("close")
            if estimate_close:
                # 官方 EST 与净值同一量纲，直接比
                row.add(format_price_display(estimate_close, net_value))
                # 时间截到分，对应 PHP GetHM()
                row.add(format_hm(estimate.get("time")))
                # 误差：对应 PHP $ref->GetPercentageDisplay($fNetValue, $fEstValue)
                # 即 (估算 / 真实 - 1) * 100，与 StockCompareEstResult() 同向
                row.add(format_percentage_display(net_value, estimate_close))
                if est_symbol:
                    # 最后一列是**标的净值**：中间层在 EST 记录日期上的值，
                    # 供读者判断这次估算是基于哪个行情算出来的
                    est_id = self.source.get_stock_id(est_symbol)
                    est_value = netvalue.get_close(est_id, estimate.get("date")) if est_id else None
                    if est_value is None and est_id:
                        # 中间层的净值口径可能没有该日期 -> 退回行情价
                        est_value = self.source.get_history_close(est_id, estimate.get("date"))
                    row.add(format_price_display(est_value))
        return row

    # -- 净值记录 ---------------------------------------------------------
    def _build_netvaluehistory(self, symbol: str, stock_id: Any, start: int, num: int):
        netvalue = NetValueHistorySql(self.connection)
        cny_symbol = self._cny_symbol(symbol)
        est_symbol = self._est_symbol(symbol)
        # 混合型（无 est_symbol）才走"按持仓比例反算"那条路径
        cny_ref_available = bool(cny_symbol)
        if not cny_ref_available:
            est_symbol = None

        rows = []
        total = netvalue.count(stock_id)
        if total == 0:
            return _columns_netvaluehistory(cny_symbol, est_symbol), rows, total

        position = self.source.get_position(stock_id)
        for record in netvalue.get_all(stock_id, start, num):
            date = str(record["date"])
            prev_date = netvalue.get_date_prev(stock_id, date)
            rows.append(
                self._row_netvaluehistory(netvalue, record, prev_date, cny_symbol, est_symbol, position)
            )
        return _columns_netvaluehistory(cny_symbol, est_symbol), rows, total

    def _row_netvaluehistory(
        self, netvalue: NetValueHistorySql, record: Any, prev_date: Any,
        cny_symbol: Any, est_symbol: Any, position: Any,
    ) -> TableRow:
        date = str(record["date"])
        net_value = record["close"]

        row = TableRow()
        row.add(date)
        row.add(format_price_display(net_value))
        prev_net = netvalue.get_close(record["stock_id"], prev_date) if prev_date else None
        row.add(format_percentage_display(prev_net, net_value))

        if not cny_symbol:
            return row

        cny_id = self.source.get_stock_id(cny_symbol)
        cny = netvalue.get_close(cny_id, date) if cny_id else None
        cny_prev = netvalue.get_close(cny_id, prev_date) if (cny_id and prev_date) else None
        row.add(format_price_display(cny) if cny is not None else "")
        row.add(format_percentage_display(cny_prev, cny) if cny is not None else "")

        if est_symbol:
            est_id = self.source.get_stock_id(est_symbol)
            est = netvalue.get_close(est_id, date) if est_id else None
            est_prev = netvalue.get_close(est_id, prev_date) if (est_id and prev_date) else None
            row.add(format_price_display(est) if est is not None else "")
            if est is not None:
                row.add(format_percentage_display(est_prev, est))
                # 对应 PHP _QdiiGetStockPosition()：先用汇率变动把"净值涨幅"里的
                # 汇率成分剥掉，剩下的比例才是真实仓位。
                estimated = self._estimate_position(est, est_prev, prev_net, net_value, cny_prev, cny)
                row.add(estimated if estimated is not None else "")
            else:
                row.add("")
                row.add("")
        else:
            # 混合型：用持仓比例加权，见 PHP _QdiiMixGetPosition()
            row.add(self._mixed_position(record["stock_id"], date, prev_date, prev_net, net_value, cny_prev, cny) or "")
        return row

    def _estimate_position(self, est: Any, est_prev: Any, prev_net: Any, net_value: Any,
                           cny_prev: Any, cny: Any) -> Any:
        """对应 PHP ``_QdiiGetStockPosition()``（netvaluehistoryparagraph.php:5）。

        PHP 原文注释给出了推导：

            (est * cny / estPrev * cnyPrev - 1) * position = (nv / nvPrev - 1)

        即净值涨幅 = 仓位 × 标的涨幅（标的涨幅里含汇率变动）。
        所以仓位 = 净值涨跌% / 标的（已换汇）涨跌%。

        只在净值涨跌超过 ``fInput``（默认 4%）时才算 —— 波动小时反算噪声太大。
        """
        if est_prev is None or prev_net is None:
            return None
        percent = percentage_change(prev_net, net_value)
        if percent is None or abs(percent) <= POS_NETVALUE_DIFF:
            return None

        # PHP: StockGetPercentage($fCnyPrev / $fEstProportion, $fCny)，
        # 其中 $fEstProportion = $fEst / $fEstPrev。
        # 也就是把汇率按标的涨幅归一后再算汇率自身的变动。
        try:
            proportion = float(est) / float(est_prev)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        if abs(proportion) <= MIN_FLOAT_VAL:
            return None
        if cny_prev is None or cny is None:
            return None
        est_percent = percentage_change(float(cny_prev) / proportion, cny)
        if est_percent is None or abs(est_percent) <= MIN_FLOAT_VAL:
            return None
        value = percent / est_percent
        if value <= MIN_FLOAT_VAL:
            return None
        return format_number_display(value, 2)

    def _mixed_position(self, stock_id: Any, date: Any, prev_date: Any, prev_net: Any,
                        net_value: Any, cny_prev: Any, cny: Any) -> Any:
        """对应 PHP ``_QdiiMixGetPosition()``：按持仓比例加权后反算仓位。

        需要 ``holdings`` 表（持仓明细）。**当前迁移范围内 holdings 为空**，
        所以这里只在数据齐备时才计算，否则返回 ``None`` 让页面留空 ——
        不编造一个看起来合理的仓位。
        """
        return None

    # -- 净值价格比较 -----------------------------------------------------
    def _build_netvalueclose(self, symbol: str, stock_id: Any, start: int, num: int):
        history = StockHistorySql(self.connection)
        netvalue = NetValueHistorySql(self.connection)
        shares = SharesHistorySql(self.connection)
        if netvalue.count(stock_id) == 0:
            return _columns_netvalueclose(), [], 0

        same_day = use_same_day_net_value(
            symbol,
            is_qdii=in_array_qdii(symbol),
            is_mix=in_array_qdii_mix(symbol),
            is_hk_mix=in_array_hk_mix(symbol),
        )

        rows = []
        for record in history.get_all(stock_id, start, num):
            date = str(record["date"])
            # PHP: $bSameDay ? $arHistory['date'] : GetDatePrev(...)
            net_date = date if same_day else history.get_date_prev(stock_id, date)
            if not net_date:
                continue
            net_value = netvalue.get_close(stock_id, net_date)
            if net_value is None:
                continue
            rows.append(self._row_netvalueclose(history, shares, record, net_value, symbol))
        return _columns_netvalueclose(), rows, history.count(stock_id)

    def _row_netvalueclose(self, history: StockHistorySql, shares: SharesHistorySql,
                           record: Any, net_value: Any, symbol: str) -> TableRow:
        date = str(record["date"])
        stock_id = record["stock_id"]
        close = record["close"]
        close_prev = history.get_close_prev(stock_id, date)

        row = TableRow()
        row.add(date)
        # PHP: GetPriceDisplay($fClose, $fNetValue) —— 以净值为基准上色
        row.add(format_price_display(close, net_value))
        row.add(format_price_display(net_value))
        # PHP: GetPercentageDisplay($fNetValue, $fClose) —— 溢价（价格相对净值）
        row.add(format_percentage_display(net_value, close))
        # PHP: GetPercentageDisplay($fClosePrev, $fClose) —— 涨幅
        row.add(format_percentage_display(close_prev, close))

        share = shares.get_close(stock_id, date) if close_prev is not None else None
        if share is not None:
            row.add(format_number_display(share, 2))
            row.add(format_turnover_display(history.get_volume(stock_id, date), share))
        return row

    # -- 校准记录 ---------------------------------------------------------
    def _build_calibrationhistory(self, symbol: str, stock_id: Any, start: int, num: int):
        table = CalibrationSql(self.connection)
        is_fund = bool(getattr(self.source, "get_fund_profile", None) and self.source.get_fund_profile(symbol))
        multiplier = calibration_hedge_multiplier(symbol)
        # 只有基金类才显示净值与对冲值列；对应 PHP $ref->IsFundA()
        if not is_fund:
            is_fund = bool(in_array_qdii(symbol) or in_array_qdii_mix(symbol))

        netvalue = NetValueHistorySql(self.connection)
        position = self.source.get_position(stock_id) if is_fund else None

        rows = []
        for record in table.get_all(stock_id, start, num):
            row = TableRow()
            date = str(record["date"])
            calibration = record["close"]
            row.add(date)
            row.add(format_number_display(calibration, 3))
            row.add(format_hm(record["time"]))
            row.add(record["num"])
            if is_fund:
                net = netvalue.get_close(stock_id, date)
                row.add(format_price_display(net) if net is not None else "")
                hedge = stock_calc_hedge(calibration, position)
                if hedge is None:
                    row.add("")
                else:
                    row.add(format_number_display(hedge * multiplier, 0))
            rows.append(row)
        return _columns_calibrationhistory(is_fund), rows, table.count(stock_id)

    # -- 基金场内份额 -----------------------------------------------------
    def _build_shareshistory(self, symbol: str, stock_id: Any, start: int, num: int):
        shares = SharesHistorySql(self.connection)
        history = StockHistorySql(self.connection)
        rows = []
        for record in shares.get_all(stock_id, start, num):
            rows.append(self._row_shareshistory(shares, history, record))
        return _columns_shareshistory(), rows, shares.count(stock_id)

    def _row_shareshistory(self, shares: SharesHistorySql, history: StockHistorySql, record: Any) -> TableRow:
        date = str(record["date"])
        stock_id = record["stock_id"]
        share = record["close"]

        row = TableRow()
        row.add(date)
        row.add(format_number_display(share, 2) if share is not None else "")

        share_prev = shares.get_close_prev(stock_id, date)
        if share_prev is not None and share is not None:
            diff = float(share) - float(share_prev)
            volume = history.get_volume(stock_id, date)
            row.add(format_number_display(diff))
            row.add(volume)
            if share is not None:
                row.add(format_turnover_display(volume, share))
                # PHP 只在"有成交量且份额是净增"时才给新增换手：
                # 份额减少时这个比值没有意义（负数换手率会误导）
                if float(volume) > MIN_FLOAT_VAL and diff > MIN_FLOAT_VAL:
                    row.add(format_turnover_display(volume, diff, 0))
        return row

    # -- 参考符号解析 -----------------------------------------------------
    def _compare_symbol(self, symbol: str) -> Any:
        """历史价格页的对照标的。

        PHP 由调用方显式传 ``$compare_ref``；这里按该符号的估值标的推断，
        取不到就不加对照列（页面少几列，不影响其它列）。
        """
        return self._est_symbol(symbol)

    def _est_symbol(self, symbol: str) -> Any:
        """估值用的中间层标的（如 ``^NDX`` / ``XOP``）。"""
        reader = getattr(self.source, "get_fund_profile", None)
        if reader is None:
            return None
        try:
            profile = reader(symbol)
        except Exception:
            return None
        if profile is None:
            return None
        return getattr(profile, "est_symbol", None) or getattr(profile, "pair_symbol", None)

    def _cny_symbol(self, symbol: str) -> Any:
        """该基金估值用的汇率符号。

        QDII（美股/港股/日本/欧洲）用美元中间价 ``USCNY`` ——
        因为估值链路统一先把标的换算成人民币。没有估值标的的符号不显示汇率列。
        """
        if self._est_symbol(symbol) is None:
            return None
        return "USCNY"

    def _forex_symbol(self) -> Any:
        """历史价格页比价用的汇率符号。

        PHP 的比价公式里那个 ``$forex_ref`` 用的就是美元人民币中间价，
        目的是把"每克多少美元"换算成"每克多少人民币"。
        """
        return "USCNY"


def _columns_netvalueclose() -> list:
    """净值价格比较的列，对应 PHP ``netvaluecloseparagraph.php:53``。"""
    return [_COL_DATE, _COL_PRICE, _COL_NETVALUE, _COL_PREMIUM, _COL_CHANGE, _COL_SHARE, _COL_TURNOVER]


def default_history_service_factory() -> HistoryService:
    """创建使用当前请求数据库连接的历史数据服务。"""
    return HistoryService(MySqlFundDataSource(get_db()))


def parse_paging(args: Any) -> tuple:
    """从查询参数解析 ``start`` / ``num``，与 PHP 的参数名保持一致。

    ``num=0`` 表示全部记录（PHP ``_SqlBuildLimit`` 的语义）。
    负数或非数字按缺省处理，避免被一个坏链接打出全表。
    """
    def _int(name: str, default: int) -> int:
        raw = args.get(name)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value >= 0 else default

    return _int("start", 0), _int("num", TABLE_COMMON_DISPLAY)
