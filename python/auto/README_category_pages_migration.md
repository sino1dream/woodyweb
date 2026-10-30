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
共 17 例：一致 15，按设计差异 2，失败 0
```

两例"按设计差异"：

| 分类 | 差异 | 原因 |
|---|---|---|
| `qdii` | PHP 33 / Python 41 | 生产端已扩充，本仓库是旧快照。见 `core/stocksymbol.py::qdii_get_symbol_array()` |
| `commodity` | 成员一致、排列不同 | 页面行序按生产端排，见下 |

`commodity` 的黄金段：本地 PHP 快照的顺序是
`SZ160216 SZ161815 SZ160719 SZ161116 SZ164701 SZ165513`，生产端是
`SZ160216 SZ164701 SZ160719 SZ161116 SZ165513 SZ161815`。成员完全一样，只调顺序 ——
因为**页面行序是人工比对的对象**（`qdii_get_symbol_array()` 的 docstring 说过同一条理由）。
实现上成员仍只从 `get_qdii_gold_symbol_array()` 取，顺序由
`category_page_service.GOLD_DISPLAY_ORDER` 决定；上游增删符号时不抛异常
（认识的按生产端序在前、新来的按源顺序追加、删掉的自然消失）。

### 6.2 与生产端（`_prod_category_check.py`，2026-10-30 抓取）

```
共 17 个分类：一致 14，漂移 3，抓取/解析失败 0
```

| 分类 | 线上 | 本地 | 线上多出来 / 本地多出来 |
|---|---|---|---|
| `chinaindex` | 27 | 23 | 线上多 4 只：`SH588000 SH588050 SH588080 SH588090`（科创 50 ETF，插在 Ashr 组之后、深证 100 组之前） |
| `qdiihk` | 38 | 29 | 线上多 9 只：`SH513090 SH513230 SH513750 SH513990 SZ159567 SZ159570 SZ159615 SZ159751 SZ159792`（接在恒生指数组之后） |
| `qdiimix` | 15 | 32 | 本地多 17 只 = 上面 9 只 + qdii 那 8 只（生产端把这 17 只搬去了 `qdii` / `qdiihk`，本地快照还是旧口径） |

**这一轮没有修**，原因不是"懒得修"，而是这三处都不是本页的事：数组在
`core/stocksymbol.py`，是**全站公共资产**，改成员会牵动下面这些地方：

| 调用点 | 影响 |
|---|---|
| `web/routes/fund.py:132 _is_qdii_symbol()` | `/fund/<symbol>` 走 QDII 分支还是持仓（Holdings）分支 |
| `web/services/history_service.py`（`is_mix=`） | 净值预览/历史表的取数口径 |
| `web/services/portfolio_service.py`、`transaction_service.py` | QDII 判定影响份额与金额的换算 |
| `auto/updater/holdingsupdater.py` | **后台抓哪些符号的持仓** |
| `web/services/stock_option_service.py:1017` | 编辑页某个选项的可用性 |

所以它是一条独立的"数据修正 + 逐页回归"任务，不该夹带在页面迁移里
（改动范围与上一轮 `editstockamount` 那种"加一个写入口"完全不是一个量级）。
线上顺序已经落在 `auto/_scratch/_prod_category_cache/<name>.order.json`，
下一轮照着它补数组即可，不用重新抓。

## 七、演示库现状：有的分类估值段是空的

真库 smoke 结果（`build_category_page` 的 `counts`）：

| 分类 | 符号 | 算出官方净值 | 页面上的估值段 |
|---|---|---|---|
| `biotech` | 3 | 3 | 有 |
| `oilfund` | 9 | 5 | 有（缺 4 只的行不摆，PHP 同义） |
| `spyfund` | 5 | 5 | 有 |
| `hstech` | 13 | **0** | **整段不摆** |
| `hangseng` | 8 | **0** | **整段不摆** |
| `mscius50` | 2 | **0** | **整段不摆** |
| `chinaindex` | 23 | **0** | **整段不摆** |

不是页面逻辑的问题：`netvaluehistory` 里这些符号只有**一条**很早的行
（例如 `SH513180` 只有 `0.521 @ 2026-10-13`），而 `SH513100` 有 23 条到 10-28，
官方净值就算得出来。生产端这 13 只全有 EST，说明是**演示库的数据缺口**。
补法走既有的净值回填流程（`backfill_nav_history.py`），与本页无关。

## 八、单测

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

**不**断言数组的具体数量：数组会随基金发行增长，写死只会变成维护负担；
"与 PHP/生产端一致"交给两个 `_scratch` 脚本。但**不变量**要钉住
（形状、无重复、硬编码追加项、行序）。

跑法：

```bash
cd python/web && D:/python/python.exe -B -m unittest discover -s tests -t .   # 1067
cd python/auto && D:/python/python.exe -B -m unittest discover -s . -p "test_*.py"  # 1606
```

## 九、刻意偏离（照抄会错的地方都在这里）

1. **建不出引用对象的符号跳过**，PHP 会 fatal。与基金详情页的既有取舍一致：
   宁可少一行，也不要整页 500；
2. **不渲染 `ImgStockGroup($strPage)` 的配图**（`hardlife.jpg` 那类），
   也不渲染 `EchoLinks()` 的站内串；
3. **页尾导航换了内容**（见 §五）；
4. **`commodity` 黄金段顺序按生产端**（见 §6.1）；
5. `/qdii` 从 `fund` blueprint 搬到 `category` blueprint —— endpoint 改名
   `fund.qdii_page` → `category.qdii_page`，三处导航与一个单测跟着改。

## 十、踩到的坑

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
