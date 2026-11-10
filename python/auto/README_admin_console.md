# 后台控制台（``/admin``）

> 2026-11-04。对应 `web/routes/admin.py::admin_index`、`web/services/admin_console.py`、
> `web/templates/admin_console.html`。测试 `web/tests/test_admin_console.py`。

## 一、这一页存在的理由

`README_pending_pages.md` 第一节的定义：

> "生产端未展示页面" = **在生产端站点上点不出来、只能手敲 URL 的页面**。
> 它们不进任何导航（`GetStockMenuLinks()` / `EchoLinks()` 都没有入口），
> 绝大多数是"某只标的的编辑表单 + 它的 POST 目标"，且要登录（`IsAdmin()`）才执行。

迁完之后本地的处境**比生产端更差**：PHP 那边至少 URL 名字就是页面名
（`editcalibrationcn.php`），我们收成了 `/stock/<symbol>/edit/editcalibration` ——
多一个必填参数，更没法猜。于是出现"功能能用，但没人找得到"。

`/admin` 就是补这个入口。它同时是一张**迁移台账**：本地比生产端多了什么、
少了什么，一屏看完，不必翻文档。

## 二、三类清单（判定依据）

| 分组 | 口径 | 条数 |
|---|---|---|
| 生产端只能手敲 URL | PHP 有页面、无任何入口链接。已迁 | 16（`edit*.php`）+ 4 |
| 生产端根本没有 | PHP 那边是散装副作用或服务器专属运维页，本版新增 | 6 |
| 生产端有、本地未迁 | `uploadfile.php` / `debugcn.php` | 2 |

**"无入口"是查过的，不是猜的**：`grep -rn "editstockgroupcn.php\|editstocktransactioncn.php"
php/ woody/ --include=*.php` 零命中；`GetStockOptionArray()` 的 16 个页面名只被
`GetStockOptionLink()` 用（该函数本身没有任何调用点，除了删标的旁边那个
`GetStockEditDeleteLink()`）。三个 `submit*.php` 同理 —— 链接函数
`GetUpdateStockHistoryLink()` 在全库无调用点。

## 三、16 项从 `STOCK_OPTIONS` 生成，不另抄一份

`web/services/stock_option_service.py` 的 `STOCK_OPTIONS` 已经是这 16 项的
单一真相源：中文动作名（`submit`）、说明（`memo`）、是否需要符号、哪两项跳
专门页（`route`）、哪一项只要登录（`member_level`）。控制台直接遍历它。

**反面做法是照抄一张表**：将来加第 17 项时只改一处，控制台会默默少一格，
而且没人会发现。这条与 `README_pending_pages.md` 里"`qdii_mix_get_symbol_array()`
≠ `qdii_mix_holdings_symbol_array()`"那类坑同源。

两项有专门页的直接指过去（`/holdings/quarter/<symbol>` / `/holdings/exhaustive/<symbol>`），
不给 `<option>` 链接 —— 与 `/stock/<symbol>/edit` 索引页同一个取舍：不在两处
各写一份写入口。

**2026-11-09 例外**：`editquarterreport` 在**控制台**这一处改成两级入口 ——
先到 `/holdings/quarter` 索引页挑基金代码，再进 `/holdings/quarter/<symbol>`。
它仍留在 `STOCK_OPTIONS` 里（`/stock/<symbol>/edit` 索引页还要用 `spec.route`
指到专门页），只是 `stock_option_entries()` 给它的 `href` 不带符号、
`needs_symbol=False`。理由见第九节。

## 四、符号占位符与那 40 行 JS

17 个链接（15 项 edit + 删标的 + 可编辑项索引）都要一只标的才有意义
（第 16 项季报是索引页，见第九节）。三种做法：

| 做法 | 问题 |
|---|---|
| 每条挂一个 `<form>` | 17 个输入框，填 17 次 |
| 一条 `/admin/goto?symbol=X&target=Y` 路由 | 纯服务端，但每个动作要多一个按钮 + 一次 302；`target` 还得自己维护白名单 |
| 一个输入框 + 前端改写 `href` | **选它**。一次输入，17 个链接跟着走 |

实现：service 生成 `href` 时把符号位写成 `__SYMBOL__`（`SYMBOL_PLACEHOLDER`），
模板**只在 `entry.needs_symbol` 为真时**输出 `data-symbol-href`（原始模板）与
`data-symbol-link`（可改写标记），内联脚本监听 input，把带标记的链接一起替换。
季报那个 chip 没标记 —— 挂上就会被 JS 拿去改写一个没有占位符的静态链接。

三个细节：

1. **归一规则与 `_normalize_symbol()` 一致**：没有下划线就大写。
   `nf_AU0` 必须原样保留大小写 —— 新浪对 `nf_au0` 返回空串，而 `stock` 表的
   排序规则不区分大小写，查重查不出来，只有行情会静默失踪；
2. **没有 JS 时给服务端兜底**：右侧那条「打开可编辑项索引 →」由
   `url_for("stock.stock_option_index", symbol=默认符号)` 直接生成，不依赖脚本。
   chip 本身在无 JS 时指向 `__SYMBOL__`（404），所以兜底链接必须存在；
3. **符号框清空时禁用整组 chip**（`is-disabled` + `pointer-events: none`）：
   点进 `/stock//edit` 看 404 比"明确表示先填符号"更糟。

默认符号取 `?symbol=`，没有就 `QQQ`（演示库一定有）。不查库决定默认值 ——
那是一次为了好看而增加的首屏查询。

## 五、计数吞异常是刻意的

三张卡右下角各一行现状（`190 个符号` / `4 个分组` / `3 个工具`）。
前两个走 `_count_or_none()`，**出任何错都返回 `None`**，模板对 `None` 的处理
是整块不渲染。

理由：后台首页恰恰在"库出问题"时最需要能打开。为了一个装饰性数字让整页 500，
等于把唯一能进去排查的入口也关掉。`test_counts_missing_does_not_break_the_page`
钉的就是这条（fake 数据源的 `cursor()` 直接抛异常，页面仍 200）。

代价：`FakeCursor` 那种"不认识这条 SQL 就 assert"的测试替身在这里会**静默通过**
（异常被吞）。所以路由测试另写了一个只认 `SELECT COUNT(*)` 的 `CountCursor`，
把"数字真的显示出来"单独断言一次 —— 否则吞异常会顺带吞掉测试。

## 六、布局决定

| 区块 | 形态 | 为什么 |
|---|---|---|
| 三张入口卡 | `grid` 三列、整卡可点 | 这是进后台最常干的三件事 |
| 16 项 `edit*.php` | chip 网格（`auto-fill, minmax(200px, 1fr)`） | 15 项是"给某只标的打开一个表单"，一屏扫完比逐行读表格快；说明放 `title` 悬停。季报那一项是静态索引链接，形状相同但不随符号变 |
| 其余三块 | 表格 | 需要读说明，chip 装不下 |
| 未迁那一段 | 表格 + 暖色分隔线 + 「结论」列 | 性质与上面两段相反（不能用 vs 能用），不该长得一样 |

所有链接都**真的可点**（除了未迁那两条 —— 死链比没有更糟，所以它们只给文件名和值）。

## 七、没做的事

1. **不在控制台里嵌分组/抓取的表单** —— 那两页各自已经有完整表格，
   嵌一份等于两处维护；这里只给入口卡。
2. **不复刻 `debugcn.php`** —— 它读 nginx 访问日志、统计恶意 IP，是服务器
   专属运维页。换域名才有意义，列在"未迁"里是为了别人不再问一遍。
3. **不做 `uploadfile.php`** —— 本地历史行情一律走 `dbtools/seed_*`
   （新浪日线，`nf_` 那族还带结算价），没有"上传 CSV"的场景。
4. **不把 JSON API 拆成六行** —— 那是给程序用的，人只需要知道有这六个端点，
   一行带说明就够，链接指最好点的 `/api/status`。

## 八、与生产端的关系

`/admin` 这个入口 **PHP 没有**（那边后台是 `editstockgroupcn.php` 一张表单）。
本地 2026-10-26 起把它做成 302 跳 `/admin/groups`，2026-11-04 换成控制台：
分组只是后台能力的一小块，而上面那 20 个页面**没有别的地方能发现它们**。

页头导航的「后台」从 `/admin/groups` 改成 `/admin`；`/admin/groups` 与
`/admin/symbols` 都加了「← 后台控制台」回链。页脚「后台管理」仍指
`/admin/groups`（`site_links.local_links()` 的管理员段，与登录身份无关，
测试 `test_site_links.py` 会断言它在不在）。

## 九、季报持仓索引页（2026-11-09 新增）

`/admin` 里点「修改季报持仓」现在先到 **`GET /holdings/quarter`**，列出待维护的
基金代码，点代码（如 `161815`、`160216`）进
`GET/POST /holdings/quarter/SZ161815`。路由与取数都在
`web/routes/holdings.py`（`quarter_report_index` / `_quarter_report_targets`），
模板 `web/templates/quarter_report_index.html`，测试
`web/tests/test_quarter_report_index.py`。

| 决定 | 理由 |
|---|---|
| 清单取 `updater.holdingsupdater.qdiimix_holdings_symbols()`（**32 只**） | 这 32 个详情页 `require _qdiimix.php`，季报持仓要逐只维护；与 `holdingscli` 同源。**不是**分类页 `qdiimixcn.php` 那 15 只 —— 用它当待办清单会静默漏 17 只 |
| 不从 `quarterreport` 反推清单 | 演示库该表**一条记录都没有**（2026-11-09 实测）。这是"还没录"的待办清单，不是"录了什么"的报表 |
| 名称 / 最新季报日期只是附加列 | 两条 SELECT 各自 try/except；库挂了照常列全 32 个代码。后台索引页不该因一条查询 500 |
| 这一页不判权限 | 只是导航 + 只读概览；真正闸门在编辑页的 POST（`admin_auth.is_admin()`） |
| 写入口仍只有 `/holdings/quarter/<symbol>` 一处 | 索引页不重复实现表单；`/stock/<symbol>/edit` 索引页的 `spec.route` 也仍指专门页 |

代码列剥掉 `SH`/`SZ` 前缀显示 6 位数字（`SZ161815` → `161815`），
旁边保留完整库内符号，避免"代码"与"符号"两个口径混在一起。
`test_quarter_report_index.py` 钉住 32 个链接、库挂了的降级、以及从
`/admin` 那个 chip 点进去确实是 200。

## 十、验收

- `web` 全量测试全绿（新增 `tests/test_admin_console.py` 与
  `tests/test_quarter_report_index.py`）；
- `/admin`、`/admin/`、`/admin?symbol=nf_AU0` 均 200，默认值 / 兜底索引链接正确；
- 页面含 16 个 chip（其中 15 个带 `__SYMBOL__` 占位符）、4 张三表条目、
  6 条本地新增、2 条未迁；
- `/holdings/quarter` 200 + 32 个基金代码；点 `161815` 到
  `/holdings/quarter/SZ161815` 200；
- `test_admin.py` 原来那条 `test_bare_admin_redirects_to_groups` 改成
  `test_bare_admin_renders_console`（302 → 200）。
