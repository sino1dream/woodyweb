# PHP 逐例对照：EchoFundTradingParagraph 的三档溢价列装配
# 源：php/ui/tradingparagraph.php:107-153 ↔ python/web/services/trading_service.py
#     TradingService.fund_table()
#
# 对照点（2026-10-19 修复「参考/实时两个并列 if 被误译成嵌套」后补）：
#   8 种 official/fair/realtime 组合，比对**列文本列表**与**标题文本**。
#   PHP 侧输出带 <font>/<b> 标签，两边都先剥标签再比（标签属渲染层，
#   Python 侧用 span class 表达，见 web/templates/fund_detail.html）。
#
# 跑法（项目惯例，stdout 只承载 JSON，诊断走 stderr）：
#   php -n -d display_errors=stderr auto/_scratch/_php_trading_check.php
#   python -B auto/_scratch/_php_trading_compare.py
"""PHP 逐例对照：五档交易表三档溢价列的装配结构（tradingparagraph.php ↔ trading_service）。

这次修复（commit 待定，2026-10-19）把 `fund_table()` 里「参考/实时」两个并列
if 摆回 PHP 原结构 —— 曾经的嵌套误译让「有实时估值但没参考估值」时实时溢价
列整列消失（用户对照生产端 sz161130cn.php 发现）。本对照钉住 8 种组合：

* 列列表（去标签后的文本）：交易/价格/数量 + 0~3 个溢价列；
* 标题文本（去标签）：``{符号}当前5档交易价格相对于EST{p1}|{p2}|{p3}的溢价``。

价格显示走**真实**的 `format_price()`（与 PHP `StockGetPriceDisplay` 的文本
部分同规则：number_format 后 rtrim 尾零），不是测试 FakeRef 的 "%.*f" ——
否则 4.600 vs 4.6 会假报差异。
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths

install_paths()

from ref.stockref import format_price  # noqa: E402
from web.services.trading_service import TradingService  # noqa: E402
# 复用五档测试替身（含 is_stock_market_trading 的真实委托）——
# 自己重写一遍五档只会得到一张空表（PHP 无挂单不摆表，同款行为）。
from web.tests.test_trading import full_ref  # noqa: E402

#: 必须 resolve()：命令行可能传相对路径（``auto/_scratch/_php_trading_compare.py``），
#: 而 run_php() 会把 cwd 切到 ROOT_DIR —— 不 resolve 的话 php 会去
#: ``woodyweb/auto/...`` 找一个不存在的文件，rc 非零且 stderr 为空，很难查。
PHP_SCRIPT = Path(__file__).resolve().with_name("_php_trading_check.php")

#: 与 PHP harness 一致的 8 种组合（false 用 None 表达）
CASES = [
    {"official": 4.545, "fair": None, "realtime": None},
    {"official": 4.545, "fair": 4.549, "realtime": None},
    {"official": 4.545, "fair": None, "realtime": 4.6},
    {"official": 4.545, "fair": 4.549, "realtime": 4.6},
    {"official": None, "fair": None, "realtime": None},
    {"official": None, "fair": 4.549, "realtime": None},
    {"official": None, "fair": None, "realtime": 4.6},
    {"official": None, "fair": 4.549, "realtime": 4.6},
]

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text or "")


class Fund:
    """假基金：三档估值按组合给值。"""

    def __init__(self, official, fair, realtime) -> None:
        self._official = official
        self._fair = fair
        self._realtime = realtime
        self.ref = _patched_ref()

    def get_stock_ref(self):
        return self.ref

    def GetStockRef(self):
        return self.ref

    def get_official_net_value(self):
        return self._official

    def GetOfficialNetValue(self):
        return self._official

    def get_fair_net_value(self):
        return self._fair

    def GetFairNetValue(self):
        return self._fair

    def get_realtime_net_value(self):
        return self._realtime

    def GetRealtimeNetValue(self):
        return self._realtime


def run_php() -> list:
    # 不用管道：Windows 下 text=True 会按本地代码页（cp936）解 PHP 的
    # UTF-8 输出，中文直接炸。写临时文件再按 utf-8 读。
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "php_out.json"
        with open(out_path, "wb") as out:
            proc = subprocess.run(
                ["php", "-n", "-d", "display_errors=stderr", str(PHP_SCRIPT)],
                stdout=out, stderr=subprocess.PIPE, cwd=str(ROOT_DIR),
            )
        if proc.returncode != 0:
            raise RuntimeError("PHP harness failed: %s"
                               % proc.stderr.decode("utf-8", "replace")[-2000:])
        return json.loads(out_path.read_text(encoding="utf-8"))


def _patched_ref():
    """full_ref() + 把 get_price_display 换成真实的 format_price。

    ``web.tests.test_trading.FakeRef`` 的替身是 ``"%.*f"``（不 rtrim 尾零），
    而真实路径（``stockref.format_price``）与 PHP ``StockGetPriceDisplay``
    一样会 rtrim —— 4.600 vs 4.6 会在这里假报差异。
    """
    ref = full_ref()
    ref.get_price_display = lambda disp=None, prev=None, precision=None: format_price(
        float(disp), float(prev), 3)
    return ref


def run_python() -> list:
    service = TradingService()
    results = []
    for case in CASES:
        fund = Fund(case["official"], case["fair"], case["realtime"])
        table = service.fund_table(fund)
        if table is None:
            # PHP 侧盘口为空整段 return；full_ref() 带齐五档，走到这里说明
            # 是真回归，直接炸出来。
            raise RuntimeError("fund_table returned None (no order book in fake ref)")
        results.append({
            "columns": [c["text"] for c in table["columns"]],
            "title": table["title"],
        })
    return results


def main() -> int:
    php_results = run_php()
    py_results = run_python()

    strict = 0
    equivalent = 0
    for index, (php, py) in enumerate(zip(php_results, py_results)):
        case = CASES[index]
        php_columns = [_strip_tags(c) for c in php["columns"]]
        php_title = _strip_tags(php["title"])
        py_columns = [_strip_tags(c) for c in py["columns"]]
        py_title = _strip_tags(py["title"])

        problems = []
        if php_columns != py_columns:
            problems.append("columns differ:\n  php: %s\n  py : %s" % (php_columns, py_columns))
        if php_title != py_title:
            problems.append("title differ:\n  php: %s\n  py : %s" % (php_title, py_title))

        if problems:
            print("CASE %d official=%s fair=%s realtime=%s" % (
                index, case["official"], case["fair"], case["realtime"]))
            for problem in problems:
                print("  " + problem)
            print("  VERDICT: DIFF")
            return 1

        strict += 1
        print("CASE %d official=%s fair=%s realtime=%s" % (
            index, case["official"], case["fair"], case["realtime"]))
        print("  columns: %s" % " | ".join(py_columns))
        print("  title  : %s" % py_title)
        print("  VERDICT: MATCH (strict)")

    print("\n%d cases, %d strict match, %d equivalent, 0 diff" % (
        len(php_results), strict, equivalent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
