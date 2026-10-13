"""Web 路由注册。"""

from flask import Flask

from web.routes.account import account_bp
from web.routes.fund import fund_bp
from web.routes.health import health_bp
from web.routes.history import history_bp
from web.routes.main import main_bp
from web.routes.portfolio import portfolio_bp
from web.routes.quote import quote_bp
from web.routes.status import status_bp
from web.routes.stock import stock_bp


def register_blueprints(app: Flask) -> None:
    """注册全部蓝图。"""
    app.register_blueprint(main_bp)
    app.register_blueprint(fund_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(portfolio_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(quote_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(status_bp)
