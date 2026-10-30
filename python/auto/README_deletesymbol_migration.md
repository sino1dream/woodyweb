# deletesymbol.php 迁移记录（删标的 + 连带清表）

> 源文件：`woody/res/deletesymbol.php`（119 行）+ `php/deletestock.php`（28 行）
> 落地：`python/auto/deletesymbol.py` + `web/services/delete_symbol_service.py`
> + `GET/POST /stock/<symbol>/delete` + `web/templates/stock_delete.html`
> 日期：2026-10-30

## 一、PHP 那一页是什么

一个 `SymbolAccount` 子类，只实现 `AdminProcess()`，**页面上什么都不显示**：

```php
class _DeleteSymbolAccount extends SymbolAccount
{
    public function AdminProcess()
    {
        if ($ref = $this->GetSymbolRef())   { _deleteStockSymbol($ref); }
    }
}
$acct = new _DeleteSymbolAccount();
$acct->AdminRun();
```

`AdminRun()`（`php/account.php:279`）的形状是关键：

```php
function AdminRun()
{
    if ($this->IsAdmin())   { $this->AdminProcess(); }
    SwitchToSess();          // ← 无条件跳回
}
```

所以：

* 管理员点进来 → 删（或者被拦）→ 跳回，**没有任何反馈**；
* 非管理员点进来 → 连 `AdminProcess()` 都不进 → 跳回，**看起来完全一样**；
* 失败也不说话：五道闸门里任何一道命中都是 `return`，`debug.txt` 之外无痕迹。

入口是 `GetStockEditDeleteLink()`（`php/stocklink.php:285`）拼的带 `confirm()` 的
`<a href>`，与"修改"链接并排 —— 也就是说它是 **GET 副作用**：

```php
if ($bAdmin)	$str .= ' '.GetDeleteLink(PATH_STOCK.'deletesymbol.php?symbol='.$strSymbol, '股票'.$strSymbol);
```

顺带一提：`woody/res/php/_deletesymbol.php` 是同一份内容的副本（107 行处同样一段），
`woody/res/deletesymbol.php` 才是壳子指向的那个。

## 二、落地形态

| 路由 | 干什么 |
|---|---|
| `GET /stock/<symbol>/delete` | 预览：五道闸门逐条结论 + 会删的行数 + 会留下的残留 + 按钮 |
| `POST /stock/<symbol>/delete` | 执行；`include_orphans=1` 时连残留一起清 |

入口放在 `GET /stock/<symbol>/edit`（可编辑项索引）末尾的「危险操作」段。
PHP 把"修改 / 删除"并排放在详情页，本地的"修改"落在那个索引页，所以
"删除"跟着它 —— **入口与 PHP 一样只有一个**。

三处与 PHP 的形制差异（都是本项目一贯的取舍）：

1. **写操作不走 GET**（同 `/submit`、`/holdings/submit`）；
2. **权限真的判**：`is_admin()`（`web/services/admin_auth.py`），非管理员 GET 只读、
   POST 直接 403 且**一行都不写**；PHP 是静默跳回，看着像成功；
3. **有反馈**：删了几行 / 被哪道闸门拦住，都 flash 出来。

## 三、五道闸门

`_deleteStockSymbol()` 是一条 `if / else if` 链，**任意一环命中就放弃删除**：

| 顺序 | 判据 | 命中后 |
|---|---|---|
| 1 | `abpair` 里有本符号（本体或配对侧） | 放弃 |
| 2 | `adrpair` 同上 | 放弃 |
| 3 | `ahpair` 同上 | 放弃 |
| 4 | `fundpair` 同上 | 放弃 |
| 5 | `_deleteHasCalibration()` | **已经删了校准值**，返回 `false` 继续 |
| 6 | `SqlDeleteStockGroupItemByStockId()` | 有分组持仓就放弃 |

两个容易看错的点：

### 3.1 `SqlDeleteStockGroupItemByStockId()` 名字带 Delete，实际只看不删

```php
function SqlDeleteStockGroupItemByStockId($strStockId)
{
    $sql = new StockGroupItemSql();
    $iTotal = $sql->CountByStockId($strStockId);
    if ($iTotal > 0)
    {
        DebugVal($iTotal, 'Stock group item existed');
        return false;        // ← 拦住删除
    }
    return true;
}
```

它一行都不删，只是数。**所以"标的在任意一个分组里持有"时，这个符号根本删不掉** ——
要先把分组持仓拆了（或换成别的标的）。

### 3.2 `_deleteHasCalibration()` 是塞在 if 链里的 do 块

```php
function _deleteHasCalibration($strStockId)
{
	$cal_sql = GetCalibrationSql();
	$iTotal = $cal_sql->Count($strStockId);
	if ($iTotal > 0)
	{
		DebugVal($iTotal, 'Calibration history existed');
		$cal_sql->DeleteAll($strStockId);     // ← 副作用
	}
	return false;                             // ← 恒 false，永远拦不住
}
```

名字问"有没有"，行为是"有就删，然后说没有"。它被放在第 4、第 6 环之间，
于是：**被 `stockgroupitem` 拦下时，校准历史已经没了，标的却还在**。
净效果是"什么都没删成，但校准历史丢了"。

## 四、PHP 真会删 6 张表

`DeleteStock()`（`php/deletestock.php:20`）只做四件事，加上前面那个副作用：

| 顺序 | 表 | 匹配列 | 说明 |
|---|---|---|---|
| 1 | `calibrationhistory` | `stock_id` | 上面那个副作用 |
| 2 | `stockema50` | `stock_id` | `SqlDeleteStockEma()` 先 50 后 200 |
| 3 | `stockema200` | `stock_id` | |
| 4 | `stockhistory` | `stock_id` | |
| 5 | `netvaluehistory` | `stock_id` | |
| 6 | `stock` | **`id`** | 符号本身那一行，**必须最后** |

**`README_pending_pages.md` 原来写的"要连带清 8 张表"是错的** —— 当初只看了
页面标题就估了个数，没读 `deletestock.php`。实测 6 张。已就地改正。

每张表的删除都先 `Count > 0` 再 `DeleteAll`，净效果与直接删一样
（`DeleteAll` 走 `KeySql::DeleteAll`，`WHERE stock_id = …`）。

`stock` 排最后不是风格问题：`DeleteAll` 之后 `GetId($symbol)` 就返回 `false`，
任何"再按符号定位一次"的后续步骤都会落空。Python 侧把这条钉进了单测
（`test_stock_row_goes_last`）。

## 五、刻意偏离（都实测过，数字在 §8.1）

| # | PHP 行为 | 本版行为 | 为什么 |
|---|---|---|---|
| 1 | 被 `stockgroupitem` 拦下时，`calibrationhistory` 已经被删 | **一行都不动** | §3.2，纯属顺序事故；照抄等于"拒绝操作却毁掉数据" |
| 2 | 五道闸门 `if / else if`，只报第一道 | 五道**全查**，全部列出 | 管理员要知道"把 AB 拆了之后还会不会被 fund 拦住"；判定仍然只看"有没有任何一道命中"，与 PHP 等价 |
| 3 | 静默跳回，成功失败都不说 | flash 出删了几行 / 被谁拦住 | 见 §1 |

第 2 条不是语义变化：`plan()` 里 `blocked = 任意 gate.hit`，只是**报告**更全。
对照脚本专门有一例（`gate_all`）验证"三道同时命中时，PHP 只报 ab、本版报三道，
但两边都判定为不可删"。

## 六、PHP 不碰、会留下孤儿的 15 张表

`information_schema` 里带 `stock_id`（或以 `id` 存 stock id）的表有 20 多张，
`deletestock.php` 只清 6 张。剩下的不是"占点空间"，而是**把同一符号再加回库里时
静默出错**：

| 表 | 匹配列 | 留下的后果 |
|---|---|---|
| `stockhistorydate` | `id` | **最要命的一张**：它是 `/submit/history` 的水位线。残留水位线比新库里全部历史都新 → 重新加回符号后 `submithistory` 一直报 `already_updated`，历史一条都补不进来 |
| `lastcalibration` | `id` | 重加回的符号带着旧校准值参与估算（`fundpairref` 直接读它） |
| `fundposition` | `id` | 被当成持有该仓位的基金 |
| `futurespair` | `id` / `stock_id` | PHP 四道配对闸门**不含**它 —— 能删掉一个还在被引用的期货符号 |
| `fundest` | `stock_id` | 旧 EST 当基准 |
| `holdings` | `stock_id` / **`holding_id`** | 两个身份都要清：作为基金时 `stock_id` 是本符号，作为成分股时在 `holding_id` |
| `holdingsdate` | `id` | 持仓表的日期水位线 |
| `quarterreport` | `stock_id` | 季报 |
| `shareshistory` | `stock_id` | 场内份额 |
| `sharesdiff` | `stock_id` | 每日新增份额 |
| `stockdividend` | `stock_id` | 分红 |
| `stocksplit` | `stock_id` | 拆合股 |
| `futurepremium` | `stock_id` | 期货升水 |
| `estquote` | **`symbol`** | 快照，按符号字符串存 |
| `realtimequote` | **`symbol`** | 同上 |

三种匹配列（`id` / `stock_id` / `symbol`）混用是这一节最容易写错的地方：
`realtimequote.id` 存的也是 stock id，但**按 `symbol` 删才是对的** ——
同一张表里 id 与本符号无关的行不能被带走（`test_symbol_keyed_does_not_match_by_id`）。

**默认不清**：`include_orphans=False` 时与 PHP 口径逐字节一致（§8.1 的 `clean`
一例两边完全相等）。页面上那个复选框不勾，行为就是 PHP；勾了才级联。

## 七、明确不处理的 3 张表

| 表 | 为什么 |
|---|---|
| `stock_minute_data` | 只有 `id`/`time`/`ohlc`，没有 `stock_id` 也没有 `symbol` —— 判断不了某行属于哪只标的；本版也不写这张表 |
| `stocktransaction` | 挂在 `stockgroupitem` 上（`groupitem_id`），不挂 stock_id；而 `stockgroupitem` 本身就是闸门，有持仓时删不下去 |
| `groupitemamount` | 同上，键是 `stockgroupitem.id` |

`stockgroupitem` / `stockgroup` 本身也不删：它是闸门（§3.1），不是清理对象。

## 八、验证

### 8.1 PHP 真身逐例对照（9 例：一致 8 / 按设计偏离 1 / 失败 0）

```
cd python && D:/python/python.exe -B auto/_scratch/_php_deletesymbol_compare.py
```

做法：在真库上为每一例建**两个镜像的一次性符号**（`ZZPHP*` 给 PHP、`ZZPY*` 给
Python，fixture 逐行相同），两边各删一次，再用**同一组 SQL** 数"删完还剩几行"。
跑完连残留一起清掉，真实数据一行不碰（基线核对过三遍，见 §8.4）。

PHP 端（`_php_deletesymbol_check.php`）eval 的是 `woody/res/deletesymbol.php` 里
六个函数的**原文**，`DeleteStock()` / `GetAbPairSql()` /
`SqlDeleteStockGroupItemByStockID()` 全是真身 —— 靠 `SqlDieByQuery()` 拦掉
`CREATE TABLE` 才装得起来（pitfalls #145）。

| 用例 | 结果 |
|---|---|
| `clean` | 两边各表剩余行数**完全相等**（6 张清空、6 张残留各 1） |
| `cascade` | 本版残留也清空（PHP 没有这个行为，差异即设计） |
| `gate_groupitem` | **唯一偏离**：`calibrationhistory` PHP 剩 0 / 本版剩 3 |
| `gate_abpair` / `gate_abpair_rev` | 一致（正向、反向各一例） |
| `gate_ahpair` / `gate_adrpair`（反向） / `gate_fundpair` | 一致 |
| `gate_all` | 一致：三道同时命中时，PHP 短路在第一道、本版三道全报，但**两边都不删** |

`gate_groupitem` 那行输出就是 §5 第 1 条的证据：

```
偏离  gate_groupitem   分组持仓命中：PHP 已删校准值再拒绝；本版不动
      · calibrationhistory: PHP 剩 0 / Python 剩 3
```

### 8.2 单测

```
cd python/auto && D:/python/python.exe -B -m unittest discover -s . -p "test_*.py"
cd python/web  && D:/python/python.exe -B -m unittest discover -s tests -t .
```

| 层 | 文件 | 用例 | 钉住的东西 |
|---|---|---|---|
| auto | `test_deletesymbol.py` | 34 | 六张表的顺序、五种闸门（含配对两个方向）、被拦时一行不删、残留三种匹配列、`holdings`/`futurespair` 双身份、`describe()` 五种文案 |
| web | `test_delete_symbol_page.py` | 13 | 预览页渲染、非管理员 403 且库没少、被拦 302 且库没少、成功 302 到 `/stock`、级联清残留、大小写归一、未知符号 200 |

auto **1644 → 1678**（+34），web **1085 → 1098**（+13），`compileall` 干净。

两个假库的分工：auto 用手写假库（按 SQL 特征分派，写语句真改内存），web 用
`web/tests/sqlitedb.py` 真跑 SQL（`%s` → `?`）。后者更硬，所以页面上的行数是
真查出来的，不是桩给的。

### 8.3 真库 smoke（走 HTTP，不是 test_client）

两个一次性符号 `ZZSMOKE1/ZZSMOKE2`，各铺 6 张 PHP 表 + 5 张残留表：

| 动作 | 结果 |
|---|---|
| `GET /stock/ZZSMOKE1/delete` | 200，五道闸门全"通过"，"PHP 会删的 6 张表（共 8 行）" |
| `POST /stock/ZZSMOKE1/delete` | 302 → `/stock`；6 张表归零，5 张残留各留 1 行 |
| `POST /stock/ZZSMOKE2/delete` + `include_orphans=1` | 302 → `/stock`；连残留一起归零 |
| `POST /stock/SPY/delete`（被 fundpair + stockgroupitem 拦） | 302 回预览页；SPY 的 cal 8 / his 34 / net 7 / stock 1 **一行没少** |

页面 smoke：`/stock/ZZSMOKE1/delete`、`/stock/SPY/delete`（拦住态，按钮 disabled）、
`/stock/SPY/edit`（有「危险操作」入口）、`/stock/NOSUCH/delete`（200 + 提示）。

### 8.4 基线核对（这次踩了个坑）

第一版对照脚本跑完没核对，事后发现 `stockhistorydate` / `lastcalibration` /
`fundposition` 各多了 4 行。原因在**清理函数**而不在业务代码：

```python
row = _fetchone(connection, "SELECT id FROM stock WHERE symbol = %s", (symbol,))
ids = [row["id"]] if row else []      # ← 跑完之后 stock 行已经没了 → ids 为空
```

于是按 `id` 匹配的三张表一行都没清。修法是让调用方把 id 显式传进来
（`_cleanup(connection, symbols, all_ids)`）。教训：**"跑完库应该跟跑之前一样"
这句话必须用行数核对，不能靠"脚本里写了 DELETE"**。

现在每次跑都核对这九个数：`stock` 179 / `stockhistorydate` 133 /
`lastcalibration` 3 / `fundposition` 5 / `calibrationhistory` 343 /
`stockhistory` 5627 / `netvaluehistory` 1227 / `stockgroupitem` 6 /
`realtimequote` 173。

## 九、已知缺口

1. **没有 CLI**。页面足够用；批量清理（比如"删掉一批没数据的符号"）以后再说。
   `deletesymbol.plan()/apply()` 本身不依赖 Flask，要写 CLI 直接调。
2. **MyISAM 没有事务**。删到一半出错会留半删状态（`stock` 排最后能少踩一点）。
   页面上写了提示：要可回滚就先跑 `dbtools/backup_db.py`。
3. **没有"撤销"**。`calibrationhistory` 等表被删后只能靠 dump 恢复。
4. **`stock` 表的 `AUTO_INCREMENT` 不会回退**。删掉 id 最大的符号后再建同名的，
   id 会变 —— 库里凡是按 id 存的外部引用（`realtimequote.id` 之类）都依赖
   "删干净"，这也是 §6 那个级联存在的理由。
5. **`fundposition` / `holdings` 之外还有别的"符号被引用"的地方没梳理完**：
   `stock_minute_data`（无关联列）、`convertible_bond_meta`（本地新增表，
   PHP 没有，未纳入）留到以后。
