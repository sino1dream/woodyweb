"""用真实 PHP 逐例对照 `editstockdividend` / `editstocksplit` 的写入口。

    cd F:\\woody\\woodyweb\\python\\auto\\_scratch
    D:\\python\\python.exe -B _php_dividend_split_compare.py

对端：`_php_dividend_split_check.php`（**加载真身类**跑 `_updateStockOptionDividend()`
/ `_updateStockOptionSplit()`，见那个文件头）。本脚本负责四件事：

1. 导出**基线**（七张表的相关行）→ `_dividend_split_baseline.json`；
2. 逐例：复原基线 → 起一个 PHP 进程 → 拿快照；
3. 逐例：复原基线 → 直接在同一个库里跑 Python 的 `submit()` → 拿同构快照；
4. 比两边快照，最后把库复原并**校验复原结果**。

## 为什么用真表而不是临时表

上一组（`editcalibration`）用三张临时表，因为那一组的差异全在 SQL 层。这一组
要连带改七张表、还要走 `TableSql(TABLE_STOCK_GROUP)` 的全表遍历，把表名全换掉
等于把"真身"也换掉了。所以改成"真表 + 前后快照 + 复原"：PHP 侧跑的是**解析
真库、调真类**的原函数，Python 侧的改动也一样落进真库，谁多写一行都看得见。

代价是**必须复原**。`restore()` 是显式 DELETE + INSERT 基线行，跑完还会重查一遍
行数与内容，确认与基线一致才打印 ok。

## 两个"该不一样"的用例

`expect="diff"` 的两例对应两处**刻意偏离**（见 `_write_stockdividend` 的 docstring）：

* `qdii_branch_with_nav` —— PHP 在 QDII 分支里用循环变量把**本标的**的净值改了
  （`$strDatePrev` / `$fNewNetValue` 来自最后一个命中的配对基金），Python 不写；
* `qdii_branch_without_nav` —— 配对基金取不到本标的净值时 PHP 直接
  `DivisionByZeroError`，Python 提前判掉、并把 dividend 行写完（PHP 那行已写、
  复权价没动）。

两例都带 `expect_keys`：差异**必须**只落在指定范围内，否则照样报失败 ——
"设计差异"不该变成掩盖真错误的借口。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(os.path.dirname(SCRATCH_DIR))
for _path in (os.path.join(PYTHON_DIR, "auto"), PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pymysql  # noqa: E402

from calc.fundcalc import qdii_est_symbol_map, qdii_symbols_for_est  # noqa: E402
from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402
from ref.stockref import _php_strval_float  # noqa: E402
from web.services.stock_option_service import StockOptionService  # noqa: E402

PHP_SCRIPT = os.path.join(SCRATCH_DIR, "_php_dividend_split_check.php")
CASE_FILE = os.path.join(SCRATCH_DIR, "_dividend_split_case.json")
BASELINE_FILE = os.path.join(SCRATCH_DIR, "_dividend_split_baseline.json")

#: 演示库里的四个符号：两只美股 ETF（有净值 + 校准行）、一只 QDII 基金、
#: 一个**配对侧**指数（没有净值、只有一行校准值）。
QQQ, SPY, SZ161130, NDX = 4, 5, 1, 6


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
def connect():
    config = mysql_config_from_env(load_mysql_env(os.environ))
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **config)


def stock_id(connection, symbol):
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
    finally:
        cursor.close()
    return row["id"] if row else None


#: 复原要管的七张表：``(表, 取基线的 SQL, 复原用的 DELETE, 主键列)``。
#: ``stocktransaction`` 用 ``id`` 删（它的自增计数器在 DELETE 后不回退，
#: 所以断言里不比 id，只比内容）。
TABLES = (
    ("stockdividend", "SELECT * FROM stockdividend", "DELETE FROM stockdividend",
     ("id", "stock_id", "date", "close")),
    ("stocksplit", "SELECT * FROM stocksplit", "DELETE FROM stocksplit",
     ("id", "stock_id", "date", "close")),
    ("calibrationhistory", "SELECT * FROM calibrationhistory WHERE stock_id IN (%s)",
     "DELETE FROM calibrationhistory WHERE stock_id IN (%s)",
     ("id", "stock_id", "date", "close", "time", "num")),
    ("netvaluehistory", "SELECT * FROM netvaluehistory WHERE stock_id IN (%s)",
     "DELETE FROM netvaluehistory WHERE stock_id IN (%s)",
     ("id", "stock_id", "date", "close")),
    ("stockhistory", "SELECT * FROM stockhistory WHERE stock_id IN (%s)",
     "DELETE FROM stockhistory WHERE stock_id IN (%s)",
     ("id", "stock_id", "date", "close", "volume", "adjclose")),
    ("stockgroupitem", "SELECT * FROM stockgroupitem", "DELETE FROM stockgroupitem",
     ("id", "stockgroup_id", "stock_id", "quantity", "cost", "record")),
    ("stocktransaction", "SELECT * FROM stocktransaction", "DELETE FROM stocktransaction",
     ("id", "groupitem_id", "quantity", "price", "fees", "filled", "remark")),
)


def export_baseline(connection, watch):
    """按 ``watch`` 里的 stock_id 把七张表的相关行抓下来。"""
    ids = ", ".join(str(int(x)) for x in watch)
    baseline = {}
    for table, select, _delete, columns in TABLES:
        sql = select % ids if "%s" in select else select
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            baseline[table] = [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
    with open(BASELINE_FILE, "w", encoding="utf-8") as handle:
        json.dump(baseline, handle, default=str, ensure_ascii=False, indent=1)
    return baseline


def restore(connection, baseline, watch):
    """把七张表复原到基线（DELETE + 显式 INSERT，含 id）。"""
    ids = ", ".join(str(int(x)) for x in watch)
    cursor = connection.cursor()
    try:
        for table, _select, delete, columns in TABLES:
            sql = delete % ids if "%s" in delete else delete
            cursor.execute(sql)
            rows = baseline[table]
            if not rows:
                continue
            names = ", ".join("`%s`" % name for name in columns)
            marks = ", ".join(["%s"] * len(columns))
            cursor.executemany(
                "INSERT INTO `%s` (%s) VALUES (%s)" % (table, names, marks),
                [tuple(row[name] for name in columns) for row in rows],
            )
    finally:
        cursor.close()
    connection.commit()


# ---------------------------------------------------------------------------
# 快照（与 PHP 侧 MakeSnapshot 同构、同序、同过滤）
# ---------------------------------------------------------------------------
def _cell(value):
    """一律转成 PHP ``strval(floatval(...))`` 的文本形状。"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if value is None:
        return ""
    try:
        return _php_strval_float(float(value))
    except (TypeError, ValueError):
        return str(value)


def snapshot(connection, watch, history_id):
    ids = ", ".join(str(int(x)) for x in watch)
    queries = {
        "dividend": ("SELECT stock_id, date, close FROM stockdividend "
                     "ORDER BY stock_id, date", ()),
        "split": ("SELECT stock_id, date, close FROM stocksplit "
                  "ORDER BY stock_id, date", ()),
        "cal": ("SELECT stock_id, date, close, num FROM calibrationhistory "
                "WHERE stock_id IN (%s) ORDER BY stock_id, date" % ids, ()),
        "nav": ("SELECT stock_id, date, close FROM netvaluehistory "
                "WHERE stock_id IN (%s) ORDER BY stock_id, date" % ids, ()),
        "history": ("SELECT id, stock_id, date, close, adjclose FROM stockhistory "
                    "WHERE stock_id = %s ORDER BY date", (history_id,)),
        "item": ("SELECT id, stock_id, quantity, cost, record FROM stockgroupitem "
                 "ORDER BY id", ()),
        "trans": ("SELECT groupitem_id, quantity, price, fees FROM stocktransaction "
                  "ORDER BY groupitem_id, quantity, price, fees", ()),
    }
    result = {}
    cursor = connection.cursor()
    try:
        for key, (sql, params) in queries.items():
            cursor.execute(sql, params)
            result[key] = [
                {name: _cell(value) for name, value in row.items()}
                for row in cursor.fetchall()
            ]
    finally:
        cursor.close()
    return result


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
class Case:
    """一例：``type`` 决定写哪个表，``setup`` 是本例专属的库内预置。

    为什么需要 ``setup``：单靠演示库里现成的数据凑不出几条分支 ——
    "同值不写""已有拆股行""配对基金没有本标的净值"都要先摆一个特定的库内状态。
    ``setup`` 与基线一起受复原保护。
    """

    def __init__(self, name, symbol, date, value, kind, setup=(), diff_keys=None):
        self.name = name
        self.symbol = symbol
        self.date = date
        self.value = value
        self.kind = kind
        self.setup = list(setup)
        self.diff_keys = diff_keys

    @property
    def option(self):
        return "editstockdividend" if self.kind == "dividend" else "editstocksplit"


def build_cases():
    """全部用例。顺序：先"该一致"的，再两个"设计差异"。"""
    return [
        # -- 分红：写入 ---------------------------------------------------
        Case("dividend_write", "QQQ", "2026-10-23", "0.5", "dividend"),
        Case("dividend_write_spy", "SPY", "2026-10-23", "0.25", "dividend"),
        # -- 分红：三种"删" -----------------------------------------------
        Case("dividend_clear", "QQQ", "2026-10-23", "", "dividend",
             setup=["INSERT INTO stockdividend (stock_id, date, close) "
                    "VALUES (4, '2026-10-23', 0.5)"]),
        Case("dividend_zero", "QQQ", "2026-10-23", "0", "dividend",
             setup=["INSERT INTO stockdividend (stock_id, date, close) "
                    "VALUES (4, '2026-10-23', 0.5)"]),
        # -- 分红：值没变 → 整段副作用不跑 ---------------------------------
        Case("dividend_same_value", "QQQ", "2026-10-23", "0.5", "dividend",
             setup=["INSERT INTO stockdividend (stock_id, date, close) "
                    "VALUES (4, '2026-10-23', 0.5)"]),
        # -- 分红：拒绝 ---------------------------------------------------
        #: `abc` → PHP 的 `floatval` 是 0，会插一条 0 分红（不报错、也不算错）；
        #: 本版直接拒绝 —— 一条 0 分红没有任何意义，还会在页面上占一行。
        Case("dividend_bad_number", "QQQ", "2026-10-23", "abc", "dividend",
             diff_keys=("dividend",)),
        Case("dividend_empty_date", "QQQ", "", "0.5", "dividend"),
        # -- 拆股：比例 ---------------------------------------------------
        Case("split_10_1", "QQQ", "2026-10-23", "10:1", "split"),
        Case("split_1_10", "SPY", "2026-10-23", "1:10", "split"),
        Case("split_3_2_truncates", "SPY", "2026-10-23", "3:2", "split"),
        # -- 拆股：0:0 删 + 三种拒绝 ---------------------------------------
        Case("split_delete", "QQQ", "2026-10-23", "0:0", "split",
             setup=["INSERT INTO stocksplit (stock_id, date, close) "
                    "VALUES (4, '2026-10-23', 10)"]),
        Case("split_zero_zero_tenths", "QQQ", "2026-10-23", "0.0:0", "split",
             setup=["INSERT INTO stocksplit (stock_id, date, close) "
                    "VALUES (4, '2026-10-23', 10)"]),
        Case("split_no_colon", "QQQ", "2026-10-23", "101", "split"),
        #: `10:0`：PHP 在算比例那一步就抛 `DivisionByZeroError`（还没插行），
        #: 本版提前拒绝。**快照必须一致**（都没写），只有 outcome 不同 ——
        #: 所以 `diff_keys=()` 是"允许异常不同、不允许落库不同"。
        Case("split_zero_denominator", "QQQ", "2026-10-23", "10:0", "split",
             diff_keys=()),
        #: `0:10` → 比例 0。PHP 先把 `stocksplit` 行插进去、把校准值写成 0，
        #: 再在"复权价 ÷ 0"处抛 `DivisionByZeroError`；本版在写任何东西之前拒绝。
        Case("split_zero_numerator", "QQQ", "2026-10-23", "0:10", "split",
             diff_keys=("split", "cal")),
        # -- 拆股：同一天已有行 → 不插流水但**照样重算** --------------------
        Case("split_already_exists", "QQQ", "2026-10-23", "10:1", "split",
             setup=["INSERT INTO stocksplit (stock_id, date, close) "
                    "VALUES (4, '2026-10-23', 10)"]),
        # -- 拆股：持仓成员的两笔流水 + 预统计回填 --------------------------
        Case("split_with_member", "QQQ", "2026-10-23", "10:1", "split",
             setup=["UPDATE stockgroupitem SET quantity = 100, record = 1 WHERE id = 12",
                    "INSERT INTO stocktransaction (groupitem_id, quantity, price, fees, "
                    "filled, remark) VALUES (12, 100, 700, 0, '2026-10-20 10:00:00', 'probe')"]),
        # -- 两个"设计差异"（见文件 docstring）----------------------------
        Case("qdii_branch_with_nav", "^NDX", "2026-10-23", "0.5", "dividend",
             setup=["DELETE FROM calibrationhistory WHERE stock_id = 6",
                    "INSERT INTO netvaluehistory (stock_id, date, close) "
                    "VALUES (6, '2026-10-22', 20000)"],
             diff_keys=("nav",)),
        Case("qdii_branch_without_nav", "^NDX", "2026-10-23", "0.5", "dividend",
             setup=["DELETE FROM calibrationhistory WHERE stock_id = 6"],
             diff_keys=("history", "nav", "cal")),
    ]


# ---------------------------------------------------------------------------
# 两侧执行
# ---------------------------------------------------------------------------
def write_case_file(case, watch):
    history_id = stock_id_for_case(case)
    payload = {
        "symbol": case.symbol,
        "date": case.date,
        "value": case.value,
        "type": case.kind,
        "setup": case.setup,
        "watch_ids": list(watch),
        "history_id": history_id,
        #: `QdiiGetArray()` 的替身（见 PHP 文件头）：est → 基金符号数组。
        "qdii_pairs": {},
    }
    mapping = qdii_est_symbol_map()
    for est in set(mapping.values()):
        payload["qdii_pairs"][est] = [k for k, v in mapping.items() if v == est]
    with open(CASE_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return payload


def stock_id_for_case(case):
    """用例涉及的符号 id；`^NDX` 这类没有真名的走单独查表。"""
    return _CACHE["ids"][case.symbol]


def run_php(case, watch):
    write_case_file(case, watch)
    env = {**os.environ, **load_mysql_env(os.environ)}
    proc = subprocess.run(
        ["php", "-d", "display_errors=stderr", PHP_SCRIPT, CASE_FILE],
        cwd=SCRATCH_DIR, env=env, capture_output=True, text=True, encoding="utf-8",
    )
    if not proc.stdout.strip():
        raise SystemExit("PHP 没有输出\nSTDERR:\n%s" % proc.stderr[-4000:])
    return json.loads(proc.stdout)


def run_python(case, service, connection, watch):
    cursor = connection.cursor()
    try:
        for sql in case.setup:
            cursor.execute(sql)
        connection.commit()
    finally:
        cursor.close()
    outcome = "ok"
    try:
        reply = service.submit(case.symbol, case.option, case.date, case.value)
        result = "true" if reply.get("ok") else "false"
    except Exception as exc:  # pragma: no cover - 只在真身抛异常时进
        outcome = "throw:%s" % type(exc).__name__
        result = "false"
    data = snapshot(connection, watch, stock_id_for_case(case))
    data["outcome"] = outcome
    data["result"] = result
    return data, reply if outcome == "ok" else {"message": outcome}


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------
KEYS = ("dividend", "split", "cal", "nav", "history", "item", "trans")


def diff_keys(php, py):
    return [key for key in KEYS if php[key] != py[key]]


def explain(php_rows, py_rows):
    """一句话说清差在哪：条数不同，还是第几条的哪一格不同。

    不打印整张表 —— 164 行校准值铺满屏幕以后，"差在哪"反而更难看出来。
    """
    if len(php_rows) != len(py_rows):
        return "行数 PHP %d ≠ PY %d" % (len(php_rows), len(py_rows))
    for index, (left, right) in enumerate(zip(php_rows, py_rows)):
        if left != right:
            cells = [key for key in left if left.get(key) != right.get(key)]
            return "第 %d 行 %s：PHP %s / PY %s" % (
                index, cells,
                {key: left.get(key) for key in cells},
                {key: right.get(key) for key in cells})
    return ""


def main():
    connection = connect()
    cases = build_cases()

    symbols = sorted({case.symbol for case in cases})
    ids = {}
    for symbol in symbols:
        value = stock_id(connection, symbol)
        if value is None:
            raise SystemExit("演示库里没有 %s，无法对照" % symbol)
        ids[symbol] = value
    _CACHE["ids"] = ids

    watch = set(ids.values())
    for symbol in symbols:
        for qdii in qdii_symbols_for_est(symbol):
            value = stock_id(connection, qdii)
            if value:
                watch.add(value)
    watch = sorted(watch)

    baseline = export_baseline(connection, watch)
    print("基线：%s" % "  ".join("%s=%d" % (t, len(rows)) for t, rows in baseline.items()))
    print("watch ids：%s" % watch)

    service = StockOptionService(source=_Source(connection))
    matched, designed, failures = [], [], []

    for case in cases:
        restore(connection, baseline, watch)
        php = run_php(case, watch)

        restore(connection, baseline, watch)
        py, reply = run_python(case, service, connection, watch)

        php_outcome = php.get("outcome")
        py_outcome = py.get("outcome")
        keys = diff_keys(php, py)
        allowed = set(case.diff_keys or ())
        extra = set(keys) - allowed
        #: outcome 不同只在**没被 diff_keys 覆盖**时才算失败。
        outcome_bad = php_outcome != py_outcome and (case.diff_keys is None or extra)

        if not keys and php_outcome == py_outcome:
            matched.append(case.name)
            print("= %-26s 一致（%d 张表快照相同）" % (case.name, len(KEYS)))
            continue

        if extra or outcome_bad:
            failures.append(case.name)
            note = "  ← 失败"
        else:
            designed.append(case.name)
            note = "  ← 按设计差异"
        print("~ %-26s 差异 %s%s" % (case.name, keys, note))
        if php_outcome != py_outcome:
            print("    outcome  PHP %s / PY %s" % (php_outcome, py_outcome))
        for key in keys:
            print("    %-8s %s" % (key, explain(php[key], py[key])))
        if reply.get("message"):
            print("    PY 消息  %s" % reply["message"])

    # -- 复原 + 校验 --------------------------------------------------------
    #: 校验用**同一个 select** 再数一遍（cal / nav / history 只备份了 watch 内的行，
    #: 直接 ``SELECT COUNT(*)`` 数的是全表，跟基线对不上）。
    restore(connection, baseline, watch)
    ids = ", ".join(str(int(x)) for x in watch)
    dirty = []
    for table, select, _delete, _columns in TABLES:
        sql = select % ids if "%s" in select else select
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            count = len(cursor.fetchall())
        finally:
            cursor.close()
        if count != len(baseline[table]):
            dirty.append("%s %d≠%d" % (table, count, len(baseline[table])))

    connection.close()
    print("\n%d 例：%d 例完全一致，%d 例按设计差异（%s），%d 例失败%s"
          % (len(cases), len(matched), len(designed), "、".join(designed) or "无",
             len(failures), "：" + "、".join(failures) if failures else ""))
    if dirty:
        print("复原不完整：%s" % "，".join(dirty))
        return 2
    if failures:
        return 1
    return 0


class _Source:
    """``StockOptionService`` 只要一个带 ``.connection`` 的对象。"""

    def __init__(self, connection):
        self.connection = connection


_CACHE = {"ids": {}}


if __name__ == "__main__":
    sys.exit(main())
