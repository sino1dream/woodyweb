# stocktransaction 迁移记录（交易写入链路）

对应 PHP 四处源码，按"数据层 → 纯逻辑 → 写入口 → 页面"四个阶段推进：

```php
php/sql/sqlstocktransaction.php      StockTransactionSql        （T1）
php/sql/sqltable.php:195/:243        GetRecordById / DeleteById  （T1，基类里）
woody/res/php/_submittransaction.php _SubmitTransactionAccount   （T3 七个动作）
                                     _getStockQuantity / _getStockCost（T2）
php/stocktrans.php:134/:148          UpdateStockGroupItem(Transaction)（T3 回填）
woody/res/php/_stock.php:151         StockGetFundFeeRatio        （T3 申购费率）
woody/res/php/_edittransactionform.php  编辑表单                 （T4，待办）
woody/res/editstocktransactioncn.php    页面装配                 （T4，待办）
```

## 一、结论

| 阶段 | 内容 | 落点 |
|---|---|---|
| T0 | `stocktransaction` 建表 + 演示种子（4 条交易） | `dbtools/seed_stock_group_tables.py` |
| T1 | `StockTransactionSql` 全量方法 + 分组侧四个交易查询 | `sql/stocktransactiontable.py`、`core/stockgroupsql.py` |
| T2 | 数量符号 / 六种佣金费率 / `strval_round` | `core/stocktrans.py` |
| T3 | 七个写入动作 + 成员预统计回填 | `core/submittransaction.py` |
| T4 | 编辑表单 + `/transactions`、`/account` 切真库 | **待办** |

测试：auto 全量 1463 OK（新增 `test_stocktransactiontable.py` 24、
`test_stocktrans.py` +12、`test_submittransaction.py` 41）；
PHP 逐例对照 `cost / quantity / round`（T2）与 `fee / arb / adjust`（T3）
全部一致。

## 二、T0：种子（先有数据才谈端到端）

`seed_stock_group_tables.py` 补 `stocktransaction` 的 DDL 与
`DEMO_TRANSACTIONS`（"演示分组A"四笔：SZ161125 买/卖、SH513100、
SZ159501 定投）。两个设计点：

* **`filled` 用固定时间戳**，不用 `NOW()` —— 种子要可重现，"成交日期"
  列才稳定；
* **查重键是四列**（`groupitem_id + quantity + price + filled`），
  不是"按成员查重"。最早写成后者，同一成员的买入+卖出只灌进第一条
  （4 条变 3 条）。

`--clear` 后从零 apply 得 4 条交易 / 5 成员 / 1 会员；`check_schema.py` 通过。

## 三、T1：数据层

* `sql/stocktransactiontable.py` —— `StockTransactionSql` 的
  `get_all / get_record / count_all / count / delete / insert / update /
  merge / delete_all_by_ids`，外加 T3 用到才发现缺的
  **`get_record_by_id` / `delete_by_id`**（这两个在 PHP 的 `TableSql`
  基类里，不在子类，见 pitfalls #113）。
* `core/stockgroupsql.py` —— `StockGroupItemSql` 补四个"按 stock_id 问
  交易"的转发方法（T1 之前已存在，本轮补齐并反填级联删）：
  `sql_update_stock_group` 的移除成员分支与
  `sql_delete_stock_group_item_by_group_id` 现在会**先删交易再删成员**。

三个必须照抄的细节写在模块 docstring 里：`filled` 只在 Insert 时生成、
`GetAll` 排序是 `filled` DESC、空键三态（不执行 SQL 就返回 `None`）。

## 四、T2：纯逻辑（佣金与数量）

`core/stocktrans.py` 新增 `strval_round` / `sql_clean_string` /
`get_stock_quantity` / `get_stock_cost`，全部不碰全局（`$POST` 由调用方
以 `params` 字典传入）。

* **`get_stock_quantity`**：卖出（`type == '0'`）在 trim **之后**串前加
  `-`，`''` 得到 `'-'`（PHP 同款不设防）。
* **`get_stock_cost`**：六种默认费率（期货 0.62 / 外汇 15 / A 股基金
  ×0.0001 / A 股 ×0.0002·×0.0015 且保底 5 元 / 港股 ×0.002 / 美股
  <200 份 1 元否则 ×0.005，卖出加 ×0.000028）；`commissionselect == '1'`
  把值当**千分比**，税费只有显式分支才有。
* **`strval_round`**：不传精度按量级三档（`> (10 - MIN_FLOAT_VAL)` → 2 位、
  `> (2 - MIN_FLOAT_VAL)` → 3 位、其余 4 位），舍入委托
  `sql.historytable.mysql_round`（PHP 8 实测口径），不另造一套。
  坑见 pitfalls #112。

对照 harness：`_php_stocktrans_check.php` + `_compare.py`，三个用例
（quantity 6 键 / cost 15 键 / round 18 键）与 PHP 逐键一致。

## 五、T3：七个写入动作

`core/submittransaction.py` 的 `SubmitTransactionAccount::process(params)`
照搬 `_SubmitTransactionAccount::Process()` 的分派顺序：

```
submit（merge → new → edit）→ delete → adjust → empty → groupid（基金申购）
```

### 与 PHP 的三处差异（用户拍板）

1. **一律 POST**：PHP 的 delete / adjust / empty / 基金申购是 **GET 链接
   副作用**（`submittransaction.php?delete=123`）。本项目把它们并进同一个
   `params` 字典，路由层只允许 POST。分派顺序与取值语义不变。
2. **登录态收敛成构造参数**：`member_id`（PHP 的 `GetLoginId`）+ `is_admin`
   （`IsAdmin`）。`is_group_read_only` 的判定一字不改（分组不属于当前
   登录用户即只读，且 group_id 为假值时**不**只读）。
3. **返回四键字典** `{"action", "ok", "group_id", "group_item_id"}` ——
   PHP 的 `Process` 无返回，这里给 web 层做 flash + 重定向用。

### 五个照抄点（别"修正"）

* **管理员旁路只覆盖 delete**（pitfalls #116）：adjust / empty 走不带旁路的
  `_canModifyStockTransaction`。
* **合并方向由 `type0` 决定**：`type0 == '0'` 时 `symbol0` 是源，否则
  `symbol1` 是源；合并后**两边**成员都重算。
* **`UpdateStockGroupItem` 重算整组**（pitfalls #117）：`item_id` 只参与
  `if ($strGroupItemId == false) return;` 这一句闸门。
* **`UrlGetQueryValue(...) == false` 把 `'0'` 一起判掉**（pitfalls #115）：
  amount / netvalue 填 0 等于没填。Python 侧用 `_php_query_value`。
* **基金申购的手续费多乘 0.1、备注带前导 `}`**（pitfalls #114）：都是作者
  约定，改了就和生产端落库值不一致。

### 对照

`_php_submittransaction_check.php` + `_compare.py` 三个用例
（fee 18 键 / arb 10 键 / adjust 7 键）与 PHP 逐键一致。没对照的是
"分派与权限闸门"—— 那是流程不是纯函数，靠 `test_submittransaction.py`
的 41 条单测覆盖（含"管理员旁路不覆盖 adjust"这类防回归断言）。

## 六、已知差异（known deviations）

1. **不做日志**：PHP 的 `_debugStockTransaction` / `_debugFundPurchase`
   是 `trigger_error`。本模块留空实现（`_debug_stock_transaction` /
   `_debug_fund_purchase`）给调用方子类化覆盖。
2. **`empty()` 把 `'0'` 当没填**：佣金框填 0 走默认费率（`_php_empty` 照抄）。
   这是 PHP 的行为，不是 bug —— 但页面层要提示用户"想清零请填 0.001 之类"。
3. **对冲比率需注入**：`GetArbitrageQuantity` 依赖 `GetStockHedge`（要连库
   查持仓）。auto 层的 `get_arbitrage_quantity` 收 `hedge_of` 回调，不传
   按 1.0 计（与 PHP 查不到时一致）。web 层接真库时要注入。
4. **逐例对照不含除零档**：PHP 8 的 `/` 遇 0 抛 `DivisionByZeroError`
   （pitfalls #118），Python 同款 `ZeroDivisionError`，两边都不设防。

## 七、下一步（T4）

1. `woody/res/php/_edittransactionform.php` → 新增/编辑表单（含
   `_getGroupItemQuantityArray` 的"上次成交数量"默认值、
   `{`/`}` 批次标记的取反）；
2. `web/routes/transactions.py` 从演示数据切成真库（用户拍板 T0 建种子后
   就切），并挂上 POST 写端点；
3. `/account` 同步切真库。
