# 旧地址兼容层（`/woody/res/*.php`）

> 起因：2026-11-05 用户打开 `http://127.0.0.1:5000/woody/res/sz161226cn.php` 得到 404。
> 对照生产端 `https://palmmicro.com/woody/res/sz161226cn.php`（200）。

## 一、结论：页面是迁过的，缺的是"文件名 → 路径"这一层

`woody/res/sz161226cn.php` 本身只有 4 行：

```php
<?php
require('php/_chinafuture.php');
function GetChinaFutureRelated($strDigitA) { ... }
require('../../php/ui/_dispcn.php');
```

真正的实现是 `woody/res/php/_chinafuture.php`（`FundPairReference($symbol)` +
六段 `Echo*Paragraph`），本地对应 `GET /fund/<symbol>` —— 那条路由 2026-10 就通了。
**195 个 `*cn.php` 全是这种 4 行壳子**（每标的 131 个 + 分类 19 个 + 其它 45 个）。

问题在生产端的"**文件名就是地址**"这条约定没有跟着迁：本地 URL 换成了按功能分组
（`/fund/<symbol>` / `/category/<name>` / `/stock/<symbol>/edit/<option>`），
于是所有旧地址都是 404。**不是页面没迁，是地址没了。**

## 二、做法：只做 302，不重写任何页面

| 位置 | 作用 |
|---|---|
| `web/services/legacy_urls.py` | 映射表 + `resolve()`（纯函数，不碰 Flask 也不碰库） |
| `web/routes/legacy.py` | `GET /woody/res/<path:page>`，三种结果 |
| `web/templates/legacy_notice.html` | 需要符号（给输入框）/ 没有对应页（给原因） |

三种结果：

1. **命中** → 302，地址换成目标路径；
2. **这一页要符号但没给**（`SymbolRequired`）→ 200 + 一个输入框，表单提交回本地址；
3. **没有对应页** → 404 + 一句准确的原因（`KNOWN_MISSING`）。

覆盖率：206 个页面里 **196 个能跳**、**10 个明确没有对应**。

## 三、三条规则，按顺序匹配

1. **每标的页 131 个** —— `^(sh|sz)\d{6}cn\.php$` → `/fund/<SYMBOL>`。
   PHP 把符号编在文件名里，本地一个 `/fund/<symbol>` 全覆盖；
2. **独立页 / 需要符号的页** —— 两张手写表（`FIXED_PAGES` / `SYMBOL_ROUTES`）；
3. **分类页 19 个** —— 从 `CATEGORY_PAGES` 注册表的 `php` 字段**反推**；
   **edit 族 16 项**从 `STOCK_OPTIONS` 生成（`key` 就是页面名去掉 `cn.php`）。
   两处都不另抄清单：加第 20 个分类 / 第 17 项编辑时这里自动跟上。

## 四、几个刻意决定

* **不猜**。映射不上就 404 + 原因，不往"相近的页面"跳 —— 跳到错的页面比 404
  更难排查（人以为对了，数字却是别的标的）。10 个没有对应的页面全部写进
  `KNOWN_MISSING` 并给出原因（`uploadfile.php` 未迁、`debugcn.php` 不迁、
  `cateyescn.php` 是博客……）；
* **符号归一**：`sz161226` → `SZ161226`；`nf_au0` → `nf_AU0`（前缀小写、后缀大写，
  新浪只认这个写法，见 pitfalls"`nf_` 符号必须原样大写"）；
* **参数处理**：`?symbol=` / `?sym=` 被吃进路径，其余查询串原样转发 ——
  `overnightcnhcn.php?overnightcnh=100000` 靠这条保住预填金额；
* **302 而不是 301**：映射在开发期还会调整（分类注册表、编辑项都可能增删），
  301 会被浏览器长期缓存，改错之后本地很难回头。

## 五、顺带修掉的一处内容缺口

对照生产端 `sz161226cn.php` 时发现本地「参考数据」表**少了 `AG0` 那一行**：

```
生产端：AG0 / SZ161226 / SI / USDCNH
本地（改前）：SZ161226 / USDCNH / USDCNY / USCNY
```

根因在 `web/services/reference_service.py::_fund_reference_symbols()` ——
估值标的只查了四张硬编码表（`qdii_est_symbol` 一族，覆盖美股 / 港股 / 日股 / 欧股），
**A股商品（`nf_`）与 A股指数这两族的估值标的只写在 `fundpair` 里**，于是取到 `None`。
PHP 那边 `_chinafuture.php` 直接用 `new FundPairReference($strSymbol)`，配对标的由
它自己查 `fundpair`，所以生产端有那一行。补一条 `fundpair` 兜底后本地对齐。

## 六、最初没对齐的三处（2026-11-05 已全部补齐）

对照生产端 `sz161226cn.php` 时列过三处差异，都在同一天补完，
细节见 `README_silver_si_migration.md`：

| 缺口 | 原因 | 结果 |
|---|---|---|
| 参考数据的 `SI`（纽约白银）行 + 实时 EST | PHP 对 `SZ161226` 挂了一条专用回调：`hf_SI × fx_susdcnh × 1000 ÷ 31.1035` → 元/千克，既进参考数据、也当实时 EST 用 | **已补**：`stock` 加 `hf_SI`、`fundpair` / `futurespair` 加 `nf_AG0 → hf_SI`，换算写在 `calc/futurescalib.FUTURES_VALUE_TRANSFORMS`。生产端实时 EST 1.285，本地 1.286（净值日差一天） |
| 「基金场内份额」表 | `shareshistory` 里没有 `SZ161226` 的行，取不到记录就整段不出现（PHP 同款守卫） | **已补**：`dbtools/patch_shares_sz161226.py` 照抄生产端 10 行；只剩今天那行的「数量」待日线归档（见该 README §五） |
| 参考数据的行序 | PHP 是 `CreateGroup(array($pair, $ref))` → **配对标的在前** | **已改**：`reference_service._pair_goes_first()` 只对 `nf_` 一族生效 |

顺带修掉的两处：

* 参考数据里多出的 `fx_susdcny` / `USCNY` 两行 —— A股商品没有汇率这一档
  （PHP `fundpairref.php:17` 是空分支），现在不给了；
* 「算法说明」里官方 EST 的汇率口径印成 `None 中间价 2026-11-05`
  —— `fundestcalc._official_cny_label()` 对 `cny_symbol is None` 给出
  「人民币计价，不折汇率」。

## 七、复核方式

```bash
# 跳转是否生效（302 + Location）
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" \
  http://127.0.0.1:5000/woody/res/sz161226cn.php

# 覆盖率（196 能跳 / 10 无对应）
python -c "
import sys; sys.path[:0]=['.','auto']
from web.services import legacy_urls as L
import os
names=[f for f in os.listdir('../woody/res') if f.endswith('.php')]
..."
```

单测：`python -B -m unittest tests.test_legacy_urls`（16 条）。其中
`test_every_mapped_target_matches_a_real_route` 把全部映射目标过一遍
`url_map.match()` —— 防的是"映射写对了但本地没这一页"，比逐个手测可靠。
