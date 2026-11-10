# 穷举持仓页迁移记录（`exhaustiveholdingscn.php`）

> 对应 PHP：`woody/res/exhaustiveholdingscn.php`（375 行）+
> `editquarterreportcn.php`（写入口）+ `submitholdings.php`（管理员写回）+
> `_stockaccount.php::EchoLinks()`（页脚）
> 本地路由：`/holdings/exhaustive/<symbol>`、`/holdings/quarter`（2026-11-09
> 新增的季报索引页，PHP 没有）、`/holdings/quarter/<symbol>`、`POST /holdings/submit`
> 生产端参照：`https://palmmicro.com/woody/res/exhaustiveholdingscn.php?symbol=SZ164701`
> 完成日期：2026-10-26（写入口 / 权限闸门 / 页脚同日增补）

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
core/exhaustiveholdings.py                 行级纯逻辑（数字格式、方程装配、两套解法的下标差、
                                           写回载荷编码 UpdateCell / encode_holdings_payload）
calc/linearsolve.py                        CramersRule / determinant / GaussianElimination / SolveOverdetermined
sql/historytable.py                        DailyStringSql（季报的比较语义）+ QuarterReportSql
updater/holdingsupdater.py                 process_admin_holdings（带 admin 闸门的写回）
web/services/exhaustive_holdings_service.py 四张表的装配与列定义
web/services/admin_auth.py                 谁是管理员（IsAdmin 的单点）
web/services/site_links.py                 页脚 EchoLinks 的数据表
web/routes/holdings.py                     /holdings/exhaustive/<symbol>、/holdings/quarter（索引）、
                                           /holdings/quarter/<symbol>、POST /holdings/submit
web/templates/exhaustive_holdings.html     渲染
web/templates/quarter_report.html          季报写入口
web/templates/quarter_report_index.html    季报索引页（32 只基金代码，本版新增）
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

1. **读页面没有账号层，但写入口有管理员闸门**。PHP 整页在
   `$acct->EchoStockGroup()` 后面（登录 + 分组可见性），阈值表单也依赖登录态。
   本版按 admin 分组页的既定做法收敛：**穷举页本身**（`/holdings/exhaustive/<symbol>`）
   直接取 URL 里的符号，不做鉴权 —— 用户的要求就是"不用登录客户界面，直接跳转"。
   但只要**动库**（写回持仓 / 写季报），就必须过 `web/services/admin_auth.is_admin()`，
   见 §八。
2. **管理员"确认更新持仓"链接已接上**（2026-10-26）。PHP 在"仓位落在 0.25–1.25
   但比例不匹配"时给一个 `submitholdings.php` 的 `onclick` 确认链接，把人算出来的
   比例写回库。本版经 `position_cell(update_href=...)` 注入口生成一个
   `<form method="post" action="/holdings/submit">`（详见 §八 的"为什么是表单不是链接"）。
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
* **`quarterreport` 表**：演示库有表无数据，所以季报段默认不出现；写入口见 §八。

### 6.3 踩坑记录

## 七、验证

```bash
# 纯逻辑
cd python/auto && python -B -m unittest test_linearsolve test_exhaustiveholdings

# PHP 逐例对照（行列式 / 克莱姆 / 超定，8 个用例 × 3 个函数）
cd python/auto/_scratch && python -B _php_linearsolve_compare.py

# web 层（路由三态阈值 + 说明文字链接 + 管理员表单 + 权限闸门）
cd python/web && python -B -m unittest tests.test_exhaustive_holdings
cd python/web && python -B -m unittest tests.test_quarter_report
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
| `/holdings/quarter` | 200 + 32 只持仓型 QDII 的基金代码（如 `161815`），点代码进编辑页；从 `/admin` 的「修改季报持仓」也能进 |
| `/holdings/quarter/SZ164701` | 200 + 日期/值两个框（值回填样例串或库里最新一条） |
| `/holdings/quarter/NOPE999` | 200 + "没有符号"，**不摆表单**（POST 那支会 400） |

季报 / 写回的往返冒烟走脚本（会真写库，但退出前一定复原）：

```bash
cd python && PYTHONPATH=/f/woody/woodyweb/python/auto:/f/woody/woodyweb/python \
    D:/python/python.exe -B auto/_scratch/_quarter_roundtrip.py
```

```
起始行数 = 0
写入后行数 = 1 ; flash = True
穷举页出现 GLD 列 = True
穷举页出现「编辑」链接 = True
穷举页出现管理员按钮 = True
确认问句 = ... onsubmit="return confirm('确认更新持仓GLD:50.00,SPY:50.00和仓位1.00？')"
删除后行数 = 0 ; flash = True
复原后行数 = 0 (期望 0) -> True
```

> 脚本第一版 `flash = False`，原因是 `urllib` **默认不带 cookie jar** ——
> flash 走 session cookie，302 之后的 GET 拿不到刚 flash 的那条消息。
> 这是客户端假象，不是服务端 bug；换成 `HTTPCookieProcessor` 就 `True` 了。
> 以后验 flash 一定要带 cookie，否则会误判成"没写进去"。

非管理员闸门（换身份重启）：

```bash
cd python && PALMMICRO_ADMIN_EMAIL=guest@example.com \
    PYTHONPATH=/f/woody/woodyweb/python/auto:/f/woody/woodyweb/python \
    D:/python/python.exe -B -m web.app
```

| 请求 | 期望 |
|---|---|
| `POST /holdings/quarter/SZ164701` | **403**，且 `quarterreport` 行数不变 |
| `POST /holdings/submit` | **403**，且 `holdings`/`fundposition` 三表不变 |
| `GET /holdings/exhaustive/SZ164701` | 200，`name="holdings"` 与管理员说明**都不出现** |
| `GET /holdings/quarter/SZ164701` | 200，页面提示只有管理员能提交（含 `PALMMICRO_ADMIN_EMAIL` 用法） |

## 八、季报写入口与"确认更新持仓"（`editquarterreport` + `submitholdings.php`）

两条写路径共用同一套闸门与页面骨架，一起说。

### 8.1 身份：`web/services/admin_auth.py`

`IsAdmin()` 在 PHP 里就是 `GetLoginEmail() == ADMIN_EMAIL`
（`php/email.php:2`，`ADMIN_EMAIL = 'woody@palmmicro.com'`）。本版照抄成一个
**单点**模块，把"谁是管理员"收在一处，省得 `money_service` 与路由各写一份字符串
（`money_service.ADMIN_EMAIL` 现在是从这里再导出的）：

| 函数 | 语义 |
|---|---|
| `current_admin_email()` | session `email` → 环境变量 `PALMMICRO_ADMIN_EMAIL` → 演示默认值 |
| `is_admin()` | `current_admin_email() == ADMIN_EMAIL` |
| `denied_message()` | 403 页面的统一文案 |

**演示默认身份就取 `ADMIN_EMAIL` 本身**。所以"不用登录也能看到管理员控件"不是
绕过了判断，而是默认身份恰好是管理员；换成 `PALMMICRO_ADMIN_EMAIL=guest@…`
立刻掉到 `False`。这个恒等式比"给个 demo@example.com 再用 if 放行"更干净 ——
没有第二条通路。

### 8.2 季报写入口（`/holdings/quarter/<symbol>`，GET + POST）

PHP 侧是 `editquarterreportcn.php` + `QuarterReportSql`。三处必须照抄：

1. **值的形状**是 `"符号":百分比` 的编码串，例如 `"GLD":100`；
   解析器把比例归一化成合计 100（同一份 `fRatio/fPos` 逻辑）。
2. **空串 = 删除，填 0 也是删除**。PHP 是
   `if (empty($strVal)) DeleteByDate(...) else WriteDaily(...)`，而
   `empty('0')` 为 **真** —— 所以 `"GLD":0` 走删除分支。`_php_empty()` 复刻了
   PHP 的 `empty()`（`''`/`'0'`/`0`/`None`/空容器都为真）。
3. **日期是参数**（与管理员写回相反：那边日期取库里现有的持仓日）。季报日期
   可以是季末任意一天，所以没有周末闸门。

**库里没有这个符号时连表单都不摆**：GET 只给一行提示（与穷举页
`NOPE999 → 200 + 提示` 的取舍一致），POST 那支返回 400 并说明
"stock 表里没有符号"。PHP 那边 `SqlGetStockId` 取不到会落到 `stock_id = 0`
写脏行 —— 本版拒绝。

数据层为此新增 `sql/historytable.py::DailyStringSql`（`DailyCloseSql` 的子类），
与父类只有两处不同：

| | `DailyCloseSql` | `DailyStringSql` |
|---|---|---|
| 值比较 | 按数值比 | `php_text_equal()`：**两个都是数字串时按数值比**（`'100' == '100.00'`），否则按字节比 |
| 周末闸门 | 有（INSERT 分支） | **没有**（季报日期可以是季末任意一天） |

`write_daily()` 是"值没变就返回 `False`"，靠它把 `num` 计数与重复提交分开。

### 8.3 "确认更新持仓"：为什么是表单而不是链接

PHP 用 `GetOnClickLink()` 拼 `submitholdings.php?...&holdings=...`，即 **GET 里
带写参数**。本版改成 `POST /holdings/submit` 的 inline 表单：

* GET + 写副作用会被浏览器预取、被代理重放、被 `Referer` 泄漏整份持仓；
* 确认问句照抄 `GetOnClickLink()` 的口径，但要**去掉问句里的双引号**
  （`str_replace('"', '', ...)`）—— 问句里一旦有 `"`，`onsubmit="…"` 属性就被
  截断。`confirm_update_question()` 里已经做掉。

载荷由 `core/exhaustiveholdings.py::encode_holdings_payload()` 生成，与 PHP 逐字
一致：`DebugEncode(array_combine(符号, number_format($fRatio * $fPos, 2)))`，
即 `"GLD":"50.25"` —— **键换成符号、值 = 比例×仓位、值带两位尾零的字符串**。

### 8.4 闸门放在函数里，不是调用方

`process_admin_holdings(source, symbol, params, holdings_ref_builder=None, *, admin)`
把 `admin` 做成**必填关键字参数**：

* PHP 的闸门是 `if ($this->IsAdmin()) $this->AdminProcess();` —— 在
  `AdminProcess` **外面**。只迁里面那半截，等于留了个未鉴权写入口。
* 给 `admin` 默认值就等于没补洞，所以**不给默认值**：少写一个参数是
  `TypeError`，不是静默放行。
* `admin=False` 时**直接返回 `{"denied": True, ...}`，一个字节都不碰数据源**
  —— 测试里用一个"一碰 connection 就抛"的替身守住这条。
* CLI 侧 `holdingscli` 传 `admin=True`：命令行从来不对外暴露。

### 8.5 页脚：`EchoLinks()`

`$acct->EchoLinks()`（`woody/res/php/_stockaccount.php:126`）是几乎每页末尾都调的
"相关链接"块。数据表落在 `web/services/site_links.py`：

| 段 | 内容 | 本地处理 |
|---|---|---|
| 工具页 5 条 | LOF基金 / 义工群对比 / 义工群对冲计算器 / A股和H股对比 / 拖拉机自动化 | 全部指向生产端（本地没迁） |
| 账号工具 + 开发记录 | `account/simpletest.php`、`blog/entertainment/…` | 同上 |
| 股票菜单 7 条 | A股指数 / A股商品 / 美股QDII / 混合QDII / 港股QDII / 日本QDII / 欧洲QDII | 指向本地 `/fund/<group>` 等 |
| 行业分类 10 条 | 生物科技 … 标普500 | 指向本地 `/fund/<group>` |
| 个人 / 管理员 | `GetMyPortfolioLink()` + 分组 + debug 链接 | 账号体系暂缓，只保留已有的站内入口 |
| 按符号的类别链接 | `GetStockCategoryLinks()` / `GetCategoryLinks()` | 有 `symbol` 就只出该符号的类别 |

实现要点：`app.py` 里一个 `context_processor` 从 `request.view_args` 取
`symbol`/`group` 注入，模板 `base.html` 两段循环渲染；外链统一带
`target="_blank" rel="noopener noreferrer"` 与 `&#8599;` 标记。
`safe_footer_sections()` 兜住异常返回 `[]` —— **页脚拼死不该让整页 500**。

## 九、未做 / 后续

1. **`GetNetValueTableColumn($est_ref, $cny_ref)` 的完整形态**只迁了前三列
   （日期/净值/涨幅）。带 `est_ref`/`cny_ref` 的 5–6 列形态本页用不到，
   留给需要它的段落。
2. **账号体系**（`stockgroup` 三张表 + 登录）整体暂缓。所以页脚的"个人"段只摆
   已有的站内入口；`admin_auth` 现在是"session 里有 email 就用，没有就用演示身份"，
   接上真正的登录后只需改 `current_admin_email()` 一处。
3. **`_getStockExchangeLinks()`**（历史价 / 净值 / 份额 / 申购 / 悖论那一串）未迁
   —— 多数页面还没有路由，摆死链不如不摆。

