"""页脚导航（``SymbolAccount::EchoLinks()`` 的本地对应物）。

对应 PHP ``woody/res/php/_stockaccount.php:126``::

    function EchoLinks($strVer = false, $callback = false)
    {
        $str = GetAllLofLink().' '.GetOvernightLink().' '.GetOvernightCnhLink().' '
             .GetAhCompareLink().' '.GetAutoTractorLink().' '
             .GetAccountToolLink('simpletest').' '
             .GetDevLink('entertainment/20150818cn.php#'.$this->GetPage());
        $str .= GetStockMenuLinks();
        if ($strLoginId = $this->GetLoginId())
        {
            $str .= $strNewLine.GetMyPortfolioLink().$this->_getPersonalLinks($strLoginId);
            if ($this->IsAdmin())  $str .= ... GetStockPageLink('debug') ...;
        }
        if ($callback) ...
        else
        {
            if ($strSymbol = UrlGetQueryValue('symbol'))  $str .= GetStockCategoryLinks(...);
            else                                          $str .= GetCategoryLinks(...);
        }
    }

每个页面的 ``EchoAll()`` 末尾都调它一次（例如 ``exhaustiveholdingscn.php:353``），
所以它出现在**所有页面**的页脚 —— 本地对应物就是 ``base.html`` 的
``<footer>``，由 :func:`footer_sections` 供数据。

## 五段顺序照抄，目标地址按"本地有就本地、没有就生产端"映射

PHP 那串链接里，**工具页那一段**（``lof.php`` / ``overnight.php`` /
``overnightcnh.php`` / ``ahcompare.php`` / ``autotractor.php``）2026-11-01 起
**全部有本地页**了，整段挪进了 :func:`local_links`；剩下的（账号工具、开发记录、
七个股票菜单、十个行业分类）仍然只有生产端有页面。

照本项目一贯的取舍：**不给死链，也不假装它存在**。所以每条链接都带一个
``external`` 标记：

* ``external == False`` —— 本地路由，直接跳；
* ``external == True``  —— 生产端绝对地址（``https://palmmicro.com/…``），
  新窗口打开、文本后面带 ``↗``。这些地址全部真实可访问，是"生产端参照"
  这条既定原则的一部分（迁移判断以它为准）。

## 与 PHP 的三处差异（都是"本地没有对应的东西"）

1. **个人分组的 ``_getPersonalLinks()``** 需要登录态 + ``stockgroup`` 表，
   账号体系暂缓 —— 本地只给 ``/group`` 与 ``/portfolio`` 两个演示页；
2. **管理员那段 debug 链接**（``stockpage('debug')`` / ``GetDebugFileLink()`` /
   ``php/test.php``）本地没有对应页面，只保留 ``/admin/groups``；
3. **``GetStockCategoryLinks($strSymbol)`` 的十个行业分类**（biotech /
   commodity / qqqfund …）本地换成了分类页注册表（``/category/<name>``，
   2026-10-30 起 19 个页面全在本地），语义仍是"这一类还有哪些同类页"。

第 3 条另有一个细节：PHP 会把**该符号所属的那一类去掉**（``unset($ar[$strItem])``），
因为它就是当前页；本地同样不给当前分组自己挂链接。
"""

from __future__ import annotations

from typing import Callable, Optional

#: 生产端站点根。``PATH_STOCK`` 是 ``/woody/res/``（``php/httplink.php:2``），
#: ``PATH_ACCOUNT`` 是 ``/account/``、``PATH_BLOG`` 是 ``/blog/``。
PRODUCTION_ROOT = "https://palmmicro.com"
PATH_STOCK = "/woody/res/"
PATH_ACCOUNT = "/account/"
PATH_BLOG = "/blog/"

#: ``GetStockMenuArray()``（``php/stocklink.php``）+ 那七个 ``*_DISPLAY`` 常量。
#: **顺序照抄** —— 它是作者的"从近到远"排列（A 股 → 商品 → 美股 → 混合 →
#: 港股 → 日本 → 欧洲）。
STOCK_MENU = (
    ("chinaindex", "A股指数"),
    ("chinafuture", "A股商品"),
    ("qdii", "美股QDII"),
    ("qdiimix", "混合QDII"),
    ("qdiihk", "港股QDII"),
    ("qdiijp", "日本QDII"),
    ("qdiieu", "欧洲QDII"),
)

#: ``GetStockCategoryArray()``（``php/stocklink.php``）+ 那十个 ``*_GROUP_DISPLAY``。
STOCK_CATEGORY = (
    ("biotech", "生物科技"),
    ("chinainternet", "中国互联网"),
    ("commodity", "大宗商品"),
    ("hangseng", "恒生指数"),
    ("hshares", "H股"),
    ("hstech", "恒生科技"),
    ("mscius50", "MSCI美国50"),
    ("oilfund", "原油基金"),
    ("qqqfund", "纳指100"),
    ("spyfund", "标普500"),
)

#: ``EchoLinks()`` 开头那串"工具页"里**仍然只能指向生产端**的部分。
#:
#: 2026-11-01 清空：最后四个（``lof`` / ``overnight`` / ``overnightcnh`` /
#: ``autotractor``）都迁到本地了 —— ``lofcn.php`` 与 ``overnightcn.php`` 进了
#: 分类页注册表（``/category/lof`` / ``/category/overnight``），另两页各有自己的
#: 路由。**迁完还留在这里就是自己给自己留死链**（``ahcompare`` 那次的教训，
#: 见下），所以本地入口一律挪去 :func:`local_links`。
#:
#: 保留这个空元组是为了让"工具页 → 账号工具 → 开发记录 → 菜单 → 分类"这条
#: 分段顺序在代码与测试里仍然显式可见；将来再有新页面回到这里时不用改结构。
UTILITY_LINKS: tuple = ()


#: ``GetAccountToolLink('simpletest')``（``/account/simpletest.php``）。
ACCOUNT_TOOL_LINK = (PATH_ACCOUNT + "simpletest.php", "账号工具")

#: ``GetDevLink()`` 的锚点用页面名（PHP 传 ``#'.$this->GetPage()``）。
DEV_LINK = (PATH_BLOG + "entertainment/20150818cn.php", "开发记录")


def _external(page: str, query: str = "") -> str:
    """拼生产端绝对地址（``GetPageLink(PATH_STOCK, …)`` 的输出）。"""
    url = PRODUCTION_ROOT + page
    return "%s?%s" % (url, query) if query else url


def production_links() -> list:
    """``EchoLinks()`` 里"本地还没有页面"的那一串，全指向生产端。

    返回 ``[{"text", "url", "external": True}, ...]``，顺序 = PHP 顺序：
    工具页（本地已全迁，见 ``UTILITY_LINKS``，当前为空）→ 账号工具 →
    开发记录 → 七个股票菜单 → 十个行业分类。
    """
    links = []
    for page, query, text in UTILITY_LINKS:
        links.append({"text": text, "url": _external(PATH_STOCK + page, query),
                      "external": True})
    links.append({"text": ACCOUNT_TOOL_LINK[1], "url": _external(ACCOUNT_TOOL_LINK[0]),
                  "external": True})
    links.append({"text": DEV_LINK[1], "url": _external(DEV_LINK[0]), "external": True})
    for page, text in STOCK_MENU:
        links.append({"text": text, "url": _external(PATH_STOCK + page + ".php"),
                      "external": True})
    for page, text in STOCK_CATEGORY:
        links.append({"text": text, "url": _external(PATH_STOCK + page + ".php"),
                      "external": True})
    return links


def local_links(url_for: Callable[..., str], symbol: Optional[str] = None,
                current_group: Optional[str] = None, is_admin: bool = False) -> list:
    """本站点已有的页脚入口（PHP 那串里"本地真有页面"的部分 + 等价替身）。

    ``url_for`` 由调用方注入（``web/app.py`` 的 context processor），本模块
    因此不依赖 Flask，测试可以传个假的。

    ``symbol`` 有值时补一条"该符号的详情页"，与 PHP 的
    ``GetStockCategoryLinks($strSymbol)`` 同位置（页脚最后一段）。
    """
    links: list = [
        {"text": "首页", "url": url_for("main.index"), "external": False},
        {"text": "美股QDII", "url": url_for("category.qdii_page"), "external": False},
    ]

    #: ``GetCategoryLinks(GetStockCategoryArray())`` 的本地替身：两个 QDII 分组。
    #: **当前分组不给链接**（PHP ``unset($ar[$strItem])`` 同义）。
    from calc.fundgroups import get_fund_group

    for name in ("qdiilof", "qdiietf"):
        group = get_fund_group(name)
        if group is None or name == current_group:
            continue
        links.append({"text": group["title"].replace("基金指数对照表", "").strip(),
                      "url": url_for("fund.fund_group_page", group=name),
                      "external": False})

    #: ``EchoLinks()`` 开头那串"工具页"，2026-11-01 起**全部在本地**。
    #: 顺序照 PHP：``GetAllLofLink()`` → ``GetOvernightLink()`` →
    #: ``GetOvernightCnhLink()`` → ``GetAutoTractorLink()``（``ahcompare`` 在
    #: 2026-10-30 就迁了，位置在它原本的位置上）。
    #:
    #: **``/overnightcnh`` 不带 ``?overnightcnh=95000``**：那个查询串是 PHP 页面的
    #: 缺省金额（本地缺省 100000，见 ``overnight_cnh_service.DEFAULT_INPUT``），
    #: 页面上有预设按钮可改，链接里钉死一个数反而会盖掉用户上次填的值。
    links.append({"text": "LOF基金", "url": url_for("category.category_page",
                                                    name="lof"), "external": False})
    links.append({"text": "义工群对比", "url": url_for("category.category_page",
                                                      name="overnight"), "external": False})
    links.append({"text": "义工群对冲计算器", "url": url_for("overnight.overnight_cnh_page"),
                  "external": False})
    links.append({"text": "A股和H股对比", "url": url_for("compare.ahcompare_page"),
                  "external": False})
    links.append({"text": "拖拉机自动化", "url": url_for("autotractor.autotractor_page"),
                  "external": False})

    links.append({"text": "股票列表", "url": url_for("stock.stock_page"), "external": False})
    #: ``GetMyPortfolioLink()`` —— 只对"登录用户"输出（PHP 在
    #: ``if ($strLoginId)`` 里）。账号体系暂缓，本地恒给（演示数据）。
    links.append({"text": "投资组合", "url": url_for("portfolio.portfolio_page"),
                  "external": False})
    links.append({"text": "分组", "url": url_for("group.group_page"), "external": False})
    links.append({"text": "交易", "url": url_for("transactions.transactions_page"),
                  "external": False})
    links.append({"text": "账户", "url": url_for("account.account_page"), "external": False})
    #: 管理员段（PHP 是 ``if ($this->IsAdmin())`` 里的 debug 三连）。
    #: 本地对应的只有后台分组管理页。
    if is_admin:
        links.append({"text": "后台管理", "url": url_for("admin.groups"), "external": False})
    links.append({"text": "状态", "url": url_for("status.status_page"), "external": False})

    if symbol:
        links.append({"text": "%s 详情" % symbol,
                      "url": url_for("stock.stock_detail_page", symbol=symbol),
                      "external": False})
    return links


def footer_sections(url_for: Callable[..., str], symbol: Optional[str] = None,
                    current_group: Optional[str] = None,
                    is_admin: bool = False) -> list:
    """页脚的两段：本站点入口 + 生产端对照。

    返回 ``[{"title", "links"}, ...]``；模板只负责摆，不做判断。
    标题里写明"生产端"三个字 —— 点过去是另一个站点，用户该提前知道。
    """
    return [
        {"title": "本站点",
         "links": local_links(url_for, symbol=symbol,
                              current_group=current_group, is_admin=is_admin)},
        {"title": "生产端对照（PHP 页面）",
         "links": production_links()},
    ]


def safe_footer_sections(url_for: Callable[..., str], symbol: Optional[str] = None,
                         current_group: Optional[str] = None,
                         is_admin: bool = False) -> list:
    """:func:`footer_sections` 的"永不抛异常"版本（context processor 用）。

    页脚是**每一页**都要渲染的东西 —— 它里面任何一处 ``url_for`` 拼错都会
    让整个站点 500，而页脚本身又不承载业务。所以这里兜住异常，返回空列表，
    模板见到空列表就不摆那一段（宁可少一段页脚，也不要整页挂）。
    拼装期就报错的 bug 由 ``web/tests/test_site_links.py`` 逐条钉住 ——
    兜底是为了**不扩大故障面**，不是为了掩盖拼错。
    """
    try:
        return footer_sections(url_for, symbol=symbol,
                               current_group=current_group, is_admin=is_admin)
    except Exception:
        return []
