"""Web 服务配置。"""

from __future__ import annotations

import os


class Config:
    """Flask 默认配置。"""

    SECRET_KEY = os.getenv("PALMMICRO_WEB_SECRET_KEY", "palmmicro-web-dev")
    TEMPLATES_AUTO_RELOAD = True
