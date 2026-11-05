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
  换算分支不读它，所以不影响本页；但 `futurescli` 若哪天成功给
  `nf_AG0 ← hf_SI` 校准（沪银 15:00 收、纽约白银按美东时间，实际对不齐），
  会往这行上再叠一个平均。留给"理顺 `nf_` 的 cal 语义"那一轮处理。
* `futurescli` 的"刷新指数快照"对 `nf_AG0` 报**刷新失败**
  （2026-11-05 实测），其余五个指数都成功。快照本身由 `quoteupdater`
  从 `stockhistory` 反推的符号集覆盖，不影响实时 EST；失败原因未查。

## 六、复核

```bash
# 参考数据 4 行 / 实时 EST 是否真的走了纽约白银
curl -s http://127.0.0.1:5000/fund/SZ161226 | grep -o "纽约白银\|31.1035"

# 三档估值（实时 EST 必须 ≠ 参考 EST）
python -B auto/cli/futurescli.py --est SZ161226

# 份额表（10 行，除今天那行的数量外应与生产端逐位相同）
curl -s http://127.0.0.1:5000/history/shareshistory/SZ161226
```

单测：

* `auto/test_futures.py::SilverTransformTest`（5 条）—— 换算、缺汇率不猜 1.0、
  量级保险、"算法说明"只给这一族加算式；
* `web/tests/test_reference.py::CnFutureReferenceOrderTest`（4 条）—— 配对在前、
  没有汇率两行、整张表就是生产端那 4 行。
