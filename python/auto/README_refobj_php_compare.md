# refobj 的 PHP 对照验证（P1）

给 `refobj` 那 926 行代码补上**与真实 PHP 逐项对照**的验证手段。

## 一、为什么需要

第 3 步（引用对象层）与第 4 步（渲染层）一共约 2600 行新代码，
却**没有走项目已有的对照方法**：`stocksymbol` 有 6280 例对照、
`stockprefetch` 有 26800 例、数字格式化有 87 例，而 `refobj` 一例都没有。

单测（52 个）覆盖的是"我给的行为"，抓不到"我理解错了 PHP"。

## 二、范围：只对照**纯数学**

`refobj` 大部分是**有状态**的（要读 MySQL：净值、仓位、校准值、行情），
没有数据库就没法逐项对照。但恰恰是下面这批纯函数最容易出**静默**的移植错误，
而页面上的数字全部由它们算出来：

================================  ==========================================  ==============
PHP                                 作用                                         Python
================================  ==========================================  ==============
``QdiiGetCalibration``             校准值 = 中间层值 × 汇率 / 净值               `fundcalc`
``QdiiGetVal``                     中间层值 → 基金净值口径                       `fundcalc`
``QdiiGetPeerVal``                 ``QdiiGetVal`` 的逆                          `fundcalc`
``FundAdjustPosition``             按仓位加权                                    `fundcalc`
``FundReverseAdjustPosition``      上式逆                                       `fundcalc`
``StockGetPercentage``             涨跌幅百分比（含 ``MIN_FLOAT_VAL`` 除零保护）  `stockref`
``StockCalcHedge``                 对冲值 = 校准值 / 仓位                         `historytable`
``GetEstQuantity``                 中间层份额 → 基金份额（``intval`` 截断）        `refobj`
``GetQdiiQuantity``                基金份额 → 中间层份额（``intval`` 截断）        `refobj`
================================  ==========================================  ==============

## 三、运行

```powershell
cd F:\woody\woodyweb\python\auto
$env:PATH = "D:\xampp\php;" + $env:PATH

python -B _php_refobj_compare.py              # 对照，干净时 exit 0
python -B _php_refobj_compare.py --selftest   # 自检：证明它抓得到注入的错误
```

实测：**98 例，0 个 PHP-ONLY / 0 个 PY-ONLY / 0 个意外差异**。

## 四、用法集：为什么是组合生成

`VALUES` 里 23 个值，再由 `build_case_tables()` 组合出用例：

```text
TRIPLES  (v[i], v[i+3], v[i+7])      三参数不共位，避免退化组合
ADJUSTS  (0.95|1.0, v[i], v[i+2])    真实 LOF / ETF 仓位
PERCENTS 阈值边界 + 真实价格对
HEDGES   真实校准值 / 仓位
```

**关键在 ``VALUES`` 里那三个值**：`4e-7 / 5e-7 / 6e-7`。

`StockGetPercentage` 里那句 ``if (abs($divisor) > MIN_FLOAT_VAL)``（阈值 `5e-7`）
是典型的"判据附近"陷阱 —— 把 `>` 写成 `>=` 只有在这个量级才暴露。
实测前三个百分比用例的 PHP 值是 `['0', '0', '100']`，说明阈值两侧确实被区分开了。

## 五、途中撞到的三个坑（都值得记）

### 5.1 在 PHP 注释里写了 `?>`

PHP 解析器把注释里的 `?>` 当成**真的结束标签**，之后整份文件被当纯文本
输出到 stdout —— 于是 Python 收到的"JSON"其实是 PHP 源码。

```text
json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes
```

这个坑很隐蔽：报错指向 JSON，根因在两行之外的注释。**harness 文件里永远不要
写 PHP 结束标签**，注释里也不行。

### 5.2 PHP 与 Python 对同一个数的字符串化不一致

我用数值拼 key，结果同一组用例生成两个 key，87 个用例**全部**被报成
"PHP-ONLY 87 + PY-ONLY 87"。实测：

```text
PHP   sprintf('%.12E', 1e-9)  ->  '1.000000000000E-9'     指数只有 1 位
Python '%.12E' % 1e-9         ->  '1.000000000000E-09'    指数补到 2 位
```

连 `%.12E` 都不一致。**修法：key 用序号，不放数值文本** ——
序号 + 一一对应的用例表从结构上杜绝这类问题。

### 5.3 返回值比较也要抗"同值异写"

key 修好了，返回值还在比字符串：

```text
php='1.0E-7'   python='1E-07'      差值在尾数 .0 与指数补零
php='1.0E-18'  python='1E-18'
```

6 个假失败。**修法**：`normalize_value()` 把能解析成数字的归一到 `float`，
`values_equal()` 再按相对误差（1e-9）比较；非数字（`'#ERR:...'`）原样比。

## 六、一个真实的语义差异

`FundReverseAdjustPosition` 要除以仓位，仓位为 0 时：

```text
PHP 8   DivisionByZeroError
Python  ZeroDivisionError
```

两边**都抛**，只是语言给的异常类名不同。`values_equal()` 把它统一判为一致 ——
**不是**逐个登记进 `KNOWN_DIVERGENCES`：逐条登记会随着用例集扩大不断漏掉
新的同类项，而"该炸的地方两边都炸"才是要守的语义。

所以 `KNOWN_DIVERGENCES` 现在是**空的**，这是刻意的。

## 七、`--selftest`：证明它不是摆设

一个从不失败的对照脚本等于没有。`--selftest` 在内存里把公式改错
（不动源文件），断言比较逻辑抓得到：

```text
① 基准                与 PHP 不一致的用例数：0
② 乘除写反            抓到 4 个 QdiiGetCalibration 用例
③ 阈值判断改错        抓到 10 个 StockGetPercentage 用例
④ 边界区分度          前三个百分比用例 PHP 值 = ['0', '0', '100']
```

它检验的是**判据的灵敏度**，不是某个具体用例 —— 所以用例集扩大后依然有用。

## 八、不做 `GbToUtf8` 对照的原因

PHP 那个是**手写 GB2312 查表**，表在 `GB2312Sql` 里（要连数据库）。
本版改用标准库 `gb18030`，属于**有意的实现替换**，无法用同一个 harness
逐项对照。已记在 `refobj` 的 docstring 里作为已知偏差。

## 九、现在四个对照脚本的总览

| 脚本 | 用例数 | 意外差异 |
| --- | --- | --- |
| `_php_port_compare.py`（stocksymbol） | 6280 | 0（1 个已登记的已知差异） |
| `_php_prefetch_compare.py`（预取判定） | 26800 | 0 |
| `_php_numfmt_compare.py`（数字/时间格式） | 87 | 0 |
| `_php_refobj_compare.py`（**本次新增**） | 98 | 0 |
| **合计** | **33265** | **0** |
