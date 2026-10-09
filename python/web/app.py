"""Palmmicro Python Web 服务入口。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping


# 直接执行 python/web/app.py 时，先把 python 和 python/auto 放入导入路径。
WEB_DIR = Path(__file__).resolve().parent
PYTHON_DIR = WEB_DIR.parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths

install_paths()

from flask import Flask, jsonify, render_template, request
from web.config import Config
from web.db import DatabaseUnavailableError, init_app
from web.routes import register_blueprints
from web.services.fund_list_service import (
    FundGroupNotFoundError,
    default_fund_list_service_factory,
)
from web.services.fund_service import FundNotFoundError, default_fund_service_factory
from web.services.history_service import (
    HistoryNotFoundError,
    default_history_service_factory,
)
from web.services.reference_service import ReferenceService, default_reference_service_factory
from web.services.status_service import default_status_service_factory


def _error_response(message: str, status: int):
    """按请求类型返回 JSON 或 HTML 错误。"""
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), status
    return render_template("error.html", message=message, status=status), status


def create_app(
    config: Mapping[str, Any] | None = None,
    fund_service_factory: Any = None,
    status_service_factory: Any = None,
    fund_list_service_factory: Any = None,
    history_service_factory: Any = None,
    updater_factory: Any = None,
    reference_service_factory: Any = None,
    reference_refs_builder: Any = None,
) -> Flask:
    """创建 Flask 应用，允许测试注入服务工厂。"""
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    app.json.ensure_ascii = False

    app.extensions["fund_service_factory"] = fund_service_factory or default_fund_service_factory
    app.extensions["status_service_factory"] = status_service_factory or default_status_service_factory
    app.extensions["fund_list_service_factory"] = (
        fund_list_service_factory or default_fund_list_service_factory
    )
    app.extensions["history_service_factory"] = (
        history_service_factory or default_history_service_factory
    )
    # 实时行情更新器：缺省用进程级单例（quoteupdater.get_updater），
    # 测试可注入带假行情源的实例。
    if updater_factory is not None:
        app.extensions["updater_factory"] = updater_factory

    # 基金详情页的「参考数据」表（php/ui/referenceparagraph.php）。
    # 两个口子都可以注入：
    #   reference_service_factory  —— 替换整个表格服务；
    #   reference_refs_builder     —— 只替换"按符号建引用对象"这一步
    #                                 （默认实现会真的抓新浪，测试必须顶掉）。
    app.extensions["reference_service_factory"] = (
        reference_service_factory or default_reference_service_factory
    )
    if reference_refs_builder is not None:
        app.extensions["reference_refs_builder"] = reference_refs_builder

    init_app(app)
    register_blueprints(app)

    @app.errorhandler(FundNotFoundError)
    def handle_fund_not_found(exc: FundNotFoundError):
        return _error_response("未找到基金代码：%s" % exc, 404)

    @app.errorhandler(FundGroupNotFoundError)
    def handle_fund_group_not_found(exc: FundGroupNotFoundError):
        return _error_response("未找到基金分组：%s" % exc, 404)

    @app.errorhandler(HistoryNotFoundError)
    def handle_history_not_found(exc: HistoryNotFoundError):
        return _error_response("未找到历史数据：%s" % exc, 404)

    @app.errorhandler(DatabaseUnavailableError)
    def handle_database_unavailable(exc: DatabaseUnavailableError):
        return _error_response(str(exc), 503)

    @app.errorhandler(ValueError)
    def handle_value_error(exc: ValueError):
        return _error_response(str(exc), 400)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("PALMMICRO_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("PALMMICRO_WEB_PORT", "5000")),
        debug=os.getenv("PALMMICRO_WEB_DEBUG", "0") == "1",
    )
