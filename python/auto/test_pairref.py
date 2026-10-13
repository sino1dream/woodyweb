"""``refobj.PairReference``（``php/stock/fundpairref.php`` 的 ``MyPairReference``）单元测试。

对应 PHP：
* ``MyPairReference`` 构造里的 ``cny_ref`` 推导（``fundpairref.php:16-43``）；
* ``LoadCalibration()``（``fundpairref.php:48``）；
* ``EstFromPair`` / ``EstToPair`` / ``GetPriceRatio``；
* ``StockGetPairReferences``（``php/stock.php:352``）的链式双向查找。

**不联网、不连真库**：数据源是内存假实现，行情加载回调不传。

重点钉住几件迁移易错的事：
1. A→H 用 ``HKCNY``、A→SH-B 用 ``USCNY``、A→SZ-B 用 ``HKCNY``、ADR→H 用
   ``fx_susdhkd``、印度 Sensex 配对用 ``fx_susdinr``、无配对时 ``cny_ref`` 为 ``None``；
2. ``LoadCalibration`` 有校准记录时 ``factor = close``、``last_calibration_val`` 取
   ``lastcalibration`` 表（没有则回落校准日净值），无记录时 ``factor = 1/仓位``；
3. ``EstFromPair`` / ``EstToPair`` 互为逆运算（同一组 factor/position 下）；
4. ``StockGetPairReferences`` 的链式与双向（从 B 侧进来也能构造出 A 侧）。
"""

from __future__ import annotations

import unittest

from ref import refobj
from ref.refobj import (
    AbPairReference,
    AdrPairReference,
    AhPairReference,
    StockGetPairReferences,
    PairReference,
    _get_ab_pair_reference,
)


class Rec:
    """日频记录（只需要 ``date`` / ``close``）。"""

    def __init__(self, date=None, close=None):
        self.date = date
        self.close = close


class FakeCursor:
    """假游标：伺候四张配对表的正查（A→配对）与反查（配对→A）。

    真 SQL 形如 ``FROM abpair f ... WHERE f.id = (...)``（正查）或
    ``FROM abpair f ... WHERE f.stock_id = (...)``（反查）。这里按表名 +
    方向直接从内存映射答，tuple / dict 两种行形状都认。
    """

    def __init__(self, tables):
        self.tables = tables
        self.sql = ""
        self.param = None
        self.closed = False

    def execute(self, sql, params=None):
        self.sql = sql
        self.param = params[0] if params else None

    def fetchone(self):
        for table, mapping in self.tables.items():
            if ("FROM %s f" % table) in self.sql:
                # 反查：WHERE 子句用 ``f.stock_id =``（参数是配对侧，问 A 侧）；
                # 正查：``WHERE f.id =``（参数是本体侧）。注意不能只认
                # ``f.stock_id`` 子串 —— 正查的 JOIN 里也有 ``p.id = f.stock_id``。
                if "WHERE f.stock_id" in self.sql:
                    for a_side, pair in mapping.items():
                        if pair == self.param:
                            return {"symbol": a_side} if self._as_dict() else (a_side,)
                    return None
                return {"symbol": mapping.get(self.param)} if self._as_dict() \
                    else (mapping.get(self.param),)
        return None

    def _as_dict(self):
        # 真假驱动行形状不同，测试两种都过
        return getattr(self, "_dict", False)

    def close(self):
        self.closed = True


class FakeConnection:
    """假连接：只提供 ``cursor()``。"""

    def __init__(self, tables):
        self.tables = tables

    def cursor(self):
        return FakeCursor(self.tables)


class FakeSource:
    """内存数据源。

    ``tables``：``{表名: {A侧符号: 配对符号}}``，正查/反查都靠它。
    ``calibration``：``{stock_id: (date, close)}``；``last_calibration``：
    ``{stock_id: float}``；``netvalue``：``{(stock_id, date): close}``；
    ``position``：``{stock_id: float}``。
    """

    STOCK = {
        "SZ161725": 1, "SH900919": 2, "00700": 3, "HWM": 4, "ADR1": 5,
        "fx_susdcnh": 8, "fx_susdhkd": 9, "fx_susdinr": 10, "USCNY": 11,
        "HKCNY": 12, "SH600519": 13, "SZ200002": 14, "ZZ999": 15,
        "znb_SENSEX": 16,
    }

    def __init__(self, tables=None, calibration=None, last_calibration=None,
                 netvalue=None, position=None, names=None, dict_rows=False):
        self.tables = tables or {}
        self.calibration = calibration or {}
        self.last_calibration = last_calibration or {}
        self.netvalue = netvalue or {}
        self.position = position or {}
        self.names = names or {}
        self.dict_rows = dict_rows
        self.connection = FakeConnection(self.tables)

    def get_stock_id(self, symbol):
        return self.STOCK.get(symbol)

    def get_stock_name(self, symbol):
        return self.names.get(symbol)

    def get_position(self, stock_id):
        return self.position.get(stock_id)

    def get_calibration_now(self, stock_id):
        rec = self.calibration.get(stock_id)
        return Rec(*rec) if rec else None

    def get_last_calibration(self, stock_id):
        return self.last_calibration.get(stock_id)

    def get_netvalue_close(self, stock_id, date):
        return self.netvalue.get((stock_id, str(date)))


class FakeCny:
    """够用的汇率引用：只实现 ``get_val``。"""

    def __init__(self, val=1.0):
        self._val = val

    def get_val(self, date=None):
        return self._val

    def GetVal(self, date=None):
        return self._val


# ---------------------------------------------------------------------------
# cny_ref 推导
# ---------------------------------------------------------------------------
class CnyRefTest(unittest.TestCase):
    """``_build_cny_ref`` 的汇率引用推导（``fundpairref.php:16-43``）。"""

    def test_ah_uses_hkcny(self):
        """A→H：本符号 A、配对 H → ``HKCNY`` 中间价。"""
        source = FakeSource(tables={"ahpair": {"SZ161725": "00700"}})
        ref = AhPairReference("SZ161725", source=source)
        cny = ref.get_cny_ref()
        self.assertIsNotNone(cny)
        self.assertEqual(cny.get_symbol(), "HKCNY")

    def test_ab_shanghai_b_uses_usdcny(self):
        """A→沪市 B（900 开头）：``USCNY``。"""
        source = FakeSource(tables={"abpair": {"SZ161725": "SH900919"}})
        ref = AbPairReference("SZ161725", source=source)
        self.assertEqual(ref.get_cny_ref().get_symbol(), "USCNY")

    def test_ab_shenzhen_b_uses_hkcny(self):
        """A→深市 B（200 开头）：``HKCNY``。"""
        source = FakeSource(tables={"abpair": {"SZ161725": "SZ200002"}})
        ref = AbPairReference("SZ161725", source=source)
        self.assertEqual(ref.get_cny_ref().get_symbol(), "HKCNY")

    def test_adr_uses_fx_susdhkd(self):
        """ADR→H：本符号美股、配对 H → 离岸 ``fx_susdhkd``。"""
        source = FakeSource(tables={"adrpair": {"HWM": "00700"}})
        ref = AdrPairReference("HWM", source=source)
        self.assertEqual(ref.get_cny_ref().get_symbol(), "fx_susdhkd")

    def test_india_sensex_uses_fx_susdinr(self):
        """配对符号是 ``znb_SENSEX`` → 离岸 ``fx_susdinr``。"""
        source = FakeSource(tables={"ahpair": {"SZ161725": "znb_SENSEX"}})
        ref = AhPairReference("SZ161725", source=source)
        self.assertEqual(ref.get_cny_ref().get_symbol(), "fx_susdinr")

    def test_fx_branch_uses_stock_ref_not_cny_ref(self):
        """``fx_*`` 分支与中间价分支建的是**两个不同的类**。

        这是 ``fundpairref.php:16-43`` 里最容易抹平的一处差别：中间价
        （``USCNY`` / ``HKCNY``）走 ``new CnyReference($strCNY)``，而离岸外汇
        （``fx_susdcnh`` / ``fx_susdhkd`` / ``fx_susdinr``）走
        ``new MyStockReference(...)`` —— 后者是"当股票行情取"的外汇符号，
        不是中间价净值表。写成同一个类不报错，只是取到的汇率口径不对。

        这里用**类**而不是符号名去钉，符号名对了类错的情况也能抓到。
        """
        # 中间价分支 → CnyReference
        source = FakeSource(tables={"ahpair": {"SZ161725": "00700"}})
        ref = AhPairReference("SZ161725", source=source)
        self.assertIsInstance(ref.get_cny_ref(), refobj.CnyReference)

        # 离岸外汇分支 → MysqlReference（PHP 的 MyStockReference）
        source = FakeSource(tables={"adrpair": {"HWM": "00700"}})
        ref = AdrPairReference("HWM", source=source)
        cny = ref.get_cny_ref()
        self.assertIsInstance(cny, refobj.MysqlReference)
        self.assertNotIsInstance(cny, refobj.CnyReference)

    def test_sina_future_cn_pair_leaves_cny_none(self):
        """配对是境内期货（``IsSinaFutureCN``）时 ``cny_ref`` 保持 ``None``。

        PHP 那段是个**空分支**（``fundpairref.php:17-19``）：既不赋 ``$strCNY``
        也不建 ``cny_ref``。这里验证没有顺手给它兜底成 1.0 或别的汇率。
        """
        # ``nf_`` 是境内期货前缀（``SINA_CN_FUTURE_PREFIX``），``AU0`` 是黄金
        source = FakeSource(tables={"fundpair": {"SH000300": "nf_AU0"}})
        ref = PairReference("SH000300", source=source)
        self.assertIsNotNone(ref.get_pair_ref())
        self.assertIsNone(ref.get_cny_ref())

    def test_no_pair_leaves_cny_none(self):
        """无配对标的时 ``cny_ref`` 为 ``None``（与 PHP ``$pair_ref = false`` 一致）。"""
        source = FakeSource(tables={"ahpair": {}})
        ref = AhPairReference("ZZ999", source=source)
        self.assertIsNone(ref.get_pair_ref())
        self.assertIsNone(ref.get_cny_ref())

    def test_reverse_entry_still_builds_cny(self):
        """从 B 侧（``SH900919``）进来，工厂反查构造出 A 侧并推导汇率。"""
        source = FakeSource(tables={"abpair": {"SZ161725": "SH900919"}})
        ref = _get_ab_pair_reference("SH900919", source)
        self.assertIsNotNone(ref)
        self.assertIsNotNone(ref.get_cny_ref())
        self.assertEqual(ref.get_cny_ref().get_symbol(), "USCNY")


# ---------------------------------------------------------------------------
# LoadCalibration
# ---------------------------------------------------------------------------
class LoadCalibrationTest(unittest.TestCase):
    """``LoadCalibration()``（``fundpairref.php:48``）。"""

    def test_with_calibration_record(self):
        source = FakeSource(
            tables={"ahpair": {"SZ161725": "00700"}},
            calibration={1: ("2026-01-01", 2.5)},
            last_calibration={1: 2.4},
        )
        ref = AhPairReference("SZ161725", source=source)
        self.assertAlmostEqual(ref.get_factor(), 2.5)
        self.assertAlmostEqual(ref.get_last_calibration_val(), 2.4)

    def test_fallback_to_netvalue_when_no_last_calibration(self):
        """``lastcalibration`` 没有时回落校准日那天的净值。"""
        source = FakeSource(
            tables={"ahpair": {"SZ161725": "00700"}},
            calibration={1: ("2026-01-01", 2.5)},
            netvalue={(1, "2026-01-01"): 2.45},
        )
        ref = AhPairReference("SZ161725", source=source)
        self.assertAlmostEqual(ref.get_factor(), 2.5)
        self.assertAlmostEqual(ref.get_last_calibration_val(), 2.45)

    def test_no_calibration_record_uses_one_over_position(self):
        """无校准记录 → ``fFactor = 1.0 / 仓位``。"""
        source = FakeSource(
            tables={"ahpair": {"SZ161725": "00700"}},
            position={1: 0.95},
        )
        ref = AhPairReference("SZ161725", source=source)
        self.assertAlmostEqual(ref.get_factor(), 1.0 / 0.95)
        self.assertIsNone(ref.get_last_calibration_val())


# ---------------------------------------------------------------------------
# EstFromPair / EstToPair（互为逆）
# ---------------------------------------------------------------------------
class EstPairTest(unittest.TestCase):
    """``EstFromPair`` / ``EstToPair`` 在 ``PairReference`` 上作为方法接线。"""

    def _ref_with(self, symbol, cny_val, factor, position):
        source = FakeSource(tables={"ahpair": {symbol: "00700"}})
        ref = AhPairReference(symbol, source=source)
        # 直接钉死估算要用的那几样，避免受假库行情影响
        ref.factor = factor
        ref.last_calibration_val = None
        ref.ratio = position
        ref.cny_ref = FakeCny(cny_val)
        return ref

    def test_est_from_pair_returns_float(self):
        ref = self._ref_with("SZ161725", 0.9, 1.0, 1.0)
        value = ref.est_from_pair(100.0, 0.9)
        self.assertIsInstance(value, float)

    def test_est_from_pair_and_to_pair_are_inverse(self):
        """同一组 factor/position/cny 下，``EstToPair(EstFromPair(x)) ≈ x``。"""
        ref = self._ref_with("HWM", 0.9, 2.0, 0.95)  # HWM 是美股（非 A 分支）
        est = ref.est_from_pair(100.0, 0.9)
        back = ref.est_to_pair(est, 0.9)
        self.assertAlmostEqual(back, 100.0, places=6)

    def test_est_from_pair_delegates_to_free_function(self):
        """方法只是把 ``self`` 转交给 ``pairestimate.est_from_pair``。"""
        ref = self._ref_with("SZ161725", 0.9, 1.0, 1.0)
        self.assertEqual(
            ref.est_from_pair(100.0, 0.9),
            refobj._est_from_pair(ref, 100.0, 0.9),
        )


# ---------------------------------------------------------------------------
# GetPriceRatio
# ---------------------------------------------------------------------------
class PriceRatioTest(unittest.TestCase):
    """``GetPriceRatio()``（``fundpairref.php:104``）。"""

    def test_ratio_is_my_price_over_est_from_pair(self):
        source = FakeSource(tables={"ahpair": {"SZ161725": "00700"}})
        ref = AhPairReference("SZ161725", source=source)
        ref.str_price = "10"
        ref.factor = 1.0
        ref.ratio = 1.0
        ref.last_calibration_val = None
        ref.cny_ref = FakeCny(0.9)
        # 配对价 100、汇率 0.9、A 股分支 → EstFromPair(100, 0.9) = 100*0.9/1.0 = 90
        ref.get_pair_ref().str_price = "100"
        self.assertAlmostEqual(ref.get_price_ratio(), 10.0 / 90.0, places=6)

    def test_no_pair_ratio_is_one(self):
        source = FakeSource(tables={"ahpair": {}})
        ref = AhPairReference("ZZ999", source=source)
        self.assertAlmostEqual(ref.get_price_ratio(), 1.0)

    def test_zero_est_ratio_is_one(self):
        """配对估算为 0（或失败）时回落 1.0，不画 INF。"""
        source = FakeSource(tables={"ahpair": {"SZ161725": "00700"}})
        ref = AhPairReference("SZ161725", source=source)
        ref.str_price = "10"
        ref.factor = 0.0  # 因子为 0 → EstFromPair 返回 None → 比率回落 1.0
        ref.ratio = 1.0
        ref.cny_ref = FakeCny(0.9)
        ref.get_pair_ref().str_price = "100"
        self.assertAlmostEqual(ref.get_price_ratio(), 1.0)


# ---------------------------------------------------------------------------
# StockGetPairReferences 链式双向查找
# ---------------------------------------------------------------------------
class StockGetPairReferencesTest(unittest.TestCase):
    """``StockGetPairReferences()``（``php/stock.php:352``）。"""

    def _source_full_chain(self):
        return FakeSource(tables={
            "abpair": {"SH600519": "SH900919"},
            "ahpair": {"SH600519": "00700"},
            "adrpair": {"ADR1": "00700"},
        })

    def test_full_chain_from_a_side(self):
        ab, ah, adr = StockGetPairReferences("SH600519", self._source_full_chain())
        self.assertIsNotNone(ab)
        self.assertIsNotNone(ah)
        self.assertIsNotNone(adr)
        self.assertEqual(ab.get_symbol(), "SH600519")
        self.assertEqual(ah.get_symbol(), "SH600519")
        self.assertEqual(adr.get_symbol(), "ADR1")

    def test_reverse_entry_from_b_side(self):
        """从 B 侧（``SH900919``）进来也能串起整条链。"""
        ab, ah, adr = StockGetPairReferences("SH900919", self._source_full_chain())
        self.assertIsNotNone(ab)
        self.assertIsNotNone(ah)
        self.assertIsNotNone(adr)
        self.assertEqual(adr.get_symbol(), "ADR1")

    def test_partial_chain_only_ah(self):
        """只有 A/H 配对、没有 A/B 与 ADR/H 时，ab 与 adr 为 None。"""
        source = FakeSource(tables={"ahpair": {"SZ161725": "00700"}})
        ab, ah, adr = StockGetPairReferences("SZ161725", source)
        self.assertIsNone(ab)
        self.assertIsNotNone(ah)
        self.assertIsNone(adr)

    def test_no_pairs_all_none(self):
        source = FakeSource(tables={})
        ab, ah, adr = StockGetPairReferences("ZZ999", source)
        self.assertIsNone(ab)
        self.assertIsNone(ah)
        self.assertIsNone(adr)


if __name__ == "__main__":
    unittest.main()
