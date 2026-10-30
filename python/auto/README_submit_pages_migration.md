# submit 族三个"抓外部数据"页：submitdividend / submitnetvalue / submithistory

> 迁完日期：2026-10-30
> PHP 源：`woody/res/submitdividend.php`（90 行）、`woody/res/submitnetvalue.php`
> （20 行）、`woody/res/submithistory.php`（20 行）+ `woody/res/php/_spdrnetvaluexls.php`、
> `_updateinvesconetvalue.php`、`_submithistory.php`
> Python：`auto/updater/etfdividend.py`、`auto/updater/etfnav.py`、
> `auto/updater/historyupdate.py`、`auto/source/xlsxreader.py`、
> `web/services/submit_service.py`、`web/routes/submit.py`、
> `web/templates/submit_tools.html`

---

## 一、这三页在 PHP 里是什么

三页形状完全一致 —— 都是 `SymbolAccount` 的子类，只实现 `AdminProcess()`，
跑完 `SwitchToSess()` 跳回来，**页面上一个字节都不输出**：

| PHP 页面 | `AdminProcess()` 里那一句 | 写哪张表 |
|---|---|---|
| `submitdividend.php` | `if (GetSpdrOfficialUrl($s)) _updateSpdrEtfDividend(...) else _updateProsharesEtfDividend(...)` | `stockdividend` |
| `submitnetvalue.php` | `if (GetInvescoOfficialUrl($s)) UpdateInvescoNetValue($s) else DebugNetValueXlsStr($ref)` | `netvaluehistory`（+`shareshistory`） |
| `submithistory.php` | `UpdateYahooHistoryChart($ref)` | `stockhistory`（+`stockhistorydate`） |

触发方式是一个带 `confirm()` 的链接（`GetOnClickLink()` → GET 副作用）。

**关键事实：这三个页面在生产端几乎进不去。**

* `submitdividend` 的链接在 `etfdividendcn.php`（未迁）；
* `submitnetvalue` 的链接在 `netvalueclosecn.php`（未迁）；
* `submithistory` 的链接函数 `GetUpdateStockHistoryLink()`
  （`php/stocklink.php:155`）在整个 PHP 仓库里**没有任何调用点** —— `grep`
  只命中它自己的 `function` 行。

所以它们不是"网站的一部分"，而是"管理员手上的一条 URL"。本地照这个事实
做成一张工具页 + 三个 POST 端点，而不是三张空页面。

---

## 二、落地形态

### 2.1 路由

| 路由 | 对应 PHP | 说明 |
|---|---|---|
| `GET /submit` | —— | 三个页面合起来的工具表（本地新增）。`?symbol=` 时列出该符号三张目标表的现状 |
| `POST /submit/dividend` | `submitdividend.php` | |
| `POST /submit/netvalue` | `submitnetvalue.php` | |
| `POST /submit/history` | `submithistory.php` | |

入口挂在后台分组页（`/admin/groups` 的「抓取工具」链接），因为页头「后台」
本来就落在那里。

### 2.2 为什么是 POST

PHP 用 GET。本项目已经定过"写操作一律 POST"（`routes/admin.py` 的取舍 1、
`routes/holdings.py::submit_holdings`），三个入口都跟着这条走。副作用是
**不能直接把 PHP 的那个 `<a href>` 搬过来**，模板里改成三行小表单。

### 2.3 权限

`is_admin()`（`web/services/admin_auth.py`）。非管理员：`POST` 一律 403 且
updater 一次都不调（`test_non_admin_is_403_and_writes_nothing` 钉着这条）；
页面上三个按钮 `disabled`。

### 2.4 有反馈

PHP 把结果写进 `debug.txt`，页面上看不出来。这里 flash 一句话，例如：

```
XOP：解析出 80 条分红，写入 80 条（其余值未变）。
QQQ（invesco）：净值写入 1 条。
SPY（spdr）：净值写入 3227 条，流通股数 3227 条，顺带清掉 8 条同日校准值。
^NDX：写入 2370 行，清掉 0 行 0 成交量（跳过：10 行成交量 0）
^NDX：今天已经更新过（水位线 2026-10-29），没有重复抓。
```

---

## 三、分红：SPDR 与 Proshares

`auto/updater/etfdividend.py`。

### 3.1 分派

照 `GetSpdrOfficialUrl()`：**只认 SPY / XBI / XLE / XLY / XOP** 五只走 SSGA，
其余**全部**走 Proshares（PHP 是 `else` 兜底，连 `^NDX` 这种指数也会被送过去，
那当然 404 —— 照抄）。

### 3.2 两个源的日期格式不一样，这是最容易错的地方

| 源 | 字段 | 形状 | PHP 怎么解 |
|---|---|---|---|
| SSGA | `exDate` | `09/21/2026` | `strtotime()` —— **美式** 月/日/年 |
| Proshares | `ExDate` | `2024-12-23T00:00:00` | `substr(..., 0, 10)` **切字符串**，不做时区换算 |

两条都必须照抄：

* 把 `09/21/2026` 按 ISO 解会抛异常或解析成别的东西（`2026-21-09` 不合法，
  但 `05/06/2026` 就"合法"地变成 5 月 6 日 —— 那种错误不会报，只会错数据）；
* Proshares 那条若改用 `datetime.fromisoformat()` 再按本机时区格式化，
  负时区会退一天。

### 3.3 `dividend: "-"` 跳过（**不照抄**）

SSGA 用 `"-"` 表示"没有这一项"（`shortTeamCapital` / `longTeamCapital` 常见）。
PHP 的 `WriteDaily($id, $date, $ar[$i]['dividend'])` 会把 `floatval('-') == 0`
写进 `stockdividend` —— 一条 0 分红。分红数据表的每一行都会被
`etfdividendcn.php` 拿去算"除权当日的假溢价"，0 分红那行算出来是 0%，
是纯噪声。所以 Python 侧跳过并把条数报出来，**这是与 PHP 的差异 1**。

### 3.4 年份（**不照抄**）

PHP：`GetProsharesUrl().'api/distributionsummary?fund='.$strSymbol.'&year=2025'`
—— **写死 2025**。到 2026 年，Proshares 那一路（TQQQ 这些）就只会补 2025
年的分红，等于每年 1 月 1 日静默失效一年。Python 默认取当前年份，可用
`year=` 覆盖。**这是差异 2。**

### 3.5 Proshares 现在抓不到（外部原因）

`www.proshares.com` 全站挂在 Cloudflare 的浏览器挑战后面。2026-10-30 实测：

```
python urllib（裸）                   403
python urllib（带 UA）                403
python urllib（UA + Accept + Referer）403
curl（-A 浏览器 UA）                  403 —— 返回的是挑战页 HTML，不是 JSON
```

同一天同一台机器上，SSGA（200）与 Invesco（200）都正常，所以不是本机网络问题。
解析函数与 URL 拼接都按 PHP 写好，并用 PHP docblock 里的真实响应
（`submitdividend.php:53-95`，TQQQ 2024 四条）测过 —— 接上可用通道即可用。
在此之前这一支只报"没取到数据（Proshares 接口被 Cloudflare 拦（403））"。

---

## 四、净值：Invesco JSON 与 SPDR xlsx

`auto/updater/etfnav.py`。

### 4.1 分派

照 `_AdminNetValueAccount::AdminProcess()`：`GetInvescoOfficialUrl()`
（只认 QQQ）走 JSON，其余走 xls。

### 4.2 Invesco：`usnavsource` 里那条"406"结论已经过时

`auto/source/usnavsource.py` 的模块 docstring 写着"Invesco `dng-api`（页面 JS
调的那个）**HTTP 406**（需要 JS 挑战）"，并据此把整个美股 ETF 净值改成
"用收盘价派生"。2026-10-30 复测：**带浏览器 UA 就 200**，返回真 NAV：

```json
{"effectiveDate": "2026-10-29", "cusip": "46090E103", "currency": "USD",
 "nav": 715.178947, "marketValue": 480886323695.28, "sharesOutstanding": 672400000}
```

差别只在 `User-Agent`：不带 UA 仍是 406。**这条结论没有改 `usnavsource`**
（那是另一条链路，动它要重算中间层校准），但记在这里：QQQ 的真实 NAV
一直取得到，`715.178947` 与当天收盘 `715.34` 差 0.02%。

### 4.3 SPDR xlsx：结构（真实文件实测）

```
第 1 行  Fund Name:      State Street SPDR S&P 500 ETF Trust
第 2 行  Ticker Symbol:  SPY
第 3 行  （整行空白 —— 不占位就会错位）
第 4 行  Date            NAV          Shares Outstanding  Total Net Assets
第 5 行  29-Oct-2026     769.506335   1057282116          813585286253.05
```

* **降序**（最新在最上），所以 `IsTooOld` 命中时是 `break` 而不是 `continue`；
* 日期是**共享字符串** `"29-Oct-2026"`（`d-M-Y`），不是 Excel 日期序列号 ——
  省掉了 1900 闰年那一套换算；
* 列位置照 PHP：`$ar[0]` 日期 / `$ar[1]` 净值 / `$ar[2]` 流通股数；
* 流通股数写库时 **除以 10000**（`shareshistory` 存"万份"）：1057282116 →
  `105728.2116`。

### 4.4 净值写成功才删同日校准值

```php
if ($net_sql->WriteDaily($strStockId, $strDate, $ar[$iNetValueIndex]))
{
    if ($cal_sql->GetClose($strStockId, $strDate))
    {
        DebugString('Delete calibaration on '.$strDate);
        $cal_sql->DeleteByDate($strStockId, $strDate);
    }
}
```

三层条件都要照抄：**净值真的变了** → 才删；**那天有校准值** → 才删。
净值没变说明这天早就处理过，那之后算出来的校准值是新的，误删等于把中间层
校准打回重算。实测跑一遍 SPY：写入 3227 条净值，删掉 8 条校准值（10-24 到
10-29 那几天）；再跑一遍写入 0 条、删 0 条。

### 4.5 xlsx 读取器：为什么不用 openpyxl

`auto/source/xlsxreader.py`。运行环境是 Python 3.7 + `pymysql` + `flask`，
没有 `openpyxl`（虽然本机 `D:\python` 装了，但 `web/requirements.txt` 里没有），
而 xlsx 本质是"zip 装几张 XML"，读它不需要一百行以外的代码。PHP 用
`PHPExcel_IOFactory` 换来的是"什么格式都能开"，代价是 iShares 那种老式
`.xls`（OLE 复合文档）我们读不了 —— 见 §六。

---

## 五、历史价格：Yahoo 换新浪

`auto/updater/historyupdate.py`。

### 5.1 为什么必须换源

Yahoo 的 chart 接口对本机**全面 403**（`query1` / `query2`，带 UA 也一样，
2026-10-30 复测仍 403）。`auto/updater/indexhistory.py` 早就为"补指数历史"
做过同一件事，本模块**直接复用它的抓取函数**，不写第二份。

### 5.2 字段差异

| Yahoo | 新浪 | 写不写库 |
|---|---|---|
| `open` / `high` / `low` | —— | 不写（PHP 只用来打日志） |
| `close` | `c` | 写 |
| `volume` | `v` | 写 |
| `adjclose` | **没有** | 取 `close` |

`adjclose = close` 是**唯一的数据口径差异**，与 `dbtools/seed_index_history.py`
同一个口径（指数/ETF 的日线本来就按复权价发布）。

### 5.3 四道闸门（照抄）

```
早于 2014-01-01        → 跳过（OldestYMD::IsTooOld，新浪是升序所以等价于停）
与前一条同一天          → 跳过（期货那种 23 小时连续行情会重复日期）
收盘价 '-'/'null'/0     → 跳过（Yahoo 用 '-' 表示缺失；新浪不会给）
成交量 0               → 跳过（假期）
```

写完再 **无条件** `DeleteByZeroVolume($strStockId)` —— PHP 里包着它的
`if ($ref->IsSymbolA() || $ref->IsSymbolH())` 被注释掉了
（`yahoostock.php:477-479`），注释写着 "Yahoo has wrong Chinese and Hongkong
holiday record with '0' volume"。所以美股也照清。

### 5.4 水位线

`stockhistorydate`（`id` + `date`，**条件列是 `id` 不是 `stock_id`**，
同 `holdingsdate` 那个坑）是"这只标的历史抓到哪天"。进来先比，相等就
`already_updated` 直接返回；抓完写回。

PHP 用 `$ref->GetDate()`（参考对象加载出来的最新行情日）。本版由路由用
`MysqlReference(symbol).get_date()` 取好传下去；**取不到就拒绝执行** ——
PHP 在那种情况下会 `WriteDate($id, '')`，把水位线写成 `0000-00-00`。
**这是差异 3。**

### 5.5 不删 config 缓存（**差异 4**）

PHP 最后一句 `unlinkConfigFile($ref->GetSymbol())` 删的是
`debug/config/<符号>.txt` —— PHP 参考对象的磁盘缓存。本项目的参考对象每次
都从库里现读，没有这个文件；照抄一句 `unlink` 只会删到别的东西。

---

## 六、已知缺口（都写进代码里的 docstring / 页面提示）

| 缺口 | 原因 | 影响 |
|---|---|---|
| **Proshares 分红拿不到** | Cloudflare 拦（403，四种请求方式全试过） | TQQQ 这类非 SPDR 符号报"没取到数据" |
| **iShares 老式 .xls 不支持** | `_getIsharesXlsUrl()` 只登记了 IEO，返回的是 BIFF/OLE，`xlsxreader` 只认 zip | IEO 报"不是 SPDR 的 ETF（iShares 那条老式 .xls 本版不支持）" |
| **Yahoo 日线不可用** | 全站 403 | 已换新浪，不构成缺口，但 `adjclose` 只能用 `close`（见 §5.2） |
| **`etfdividendcn` / `netvalueclosecn` 两页未迁** | 它们是分红表/净值-收盘价对照的展示页，属"其它 50" | 抓回来的数据暂时没有页面可看（`stockdividend` 表在编辑页里能看） |

---

## 七、验证

### 7.1 PHP 逐例对照（19 例，全一致）

```
cd python && D:/python/python.exe -B auto/_scratch/_php_submit_compare.py

OK    SPDR 分红（XOP 80 条）                  80 行
OK    Proshares 分红（TQQQ 4 条）             4 行
OK    Invesco 净值（QQQ 1 条）                1 行
OK    原语 spdr_date_09212026              '2026-09-21' vs '2026-09-21'
OK    原语 xls_date_29Oct2026              '2026-10-29' vs '2026-10-29'
OK    原语 proshares_slice                 '2024-12-23' vs '2024-12-23'
OK    原语 zero_dash                       True vs True
OK    原语 round_769_506335                '769.506335' vs '769.506335'
OK    原语 shares_div                      '105728.2116' vs '105728.2116'
OK    xlsx 表头在第 4 行                      ['Date', 'NAV', 'Shares Outstanding']
OK    xlsx 首行（降序最新）                      2026-10-29 769.506335
OK    xlsx 流通股 /10000                    105728.2116
...
共 19 例：一致 19，失败 0
```

`_php_submit_check.php` 的做法值得记一笔：**把 PHP 原函数从源文件里抠出来
`eval`**，而不是重写一遍逻辑。`submitdividend.php` 末尾有
`$acct = new _AdminDividendAccount(); $acct->AdminRun();`，整份 `require`
会在输出 JSON 之前就把页面跑起来；抠函数 + 桩掉 `StockDebugJson` /
`GetSpdrUrl` / `DebugGetDate`，能保证"对照的是真身"。分红那两个函数是顶层
`function`，抠得很干净。

**SPDR 的 xlsx 那条 PHP 跑不了**：`_spdrnetvaluexls.php` 第 2 行就
`require_once('../../php/class/PHPExcel/IOFactory.php')`，本机没有 PHPExcel
（PEAR 里连 `HTML/` 都没有，见 pitfalls #152）。所以改成"对真实文件做结构
核对"（表头在第 4 行、首行降序最新、列位置、流通股换算），结论见 §4.3。

### 7.2 单测

```
cd python/auto && D:/python/python.exe -B -m unittest discover -s . -p "test_*.py"
cd python/web  && D:/python/python.exe -B -m unittest discover -s tests -t .
```

`auto` **1611 → 1644**（新增 `test_submitupdaters.py` 33 个）；
`web` **1067 → 1085**（新增 `test_submit_pages.py` 18 个）。

`test_submitupdaters.py` 里那个假库值得说明：它按 SQL 特征分派但不是
"只记录不执行" —— 写语句真的改内存，所以"第二次跑写入 0 条""净值没变就不删
校准值"这类断言验的是**行为**，不是 SQL 长什么样。踩到一个坑：
判断 `id = %s` 不能用 `"id = %s" in sql`（`stock_id = %s` 里也含这三个字符），
必须用正则 `(?<![_\w])`?id`?\s*=\s*%s` —— 否则所有按日期取行的查询都查不到。

### 7.3 真库 smoke（跑完已复原）

| 动作 | 结果 |
|---|---|
| `POST /submit/dividend` XOP | 解析 80 → 写 80；再跑一次写 0（浮点比较生效） |
| `POST /submit/netvalue` QQQ | 净值写入 1 条（`715.178947 @ 2026-10-29`） |
| 直调 SPY（xlsx） | 净值 3227 条 + 流通股 3227 条 + 删 8 条校准值 |
| `POST /submit/history` ^NDX | 写入 2370 行，跳过 10 行 0 成交量；再跑一次 `already_updated` |
| 页面 | `/submit`、`/submit?symbol=SPY`、`/admin/groups`、首页、`/qdii`、`/fund/SH513100`、`/stock/SPY`、`/history/stockhistory/SPY` 全 200 |

跑前 `mysqldump --lock-tables` 备份了六张表
（`stockdividend` / `netvaluehistory` / `shareshistory` / `stockhistory` /
`stockhistorydate` / `calibrationhistory`），跑完 `mysql < dump` 复原，
六张表的行数回到 0 / 1227 / 4 / 5627 / 133 / 343。**MyISAM 没有事务，
这类"抓一批真实数据看效果"的实验只能这么收尾。**

### 7.4 fixture 从哪来

`auto/_scratch/_fixtures/` 存的是 2026-10-30 抓下来的四个真实样本，来源 URL、
抓取方式与重抓命令都写在同目录的 `README.md` 里（`xlsx` 是二进制，被
`.gitignore` 排除，其余三个 JSON 进版本库）。**单测不读这个目录** ——
`test_submitupdaters.py` 里把样本写成常量，所以离线跑测试与 CI 都不依赖它；
这个目录只服务于"想重跑一遍 PHP/Python 逐例对照"的场景。

抓取时记得带 UA：三家站点都按 UA 判断"是不是脚本"，裸 `urllib` 会 403。
Proshares 那一支**带 UA 也过不去**（Cloudflare 挑战页），所以
`proshares-dividend-sample.json` 是按 PHP docblock 里 TQQQ 那条手造的 ——
它的作用是锁住"ISO 带时间 → `substr(...,0,10)`"这个解析口径，不是假装抓到了。

---

## 八、下一步

* `etfdividendcn.php` / `netvalueclosecn.php` 两页迁掉，抓回来的分红与净值
  才有页面可看（两页都不重，是"表格 + 一个 price pool"）；
* Proshares 换通道（或等它放开）后不用改代码，URL 与解析都在；
* `usnavsource` 里"Invesco 406"那条结论值得复查一遍：如果 dng-api 稳定
  可用，美股 ETF 的净值可以从"收盘价派生"换成真实 NAV，`netvalueclose`
  那页的折溢价列才真正有意义。
