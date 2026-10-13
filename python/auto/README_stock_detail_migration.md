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
源码在 **`php/stock/fundpairref.php`（约 278 行）**，`php/stock` 层，**尚未迁入
`auto/ref/refobj.py`**（`refobj.py:523` 与 `pair_service.py` 文档都明确标注"未迁移"）。

没有这层，AH/AB/ADR 对比、配对 SMA、A 股带配对溢价的 trading、Holdings 分支**全部拿不到
数据**。`pair_service.py` 已通过 `_invoke(ref, snake, camel)` 的 snake/camel 兜底容忍该层缺失
（方法出现前格子留空），所以迁完配对引用层后这些服务能自动"通电"。

## 3. 分阶段 checkpoint

- **CK1（已完成）**：把已迁的 `stockhistory` + `netvalueclose` 接进 `/fund/<symbol>`
  详情页（仅接线 + 模板，不碰配对引用层）。零风险、立刻可见。
- **CK2（关键石）**：迁移 `fundpairref.php` 配对引用层到 `refobj.py`
  （`MyPairReference` / `FundPairReference` / `HoldingsReference` + 三个 `StockGet*Reference`）。
  解锁 AH/ADR/Holdings 全栈。
- **CK3（接 A/H/ADR 详情）**：新建 `/stock/<symbol>` 详情页（路由策略已与用户确认：非基金
  详情独立成 `/stock/<symbol>`，`/fund` 仍只管基金），渲染 reference + 通用 trading(配对溢价) +
  AH/AB/ADR 对比 + 通用 SMA + ah/fundpair SMA + stockhistory + netvalueclose（依赖 CK2）。
- **CK4（Holdings 分支）**：接 `HoldingsReference`（依赖 CK2）。
- **CK5（交易块 + 链接，账号可演示）**：`_echoMyStockTransactions` 复用演示数据 + `_getMyStockLinks` / `GetMyStockLinks`。
- **CK6（长期）**：order book（五档盘口）写入 snapshot 表，彻底解决 tradingparagraph 历史问题。

## 4. 路由策略（已确认）

非基金详情（A/H/ADR/外汇/Holdings）走新建的 `/stock/<symbol>`，与现有 `/stock` 列表页同源；
`/fund/<symbol>` 仍只管基金。URL 语义干净，且分别贴近 PHP 的"基金页"与"个股页"两类生产端页面。

## 5. 验证

- CK1：web **619** OK（+2 `test_fund_detail_history.py`）、auto 988 OK、compileall 双绿；
  真实库端到端冒烟：`/fund/SZ161130` 两段都渲染，`/fund/SZ162411` 仅 stockhistory 渲染
  （无净值数据则 netvalueclose 按 PHP `CountNetValue()==0` 守卫隐藏）。
- 顺带修复 `test_sma_route.FundPairSmaRouteTest` 的隔离缺口：该测试未注入 `history_service`
  （走真实库），CK1 新段落暴露后补了 `_EmptyHistory` 注入，使测试只验 SMA 列顺序。
