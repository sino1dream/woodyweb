# 数据库表重建方案（已执行）

> **状态：已于本次执行完成。** 结果与偏差见文末「执行结果」一节。
> 临时改动记录见 `python/sql/PATCHES.md`。

对应需求：补齐 `stock`、`calibration`、`fundposition`、`netvaluehistory` 等表，
让 Web 页面能显示真实数据。

## 一、现状与核心问题

### 1.1 本机确实没有这些表

| 检查对象 | 结果 |
| --- | --- |
| `127.0.0.1:3306` 的 `stock_data` 库 | 只有 `convertible_bond_meta`、`stock_minute_data` 两张无关表 |
| `C:\xampp\mysql\data\stock_data`、`D:\xampp\mysql\data\stock_data` | 同样只有那两张表 |
| 仓库里的 `.sql` / dump / 备份文件 | **不存在** |

结论：表结构必须重建，没有可直接导入的现成 DDL。

### 1.2 表结构硬编码在生产库名上（关键障碍）

`php/sql/sqltable.php` 的建表语句把库名写死了：

```php
// php/sql/sqltable.php:88
$strQuery = 'CREATE TABLE IF NOT EXISTS `n5gl0n39mnyn183l_camman`.`'.$this->strName.'` (...)'
```

这意味着：**在本地跑 PHP 建表逻辑，它会去连生产库名**，不会建到本地
`stock_data`。要么临时改这两处硬编码（`CreateTable` 与 `AlterTable`），
要么手工整理 DDL。

### 1.3 建表是运行时自动发生的

`TableSql` 的构造函数里直接调用 `$this->Create()`：

```php
// php/sql/sqltable.php:10
public function __construct($strTableName) { $this->strName = $strTableName; $this->Create(); }
```

而 `StockSql::__construct()` 会实例化全部子表类，所以只要跑一次
`InitGlobalStockSql()`（`php/account.php:99`、`php/stockbot.php:126`、
`php/stockdataarray.php:43` 都会调用），**所有表会一次性建出来**。
这是"从 PHP 反推 DDL"的可靠抓手。

## 二、需要迁移的表与文件对应关系

按「Web 页面能不能出数据」分三批。列名由基类推导，已逐条对照过源码。

### 第 1 批：页面能显示数据的最小集合

| 表 | 定义位置（PHP） | 列 | Python 读取位置 |
| --- | --- | --- | --- |
| `stock` | `sqlstocksymbol.php` `StockSql::Create()` | `id` INT UNSIGNED AI PK、`symbol` VARCHAR(32) latin1、`name` VARCHAR(128) utf8、UNIQUE(`symbol`) | `stockdata.py` `get_stock_id` / `get_stock_name` / `get_stock_symbol` |
| `calibrationhistory` | `sqldailytime.php` `CalibrationSql::Create()` | `id`、`stock_id` INT UNSIGNED、`date` DATE、`close` DOUBLE(13,6)、`time` TIME、`num` INT UNSIGNED、FK→stock、UNIQUE(`date`,`stock_id`) | `get_calibration_now` / `get_calibration_from_date` / `get_last_calibration` |
| `fundposition` | `sqlval.php` `FundPositionSql` | `id` INT UNSIGNED PK、`close` DOUBLE(13,6) | `get_position`（读 `close` 当仓位） |
| `netvaluehistory` | `sqlstocksymbol.php` `DailyCloseSql('netvaluehistory')` | `id`、`stock_id`、`date`、`close` DOUBLE(13,6)、FK、UNIQUE(`date`,`stock_id`) | `get_netvalue_close` |
| `stockhistory` | `sqlstocksymbol.php` `StockHistorySql::Create()` | `id`、`stock_id`、`date`、`close` DOUBLE(10,3)、`volume` BIGINT UNSIGNED、`adjclose` DOUBLE(13,6)、FK、UNIQUE(`date`,`stock_id`) | `get_history_close` / `get_history_adjclose` |
| `stockhistorydate` | `sqldate.php` `StockHistoryDateSql` | `id` INT UNSIGNED PK、`date` DATE | 数据日期判定 |
| `holdingsdate` | `sqldate.php` `HoldingsDateSql` | `id` INT UNSIGNED PK、`date` DATE | `_read_date("holdingsdate", ...)` |

### 第 2 批：涉及持仓/配对估值的基金才需要

| 表 | 定义位置 | 说明 |
| --- | --- | --- |
| `holdings` | `sqlholdings.php` `HoldingsSql::Create()` | `id`、`stock_id` INT UNSIGNED、`holding_id` INT UNSIGNED、`ratio` DOUBLE(13,6)、FK→stock。持仓型基金（`calc_with_holdings`）必需 |
| `fundpair` | `sqlpair.php` `PairSql`（`fundpair` 传 `stock` 前缀） | `id` INT UNSIGNED PK、`stock_id` INT UNSIGNED、KEY(`stock_id`)。**实测生成结果里没有 `fund_id`**：`PairSql` 用 `'stock'` 前缀，`Add_id('stock')` 得 `stock_id`（值列）与 `Remove_id` 得 `stock`（被引用表），所以"基金→标的"两列都叫 `id`/`stock_id`，这正是 `get_fund_pair_id()` 的 `WHERE id = ?` 能对上的原因 |
| `stockema50` / `stockema200` | `sqldailyclose.php` `StockEmaSql($iDays)` | 均线，Web 暂不用 |
| `fundest` | `sqlstocksymbol.php` `DailyTimeSql('fundest')` | 估值缓存（`close` + `time`） |
| `quarterreport` | `sqldailystring.php` `QuarterReportSql` | 季报文本（`close` 为 VARCHAR） |

### 第 3 批：站点其它功能（本次不做）

`abpair` / `adrpair` / `ahpair` / `shareshistory` / `sharesdiff` /
`futurepremium` / `stocktick` / `stockgroup` / `stockgroupitem` /
`stocktransaction` / 博客与访客统计相关表。

## 三、方案选择（需要你决策）

### 3.1 生成 DDL 的两种做法

| | A. 让 PHP 自己生成再导出 | B. 手工整理 DDL |
| --- | --- | --- |
| 做法 | 临时把 `sqltable.php` 里两处硬编码库名改成 `stock_data`，跑一次 `InitGlobalStockSql()` 建表，再用 `SHOW CREATE TABLE` 导出 | 按上表逐列手写 `CREATE TABLE` |
| 优点 | 与 PHP 100% 一致，零抄写误差 | 不碰 PHP 源码；DDL 可读、可加注释与索引优化 |
| 缺点 | 临时改生产源码（改完要还原）；MYISAM 引擎会被原样带过来 | 有抄错风险，需要用 PHP 侧再核对一遍 |
| 我的建议 | **用 A 生成、以 B 的形式提交**：先跑 A 拿到权威 DDL，再整理成带中文注释的 `schema.sql`，并保留脚本可重复生成 | |

### 3.2 引擎与字符集：是否照搬 MYISAM

PHP 建表固定 `ENGINE = MYISAM CHARACTER SET utf8 COLLATE utf8_unicode_ci`。

- **照搬 MYISAM**：与 PHP 完全一致，无外键约束实际生效（MYISAM 忽略 FK），
  但 `UNIQUE` 索引保留。
- **改用 InnoDB**：外键真正生效，事务/崩溃安全更好，但删父行会级联删子行
  （PHP 的 `ON DELETE CASCADE` 会真的生效），行为可能与 PHP 不同。

我的建议：**照搬 MYISAM**，理由是与 PHP 行为完全对齐、降低迁移期变量；
等 Python 版稳定后再单独评估换 InnoDB。

### 3.3 表名与库名

- 表名：与 PHP 完全一致（`stock`、`calibrationhistory`、`netvaluehistory`、
  `fundposition`、`holdings`、`fundpair`、`stockhistory`、`stockhistorydate`、
  `holdingsdate`），保证 Python 现有 SQL 不用改。
- 库名：保持 `.env` 里的 `stock_data`，不沿用生产库名
  `n5gl0n39mnyn183l_camman`。

### 3.4 数据从哪来（比建表更关键）

**只有表结构、没有数据，页面仍然是空的。** 三条路：

| | 方式 | 适用 |
| --- | --- | --- |
| 1 | 从生产库导出数据再导入本地 | 你有生产库访问权限时最快、最完整 |
| 2 | 用现有的 `chinastock.py` / `chinamoney.py` 抓数据 | 这两个脚本目前**只读不写**，需要先补写入方法 |
| 3 | 手工插入几只基金的种子数据 | 只为验证页面链路 |

我的建议：**先用 3 验证链路，再按 1 补真实数据**；2 作为长期方案单独排期
（它其实是 `php/test/updatechinastock.php` 等更新脚本的迁移，属于另一条线）。

## 四、文件与架构设计（拟新增/修改）

```text
python/
  sql/                                  # 新增：数据库迁移脚本目录
    schema.sql                          # 核心表 DDL（带中文注释）
    build_schema.py                     # 执行 schema.sql 建表 + 校验
    check_schema.py                     # 只读体检：连库、列表、行数、最近日期
  auto/
    stockdata.py                        # 不动读逻辑；如需要写入能力，另加 write_* 方法
  web/
    services/status_service.py          # MIGRATION_STATUS 增加建表进度条目
  DATABASE_PLAN.md                      # 本文档
```

设计要点：

1. **DDL 单独落文件**，不藏在 Python 字符串里，方便你逐行审核与手工调整。
2. `build_schema.py` 只做「建表 + 建索引」，缺表补建、已存在则跳过
   （对应 PHP 的 `CREATE TABLE IF NOT EXISTS` 语义），不删数据。
3. `check_schema.py` 复用 `stockdata.py` 的连接配置，输出每张表的行数、
   最近日期、缺失表清单，用来回答"现在缺什么"。
4. **不改现有读取代码**：`MySqlFundDataSource` 的 SQL 与 PHP 列名一致，
   建好表即可工作。
5. Web 层不新增路由，建表后用现有 `/fund/qdiilof`、`/fund/<group>/<symbol>`
   即可看到真实数据。

## 五、验收标准

1. `check_schema.py` 报告第 1 批 7 张表存在，无缺失。
2. `/status` 页面 `tables` 段能取到 `stock` 等表的行数（不再是 `null`）。
3. 插入种子数据后，`/fund/qdiilof` 列表能显示名称、仓位、校准值、日期。
4. `/fund/qdiilof/SZ161130` 能显示真实基金名与校准后的估值净值。
5. `python -B -m unittest discover -s web\tests` 与 `-s auto` 全绿。

## 六、需要你确认的点

1. **生成方式**：同意「PHP 生成 + 手工整理提交」吗？还是要我完全手写 DDL？
2. **临时改 PHP 源码**：允许我临时改 `php/sql/sqltable.php` 里的两处硬编码库名
   （改完立即还原、并在文档里记录）吗？若不允许，我改用手工整理。
3. **引擎**：照搬 MYISAM，还是换 InnoDB？
4. **数据来源**：走「生产库导出」（你能提供导出文件或连接信息吗），
   还是先用种子数据验证链路？
5. **表范围**：第 2 批（`holdings`、`fundpair` 等）本次是否一并建？
   持仓型基金缺这两张表时估值会走不通。

审核通过后我再动手，并严格按「先建表 → 再验证 → 再灌数据」的顺序推进。

---

# 执行结果

## 生成与还原

- 用「PHP 生成」路线拿到**权威 DDL**：临时把 `php/sql/sqltable.php` 里 3 处硬编码
  库名改成 `stock_data`，用 `D:\xampp\php\php.exe python/sql/ddl_generate.php`
  建出并导出 14 张表，然后立即还原。
- 还原校验：改前/改后 SHA256 均为
  `A5601668520D7471B3F5A2FA96A13CA465E611010B257B3A90F0D5300F87C824`，
  `git status -- php` 无输出。详见 `python/sql/PATCHES.md`。
- 一个环境坑：PATH 里的 `C:\php` **没有 mysqli 扩展**，必须用 XAMPP 的
  `D:\xampp\php\php.exe`，且不能加 `-n`（会禁用扩展）。

## 实际落地文件

```text
python/sql/
  schema.sql            # 14 张表 DDL，带中文注释（由权威导出整理）
  ddl_generate.php      # 从 PHP 权威生成 DDL 的一次性脚本（保留以便复现）
  build_schema.py       # 按 schema.sql 建表，可重复执行
  check_schema.py       # 数据库体检：表存在性/行数/最近日期
  seed_demo_data.py     # 灌种子数据，并迭代求解基金校准值
  verify_pages.py       # 用真实数据库验证分组列表与估值
  PATCHES.md            # PHP 临时改动记录
```

## 与方案的三处偏差

1. **`fundpair` 没有 `fund_id` 列**。方案文档里写错了，实际是 `id` + `stock_id`
   （原因见上面第 2 批表格的说明）。已修正文档。
2. **建表时表已存在**。生成 DDL 的那一步已经把表建进了 `stock_data`，
   所以随后跑 `build_schema.py` 显示「新建 0 张表」。`IF NOT EXISTS` 语义正确。
3. **`get_calibration_now` 有一个真 bug，已修**。
   `stockdata.py` 一直用短表名 `"calibration"` 查询，而真实表（也是 PHP 的表）叫
   `calibrationhistory`，所以接了真库会直接 `Table 'stock_data.calibration' doesn't exist`。
   此前所有测试都用内存数据源，因此没暴露。修法是在
   `MySqlFundDataSource._daily_table()` 里做一次短名映射。

## 种子数据的口径（重要）

`fundcalc.est_net_value()` 有两条路径，量级完全不同：

- **不传 source**（页面默认）：估值与基金校准值成反比，取「与净值同量级」的值才正常；
- **传 source**：会先用**中间层自己的**仓位/净值反算 hedge_value，因此 source 的尺度
  必须与「中间层校准值」一致，中间层 netvalue 也要同尺度，否则同一 source 会被放大。

所以 `seed_demo_data.py` 不手填校准值，而是：

1. 中间层（QQQ/SPY）用「指数点位」做 calibration、netvalue 取同尺度；
2. 基金层先写候选值，再用真实的 `StockDataArrayBuilder` + `PalmmicroFundCalculator`
   迭代，直到估值收敛到该基金官方净值（实测收敛到 1e-5）。

诊断细节记在 `python/auto/_calibration_probe.py` 的文件头注释里。

## 验证结果

```text
python -B sql\check_schema.py     # 14 张核心表齐全，8 张 Web 依赖表有数据
python -B sql\verify_pages.py     # 分组列表出名称/仓位/校准值/日期；估值 ≈ 官方净值
python -B -m unittest discover -s auto         # 222 个通过
python -B -m unittest discover -s web\tests    # 30 个通过
```

种子数据下 `/fund/qdiilof` 与 `/fund/qdiietf` 能显示真实字段，估值分别为
1.5235 / 1.8765 / 1.2000，与种子里的官方净值一致。

## 仍未完成的

- **真实数据**：本次只灌了 7 个符号的种子数据，其余 40 只仍是空行。
  要显示真实数据，仍需按下文第 3.4 条第 1 或第 2 条路线补数据。
- **`holdings` 表为空**：持仓型基金的估值走不通，需要持仓明细数据。
- **行情层未接进 Web**：`stockref` / `stockprefetch`（第 2 步已迁移）还没有与
  HTTP 抓取结合，所以页面拿不到实时价，只有库里的静态校准值。
- **实时源量级**：`verify_pages.py` 里「带实时源」那一列量级不可信，
  因为真实 calibrationhistory 的原始口径无法用种子数据复现。
