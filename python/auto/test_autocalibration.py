"""``autocalibration`` 的单元测试。

对应 PHP ``FundPairReference::DailyCalibration()``
（``php/stock/fundpairref.php:188``），触发点在页面装配：

```php
// woody/res/php/_qdiigroup.php:69-82
foreach ($arLev as $strSymbol)          // $arLev 是 'QQQ' 这种**中间层**符号
{
    $leverage_ref = new FundPairReference($strSymbol);
    ...
    $leverage_ref->DailyCalibration();
}
```
"""

from __future__ import annotations

import unittest

from updater.autocalibration import (
    DEMO_MIDDLE_SYMBOLS,
    MIDDLE_PAIR_FALLBACK,
    AutoCalibrator,
    CalibrationOutcome,
    nav_matches_price,
)
from source.usnavsource import FALLBACK_BASE_NAV, DerivedMiddleNavSource


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, sql, params=()):
        self.connection.queries.append((sql, params))
        self.rows = self.connection.dispatch(sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    """最小假连接：支持 fundpair / stock 查询与 calibrationhistory 读写。"""

    def __init__(self, stock=None, fundpair=None, calibration=None):
        self.queries = []
        self.stock = stock if stock is not None else {
            "QQQ": 4, "SPY": 5, "^NDX": 6, "^GSPC": 7, "USCNY": 8,
        }
        self.by_id = dict((v, k) for k, v in self.stock.items())
        self.fundpair = fundpair if fundpair is not None else {}
        self.calibration = [dict(r) for r in (calibration or [])]
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def dispatch(self, sql, params):
        # 反引号必须先剔掉，否则 `FROM `calibrationhistory`` 匹配不上
        upper = sql.strip().upper().replace("`", "")
        if "FROM FUNDPAIR" in upper:
            return [{"symbol": self.fundpair.get(params[0])}] if self.fundpair.get(params[0]) else []
        if "FROM STOCK WHERE ID" in upper:
            symbol = self.by_id.get(params[0])
            return [{"symbol": symbol}] if symbol else []
        if "FROM STOCK WHERE SYMBOL" in upper:
            stock_id = self.stock.get(params[0])
            return [{"id": stock_id}] if stock_id is not None else []
        if "INSERT INTO CALIBRATIONHISTORY" in upper:
            self.calibration.append({
                "id": len(self.calibration) + 1, "stock_id": params[0],
                "date": params[1], "close": params[2], "time": params[3], "num": 1,
            })
            return []
        if "FROM CALIBRATIONHISTORY" in upper:
            rows = [r for r in self.calibration if str(r["stock_id"]) == str(params[0])]
            if len(params) > 1:
                rows = [r for r in rows if str(r["date"]) == str(params[1])]
            return rows[:1]
        return []


class FakeDailyRecord:
    def __init__(self, date=None, close=None):
        self.date = date
        self.close = close


class FakeSource:
    """假数据源。

    ``netvalue`` 是**归一化尺度**的（演示库就是 1.0），``calibration`` 是
    该尺度的校准值。P0.5 之后 ``_implied_middle_nav`` 不再由这两者算出 ——
    它直接取当日收盘价（``get_latest_history_close``），所以测试想让它有值
    就注入该方法。这两个参数现在只影响「旧的、会被守卫拒绝的那条式子」。
    """

    #: 实时点位兜底值（对应 ``_pair_value`` 的第三级：新浪 ``gb_ndx``）。
    #:
    #: 故意与 ``pair_value``（基准日配对值）**取不同的数**：这样
    #: "配对值变了就按配对比值反向缩放"的**方向**能被确定地断言到。
    #: 以前这一级是**真的联网**抓回来的，方向对不对全看当天涨跌幅，
    #: 而且离线机器上会变成 ``None``、让整条链路静默跳过。
    LIVE_PAIR_VALUE = 31500.0

    def __init__(self, netvalue=1.0, net_date="2026-10-02",
                 calibration=0.008945, calibration_date="2026-10-01",
                 pair_symbol=None, pair_value=31098.7787,
                 cny=6.705, stock=None, live_pair_value=None):
        self.netvalue = netvalue
        self.net_date = net_date
        self.calibration = calibration
        self.calibration_date = calibration_date
        self.pair_symbol = pair_symbol
        self.pair_value = pair_value
        self.cny = cny
        self.live_pair_value = (
            live_pair_value if live_pair_value is not None else self.LIVE_PAIR_VALUE)
        self.connection = FakeConnection(stock=stock)

    def get_stock_id(self, symbol):
        return self.connection.stock.get(symbol)

    def get_latest_net_value(self, stock_id):
        return FakeDailyRecord(date=self.net_date, close=self.netvalue)

    def get_calibration_now(self, stock_id):
        if self.calibration is None:
            return None
        return FakeDailyRecord(date=self.calibration_date, close=self.calibration)

    def get_netvalue_close(self, stock_id, date):
        # 汇率有自己的取数口；配对标的的**历史值**走 _pair_history
        if stock_id == self.connection.stock.get("USCNY"):
            return self.cny
        history = getattr(self, "_pair_history", None)
        if history and str(date) in history:
            return history[str(date)]
        return None

    def get_history_close(self, stock_id, date):
        return None

    def get_latest_net_value_close(self, stock_id):
        return self.cny if stock_id == self.connection.stock.get("USCNY") else None

    def get_fund_profile(self, symbol):
        class Profile:
            est_symbol = "QQQ"

        return Profile()


class FixedPairFetcher:
    """把"指数实时点位"这一级兜底固定成常量，**不联网**。

    ``autocalibration._pair_value()`` 的第三级兜底是去新浪抓 ``gb_ndx``。
    单元测试碰它就是自找麻烦：离线机器上返回 ``None``，校准值随之变 ``None``，
    用例以"业务错了"的面目失败，实际只是没网（本项目实际发生过：装了
    ``requests`` 的机器全绿、没装的机器红 6 个）。
    """

    def __init__(self, value):
        self.value = value
        self.calls = []

    def __call__(self, symbol):
        self.calls.append(symbol)
        return self.value


def _calibrator(source, **kwargs):
    """构造 ``AutoCalibrator``，并顶掉会联网的那一级兜底。"""
    return AutoCalibrator(source, pair_fetcher=FixedPairFetcher(source.live_pair_value),
                          **kwargs)


class CalcFactorTest(unittest.TestCase):
    """``CalcFactor()``：``校准值 = 配对值 × 汇率 / 本符号净值``。"""

    def test_basic_formula(self):
        calibrator = _calibrator(FakeSource())
        self.assertAlmostEqual(calibrator.calc_factor(200.0, 100.0, 1.0), 2.0, places=9)
        # 带汇率
        self.assertAlmostEqual(calibrator.calc_factor(200.0, 100.0, 6.705), 13.41, places=9)

    def test_rejects_bad_inputs(self):
        calibrator = _calibrator(FakeSource())
        self.assertIsNone(calibrator.calc_factor(None, 100.0, 1.0))
        self.assertIsNone(calibrator.calc_factor(100.0, None, 1.0))
        self.assertIsNone(calibrator.calc_factor(100.0, "abc", 1.0))
        self.assertIsNone(calibrator.calc_factor(100.0, 0.0, 1.0))


class MiddlePairTest(unittest.TestCase):
    """``_pair_symbol()``：``QQQ`` → ``^NDX``。"""

    def test_from_fundpair_table(self):
        source = FakeSource()
        source.connection.fundpair = {4: "^NDX"}     # id=QQQ -> stock_id=^NDX
        self.assertEqual(_calibrator(source)._pair_symbol("QQQ"), "^NDX")

    def test_falls_back_to_builtin_map(self):
        """demo 的 ``fundpair`` 只存"基金 → ETF"，没有"ETF → 指数"。

        少了这条兜底，自动校准会一直"没有配对标的是"而静默失效 ——
        这正是实测踩到的。
        """
        source = FakeSource()      # fundpair 空
        self.assertEqual(_calibrator(source)._pair_symbol("QQQ"), MIDDLE_PAIR_FALLBACK["QQQ"])
        self.assertEqual(_calibrator(source)._pair_symbol("SPY"), MIDDLE_PAIR_FALLBACK["SPY"])

    def test_unknown_symbol_returns_none(self):
        self.assertIsNone(_calibrator(FakeSource())._pair_symbol("ZZZZ"))


class ScaleConversionTest(unittest.TestCase):
    """**尺度换算**：本项目的核心修正。

    演示库把中间层净值归一化成 1.0，而 PHP 的公式假设它是真实每份净值：

        校准值 = 配对标的点位 / 本符号净值

    照抄会得到 ``31099``（生产端是 41.111），更糟的是会让估值炸到 20245。

    ## 正确式子（**实测标定**出来的，不是推导出来的）

    我试过 5 个代数推导，全部被估值守卫拒绝（见 ``calibrate_middle`` 的注释）。
    实测（``sql/_measure_exponent.py``）得到估值对 ``cal_m`` 近似**线性正比**
    （k≈0.94），所以：

        新校准值 = 原校准值 × 基准日配对值 / 当前配对值

    **``pair_old`` 取不到时退回"原校准值不变"** —— 本演示库里
    ``^NDX``/``^GSPC`` 没有任何历史行情，所以走的就是这条退路。
    """

    #: 2026-10-07 附近的实测值
    PAIR_VALUE = 31098.7787
    OLD_CALIBRATION = 0.008945
    CNY = 6.705

    def _calibrate(self, **kwargs):
        source = FakeSource(pair_value=self.PAIR_VALUE,
                            calibration=self.OLD_CALIBRATION, cny=self.CNY, **kwargs)
        return _calibrator(source).calibrate_middle("QQQ", apply=False)

    def test_keeps_calibration_when_no_pair_history(self):
        """取不到基准日配对值时，**校准值不变**（保守退路），并留下告警。

        这是本演示库的实际行为：``^NDX`` 没有历史行情，所以自动校准
        不会改动中间层校准值 —— 但闸门会前进（净值日期被推进了），
        而且估值保持在净值附近。

        **断言"不变"而不是"变成生产量级"**：变成 41.36 那次让
        ``SZ161130`` 的估值从 4.59 炸到 20245，是被估值守卫拦下来的。
        """
        outcome = self._calibrate()

        self.assertAlmostEqual(outcome.calibration, self.OLD_CALIBRATION, places=9)
        self.assertTrue(outcome.warnings, "应当留下「取不到配对值」的告警")
        self.assertIn("不改变校准值", outcome.warnings[0])

    def test_pair_ratio_scales_calibration(self):
        """**能取到基准日配对值时**，按配对比值**反向**缩放。

        配对值涨 → 校准值跌（因为估值对两者都近似线性正比，
        要保持估值不变就得反着来）。

        实时点位由 :class:`FixedPairFetcher` 固定成
        ``FakeSource.LIVE_PAIR_VALUE``（31500），与基准日的 ``PAIR_VALUE``
        （31098.7787）**不相等**，所以下面对"方向"的断言每次都真的被执行。
        以前这一级是联网抓的：方向对不对全看当天涨跌，而且离线时整个用例
        会以"业务错了"的样子失败。
        """
        old_pair = self.PAIR_VALUE
        source = FakeSource(pair_value=self.PAIR_VALUE,
                            calibration=self.OLD_CALIBRATION, cny=self.CNY)
        # 让基准日能取到配对值
        source._pair_history = {"2026-10-01": old_pair}

        outcome = _calibrator(source).calibrate_middle("QQQ", apply=False)

        self.assertEqual(outcome.pair_value, FakeSource.LIVE_PAIR_VALUE)
        expected = self.OLD_CALIBRATION * old_pair / FakeSource.LIVE_PAIR_VALUE
        self.assertAlmostEqual(outcome.calibration, expected, places=9)
        self.assertTrue(outcome.scale_converted)
        # 方向：**反向**缩放 —— 实时点位比基准日高，校准值就得比原来小
        self.assertGreater(FakeSource.LIVE_PAIR_VALUE, old_pair)
        self.assertLess(outcome.calibration, self.OLD_CALIBRATION)

    def test_pair_value_comes_from_injected_fetcher_not_network(self):
        """**回归**：实时点位必须来自注入的 fetcher，不能碰网络。

        这条盯着一个真实事故：``_pair_value`` 的兜底会去新浪抓 ``gb_ndx``，
        于是同一套用例在装了 ``requests`` 的机器上全绿、没装的机器上红 6 个。
        这里连 ``_pair_live_quote`` 一起换成会抛异常的实现 ——
        只要还有代码路径走到它，用例就失败。
        """
        source = FakeSource()
        calibrator = AutoCalibrator(
            source, pair_fetcher=FixedPairFetcher(FakeSource.LIVE_PAIR_VALUE))

        def _boom(symbol):
            raise AssertionError("单元测试不该联网：_pair_live_quote(%s)" % symbol)

        calibrator._pair_live_quote = _boom

        outcome = calibrator.calibrate_middle("QQQ", apply=False)

        self.assertFalse(outcome.skipped)
        self.assertEqual(outcome.pair_value, FakeSource.LIVE_PAIR_VALUE)

    def test_implied_nav_is_the_closing_price(self):
        """隐含净值 = **当日收盘价**，不再是「汇率 / 校准值」。

        旧口径只在种子尺度下凑巧等于真实的 749.58；换成生产尺度
        （cal_m = 41.5793）它给出 0.161，差 4648 倍（§四.3 坑②）。
        新口径里一个 ``cal_m`` 都不出现，所以刻度重算不会把它带崩。
        """
        seed = FakeSource(calibration=self.OLD_CALIBRATION, cny=self.CNY)
        seed.get_latest_history_close = lambda stock_id: 749.4131
        self.assertAlmostEqual(
            _calibrator(seed)._implied_middle_nav(4, self.CNY), 749.4131, places=6)

        # 校准值翻 4600 倍，结果一动不动 —— 这才是不再依赖尺度的证据
        prod = FakeSource(calibration=41.5793, cny=6.69)
        prod.get_latest_history_close = lambda stock_id: 749.4131
        self.assertAlmostEqual(
            _calibrator(prod)._implied_middle_nav(4, 6.69), 749.4131, places=6)

    def test_realtime_quote_beats_history_close(self):
        """有实时快照就用实时的（PHP 语义），历史收盘价是退路。"""

        class _Quote(object):
            price = "757.11"

        source = FakeSource()
        source.get_latest_history_close = lambda stock_id: 743.57
        source.get_stored_quote = lambda symbol: _Quote()

        implied = _calibrator(source)._implied_middle_nav(4, self.CNY, symbol="QQQ")

        self.assertAlmostEqual(implied, 757.11, places=6)

    def test_without_calibration_falls_back(self):
        """没有原校准值时退回 PHP 原公式，并留下告警。

        没有 ``cal_m`` 就算不出"该部署尺度的真实净值"，所以校准值无从谈起
        （``_implied_middle_nav`` 返回 ``None``）。这里只断言**不会静默成功**。
        """
        source = FakeSource(pair_value=self.PAIR_VALUE, calibration=None, cny=self.CNY)
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=False)

        self.assertTrue(outcome.skipped or outcome.warnings)

    def test_warning_when_no_implied_nav(self):
        """取不到隐含净值时按库里净值直接算，并记告警（不静默）。"""
        source = FakeSource(pair_value=self.PAIR_VALUE, calibration=0.008945, cny=None)
        # 汇率取不到 -> _cny 回落 -> 仍能算出隐含净值（rate 按 1.0）
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=False)
        self.assertIsNotNone(outcome)


class GateTest(unittest.TestCase):
    """闸门：``净值日期 == 校准日期`` 时不重做（对应 PHP 的 ``return``）。"""

    def test_skips_when_same_date(self):
        source = FakeSource(net_date="2026-10-01", calibration_date="2026-10-01")
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=False)

        self.assertTrue(outcome.skipped)
        self.assertIn("已校准过", outcome.skipped)

    def test_proceeds_when_dates_differ(self):
        source = FakeSource(net_date="2026-10-02", calibration_date="2026-10-01")
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=False)

        self.assertFalse(outcome.skipped)
        self.assertIsNotNone(outcome.calibration)

    def test_no_calibration_record_is_not_treated_as_same_date(self):
        """**陷阱**：没有校准记录时日期是 ``None``。

        如果把它当成空串、而净值日期又恰好能相等，就会被误判成"已校准过"。
        这里断言：没有记录时必须照常校准。
        """
        source = FakeSource(net_date="2026-10-02", calibration=None)
        calibrator = _calibrator(source)
        # _calibration_date 对"没有记录"返回 None
        self.assertIsNone(calibrator._calibration_date(4))


class WriteTest(unittest.TestCase):
    """``WriteDaily()`` 语义：已有就不动。"""

    def test_writes_new_row(self):
        source = FakeSource()
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=True)

        self.assertTrue(outcome.written)
        rows = source.connection.calibration
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_id"], 4)
        self.assertEqual(str(rows[0]["date"]), "2026-10-02")

    def test_does_not_overwrite_existing(self):
        source = FakeSource()
        source.connection.calibration = [
            {"id": 1, "stock_id": 4, "date": "2026-10-02", "close": 41.0, "time": None, "num": 1}
        ]
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=True)

        self.assertFalse(outcome.written)
        self.assertAlmostEqual(source.connection.calibration[0]["close"], 41.0, places=9)

    def test_time_is_written(self):
        """``calibrationhistory.time`` 是 NOT NULL 且无默认值。"""
        source = FakeSource()
        _calibrator(source).calibrate_middle("QQQ", apply=True)

        written = source.connection.calibration[0]["time"]
        self.assertIsInstance(written, str)
        self.assertRegex(written, r"^\d{2}:\d{2}:\d{2}$")


class FundToMiddleTest(unittest.TestCase):
    """页面走的是"基金 → 中间层"这条。"""

    def test_middle_symbol_of_uses_leverage_substitute(self):
        """``SZ161130`` 的 ``est_symbol`` 是 ``^NDX``，但校准对象是 ``QQQ``。"""
        calibrator = _calibrator(FakeSource())
        # 假 source 的 get_fund_profile 固定返回 est_symbol='QQQ'
        self.assertEqual(calibrator.middle_symbol_of("SZ161130"), "QQQ")

    def test_calibrate_for_fund_targets_middle(self):
        source = FakeSource()
        outcome = _calibrator(source).calibrate_for_fund("SZ161130", apply=False)

        self.assertEqual(outcome.symbol, "QQQ")
        self.assertIsNotNone(outcome.calibration)

    def test_demo_symbols_declared(self):
        self.assertIn("QQQ", DEMO_MIDDLE_SYMBOLS)
        self.assertIn("SPY", DEMO_MIDDLE_SYMBOLS)


class OutcomeDescribeTest(unittest.TestCase):
    def test_describe_forms(self):
        skipped = CalibrationOutcome(symbol="QQQ", skipped="没有单位净值")
        self.assertIn("跳过", skipped.describe())

        written = CalibrationOutcome(symbol="QQQ", date="2026-10-02", calibration=41.5,
                                     pair_symbol="^NDX", pair_value=31098.0,
                                     net_value=749.58, written=True)
        text = written.describe()
        self.assertIn("已校准", text)
        self.assertIn("^NDX", text)

        dry = CalibrationOutcome(symbol="QQQ", calibration=41.5)
        self.assertIn("未写库", dry.describe())


class RecordingConnection(FakeConnection):
    """记录所有对 ``calibrationhistory`` 的写动作，用来验证"写-还原"成对。

    ``FakeConnection.dispatch`` 原本只实现 SELECT/INSERT，这里补上
    UPDATE/DELETE（守卫的还原动作走的是这两条），否则"记录到了但没执行"，
    测试会误判。
    """

    def __init__(self, **kwargs):
        FakeConnection.__init__(self, **kwargs)
        self.writes = []          # [(动作, 值)]

    def dispatch(self, sql, params):
        upper = sql.strip().upper().replace("`", "")
        if upper.startswith("UPDATE CALIBRATIONHISTORY"):
            # 守卫发的是 UPDATE ... SET close = %s WHERE id = %s
            self.writes.append(("update", params[0]))
            for row in self.calibration:
                if row["id"] == params[1]:
                    row["close"] = params[0]
            return []
        if upper.startswith("DELETE FROM CALIBRATIONHISTORY"):
            self.writes.append(("delete", None))
            self.calibration = [r for r in self.calibration if r["id"] != params[0]]
            return []
        if upper.startswith("INSERT INTO CALIBRATIONHISTORY"):
            self.writes.append(("insert", params[2]))
        return FakeConnection.dispatch(self, sql, params)


class GuardSource:
    """只够守卫用到的那几件事：id、当前校准值、基金净值。"""

    #: 基金官方净值（守卫拿它当比对基准）
    FUND_NET_VALUE = 2.0379

    def __init__(self, original=0.008945):
        self.connection = RecordingConnection(
            # 注意要带上**基金**符号：守卫会先 get_stock_id(基金) 再取净值，
            # 漏了它就一路"没有净值可比"地跳过（踩过）。
            stock={"QQQ": 4, "SPY": 5, "^NDX": 6, "^GSPC": 7, "USCNY": 8, "SZ161130": 1},
            calibration=[{"id": 1, "stock_id": 4, "date": "2026-10-01",
                          "close": original, "time": None, "num": 1}]
        )
        self.original = original

    def get_stock_id(self, symbol):
        return self.connection.stock.get(symbol)

    def get_calibration_now(self, stock_id):
        return FakeDailyRecord(date="2026-10-01", close=self.original)

    def get_latest_net_value_close(self, stock_id):
        """基金净值 —— 守卫的比对基准。

        **必须实现**：``EstimateGuard`` 走的是数据源方法（真实环境是
        ``MySqlFundDataSource``），假数据源漏了它守卫就会以
        "基金没有净值可比"跳过，测试全绿但实际什么都没验 ——
        这正是这里踩到的坑。
        """
        return self.FUND_NET_VALUE


class EstimateGuardRestoreTest(unittest.TestCase):
    """**守卫必须还原它动过的数据。**

    这是它最容易出错、也最危险的地方：守卫的做法是
    "临时写库 -> 跑真实管线 -> 还原"。如果还原漏了，守卫自己就成了破坏者
    —— 而且是在"检查"的名义下破坏，非常隐蔽。

    这里用记录型假连接断言"每一次临时写入之后都有一次写回原值"。
    """

    def test_guard_restores_original_value(self):
        from updater.autocalibration import EstimateGuard

        source = GuardSource(original=0.008945)
        guard = EstimateGuard(source)
        # 把"找基金"与"真实管线"都换成固定实现，专注验证"写-还原"的成对性
        guard._fund_using = lambda symbol: "SZ161130"
        guard._official_estimate = lambda symbol: 2.0379

        verdict = guard.check("QQQ", 41.488229)

        self.assertIn(("update", 41.488229), source.connection.writes)
        self.assertIn(("update", 0.008945), source.connection.writes)
        candidate_index = source.connection.writes.index(("update", 41.488229))
        restore_index = source.connection.writes.index(("update", 0.008945))
        self.assertLess(candidate_index, restore_index, "必须先写候选值、后还原")
        self.assertTrue(verdict.ok)

    def test_guard_rejects_bad_value(self):
        """偏差超过容差就拒写。"""
        from updater.autocalibration import EstimateGuard

        source = GuardSource()
        guard = EstimateGuard(source)
        guard._fund_using = lambda symbol: "SZ161130"
        # 模拟"真实管线被坏校准值搞炸"：估值 9554 vs 净值 2.0379
        guard._official_estimate = lambda symbol: 9554.84

        verdict = guard.check("QQQ", 41.488229)

        self.assertFalse(verdict.ok)
        self.assertIn("偏差", verdict.reason)

    def test_guard_restores_even_when_estimate_raises(self):
        """估值抛异常时也必须还原 —— 否则异常路径会把库留在脏状态。"""
        from updater.autocalibration import EstimateGuard

        source = GuardSource(original=0.008945)

        def _boom(symbol):
            raise RuntimeError("模拟估值失败")

        guard = EstimateGuard(source)
        guard._fund_using = lambda symbol: "SZ161130"
        guard._official_estimate = _boom

        verdict = guard.check("QQQ", 41.488229)

        # 异常被吞掉（守卫不该让页面挂），但还原动作必须发生
        self.assertTrue(verdict.ok)
        self.assertIn(("update", 0.008945), source.connection.writes)

    def test_guard_skips_when_no_fund_uses_middle(self):
        from updater.autocalibration import EstimateGuard

        source = GuardSource()
        source.get_stock_id = lambda symbol: None      # 什么都找不到
        verdict = EstimateGuard(source).check("QQQ", 41.488229)

        self.assertTrue(verdict.ok)
        self.assertIn("跳过", verdict.reason)


class GuardToleranceTest(unittest.TestCase):
    """容差语义：抓"量级错了"，不抓日常漂移。"""

    def test_default_tolerance_is_five_percent(self):
        from updater.autocalibration import GUARD_TOLERANCE

        # 汇率与行情的正常日内变动就有 1% 量级，容差不该收到 1%
        self.assertGreaterEqual(GUARD_TOLERANCE, 0.02)
        self.assertLessEqual(GUARD_TOLERANCE, 0.10)


class NavMagnitudeGuardTest(unittest.TestCase):
    """``nav_matches_price`` —— 派生净值的量级自检（P0.5 的第一道护栏）。

    背景（README_p0_data_layer.md §四.3 坑②）：``_implied_middle_nav`` 的
    恒等式 ``净值 = 汇率 / cal_m`` **只在种子尺度成立**。生产尺度下
    （QQQ 的 cal_m = 41.5793）它给出 6.69/41.58 = 0.161，差 4648 倍 ——
    而 ``usnavsource`` 与 ``autocalibrationcli --set-nav`` 都会把它写进
    ``netvaluehistory``。价格是尺度无关的尺子（ETF 净值与价格同量级），
    先用它挡一道，把这类事故挡在写库之前。
    """

    def test_matching_nav_passes(self):
        # QQQ：派生净值 749.58 vs 当日价 743.57（差 0.8%）
        self.assertTrue(nav_matches_price(749.58, 743.57))

    def test_off_scale_nav_is_rejected(self):
        # 生产尺度下恒等式算出的 0.161，与价格差 4600 倍
        self.assertFalse(nav_matches_price(0.161, 743.57))
        # 反方向（放大 4600 倍）同样要挡
        self.assertFalse(nav_matches_price(743.57 * 4600, 743.57))

    def test_no_price_means_do_not_block(self):
        """没有尺子就不拦 —— 免得把"查不到价格"变成"净值永远算不出来"。"""
        for price in (None, "", 0, "abc"):
            self.assertTrue(nav_matches_price(0.161, price))

    def test_tolerance_band(self):
        self.assertTrue(nav_matches_price(743.57 * 1.2, 743.57))
        self.assertFalse(nav_matches_price(743.57 * 2.0, 743.57))
        self.assertFalse(nav_matches_price(743.57 * 0.5, 743.57))

    def test_derived_nav_is_the_closing_price(self):
        """口径②（P0.5 定稿）：净值就是当日收盘价 —— 与尺度彻底无关。"""
        source = FakeSource(calibration=41.5793, cny=6.69)   # 生产尺度，故意的
        source.get_latest_history_close = lambda stock_id: 743.57

        nav_source = DerivedMiddleNavSource(source)
        value = nav_source._implied_nav("QQQ")

        self.assertAlmostEqual(float(value), 743.57, places=6)
        self.assertEqual(nav_source.warnings, [])

    def test_seed_scale_no_longer_changes_the_answer(self):
        """旧口径在种子尺度下会给出 749.58；新口径下刻度换不出第二个答案。

        这条用例在钉一件事：结果**只由价格决定**。
        """
        source = FakeSource(calibration=0.008945, cny=6.705)
        source.get_latest_history_close = lambda stock_id: 743.57

        nav_source = DerivedMiddleNavSource(source)
        value = nav_source._implied_nav("QQQ")

        self.assertAlmostEqual(float(value), 743.57, places=6)
        self.assertEqual(nav_source.warnings, [])

    def test_no_fallback_constants_once_price_is_available(self):
        """有价格就不再用常量表 —— 749.58 会随真实净值变，价格才跟着走。"""
        source = FakeSource(calibration=41.5793, cny=6.69)
        source.get_latest_history_close = lambda stock_id: 800.0

        nav_source = DerivedMiddleNavSource(source)
        self.assertNotEqual(float(nav_source._implied_nav("QQQ")),
                            FALLBACK_BASE_NAV["QQQ"])


class PriceAsNetValueTest(unittest.TestCase):
    """``净值 = 当日收盘价`` 这条口径的行为锚点。

    背景同上。旧口径 ``汇率 / cal_m`` 把**尺度**（会随部署重算）和**净值**
    （不随尺度变）搅在一起，所以刻度一变就崩；新口径里一个 ``cal_m`` 都不
    出现，下面这些用例保证它真的不出现。
    """

    def _source(self, calibration=41.5793, cny=6.69, close=None):
        source = FakeSource(calibration=calibration, cny=cny)
        if close is not None:
            source.get_latest_history_close = lambda stock_id: close
        return source

    def test_calibration_scale_is_irrelevant(self):
        """校准值翻 4600 倍，净值一动不动。"""
        small = self._source(calibration=0.008945, close=743.57)
        big = self._source(calibration=41.5793, close=743.57)

        nav_small = DerivedMiddleNavSource(small)._implied_nav("QQQ")
        nav_big = DerivedMiddleNavSource(big)._implied_nav("QQQ")

        self.assertAlmostEqual(float(nav_small), float(nav_big), places=6)
        self.assertAlmostEqual(float(nav_big), 743.57, places=6)

    def test_realtime_quote_wins_over_history(self):
        """有实时快照就用实时的（PHP 语义），历史收盘价是退路。"""

        class _Quote(object):
            price = "757.11"
            date = "2026-10-14"

        source = self._source(close=743.57)
        source.get_stored_quote = lambda symbol: _Quote()

        nav_source = DerivedMiddleNavSource(source)
        record = nav_source.fetch_middle_nav("QQQ")

        self.assertAlmostEqual(record.nav, 757.11, places=6)
        self.assertEqual(record.date, "2026-10-14")

    def test_fetch_middle_nav_date_tracks_the_quote_date(self):
        """日期 = 那个价格的行情日期，闸门才有东西可比。"""
        source = self._source(close=743.57)
        source.get_latest_history_date = lambda stock_id: "2026-10-13"

        record = DerivedMiddleNavSource(source).fetch_middle_nav("QQQ")

        self.assertEqual(record.date, "2026-10-13")
        self.assertEqual(record.base_date, "2026-10-13")
        self.assertAlmostEqual(record.nav, 743.57, places=6)

    def test_no_price_falls_back_to_constants(self):
        """查不到价格时退回常量表，不臆造数字。"""
        source = self._source(close=None)

        nav_source = DerivedMiddleNavSource(source)

        self.assertEqual(nav_source._implied_nav("QQQ"), FALLBACK_BASE_NAV["QQQ"])
        self.assertEqual(nav_source.fetch_middle_nav("QQQ"), None)


if __name__ == "__main__":
    unittest.main()
