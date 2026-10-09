"""健康检查路由。"""

from __future__ import annotations

import sys

from flask import Blueprint, jsonify


health_bp = Blueprint("health", __name__)


@health_bp.route("/health")
def health():
    """返回 Web 进程是否存活。"""
    return jsonify({"status": "ok", "python": sys.version})
