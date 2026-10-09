"""请求级 MySQL 连接管理。"""

from __future__ import annotations

import os
from typing import Any

from flask import Flask, g

from web.bootstrap import install_paths

install_paths()

from stockdata import load_mysql_env, mysql_config_from_env, mysql_connection_error_message


class DatabaseUnavailableError(RuntimeError):
    """数据库连接或查询不可用的统一异常。"""


def connect() -> Any:
    """按环境变量创建 PyMySQL 连接。"""
    try:
        import pymysql
    except ImportError as exc:
        raise DatabaseUnavailableError("未安装 PyMySQL，请先安装后启动 Web 服务。") from exc

    config = mysql_config_from_env(load_mysql_env(os.environ))
    try:
        return pymysql.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
            charset="utf8",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
    except Exception as exc:
        raise DatabaseUnavailableError(mysql_connection_error_message(config, exc)) from exc


def get_db() -> Any:
    """取得当前请求的数据库连接。"""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(exc: BaseException | None = None) -> None:
    """请求结束后关闭数据库连接。"""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def fetchone(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """执行查询并返回单行字典。"""
    cursor = get_db().cursor()
    try:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        cursor.close()


def fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """执行查询并返回多行字典。"""
    cursor = get_db().cursor()
    try:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()


def init_app(app: Flask) -> None:
    """注册 Flask 请求结束时的连接清理。"""
    app.teardown_appcontext(close_db)


def ping() -> bool:
    """检查数据库连接是否可用。"""
    row = fetchone("SELECT 1 AS ok")
    return bool(row and int(row["ok"]) == 1)
