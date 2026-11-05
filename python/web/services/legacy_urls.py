"""PHP 时代的页面地址 → 本地路由（``/woody/res/*.php`` 兼容层）。

## 为什么需要这一层

生产端的 206 个页面全靠"文件名就是地址"：``sz161226cn.php``、``chinafuturecn.php``、
``editnetvaluecn.php``。迁到 Flask 之后 URL 换成了按功能分组的路径
（``/fund/<symbol>``、``/category/<name>``、``/stock/<symbol>/edit/<option>``），
于是**旧地址一个都不通** —— 2026-11-05 用户按生产端的习惯敲
``http://127.0.0.1:5000/woody/res/sz161226cn.php`` 拿到 404。

页面本身其实是迁过的（``sz161226cn.php`` 只是 4 行壳子：``require('php/_chinafuture.php')``
再交给 ``_dispcn.php`` 渲染），缺的只是\"这个文件名该落到哪个本地路径\"这张表。
所以这一层只做**跳转**，不重新实现任何页面。

## 三条规则，按顺序匹配

1. **每标的页** —— ``^(sh|sz)\d{6}cn\.php$`` 共 131 个 → ``/fund/<SYMBOL>``。
   PHP 那边这些文件的名字里就带着符号，本地统一由 ``/fund/<symbol>`` 一个路由覆盖；
2. **分类页 19 个** —— 从 ``CATEGORY_PAGES`` 注册表反推（``"php"`` 字段就是文件名），
   不另抄一份清单，否则加第 20 个分类时这里会悄悄漏掉；
3. **独立页 / 需要符号的页** —— 下面两张手写表。

匹配不上的一律返回 ``None``，由路由渲染一张\"本地没有对应页\"的说明，**不要**
猜一个相近的路径跳过去：跳到错的页面比 404 更难排查。

## 符号从哪儿来

PHP 的每标的页把符号编进文件名，其它页（``editnetvaluecn.php`` 一族）靠查询串
``?symbol=``。这里两种都收，另外容错 ``?sym=``；**给了符号才跳**，没给就返回
:class:`SymbolRequired`，让路由摆一个输入框 —— 而不是让 ``/stock//edit`` 去 500。
"""

from __future__ import annotations

import re
from typing import Any

from web.services.stock_option_service import STOCK_OPTIONS

#: 每标的页：``sz161226cn.php`` / ``sh513100cn.php``。131 个全部长这样。
_SYMBOL_PAGE = re.compile(r"^(sh|sz)\d{6}cn\.php$", re.I)

#: 只看前缀的大写归一：``sh513100`` → ``SH513100``。
_EXCHANGE_SYMBOL = re.compile(r"^(sh|sz|hk)\d", re.I)

#: 带下划线前缀的符号（``nf_AU0`` / ``hf_NQ`` / ``gb_aapl``，见 ``stocksymbol``）。
#: **前缀小写、后缀大写**是新浪那一侧的写法，``nf_au0`` 会拿到空串（见 pitfalls）。
_PREFIXED_SYMBOL = re.compile(r"^(nf|hf|gb)_(.+)$", re.I)


class SymbolRequired(LookupError):
    """这一页要靠符号才能定位，但请求里没带。

    带上页面名（``page``）是为了让路由能在提示里写清"哪一页需要符号"。
    """

    def __init__(self, page: str):
        super().__init__(page)
        self.page = page


#: 不需要符号的独立页。右边是本地路径（都是已迁完的页面）。
FIXED_PAGES: dict = {
    # 三个 A/B/AH/ADR 对比页 + AH 历史（后者在 SYMBOL_ROUTES 里，要符号）
    "abcomparecn.php": "/abcompare",
    "ahcomparecn.php": "/ahcompare",
    "adrhcomparecn.php": "/adrhcompare",
    # 两个工具页
    "autotractorcn.php": "/autotractor",
    "overnightcnhcn.php": "/overnightcnh",
    # 后台两页：分组编辑与交易编辑
    "editstockgroupcn.php": "/admin/groups",
    "editstocktransactioncn.php": "/transactions/edit",
    # 演示数据的三页（同样是"我的"视角，本地按功能页面承接）
    "myportfoliocn.php": "/portfolio",
    "mystockgroupcn.php": "/group",
    "mystocktransactioncn.php": "/transactions",
    # 抓取族：七个 POST 目标在本地合成一张工具页（GET /submit）
    "submitdividend.php": "/submit",
    "submitgroup.php": "/submit",
    "submithistory.php": "/submit",
    "submitholdings.php": "/submit",
    "submitnetvalue.php": "/submit",
    "submitstockoptions.php": "/submit",
    "submittransaction.php": "/submit",
}

#: 需要符号的页。``{symbol}`` 由查询串填入。
SYMBOL_ROUTES: dict = {
    # 七张历史表。``fundsharecn.php`` 对应的是 ``shareshistory``（份额），
    # 本地那张表叫"基金场内份额"，kind 名与 PHP 页面名不同，别照名字直译。
    "stockhistorycn.php": "/history/stockhistory/{symbol}",
    "fundhistorycn.php": "/history/fundhistory/{symbol}",
    "netvaluehistorycn.php": "/history/netvaluehistory/{symbol}",
    "netvalueclosecn.php": "/history/netvalueclose/{symbol}",
    "calibrationhistorycn.php": "/history/calibrationhistory/{symbol}",
    "fundsharecn.php": "/history/shareshistory/{symbol}",
    "fundpositioncn.php": "/history/fundposition/{symbol}",
    # 详情页 / 专门页
    "holdingscn.php": "/holdings/{symbol}",
    "exhaustiveholdingscn.php": "/holdings/exhaustive/{symbol}",
    "etfdividendcn.php": "/stock/{symbol}/etfdividend",
    "rotationtradingcn.php": "/stock/{symbol}/rotationtrading",
    "ahhistorycn.php": "/ahhistory/{symbol}",
    "deletesymbol.php": "/stock/{symbol}/delete",
}

#: 上面两个选项的专门页（``_editstockoption.php`` 里 16 项中的 2 项：季报 / 持仓）。
#: 它们的 key 也出现在 ``STOCK_OPTIONS`` 里，但**不能**落到 ``/stock/<sym>/edit/<key>``
#: —— 那两条 GET 会 302 到专门页，直接写专门页省一次跳转。
_OPTION_SPECIAL_PAGES: dict = {
    "editquarterreportcn.php": "/holdings/quarter/{symbol}",
    "editstockholdingscn.php": "/holdings/exhaustive/{symbol}",
}

#: 本地\"没有对应页\"的已知页面。用途只有一个：给用户一句准确的原因，
#: 而不是笼统一句\"不认识\"。键是 PHP 页面名。
KNOWN_MISSING: dict = {
    "uploadfile.php": "上传 Yahoo 历史行情 CSV 写 stockhistory。本地用 dbtools/seed_* 回填，不做这一页。",
    "debugcn.php": "访问日志 / 恶意 IP 统计，读的是 nginx 日志，PHP 专属运维页，不迁。",
    "cateyescn.php": "博客杂文（站点内容，不在迁移范围）。",
    "thanousparadoxcn.php": "博客杂文（站点内容，不在迁移范围）。",
    "btbondcn.php": "公司介绍页（``_res.php`` 静态内容，不在迁移范围）。",
    "fundaccountcn.php": "单只基金的开户/费率说明页（``_fundaccount.php``），内容绑生产端券商，未迁。",
    "fundlistcn.php": "基金清单页（``_stock.php`` + 两张段落），与分类页重叠，未单独迁。",
    "mystockcn.php": "``_mystock.php`` 的「我的标的」入口，本地拆成了 /fund/<symbol> 与 /stock/<symbol>。",
}


def normalize_symbol(value: Any) -> str:
    """归一符号写法，规则与 ``web/routes/admin.py::_normalize_symbol()`` 一致。

    * ``sh513100`` / ``sz161226`` / ``hk00700`` → 全大写；
    * ``nf_au0`` / ``hf_nq`` → 前缀小写、后缀大写（新浪只认 ``nf_AU0``）；
    * 其它（``^NDX``、``QQQ``）原样返回 —— 大写化会把 ``^NDX`` 变成 ``^NDX``
      没问题，但 ``gb_aapl`` 那种小写代码会被破坏，所以只对上面两类动手。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    prefixed = _PREFIXED_SYMBOL.match(text)
    if prefixed:
        return "%s_%s" % (prefixed.group(1).lower(), prefixed.group(2).upper())
    if _EXCHANGE_SYMBOL.match(text):
        return text.upper()
    return text


def _category_pages() -> dict:
    """``{PHP 文件名: /category/<key>}``，从分类页注册表反推。

    延迟导入：``category_page_service`` 会把 ``auto`` 那一整串模块拉进来，
    而这一层在路由注册阶段就被 import，没必要为此付启动成本。
    """
    try:
        from web.services.category_page_service import CATEGORY_PAGES
    except Exception:
        return {}
    pages = {}
    for name, spec in CATEGORY_PAGES.items():
        php = spec.get("php") or ""
        if php:
            pages[php] = "/category/%s" % name
    return pages


def _option_pages() -> dict:
    """``{edit*.php: /stock/<symbol>/edit/<key>}``，从 ``STOCK_OPTIONS`` 生成。

    ``key`` 就是 PHP 页面名去掉 ``cn.php``（见 ``StockOptionSpec.key`` 的注释），
    所以文件名不用另写一份：加第 17 项时这里自动跟上。
    """
    pages = dict(_OPTION_SPECIAL_PAGES)
    for spec in STOCK_OPTIONS:
        page = "%scn.php" % spec.key
        if page in pages:
            continue
        pages[page] = "/stock/{symbol}/edit/%s" % spec.key
    return pages


def resolve(page: str, symbol: Any = None) -> str:
    """把 PHP 页面名换成本地路径；没有对应页返回 ``None``。

    :param page: 请求路径的最后一段，如 ``sz161226cn.php``（可带前导 ``/``）。
    :param symbol: 查询串里的符号（``?symbol=`` / ``?sym=``），可为 ``None``。
    :raises SymbolRequired: 这一页必须带符号，但没带。
    """
    name = str(page or "").strip().lstrip("/")
    if not name.endswith(".php"):
        return None

    if _SYMBOL_PAGE.match(name):
        # 文件名里就带着符号：``sz161226cn.php``[:-6] → ``sz161226``
        return "/fund/%s" % normalize_symbol(name[:-6])

    fixed = FIXED_PAGES.get(name)
    if fixed:
        return fixed

    template = _option_pages().get(name) or SYMBOL_ROUTES.get(name)
    if template:
        text = normalize_symbol(symbol)
        if not text:
            raise SymbolRequired(name)
        return template.format(symbol=text)

    category = _category_pages().get(name)
    if category:
        return category
    return None


def missing_reason(page: str) -> str:
    """没有对应页时的原因；不知道就返回空串（路由用统一文案）。"""
    return KNOWN_MISSING.get(str(page or "").strip().lstrip("/"), "")
