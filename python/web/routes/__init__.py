"""Web 路由注册。"""

from flask import Flask

from web.routes.account import account_bp
from web.routes.admin import admin_bp
from web.routes.category import category_bp
from web.routes.fund import fund_bp
from web.routes.group import group_bp
from web.routes.health import health_bp
from web.routes.history import history_bp
from web.routes.holdings import holdings_bp
from web.routes.main import main_bp
from web.routes.portfolio import portfolio_bp
from web.routes.quote import quote_bp
from web.routes.status import status_bp
from web.routes.stock import stock_bp
from web.routes.transactions import transactions_bp


def register_blueprints(app: Flask) -> None:
    """注册全部蓝图。"""
    app.register_blueprint(main_bp)
    app.register_blueprint(fund_bp)
    # 分类页（16 个 woody/res/*cn.php 共用一个视图）：/category/<name> 与
    # 生产端短路径 /qdii。见 web/routes/category.py
    app.register_blueprint(category_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(portfolio_bp)
    app.register_blueprint(account_bp)
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
