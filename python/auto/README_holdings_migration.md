# 持仓型基金迁移：`HoldingsReference` + `/stock/<symbol>` Holdings 分支（CK4）

对应 PHP：

| PHP | 行数 | Python |
|---|---|---|
| `php/stock/holdingsref.php` | 441 行 / 24 方法 | `auto/ref/holdingsref.py` |
| `php/stock.php:133` `StockGetHoldingsReference` | 6 行 | `holdingsref.StockGetHoldingsReference` |
| `php/sql/sqlholdings.php`（`holdings` / `holdingsdate`） | — | `auto/sql/holdingstable.py` |
| `res/php/_mystock.php:138-142` Holdings 分支 | 5 行 | `web/routes/stock.py` 第 3 分支 |
| `fundestparagraph.php:160` `EchoHoldingsEstParagraph` | ~25 行 | `web/services/stock_detail_service.get_holdings_detail` |

生产端参照：<https://palmmicro.com/woody/res/holdingscn.php?symbol=SH513050>

> 本文引用的 `pitfalls #NN` 在 `.dsh/skills/palmmicro-php-to-python/references/`
> （**`.dsh/` 被 gitignore，不随仓库提交**）。读不到就当它们是本地批注，
> 关键结论本文都就地写了一遍 —— 但反过来要记住：**仓库外的文档不会跟着
> 仓库走**，跨机器协作时那些编号就是死链。

## 1. 这个分支和 QDII 那条路有什么不同

P0 迁的是**配对型**基金：一只基金跟踪**一个**标的（`SZ161130` 跟 `^NDX`），
估值 = 标的价 × 校准值。本分支是**持仓型**：一只基金持有**几十只**成分股
（`SH513050` 持有腾讯 30.68%、阿里 27.34%、PDD 7.31% … 共 36 只），
估值 = 各成分股按持仓比例的**加权涨幅** × 汇率调整。

分支判据（`_stock.php:133`）只看 `holdings` 表有没有行：

```php
function StockGetHoldingsReference($strSymbol)
{
    if (SqlCountHoldings($strSymbol) > 0)   return new HoldingsReference($strSymbol);
    return false;
}
```

**只看表有没有行，不看净值/行情齐不齐** —— 所以持仓日或净值缺失时，对象会
退化成一只普通股票引用（持仓列表为空），页面上的表现是"持仓段落不出现"
而不是报错。

## 2. 估值的锚：持仓日

构造时先读 `holdingsdate` 得到**持仓清单更新日** `strHoldingsDate`，然后：

* **净值**取这一天的（`SqlGetNetValueByDate`）；
* 每只成分股的**旧价**取这一天的 `adjclose`；
* **新价**取当前价（或指定日期的 `adjclose`，或 `EstFromPair()` 实时估）。

```
新净值 = 持仓日净值 × (1 + 加权涨幅 × 自身汇率调整 × 仓位)
加权涨幅 = Σ[比例 × (新价 / 旧价) / 成分股汇率调整] / Σ比例 - 1
```

PHP 原注释就一句：`// (x - x0) / x0 = sum{ r * (y - y0) / y0 }`。
注意 `if ($fTotalRatio > MIN_FLOAT_VAL)` 那一句（`holdingsref.php:337`）：
比例之和太小时**不做归一化**。照抄 —— 除以 0 会抛异常而 PHP 不会。

## 3. 三个净值

| PHP | 含义 |
|---|---|
| `GetOfficialNetValue()` | **官方 EST**：按成分股**收盘价**估（`strEstDate`） |
| `GetFairNetValue()` | **参考 EST**：按成分股**当前价**估 + `fOfficialEstAdjust` |
| `GetRealtimeNetValue()` | **实时 EST**：对能用配对实时估的成分股再估一次 |

`fOfficialEstAdjust` = 官方 EST 那天的**真实净值** − 官方估算值
（`holdingsref.php:390`），含义是"承认自己的估算有系统性偏差，用最近一次的
偏差做补偿"。后两个净值把它加回去（`:420` / `:434`）。

EST 日期取**更早**的那个市场日期（`pick_est_date`，`:97-101`）：港股比美股
早收盘，取晚的会漏掉还没收盘的市场。

## 4. 刻意与 PHP 不同的三处（known deviation）

1. **默认不写库**：PHP 的 `GetOfficialNetValue()` 里无条件调
   `StockUpdateEstResult()`（`:390`），也就是**每次页面渲染都在写 `fundest` 表**。
   本项目架构是「写只跑 daemon / CLI，web 只读」（`auto/refquote.py`），
   所以写库收进 `auto_write` 开关，默认关 —— 与 `FundPairReference` 一致。
2. **`NetValueReference` 用 `MysqlReference` 近似**：Python 侧没有对应的类
   （`php/stock/netvalueref.php`），`FundPairReference` 里也是这么近似的。
   `netvalue_ref` 只被 `get_net_value_ref()` 暴露出去，估值算法本身不用它。
3. **`_adjust_to_cny` 加了零值保护**：PHP 的 `return $fOldCNY / $fCNY;`
   （`:247`）是裸除法，`new_ref` 没价时 PHP 直接 `DivisionByZeroError` fatal。
   生产环境四个汇率引用永远有价，不会触发；演示库缺 `fx_susdcny` /
   `fx_shkdcny` 时实测会把页面打挂。这里退回 `1.0`（"不调汇率"），
   宁可数不准也不要页面 500。

## 5. 数据层两张表的坑

`holdings(stock_id, holding_id, ratio)`：

* `stock_id` = 基金，`holding_id` = 成分股，`ratio` 是**百分数**
  （30.68 表示 30.68%），查询按 `ratio DESC`（`BuildOrderBy()`）；
* 取出来是 `{int holding_id: float ratio}`。

`holdingsdate(id, date)`：**表里没有 `stock_id` 列**！PHP 的
`DateSql::ReadDate($strId)` 把 stock_id 存在 `id` 列。写
`WHERE stock_id = %s` 会**静默返回空**（不报错），于是持仓日永远是空、
整个对象退化。`test_holdingstable.py` 里有一条专门钉这个
（`test_condition_column_is_id_not_stock_id`）。

## 6. 验证：三道

### 6.1 PHP 逐例对照（人造输入）

`auto/_scratch/_php_holdings_check.php` + `_php_holdings_compare.py`：
函数体从 `php/stock/holdingsref.php` **现场切出**（花括号配对，不手抄），
12 项对齐，容差 `1e-12`。

### 6.2 PHP 逐例对照（真实 36 只，CK4-D）

`auto/_scratch/_php_holdings_real.php` + `_ck4_real_check.py`：从演示库导出
36 只真实持仓（比例 / 持仓日旧价 / EST 日收盘价 / 当前价）喂给两边，
**7 项 ≤1e-12 一致**（`adjust_usd/hkd` 各两条、`official`、`fair`、
`ratio_display`）。

这一步不可省：人造小样本是 3 只、两边类型一致，**盖住了 §7 第 1 条那个
只在真实形状下才暴露的 bug**。

### 6.3 页面实测（本地 vs 生产端）

`auto/_scratch/_ck4_sample.py` 把生产端那一页的 input/output 搬进演示库，
`_ck4_compare.py` 逐格比：

| 列 | 生产端 | 本地 | 差 |
|---|---|---|---|
| 官方 EST | 1.025 | 1.026 | +0.10% |
| EST 日期 | 2026-10-14 | 2026-10-14 | 一致 |
| 参考 EST | 1.006 | 1.011 | +0.49% |
| 说明文字（持仓更新日） | 2026-10-13 | 2026-10-13 | 一致 |

**差额归因（不是迁移错误）**，两条都是演示库的输入缺口：

1. **汇率调整退化成 1.0**：`fx_susdcny` / `fx_shkdcny` 在演示库里没人抓
   （无价），`USCNY` / `HKCNY` 的 `netvaluehistory` 又缺持仓日 10-13 那条
   （只有 10-14 / 10-12 / 10-09）。两处 `_adjustToCNY` 都走了"取不到就当 1.0"。
   港股占 89%，汇率一动就是全局的。
2. **`fOfficialEstAdjust` 取不到**：`fair = _estNetValue() + fOfficialEstAdjust`，
   后者需要官方 EST 日（10-14）的**真实净值**，演示库 `netvaluehistory`
   只到 10-13，于是 adjust = 0。反推生产端的 fair，它那边的 adjust ≈ −0.0059，
   即 10-14 真实净值 ≈ 1.0204（10-13 是 1.0115）—— 生产端有这条数据，
   只是 `EchoNetValueHistoryParagraph($ref, false, 0, 1)` 只画一行所以页面上
   看不到。

公式正确性由 §6.1 / §6.2 保证（同一份输入下 PHP 与 Python 逐位一致）；
§6.3 验的是"端到端能不能出数、量级对不对"。

## 7. 本次踩到并修掉的三个真问题

1. **`holdings_ratio` 的键是 int 而 `get_stock_id()` 给 str**（`ratio_of()`）。
   PHP 里数字字符串数组键会自动转 int，永远命中；Python 的 dict 不认，
   比例**全取成 0** → `Σ比例 = 0` → `0 - 1 = -1` → **估值整列归零且不报错**。
   页面表现是"官方 EST / 参考 EST 全是 0，其它列都正常"。
   已加 `RatioOfTest` + `test_string_stock_id_still_finds_ratio` 两条回归测试。
   见 pitfalls #71 / #72。
2. **`realtimequote.id` 不是自增**（主键就是 `stock_id`）。造样本时没写 `id`，
   38 只成分股全插到 `id = 0` 那一行上互相覆盖，最后只剩 1 行。
   见 pitfalls #73。
3. **造样本的 `DELETE ... IN (SELECT id FROM stock)`** 把 10-13 那天**所有符号**
   的历史行清了（中间层 `^NDX` / `^GSPC` / QQQ / SPY 全没）。靠事前
   `mysqldump` 的备份恢复了 18 行。见 pitfalls #74。

## 8. 刻意没做的（下轮再补）

| PHP | 状态 |
|---|---|
| `EchoNetValueHistoryParagraph`（181 行，净值历史段落） | ❌ 未迁，CK4 只取它的"持仓更新日"那句说明 |
| ~~`EchoFundHistoryParagraph`（`:141`）~~ | ✅ **2026-10-15 复核：已迁**，见 `web/services/detail_sections.build_fund_history`（`cfc6e702`），CK4 只是没在 Holdings 分支上接它 |
| `GetProportionArray` 的调用方 `netvaluehistoryparagraph.php:25` | ❌ 方法已迁，调用方没接 |
| `StockGetHoldingsReference` 之外的三个 stocklink 用法 | 方法已加（`get_holdings_link` 等），只接了 Holdings 分支 |

## 9. 下游接线：QDII 的持仓口径（CK4 收尾，已接）

`HoldingsReference` 落地后，PHP 里有**两处**会按"这个标的是不是持仓型"切分支。
它们不影响 CK4 自己的 `/stock/<symbol>` 页，影响的是 **QDII 基金的估值**
（`/fund/<symbol>`、`/qdii`）。本轮两处都接了。

**演示库上看不出变化**：只有 `SH513050` 有持仓数据，而它不是任何 QDII 基金的
est 标的（est 标的是 `QQQ` / `^NDX` 那一层）。所以"接对了没有"不能靠页面看，
只能靠单测 + PHP 逐例对照 —— 这两样都做了，见 §9.4。

### 9.1 `QdiiReference` 构造时选 est_ref

```php
// php/stock/qdiiref.php:245
if (SqlCountHoldings($strEstSymbol) > 0)   $est_ref = new HoldingsReference($strEstSymbol);
else                                       $est_ref = new FundPairReference($strEstSymbol);
```

Python 侧原先只有 `set_est_ref(est_symbol)` —— 传**字符串**，`set_est_ref`
内部一律包成 `EstReference`，所以持仓口径永远到不了估值那一步。

本轮加了 `refobj.build_est_reference(symbol, source)`：判据用
`refobj.count_holdings()`（= `HoldingsSql.count()`），**查不到一律当 0**
（走配对口径）—— 它只是选型判据，不该让引用对象构造失败。

### 9.2 `_getOfficialEstVal()` / `_getFairEstVal()` 的持仓分支

```php
// php/stock/qdiiref.php:110-124
if ($str = $this->_getEstNetValue($est_ref, $strDate))   return $str;
if (method_exists($est_ref, 'GetHoldingsDate'))  return strval($est_ref->_estNetValue($strDate));
return $this->_getEstPrice($est_ref, $strDate);
```

Python 侧对应实现是 `fundestcalc._official_est_value()`（`:816`），原先注释里
照抄了 PHP 三行但**中间那行没实现**，直接从"净值"跳到"行情价"。本轮补上
`_holdings_est_value()`，**刻意不缓存引用对象** —— 持仓清单会被后台改，
缓存住了页面就会一直用旧比例，而这种错不报错。

**两档顺序是反的，别照抄一半**：官方 EST 净值优先、参考 EST 持仓优先。
写错成一样的顺序，页面照样出数（那格取自另一档而已），不报错。

> 参考 EST 那条**不在本次接线范围**：Python 走的是
> `FundPairReference::GetFairNetValue()`（`fundpairref.php:256`）另一套语义，
> 与 `_getFairEstVal()` 不是同一个东西。

### 9.3 一处刻意偏差：`else` 分支仍是 `EstReference`

PHP 的 `else` 是 `new FundPairReference(...)`，本版仍是 `EstReference`。
理由沿用了 `_build_pair_reference()` 那条：**`FundPairReference` 构造时会再查
自己的配对**（`^NDX → hf_NQ → hf_NQ 的配对 → …`），PHP 靠 `IsSinaFutureUS()`
打断递归，本版用更直白的"中间层引用不再找配对"。换成 `FundPairReference`
会一次改动全部 33 只基金的取值路径，**不在这轮的风险预算内**。

### 9.4 验证

| 手段 | 结果 |
|---|---|
| `test_refobj.EstReferenceSelectionTest` | 7 个：选型两个方向、`count_holdings` 三种退化、`QdiiReference` 构造两条 |
| `test_fundest.HoldingsEstValueTest` | 3 个：非持仓返回 None、无连接返回 None、持仓档拿到值**且带上日期** |
| PHP 逐例对照 `_ck4_qdii_compare.py` | **4/4**，含"持仓 + 无净值 → 走持仓档且传 `2026-10-14`" |

对照特意比了两样：数值**和**"传给 `_estNetValue` 的日期"。只比数值看不出
档位错 —— 三档恰好给出同一个数时（净值 1.0 与行情价 1.0）是分不出来的。

## 10. 跑法

```bash
# 单测
cd python/auto && D:/python/python.exe -B -m unittest test_holdingsref test_holdingstable -v

# PHP 逐例对照（人造输入）
cd python && D:/python/python.exe -B auto/_scratch/_php_holdings_compare.py

# PHP 逐例对照（真实 36 只）
cd python && D:/python/python.exe -B auto/_scratch/_ck4_real_check.py

# 页面实测（需先起 web 服务）
cd python && D:/python/python.exe -B auto/_scratch/_ck4_sample.py --apply
curl -s "http://127.0.0.1:5000/stock/SH513050" -o /tmp/local_SH513050.html
cd python && D:/python/python.exe -B auto/_scratch/_ck4_compare.py
```
