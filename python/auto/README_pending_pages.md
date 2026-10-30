# 剩余未迁页面清单（"生产端未展示页面"及其它）

> 复核日期：2026-10-30
> 判定依据：`woody/res/*.php` 全量清点 + Flask `url_map` 41 条路由 + 真库/测试客户端实测
> 本文只记**现状与缺口**，已完成项的细节见各自的 `README_*_migration.md`

## 一、这一批指什么

"生产端未展示页面"= **在生产端站点上点不出来、只能手敲 URL 的页面**。
它们不进任何导航（`GetStockMenuLinks()` / `EchoLinks()` 都没有入口），绝大多数
是"某只标的的编辑表单 + 它的 POST 目标"，且要登录（`IsAdmin()`）才执行。

清点口径（`woody/res/` 共 206 个 `.php`）：

| 类别 | 数量 | 说明 |
|---|---|---|
| 按符号生成的详情页 | 131 | `sh513100cn.php` 这类，靠 URL 参数生成，Python 用 `/fund/<symbol>`、`/stock/<symbol>` 一个路由覆盖 |
| **edit 族** | 6 | `_editstockoption.php` 的 16 项分派表里已迁 13 项（一个路由承接）、剩 3 项，另 3 个独立页 |
| **submit 族** | 4 | 表单 POST 目标（其中 `submitstockoptions` 的 11 个分支已由编辑页承接） |
| 其它 | 50 | 分组页 / 历史页 / 账号页 / 工具页 |

Flask 侧现有 **41 条路由**（`create_app().url_map`）。

## 二、已迁的部分（7 个入口）

| PHP 页面 | Python | 备注 |
|---|---|---|
| `editquarterreportcn.php` | `GET/POST /holdings/quarter/<symbol>` | `QuarterReportSql(DailyStringSql)`，填 `0` = 删除 |
| `editstockgroupcn.php` | `/admin/groups`、`/admin/group/<id>`、`POST /admin/group/submit` | 账号体系暂缓，固定演示成员 |
| `editstocktransactioncn.php` | `GET /transactions/edit` | `core/edittransactionform.py` |
| **edit 族 13 项** | `GET|POST /stock/<symbol>/edit/<option>` | 一个路由顶 13 个页面，**索引页** `GET /stock/<symbol>/edit`（PHP 没有），见 `README_stock_option_edit_migration.md` |
| `submitgroup.php` | `POST /admin/group/submit` | |
| `submitholdings.php` | `POST /holdings/submit` | 穷举页"确认更新持仓"按钮 |
| `submittransaction.php` | `POST /transactions/submit` | `core/submittransaction.py` 七动作 |
| `submitstockoptions.php` | **只迁了 REPORT 分支** | 16 项里另 13 项的分派已在 `/stock/<symbol>/edit/<option>`，但**不是**走这个 POST 入口 |

## 三、edit 族：还剩 3 项

分派表 `GetStockOptionArray()`（`php/stocklink.php:255`）共 16 项，
减去已迁的 13 项 = **3 项未迁**（其中 2 项已有专门入口）。16 个页面文件都是 4 行壳子：

```php
<?php
require('php/_editstockoption.php');      // 分派 + EchoAll
require('../../php/ui/_editcn.php');
```

真正的内容在 `_editstockoptionform.php`（407 行）与写入口
`_submitstockoptions.php` 的 `_updateOptionDailySql()`。

| 页面 | 动作（`STOCK_OPTION_*` 原文） | 落库表 | 状态 |
|---|---|---|---|
| `editcalibrationcn.php` | 手工校准值 | `calibrationhistory` + `lastcalibration` | **已迁**（估算值换算成校准值） |
| `editfundcn.php` | 修改对应配对代码和仓位 | `fundpair` + `fundposition` | **已迁** |
| `editnetvaluecn.php` | 修改净值 | `netvaluehistory` | **已迁** |
| `editpremiumcn.php` | 期货升水 | `futurepremium` | **已迁** |
| `editsharesdiffcn.php` | 场内新增(万) | `sharesdiff` | **已迁**（建表种子已补） |
| `editstockcn.php` | 修改股票说明 | `stock.name` | **已迁** |
| `editstockadrcn.php` | 修改H股对应ADR代码 | `adrpair` + `fundposition` | **已迁** |
| `editstockahcn.php` | 修改A股对应H股代码 | `ahpair` | **已迁** |
| `editstockamountcn.php` | 基金申购金额 | `groupitemamount` | 未迁（账号体系） |
| `editstockclosecn.php` | 更新收盘价 | `stockhistory` | **已迁**（只改已有行） |
| `editstockdividendcn.php` | 分红 | `stockdividend` + 复权重算 | **已迁**（连带改校准值 / 净值 / 复权序列） |
| `editstockemacn.php` | 修改200/50日EMA | `stockema200` / `stockema50` | **已迁** |
| `editstockhacn.php` | 修改H股对应A股代码 | `ahpair` | **已迁** |
| `editstockholdingscn.php` | 修改持仓 | `holdings` + `holdingsdate` + `fundposition` | 已有入口（穷举持仓页） |
| `editstocksplitcn.php` | 拆股或合股 | `stocksplit` + `stockhistory` 全序列 | **已迁**（连带改交易流水 / 校准值 / 复权序列 / 删 SMA 缓存） |

**底座两个（均已补齐）**：

* `sql/historytable.py::DailyCloseSql` 原来的写方法只长在 `DailyStringSql`
  （季报那条线）上，现已按 `php/sql/sqldailyclose.php` 补齐到基类，并连带补了
  `DailyTimeSql`（多 `time`）、`CalibrationSql`（`num` 重置/累加）、
  `StockHistorySql.write_history`（多 `volume`/`adjclose`）。注意
  **MIN_FLOAT_VAL 比较**与 **INSERT 分支的周末闸门** —— 与 `DailyStringSql`
  的差异见 pitfalls #124。详见 `README_dailyclose_write_migration.md`（22 例对照）。
* `sql/optionstore.py`：`ValStore` / `IntStore`（字符串读写在**这层**）/
  `PairStore` / `StockPairStore` + 六张日频小表。19 个操作与真 PHP 逐例一致，
  见 `README_stock_option_edit_migration.md`。

**未迁的 3 项按依赖分组**（理由也写在页面 `note` 里，不在代码里藏）：

1. 已有专门页：`editquarterreport`、`editstockholdings`；
2. 账号体系：`editstockamount`（要先换 `stockgroupitem.id`）。

原本第三类"要连带重算"的 `editstockdividend` / `editstocksplit` **已迁**：
写一行之后重算复权序列与校准值的那条链路收在
`_write_stockdividend` / `_write_stocksplit` 里（连带净值、拆股流水、SMA 缓存），
细节与 19 例对照见 `README_stock_option_edit_migration.md` §七 / §九。
`editcalibration` 更早迁掉：它的换算只依赖当天的中间价与基金净值，不需要重算历史。

## 四、submit 族：还剩 3 个页面（+ `submitstockoptions` 的 6 个分支）

| 页面 | 干什么 | 状态 |
|---|---|---|
| `submitstockoptions.php` | `_editstockoptionform` 的 POST 目标，按选项分派到 15 张表 | 16 项里 **13 项**已由 `/stock/<symbol>/edit/<option>` 的 POST 承接（走服务层分派，不复用这个文件）；剩 3 项随各自的页面一起做 |
| `submitdividend.php` | 抓 SPDR 官方分红（`ssga.com` JSON）写 `stockdividend` | 未迁 |
| `submitnetvalue.php` | 抓 Invesco / SPDR 官方净值写 `netvaluehistory` | 未迁 |
| `submithistory.php` | 管理员刷新 Yahoo 历史图表（`UpdateYahooHistoryChart`） | 未迁 |

## 五、其它未展示的杂项页

| 页面 | 干什么 | 状态 | 建议 |
|---|---|---|---|
| `deletesymbol.php` | 删标的（`php/deletestock.php`，先查 AB/AH 配对再连锁删） | 未迁 | 值得迁，但现在删数据靠 SQL |
| `uploadfile.php` | 上传 Yahoo 历史行情 CSV 到 `stockhistory` | 未迁 | 低优先（本地用 `dbtools/seed_*` 顶替） |
| `debugcn.php` | 访问日志/恶意 IP 统计页（依赖 nginx 日志） | 未迁 | **不建议迁**，PHP 专属运维页 |

## 六、不属于这批、但也没迁的（粗查）

| 类别 | 缺口 | 证据 |
|---|---|---|
| **行业分类/股票菜单分组页** | 只迁了 `qdiicn.php`（`/qdii`）与两张"基金指数对照表"（`/fund/qdiilof`、`/fund/qdiietf`）。`chinaindexcn` / `chinainternetcn` / `biotechcn` / `commoditycn` / `chinafuturecn` / `hangsengcn` / `hstechcn` / `hsharescn` / `mscius50cn` / `oilfundcn` / `qqqfundcn` / `spyfundcn` / `btbondcn` / `qdiihkcn` / `qdiijpcn` / `qdiieucn` / `qdiimixcn` 共 17 个都**没有** | `calc/fundgroups.py` 的 `FUND_GROUPS` 只有 2 个键；实测 `/fund/qdiihk` 等 22 个名字全部 **404** |
| **对比页** | `ahcomparecn.php` / `abcomparecn.php` / `adrhcomparecn.php` / `ahhistorycn.php` | 无对应路由；配对数据只出现在 `/stock/<symbol>` 的段落里 |
| **工具页** | `autotractorcn.php`（拖拉机自动化）/ `overnightcn.php`（义工群对比）/ `overnightcnhcn.php`（对冲计算器）/ `rotationtradingcn.php`（轮动交易） | 无对应路由 |
| **账号体系** | `fundaccountcn.php`（`/account` 目前是演示数据，未切真库）；`member` 表 + 登录 + 分组可见性整体暂缓 | `WEB_MIGRATION_PLAN.md` T4b |
| **博客/杂文** | `cateyescn.php`、`thanousparadoxcn.php`、`woody/blog/*` | 站点内容，不在迁移范围 |

## 七、建议的推进顺序

1. ~~**补 `DailyCloseSql` 写方法**~~ —— **2026-10-30 已完成**，见
   `README_dailyclose_write_migration.md`；
2. ~~**一个通用编辑页 `/stock/<symbol>/edit/<option>`**~~ —— 2026-10-30 已完成
   （16 项迁 13 项），见 `README_stock_option_edit_migration.md`；
3. ~~**非日频四张表补 Sql 类 + 建缺表**~~ —— `sql/optionstore.py` 已收口
   （`ValStore`/`IntStore`/`PairStore` + 六张日频小表），`sharesdiff`/
   `stockdividend`/`stocksplit` 三张表的 DDL 由 `dbtools/seed_option_tables.py`
   补上（演示库 30 → 33 张）。三张表后来都有页面写了；
4. **剩 3 项按依赖推进**：`editstockamount` 等账号体系（另 2 项已有专门页）。
   ~~`editstockdividend` / `editstocksplit`~~ 已于 2026-10-30 迁完 ——
   "改一行 → 重算复权序列与校准值"已在服务层落地
   （`_dividend_plan` / `_recalc_adjclose` / `_insert_split_transactions`）；
5. 行业分组页：`FUND_GROUPS` 加键即可复用现有 `/fund/<group>` 全套服务；
6. `deletesymbol.php` 最后做（要连带清 8 张表，风险最高）。

## 八、怎么自己复核一遍

```python
# 1) 页面清点
import os, re
PAGES = sorted(f for f in os.listdir('woody/res') if f.endswith('.php'))
SYM = re.compile(r'^(sh|sz|hk|us)[0-9a-z]+cn\.php$', re.I)
print(len(PAGES), len([p for p in PAGES if SYM.match(p)]))

# 2) 路由清点（含 404 实测）
from web.app import create_app
c = create_app({'TESTING': True}).test_client()
for p in ('/fund/qdiihk', '/fund/biotech', '/history/stockhistory/SZ161130'):
    print(p, c.get(p).status_code)
```

单测：`python -B -m unittest discover -s tests -t .`（web 层）；
`python -B -m unittest discover -s . -p "test_*.py"`（auto 层）。
