# 引用对象行情装填迁移（P0）

把详情页「参考数据」表接上真实行情。

## 一、要解决的问题

workbuddy 迁好 `refobj.py`（引用对象层）与 `uicolumn.py`（列定义地基）之后，
`/fund/qdiilof/SZ161130` 的「参考数据」表**六格里只有名称有值**，
价格/涨幅/日期/时间全空。对照生产端 `qdiicn.php`，那四格都该有数据。

根因定位到最后一环：

```text
refobj 刻意"构造不联网" → 行情靠注入
        ↓
   而这个注入点从来没接上
        ↓
str_price='0'  str_date=None  str_time=None  has_data=False
```

**数据其实都在库里**（`quoteupdatercli --daemon` 抓得好好的）：

```python
get_stored_quote('SZ161130') = RecentQuote(price='4.884', prev_price='4.814',
                                           date='2026-10-09', time='15:00:00')
```

所以这是"没接完的线"，不是"抓取坏了"。

## 二、方案：先读库快照，再兜底联网

用户裁决过的取向 —— 与项目既有分层一致：**抓取归 daemon，页面读快照**，
联网只是快照缺失时的最后手段。

新增 `python/auto/refquote.py`：

============================  ====================================================
函数                           作用
============================  ====================================================
`SnapshotQuoteSource`          从 `realtimequote` 读快照
`NetworkQuoteSource`           兜底：抓新浪原始文本
`apply_snapshot(ref, quote)`   把快照字段直接装填到引用对象
`apply_network(ref)`           走 `stockref` 既有解析器装填
`load_quote_into(ref, ...)`    路径选择器，返回 `'snapshot'/'network'/'none'`
`build_quote_loader(source)`   给 `refobj` 用的回调
`refresh_quotes(refs)`         **请求期**兜底入口
============================  ====================================================

### 为什么不让快照走 `quote_loader` 那套原始文本

`stockref.load_sina_data()` 的 loader 签名是 `loader(file_name) -> 原始文本`，
而快照里存的是**已解析的字段**。把结构化数据序列化成文本、再解析回来，
既绕又容易与新浪的字段顺序脱钩。直接装填更短、更可读。

## 三、一条被测试抓出来的真缺陷：构造期不能联网

第一版实现里 `load_quote_into` **默认联网兜底**，结果
`test_refobj.ConstructionIsOfflineTest.test_construction_does_not_load_sina_data`
立刻失败。它抓的是一个真问题：

```text
MysqlReference.__init__
  └─ load_data()
       └─ load_quote_data()  ← 我在这里联网

QdiiReference.__init__
  └─ est_net_value()         ← 构造里还会算一次实时 EST
```

也就是说**每个页面请求都会去抓一遍新浪** —— 既慢，又给站方添压力。

修法：`load_quote_into` 加 `allow_network=False` 默认值。

```text
构造期（默认）    只读库快照，命中即返回，绝不联网
请求期            显式传 allow_network=True（走 refresh_quotes）
```

这条纪律写进了 `refquote` 的模块 docstring 与 `test_refquote` 的两个用例
（`test_construction_does_not_go_to_network_by_default`、
`test_snapshot_hit_does_not_touch_network`）。

## 四、顺带修掉的"少一格"缺陷

首格恒为 `GetExternalLink()` 的产物（`referenceparagraph.php:33`），
而 `refobj` 从没设过 `external_link`，于是首格空、**整行少一格、后面全列错位**。

workbuddy 把外链族（`php/ui/htmlelement.php`）列为"刻意不迁"，
但那不等于可以让首格空着。所以新增 `python/auto/externallink.py`，
**只补参考数据表用到的这一个**：

```text
GetLinkElement()    -> get_link_element()
GetExternalLink()   -> get_external_link()
GetSinaFundLink()   -> get_sina_fund_link()
```

`get_sina_fund_link()` 照抄了 PHP 那条"**没有外链时退回纯符号文本**"的分支，
它保证首格永远有内容。

**不要**顺手把 `htmlelement.php` 整份搬过来 —— 其余外链用到时再加。

## 五、web 层接线

`reference_service.build_stock_references()` 从 `stockref.StockReference`
改成 `refobj.MysqlReference`（前者只有行情，后者还带净值/仓位/校准值，
是详情页真正需要的），并接受 `source` 参数用于读快照。
`refobj` 不可用时退回旧实现（`_build_one_via_stockref`）。

`routes/fund.py` 的 `_get_reference_refs()` 把 service 的 `source` 传进去。

## 六、验收：与生产端逐格对照

生产端 `qdiicn.php` 实际输出（2026-10-09 抓取）与本版：

| 符号 | 生产端 | 本版 |
| --- | --- | --- |
| SZ161130 | 4.884 / 1.45% / 2026-10-09 / 15:00 | **一致** |
| SZ161125 | 3.312 / 0.55% / 2026-10-09 / 15:00 | **一致** |
| SH513100 | 2.398 / 1.01% / 2026-10-09 / 15:06 | 一致（时间 15:34，daemon 抓得更勤） |

名称列**故意**取库名（`SqlGetStockName()`）而不是行情名 —— 这是 PHP 的行为
（`referenceparagraph.php:23`），不是偏差。所以本版显示"易方达纳斯达克100LOF"，
生产端显示"纳斯达克100LOF"，只是两边 `stock.name` 不同。

## 七、测试

新增：

* `python/auto/test_refquote.py`（19 个）：快照装填、`'0'` 不算有行情、
  **构造期不联网**、快照命中不联网、请求期兜底、loader 还原、路径选择；
* `python/auto/test_externallink.py`（9 个）：`<a>` 拼装格式、URL 形状、
  **没外链时退回符号文本**；
* `python/web/tests/test_reference.py` 加 4 个**跨层集成**用例：
  真数据源 → 引用对象 → 六格全有值。

最后那组是补**以前缺失的那一层**：单测都在 service 边界打桩，
没有"真数据源 → 引用对象 → 表格"这条链，所以"整张表是空的"能一路绿灯溜过去。

全量：`auto` **689 个**、`web` **144 个**（P0 之前是 661 / 140）。
2026-10-10 收尾一轮后测得 `auto` **694** / `web` **168**。

## 八、已知偏差与后续

1. **~~只接了基金的外链~~（已过期，2026-10-10）**：本 P0 落地的同时补上了
   `auto/externallink.py`，现在有 `GetSinaStockLink` / `GetSinaFundLink` /
   `GetSinaUsStockLink` / `GetSinaFutureLink` / `GetSinaForexLink` /
   `GetForexMiddleLink` 六条，`MysqlReference._build_external_link()` 按符号
   类型分发（一张参考表会把基金、A 股、港股、美股指数、外汇混在一起，
   只认基金是不够的）。**仍然没迁**：雪球、东财、CME 那几条，以及
   `php/ui/htmlelement.php` 的其余部分 —— 那是刻意的范围取舍，不是遗漏；
2. **~~详情页缺三样生产端有的东西~~ —— 这条已于 2026-10-10 结掉三样中的两样**：
   参考数据表的标的清单（`reference_symbols_for_fund`）与 EST 估值表
   （`_build_fundest`）都已接线（见 `README_detail_page_sections.md`）。
   仍缺的是**实时 EST 那一档** —— 它要 `GetRealtimeRef()` 的配对引用，
   而 `MyPairReference` 未迁移，所以恒为 `None`；
   基金份额表也已经补上（复用 `shareshistory`）。
   剩下没做的只有第 3 条的联网兜底接线；
3. **联网兜底没有接进路由**。`refresh_quotes()` 已就绪，但页面目前
   只用快照；接进去要同时决定"什么时候允许联网"（建议仅登录用户或限流）。
