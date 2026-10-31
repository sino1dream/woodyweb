"""义工群对冲计算器（``overnightcnhcn.php``）的 PHP ↔ Python 逐例对照。

跑 ``_php_overnight_cnh_check.php``，把 PHP 从源码原样抠出来的算式与 Python
服务层逐格比：

========================================  ==========================================
对照项                                      Python 侧对应
========================================  ==========================================
``_convertCnhInput()`` 四种后缀             :func:`overnight_cnh_service.convert_cnh_input`
``_fundCnhInput()``（费率 × 溢价 × 仓位）    :func:`overnight_cnh_service._fund_cnh_input`
``_futureCnhInput()``（期货 × 汇率 × 乘数）  :func:`overnight_cnh_service._future_cnh_input`
``_echoOverNightCnhItem()`` 的六格骨架      :func:`overnight_cnh_service.build_row`
``GetNumberDisplay($fHedge, 0)``           :func:`rotation_service.number_display`
========================================  ==========================================

## 对照口径：喂同样的原始数，比公式与文案

PHP 侧的盘口五档只有"现场抓新浪"这一条路（`grep realtimequote php/` 为空 ——
那张快照表是迁移时才加的），所以**没法**让 PHP 自己从库里取到和 Python 一样的
输入。本脚本把 Python 从真库读到的原始数（盘口价/量、官方净值、仓位、
对冲值、中间价、期货价）写成 JSON 喂给 PHP，再拿 PHP 算出来的结果和
Python 用同一个真引用对象算出来的结果逐格比。

于是分工是：

* **本脚本覆盖**：四种后缀的换算、费率表、溢价口径、"金额 ÷ 仓位 ÷ 价格"、
  对冲值那一支的两层取整、`strval()` 的尾巴、文案拼接、超量标红；
* **不在本脚本范围**：那些原始数是谁从哪张表取的 —— 由
  ``web/tests/test_overnight_cnh_page.py``（47 例）与真库冒烟覆盖。

## 跳过哪些例子

Python 判定"算不出"（``build_row`` 返回非空 reason）而 PHP 会**除零**
（PHP 8 抛 ``DivisionByZeroError`` 直接 500）的例子不喂给 PHP —— 那不是
"两边不一致"，是 PHP 根本出不来结果（本版刻意改成不崩，见 README 偏离 2）。
这类例子单独统计并打印，数量会随演示库的数据缺口变化。

用法（仓库根目录）：

    D:/python/python.exe -B python/auto/_scratch/_php_overnight_cnh_compare.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for path in (str(ROOT / "python"), str(ROOT / "python" / "auto")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.stockdata import MySqlFundDataSource  # noqa: E402
from web.services import overnight_cnh_service as service  # noqa: E402

#: 要跑的输入串：四种后缀 + 正负 + 0。
INPUTS = ("100000", "-100000", "100000F", "-100000F", "0", "1MCL", "1MGC")

_failed = 0
_skipped = 0
_excluded = 0


def _check(name, got, want, note=""):
    global _failed
    if got == want:
        print("  OK   %-52s %s" % (name, _short(want)))
        return True
    _failed += 1
    print("  FAIL %-52s" % name)
    print("        PHP : %s" % _short(want))
    print("        PY  : %s" % _short(got))
    if note:
        print("        注  : %s" % note)
    return False


def _skip(name, note):
    global _skipped
    _skipped += 1
    print("  SKIP %-52s %s" % (name, note))


def _short(value, limit=88):
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _cell_text(cell):
    """Python 的格子（``str`` 或 ``(文本, 颜色)``）→ PHP ``GetFontElement`` 形态。

    PHP 的 ``GetHtmlElement`` 拼的是 ``<font color=red>``（属性**不加引号**），
    这里必须一致，否则一次对照会报出几十条"格式不同但内容相同"的噪声。
    """
    if isinstance(cell, tuple):
        text, color = cell
        return "<font color=%s>%s</font>" % (color, text)
    return cell


def _strip_tags(html):
    return re.sub(r"<[^>]+>", "", html)


class _SyntheticCnyRef:
    def __init__(self, value):
        self.value = value

    def get_val(self, date=None):
        return self.value


class _SyntheticFuture:
    def __init__(self, value):
        self.value = value

    def get_val(self, date=None):
        return self.value


class _SyntheticRef:
    """合成用例用的最小引用：只提供 `_futureCnhInput` 会碰的那几个方法。"""

    def __init__(self, base):
        self.base = base

    def get_symbol(self):
        return self.base["symbol"]

    def get_position(self):
        return self.base["position"]

    def get_val(self, date=None):
        return self.base["price"]

    def get_realtime_net_value(self):
        return self.base["realtime"]

    def get_cny_ref(self):
        return _SyntheticCnyRef(self.base["cny"])


def _php_would_divide_by_zero(base, text):
    """这个例子喂给 PHP 会不会除零（PHP 8 抛 ``DivisionByZeroError``）。

    两条：

    * ``F`` 那一支：``价格 ÷ 官方净值`` —— 净值缺了就炸；
    * 持仓折股那一支：``CNH ÷ 当日汇率`` —— 季报日期取不到时汇率也是 0，同样炸。

    ``MCL`` / ``MGC`` 那一支**不会炸**（取不到就是 0），但本版按"算不出"
    处理，那一侧由 ``reason`` 挡住，不在这里管。
    """
    if text.endswith("F") and not base["official"]:
        return True
    if not base["has_stock_ref"] and base["is_holdings"] and not base["cny_at_date"]:
        return True
    return False


def _run_php(cases_path):
    script = str(HERE / "_php_overnight_cnh_check.php")
    proc = subprocess.run(
        ["php", "-d", "display_errors=stderr", script, str(cases_path)],
        cwd=str(HERE), env=dict(os.environ),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace")[:4000])
        raise SystemExit("PHP 探针失败 rc=%d" % proc.returncode)
    return json.loads(proc.stdout.decode("utf-8"))


def _primitives(symbol, ref, source, futures):
    """把一只标的的原始数摘成 PHP 探针要的形状（字段名与探针一一对应）。"""
    stock_ref = service.stock_ref_of(ref)
    call = service._call

    def book(snake, camel, is_sell):
        value = call(stock_ref, snake, camel, is_sell)
        return "" if value is None else str(value)

    official = service._floatval(call(ref, "get_official_net_value", "GetOfficialNetValue"))
    position = service._floatval(call(ref, "get_position", "GetPosition"))
    stock_id = call(ref, "get_stock_id", "GetStockId")
    has_stock_ref = service._has_method(ref, "get_stock_ref", "GetStockRef")
    est_ref = call(ref, "get_est_ref", "GetEstRef") if has_stock_ref else None
    hedge = service._hedge_of(source, symbol, stock_id)

    hedge_symbol = service._hedge_symbol_of(est_ref, symbol) if est_ref else ""
    cny_ref = service._choose_cny_ref(ref, symbol)
    realtime = call(ref, "get_realtime_net_value", "GetRealtimeNetValue")
    holdings = _holdings_primitive(ref, source, cny_ref)

    return {
        "symbol": symbol,
        "official": official,
        "position": position,
        #: PHP 的 `if ($est_ref)` —— 判的是**中间层建没建出来**，不是
        #: "有没有 GetStockRef"。名称保持与探针里的键一致。
        "has_stock_ref": bool(est_ref),
        "hedge": hedge if est_ref else 0.0,
        "hedge_symbol": hedge_symbol,
        "bid_price": book("get_available_price", "GetAvailablePrice", True),
        "ask_price": book("get_available_price", "GetAvailablePrice", False),
        "bid_quantity": book("get_available_quantity", "GetAvailableQuantity", True),
        "ask_quantity": book("get_available_quantity", "GetAvailableQuantity", False),
        "realtime": service._floatval(realtime) if service._php_truthy(realtime) else None,
        "price": service._floatval(call(ref, "get_val", "GetVal")),
        "cny": service._floatval(call(cny_ref, "get_val", "GetVal")),
        "future_cl": futures["hf_CL"],
        "future_gc": futures["hf_GC"],
        **holdings,
    }


def _holdings_primitive(ref, source, cny_ref):
    """持仓型那一支要的原始数：季报日期、当日汇率、净值、成分股与收盘价。

    非持仓型（没有 ``GetHoldingsDate``）时给 "空持仓" 的形状 ——
    PHP 那边的 `else if ($ref['is_holdings'])` 为假，两个分支都不进。
    """
    call = service._call
    if not service._has_method(ref, "get_holdings_date", "GetHoldingsDate"):
        return {"is_holdings": False, "holdings": [], "net_value": 0.0, "cny_at_date": 0.0}

    date = call(ref, "get_holdings_date", "GetHoldingsDate")
    ratios = call(ref, "get_holdings_ratio_array", "GetHoldingsRatioArray") or {}
    history = service._history_sql(source)
    items = []
    for holding_id, ratio in ratios.items():
        close = service._history_close(history, holding_id, date) if history else 0.0
        items.append({
            "id": holding_id,
            "ratio": str(ratio),
            "close": close,
            "symbol": str(call(source, "get_stock_symbol", "GetStockSymbol", holding_id) or ""),
        })
    return {
        "is_holdings": True,
        "holdings": items,
        "net_value": service._floatval(
            call(ref, "get_net_value_string", "GetNetValueString")),
        "cny_at_date": service._floatval(
            call(cny_ref, "get_val", "GetVal", date)) if date else 0.0,
    }


def main() -> int:
    global _excluded
    source = MySqlFundDataSource.from_env()
    symbols = service.overnight_symbols()
    refs = [service.build_fund_reference(symbol, source) for symbol in symbols]
    futures = {
        name: service._floatval(
            service._call(service.build_future_reference(name, source), "get_val", "GetVal"))
        for name in ("hf_CL", "hf_GC")
    }

    cases = []
    rows = {}
    total = 0
    for index, symbol in enumerate(symbols):
        ref = refs[index]
        if ref is None:
            print("  跳过 %s：引用对象建不出来" % symbol)
            continue
        base = _primitives(symbol, ref, source, futures)
        for text in INPUTS:
            total += 1
            #: **先算 Python 这一行**：reason 非空说明净值/仓位/价格缺一项，
            #: 那种例子喂给 PHP 会直接除零（DivisionByZeroError 整页 500），
            #: 不是"两边不一致"，所以根本不进对照集。
            rows[(symbol, text)] = service.build_row(
                symbol, ref, text, source=source)
            if rows[(symbol, text)][1]:
                _excluded += 1
                continue
            if _php_would_divide_by_zero(base, text):
                #: PHP 的换算在"有没有挂单"那道闸门**之前**（见探针的
                #: `_echoOverNightCnhItem`），所以哪怕这一行最后只剩代码一格，
                #: 它也已经除过一次零了。这类例子 PHP 根本出不来结果。
                _excluded += 1
                continue
            case = dict(base)
            case["input"] = text
            case["is_sell"] = service.is_sell_input(text)
            cases.append(case)

    #: 中间 JSON 写进临时目录，不在 `_scratch` 里留垃圾（上一轮留了十几个
    #: `_*.json` 清理起来很费事）。
    workdir = Path(tempfile.mkdtemp(prefix="overnight_cnh_"))
    cases_path = workdir / "cases.json"
    cases_path.write_text(
        json.dumps({"symbols": symbols, "cases": cases}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    php = _run_php(cases_path)

    print("== 1. 申购费率表（StockGetFundFeeRatio 真身）==")
    for symbol, want in php["fees"].items():
        _check(symbol, service.stock_get_fund_fee_ratio(symbol), want)

    print("\n== 2. 六格骨架（_convertCnhInput + _echoOverNightCnhItem）==")
    for key, payload in php["rows"].items():
        symbol, _, text = key.partition("@")
        row, reason = rows[(symbol, text)]
        if row is None:
            _skip(key, "引用对象为空")
            continue
        if reason:
            _skip(key, "Python 判定算不出，PHP 会除零 —— " + reason)
            continue

        cells = row.cells
        _check("%s 格数" % key, len(cells), payload["cells"])
        _check("%s 代码" % key, cells[0], payload["symbol"])
        if payload["cells"] == 1:
            continue
        _check("%s 价格" % key, service._floatval(cells[1]), round(payload["price"], 6))
        _check("%s 可成交数量" % key, cells[2], payload["quantity"])
        _check("%s 建议数量" % key, _cell_text(cells[3]), payload["hint"])
        _check("%s 超量标红" % key, isinstance(cells[3], tuple), payload["hint_over"])
        _check("%s 对冲值" % key, _cell_text(cells[4]), payload["hedge_display"])
        if payload["memo"]:
            _check("%s 建议对冲操作" % key, _strip_tags(cells[5]), payload["memo"])
        else:
            _check("%s 无建议对冲操作" % key, len(cells), 5)

    print("\n== 3. 金额 → 对冲敞口（_convertCnhInput 四种后缀）==")
    for key, payload in php["rows"].items():
        symbol, _, text = key.partition("@")
        ref = refs[symbols.index(symbol)]
        if ref is None:
            continue
        stock_ref = service.stock_ref_of(ref)
        got = service.convert_cnh_input(
            ref, stock_ref, text,
            lambda name: service.build_future_reference(name, source))
        want = payload.get("fcnh")
        if want is None:
            continue
        if got is None:
            _skip("%s 换算" % key, "Python 判定取不到净值/期货价")
            continue
        #: PHP 那边打印的是 float，比到 6 位小数即可（同一批 double 运算）。
        _check("%s 换算" % key, round(float(got), 6), round(float(want), 6))

    print("\n== 4. 合成用例：MCL / MGC 两支（演示库没有期货快照，只能喂数）==")
    #: ``hf_CL`` / ``hf_GC`` 在演示库里连 ``realtimequote`` 行都没有，真数据
    #: 那一路全是 0 —— 所以这两支的算式（尤其 `$fCnh *= 1.0 + ($fPremium - 1.0) * $fPos`
    #: 那个**只补仓位那一部分**的写法）只能靠合成数验。
    synthetic = []
    base_case = {
        "symbol": "SZ162411", "official": 1.0, "position": 0.95,
        "has_stock_ref": True, "hedge": 1367.728, "hedge_symbol": "XOP",
        "bid_price": "1.054", "ask_price": "1.055",
        "bid_quantity": "1214400", "ask_quantity": "103600",
        "realtime": 1.10, "price": 1.054, "cny": 6.7138,
        "future_cl": 70.0, "future_gc": 4000.0,
        "is_holdings": False, "holdings": [], "net_value": 0.0, "cny_at_date": 0.0,
    }
    for text in ("1MCL", "-1MCL", "3MCL", "1MGC", "-2MGC"):
        case = dict(base_case)
        case["input"] = text
        case["is_sell"] = service.is_sell_input(text)
        synthetic.append(case)
    synthetic_path = workdir / "synthetic.json"
    synthetic_path.write_text(
        json.dumps({"cases": synthetic}, ensure_ascii=False, indent=1), encoding="utf-8")
    php_synthetic = _run_php(synthetic_path)

    for key, payload in php_synthetic["rows"].items():
        symbol, _, text = key.partition("@")
        got = service.convert_cnh_input(
            _SyntheticRef(base_case), None, text,
            lambda name: _SyntheticFuture(base_case[
                "future_cl" if name == "hf_CL" else "future_gc"]))
        _check("%s 换算（合成）" % key, round(float(got), 6), round(float(payload["fcnh"]), 6))

    print("\n%s" % ("全部一致" if _failed == 0 else "有 %d 项不一致" % _failed))
    print("对照 %d 例（%d 只 × %d 个输入串，去掉 PHP 会除零/本版判定算不出的 %d 例）"
          % (len(cases), len(symbols), len(INPUTS), _excluded))
    print("其中 %d 例在逐格比对里被跳过（引用对象为空）" % _skipped)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
