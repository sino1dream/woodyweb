# A 股指数分类页：`stock` 符号补齐 + `SZ160632` 空壳数据（2026-11-10）

> 用户诉求：18 只基金在本地页面显示「库里无此符号」，要求「在库里增加符号，
> 然后 `SZ160632` 的空壳数据一并补了」。
> 本文记录 PHP 源、查证、两步补丁的范围取舍、实跑结果与 known deviations。
> `.dsh/` 被 gitignore，运维侧同步写在
> `.dsh/skills/palmmicro-ops/SKILL.md` §8.8。

## 一、PHP / 生产端依据

| 依据 | 位置 | 用途 |
| --- | --- | --- |
| 符号数组 | `php/stock/stocksymbol.php:88` `ChinaIndexGetSymbolArray()` → `core/stocksymbol.py::china_index_get_symbol_array()` | 分类页 27 只的**唯一事实源** |
| 分类页表格 | 生产端 `woody/res/chinaindexcn.php`（快照 `auto/_scratch/_prod_category_cache/chinaindexcn.php.html`，2026-11-10 抓取） | 27 个「名称」列，逐位对齐数组顺序 |
| 基金详情 | 生产端 `woody/res/sz160632cn.php` | 官方 EST / 参考数据 / 基金指数对照表 / 仓位 / 校准值 |
| 页面状态判定 | `web/services/reference_service.py::quote_state_of()` | `stock_id` 为空 → 「库里无此符号」；有 `stock_id` 无行情 → 「无行情快照」 |
| 默认仓位 | `php/stock/stocksymbol.php:1003` `GetDefaultPosition()` → `core/stocksymbol.py::get_default_position()` | `IsLofA() ? 0.95 : 1.0`，**缺行回落** |

用户点名的 18 只全部落在 `china_index_get_symbol_array()` 尾部 10 只 LOF 组
与前段数组里。查库发现**整个 27 只数组只有 `SZ160632`（`id=296`，酒LOF）
有 `stock` 行**，其余 26 只全缺（其中 8 只是科创/沪深 300 ETF，用户没点名，
但同属一个分类页，`/category/chinaindex` 上实测 26 次「库里无此符号」）。

## 二、为什么分两个脚本，不合成一步

「增加符号」与「补齐数据」是两件事，混在一起会越过用户授权范围：

| 脚本 | 范围 | 默认 |
| --- | --- | --- |
| `dbtools/patch_china_index_symbols.py` | 只写 `stock(symbol, name)`，**26 行** | dry-run，`--apply` 才写 |
| `dbtools/patch_china_index_fund_data.py` | 只给**点名的基金**补四张表 | 默认只补 `SZ160632` |

合成一步会给 26 只都灌净值/日线/快照，而用户只点名了 `SZ160632`。
`patch_china_index_fund_data.py` 的 `--symbols` 支持多只，但
`test_china_index_patches.py` 钉住「默认不含其余 26 只」，防止以后有人
顺手把范围放大。

### 名称为什么照抄生产端、不抓新浪

`seed_p0_symbols.py` 那套「名称从新浪抓」适用于 QDII；新浪对这批 A 股
LOF/ETF 返回的是**简称**（`SZ160706 -> 嘉实300A`、`SZ161005 -> 富国天惠`），
而生产端表格用 LOF **全称**（`沪深300LOF` / `富国天惠LOF`）。
页面「名称」列读 `stock.name`，要逐位对齐就得用全称。
`_scratch/` 被 gitignore，所以 27 个名称就地写进脚本的
`PRODUCTION_NAMES`，并由 `stock_rows()` 校验数组与名称表**等长** ——
序号对不上就 `RuntimeError`，宁可停手也不要把「煤炭LOF」写到
「白酒基金LOF」头上。

## 三、`SZ160632` 补了什么、按什么语义

补前 9 张相关表在这个 `stock_id` 上全 0 行（注意
`netvaluehistory` / `stockhistory` / `calibrationhistory` 的条件列是
`stock_id`，`realtimequote` / `stockhistorydate` / `fundposition` 是 `id`）。
页面表现为估值表 `0 / 0`、参考数据「无行情快照」、五档盘口空。

| 表 | 数据源 | 写入语义 | 实跑 |
| --- | --- | --- | --- |
| `netvaluehistory` | `EastmoneyNavSource`（东财 `f10/lsjz`，与 `navcli` 同源） | **只补不覆盖**，同 `backfill_nav_history.py` | 20 行，`2026-10-13 ~ 11-09`，最新 `0.2800` |
| `stockhistory` | `updater.asharehistory`（新浪 `CN_MarketData.getKLineData`） | 复用 `seed_index_history._write_history()`，`ON DUPLICATE KEY UPDATE` | 30 行，`2026-09-21 ~ 11-09` |
| `stockhistorydate` | 日线里最新那一天 | `QuoteStore.write_stock_history_date()`，对齐 `quotecli.save_reference()` | 1 行，`2026-11-09` |
| `realtimequote` | `updater.quoteupdater.QuotesUpdater`（新浪实时 + 五档） | 与 daemon / `--once` 同一条链路：没抓到价**不写库**，避免把上一轮好值覆盖成空串 | 1 行，`0.279 @ 2026-11-10 15:00:00`，盘口有 |

实时快照走**单独一个连接并显式 `commit()`**：
`QuotesUpdater._save_to_store()` 只 `execute` 不 `commit`（daemon 靠常驻连接），
而 `RealtimeQuoteStore.save()` 失败时会 `rollback()` —— 单独连接能把这次
回滚隔离在快照表，不牵连已经提交好的净值/日线。
脚本顺序是「预演 → 备份 → 写净值/日线 → 备份后再抓一次写快照」，
预演时用 `store=None` 的 updater 只抓网络，保证写库严格发生在备份之后。

### 明确不写的两处（scope trade-offs）

| 不写 | 理由 |
| --- | --- |
| `fundposition` | 生产端 `sz160632cn.php` 的仓位 `0.95` 是 `GetDefaultPosition()` 对 `IsLofA()` 的回落，不是管理员存进表里的值（`README_fundposition_migration.md` §「缺行回落」）。写一行 0.95 会把代码默认伪装成实测仓位。 |
| `fundpair` / `calibrationhistory` | 生产端是 `SZ160632 → SZ399987`（中证酒）、校准 `14373.3143 @ 2026-11-09`。本地 A 股指数这一族（基金 → 中证指数）的配对链路**尚未整体迁移**，`SZ399987` 连 `stock` 行都没有。逐只手写校准值等于把「没迁的功能」伪装成「已迁的数据」。 |

## 四、净值清单口径：108 → 109

`nav_symbols()` 现在是 **109 只** =
102 只 QDII `fund_symbols()` + 6 只 A 股商品基金 `china_future_get_symbol_array()`
+ 1 只 `SZ160632`（新增常量 `CHINA_INDEX_NAV_SYMBOLS`）。

加第 109 只的原因：它本来就在 `navcli` 的
`NavUpdater.collect_targets()` 里（按 6 位代码从 `stock` 表收集），
而日常只跑 `--all-nav`、不单独跑 `navcli`；不纳入就会让净值停在补齐那天，
重演前几轮刚修掉的「净值停在旧月份」。

**其余 26 只故意不纳入**：它们目前只有 `stock` 行，净值/日线/快照全空，
纳入会让 `--report` 一次报出 26 只空壳断档。等 A 股指数配对链路整体迁移
后再一起放进清单。`test_backfill_nav_history.py` 与
`test_china_index_patches.py` 都钉住了这个边界。

## 五、结果对照

`/category/chinaindex`：27 只全部摆出生产端名称，「库里无此符号」出现次数
**26 → 0**；其余 26 只显示「无行情快照」（符合「只增加符号」的授权范围）。

`/fund/SZ160632` 与 `/fund/chinaindex/SZ160632`：

| 段落 | 补前 | 补后 |
| --- | --- | --- |
| 参考数据 | `酒LOF（无行情快照）` | `0.279 / 0 / 2026-11-10 / 15:00 / 酒LOF` |
| 五档交易 | 「暂无五档盘口数据」 | 涨停 `0.307`、跌停 `0.251`、买卖各五档齐全 |
| 基金溢价记录 | 空 | 10 行，如 `11-09 0.279 / 0.28 / -0.36%` |
| 历史价格 | 空 | 10 行，含成交量与复权价 |

## 六、Known deviations（与生产端的差，别当 bug 重查）

1. **估值表三档**：本地「官方EST `0.279` / EST日期空 / 溢价 `0`」，
   生产端 `0.28 / 2026-11-10 / -0.57%`。本地
   `get_official_net_value()` 回落到场内价、`get_official_date()` 为空，
   是 A 股指数配对链路（`fundpair` → `SZ399987` + `calibrationhistory`）
   **未迁**的表现，不是数据缺口，补数据补不出来。
2. **其余 26 只仍「无行情快照」**：只补了 `stock` 行。若要补数据，
   `patch_china_index_fund_data.py --symbols <逗号串> --apply` 已支持，
   但那是一个独立的范围决定。
3. **`stock` 表补 26 行后**，`navcli` 的 `collect_targets()` 会多认这 26 只
   （它按 6 位代码扫 `stock` 表）。日常不跑 `navcli`，影响有限；
   若以后恢复 `navcli`，要先决定这 26 只的净值口径。
4. 实时快照是**收盘后**抓的 `0.279 @ 15:00:00`，生产端页面当时是盘中
   `0.278 @ 14:36:57`，属时点差，不是数据源不一致。

## 七、备份 / 幂等 / 验证

备份（均 `backup_db.py --lock-tables`，35 张表 / 约 2.73 MB）：

```text
E:\mysqldata\stock_data_20261110_144616.sql   动库前手工备份
E:\mysqldata\stock_data_20261110_145926.sql   patch_china_index_symbols.py 内置
E:\mysqldata\stock_data_20261110_150504.sql   patch_china_index_fund_data.py 内置
```

幂等：两个脚本都默认 dry-run；`stock` 已有行不动、净值只补不覆盖、
日线 `ON DUPLICATE KEY UPDATE`、快照按主键 UPSERT。

验证：

```text
python -B dbtools\patch_china_index_symbols.py            # 26 行待插入
python -B dbtools\patch_china_index_fund_data.py          # 20 净值 / 30 日线 / 快照
cd auto && python -B -m unittest test_backfill_nav_history test_china_index_patches -v
```

`test_china_index_patches.py` 覆盖 `stock_rows()` 的逐位对应与长度守卫、
`resolve_symbols` 的默认范围/去重/保序、`plan_nav_rows()` 的只补不覆盖
（含 `date` 对象与字符串混合）。

## 八、日常维护

`--all-nav --days 3 --apply` 现在会自动带上 `SZ160632`，无需额外命令。
两个补丁脚本都是一次性补齐工具，**不进每日例程**。
运维口径见 `.dsh/skills/palmmicro-ops/SKILL.md` §8.8 与 §10.1。
