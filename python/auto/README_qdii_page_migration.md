# QDII 分类页（qdiicn.php）

把 ``woody/res/qdiicn.php`` 这个页面迁到 ``/qdii``。它是本项目**第一个按分类汇总的
基金页**：之前的 ``/fund/<symbol>`` 是一只基金的详情，这里是一整个分类的两张表。

生产端：<https://palmmicro.com/woody/res/qdiicn.php>

## 一、PHP 侧的调用链

``qdiicn.php`` 本身只有 4 行，逻辑全在后面：

```php
require('php/_mystockgroup.php');        // woody/res/php/_mystockgroup.php
require('../../php/ui/_dispcn.php');
```

``_mystockgroup.php`` 的 ``EchoAll()`` 对非 ``mystockgroup`` 页面走同一支：

```php
_echoStockGroupArray(GetCategoryArray($strPage), LayoutUseWide());
```

* ``GetCategoryArray('qdii')`` → ``php/stock/stocksymbol.php:199`` 的
  ``QdiiGetSymbolArray()``（纯符号数组）；
* ``_echoStockGroupArray()`` → ``_mystockgroup.php:11``，负责**分类装配**；
* 两个段落函数在 ``php/ui/`` 下：``EchoReferenceParagraph()`` 与
  ``EchoFundArrayEstParagraph()``。

``GetCategoryArray()`` 是个 21 分支的 switch（abcompare / ahcompare / biotech /
chinafuture / …）。本步只迁 ``qdii`` 这一支 —— 其余分支要么是 A/H 对比
（``ahparagraph`` 未迁），要么是港股/日本/欧洲/混合 QDII，都属后续步骤。

## 二、分类装配规则（``_echoStockGroupArray()`` 的 foreach）

对每个符号：

1. ``$ref = StockGetReference($strSymbol)`` —— 普通引用对象；
2. 若 ``$ref->IsFundA()``：
   * ``$fund = StockGetFundReference($strSymbol)``；
   * 若 ``$fund->GetOfficialNetValue()`` 有值 → 进 ``$arFund``（EST 表）；
3. 否则建 AB / AH / ADR 三个配对引用，分别进三个数组
   （``EchoAbParagraph`` / ``EchoAhParagraph`` / ``EchoAdrhParagraph``）；
4. ``if ($bWide == false || $bIncluded == false) $arRef[] = $ref;``；
5. 若 ``$ref->IsTradable()`` → 进 ``$arTransactionRef``（只服务「我的股票」页）。

对应实现：``web/services/qdii_page_service.py`` 的
:func:`~web.services.qdii_page_service.classify_symbols`。

**两条关键推论**：

* **第 4 条在非宽屏时恒真**，所以全部符号都进参考数据表 —— 生产端表1 与表2
  行数相同（都是 41 行）正是这个原因，不是巧合。
* **第 3 条在本页不会触发**：``QdiiGetSymbolArray()`` 全是 SH/SZ 号段的
  A 股基金。即使触发，那三个段落本页也不渲染，行为与"什么都不做"一致，
  所以 Python 侧没有实现这一支（见 §六 取舍 1）。

## 三、两张表

=========================  ============================  ==============================
PHP 段落                    列                            首格
=========================  ============================  ==============================
``EchoReferenceParagraph``  代码 / 价格 / 涨幅 / 日期 /     ``GetExternalLink()``
                             时间 / 名称（6 列）             → **新浪外链**
``EchoFundArrayEstParagraph`` 代码 / 官方EST / EST日期 /   ``GetStockLink()**
                             溢价 / [参考EST / 溢价] /      → **站内详情页**
                             [实时EST / 溢价]（6 或 8 列）
=========================  ============================  ==============================

估值表的**列是动态的**：``_getFundEstTableColumn()`` 扫遍整个列表，一只基金都
没有参考（或实时）估值时，对应的两列就不加。行也是动态的 ——
``_echoFundEstTableItem()`` 里实时那一档**没有 else 补空**（fair 那一档有），
所以 8 列表头下会出现 6 格的行。生产端正是这样：QQQ 组 8 格，SH501300 那类
只有官方 EST 的 6 格。

排序：``?sort=symbol`` / ``?sort=premium``，只在超过 2 行时生效
（``if ($iCount > 2)``）。``premium`` 的口径是
``_callbackSortFundEst()`` —— 优先用 ``GetStockRef()`` 算，拿不到才用 ref 自己。

## 四、生产端与本地 PHP 仓库差 8 个符号（重要）

本地 PHP 仓库的 ``QdiiGetSymbolArray()`` 是 **33 个**符号；生产端
``qdiicn.php``（2026-10-12 抓取）是 **41 个**。多出的 8 个：

=================  ==========================  ==========================
符号               生产端名称                   推测来源
=================  ==========================  ==========================
``SZ159509``       纳指科技ETF景顺               景顺长城
``SZ159529``       标普消费ETF景顺               景顺长城
``SH513850``       美国50ETF易方达               mscius50 组
``SZ159577``       美国50ETF汇添富               mscius50 组
``SH501312``       海外科技LOF                   ——
``SZ160216``       国泰商品LOF                   commodity 组
``SZ164701``       黄金LOF                      commodity 组
``SZ163208``       全球油气能源LOF               oilfund 组
=================  ==========================  ==========================

**怎么确认它们不是"从原分组搬过来的"**：逐个抓了生产端的 ``qqqfundcn`` /
``spyfundcn`` / ``mscius50cn`` / ``commoditycn`` / ``oilfundcn``，
那几个分组仍各自显示这 8 只，所以是**额外并入 qdii**，不是搬家。

**怎么确认它们的追加位置**：生产端行序上，这 8 只整块排在 SPY 组之后
（``SZ159655`` 之后紧接着 ``SZ159509``），不是插进 QQQ / SPY 组中间，
所以实现为数组末尾一个独立的追加段。

**为什么连顺序都要对齐**：两张表的行序就是数组顺序，而页面是人工比对的对象；
顺序错了看不出公式问题，只会多出一堆无意义差异。

## 五、途中抓出的一个真 bug：``GetStockLink`` 被错别名成外链

``refobj.py`` 原先写着::

    MysqlReference.GetStockLink = MysqlReference.get_external_link

注释还说"语义与 GetExternalLink() 相同"。于是估值表首格渲染出
**新浪外链**，而生产端是 ``https://palmmicro.com/woody/res/sh513100cn.php``。

**为什么之前没发现**：基金详情页只有一只基金、一张表，没有外链并排，
视觉上看不出"这个链接跳错了地方"。是分类页把两张表摆到一起、
用 ``_scratch/_prod_qdiipage_compare.py`` 逐格比 href 才暴露的。

修法：新建 ``ref/stocklink.py``（对应 ``php/stocklink.php``），实现
``GetPhpLink()`` / ``GetGroupStockLink()`` / ``GetMyStockLink()`` /
``GetStockLink()`` 四支，再把 ``MysqlReference.get_stock_link()`` 接上。
``test_stocklink.py`` 里专门有一条回归测试，断言两个链接**不相等**。

PHP 原文（``php/stock/stocksymbol.php:1173``）::

    if ($str = GetGroupStockLink($this->GetSymbol())) return $str;
    return $this->GetMyStockLink();

``GetGroupStockLink()`` 靠 ``in_arrayAll()`` 判断（``GetAllSymbolArray()``
= QDII + Mix + HK + JP + EU + ChinaIndex + ChinaFuture），页面名是
``strtolower($strSymbol)``，于是 URL 形如 ``/woody/res/sh513100cn.php``。

## 六、已知取舍

1. **AB / AH / ADR 三个段落没有实现**（见 §二）。本页不会触发；真要做时
   要先迁 ``ahparagraph.php`` 与配对引用。
2. **站内链接不带服务器名**。PHP 的 ``GetInternalLink()`` 用 ``UrlGetServer()``
   拼绝对 URL；这里返回站点绝对路径（``/woody/res/xxx.php``），由部署层决定
   域名。对照脚本因此只比对路径部分。**迁移期这些链接在 Flask 站点里点不动**
   （指向的是 PHP 路径而非 ``/fund/<symbol>``）—— 与参考数据表首格的新浪
   外链同性质：先把 PHP 的形状复现对，保证可对照。
3. **没有"当前页"分支**。PHP 的 ``GetPageLink()`` 在目标就是当前页时返回
   纯文本（不加 ``<a>``）；页面层没有"当前页"概念，直接给链接。
4. **估值表一行都没有时整段不渲染**。PHP 会照常渲染一张只有表头的空表；
   沿用详情页"宁可少一块，也不摆空表让人以为数据丢了"的既定做法。
5. **建不出引用对象的符号跳过**（PHP 会直接 fatal）。
6. **``php/stocklink.php`` 里的基金公司链接区不迁**（``GetEFundSoftwareLinks``
   等二十来个函数）—— 那是页面底部的"基金公司软件"外链区，本项目还没有那一块。
   注意别和 ``$acct->EchoLinks()``（"相关链接"页脚）搞混：**后者已经迁了**
   （``web/services/site_links.py`` + ``base.html`` 两段页脚，
   见 ``README_exhaustive_holdings_migration.md`` §8.5）；
   这一条说的是 ``stocklink.php`` 那组针对具体基金公司站点的外链，仍未迁。

## 七、文件

=========================  =====================================================
文件                      内容
=========================  =====================================================
``web/services/qdii_page_service.py``  分类装配 + 两张表的组装
``web/routes/fund.py``                 ``/qdii`` 路由、页尾分组导航
``web/templates/qdii.html``            页面（标题 + 说明 + 两张表 + 导航）
``auto/ref/stocklink.py``              站内链接（``GetStockLink()`` 一族）
``auto/test_stocklink.py``             17 个用例（含"两个链接必须不同"）
``web/tests/test_qdii_page.py``        17 个用例（分类 / 组装 / 路由）
``auto/_scratch/_prod_qdiipage_compare.py``  生产端逐格对照 + selftest
=========================  =====================================================

## 八、验证

| 项目 | 结果 |
| --- | --- |
| ``auto`` 单元测试 | 894 → **911**（新增 ``test_stocklink`` 17 个）全绿 |
| ``web`` 单元测试 | 414 → **431**（新增 ``test_qdii_page`` 17 个）全绿 |
| 生产端对照 | 符号顺序逐位一致（41/41）、列数一致、两张表首格链接形状全部符合 |
| 对照 selftest | 打乱本地顺序 → 抓到（不是"从不失败的对照"） |
| 真连库 | ``/qdii`` 返回 200；``compileall`` 无语法错误 |

**已知数据覆盖差异（非结构问题）**：演示库 ``realtimequote`` 只有 14 个符号
（QQQ / SPY / ^NDX / ^GSPC / hf_NQ / hf_ES / USCNY / EUCNY / HKCNY / JPCNY +
4 只基金），所以参考数据表 41 行里只有 4 行有行情、估值表只有 3 行。
生产端 41 行全有数据。对照脚本把这种情况报成"子序列"提示而不是失败。
要补齐得先有真实的行情/净值种子数据，**不能凭空造**。
