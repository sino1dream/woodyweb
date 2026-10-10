# 基金详情页补齐三样（参考数据多行 + EST 表 + 份额表）

把 ``/fund/qdiilof/SZ161130`` 这类详情页补到接近生产端 ``sz161130cn.php`` 的形状。

## 一、改之前是什么样

详情页只有两张表：一张"估值配置"（7 行基本信息）与「参考数据」**1 行**
（只有基金自己）。对照生产端，缺三样：

================================  ==========================================  ================
缺什么                             生产端长什么样                                本步做法
================================  ==========================================  ================
**参考数据只有 1 行**                **8 行**：基金/^NDX/hf_NQ/QQQ/TQQQ/            做到 4 行（缺的符号
                                   USDCNH/USDCNY/USCNY                          照实不显示）
**「估值」表整段不渲染**             官方EST / EST日期 / 溢价 / 实时EST / 溢价     **已接线**（4 或 6 列）
**没有基金份额表**                  日期/份额(万)/场内新增(万)/数量/换手/新增换手   **已接线**（复用历史服务）
================================  ==========================================  ================

## 二、第一样：参考数据的标的清单

新增 ``reference_service.reference_symbols_for_fund()``，对应 PHP
``_qdiigroup.php:55-83`` 的装配 + ``EchoFundListParagraph()``：

```php
$arRef = [$stock_ref, $est_ref];                      // 基金 + 估值标的
if ($realtime_ref = $ref->GetRealtimeRef()) $arRef[] = $realtime_ref;
foreach ($arLev as $strSymbol)                        // 杠杆标的（查 fundpair）
    $leverage_ref = new FundPairReference($strSymbol);
```

顺序：**基金 → 估值标的 → 实时标的 → 杠杆标的（都查 ``fundpair``）
→ 即期外汇 → 中间价**。

> **2026-10-10 更正**：中间那一档 ``GetRealtimeRef()`` 当时没做，
> 已补上，见第九节。

### 取舍：~~有什么放什么~~ → 有什么放什么，**没有的在页面上说出来**

> **2026-10-10 更正**：原文写"缺的符号直接跳过，不补空行"，
> 但代码其实除了 ``fundpair`` 那两档之外并没有做存在性检查 ——
> ``forex_symbol`` 就算库里没有也会被列进来。
> 现在不补的是"先查一遍再跳过"，改成照实列表 +
> :func:`quote_state_of` 在名称格标注成因（见 9.3），
> 比悄悄少一行更诚实。

本演示库实际是 4 行：

```text
SZ161130  4.884   1.45%   2026-10-09  15:00  易方达纳斯达克100LOF
^NDX      (空)                             纳斯达克100指数（无行情快照）
USDCNY    (空)                             库里无此符号
USCNY     6.7035  0       2026-10-09  00:21  美元人民币
```

缺 ``TQQQ``/``USDCNH``/``hf_NQ`` 是因为 ``stock`` 表里没有它们，或
``fundpair`` 里没有"ETF → 指数"那几行。后四格空的两种成因以前长得一样，
2026-10-10 起分行标注（见 9.3）。

## 三、8 类外链逐字对齐生产端

参考数据表的**首格**就是外链，为空会让整行少一格、后面全列错位
（上一轮已经踩过一次）。所以本步把 ``externallink.py`` 从"只认基金"扩成
``get_sina_stock_link()`` 的**类型分发**，对应 PHP ``GetSinaStockLink()``：

| 符号 | 生产端 href | 本版 |
| --- | --- | --- |
| SZ161130 | `finance.sina.com.cn/fund/quotes/161130/bc.shtml` | 一致 |
| ^NDX | `stock.finance.sina.com.cn/usstock/quotes/.NDX.html` | 一致 |
| hf_NQ | `finance.sina.com.cn/futures/quotes/NQ.shtml` | 一致 |
| QQQ / TQQQ | `stock.finance.sina.com.cn/usstock/quotes/<符号>.html` | 一致 |
| USDCNH / USDCNY | `finance.sina.com.cn/money/forex/hq/<符号>.shtml` | 一致 |
| USCNY | `www.chinamoney.com.cn` | 一致 |

### 中间价 vs 即期外汇（容易搞混）

实测两者的区分点是 ``is_forex()``：

```text
USCNY / HKCNY / EUCNY / JPCNY   is_forex() = True     → 中国货币网
USDCNY / USDCNH                 is_forex() = False    → 新浪外汇页
```

搞混了会给出一个 404 的新浪页面。这条钉在
``test_externallink.SinaStockLinkDispatchTest``。

## 四、第二样：EST 表接线

``fundest_service`` 早就写好了（连 PHP 那个"实时档少补一次空格"的错位 bug
都照抄并测了），缺的只是**注入引用对象**。本步把
``_build_fundest()`` 打开：默认走 ``refobj.QdiiReference`` 现算。

### 为什么现在可以安全打开

上一轮 workbuddy 特意不接线，理由是"硬塞会把
``PalmmicroFundCalculator.est_net_value()`` 的结果冒充官方EST，等于又造一条口径"。
那个顾虑是对的，但**现在不成立**：``QdiiReference`` 直接委托
``fundestcalc``（唯一实现），没有第二套公式。

实测 ``SZ161130``：

```text
官方 EST = 4.545196      （与官方净值 4.5452 一致 ✓）
参考 EST = 4.549331
实时 EST = None          ← 见下
```

### 实时 EST 现在恒为 None（真实缺口）

它需要中间层的**配对引用**（``GetRealtimeRef()`` → ``FundPairReference``），
而那个类未迁移。本版照 PHP 行为：取不到时该行少两格、表头也不加那两列。
所以本演示库的实际表头是 **4 列**（代码/官方EST/EST日期/溢价），
生产端是 6 列。

## 五、第三样：基金份额表

直接复用历史数据服务里的 ``shareshistory`` —— 它与
``/history/shareshistory/<sym>`` 是**同一张表**（PHP 那边也是同一个
``EchoFundShareParagraph()``），没有第二套算法。

顺手把那段渲染抽成 ``_history_table.html`` 宏，让 ``history.html`` 与
``fund_detail.html`` 共用，避免"列色是数据"这条约定出现两处实现。

**权限差异**：生产端的独立页面 ``fundsharecn.php`` 要登录，详情页里这段不带鉴权。
本版跟随详情页（不鉴权），因为演示库里那份数据本来就只有 4 行样本。

## 六、途中修掉的三个真 bug

### 6.1 空昨收让整张参考数据表消失

```text
USCNY 有现价 6.7035、没有昨收 → str_prev_price = ''
get_price_display() → float('')  → ValueError
```

PHP 的 ``floatval('')`` 给 ``0.0``，而 ``float('')`` 抛异常。因为
``_build_reference()`` 把异常吞掉，表现是**整段参考数据消失**（页面不报错）。

修法：``stockref`` 的 ``get_price_display()`` / ``get_percentage()`` /
``get_percentage_string()`` 三处都改走 ``_php_floatval``（``floatval()`` 语义）。

### 6.2 ``get_table([])`` 返回 None

``_build_fundest()`` 里 ``table.get("rows")`` 在 ``get_table`` 返回 ``None``
时抛 ``AttributeError``，让详情页整个 500。已容住。

### 6.3 缺一个 import

``_build_reference()`` 用了 ``reference_symbols_for_fund`` 却没导入，
被 ``except`` 吞成"整段不显示"。

## 七、测试

| 文件 | 变化 |
| --- | --- |
| `auto/test_externallink.py` | 9 → **14**：加 8 类 href 对照生产端、中间价/即期区分、指数点号前缀、期货展示名、未知符号退回纯文本 |
| `web/tests/test_reference.py` | 加 4 个标的清单用例（首项是基金、去重、无数据源退化） |
| `web/tests/test_fundest.py` | 把"默认不开"翻转为"默认已接线"，并说明为什么翻转 |

另见第九节收尾一轮的增量：

| 文件 | 变化 |
| --- | --- |
| `web/tests/test_reference.py` | 再 +20：`FundPairQueryTest`（11）、`QuoteStateClassificationTest`（5）、`QuoteStateRowTest`（4） |
| `auto/_php_refobj_compare.py` | `--selftest` 第 ④ 项改成按数值序号取阈值用例，不再是假通过 |

全量：`auto` **694 个**、`web` **148 个**（这一步之前是 689 / 144）；
收尾一轮后 `auto` **694** / `web` **168**。

## 八、仍未完成

1. **实时 EST 缺配对引用**：要迁 ``FundPairReference``（或给
   ``EstReference`` 接上 ``get_pair_ref()``），表头才会变 6 列；
2. **演示库缺几个符号**：``TQQQ``、``USDCNH``、``USDCNY`` 不在 ``stock`` 表，
   ``fundpair`` 也没有"ETF → 指数"那几行，所以参考数据达不到 8 行
   （2026-10-10 起这两个成因在页面上会被分别标出来，见第九节）；
3. **详情页还缺生产端更多的段落**：校准记录、五档交易、均线对比（5 日/10 日/
   布林轨/牛熊分界）、基金溢价记录、净值价格比较。本步只做用户点名的三样。

## 九、2026-10-10 收尾：补实时标的、补 ``fundpair`` 测试、区分两种"空行"

### 9.1 补上 PHP ``GetRealtimeRef()`` 那一档

前面第二节写的"基金 + 估值标的"，其实还漏了生产端第 3 行 ``hf_NQ``。
PHP 的组装是（``woody/res/php/_qdiigroup.php:58-59``）：

```php
$arRef = [$stock_ref, $est_ref];
if ($realtime_ref = $ref->GetRealtimeRef())  $arRef[] = $realtime_ref;
```

而 ``GetRealtimeRef()`` 套了两层：``$this->est_ref->GetPairRef()``，
``est_ref`` 是 ``MyPairReference``，它在**构造时**就去查配对表
（``GetPairSymbol()``）。落到 SQL 上是「``fundpair`` 里 ``id`` = 估值标的
那一行的 ``stock_id``」。

**这里有个复制粘贴必踩的坑**：``fundpair`` 同一张表有两个反向读法 ——

| 要什么 | PHP | 谁是 id | 谁进 where |
| --- | --- | --- | --- |
| 实时标的（我跟踪谁） | ``GetPairSymbol()`` | 估值标的 | 取它的 ``stock_id`` |
| 杠杆标的（谁跟踪我） | ``GetSymbolArray()`` | 候选杠杆 ETF | ``stock_id`` = 估值标的 |

搞反了页面照样出得来、行数也一样，只是从第 3 行开始全错位。
所以把两条 SQL 提到模块级常量，并让测试用**同一份数据**验证两个方向给出不同
答案（``FundPairQueryTest.test_the_two_directions_do_not_bleed_into_each_other``）。

演示库 ``fundpair`` 里没有 ``id = ^NDX`` 的行，所以这一档查出来是空的 ——
**不是代码没接通，是库里没数据**，单元测试已经把接通的行为钉住了。

### 9.2 给 ``_leverage_symbols()`` 补假 cursor 测试

它原来是裸 SQL + ``except Exception: return []``，而现有测试里
``source.connection`` 全是 ``None``，等于这段 SQL **零覆盖** ——
写错的表现是"杠杆标的静默消失"。补了 11 个用例：两个方向、
**tuple 行与 dict 行都认**（pymysql 换游标类型就会从 dict 变 tuple）、
数据库抛异常要降级且游标照样关、符号为空的行要跳过。

### 9.3 「库里没有」与「没抓到行情」必须在页面上看得出区别

PHP 两者长得一模一样（``GetStockReferenceArray`` 的 else 分支统一四个空串）。
演示库正好各有一个活样本：

| 符号 | ``stock`` 表 | ``realtimequote`` 快照 | 以前显示 | 现在显示 |
| --- | --- | --- | --- | --- |
| ``^NDX`` | 有（id=6） | **没有** | 四格空格 | ``纳斯达克100指数（无行情快照）`` |
| ``USDCNY`` | **没有** | 没有 | 四格空格 | ``库里无此符号`` |

前者是"后台抓取进程没覆盖到"，后者是"符号配置与库不一致"，
排成一模一样的空格会让人误读成数据丢了。

实现走 ``quote_state_of()`` 判四态（``ok`` / ``no-quote`` / ``no-symbol``，
外加"问不到"时的 ``unknown``），把说明挂在**名称**那一格上并染灰 ——
中间四格必须保持空串，那是表格列对齐的硬要求，
说明不管写在哪儿都不能占那四格。

### 9.4 顺手修好的一处假通过

``auto/_php_refobj_compare.py --selftest`` 的第 ④ 项"边界区分度"原来写的是
``sorted(reference)[:3]`` —— 键名是 ``StockGetPercentage[10]`` 这种，而 ``'0' < ']'``（48 < 93），
字典序会把 ``[10]`` 排在 ``[1]`` **前面**，于是"前三个"落在用例 0 / 10 / 11 上，
跟 ``MIN_FLOAT_VAL`` 阈值毫无关系，却因为三个值碰巧互不相同而一直通过。
改成按数值序号取 ``[0, 1, 2]``，并直接钉住"恰等阈值必须是 0、刚过阈值必须非 0"。
注入两种错误都已验证能抓到（详见 ``README_refobj_php_compare.md`` 第七节）。
