"""``updater.navupdater`` 的目标收集测试。

## 为什么值得单测

``collect_targets()`` 决定"哪些符号要被抓净值"，判错的后果不是报错，
而是**把错尺度的数写进 ``netvaluehistory``**：est 中间层 ``SH000869``
（恒生香港银行指数）剥出 6 位代码 ``000869`` 后会被当成基金，净值源
返回同名公募基金的单位净值（0.2628），而指数点位约 5322 ——
差 2 万倍，且官方 EST 整条链路都用这个数。
"""

from __future__ import annotations

import unittest

from updater.navupdater import NavUpdater


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _args=None):
        return None

    def fetchall(self):
        return self._rows

    def close(self):
        return None


class FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return FakeCursor(self._rows)


class FakeStore:
    def __init__(self, rows):
        self.connection = FakeConnection(rows)


class CollectTargetsTest(unittest.TestCase):
    #: 库里符号（id, symbol）—— 覆盖三类：真基金 / est 中间层带 6 位代码 /
    #: est 中间层不带代码。
    ROWS = [
        {"id": 1, "symbol": "SZ161130"},     # 真基金
        {"id": 2, "symbol": "SH513100"},     # 真基金
        {"id": 3, "symbol": "SH513090"},     # 真基金（港股通，仍是基金）
        {"id": 134, "symbol": "SH000869"},   # est 中间层：恒生香港银行指数
        {"id": 4, "symbol": "QQQ"},          # est 中间层：ETF，无 6 位代码
        {"id": 6, "symbol": "^NDX"},         # est 中间层：指数，无代码
        {"id": 138, "symbol": "znb_TPX"},    # est 中间层：全球指数
        {"id": 8, "symbol": "USCNY"},        # 汇率，无代码
    ]

    def test_collect_targets_skips_est_middle_layers(self):
        """est 中间层一律不当基金抓，哪怕它带 6 位代码。"""
        updater = NavUpdater(store=FakeStore(self.ROWS))
        targets = updater.collect_targets()

        symbols = [symbol for symbol, _ in targets]
        self.assertEqual(symbols, ["SZ161130", "SH513100", "SH513090"])
        self.assertNotIn("SH000869", symbols)
        self.assertNotIn("QQQ", symbols)

    def test_collect_targets_without_connection(self):
        """store 没有 connection 时返回空列表，不抛异常。"""
        updater = NavUpdater(store=object())
        self.assertEqual(updater.collect_targets(), [])


if __name__ == "__main__":
    unittest.main()
