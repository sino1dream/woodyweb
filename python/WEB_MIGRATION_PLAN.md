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

基金分组按代码号段划分（`python/auto/calc/fundgroups.py`）：

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

`python\auto` 自 2026-10-12 起按职责分成子包（`core` / `sql` / `source` /  
`calc` / `ref` / `updater` / `ui` / `cli` / `_scratch`），测试文件仍留在根目录，  
所以测试还是要在该目录内运行，让 `python/auto` 进 `sys.path`：

```powershell
cd python\auto
python -B -m unittest discover -s . -t . -p "test_*.py"
```

分档的目录职责、模块映射表与已知取舍见 `python/auto/README_layout.md`。

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
   已完成，见 `python/auto/core/stocksymbol.py` 与  
   `python/auto/README_stocksymbol_migration.md`。
2. ~~`php/stock/stockref.php`、`php/stock/stockprefetch.php`：实时行情和缓存。~~  
   已完成，见 `python/auto/ref/stockref.py`、`python/auto/updater/stockprefetch.py`、  
   `python/auto/core/tzutil.py` 与 `python/auto/README_stockref_migration.md`。
3. ~~`php/stock/mysqlref.php`、`fundref.php`、`qdiiref.php`：引用对象层。~~  
   已完成，见 `python/auto/ref/refobj.py` 与  
   `python/auto/README_refobj_migration.md`（含 `cnyref.php` 与 `GbToUtf8()`）。
4. `php/ui/table.php`、`stocktable.php`、`stockdisp.php`、`stockcomm.php`：  
   渲染层已做两轮，见 `python/auto/README_ui_column_migration.md`。  
   **第 1 轮**：列定义地基 `auto/uicolumn.py`（34 个列预置 + 宽度算法 + 排序），  
   以及 `referenceparagraph.php` → `web/services/reference_service.py`（已接线  
   到 `/fund/<symbol>`）。**第 2 轮**：`pricepoolparagraph` → `auto/pricepool.py`、  
   `ahparagraph` → `web/services/pair_service.py`、`fundestparagraph` →  
   `web/services/fundest_service.py`，并抽出 `web/templates/_data_table.html` 宏。  
   **第 3 轮**：`tradingparagraph` → `web/services/trading_service.py`、     
   `stockparagraph` → `web/services/stock_service.py`（含五档盘口进快照表）。     
   **第 4 轮**：`smaparagraph` → `web/services/sma_service.py`，外加两个     
   支撑模块 `auto/pairestimate.py`（配对双向估算）与     
   `auto/futurepremium.py`（期货理论溢价，走 `futurepremium` 表）。  
   **第 5 轮**：`calibrationhistoryparagraph` → `web/services/calibration_service.py`     
   （详情页两张「校准记录」，复用历史服务而不是重写取数），以及     
   `_qdiigroup.php` 的 `_tradingUserDefined` → `web/services/qdii_trading_column.py`     
   （五档表最后一列「换算价」），附 PHP 逐项对照 5 例全一致。  
   详见 `python/auto/README_calibration_record.md` 与     
   `python/auto/README_qdii_trading_column.md`。  
   **第 6 轮**：`fundlistparagraph` 带回调那一版 →     
   `web/services/qdii_fund_list.py`（详情页「基金指数对照表」，末列「对冲值」     
   由 `_callbackFundListHedge` 提供；公式复用已迁好的     
   `stockdata.get_stock_hedge`），附 PHP 逐项对照 5 例一致、2 条已知差异、     
   自检 4/4。详见 `python/auto/README_qdii_fund_list.md`。     
   **第 7 轮**：`_qdiigroup.php:101 EchoFundPairSmaParagraphs` —— 每个杠杆标的     
   各一张均线表，服务层复用已有的 `SmaService.fund_pair_tables()`，本轮只补     
   接线（`web/routes/fund.py`）、模板循环与测试（`fund_pair_tables` 此前零覆盖）。     
   PHP 逐项对照 5 例 0 差异（列组装用 PHP 自己的 `TableColumn` 真身）、自检 4/4。     
   顺带修掉 `_stock_column` 漏掉的 maroon 染色（同时影响 `EchoQdiiSmaParagraph`）。     
   详见 `python/auto/README_qdii_fund_pair_sma.md`。     
   **第 8 轮**：补 `php/stock/fundref.php:19` 那句     
   `if ($this->IsFundA()) $this->stock_ref = new MyStockReference($strSymbol)` ——     
   `FundReference.__init__` 现在自动建 stock_ref，`MyStockReference` 未迁、     
   由 `MysqlReference` 顶上（同样读库快照行情）。修掉了 QDII 均线换算列整列     
   空白的问题。改共享构造，所以按规矩先留页面基线再改：受影响面只有那一列，     
   其余九张表一行未动。PHP 逐例对照 5 例 0 差异（验的是四个委托方法的转发与     
   参数个数）、自检 4/4。详见 `python/auto/README_stock_ref_autobuild.md`。     
   **第 9 轮（分类页）**：`woody/res/qdiicn.php` → `/qdii`，本项目第一个     
   **按分类汇总**的基金页。装配套 `func:`web.services.qdii_page_service`（PHP`\_mystockgroup.php:\_echoStockGroupArray`的 foreach：非宽屏时全部
   符号进参考数据表、A 股基金且有官方净值的另进 EST 表），两张表分别复用
   已有的`reference_service`/`fundest_service`。
   ① **符号数以生产端为准**：本地 PHP 仓库的 `QdiiGetSymbolArray()`是 33 个，
   生产端（2026-10-12 抓取）已是 **41 个**；逐个抓了 qqqfund / spyfund /
   mscius50 / commodity / oilfund 五个子页面，确认多出的 8 只系"额外并入
   qdii"而非从原分组搬走，且整块追加在 SPY 组之后 —— 据此改`core/stocksymbol.py` 并逐位对齐（顺带影响 qdiilof 22→26、qdiietf 21→25）。
   ② **抓出一个真 bug**：`MysqlReference.GetStockLink`被错别名成`get_external_link`，估值表首格于是渲染成新浪外链，而生产端是站内详情页
   `res/<sym>cn.php`。详情页只有一张表所以一直没暴露，是分类页把两张表
   摆到一起、用 `\_scratch/\_prod_qdiipage_compare.py`逐格比 href 才发现的。
   新建`auto/ref/stocklink.py`修掉，并留了一条"两个链接必须不相等"的
   回归测试。详见`python/auto/README_qdii_page_migration.md`。 **仍未迁**：`stockgroup`/`fundpurchase`/`transaction`/`comment`（都要等账号登录体系）。 **已迁但受数据所限**：ETF（如`SH513100`）走即期外汇分支，演示库
   `fx_susdcny` 无行情快照，换算格仍为空 —— 列头已正确。 **第 10 轮（投资组合）**：`php/ui/portfolioparagraph.php`→`web/services/portfolio_service.py`+`auto/core/stocktrans.py`，
   `/portfolio` 演示页（内存交易集，`stockgroup`三张表与账号体系暂缓）。
   PHP 逐例对照 **86 例 0 差异**，并抓到一个真 bug：`_getArbitrageTestStr()`的`while ($record = fetch())` 在**自然耗尽时
   把 null 写回 `$record`**，初版误实现成"取最后一行"（pitfalls 第 47 条）。
   详见 `python/auto/README_portfolio_migration.md`。 **第 11 轮（合并段落 + 第二调用点）**：同页第三张表
   `_transEchoMergeParagraph`/`_echoMergeParagraph`（`_myportfolio.php:51-119`）
   一并迁入 `portfolio_service.py`（`merge_transactions_by_symbol`/`build_merge_table`），演示页加第二个分组让 KWEB/SPY 有符号重叠。
   PHP 逐例对照扩到 **125 例 0 差异**（+39），注入自检三类旧 bug
   （吞并、KWEB 口径、耗尽路径）全部精确复现。第二调用点
   （`_stockaccount.php:207`，单分组原始数组）服务层已验证
   （`RawGroupArrayTest`），页面外壳仍随账号体系暂缓。
   记 pitfalls 第 49 条（`foreach`键泄漏"碰巧正确" + PHP 数组键铸`false`为`0`）。 **第 12 轮（交易明细 + 资金汇总）**：同条线上的另两个段落 ——
   `php/ui/transactionparagraph.php`→`web/services/transaction_service.py`、
   `_stockaccount.php:213 EchoMoneyParagraphs`+`_stock.php:13-91`→`web/services/money_service.py`，底层模型
   `php/class/multi_currency.php`+`php/stockgroup.php`→`auto/core/stockgroup.py`。`/account`演示页（可切分组）把两段并在一起，`stockgroup`三张表与账号体系仍暂缓。
   三处新机制：①`TableRow.html_indexes`—— 备注格（清空链接 + 原文 +
   校准链接）与操作格（修改 + 删除）是**拼好的 HTML 且带`onclick`**，
   现有 `hrefs`与`first_cell_is_html`都表达不了，于是按格子下标开口子；
   ②`ref/stocklink.py`补五个链接助手；
   ③`historytable.\_trim_number_text` 修负零（数字层，全站受益）。
   PHP 逐例对照 **481 例 0 差异**，注入自检三类旧 bug
   （操作格空格、`empty()`闸门、PHP 松散比较）分别复现 90 / 40 / 5 处。
   抓到两个真 bug：清空链接的确认文字漏了「确认」（**单测也照着错的写**，
   见 pitfalls 第 53 条）、负零不归零（pitfalls 第 51 条）。
   详见`python/auto/README_account_migration.md`。 **第 13 轮（分组页 + 交易页）**：把"账号体系阻塞、且能用已迁服务演示接出"
   的两页迁成演示页 —— `_mystockgroup.php`的`mystockgroup`分支 →`/group`
   （单组交易明细 + 该组资金汇总，无"全部"行）、`_mystocktransaction.php`→`/transactions`（分组交易明细，支持 `?symbol=`过滤）。复用第 12 轮的`transaction_service`/`money_service`，演示数据抽到共用模块
   `web/routes/account_demo_data.py`（账户 / 分组 / 交易三页一起复用，避免
   三份重复）；`/account`同步重构为从该模块导入。导航与首页加入口。`_mystock.php`的主体是整条 stock-detail 渲染栈（trading/sma/fundest/ah…
   ），**远大于账号体系阻塞**，列为后续大项，本轮只迁了它的"我的交易"叠加
   （已并入`/group`、`/transactions`、`/account`）。测试：web **617** OK
   （+14）、auto 988 OK、compileall 双绿。详见
   `python/auto/README_group_transactions_migration.md`。 **第 14 轮（_mystock.php 详情栈 · CK1 快赢）**：启动 `_mystock.php` 个股详情大页
   这条大线。先做了侦察，结论比预期关键 —— 详情栈的**服务层大部分已迁**，真正卡住
   整条非基金详情线的是底层**配对引用层**（`php/stock/fundpairref.php`，约 278 行，
   含 `MyPairReference`/`FundPairReference`/`HoldingsReference`及`StockGetFundPairReference`/`StockGetHoldingsReference`/`StockGetPairReferences`），**尚未迁入`auto/ref/refobj.py`**（refobj.py:523、pair_service.py 文档都标注
   "未迁移"）。CK1 只做不依赖该层的快赢：把已迁的 `stockhistory`+`netvalueclose`
   两段（`history_service`已支持，原仅供`/history/*`页）接进`/fund/<symbol>`详情页（仅接线 + 模板，顺序对齐 PHP：netvalueclose → shares → stockhistory）。
   测试：web **619** OK（+2）、auto 988 OK、compileall 双绿；并修复一处`test_sma_route.FundPairSmaRouteTest`的隔离缺口（CK1 新段落暴露其未注入`history_service`，已补 `_EmptyHistory`注入）。详见`python/auto/README_stock_detail_migration.md`。 **第 14 轮（_mystock.php 详情栈 · CK2 关键石）**：迁移 `fundpairref.php`配对引用层。`MyPairReference`→`refobj.PairReference`，外加 `Ab`/`Ah`/`Adr`三子类（只是`pair_table` 不同）、四张配对表（`abpair`/`adrpair`/`ahpair`/`fundpair`）的正查与反查
   （对应 PHP `StockPairSql::GetPairSymbol`/`GetSymbol`）、`StockGetPairReferences`链式双向工厂，以及构造里的`cny_ref`推导分支表与`LoadCalibration()`。
   `EstFromPair`/`EstToPair`委托给已迁的`calc.pairestimate`自由函数（公式只留一份实现）。
   消费方`pair_service`/`trading_service`/`sma_service`走`_invoke`鸭子类型， **零改动自动通电**。测试：auto **1010** OK（988 + 22`test_pairref.py`）、web 619 OK、
   compileall 双绿；**PHP 逐例对照 15 例 0 差异**
   （`\_scratch/\_php_pairref_check.php`+`\_php_pairref_compare.py`，同时比对汇率符号与
   建出来的类 —— PHP 两条赋值路径 `new`的是`CnyReference`与`MyStockReference`两个不同的类）。对照抓到一个真 bug：B 股判定写成字符串前缀`900`，而 PHP 是数值区间
   `[900000, 1000000)`；根因是**没先 grep 代码库里已有的 `is_shanghai_b()`**
   就新增了一个 `is_shang_hai_b()`。已撤掉重复实现，记 pitfalls #54。详见
   `python/auto/README_pair_reference_migration.md`。

   **CK2b `FundPairReference` 已完成**：迁了四个 EST 取值（`GetOfficialDate` /
   `GetOfficialNetValue` / `GetFairNetValue` / `GetRealtimeNetValue`）、`netvalue_ref`
   与自动校准结构，外加工厂 `StockGetFundPairReference`。关键坑是 `CalcFactor`
   **两个分支的汇率方向相反**（A 股乘、其它除），写成同向会差 `cny²` 且不报错，
   用"与 `EstFromPair` 往返"的断言钉住。自动校准`auto_write`默认关 —— PHP 在构造
   函数里就写库，本项目 web 只读。已接线到`/stock/<symbol>`第 2 分支，演示库 5 个
   符号（^NDX/^GSPC/QQQ/TQQQ/SPY）多出估值表。详见
   `README_pair_reference_migration.md` §8。

   **CK2b 的两个缺口已收**（第 2 分支四段齐全）：`EchoFundListParagraph` 走
   `qdii_fund_list.fund_reference_table`（末列「参考值」，读 `lastcalibration`；
   此前迁的 `fund_list_table` 是同一 PHP 函数**带回调**那支，不是接不上），
   `EchoFundHistoryParagraph` 复用 `history_service` 的 `fundhistory`。两个都有
   真实 PHP 逐例对照（10 例 / 8 例，各带自检 4/4），顺带修掉"官方 EST 为 `0`
   时本版多摆三格"的偏差。

   **CK2b 的一处自身回归已修**：`stock_detail.html` 调 `data_table` 渲染估值表时
   漏了 `first_cell_is_html=True`，首格 `GetStockLink()` 的 `<a>` 被 Jinja 转义成
   可见文本（`/fund/<symbol>` 与 `/qdii` 那两处是对的）。已补渲染用例并做变异
   测试；替身首格也改成真形态的 `<a>` 串 —— 原先给纯文本时"漏传"与"传了"
   渲染结果一样，测不出来。详见 `README_stock_detail_migration.md` §8、pitfalls #59。

   **校准口径已统一（2026-10-13）**：`/stock/QQQ` 官方 EST 从 3452559.84 降到
   742.75（SPY 896641.41 → 770.42），与生产端同期 QQQ 743.07 / SPY 777.77 一致。
   根因**不在代码而在数据**：演示库 QQQ 的 cal 被播种成 `0.008945`（`汇率/净值`），
   生产端是 `41.111`（`^NDX/净值`），差 4595 倍。两条口径都用生产端页面实测反推
   并复验过：美股配对**不含汇率**（`cny_ref` 为 false → `GetDefaultCny()` = 1.0，
   `fundpairref.php:39-42`），A 股 QDII 走 `QdiiGetCalibration = est × 汇率 / 净值`
   （`qdiiref.php:3`，est 是 `^NDX`/`^GSPC` 不是 ETF）。动数据前 `mysqldump`
   到 `dbtools/_snapshots/calib_before_20261013.sql`；重算脚本
   `_scratch/_calibration_recount.py` 默认 dry-run。补了两组生产端锚点测试
   （`test_pairref.UsPairCaliberTest` 4 例、`test_fundcalc.ProductionCaliberTest`
   3 例），变异确认有牙。详见 `README_pair_reference_migration.md` §9、pitfalls #60。

   **仍存在的关键石 / 后续 checkpoint**：CK4 Holdings 分支（含
   `HoldingsReference`，441 行/24 方法 —— 注意演示库 21 个符号里**一个 QdiiMix
   都没有**，迁完在演示环境看不到变化）；CK5 交易块 + 链接（账号可演示）。
   **更新（2026-10-15）**：~~CK6 order book 写 snapshot~~ 已完成
   （`4e2ec161`：`realtimequote.orderbook` 列 + quotestore 编解码 + daemon 写入 +
   `refquote.apply_order_book` 回填，页面不联网也有五档）；~~A 股带配对溢价的
   trading~~ 已补（`/stock/<symbol>` 第 4 分支接上 `trading_service.get_table`，
   见 `README_stock_detail_migration.md` §6.4）。**仍未迁**：`_mystock.php`
   的 Holdings 分支；三个整页外壳（登录、`GetGroupSql()`、
   `StockEditTransactionForm`/`StockMergeTransactionForm`）随账号体系暂缓。
5. `php/layout.php`、`php/menu.php`、`php/stocklink.php`：站点布局和导航。

第 1、2、3 步已经落地：

- `python/auto/core/stocksymbol.py`：`stocksymbol.php` 的纯逻辑移植（常量、全部符号表、  
  数字区间、符号构造、`StockSymbol` 分类与交易时段/时区）。
- `python/auto/ref/stockref.py`：`stockref.php` 的行情解析层（四个市场的字段映射、  
  涨跌幅、时区换算、盘前盘后引用）。
- `python/auto/updater/stockprefetch.py`：`stockprefetch.php` 的预取调度判断，数据库与  
  缓存文件依赖用回调注入，保持逻辑可测。
- `python/auto/core/tzutil.py`：时区获取；`zoneinfo` 不可用时回退到带夏令时规则的实现，  
  使 Python 3.7（当前 Flask 运行环境）也能正确换算。
- `python/auto/calc/fundgroups.py`：基金分组定义（`qdiilof` 26 只、`qdiietf` 21 只，  
  按深圳 15/16、上海 50/51 号段划分），Web 层据此提供分组列表页。
- `python/auto/source/sinaquotes.py`：新浪行情 HTTP 抓取层（`GetSinaQuotes()` /  
  `url_get_contents()` 的迁移），补上了第 2 步刻意留成回调的 IO 实现。
- `python/auto/sql/quotestore.py`：行情/净值落库（`StockHistorySql::WriteHistory()`  
  等）与**溢价率**计算。
- `python/auto/cli/quotecli.py`：抓取 + 落库命令行。
- `python/auto/source/quotesource.py`：**行情源抽象与注册表**。PHP 侧实时行情写死新浪、  
  没有任何接口，这里预留了可替换接口，换源只改环境变量 `PALMMICRO_QUOTE_SOURCE`。
- `python/auto/updater/quoteupdater.py` + `quoteupdatercli.py`：**后台定时抓取**与  
  跨进程共享（新增 `realtimequote` 快照表）。PHP 是在页面请求内同步抓取，  
  这里改成独立进程定时抓、页面直读。
- `python/web/routes/quote.py`：`/api/quotes`、`/api/quotes/refresh`、  
  `/api/quotes/source`。
- 分组列表末两列为**最新价**（带行情时间）与**溢价率**（正值红/负值绿）；  
  真实联网抓取 + 页面直读已验证。详见  
  `python/auto/README_quotes_migration.md` 与 `python/auto/README_realtime_quotes.md`。
- `python/auto/source/navsource.py` + `navupdater.py` + `navcli.py`：**基金官方净值**抓取，  
  替代 `php/stock/yahoostock.php`（Yahoo 实测全面 403）。净值源同样是可替换接口，  
  换源只改 `PALMMICRO_NAV_SOURCE`；已实现东方财富（首选）/ 新浪（备选）/  
  深交所（兜底校验）三个源，实测数值互相一致。
- 净值默认走**双源校验**（`crosscheck`）：东方财富为主信源、新浪为对照，  
  清洗后对比，差异超容差打 `ALERT` 到 stderr；主源缺失自动用对照源。  
  可用 `PALMMICRO_NAV_SOURCE` 退回单源，容差用 `PALMMICRO_NAV_TOLERANCE`。
- `python/dbtools/schema.sql` 新增 `estquote` 表：中间层估值数值单独存，  
  不再像 PHP 那样混进 `netvaluehistory`。详见  
  `python/auto/README_nav_migration.md` 与方案 `python/NAV_SOURCE_PLAN.md`。
- `python/auto/updater/estcalibration.py`：中间层估值**尺度自洽**工具（诊断 + 重标定）。  
  `estquote` 已接进 `fundcalc` 的估值公式，静态与实时两条路径结果一致  
  （实测三只基金估值都等于官方净值）。详见  
  `python/auto/README_estquote_and_navcheck.md`。
- 页面溢价率现在是**真实数值**（实测 SZ161130 = 5.52%、SZ161125 = 4.54%）。
- `python/auto/source/forexsource.py` + `forexupdater.py` + `forexcli.py`：**汇率**抓取，  
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
  共享取数与显示口径抽到 `python/auto/sql/historytable.py`（对应 PHP  
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
  `dbtools/backup_demo_tables.py` / `restore_demo_tables.py`（改数据前的安全网）。  
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
  由 `EstReference` 顶上；`MyStockReference`（`stock_ref`）同样未迁，    
  `is_fund_a()` 为真时由 `MysqlReference` 顶上（第 8 轮补的自动建）。  
  详见 `python/auto/README_refobj_migration.md`。
- **渲染层（第 4 步，已做三轮）**：先说一个推翻预设的事实 —— `php/ui` 里  
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
  已迁：`reference` / `pricepool` / `ah` / `fundest` / `trading` / `stock`    
  六个段落 + 列定义地基。**🟡 档（第三轮）的两个**要点：
  - `tradingparagraph` 是 `php/ui` 里**唯一依赖实时五档**的段落。写这一轮时      
    库快照没有买卖档位，所以它**默认不渲染**、需 `?live=1`；      
    **第四轮**（`README_ui_column_migration.md` 的第四轮那一节）已把盘口写进      
    快照表的 `orderbook` 列，现在默认就读库渲染，`?live=1` 退化为      
    "这一请求额外联网补一次"。
  - 按用户指令**删掉了 `strlen()*11` 的像素宽度估算** —— 它在 CSS 布局下      
    没有意义，还依赖未迁移的 `LayoutGetDisplayWidth()` 与 `$_SESSION['mobile']`。
  - `stockparagraph` 只有 45 行，但它把 `GetMenuLink()` 的 HTML 塞在标题里；      
    本版把翻页数据化（复用上一轮的 `stocksql.stock_paging_columns()`），      
    新增 `/stock` 与 `/api/stock`，顺带把孤着的 `auto/stocksql.py` 接上线。
  - 两个 PHP 陷阱：`_checkTradingQuantity()` 里那句 `!= '0'` 是**不可达的**      
    （`'0'` 在 PHP 是假值），而它在 Python 里是真值 —— 照字面翻译会多摆一张      
    空盘口表；`GetMenuLink()` 的候选项允许**超出总数**（250 条时给到 300）。
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
  = `4.545196`，与净值一致。    
  当时实时 EST 还是空的（缺配对引用），**已在下面第 ⑤ 条补齐**。    
  ③ **基金份额表**：直接复用历史服务的 `shareshistory`（与    
  `/history/shareshistory/<sym>` 同一张表、同一个 PHP 段落），    
  并顺手把渲染抽成 `_history_table.html` 宏供两处共用。    
  途中修掉三个真 bug：`float('')` 让**整张参考数据表消失**（PHP `floatval('')`    
  给 0；已改走 `_php_floatval`）、`get_table([])` 返回 `None` 让详情页 500、    
  以及一个被 `except` 吞掉的缺 import。    
  详见 `python/auto/README_detail_page_sections.md`。

  ④ **收尾一轮（2026-10-10）**：补上上面第 ① 条漏掉的 `GetRealtimeRef()`    
  那一档（`fundpair` 的反向读法，`_realtime_symbol()`），给零覆盖的    
  `_leverage_symbols()` 补了假 cursor 测试（连 tuple / dict 两种游标行都认），    
  并把参考表里「库里没有」与「没抓到行情」两种空行在页面上区分开    
  （`quote_state_of()` 四态 + 名称格标注）—— PHP 那边两者长得一模一样。    
  同时修掉 `_php_refobj_compare.py --selftest` 的一处**假通过**：    
  它按字符串排序取用例，`[10]` 会排在 `[1]` 前面，验的根本不是阈值边界。

  ⑤ **接通实时 EST（2026-10-10）**：第 ④ 条只补了**参考数据那一侧**的    
  `GetRealtimeRef()` 分支，实时 EST **本身**仍然算不出来 —— 实测    
  `est_ref.get_pair_ref()` 不存在、`get_realtime_ref() = None`、    
  `realtime_net_value = None`，页面 EST 表少两列。    
  根因是 `refobj` 里缺配对引用：
  - `EstReference` 缺 `get_pair_ref()`（`FundReference.get_realtime_ref()`      
    靠 `hasattr` 探测它，没有就恒返回 `None`）；
  - 演示库 `fundpair` 缺 `^NDX → hf_NQ`、`^GSPC → hf_ES` 两行。
  本步补上两者（新增 `dbtools/seed_fundpair_index.py`，幂等），实测：    
  `SZ161130` 配对到 `hf_NQ`、实时 EST = `4.555899`；    
  **EST 表 8 列**（官方EST/EST日期/溢价/参考EST/溢价/实时EST/溢价）、    
  参考数据 **5 行**（`NQ 31137.58` 期货行已出现）。    
  验证实时 EST 的计算路径本来就通（`fundestcalc.compute_realtime()` 早有实现    
  与测试），缺的只是把配对引用建出来。    
  `PairReferenceTest` 钉住配对查询（含 tuple / dict 两种游标行、惰性缓存、    
  不递归）。

  ⑥ **StockHistory 数值核心（2026-10-10，第一刀）**：`php/stockhis.php` 586 行里    
  **纯算数**的那部分 —— `_estSma`、`GetQuadraticEquationRoot`、    
  `_estBollingerBands`、`_estNextBollingerBands`、`_isWeekEnd`、`_isMonthEnd`、    
  `MaxMin`、`_getColorAndOrderArray`，落在 `python/auto/sql/stockhistory.py`。

  分刀的理由：`StockHistory` 的构造同时做"算数 / 查库 / **回写配置文件** / 渲染"    
  四件事，第 3 件有外部可见副作用，不能和纯数值混在一起迁。

  三个必须照抄的地方（都已在 `README_stockhistory_migration.md` 记明）：
  - **数组是最新在前**（`ORDER BY date DESC`），`_estSma` 取的是**开头**那几个；
  - **`_estSma` 的元素个数与除数都是 `avg - 1`** —— `est_sma(x, 5)` 是最近      
    **4** 个的均值。看着像 off-by-one，改掉会让所有均线偏移；
  - **`GetDayOfWeek()` 是 `date('w')`**（周日 = 0 … 周六 = 6），不是 ISO 的      
    1-7；`_isWeekEnd`/`_isMonthEnd` 的比较依赖它，换成 `weekday()` 会静默错一天。
  两条布林式子的差别是 `iAvg-1` vs `iAvg-2`、`factor` 的 `-4` vs `-8`、    
  `sigma` 的 `/2` vs `/4` —— 抄错**不崩**，只是轨道慢慢偏，所以逐项对照。

  对照：新增 `_php_sma_compare.py` + `_php_sma_check.php`，**454 例 0 意外差异**，    
  带 `--selftest`（注入 off-by-one 抓 20 例、注入 sigma 错误抓 5 例、    
  注入收盘边界错误抓 2 例，并断言两条布林式子结果必须不同）。

  已知偏差：`GetNextTradingDayTick()` 走节假日表，本版简化为"下一个工作日"，    
  仅节假日边界会差（180 个 `_isMonthEnd` 用例上没触发）。

  ⑦ **StockHistory 第二刀：查库与切片（2026-10-10）**：    
  `_calcStartDate`、`_ignoreCurrentTradingData`、`_getDayWeekMonthData`、    
  `_get_EMA`、`NeedAfterHourEst`、`_onTest` 全部落地。

  ⚠️ **途中写错一处并被测试抓到**：`GetFromDate` 的判据是    
  `date <= startDate`（`php/sql/sqldailyclose.php:41`），我第一版写成    
  `date >= startDate` —— 于是只返回一行，**所有均线退化成同一个价格且不报错**。    
  正确语义是"先算最新可用日期，再按它**往回**取 620 天"，    
  即 `start_date` 是**上界**。这条已加进对照（自检第 ⑤⑥ 项）。

  另外 `IsStockTradingHourEnd()` 的边界是 `GetHour() <= 16` ——    
  **当地 16:59 仍算未收盘、17:00 才算**，不是 15:00 收盘价出来就算。

  **配置改成可替换的内存实现**：PHP 的 `_configSMA()` 会    
  `$cfg->save_data()` 回写 INI，直译等于"渲染页面会写工作区文件"。    
  本版抽成 `ConfigSmaStore`，默认纯内存 —— 语义等价（缓存永不命中 = 永远重算，    
  结果相同），文件后端留待单独评审并发与测试隔离。

  测试：`test_stockhistory.py` 从 46 例扩到 **85 例**。    
  全量 `auto` **813** / `web` **251**。

  ⑧ **均线段落 `smaparagraph.php`（2026-10-11，第 4 刀）**：225 行里的    
  四个回调（`_callbackQdiiSma` / `_callbackFundPairSma` / `_callbackAhPairSma` /    
  `_callbackFutureSma`）与 `EchoFutureSmaParagraph` 的期货理论溢价全部落地，    
  落在 `web/services/sma_service.py` + `auto/pairestimate.py` +    
  `auto/futurepremium.py`。详见 `python/auto/README_sma_paragraph_migration.md`。

  分三个模块而不是一个的理由：段落里塞了两类**不属于渲染**的东西 ——    
  配对双向估算（`EstFromPair`/`EstToPair`，`MyPairReference` 那一档还没迁）    
  和期货溢价（带时区副作用与 `futurepremium` 读表）。`pairestimate` 特意做成    
  **自由函数 + 鸭子类型**，这样 `MyPairReference` 落地时 `sma_service` 不用改。

  四个必须照抄的点：
  - **`memo` 用 `is False` 严格判断**：PHP 是 `if ($str === false)`，所以      
    `EchoFundPairSmaParagraphs()` 传进来的**空串不会被默认 memo 顶掉** ——      
    那决定了一组配对标的并排展示时只有第一张表带标题；
  - **列数组里第 3 与第 5 列是同一个 `TableColumnPremium` 实例**，      
    列头因此长得一样。看着像笔误，去重就与生产端不一致；
  - **没有 T+1 时 `else` 分支显式补两个 `''`** —— 行是变长的，      
    少追加一格会让整行错位；
  - **又一次遇到 `'0'` 假值**（`_getSmaCallbackPriceDisplay`），      
    一律走 `_php_truthy()`；值是 0 时**那一格照样要画出来**。
  期货溢价三处不照抄：`date_default_timezone_set()` 是**不恢复的**全局副作用    
  （改成显式折算到美东）；天数差是"零点对零点"、跨夏令时是 **68.0417 天**    
  而不是整 68；`round()` 在 PHP 是四舍五入、Python 是银行家舍入，    
  所以走 `futurepremium.php_round()`。

  ⚠️ **一处有意偏离 PHP**：PHP 遇到"没有历史数据"时会摆一整张**全是 0** 的    
  均线表（`_calcStartDate()` 拿不到日期，后面一串 `_estSma()` 全算出 0）。    
  演示库 `^NDX` 一行历史都没有、`stockema50/200` 两张表也是空的，    
  所以加了一道守卫：全部档位都是 PHP 假值时**整段跳过**。    
  没有为此"造 200 天假历史"——`sql/_clean_seed_history.py` 确立的约定是    
  合成值一旦显示就会被误当成真实历史。

  对照：新增 `_php_pairest_compare.py`（13 例）与    
  `_php_futurepremium_compare.py`（13 例），都带 `--selftest`。    
  测试：`test_pairestimate.py` 15 例、`test_futurepremium.py` 21 例、    
  `test_sma.py` 31 例、`test_sma_route.py` 5 例。    
  全量 `auto` **849** / `web` **287**。

  ⑨ **SMA 缓存的文件后端（2026-10-11，单独评审）**：`php/class/ini_file.php`    
  的 `INIFile` + `php/stockhis.php:365 _configSMA()`。第二刀已把配置换成    
  可替换的接口、**默认纯内存**；本刀回答当初挂着的三个问题：
  1. **路径从哪来** —— `MyStockReference` 看着是依赖，其实只贡献一个       
     `strConfigName`，而它是 `GetSymbol()` 的**纯函数**，Python 侧早已算好       
     同一条规则，不用等那一档迁完。真正缺的是 `root`（PHP 用       
     `$_SERVER['DOCUMENT_ROOT']`，常驻进程里没有）→ 改成显式输入，       
     拿不到就退回内存；
  2. **并发写** —— PHP 自己就是 `fopen($f,'w')` 无锁。坏结果有两种：       
     "读到半个文件"用**原子替换**（同目录临时文件 + `os.replace`）治掉；       
     "丢更新"**不治**，因为内容是 `(symbol, start_date)` 的纯函数，       
     最坏只是多算一遍，写错数据不可能，不值得背 `fcntl`/`msvcrt` 两套 API；
  3. **测试隔离** —— 默认内存 + 临时目录 + **绝不拿 CWD 当 root**（有测试把       
     CWD 切到临时目录再渲染，断言事后是空的）。
  新增模块 `python/auto/sql/smaconfig.py`（`stockhistory.py` 被    
  `ModuleIsImportCleanTest` 钉着不许出现 `open(`，落盘只能单列）。    
  文件格式与 PHP 互通，`[段]\n键=值\n`，**保留未知段**。

  两处新增的偏离：PHP 的 `save_data()` 用 `for(reset($g); $key=key($g);
  next($g))` 遍历，**碰到 falsy 键名就停**（会写出空文件），不复制；    
  `parse_data()` 只取 `explode('=')[1]`（值里带 `=` 会被截断），原样保留。    
  另外真库验证时发现：无历史时 PHP 会写 `Date=` + 一串 0，且下次 `'' == ''`    
  判定**命中** —— 演示库 `^NDX` 一开开关就每请求落一份垃圾，加了"没有起始    
  日期就不写"的守卫。

  **默认仍是内存**：要启用得显式设 `PALMMICRO_SMA_CACHE_DIR`。待评审的    
  三条取舍（默认内存可接受吗 / 要不要换成进程内 LRU / 目录要不要接进    
  `web/config.py`）见 `python/auto/README_sma_config_backend.md` 第七节。

  对照：`_php_smaconfig_compare.py` 15 例 0 意外差异，自检 3/3。    
  测试：`test_smaconfig.py` 26 例、`test_sma_route.py` 新增 7 例。    
  全量 `auto` **877** / `web` **294**。

  ⑩ **指数历史种子（2026-10-11）**：SMA 表代码早就完成，但**页面上一�直看不到**。    
  根因是数据**自锁**：daemon 的 `collect_symbols_from_source()` 从    
  `stockhistory` **反推**要抓哪些符号，所以"没有历史 → 进不了抓取集合 →    
  永远没有历史"。而 `^NDX` 正是 QDII 的**估值标的**    
  （`EchoQdiiSmaParagraph()` 用的就是它）。

  新增 `auto/indexhistory.py`（新浪美股日线 jsonp 接口）+    
  `dbtools/seed_index_history.py`（幂等），给 `^NDX` / `^GSPC` 各补 **800 天**历史    
  （2023-08-03 ~ 2026-10-09），并顺带算 `EMA50` / `EMA200` ——    
  `GetBullBear()` 是**直接查** `stockema*` 表的，不是算出来的，那两张表原来    
  是空的，所以页面上不会出现"牛市/熊市"。

  数据来源可核：新浪 `^NDX 2026-10-09 = 30883.15` 与生产端参考数据表    
  **逐字一致**；`QQQ 2026-10-08 = 747.58` 与生产端 `QQQnetvalueclosetable`    
  也一致。Yahoo 已全面 403（`navsource.py` 记过）；东财 `push2his` 指数接口    
  也能用，但新浪这条与生产端同源，优先用。

  **原计划的第一步"改代码加指数白名单"实际不需要做** —— 种子一种下去，    
  daemon 就自动把两个指数收进抓取集合（10 个符号）。这比维护一份白名单更好：    
  机制本身自持续。

  ⚠️ **途中修掉一个真 bug**：`SmaService._row()` 调用    
  `ref.get_percentage_display()`，而 `refobj.EstReference` **没有那个方法**    
  （它只有 `get_percentage_text`）→ `_invoke` 静默返回空串 →    
  **整列「溢价」都是空的**，页面不报错。PHP 的 `GetPercentageDisplay()`    
  是"文本 + 染色"两步（`php/stockref.php:146`），而染色在本项目由    
  `reference_service.percentage_cell()` 负责，所以 SMA 表只要文本。

  **这个 bug 测试抓不到**：`test_sma.py` 的 `FakeRef` 多实现了一个真实类上    
  没有的方法，造成典型的"替身假通过"。已补 `FakeRefContractTest` ——    
  直接对着 `refobj.EstReference` 检查"服务调用的每个方法它都有"，并显式断言    
  `get_percentage_display` **不该**存在。

  验收（对照生产端 `sz161130cn.php`）：
  ```
  参考数据 ^NDX 行   30883.15 / 0.51% / 17:16     与生产端逐字一致
  均线表  5日        30998.38 / -0.37%            生产端 30998.43 / -0.37%
  均线表  20日       30376.32 / 1.67%             生产端 30376.34 / 1.67%
  均线表  牛熊分界   27925.8  / 10.59%            生产端 27926.18 / 10.59%
  顶部「牛市」       有
  页面体积           21 KB → 55 KB
  ```
  参考数据表从 4 行 → **7 行**（多了有价的 `^NDX`）。

  遗留：`TQQQ` 行仍空（它自己没有 `stockhistory`，同 `^NDX` 的解法）；    
  `USDCNH`/`USDCNY` 行仍空（库里即期符号是 `fx_susdcny`，属"符号口径"    
  问题，要先决定参考表显示哪个）。详见    
  `python/auto/README_index_history_seed.md`。

远端分支 `origin/devin/1780933111-migrate-php-core-to-python` 已有  
`python/core/stock_symbol.py` 和 `python/core/sina_fetcher.py`：

- `stock_symbol.py` 是第 1 步的早期版本，缺少交易时段和时区方法，  
  且未接入当前分支的 `python/auto` 包，现已由 `python/auto/core/stocksymbol.py`  
  取代；新模块保留了 camelCase 方法别名，因此按该分支写法调用仍然可用。
- `sina_fetcher.py` 里的抓取动作最终由 `python/auto/source/sinaquotes.py` 实现  
  （结构上仍是先解析后落库，只是把 IO 补齐了，并保持与 PHP 一致的分层）。

## 测试

```powershell
cd F:\woody\woodyweb\python
python -B -m unittest discover -s auto -p "test_*.py"        # 911 个
python -B -m unittest discover -s web\tests -p "test_*.py"   # 431 个
```

注意：Web 层测试需要 Flask 与 PyMySQL，当前只装在系统 Python 3.7 上。  
`python/auto` 的测试在 3.7 与 3.12 上都通过；3.12 上 `test_stockdata.py` 有 2 个  
既有用例需要向临时目录写文件，在受限文件权限环境下会报错，与本迁移无关。

与 PHP 的逐项对照（需要 PHP 在 PATH 中）：

```powershell
cd F:\woody\woodyweb\python\auto
python -B _scratch/_php_port_compare.py        # stocksymbol，6280 个用例
python -B _scratch/_php_numfmt_compare.py      # 数字/时间格式化，87 个用例
python -B _scratch/_php_prefetch_compare.py    # 预取判定，26800 个用例
python -B _scratch/_php_refobj_compare.py      # refobj 纯数学，98 个用例
python -B _scratch/_php_sma_compare.py         # StockHistory 数值核心，436 个用例
python -B _scratch/_php_pairest_compare.py     # 配对双向估算，13 个用例
python -B _scratch/_php_futurepremium_compare.py   # 期货理论溢价，13 个用例
python -B _scratch/_php_smaconfig_compare.py       # 配置路径与 INI 格式，15 个用例
python -B _scratch/_prod_qdiipage_compare.py       # /qdii 与生产端 qdiicn.php 逐格对照
python -B _scratch/_prod_qdiipage_compare.py --selftest   # 自检：证明对照抓得到
python -B _scratch/_php_refobj_compare.py --selftest   # 自检：证明对照抓得到注入的错误
python -B _scratch/_php_sma_compare.py --selftest      # 同上（含"两条布林式子必须不同"）
python -B _scratch/_php_rtrim0_fuzz.py 2000    # rtrim0 随机模糊
```
