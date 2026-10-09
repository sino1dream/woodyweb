"""数据库和迁移状态服务。"""

from __future__ import annotations

from typing import Any

from web.bootstrap import install_paths

install_paths()

from web.db import fetchone


MIGRATION_STATUS = {
    "fundcalc.py": "已迁移",
    "stockdata.py": "已迁移",
    "httpjson.py": "已迁移",
    "chinastock.py": "已迁移",
    "chinamoney.py": "已迁移",
    "stocksymbol.py": "已迁移",
    "stockref.py": "已迁移",
    "stockprefetch.py": "已迁移",
    "tzutil.py": "已迁移",
    "fundref.py": "未迁移",
    "qdiiref.php": "未迁移",
    "ui/table.php": "未迁移",
    "layout.php": "未迁移",
}


class StatusService:
    """汇总 Web 服务依赖的数据库状态。"""

    def get_status(self) -> dict[str, Any]:
        """返回数据库连通性、表行数和迁移状态。"""
        version = self._safe_scalar("SELECT VERSION() AS value")
        return {
            "database": {"ok": version is not None, "version": version},
            "tables": {
                "stock": self._safe_scalar("SELECT COUNT(*) AS value FROM stock"),
                "stockhistory": self._safe_scalar("SELECT COUNT(*) AS value FROM stockhistory"),
                "calibrationhistory": self._safe_scalar("SELECT COUNT(*) AS value FROM calibrationhistory"),
                "netvaluehistory": self._safe_scalar("SELECT COUNT(*) AS value FROM netvaluehistory"),
                "holdings": self._safe_scalar("SELECT COUNT(*) AS value FROM holdings"),
            },
            "latest": {
                "stockhistory": self._safe_scalar("SELECT MAX(date) AS value FROM stockhistory"),
                "calibrationhistory": self._safe_scalar("SELECT MAX(date) AS value FROM calibrationhistory"),
                "netvaluehistory": self._safe_scalar("SELECT MAX(date) AS value FROM netvaluehistory"),
                "holdingsdate": self._safe_scalar("SELECT MAX(date) AS value FROM holdingsdate"),
            },
            "migration": MIGRATION_STATUS,
        }

    def _safe_scalar(self, sql: str) -> Any:
        """单条状态查询失败时返回 None，不阻塞整个状态页。"""
        try:
            row = fetchone(sql)
        except Exception:
            return None
        return row.get("value") if row else None


def default_status_service_factory() -> StatusService:
    """创建状态服务。"""
    return StatusService()
