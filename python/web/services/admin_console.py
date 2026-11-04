"""后台控制台（``/admin`` 首页）的清单：生产端「未展示」的功能项。

## 这一页要解决什么

生产端有一批页面**不进任何导航** —— ``GetStockMenuLinks()`` 与 ``EchoLinks()``
都没有入口，只能靠记得 URL 手敲；其中 16 个 ``edit*.php`` 更是连"猜"都没法猜
（文件名是选项名 + ``cn.php``，选项名又只在 ``GetStockOptionArray()`` 里）。
迁完之后本地同样没有统一入口 —— 于是变成"能用了，但找不到"。

这一页把三类东西收成一张台账 + 一套入口：

=================================  ==========================================
分组                                口径
=================================  ==========================================
生产端只能手敲 URL                   PHP 有页面、无入口。已迁，本页给入口
生产端根本没有                       本版新增的能力（PHP 那边是散装副作用）
生产端有、本地未迁                    明确记下来，免得被当成漏掉
=================================  ==========================================

## 为什么 16 项 edit 不另写一份清单

``STOCK_OPTIONS``（``web/services/stock_option_service.py``）已经是这 16 项的
单一真相源 —— 中文动作名、日期口径、是否会员级、哪两项跳专门页全在里面。
这里再抄一遍的结果是：将来加第 17 项时只改一处，控制台默默漏掉。
所以 :func:`stock_option_entries` 直接遍历它。

## 符号占位符

需要一只标的才有意义的入口（16 项 edit + 删标的）用 :data:`SYMBOL_PLACEHOLDER`
占位，页面上的输入框通过 ``data-symbol-link`` 属性统一改写 —— 这样 20 个链接
共用一次输入，不用为每条挂一个表单。没有 JS 时这些链接指向占位符本身（404），
所以模板另外给了一个"打开索引页"的显式链接兜底。
"""

from __future__ import annotations

from typing import Callable, List, Optional

from web.bootstrap import install_paths

install_paths()

from web.services.stock_option_service import STOCK_OPTIONS  # noqa: E402
from web.services import submit_service  # noqa: E402

#: 需要符号的链接里代表"这里要填符号"的占位串。
#:
#: 选这种形状是因为它在 URL 里合法（下划线不需要转义），而且不可能撞上真实
#: 符号 —— 新浪的 ``nf_AU0`` 是唯一带下划线的族，格式是 ``nf_`` + 大写代码。
SYMBOL_PLACEHOLDER = "__SYMBOL__"

#: 权限档位 → 页面上的显示文字。
ACCESS_ADMIN = "admin"
ACCESS_MEMBER = "member"
ACCESS_OPEN = "open"

ACCESS_LABELS = {
    ACCESS_ADMIN: "管理员",
    ACCESS_MEMBER: "登录即可",
    ACCESS_OPEN: "公开",
}


def _entry(title: str, php: str, href: str, note: str = "",
           access: str = ACCESS_ADMIN, needs_symbol: bool = False) -> dict:
    """一条功能项。``href`` 已生成好（模板不再碰 ``url_for``）。"""
    return {
        "title": title,
        "php": php,
        "href": href,
        "note": note,
        "access": access,
        "access_label": ACCESS_LABELS.get(access, access),
        "needs_symbol": needs_symbol,
    }


# ---------------------------------------------------------------------------
# 生产端只能手敲 URL
# ---------------------------------------------------------------------------

def stock_option_entries(url_for: Callable[..., str]) -> List[dict]:
    """16 个 ``edit*.php`` → ``/stock/<symbol>/edit/<option>``。

    两项有专门页（季报 / 持仓）直接指过去 —— 与 ``/stock/<symbol>/edit``
    索引页同一个取舍：不在两处各写一份写入口。
    """
    entries = []
    for spec in STOCK_OPTIONS:
        if spec.route:
            href = url_for(spec.route, symbol=SYMBOL_PLACEHOLDER)
        else:
            href = url_for("stock.stock_option_edit",
                           symbol=SYMBOL_PLACEHOLDER, option=spec.key)
        entries.append(_entry(
            title=spec.submit,
            php=spec.key + "cn.php",
            href=href,
            note=spec.memo,
            # PHP 这一族除了"基金申购金额"都要求 IsAdmin()，见各 spec 的 note
            access=ACCESS_MEMBER if spec.member_level else ACCESS_ADMIN,
            needs_symbol=True,
        ))
    return entries


def hand_url_pages(url_for: Callable[..., str]) -> List[dict]:
    """其余"生产端有页面、但进不去"的入口。"""
    return [
        _entry(
            title="删标的",
            php="deletesymbol.php",
            href=url_for("stock.stock_delete", symbol=SYMBOL_PLACEHOLDER),
            note="五道配对闸门 + 6 张主表；PHP 是 GET 副作用且页面零输出，"
                 "本地拆成「先预览再执行」，另可显式级联清 12 张残留表。",
            needs_symbol=True,
        ),
        _entry(
            title="分组编辑",
            php="editstockgroupcn.php + submitgroup.php",
            href=url_for("admin.groups"),
            note="组名 + 成员符号多行文本。PHP 没有列表页（列表是账号页里的"
                 "一段），本地补了一张，新建 / 编辑 / 删除都在这里。",
        ),
        _entry(
            title="交易编辑",
            php="editstocktransactioncn.php",
            href=url_for("transactions.edit_transaction_page"),
            note="新增 / 修改一条分组交易。PHP 的删除与调仓是 GET 链接副作用，"
                 "本地一律 POST。",
            access=ACCESS_MEMBER,
        ),
        _entry(
            title="抓取工具（三合一）",
            php="submitdividend.php / submitnetvalue.php / submithistory.php",
            href=url_for("submit.index"),
            note="三页在 PHP 里跑完就跳回、页面零输出，本地合成一张工具表："
                 "填符号 → 看现状 → 点哪行抓哪路。",
        ),
    ]


# ---------------------------------------------------------------------------
# 生产端根本没有（本版新增）
# ---------------------------------------------------------------------------

def local_only_pages(url_for: Callable[..., str]) -> List[dict]:
    """PHP 没有对应页面、本版补的能力。

    每条的 ``note`` 都写"为什么值得存在" —— 这一栏最容易被后人当成冗余删掉。
    """
    return [
        _entry(
            title="符号管理",
            php="（无）",
            href=url_for("admin.symbols"),
            note="stock 表增 / 删 / 改名。PHP 三件事是散的：改名在 editstock.php、"
                 "删除在 deletesymbol.php、「新增」只是访问页面时 StockGetReference "
                 "的副作用。本地没有那个副作用，缺显式入口就会像 "
                 "/category/chinafuture 那样整页「库里无此符号」。",
        ),
        _entry(
            title="分组列表",
            php="（无）",
            href=url_for("admin.groups"),
            note="PHP 的后台只有一张编辑表单，没有「有哪些分组」的列表；"
                 "本地补列表页，编辑表单从列表进。",
        ),
        _entry(
            title="可编辑项索引",
            php="（无）",
            href=url_for("stock.stock_option_index", symbol=SYMBOL_PLACEHOLDER),
            note="16 个 edit*.php 在 PHP 里只能手敲 URL；本地既把它们收成一个 "
                 "<option> 参数，就必须补一个索引，否则更没法发现。",
            needs_symbol=True,
        ),
        _entry(
            title="登出",
            php="（无）",
            href=url_for("account_auth.logout"),
            note="PHP 没有登出入口（换身份只能清 cookie）。",
            access=ACCESS_MEMBER,
        ),
        _entry(
            title="状态页",
            php="（无）",
            href=url_for("status.status_page"),
            note="各表行数 + 迁移进度。PHP 的 debugcn.php 读 nginx 访问日志，"
                 "属于服务器专属运维页，本版不迁。",
            access=ACCESS_OPEN,
        ),
        _entry(
            title="JSON API",
            php="（无）",
            href=url_for("status.api_status"),
            note="六个只读端点：/api/status、/api/fund/<symbol>、/api/stock、"
                 "/api/quotes、/api/history/<kind>/<symbol>，"
                 "以及会写库的 /api/quotes/refresh（GET 只报状态、POST 才刷）。",
            access=ACCESS_OPEN,
        ),
    ]


# ---------------------------------------------------------------------------
# 生产端有、本地未迁
# ---------------------------------------------------------------------------

MISSING_ENTRIES = [
    {
        "title": "上传历史行情 CSV",
        "php": "uploadfile.php",
        "note": "把 Yahoo 导出的 CSV 灌进 stockhistory。本版用 "
                "dbtools/seed_*（新浪日线）顶替，没有上传入口。",
        "verdict": "缺口",
    },
    {
        "title": "访问日志统计",
        "php": "debugcn.php",
        "note": "依赖 nginx 日志与恶意 IP 统计，属于服务器专属运维页。"
                "换域名才有意义，记在这里是为了「别再问」。",
        "verdict": "不迁",
    },
]


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------

def build_sections(url_for: Callable[..., str], is_admin: bool = False,
                   member_email: Optional[str] = None) -> List[dict]:
    """把四块清单渲染成模板能直接用的结构。

    ``url_for`` 由调用方注入（Flask 里就是 ``flask.url_for``），本模块因此
    不依赖请求上下文，测试可以传个假的 —— 与 ``services/site_links.py`` 同法。

    ``is_admin`` / ``member_email`` 只用于页面顶部那句"当前身份"提示：这一页
    本身不判权限，真正的闸门在各目的路由里（``web/services/admin_auth.py``）。
    """
    return [
        {
            "key": "hand-url",
            "title": "生产端只能手敲 URL",
            "layout": "grid",
            "note": "PHP 有页面，但不进任何导航 —— GetStockMenuLinks() 与 "
                    "EchoLinks() 都没有入口，16 项连文件名都猜不出来。"
                    "迁完之后本地同样没有统一入口，所以收在这里。",
            "entries": stock_option_entries(url_for),
        },
        {
            "key": "hand-url-pages",
            "title": "其它只能手敲 URL 的页面",
            "layout": "table",
            "note": "同样不进导航，但不是「某只标的的编辑表单」，所以单独一排。",
            "entries": hand_url_pages(url_for),
        },
        {
            "key": "local-only",
            "title": "生产端根本没有",
            "layout": "table",
            "note": "PHP 那边要么是散装副作用、要么只有服务器能用的运维页。"
                    "每行都写了「为什么值得存在」。",
            "entries": local_only_pages(url_for),
        },
        {
            "key": "missing",
            "title": "生产端有、本地未迁",
            "layout": "table",
            "note": "明确记下来，免得被当成漏掉。完整清单见 "
                    "auto/README_pending_pages.md。",
            "entries": MISSING_ENTRIES,
        },
    ]


def quick_links(url_for: Callable[..., str]) -> List[dict]:
    """页面顶部的三张入口卡。"""
    return [
        {
            "title": "符号管理",
            "href": url_for("admin.symbols"),
            "lead": "stock 表的增 / 删 / 改名",
            "detail": "列表带四张主要表的行数；详情页显示删除影响面。",
            "count_key": "symbols",
        },
        {
            "title": "分组管理",
            "href": url_for("admin.groups"),
            "lead": "分组 + 成员符号",
            "detail": "新建 / 编辑 / 删除，删分组会连带删交易。",
            "count_key": "groups",
        },
        {
            "title": "抓取工具",
            "href": url_for("submit.index"),
            "lead": "分红 / 净值 / 历史价格",
            "detail": "填一个符号，看三张目标表的现状，再决定抓哪一路。",
            "count_key": "tools",
        },
    ]


def entry_counts(symbols: Optional[int] = None,
                 groups: Optional[int] = None) -> dict:
    """三张卡右下角的"现状"数字。

    拿不到就传 ``None``，模板那边会整块不渲染 —— 后台首页不该因为一条
    ``COUNT(*)`` 失败就打不开（那种页面恰恰是出问题时最需要能打开的）。
    """
    counts = {}
    if symbols is not None:
        counts["symbols"] = "%d 个符号" % symbols
    if groups is not None:
        counts["groups"] = "%d 个分组" % groups
    counts["tools"] = "%d 个工具" % len(submit_service.tool_list())
    return counts
