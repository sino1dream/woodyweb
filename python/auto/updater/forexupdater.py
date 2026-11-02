"""汇率更新器：把汇率写进数据库，供估值公式使用。

对应 PHP ``php/stock/chinamoney.php`` 的 ``GetChinaMoney()``：它抓中间价后调用
``_chinaMoneyInsertData()``，把 ``USCNY`` / ``HKCNY`` / ``EUCNY`` / ``JPCNY``
写进 ``netvaluehistory``。

## 为什么汇率必须进库

``stockdata.MySqlFundDataSource._get_default_cny_est()`` 会去读 ``USCNY``
的历史值，再交给 ``FundProfile.cny_est``，最终在
``fundcalc.calc_with_calibration()`` 里当作 ``cny``：

    est_f = (1 - p_f) * nv_f + p_f * est_value * cny / cal_f

库里没有 ``USCNY`` 符号时 ``cny`` 退化成 ``1.0`` —— **等于把美元当人民币用**，
QDII 估值会差 7 倍左右。本模块负责把汇率补上。

## 写哪两张表

* ``netvaluehistory``：按日的汇率历史（PHP 就是写这里，``CnyReference`` 读它）；
* ``realtimequote``：最新汇率快照，供页面直读与诊断。两类符号：
  ``USCNY`` 等中间价符号（``update()`` 顺手写）与参考数据表用的
  ``fx_susdcny`` / ``fx_susdcnh`` 实时价（``update_page_forex()`` 写，
  对应 PHP 的 ``LoadSinaForexData``）。
"""

from __future__ import annotations

import datetime as _datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from source import forexsource

#: 参考数据表里的外汇**页面符号**：离岸 / 在岸人民币 + 港币 / 日元 / 欧元即期。
#: PHP 的对应物是 ``IsSinaForex``（``fx_`` 前缀，stocksymbol.php:771）：
#: ``MyStockReference::LoadData()`` 抓新浪实时价只进内存、**不写库**
#: （php/stock/stockref.php:493）。Python 侧按"web 只读快照"的架构
#: （refquote.py）落到 ``realtimequote``，由 :meth:`ForexUpdater.update_page_forex`
#: 负责写。
#: 它们**不进 netvaluehistory** —— 那是 USCNY 等中间价符号的路
#: （PHP ``chinamoney.php`` 的正路，见 :meth:`ForexUpdater.update`），
#: 两张表两种语义，别混。
#:
#: ## 为什么港 / 日 / 欧三对也必须在这里（2026-11-02 补）
#:
#: 原来只有美元两对。后果不是"表里少一行"，而是**参考 EST 算错**：
#: ``fundestcalc._fair_cny()`` 对 ETF 用``即期``、对 LOF 用``中间价``
#: （PHP ``GetForexRef()`` 口径），而即期取不到时会**静默退回中间价**。
#: 于是港 / 日 / 欧 QDII 的参考 EST 全部退化成"用中间价算的值"：
#: 页面照常 200、数字看着正常，只是与生产端差 0.1–0.6%。
#: 实测 ``SH513030`` 参考 EST 我们 1.757（＝中间价那档）/ 生产端 1.746
#: （用 ``fx_seurcny`` 7.4336 算）—— 差的那点正是即期与中间价之差。
#:
#: 五个符号的新浪返回**都是 18 个字段、第 9 位是价、第 10 位是名称**，
#: 所以 :meth:`SinaForexSource.fetch_rate` 不用为地区分叉。
#: ``fx_sjpycny`` 是**每 1 日元**（0.0421），与 ``JPCNY`` 中间价的存储口径
#: 一致；生产端页面显示的 "JPYCNY 4.2126 / JPCNY 4.2424" 是每 100 日元，
#: 只是个显示倍数，别据此把库里的值乘 100。
PAGE_FOREX_SYMBOLS = ("fx_susdcny", "fx_susdcnh",
                      "fx_shkdcny", "fx_sjpycny", "fx_seurcny")


class ForexUpdater:
    """抓汇率并落库。

    ``store`` 需要有 ``write_net_value(stock_id, date, close)``
    （``quotestore.QuoteStore`` / ``MemoryQuoteStore`` 都有）。
    """

    def __init__(
        self,
        source: Any = None,
        store: Any = None,
        symbols: Sequence = None,
        now_provider=None,
        snapshot_store: Any = None,
    ):
        self.source = source or forexsource.get_forex_source()
        self.store = store
        self.symbols = list(symbols or forexsource.FOREX_SYMBOLS)
        self.now_provider = now_provider or _datetime.datetime.now
        self.snapshot_store = snapshot_store
        self.last_error = None
        self.last_updated = 0
        self.comparisons = []

    def update(self, apply: bool = True, force: bool = True) -> int:
        """抓全部汇率符号并写库，返回成功写入的条数。

        ``force`` 默认 True：汇率每天都可能变，而且同一个日期重写一遍没有副作用
        （``netvaluehistory`` 有 (date, stock_id) 唯一键，走 UPSERT）。
        """
        connection = getattr(self.store, "connection", None)
        if connection is None and apply:
            self.last_error = "没有可用的数据库连接"
            return 0

        updated = 0
        errors = []
        comparisons = []

        for symbol in self.symbols:
            try:
                record = self.source.fetch_rate(symbol)
            except Exception as exc:
                errors.append("%s: %s: %s" % (symbol, type(exc).__name__, exc))
                continue
            # 记下对比结果（crosscheck 源才有）
            if getattr(self.source, "last_comparison", None) and self.source.last_comparison.get("symbol") == symbol:
                comparisons.append(self.source.last_comparison)
            if not record or record.get("rate") in (None, ""):
                errors.append("%s: 源没有返回汇率" % symbol)
                continue

            date = record.get("date") or self.now_provider().strftime("%Y-%m-%d")
            rate = record["rate"]

            if apply:
                stock_id = self._stock_id(connection, symbol)
                if stock_id is None:
                    errors.append("%s: stock 表里没有这个符号（先建名录）" % symbol)
                    continue
                if not self.store.write_net_value(stock_id, date, rate, allow_weekend=True):
                    errors.append("%s: 写 netvaluehistory 被拒" % symbol)
                    continue
                self._write_snapshot(stock_id, record)
            updated += 1

        if apply:
            connection.commit()
        self.last_error = "; ".join(errors) if errors else None
        self.last_updated = updated
        self.comparisons = comparisons
        return updated

    def update_page_forex(self, apply: bool = True, source: Any = None) -> int:
        """抓参考数据表用的离岸/在岸人民币**实时快照**，写 ``realtimequote``。

        为什么单独立一个方法，不并进 :meth:`update`：

        * ``update()`` 写 ``netvaluehistory``（USCNY 中间价**历史**，
          对应 PHP ``chinamoney.php``），数据源是货币网/外汇管理局；
        * 本方法写 ``realtimequote``（USDCNH/USDCNY **实时行情**快照，
          对应 PHP ``LoadSinaForexData``），数据源是新浪。

        曾经的缺口：``quoteupdater`` 的符号集合从 ``stockhistory`` 反推，
        外汇没有日线历史 → 这两个符号永远进不了更新列表，快照一直停在
        种子手写那天（实测停演到 2026-10-15，生产端同日已是 10-19，
        参考数据表里这两行的日期/时间因此比生产端落后 4 天）。

        ``source`` 可注入假源（测试用）；默认建新浪源 —— crosscheck 的
        对照源（货币网）没有离岸/在岸的实时代码，用不了。
        """
        connection = getattr(self.store, "connection", None)
        if connection is None and apply:
            self.last_error = "没有可用的数据库连接"
            return 0
        if self.snapshot_store is None and apply:
            self.last_error = "没有可用的快照存储（realtimequote 写不进去）"
            return 0

        source = source or forexsource.SinaForexSource()
        written = 0
        errors = []
        for symbol in PAGE_FOREX_SYMBOLS:
            try:
                record = source.fetch_rate(symbol)
            except Exception as exc:
                errors.append("%s: %s: %s" % (symbol, type(exc).__name__, exc))
                continue
            if not record or record.get("rate") in (None, ""):
                errors.append("%s: 新浪没有返回行情" % symbol)
                continue
            if not apply:
                written += 1
                continue
            stock_id = self._stock_id(connection, symbol)
            if stock_id is None:
                errors.append("%s: stock 表里没有这个符号（先建名录）" % symbol)
                continue
            try:
                self._write_snapshot(stock_id, record)
            except Exception as exc:
                errors.append("%s: 写快照失败: %s" % (symbol, exc))
                continue
            written += 1
        if errors:
            self.last_error = "; ".join(errors)
        self.last_updated = written
        return written

    def backfill_history(self, apply: bool = True, limit: int = 400, days: int = 90) -> int:
        """回补历史汇率序列，返回写入条数。

        为什么需要回补：基金估值的"官方日期"往往是几天前，而实时接口只给当下汇率。
        没有历史序列时只能往前找最近一天，可能差很多（实测曾回退到 9 个月前的
        种子占位值）。

        数据源按以下顺序尝试：

        1. **国家外汇管理局**（``forexsource.fetch_history_safe``）
           —— **实测可用的正解**，按区间返回历史中间价，能精确命中基金官方日期；
        2. 东方财富 K 线 —— 实测不稳定（时通时断），作为兜底。

        ``apply=False`` 时只报告将要写多少条。
        """
        connection = getattr(self.store, "connection", None)
        if connection is None:
            self.last_error = "没有可用的数据库连接"
            return 0

        written = 0
        errors = []
        for symbol in self.symbols:
            rows = self._history_rows(symbol, limit=limit, days=days)
            if not rows:
                errors.append("%s: 历史接口没返回数据" % symbol)
                continue
            stock_id = self._stock_id(connection, symbol)
            if stock_id is None:
                errors.append("%s: stock 表里没有这个符号" % symbol)
                continue
            for row in rows:
                if not apply:
                    written += 1
                    continue
                if self.store.write_net_value(stock_id, row["date"], row["rate"], allow_weekend=True):
                    written += 1
        if apply:
            connection.commit()
        self.last_error = "; ".join(errors) if errors else None
        self.last_updated = written
        return written

    def _history_rows(self, symbol: str, limit: int = 400, days: int = 90) -> List:
        """按"外汇管理局优先、东财兜底"的顺序取历史序列。"""
        import datetime as _datetime

        today = _datetime.date.today()
        start = (today - _datetime.timedelta(days=days)).isoformat()
        end = today.isoformat()

        try:
            rows = forexsource.fetch_history_safe(symbol, start, end)
        except Exception:
            rows = []
        if rows:
            return rows[-max(1, limit):]

        try:
            return forexsource.fetch_history_eastmoney(symbol, limit=limit)
        except Exception:
            return []

    def fill_official_dates(self, apply: bool = True, max_gap_days: int = 5) -> List[Dict[str, Any]]:
        """给"基金的官方日期"补上汇率。

        为什么需要：`_get_default_cny_est()` 按基金的**官方日期**取汇率。
        实时接口只给当下（例如 2026-10-01），而基金的官方日期常在几天前
        （例如 2026-09-29）。官方日期没有汇率时只能往前回退，可能差几百天
        （实测回退命中了 9 个月前的种子占位值 7.1）。

        做法：找该日期**之前最近一条真实汇率**，若差距在 ``max_gap_days`` 以内，
        就用它填到官方日期上。汇率在几个交易日内的变动对估值影响很小
        （实测 09-29 与 09-30 的 USD/CNY 相差不到 0.5%），比用 9 个月前的值好得多。

        返回填充清单，便于核对"用了哪一天的汇率"。
        """
        connection = getattr(self.store, "connection", None)
        if connection is None:
            self.last_error = "没有可用的数据库连接"
            return []

        cursor = connection.cursor()
        fills = []
        try:
            # 需要汇率的日期：所有已校准基金的校准值日期
            cursor.execute("SELECT id, symbol FROM stock ORDER BY id")
            stocks = cursor.fetchall()
            targets = []
            for row in stocks:
                stock_id, symbol = row["id"], row["symbol"]
                if not forexsource.SINA_FOREX_CODES.get(symbol) and symbol not in forexsource.FOREX_SYMBOLS:
                    # 只处理基金（有 6 位数字代码的），拿到它们的官方日期
                    if not any(char.isdigit() for char in str(symbol)):
                        continue
                    cursor.execute(
                        "SELECT date FROM calibrationhistory WHERE stock_id = %s "
                        "ORDER BY date DESC LIMIT 1",
                        (stock_id,),
                    )
                    calibration = cursor.fetchone()
                    if calibration:
                        targets.append(str(calibration["date"]))

            cny_id = self._stock_id(connection, "USCNY")
            if cny_id is None:
                self.last_error = "stock 表里没有 USCNY"
                return []

            for date in sorted(set(targets)):
                cursor.execute(
                    "SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s LIMIT 1",
                    (cny_id, date),
                )
                if cursor.fetchone() is not None:
                    continue  # 该日期已有汇率

                cursor.execute(
                    "SELECT date, close FROM netvaluehistory WHERE stock_id = %s AND date < %s "
                    "ORDER BY date DESC LIMIT 1",
                    (cny_id, date),
                )
                previous = cursor.fetchone()
                if not previous:
                    continue

                gap = _days_between(str(previous["date"]), date)
                if gap is None or gap > max_gap_days:
                    continue

                fills.append({
                    "date": date,
                    "source_date": str(previous["date"]),
                    "rate": float(previous["close"]),
                    "gap_days": gap,
                })
                if apply:
                    self.store.write_net_value(cny_id, date, previous["close"], allow_weekend=True)
            if apply:
                connection.commit()
        finally:
            cursor.close()
        return fills

    def purge_fake_rates(self, apply: bool = True) -> List[Dict[str, Any]]:
        """清掉"看起来是手工占位"的汇率记录。

        判据：日期是种子用的 SEED 日期（``2026-01-02``）。那条记录上的 7.1
        并不是真实汇率，却会作为"9 个月前的最近值"被回退取到，污染估值。
        """
        fake_date = "2026-01-02"
        connection = getattr(self.store, "connection", None)
        if connection is None:
            return []

        cursor = connection.cursor()
        removed = []
        try:
            for symbol in forexsource.FOREX_SYMBOLS:
                stock_id = self._stock_id(connection, symbol)
                if stock_id is None:
                    continue
                cursor.execute(
                    "SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s LIMIT 1",
                    (stock_id, fake_date),
                )
                row = cursor.fetchone()
                if not row:
                    continue
                removed.append({"symbol": symbol, "date": fake_date, "rate": row["close"]})
                if apply:
                    cursor.execute(
                        "DELETE FROM netvaluehistory WHERE stock_id = %s AND date = %s",
                        (stock_id, fake_date),
                    )
            if apply:
                connection.commit()
        finally:
            cursor.close()
        return removed

    def _stock_id(self, connection, symbol: str) -> Optional[Any]:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT id FROM stock WHERE symbol = %s LIMIT 1", (symbol,))
            row = cursor.fetchone()
            return row["id"] if row else None
        finally:
            cursor.close()

    def _write_snapshot(self, stock_id: Any, record: Mapping[str, Any]) -> None:
        """顺手写一份 realtimequote 快照，方便页面/诊断直接读最新汇率。

        ``prev_price`` 取自 ``record["prev"]``（新浪 ``$ar[3]``，见
        ``SinaForexSource.fetch_rate``）—— 页面参考数据表的"涨幅"列要
        用它算 ``(price - prev) / prev``。早期这里恒传空串，涨幅列因此
        常年空白/为零。
        """
        if self.snapshot_store is None:
            return
        try:
            self.snapshot_store.save(
                stock_id,
                str(record.get("symbol")),
                str(record.get("symbol")),
                record.get("rate"),
                record.get("prev") or "",
                str(record.get("name") or ""),
                record.get("date"),
                record.get("time") or "00:00:00",
                str(record.get("source") or ""),
                self.now_provider(),
            )
        except Exception:
            # 快照写失败不影响汇率落库
            pass

    def describe(self) -> Dict[str, Any]:
        return {
            "source": self.source.describe() if hasattr(self.source, "describe") else {"name": "?"},
            "symbols": list(self.symbols),
            "last_updated": self.last_updated,
            "last_error": self.last_error,
            "comparisons": self.comparisons,
        }


def _days_between(earlier: str, later: str) -> Optional[int]:
    """两个 ``YYYY-MM-DD`` 相差多少天；解析失败返回 ``None``。"""
    import datetime as _datetime

    try:
        first = _datetime.datetime.strptime(str(earlier)[:10], "%Y-%m-%d")
        second = _datetime.datetime.strptime(str(later)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return (second - first).days


def build_default_updater(store: Any = None, **kwargs) -> ForexUpdater:
    """构造生产用汇率更新器：连库 + 真实汇率源。"""
    import os

    from sql.quotestore import QuoteStore, RealtimeQuoteStore

    if store is None:
        import pymysql

        from core.stockdata import load_mysql_env, mysql_config_from_env

        config = mysql_config_from_env(load_mysql_env(os.environ))
        connection = pymysql.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
            charset="utf8",
            cursorclass=pymysql.cursors.DictCursor,
        )
        store = QuoteStore(connection)

    connection = getattr(store, "connection", None)
    updater = ForexUpdater(
        store=store,
        snapshot_store=RealtimeQuoteStore(connection) if connection is not None else None,
        **kwargs
    )
    updater.connection = connection
    return updater
