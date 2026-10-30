# 分类页迁移（16 个 `woody/res/*cn.php` → 一个视图）

> 日期：2026-10-30
> 涉及文件：`web/routes/category.py`（新）、`web/services/category_page_service.py`（新）、
> `web/services/qdii_page_service.py`（改成薄包装）、`web/templates/category_page.html`（新，
> 顶替 `qdii.html`）、`web/routes/fund.py`（摘掉 `/qdii`）、`web/routes/main.py`（首页分类清单）
> 对照工具：`auto/_scratch/_php_category_check.php`、`_php_category_meta_check.php`、
> `_php_category_compare.py`、`_prod_category_check.py`

## 一、这一批是什么

`biotechcn.php` / `chinafuturecn.php` / `chinaindexcn.php` / `chinainternetcn.php` /
`commoditycn.php` / `hangsengcn.php` / `hsharescn.php` / `hstechcn.php` /
`mscius50cn.php` / `oilfundcn.php` / `qdiicn.php` / `qdiieucn.php` / `qdiihkcn.php` /
`qdiijpcn.php` / `qdiimixcn.php` / `qqqfundcn.php` / `spyfundcn.php`

**16 个页面文件全是 4 行壳子**：

```php
<?php
require('php/_mystockgroup.php');
require('../../php/ui/_dispcn.php');
?>
```

真正的内容在 `woody/res/php/_mystockgroup.php:154`：

```php
_echoStockGroupArray(GetCategoryArray($strPage), LayoutUseWide());
```

也就是说**页面之间的差别只有三样东西**：符号数组、标题、说明。渲染逻辑（参考数据表 +
估值表）一模一样。所以这里做成"一个注册表 + 一个视图"，与
`/stock/<symbol>/edit/<option>` 一个路由顶 16 个编辑页是同一个取舍。

> 补一句实测结论：`chinaindex` 的说明里写着"包括美股 ASHR"，但
> `GetCategoryArray('chinaindex')` 给的是 `ChinaIndexGetAshrArray()` —— 数组里是
> **跟踪 ASHR 的国内基金**，不是 ASHR 本身。所以 16 个数组清一色是 A 股场内基金
> （SH/SZ 号段），PHP 里 `IsFundA()` 的 else 分支（建 AB/AH/ADR 配对引用并渲染
> 那三个段落）**一次都不会进**。单测里用 `^(SH|SZ)\d{6}$` 把这条钉住了。

## 二、三样数据从哪来

三样都不能凭印象，各有一个出处：

| 数据 | PHP 出处 | 取法 |
|---|---|---|
| 符号数组 | `woody/res/php/_stocklink.php:3 GetCategoryArray()` | 探针 `require` 真文件直接吐 JSON |
| 标题 | `_mystockgroup.php::_getTitleStr()` | 从源文件抠字面量 `eval` |
| 说明 | `_mystockgroup.php::_getMetaDescriptionStr()` | 同上 |

标题/说明为什么不能手抄：它们是常量拼接，例如

```php
'biotech' => BIOTECH_GROUP_DISPLAY.'基金的'.STOCK_DISP_NETVALUE.'估算。使用XBI的'.STOCK_DISP_EST.'，目前包括…'
```

`BIOTECH_GROUP_DISPLAY='生物科技'`、`STOCK_DISP_NETVALUE='净值'`、`STOCK_DISP_EST='EST'`
—— 抄错一个常量就是页面上多一个字，肉眼很难发现。所以
`_php_category_meta_check.php` 直接读 `_mystockgroup.php` 的**源码**，把
`$ar = array(...)` 段用**跳过引号内括号**的扫描器抠出来 `eval`，让 PHP 自己拼。

> 坑 1：不能 `require_once('stocklink.php')` —— 它第 2 行拉 `ui/stocktable.php`，
> 后者又 require PEAR 的 `HTML/Table.php`，这台机器的 PEAR 没装 `HTML` 包
> （`D:\xampp\php\pear\HTML\` 不存在）→ 直接 fatal。解法是按行 `eval` 那 61 条
> `define()`，再用 `try/catch` 吞掉依赖别处常量的几条。
>
> 坑 2：说明文字里有 `信诚商品(SZ165513)` 这种**半角括号**。抠数组时按括号配平会
> 提前截断，必须引号内不计数。

## 三、Python 侧结构

```
web/services/category_page_service.py
    CATEGORY_PAGES            17 个键（16 个分类 + qdii），值含
                              display / title / description / symbols / php
    get_category(name)        不认识就抛 CategoryNotFoundError（路由转 404）
    get_category_nav()        页尾导航数据，只读注册表、不连库
    classify_symbols()        原来在 qdii_page_service，搬过来共用
    build_category_page()     两张表的装配
web/services/qdii_page_service.py
    PAGE_TITLE / PAGE_DESCRIPTION   从注册表取，不另写一份
    build_qdii_page()               build_category_page('qdii', ...) 的薄包装
    classify_symbols                再导出（老 import 继续可用）
web/routes/category.py
    GET /category/<name>      分类页
    GET /qdii                 生产端短路径，同一页
web/templates/category_page.html  一个模板顶 16 个页面
```

模板里**不允许出现任何分类名** —— 那会让第 17 个分类漏改。标题、说明、表格、
页尾导航全部由 `page` / `nav` 给。

## 四、URL 为什么是 `/category/<name>`

* `/qdii` 保留：生产端页面名就是 `qdiicn.php`，站内 `base.html` / `index.html` /
  `services/site_links.py` 三处导航都指向它，改了就是一堆死链；
* 其余 15 个没有各占一个顶层路径：PHP 的页面名带 `cn` 后缀（`hstechcn.php`），
  照抄成 `/hstechcn` 既难看也没必要。`/category/<name>` 一个模式覆盖；
* **不能落在 `/group/<name>`**：`/group` 已被 `web/routes/group.py` 占作
  "某个股票分组的交易明细"（演示数据那套），两者不是一回事（一个是标的分类页，
  一个是持仓分组页），挤在一个前缀下迟早误会。

## 五、页尾导航从「其它分组」改成「其它分类」

原 `qdii.html` 页尾列的是两个**基金指数对照表**（`/fund/qdiilof`、`/fund/qdiietf`）。
那两页是 `fundlistparagraph.php` 的另一张表，与分类页不是一类东西；把一个分类页的
页尾指过去，读者会以为它们是同一种列表。

现在改成列**其它 16 个分类页**（同一份注册表，链接不会与符号数组走散），
PHP 那边对应 `$acct->EchoLinks($strPage)`。对应地
`test_qdii_page.py::test_page_links_to_other_categories` 的断言换成了
"`其它分类` + 恒生科技指数 + 自己不列"。

## 六、对照结果

### 6.1 与本地 PHP（`_php_category_compare.py`）

```
共 17 例：一致 12，按设计差异 5，失败 0
```

五例"按设计差异"（Python 跟生产端、本仓库是旧快照，详见 §七）：

| 分类 | 差异 | 原因 |
|---|---|---|
| `qdii` | PHP 33 / Python 41 | 生产端已扩充 8 只，见 `core/stocksymbol.py::qdii_get_symbol_array()` |
| `chinaindex` | PHP 23 / Python 27 | 生产端多了 4 只科创 50，见 `china_index_get_star_array()` |
| `qdiihk` | PHP 29 / Python 38 | 生产端把那 9 只港股混合并进本页，见 `qdii_hk_get_symbol_array()` |
| `qdiimix` | PHP 32 / Python 15 | 生产端把那 17 只分给了 `qdii` 与 `qdiihk`，见 `qdii_mix_get_symbol_array()` |
| `commodity` | 成员一致、排列不同 | 页面行序按生产端排，见下 |

`commodity` 的黄金段：本地 PHP 快照的顺序是
`SZ160216 SZ161815 SZ160719 SZ161116 SZ164701 SZ165513`，生产端是
`SZ160216 SZ164701 SZ160719 SZ161116 SZ165513 SZ161815`。成员完全一样，只调顺序 ——
因为**页面行序是人工比对的对象**（`qdii_get_symbol_array()` 的 docstring 说过同一条理由）。
实现上成员仍只从 `get_qdii_gold_symbol_array()` 取，顺序由
`category_page_service.GOLD_DISPLAY_ORDER` 决定；上游增删符号时不抛异常
（认识的按生产端序在前、新来的按源顺序追加、删掉的自然消失）。

### 6.2 与生产端（`_prod_category_check.py`，2026-10-30 抓取）

第一轮跑出来 3 处漂移，第二轮已经修掉，现在：

```
共 17 个分类：一致 17，漂移 0（其中线上更多 0），抓取/解析失败 0
```

那 3 处的原始数据与修法见 §七。

## 七、第二轮：把三处数组漂移补上

### 7.1 改了什么

| 分类 | 线上 | 改前 | 改后 | 改法 |
|---|---|---|---|---|
| `chinaindex` | 27 | 23 | 27 | 新增 `china_index_get_star_array()`（4 只科创 50），插在 ashr 段之后、深证 100 段之前 |
| `qdiihk` | 38 | 29 | 38 | `qdii_hk_get_symbol_array()` 末尾追加 `get_hk_mix_symbol_array()` |
| `qdiimix` | 15 | 32 | 15 | 改成显式 15 只；那 17 只归 `qdii`（8）/ `qdiihk`（9） |

三处**必须一起改**：`qdiihk` 加 9 只与 `qdiimix` 减 9 只是同一件事的两半
（生产端把这批从"混合"改判成"港股"），只改一边就会变成"两个分类都有它"
或"两个分类都没有它"。

### 7.2 为什么 `qdii_mix_get_symbol_array()` 不能再拼子数组

原来它是
`["SH513360","SZ159509","SZ159529"] + get_lof_mix_symbol_array() + 中国互联网 + 港股混合 + MSCI美国50`。
现在那 17 只走了，`get_lof_mix_symbol_array()` 里也有 4 只（`SH501312`
`SZ163208` `SZ160216` `SZ164701`）不在本页了，而且剩下部分的**顺序**也变了
（`SZ161815` 到末尾）。所以改成显式列表 + 逐行对齐。

三个"段"函数 `get_lof_mix_symbol_array()` / `get_hk_mix_symbol_array()` /
`get_msci_us50_symbol_array()` **没动** —— 它们各有独立语义：
`in_array_lof_mix` 决定历史页走持仓口径、`in_array_hk_mix` 参与
`use_same_day_net_value()`、msci 那个是 `mscius50` 分类页的数组。

### 7.3 持仓覆盖率不能跟着收窄（最容易踩的一脚）

`holdingsupdater.qdiimix_holdings_symbols()` 原来直接返回
`qdii_mix_get_symbol_array()`。数组一收窄，**后台就会静默少抓 17 只的持仓**：
不报错、不警告，只是那些详情页的持仓明细停止更新。

所以新增 `qdii_mix_holdings_symbol_array()`（32 只）专供持仓更新，出处是
`woody/res/php/_qdiimix.php` 被 **32 个符号详情页**逐个 `require`
（`grep -l _qdiimix.php woody/res/*.php` 正好 32 个，与旧数组成员完全一致）。
判据是"页面需要什么数据"，不是"分类页数组有几个"。

### 7.4 影响面逐项核对

改公共数组会牵动下面这些判断，逐条对过 —— 结论都是**与生产端 PHP 同口径**，
不是"顺手改"：

| 调用点 | 那 17 只的变化 | 结论 |
|---|---|---|
| `history_service._qdii_region()` | 9 只港股混合：`None` → `hk` | 汇率列从空变成 `HKCNY`，与生产端一致（这 9 只本来就是港股 QDII） |
| `history_service` 的 `is_mix=` | 17 只全部 `True` → `False` | `use_same_day_net_value()` 的结果**不变**：8 只仍 `is_qdii=True`（先判 QDII → false），9 只仍 `is_hk_mix=True`（`is_mix` 假时直接返回 true） |
| `portfolio_service._in_qdii()` | 9 只 `True` → `False` | 不再进 `cny.add_qdii()` 折算 —— 这 9 只是**人民币计价**的场内 ETF，之前按仓位折汇是错的 |
| `transaction_service` 的 `prev` | 9 只 `True` → `False` | "确认校准"提示改用**当天**净值（A 股 ETF 当天出净值），之前错用前一天 |
| `stock_option_service._qdii_region()` | 9 只 → `hk` | 编辑页校准值的中间价从"无"变成 `HKCNY` |
| `stock_option_service` 的 `in_array_china_index` | 4 只科创 50 → `True` | 校准值判 `1.0`（境内指数基金不乘汇率），这 4 只本来就是境内 ETF |
| `holdingsupdater` | 不变（32 只） | 见 §7.3 |

`fund_symbols()` 的并集**一点没变**（102 只）：那 17 只只是换了数组，
没有进出。所以 `seed_p0_symbols` 不用重跑；五个数组现在两两不相交，
`test_stocksymbol.SymbolGroupAlignmentTest` 把这条与"数量/位置"一起钉住了。

### 7.5 还没解决的问题（都记在这，别下次又踩）

1. **`SH588xxx` 的 `IsFundA()`**：生产端 `chinaindexcn.php` 里这 4 只的代码列链接是
   `finance.sina.com.cn/fund/quotes/588000/bc.shtml`（基金页），Python 走的是
   `realstock/company/sh588000/nc.shtml`（股票页）。说明生产端的
   `_isDigitShangHaiEtf` 区间比本地快照（`510000..569999`）宽 —— 但具体上限
   无法从页面反推（570000–588999 之间还有 580xxx 权证），**没动**。
   影响仅限链接形态与"是否进 EST 表"，不影响行数与排序。
2. **`chinaindex` 27 行全部显示「库里无此符号」**：演示库连原有的 23 只
   A 股 ETF 都没有 `stock` 行（不只是新加的 4 只科创 50）。属 P0 数据层缺口，
   补法是 `seed_p0_symbols` 那类脚本 + 净值回填，与数组无关。
3. 线上行序缓存在 `auto/_scratch/_prod_category_cache/<name>.order.json`
   （gitignore）。下次复核直接跑 `_prod_category_check.py`，别手抄数组。

## 八、演示库现状：有的分类估值段是空的

真库 smoke 结果（`build_category_page` 的 `counts`）：

| 分类 | 符号 | 算出官方净值 | 页面上的估值段 | 缺 `stock` 行 |
|---|---|---|---|---|
| `biotech` | 3 | 3 | 有 | 0 |
| `oilfund` | 9 | 5 | 有（缺 4 只的行不摆，PHP 同义） | 0 |
| `spyfund` | 5 | 5 | 有 | 0 |
| `hstech` | 13 | **0** | **整段不摆** | 0 |
| `hangseng` | 8 | **0** | **整段不摆** | 0 |
| `mscius50` | 2 | **0** | **整段不摆** | 0 |
| `chinaindex` | 27 | **0** | **整段不摆** | **27（全部）** |

不是页面逻辑的问题：`netvaluehistory` 里这些符号只有**一条**很早的行
（例如 `SH513180` 只有 `0.521 @ 2026-10-13`），而 `SH513100` 有 23 条到 10-28，
官方净值就算得出来。生产端这 13 只全有 EST，说明是**演示库的数据缺口**。
补法走既有的净值回填流程（`backfill_nav_history.py`），与本页无关。

`chinaindex` 多一层缺口：那 27 只 A 股 ETF 在 `stock` 表里**一行都没有**
（连第一轮就在数组里的 23 只也没有），所以代码列旁边一律显示
「库里无此符号」。这不是本轮新加的 4 只带出来的问题 —— 补 4 只不会让页面
变完整，要么整批补（`seed_p0_symbols` 那类脚本 + 净值/行情回填），要么先摆着。
`qdiihk` 新增的 9 只反而**不缺行**：它们原先在 `qdii_mix` 里，本来就在
`fund_symbols()` 的并集里。

## 九、单测

`web/tests/test_category_pages.py`（19 个），分三段：

1. **注册表**（`CategoryRegistryTest`）：17 个键与顺序、五个字段齐全、
   标题唯一且 = 显示名 + "基金净值计算工具"、`php` 字段对得回真的 4 行壳子、
   数组全是 A 股场内基金且无重复、三个"拼出来的"分类硬编码追加项还在、
   `commodity` 黄金段顺序；
2. **装配**（`BuildCategoryPageTest`）：标题/说明来自注册表、`symbols` 注入生效、
   没有服务时两张表都是 `None` 而不是抛异常；
3. **路由**（`CategoryRouteTest`）：17 个分类全部 200 且标题/说明/两个表格类名都在、
   未知分类 404、`/qdii` 与 `/category/qdii` 同页、排序链接指回**当前** endpoint、
   `?sort=` 三个值都接受、页尾导航不列自己、库挂掉时页面仍能打开。

**web 侧不**断言数组的具体数量：数组会随基金发行增长，写死只会变成维护负担；
"与 PHP/生产端一致"交给两个 `_scratch` 脚本。但**不变量**要钉住
（形状、无重复、硬编码追加项、行序）。

**auto 侧反过来要钉死数量**：`test_stocksymbol.SymbolGroupAlignmentTest`（5 个）
专门锁第二轮的三个数组 —— 数量（27 / 38 / 15）、位置（科创 50 段在 ashr 之后、
hk_mix 在港股末尾）、`qdii_mix` 不含那 17 只、持仓全集 32 只、五个数组两两不相交。
理由：这三处是"照着生产端页面逐行对齐"的结果，不是随手拼的集合；
只要数量或位置一变，页面行序就跟生产端对不上了，而单测是唯一能自动发现这件事的地方。

跑法：

```bash
cd python/web && D:/python/python.exe -B -m unittest discover -s tests -t .   # 1067
cd python/auto && D:/python/python.exe -B -m unittest discover -s . -p "test_*.py"  # 1611
```

## 十、刻意偏离（照抄会错的地方都在这里）

1. **建不出引用对象的符号跳过**，PHP 会 fatal。与基金详情页的既有取舍一致：
   宁可少一行，也不要整页 500；
2. **不渲染 `ImgStockGroup($strPage)` 的配图**（`hardlife.jpg` 那类），
   也不渲染 `EchoLinks()` 的站内串；
3. **页尾导航换了内容**（见 §五）；
4. **`commodity` 黄金段顺序按生产端**（见 §6.1）；
5. `/qdii` 从 `fund` blueprint 搬到 `category` blueprint —— endpoint 改名
   `fund.qdii_page` → `category.qdii_page`，三处导航与一个单测跟着改。

## 十一、踩到的坑

1. **`classify_symbols` 搬家后单测会"假绿"**：它执行时的全局名在
   `category_page_service`，`test_qdii_page.py` 里
   `mock.patch.object(qdii_page_service, "MysqlReference", ...)` 打的是另一个命名空间，
   补丁静默失效 —— 测试仍全部通过，但测的是真构造函数。搬到哪就打到哪
   （pitfalls #39 的同一件事，这次换了触发方式）。
2. **`/category/qdii` 做子串断言会撞上 `qdiieu` / `qdiihk`**：
   `assertNotIn("/category/qdii", html)` 永远失败。要带引号：
   `assertNotIn('href="/category/qdii"', html)`。
3. **`MysqlReference(symbol, source=None)` 取不到官方净值**：直接写脚本调
   `classify_symbols()` 时必须传 app 里那个真 source
   （`app.extensions['reference_service_factory']()` 的 `.source`），
   否则所有分类都算成 `funds=0`，看起来像"页面逻辑错了"。
4. **生产端页面里符号不是紧跟在 href 后面**：`href="…bc.shtml" target=_blank>SZ159502`，
   正则写成 `.shtml">(SH|SZ)\d{6}` 会一个都匹配不到（页面下来了却"解析不到符号"）。
5. **`web/tests/test_category_pages.py` 复用 `test_qdii_page.py` 的假对象**：
   同一个页面家族没必要各写一份 `FakeRef`；但要注意被 import 的测试模块**不会**
   因此在 `discover` 里跑两遍（实测 1048 → 1067，正好 +19）。
6. **收窄分类页数组会静默削掉后台的持仓覆盖**：
   `holdingsupdater.qdiimix_holdings_symbols()` 直接复用
   `qdii_mix_get_symbol_array()`，数组从 32 收到 15 的那一刻，后台就少抓 17 只
   的持仓 —— 不报错、不告警。判据要问"这些页面需要什么数据"
   （`_qdiimix.php` 的 32 个详情页），而不是"分类页数组有几个"（见 §7.3）。
7. **`grep -l` 数详情页比数数组可靠**：想知道持仓型 QDII 到底几只，
   数 `woody/res/*.php` 里 `require` 了 `_qdiimix.php` 的文件 —— 那是手写清单，
   32 个；拿 `QdiiMixGetSymbolArray()` 反推会跟着分类页一起漂。
8. **同一只标的的"分类归属"是成对的**：`qdiihk` +9 与 `qdiimix` -9 是同一件事。
   只改一边，那 9 只要么同时出现在两个分类页上，要么哪个页都没有 ——
   而且 `_qdii_region()` 是"先 qdii 再 hk"，顺序会让"同时属于两个"表现为
   静默取第一个分支，页面看不出来。
9. **"生产端链接形态"能反查出符号判定的区间**：`chinaindexcn.php` 里
   `SH588000` 的链接是 `fund/quotes/588000/bc.shtml`（基金）而不是
   `realstock/company/sh588000/nc.shtml`（股票），说明生产端的
   `_isDigitShangHaiEtf` 比本地快照宽。这类"链接形态差"不能靠肉眼扫，
   抓一次页面 grep 符号位置就有了（见 §7.5 第 1 条）。
