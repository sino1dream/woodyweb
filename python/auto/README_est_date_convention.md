# EST 日期与估值口径：四个根因

用户报（2026-10-20）：

```text
本地 127.0.0.1:5000/fund/qdiilof/SZ161125
  官方EST 3.121 | EST日期 2026-10-16 | 溢价 5.14%
  参考EST 3.102 | 溢价 5.77%
  实时EST 3.117 | 溢价 5.27%

生产端 sz161125cn.php
  官方EST 3.132 | EST日期 2026-10-19 | 溢价 4.75%
  参考EST 3.132 | 溢价 4.76%
  实时EST 3.136 | 溢价 4.61%
```

"最新的基金日期是 2026-10-16，我已更新基金净值，为什么还会出现 EST 日期不一致、
估值表估算不一致？"

查下来是**四个独立缺陷**叠加。修完之后实测：

```text
本地  /fund/SZ161125
  官方EST 3.134 | EST日期 2026-10-19 | 4.64%
  参考EST 3.133 | 4.66%
  实时EST 3.136 | 4.55%
```

日期一致，估值差 0.05% 量级（行情在动 + 显示三位小数）。

---

## 一、EST 日期取错了表（主因）

### PHP 用的是**行情**日期

`qdiiref.php:126` 的 `EstNetValue()`：

```php
$strDate = $est_ref->GetDate();
if ($this->strOfficialCNY = $cny_ref->GetClose($strDate))
{
    $this->fOfficialNetValue = $this->GetQdiiValue($this->_getOfficialEstVal($strDate), $this->strOfficialCNY);
    $this->strOfficialDate = $strDate;          // ← 官方日期 = 中间层**行情**日期
    $this->UpdateEstNetValue();
}
```

`$est_ref` 是 `FundPairReference('^GSPC')`（`QdiiReference` 构造函数按
`QdiiGetEstSymbol()` 建），它的 `GetDate()` 返回**新浪缓存里那一天** ——
与"净值表有没有那一天"无关。

### 我们取的是**净值**日期

`calc/fundestcalc.py` 的 `_latest_date()` 原本先查 `netvaluehistory`：

```python
for reader_name in ("get_latest_net_value_date", "get_latest_history_date"):
```

实测 `SPY` 的行情快照已经是 `2026-10-19`，但它的**净值**只到 `2026-10-16`，
于是 `mid_date` 被定成 `10-16`。

### 连锁后果比日期本身更严重

`_official_est_value(est_symbol, mid_date)` 取 `mid_date` 的净值 → `763.25`；
而 `_est_quote_ref`（校准日的中间层值）**恰好也是 10-16 的 763.25** →
比值 `est / est_quote_ref` 退化成 **1.0** → 官方 EST **恒等于基金净值**
（3.1206）。

所以那 3.121 不是"算出来的估值"，就是净值本身。

### 修法

`_latest_date()` 改成**行情优先**：

1. `realtimequote` 快照日期（daemon 写的跨进程缓存，角色同 PHP 的
   `debug/sina/*.txt`）；
2. `stockhistory` 最新日期；
3. `netvaluehistory` 最新日期（最后兜底）。

修完 `mid_date = 2026-10-19`，官方 EST = `3.1326`，生产端 `3.132` ✓。

---

## 二、中间价历史被即期汇率污染

### 症状

`USCNY` 的 `2026-10-20` 是 `6.6798`，而当天真实中间价是 `6.72`。这个
`6.6798` 与即期 `fx_susdcny` 的快照**逐位相同**。

### 根因

`source/forexsource.py` 原本：

```python
DEFAULT_PRIMARY_FOREX = "sina"
SINA_FOREX_CODES = {"USCNY": "fx_susdcny", ...}   # ← 即期代码
```

而 `netvaluehistory` 里存的是**中间价历史**（PHP `CnyReference` 读它：
`SetForex('USCNY')` → `GetClose($date)`）。两源差 0.6%：

| 符号 | 新浪（即期） | 货币网（中间价） |
|---|---|---|
| USCNY | 6.679 | **6.72** |
| HKCNY | 0.85113 | 0.85628 |
| EUCNY | 7.4944 | 7.5159 |

所以"新浪为主"等于**把即期价写进中间价历史**，参考 EST / 实时 EST
系统性偏低约 0.6%。

### 修法

- `DEFAULT_PRIMARY_FOREX = "chinamoney"`（唯一权威中间价源，每天 09:15）；
- `CrossCheckForexSource` 的 `secondary` 默认改成 `"sina"`（只做对照）；
- 描述文案两处跟着改；
- 用 `forexcli` 重写一遍，`USCNY 10-20` 恢复成 `6.72`，即期快照仍留 `6.679`。

---

## 三、参考/实时 EST 该用哪个汇率：ETF 即期、LOF 中间价

### PHP 的规则

`php/stock/fundref.php`：

```php
function GetForexRef()
{
    return $this->IsEtfA() ? $this->forex_ref : $this->cny_ref;
}
```

原注释的理由：**ETF 可以按盘中汇率实时申赎，LOF 只能按当天中间价。**

而 `GetQdiiValue($strEst, $strCNY = false)` 在 `$strCNY` 缺省时正是取
`GetForexRef()->GetPrice()`（`qdiiref.php:203-207`）。

**官方 EST 不走这条** —— 它用 `strOfficialCNY = $cny_ref->GetClose($strDate)`，
恒用中间价。

### 症状

修完第二项之后，偏差呈**完美规律**：

| 类型 | 符号 | 我们参考 EST | 生产端 | 偏差 |
|---|---|---|---|---|
| LOF | SZ161125 | 3.1323 | 3.132 | 0.01% ✓ |
| LOF | SZ161130 | 4.4192 | 4.418 | 0.03% ✓ |
| **ETF** | SH513100 | 1.9778 | 1.965 | **0.65%** ✗ |
| **ETF** | SH513500 | 2.4455 | 2.43 | **0.64%** ✗ |

ETF 全高 0.6% —— 正好是用了中间价（6.72）而不是即期（6.679）。

### 修法

`_resolve()` 里新增 `is_etf` 与 `cny_fair`：

- `_is_etf_a(symbol)` → `core.stocksymbol.StockSymbol(symbol).is_etf_a()`；
- `_fair_cny(symbol, cny_current)` → ETF 取 `_spot_cny()`（`fx_susdcny`
  快照），LOF 取 `cny_current`；即期取不到退回中间价（PHP 兜底同向）。

`compute_fair` / `compute_realtime` 用 `cny_fair`；`compute_official`
**保持** `cny_mid`（中间价）—— 这一点不能混。

修完 ETF 也对了：`SH513100 1.9657 / 生产端 1.965` ✓。

---

## 四、基金层校准值把**场内价**当分母

### 症状

`SZ161125` 的 `calibrationhistory` 里 `2026-10-16 = 15606.776298`，
而生产端是 `16498.992`。差 **5.4%**。

### 根因

`refobj.adjust_factor()` 原本：

```python
self.factor = qdii_get_calibration(float(est), float(cny), float(self.get_price()))
```

PHP 的 `$this->GetPrice()` 对**基金引用对象**返回的是**单位净值**
（新浪 `f_` 行情给的就是净值）；本版 `get_price()` 拿到的是 LOF 的
**场内成交价**。

验算（`^GSPC 10-16 = 7659.44`、`USCNY 10-16 = 6.722`）：

```text
÷ 场内价 3.299   = 15606.776502   ← 库里那个值
÷ 净值   3.1206  = 16498.992399   ← 生产端 16498.992（偏差 0.0000%）
```

### 它不影响页面估值

`fundestcalc._qdii_value()` 里：

```text
contribution = nv × cal_f / cny
hedge_value  = contribution × (est/est_cal) × (cny/cny_cal)
calc_with_calibration 再除 cal_f     ← 与上面乘的 cal_f 约掉
```

所以错的是「校准记录」「对冲值」两处展示，以及任何直接读 `cal_f` 的地方。

### 修法

`adjust_factor()` 改用 `_anchor_nav()`：分母取**单位净值**，锚点日期跟着
净值走（`est` 与 `cny` 都取净值那天 —— 即 `_plan_a_recalibrate.py` 说的
"同锚一日"）。没有净值时**返回 None**，不拿场内价顶替。

---

## 五、验证

### 官方 EST（对照生产端 `qdiicn.php` 2026-10-20 快照）

| 符号 | 我们 | 生产端 | 偏差 |
|---|---|---|---|
| SZ161130 | 4.4197 | 4.418 | 0.04% |
| SZ161125 | 3.1326 | 3.132 | 0.02% |
| SH513100 | 1.9780 | 1.977 | 0.05% |
| SZ162411 | 1.0326 | 1.033 | 0.04% |
| SH513500 | 2.4458 | 2.445 | 0.03% |
| SZ161127 | 1.9416 | 1.942 | 0.02% |
| SZ164906 | 0.8679 | 0.873 | 0.58%（已知 KWEB 数据源差异） |

### 参考 / 实时 EST

修完第三项之后全部 ≤0.12%（`SZ164906` 仍是数据源差异）。

### 校准值

新增 `dbtools/verify_calibration_anchor.py`：逐条按 `est × cny ÷ 净值` 复核，
默认只报告、`--apply` 才写。实测 **85 条里 1 条写歪**（SZ161125 10-16），
修完复核 0 条。

### 测试

| 套件 | 数量 |
|---|---|
| `auto` | **1267** |
| `web` | **750** |

新增/改写的用例：

| 测试 | 钉住什么 |
|---|---|
| `MiddleDateOrderTest`（5 例） | 快照日期压过净值日期；无快照时行情优先；净值只做兜底；`symbol` 可省 |
| `FairCnyTest`（4 例） | LOF 用中间价、ETF 用即期、即期缺失回退、LOF 不读即期 |
| `test_default_is_crosscheck_with_chinamoney_primary` | 汇率主源必须是货币网（旧断言写的是**错的**那个） |
| `test_recalculates_when_net_value_is_new` | 分母用净值而非场内价（旧断言也用错了，已改） |
| `test_returns_none_without_any_net_value` | 没有净值时不拿场内价顶替 |

---

## 六、⚠️ 重启才会生效

`127.0.0.1:5000` 上跑的是用户自己的 `python -B web/app.py`，
**Flask 没有自动重载**。本次改动必须重启那个进程才能看到。

---

## 七、遗留

1. **`^GSPC` 的 `stockhistory` 缺 `2026-10-15` 与 `2026-10-19`**
   （只有快照有 10-19）。均线表与「历史价格」会少这两天；
   `_middle_day_quote` 有快照兜底，所以 EST 不受影响。
2. **`_fair_cny` 的中间价固定取 `USCNY`** —— `_resolve()` 里本来就是这样
   （HK/JP/EU 地区的基金也走 USCNY），这是既有问题，本次没扩大范围。
3. **详情页还缺生产端的几段**：校准记录、基金指数对照表、基金溢价记录、
   净值价格比较。
