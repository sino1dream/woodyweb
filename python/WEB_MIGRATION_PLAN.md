# Python Web 迁移第一版方案

## 目标

在保留 `python/auto` 作为迁移业务层的前提下，新增一个轻量 Flask Web 服务，  
先提供基金估值 JSON API 和对应 HTML 页面，验证 Python Web 架构是否可用。

## 目录

```text
python/
  auto/                     # 已迁移的基金、中国股票和汇率数据模块
  sql/                      # 数据库表结构、建表与体检脚本（见 DATABASE_PLAN.md）
    schema.sql              # 14 张核心表 DDL（由 PHP 权威生成）
    build_schema.py         # 建表
    check_schema.py         # 体检：表存在性/行数/最近日期
    seed_demo_data.py       # 种子数据（验证链路用）
    verify_pages.py         # 用真实数据库验证页面数据
  web/
    app.py                  # Flask 应用工厂和启动入口
    config.py               # Web 配置
    db.py                   # 请求级 PyMySQL 连接
    services/               # 业务服务层
    routes/                 # 路由
    templates/              # Jinja2 页面
    static/                 # 静态资源
    tests/                  # Web 层测试
```

## 第一版范围

- `/`：首页，输入基金代码，并列出基金分组入口。
- `/fund/<group>`：基金分组列表（基金指数对照表），目前有 `qdiilof`、`qdiietf`。
- `/fund/<group>/<symbol>`：分组下的基金估值 HTML 页面。
- `/fund/<symbol>`：兼容旧链接，按代码号段推断分组。
- `/api/fund/<symbol>`：基金估值 JSON API。
- `/health`：Web 服务健康检查。
- `/status`：数据库连通性和迁移状态。

基金分组按代码号段划分（`python/auto/fundgroups.py`）：

| 分组        | 号段                      | 数量 |
| --------- | ----------------------- | -- |
| `qdiilof` | 深圳 `16xxxx`、上海 `50xxxx` | 26 |
| `qdiietf` | 深圳 `15xxxx`、上海 `51xxxx` | 21 |

两者互不相交，并集是 PHP `GetLofMixSymbolArray()` 与 `QdiiGetSymbolArray()` 的  
全部 47 只。注意 PHP 的 `QdiiGetSymbolArray()` 把 LOF 和 ETF 混在一起，  
所以分组必须以号段为准，不能直接照搬那个数组。

第一版只使用已经迁移的 Python 模块：

- `fundcalc.py`：基金估值计算。
- `stockdata.py`：MySQL 读取和数据结构组装。
- `stocksymbol.py`：符号分类与交易时段。
- `fundgroups.py`：基金分组定义。
- `httpjson.py`：后续数据源请求复用。

## 启动和测试

```powershell
cd F:\woody\woodyweb\python
python -B -m web.app
```

也可以直接执行：

```powershell
python -B web\app.py
```

默认访问地址是 `http://127.0.0.1:5000/`。可以用  
`PALMMICRO_WEB_HOST`、`PALMMICRO_WEB_PORT` 和 `PALMMICRO_WEB_DEBUG`  
覆盖监听地址、端口和调试开关。

不依赖数据库的页面：

- `/`
- `/health`

依赖数据库的页面：

- `/fund/<group>`（`/fund/qdiilof`、`/fund/qdiietf`）
- `/fund/<group>/<symbol>`
- `/fund/<symbol>`
- `/api/fund/<symbol>`
- `/status`
- `/api/status`

依赖数据库的页面需要先在 `python/auto/.env` 中填写真实的  
`PALMMICRO_DB_*` 配置（可参考同目录的 `.env.example`）。注意值不要写成  
`<...>` 形式，校验会把它当成未填写的占位值并拒绝启动。

想在导入真实库之前先看页面形态，可以跑演示服务（内存示例数据，不连库）：

```powershell
cd F:\woody\woodyweb\python
python -B web\app_demo.py     # 默认 http://127.0.0.1:5001/
```

测试命令：

```powershell
cd F:\woody\woodyweb\python
python -B -m unittest discover -s auto -p "test_*.py"        # 222 个
python -B -m unittest discover -s web\tests -p "test_*.py"   # 30 个
```

`python\auto` 下的测试需要在该目录内运行（`python/auto` 不是包，模块之间是平铺导入）：

```powershell
cd python\auto
python -B -m unittest discover -s . -p "test_*.py"
```

## 数据流

```text
Flask 路由
  -> FundService
  -> db.py 请求级连接
  -> MySqlFundDataSource(connection)
  -> StockDataArrayBuilder
  -> PalmmicroFundCalculator
  -> Jinja2 页面或 JSON 响应
```


## 后续迁移优先级

1. ~~`php/stock/stocksymbol.php`：符号分类、精度、市场和时区。~~  
   已完成，见 `python/auto/stocksymbol.py` 与  
   `python/auto/README_stocksymbol_migration.md`。
2. ~~`php/stock/stockref.php`、`php/stock/stockprefetch.php`：实时行情和缓存。~~  
   已完成，见 `python/auto/stockref.py`、`python/auto/stockprefetch.py`、  
   `python/auto/tzutil.py` 与 `python/auto/README_stockref_migration.md`。
3. ~~`php/stock/mysqlref.php`、`fundref.php`、`qdiiref.php`：引用对象层。~~  
   已完成，见 `python/auto/refobj.py` 与  
   `python/auto/README_refobj_migration.md`（含 `cnyref.php` 与 `GbToUtf8()`）。
4. `php/ui/table.php`、`stocktable.php`、`stockdisp.php`、`stockcomm.php`：  
   渲染层已做两轮，见 `python/auto/README_ui_column_migration.md`。  
   **第 1 轮**：列定义地基 `auto/uicolumn.py`（34 个列预置 + 宽度算法 + 排序），  
   以及 `referenceparagraph.php` → `web/services/reference_service.py`（已接线  
   到 `/fund/<symbol>`）。**第 2 轮**：`pricepoolparagraph` → `auto/pricepool.py`、  
   `ahparagraph` → `web/services/pair_service.py`、`fundestparagraph` →  
   `web/services/fundest_service.py`，并抽出 `web/templates/_data_table.html` 宏。  
   **仍未迁**：`tradingparagraph`、`stockparagraph`、`smaparagraph`、  
   `portfolioparagraph`、`stockgroup` / `fundpurchase` / `transaction` / `comment`。
5. `php/layout.php`、`php/menu.php`、`php/stocklink.php`：站点布局和导航。

第 1、2、3 步已经落地：

- `python/auto/stocksymbol.py`：`stocksymbol.php` 的纯逻辑移植（常量、全部符号表、  
  数字区间、符号构造、`StockSymbol` 分类与交易时段/时区）。
- `python/auto/stockref.py`：`stockref.php` 的行情解析层（四个市场的字段映射、  
  涨跌幅、时区换算、盘前盘后引用）。
- `python/auto/stockprefetch.py`：`stockprefetch.php` 的预取调度判断，数据库与  
  缓存文件依赖用回调注入，保持逻辑可测。
- `python/auto/tzutil.py`：时区获取；`zoneinfo` 不可用时回退到带夏令时规则的实现，  
  使 Python 3.7（当前 Flask 运行环境）也能正确换算。
- `python/auto/fundgroups.py`：基金分组定义（`qdiilof` 26 只、`qdiietf` 21 只，  
  按深圳 15/16、上海 50/51 号段划分），Web 层据此提供分组列表页。
- `python/auto/sinaquotes.py`：新浪行情 HTTP 抓取层（`GetSinaQuotes()` /  
  `url_get_contents()` 的迁移），补上了第 2 步刻意留成回调的 IO 实现。
- `python/auto/quotestore.py`：行情/净值落库（`StockHistorySql::WriteHistory()`  
  等）与**溢价率**计算。
- `python/auto/quotecli.py`：抓取 + 落库命令行。
- `python/auto/quotesource.py`：**行情源抽象与注册表**。PHP 侧实时行情写死新浪、  
  没有任何接口，这里预留了可替换接口，换源只改环境变量 `PALMMICRO_QUOTE_SOURCE`。
- `python/auto/quoteupdater.py` + `quoteupdatercli.py`：**后台定时抓取**与  
  跨进程共享（新增 `realtimequote` 快照表）。PHP 是在页面请求内同步抓取，  
  这里改成独立进程定时抓、页面直读。
- `python/web/routes/quote.py`：`/api/quotes`、`/api/quotes/refresh`、  
  `/api/quotes/source`。
- 分组列表末两列为**最新价**（带行情时间）与**溢价率**（正值红/负值绿）；  
  真实联网抓取 + 页面直读已验证。详见  
  `python/auto/README_quotes_migration.md` 与 `python/auto/README_realtime_quotes.md`。
- `python/auto/navsource.py` + `navupdater.py` + `navcli.py`：**基金官方净值**抓取，  
  替代 `php/stock/yahoostock.php`（Yahoo 实测全面 403）。净值源同样是可替换接口，  
  换源只改 `PALMMICRO_NAV_SOURCE`；已实现东方财富（首选）/ 新浪（备选）/  
  深交所（兜底校验）三个源，实测数值互相一致。
- 净值默认走**双源校验**（`crosscheck`）：东方财富为主信源、新浪为对照，  
  清洗后对比，差异超容差打 `ALERT` 到 stderr；主源缺失自动用对照源。  
  可用 `PALMMICRO_NAV_SOURCE` 退回单源，容差用 `PALMMICRO_NAV_TOLERANCE`。
- `python/sql/schema.sql` 新增 `estquote` 表：中间层估值数值单独存，  
  不再像 PHP 那样混进 `netvaluehistory`。详见  
  `python/auto/README_nav_migration.md` 与方案 `python/NAV_SOURCE_PLAN.md`。
- `python/auto/estcalibration.py`：中间层估值**尺度自洽**工具（诊断 + 重标定）。  
  `estquote` 已接进 `fundcalc` 的估值公式，静态与实时两条路径结果一致  
  （实测三只基金估值都等于官方净值）。详见  
  `python/auto/README_estquote_and_navcheck.md`。
- 页面溢价率现在是**真实数值**（实测 SZ161130 = 5.52%、SZ161125 = 4.54%）。
- `python/auto/forexsource.py` + `forexupdater.py` + `forexcli.py`：**汇率**抓取，  
  替代 PHP `php/stock/chinamoney.php` 的 `GetChinaMoney()`。汇率源同样是可替换  
  接口（新浪为主 + 中国货币网对照 + 相对差告警）。  
  修前汇率恒为 `1.0`（库里没有 `USCNY` 符号），**等于把美元当人民币用**。
- `NavUpdater.sync_names()` + `navcli.py --check-names`：用权威源核对基金名称。  
  顺带修掉一个数据错误：`SZ161130` 曾被写成"易方达标普信息科技LOF"（那是  
  `SZ161128` 的名字）。详见 `python/auto/README_name_and_forex.md`。
- 汇率历史：**已用国家外汇管理局**（`safe.gov.cn/AppStructured/hlw/RMBQuery.do`）  
  回补成功，实测写入 244 条（每币种 62 天，含基金官方日期 2026-09-29 的真值 6.7411）。  
  该接口是通过**读 AKShare 源码**（`currency_boc_safe()`）找到的 ——  
  AKShare 本身要求 Python >= 3.11、本项目运行时是 3.7 装不上，  
  所以读源码后用 requests 自行实现，注册为 `forexsource.SafeForexSource`（`safe`）。  
  另加了汇率**陈旧度警告**（`stockdata.MAX_RATE_FALLBACK_DAYS`，回退超 10 天报警），  
  并清掉了种子里手工占位的 `7.1`（不是任何一天的真实汇率）。
- **仓位默认值修正**：`get_position()` 原先把默认值硬编码成 `1.0`，而 PHP 是回落到  
  `StockSymbol::GetDefaultPosition()`，即 `IsLofA() ? 0.95 : 1.0`。修后 LOF 基金  
  （含库里无 `fundposition` 行的）都取 0.95，与 PHP 官网一致  
  （官网 `sz161125cn.php` 明写"仓位估算值使用0.95"）。同时修正了种子数据里  
  `SZ161125` 被显式写成 `1.0` 的错误。详见  
  `python/auto/README_name_and_forex.md` 第零节。
- **列表页表头对齐生产端**（`qdiicn.php` / `fundlistcn.php`）：原来只有一列 `日期`，  
  承载的其实是 `calibrationhistory.date`（校准值日期）。生产端有**两张表**、各有一个  
  同名"日期"（行情日期 / 校准值日期），本页合并成一张就必须拆开，否则等于用校准值  
  日期冒充行情日期。现改为 12 列：基金代码 / 名称 / 跟踪代码 / 仓位 / 校准值 /  
  校准日期 / 最新行情日期 / 行情更新时间 / 最新价 / 涨跌幅 / 溢价率 / 数据源。  
  途中修掉两个真问题：① `QuotesUpdater.get()` 的 TTL 只有 60 秒，页面用它取行情导致  
  抓取进程没跑时行情列全空 —— 新增 `get_last_stored()` 不判 TTL 读最近快照；  
  ② MySQL `TIME` 列经 PyMySQL 返回 `timedelta`，新增 `_normalize_quote_time()` 换算。  
  实测 SZ161130/SZ161125 的价格、涨幅（1.46% / 1.08%）、行情日期、行情时间与生产端  
  逐项一致。详见 `python/auto/README_name_and_forex.md` 第零节 0.5。
- **历史数据页面组**（新增）：迁移 `php/ui` 下 6 个历史数据段落  
  （历史价格 / 基金溢价记录 / 净值记录 / 净值价格比较 / 校准记录 / 基金场内份额），  
  路由 `/history/<kind>/<sym>`，并加了 `/api/history/<kind>/<sym>`。  
  共享取数与显示口径抽到 `python/auto/historytable.py`（对应 PHP  
  `php/sql/sqldailyclose.php` 与 `php/stock.php` 的显示函数）。  
  核对到生产端的两个关键口径：① QDII 用**前一交易日**净值  
  （`UseSameDayNetValue()`，作者明确说过这是设计不是 bug）；  
  ② 净值记录里的仓位是**反算**的，且只在净值涨跌超 4% 时才算。  
  途中新增 `shareshistory` 表（PHP 里叫这个名字，不是 `fundshares`），  
  并清掉了种子占位历史行（占位值曾让页面显示 1.5234 净值、214% 溢价）。  
  详见 `python/auto/README_history_migration.md`。
- **官方 EST（`fundest`）迁移**（新增）：`StockUpdateEstResult()` /  
  `StockCompareEstResult()` / `_QdiiReference::EstNetValue()`，含 20:55 闸门与  
  误差告警（阈值 1%）。新增 `fundeststore.py` / `fundestcalc.py` /  
  `fundestcli.py`。「基金溢价记录」因此多出「官方EST / 时间 / 误差」三列。  
  查清两条关键口径：① `fundest` 存的是「**官方净值出来之前**那一刻的估值」，  
  真实净值到货后不再写；② 中间层符号要用**杠杆替代**解析  
  （`est_symbol` 是 `^NDX`，但能取到数据的是 `QQQ`），否则静默算不出来。  
  详见 `python/auto/README_fundest_migration.md`。
- **三个 EST 齐备**（新增）：在 `fundest` 之上补齐**参考 EST**（`FairEst`）与  
  **实时 EST**，共用 `fundestcalc._resolve()` 抽出的公共上下文，  
  差别只在"用哪个中间层值 + 哪天的汇率"：  
  官方用**净值** + 中间层日期汇率、参考用**当日行情价** + **当前**汇率、  
  实时用**实时快照价** + 当前汇率。参考 EST 在「汇率日期与中间层日期一致」时  
  按定义为空（那时它与官方 EST 相同），这是 `GetFairNetValue()` 的语义而非缺陷。  
  途中修掉三个真 bug：① `_qdii_value` 写死取实时快照、忽略传入的中间层值  
  （导致官方 EST 不再等于净值，4.5452 变 4.5833）；② 校准时汇率 `cny_ref`  
  缺失而回落到当前汇率（参考 EST 从 4.4110 算成 4.6139）；③ `_getEstPrice`  
  兜底顺序写反、直接用昨收（官方 EST 偏 1%，4.5017 而非 4.5452）。  
  新增 `fundestcli.py --est` 并排显示三个 EST 与溢价。
- **外盘期货接入实时 EST**（新增）：`LoadSinaFutureData()` / `_onSinaFuture()` /  
  `CheckAdjustFactorTime()` / `AverageCalibraion()` / `CalcFactor()`。  
  新增 `futuresquotes.py`（期货行情）、`futurescalib.py`（配对/校准/换算）、  
  `futurescli.py`、`futurespair` 表。  
  **需要说明**：本工作区的 PHP 快照里六处 `new FundPairReference(...)` **都没传  
  `realtime_callback`**，即期货接线在这个版本里不存在，所以本步是**按作者文档  
  实现**而非照抄。  
  实测确认三件事：① 期货必须用**裸符号** `hf_NQ` 取数（`get_sina_symbol()` 会加  
  `gb_` 前缀导致返回空，PHP 的期货取数同样没走它）；② 字段映射与  
  `_onSinaFuture()` 完全一致（15 个字段）；③ **量纲必须换算** —— 期货价是指数  
  点位，直接用会让实时 EST 大 100 倍（487.68 而非 4.59），  
  正确式子是 `ETF价 = 期货价 × cal_m / cal_futures`。  
  详见 `python/auto/README_futures_migration.md`。
- **自动校准接进页面**（新增）：`FundPairReference::DailyCalibration()` /  
  `CalcFactor()`。新增 `autocalibration.py`、`test_autocalibration.py`（19 个），  
  触发点接在 `FundListService.get_group()`（对应 `/fund/qdiilof`、`/fund/qdiietf`），  
  环境变量 `WOODY_AUTO_CALIBRATE=0` 可关闭。  
  途中搞清两个**层级**问题：① **校准对象是中间层**（`QQQ`）而不是基金 ——  
  PHP 是在 `new FundPairReference('QQQ')` 上调 `DailyCalibration()` 的；  
  ② 演示库把中间层净值**归一化成了 1.0**，照抄 PHP 公式会把校准值放大 750 倍  
  （`31099` 而非生产端的 `41.111`），需先反推"该部署尺度的真实净值"再换算。  
  **勘误**：本步当时据 `PalmmicroFundCalculator.est_net_value()` 得出  
  "中间层校准值对基金估值零影响"，并据此写入了 `QQQ=41.488`。  
  这个结论**是错的** —— `est_net_value()`（不带 source）恰好是对 `cal_m`  
  不敏感的那条路径，而**真实管线** `fundestcalc.compute_official()` 对它是  
  **线性**的。写进去之后 `SZ161130 官方 EST` 从 4.59 炸到 **20245**。  
  修法与守卫见下一条。  
  详见 `python/auto/README_autocalibration_migration.md`。
- **自动校准运维 + 估值守卫**（新增，含上一条的补救）：`autocalibrationcli.py`  
  （`--show` 看闸门 / `--force` 忽略闸门 / `--set-nav` 推进中间层净值日期）。  
  查清"daemon 跑着但校准值不动"的根因：**行情、净值、校准是三条独立的线**，  
  daemon 只抓行情；而 `navupdater.collect_targets()` 只认 6 位数字代码，  
  中间层（QQQ/SPY）**被排除**，所以净值日期永不前进、闸门永远挡住。  
  新增 **估值守卫**：写入前临时写库 → 跑真实管线 → 比对净值 → **还原**，  
  偏差 >5% 拒写。它现在能挡住上面那次事故（实测拒绝理由给出  
  `偏差 468757% > 容差 5%`）。  
  新增 `sql/fix_middle_calibration.py`（幂等修复 + 改完自证估值）、  
  `sql/backup_demo_tables.py` / `restore_demo_tables.py`（改数据前的安全网）。  
  运维手册见 skill `palmmicro-ops`。
- **页面新增「基金净值 / 基金净值日期」两列**（12 → 14 列）：数据来自  
  `netvaluehistory`（官方公布的单位净值），与「最新价 / 最新行情日期」是两回事  
  —— 净值一天一个数、T+1 才公布，价格盘中一直动；溢价率正是拿净值算的。  
  日期列与净值列**走同一条回退链**，避免"净值取自净值表、日期取自行情表"的错配。
- 同时把 `stockdata.py` 的 `stock_get_symbol()` 修正为与 PHP `StockGetSymbol()`  
  一致（补上基金/股票代码前缀推断），并让 `fundcalc.py` 复用 `stocksymbol`  
  的符号表，消除两处重复定义。
- 验证方式：三个 PHP 对照脚本（共约 2.9 万个用例）在 Python 3.7 与 3.12 上结果  
  一致且无非预期差异，详见 `python/auto/README_stockref_migration.md`。
- **中间层派生净值 + `hedge_value` 贡献值**（新增，含一次固化修复）：  
  `usnavsource.py`（美股 ETF 净值的**派生**源，实测 `6.705 / 0.008945 = 749.58`  
  对生产端 749.378 差 0.03%，但**分红除息日会失真、不可用于对账**）、  
  `stockdata._attach_hedge_value()` 按 `est_value = nv_f × cal_f / cny` 把中间层  
  贡献值挂在**基金行**、`fundcalc.est_source()` / `_middle_est_value()` /  
  `est_net_value_with_est_quote()` 三条取值路径、`fundcalc` 改为复用  
  `stocksymbol` 的 QDII 符号表（消除重复定义）。  
  同时修掉一个**测试缺陷**：`AutoCalibrator._pair_value()` 的兜底会真的联网抓  
  新浪指数点位，导致同一套用例在装了 `requests` 的机器上全绿、没装的机器上红 6 个。  
  现把联网收敛成可注入的 `pair_fetcher`，测试不再依赖外网。  
  详见 `python/auto/README_usnav_and_hedge_migration.md`。
- **引用对象层（第 3 步）**：`refobj.py` 迁 `mysqlref.php` / `fundref.php` /  
  `qdiiref.php` / `cnyref.php` 四个文件与 `GbToUtf8()`，`Gb2312` 查表改用标准库  
  `gb18030`。它是"一个有状态的符号"：行情 + 库里的 id/净值/仓位/校准值 + 三个估值，  
  页面上几乎所有数字都从它身上取。三条刻意取舍：**不在构造时联网**（名称由数据源  
  给，PHP 是 `LoadSinaFundData()`）、**三个 EST 不在这里实现**（唯一实现是  
  `fundestcalc`，避免重演"估值 4.59 变 20245"那次两条实现并存的事故）、  
  **外部依赖全部可注入**。四个地区合成一个类 + `region` 查表。  
  已知偏差：PHP 这个位置用的 `FundPairReference` / `HoldingsReference` 未迁移，  
  由 `EstReference` 顶上；`MyStockReference`（`stock_ref`）由调用方注入。  
  详见 `python/auto/README_refobj_migration.md`。
- **渲染层（第 4 步，已做两轮）**：先说一个推翻预设的事实 —— `php/ui` 里  
  **几乎没有 `XxxParagraph` 类**，20 多个 `EchoXxxParagraph()` 全是过程式函数  
  直接 `echo` HTML，所以按"类对类"平推是错的。改成**先搬最共享的列定义**  
  （`auto/uicolumn.py`，PHP `stocktable.php` 那 30 个类其实只是"默认值不同"的  
  常量预置），再把段落重构成"返回 `(columns, rows)` 的服务"。  
  四条刻意取舍：**不迁 `htmlelement.php`**（整份都是拼 HTML 片段的函数）、  
  **颜色不再烧进文本**（PHP 在构造 `TableColumn` 时就把 `<font>` 拼进单元格，  
  本版把颜色作为数据随单元格返回，由 Jinja2 映射 CSS 类）、  
  **`RefSortByNumeric` 不去重**（PHP 的 `asort` 以 symbol 为键会静默丢行）、  
  **`TableColumnGetLastWidth` 显式传 `layout_width`**（不再依赖 PHP 的全局  
  `LayoutGetDisplayWidth()`）。  
  顺带查清：**7 个段落早就在 `python/web` 里了**（`history_service.py` 6 个 +  
  `fund_list_service.py`），真正待迁的是约 13 个。  
  已迁：`reference` / `pricepool` / `ah` / `fundest` 四个段落 + 列定义地基。  
  **配色裁决**：涨跌幅统一成**涨红跌绿**。PHP 自身在这件事上是矛盾的 ——  
  `php/stock/stockref.php:147` 的 `GetPercentageDisplay()` 是"跌红涨黑"，  
  而 `php/stock.php:91` 的 `StockGetPriceDisplay()` 默认参数（被 `GetRatioDisplay()`  
  等一大片函数共用）是"涨红跌绿"。选后者：前者是孤例、后者是主流口径，  
  改动面也最小。  
  详见 `python/auto/README_ui_column_migration.md`。
- **引用对象行情装填（P0）**：`refobj` 迁好了、列定义也迁好了，
  但**行情注入点从没接上**，所以详情页「参考数据」表六格里只有名称有值
  （价格/涨幅/日期/时间全空）。根因：`MysqlReference` 刻意"构造不联网"，
  而本项目的行情在 `realtimequote`（daemon 写的）里，没有任何东西把它读进来。
  新增 `refquote.py`（**先读库快照、再兜底联网**）与 `externallink.py`
  （补首格外链；PHP 靠它在没有外链时退回符号文本，保证列不错位）。
  `build_stock_references()` 从 `stockref.StockReference` 改走
  `refobj.MysqlReference`，并接上 `source` 用于读快照。
  途中被既有测试抓出一个真缺陷：第一版**构造期就联网**，而
  `QdiiReference` 构造里还会算实时 EST，等于**每个请求都抓一遍新浪**；
  已改成"构造只读快照、请求期才兜底"（`refquote.refresh_quotes()`）。
  验收：三只基金的参考数据与生产端 `qdiicn.php` **逐格一致**
  （`4.884 / 1.45% / 2026-10-09 / 15:00` 等）。
  详见 `python/auto/README_refquote_migration.md`。
- **补上跨层集成测试**：`web/tests/test_reference.py` 新增"真数据源 →
  引用对象 → 六格全有值"。这一层以前缺测试，正是"整张表是空的"
  能一路绿灯溜过去的原因（原有单测都在 service 边界打桩）。
- **refobj 的 PHP 对照验证（P1）**：`refobj` + 渲染层约 2600 行新代码
  **没走项目已有的对照方法**，单测覆盖的是"我给的行为"，抓不到"我理解错了 PHP"。
  新增 `_php_refobj_compare.py` / `_php_refobj_check.php`，对照 9 个纯数学函数
  （`QdiiGetCalibration` / `QdiiGetVal` / `QdiiGetPeerVal` /
  `FundAdjustPosition` / `FundReverseAdjustPosition` / `StockGetPercentage` /
  `StockCalcHedge` / 两个份额换算）：**98 例，0 意外差异**。
  用例集里刻意放了 `4e-7 / 5e-7 / 6e-7` 三个值 —— `StockGetPercentage` 的
  `> MIN_FLOAT_VAL`（`5e-7`）判据只有在这个量级才暴露 `>` 写成 `>=` 的错误。
  另加 `--selftest`：在内存里把公式改错，断言对照抓得到（实测抓到 4 / 10 例），
  避免"从不失败的对照脚本等于没有"。
  途中三个坑：① PHP 注释里写 `?>` 会让解析器提前结束、源码被当 JSON 输出；
  ② PHP 与 Python 的 `%.12E` 对同一个数给出 `E-9` / `E-09`，用数值拼 key 会让
  87 个用例全部误报，改用**序号做 key**；③ 返回值比较也要按数值而非字符串。
  详见 `python/auto/README_refobj_php_compare.md`。
- **基金详情页补齐三样（本步）**：详情页原来只有「参考数据」1 行、没有 EST 表、
  没有份额表。
  ① **参考数据标的清单**：新增 `reference_symbols_for_fund()`，
  对应 PHP `_qdiigroup.php` 的 `$arRef` + `$arLev` 装配
  （基金 → 估值标的 → 杠杆标的（查 `fundpair`）→ 即期外汇 → 中间价）；
  缺的符号**照实跳过**，不补空行。本演示库实际 4 行
  （缺 `TQQQ`/`USDCNH`/`hf_NQ`：不在 `stock` 表，或 `fundpair` 没有"ETF → 指数"行）。
  `externallink.py` 从"只认基金"扩成 `get_sina_stock_link()` 的**类型分发**，
  **8 类 href 逐字对齐生产端** `sz161130cn.php`；其中中间价 `USCNY`
  指向货币网、即期 `USDCNY` 指向新浪外汇页，区分点是 `is_forex()`。
  ② **EST 表接线**：`fundest_service` 早已写好，缺的只是注入引用对象；
  现在默认走 `refobj.QdiiReference` 现算（委托 `fundestcalc`，**没有第二套口径**，
  所以 workbuddy 当初"怕重影"的顾虑不再成立）。实测 `SZ161130` 官方 EST
  = `4.545196`，与净值一致。**实时 EST 仍为 None** —— 它要中间层的配对引用，
  而 `FundPairReference` 未迁移，所以表头是 4 列而非生产端的 6 列。
  ③ **基金份额表**：直接复用历史服务的 `shareshistory`（与
  `/history/shareshistory/<sym>` 同一张表、同一个 PHP 段落），
  并顺手把渲染抽成 `_history_table.html` 宏供两处共用。
  途中修掉三个真 bug：`float('')` 让**整张参考数据表消失**（PHP `floatval('')`
  给 0；已改走 `_php_floatval`）、`get_table([])` 返回 `None` 让详情页 500、
  以及一个被 `except` 吞掉的缺 import。
  详见 `python/auto/README_detail_page_sections.md`。

远端分支 `origin/devin/1780933111-migrate-php-core-to-python` 已有  
`python/core/stock_symbol.py` 和 `python/core/sina_fetcher.py`：

- `stock_symbol.py` 是第 1 步的早期版本，缺少交易时段和时区方法，  
  且未接入当前分支的 `python/auto` 包，现已由 `python/auto/stocksymbol.py`  
  取代；新模块保留了 camelCase 方法别名，因此按该分支写法调用仍然可用。
- `sina_fetcher.py` 里的抓取动作最终由 `python/auto/sinaquotes.py` 实现  
  （结构上仍是先解析后落库，只是把 IO 补齐了，并保持与 PHP 一致的分层）。

## 测试

```powershell
cd F:\woody\woodyweb\python
python -B -m unittest discover -s auto -p "test_*.py"        # 694 个
python -B -m unittest discover -s web\tests -p "test_*.py"   # 148 个
```

注意：Web 层测试需要 Flask 与 PyMySQL，当前只装在系统 Python 3.7 上。  
`python/auto` 的测试在 3.7 与 3.12 上都通过；3.12 上 `test_stockdata.py` 有 2 个  
既有用例需要向临时目录写文件，在受限文件权限环境下会报错，与本迁移无关。

与 PHP 的逐项对照（需要 PHP 在 PATH 中）：

```powershell
cd F:\woody\woodyweb\python\auto
python -B _php_port_compare.py        # stocksymbol，6280 个用例
python -B _php_numfmt_compare.py      # 数字/时间格式化，87 个用例
python -B _php_prefetch_compare.py    # 预取判定，26800 个用例
python -B _php_refobj_compare.py      # refobj 纯数学，98 个用例
python -B _php_refobj_compare.py --selftest   # 自检：证明对照抓得到注入的错误
python -B _php_rtrim0_fuzz.py 2000    # rtrim0 随机模糊
```
