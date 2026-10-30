# 剩余未迁页面清单（"生产端未展示页面"及其它）

> 复核日期：2026-10-30
> 判定依据：`woody/res/*.php` 全量清点 + Flask `url_map` 44 条路由 + 真库/测试客户端实测
> 本文只记**现状与缺口**，已完成项的细节见各自的 `README_*_migration.md`

## 一、这一批指什么

"生产端未展示页面"= **在生产端站点上点不出来、只能手敲 URL 的页面**。
它们不进任何导航（`GetStockMenuLinks()` / `EchoLinks()` 都没有入口），绝大多数
是"某只标的的编辑表单 + 它的 POST 目标"，且要登录（`IsAdmin()`）才执行。

清点口径（`woody/res/` 共 206 个 `.php`）：

| 类别 | 数量 | 说明 |
|---|---|---|
| 按符号生成的详情页 | 131 | `sh513100cn.php` 这类，靠 URL 参数生成，Python 用 `/fund/<symbol>`、`/stock/<symbol>` 一个路由覆盖 |
| **edit 族** | 3 | `_editstockoption.php` 的 16 项分派表**全部有去处**（14 项由同一个路由承担、2 项跳专门页），另 3 个独立页 |
| **submit 族** | 4 | 表单 POST 目标：3 个抓外部数据的页已迁（`POST /submit/xxx`），`submitstockoptions` 的分支由编辑页承接 |
| 分类页 | 16 | `biotechcn.php` 一族 4 行壳子，**已全部迁掉**（一个注册表 + 一个视图），见 `README_category_pages_migration.md` |
| 其它 | 34 | 分组页 / 历史页 / 账号页 / 工具页 |

Flask 侧现有 **44 条路由**（`create_app().url_map`）。

## 二、已迁的部分（9 个入口）

| PHP 页面 | Python | 备注 |
|---|---|---|
| `editquarterreportcn.php` | `GET/POST /holdings/quarter/<symbol>` | `QuarterReportSql(DailyStringSql)`，填 `0` = 删除 |
| `editstockgroupcn.php` | `/admin/groups`、`/admin/group/<id>`、`POST /admin/group/submit` | 账号体系暂缓，固定演示成员 |
| `editstocktransactioncn.php` | `GET /transactions/edit` | `core/edittransactionform.py` |
| **edit 族 14 项** | `GET|POST /stock/<symbol>/edit/<option>` | 一个路由顶 14 个页面（另 2 项 302 到季报/持仓的专门页），**索引页** `GET /stock/<symbol>/edit`（PHP 没有），见 `README_stock_option_edit_migration.md` |
| `submitgroup.php` | `POST /admin/group/submit` | |
| `submitholdings.php` | `POST /holdings/submit` | 穷举页"确认更新持仓"按钮 |
| `submittransaction.php` | `POST /transactions/submit` | `core/submittransaction.py` 七动作 |
| `submitstockoptions.php` | **只迁了 REPORT 分支** | 16 项里另 13 项的分派已在 `/stock/<symbol>/edit/<option>`，但**不是**走这个 POST 入口 |
| `submitdividend.php` / `submitnetvalue.php` / `submithistory.php` | `GET /submit`（工具页）+ `POST /submit/<name>`（`dividend` / `netvalue` / `history`） | 三页在 PHP 里都零输出、且入口链接点不到，所以合成一张工具页；细节见 `README_submit_pages_migration.md` |
| **分类页 16 个** | `GET /category/<name>`，另有短路径 `GET /qdii` | 16 个 4 行壳子全部落到 `_mystockgroup.php`，所以做成一个注册表 + 一个视图；`_stocklink.php::GetCategoryArray()` 的数组与 `_getTitleStr()` / `_getMetaDescriptionStr()` 的文本都逐例对照过，见 `README_category_pages_migration.md` |

## 三、edit 族：16 项全部有去处（0 项未迁）

分派表 `GetStockOptionArray()`（`php/stocklink.php:255`）共 16 项：
**14 项**由 `/stock/<symbol>/edit/<option>` 读写，**2 项**（季报 / 持仓）跳各自的
专门页。16 个页面文件都是 4 行壳子：

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
| `editstockamountcn.php` | 基金申购金额 | `groupitemamount` | **已迁**（会员级：邮箱 → member → 分组名=符号 → 组项） |
| `editstockclosecn.php` | 更新收盘价 | `stockhistory` | **已迁**（只改已有行） |
| `editstockdividendcn.php` | 分红 | `stockdividend` + 复权重算 | **已迁**（连带改校准值 / 净值 / 复权序列） |
| `editstockemacn.php` | 修改200/50日EMA | `stockema200` / `stockema50` | **已迁** |
| `editstockhacn.php` | 修改H股对应A股代码 | `ahpair` | **已迁** |
| `editstockholdingscn.php` | 修改持仓 | `holdings` + `holdingsdate` + `fundposition` | **跳专门页**（穷举持仓页的「确认更新持仓」） |
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

**最后三项怎么收的**（都不在代码里藏理由）：

1. **跳转**：`editquarterreport` → `/holdings/quarter/<symbol>`；
   `editstockholdings` → `/holdings/exhaustive/<symbol>`。本页 GET/POST 都 302，
   **不重复实现**写入口 —— 季报那张表是 `DailyStringSql`（口径与日频表不同，#124），
   持仓还要连带 `fundposition`；
2. **会员级**：`editstockamount` 按 PHP 原样写 `groupitemamount`：
   邮箱 → `member.id` → 分组名**恰好等于符号**的那个分组 → 组项 → `num`。
   PHP 这一支是 16 项里唯一没有 `$bAdmin` 的，所以权限只到"有登录身份"。

细节与对照见 `README_stock_option_edit_migration.md` §八（申购金额 / 跳转）、
§七（分红拆股）、§十（19 + 14 + 19 + 11 例）。

## 四、submit 族：三个抓取页 2026-10-30 迁完（`submitstockoptions` 仍有 2 个分支在专门页）

| 页面 | 干什么 | 状态 |
|---|---|---|
| `submitstockoptions.php` | `_editstockoptionform` 的 POST 目标，按选项分派到 15 张表 | 16 项里 **14 项**已由 `/stock/<symbol>/edit/<option>` 的 POST 承接（走服务层分派，不复用这个文件）；另 2 项在专门页提交 |
| `submitdividend.php` | 抓 SPDR / Proshares 分红 JSON 写 `stockdividend` | **已迁** → `POST /submit/dividend` |
| `submitnetvalue.php` | 抓 Invesco JSON / SPDR xlsx 写 `netvaluehistory`（+`shareshistory`） | **已迁** → `POST /submit/netvalue` |
| `submithistory.php` | 管理员刷新历史日线（`UpdateYahooHistoryChart`） | **已迁** → `POST /submit/history`（Yahoo 已 403，改抓新浪） |

三页在 PHP 里都没有可见输出（`AdminProcess()` 跑完就 `SwitchToSess()`），
而且 `submitdividend` / `submitnetvalue` 的管理员链接挂在两页未迁的展示页上、
`submithistory` 的链接函数 `GetUpdateStockHistoryLink()` **没有任何调用点** ——
所以本地合成一张工具页 `GET /submit` + 三个 POST 端点。细节与对照见
`README_submit_pages_migration.md`。

## 五、其它未展示的杂项页

| 页面 | 干什么 | 状态 | 建议 |
|---|---|---|---|
| `deletesymbol.php` | 删标的（`php/deletestock.php`，先查 AB/AH 配对再连锁删） | 未迁 | 值得迁，但现在删数据靠 SQL |
| `uploadfile.php` | 上传 Yahoo 历史行情 CSV 到 `stockhistory` | 未迁 | 低优先（本地用 `dbtools/seed_*` 顶替） |
| `debugcn.php` | 访问日志/恶意 IP 统计页（依赖 nginx 日志） | 未迁 | **不建议迁**，PHP 专属运维页 |

## 六、不属于这批、但也没迁的（粗查）

| 类别 | 缺口 | 证据 |
|---|---|---|
| **行业分类页** | **2026-10-30 已全部迁掉**：`chinaindexcn` / `chinainternetcn` / `biotechcn` / `commoditycn` / `chinafuturecn` / `hangsengcn` / `hstechcn` / `hsharescn` / `mscius50cn` / `oilfundcn` / `qqqfundcn` / `spyfundcn` / `qdiihkcn` / `qdiijpcn` / `qdiieucn` / `qdiimixcn` 共 16 个 → `/category/<name>`（`qdiicn.php` 另有短路径 `/qdii`） | 见 `README_category_pages_migration.md`。**原判断是错的**：这里曾写成"`FUND_GROUPS` 加个键即可复用 `/fund/<group>`"，实测这些页根本不是"基金指数对照表"，而是 `_mystockgroup.php` 驱动的分类页（参考数据表 + 估值表）；`/fund/qdiihk` 之类的 404 是**另一个**东西（那 22 个名字本就不是分组名）。`btbondcn.php` 也不属于这一族（是公司介绍页，`_res.php`）。数组随后按生产端对齐（27 / 38 / 15），见该文 §七；**剩下的缺口是数据不是页面**：`chinaindex` 的 27 只 A 股 ETF 在演示库里连 `stock` 行都没有 |
| **对比页** | `ahcomparecn.php` / `abcomparecn.php` / `adrhcomparecn.php` / `ahhistorycn.php` | 无对应路由；配对数据只出现在 `/stock/<symbol>` 的段落里。**注意**分类页家族里 `GetCategoryArray()` 还有 `ahcompare` / `abcompare` / `adrhcompare` 三个键，它们的数组来自配对表、还要渲染 AB/AH/ADR 三个段落，比这 16 个基金分类页重，未进本轮 |
| **工具页** | `autotractorcn.php`（拖拉机自动化）/ `overnightcn.php`（义工群对比）/ `overnightcnhcn.php`（对冲计算器）/ `rotationtradingcn.php`（轮动交易） | 无对应路由 |
| **账号体系** | `fundaccountcn.php`（`/account` 目前是演示数据，未切真库）；`member` 表 + 登录 + 分组可见性整体暂缓 | `WEB_MIGRATION_PLAN.md` T4b |
| **博客/杂文** | `cateyescn.php`、`thanousparadoxcn.php`、`woody/blog/*` | 站点内容，不在迁移范围 |

## 七、建议的推进顺序

1. ~~**补 `DailyCloseSql` 写方法**~~ —— **2026-10-30 已完成**，见
   `README_dailyclose_write_migration.md`；
2. ~~**一个通用编辑页 `/stock/<symbol>/edit/<option>`**~~ —— 2026-10-30 已完成
   （16 项：14 项一个路由、2 项跳专门页），见 `README_stock_option_edit_migration.md`；
3. ~~**非日频四张表补 Sql 类 + 建缺表**~~ —— `sql/optionstore.py` 已收口
   （`ValStore`/`IntStore`/`PairStore` + 六张日频小表），`sharesdiff`/
   `stockdividend`/`stocksplit` 三张表的 DDL 由 `dbtools/seed_option_tables.py`
   补上（演示库 30 → 33 张）。三张表后来都有页面写了；
4. ~~**剩 3 项按依赖推进**~~ —— 2026-10-30 收口：`editstockamount` 走**会员级**
   写入口（`SqlGetIdByEmail` + `sql_get_my_stock_group_item_id`），季报 / 持仓
   **302 到专门页**；`editstockdividend` / `editstocksplit` 同日迁完
   （`_dividend_plan` / `_recalc_adjclose` / `_insert_split_transactions`）；
5. ~~**行业分组页：`FUND_GROUPS` 加键即可复用 `/fund/<group>`**~~ —— 2026-10-30
   已做，但**做法与原判断不同**：这 16 个页面不是对照表，而是
   `_mystockgroup.php` 的分类页，所以新建了 `category_page_service` 注册表 +
   `GET /category/<name>` 一个视图，见 `README_category_pages_migration.md`；
   同日查出并修掉 3 个分类的数组落后生产端（`chinaindex` 27 / `qdiihk` 38 /
   `qdiimix` 15，见该文 §七）。现在 `_prod_category_check.py` 是 17/17 一致；
6. **`deletesymbol.php` 仍排在最后**（要连带清 8 张表，风险最高）；
   ~~`submitdividend` / `submitnetvalue` / `submithistory` 三个抓外部 JSON/XLS 的页未动~~
   —— **2026-10-30 已迁**（`GET /submit` + 三个 POST），对照 19 例、真库 smoke 见
   `README_submit_pages_migration.md`。左侧真正的缺口只剩 `deletesymbol` 与
   `uploadfile`（后者本地用 `dbtools/seed_*` 顶替，不建议迁）。

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
