# 分组页 / 交易页迁移记录

对应两个 PHP 调用现场，它们是第 12 轮那两个段落（交易明细 + 资金汇总）的
**页面装配** —— 渲染逻辑已在第 12 轮迁出，这一轮把同样的段落接到另外两个
调用现场，并补上"账号体系阻塞、但能用已迁服务演示接出"的那两页。

```php
woody/res/php/_mystockgroup.php:129    EchoAll() 的 mystockgroup 分支
    → $acct->EchoStockTransaction($group)   // 内部出交易明细 + 投资组合
    → $acct->EchoMoneyParagraph($group, USCNY, HKCNY)
woody/res/php/_mystocktransaction.php:5  EchoAll()
    → EchoTransactionParagraph($acct, $strGroupId)                        // 整组
    → EchoTransactionParagraph($acct, $strGroupId, new MyStockReference($strSymbol))  // 单标的
```

## 一、结论

| 项 | 结果 |
|---|---|
| 新增代码 | `web/routes/group.py`、`web/templates/group.html`、`web/routes/transactions.py`、`web/templates/transactions.html`、`web/routes/account_demo_data.py`（共用演示数据）、`web/tests/test_group.py`、`web/tests/test_transactions.py` |
| 改动代码 | `web/routes/account.py`（演示数据抽到共用模块）、`web/routes/__init__.py`（注册两蓝图）、`web/templates/base.html` / `index.html`（导航入口） |
| 复用（第 12 轮） | `web/services/transaction_service.py`、`web/services/money_service.py`、`auto/core/stockgroup.py` |
| 测试 | auto 988 OK、web 617 OK（+14）、compileall 双绿 |
| 页面 | `/group`、`/transactions` 演示页（导航 + 首页有入口） |

## 二、这一轮迁了什么（页面装配层）

第 12 轮把"两个段落"的渲染逻辑迁出来了，但只接在 `/account` 一个页面上。
这一轮把同样的段落接到另外两个调用现场：

1. **`/group`** = `_mystockgroup.php` 的 `mystockgroup` 分支：选中一个分组，出它的
   交易明细表（最新 10 条）+ 资金汇总表（**仅这一组**，没有末尾的"全部"行）。
   切 `?groupid=1/2` 换分组。
2. **`/transactions`** = `_mystocktransaction.php`：某个分组的交易明细表（默认最新 10 条），
   支持 `?symbol=` 只看某一支标的（引用对象按该符号建）。

两页与 `/account` 共用一份写死的样例数据（见第三节），所以都是**演示页**：
数据不连库、不随行情更新，链接指向 PHP 页面、点不通。

## 三、演示数据抽到共用模块

`_account.py` 里写死的样例（引用对象、分组、交易记录、净值）原本只有它自己用，
这一轮三页都要用，于是抽到 `web/routes/account_demo_data.py`：
`DEMO_REF_ARGS` / `DEMO_GROUP_NAMES` / `DEMO_GROUP_RECORDS` /
`DEMO_TRANSACTION_RECORDS` / `DEMO_NET_VALUES`、样例引用对象 `_DemoRef`，
以及查询助手 `_ref_of` / `_demo_net_value` / `_demo_groups`（带"全部"行，给
`/account`）/ `_demo_single_group`（单组，给 `/group`）/ `_demo_transaction_records`
（按分组 + 可选标的过滤，给 `/transactions`）。`account.py` 同步改为从它导入，
消除三份重复。

## 四、已知差异（known deviations）

1. **演示数据**：真实页面依赖 `stockgroup` / `stockgroupitem` / `stocktransaction`
   三张表 + 登录态取出的会员与分组；演示库没有这些表、账号体系暂缓，所以用写死样例。
   接真实数据时只换数据来源，段落渲染不动。
2. **`/group` 无"全部"行**：`_mystockgroup.php` 的 `mystockgroup` 分支只对选中的
   那一个分组调 `EchoMoneyParagraph`，所以资金汇总表只有这一组；"全部"行由
   `/account` 负责。这是 PHP 原意，不是疏漏。
3. **`$bAll` 取默认 false**：`_mystocktransaction.php` 调用 `EchoTransactionParagraph`
   时不传 `$bAll`，于是也是"最新 10 条 + 标题链接"，不是全量翻页。与 `/account`、
   `/group` 一致。
4. **"测试"列不出现**：没注入会员邮箱（等价于没登录），`money_service` 只在
   "全部"行 + 登录会员时出现该列；演示页恒不出现。
5. **链接点不通**：清空 / 校准 / 修改 / 删除、"股票分组"标题、"交易记录"标题都指向
   PHP 页面，演示环境下点不动，仅让输出形态与 PHP 一致（逐例对照比对的就是这些串）。

## 五、未迁：`_mystock.php` 个股详情大页

`_mystock.php` 是**个股详情大页**，主体是整条 stock-detail 渲染栈
（`EchoTradingParagraph` / `EchoSmaParagraph` / `EchoFundArrayEstParagraph` /
`EchoAbParagraph` / `EchoAhParagraph` / `EchoAdrhParagraph` … 加上 AH/AB/ADR
配对）。这套段落**尚未迁移**，远大于"账号体系阻塞"—— 它依赖一整套详情渲染
段落，不是单靠登录 / 分组 SQL 就能接出来。本轮只迁了它里面对账号体系相关的
"我的交易"叠加（已并入 `/group`、`/transactions`、`/account`）。整条详情栈列为
后续大项，等 trading/sma/fundest 等段落全套迁移后再做。

## 六、验证

- `python -m unittest discover -p "test_*.py"`（auto）：988 OK
- `python -m unittest discover -s web/tests -t .`：617 OK（+14，其中 test_group 7 + test_transactions 7）
- `python -m compileall -q auto web`：双绿
- 页面冒烟：`/group`、`/group?groupid=2`、`/transactions`、`/transactions?groupid=2&symbol=SZ161125` 均 200，资金表行数 / 过滤行数核对正确。

## 七、下一步候选

- `_mystock.php` 个股详情大页（整条 stock-detail 渲染栈：trading/sma/fundest/ah/ab/adr…），工程量远大于账号体系。
- 三个整页的外壳：登录、`GetGroupSql()`、`StockEditTransactionForm` / `StockMergeTransactionForm`，仍随账号体系暂缓。
- 或 order book 写 snapshot 表的长期项。
