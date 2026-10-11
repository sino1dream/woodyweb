"""给演示库补 ``futurepremium`` 的样本（**幂等**，可重复执行）。

对应 PHP：``php/ui/smaparagraph.php`` 的 ``EchoFutureSmaParagraph()`` 靠
``FuturePremiumSql::GetCloseNow()`` 读这张表；演示库里既没有表也没有数据，
所以那段一直不渲染。

用法::

    cd F:\\woody\\woodyweb\\python
    D:\\python\\python.exe -B sql\\seed_future_premium.py
    D:\\python\\python.exe -B sql\\seed_future_premium.py --dry-run

## 数字从哪来

``close`` 是**年化利率的百分点**（``5.00625`` 表示 5.00625%），
``date`` 是**交割日** —— 两者都在 PHP 里摆得很清楚：

.. code-block:: php

    $fRate = floatval($strClose) / 100.0;
    return 1.0 + $fRate * $iDay / 365.0;

取的是 PHP 默认参数那组（``0.0500625`` / ``'2025-03-21'``）的同一量级，
只是把交割日换成一个**还没到**的日期 —— 用 2025-03-21 的话天数是负的，
演示页面上会看到"溢价 < 1"，看着像算错了。
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SQL_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SQL_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402

#: ``(符号, 利率百分点, 交割日)`` —— 只给演示库当前在跟的两个期货。
SEED_ROWS = (
    ("hf_NQ", 5.00625, "2026-12-18"),
    ("hf_ES", 4.75000, "2026-12-18"),
)


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    config = mysql_config_from_env(load_mysql_env(os.environ))
    print("目标库：%s/%s" % (config["host"], config["database"]))

    import pymysql

    connection = pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor,
    )
    written = 0
    try:
        with connection.cursor() as cursor:
            for symbol, rate, end_date in SEED_ROWS:
                cursor.execute("SELECT id FROM stock WHERE symbol = %s LIMIT 1", (symbol,))
                row = cursor.fetchone()
                if not row:
                    print("  跳过 %s：stock 表里没有这个符号" % symbol)
                    continue
                stock_id = row["id"]

                # 幂等：同一 (stock_id, date) 只插一次，已存在就更新利率
                cursor.execute(
                    "SELECT id FROM futurepremium WHERE stock_id = %s AND date = %s LIMIT 1",
                    (stock_id, end_date))
                existed = cursor.fetchone() is not None

                if dry_run:
                    print("  %s %s  id=%s  close=%s  date=%s"
                          % ("更新" if existed else "插入", symbol, stock_id, rate, end_date))
                    continue

                cursor.execute(
                    "INSERT INTO futurepremium (stock_id, date, close) VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE close = VALUES(close)",
                    (stock_id, end_date, rate))
                print("  %s %s  id=%s  close=%s  date=%s"
                      % ("更新" if existed else "插入", symbol, stock_id, rate, end_date))
                written += 1
        if not dry_run:
            connection.commit()
    finally:
        connection.close()

    print("完成：%s" % ("（dry-run，未写库）" if dry_run else "%d 行" % written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
