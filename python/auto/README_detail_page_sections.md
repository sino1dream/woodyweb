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

顺序：**基金 → 估值标的 → 杠杆标的（查 ``fundpair``）→ 即期外汇 → 中间价**。

### 取舍：有什么放什么

缺的符号**直接跳过，不补空行** —— 摆一行全空的数据比少一行更容易被误读成
"抓取挂了"。所以本演示库实际只有 4 行：

```text
SZ161130  4.884   1.45%   2026-10-09  15:00  易方达纳斯达克100LOF
^NDX      (空)                             纳斯达克100指数
USDCNY    (空)                             (空)
USCNY     6.7035  0       2026-10-09  00:21  美元人民币
```

缺 ``TQQQ``/``USDCNH``/``hf_NQ`` 是因为 ``stock`` 表里没有它们，或
``fundpair`` 里没有"ETF → 指数"那几行。

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

全量：`auto` **694 个**、`web` **148 个**（本步之前是 689 / 144）。

## 八、仍未完成

1. **实时 EST 缺配对引用**：要迁 ``FundPairReference``（或给
   ``EstReference`` 接上 ``get_pair_ref()``），表头才会变 6 列；
2. **演示库缺几个符号**：``TQQQ``、``USDCNH``、``USDCNY`` 不在 ``stock`` 表，
   ``fundpair`` 也没有"ETF → 指数"那几行，所以参考数据达不到 8 行；
3. **详情页还缺生产端更多的段落**：校准记录、五档交易、均线对比（5 日/10 日/
   布林轨/牛熊分界）、基金溢价记录、净值价格比较。本步只做用户点名的三样。
