# 持仓写入链路迁移：`_qdiimix.php` + `submitholdings.php`

对应 PHP：

| PHP | 行数 | Python |
|---|---|---|
| `woody/res/php/_qdiimix.php`（`Create` / `_updateStockHoldings` / `EchoAll` / `GetQdiiMixLinks`） | 154 行 | `auto/updater/holdingsupdater.py` + `auto/cli/holdingscli.py` |
| `woody/res/php/_holdingscsvfile.php` | 128 行 | `auto/updater/etfholdings.py`（`EtfHoldingsFile`） |
| `woody/res/php/_etfholdings.php` | 59 行 | 同上（`add_holding` / `convert_holding`） |
| `woody/res/php/_sseholdings.php` | 27 行 | 同上（`SseHoldingsFile`） |
| `woody/res/php/_szseholdings.php` | 98 行 | 同上（`SzseHoldingsFile`） |
| `woody/res/php/_updateholdings.php`（`StockOptionDecodeHoldings` / `UpdateStockOptionHoldings`） | 88 行 | `holdingsupdater.stock_option_decode_holdings` / `update_stock_option_holdings` |
| `woody/res/submitholdings.php`（`AdminProcess`） | 31 行 | `holdingsupdater.process_admin_holdings` |
| `php/csvfile.php`（`CsvFile` 基类） | 155 行 | **未迁**（磁盘文件那一层，见 §7） |
| `php/sql/sqlval.php` 的 `FundPositionSql` | — | `auto/sql/positiontable.py` |

配套的读侧在 `README_holdings_migration.md`（CK4：`HoldingsReference` +
`/stock/<symbol>` Holdings 分支）。**读侧只管展示，本文管的是"数据从哪来"**。

> 本文引用的 `pitfalls #NN` 在 `.dsh/skills/palmmicro-php-to-python/references/`
> （**`.dsh/` 被 gitignore，不随仓库提交**）。读不到就当它们是本地批注，
> 关键结论本文都就地写了一遍。

## 1. 两条写入路径，按基金类型分

PHP 的 `_updateStockHoldings` 在 `in_arrayLofMix($strSymbol)` 上分岔，**两条路
一条自动一条人工**：

```
_QdiiMixAccount::_updateStockHoldings($strSymbol)
├─ in_arrayLofMix(symbol)                       ← QDII-LOF-FOF 族（黄金/原油 LOF）
│   └─ CheckHoldingsDate(netvalue_date)         ← 每只成分股在净值日都有收盘价？
│       └─ WriteDate(stock_id, netvalue_date)   ← 只推进持仓**日期**，明细不动
│                                                （等管理员在 submitholdings 页提交）
└─ 否则                                          ← QDII-ETF 族
    ├─ 沪市：ReadSseHoldingsFile()               ← 下载申赎清单 XML，整份替换
    └─ 深市：ReadSzseHoldingsFile()              ← 下载 PCF 文本，整份替换
```

**为什么 LOF 族只能人工**：LOF 不公布申赎清单，交易所没有"这只 LOF 持有哪些
股票"的官方文件，所以明细只能由管理员在网页上填；ETF 则有每日 PCF/XML，
可以全自动。

演示库里的样本：`SZ164701`（QDII-LOF-FOF，持仓 GLD 100%）走 LOF 支线；
`SH513050`（沪市）、`SZ159605`（深市）走 ETF 支线。

## 2. 四道闸门（`_qdiimix.php:55-82` 逐条照抄）

任何一道不过就**直接返回，不写任何东西**：

| # | PHP | 含义 |
|---|---|---|
| 1 | `$strNetValueDate == $strHoldingsDate` | 净值日 == 持仓日，已经最新 |
| 2 | `$strHoldingsDate == $ref->GetOfficialDate()` | 持仓日 == 官方 EST 日期，官方估值已用新净值，再补是同一天重复写 |
| 3a | ETF：`$strEstDate == $strNetValueDate` | `fundest` 还没到新净值日 |
| 3b | ETF：`$strEstDate == $strDate`（`!in_arrayHkMix` 时） | "a day too early" —— 当天清单不是最终版 |
| 3c | ETF：`$iHourMinute < 930` / `> 1455` | 开盘前不取、收盘后停止自动更新 |
| 4 | LOF：`!CheckHoldingsDate($strNetValueDate)` | 成分股在净值日缺收盘价，不推进日期 |

三个**不显眼但会错**的点：

* **闸门 2 没有假值守卫**。持仓日与官方日期都取不到时（PHP 两侧都是
  `false`）同样命中提前返回。Python 用 `None` 复刻（`None == None`）。
* **港股混合族豁免 3b**（`in_arrayHkMix`）：港股 16:00 收市，当晚清单就是
  最终版，而 QDII 净值要等美股 —— 所以对港股混合族，"est 日 == 行情日"
  不是"早一天"。豁免的符号共 9 个（`SH513090`/`SH513230`/`SH513750`/
  `SH513990`/`SZ159567`/`SZ159570`/`SZ159615`/`SZ159751`/`SZ159792`）。
* **时间窗是闭区间**：`930` 与 `1455` 都放行（PHP 用 `<` / `>`）。

第 3c 条 PHP 的原注释解释了为什么是 14:55 而不是收盘：美股休市后第二天的
盘前有可能出现"看上去像休市日数据"的老文件，导致 5 分钟一次频繁下载。
**这个闸门是防抖，不是防过期**。

## 3. 深市 PCF（`_szseholdings.php` → `SzseHoldingsFile`）

文件：`reportdocs.static.szse.cn/files/text/etf/ETF15960520250901.txt`
（GB2312、CRLF，按**行情日**拼文件名）。

### 3.1 行的四种形状

| 行 | 处理 |
|---|---|
| `2025-08-28日 信息内容` | 取**第一张表**（T-1）的日期 → `SetDate`（顺带 `CalcCurrency`） |
| `现金差额：  820.72元` | `SubCash` |
| `最小申购、赎回单位资产净值：  1100754.99元` | `AddCash` |
| `证券代码 证券简称 股份数量 …` | 表头 → `bUse = true` + `DeleteAllHoldings()` |
| 其余 8 列行 | 持仓行 |

现金两行用**全角冒号**（`：`），先替换成半角再 `explode(':')`；切出来正好
2 段才当现金行处理（持仓行里没有冒号，走 `count == 1` 分支）。

### 3.2 持仓行的切分：`array_filter` 连 `'0'` 一起滤

```php
$arWord = array_filter(explode(' ', $strLine));   // 默认回调：剔 falsy
```

宽间隔分隔导致 `explode` 出大量空串，`array_filter` 一并清掉；**`'0'` 也是
falsy**，于是"股份数量"列为 `0` 的行（申赎现金 `159900`）列数从 8 掉到 7，
被 `count($arWord) > 7` 放过后又因列索引整体前移而跳过 —— `159900` 因此
永远进不了持仓表。**复刻这个行为**（`etfholdings._php_array_filter`）。

### 3.3 汇率判据：`is_numeric($strHolding)`

`GetMarketVal` 里一行三元式：

```php
$fForex = is_numeric($strHolding) ? $this->fHKDCNY : $this->fUSDCNY;
```

**港股代码补零到 5 位后仍是数字串**（`'01024'`），所以走港元；美股代码
（`'GLD'`）走美元。`ConvertHolding` 的顺序因此是语义的一部分：
`700` → `00700`（港股），`BRK/B` → `BRK.B`（斜杠换点）。

### 3.4 `CalcCurrency` 按 **stock_id** 查汇率

`GetId('USCNY')` 先拿 id（演示库 501/502），再按 id 查 `netvaluehistory`
当天的中间价；取不到当天**回落** `GetCloseNow`（最新一条）。中间价偶尔晚
一天公布，没有这个回落整份清单的市值都会按 0 汇率算。

PHP 末尾 `fUSDCNY / fHKDCNY` **无零守卫**（除零抛 `DivisionByZeroError`）；
Python 版有守卫（`f_hkdcny` 为 0 时给 0.0）—— 有意偏差，见 §8。

## 4. 沪市 XML（`_sseholdings.php` → `SseHoldingsFile`）

URL：`query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=513050`
（`symbol[2:8]`）。

```xml
<Funds>
  <PreTradingDay>20261021</PreTradingDay>          ← 持仓日（T-1，要 ConvertYMD）
  <NAVperCU>977582.66</NAVperCU>                   ← 分母（AddCash）
  <EstimatedCashComponent>-15.43</EstimatedCashComponent>   ← 分子修正（SubCash）
  <ComponentList>
    <Component>
      <InstrumentID>600519</InstrumentID>
      <InstrumentName>贵州茅台</InstrumentName>
      <SubstitutionCashAmount>1474.53</SubstitutionCashAmount>   ← 市值口径
```

### 4.1 沪市的现金修正**从来没生效过**（照抄）

PHP `_sseholdings.php:12` 读的标签名是 `EstimateCashComponent`（**少一个
`d`**），而 XML 里的实际标签是 `EstimatedCashComponent`。SimpleXML 取不到
给空串，`floatval('')` = 0，于是 `fCash` 恒等于 `NAVperCU`，`SubCash` 那一步
永远是加 0。

这是**生产端的真实行为**（用真实样本 `_sse_513050.xml` 逐例比对确认：
PHP 的 ratio 对应 `fCash = NAVperCU` 单独的值）。按项目"照抄别修正"的规矩，
Python 侧同样读 `EstimateCashComponent`，并在 docstring 里记明。修不修、
什么时候修，是数据 Owner 的决定，不是迁移者的。

## 5. 管理员编码串（`submitholdings.php`）

### 5.1 入参形状

`DebugDecode($str)` = `json_decode('{'.$str.'}', true)` —— 入参是**去掉花
括号的 JSON 对象体**：`?holdings="GLD":100,"SPY":50`。

### 5.2 归一化口径（`_updateholdings.php:21-25`）

```php
$fPos /= 100.0;
foreach ($arRatio as $strHoldingId => $fRatio)
    $arRatio[$strHoldingId] = $fRatio / $fPos;
return $fPos;
```

所以 `"GLD":100,"SPY":50` 进来：`fPos = 1.5`，两条比例落库成 `66.666…` 与
`33.333…`（**不是原值**）。少了这一步，仓位 150% 的基金明细会合计 150。

### 5.3 三种 falsy（pitfalls #100 的同款管道）

`if ($strRatio != '0')` 在 PHP 8 是**数字串数值比较**：

| 入参 | 跳过？ | 落库比例 |
|---|---|---|
| `'0'` | 跳 | — |
| `'0.0'` | 跳（数值等于 0） | — |
| `''` | **不跳**（字符串比，`'' != '0'` 为真） | `0.0`（`floatval('')` = 0，归一化后还是 0） |
| `'abc'` | 不跳 | `0.0` |

### 5.4 写序（`UpdateStockOptionHoldings`）

**仓位 → 持仓日 → 整份替换明细**（`DeleteAll` + 逐行 `InsertHoldingId`）。
顺序有意义：中途失败时留下"新仓位 + 旧明细"比"新明细 + 旧仓位"更容易被
下一次更新纠正（仓位影响估值，明细影响测算表）。

## 6. CLI：`python -B -m auto.cli.holdingscli`

```bash
# 诊断（默认 dry-run，只算闸门不写库）
D:/python/python.exe -B -m auto.cli.holdingscli
D:/python/python.exe -B -m auto.cli.holdingscli --symbol SH513050

# 写库
D:/python/python.exe -B -m auto.cli.holdingscli --symbol SH513050 --apply
D:/python/python.exe -B -m auto.cli.holdingscli --all-qdii --apply

# 管理员写入口（对应 submitholdings.php）
D:/python/python.exe -B -m auto.cli.holdingscli --admin SZ164701 \
    --holdings '"GLD":100' --apply
D:/python/python.exe -B -m auto.cli.holdingscli --admin SZ164701 \
    --fundposition 1.00 --apply
```

`dry_run` 与 `trace` 是 `update_stock_holdings` 的入参，**诊断与写库共用
同一份闸门逻辑**，不会两处漂移。

注意 `--admin` **没有 `--date`**：PHP 读的是基金现有的持仓日
（`submitholdings.php:18` 的 `$ref->GetHoldingsDate()`），表单只改比例
不改日期。

CLI 调用 `process_admin_holdings` 时恒传 `admin=True`：命令行从来不对公网暴露，
权限由"谁能在服务器上跑"承担。Web 侧则必须走 `admin_auth.is_admin()`（§7.1）。

## 7. 架构偏差：realtime 抓取只归 daemon

PHP 的 `_updateStockHoldings` 挂在**页面渲染**里（`Create()` → 每次渲染
顺带更新）。本项目 `refquote` 的架构原则是 **realtime 抓取只归 daemon、
web 只读快照**，所以下载/写库编排放 `cli` 定时调用（建议挂在美股收盘后的
凌晨窗口，与 `quotecli`/`fundestcli` 同一批）。

`submitholdings.php` 外层的 `IsAdmin()` 登录闸门（账号体系）按用户要求暂缓，
但**闸门本身已经补上**（2026-10-26）：`process_admin_holdings` 的 `admin` 是
**必填关键字参数**，见 §7.1。

### 7.1 权限闸门的形状：必填关键字参数

PHP 的结构是：

```php
if ($this->IsAdmin())        // 闸门在外面
    $this->AdminProcess();   // 里面才是 "_updateStockHoldings"
```

只迁里面那半截，等于留了个**未鉴权的写入口**。所以本版把闸门做进函数签名：

```python
def process_admin_holdings(source, symbol, params,
                          holdings_ref_builder=None, *, admin) -> dict:
```

| 决定 | 理由 |
|---|---|
| `admin` 是**必填**关键字参数 | 给默认值 = 没补洞。少写一个参数是 `TypeError`，不是静默放行 |
| 关键字专用（`*`） | 防止按位置传错（第 5 个位置参数恰好是 `True` 的偶然写法） |
| `admin=False` **立刻返回** `{"denied": True, ...}` | 一个字节都不碰数据源 —— 测试用"一碰 `connection` 就抛"的替身守住 |
| 调用方如何取 admin | Web 侧 `web/services/admin_auth.is_admin()`；CLI 侧恒 `admin=True`（命令行不对外暴露） |

返回值形状：

```python
{"symbol": str, "stock_id": int | None, "updated": bool,
 "position": float | None, "denied": bool}
```

`denied=True` 时其余字段都是初始值，调用方靠它区分"被拒"与"没算出东西"。
Web 路由 `/holdings/submit` 收到 `denied=True` 直接 403（`admin_auth.denied_message()`），
不会把"解析失败"和"没权限"混成一条提示。

`IsAdmin()` 的等价物只有一处：`admin_auth.current_admin_email() == ADMIN_EMAIL`
（`woody@palmmicro.com`，`php/email.php:2`）。演示环境默认身份**就是**
`ADMIN_EMAIL` 本身，所以"不用登录也看得到管理员控件"不是绕过了判断，而是默认
身份恰好是管理员；`PALMMICRO_ADMIN_EMAIL=guest@example.com` 立刻掉到 `False`。


## 8. 已知偏差（相对 PHP，全部朝安全方向或形制方向）

| # | PHP | 本版 | 理由 |
|---|---|---|---|
| 1 | `GetId()` 取不到的符号写一条 `holding_id=0` 的明细 | 不写这行，但**比例照算进 `fPos`** | 表单符号本该来自库内清单；`$fPos +=` 在 PHP 里与查 id 无关，所以 fPos 仍逐字一致 |
| 2 | 一条有效比例都没有时返回 `0.0` | 返回 `(None, {})` | 调用方都把假值当"不写库"，写库行为一致；同时防住"全打错的表单清空真实持仓" |
| 3 | `fUSDCNY / fHKDCNY` 除零抛 `DivisionByZeroError` | 守卫，给 0.0 | 页面挂掉不如给 0 |
| 4 | `DebugDecode('')` 解出 `[]`（假值） | `debug_decode('')` 返回 `None` | 调用方的 `if` 判定等价 |
| 5 | `_updateStockHoldings` 在页面渲染里跑 | CLI 定时跑 | 见 §7 |
| 6 | `CsvFile` 基类的磁盘文件层（`StockDebugXml` / `StockSaveDebugCsv`） | 未迁，直接吃内存 bytes | 本项目没有"落盘再读回"的需要；harness 里用文件样本顶替 |
| 7 | `IsAdmin()` 由 `AdminRun()` 在**调用处**判断 | `admin` 是 `process_admin_holdings` 的**必填关键字参数** | 闸门做进签名，漏传是 `TypeError` 而不是静默放行（见 §7.1） |

## 9. 验证

相关的 pitfalls 条目（`.dsh/`，gitignore，读不到就当本地批注）：
#106 沪市标签名笔误、#107 `array_filter` 滤 `'0'`、#108 `CalcCurrency` 按
stock_id 查汇率、#109 假库 WHERE 失配、#110 `static` 累计器与一用例一进程、
#111 管理员入口的日期不是参数。

### 9.1 单元测试（`auto/test_holdingsupdater.py`，38 个）

* 解码：归一化、三种 falsy、未知符号、坏 JSON、空输入；
* 写序：pos → date → delete → insert（用记录型替身断言顺序）；
* 四道闸门逐条（含闭区间端点、港股混合豁免、两道"提前返回"）；
* **管理员闸门三态**（`ProcessAdminHoldingsGateTest`）：`admin=False` 不碰数据源
  且 `denied=True`；漏传 `admin` 抛 `TypeError`；`admin=True` 时 `denied=False`；
* 深市 PCF 端到端（**合成样本**，不走网络）：现金两行、T-1 日期、
  `array_filter` 跳过 `159900`、港股补零、比例字符串落库、持仓日写入；
* 假连接是一个 **SQL 子集解释器**（`WHERE` / `ORDER BY` / `LIMIT` /
  `INSERT` / `UPDATE` / `DELETE` + `ON DUPLICATE KEY UPDATE`）。持仓链路要打
  五张表，逐个方法分派的假库会随实现演进而失效。

  > 踩过的坑：解释器的 `WHERE` 正则一开始要求反引号，而 `DailyCloseSql` 的
  > SQL 里 `stock_id = %s` **不带**反引号（`date` 带）—— 于是条件静默失配，
  > 汇率查成了第一行的值。假库失配不报错、只给错数，比真库更危险。

### 9.2 真实 PHP 逐例对照（`_scratch/_php_etfholdings_compare.py`）

```bash
cd python/auto/_scratch
PYTHONPATH=/f/woody/woodyweb/python/auto:/f/woody/woodyweb/python \
    D:/python/python.exe _php_etfholdings_compare.py
```

脚本自己起 PHP 进程（一个用例一个进程，pitfalls #101：PHP 的 `static`
累计器跨调用存活），与 Python 侧**同一批假 sql 替身**录同一份结构，逐键比对：

| 用例 | 样本 | 覆盖 |
|---|---|---|
| `szse_pcf` | `_szse_159605.txt`（真实 PCF，31 只成分） | 深市全链路 |
| `sse_xml` | `_sse_513050.xml`（真实 XML，36 只成分） | 沪市全链路 + §4.1 的笔误复刻 |
| `admin_submit` | — | 写序与落库值 |
| `admin_decode` | 6 种边界 | 归一化与 falsy |

已知偏差只有 §8 第 1 条那一行（脚本显式打印，不算失败）。

### 9.3 演示库实测

`dbtools/seed_sz164701_holdings.py`（默认 dry-run，`--apply` 才写）给演示库
补了 SZ164701 的持仓样本：GLD 100%、`fundposition` = 1.00、
`realtimequote` 快照（`gb_gld` 366.61/369，取自 `quotecli GLD` 实测 ——
daemon 的符号清单里没有 GLD，快照不会自现）+ 10 天真实净值回填。
`/fund/qdiilof/SZ164701` 的说明文字与生产端逐字一致。

`holdingscli --all-qdii` 全量 dry-run 冒烟过：LOF 支线与 ETF 支线的符号
归属、闸门判定都不炸。
