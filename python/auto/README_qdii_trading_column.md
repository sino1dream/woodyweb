# 五档交易表补最后一列「换算价」（`web/services/qdii_trading_column.py`）

对应 `woody/res/php/_qdiigroup.php:5` 的 `_tradingUserDefined()`，
由同文件 `:96` 的 `EchoFundTradingParagraph($ref, '_tradingUserDefined')` 挂上。

生产端 `sz161125cn.php` 实测：

```html
<th width=198><b><font color=maroon>^GSPC</font></b>|<b><font color=maroon>SH</font></b>|<b><font color=maroon>SPY</font></b><b><font color=blue>价格</font></b></th>
<td><font color=red>8996.19</font>|<font color=green>26.98</font>|<font color=red>896.51</font></td>
```

本版表头与格子输出纯文本：`^GSPC|SH|SPY价格` / `8996.19|26.98|896.51`。

---

## 一、结论先摆在前面

| 问题 | 结论 |
|---|---|
| 回调列的机制要不要新写 | **不用**。`TradingService.fund_table(fund, callback)` 早就支持；缺的是**调用方没传**和 `_tradingUserDefined` 本身 |
| `MyPairReference`（未迁移）怎么办 | 只补 `EstFromPair` 真正读的那三样：行情 / `fFactor` / `fLastCalibrationVal` |
| 列宽要不要照 `strlen()*11` | **不要**（沿用既有裁决），固定 200 |
| 数值会不会错得安静 | 会。所以做了 PHP 逐项对照，见 §五 |

## 二、这一列在说什么

左边三列是"我这基金现在多少钱"，这一列是**同一档价格换算成基金经理看的
那些标的**。三份换算串起来：

1. `$fund->GetEstValue($strVal)` —— 基金净值口径 → 中间层点位（`GetQdiiValue` 的逆）；
2. `$leverage_ref->EstFromPair($fEst)` —— 中间层点位 → 杠杆 ETF 报价；
3. `$ref->GetPriceDisplay($fVal)` —— 各引用按自己的精度格式化。

三段都已迁移过（分别在 `refobj.QdiiReference`、`pairestimate`、`stockref`），
本模块**只负责串**，不复制任何公式。

## 三、PHP 里那句 `$strVal == '0'` 是死代码

```php
if ($strVal)
{
    if ($strVal == '0')  { return ''; }     // ← 永远进不来
    ...
}
```

PHP 的字符串 `'0'` 是**假值**，`if ($strVal)` 已经把它挡在外面。
所以本版不抄这一行 —— 抄了要额外注释"这行永不执行"。
两侧行为一致还有第二重保障：上游 `_make_row` 的 `if (empty($strPrice) == false)`
同样会把 `'0'` 滤掉。

对照脚本把这点做成了一次真实 PHP 确认：

```text
if ('0')      -> False     ← 所以 `'0'` 走的是"表头"分支
if ('0.0')    -> True      ← `'0.0'` 是真的价格
empty('0')    -> True      ← 上游那一层已经滤掉了
```

翻译成 Python 时写成 `if price:` 就会翻车：价格恰好是 `'0'` 时会返回一个表头文本。

## 四、已知偏差

| 偏差 | 理由 |
|---|---|
| 表头/格子里的**分色**没照搬 | PHP 在同一格里塞了 maroon 的标的名与 blue 的"价格"，每个数字还各自红/绿；本项目的列/格只有「一个文本 + 一个颜色」。与 `fund_table` 里"官方/参考/实时"三档估值那格同一做法（也是 `\|` 拼的纯文本） |
| 列宽不用 `strlen()*11` | CSS 才是宽度决策者；PHP 按字节算，混排中文时偏窄三重。生产端那一列算出来是 198，本版固定 200 |
| 不做 PHP 的**页面渲染期写库** | PHP `QdiiCreateGroup` 会对每个杠杆标的调 `DailyCalibration()`（渲染时改库）。本项目的部署形态是"daemon 抓、页面读"，页面不落库；要补算走 `autocalibrationcli.py` |
| `CnyReference` 地区对照表没迁 | QDII 场景是"美股 ETF 跟踪美股指数"，PHP 在这条分支上**不建** `cny_ref`、`GetDefaultCny()` 得 1.0，本版同样不给。跨境配对（A 股基金跟踪港股标的）时需要补 |

## 五、PHP 逐项对照

```
php -n -d display_errors=stderr auto/_php_qdiicolumn_check.php > ref.json
python -B auto/_php_qdiicolumn_compare.py --reference ref.json
python -B auto/_php_qdiicolumn_compare.py --selftest
```

`_php_qdiicolumn_check.php` **直接从 `_qdiigroup.php` 里抠出**
`_tradingUserDefined` 的函数体，替身对象只提供纯数据
（同 `_php_pairest_check.php` 的手段）。5 例矩阵覆盖：

* 生产端 `SZ161125` 那一组量级；
* 没有杠杆标的（`fundpair` 查不到是常态）；
* 两个杠杆标的、精度不同（确认顺序与各引用自己的格式化）；
* A 股一侧（`EstFromPair` 走 `QdiiGetVal` 那条倒数方向）；
* 仓位不是 1（`FundAdjustPosition` 的混合项要参与）。

**结果：0 项不一致；`--selftest` 4/4 抓到注入错误**（EST 方向、校准值、
精度、`GetEstValue` 的逆被换成正公式）。

### 顺手修掉的一个 harness bug

替身 `ForexStub` 最初只有 `GetPrice()`，而 PHP 的 `GetDefaultCny()` 用
`method_exists($ref, 'GetVal')` 探测 —— 探测失败**静默**退化成 1.0，
导致汇率根本没参与换算。这类"不会报错、只差一个数量级"的偏差，
正是逐项对照存在的理由。

## 六、演示库里那一格是百万级：为什么改不得

`SZ161125` 的 `卖5` 行是 `8237.57|945542.91`。中间那格是纯粹的除法：

```text
(8237.57 / 1.0) / 0.008712 = 945542.9      ← est_from_pair 的非 A 股分支
```

`0.008712` 是演示库 `SPY` 的 `calibrationhistory`。**它不是坏数据**，改了才坏。

### 6.1 一度以为是垃圾值（结论错了，这里留档）

我第一版的判断是"生产端按 `QdiiGetCalibration` 该落在 10 上下，演示库存的是
`0.008712`，所以是种子数据坏了，该重算"。这个判断**是错的**，而且是危险的那种：

* 只看了一个量的量级，没看它所在的**成对约束**；
* 没先翻 `README_autocalibration_migration.md` —— 那份文档第 3 节记录的
  正是这次要犯的错：**有人把 `QQQ` 写成生产的 `41.488229`，基金估值立刻从
  `4.59` 变成 `20245.2`**，页面不报错，只显示荒谬数字。

演示库把中间层净值归一化成 1.0（`dbtools/seed_demo_data.MARKET_LAYERS`，刻意设计），
于是按 README 第 3.3 节，`est_m × cal_m` 这个**乘积必须守恒**：
演示库的 `est_m` 是中间层实时价（QQQ ≈ 757），配的 `cal_m` 只能是 `0.008945`。
这跟配对换算要的"点位 ÷ ETF 价 ≈ 41.2"是**两个数**，在归一化表示下不可能同时成立。

### 6.2 受控实验：改对了这一格，估值就炸

备份整库（`dbtools/_snapshots/calib_before_20261012.sql`）之后，临时把中间层改写成
生产量级（`QQQ = 30883.15 / 749.41308 = 41.21`、`SPY = 7811.54 / 769.46 = 10.15`）
抓页面，然后立即还原：

| | 改之前 | 改成生产量级之后 |
| --- | --- | --- |
| SZ161130 官方 EST | `4.556`（净值 4.5452 ✓） | `19942.349`（溢价 −99.98%） |
| SZ161125 官方 EST | `3.168`（净值 3.1328 ✓） | `3509.334`（溢价 −99.91%） |
| 新列 QQQ 格 | `3997403.22` | `867.68` ✅ |
| 新列 SPY 格 | `945542.91` | `890.90` ✅ |

两边的正确性**互斥**：要估值对，这一列就得是百万级；要这一列对，估值就得炸。
选哪个没有悬念 —— 估值影响的是**所有基金页**，这一列只影响一行的一个格子。

### 6.3 所以怎么处理

1. **不动库**。`0.008945` / `0.008712` 保持原样（`sql/fix_middle_calibration.py`
   就是专门把它们改回去的脚本）。
2. `autocalibrationcli.py --force --apply` **帮不上忙**：它要基准日的配对值，
   而 `^NDX` / `^GSPC` 没有净值，实测预演结果是"写了 0 条、保持原值"。
3. 想让演示库两边都对，得**整体换尺**（中间层净值、中间层实时价、
   基金自身的校准值一起重标），不是改一行的事 —— 那是数据侧的独立任务。
4. 这一格在演示库里显示百万级属于**演示数据的表达**，不是代码 bug：
   换算路径本身已由 PHP 逐例对照验证（第五节 5 例 0 差异）。

失败模式属于"A 比 B 重要，两者不可兼得"，记下来是为了下次不再花时间重新怀疑。

## 七、测试

* `web/tests/test_qdii_trading_column.py`（30 个，离线、两版本解释器都跑）——
  一个 callable 干两件事、表头形状、`'0'` 陷阱、换算顺序、伪 pair 校准装载；
* `web/tests/test_trading_route.py` 新增 **5** 个（需要 flask）——
  表头与格子真的到页面上、列宽 200、**列数与格数对齐**、
  没有中间层标的就退回通用表（`without_est_ref` 时列数与格数也不塌）。

## 八、仍未完成

* **`FundPairReference` 整体迁移**后，`build_leverage_references()` 可以直接删掉，
  改成 `new FundPairReference($symbol)`；调用方（`_build_trading_column`）不用动；
* ~~PHP 的 `_callbackFundListHedge()`（`_qdiigroup.php:41`）还没迁~~ ——
  **已迁**（`web/services/qdii_fund_list.py` 的 `build_hedge_callback`，
  末列"对冲值"，带真实 PHP 逐例对照，见 `README_qdii_fund_list.md`）。
