# 最后几个工具页：`overnightcn.php` / `lofcn.php` / `autotractorcn.php`

> 2026-11-01。三个 4 行壳子页，其中前两个直接加进分类页注册表、第三个单独一页。
> 做完这一步，`README_pending_pages.md` §六 的"工具页"那一行清空，
> 剩下的只有账号体系（暂缓）与博客内容（不在范围）。
>
> 顺带修掉两件早就该修的事：**`lofcn.php` 从来没进过任何清单**，
> 以及**页脚四个工具页链接还指着生产端**（迁完了却没改，等于给自己留死链）。

## 一、这一批是三个页面，两种做法

| PHP 页面 | 页面名 | 本地 | 做法 |
|---|---|---|---|
| `overnightcn.php`（义工群对比） | `overnight` | `GET /category/overnight` | 加一条 `CATEGORY_PAGES` 条目 |
| `lofcn.php`（LOF基金） | `lof` | `GET /category/lof` | 同上 |
| `autotractorcn.php`（拖拉机自动化） | `autotractor` | `GET /autotractor` | 新 service + 模板 + 路由 |

前两个的源文件是同一套 4 行壳子：

```php
<?php
require('php/_mystockgroup.php');
require('../../php/ui/_dispcn.php');
?>
```

分派在 `_mystockgroup.php:154` 的 `_echoStockGroupArray(GetCategoryArray($strPage), …)`，
`GetCategoryArray()`（`_stocklink.php:3`）的 `switch` 里 `lof` / `overnight` 两支一直在 ——
**2026-10-30 那批"16 个分类页"漏了它们**，不是它们有什么特殊性。所以这次不新建任何
渲染逻辑，只是往注册表补两条（17 → 19）。

`autotractorcn.php` 不是列表页（没有符号数组），它 `require('php/_autotractor.php')`，
98 行全在 `EchoAll()` 里拼图文说明，所以单独做。

## 二、两条注册表条目

三样数据各有出处，**标题不能靠"看起来整齐"补后缀**：

| 字段 | `lof` | `overnight` |
|---|---|---|
| `display` | `LOF_ALL_DISPLAY` = `LOF基金` | `OVERNIGHT_DISPLAY` = `义工群对比` |
| `title` | `LOF基金`（**不带后缀**） | `义工群对比`（**不带后缀**） |
| `description` | `GetMetaDescriptionStr()` 的 lof 分支 | 同，overnight 分支 |
| `symbols` | `GetAllSymbolArray()` 过滤 `IsLofA()` | `GetOverNightSymbolArray()` |

`_getTitleStr()` 里这两支写的是 `'lof' => LOF_ALL_DISPLAY` / `'overnight' => OVERNIGHT_DISPLAY`，
**没有拼** `$strTool`（`基金净值计算工具`），与 biotech 那 15 个不一样。原来的单测
`test_titles_are_unique_and_match_the_php_pattern` 断言"每个 title 都以基金净值计算工具
结尾"，这次按 PHP 原文放宽成两分支（`TITLE_WITHOUT_TOOL_SUFFIX = {"lof", "overnight"}`），
并在注释里写明"别为了整齐补上后缀"。

标题与说明的取值方式没变：`_php_category_meta_check.php` 从源码里抠 `$ar = array(...)`
字面量 `eval` 出来，让 PHP 自己拼常量。这次把它的 `$arCategory` 从 17 项扩到 19 项。

## 三、两条顺序漂移（成员都对，只有排列不同）

### 1. `overnight` 的黄金段

生产端 `overnightcn.php`（2026-11-01 抓取）的黄金 6 只是：

```
SZ160216 SZ164701 SZ160719 SZ161116 SZ165513 SZ161815
```

本地 `get_qdii_gold_symbol_array()` 是：

```
SZ160216 SZ161815 SZ160719 SZ161116 SZ164701 SZ165513
```

**同一个坑 2026-10-30 已经在 `commodity` 上踩过一次**（那里引入
`category_page_service.GOLD_DISPLAY_ORDER`）。这次复用同一份顺序：
`_overnight_symbols()` 把黄金 6 只整体挪到"第一个黄金成员"所在的位置，
其余符号原位不动。

**不去改 `core.stocksymbol.get_qdii_gold_symbol_array()` 本身** —— 那个数组是本地
PHP 快照的忠实移植，auto 层的对照脚本靠它；这里是**展示顺序**问题，在展示层解，
与 commodity 的处置保持一致（见该模块里 `_commodity_symbols()` 的注释）。

### 2. `lof` 的整体顺序

`lof` 是过滤出来的，顺序 = `get_all_symbol_array()` 的原序。生产端 `lofcn.php`
的 53 只与 Python **完全一致**，本地 PHP 快照的 `GetAllSymbolArray()` 却是旧的合并
顺序（成员一致）。所以这一页不需要任何补偿，只是要在对照脚本里把它记进
`ORDER_BY_PRODUCTION`，否则会被判成"数组不一致"。

## 四、`autotractor` 内容页

### 块结构 = PHP 的 echo 顺序

`build_page()` 返回的 `blocks` 有 9 项，顺序就是 `EchoAll()` 里 echo 的先后：

| # | `type` | 对应 PHP |
|---|---|---|
| 1 | `paragraph` | `EchoHtmlElement(GetRemarkElement('完整软件安装步骤：'))` |
| 2 | `list`（有序，4 条） | `echo GetListElement(array(...))` |
| 3 | `paragraph` | 软件更新（`$str .=` 三段拼成一段） |
| 4 | `paragraph` | 软件开发 |
| 5 | `paragraph` | 软件执行 |
| 6 | `list`（**无序**，7 条） | `echo GetListElement(array(...), false)` |
| 7 | `paragraph` | `GetRemarkElement('管理客户号：')` |
| 8 | `list`（有序，5 条） | 管理客户号那五条 |
| 9 | `bugs`（`<h3>` + 有序 10 条） | `echo GetKnownBugs(array(...))` |

模板只按 `type` 分派，**顺序由数据决定** —— 漏一段会体现在服务层的对照测试里，
不会藏进模板的 `if` 里。

### 元素函数：`<font color>` → CSS 类

PHP 用 `GetRemarkElement()` / `GetInfoElement()` / `GetFontElement()` 输出
`<font color=green|blue|red>`。这是**显示**不是语义，本版换成语义类：

| PHP | 本版 | 颜色 |
|---|---|---|
| `GetRemarkElement()` | `<span class="remark">` | `#087c25`（小标题） |
| `GetInfoElement()` | `<span class="info">` | `#1a4fa0`（界面控件名） |
| `GetFontElement($x)`（缺省 red） | `<span class="alert">` | `#cc0000` |
| `GetQuoteElement()` | `<span class="quote">` | `#777` |
| `GetBoldElement()` | `<b>` | — |

配色沿用表里已有的 `.data-table .col-green` / `.col-blue`，免得同一个站点出现两套绿。
`style.css` 新增一小节（`.remark` / `.info` / `.alert` / `.quote` / `.autotractor-list li`）。

### 三个容易写错的地方

* **版本号是两处拼出来的**：`YINHE_AU3_VER = '99'` → `0.99` 与 `yinhe099.au3`。
  文案里三处引用了它（软件更新、软件执行、"给它哪个文件都可以"），漏一处就对不上。
* **`GetFileLink()` 的显示名是 `basename()`**：`/autoit/yinhe.au3` 显示成
  `yinhe.au3`。链接保持**根相对路径**（与 PHP 一致），不改生产端绝对地址 ——
  本地确实没有这几个文件，但那是资源托管问题，不是页面问题。
* **雪球链接的斜杠**：PHP 的 `GetXueqiuUrl()` 返回 `https://xueqiu.com/`（带斜杠），
  而 `ref.externallink.XUEQIU_URL` 不带，拼 `u/2747564710` 时自己补。

### 刻意偏离（3 条）

1. **不渲染两张截图**（`ImgAutoQuote(PATH_BLOG_PHOTO.'autoit066screen.jpg', …)`、
   `GetWoodyImgQuote('20201029.jpg', …)`）。`ImgAutoQuote()` 会在服务端用 GD
   按屏幕宽度现生成缩放图并写盘，Python 侧没有这条链路；图片本身是博客资源。
   与分类页"不渲染 `ImgStockGroup()` 配图"同一个决定。
2. **不渲染 `$acct->EchoLinks('chaos')`**：页脚由 `base.html` 统一给。
3. **被 `/* */` 注掉的两条安装步骤照现状不渲染**（Tesseract 验证码方案、三个脚本
   文件那版）。它们不是"待补"，是作者已经弃用的写法。

另外页尾加了一行说明（"本版不渲染那两张界面截图"）—— 这条是 PHP 没有的，
属于"偏离要在页面上说一声"的既有做法。

## 五、页脚：四个工具页链接从生产端挪到本地

`services/site_links.py` 的 `UTILITY_LINKS` 原来挂着四条指向生产端的链接
（`lof.php` / `overnight.php` / `overnightcnh.php?overnightcnh=95000` /
`autotractor.php`）。2026-11-01 起**这四页全在本地**，整段挪进 `local_links()`：

| 文本 | 本地地址 |
|---|---|
| LOF基金 | `/category/lof` |
| 义工群对比 | `/category/overnight` |
| 义工群对冲计算器 | `/overnightcnh`（**不带** `?overnightcnh=95000`） |
| A股和H股对比 | `/ahcompare`（2026-10-30 就迁了） |
| 拖拉机自动化 | `/autotractor` |

`UTILITY_LINKS` 保留为**空元组**：分段顺序（工具页 → 账号工具 → 开发记录 → 菜单 →
分类）在代码和测试里仍然显式可见，将来有新页面回来时不用改结构。

`/overnightcnh` 不钉 `95000` 的原因：那是 PHP 页面的缺省金额，本地缺省
`DEFAULT_INPUT = "100000"`，页面上有三个预设按钮，链接里钉死一个数反而会盖掉
用户上次填的值。

## 六、对照口径

### 1. 分类页注册表：19 例，两层

**本地 PHP**（`_php_category_compare.py`）：

```
共 19 例：一致 12，按设计差异 7，失败 0
```

7 条"按设计差异"里，这次新增 2 条（`lof` / `overnight` 都进了
`ORDER_BY_PRODUCTION`，理由是 §三 的两处排列差异），另外 5 条早就在
（qdii +8、chinaindex +4、qdiihk +9、qdiimix −17、commodity 排列）。

**生产端**（`_prod_category_check.py`，只读线上页面）：

```
共 19 个分类：一致 19，漂移 0（其中线上更多 0），抓取/解析失败 0
```

这一层是必须的：`overnight` 的顺序漂移只有线上页面能证明，本地 PHP 快照
自己就是旧的那一份。

### 2. 内容页：13 段 / 26 条

`_php_autotractor_check.php` 把 `EchoAll()` **整段从源码抠出来 `eval`**：

* 不 `require` `_stock.php`（拉数据库与会话，本页一行都不需要）；
* 不 `require` `imagedisp.php`（`ImgAutoQuote()` 要 GD 现生成图）；
* `ImgAutoQuote()` / `GetWoodyImgQuote()` 用返回空串的替身；
* `GetXueqiuIdLink()` / `GetKnownBugs()` 按源码原样定义（`externallink.php` 拉
  `stocklink.php`，后者要 PEAR 的 `HTML/Table`，本机没装）；
* `$acct` 用一个只有 `EchoLinks()` 空方法的假对象。

`_php_autotractor_compare.py` 把两边的 HTML 都切成"段落序列"再比：
`<p>` / `<ol>` / `<ul>` / `<h3>` 四类，`text` 一律 `strip_tags` 后比
（`<font color=green>` 与 `<span class="remark">` 的差异在这里抹平）。

```
== 1. 标题 / meta description ==   2 项 OK
== 2. 段落序列（10 段 vs 10 段）==  10 段全一致
== 3. 列表条目总数 ==               两边都是 26 条（4 + 7 + 5 + 10）
通过 13，失败 0
```

## 七、状态

* `compileall` OK；auto **1688 OK**；web **1239 OK**（本轮 +17 例：
  分类页 4 例、autotractor 15 例里新增、页脚 3 例改口径）。
* Flask 路由 **52 → 53**（`len(create_app().url_map)`，含 static 一条；
  `/category/<name>` 一条规则覆盖 19 个分类，所以新页面只多出一条）。
* 真库冒烟（5000 端口，**先杀掉旧进程**再看）：`/autotractor`、`/category/lof`、
  `/category/overnight`、`/overnightcnh`、`/`、`/qdii`、`/category/biotech` 全 200。
  `overnight` 页 `共 23 只基金`、`lof` 页 `共 53 只基金`、`/autotractor` 26 个 `<li>`。
* pitfalls 追加 #187-#189。

## 八、踩到的坑（都写进 pitfalls 了）

1. **`git add -A` 会把历史遗留的 `_scratch` 未跟踪脚本一起暂存** ——
   提交前 `git status --short` 看一眼，用 `git restore --staged` 踢掉。
2. **Windows 上 Flask 的旧进程不会因为新进程启动而退出**：两个 `python -m web.app`
   可以同时 bind 5000（`SO_REUSEADDR`），请求全部落到先启动的那个 → 新路由 404、
   老路由 200，症状极像"代码没生效"。`Get-CimInstance Win32_Process` 按 CommandLine
   过滤 `web.app` 找全，`Stop-Process -Force` 杀掉旧的再测。
3. **Jinja 里 `dict.items` 是方法不是数据**：`{% for item in block.items %}` 报
   `'builtin_function_or_method' object is not iterable`。字典用下标写法
   （`b['items']`），别用点号。

## 九、文件清单

**新增**

| 文件 | 说明 |
|---|---|
| `web/services/autotractor_service.py` | 内容页数据（9 块 / 26 条）+ 元素函数替身 |
| `web/routes/autotractor.py` | `GET /autotractor` |
| `web/templates/autotractor.html` | 按 `type` 分派渲染 |
| `web/tests/test_autotractor_page.py` | 15 例（块顺序/文案/链接/两条偏离/CSS 类/路由） |
| `auto/_scratch/_php_autotractor_check.php` | 抠 `EchoAll()` eval 的探针 |
| `auto/_scratch/_php_autotractor_compare.py` | 13 段 / 26 条对照 |

**修改**

| 文件 | 改动 |
|---|---|
| `web/services/category_page_service.py` | `lof` / `overnight` 两条 + `_lof_symbols()` + `_overnight_symbols()` |
| `web/services/site_links.py` | `UTILITY_LINKS` 清空、五条工具页链接挪进 `local_links()`、docstring |
| `web/routes/__init__.py` | 注册 `autotractor_bp` |
| `web/static/style.css` | 内容页小节（4 个颜色类 + 列表间距） |
| `web/tests/test_category_pages.py` | 19 个键、标题两分支、`lof` 子序列、`overnight` 金块顺序 |
| `web/tests/test_site_links.py` | 工具页不再外链、数量按常量算、外链样本换成 `chinaindex.php` |
| `auto/_scratch/_php_category_check.php` | `$arCategory` 17 → 19 |
| `auto/_scratch/_php_category_meta_check.php` | 同上 |
| `auto/_scratch/_php_category_compare.py` | `ORDER_BY_PRODUCTION` 加 `lof` / `overnight` |

## 十、没做的（有意）

* **`uploadfile.php`**（上传历史 CSV）—— 本地用 `dbtools/seed_*` 顶替，不建议迁；
* **账号体系**（`fundaccountcn.php` 真库、`member` 表、登录、分组可见性）——
  用户要求暂缓；页脚里"投资组合/账户/分组/交易"仍是演示数据；
* **博客/杂文**（`cateyescn.php`、`thanousparadoxcn.php`、`woody/blog/*`）——
  站点内容，不在迁移范围；
* **`/autotractor` 的那两个 `.au3` 下载链接**在本地是 404（文件在生产端站点根）。
  要么把文件放进 `python/web/static/`，要么保持"照着 PHP 写、点了会 404"。
  目前选后者 —— 这属于静态资源搬运，不改变页面内容。
