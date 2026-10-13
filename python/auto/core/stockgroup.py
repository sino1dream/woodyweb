"""股票分组的货币汇总模型。

对应 PHP 两个文件：

* ``php/class/multi_currency.php`` —— :class:`MultiCurrency`（34 行）；
* ``php/stockgroup.php`` —— :class:`StockGroup` / :class:`MyStockGroup`（146 行）。

这是**资金汇总段落**（``EchoMoneyParagraphs``）的底层：分组下的每一笔交易
先按**币种**分桶累加（A 股 / 国内期货 / 离岸人民币 → 人民币，H 股 → 港币，
其余 → 美元），再把三个桶按汇率折成一个"折人民币"总数。

## 一、为什么要分两个累加器

``StockGroup`` 持有 **两个** ``MultiCurrency``：``multi_amount``（市值）与
``multi_profit``（盈亏）。费率、汇率只影响市值，不影响盈亏，
所以两个桶必须分开走 —— 混在一个对象里会让"折人民币盈亏"把汇率波动
也算成盈亏。

## 二、三处容易改错的地方

### 1. ``fx_susdcnh`` 归人民币桶

``OnStockTransaction()`` 的分桶条件（``php/stockgroup.php:26``）::

    if ($sym->IsSymbolA() || $sym->IsSinaFutureCN() || $sym->GetSymbol() == 'fx_susdcnh')

``fx_susdcnh`` 是美元兑离岸人民币的**外汇符号**，走"其余 → 美元"那一支
会让整张资金表的美元列多出一份"汇率本身"的市值。它在组合里是**对冲工具**
而不是持仓，作者把它按人民币记（与 A 股同桶）。

### 2. 三个 ``fConvert*`` 是 ``null`` 起步，不是 0.0

PHP 的构造函数只初始化 ``fCNY/fHKD/fUSD``，三个 ``fConvert*`` 是
``var $fConvertCNY;`` —— **未赋值**，即 ``null``，只有调过
``Convert()`` 才有值。本版照抄（:class:`MultiCurrency` 里初始化为
``None``）：这样"没折算过"与"折算出来是 0"在下游能区分开，
``GetNumberDisplay(null)`` 与 ``GetNumberDisplay(0.0)`` 的产出也不同。

### 3. ``Convert()`` 的除数守卫是"有汇率才除"

.. code-block:: php

    $this->fConvertUSD = $strUSDCNY ? $this->fConvertCNY / floatval($strUSDCNY) : 0.0;

没有汇率时**直接给 0.0**，不是"跳过赋值"（那会留着 null），也不是除零。
生产上 ``$strUSDCNY`` 是外汇行情价，取不到时空串/false，于是折美元列
显示 0 —— 总比 PHP 的除零警告 + ``INF`` 好读。

注意 ``if ($strUSDCNY)`` 是 **PHP 真假判断**：``'0'`` 是假值，
而 Python 里非空字符串都是真值。这里走 :func:`_php_truthy`。

## 三、数据库在哪儿

PHP 的 ``MyStockGroup::__construct($strGroupId, $arRef)`` 在构造时**当场查
``StockGroupItemSql``**（``php/stockgroup.php:131``），把 ``stockgroupitem``
表里预统计的 ``record/quantity/cost`` 灌进每一笔交易。本版把这个循环
**外提**成构造参数 ``records``：

* 传 ``records``（形如 ``[{'stock_id':…, 'record':…, 'quantity':…, 'cost':…}, …]``）
  时逐条回放 PHP 构造函数的循环；
* 不传就只建"空交易"，由调用方调 :meth:`MyStockGroup.set_value` 填数
  —— 演示库没有 ``stockgroupitem`` 表，测试也靠这条路径。

``symbol_of`` 是 ``SqlGetStockSymbol()`` 的替身（``stock_id`` → 符号），
``ref_builder`` 是 ``StockGetReference()`` 的替身（``_checkSymbol`` 里
遇到没见过的符号时要现建一笔交易）。两者都缺省为 ``None``，
此时遇到未知符号**直接跳过** —— 与 PHP 建不出引用对象时的表现一致
（那笔交易不会出现在表里）。

## 四、命名

与 :mod:`core.stocktrans` 保持一致：**属性用 snake_case**（``f_cny``、
``multi_amount``），**方法补 camelCase 别名**（``Convert``、
``OnStockTransaction``…）。PHP 的 ``$group->multi_amount->fCNY`` 在本版
写作 ``group.multi_amount.f_cny``。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from core.stocktrans import MyStockTransaction, _php_floatval


#: ``OnStockTransaction()`` 里那个"看着像美元、其实按人民币记"的符号。
FX_SUSDCNH_SYMBOL = "fx_susdcnh"


def _php_truthy(value: Any) -> bool:
    """PHP 真假判断：``''`` / ``'0'`` / ``False`` / ``None`` 都是假值。

    与 :func:`ref.stocklink._php_truthy` 同一套语义（那里为链接的
    ``if ($strSymbol)`` 服务）。这里的服务对象是 ``Convert()`` 的
    ``if ($strUSDCNY)`` —— 汇率取不到时空串，就有汇率除、没汇率给 0.0。
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value not in ("", "0")
    return bool(value)


#: 公开别名：``_echoMoneyItem()`` 的 ``empty()`` 判定也走同一套真假语义，
#: 那边是跨模块使用，所以给一个不带下划线的名字。
php_truthy = _php_truthy


def _call(ref: Any, snake: str, camel: str) -> bool:
    """先试 snake_case 再试 camelCase，结果转成布尔。"""
    method = getattr(ref, snake, None)
    if method is None:
        method = getattr(ref, camel, None)
    if method is None:
        return False
    return bool(method())


def _symbol_of_ref(ref: Any) -> Any:
    getter = getattr(ref, "get_symbol", None)
    if getter is None:
        getter = getattr(ref, "GetSymbol", None)
    if getter is None:
        return None
    return getter()


class MultiCurrency:
    """对应 ``php/class/multi_currency.php`` 的 ``MultiCurrency``。

    三个原始桶（``f_cny`` / ``f_hkd`` / ``f_usd``）+ 三个折算结果
    （``f_convert_cny`` / ``f_convert_usd`` / ``f_convert_hkd``）。
    后者**只在** :meth:`convert` 之后才有值（初始 ``None``，见模块
    docstring 第 2 条）。
    """

    def __init__(self) -> None:
        self.f_cny = 0.0
        self.f_hkd = 0.0
        self.f_usd = 0.0
        # PHP 里这三个是 var $fConvertXXX; —— 未赋值即 null，照抄
        self.f_convert_cny = None
        self.f_convert_hkd = None
        self.f_convert_usd = None

    def convert(self, usdcny: Any = False, hkdcny: Any = False) -> None:
        """对应 ``Convert()``：三桶折成一个"折人民币"总数，再反折回各币种。

        .. code-block:: php

            $this->fConvertCNY = $this->fCNY;
            if ($strUSDCNY)   $this->fConvertCNY += $this->fUSD * floatval($strUSDCNY);
            if ($strHKDCNY)   $this->fConvertCNY += $this->fHKD * floatval($strHKDCNY);
            $this->fConvertUSD = $strUSDCNY ? $this->fConvertCNY / floatval($strUSDCNY) : 0.0;
            $this->fConvertHKD = $strHKDCNY ? $this->fConvertCNY / floatval($strHKDCNY) : 0.0;

        注意**折美元/折港币都是拿"折人民币总数"反除**，不是"本币 + 折算"
        —— 于是美元列里也含着 A 股市值折过来的部分。这正是作者要的口径：
        一眼看到"全部换成美元是多少"。
        """
        self.f_convert_cny = self.f_cny
        if _php_truthy(usdcny):
            self.f_convert_cny += self.f_usd * _php_floatval(usdcny)
        if _php_truthy(hkdcny):
            self.f_convert_cny += self.f_hkd * _php_floatval(hkdcny)

        self.f_convert_usd = (self.f_convert_cny / _php_floatval(usdcny)
                              if _php_truthy(usdcny) else 0.0)
        self.f_convert_hkd = (self.f_convert_cny / _php_floatval(hkdcny)
                              if _php_truthy(hkdcny) else 0.0)


class StockGroup:
    """对应 ``php/stockgroup.php`` 的 ``StockGroup``（基类）。

    只做两件事：按币种累加（:meth:`on_stock_transaction`）与折算
    （:meth:`convert_currency`）。分组名、成员等账号信息都在子类 /
    调用方，这里不碰。
    """

    def __init__(self) -> None:
        self.multi_amount = MultiCurrency()
        self.multi_profit = MultiCurrency()
        # PHP 是 var $strGroupId = false —— 未分组（"全部"）时为 false
        self.group_id = False

    def get_group_id(self) -> Any:
        return self.group_id

    def on_stock_transaction(self, trans: Any) -> None:
        """对应 ``OnStockTransaction()``：按**币种**分桶累加市值与盈亏。

        .. code-block:: php

            $sym = $trans->ref;          // 注意是属性，不是方法
            if ($sym->IsSymbolA() || $sym->IsSinaFutureCN()
                    || $sym->GetSymbol() == 'fx_susdcnh')   { CNY }
            else if ($sym->IsSymbolH())                     { HKD }
            else                                            { USD }

        ``$trans->ref`` 为空时 PHP 会**致命错**（在 null 上调方法）。
        本版退化：三个判定都拿不到就落进 else 分支（美元桶）。
        生产上 ``ref`` 恒非空，这条只影响构造错误的调用。
        """
        ref = getattr(trans, "ref", None)
        is_cny = (
            _call(ref, "is_symbol_a", "IsSymbolA")
            or _call(ref, "is_sina_future_cn", "IsSinaFutureCN")
            or _symbol_of_ref(ref) == FX_SUSDCNH_SYMBOL
        )
        if is_cny:
            self.multi_amount.f_cny += trans.get_value()
            self.multi_profit.f_cny += trans.get_profit()
        elif _call(ref, "is_symbol_h", "IsSymbolH"):
            self.multi_amount.f_hkd += trans.get_value()
            self.multi_profit.f_hkd += trans.get_profit()
        else:
            self.multi_amount.f_usd += trans.get_value()
            self.multi_profit.f_usd += trans.get_profit()

    def convert_currency(self, usdcny: Any = False, hkdcny: Any = False) -> None:
        """对应 ``ConvertCurrency()``：市值与盈亏**各折一次**。"""
        self.multi_amount.convert(usdcny, hkdcny)
        self.multi_profit.convert(usdcny, hkdcny)


class MyStockGroup(StockGroup):
    """对应 ``php/stockgroup.php`` 的 ``MyStockGroup``。

    在基类之上多一个 ``ar_stock_transaction``（每个符号一笔
    :class:`~core.stocktrans.MyStockTransaction`）。

    PHP 的构造函数会**当场查库**，本版把那段循环外提成参数 ``records``，
    详见模块 docstring 第三节。
    """

    def __init__(self,
                 group_id: Any = False,
                 refs: Sequence[Any] = (),
                 records: Iterable[Any] = None,
                 symbol_of: Callable[[Any], Any] = None,
                 ref_builder: Callable[[str], Any] = None) -> None:
        super().__init__()

        self.group_id = group_id
        self.ref_builder = ref_builder
        self.ar_stock_transaction = []
        for ref in refs or ():
            self._add_transaction(ref)

        if records is not None:
            self._load_records(records, symbol_of)

    # -- PHP 构造函数里的查库循环 -----------------------------------------
    def _load_records(self, records: Iterable[Any],
                      symbol_of: Callable[[Any], Any] = None) -> None:
        """回放 ``MyStockGroup::__construct()`` 里那段循环。

        .. code-block:: php

            while ($record = mysqli_fetch_assoc($result)) {
                if (intval($record['record']) > 0) {
                    $this->SetValue(SqlGetStockSymbol($record['stock_id']),
                                    intval($record['record']),
                                    intval($record['quantity']),
                                    floatval($record['cost']));
                }
            }

        **只有** ``record`` 大于 0 才灌进去 —— 分组里预留了标的但一笔都没
        买过的人，不应该在资金表里看到那一行。

        ``record`` 字段来自数据库的 ``int``，所以用 :func:`int` 直转
        （不需要 ``intval()`` 的字符串语义）；缺字段按 0 处理，于是跳过。
        """
        for record in records or ():
            total_records = int(record.get("record") or 0)
            if total_records <= 0:
                continue
            stock_id = record.get("stock_id")
            symbol = symbol_of(stock_id) if symbol_of else stock_id
            self.set_value(
                symbol,
                total_records,
                int(record.get("quantity") or 0),
                float(record.get("cost") or 0.0),
            )

    # -- 交易集合 ----------------------------------------------------------
    def get_stock_transaction_array(self) -> list:
        return self.ar_stock_transaction

    def get_stock_transaction_by_symbol(self, symbol: Any) -> Any:
        """对应 ``GetStockTransactionBySymbol()``：线性查找，找不到回 ``None``。

        PHP 返回 ``false``，本模型里 ``None`` 是同义（调用方只做真假判断）。
        """
        for trans in self.ar_stock_transaction:
            if trans.get_symbol() == symbol:
                return trans
        return None

    def _add_transaction(self, ref: Any) -> None:
        """对应 ``_addTransaction()``：``new MyStockTransaction($ref, $strGroupId)``。"""
        self.ar_stock_transaction.append(MyStockTransaction(ref, self.group_id))

    def _check_symbol(self, symbol: Any) -> None:
        """对应 ``_checkSymbol()``：没见过的符号现建一笔交易。

        PHP 是 ``StockGetReference($strSymbol)`` —— 连库 + 建引用对象。
        本版走构造参数 ``ref_builder``；没给就**跳过**（见模块 docstring）。
        """
        if self.get_stock_transaction_by_symbol(symbol):
            return
        if self.ref_builder is None:
            return
        self._add_transaction(self.ref_builder(symbol))

    def set_value(self, symbol: Any, total_records: int = 0,
                  total_shares: int = 0, total_cost: float = 0.0) -> None:
        """对应 ``SetValue()``：填一笔交易的三个总量，然后计入分桶。

        .. code-block:: php

            $this->_checkSymbol($strSymbol);
            foreach ($this->arStockTransaction as $trans) {
                if ($trans->GetSymbol() == $strSymbol) {
                    $trans->SetValue($iTotalRecords, $iTotalShares, $fTotalCost);
                    $this->OnStockTransaction($trans);
                    break;
                }
            }

        两个细节：先 ``_checkSymbol`` 保证存在（可能当场新建），
        找到后 **break**（同一个符号只可能有一笔，后来者进不来）。
        """
        self._check_symbol(symbol)
        for trans in self.ar_stock_transaction:
            if trans.get_symbol() == symbol:
                trans.set_value(total_records, total_shares, total_cost)
                self.on_stock_transaction(trans)
                break

    def get_total_records(self) -> int:
        """对应 ``GetTotalRecords()``：所有交易的笔数之和。

        ``EchoStockTransaction()`` 用它判"这个分组有没有交易"
        （``> 0`` 才渲染段落），所以**不能**用交易条数代替。
        """
        total = 0
        for trans in self.ar_stock_transaction:
            total += trans.get_total_records()
        return total


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：方法保留 camelCase 别名）
# ---------------------------------------------------------------------------
for _camel, _snake in (
    ("Convert", "convert"),
):
    for _cls in (MultiCurrency,):
        if hasattr(_cls, _snake):
            setattr(_cls, _camel, getattr(_cls, _snake))

for _camel, _snake in (
    ("GetGroupId", "get_group_id"),
    ("OnStockTransaction", "on_stock_transaction"),
    ("ConvertCurrency", "convert_currency"),
    ("GetStockTransactionArray", "get_stock_transaction_array"),
    ("GetStockTransactionBySymbol", "get_stock_transaction_by_symbol"),
    ("SetValue", "set_value"),
    ("GetTotalRecords", "get_total_records"),
):
    for _cls in (StockGroup, MyStockGroup):
        if hasattr(_cls, _snake):
            setattr(_cls, _camel, getattr(_cls, _snake))

del _camel, _snake, _cls
