# 外链函数族迁移（`php/externallink.php` → `python/auto/ref/externallink.py`）

> 补记：这一族函数是 2026-10-10 由「引用对象行情装填」那一步（见
> `README_refquote_migration.md`）顺手带出来的，当时**没有单独写迁移记录**，
> 只靠模块 docstring 顶着 —— 不符合 `AGENTS.md` 第 4 条。这里补上。
> 2026-10-10 同一天又因 `stockparagraph` 补了 `GetXueqiuLink()`。

## 一、为什么这一族必须迁

它不是一个"有空再说"的装饰。**参考数据表的首格就是外链**，而那里有个
硬约束：

> 首格必须**永远有内容**。空一格 → 整行后面所有列错位。

PHP 靠 `GetSinaFundLink()` 末尾那句 `return $strSymbol;`（没有外链时退回
纯符号文本）保证这一点。所以本模块的每个函数都必须有"退回纯文本"的分支，
`test_sina_stock_link` 那组用例专门盯着它。

## 二、迁了哪些

| PHP | Python | 备注 |
| --- | --- | --- |
| `GetLinkElement()` | `get_link_element()` | 属性顺序 `href` 在前，与 `GetHtmlElement()` 一致 |
| `GetExternalLink()` | `get_external_link()` | 缺省 `target="_blank"` |
| `GetSinaFundLink()` | `get_sina_fund_link()` | 先 `IsFundA()` 再退 `IsSinaFund()` |
| `GetSinaStockLink()` | `get_sina_stock_link()` | 总分发，见下 |
| `GetXueqiuLink()` | `get_xueqiu_url()` + `get_xueqiu_link()` | 拆成两个，见第四节 |

## 三、`GetSinaStockLink()` 比 PHP 多两条分支

PHP 的分派（``php/externallink.php:249``）是：
`IsSinaGlobalIndex → IsSymbolA → IsSymbolH → 其他`。

本版**多两条**，因为参考数据表会放它们，而 PHP 那几行不走这个函数：

* **期货**（`hf_NQ`）→ `get_sina_future_link()`；
* **外汇**要分两类（这条是实测踩出来的）：
  `USCNY` / `HKCNY` / `EUCNY` / `JPCNY` 的 `is_forex()` 为真，它们是**中间价**，
  新浪没有行情页，生产端指向中国货币网；
  而 `USDCNY` / `USDCNH` 的 `is_forex()` 为**假**，是即期外汇，新浪有行情页。

判错一类的表现是"链接 404"，页面上完全看不出来。

## 四、`GetXueqiuLink()` 拆成了两个函数

PHP 直接返回 `<a>`；渲染层（`stockparagraph` 的名称格）需要的却是**URL**
—— 服务层不该产 HTML。所以：

* `get_xueqiu_url(symbol)` → `https://xueqiu.com/S/<代码>`；
* `get_xueqiu_link(symbol, display=None)` → 复用上面那个 URL 拼 `<a>`。

拆开时必须保证**换名字典只有一份**（`XUEQIU_INDEX_CODES` /
`XUEQIU_FUTURE_CODES`）。两份字典迟早会分家，所以
`test_url_and_link_do_not_duplicate_the_mapping` 断言 link 里含 url。

雪球不认 `^` 开头的指数写法，要逐条换名（`^NDX → .NDX`、`^GSPC → .INX`、
`^HSI → HKHSI`、`^HSCE → HKHSCEI`、`^HSTECH → HKHSTECH`），
期货 `hf_CHA50CFD → CNmain`。**未列入的原样使用符号** —— PHP 的缺省分支就是这条。

## 五、刻意不迁

**不迁任何写库/登录相关链接**（`GetStockOptionArray()` 那一族编辑链接属于
🔴 membership 域）。理由与 `README_ui_column_migration.md` 里那条一致：
把它们牵进来等于要一并迁一整套 POST/CSRF/权限端点，是这一层最大的范围膨胀源。

## 六、测试

`auto/test_externallink.py`，**20 个用例**，离线、不联网、不连 MySQL。
