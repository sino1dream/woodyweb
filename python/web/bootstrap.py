"""兼容当前 python/auto 平铺模块结构的导入路径桥接。"""

from __future__ import annotations

import sys
from pathlib import Path


WEB_DIR = Path(__file__).resolve().parent
PYTHON_DIR = WEB_DIR.parent
AUTO_DIR = PYTHON_DIR / "auto"


def install_paths() -> None:
    """把 python 和 python/auto 加入模块搜索路径。"""
    for path in (AUTO_DIR, PYTHON_DIR):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
