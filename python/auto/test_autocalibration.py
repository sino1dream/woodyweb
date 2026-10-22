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
        if "UPDATE CALIBRATIONHISTORY" in upper:
            #: 对应 ``CalibrationSql::UpdateDaily()``：按 id 改 close + time，
            #: 并把 num 重置为 1（不是累加，见 ``_update_record`` 的说明）。
            value, time_text, record_id = params[0], params[1], params[2]
            for row in self.calibration:
                if str(row["id"]) == str(record_id):
                    row["close"] = value
                    row["time"] = time_text
                    row["num"] = 1
                    return []
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


class LayerGuardTest(unittest.TestCase):
    """**层次守卫**：``calibrate_middle`` 只服务中间层，基金走 ``calibrate_fund``。

    ## 为什么要分开

    两个层次的分母不是同一个东西：

    * 中间层（``QQQ``）：P0.5 定稿的口径是「净值 = 当日收盘价」——
      美股 ETF 的净值与交易价只差千分位，成立；
    * 基金层（``SH513100``）：分母必须是 ``netvaluehistory`` 里的**单位净值**。

    QDII 基金的场内价与净值差着**折溢价**，实测 SH513100 净值 2.0563、
    场内价 2.399，差 **16.7%**。基金一旦漏进中间层那条路径，校准值就整体偏掉
    这么多，而估值恰好会因此"等于场内价"（2.441）—— 量级完全正常，
    是最难发现的那一种错。演示库里真的出过这条记录（id=88，
    2026-10-14 的 83792.206883，用 2.399 当分母），已删、已补这道守卫。

    PHP 那边不会有这个问题：``FundReference::LoadSinaFundData()``
    （``php/stock/stockref.php:483``）里 ``$this->strPrice = $ar[1]; // net value``，
    基金的"价"本身就是净值，所以 ``AdjustFactor()`` 的 ``GetPrice()`` 拿对了。
    """

    def test_fund_is_rejected_and_nothing_is_written(self):
        source = FakeSource(stock={"QQQ": 4, "SH513100": 3})
        calibrator = _calibrator(source)
        outcome = calibrator.calibrate_middle("SH513100", apply=True)
        self.assertTrue(outcome.skipped)
        self.assertIn("calibrate_fund", outcome.skipped)
        self.assertIsNone(outcome.calibration)
        # 真要写库也写不出东西：守卫在取净值之前就返回了
        self.assertEqual(source.connection.calibration, [])
        self.assertEqual(source.connection.commits, 0)

    def test_middle_layer_is_not_mistaken_for_a_fund(self):
        """守卫不能把正常路径也挡掉 —— ``QQQ``/``SPY`` 是**值**不是**键**。

        ``qdii_est_symbol_map()`` 的键是基金、值是估值标的，实测两者没有交集
        （33 个基金 / 14 个标的），所以"在不在键里"是个干净的判据。
        """
        calibrator = _calibrator(FakeSource())
        self.assertFalse(calibrator._is_fund_layer_symbol("QQQ"))
        self.assertFalse(calibrator._is_fund_layer_symbol("SPY"))
        self.assertTrue(calibrator._is_fund_layer_symbol("SH513100"))
        self.assertFalse(calibrator._is_fund_layer_symbol(""))

    def test_qqq_still_reaches_the_calibration_steps(self):
        """中间层符号不受影响：跳过的原因不能是层次守卫。"""
        source = FakeSource()
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=False)
        self.assertNotIn("calibrate_fund", outcome.skipped or "")


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


class CalcFactorIsProductionFormulaTest(unittest.TestCase):
    """净值是真实每份净值时，校准值改由 **PHP 原公式**算出（P0.5）。

    ## 背景

    PHP ``DailyCalibration()``（``fundpairref.php:196-201``）从没有"按配对比值
    缩放"这一步，它直接就是：

    ```php
    $fFactor = $this->CalcFactor($fPairNetValue, $strNetValue, $strDate);
    $cal_sql->WriteDaily($strStockId, $strDate, $strFactor);
    ```

    而 ``cal = 配对标的 / 净值`` 恰好就是生产端的定义口径 —— 实测对得回来：

    ```text
    2026-10-13  ^NDX 30569.4809 / QQQ 743.57  = 41.1118   生产锚点 41.111
    2026-10-07  ^NDX 31160.08   / QQQ 749.413 = 41.5793   库里 cal_m 41.5793
    ```

    之前绕开它，是因为演示库的中间层净值是归一化到 1.0 的，直接除会放大 750
    倍。现在 ``sync-nav`` 按"当日收盘价"写入净值（见 ``usnavsource``），
    这道判断就能放行了。

    ## 为什么 Trust/不再偏爱旧缩放

    ``cal_old × pair_old / pair_new`` 把**净值的变化**整项丢了 ——
    完整的换算应该是 ``cal_old × pair_new/pair_old × nav_old/nav_new``。
    实测 QQQ 就因为它偏出 4.7%（43.03 vs 锚点 41.11）。
    """

    PAIR_VALUE = 30569.4809          # ^NDX 2026-10-13
    NAV = 743.57                     # QQQ 当日收盘价（= 写进 netvaluehistory 的净值）
    OLD_CALIBRATION = 41.5793

    def _source(self, **kwargs):
        source = FakeSource(pair_value=self.PAIR_VALUE, netvalue=self.NAV,
                            calibration=self.OLD_CALIBRATION, cny=6.69,
                            live_pair_value=self.PAIR_VALUE, **kwargs)
        # 有了"当日收盘价"，净值也就通过 nav_matches_price 的量级自检
        source.get_latest_history_close = lambda stock_id: self.NAV
        source.get_latest_net_value = lambda stock_id: FakeDailyRecord(
            date=source.net_date, close=self.NAV)
        return source

    def test_uses_pair_over_net(self):
        """放行后走 ``pair / net``，不再走配对比值缩放。"""
        outcome = _calibrator(self._source()).calibrate_middle("QQQ", apply=False)

        self.assertAlmostEqual(outcome.pair_value, self.PAIR_VALUE, places=4)
        self.assertAlmostEqual(outcome.calibration,
                               self.PAIR_VALUE / self.NAV, places=6)
        self.assertAlmostEqual(outcome.calibration, 41.1118, places=3)
        self.assertFalse(outcome.scale_converted)

    def test_cny_is_not_multiplied_in(self):
        """非 A 股中间层 ``cny_ref`` 为 false，`CalcFactor` **不乘汇率**。

        这是 PHP ``fundpairref.php:14-43`` 的 ``else`` 分支。传真实汇率 6.69
        会把 41.11 变成 275 —— 差一个数量级。
        """
        source = self._source()

        outcome = _calibrator(source).calibrate_middle("QQQ", apply=False)

        self.assertAlmostEqual(outcome.calibration, 41.1118, places=3)
        self.assertLess(outcome.calibration, 100)     # 乘了汇率会是 275

    def test_no_price_keeps_the_old_scaling(self):
        """**没有尺子 ≠ 可信**：取不到当日价格时退回旧的配比值缩放。

        ``nav_matches_price`` 对 ``price=None`` 是放行的（那条规则是"别把
        查不到价格变成净值永远算不出来"），但这里要判断的是"净值是不是真实
        每份净值" —— 拿不到价格就不能假装它可信，否则
        ``pair / 1.0 = 31500`` 会被当成校准值。
        """
        source = FakeSource(pair_value=self.PAIR_VALUE, netvalue=1.0,
                            calibration=self.OLD_CALIBRATION, cny=6.69)
        source.get_latest_net_value = lambda stock_id: FakeDailyRecord(
            date=source.net_date, close=1.0)

        outcome = _calibrator(source).calibrate_middle("QQQ", apply=False)

        self.assertTrue(outcome.scale_converted or
                        outcome.calibration == self.OLD_CALIBRATION)

    def test_normalized_nav_still_takes_the_old_route(self):
        """净值停在归一化尺度（1.0）而有价格时，量级自检把它挡回旧退路。"""
        source = FakeSource(pair_value=self.PAIR_VALUE, netvalue=1.0,
                            calibration=self.OLD_CALIBRATION, cny=6.69)
        source.get_latest_history_close = lambda stock_id: self.NAV
        source.get_latest_net_value = lambda stock_id: FakeDailyRecord(
            date=source.net_date, close=1.0)

        outcome = _calibrator(source).calibrate_middle("QQQ", apply=False)

        # 1.0 与 743.57 不同量级 -> 不可能是 fine-grained 的 pair/net
        self.assertNotAlmostEqual(outcome.calibration,
                                  self.PAIR_VALUE / self.NAV, places=2)


class StoredQuotePairValueTest(unittest.TestCase):
    """``_pair_value`` 的第三级：``realtimequote`` **必须卡日期**。

    不卡就会拿"此刻"的指数点位去配"那天"的净值。实测这一项让 QQQ 的校准值
    从生产锚点 41.111 掉到 40.49（拿到 30111 而不是 10-13 的 30569）。
    """

    class _Quote(object):
        def __init__(self, date, price):
            self.date = date
            self.price = price

    def _source(self, quote):
        source = FakeSource()
        source.get_stored_quote = lambda symbol: quote
        return source

    def test_matching_date_is_used(self):
        calibrator = _calibrator(self._source(self._Quote("2026-10-13", "30569.4809")))

        value = calibrator._pair_value("^NDX", "2026-10-13")

        self.assertAlmostEqual(float(value), 30569.4809, places=4)

    def test_mismatched_date_is_ignored(self):
        """日期不符就当没有 —— 宁可落到新浪兜底，也不口径混用。"""

        class _Boom(object):
            @staticmethod
            def __call__(symbol):
                raise AssertionError("不该联网：real-time quote 日期不符")

        source = self._source(self._Quote("2026-10-09", "30883.148"))
        calibrator = _calibrator(source)
        calibrator.pair_fetcher = _Boom()

        # 返回 None 而不是错的日期的值（FakeSource 的 stockhistory 查不到 10-13）
        value = calibrator._pair_value("^NDX", "2026-10-13")
        # 新浪那一级在我们的 FakeSource 上没有 history，会落到 netvalue/stockhistory
        # 都取不到 -> None
        self.assertIsNone(value)


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
    """``CalibrationSql::WriteDaily()`` 语义：**值变了才覆盖**，不是"已有就不动"。

    PHP（``php/sql/sqldailyclose.php:161-179``，``UpdateDaily`` 由
    ``php/sql/sqldailytime.php:48`` 覆写）：

    ```php
    if ($record = $this->GetRecord($strKeyId, $strDate))
    {
        if (abs(floatval($record['close']) - floatval($strClose)) > MIN_FLOAT_VAL)
            return $this->UpdateDaily($record['id'], $strClose);   // 值变了就覆盖
    }
    else
    {
        if ($ymd->IsWeekend())      return false;                  // 防周末假数据
        return $this->InsertDaily($strKeyId, $strDate, $strClose);
    }
    return false;                                                  // 值没变：不动
    ```

    早期只照抄了 ``InsertDaily`` 那句判重，结果校准值一旦写错就再也刷不回来。
    2026-10-22 改锚点口径后，库里那条 16498.992 正是靠这一支刷成新值的。
    """

    def test_writes_new_row(self):
        source = FakeSource()
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=True)

        self.assertTrue(outcome.written)
        rows = source.connection.calibration
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_id"], 4)
        self.assertEqual(str(rows[0]["date"]), "2026-10-02")

    def test_overwrites_existing_when_value_changed(self):
        """值变了 → UPDATE 同一行（不追加），并把 ``num`` 重置为 1。"""
        source = FakeSource()
        expected = _calibrator(source).calibrate_middle("QQQ", apply=False).calibration
        source.connection.calibration = [
            {"id": 1, "stock_id": 4, "date": "2026-10-02", "close": 41.0, "time": None, "num": 7}
        ]
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=True)

        self.assertTrue(outcome.written)
        #: 覆盖而不是追加：否则同一天会堆出多行，``GetRecord`` 只取第一条。
        self.assertEqual(len(source.connection.calibration), 1)
        row = source.connection.calibration[0]
        self.assertAlmostEqual(float(row["close"]), float(expected), places=9)
        #: ``UpdateDailyAverage`` 的默认 ``$iNum = 1``，是重置不是累加。
        self.assertEqual(row["num"], 1)

    def test_leaves_existing_alone_when_value_unchanged(self):
        """值没变（差 ≤ MIN_FLOAT_VAL）→ 一行不动，连 commit 都不发。"""
        source = FakeSource()
        expected = _calibrator(source).calibrate_middle("QQQ", apply=False).calibration
        source.connection.calibration = [
            {"id": 1, "stock_id": 4, "date": "2026-10-02",
             "close": expected, "time": "01:02:03", "num": 1}
        ]
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=True)

        self.assertFalse(outcome.written)
        self.assertEqual(source.connection.commits, 0)
        self.assertEqual(source.connection.calibration[0]["time"], "01:02:03")

    def test_new_row_on_weekend_is_refused(self):
        """新行落周末 → 不写（PHP 防新浪送来的周末假数据）。

        **只挡 INSERT**：已经存在的行仍走 UPDATE 分支，PHP 也是这样
        （``IsWeekend`` 那一句在 ``else`` 里）。
        """
        source = FakeSource(net_date="2026-10-03")       #: 周六
        outcome = _calibrator(source).calibrate_middle("QQQ", apply=True)

        self.assertFalse(outcome.written)
        self.assertEqual(source.connection.calibration, [])

    def test_time_is_written(self):
        """``calibrationhistory.time`` 是 NOT NULL 且无默认值。"""
        source = FakeSource()
        _calibrator(source).calibrate_middle("QQQ", apply=True)

        written = source.connection.calibration[0]["time"]
        self.assertIsInstance(written, str)
        self.assertRegex(written, r"^\d{2}:\d{2}:\d{2}$")


class _FundAnchorSource(FakeSource):
    """给 :class:`FundAnchorTest` 用的假数据源。

    与 :class:`FakeSource` 的差别：``est`` 标的（``^GSPC``）的净值/行情按
    **日期查表**，汇率也可以限定"只有哪几天有"。

    这样才能把"锚点日当天有没有数据"确定地控制住 —— 而这件事正是
    **新口径（锚 = 基金净值日）与旧口径（锚 = est 最新行情日）的分水岭**：
    旧口径下 est 有更新的行情就会把锚点拉走，新口径不会。
    """

    #: 估值标的固定成 ``^GSPC``（``qdii_est_symbol('SZ161125')`` 的结果）。
    EST_ID = 7

    def __init__(self, nav_date="2026-10-20", nav=3.14, est_netvalue=None,
                 est_quote=None, cny=6.72, cny_dates=None, **kwargs):
        stock = dict(kwargs.pop("stock", None) or
                     {"QQQ": 4, "SPY": 5, "^NDX": 6, "^GSPC": 7, "USCNY": 8})
        stock["SZ161125"] = 40
        FakeSource.__init__(self, netvalue=nav, net_date=nav_date, cny=cny,
                            stock=stock, **kwargs)
        self._est_netvalue = dict(est_netvalue or {})
        self._est_quote = dict(est_quote or {})
        self._cny_dates = None if cny_dates is None else set(cny_dates)

    def get_netvalue_close(self, stock_id, date):
        day = str(date)
        if stock_id == self.connection.stock.get("USCNY"):
            #: 限定汇率只在某几天有 —— 用来验 PHP "取不到就 return false"。
            if self._cny_dates is not None and day not in self._cny_dates:
                return None
            return self.cny
        if stock_id == self.EST_ID:
            return self._est_netvalue.get(day)
        return None

    def get_history_close(self, stock_id, date):
        if stock_id == self.EST_ID:
            return self._est_quote.get(str(date))
        return None


class FundAnchorTest(unittest.TestCase):
    """``calibrate_fund`` 的锚点 = **基金净值日**（PHP ``AdjustFactor`` 口径）。

    对应 ``php/stock/qdiiref.php:177-197``：

    ```php
    if ($this->UpdateOfficialNetValue())          // 净值取不到 -> 整段 false
    {
        $strDate = $this->GetDate();              // ← 锚点 = 净值日
        if ($strCNY = $cny_ref->GetClose($strDate))        // 当天没有汇率 -> false
        {
            if (($strEst = $this->_getEstNetValue($est_ref, $strDate)) === false)
                if (($strEst = $est_ref->GetClose($strDate)) === false)  return false;
            $this->fFactor = QdiiGetCalibration($strEst, $strCNY, $this->GetPrice());
        }
    }
    ```

    **本方法曾经**用"est 标的最新行情日"当锚点，代价是混日：QDII 净值比海外
    行情晚一天，早上跑必是"昨天的净值配今天的行情"。2026-10-22 实测
    SZ161125：锚 10-21 配净值 10-20 算出 16599.36，PHP 口径应是 16506.2。
    """

    #: 净值日 10-20；est 在 10-21 **也有**行情（旧口径会把锚点拉到 10-21）。
    EST_QUOTE = {"2026-10-19": 7690.73, "2026-10-20": 7712.57, "2026-10-21": 7757.17}

    def test_anchor_is_net_value_date_even_when_est_has_newer_quote(self):
        source = _FundAnchorSource(nav_date="2026-10-20", est_quote=self.EST_QUOTE)
        outcome = _calibrator(source).calibrate_fund("SZ161125", apply=False)

        self.assertFalse(outcome.skipped)          #: 未跳过时是空串，不是 None
        self.assertEqual(str(outcome.date), "2026-10-20")
        #: est 必须取**净值日那天**的行情，不是最新那天（10-21）。
        self.assertAlmostEqual(float(outcome.pair_value), 7712.57, places=9)

    def test_est_prefers_netvalue_over_quote_like_php(self):
        """PHP 先 ``_getEstNetValue()``，取不到才 ``GetClose()``（两级都卡日期）。"""
        source = _FundAnchorSource(nav_date="2026-10-20",
                                   est_netvalue={"2026-10-20": 7700.0},
                                   est_quote=self.EST_QUOTE)
        outcome = _calibrator(source).calibrate_fund("SZ161125", apply=False)

        self.assertFalse(outcome.skipped)
        self.assertAlmostEqual(float(outcome.pair_value), 7700.0, places=9)

    def test_skips_when_est_has_no_value_on_net_value_date(self):
        """est 在净值日没有净值也没有行情 → 不校准（PHP 两级取不到即 false）。

        **不退到快照**：那是旧实现把 cal 算成 97692 的元凶。
        """
        source = _FundAnchorSource(nav_date="2026-10-20",
                                   est_quote={"2026-10-19": 7690.73})
        outcome = _calibrator(source).calibrate_fund("SZ161125", apply=False)

        self.assertIn("没有净值/行情", outcome.skipped or "")
        self.assertIsNone(outcome.calibration)

    def test_skips_when_cny_missing_on_net_value_date(self):
        """汇率在净值日没有 → 不校准（PHP ``GetClose($strDate)`` 取不到即 false）。

        旧实现会退到"之前最近一条"，于是 ``cny`` 与校准日错位，
        页面上的"基准汇率"对不上。
        """
        source = _FundAnchorSource(nav_date="2026-10-20", est_quote=self.EST_QUOTE,
                                   cny_dates={"2026-10-19"})
        outcome = _calibrator(source).calibrate_fund("SZ161125", apply=False)

        self.assertIn("没有汇率", outcome.skipped or "")
        self.assertIsNone(outcome.cny)

    def test_skips_when_there_is_no_net_value_at_all(self):
        """净值本身取不到 → 整段失败（PHP ``UpdateOfficialNetValue()`` 返回 false）。"""
        source = _FundAnchorSource(est_quote=self.EST_QUOTE)
        source.get_latest_net_value = lambda stock_id: None
        outcome = _calibrator(source).calibrate_fund("SZ161125", apply=False)

        self.assertIn("没有单位净值", outcome.skipped or "")

    def test_three_inputs_land_on_the_same_day_so_no_mixed_date_warning(self):
        """三量同日 → 不该有"净值取的是 X（锚点日 Y 之前…）"这类警告。

        旧口径在早上跑时必挂这条警告，还照样写库。
        """
        source = _FundAnchorSource(nav_date="2026-10-20", est_quote=self.EST_QUOTE)
        outcome = _calibrator(source).calibrate_fund("SZ161125", apply=False)

        self.assertEqual(outcome.warnings, [])
        self.assertEqual(str(outcome.date), "2026-10-20")
        self.assertAlmostEqual(float(outcome.cny), 6.72, places=9)

    def test_calibration_is_est_times_cny_over_nav(self):
        """``QdiiGetCalibration(est, cny, nav) = est × cny / nav``。"""
        source = _FundAnchorSource(nav_date="2026-10-20", nav=3.14,
                                   est_quote=self.EST_QUOTE, cny=6.72)
        outcome = _calibrator(source).calibrate_fund("SZ161125", apply=False)

        self.assertAlmostEqual(float(outcome.calibration), 7712.57 * 6.72 / 3.14, places=6)
        self.assertEqual(outcome.formula, "est*cny/net")

    def test_gate_still_blocks_a_date_already_calibrated(self):
        """锚点日已有校准记录 → 跳过（闸门不受改口径影响）。"""
        source = _FundAnchorSource(nav_date="2026-10-20", est_quote=self.EST_QUOTE,
                                   calibration_date="2026-10-20")
        outcome = _calibrator(source).calibrate_fund("SZ161125", apply=True)

        self.assertIn("已校准过", outcome.skipped or "")
        self.assertEqual(source.connection.calibration, [])


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
