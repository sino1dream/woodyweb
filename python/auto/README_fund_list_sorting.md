# 分组列表的升降序（`/fund/<group>?sort=...&order=...`）

> 触发：2026-10-26 用户要求「本地页面 http://127.0.0.1:5000/fund/qdiietf
> 表格所有栏目增加升降序选项」。
> 性质：**本地增强，不是迁移** —— PHP 的基金列表页
> （`php/ui/fundlistparagraph.php::EchoFundListParagraph()`）没有排序。
> 别把 `?sort=symbol` / `?sort=premium` 当成同一条东西：那是 `/qdii`
> 那一页估值表的参数（`_echoFundEstParagraph()` 的两个分支，且只在
> 超过 2 行时生效，见 `fundest_service`）。

## 一、怎么做（服务端排序，没有 JS）

表头每格是一个链接，点了整页重渲染：

```
/fund/qdiietf                  → 分组定义里的原顺序
/fund/qdiietf?sort=net_value&order=desc
/fund/qdiietf?sort=symbol&order=asc
```

选服务端而不是前端 JS 的理由：

1. 本项目此前**没有任何 JS**（`web/static/` 只有 `style.css`），为一个排序
   引入第一段脚本，等于开一条新维护面；
2. 排序规则（哪列按数值比、空值怎么放、同值怎么办）能用普通单测钉住，
   而前端排序只能靠肉眼或浏览器测试；
3. 页面本来就是每次导航整页重渲染，加两个查询参数不改变成本结构。

代价是点一次表头要重新取数（含 `_auto_calibrate()`，与其它任何一次导航
同价）。真要省，得先把列表页缓存起来 —— 那是另一件事。

## 二、列定义只有一处：`FUND_LIST_COLUMNS`

```python
FUND_LIST_COLUMNS = (
    ("symbol", "基金代码", "text"),
    ("name", "名称", "text"),
    ("est_symbol", "跟踪代码", "text"),
    ("position", "仓位", "number"),
    ...                                     # 共 19 列
)
```

三元组是 `(键, 显示名, 排序类型)`：

| 字段 | 用途 |
|---|---|
| 键 | 同时是**行字典的字段名**、`?sort=` 的取值、表头的 `title` 文案来源 |
| 显示名 | 表头文字、页面提示「当前按『基金净值』降序」 |
| 类型 | `number` / `text` / `date` / `time`，决定怎么比、第一次点击给哪个方向 |

表头、`get_group()` 返回的 `columns`（纯标签列表，留给测试/外部比对）、
`column_defs`（带排序状态）全部从这一份生成。**改列只改这里**，别在
模板里另抄一遍列名 —— 之前那份 19 个中文字面量就是在 `get_group()` 里
硬写死的。

## 三、排序口径（四条，都能被单测钉住）

### 1. 拿**页面显示文本**当排序依据

`sort_value()` 把 `"17.9%"` 剥成 `17.9`、把 `"1.545"` 转成 `1.545`，空串与
`"N/A"` 一律 `None`。刻意不另存一份原始数值：这样「看到什么就按什么排」，
不会出现"显示 1.55 但按 1.5468 排"的错位。代价是显示精度相同的两个值会
判成相等 —— 由下面第 3 条兜住。

### 2. 空值**恒排最后**

升序降序都一样。缺净值的基金不该因为升序就霸占第一屏。

### 3. 相等值保持原顺序

Python 的 `sort` 是稳定的，而原顺序是分组定义里手工排的顺序 ——
"没数据差别"时看到的是熟悉的顺序。实现上只按 `(值, 行)` 的第 0 项比
（`key=lambda pair: pair[0]`），否则相等时会退化成比较 dict，Python 3 直接
`TypeError`。

### 4. 第一次点新列的方向按类型定

| 类型 | 第一次点击 | 例子 |
|---|---|---|
| `number` | **降序** | 净值、最新价、涨跌幅、溢价率、三档 EST 与溢价 |
| `text` | 升序 | 基金代码、名称、跟踪代码、数据源 |
| `date` / `time` | 升序 | 净值日期、行情日期、EST 日期、行情更新时间 |

理由：看涨跌幅 / 溢价 / 净值时人总是想看最大那个；代码和日期则天然按正序读。
已排序列再点就翻转。`date` / `time` **不解析成 datetime** —— `2026-10-22`、
`13:05:00` 都是定宽文本，字典序即时间序。

这条规则只写在 `build_column_defs()` 里：它给每列算出 `next_order` 与
`mark`，模板只负责渲染。

## 四、非法参数不炸

`?sort=` 的键不认识 → **当没传**（返回分组原顺序）；`order` 不是
`asc` / `desc` → 退化成该类型的默认方向。URL 是用户可编的，
`?sort='; DROP TABLE stock; --` 也该是 200。

## 五、顺手修的布局 bug：`main_class` 漏进 `<head>`

`base.html` 原来把 `{% block main_class %}` 声明在 `<head>` 里：

```html
<title>...</title>
{% block main_class %}{% endblock %}   ← 这里
<link rel="stylesheet" href="...">
```

Jinja 的 block **在声明处就会渲染**，于是 `fund_list.html` 填的 `wide`
变成了 `<head>` 里的一段纯文本。浏览器遇到 `<head>` 里的非空文本会提前
结束 head，把它当 body 的第一个文本节点 —— **页面左上角会多出一行 "wide"**，
而且 `<link>` 被推出 head。

改法：block 只在 `<main class="site-main {% block main_class %}{% endblock %}">`
里声明一次，页脚需要判断宽窄时用 `self.main_class()` 重取。
`test_fund_list_sort.LayoutRegressionTest` 钉住两条：head 里不含 `wide`、
`<main class="site-main wide">` 仍成立。

## 六、文件与测试

| 文件 | 改动 |
|---|---|
| `web/services/fund_list_service.py` | 新增 `FUND_LIST_COLUMNS` / `sort_value` / `sort_rows` / `build_column_defs`；`get_group()` 加 `sort` / `order` |
| `web/routes/fund.py` | `fund_group_page` 透传两个查询参数 |
| `web/templates/fund_list.html` | 表头改排序链接 + 一行操作提示（含「恢复默认顺序」） |
| `web/static/style.css` | `.sortable` / `.sorted` / `.sort-mark`（表头深蓝底上链接必须显式给白色） |
| `web/templates/base.html` | `main_class` 移出 `<head>`（见 §五） |
| `web/tests/test_fund_list_sort.py` | 29 个：纯逻辑 17 + 路由 5 + 布局回归 2 + 其余 |

`web/tests/test_web.py` 里 4 处 `<th>列名</th>` 断言同步改成 `>列名</a>`
（表头已是链接）—— 改的时候别写成 `>日期</a>` 这种会被
`>EST日期</a>` 误伤的形式，靠"`>` 紧跟列名"的边界区分。

```bash
cd python/web && python -B -m unittest tests.test_fund_list_sort
```

## 七、真库实测（2026-10-26，演示库 `/fund/qdiietf` 共 25 只）

| 请求 | 结果 |
|---|---|
| 默认 | 分组定义顺序：SH513290 / SH513400 / SZ159502… |
| `?sort=net_value&order=desc` | 2.4355 → 2.4045 → 2.3871 → 2.2256 → 2.2025 |
| `?sort=symbol&order=asc` | SH513100 / SH513110 / SH513290 / SH513300… |
| `?sort=change&order=asc` | -1.28% → -1.06% → -1.05% → … → +1.04% → +1.17% |
| `?sort=premium&order=bogus` | 退化成降序：22.45% → 16.98% → 14.94%…，页面写「按『溢价率』降序排列」 |
| `?sort=realtime_est&order=desc` | 16 行有值降序、9 行空值**全在尾部** |
| `?sort=bogus` | 200，原顺序 |

`head` 里已无 `wide` 文本，`<main class="site-main wide">` 仍在。
