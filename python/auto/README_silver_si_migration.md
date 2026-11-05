# 白银 LOF（`SZ161226`）的「纽约白银」行、实时 EST 与场内份额

> 起因：`/woody/res/sz161226cn.php` 接通后（见 `README_legacy_urls.md`），
> 这一页仍比生产端少两块 —— 参考数据没有 `SI`（纽约白银）行、实时 EST 等于参考 EST；
> 并且「基金场内份额」整段不出现。

## 一、两条数据缺口，一处代码缺口

| 现象 | 根因 | 修法 |
|---|---|---|
| 参考数据少 `SI` 行 | `stock` 表里**没有 `hf_SI`**，`fundpair` / `futurespair` 也没有 `nf_AG0 → hf_SI` | `dbtools/patch_silver_si.py` |
| 实时 EST == 参考 EST | 同上：没有配对期货 → `fundestcalc._futures_realtime_quote()` 短路，回落中间层快照 | 同上 + `calc/futurescalib.py` 的换算表 |
| 「基金场内份额」整段消失 | `shareshistory` 里没有 `SZ161226` 的行，段落守卫（PHP 同款）直接返回 | `dbtools/patch_shares_sz161226.py` |

## 二、链路：两级，中间一跳要**单位换算**

```text
SZ161226 ──fundpair──> nf_AG0 ──futurespair──> hf_SI
（国投白银LOF）        （沪银连续）            （纽约白银，美元/盎司）
```

PHP 只在 `SZ161226` 上挂这条实时回调（`woody/res/php/_chinafuture.php:20-31`），
其余五只 A股商品基金 `$strRealtime = false`：

```php
$strRealtime = 'hf_SI';   $strCNH = 'fx_susdcnh';
$callback = '_RealtimeCallback';

function _RealtimeCallback() {
    return 1000.0 * $realtime_ref->GetVal() * $cnh_ref->GetVal() / 31.1035;
}
```

对应本地的 `calc.futurescalib.FUTURES_VALUE_TRANSFORMS["nf_AG0"]`：
`hf_SI × fx_susdcnh × 1000 ÷ 31.1035` → **元/千克**（沪银的报价单位）。

### 为什么这条不能只靠 `÷ cal`

其它五对（`^NDX ← hf_NQ` 等）两端同量纲，换算只有 `期货价 ÷ cal`，
`cal` 是"期货 / 指数"的比值，存在 `calibrationhistory` 里。

白银这一条两端**不同量纲**：美元/盎司 ↔ 元/千克。如果把汇率与盎司-克
塞进 `cal`，汇率一动 `cal` 就得跟着重算，而 `cal` 是校准历史里的**死数**。
所以换算表单独放，`cal` 不参与 —— 换出来的值直接与 `est_cal`
（AG0 在**校准日**的 `adjclose`，即结算价 12013）同空间。

`est / est_cal` 这一条约束没变（见 `futurescalib.realtime_source_value`
的"为什么必须停在指数空间"）：
`11424 / 12013 = 0.951`，再乘净值才是 EST。

### 缺汇率就返回 `None`，不按 1.0 算

漏乘 6.7 倍的值照样能显示在页面上（只是错的），加一道量级保险更划算：
换算完与 `nf_AG0` 的实时快照价比，偏离超过 50% 就拒收，
让调用方回落到"中间层快照"——值是对的，只是不带纽约白银的信息。

## 三、顺带对齐的两处（同一页）

1. **参考数据没有「USDCNY 即期 / USCNY 中间价」两行**。
   PHP `fundpairref.php:17` 的 `IsSinaFutureCN()` 是**空分支**，`cny_ref`
   保持 false、`GetDefaultCny()` 返回 1.0 —— 这一族压根没有汇率这一档。
   本地原先按"地区表"给 `SZA` 配了美元即期与中间价，多出两行。
   `reference_service._fund_reference_symbols()` 现在对 `nf_` 一族直接置空。
   那行 `USDCNH` 来自页面级的 `fx_susdcnh`，与地区表无关，保留。
2. **配对标的排在基金前面**。`_chinafuture.php:47` 是
   `CreateGroup(array($this->ref->GetPairRef(), $this->ref))`，
   与 `_qdiigroup.php` 的 `array($stock_ref, $est_ref)` 顺序相反。
   只对 `nf_` 一族生效（`reference_service._pair_goes_first()`）。

改完本地 4 行与生产端**逐行对应**：`AG0 / SZ161226 / SI / USDCNH`。

## 四、场内份额：数据照抄生产端

`php/ui/fundshareparagraph.php:27` 的守卫是"取不到记录就不 echo 这张表"，
本地 `detail_sections.build_shares()` 同款（`rows` 为空返回 `None`）。

份额数字来源：生产端这一页**公开可见**（不像 `fundsharecn.php` 要登录），
2026-11-05 10:52 抓取，10 行原样写进 `shareshistory`（单位**万份**）。
「数量」与「换手」两列**不存** —— 它们来自 `stockhistory.volume`
（PHP `$his_sql->GetVolume()`），本地 10-23 ~ 11-03 的成交量与生产端**逐位相同**，
所以照抄份额就能复原整张表。

两处刻意多写的数据：

* `2026-10-22 = 365457.43` 万份 —— 不显示（表格只取最近 10 行），
  但没有它 `2026-10-23` 的「场内新增」会是空的（`get_close_prev` 找不到上一行）。
  值由生产端 `365457.68 − 0.25` 反推。
* `stockhistory` 的 `2026-11-04`（close 1.507 / volume 134981971）——
  本地日线归档只到 11-03，少了这天 11-04 的「数量」会显示 0。
  **`2026-11-05` 不写**：当天收盘价与成交量还在变。

## 五、已知遗留

* **今天那行（`2026-11-05`）的「数量 / 换手」是 `0 / 0%`** —— 本地
  `stockhistory` 还没有今天这一行，PHP 在同样缺行时也输出 0。
  收盘之后跑一次
  `python -B dbtools/seed_a_share_history.py --apply --symbols SZ161226`
  就会补上（`write_history` 是 upsert，收盘价与成交量一起覆盖）。
  **注意** `quotecli --archive-snapshot` 只写收盘价、`volume` 传空 → 落库是 0，
  所以它补不了这一列。
* **`nf_AG0` 的 `calibrationhistory` 有一行 274905.673758**，口径不明
  （不是"期货 / 基金净值"的 8868，也不是"期货 / 指数"的 ≈1.0）。
  换算分支不读它，所以不影响本页；`futurescli` 现在也**不会再往它上面叠**
  （见 §九 与 `README_futures_migration.md` §3.4）。留给"理顺 `nf_` 的 cal
  语义"那一轮处理。
* ~~`futurescli` 的"刷新指数快照"对 `nf_AG0` 报刷新失败~~ —— 2026-11-05
  查清并修掉，两个错叠在一起，见 §九。

## 九、`futurescli` 对 `nf_AG0` 报"刷新失败"：两个错（2026-11-05 修）

**症状**：`futurescli` 开头那张"刷新指数快照"表里，`nf_AG0` 是唯一一个
"刷新失败"，其余五个指数都"已刷新"。

**第一层：为什么失败。** `_refresh_index_snapshot()` 直接调
`StockReference.load_sina_data()` —— 那是"普通股票/指数"那一支
（`_on_sina_data_cn` / `_hk` / `_us`）。这个分派在 PHP 里属于
`MyStockReference::LoadData()`（`php/stock/mystockref.php:117`）。
`nf_AG0` 既不是 A 股也不是港股符号，于是落到"字段数 >= 29 就算美股"，
拿第 26 个字段（`gb_*` 的日期时间）当日期 —— 内盘期货的日期在第 18 个、
时刻在第 2 个（`HHMMSS`），那里是空串：

    ValueError: 无法解析时间字符串：''

改法：走 `load_data()`（与 `quoteupdater.parse_symbol()` 同一套三分支）。

**第二层：修好反而更糟，所以整对跳过。** 就算解析对了，这一对也不该进
这两个循环：

* `RealtimeQuoteStore.save()` 是**整行 UPSERT**，不给 `vwap` / `orderbook`
  就写成空串 / NULL —— `realtimequote.vwap` 正是商品期货基金「官方 EST」
  取均价的来源（`fundpairref.php:242`，豆粕 2.0846 vs 2.054 那件事）。
  刷一次快照就把官方 EST **静默**打回结算价。
* 校准对它没有意义：两端量纲不同，PHP `MyPairReference::__construct`
  也只在 `IsSinaFutureUS()` 时才调 `AverageCalibraion()`
  （`fundpairref.php:160`）。硬算得到"纽约银价 ÷ 沪银价" ≈ 0.004，
  `--apply` 一跑就往 `calibrationhistory` 里叠一个错量级的值。

所以 `futurescli._calibratable_pairs()` 把换算族整对滤掉，两个循环都只走
"同量纲"的配对；`nf_AG0` 仍留在 `_index_future_pairs()` 里，`--show`
照样列得出来。现在的输出：

    刷新指数快照（让两边的时刻可比）：
      GLD      已刷新
      ^GSPC    已刷新
      ^HSI     已刷新
      ^NDX     已刷新
      nf_AG0   跳过（换算族 hf_SI：快照归行情守护进程，也不做期货校准）
      znb_NKY  已刷新

**同一个坑的第二个入口**：`refquote.apply_network()` 原来也一律调
`load_sina_data()`。两个调用点（`routes/fund.py` / `routes/stock.py` 的
`_fill_order_book`，`?live=1` 触发）都用 `try/except` 包着，所以对 `nf_` /
`fx_` 符号是**静默**失败 —— 现在按符号类型分派（`_parse_quote_text()`）。

## 六、复核

```bash
# 参考数据 4 行 / 实时 EST 是否真的走了纽约白银
curl -s http://127.0.0.1:5000/fund/SZ161226 | grep -o "纽约白银\|31.1035"

# 三档估值（实时 EST 必须 ≠ 参考 EST）
python -B auto/cli/futurescli.py --est --symbol SZ161226

# 份额表（10 行，除今天那行的数量外应与生产端逐位相同）
curl -s http://127.0.0.1:5000/history/shareshistory/SZ161226
```

单测：

* `auto/test_futures.py::SilverTransformTest`（5 条）—— 换算、缺汇率不猜 1.0、
  量级保险、"算法说明"只给这一族加算式；
* `web/tests/test_reference.py::CnFutureReferenceOrderTest`（4 条）—— 配对在前、
  没有汇率两行、整张表就是生产端那 4 行。
