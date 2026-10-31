"""拖拉机自动化说明页路由（``woody/res/autotractorcn.php`` → ``/autotractor``）。

## 页面名

PHP 的页面名是 ``autotractor``（``UrlGetPage()`` 会剥掉 ``cn`` 后缀），
页脚与 ``GetAutoTractorLink()`` 拼的都是 ``autotractor.php``，所以本地路由
用 ``/autotractor``，站点内链接不会两套写法。

## 为什么单独一个 blueprint

这一页既不是分类页（没有符号数组）、也不是详情页（没有 symbol 参数），
放进任何一个现有 blueprint 都得多加一条分支。``routes/overnight.py`` 也是
同样的理由单独开的，两个说明型页面各自一个 6 行的路由文件，比塞进别处清楚。

## 数据从哪来

全部来自 :mod:`web.services.autotractor_service`，本模块不拼任何字符串 ——
文案与顺序都在服务层，测试对的就是那一份数据。
"""

from __future__ import annotations

from flask import Blueprint, render_template

from web.bootstrap import install_paths

install_paths()

from web.services.autotractor_service import build_page  # noqa: E402

autotractor_bp = Blueprint("autotractor", __name__)


@autotractor_bp.route("/autotractor")
def autotractor_page():
    """静态说明页：银河 / 华宝拖拉机自动化的安装与使用。"""
    return render_template("autotractor.html", page=build_page())
