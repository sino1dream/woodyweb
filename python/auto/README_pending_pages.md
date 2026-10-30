# 剩余未迁页面清单（"生产端未展示页面"及其它）

> 复核日期：2026-10-30
> 判定依据：`woody/res/*.php` 全量清点 + Flask `url_map` 39 条路由 + 真库/测试客户端实测
> 本文只记**现状与缺口**，已完成项的细节见各自的 `README_*_migration.md`

## 一、这一批指什么

"生产端未展示页面"= **在生产端站点上点不出来、只能手敲 URL 的页面**。
它们不进任何导航（`GetStockMenuLinks()` / `EchoLinks()` 都没有入口），绝大多数
是"某只标的的编辑表单 + 它的 POST 目标"，且要登录（`IsAdmin()`）才执行。

清点口径（`woody/res/` 共 206 个 `.php`）：

| 类别 | 数量 | 说明 |
|---|---|---|
| 按符号生成的详情页 | 131 | `sh513100cn.php` 这类，靠 URL 参数生成，Python 用 `/fund/<symbol>`、`/stock/<symbol>` 一个路由覆盖 |
| **edit 族** | 18 | 15 个是 `_editstockoption.php` 的 16 项分派表（去掉已迁的季报），另 3 个独立 |
| **submit 族** | 7 | 表单 POST 目标 |
| 其它 | 50 | 分组页 / 历史页 / 账号页 / 工具页 |

Flask 侧现有 **39 条路由**（`create_app().url_map`）。

## 二、已迁的部分（6 个入口）

| PHP 页面 | Python | 备注 |
|---|---|---|
| `editquarterreportcn.php` | `GET/POST /holdings/quarter/<symbol>` | `QuarterReportSql(DailyStringSql)`，填 `0` = 删除 |
| `editstockgroupcn.php` | `/admin/groups`、`/admin/group/<id>`、`POST /admin/group/submit` | 账号体系暂缓，固定演示成员 |
| `editstocktransactioncn.php` | `GET /transactions/edit` | `core/edittransactionform.py` |
| `submitgroup.php` | `POST /admin/group/submit` | |
| `submitholdings.php` | `POST /holdings/submit` | 穷举页"确认更新持仓"按钮 |
| `submittransaction.php` | `POST /transactions/submit` | `core/submittransaction.py` 七动作 |
| `submitstockoptions.php` | **只迁了 REPORT 分支** | 其余 15 个选项的写回都没有 |

## 三、edit 族：还剩 15 项

分派表 `GetStockOptionArray()`（`php/stocklink.php:255`）共 16 项，减去已迁的
`editquarterreport` = **15 项未迁**。16 个页面文件都是 4 行壳子：

```php
<?php
require('php/_editstockoption.php');      // 分派 + EchoAll
require('../../php/ui/_editcn.php');
```

真正的内容在 `_editstockoptionform.php`（407 行）与写入口
`_submitstockoptions.php` 的 `_updateOptionDailySql()`。

| 页面 | 动作（`STOCK_OPTION_*` 原文） | 落库表 | 数据层 | 状态 |
|---|---|---|---|---|
| `editcalibrationcn.php` | 手工校准值 | `calibrationhistory` | 缺写方法 | 未迁 |
| `editfundcn.php` | 修改对应配对代码和仓位 | `fundpair` + `fundposition` | 缺 | 未迁 |
| `editnetvaluecn.php` | 修改净值 | `netvaluehistory` | 缺写方法 | 未迁 |
| `editpremiumcn.php` | 期货升水 | `futurepremium` | 缺 | 未迁 |
| `editsharesdiffcn.php` | 场内新增(万) | `sharesdiff` + `shareshistory` | 缺（表在演示库都没有） | 未迁 |
| `editstockcn.php` | 修改股票说明 | `stock.name` | 有（`StockSql`） | 未迁 |
| `editstockadrcn.php` | 修改H股对应ADR代码 | `adrpair` | 缺 | 未迁 |
| `editstockahcn.php` | 修改A股对应H股代码 | `ahpair` | 缺 | 未迁 |
| `editstockamountcn.php` | 基金申购金额 | `groupitemamount` | 缺 | 未迁 |
| `editstockclosecn.php` | 更新收盘价 | `stockhistory` | 底座已补（`write_history`） | 未迁 |
| `editstockdividendcn.php` | 分红 | `stockdividend` | 缺（表在演示库都没有） | 未迁 |
| `editstockemacn.php` | 修改200/50日EMA | `stockema200` / `stockema50` | 缺 | 未迁 |
| `editstockhacn.php` | 修改H股对应A股代码 | `ahpair` | 缺 | 未迁 |
| `editstockholdingscn.php` | 修改持仓 | `holdings` + `holdingsdate` + `fundposition` | 部分（`holdingsupdater` 有写） | 未迁 |
| `editstocksplitcn.php` | 拆股或合股 | `stockhistory`（复权价） | 底座已补 | 未迁 |

**共同底座已补齐（2026-10-30）**：`sql/historytable.py::DailyCloseSql` 原来只有读方法，
`insert_daily/update_daily/delete_by_date/write_daily` 只长在 `DailyStringSql`
（季报那条线）上。现已按 `php/sql/sqldailyclose.php` 补齐到基类，并连带补了
`DailyTimeSql`（多 `time`）、`CalibrationSql`（`num` 重置/累加）、
`StockHistorySql.write_history`（多 `volume`/`adjclose`，**不能用父类方法**）。
注意 **MIN_FLOAT_VAL 比较**与 **INSERT 分支的周末闸门** —— 与 `DailyStringSql`
的差异见 pitfalls #124。详见 `README_dailyclose_write_migration.md`
（含 22 例真实 PHP 逐例对照）。

## 四、submit 族：还剩 3 个页面 + 1 个页面的 15 个分支

| 页面 | 干什么 | 状态 |
|---|---|---|
| `submitstockoptions.php` | `_editstockoptionform` 的 POST 目标，按选项分派到 15 张表 | **只迁了季报一个分支** |
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
2. **一个通用编辑页 `/stock/<symbol>/edit/<option>`**：按 `GetStockOptionArray()`
   的表驱动，一套模板 + 一个 POST，比 15 个独立页面省事；`submitstockoptions.php`
   的 `_updateOptionDailySql()` 语义（`empty()` → 删除）已有 `_php_empty()` 可复用；
3. 非日频的四张表（`futurepremium` / `sharesdiff` / `stockdividend` / 配对三表 +
   `groupitemamount`）各补一个小 Sql 类，**演示库里 `sharesdiff`、`stockdividend`
   两张表根本没有**，要先加 DDL 种子；
4. 行业分组页：`FUND_GROUPS` 加键即可复用现有 `/fund/<group>` 全套服务；
5. `deletesymbol.php` 最后做（要连带清 8 张表，风险最高）。

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
