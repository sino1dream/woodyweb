"""分类页路由：16 个 ``woody/res/*cn.php`` 共用一个视图。

## 对应 PHP

这 16 个页面文件都是 4 行壳子，全部落到
``woody/res/php/_mystockgroup.php:154`` 的
``_echoStockGroupArray(GetCategoryArray($strPage), LayoutUseWide())``：

====================================================================  ==================
PHP 页面                                                              分类名（本模块的键）
====================================================================  ==================
``biotechcn.php`` / ``chinafuturecn.php`` / ``chinaindexcn.php``      同名
``chinainternetcn.php`` / ``commoditycn.php`` / ``hangsengcn.php``
``hsharescn.php`` / ``hstechcn.php`` / ``mscius50cn.php``
``oilfundcn.php`` / ``qdiicn.php`` / ``qdiieucn.php``
``qdiihkcn.php`` / ``qdiijpcn.php`` / ``qdiimixcn.php``
``qqqfundcn.php`` / ``spyfundcn.php``
====================================================================  ==================

分类名、标题、说明、符号数组统一在
:mod:`web.services.category_page_service` 的注册表里，本模块只负责
"取服务 → 装配 → 渲染"。

## URL 为什么是 /category/<name>

* ``/qdii`` 保留 —— 生产端就是这个页面名（``qdiicn.php``），且站内导航、
  ``services/site_links.py`` 都在用它，改了就是一堆死链；
* 其余 15 个没有各占一个顶层路径：PHP 的页面名带 ``cn`` 后缀
  （``hstechcn.php``），照抄成 ``/hstechcn`` 既难看也没必要。
  ``/category/<name>`` 一个模式覆盖，分类名与注册表键一致；
* **不能落在 ``/group/<name>``**：``/group`` 已经被
  :mod:`web.routes.group` 占作"某个股票分组的交易明细"，两者不是一回事
  （一个是标的分类页，一个是持仓分组页），挤在一个前缀下迟早误会。

## 排序

``?sort=symbol`` / ``?sort=premium`` 与 ``/qdii`` 完全同义，透传到
``fundest_service.get_table()``。表头链接由视图算好放进 ``sort_urls``，
模板不拼 URL —— 否则模板要知道自己是哪个 endpoint，多一个分类就多一处判断。
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    render_template,
    request,
    url_for,
)

from web.bootstrap import install_paths

install_paths()

from web.services.category_page_service import (  # noqa: E402
    CategoryNotFoundError,
    build_category_page,
    get_category_nav,
)

category_bp = Blueprint("category", __name__)


def _service(name: str):
    """按扩展名取服务工厂的产物；没注册就返回 ``None``（页面照样能开）。"""
    factory = current_app.extensions.get(name)
    return None if factory is None else factory()


def render_category(name: str, endpoint: str, endpoint_args: dict = None):
    """装配并渲染一个分类页。

    ``endpoint`` / ``endpoint_args`` 只用于生成 ``?sort=`` 那三个链接 ——
    同一份模板被两个路由用（``/category/<name>`` 与 ``/qdii``），
    各自要指回自己，不能写死。
    """
    endpoint_args = endpoint_args or {}
    reference_service = _service("reference_service_factory")
    fundest_service = _service("fundest_service_factory")
    try:
        page = build_category_page(
            name,
            source=getattr(reference_service, "source", None),
            reference_service=reference_service,
            fundest_service=fundest_service,
            sort=(request.args.get("sort") or None),
        )
    except CategoryNotFoundError:
        abort(404)

    sort_urls = {
        key: url_for(endpoint, **endpoint_args, sort=key)
        for key in ("symbol", "premium")
    }
    # 页尾导航列**其它**分类，别把当前页也列上（PHP EchoLinks() 同样跳过自己）
    nav = [item for item in get_category_nav() if item["name"] != name]

    return render_template(
        "category_page.html",
        page=page,
        nav=nav,
        sort_urls=sort_urls,
    )


@category_bp.route("/category/<name>")
def category_page(name: str):
    """按分类名打开分类页，例如 ``/category/hstech``。

    分类名不认识就 404 —— 不退化成"当成基金符号"，那种兜底在
    ``/fund/<symbol>`` 才有意义（旧链接兼容），这里只会掩盖打错的分类名。
    """
    return render_category(
        name,
        endpoint="category.category_page",
        endpoint_args={"name": name},
    )


@category_bp.route("/qdii")
def qdii_page():
    """QDII 分类页，例如 /qdii、/qdii?sort=premium。

    保留这个短路径是因为**生产端页面名就是 ``qdiicn.php``**，
    ``base.html`` / ``index.html`` / ``services/site_links.py`` 三处导航都指向它。
    与 ``/category/qdii`` 是同一页的两个入口。

    **没有重定向到 /fund/<group>**：那两页（qdiilof / qdiietf）是另一张
    "基金指数对照表"，列定义与服务都不同（``fundlistparagraph.php``），
    与 PHP 的页面划分一致，不合并。
    """
    return render_category("qdii", endpoint="category.qdii_page")
