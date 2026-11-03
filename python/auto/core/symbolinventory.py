"""QDII 符号清单：撑起各 QDII 页面所需的 ``stock`` 行与 ``fundpair`` 行。

## 为什么单独一个模块

P0 数据层补齐（见 ``README_p0_data_layer.md``）要把 100 多只基金写进演示库，
写什么、写多少**只能有一个出处**。原先这份逻辑散在探针
``_scratch/_p0_1_symbol_inventory.py`` 里， seed 脚本要另写一份 ——
两份漂移了就会写错库，所以抽成模块，探针与 seed 脚本都引它。

## 口径

* 基金清单 = 五个数组合集（去重、保序）：
  ``qdii`` 41 + ``qdiihk`` 38 + ``qdiijp`` 5 + ``qdiieu`` 3 + ``qdiimix`` 15，
  五个数组**互不重叠**，合计 **102 只**；
* est 符号 = ``calc.fundcalc`` 的 ``qdii_est_symbol()`` 族
  （与 PHP ``qdiiref.php:19-37`` 的 ``QdiiGetEstArray()`` 逐条对照过），
  再按**杠杆替代**修正（``stockdata.py:279-284`` 同顺序）：
  ``qdii_qqq_symbols()`` 用 ``QQQ``、``qdii_spy_symbols()`` 用 ``SPY``
  取数 —— 指数 ``^NDX`` / ``^GSPC`` 不落 ``netvaluehistory`` /
  ``stockhistory``，拿不到净值与行情；
* QdiiMix 成员**没有 est 符号**，估值走持仓口径（CK4 ``HoldingsReference``），
  但它们仍要在 ``stock`` 表里（生产端页面有它们的行）。

## 页面归属 ≠ 数组归属

这条曾经是"漂移说明"，现在两边已经对齐：2026-10-30 把 ``qdii_hk`` 补到 38、
``qdiimix`` 收到 15、``chinaindex`` 补到 27（见 ``README_category_pages_migration.md``
§七）。并集仍是 102 只 —— 那 17 只只是换了个数组，没有进出。

还留着一处**故意的不相等**：持仓更新的覆盖面是 32 只
（``qdii_mix_holdings_symbol_array()``，``_qdiimix.php`` 的 32 个详情页），
比分类页数组的 15 只大。**本模块只算并集**，页面归属与持仓覆盖都在别处体现。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from calc.fundcalc import (
    qdii_eu_est_symbol,
    qdii_hk_est_symbol,
    qdii_jp_est_symbol,
    qdii_est_symbol,
    qdii_qqq_symbols,
    qdii_spy_symbols,
)
from core.stocksymbol import (
    qdii_eu_get_symbol_array,
    qdii_get_symbol_array,
    qdii_hk_get_symbol_array,
    qdii_jp_get_symbol_array,
    qdii_mix_get_symbol_array,
)

#: 汇率符号（四个地区各一条中间价）。``stockdata._get_default_cny_est()``
#: 按基金的 QDII 地区选一条，缺了估值整条链路断。
FOREX_SYMBOLS = ("USCNY", "HKCNY", "EUCNY", "JPCNY")

#: 外盘期货（实时 EST 的数据源，``source/futuresquotes.py`` 用裸 ``hf_`` 符号）。
#: ``hf_HSI`` 是恒生指数期货（2026-11-03 补）；恒生科技 / 国企指数没有对应
#: 的期货合约可取（新浪返回空串），所以它们没有实时 EST。
#: ``hf_GC`` 是纽约黄金（2026-11-03 补），配对在 ``GLD`` 上（两级链路：
#: 黄金基金 → GLD → hf_GC），不是配在指数上。
FUTURES_SYMBOLS = ("hf_NQ", "hf_ES", "hf_CL", "hf_NK", "hf_HSI", "hf_GC")

#: 即期外汇（参考数据表用，``refobj.QDII_REGIONS`` 引用）。
SPOT_FOREX_SYMBOLS = ("fx_susdcny", "fx_shkdcny", "fx_sjpycny", "fx_seurcny")


def fund_symbols() -> List[str]:
    """全部 QDII 基金符号（五个数组合集，去重、保序）。"""
    result = []
    for symbols in (qdii_get_symbol_array(), qdii_hk_get_symbol_array(),
                    qdii_jp_get_symbol_array(), qdii_eu_get_symbol_array(),
                    qdii_mix_get_symbol_array()):
        for symbol in symbols:
            if symbol not in result:
                result.append(symbol)
    return result


def est_symbol_of(symbol: str) -> Optional[str]:
    """基金的 est 符号；QdiiMix 成员返回 ``None``。

    顺序与 ``stockdata.StockDataArrayBuilder``（``stockdata.py:279-284``）一致：
    先查 est 映射，命中杠杆数组就用杠杆替代（ETF 而不是指数）。
    """
    est = (qdii_est_symbol(symbol) or qdii_hk_est_symbol(symbol)
           or qdii_jp_est_symbol(symbol) or qdii_eu_est_symbol(symbol))
    if est is None:
        return None
    if symbol in qdii_qqq_symbols():
        return "QQQ"
    if symbol in qdii_spy_symbols():
        return "SPY"
    return est


def fund_pairs() -> List[Tuple[str, str]]:
    """``[(基金, est), ...]`` —— 每只"有 est 的基金"一对，按基金顺序。"""
    pairs = []
    for symbol in fund_symbols():
        est = est_symbol_of(symbol)
        if est:
            pairs.append((symbol, est))
    return pairs


def est_symbols() -> List[str]:
    """全部 est 中间层符号（去重、按字母序）—— 都要在 ``stock`` 表里。"""
    return sorted({est for _, est in fund_pairs()})


def required_stock_symbols() -> List[str]:
    """撑起 QDII 页面所需的全部 ``stock`` 行符号。

    = 基金 + est 中间层 + 汇率 + 期货 + 即期外汇，去重、保序。
    """
    result = []
    for symbol in fund_symbols() + est_symbols() + list(FOREX_SYMBOLS) \
            + list(FUTURES_SYMBOLS) + list(SPOT_FOREX_SYMBOLS):
        if symbol not in result:
            result.append(symbol)
    return result


def sina_quote_symbols() -> List[str]:
    """要走**新浪行情链路**的符号：基金 + est 中间层（124 个）。

    故意不含三类，它们各有取数入口：

    * **期货** ``hf_*`` —— ``futurescli`` / ``source/futuresquotes.py``
      用裸 ``hf_`` 符号；而 ``get_sina_symbol()`` 对它们会给出
      ``gb_hf_nq`` 这种形式（PHP 同样如此，``stocksymbol.php:913``
      的 fall-through），没有入口用那个形式；
    * **即期外汇** ``fx_*`` —— ``forexcli``（中国货币网 / 新浪 ``f_``）；
    * **中间价** ``USCNY`` 等 —— 同样走 ``forexcli``，且写的是
      ``netvaluehistory`` 而不是 ``stockhistory``。
    """
    result = []
    for symbol in fund_symbols() + est_symbols():
        if symbol not in result:
            result.append(symbol)
    return result
