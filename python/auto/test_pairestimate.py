"""``auto/pairestimate.py`` 的单元测试。

对应 ``php/stock/fundpairref.php:84`` 的 ``EstFromPair()`` / ``EstToPair()``。

钉的是**PHP 的原意**而不是"我觉得应该"。最容易踩的三处：

1. ``$x == false`` 是宽松比较 —— ``0`` / ``'0'`` / ``''`` 都会命中"没传参数"；
2. A 股与非 A 股是**两条不同的式子**（差一个倒数方向）；
3. ``FundAdjustPosition`` 的第三个参数缺省是新值，有旧校准值时用旧值。
"""

import unittest

from calc.pairestimate import est_from_pair, est_to_pair, get_default_cny


class FakePairRef:
    def __init__(self, price="100.00"):
        self.str_price = price

    def get_price(self):
        return self.str_price


class FakeCnyRef:
    def __init__(self, value="6.70"):
        self._value = value

    def get_val(self, date=False):
        return self._value


class FakeRef:
    """``MyPairReference`` 的最小替身：只有公式要用到的那几样。"""

    def __init__(self, symbol="SZ161130", price="4.88", pair_price="100.00",
                 factor=1.2, position=0.95, cny="6.70",
                 last_calibration=False, symbol_a=False):
        self.str_symbol = symbol
        self.str_price = price
        self.pair_ref = FakePairRef(pair_price)
        self.cny_ref = FakeCnyRef(cny) if cny is not None else None
        self.factor = factor
        self._position = position
        self._last_calibration = last_calibration
        self._symbol_a = symbol_a

    def get_price(self):
        return self.str_price

    def get_position(self):
        return self._position

    def get_pair_ref(self):
        return self.pair_ref

    def get_cny_ref(self):
        return self.cny_ref

    def get_last_calibration_val(self):
        return self._last_calibration

    def is_symbol_a(self):
        return self._symbol_a


class EstFromPairTest(unittest.TestCase):
    """非 A 股：``($fPairVal / $fCny) / $this->fFactor``。"""

    def test_plain_case(self):
        ref = FakeRef(pair_price="100.00", cny="2.0", factor=4.0, position=1.0)

        # (100 / 2) / 4 = 12.5，仓位 1.0 时不改这个值
        self.assertAlmostEqual(est_from_pair(ref, 100.0, 2.0), 12.5)

    def test_missing_price_falls_back_to_the_pair_price(self):
        """``$fPairVal == false`` → 读 ``pair_ref->GetPrice()``。"""
        ref = FakeRef(pair_price="100.00", cny="2.0", factor=4.0, position=1.0)
        self.assertAlmostEqual(est_from_pair(ref), 12.5)

    def test_missing_cny_defaults_to_one(self):
        """没有 cny_ref 时 ``GetDefaultCny()`` 返回 1.0（见 ``:75``）。"""
        ref = FakeRef(factor=4.0, position=1.0, cny=None)
        self.assertEqual(get_default_cny(ref), 1.0)
        self.assertAlmostEqual(est_from_pair(ref, 100.0), 25.0)

    def test_zero_price_is_treated_as_missing(self):
        """PHP 的 ``0 == false`` 为真 —— 传 0 等于"没传"，会去读现价。"""
        ref = FakeRef(pair_price="100.00", cny="2.0", factor=4.0, position=1.0)
        self.assertAlmostEqual(est_from_pair(ref, 0), 12.5)

    def test_zero_string_is_treated_as_missing(self):
        ref = FakeRef(pair_price="100.00", cny="2.0", factor=4.0, position=1.0)
        self.assertAlmostEqual(est_from_pair(ref, "0"), 12.5)

    def test_position_blends_with_the_old_calibration(self):
        """``FundAdjustPosition(仓位, 新值, 旧值)``。"""
        ref = FakeRef(cny="1.0", factor=1.0, position=0.5,
                      last_calibration=10.0)
        # (100/1)/1 = 100 —— 但还要与旧值 10 按 0.5 混合 → 55
        self.assertAlmostEqual(est_from_pair(ref, 100.0), 55.0)


class EstToPairTest(unittest.TestCase):
    """``EstToPair`` 是 ``EstFromPair`` 的逆。」

    非 A 股那条：``FundReverseAdjustPosition()`` 之后再 ``* $fCny * $fFactor``。
    """

    def test_plain_case(self):
        ref = FakeRef(cny="2.0", factor=4.0, position=1.0, last_calibration=False)
        self.assertAlmostEqual(est_to_pair(ref, 12.5), 100.0)

    def test_round_trip(self):
        """两个方向互为逆运算（仓位 1.0、无旧校准值时成立）。"""
        ref = FakeRef(cny="6.70", factor=1.2, position=1.0)
        value = est_from_pair(ref, 100.0)
        self.assertAlmostEqual(est_to_pair(ref, value), 100.0)

    def test_missing_value_falls_back_to_own_price(self):
        ref = FakeRef(price="4.88", cny="1.0", factor=1.0, position=1.0)
        self.assertAlmostEqual(est_to_pair(ref), 4.88)


class SymbolABranchTest(unittest.TestCase):
    """A 股走 ``QdiiGetVal`` / ``QdiiGetPeerVal``，非 A 股走乘除两条别的式子。"""

    def test_symbol_a_uses_qdii_get_value(self):
        """A 股：``$fVal = QdiiGetVal($fPairVal, $fCny, $fFactor)`` = ``价*汇/校准``。"""
        ref = FakeRef(cny="2.0", factor=4.0, position=1.0, symbol_a=True)
        # 100 * 2 / 4 = 50，而非 A 股那条会给 (100/2)/4 = 12.5
        self.assertAlmostEqual(est_from_pair(ref, 100.0), 50.0)

    def test_symbol_a_reverse_uses_peer_value(self):
        """A 股反向：``QdiiGetPeerVal($fVal, $fCny, $fFactor)`` = ``值*校准/汇``。"""
        ref = FakeRef(cny="2.0", factor=4.0, position=1.0, symbol_a=True)
        self.assertAlmostEqual(est_to_pair(ref, 50.0), 100.0)

    def test_two_branches_really_differ(self):
        """两条式子必须给出不同结果 —— 防"只迁了一式"。

        这个是这场对照里最容易被静默跳过的一类 bug：抄错一个方向，
        页面照样是一张"看着合理"的均线表。
        """
        a = FakeRef(cny="2.0", factor=4.0, position=1.0, symbol_a=True)
        non_a = FakeRef(cny="2.0", factor=4.0, position=1.0, symbol_a=False)
        self.assertNotAlmostEqual(est_from_pair(a, 100.0), est_from_pair(non_a, 100.0))


class DegenerateInputTest(unittest.TestCase):
    """脏数据不该让页面 500。"""

    def test_zero_factor_returns_none(self):
        """PHP 会得到 ``INF`` 画在页面上；本版返回 ``None`` 让那一格空着。"""
        ref = FakeRef(factor=0.0)
        self.assertIsNone(est_from_pair(ref, 100.0))
        self.assertIsNone(est_to_pair(ref, 100.0))

    def test_missing_factor_falls_back_to_inverse_position(self):
        """``LoadCalibration()`` 没有校准记录时 ``fFactor = 1.0 / 仓位``（``:61``）。"""
        ref = FakeRef(factor=None, position=0.5, cny="1.0")
        # fFactor = 1/0.5 = 2 → (100/1)/2 = 50
        self.assertAlmostEqual(est_from_pair(ref, 100.0), 50.0)

    def test_none_ref_does_not_raise(self):
        self.assertIsNone(est_from_pair(None, 100.0))
        self.assertIsNone(est_to_pair(None, 100.0))


if __name__ == "__main__":
    unittest.main()
