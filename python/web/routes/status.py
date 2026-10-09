"""状态页路由。"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template

from web.services.status_service import StatusService


status_bp = Blueprint("status", __name__)


def _get_service() -> StatusService:
    """从应用扩展中取得状态服务。"""
    return current_app.extensions["status_service_factory"]()


@status_bp.route("/api/status")
def api_status():
    """返回数据库和迁移状态 JSON。"""
    return jsonify(_get_service().get_status())


@status_bp.route("/status")
def status_page():
    """渲染数据库和迁移状态页面。"""
    return render_template("status.html", status=_get_service().get_status())
