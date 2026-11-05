"""Web 路由注册。"""

from flask import Flask

from web.routes.account import account_bp
from web.routes.account_auth import account_auth_bp
from web.routes.admin import admin_bp
from web.routes.autotractor import autotractor_bp
from web.routes.category import category_bp
from web.routes.compare import compare_bp
from web.routes.fund import fund_bp
from web.routes.group import group_bp
from web.routes.health import health_bp
from web.routes.history import history_bp
from web.routes.holdings import holdings_bp
from web.routes.legacy import legacy_bp
from web.routes.main import main_bp
from web.routes.overnight import overnight_bp
from web.routes.portfolio import portfolio_bp
from web.routes.quote import quote_bp
from web.routes.status import status_bp
from web.routes.stock import stock_bp
from web.routes.submit import submit_bp
from web.routes.transactions import transactions_bp


def register_blueprints(app: Flask) -> None:
    """注册全部蓝图。"""
    app.register_blueprint(main_bp)
    app.register_blueprint(fund_bp)
    # 分类页（16 个 woody/res/*cn.php 共用一个视图）：/category/<name> 与
    # 生产端短路径 /qdii。见 web/routes/category.py
    app.register_blueprint(category_bp)
    # 对比页（abcompare / ahcompare / adrhcompare 三个 PHP 页面 + AH 历史页）。
    # 与分类页共用 _mystockgroup.php 的 EchoAll()，但符号数组来自三张配对表，
    # 必须带数据源，所以单开一个蓝图。见 web/routes/compare.py
    app.register_blueprint(compare_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(portfolio_bp)
    app.register_blueprint(account_bp)
    # 账号页（登录/注册/改密/改邮箱/重置/关号/资料/编辑资料/登出）——
    # 对应 account/ 目录那一族页面，session 键照 PHP（SESS_ID + email）。
    # 见 web/routes/account_auth.py
    app.register_blueprint(account_auth_bp)
    app.register_blueprint(group_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(quote_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(status_bp)
    # 持仓型基金的「基金持仓」页（woody/res/holdingscn.php）——
    # /stock/<symbol> 第 3 分支（CK4）与它共用 est 三段，但测算表与
    # ADR 对比表只在这一页有。见 web/routes/holdings.py。
    app.register_blueprint(holdings_bp)
    # 后台管理（stockgroup/stockgroupitem 增删改查）—— 2026-10-16 立项，
    # 登录态未接，页面有常驻说明。见 auto/README_stockgroupsql_migration.md
    app.register_blueprint(admin_bp)
    # 三个"抓外部数据写库"的后台动作（woody/res/submitdividend.php 一族）——
    # 一张工具页 + 三个 POST 端点。见 auto/README_submit_pages_migration.md
    app.register_blueprint(submit_bp)
    # 义工群对冲计算器（woody/res/overnightcnhcn.php）—— 独立工具页，
    # 没有符号参数，靠 ?overnightcnh= 预填金额。见 web/routes/overnight.py
    app.register_blueprint(overnight_bp)
    # 拖拉机自动化说明页（woody/res/autotractorcn.php）—— 纯图文内容页，
    # 没有符号也没有参数。见 web/routes/autotractor.py
    app.register_blueprint(autotractor_bp)
    # 旧地址兼容层（/woody/res/sz161226cn.php 一族）—— 生产端的 206 个页面
    # 靠"文件名即地址"，本地换成按功能分组的路径后旧地址全 404。这一层只做
    # 302，映射表见 web/services/legacy_urls.py
    app.register_blueprint(legacy_bp)
