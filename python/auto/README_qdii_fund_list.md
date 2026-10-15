# 基金指数对照表（带对冲值那一版）

| | |
|---|---|
| PHP | ``php/ui/fundlistparagraph.php:38 EchoFundListParagraph($arRef, $callback)``、``:14 _echoFundListItem()``、``woody/res/php/_qdiigroup.php:41 _callbackFundListHedge()`` |
| 本版 | ``web/services/qdii_fund_list.py`` + ``web/routes/fund.py`` 的 ``_build_fund_list()`` |
| 上线位置 | 基金详情页「均线之后、期货均线之前」（对应 ``_qdiigroup.php:100`` 的顺序） |
| 对照 | ``auto/_php_fundlisthedge_check.php`` + ``_php_fundlisthedge_compare.py``：**5 例逐字一致，2 条已知差异，自检 4/4** |

## 一、它在讲什么

生产端 ``sz161125cn.php`` 的 ``fundlisttable``::

    代码 | 跟踪代码 | 仓位 | 校准值    | 日期        | 对冲值
    SH   | ^GSPC    | -1   | 245.591  | 2026-10-09 | -72
    SPY  | ^GSPC    | 1    | 10.035   | 2026-10-09 | 1732

前五列是这个杠杆标的**自己**的属性；最后一列是它与**正在看的这只基金**之间的比值 ——
回答"对冲一份本基金要配多少份它"。所以同一列里既有正的也有负的
（做空标的仓位是 −1，对冲值跟着变号）。

``EchoFundListParagraph()`` 有**两种形态**，取决于有没有传回调：

* 传了回调（只有 ``_qdiigroup.php:100``）→ 末列是对冲值，**本版**；
* 没传（``fundlistcn.php`` / ``_chinaindex.php`` / ``_qdiimix.php``）→ 末列是
  「参考值」，那一版在 ``web/services/fund_list_service.py``，**不在本文范围**。

## 二、对冲值那一格：公式不用重写

```php
function _callbackFundListHedge($strSymbol) {
    $ref = $acct->GetRef();
    return GetStockHedge($ref->GetSymbol(), $ref->GetStockId(), $strSymbol);
}
```

``GetStockHedge()``（``php/stock.php:401``）因为**显式给了** ``$strLev`` 而走比率
分支：扫描本符号的全部校准记录（``ORDER BY \`date\` DESC``），取"两边都有值的
第一个日期"，返回 ``(本校准值/本仓位) / (对校准值/对仓位)``；扫不到就 ``1.0``。

**这条公式已经迁好了** —— ``auto/stockdata.py`` 的
``MySqlFundDataSource.get_stock_hedge()``。本模块只做两件事：

1. 把"本基金"取对（``$acct->GetRef()`` 是**正在看的基金**，不是行里的标的）；
2. 数据源没有那个方法时兜成 ``1.0``（PHP 同名的兜底值）。

容易搞错的一点：**比率的方向**。「本 / 对」与「对 / 本」差一个倒数，而两个数
都可能是几千，肉眼看不出哪个对 —— 对照脚本的自检专门注入了这个错误
（"对冲方向搞反"一例触发 3 例不一致）。

## 三、两处容易抄漏的细节

### 3.1 「跟踪」列只有第一个出现的是链接

```php
function _getFundPairLink($ref) {
    static $arSymbol = array();
    if (in_array($strSymbol, $arSymbol)) return $ref->GetDisplay();   // 纯文本
    $arSymbol[] = $strSymbol;
    return $ref->GetMyStockLink();                                     // 链接
}
```

生产端实测：SH 行的 ``^GSPC`` 是链接，SPY 行的同一个 ``^GSPC`` 是纯文本。
本版用 ``seen_pairs`` 集合表达（作用域是这一张表，PHP 那边是本次请求）。

### 3.2 数字按 0 为基准染色，容差是 ``0.5 / 10**精度``

``GetNumberDisplay()`` → ``StockGetPriceDisplay($fVal, 0.0, ...)``：

* 大于 +容差 → ``black``，而 ``black`` **不包 ``<font>``**（等于纯文本）；
* 小于 −容差 → ``red``；
* 落在容差内 → ``grey``。

精度 0 时容差是 0.5，所以 ``0.3`` 会染灰而不是黑。这条规则集中在
``_number_cell()`` 里，三列数字共用。

## 四、已知偏差

| # | 偏差 | 理由 |
|---|---|---|
| 1 | 表 id 带符号前缀（``SZ161125fundlist``） | PHP 写死 ``'fundlist'``；本版沿用 ``calibration_service`` 的做法。同一页将来若摆两张这种表不会撞 id |
| 2 | 「基金指数对照表」链接摆在表**后**的 note 里 | PHP 用 ``EchoTableParagraphBegin(..., $str)`` 摆在表前。位置不同、内容一样 |
| 3 | ``pair_symbol`` 参数 | PHP 的 ``FundPairReference`` 自己知道跟踪谁。~~那个类未迁，引用对象答不上来~~ → **2026-10-15 复核：类已迁**（CK2 ``0a6b7632`` / CK2b ``91d188ce``，``get_pair_ref()`` 可用）。**本版仍由调用方补** —— 改成查引用对象的 ``get_pair_ref()`` 是接线改动，不在本轮范围；真库实测这一列本来就是空的。路由找这些标的时用的正是"谁跟踪了中间层标的" |
| 4 | ``case.5``：校准值 ``0.0`` 时 PHP 8 抛 ``DivisionByZeroError``（整页 500），本版兜 ``1.0`` | **不复制崩溃**。页面上的一个装饰列不该让整页 500 |
| 5 | ``case.6``：校准值 ``'0'`` 时 PHP 判假并跳过该日期，本版当成有效值 → 除零 → 兜 ``1.0`` | 同上。改共享的 ``get_stock_hedge`` 是在用别处的风险换这里的 fidelity —— 它被其它页面共用 |
| 6 | 演示库里「校准值」显示 ``0.009``、「对冲值」是几千 | 演示库把中间层净值归一化成 1.0，与五档表那一列的已知偏差同源（见 ``README_qdii_trading_column.md`` 第六节）。是数据的表达，不是实现问题 |

## 五、对照与测试

``_php_fundlisthedge_check.php`` 从真实 PHP 里**现场抽出函数体**再跑，用了两个手法：

* 抽出来放进 ``namespace Harness``，再在该命名空间里定义
  ``mysqli_fetch_assoc()`` / ``mysqli_free_result()`` —— PHP 解析未限定函数名时
  **先看当前命名空间**，于是函数体一个字不改却调到替身（不然
  ``mysqli_fetch_assoc()`` 只认真 ``mysqli_result``，PHP 8 直接 TypeError）；
* ``GetAll()`` 的 fixture 按日期倒序给，与 PHP 的 ``ORDER BY \`date\` DESC``
  （``DailyCloseSql::BuildOrderBy()`` → ``_SqlOrderByDate()``）一致。

对照结果（PHP 8.0.10）：

```text
✓ case.0  两边最新日期都对得上        1658.4674967088874 → 显示 1658
✓ case.1  只有旧日期对得上           2.319353106233446  → 显示 2
✓ case.2  杠杆标的没有校准值 -> 1.0
✓ case.3  本基金没有校准值 -> 1.0
✓ case.4  做空标的（仓位为负）       -67.76600661047712 → 显示 -68
  case.5  校准值 0.0   PHP: DivisionByZeroError   Python: 1.0   （已知差异）
  case.6  校准值 '0'   PHP: 2.3193（判假跳过）     Python: 1.0   （已知差异）
```

自检 ``--selftest`` 注入四种错误全部被抓：对冲方向搞反、忘了除仓位、
只看最新日期不往下扫、缺数据时返回 ``None`` 而不是 ``1.0``。

测试：

* ``web/tests/test_qdii_fund_list.py``（28 个，离线，3.7 / 3.13 双绿）——
  callback 的调用姿势、表的守卫、列头、行内顺序与染色、两个链接的去重、
  ``pair_symbol`` 兜底；
* ``web/tests/test_qdii_fund_list_route.py``（8 个，需要 flask）——
  **直接断言渲染后的 HTML**：表头「跟踪代码」不能丢前缀、负数染红、
  首格链接、``^GSPC`` 只出现一次链接、没有杠杆标的时整段不出现且页面仍 200。
  之所以要这么写：上一轮「校准记录」就栽在"单元测试全绿、页面表头是空"。

## 六、真库验收（2026-10-12）

```text
/fund/SZ161130   QQQ  ^NDX  1  0.009  2026-10-07  3775
                 TQQQ ^NDX  1  1     （空）        1
/fund/SZ161125   SPY  ^GSPC 1  0.009  2026-10-07  2672
/fund/SZ162411   没有这张表（fundpair 里没有跟踪它中间层标的的符号）
```

链接：首格指向 ``/history/calibrationhistory/<符号>``；「跟踪」列只有**第一行**
是 ``/fund/%5ENDX``（Werkzeug 把 ``^`` 百分号编码，浏览器一样解码）；
note 里有分组列表页链接。

## 七、一个环境坑（不是代码问题）

改完代码后 HTTP 上看到的还是旧页面：``5000`` 端口上**有两个** Flask 进程在监听
（先前后台启的没退干净），请求被旧代码那个接走了。判断方法是拿进程内
``create_app().test_client()`` 直接渲染对比 —— 它每次都是新代码。
深层教训与 pitfalls #38 同族：**先确认自己看的是不是我以为的那个东西**。

## 八、仍未完成

* ``EchoFundPairSmaParagraphs()``（``_qdiigroup.php:101``）—— 紧跟在这张表后面，
  给每个杠杆标的各来一张均线表；
* ``FundPairReference`` 整体迁移后，``pair_symbol`` 这个参数可以删掉，
  ``web/services/qdii_trading_column.build_leverage_references()`` 也可以一并删。
