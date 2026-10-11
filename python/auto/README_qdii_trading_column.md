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

## 六、演示库的数值为什么看着不对

`SZ161125` 那一列在演示库里是 `8237.57|945542.91`。中间的 SPY 格是
`(8237.57 / 1.0) / 0.008712` —— 演示库 `SPY` 的 `calibrationhistory` 是
`0.008712`，而生产端按 `QdiiGetCalibration(^GSPC, 1.0, SPY价)` 该在 **10** 上下
（生产端 `8157.14 → 812.89` 反推正好 10.03）。

这是**种子数据**的问题，不是实现的问题 —— 换算路径已被 PHP 对照逐验证过。
需要把演示库的中间层校准值算对，走 `autocalibrationcli.py`（不是页面渲染时）。

## 七、测试

* `web/tests/test_qdii_trading_column.py`（30 个，离线、两版本解释器都跑）——
  一个 callable 干两件事、表头形状、`'0'` 陷阱、换算顺序、伪 pair 校准装载；
* `web/tests/test_trading_route.py` 新增 4 个（需要 flask）——
  表头与格子真的到页面上、列宽 200、**列数与格数对齐**、
  没有中间层标的一列就不补。

## 八、仍未完成

* **`FundPairReference` 整体迁移**后，`build_leverage_references()` 可以直接删掉，
  改成 `new FundPairReference($symbol)`；调用方（`_build_trading_column`）不用动；
* PHP 的 `_callbackFundListHedge()`（`_qdiigroup.php:41`）还没迁 —— 那是
  `EchoFundListParagraph()` 那一列的回调，与本模块同层级但不同一张表。
