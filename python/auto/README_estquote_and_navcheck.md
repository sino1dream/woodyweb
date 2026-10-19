# estquote 接入估值公式 + 双源净值校验

本文档记录两件事：

1. 把 `estquote`（中间层实时估值）接进 `fundcalc` 的估值公式；
2. 东财与新浪**双源抓取 + 清洗 + 对比 + 差异告警**，并预留信源选项。

承接 `python/NAV_SOURCE_PLAN.md`（已批准）与 `README_nav_migration.md`。

## 一、estquote 接入估值公式

### 1.1 数据流

```text
quoteupdater（后台定时抓）
  ├─ realtimequote   实时行情快照（页面"最新价"）
  └─ estquote        中间层标的数值（QQQ / SPY / XOP / INDA …）
                        │
StockDataArrayBuilder   │ 附加 est_quote 与 hedge_value
                        ▼
PalmmicroFundCalculator ─ est_net_value()         静态估值
                        └─ est_net_value_with_est_quote()  实时估值
```

### 1.2 关键：中间层要提供"估值贡献"，不是原始市价

`fundcalc.calc_with_calibration()` 用的是：

```text
est_f = (1 - p_f) * nv_f + p_f * est_value / cal_f
```

要让它等于基金官方净值 `nv_f`，必须 **`est_value = nv_f * cal_f`**。

直接把 QQQ 市价（749.58）塞进 `est_value`，这一项就变成 749.58 而不是 4.78 ——
**实测估值被放大到 643194**。这是本次最花时间的坑。

所以 `StockDataArrayBuilder._attach_hedge_value()` 会按 (A) 式
`nv_f * cal_f` 算好贡献值，存成 `hedge_value`。

### 1.3 两个必须注意的细节（都踩过）

**① 贡献值要挂在"基金行"上，不能挂在中间层行上。**

一只中间层标的可以服务多只净值不同的基金（QQQ 同时服务 SZ161130 与 SH513100）。
挂在中间层行上，后算的那只基金会拿到别人的值 —— 实测 SH513100 因此算出
`4.495`（正确值是 `2.0379`）。

**② `est_net_value()` 会再反算一次，所以要走 `est_net_value_with_est_quote()`。**

`est_net_value()` 拿到 source 后还会调一次 `reverse_calc_with_calibration()`
（又乘一次 `cal_m`）。贡献值再乘一次就错了 —— 实测 `4.5452` 被变成 `0.0061`。
`est_net_value_with_est_quote()` 直接注入贡献值、绕开反算。

### 1.4 口径：归一化自洽关系

`calibrationhistory` / `netvaluehistory` 必须满足一组自洽关系，否则估值会差
几个数量级。`estcalibration.py` 与 `_reset_est_scale.py` 负责建立与检查：

| 项目 | 取值 | 说明 |
| --- | --- | --- |
| 中间层 `netvalue` | `1.0` | 归一化基准 |
| 中间层 `calibration` | `nv_f / (est_m * p_m)` | 见 `estcalibration.py` 文件头推导 |
| 基金层 `calibration` | `1.0` | 使估值恒等于官方净值 |

### 1.5 验证结果（真实库）

```text
符号        官方净值    静态估值      带中间层估值   结论
SZ161130   4.5452     4.545200     4.545200     两者都一致
SZ161125   3.1328     3.132800     3.132800     两者都一致
SH513100   2.0379     2.037900     2.037900     两者都一致
```

**静态路径与实时路径现在给出一致结果**（以前因为静态走 `est_netvalue` 种子值，
两者会差到 `0.227` vs `4.545`）。修法见 `fundcalc._middle_est_value()`。

### 1.6 涉及的文件与函数

| 位置 | 改动 |
| --- | --- |
| `stockdata.py` `StockDataArrayBuilder.build()` | 统一附加 `est_quote`；对三条进入路径都生效 |
| `stockdata.py` `_attach_est_quote()` | 从 `estquote` 表取中间层数值 |
| `stockdata.py` `_attach_hedge_value()` | 算 `hedge_value = nv_f * cal_f`，挂在基金行 |
| `stockdata.py` `get_est_quote()` | 数据源接口（默认返回 `None`，老源不用改） |
| `stockdata.py` `MySqlFundDataSource.get_latest_net_value()` | 新增，**带日期**（校准值必须与净值同日） |
| `fundcalc.py` `est_source()` | 返回贡献值（`hedge_value`，回落 `netvalue`） |
| `fundcalc.py` `est_net_value_with_est_quote()` | 注入贡献值、绕开反算 |
| `fundcalc.py` `_middle_est_value()` | 静态路径也优先用 `hedge_value` |
| `estcalibration.py`（新） | 尺度自洽工具：诊断 + 重标定 |
| `_reset_est_scale.py`（新，开发用） | 一次性重建自洽的演示数据 |

## 二、双源净值校验（东财 vs 新浪）

### 2.1 需求与实现对照

| 需求 | 实现 |
| --- | --- |
| 东财与新浪都抓 | `CrossCheckNavSource.fetch_latest()` 两个源各抓一次 |
| 清洗后对比 | `normalize_nav()` / `normalize_date()`，见下 |
| 有差异要告警 | 超出容差 → `ALERT` 打到 stderr |
| 预留使用信源选项 | `PALMMICRO_NAV_SOURCE` 切换；也可构造时传参 |
| 默认优先东财 | `DEFAULT_PRIMARY_SOURCE = "eastmoney"` |

### 2.2 清洗规则

| 脏数据 | 处理 |
| --- | --- |
| `""` / `None` / `"-"` / `"--"` / `"N/A"` / `"null"` | 视为缺失（`None`） |
| `"1,234.5"`（千分位）、`"4.5452%"` | 去掉逗号/百分号再解析 |
| 前后空白 | 去空白 |
| 日期 `2026/09/29`、`20260929` | 统一成 `2026-09-29` |

日期归一很重要：否则"数值一样只是写法不同"会被误报成差异。

### 2.3 告警分级

| 情况 | 级别 | 输出 |
| --- | --- | --- |
| 两源净值一致 | —— | 静默 |
| 数值一致但日期不同 | `INFO` | stdout（各源更新时点不同，属正常） |
| \|净值差\| > 容差 | `ALERT` | stderr |
| 主源无数据、用对照源顶上 | `WARN` | stderr |
| 信源抛异常 | `WARN` | stderr（一只失败不影响整批） |
| 净值为脏数据无法解析 | `WARN` | stderr |

`INFO` 走 stdout、`WARN`/`ALERT` 走 stderr，是为了让日志系统能直接按 stderr
抓告警，不必解析普通输出。也可以构造时传 `alert=回调` 接到自己的日志里。

### 2.4 信源选项（预留）

```powershell
# 默认：东财为主 + 新浪对照 + 差异告警
python -B auto\cli\navcli.py

# 退回单源
$env:PALMMICRO_NAV_SOURCE = "eastmoney"     # 或 sina / szse
python -B auto\cli\navcli.py

# 调整容差（元）与"缺失也告警"
$env:PALMMICRO_NAV_TOLERANCE = "0.01"
$env:PALMMICRO_NAV_ALERT_ON_MISSING = "1"
```

代码里换源（不改 Web / 抓取层）：

```python
from navsource import CrossCheckNavSource, register_nav_source

# 换对照源或换主源
source = CrossCheckNavSource(primary="sina", secondary="szse", tolerance=0.005)

# 接自己的日志
import logging
source = CrossCheckNavSource(alert=lambda level, msg: logging.warning("%s %s", level, msg))

# 完全不对比
source = CrossCheckNavSource(secondary=None)
```

自查：`python -B auto\cli\navcli.py --api`（打印完整接口契约与当前生效配置）。

### 2.5 实测

```text
$ python -B auto\cli\navcli.py --all-sources 161130
源            日期           单位净值    累计净值    结果
eastmoney    2026-09-29   4.5452     4.5452     OK
sina         2026-09-29   4.5452     4.5452     OK
szse         2026-09-28   4.5357     -          OK
```

两源数值完全一致；深交所慢一天（正常），所以对比时"日期不同、数值一致"
只提示 INFO。

## 三、测试

```powershell
cd F:\woody\woodyweb\python
python -B -m unittest discover -s auto -p "test_*.py"        # 318 个
python -B -m unittest discover -s web\tests -p "test_*.py"   # 36 个
```

新增的 `test_navsource.py` 覆盖：清洗（净值的 6 种脏数据、日期的 3 种写法）、
双源一致/超容差告警/容差内不告警/日期不同只提示、主源缺失回落对照源、
两源都缺失、主源异常、关闭对比、容差来自环境变量、
`crosscheck` 默认配置与**自我递归防护**。

## 四、仍未完成

1. **`est_quote` 只覆盖抓过的中间层标的**。目前 `estquote` 表里只有 QQQ/SPY，
   其余 16 个标的要等 `quoteupdater` 抓到才会出现（它们的行情还没进 `stockhistory`，
   所以 `collect_symbols_from_source()` 收不到）。需要先建符号名录或手工指定。
2. **功能 B（管理端补日线历史）** 按决定暂缓。
3. **上交所净值接口** 暂缓（已按你的要求）。
4. **中间层校准值目前由工具重标定**，还没有像 PHP `DailyCalibration()` 那样
   在每日流程里自动重算。接进定时任务时补上。
