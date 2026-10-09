"""基金分组定义（基于代码号段）。

对应 PHP 侧的「分组页 + 基金指数对照表」（``php/ui/fundlistparagraph.php``、
``woody/res/fundlistcn.php``）。分组本身是 Python 侧新增的视图定义，PHP 里每个
基金页面各自列出自己的杠杆标的，没有统一的分组名字与列表页。

**划分依据是代码号段，不是 PHP 的符号数组**：PHP 的 ``QdiiGetSymbolArray()`` 把
LOF 和 ETF 混在一起（例如 SZ161130、SZ161125、SZ162411 都在里面），但按中国市场
规则它们是 LOF：

===========  ==========  ================================================
市场          号段        品种
===========  ==========  ================================================
深圳          15xxxx      ETF
深圳          16xxxx      LOF（含 160/161/162/163/164/165/166/167/168/169）
上海          50xxxx      LOF（501xxx 等）
上海          51xxxx      ETF
===========  ==========  ================================================

因此两个分组是对「深圳 15/16 + 上海 50/51」这四类号段的完整划分，互不相交。
号段判断用前两位数字，与 ``stocksymbol._is_digit_shenzhen_etf()`` 等函数同源，
但这里不依赖具体区间值，避免以后区间微调影响分组。
"""

from __future__ import annotations

from stocksymbol import get_lof_mix_symbol_array, qdii_get_symbol_array


# 参与分组划分的全部候选符号：PHP 的 LOF 组与 QDII 组的并集
def get_overseas_fund_symbol_array():
    """全部候选符号（去重后 47 只）。"""
    return _unique(get_lof_mix_symbol_array() + qdii_get_symbol_array())


def _is_lof_symbol(symbol):
    """按代码号段判断是否为 LOF（深圳 16xxxx、上海 50xxxx）。"""
    if len(symbol) < 4:
        return False
    market, code = symbol[:2].upper(), symbol[2:]
    if market == "SZ":
        return code.startswith("16")
    if market == "SH":
        return code.startswith("50")
    return False


def _is_etf_symbol(symbol):
    """按代码号段判断是否为 ETF（深圳 15xxxx、上海 51xxxx）。"""
    if len(symbol) < 4:
        return False
    market, code = symbol[:2].upper(), symbol[2:]
    if market == "SZ":
        return code.startswith("15")
    if market == "SH":
        return code.startswith("51")
    return False


def get_qdiilof_symbol_array():
    """QDII-LOF 分组：候选符号里的深圳 16xxxx 与上海 50xxxx。"""
    return [symbol for symbol in get_overseas_fund_symbol_array() if _is_lof_symbol(symbol)]


def get_qdiietf_symbol_array():
    """QDII-ETF 分组：候选符号里的深圳 15xxxx 与上海 51xxxx。"""
    return [symbol for symbol in get_overseas_fund_symbol_array() if _is_etf_symbol(symbol)]


def in_array_qdiilof(symbol):
    """符号是否属于 QDII-LOF 分组。"""
    return _is_lof_symbol(symbol) and symbol in get_overseas_fund_symbol_array()


def in_array_qdiietf(symbol):
    """符号是否属于 QDII-ETF 分组。"""
    return _is_etf_symbol(symbol) and symbol in get_overseas_fund_symbol_array()


def get_symbol_group(symbol):
    """返回符号所属的分组名，不属于任何分组时返回 ``None``。"""
    if in_array_qdiilof(symbol):
        return "qdiilof"
    if in_array_qdiietf(symbol):
        return "qdiietf"
    return None


def _unique(symbols):
    """按出现顺序去重，保持列表稳定。"""
    seen = set()
    result = []
    for symbol in symbols:
        if symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
    return result


# 分组名 -> 元数据。键会直接出现在 URL 里，例如 /fund/qdiilof/SZ161130。
FUND_GROUPS = {
    "qdiilof": {
        "title": "QDII-LOF 基金指数对照表",
        "description": "跟踪海外指数、可按净值申购赎回的 QDII-LOF 基金（深圳 16 号段、上海 50 号段）。",
        "symbols": get_qdiilof_symbol_array,
    },
    "qdiietf": {
        "title": "QDII-ETF 基金指数对照表",
        "description": "跟踪海外指数、在场内像股票一样交易的 QDII-ETF（深圳 15 号段、上海 51 号段）。",
        "symbols": get_qdiietf_symbol_array,
    },
}


def get_fund_group(name: str):
    """按分组名取定义，不存在返回 ``None``。"""
    return FUND_GROUPS.get(name)


def get_fund_group_names():
    """全部分组名，供首页列出入口。"""
    return sorted(FUND_GROUPS)
