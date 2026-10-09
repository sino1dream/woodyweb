"""基金官方净值更新器。

替代 PHP ``yahoostock.php`` 里负责净值的部分。原脚本抓 Yahoo，实测已全面
403（见 ``python/NAV_SOURCE_PLAN.md``），这里改用国内源（默认东方财富，
备选新浪，深交所兜底）。

与 PHP 的对应关系
-----------------
====================================  ==========================================
本模块                                 PHP
====================================  ==========================================
``NavUpdater.update()``                ``YahooUpdateNetValue()``
``should_update()`` 的 16:55 判断      ``YahooUpdateNetValue()`` 里的 ``< 1655``
写 ``netvaluehistory``                 ``_yahooStockGetData()`` → ``WriteDaily()``
====================================  ==========================================

一个重要的区别：PHP 的 ``YahooUpdateNetValue()`` 写的是**中间层**数值
（``^QQQ-IV``），不是基金官方净值。基金官方净值在 PHP 里走的是
``FundReference::GetOfficialNetValue()`` → ``stock.php:StockUpdateEstResult()``
→ ``fundest`` 表。本模块做的是**基金官方单位净值**（写 ``netvaluehistory``），
这才是溢价率需要的分子/分母里那个"真净值"。
"""

from __future__ import annotations

import datetime as _datetime
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

import navsource

# 收盘后多久算净值已更新。对应 PHP YahooUpdateNetValue() 里的 `< 1655`。
DEFAULT_UPDATE_HOUR_MINUTE = 1655

# 环境变量：设为 1 时，即使当天已有数据也重取（用于修数据）
ENV_FORCE = "PALMMICRO_NAV_FORCE"


def is_nav_ready(now: _datetime.datetime = None, hour_minute: int = None) -> bool:
    """当前是否已过"净值通常已公布"的时刻。

    对应 PHP ``YahooUpdateNetValue()`` 的:

        if ($now_ymd->GetYMD() == $strDate) { if ($iHourMinute < 1655) return; }

    即"当天数据还没出来就别抓"。默认 16:55，可用参数覆盖。
    """
    moment = now or _datetime.datetime.now()
    limit = DEFAULT_UPDATE_HOUR_MINUTE if hour_minute is None else hour_minute
    return (moment.hour * 100 + moment.minute) >= limit


def nav_code_of(symbol: str) -> Optional[str]:
    """从库里符号剥出 6 位基金代码（``SZ161130`` → ``161130``）。

    各净值源都按纯数字检索，不认市场前缀。
    """
    digits = "".join(char for char in str(symbol) if char.isdigit())
    return digits if len(digits) == 6 else None


class NavUpdater:
    """抓取基金官方净值并写入 ``netvaluehistory``。

    ``store`` 需要有 ``write_net_value(stock_id, date, close)`` 方法
    （``quotestore.QuoteStore`` / ``MemoryQuoteStore`` 都有）。
    """

    def __init__(
        self,
        source: Any = None,
        store: Any = None,
        now_provider=None,
        update_hour_minute: int = None,
        nav_exists=None,
    ):
        self.source = source or navsource.get_nav_source()
        self.store = store
        self.now_provider = now_provider or _datetime.datetime.now
        self.update_hour_minute = (
            DEFAULT_UPDATE_HOUR_MINUTE if update_hour_minute is None else update_hour_minute
        )
        # 显式注入的"是否已有该日期净值"判断；缺省自己从 store 读
        self.nav_exists = nav_exists
        self.last_error = None
        self.last_updated = 0
        self.skipped_existing = 0

    # -- 判断 -------------------------------------------------------------
    def has_nav(self, stock_id: Any, date: str) -> bool:
        """库里是否已有这个 (stock_id, date) 的净值。

        对应 PHP ``$net_sql->GetRecord($strStockId, $strDate)``。
        判断失败时返回 ``False``（宁可多写一次，也不要漏数据 —— 写入用的是
        ``ON DUPLICATE KEY UPDATE``，重复写不会产生脏数据）。
        """
        if self.nav_exists is not None:
            try:
                return bool(self.nav_exists(stock_id, date))
            except Exception:
                return False

        connection = getattr(self.store, "connection", None)
        if connection is None:
            # 内存 store：直接看它的字典
            net_values = getattr(self.store, "net_values", None)
            if isinstance(net_values, dict):
                return (stock_id, date) in net_values
            return False

        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s LIMIT 1",
                (stock_id, date),
            )
            return cursor.fetchone() is not None
        except Exception:
            return False
        finally:
            cursor.close()

    def should_write(self, stock_id: Any, date: str, force: bool = None) -> bool:
        """该不该把这个日期的净值写进库。

        与 PHP ``YahooUpdateNetValue()`` 的守卫对应，但**判断依据从"今天"改成
        "源给出的日期"** —— 这是踩过坑的地方：PHP 是"取当天数据"的模型，所以用
        今天判断没问题；而我们取的是源上的最新记录，下午 3 点拿到的通常是**昨天**
        的净值。若按"今天还没收盘就不写"，历史净值会被全部跳过，一条都写不进去。

        规则：

        1. 库里已有该日期 → 跳过（除非 ``force``）；
        2. 源日期 == 今天 且 还没到公布时刻（默认 16:55）→ 跳过，等收盘；
        3. 其余情况写入。
        """
        if force is None:
            force = os.getenv(ENV_FORCE, "0") == "1"

        if not force and self.has_nav(stock_id, date):
            return False

        today = self.now_provider().strftime("%Y-%m-%d")
        if not force and date == today and not is_nav_ready(self.now_provider(), self.update_hour_minute):
            return False
        return True

    # 兼容旧命名（原先叫 should_update，签名含 code，容易误用）
    should_update = should_write

    # -- 抓取 -------------------------------------------------------------
    def update(self, pairs: Sequence = None, force: bool = None) -> int:
        """抓一批基金净值并落库，返回成功写入的条数。

        ``pairs`` 每项是 ``(符号, stock_id)``；缺省时向 :meth:`collect_targets`
        要（从库里取基金符号）。某只基金取不到净值只跳过它，不影响其它基金。
        """
        if pairs is None:
            pairs = self.collect_targets()
        wanted = [(symbol, stock_id, nav_code_of(symbol)) for symbol, stock_id in pairs]
        wanted = [item for item in wanted if item[2]]
        if not wanted:
            self.last_updated = 0
            return 0

        updated = 0
        skipped = 0
        errors = []
        for symbol, stock_id, code in wanted:
            try:
                record = self.source.fetch_latest(code)
            except Exception as exc:
                errors.append("%s: %s: %s" % (symbol, type(exc).__name__, exc))
                continue
            if not record or not record.get("date") or not record.get("nav"):
                errors.append("%s: 源没有返回净值" % symbol)
                continue

            record_date = str(record["date"])
            if not self.should_write(stock_id, record_date, force=force):
                skipped += 1
                continue

            if self.store is not None:
                if not self.store.write_net_value(stock_id, record_date, record["nav"]):
                    errors.append("%s: 写库被拒（%s）" % (symbol, record_date))
                    continue
            updated += 1

        self.last_error = "; ".join(errors) if errors else None
        self.last_updated = updated
        self.skipped_existing = skipped
        return updated

    # -- 目标收集 ---------------------------------------------------------
    def collect_targets(self) -> List:
        """从库里取要更新净值的基金符号。

        只取能剥出 6 位数字代码的符号（``SZ161130``、``SH513100``），
        QQQ/^GSPC 这类非中国基金代码不在此列 —— 它们走 ``estquote``。
        """
        connection = getattr(self.store, "connection", None)
        if connection is None:
            return []
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT id, symbol FROM stock ORDER BY id")
            rows = cursor.fetchall()
        finally:
            cursor.close()

        targets = []
        for row in rows:
            symbol = str(row["symbol"])
            if nav_code_of(symbol):
                targets.append((symbol, row["id"]))
        return targets

    # -- 名称同步与核对 ---------------------------------------------------
    def sync_names(self, pairs: Sequence = None, apply: bool = True) -> List[Dict[str, Any]]:
        """用权威源同步基金名称，并报告不一致。

        起因：库里 ``stock.name`` 曾把 SZ161130 写成"易方达标普信息科技LOF"
        （那是 SZ161128 的名字），属于**种子数据写错**。
        新浪 ``f_`` 接口会返回权威全称，所以顺手同步过来并对比差异 ——
        这样"名称写错"以后会被自动纠出来，而不是靠人眼发现。

        返回每只基金的处理结果：``{symbol, code, current, authoritative, changed}``。
        源没给名字时**不动库**（避免把好名字清空）。

        ``apply=False`` 时只报告不改库。
        """
        if pairs is None:
            pairs = self.collect_targets()
        connection = getattr(self.store, "connection", None)
        if connection is None:
            return []

        results = []
        for symbol, stock_id in pairs:
            code = nav_code_of(symbol)
            if not code:
                continue
            try:
                record = self.source.fetch_latest(code)
            except Exception as exc:
                results.append({
                    "symbol": symbol, "code": code, "current": None,
                    "authoritative": None, "changed": False,
                    "error": "%s: %s" % (type(exc).__name__, exc),
                })
                continue

            authoritative = (record or {}).get("name")
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT name FROM stock WHERE id = %s LIMIT 1", (stock_id,))
                row = cursor.fetchone()
                current = row["name"] if row else None

                # 判断"库里名称是否与权威名称相容"。
                #
                # 为什么用包含关系而不是相等：权威源给的是**全称**
                # （如"易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)"），
                # 页面列窄、放不下，库里存的是简洁短名（"易方达纳斯达克100LOF"）。
                # 只要短名的主要部分能在全称里找到，就认为没错。
                #
                # 反过来，如果短名说的是**另一只基金**（如把 161130 写成
                # "标普信息科技"，那是 161128 的名字），主要部分就匹配不上，
                # 于是被标成"不一致" —— 这正是我们要抓的错误。
                mismatch = bool(authoritative) and not _name_compatible(current, authoritative)

                changed = False
                if mismatch and apply:
                    # 只在明确不一致且要求写库时才改；改为全称以保证准确
                    cursor.execute(
                        "UPDATE stock SET name = %s WHERE id = %s",
                        (authoritative, stock_id),
                    )
                    changed = True
            finally:
                cursor.close()

            results.append({
                "symbol": symbol, "code": code, "current": current,
                "authoritative": authoritative,
                "mismatch": mismatch, "changed": changed, "error": None,
            })

        if apply:
            connection.commit()
        return results

    def describe(self) -> Dict[str, Any]:
        """诊断信息。"""
        return {
            "source": self.source.describe() if hasattr(self.source, "describe") else {"name": "?"},
            "update_hour_minute": self.update_hour_minute,
            "force": os.getenv(ENV_FORCE, "0") == "1",
            "last_updated": self.last_updated,
            "last_error": self.last_error,
        }


def build_default_updater(store: Any = None, **kwargs) -> NavUpdater:
    """构造"生产用"净值更新器：连接数据库并带上真实净值源。"""
    from quotestore import QuoteStore

    if store is None:
        import pymysql

        from stockdata import load_mysql_env, mysql_config_from_env

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

    updater = NavUpdater(store=store, **kwargs)
    updater.connection = getattr(store, "connection", None)
    return updater


def _name_compatible(current: Any, authoritative: Any) -> bool:
    """判断库里名称与权威全称是否相容。

    规则：把权威全称里的**主要部分**（去掉常见后缀/修饰）取出来，看库里名称是否
    与之互为子串。这样：

    * ``易方达纳斯达克100LOF`` vs ``易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)``
      → 关键片段 ``纳斯达克100`` 能对上 → 相容；
    * ``易方达标普信息科技LOF`` vs 上面那个全称 → 关键片段对不上 → **不相容**，
      正是要抓的"名字张冠李戴"。

    取不到有效片段时退回整体子串比较。
    """
    if not current or not authoritative:
        return True  # 缺一侧就不下定论，避免误报

    left = _name_key(current)
    right = _name_key(authoritative)
    if not left or not right:
        return True
    return left in right or right in left


# 全称里常见的修饰片段，比较时去掉以免干扰
_NAME_NOISE = (
    "ETF联接", "ETF", "联接", "指数", "(QDII-LOF)", "(QDII-ETF)", "QDII", "LOF",
    "A(人民币份额)", "A", "人民币份额", "美元现汇份额", "美元钞", "美元汇",
)


def _name_key(name: Any) -> str:
    """把名称压成便于比较的关键片段（去掉修饰与标点）。"""
    text = str(name or "")
    for noise in _NAME_NOISE:
        text = text.replace(noise, "")
    for char in "()（） 　-—_":
        text = text.replace(char, "")
    return text.strip()


def nav_exists_in_store(store, stock_id: Any, date: str) -> bool:
    """默认的"这个日期是否已有净值"判断。

    对应 PHP ``$net_sql->GetRecord($strStockId, $strDate)``。
    """
    connection = getattr(store, "connection", None)
    if connection is None:
        return False
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s LIMIT 1",
            (stock_id, date),
        )
        return cursor.fetchone() is not None
    finally:
        cursor.close()
