# 穷举持仓页迁移记录（`exhaustiveholdingscn.php`）

> 对应 PHP：`woody/res/exhaustiveholdingscn.php`（375 行）
> 本地路由：`/holdings/exhaustive/<symbol>`
> 生产端参照：`https://palmmicro.com/woody/res/exhaustiveholdingscn.php?symbol=SZ164701`
> 完成日期：2026-10-26

## 一、这个页面做什么

一只持仓型基金（QDII / LOF 混基）只持有 2–4 只成分股时，可以用"连续几天
净值涨跌 + 那几天各成分股的涨跌 + 汇率变化"组方程，**反推**成分股的实际比例
与基金仓位：

```
已知：净值涨跌 f、汇率变化 n、各成分股涨跌 a/b/c…
未知：比例 x/y/z 与仓位 w（其实是 1/w 的系数）
每天一个方程：a·x + b·y + c·z − (f/n)·w = 1/n − c
```

方程数 > 未知数 → 最小二乘（`SolveOverdetermined`）；
方程数 == 未知数 → 克莱姆法则（`CramersRule`）。
两个解法落在 `python/auto/calc/linearsolve.py`。

## 二、页面结构（PHP `EchoAll()` 的顺序）

| # | 表 id | 标题 | 行从哪来 |
|---|---|---|---|
| 0 | — | 阈值输入框 | `EchoEditInputForm`，`?input=`，默认 `HOLDINGS_NETVALUE_DIFF`=1.0 |
| 1 | `current` | 当前使用的持仓数据 | 每个持仓一个比例 + 仓位，**整行黄底** |
| 2 | `quarter` | 季报持仓 | `quarterreport` 每条一行 |
| 3 | `exhaustiveholdings` | 穷举持仓比例 | 每个净值反转日一行，累积方程解比例 |
| 4 | `verify` | 验证最激进数据 | 同上，但用克莱姆法则，解出第一行就停 |

表 3/4 **只在持仓数 ≤ 4 时出现**（`if ($iCount <= 4)`）。持仓数 > 4 时页面
显式提示原因，而不是静默少两块。

## 三、分层

```
core/exhaustiveholdings.py                 行级纯逻辑（数字格式、方程装配、两套解法的下标差）
calc/linearsolve.py                        CramersRule / determinant / GaussianElimination / SolveOverdetermined
web/services/exhaustive_holdings_service.py 四张表的装配与列定义
web/routes/holdings.py                     /holdings/exhaustive/<symbol> + 阈值三态
web/templates/exhaustive_holdings.html     渲染
```

PHP 用两个 `static $arEq` 跨行累积，本版收进
`ExhaustiveHoldingsCalculator` 的**实例字段**（两个独立的列表）。理由：
`static` 在 PHP 里跨请求存活（同一进程只是巧合能跑对），实例字段把状态
明确归属给"一次页面渲染"，测试里也不会互相污染。

## 四、照抄不改的地方（都是"看着像 bug 但改了就不一致"）

### 1. 「误差」列的列名与内容错位

PHP 表头是 `array_merge(GetNetValueTableColumn(), [new TableColumnError()], $ar)`
—— 第 4 列叫「误差」，数据行那一格填的却是**估值涨幅**
（`GetPercentageDisplay($fNetValue, _estNetValue($strDate))`）。格数一致，页面
不错位，只是列名不副实。生产端就是这样，照抄。

### 2. 两张表取仓位用的解下标差一位

```php
// _echoExhaustiveHoldingsItem
$ar[] = __getPosDisplay($ref, 1.0 / $arXY[$iIndex - 1], ...);
// _echoVerifyHoldingsItem
$ar[] = __getPosDisplay($ref, 1.0 / $arXY[$iIndex], ...);
```

`$iIndex` 在两处都等于 `$iCount`。差一位是因为验证表追加了"比例合计 = 1"
那一行（`$arLine`），把未知数从 `iCount` 抬到 `iCount+1`，解向量多一个元素。
本版用 `_ratio_cells(..., extra_unknown=True)` 表达这个差别 —— 合成一个函数时
最容易在这里出错：位置取错一位，仓位会从 `1.00` 变成 `0.003` 之类，而且不报错。

### 3. `_echoExhaustiveHoldingsItem` 改 ref 的状态

```php
$ref->SetHoldingsDate($strPrevDate);
$ref->SetNetValueString($strPrevNetValue);
```

这两句把"持仓日"和"净值串"**往前挪一天**，而后续每一行都基于被改过的状态再挪。
看起来像 bug，其实是作者的手动游标 —— 改成"每行用自己的局部参数"会让
`_estNetValue()` 里"持仓日那天的价格"取成另一个日子，比例全错。照抄。

### 4. 验证表只解一次

`if (count($arEq) == $iCount)` 是**严格等于**，而且解之前又追加了一行 → 之后
再也不可能等于。所以验证表只有第一行带比例，且 `_echoVerifyHoldingsData` 见到
`$bReturn` 就 `break`。本版 `verify_row()` 返回 `done`，调用方 `break`。

### 5. 穷举表每行都重解

`if (count($arEq) >= $iCount)` —— 从第 `iCount` 行起，**每行都拿全部累积方程
重解一次**（不是增量更新）。所以越往后的行比例越稳。

### 6. 数字格式三套并存

| 用处 | PHP | 本版 |
|---|---|---|
| 「当前持仓」比例/仓位 | `GetNumberDisplay`（= `StockGetPriceDisplay`，**削尾零**） | `number_display` → `format_price_display` |
| 算出来的比例 | `number_format($fVal, 0)`（**保留尾零**） | `holding_ratio_cell` → `php_number_format` |
| 算出来的仓位 | `number_format($fPos, 2)`（**保留尾零**） | `position_cell` → `php_number_format` |

后两处要拿结果跟库里已有值做**字符串相等**判定来决定整行是否标黄，所以尾零
不能削；第一处是纯显示。混用会让列宽与匹配判定同时出错。

**这次顺带修了一个真 bug**：`_trim_number_text`（`sql/historytable.py`）原来在
`precision=0` 时直接用 `"%.0f" % x`，走 Python 的银行家舍入 —— `-2.5` 得 `-2`，
而 PHP 的 `number_format(-2.5, 0)` 是 `-3`。穷举页的整数比例正好踩这个边界。
现在改成先 `php_round` 再格式化。

## 五、与 PHP 的差异（三处，都是取舍）

1. **没有账号层**。PHP 整页在 `$acct->EchoStockGroup()` 后面（登录 + 分组可见性），
   阈值表单也依赖登录态。本版按 admin 分组页的既定做法收敛：路由直接取 URL 里的
   符号，**没有任何鉴权** —— 用户的要求就是"不用登录客户界面，直接跳转"。
   `SubmittHoldings` 那一侧的管理员校验仍未补（见
   `README_holdings_updater_migration.md`），所以写入链路不能照搬。
2. **管理员"确认更新持仓"链接不生成**。PHP 在"仓位落在 0.25–1.25 但比例不匹配"
   时给一个 `submitholdings.php` 的 `onclick` 确认链接，把人算出来的比例写回库。
   `core.exhaustiveholdings.position_cell` 留了 `update_href` 注入口，但路由恒传
   `admin=False` —— 等权限补上再开。
3. **穷举表的标题不用 `GetFundLinks()`**。PHP 那是"历史价 / 净值 / 净价 / 份额 /
   校准 / 仓位 / 申购 / 悖论"一串站内链接，本地多数页面还没有路由，摆一串死链
   不如只写表名「穷举持仓比例」。

另外两处**结构性**差异：季报表为空时整段不出现（PHP 会摆一张只有表头的空表，
与本项目"宁可少一块也不摆空表"的既定取舍一致）；错误时返回 200 + 提示而不是
500（取不到持仓 / 方程解不出都只影响那几行，不该整页挂）。

## 六、数据前提（本页最容易被误判成 bug 的地方）

### 6.1 「某行只剩三格」有四种早退，只有后两种才是问题

穷举 / 验证两张表的行计算里有四道连续闸门，命中任何一道都只输出
「日期 / 净值 / 涨幅」三格：

| # | 闸门 | 性质 |
|---|---|---|
| 1 | `|净值涨幅| <= 阈值`（默认 1.00%）→ 跳过 | **设计**，生产端一样 |
| 2 | `GetProportionArray()` 为假 → 成分股行情缺天 | 数据缺口 |
| 3 | `GetProportion(cny_id, …)` 为假 → 中间价缺天 | 数据缺口 |
| 4 | `SolveOverdetermined` 抛奇异（`1e-12`）→ 方程退化 | 数据/数学 |

**分辨方法**：看那行的「涨幅」。小于 1% 的就是第 1 种，正常；
大于 1% 却仍只有三格，才去查 2/3/4。实例：SZ164701 的 `2026-10-21`
（1.588 vs 1.583 = +0.32%）**天生只有三格**，不是 bug。

### 6.2 成分股行情必须成对完整

**每个成分股在配对的两个交易日都必须有 `adjclose`**，缺一天
`GetProportionArray()` 就整行返回 `None`。

2026-10-26 首次冒烟时踩到：演示库 `stockhistory` 里 `GLD`（SZ164701 的唯一
成分股）**只有 10-22 / 10-23 两天**，页面每行都只有三格。
用 `auto/_scratch/_seed_gld_history.py --apply` 补了 60 个交易日
（新浪美股日线，与 `seed_index_history.py` 同源），之后立刻有解：
`2026-10-22 | 1.546 | -2.64% | 0 | 100 | 1.01`。

同族的另外两个前提：

* **中间价**（`netvaluehistory` 里的 USCNY 等）要有配对两天的值 ——
  `GetProportion()` 取不到就 `1/false`，那行跳过（见 pitfalls #120）；
* **`quarterreport` 表**：演示库有表无数据，所以季报段不出现。那一支的写入口
  （PHP `editquarterreport`）未迁，见下节。

### 6.3 踩坑记录

## 七、验证

```bash
# 纯逻辑
cd python/auto && python -B -m unittest test_linearsolve test_exhaustiveholdings

# PHP 逐例对照（行列式 / 克莱姆 / 超定，8 个用例 × 3 个函数）
cd python/auto/_scratch && python -B _php_linearsolve_compare.py

# web 层（路由三态阈值 + 说明文字链接）
cd python/web && python -B -m unittest tests.test_exhaustive_holdings
```

真库冒烟（演示库）：

| URL | 期望 |
|---|---|
| `/holdings/exhaustive/SZ164701` | 三张表（current / 穷举 / 验证），穷举行有 6 格 |
| `/holdings/exhaustive/SZ164701?input=` | 0 张表 + "阈值留空"提示 |
| `/holdings/exhaustive/SZ164701?input=0` | 每个有涨跌的日子都进方程 |
| `/holdings/exhaustive/SH513050` | 1 张表（current）+ "超过 4 只"提示 |
| `/holdings/exhaustive/NOPE999` | 200 + "取不到该基金的持仓数据" |
| `/fund/qdiilof/SZ164701` | 说明行里 `穷举持仓比例` 是链接，指向 `/holdings/exhaustive/SZ164701` |

## 八、未做 / 后续

1. **`quarterreport` 的写入口**（PHP `editquarterreport` 页面 +
   `QuarterReportSql.WriteDaily`）未迁。`sql/historytable.py::QuarterReportSql`
   目前只读。
2. **管理员写入链接**（`submitholdings.php` 的"确认更新持仓"）未接，
   依赖 `process_admin_holdings` 补权限检查。
3. **`GetNetValueTableColumn($est_ref, $cny_ref)` 的完整形态**只迁了前三列
   （日期/净值/涨幅）。带 `est_ref`/`cny_ref` 的 5–6 列形态本页用不到，
   留给需要它的段落。
4. 页尾 `$acct->EchoLinks()` 那一串账号导航未迁（账号体系整体暂缓）。
