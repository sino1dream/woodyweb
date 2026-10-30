# submit 族对照用的真实外部样本

`_scratch/_php_submit_compare.py` 拿这个目录里的文件当输入，既跑 PHP 探针
（`_php_submit_check.php`）也跑 Python 解析，逐行比 `(date, 值)`。
**单测不读这里** —— 样本以常量形式写在 `auto/test_submitupdaters.py` 里，
所以离线跑测试不需要这些文件。

抓取日期 2026-10-30。抓取时用的是 `updater/etfdividend.USER_AGENT`
（各站点都按 UA 判断"是不是脚本"，裸 urllib 会 403）。

## 文件与来源

| 文件 | 来源 URL | 说明 |
|---|---|---|
| `spdr-dividend-xop.json` | `https://www.ssga.com/bin/v1/ssmp/fund/dividend-distribution?ticker=XOP&country=us&language=en&role=individual&product=etfs` | 80 条，含 `dividend: "-"` 的行（要跳过）与 `m/d/Y` 日期 |
| `invesco-nav-qqq.json` | `https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46090E103/prices?idType=cusip&variationType=priceListing&productType=ETF&productSubType=ETF` | 1 条 `715.178947 @ 2026-10-29`；**带 UA 是 200**，`usnavsource.py` 里"406"的结论已过时 |
| `navhist-us-en-spy.xlsx` | `https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/navhist-us-en-spy.xlsx` | 二进制，**被 .gitignore 排除**（278 KB），重抓见下 |
| `proshares-dividend-sample.json` | 无（手造） | Proshares 本机过不了 Cloudflare，这份是按 `submitdividend.php` docblock 里 TQQQ 那条（`2024-12-23T00:00:00` / `0.5616`）拼的 |

## 重抓

```sh
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'
cd python/auto/_scratch/_fixtures

curl -sSL -A "$UA" \
  'https://www.ssga.com/bin/v1/ssmp/fund/dividend-distribution?ticker=XOP&country=us&language=en&role=individual&product=etfs' \
  -o spdr-dividend-xop.json

curl -sSL -A "$UA" \
  'https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46090E103/prices?idType=cusip&variationType=priceListing&productType=ETF&productSubType=ETF' \
  -o invesco-nav-qqq.json

# xlsx 必须 -L：不带会拿到 301
curl -sSL -A "$UA" \
  'https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/navhist-us-en-spy.xlsx' \
  -o navhist-us-en-spy.xlsx
```

抓完复查一遍：

```sh
cd python && D:/python/python.exe -B auto/_scratch/_php_submit_compare.py
```

预期 `共 19 例：一致 19，失败 0`（分红/净值的行数与具体日期会随时间变，
看的是"两边一致"而不是具体数字）。
