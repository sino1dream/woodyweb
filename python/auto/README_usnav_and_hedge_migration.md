# 中间层派生净值 与 hedge_value 贡献值（在途步骤的补记）

本文件补齐 2026-10-09 那一轮**改了代码但没写文档**的工作。按项目约定
（``AGENTS.md``：每一步都要写清对应 PHP 源文件、记录范围取舍与已知偏差）补记，
并顺带修掉一个测试缺陷。

| PHP | 本版 |
| --- | --- |
| ``php/stock/yahoostock.php`` ``YahooUpdateNetValue()``（中间层净值推进） | ``usnavsource.DerivedMiddleNavSource`` |
| ``php/stock.php`` / ``php/stockdataarray.php`` 里的 ``est_netvalue`` | ``stockdata.StockDataArrayBuilder._attach_hedge_value()``（``hedge_value``） |
| ``php/stock/stocksymbol.php`` 的各 ``QdiiGet*SymbolArray()`` | ``fundcalc`` 改为直接复用 ``stocksymbol``（不再自己抄一份） |

---

## 零、先修的缺陷：单元测试会真的联网

### 0.1 症状

同一套 ``auto`` 测试在**装了 ``requests`` 的机器上全绿、没装的机器上红 6 个**，
且 6 个失败全在 ``test_autocalibration.py``，看起来像业务逻辑错了。

### 0.2 根因

``AutoCalibrator._pair_value()`` 的第三级兜底是 :meth:`_pair_live_quote`
—— **真的去新浪抓指数点位**（``^NDX`` → ``gb_ndx``）。测试替身 ``FakeSource``
上有个 ``pair_value`` 属性，但**没有任何方法会返回它**，于是每次都漏到联网那一级。
离线时返回 ``None``，``outcome.pair_value`` / ``outcome.calibration`` 跟着变
``None``，用例以"业务错了"的面目失败。

实测对照（同一句调用）：

```text
Python 3.7（有 requests/certifi）  fetch(['gb_ndx']) -> 285 字节正常数据
Python 3.13（无 requests/certifi） fetch(['gb_ndx']) -> None
```

### 0.3 修法：把联网这件外部依赖收敛成一个可注入的口子

* ``AutoCalibrator.__init__`` 新增 ``pair_fetcher`` 参数（默认 ``None`` → 走新浪）；
* 新增 ``_fetch_pair_quote()`` 作为**唯一出口**：``pair_fetcher`` 优先，否则
  ``_pair_live_quote()``。
  与第 2 步（``stockprefetch`` 用回调注入数据库/缓存依赖）是同一个套路 ——
  **外部依赖必须能顶掉，否则"逻辑可测"是空话**。

测试侧：

* ``FixedPairFetcher`` 把实时点位固定成常量；
* ``FakeSource.LIVE_PAIR_VALUE = 31500.0``，**故意与基准日配对值
  ``PAIR_VALUE = 31098.7787`` 不相等**。这样"配对值变了就反向缩放"的
  **方向**每次都被真的断言到 —— 以前这一级是联网抓的，方向对不对全看当天涨跌，
  那个 ``if abs(...) > 1e-6`` 分支形同虚设。
* 新增 ``test_pair_value_comes_from_injected_fetcher_not_network``：
  把 ``_pair_live_quote`` 换成会抛 ``AssertionError`` 的实现，
  **只要还有代码路径走到联网，用例就失败**（防回归，不是防当前）。

修完：``auto`` **522 个**在 Python 3.7 与 3.13 上**都全绿**（3.13 那次是在
断网环境下跑的，因此也顺带证明了不再依赖外网）。

---

## 一、``usnavsource``：美股 ETF 净值的**派生**源

### 1.1 为什么是"派生"而不是真实 NAV

生产端能拿到美股 ETF 的官方 NAV，本项目实测过一圈免费信源，**没有一个能用**
（结论写在 ``usnavsource.py`` 的模块 docstring 里，含逐个信源的实测结果）：

```text
Invesco dng-api（页面 JS 调的）  HTTP 406（需要 JS 挑战）
Invesco product 页 HTML          200，但 NAV 是前端 JS 后填的
Invesco 持仓 csv                 HTTP 406
新浪美股 gb_qqq                  200，36 个字段里没有 NAV（净资产恒为 0）
东财 push2 美股                  RemoteDisconnected（反爬）
yfinance / AKShare               网络层被拦 / 只有中国基金
```

### 1.2 派生式子

用项目里**已经在用的恒等式**反推（见 ``autocalibration._implied_middle_nav``）：

```text
中间层每份净值 = 汇率 / 本符号校准值
```

实测 ``6.705 / 0.008945 = 749.58``，与生产端 QQQ 真实 NAV ``749.378`` 吻合到
**0.03%** —— 因为种子那条校准值**本来就是按真实 NAV 解出来的**，所以这个派生值
锚定在真实净值上，不是凭空编的数。

日期按最新行情日期推进，这样 ``DailyCalibration()`` 的闸门
（净值日期 == 校准日期）才有东西可比。

### 1.3 踩过的坑：不要再乘价格比

第一版写成 ``基准净值 × 价格(t) / 价格(基准日)``，量级错到 **0.16**。
原因：**"该部署尺度的净值"与"行情价格"不是同一尺度** —— 校准值里已经含了缩放。
恒等式给出的就是最终值，**价格只用来定日期**。

### 1.4 必须说清的局限（写进了模块的 ``describe()``）

* 这是**价格驱动**的净值，不是官方 NAV：**分红除息日会失真**
  （官方 NAV 会因分红下降，派生值不会）；
* 所以**不能**用它对账，只能用来"让校准闸门前进"；
* 接到真实 NAV 源后替换 ``fetch_latest`` 即可 —— 本模块刻意实现成
  ``navsource.NavSource`` 的形状（``name`` / ``fetch_latest``），上层不用改。

接入点：``autocalibrationcli.py`` 的净值推进（``--set-nav`` 那条路，见其第 199 行注释）。

---

## 二、``hedge_value``：中间层对基金的"估值贡献"

### 2.1 要解决什么

``fundcalc.est_net_value()`` 的标准路径要先**反算**中间层
（``reverse_calc_with_calibration``）再算基金。想绕过反算、直接用中间层实时值，
就得把反算结果**预先算好** —— 而它需要**跨层信息**（基金净值 + 基金校准值），
单看中间层自己求不出来。

目标：让 ``calc_with_calibration(fund)`` 返回基金官方净值。那里用的是
``p_f * est_value * cny / cal_f``，所以

```text
est_value = nv_f × cal_f / cny                                    …(A)
```

### 2.2 两个必须注意的点（都实测踩过）

1. **``(A)`` 里的 ``/ cny`` 不能省**：估值公式里 ``est_value`` 后面还会乘一次汇率，
   省掉就等于把汇率算两遍 —— 汇率约 7，实测估值被放大 7 倍。
2. **贡献值必须挂在基金行，不能挂中间层行**：一个中间层可以服务多只净值不同的
   基金（QQQ 同时服务 ``SZ161130`` 与 ``SH513100``）。挂在中间层行上，
   后算的那只基金会拿到别人的值 —— 实测 ``SH513100`` 因此算出 4.495（应为 2.0379）。

实测核对（``fundestcalc.py`` 注释）：``SZ161130`` 的
``hedge_value = 21.746 = 4.5452 × 32.079543 / 6.705``。

### 2.3 代码落点

* ``stockdata.StockDataArrayBuilder._attach_hedge_value()``：按 ``(A)`` 算出并
  **挂在基金行**；只在中间层确实有实时源（``est_quote``）时才接管，
  否则保留标准路径（不抢老数据的路）。
* ``fundcalc._middle_est_value()``：取值优先级 ``hedge_value`` → ``est_netvalue``。
  有了前者，**静态估值与实时估值才会一致**（只有 ``est_netvalue`` 时两者会差到
  0.227 vs 4.545）。
* ``fundcalc.est_source()``：把各行贡献值拼成 source 字典。
  注释里写清了**为什么不能在这里乘系数缩放**：``est_net_value()`` 拿到 source 后
  还会再调一次反算（又乘 ``cal_m``）。
* ``fundcalc.est_net_value_with_est_quote()``：绕开反算、直接注入贡献值再算基金
  —— 这是"页面实时估值列"**应当**走的那条路。

### 2.4 未完成

``est_net_value_with_est_quote()`` 目前**只被 ``_debug_forex_verify.py`` 调用**，
``web/services/fund_list_service.py`` 还没接。
也就是说：贡献值机制在"库 → 构建器 → fundestcalc"这条链上打通了，
但**页面上的实时估值列还没用上**。这是下一步最自然的接线点。

---

## 三、``fundcalc`` 不再自己抄一份 QDII 符号表

原来 ``fundcalc.py`` 顶部硬编码了 9 组 QDII 符号元组（``QDII_QQQ_MATCH_SYMBOLS``
等），与 ``stocksymbol.py``（``stocksymbol.php`` 的移植）**重复定义**。

现改为从 ``stocksymbol`` 的 ``qdii_get_*_array()`` 取，再以同名元组
**重新导出**，保证历史 API 与身份判断不变：

```python
QDII_QQQ_MATCH_SYMBOLS = tuple(qdii_get_qqq_match_array())
```

动机：两处定义迟早会漂移，而符号表是"哪些基金属于哪一类"的唯一事实来源。
顺带让 ``qdii_symbols()`` 直接委托 ``qdii_get_symbol_array()``。

---

## 四、``stockdata`` 新增的取数口

这一轮给数据源补了一批上层要用的口子（三个实现类都补齐，保持接口一致）：

| 方法 | 用途 |
| --- | --- |
| ``get_stock_name(symbol)`` | 按符号取名称 |
| ``get_est_quote(stock_id)`` | 中间层估值源的最新值（``estquote`` 表） |
| ``get_latest_net_value_close/date`` | 最新官方净值与其日期 |
| ``get_latest_net_value(stock_id)`` | 整条净值记录 |
| ``get_latest_history_close/date`` | 最新行情收盘价与其日期 |
| ``get_prev_history_close(stock_id, date)`` | 指定日之前的最后一个收盘价（回退取值用） |
| ``get_stored_quote(symbol)`` / ``get_stored_quote_date`` | 读实时快照（不判 TTL） |

配套的内部改动：

* ``_attach_est_quote()``：把 ``estquote`` 的值补进数据行。
  **刻意不做任何换算** —— ``est_quote``（中间层估值源最新值）与 ``netvalue``
  （官方净值）语义不同，尺度由 ``calibrationhistory`` 承担
  （``php/stock/qdiiref.php:193`` ``fFactor = est * cny / netvalue`` 说明校准值
  是"源值 + 净值"反算出来的派生量，换源时跟着重算即可）。
* ``_get_daily_close_asof()``：按"截至某日"取收盘价，供回退链使用。
* ``_note_rate_age()``：汇率回退超过 ``MAX_RATE_FALLBACK_DAYS``（10 天）时报警。
* ``load_mysql_env`` / ``parse_mysql_env_file`` / ``mysql_config_from_env`` /
  ``_validate_mysql_config`` / ``mysql_connection_error_message``：
  把 MySQL 配置的读取与校验集中一处。**占位值（``<...>``）会被显式拒绝**，
  连接错误按错误码给出针对性提示（例如 1045 提示查账号密码）——
  以前这类失败只会抛一句看不懂的异常。

测试：``test_stockdata.py`` 新增 11 个用例（含新增的
``PositionDefaultTest``：默认值必须回落到 ``StockSymbol::GetDefaultPosition()``，
即 LOF 取 0.95 而不是 1.0，与 PHP 官网口径一致）。

---

## 五、``sql/measure_exponent.py``：临时测量脚本

**这不是产品代码，是排查工具**，放在这里是因为它的结论被写进了
``autocalibration.calibrate_middle()`` 的注释。

背景：为"配对值变了该怎么改校准值"做过 4 次代数推导，**每次答案都不同**
（41.35 / 0.00885 / 1.93e-06 …），因为搞不清 ``_qdii_value`` 内部哪些量是独立的。

正确做法是**测量而不是推导**：把库改成已知状态，只把中间层校准值 ``cal_m``
乘 ``r``，量出估值的变化倍数 ``E(r)``，对 ``log E = k × log r`` 做最小二乘。
实测 **k ≈ 0.94**（近似线性正比）。

结论：``cal_new = cal_old × pair_old / pair_new``（**反向**缩放）。
五个代数推导的候选值全部被估值守卫拒绝，只有这条和"不动校准值"能放行。

---

## 六、仍未完成

1. **页面实时估值列未接线**：``est_net_value_with_est_quote()`` 只被调试脚本调用
   （见 2.4）。
2. **``usnavsource`` 没有单元测试**：模块本身无 ``test_usnavsource.py``，
   目前只有手工实测（749.58 vs 749.378）。
3. **真实美股 NAV 源仍缺位**：派生值在分红除息日会失真，接不到真实源之前
   不能用于对账。
4. **自动校准的平均语义**：``AverageCalibraion()`` 的自动触发仍未做，
   PHP 在 ``CheckAdjustFactorTime()`` 通过时会取当天多次校准的平均；
   本版自动校准走 ``WriteDaily()``（已有就不动）。
5. **原油/日本/欧洲的中间层**：``MIDDLE_PAIR_FALLBACK`` 只有 ``QQQ``/``SPY``。
