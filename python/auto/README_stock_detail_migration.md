# `_mystock.php` 个股详情大页迁移（第 14 轮起）

对应生产端 `woody/res/php/_mystock.php`（及被它 `require` 的一整条渲染栈）。
这是 woodyweb 从 PHP 迁 Python 的**最大单线**——但侦察后发现：服务层大部分已经迁好，
真正卡住整条非基金详情线的是一个底层配对引用层。本文档记录侦察结论 + 分阶段 checkpoint。

## 1. `_mystock.php` 渲染栈（PHP 侧）

`EchoAll()` 的逻辑：

1. `$acct->EchoStockGroup()` —— **账号体系**（`SymbolAccount`），返回单个引用对象；
2. `EchoReferenceParagraph(array($ref))` —— 参考数据；
3. `_echoMyStockData($ref, $strStockId, $bAdmin)` —— 按符号类型分四支渲染：
   - `IsFundA()` → `EchoFundArrayEstParagraph` + `EchoFundTradingParagraph` + `EchoFundHistoryParagraph`；
   - `StockGetFundPairReference` → `EchoFundArrayEstParagraph` + `EchoFundListParagraph` + `EchoFundPairSmaParagraph` + `EchoFundHistoryParagraph`；
   - `StockGetHoldingsReference` → `EchoHoldingsEstParagraph` + `EchoSmaParagraph` + `EchoFundHistoryParagraph`；
   - **默认分支**（A/H/US/AB/ADR/外汇）：`StockGetPairReferences` 出 `ab/ah/adr` 引用，
     `EchoAbParagraph` / `EchoAhParagraph` / `EchoAdrhParagraph`，
     `EchoTradingParagraph($ref, $ah_ref, $adr_ref)`，`EchoSmaParagraph` / `EchoAhPairSmaParagraph` /
     `EchoFundPairSmaParagraph` 的各类组合，`fx_susdcnh` 特例走 `_callbackCnhSma`；
4. `_echoMyStockData` 末尾（所有分支之后）：`EchoNetValueCloseParagraph` + `EchoFundShareParagraph` + `EchoStockHistoryParagraph`；
5. `_echoMyStockTransactions` —— **账号体系**（`GetGroupSql()`）按分组摆交易明细 + 编辑/合并表单；
6. `_getMyStockLinks` / `GetMyStockLinks` —— 编辑/删除/期权链接 + 基金软件商链接（导航类）。

## 2. 侦察结论：服务层大部分已迁，缺的是配对引用层

| PHP 段落 | Python 状态 |
|---|---|
| reference / fundest / fund history / fundshare / calibration | ✅ 已迁，已由 `/fund/<symbol>` 装配（`reference_service` / `fundest_service` / `FundService` / `history_service` / `calibration_service`） |
| SMA qdii / future / fundpair | ✅ `sma_service.py` 已有 `qdii_table` / `future_table` / `fund_pair_tables` + `callback_ah_pair_sma` |
| AH/AB/ADR 对比表（`ahparagraph.php` 94 行，三函数合一） | ✅ `pair_service.py` 已写完整，但**未接线**（依赖配对引用对象） |
| stockhistory / netvalueclose | ✅ `history_service.py` 已支持两种表（原仅供 `/history/*` 页）—— **CK1 已接进 `/fund/<symbol>`** |
| trading（基金五档） | ✅ `trading_service.fund_table` |
| **A 股 generic `EchoTradingParagraph($ref,$ah_ref,$adr_ref)`** | ❌ 只有 `fund_table`，缺带 AH/ADR 溢价的通用 trading |
| **generic `EchoSmaParagraph($ref)` + `_callbackCnhSma`** | ❌ 缺 |
| **`EchoAhPairSmaParagraph` 接线** | ❌ 回调有、没接服务/路由 |
| **Holdings 分支** | ❌ 被 `HoldingsReference` 阻塞 |
| 交易块 `_echoMyStockTransactions`（账号） | ❌ 可演示化 |
| 链接生成 `_getMyStockLinks` / `GetMyStockLinks` | ❌ 导航类，低优先 |

### 关键石（真正的瓶颈）

`pair_service` / `sma_service.fund_pair_tables` / `qdii_trading_column` 全都依赖一组
**配对引用对象**——`MyPairReference` / `FundPairReference` / `HoldingsReference` 及其工厂
`StockGetFundPairReference` / `StockGetHoldingsReference` / `StockGetPairReferences`，
源码在 **`php/stock/fundpairref.php`（约 278 行）**，`php/stock` 层。

没有这层，AH/AB/ADR 对比、配对 SMA、A 股带配对溢价的 trading、Holdings 分支**全部拿不到
数据**。`pair_service.py` 已通过 `_invoke(ref, snake, camel)` 的 snake/camel 兜底容忍该层缺失
（方法出现前格子留空），所以迁完配对引用层后这些服务能自动"通电"。

> **CK2 已完成**：`MyPairReference` 及其 `Ab` / `Ah` / `Adr` 三子类、四表正/反查、
> `StockGetPairReferences` 工厂已迁入 `auto/ref/refobj.py`。
> 详见 **`README_pair_reference_migration.md`**，本文件只记 CK 层面的排期与验证。
>
> 仍未迁：`FundPairReference` 的期货自动校准（列 **CK2b**）、`HoldingsReference`。

## 3. 分阶段 checkpoint

- **CK1（已完成）**：把已迁的 `stockhistory` + `netvalueclose` 接进 `/fund/<symbol>`
  详情页（仅接线 + 模板，不碰配对引用层）。零风险、立刻可见。
- **CK2（关键石，已完成）**：迁移 `fundpairref.php` 配对引用层到 `refobj.py`
  —— `MyPairReference` + `Ab`/`Ah`/`Adr` 三子类 + 四表正/反查 +
  `StockGetPairReferences`。**不含** `FundPairReference` 的期货自动校准
  （列 CK2b，见下）与 `HoldingsReference`（并入 CK4）。
  详见 `README_pair_reference_migration.md`。
- **CK2b**：`FundPairReference` 的期货自动校准（`AverageCalibration` /
  `DailyCalibration` / `GetOfficial*` / `GetRealtimeNetValue`）。与已迁的
  `fundestcalc` / `futurescalib` 及独立 `autocalibration` 任务高度重叠，单列。
- **CK3（已完成）**：新建 `/stock/<symbol>` 详情页（路由策略已与用户确认：非基金
  详情独立成 `/stock/<symbol>`，`/fund` 仍只管基金），渲染 AB/AH/ADR 对比 +
  均线四个槽位 + stockhistory / netvalueclose / shares。
  详见 §6「CK3 落点」。
- **CK4（Holdings 分支）**：接 `HoldingsReference`（依赖 CK2）。
- **CK5（交易块 + 链接，账号可演示）**：`_echoMyStockTransactions` 复用演示数据 + `_getMyStockLinks` / `GetMyStockLinks`。
- **CK6（长期）**：order book（五档盘口）写入 snapshot 表，彻底解决 tradingparagraph 历史问题。

## 4. 路由策略（已确认）

非基金详情（A/H/ADR/外汇/Holdings）走新建的 `/stock/<symbol>`，与现有 `/stock` 列表页同源；
`/fund/<symbol>` 仍只管基金。URL 语义干净，且分别贴近 PHP 的"基金页"与"个股页"两类生产端页面。

## 6. CK3 落点

### 6.1 文件

| 文件 | 作用 |
|---|---|
| `web/services/detail_sections.py`（新） | 详情页共用的历史类段落构建器，被 `/fund` 与 `/stock` 复用 |
| `web/services/stock_detail_service.py`（新） | `_mystock.php:144-170` else 分支的段落组装 |
| `web/routes/stock.py`（改） | 加 `/stock/<symbol>`；列表页链接按符号类型分流 |
| `web/templates/stock_detail.html`（新） | 详情页模板 |
| `web/tests/test_stock_detail.py`（新） | 47 例（服务层分支 + `_callbackCnhSma` + 路由接线 + A 股五档表） |

**没有新建 Blueprint**：`routes/stock.py` 原本就有 `/stock` 列表页（对应
`php/ui/stockparagraph.php`），详情页直接挂在同一个 `stock_bp` 下，与
`_mystock.php` 里"列表页与详情页本来就是同一张壳"的结构一致。

### 6.2 为什么先抽 `detail_sections.py`

`_mystock.php:167-169` 那三段（netvalueclose / shares / stockhistory）在**四个
分支走完之后**都要执行，原本只写在 `routes/fund.py` 的 `_build_*` 里。CK3 要
复用就会变成复制粘贴，所以先抽出来、`fund.py` 改为委托调用。CK1 的
`test_fund_detail_history.py` 走 `history_service_factory` 注入，重构后仍全绿。

### 6.3 均线的四个槽位（`_mystock.php:152-165`）

PHP 是一串三分支，返回四个互斥的槽位而不是压成一个键，让模板按槽位判断：

| 条件 | `ah_pair` | `fund_pair` | `adr_extra` | `plain` |
|---|---|---|---|---|
| 有 `ah_ref` | ✓ | ✓ | 有 `adr_ref` 时 ✓ | — |
| 只有 `adr_ref` | — | ✓ | — | — |
| 都没有（普通符号） | — | — | — | ✓ |
| 都没有且是 `fx_susdcnh` | — | — | — | ✓（带 `_callbackCnhSma`） |

其中 `EchoFundPairSmaParagraph($adr_ref, '')` 传的是**空串标题**，靠
`sma_table` 里 `memo is False` 的严格判断保留 —— 多张均线表并排时只有第一张
带标题。这条被单测钉住，且变异测试确认断言有牙。

### 6.4 A 股五档交易表（曾记为"刻意不做"，后补上）

``EchoTradingParagraph($ref, $ah_ref, $adr_ref)``（``_mystock.php:150``）CK3 当时
留在缺口里，理由有两条，后来都被现实推翻：

| 当时的理由 | 实际 |
|---|---|
| ``trading_service`` 只有 ``fund_table``，没有 generic 版 | ``get_table()`` 早就写好（``9042ac02`` 第三轮），``test_trading.py`` 二十多例盯着 —— 缺的只是**接线** |
| 演示库 ``stock`` 表里没有非基金 A 股，做了也验证不了 | P0.1 符号入库后有 **52 只** A 股走这一分支（恒生科技 / H 股 ETF 那一大批），快照表里带着真实五档 |

补上的内容（服务 + 路由 + 模板 + 测试）：

| 文件 | 改动 |
|---|---|
| `web/services/stock_detail_service.py` | `get_detail` 加 `trading` 槽位与 `_trading_table()`；鸭子类型 `_is_symbol_a()` |
| `web/routes/stock.py` | `_get_trading_service()` / `_wants_live_refresh()` / `_fill_order_book()` |
| `web/templates/stock_detail.html` | 三张对比表之后、均线之前渲染 `detail.trading` |
| `web/tests/test_stock_detail.py` | +8 例（6 服务 + 2 路由） |

**两道守卫叠在一起**：``IsSymbolA()``（PHP 原样，外汇/指数/外盘期货不摆）+
服务层自己的 ``_checkTradingQuantity``（没有挂载量整段不摆）。第二道在
``trading_service._table`` 里，本模块不重复判。

**实测**（本地服务 + 演示库）：

```
/stock/SH501225  →  五档交易 12 行（涨停 4.138 / 卖5 3.768×800 …）
                      与 realtimequote.orderbook 逐档一致；涨停价 = 3.762×1.1
/stock/^NDX      →  不摆（非 A 股，与 PHP 一致）
/stock/SH513010  →  不摆（在 fundpair 表里，走第 2 分支，PHP 同样没有这段）
```

**路由测试选符号的教训**：第一版用 ``SZ162411``，页面渲染出来却找不到
"五档交易" —— 它在 ``fundpair`` 表里，路由的"第 2 分支优先"直接把它接走了，
``get_detail`` 根本没被调用。改用 ``SH501225``（A 股且不在 fundpair）才真的
走到第 4 分支。**符号选错时测试不会报错，只会安静地测不到想测的那条路。**

### 6.5 关于"页面一片空白"

演示库的 `abpair` / `ahpair` / `adrpair` **三张表都是空的**（只有 `fundpair`
有 8 行），所以 `StockGetPairReferences()` 恒返回 `(None, None, None)`，三张
对比表不出现。**这是符合 PHP 语义的**：那边同样靠 `if ($ah_ref)` 守卫。
等库里有配对记录，这几段会自己冒出来，代码不用改。

同理 `fx_susdcny` / `hf_CL` 这类符号页面空，是因为它们的 `stockhistory`
行数为 0（已查证：`^NDX`=800、`SPY`=3、这两个=0），不是渲染问题。

## 7. CK2b 的两个缺口（跟踪表 / 基金溢价记录）

CK3 之后 `/stock/<symbol>` 的第 2 分支（``_mystock.php:131-136``）还差两段，
本轮补齐 —— 至此四段齐全（估值 / 跟踪 / 配对均线 / 基金溢价记录）。

### 7.1 文件

| 文件 | 改动 |
|---|---|
| `web/services/qdii_fund_list.py` | 新增 `fund_reference_table`（不带回调那版）；`fund_list_table` 抽出共用的 `_build` |
| `web/services/detail_sections.py` | 新增 `build_fund_history` + `IsYahooNetValue` 守卫 |
| `web/services/stock_detail_service.py` | 构造函数加 `link_builder`；`get_fund_pair_detail` 加 `fundlist` / `fundhistory` |
| `web/routes/stock.py` | 新增 `_table_link`（`(符号, 页面名) -> URL`）注入给服务层 |
| `web/templates/stock_detail.html` | fundpair 分支多两段 |
| `web/services/history_service.py` | 修 EST 为 `"0"` 时的假值判断（对照发现） |
| `web/tests/test_stock_detail.py` / `test_qdii_fund_list.py` / `test_history.py` | +19 例 |

### 7.2 为什么不复用 `fund_list_table` 加个参数

PHP 的 `EchoFundListParagraph($arRef, $callback = false)` 是**同一个函数**，
但两个调用点给出的东西不一样：QDII 分组页要给"被看的基金"（用于算对冲值），
个股详情页只给行列表（就是 ref 自己）。合成一个函数的签名会变成
"`fund` 有时是基金、有时是行自己"，所以分成两个具名函数、共用 `_build`。

### 7.3 链接构造器为什么由路由层给

跟踪表有两格要挂 URL（校准记录页 / 标的详情页），都得 `url_for` 才拼得出来，
而服务层跑在没有请求上下文的地方（脚本导出、单测）。`_table_link` 里
``kind == 'fund'`` 那一支**复用列表页那套分流**（`get_symbol_group()` 为空走
`/stock`）—— 跟踪列可能指向中间层标的（`^NDX`），直接送进 `/fund/<symbol>`
会落到一个没有这一行的分组页。

### 7.4 演示库上的样子

真实库冒烟（`_scratch/_ck2b_gaps_smoke.py`）::

    QQQ      HTTP 200  跟踪表 1 行  溢价记录 2 行
    SPY      HTTP 200  跟踪表 1 行  溢价记录 2 行
    ^NDX     HTTP 200  跟踪表 1 行  溢价记录 0 行
    TQQQ     HTTP 200  跟踪表 1 行  溢价记录 0 行
    HF_CL    HTTP 200  跟踪表 0 行  溢价记录 0 行

`^NDX` / `^GSPC` 有 800 条行情但**没有净值** → 溢价记录整段不出现（PHP 同样：
`GetClose()` 取不到就整行不 echo）。跟踪表的「参考值」格在中间层标的上是空的，
因为 `lastcalibration` 表只有 id=1/2/3 三行（SZ161130 / SZ161125 / SH513100）。

## 5. 验证

- CK1：web **619** OK（+2 `test_fund_detail_history.py`）、auto 988 OK、compileall 双绿；
  真实库端到端冒烟：`/fund/SZ161130` 两段都渲染，`/fund/SZ162411` 仅 stockhistory 渲染
  （无净值数据则 netvalueclose 按 PHP `CountNetValue()==0` 守卫隐藏）。
- CK2：auto **1010** OK（988 + 22 `test_pairref.py`）、web **619** OK（未动）、
  compileall 双绿；**PHP 逐例对照 15 例 0 差异**
  （`_scratch/_php_pairref_check.php` + `_php_pairref_compare.py`），
  同时比对汇率符号与建出来的类。
  对照过程中抓到并修掉一个真 bug：B 股判定写成了字符串前缀 `900`，
  而 PHP 是数值区间 `[900000, 1000000)` —— 详见 pitfalls #54。
- CK3：web **640** OK（619 + 21 `test_stock_detail.py`）、auto **1010** OK、
  compileall 双绿；**真实库冒烟**（`_scratch/_ck3_smoke.py`）：

  | 符号 | 结果 |
  |---|---|
  | `/stock/^NDX` | 历史价格 ✓ 均线 ✓ |
  | `/stock/SPY` | 历史价格 ✓ 均线 ✓ 净值价格比较 ✓ |
  | `/stock/fx_susdcny`、`/stock/hf_CL` | 全段隐藏（`stockhistory` 行数为 0，已查证） |
  | `/stock/SZ_NOTEXIST` | 200 且走「取不到数据」分支，不 500 |

  列表页链接分流验证（`/stock?num=0`，共 21 个符号）：4 个基金走 `/fund/`，
  17 个外汇/指数/期货/美股走 `/stock/`。

  变异测试两处：① 去掉 `fund_pair_table(adr_ref, "")` 的空串标题 →
  精确失败；② 把 `_callbackCnhSma` 的"放大-取整-缩小"改成就地保留四位小数
  → 靠 `test_result_is_multiple_of_half_cent` 精确抓到（14700.2 ≠ 14700）。
  第 ② 处最初**没被抓到**，因为 `est=7.0` 恰好能被 0.0005 整除、两种写法
  结果相同 —— 换了个落在两个刻度之间的值才测出差别。
- 顺带修复 `test_sma_route.FundPairSmaRouteTest` 的隔离缺口：该测试未注入 `history_service`
  （走真实库），CK1 新段落暴露后补了 `_EmptyHistory` 注入，使测试只验 SMA 列顺序。
- CK2b 两个缺口：web **669** OK（650 + 19 新增）、auto **1045** OK、compileall 双绿。
  **真实 PHP 逐例对照两套**：

  | 脚本 | 例数 | 结果 |
  |---|---|---|
  | `_php_fundlistitem_check.php` + `_php_fundlistitem_compare.py` | 10 | 8 例逐格一致 + 2 例已知差异（`lastcalibration` 为 `0` / 缺记录时 PHP 只有五格），自检 4/4 |
  | `_php_fundhistory_check.php` + `_php_fundhistory_compare.py` | 8 | 8 例逐格一致，自检 4/4 |

  对照顺带抓到并修掉一个真 bug：`history_service._row_fundhistory` 用
  `if estimate_close:` 判断官方 EST —— Python 里非空字符串 `"0"` 是真值，
  于是 0 值的 EST 会多摆三格；PHP 判它为假。已改 `_php_val_is_false()`
  并补单测（变异确认有牙：8 格 vs 4 格）。

## 8. CK2b 回归：估值表首格被转义成可见文本

两个缺口收完后跑真实库冒烟时发现：`/stock/<symbol>` 的估值表「代码」格
显示的是一整串文本 `<a href="/woody/res/mystockcn.php?symbol=QQQ">QQQ</a>`，
而同一张表在 `/fund/SZ161130` 上是正常链接。

**根因**：`_data_table.html` 的 `data_table` 宏默认对首格做 HTML 转义，
透传标签要显式传 `first_cell_is_html=True`。估值表首格是
`$ref->GetStockLink()`（`fundestparagraph.php:9`）拼好的 `<a>`，
`fundest_service.py:225` 交给模板的就是 HTML 串；
`fund_detail.html:86` 与 `qdii.html:37` 都传了，CK2b（`91d188ce`）
新接的 `stock_detail.html` 那一处漏了。

**为什么单测没挡住**：替身 `RecordingFundEst` 的首格给的是纯文本 `"^NDX"`，
"漏传"与"传了"的渲染结果完全相同。已把替身首格改成真形态的 `<a>` 串，
并补 `test_est_first_cell_is_a_link_not_escaped_html`，断言页面里
既要有未转义的 `<a href=...>`、也不能出现 `&lt;a `。
变异确认有牙：回退模板那一行后，渲染出
`<td>&lt;a href=&#34;...&#34;&gt;^NDX&lt;/a&gt;</td>`。

**顺带扫了全站**：服务层只有 `fundest_service`（`GetStockLink`）与
`reference_service`（`GetExternalLink`）产 HTML 首格，
对应的模板调用点现在**全部**开了开关；走 `row.href` / `row.hrefs`
的表（跟踪表、交易明细）保持关闭 —— 宏里那是独立分支，
开了会把业务文本一并解转义。真实库复验：`/stock/*`、
`/fund/SZ161130`、`/qdii` 的 `&lt;a ` 计数均为 0。

记录见 pitfalls **#59**。
