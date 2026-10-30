"""用真实 PHP 逐例对照 `editcalibration` 的写入。

    D:\\python\\python.exe -B _php_calibration_write_compare.py

对端：`_php_calibration_write_check.php`（PHP 侧照抄 `_updateStockOptionCalibration()`
的分支，方法体用真身）。

## 比什么

每个用例比三样：**校准表整表快照 + lastcalibration 快照 + `time` 列**。
最后两项专门盯两处最容易漏的地方：

* 空值分支要**同时删两处**（`lastcalibration` 整只标的一条 + 当天
  `calibrationhistory` 一行）；
* 值没变时**一句 UPDATE 都不该发**。这一点只看 `close` / `num` 是看不出来的
  （覆盖成同一个值和不写的结果一样），只有 `time` 会变 —— 所以两边都把
  `DebugGetTime()` / `now_time()` 换成**每例归零的计数器**：一次写 = `09:00:01`，
  写两次 = `09:00:02`。这样"发了几条写语句、按什么顺序发"也进了快照。

## 三张临时表

`cw_probe_cal` / `cw_probe_last` / `cw_probe_nav`，两边都在这三张表上跑完整业务
路径，跑完 DROP。**真库只被读**（读 `stock` 表把符号翻成 id），一行都不改。

为什么净值也要临时表：演示库只有 179 个符号，没有 A 股个股、没有境内指数基金、
也没有商品期货基金，而这几支恰好各走一条不同的分支（`in_arrayChinaIndex` 的
汇率写死 `'1.0'`、`in_arrayChinaFuture` 直接 return、其余 unhandled）。用真实符号
凑不出来，又不能往真表里塞种子 —— 换成临时表后，符号、净值、中间价全由用例
自己给，分支覆盖才全。

## 流程

1. 从真库把符号翻成 `stock_id`，拼出用例与种子（含每例自己的净值 / 校准 / 末值
   种子），写成 JSON；
2. 起 PHP 跑同一份 JSON（它在临时表上跑真身，跑完删表）；
3. 在同样的临时表上跑 Python 的 `_write_calibration()`（子类只换表名与时钟，
   业务代码一行不改）；
4. 逐例比快照。

## 已知的设计差异（不是 bug）

`缺基金净值` 那一例：PHP 8 上 `floatval(false)` 当分母会抛 `DivisionByZeroError`
（整页 500）；PHP 7 上算出 `INF`，非 strict 模式写进 `double` 列变 **0** —— 校准值
变 0 等于把这只基金的估值整条打废，还没有报错。本版直接拒绝并说清缺什么。
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(os.path.dirname(SCRATCH_DIR))
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pymysql  # noqa: E402

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402
from ref.stockref import _php_strval_float  # noqa: E402
from sql.historytable import CalibrationSql, NetValueHistorySql  # noqa: E402
from sql.optionstore import LastCalibrationStore  # noqa: E402
from web.services.stock_option_service import StockOptionService  # noqa: E402

CASES_FILE = os.path.join(SCRATCH_DIR, "_calibration_write_cases.json")
PHP_SCRIPT = os.path.join(SCRATCH_DIR, "_php_calibration_write_check.php")

CAL_TABLE = "cw_probe_cal"
LAST_TABLE = "cw_probe_last"
NAV_TABLE = "cw_probe_nav"

#: 演示库里没有的符号，给个不会撞上真实 id 的编号（只在临时表里出现）。
SYNTHETIC_ID = {"SH510300": 900001, "SH518800": 900002, "SH600030": 900003,
                "INDA": 900004, "hf_CHA50CFD": 900005}

#: 基础净值种子。日期只覆盖用例用到的几天，中间价按 QDII 四地区各放一个。
BASE_NAV = [
    (1, "2026-10-26", "4.4324"),        # SZ161130 基金净值
    (1, "2026-10-27", "4.4471"),
    (1, "2026-10-28", "4.4553"),
    (1, "2026-10-31", "4.4600"),        # 周六，只用来验周末闸门
    (8, "2026-10-27", "6.7182"),        # USCNY 中间价
    (8, "2026-10-28", "6.7165"),
    (8, "2026-10-29", "6.7204"),
    (8, "2026-10-31", "6.7200"),
    (9, "2026-10-28", "0.85621"),       # HKCNY
    (63, "2026-10-28", "0.538"),        # SH513010 基金净值（港股 QDII）
    (900001, "2026-10-28", "2.0"),      # SH510300（境内指数基金）净值
    (900002, "2026-10-28", "1.2"),      # SH518800（商品期货基金）净值
]

#: 基础校准表种子：一行 10-27 的旧值（`time` 特意不是 09:00:0X，
#: 这样"有没有被 UPDATE 过"一眼能看出来）。
BASE_CAL = [(1, "2026-10-27", 44935.404662, "10:00:00", 1)]

#: 基础 lastcalibration 种子（空值分支要删它）。
BASE_LAST = [(1, 1.5234)]


def _stock_id_of(cursor, symbol: str) -> int:
    cursor.execute("SELECT `id` FROM `stock` WHERE `symbol` = %s", (symbol,))
    row = cursor.fetchone()
    if row is None:
        raise SystemExit("演示库里没有 %s，无法对照" % symbol)
    return int(row["id"])


def build_cases() -> dict:
    """用例表。`stock_id` 对真实符号从库取，对合成符号用 :data:`SYNTHETIC_ID`。"""
    connection = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **CONFIG)
    try:
        cursor = connection.cursor()
        ids = dict(SYNTHETIC_ID)
        for symbol in ("SZ161130", "SH513010", "USCNY", "HKCNY", "JPCNY", "EUCNY"):
            ids[symbol] = _stock_id_of(cursor, symbol)
    finally:
        connection.close()

    sz, hk = ids["SZ161130"], ids["SH513010"]

    def case(name, symbol, date, value, **extra) -> dict:
        item = {
            "name": name,
            "symbol": symbol,
            "stock_id": ids[symbol],
            "date": date,
            "value": value,
            "times": 1,
            "nav": BASE_NAV,
            "seed_cal": BASE_CAL,
            "seed_last": BASE_LAST,
        }
        item.update(extra)
        return item

    return {
        "ids": ids,
        "cases": [
            # 新行：估算值 × USCNY ÷ 基金净值
            case("QDII 新行换算", "SZ161130", "2026-10-28", "29402.51"),
            # 同一笔再写一次：值没变 → 不该发 UPDATE（靠 time 列体现）
            case("QDII 同值再写", "SZ161130", "2026-10-28", "29402.51", times=2),
            # 种子里那行 10-27 值变了 → UPDATE，num 重置为 1
            case("QDII 覆盖已有行", "SZ161130", "2026-10-27", "29000"),
            # 空值：删当天校准行 + 删 lastcalibration
            case("空值双删", "SZ161130", "2026-10-27", ""),
            # '0' 也走删除（empty('0') 为真，pitfalls #123）
            case("填 0 双删", "SZ161130", "2026-10-27", "0"),
            # 港股 QDII：中间价换成 HKCNY，基金净值是自己的
            case("港股 QDII 换算", "SH513010", "2026-10-28", "5200"),
            # 境内指数基金：汇率写死 '1.0'，只除基金净值
            case("境内指数换算", "SH510300", "2026-10-28", "100"),
            # 那天 USCNY 没有中间价 → 两边都不写
            case("缺中间价", "SZ161130", "2026-10-05", "29000"),
            # 有中间价、基金自己没净值 → 分母为 0（见文件头的设计差异）
            case("缺基金净值", "SZ161130", "2026-10-29", "29000",
                 deviation="PHP 8 抛 DivisionByZeroError，PHP 7 写 0；本版拒绝",
                 expect_php="throw"),
            # 周六：无行 + 周末闸门 → 两边都不插。
            # 注意 PHP 的返回值是"要写的值"而不是"写没写"：闸门拦下时它照样返回
            # 换算结果，页面上看起来像成功 —— 所以这一例只能靠快照比，
            # 不能拿返回值当判据。
            case("周末闸门", "SZ161130", "2026-10-31", "29000"),
            # 既非境内指数也非 QDII → unhandled other symbol
            case("其它符号", "SH600030", "2026-10-28", "100", expect_php="false"),
            # 商品期货基金 → unhandled China future symbol（PHP 自己也不写）
            case("商品期货", "SH518800", "2026-10-28", "100", expect_php="false"),
            # INDA：PHP 要先抓 Yahoo 官方净值，本例两边都拒绝
            case("INDA 未迁", "INDA", "2026-10-28", "100", expect_php="false"),
            # hf_CHA50CFD：PHP 用期货 VWAP 换算，本例两边都拒绝
            case("期货 VWAP 未迁", "hf_CHA50CFD", "2026-10-28", "100",
                 expect_php="false"),
        ],
    }


class _Clock:
    """每例归零的假时钟，对齐 PHP 侧的 `DebugGetTime()` 计数器。"""

    def __init__(self) -> None:
        self.ticks = 0

    def tick(self) -> str:
        self.ticks += 1
        return "09:00:%02d" % self.ticks


class ProbeCalibration(CalibrationSql):
    """`calibrationhistory` → 临时表 + 假时钟。写方法全部沿用真身。"""

    def __init__(self, connection, clock):
        super().__init__(connection)
        self.table = CAL_TABLE
        self._clock = clock

    def now_time(self) -> str:
        return self._clock.tick()


class ProbeNav(NetValueHistorySql):
    """`netvaluehistory` → 临时表（用例自己播种，不碰真表）。"""

    def __init__(self, connection):
        super().__init__(connection)
        self.table = NAV_TABLE


class ProbeService(StockOptionService):
    """只换表名 / 时钟 / 符号翻译，`_write_calibration()` 一行不改。"""

    def __init__(self, source, clock, ids):
        super().__init__(source, net_value_sql=ProbeNav(source.connection))
        self._clock = clock
        self._ids = ids

    def _calibration_sql(self):
        return ProbeCalibration(self.connection, self._clock)

    def _last_calibration(self):
        store = LastCalibrationStore(self.connection)
        store.table = LAST_TABLE
        return store

    def _stock_id(self, symbol):
        #: 合成符号（SH510300 / SH518800 / SH600030 / INDA）不在真 `stock` 表里，
        #: id 由用例给；其余仍走真身查表 —— 中间价那四个符号两边都查真表，
        #: 查到不同 id 才会暴露问题。
        if symbol in self._ids:
            return self._ids[symbol]
        return super()._stock_id(symbol)


def create_tables(cursor) -> None:
    for table in (CAL_TABLE, LAST_TABLE, NAV_TABLE):
        cursor.execute("DROP TABLE IF EXISTS `%s`" % table)
    cursor.execute(
        "CREATE TABLE `%s` (`id` int(10) unsigned NOT NULL AUTO_INCREMENT, "
        "`stock_id` int(10) unsigned NOT NULL, `date` date NOT NULL, "
        "`close` double(13,6) NOT NULL, `time` time NOT NULL, "
        "`num` int(10) unsigned NOT NULL, PRIMARY KEY (`id`), "
        "UNIQUE KEY `date` (`date`, `stock_id`), KEY `stock_id` (`stock_id`)) "
        "ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci" % CAL_TABLE
    )
    cursor.execute(
        "CREATE TABLE `%s` (`id` int(10) unsigned NOT NULL, `close` double(13,6) NOT NULL, "
        "PRIMARY KEY (`id`)) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci"
        % LAST_TABLE
    )
    cursor.execute(
        "CREATE TABLE `%s` (`id` int(10) unsigned NOT NULL AUTO_INCREMENT, "
        "`stock_id` int(10) unsigned NOT NULL, `date` date NOT NULL, "
        "`close` double(13,6) NOT NULL, PRIMARY KEY (`id`), "
        "UNIQUE KEY `date` (`date`, `stock_id`), KEY `stock_id` (`stock_id`)) "
        "ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci" % NAV_TABLE
    )


def _insert(cursor, table: str, columns: str, rows) -> None:
    cursor.execute("DELETE FROM `%s`" % table)
    if not rows:
        return
    placeholders = ", ".join(["%s"] * len(columns.split(",")))
    cursor.executemany(
        "INSERT INTO `%s` (%s) VALUES (%s)" % (table, columns, placeholders), rows
    )


def seed(cursor, case: dict) -> None:
    _insert(cursor, NAV_TABLE, "`stock_id`, `date`, `close`", case["nav"])
    _insert(cursor, CAL_TABLE, "`stock_id`, `date`, `close`, `time`, `num`",
            case["seed_cal"])
    _insert(cursor, LAST_TABLE, "`id`, `close`", case["seed_last"])


def _cell(value) -> str:
    """一格转成 PHP ``strval(floatval(...))`` 的样子。

    MySQL 的 ``double(13,6)`` 读回来是 ``Decimal`` 还是 ``float`` 取决于驱动，
    而 PHP 那边统一走 ``strval(floatval($v))``（14 位有效数字）。同一个函数
    两边都用，才比得出"真不一样"。

    ``time`` 列要单独看一眼：pymysql 把 `TIME` 读成 `timedelta`，`str()` 出来是
    `9:00:01`（不带前导零），PHP 那边是字符串 `09:00:01` —— 不对齐这一处，
    每例都会假报差异。
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime.timedelta):
        total = int(value.total_seconds())
        return "%02d:%02d:%02d" % (total // 3600, total % 3600 // 60, total % 60)
    if isinstance(value, int):
        return str(value)
    try:
        return _php_strval_float(float(value))
    except (TypeError, ValueError):
        return str(value)


def snapshot(cursor, table, columns, order) -> list:
    """与 PHP 的 SnapshotRows 同一个口径：数值列转成最简文本。"""
    cursor.execute("SELECT %s FROM `%s` ORDER BY %s" % (columns, table, order))
    return [
        dict((key, _cell(value)) for key, value in record.items())
        for record in cursor.fetchall()
    ]


def run_python(cases: dict) -> list:
    connection = pymysql.connect(cursorclass=pymysql.cursors.DictCursor,
                                 autocommit=True, **CONFIG)
    results = []
    try:
        cursor = connection.cursor()
        create_tables(cursor)
        clock = _Clock()
        service = ProbeService(_Source(connection), clock, cases["ids"])
        for case in cases["cases"]:
            seed(cursor, case)
            clock.ticks = 0
            messages = []
            for _ in range(case["times"]):
                result = service.submit(case["symbol"], "editcalibration",
                                        case["date"], case["value"])
                messages.append(result.get("message", ""))
            results.append({
                "name": case["name"],
                "wrote": "true" if any("已写入" in m for m in messages) else "false",
                "message": " / ".join(messages),
                "cal": snapshot(cursor, CAL_TABLE,
                                "`stock_id`, `date`, `close`, `time`, `num`", "`date`"),
                "last": snapshot(cursor, LAST_TABLE, "`id`, `close`", "`id`"),
            })
        for table in (CAL_TABLE, LAST_TABLE, NAV_TABLE):
            cursor.execute("DROP TABLE IF EXISTS `%s`" % table)
    finally:
        connection.close()
    return results


class _Source:
    """服务层只用到 ``.connection``。"""

    def __init__(self, connection):
        self.connection = connection


def _php_flags(php: dict) -> tuple:
    """PHP 的返回值翻成 ``(是否被挡下, 是否抛异常)``。"""
    text = php["result"]
    return text == "false", text.startswith("throw")


def main() -> int:
    cases = build_cases()
    with open(CASES_FILE, "w", encoding="utf-8") as handle:
        json.dump(cases, handle, ensure_ascii=False, indent=1)

    env = {**os.environ, **load_mysql_env(os.environ)}
    proc = subprocess.run(
        ["php", "-d", "display_errors=stderr", PHP_SCRIPT, CASES_FILE],
        cwd=SCRATCH_DIR, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or "")
        print("PHP 探针失败，退出码 %s" % proc.returncode)
        return 1
    php_results = json.loads(proc.stdout)
    py_results = run_python(cases)

    problems = 0
    for case, php, py in zip(cases["cases"], php_results, py_results):
        php_blocked, php_threw = _php_flags(php)
        py_wrote = py["wrote"] == "true"
        diffs = []
        if php["cal"] != py["cal"]:
            diffs.append("校准表")
        if php["last"] != py["last"]:
            diffs.append("lastcalibration")
        if php_blocked and py_wrote:
            diffs.append("PHP 挡下而 Python 写了")
        if not php_blocked and not php_threw and not py_wrote and php["cal"] != py["cal"]:
            diffs.append("写没写不一致")

        #: 用例自己声明了 PHP 该是什么表现，就核一遍 —— 免得"设计差异"的
        #: 注脚过期了还挂在代码里。
        expect = case.get("expect_php")
        if expect == "false" and not php_blocked:
            diffs.append("PHP 应该挡下，实际 %s" % php["result"])
        if expect == "throw" and not php_threw:
            diffs.append("PHP 应该抛异常，实际 %s" % php["result"])

        tag = "OK  " if not diffs else "差异"
        if case.get("deviation") and not diffs:
            tag = "设计"
        print("[%s] %-16s PHP=%s" % (tag, case["name"], php["result"]))
        if case.get("deviation"):
            print("      ↳ %s" % case["deviation"])
        if diffs:
            problems += 1
            print("      不一致：%s" % "、".join(diffs))
            print("      PHP  cal=%s last=%s" % (php["cal"], php["last"]))
            print("      PY   cal=%s last=%s" % (py["cal"], py["last"]))
            print("      PY 消息：%s" % py["message"])

    print()
    if problems:
        print("%d / %d 例不一致" % (problems, len(py_results)))
        return 1
    print("%d 例全一致（校准表快照 / lastcalibration / time 列）" % len(py_results))
    return 0


CONFIG = mysql_config_from_env(load_mysql_env(os.environ))

if __name__ == "__main__":
    sys.exit(main())
