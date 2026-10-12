# `FundReference.stock_ref` 自动建（`php/stock/fundref.php:19`）

**提交**：本次（`refobj.py` + 测试 + 对照脚本）
**PHP 源**：`php/stock/fundref.php:19`、`php/ui/smaparagraph.php:141`

---

## 一、缺口是什么

`php/stock/fundref.php` 的构造函数里有这么一段：

```php
public function __construct($strSymbol)
{
    parent::__construct($strSymbol);

    if ($this->IsFundA())
    {
        $this->stock_ref = new MyStockReference($strSymbol);   // ← 这一行
    }
    ...
}
```

Python 侧 `FundReference.__init__` 只留了"调用方注入"一条路，没有这一行，
于是 `get_stock_ref()` 恒为 `None`。后果是 QDII 均线表的换算列**整列空白**：

| | 生产端 `sz161125cn.php` | 补之前 | 补之后 |
|---|---|---|---|
| 列头 | `SZ161125` | `''` | `SZ161125` |
| 5 日行 | `3.179` | `''` | `3.136` |
| T+1 行 | `3.177` | `''` | `3.134` |

（数值不同是因为演示库的数据日期是 2026-10-07，不是生产端的当天。）

这个缺口在上一轮做 `EchoFundPairSmaParagraphs` 时被发现并记下，当时判断
"它是共享构造，喂给详情页几乎所有段落，改它得配一次受影响面受控实验，
不该夹在那轮里" —— 本轮就是来做那次实验的。

## 二、为什么"整列空白"很难被发现

`_callbackQdiiSma`（`smaparagraph.php:141`）是一个 callable 承担两件事：

```php
function _callbackQdiiSma($qdii_ref, $strEst = false)
{
    if ($strEst)    return $qdii_ref->GetQdiiValue($strEst);
    return $qdii_ref->GetStockRef();     // 列头与格子格式都来自它
}
```

列头走 `new TableColumnStock($stock_ref, 90)`，格子走
`$display_ref->GetPriceDisplay(...)`。两头都拿到 `None` 时，
Python 侧 `_stock_column()` 给出空串、格子给空串 —— **不抛异常、单测也不红**
（因为单测里的 `FakeQdiiRef` 自己实现了 `GetStockRef()`）。

这是本项目第二次栽在"替身让测试全绿、真库上空一列"上，见
`README_qdii_fund_pair_sma.md` 第五节与 `test_sma.py` 的
`FakeRefContractTest`。

## 三、改了什么

`auto/refobj.py` 的 `FundReference.__init__` 补上分支，新增 `_build_stock_ref()`：

```python
if stock_ref is not None:
    self.stock_ref = stock_ref
elif self.is_fund_a():
    self.stock_ref = self._build_stock_ref(symbol)
```

### 取舍 1：`MyStockReference` 未迁，用 `MysqlReference` 顶上

| PHP | 本版 | 理由 |
|---|---|---|
| `new MyStockReference($strSymbol)` | `MysqlReference(symbol, source=self.source)` | `MyStockReference` 构造时联网抓新浪；本项目是"`quoteupdatercli --daemon` 抓进 `realtimequote`、页面读快照"（`auto/refquote.py`），`MysqlReference` 构造已经含"读快照装填行情" |

`reference_service._build_one()` 里"PHP 的 `new StockReference($strSymbol)`"
也是这么落的，两处口径一致。

`stock_ref` 真正被用到的只有两件事：**符号**（列头）与**精度**（`GetPriceDisplay`
格式化传入值）。这两样 `MysqlReference` 都有，所以顶得上。

### 取舍 2：兜底放在调用处

```python
try:
    self.stock_ref = self._build_stock_ref(symbol)
except Exception:
    self.stock_ref = None
```

放在 `__init__` 里而不是 `_build_stock_ref()` 内部 —— 这样无论哪一步失败
（甚至将来换了实现）"这列只是装饰，不该让整页 500"的承诺都成立。
回退成 `None` 后行为与补这一行之前完全一致。

### 取舍 3：`symbol` 用参数而不是 `self.get_symbol()`

`get_symbol()` 会委托给 `stock_ref`，而此刻它正在被赋值。绕开更省心，
也避免以后有人调整构造顺序时踩雷。

### 没做的：惰性建

`EstReference.get_pair_ref()` 是**惰性**建的（README_refobj 有说明），
但 `stock_ref` 不能照做：`get_price_display()` / `get_percentage_text()` 也会
转发给 `stock_ref`，惰性会让"调用顺序"影响返回值。
PHP 是构造时建，本版跟着构造时建。

## 四、受影响面受控实验（改之前先留基线）

改的是共享构造，喂给详情页几乎所有段落，所以按 pitfalls #30 / #38 的规矩，
先用进程内 `test_client()` 抓改之前的页面，改完再抓一次 diff。

结果 —— **只有预期那一列变了**，其余九张表一行未动：

| 符号 | 变化 |
|---|---|
| `SZ161125` | 表 1 第 6 列：列头 `'' → SZ161125`，4 行格子 `'' → 3.136 / 3.114 / 3.1 / 3.168` |
| `SZ161130` | 同上 → `4.649 / 4.619 / 4.556 / 4.783` |
| `SH513100` | 只变了列头（见第六节） |
| `QQQ` / `SPY` / `SZ162411` | **无变化**（`is_fund_a()` 为假，或该表不存在） |

`SZ161130` 的 20 日行给出 `4.556`，与详情页官方 EST `4.556`、净值 `4.5452`
对得上 —— 换算方向是对的。

## 五、PHP 逐例对照：5 例 0 差异，自检 4/4

```
D:/python/python.exe -B _php_stockref_compare.py             # 5 例 0 差异
D:/python/python.exe -B _php_stockref_compare.py --selftest   # 抓到 4 / 4
```

对照的是**被这一行激活的东西**：四个委托方法
（`GetSymbol` / `GetStockId` / `GetPriceDisplay` / `GetPercentageDisplay`）。
只比"那一行"没意义 —— 它只有两行；抄错的是**参数个数**
（`GetPriceDisplay` 是三参数）才看不出来。

PHP 侧把方法体从 `fundref.php` 现场切出来塞进 stub 子类，方法体里的
`parent::` 落到 `BaseRef`、`$this->stock_ref` 可注入 —— 一字不改跑 PHP 自己的实现。
Python 侧用**真的** `refobj.FundReference`，只在 `StockReference` /
`MysqlReference` 那层包一层记账（原实现照旧执行）。

5 个用例：无 `stock_ref` 全走 parent、有全转发、三参数原样传、
无 `stock_ref` 时参数照样传 parent、百分比两参数。

自检注入的四处差错：委托方向、参数个数、参数顺序、自动建条件 —— 全部被抓到。

## 六、遗留：`SH513100` 的列头有了，格子仍空

```text
SH513100  ['均线','EST','溢价','T+1EST','溢价','SH513100','T+1EST']
          ['5日','30998.38','-0.37%','30923.01','-0.13%', '', '']
```

列头补上了，但 `GetQdiiValue()` 算不出值。它是 ETF，走**即期外汇**分支
（`get_forex_ref()`：`ETF 用 forex_ref、LOF 用 cny_ref`），而演示库里
`fx_susdcny` 没有行情快照 —— 参考数据表上那一行就写着
"美元离岸人民币即期（无行情快照）"。

与本轮改动无关（补之前这两格也是空的），属于**数据**缺口，不是实现缺口。
要验的话得先给演示库补 `fx_susdcny` 的快照。

## 七、测试

| 位置 | 数量 | 内容 |
|---|---|---|
| `auto/test_refobj.py` `StockRefAutoBuildTest` | 8 | A 股基金自动建、`QdiiReference` 继承、非 A 股基金为 `None`、注入优先、**不能是 `FundReference`（递归）**、委托恒等、建失败回退、`source` 透传 |
| `web/tests/test_sma.py` `RealQdiiReferenceTest` | 3 | 用**真的** `QdiiReference` 走一遍 `_callbackQdiiSma`：有 `stock_ref`、回调返回它、列头 `SZ161130` |

那条 `test_delegation_stays_identity` 最要紧：自动建的 `stock_ref` 与自己同符号，
所以 `get_symbol()` / `get_stock_id()` 的委托**必须是恒等变换**。一旦不是，
详情页几乎所有段落（估值 / 校准 / 参考数据）都会跟着错。

`RealQdiiReferenceTest` 的 docstring 里写明了"不许再自己造引用对象" ——
上一轮的 `FakeQdiiRef` 自带 `GetStockRef()`，正是它让单测全绿而真库空列。

总计：web **414**（+3）、auto **885**（+8）全绿，新用例在 3.13 上双绿。

## 八、本轮踩的坑

1. **兜底放错层**：第一版把 `try/except` 写在 `_build_stock_ref()` 内部，
   测试 monkeypatch 整个方法一绕过就炸。挪到调用处才对任何失败都成立。
2. **PHP harness 少一个 `}`**：stub 类 `class FundStub extends BaseRef {`
   开了括号，抽出来的方法体填在它里面，末尾得再补一个 `}` ——
   上一版脚本是 `$code .= "}\n"` 分步拼的，这次一次性拼接给漏了。
3. **对照脚本的 json 串不能逐字符比**：PHP 的 `json_encode` 不给空格、
   把 `3.0` 写成 `3`、"没传"写成 `false`；Python 是 `null` 与带空格。
   必须按结构解析后再比（见 `_norm_ret()`）。
4. **探针重复包装**：`run_python()` 每次都装一遍探针会层层嵌套，
   同一个调用被记两遍。加 `_PROBES_INSTALLED` 只装一次。

## 九、下一步

* 演示库补 `fx_susdcny` 行情快照，验 ETF 分支（第六节）；
* 回到排期：`portfolioparagraph`（160 行，含 PHP `static $fCny`）。
