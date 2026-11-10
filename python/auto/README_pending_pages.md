# 剩余未迁页面清单（"生产端未展示页面"及其它）

> 复核日期：2026-11-05
> 判定依据：`woody/res/*.php` 全量清点 + Flask `url_map` 63 条路由 + 真库/测试客户端实测
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
| 分类页 | 16 | `biotechcn.php` 一族 4 行壳子，**已全部迁掉**（一个注册表 + 一个视图），见 `README_category_pages_migration.md`；2026-11-01 又补上 `lofcn.php` / `overnightcn.php` 两个同构页（注册表 17 → 19） |
| 其它 | 34 | 分组页 / 历史页 / 账号页 / 工具页 |

Flask 侧现有 **63 条路由**（`create_app().url_map`，含 `/static/<path>` 一条）。

**旧地址已接上（2026-11-05）**：生产端"文件名就是地址"，本地换成了按功能分组的
路径，于是 195 个 `*cn.php` 全部 404（用户按生产端习惯敲
`/woody/res/sz161226cn.php` 撞到的就是这个）。现在
`GET /woody/res/<path:page>` 统一 302 过去，206 个页面里 **196 个能跳、10 个
明确没有对应**（404 + 原因）。映射表在 `web/services/legacy_urls.py`，**分类页从
`CATEGORY_PAGES` 反推、edit 族从 `STOCK_OPTIONS` 生成**，不另抄清单。
见 `README_legacy_urls.md`。

## 二、已迁的部分（16 个入口）

| PHP 页面 | Python | 备注 |
|---|---|---|
| `editquarterreportcn.php` | `GET /holdings/quarter`（本版新增的基金代码索引）+ `GET/POST /holdings/quarter/<symbol>` | `QuarterReportSql(DailyStringSql)`，填 `0` = 删除；索引页清单 32 只，见 §五 |
| `editstockgroupcn.php` | `/admin/groups`、`/admin/group/<id>`、`POST /admin/group/submit` | 账号认证 2026-11-02 已接上（`/account/login`），但这一组页面仍固定演示成员 `DEMO_MEMBER_ID`；要按登录会员取分组得等 §八 第 1 项 |
| `editstocktransactioncn.php` | `GET /transactions/edit` | `core/edittransactionform.py` |
| **edit 族 14 项** | `GET|POST /stock/<symbol>/edit/<option>` | 一个路由顶 14 个页面（另 2 项 302 到季报/持仓的专门页），**索引页** `GET /stock/<symbol>/edit`（PHP 没有），见 `README_stock_option_edit_migration.md` |
| `submitgroup.php` | `POST /admin/group/submit` | |
| `submitholdings.php` | `POST /holdings/submit` | 穷举页"确认更新持仓"按钮 |
| `submittransaction.php` | `POST /transactions/submit` | `core/submittransaction.py` 七动作 |
| `submitstockoptions.php` | **只迁了 REPORT 分支** | 16 项里另 13 项的分派已在 `/stock/<symbol>/edit/<option>`，但**不是**走这个 POST 入口 |
| `submitdividend.php` / `submitnetvalue.php` / `submithistory.php` | `GET /submit`（工具页）+ `POST /submit/<name>`（`dividend` / `netvalue` / `history`） | 三页在 PHP 里都零输出、且入口链接点不到，所以合成一张工具页；细节见 `README_submit_pages_migration.md` |
| `deletesymbol.php` | `GET/POST /stock/<symbol>/delete` | 五道闸门 + 6 张表；**PHP 漏清 15 张残留表**，本版可显式级联；见 `README_deletesymbol_migration.md` |
| **分类页 19 个** | `GET /category/<name>`，另有短路径 `GET /qdii` | 19 个 4 行壳子全部落到 `_mystockgroup.php`，所以做成一个注册表 + 一个视图；`_stocklink.php::GetCategoryArray()` 的数组与 `_getTitleStr()` / `_getMetaDescriptionStr()` 的文本都逐例对照过，见 `README_category_pages_migration.md`。2026-11-01 补上 `lofcn.php` / `overnightcn.php`（17 → 19），见 `README_autotractor_migration.md` §二 |
| **对比页 4 个** | `GET /abcompare`、`GET /ahcompare`、`GET /adrhcompare`、`GET /ahhistory/<symbol>` | 三个对比页与分类页**共用同一个 `EchoAll()`**，差别只是符号数组来自 `abpair` / `ahpair` / `adrpair` 三张配对表；`ahhistorycn.php` 另走 `_ahhistory.php`。PHP 真身对照 **39 例全一致**，见 `README_compare_pages_migration.md`。**演示库三张表都是 0 行**（且没有 A 股与 H 股标的），所以本地恒空态 |
| **账号认证 9 条** | `GET/POST /account/<action>`（6 个动作）、`GET /account/profile`、`GET/POST /account/editprofile`、`GET /account/logout` | `account/` 目录 57 个文件里只有 4 个是真的实现（`_editemail.php` / `_editemailform.php` / `_profile.php` / `_editprofileform.php`），其余是 4 行壳子或统计工具页。六个邮箱表单页合成一张 `PAGE_CONFIG` 表；session 键照 PHP（`SESS_ID` + `email`）。**前置**：`member` 表补 `password`/`ip` 两列 + 建 `profile` 表（`dbtools/patch_member_auth.sql`）。见 `README_member_auth_migration.md` |
| `etfdividendcn.php` | `GET /stock/<symbol>/etfdividend` | 分红数据页：`stockdividend` + `stockhistory` 四列（日期/分红/价格/比例%）。两道闸门（无 ref、`CountNetValue()==0`）+ 单行跳过。**外链只留 Sina，Yahoo 那一支去掉**（该域名对大陆返回服务停止公告）。对照 9 项全一致，见 `README_etfdividend_migration.md`。**演示库 `stockdividend` 4 行全是孤儿 `stock_id`**，所以本地最好结果是"表头 + 空行" |
| `rotationtradingcn.php` | `GET/POST /stock/<symbol>/rotationtrading` | 轮动交易计算器：把持有的 QDII 换成同指数的另一只，给出可成交价/量、对冲值与建议股数。四族（XOP / XBI / 纳指匹配 / 标普匹配）+ 三层闸门。**标普那一支刻意放宽到 `SpyQdii`**，修 PHP "`SZ159655` 有入口链接、页面全空"的判据不齐。对照 43 条断言全通过（含"页面数组 PHP 为空 / 本版 4 只"这条刻意差异），见 `README_rotationtrading_migration.md`。顺手修了 `apply_snapshot()` 在价格为 0 时丢掉盘口的老问题 |
| `overnightcnhcn.php` | `GET/POST /overnightcnh` | 义工群对冲计算器：把一笔要平衡的离岸人民币折成 23 只 QDII 各自的买卖股数，再换算成对冲标的（QQQ / SPY / XOP …）的股数。输入串末几位决定口径（`F` 申购 / `MCL` `MGC` 期货 / 无后缀纯金额），首字符 `-` = 卖。两套「建议对冲操作」文案（对冲值口径 / 季报成分股逐股）。对照 **617 条断言全通过**，见 `README_overnight_cnh_migration.md`。**演示库 11 只没有官方净值、2 只期货没有快照**，所以本地要挑纯金额（如 `?overnightcnh=100000`）才看得到全部 23 行 |
| `autotractorcn.php` | `GET /autotractor` | 拖拉机自动化说明页：纯图文（安装步骤 4 条、操作 7 种、客户号 5 条、已知问题 10 条），没有数字也没有数据库。PHP 那边全在 `EchoAll()` 里拼字符串，所以做成"按 PHP echo 顺序排的 blocks + 按类型分派渲染"。**不渲染两张界面截图**（PHP 用 GD 现生成缩放图）。对照 13 段 / 26 条全一致，见 `README_autotractor_migration.md` |

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
| `deletesymbol.php` | 删标的（`php/deletestock.php`，先查五道闸门再连锁删） | **已迁** | `GET/POST /stock/<symbol>/delete`（先预览再执行），见 `README_deletesymbol_migration.md` |
| ~~后台符号管理~~ | `stock` 表的增 / 删 / 改名 | **本地新增（PHP 没有这一页）** | `GET /admin/symbols`、`/admin/symbol/new`、`/admin/symbol/<symbol>`（关联数据 + 删除确认）、`POST /admin/symbol/submit`、`POST /admin/symbol/<symbol>/delete`。PHP 那边三件事是散的：改名在 `editstock.php`、删除在 `deletesymbol.php`、「新增」只是访问页面时 `StockGetReference` 的副作用。删除口径全部复用 `auto/deletesymbol.py`（闸门 + 6 张主表 + 可选残留表）。起因：`/category/chinafuture` 六只基金全显示「库里无此符号」，而本地没有那个"顺手入库"的副作用，缺一个显式入口 |
| ~~后台控制台~~ | 把本节这些"只能手敲 URL"的页面收成一个入口 | **本地新增（PHP 没有这一页）** | `GET /admin`（2026-11-04 起；之前 302 跳 `/admin/groups`）。分三类列：① 生产端只能手敲 URL 的（16 项 `edit*.php` 从 `STOCK_OPTIONS` 生成 + 删标的 / 分组 / 交易 / 抓取）；② 生产端根本没有的（符号管理 / 索引页 / 登出 / 状态页 / JSON API）；③ 生产端有、本地未迁（`uploadfile.php` / `debugcn.php`）。符号相关的 17 个链接共用一个输入框（`__SYMBOL__` 占位 + `data-symbol-href`）；2026-11-09 起「修改季报持仓」改成静态链接指 `/holdings/quarter` 索引页，不带占位符。清单定义在 `web/services/admin_console.py`，见 `README_admin_console.md` |
| ~~季报持仓索引页~~ | 列出要按基金季报手工维护持仓的基金代码 | **本地新增（PHP 没有这一页）** | `GET /holdings/quarter`（2026-11-09 起）。32 只持仓型 QDII 来自 `qdiimix_holdings_symbols()`，点 `161815` → `/holdings/quarter/SZ161815`。PHP 那边 `editquarterreportcn.php` 必须先知道 `?symbol=` 才能打开，"哪些基金要录"全靠人记。见 `README_admin_console.md` §九 |
| ~~旧地址兼容层~~ | `woody/res/*.php` 这 206 个地址 | **本地新增（生产端不需要：文件名本身就是地址）** | `GET /woody/res/<path:page>` → 302 到对应本地路径，196 个能跳 / 10 个明确没有对应（404 + 原因）。分类页从 `CATEGORY_PAGES` 反推、edit 族从 `STOCK_OPTIONS` 生成。见 `README_legacy_urls.md` |
| `uploadfile.php` | 上传 Yahoo 历史行情 CSV 到 `stockhistory` | 未迁 | 低优先（本地用 `dbtools/seed_*` 顶替） |
| `debugcn.php` | 访问日志/恶意 IP 统计页（依赖 nginx 日志） | 未迁 | **不建议迁**，PHP 专属运维页 |

## 六、不属于这批、但也没迁的（粗查）

| 类别 | 缺口 | 证据 |
|---|---|---|
| **行业分类页** | **2026-10-30 已全部迁掉**：`chinaindexcn` / `chinainternetcn` / `biotechcn` / `commoditycn` / `chinafuturecn` / `hangsengcn` / `hstechcn` / `hsharescn` / `mscius50cn` / `oilfundcn` / `qqqfundcn` / `spyfundcn` / `qdiihkcn` / `qdiijpcn` / `qdiieucn` / `qdiimixcn` 共 16 个 → `/category/<name>`（`qdiicn.php` 另有短路径 `/qdii`）；2026-11-01 又补上同构的 `lofcn.php` / `overnightcn.php`，注册表共 **19** 个 | 见 `README_category_pages_migration.md` 与 `README_autotractor_migration.md` §二。**原判断是错的**：这里曾写成"`FUND_GROUPS` 加个键即可复用 `/fund/<group>`"，实测这些页根本不是"基金指数对照表"，而是 `_mystockgroup.php` 驱动的分类页（参考数据表 + 估值表）；`/fund/qdiihk` 之类的 404 是**另一个**东西（那 22 个名字本就不是分组名）。`btbondcn.php` 也不属于这一族（是公司介绍页，`_res.php`）。数组随后按生产端对齐（27 / 38 / 15），见该文 §七；**剩下的缺口是数据不是页面**：`chinaindex` 的 27 只 A 股 ETF 在演示库里连 `stock` 行都没有 |
| ~~**对比页**~~ | ~~`ahcomparecn.php` / `abcomparecn.php` / `adrhcomparecn.php` / `ahhistorycn.php`~~ | **2026-10-31 已全部迁**：`/abcompare` / `/ahcompare` / `/adrhcompare` / `/ahhistory/<symbol>`，见 `README_compare_pages_migration.md`。**剩余缺口是数据不是页面**：演示库 `abpair` / `ahpair` / `adrpair` 都是 0 行，且 `stock` 表里没有 A 股（`SH600*` 0 只）也没有 H 股（`HK*` 只有外汇 `HKCNY`），所以四页恒空态 |
| ~~**分红数据页**~~ | ~~`etfdividendcn.php`~~ | **2026-10-31 已迁**：`GET /stock/<symbol>/etfdividend`，见 `README_etfdividend_migration.md`。**剩余缺口同样是数据**：`stockdividend` 那 4 行的 `stock_id`（182/184/186/214）全部超出 `stock` 表最大 id（181），任何符号都查不到；`QQQ` / `SZ161125` 有净值能过闸门、但分红行为空，所以本地看到的是"表头 + 空行提示" |
| ~~**工具页**~~ | ~~`autotractorcn.php`（拖拉机自动化）/ `overnightcn.php`（义工群对比）/ `lofcn.php`（LOF基金）~~ | **2026-11-01 全部迁完**：`/autotractor`（新 service + 模板 + 路由）、`/category/overnight` 与 `/category/lof`（分类页注册表 17 → 19，见 `README_autotractor_migration.md`）。同族另两页 `rotationtradingcn.php` 与 `overnightcnhcn.php` 于 2026-10-31 迁完（`README_rotationtrading_migration.md` / `README_overnight_cnh_migration.md`）。**顺带修掉两个老账**：`lofcn.php` 从来没进过任何清单（本文 2026-10-30 那版漏了它），以及页脚四个工具页链接还指着生产端（迁完没改 = 自留死链），现在整段挪到 `site_links.local_links()` |
| **账号体系** | **2026-11-02 迁完"认证内核"**：登录 / 注册 / 改密 / 改邮箱 / 重置密码 / 关号 + 资料页 + 编辑资料 + 登出（9 条路由，`README_member_auth_migration.md`）。**仍然是演示数据的**：`/account`（交易明细 + 资金汇总）与 `/group` / `/portfolio` 还读 `web/routes/account_demo_data.py` 的写死样例，没切 `stockgroup` 三张表 —— 那是同一份文档 §八 列的第一项待办 | `WEB_MIGRATION_PLAN.md` T4b |
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
6. ~~**`deletesymbol.php` 仍排在最后**（要连带清 8 张表，风险最高）~~ ——
   **2026-10-30 已迁**（`GET/POST /stock/<symbol>/delete`，先预览再执行）。
   查清了两件事：**实际是 6 张表不是 8 张**（`deletestock.php` 只做四件事加一个
   副作用）；真正的风险不在"删哪几张"，而在"**PHP 漏了 15 张残留表**"，其中
   `stockhistorydate` 那条水位线会让重新加回的符号永远补不进历史。这两点连同
   三条刻意偏离见 `README_deletesymbol_migration.md`。
   ~~`submitdividend` / `submitnetvalue` / `submithistory` 三个抓外部 JSON/XLS 的页未动~~
   —— 同日已迁（`GET /submit` + 三个 POST），对照 19 例、真库 smoke 见
   `README_submit_pages_migration.md`。**左侧真正的缺口只剩 `uploadfile`**
   （上传历史 CSV，本地用 `dbtools/seed_*` 顶替，不建议迁）。
7. ~~**对比页四页**（`GetCategoryArray()` 里那三个 compare 分支）~~ ——
   **2026-10-31 已全部迁**（`/abcompare` / `/ahcompare` / `/adrhcompare` /
   `/ahhistory/<symbol>`）。与分类页共用 `_mystockgroup.php` 的 `EchoAll()`，
   差别只是数组来自三张配对表，所以单开一个 `compare_bp`（数据源必须带）。
   PHP 真身对照 39 例全一致（含 `fundpair` 76 行那条"有数据的同款"），
   见 `README_compare_pages_migration.md`。**同日顺手修掉一个周末红灯**：
   `is_month_end()` / `is_week_end()` 的"没有下一天"分支原来回退到本机日期，
   而 PHP 用的是 `SetTimeZone()` 之后的 `GetNowYMD()` —— 本机周六时月末判据
   会整体偏移，改成显式传 `_market_ymd()`。

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
