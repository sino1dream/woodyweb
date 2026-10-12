# 行情抓取与溢价率迁移说明

本文档记录「把行情抓取接到 Python、并算出基金溢价率」这一步。
对应 `python/WEB_MIGRATION_PLAN.md` 里第 2 步遗留的最后一环：**当初只迁移了
解析，没有迁移抓取**。

## 一、先回答那个问题：为什么说「已迁移但没接 HTTP」

第 2 步迁移 `stockref.php` / `stockprefetch.php` 时，我刻意把 IO 做成了回调：

| PHP 里的动作 | 第 2 步的做法 | 缺的东西 |
| --- | --- | --- |
| `PrefetchLoadSinaData()` 读缓存 | 注入 `quote_loader` 回调 | 没有人实现这个回调 |
| `GetSinaQuotes()` 抓行情 | `fetch_quotes` 参数 | 没有网络实现 |
| `PrefetchSaveSinaData()` 写缓存 | `save_quote` 回调 | 没有人实现 |
| `url_get_contents()` + `GetSinaDataUrl()` | **完全没有** | 整个 HTTP 层 |

所以：**解析逻辑（`stockref.py`）已经完整迁移并有 6280 个 PHP 对照用例，
但它拿不到数据** —— 没人把「行情的字节」送到它手里。写测试时是我手工塞字符串
进去的，这正是它能全部通过、却在真实环境下一个字都取不到的原因。

本步补上的就是这张表最右侧那一列。

## 二、新增内容

| 文件 | 对应 PHP | 职责 |
| --- | --- | --- |
| `python/auto/source/sinaquotes.py` | `php/stock.php::GetSinaQuotes()`、`php/externalurl.php::GetSinaDataUrl()`、`php/url.php::url_get_contents()` | HTTP 抓取、Referer、GBK 解码、按符号切行、60 秒缓存 |
| `python/auto/sql/quotestore.py` | `php/stock/sqlstocksymbol.php::StockHistorySql::WriteHistory()`、`php/stock/sqldailyclose.php::WriteDaily()`、`php/stock/mystockref.php::_updateStockHistory()` | 行情/净值落库、字段校验、溢价率 |
| `python/auto/cli/quotecli.py` | `php/stock.php::StockPrefetchArrayData()` 那条链路 | 命令行：抓取 + 落库 + `--check` 预览 |
| `python/sql/sync_demo_netvalue.py` | —— | 把种子净值与真实市价对齐，让溢价率有意义 |

### 抓取层的三个关键细节

1. **Referer 必须带**：`hq.sinajs.cn` 不带 `Referer: https://finance.sina.com.cn`
   会返回 403（实测）。
2. **解码顺序不能反**：必须**先 UTF-8 后 GB18030**。我先写成 GB18030 优先，
   结果把 UTF-8 的港美股响应解成乱码 —— `gb18030` 几乎能把任意字节序列解成
   "看似合法"的字符串，永远不抛 `UnicodeDecodeError`，于是回退分支形同虚设。
3. **返回文本按请求顺序排列**：`fetch()` 返回的每一行与传入的 `symbols` 一一对应，
   直接喂给 `stockprefetch._prefetch()` 就能逐行回写，与 PHP 的
   `explode("\n", $str)` 完全一致。

### 溢价率

溢价率 = 市价 / 单位净值 - 1，对应 PHP 里把 `StockGetPercentage()` 用在
「场内价 vs 净值」的场景。

数据来源：市价取 `stockhistory` 最新收盘，净值优先取 `netvaluehistory` 最新一条、
取不到则退回 `stockhistory`（ETF 靠行情、场外基金靠净值）。任一侧缺失时显示空白，
不显示 0 —— 这一点与 PHP 的容错习惯一致。

列表页因此在 PHP `fundlistparagraph.php` 的「股票代码 / 跟踪 / 仓位 / 校准值 /
日期 / 参考值」基础上，把最后的"参考值"落成**市价**与**溢价率**两列；
溢价率正值红色、负值绿色（国内涨跌配色）。

## 三、实时验证（真实联网）

`quotecli.py --check` 于 2026-09-30 实测成功：

```text
请求 5 个符号：sz161130,sz161125,sh513100,gb_qqq,gb_spy

SZ161130   -> sz161130   名称=纳指LOF          价=4.796   昨收=4.727   日期=2026-09-30  时间=15:00:00
SZ161125   -> sz161125   名称=标普500          价=3.275   昨收=3.24    日期=2026-09-30  时间=15:00:00
SH513100   -> sh513100   名称=纳指ETF国泰        价=2.352   昨收=2.324   日期=2026-09-30  时间=15:34:59
QQQ        -> gb_qqq     名称=纳百ETF          价=742.03  昨收=739.77  日期=2026-10-01  时间=16:00:00
SPY        -> gb_spy     名称=SPDR标普500 ETF  价=763.99  昨收=762.63  日期=2026-10-01  时间=16:00:00
```

落库后页面（真实数据库 + 真实行情）：

```text
/fund/qdiilof    股票代码 名称 仓位 校准值 日期 市价 溢价率
                 SZ161130 易方达标普信息科技LOF 0.95 0.829884 2026-01-02 4.796 -0.5%
                 SZ161125 易方达标普500LOF    1.0  1.215251 2026-01-02 3.275 -0.5%
/fund/qdiietf    SH513100 国泰纳斯达克100ETF   0.98 1.692196 2026-01-02 2.352 -0.5%
```

**-0.5% 是 `sync_demo_netvalue.py` 按「折价 0.5%」反算出来的**，不是市场真实
溢价率（真实的 unit net value 要靠场外净值数据，见下方"仍未完成"）。

## 四、顺带修掉的一个解析 bug

实时抓取暴露了 `stockref._parse_datetime()` 的缺口：新浪美股盘前/盘后时间格式
`Jun 08 11:36AM EDT`（月份英文缩写 + AM/PM + 时区缩写）解析不了，直接抛
`ValueError`。这是**只有真实数据才会触发**的问题 —— 离线样本里我用的是
`2026-06-08 23:36:45` 这种带年份的格式，所以测试全绿。

修法：`_parse_datetime()` 增加美式格式分支 `_parse_us_style_datetime()`，
年份用当前年占位（调用方 `_convert_date_time_from_us()` 随后用行情里的
`$ar[29]` 覆盖年份，与 PHP `strtotime()` + `$strYear` 的行为一致）。

## 五、测试与验证

```powershell
cd F:\woody\woodyweb\python
python -B -m unittest discover -s auto -p "test_*.py"        # 245 个
python -B -m unittest discover -s web\tests -p "test_*.py"   # 30 个

python -B auto\quotecli.py SZ161130 --check                  # 真实抓取（需网络）
python -B auto\_quote_probe.py --parse                       # 离线走通"解析 -> 落库"
python -B auto\_quote_probe.py                               # 查库里的市价/净值/溢价率
python -B sql\sync_demo_netvalue.py                          # 对齐种子净值后重解校准值
```

新增 `test_sinaquotes.py`（23 个）覆盖：按符号切行、GBK/UTF-8 解码、
Referer 与 UA 请求头、按顺序返回、失败返回 `None`、缓存命中与过期、
`load()` 的文件名映射、落库校验（空日期/周末/字段缺失）、溢价率各种边界。

## 六、仍未完成

1. **实时行情还没接进 Web 请求**。现在有两条独立路径：
   * 页面读库里的静态值（`stockhistory` / `netvaluehistory`）—— 已经能看到真实市价；
   * `quotecli.py` 手动抓取并落库 —— 需要人工执行。
   要"打开页面就是最新价"，还需要一个定时任务或请求内的抓取策略（要考虑
   新浪的频率限制与 PHP 那边"每分钟最多一次"的约定）。
2. **净值仍是占位值**。真实单位净值要从场外净值数据源取（PHP 走
   `php/stock/fundref.php` / `yahoostock.php` 那条线），本步没有迁移。
   所以当前溢价率的绝对数值不可信，只有计算链路是通的。
3. **只覆盖了行情类数据**。`updatechinastock.php` 那条线（从新浪 Market_Center
   更新股票**名录**）此前已由 `chinastock.py` 迁移完成，本次不涉及。
4. **`holdings` 相关**按你的要求暂不涉及（等季度持仓抓取方案确定）。
