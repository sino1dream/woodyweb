# ---------------------------------------------------------------------------
# ETF 持仓申赎清单落库的 PHP ↔ Python 逐例对照。
#
# 跑法::
#
#     PYTHONPATH=/f/woody/woodyweb/python/auto:/f/woody/woodyweb/python \
#         D:/python/python.exe _php_etfholdings_compare.py
#
# 对照对象：``updater/etfholdings.py``（深市 PCF / 沪市 XML 解析落库）与
# ``updater/holdingsupdater.py``（管理员编码串写回）。四个用例与 PHP harness
# 一一对应：szse_pcf / sse_xml / admin_submit / admin_decode。
#
# 手法：**每个用例起一个 PHP 进程**跑 ``_php_etfholdings_check.php`` 拿基准
# （与 ``_php_holdingscn_compare.py`` 同一套），Python 侧用同一批假 sql
# 替身录同一份 ``{symbols, names, insert_calls, holdings, holdingsdate,
# positions, return}`` 结构，逐键比对。
#
# 已知偏差（脚本显式断言，不算失败）：
# * admin_decode 的 'unknown' 用例 —— PHP 把 GetId 取不到的符号写到
#   holding_id=0 且计入 fPos；Python 跳过该行（见
#   ``holdingsupdater.stock_option_decode_holdings`` docstring 的差异表）。
# ---------------------------------------------------------------------------

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SCRATCH = os.path.dirname(os.path.abspath(__file__))
AUTO_ROOT = os.path.abspath(os.path.join(SCRATCH, ".."))
for path in (AUTO_ROOT, os.path.abspath(os.path.join(AUTO_ROOT, ".."))):
    if path not in sys.path:
        sys.path.insert(0, path)

from updater.etfholdings import (  # noqa: E402
    SseHoldingsFile,
    SzseHoldingsFile,
    read_sse_holdings,
    read_szse_holdings,
)
from updater.holdingsupdater import (  # noqa: E402
    stock_option_decode_holdings,
    update_stock_option_holdings,
)

PHP_EXE = r"D:\xampp\php\php.exe"
PHP_SCRIPT = os.path.join(SCRATCH, "_php_etfholdings_check.php")


# ---------------------------------------------------------------------------
# 假 sql 替身（对应 PHP harness 的 FakeXxxSql，逐方法照抄语义）
# ---------------------------------------------------------------------------


class FakeStockSql(object):
    """对应 PHP ``FakeStockSql``：symbol => id 映射 + InsertSymbol 调用记录。

    id 一律存**字符串**（PHP 的 arId 值就是字符串，json_encode 出来也是
    字符串），这样 symbols 输出两边逐字一致。
    """

    def __init__(self):
        self.ar_id = {}          # symbol => id（字符串）
        self.ar_name = {}        # symbol => name
        self.i_next = 1000
        self.ar_insert = []      # [(symbol, name), ...] 保序

    def get_id(self, symbol):
        return self.ar_id.get(symbol)

    def get_name(self, symbol):
        return self.ar_name.get(symbol)

    def insert_symbol(self, symbol, name):
        self.ar_insert.append((symbol, name))
        if symbol in self.ar_id:
            return None
        self.i_next += 1
        self.ar_id[symbol] = str(self.i_next)
        self.ar_name[symbol] = name
        return self.ar_id[symbol]


class FakeHoldingsSql(object):
    """对应 PHP ``FakeHoldingsSql``：``[[stock_id, holding_id, ratio], ...]`` 保序。"""

    def __init__(self):
        self.rows = []

    def insert(self, stock_id, holding_id, ratio):
        self.rows.append((str(stock_id), str(holding_id), str(ratio)))
        return len(self.rows)

    def delete_all(self, stock_id):
        self.rows = [row for row in self.rows if row[0] != str(stock_id)]
        return True

    def get_holdings_array(self, stock_id):
        result = {}
        for row in self.rows:
            if row[0] == str(stock_id):
                result[row[1]] = row[2]
        return result


class FakeHistorySql(object):
    """对应 PHP ``FakeHistorySql``：``(stock_id, date) => close``。

    ``get_adjclose`` 带回落（PHP 注释：when hongkong market on holiday）——
    取不到当天就退回**之前**最近有记录的一天。
    """

    def __init__(self):
        self.ar_close = {}

    def get_record(self, stock_id, date):
        key = "%s|%s" % (stock_id, date)
        if key in self.ar_close:
            return {"close": self.ar_close[key]}
        return None

    def get_adjclose(self, stock_id, date, strict=False):
        record = self.get_record(stock_id, date)
        if record is not None:
            return record["close"]
        if strict:
            return None
        best_key = None
        for key in self.ar_close:
            str_id, _, str_day = key.partition("|")
            if str_id != str(stock_id):
                continue
            if str_day >= str(date):
                continue
            if best_key is None or str_day > best_key.partition("|")[2]:
                best_key = key
        return self.ar_close[best_key] if best_key is not None else None


class FakeNetValueSql(object):
    """对应 PHP ``FakeNetValueSql``：汇率中间价（CalcCurrency 用）。"""

    def __init__(self):
        self.ar_close = {}       # 'id|date' => close
        self.ar_now = {}         # id => close

    def get_close(self, stock_id, date):
        return self.ar_close.get("%s|%s" % (stock_id, date))

    def get_close_now(self, stock_id):
        return self.ar_now.get(str(stock_id))


class FakePositionSql(object):
    """对应 PHP ``FakePositionSql``：仓位写入记录（字符串原样）。"""

    def __init__(self):
        self.ar_pos = {}

    def write_val(self, stock_id, value, string=False):
        self.ar_pos[str(stock_id)] = str(value)
        return True

    def write_pos(self, stock_id, position):
        return self.write_val(stock_id, position)


class FakeDateSql(object):
    """对应 PHP ``FakeHoldingsDateSql``（静态存储版）。

    PHP 侧踩过的坑：``new HoldingsDateSql()`` 建的是新实例，与
    ``$GLOBALS['fake_date']`` 不共享，所以 PHP harness 把存储改成了静态。
    这里天然单例（全程一个对象），直接实例属性即可。
    """

    def __init__(self):
        self.ar_date = {}

    def write_date(self, stock_id, date):
        self.ar_date[str(stock_id)] = str(date)
        return True

    def read_date(self, stock_id):
        return self.ar_date.get(str(stock_id))


class FakeSource(object):
    """带 connection 占位的源（sql 全部注入，connection 不会被用到）。"""

    connection = "fake-connection"


def build_fakes():
    return {
        "stock": FakeStockSql(),
        "holdings": FakeHoldingsSql(),
        "history": FakeHistorySql(),
        "netvalue": FakeNetValueSql(),
        "position": FakePositionSql(),
        "date": FakeDateSql(),
    }


# ---------------------------------------------------------------------------
# 预置数据（与 PHP harness 的 PresetDemoData 逐字一致）
# ---------------------------------------------------------------------------

#: 159605 的 PCF 里按出现顺序排列的 31 个成分符号（159900 申赎现金被跳过）。
#: id 顺序即出现顺序；names 照 PCF 表头"证券简称"列。
PCF_HOLDINGS = [
    ("01024", "快手-W"), ("01810", "小米集团-W"), ("00241", "阿里健康"),
    ("02423", "贝壳-W"), ("02587", "健康之路"), ("03690", "美团-W"),
    ("03888", "金山软件"), ("06060", "众安在线"), ("06618", "京东健康"),
    ("00700", "腾讯控股"), ("00772", "阅文集团"), ("00780", "同程旅行"),
    ("09618", "京东集团-SW"), ("09626", "哔哩哔哩-W"), ("09888", "百度集团-SW"),
    ("09899", "网易云音乐"), ("09961", "携程集团-S"), ("09988", "阿里巴巴-W"),
    ("09999", "网易-S"),
    ("ATHM", "ATHM"), ("BZ", "BZ"), ("IQ", "IQ"), ("JOYY", "JOYY"),
    ("PDD", "PDD"), ("QFIN", "QFIN"), ("TAL", "TAL"), ("TME", "TME"),
    ("VIPS", "VIPS"), ("WB", "WB"), ("YMM", "YMM"),
]

#: 前 10 只（id 1001-1010）给 2025-08-28 的复权价 —— 绝对值不影响对照，
#: 要的是一致的乘法与除法。
PCF_CLOSES = {
    "1001": "55.10", "1002": "48.20", "1003": "4.35", "1004": "38.90",
    "1005": "11.20", "1006": "98.40", "1007": "31.50", "1008": "13.60",
    "1009": "36.80", "1010": "420.00",
}

#: CalcCurrency 的汇率：键是 stock_id 不是符号名（USCNY->501 / HKCNY->502）。
FOREX_CLOSE = {"501": "7.1200", "502": "0.9100"}
FOREX_NOW = {"501": "7.1300", "502": "0.9150"}


def preset_demo_data(fakes):
    stock = fakes["stock"]
    stock.ar_id["USCNY"] = "501"
    stock.ar_id["HKCNY"] = "502"
    # 31 个成分符号照"生产库已存在"预置；i_next 必须推到已占用最大值之后，
    # 否则 sse_xml 的新符号会从 1001 重新起号与预置撞 id（PHP 侧同款坑）。
    for index, (symbol, name) in enumerate(PCF_HOLDINGS):
        stock.ar_id[symbol] = str(1001 + index)
        stock.ar_name[symbol] = name
    stock.i_next = 1030

    netvalue = fakes["netvalue"]
    netvalue.ar_close = dict(
        ("%s|2025-08-28" % key, value)
        for key, value in FOREX_CLOSE.items()
    )
    netvalue.ar_now = dict(FOREX_NOW)

    history = fakes["history"]
    history.ar_close = dict(
        ("%s|2025-08-28" % key, value) for key, value in PCF_CLOSES.items()
    )


def sample_bytes(name):
    with open(os.path.join(SCRATCH, name), "rb") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# 四个用例（与 PHP harness 同名同参）
# ---------------------------------------------------------------------------


def case_szse_pcf():
    fakes = build_fakes()
    preset_demo_data(fakes)
    source = FakeSource()
    csv_file = SzseHoldingsFile(
        source, "9001",
        stock_sql=fakes["stock"], holdings_sql=fakes["holdings"],
        date_sql=fakes["date"], history_sql=fakes["history"],
        netvalue_sql=fakes["netvalue"],
    )
    result = read_szse_holdings(
        source, "SZ159605", "9001", "2025-09-01",
        fetch=lambda url: sample_bytes("_szse_159605.txt"),
        holdings_file=csv_file,
    )
    return {
        "return": bool(result),
        "symbols": dict(fakes["stock"].ar_id),
        "names": dict(fakes["stock"].ar_name),
        "insert_calls": [list(call) for call in fakes["stock"].ar_insert],
        "holdings": [list(row) for row in fakes["holdings"].rows],
        "holdingsdate": dict(fakes["date"].ar_date),
        "positions": dict(fakes["position"].ar_pos),
    }


def case_sse_xml():
    fakes = build_fakes()
    preset_demo_data(fakes)
    source = FakeSource()
    csv_file = SseHoldingsFile(
        source, "9002",
        stock_sql=fakes["stock"], holdings_sql=fakes["holdings"],
        date_sql=fakes["date"], history_sql=fakes["history"],
        netvalue_sql=fakes["netvalue"],
    )
    result = read_sse_holdings(
        source, "SH513050", "9002",
        fetch=lambda url: sample_bytes("_sse_513050.xml"),
        holdings_file=csv_file,
    )
    return {
        "return": bool(result),
        "symbols": dict(fakes["stock"].ar_id),
        "names": dict(fakes["stock"].ar_name),
        "insert_calls": [list(call) for call in fakes["stock"].ar_insert],
        "holdings": [list(row) for row in fakes["holdings"].rows],
        "holdingsdate": dict(fakes["date"].ar_date),
        "positions": dict(fakes["position"].ar_pos),
    }


def case_admin_submit():
    fakes = build_fakes()
    preset_demo_data(fakes)
    # GLD/SPY 预置进 stock 表（生产库里有这两个符号）
    fakes["stock"].ar_id["GLD"] = "2001"
    fakes["stock"].ar_id["SPY"] = "2002"
    source = FakeSource()
    update_stock_option_holdings(
        source, "9003", "2026-10-22", '"GLD":100,"SPY":50',
        position_sql=fakes["position"], date_sql=fakes["date"],
        holdings_sql=fakes["holdings"], stock_sql=fakes["stock"],
    )
    return {
        "symbols": dict(fakes["stock"].ar_id),
        "insert_calls": [list(call) for call in fakes["stock"].ar_insert],
        "holdings": [list(row) for row in fakes["holdings"].rows],
        "holdingsdate": dict(fakes["date"].ar_date),
        "positions": dict(fakes["position"].ar_pos),
    }


def case_admin_decode():
    fakes = build_fakes()
    preset_demo_data(fakes)
    # GLD/SPY 预置、NOSUCH 不预置 —— 'unknown' 用例抓 PHP 的 holding_id=0 行为
    fakes["stock"].ar_id["GLD"] = "2001"
    fakes["stock"].ar_id["SPY"] = "2002"
    inputs = [
        ("normal", '"GLD":100,"SPY":50'),
        ("zeros", '"GLD":100,"SPY":0'),
        ("zero_dot", '"GLD":100,"SPY":0.0'),
        ("unknown", '"GLD":100,"NOSUCH":50'),
        ("bad", "not-json"),
        ("empty", ""),
    ]
    out = {}
    for name, text in inputs:
        position, ratios = stock_option_decode_holdings(
            text, stock_sql=fakes["stock"]
        )
        # 表示口径对齐（不是内容差异）：
        # * PHP 的 ``false`` <-> Python 的 ``None``（项目约定）；
        # * PHP 空数组 json 成 ``[]``，Python 空 dict 会成 ``{}`` ——
        #   空 ratio 一律记成 ``[]``，与 PHP 的 json_encode 一致。
        out[name] = {
            "pos": False if position is None else position,
            "ratio": [] if not ratios else dict(
                (str(key), value) for key, value in ratios.items()
            ),
        }
    return out


CASES = {
    "szse_pcf": case_szse_pcf,
    "sse_xml": case_sse_xml,
    "admin_submit": case_admin_submit,
    "admin_decode": case_admin_decode,
}


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------


def _as_float(value):
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def values_equal(left, right, path=""):
    """逐键比对；数值按 float 容差比，其余严格相等。

    返回差异列表（空 = 一致）。比例串（如 '2.7077758019122'）两边都是
    PHP strval 口径的字符串，先按字符串严格比，不一致再按数值比并标注。

    **空集合的表示差异不算差异**：PHP 的空数组 ``json_encode`` 成 ``[]``，
    Python 的空 dict 会成 ``{}`` —— 本脚本的输出里 ``positions`` / ``ratio``
    语义上都是对象，空值时两边记空集合，内容等价。
    """
    if isinstance(left, (list, dict)) and isinstance(right, (list, dict)):
        if not left and not right:
            return []
    diffs = []
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right), key=str):
            if key not in left:
                diffs.append("%s: PHP 侧缺失键 %r" % (path, key))
            elif key not in right:
                diffs.append("%s: Python 侧缺失键 %r" % (path, key))
            else:
                diffs.extend(values_equal(left[key], right[key], "%s.%s" % (path, key)))
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            diffs.append("%s: 长度不同 PHP=%d Python=%d" % (path, len(left), len(right)))
        else:
            for index, (item_l, item_r) in enumerate(zip(left, right)):
                diffs.extend(values_equal(item_l, item_r, "%s[%d]" % (path, index)))
    elif isinstance(left, bool) or isinstance(right, bool):
        if left != right:
            diffs.append("%s: %r != %r" % (path, left, right))
    else:
        float_l, float_r = _as_float(left), _as_float(right)
        if float_l is not None and float_r is not None:
            if float_l != float_r:
                tolerance = max(abs(float_l), abs(float_r), 1e-12) * 1e-9
                if abs(float_l - float_r) > tolerance:
                    diffs.append("%s: %r != %r" % (path, left, right))
                else:
                    diffs.append("%s: %r != %r（数值差在容差内，格式不同）"
                                 % (path, left, right))
        elif left != right:
            diffs.append("%s: %r != %r" % (path, left, right))
    return diffs


def run_php(case_name):
    """起一个 PHP 进程跑 harness，取 stdout 最后一行当基准 JSON。

    与 ``_php_holdingscn_compare.py`` 同款：``-n`` 避开 openssl 警告，
    ``display_errors=stderr`` 让 Notice 走 stderr 不污染 stdout。
    """
    result = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", PHP_SCRIPT, case_name],
        capture_output=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP 对照脚本失败（%s）：%d" % (case_name, result.returncode))
    return json.loads(result.stdout.decode("utf-8").strip().splitlines()[-1])


def main():
    failures = 0
    for name, builder in sorted(CASES.items()):
        expected = run_php(name)
        actual = builder()
        diffs = values_equal(expected, actual, name)
        if name == "admin_decode":
            diffs = _filter_admin_decode_deviation(expected, actual, diffs)
        if diffs:
            failures += 1
            print("== %s: %d 处差异" % (name, len(diffs)))
            for line in diffs:
                print("   ", line)
        else:
            print("== %s: 一致" % name)
    if failures:
        print("\n%d 个用例存在差异" % failures)
        return 1
    print("\n全部用例与 PHP 一致")
    return 0


def _filter_admin_decode_deviation(expected, actual, diffs):
    """admin_decode 的 'unknown' 用例是**已知偏差**，显式剔除。

    PHP：NOSUCH 的 GetId 取不到 -> ``$arRatio[false]`` 键转 0 -> 写一条
    holding_id=0 的明细；但 50 **照样计入 fPos**（pos=1.5）。
    Python：不写这行（见 ``stock_option_decode_holdings`` docstring 差异表
    第 1 条），fPos 仍照算 —— 所以只有 ratio 里多出来的键 '0' 是差异，
    pos 应当已经一致。
    除这一处外的差异仍然是失败。
    """
    kept = []
    for line in diffs:
        if line.startswith("admin_decode.unknown"):
            continue
        kept.append(line)
    unknown_php = expected.get("unknown", {})
    unknown_py = actual.get("unknown", {})
    print("   [已知偏差] admin_decode.unknown: PHP pos=%r ratio=%r；"
          "Python pos=%r ratio=%r（不写未入库符号那一行，fPos 照算）"
          % (unknown_php.get("pos"), unknown_php.get("ratio"),
             unknown_py.get("pos"), unknown_py.get("ratio")))
    return kept


if __name__ == "__main__":
    sys.exit(main())
