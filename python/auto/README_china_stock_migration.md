# China Market Migration Notes

This note tracks the PHP-to-Python migration for the Chinese-market data path.

## Migrated

- `php/stockdataarray.php` -> `python/auto/stockdata.py`
  - Builds the fund estimation data structure from the legacy MySQL tables.
- `php/stock.php` fund calculation helpers -> `python/auto/fundcalc.py`
  - Covers calibrated QDII funds, holdings-based funds, and reverse estimation.
- `php/stock.php` fund CLI flow -> `python/auto/fundcli.py`
  - Provides a JSON CLI for estimates and database connectivity checks.
- `php/test/updatechinastock.php` -> `python/auto/chinastock.py`
  - Updates `stock` rows for `hs_a`, `hs_b`, `hs_s`, `etf_hq_fund`,
    `lof_hq_fund`, and `qbgg_hk`.
  - Updates `ahpair` rows for the `aplush` node.
  - Deletes stale `stock` rows only when they are not referenced by
    `stockgroupitem`, matching the PHP guard.
- `php/stock/chinamoney.php` -> `python/auto/chinamoney.py`
  - Updates `netvaluehistory` for `USCNY`, `EUCNY`, `JPCNY`, and `HKCNY`
    from the China Money `ccpr.json` feed.
  - Inserts missing rows, optionally overwrites existing rows, and reports
    missing currency stock IDs instead of writing invalid data.
- Shared HTTP/JSON handling -> `python/auto/httpjson.py`
  - User-Agent, UTF-8/GB18030 JSON decoding, and network error wrapping shared
    by `chinastock.py` and `chinamoney.py`.

There is also a Python symbol/Sina layer on the unmerged remote branch
`origin/devin/1780933111-migrate-php-core-to-python`:

- `php/stock/stocksymbol.php` -> `python/core/stock_symbol.py`
- `php/stock/stockref.php` and quote parsing -> `python/core/sina_fetcher.py`

That branch is not part of the current checkout, so the China updater keeps its
own small Sina `Market_Center` client and does not depend on it.

## Remaining

- `php/stock/szse.php`: Shenzhen LOF shares history.
- `php/stock/yahoostock.php`: Yahoo chart history and net-value backfill.
- `php/stock/stockref.php` / `php/stock/stockprefetch.php`: full realtime
  reference and batch-prefetch layer.
- `php/stock/mysqlref.php`, `php/stock/fundref.php`, and the remaining
  reference classes still have no Python counterparts.

## Commands

Use `python/auto/.env` or `PALMMICRO_DB_*` environment variables for database
access. Do not commit real credentials.

Check a Sina node without touching MySQL:

```powershell
python -B python\auto\chinastock.py hs_a --check
python -B python\auto\chinastock.py etf_hq_fund --peek 2 --pretty
```

Check the local row count with a read-only query:

```powershell
python -B python\auto\chinastock.py hs_a --check-db
```

Run the full node update:

```powershell
python -B python\auto\chinastock.py hs_a
python -B python\auto\chinastock.py aplush
```

Inspect the China Money feed without touching MySQL:

```powershell
python -B python\auto\chinamoney.py --check
python -B python\auto\chinamoney.py --peek
python -B python\auto\chinamoney.py --check-db
```

Update the four CNY central parity rates:

```powershell
python -B python\auto\chinamoney.py
python -B python\auto\chinamoney.py --dry-run
python -B python\auto\chinamoney.py --overwrite
python -B python\auto\chinamoney.py --force
```

`--overwrite` updates rows that already exist for the remote date and bypasses
the reference-date pre-check, so it can also fill partially missing rows.
`--force` bypasses the 09:15 and reference-date pre-checks for manual runs.

Run all migration tests:

```powershell
python -B -m unittest discover -s python\auto -p "test_*.py"
```

## Verification Status

- `python -B -m unittest discover -s python\auto -p "test_*.py"` covers the
  migrated updater logic and currently passes 62 tests in this checkout.
- The live China Money feed was verified with `--check` on 2026-09-28:
  `lastDate=2026-09-28 9:15`, 25 records, and all four mapped currencies.
- A real MySQL write test still requires replacing the placeholder
  `PALMMICRO_DB_*` values in `python/auto/.env`.
