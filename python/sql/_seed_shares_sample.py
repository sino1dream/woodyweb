"""临时工具：写入场内份额的**渲染验证样本**。

## 数据来源与诚实说明

生产端 ``fundsharecn.php`` **需要登录**（实测无凭据访问会跳转到
``account/logincn.php``），所以取不到真实的 ``shareshistory`` 数据。

下面的日期与份额取自作者的开发历程文档
<https://palmmicro.com/woody/blog/entertainment/20150818cn.php>
里"基金场内份额"页面表格展示的 SZ162411 数据（那一段是公开可见的）：

    2026-09-30  79553.18  -4190.48  62923045  7.91%
    2026-09-29  83743.66  -9276.61  46053204  5.5%
    2026-09-28  93020.27  -162.65   87542131  9.41%
    2026-09-24  93182.92  -1165.5   100639039 10.8%

**用途**：验证「基金场内份额」页面的列与换手率算法渲染正确。
**不是**完整的历史数据，也**不能**据此判断真实业务结论。
真实数据应由 ``submitholdings.php`` 之类的入口持续写入（PHP 侧行为），
或后续加抓取脚本。
"""

from __future__ import annotations

import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, "auto")

from stockdata import MySqlFundDataSource  # noqa: E402

# 来源见模块 docstring：作者文档里公开可见的 SZ162411 表格
# (symbol, date, 份额万份, 数量[成交量])
SAMPLES = [
    ("SZ162411", "2026-09-24", 93182.92, 100639039),
    ("SZ162411", "2026-09-28", 93020.27, 87542131),
    ("SZ162411", "2026-09-29", 83743.66, 46053204),
    ("SZ162411", "2026-09-30", 79553.18, 62923045),
]

# 同样来自该文档的 stockhistory 侧数据（成交量用于算换手）
VOLUMES = dict(((symbol, date), volume) for symbol, date, _share, volume in SAMPLES)

# 样本符号（华宝油气，作者文档里的主角）当前不在 demo 的 stock 表里，
# 需要先建一行，否则外键无对应、页面也查不到这个符号。
NEW_STOCKS = [("SZ162411", "华宝油气LOF")]


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== 计划写入的样本（来源见本文件 docstring）===")
    print("  先补 stock 行：%s" % ", ".join("%s(%s)" % (s, n) for s, n in NEW_STOCKS))
    for symbol, date, share, volume in SAMPLES:
        print("  %-10s %s  份额=%.2f 万份  数量=%d" % (symbol, date, share, volume))

    if not apply:
        print("\n这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    for symbol, name in NEW_STOCKS:
        cursor.execute(
            "INSERT INTO stock (symbol, name) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE name = VALUES(name)",
            (symbol, name),
        )

    for symbol, date, share, volume in SAMPLES:
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("  跳过（stock 表里没有）：%s" % symbol)
            continue
        cursor.execute(
            "INSERT INTO shareshistory (stock_id, date, close) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE close = VALUES(close)",
            (stock_id, date, share),
        )
        cursor.execute(
            "INSERT INTO stockhistory (stock_id, date, close, volume, adjclose) VALUES (%s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE volume = VALUES(volume)",
            (stock_id, date, 0.936, volume, 0.936),
        )
    source.connection.commit()

    cursor.execute("SELECT COUNT(*) AS n FROM shareshistory")
    print("\nshareshistory 现在 %d 行" % cursor.fetchone()["n"])
    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
