# Fund Migration Utilities

This folder contains the Python migration path for the PHP fund estimation code.

## Local MySQL Setup

Do not commit or paste database passwords. Provide credentials through environment variables:

```powershell
$env:PALMMICRO_DB_HOST = "127.0.0.1"
$env:PALMMICRO_DB_PORT = "3306"
$env:PALMMICRO_DB_USER = "<your user>"
$env:PALMMICRO_DB_DATABASE = "<your database>"
$env:PALMMICRO_DB_PASSWORD = "<your password>"
```

`PALMMICRO_DB_HOST`, `PALMMICRO_DB_PORT`, `PALMMICRO_DB_USER`, and `PALMMICRO_DB_DATABASE` have defaults matching the legacy PHP configuration. `PALMMICRO_DB_PASSWORD` is required.

The legacy PHP host default is `mysql`, which is usually a deployment/container service name. On a local Windows machine, use `127.0.0.1`, `localhost`, or the actual remote MySQL host name instead.

If MySQL returns error `1045 Access denied`, the TCP connection already reached MySQL. Fix `PALMMICRO_DB_USER`, `PALMMICRO_DB_PASSWORD`, or grant that user permission to connect from `localhost`/`127.0.0.1`.

Alternatively, create `python\auto\.env` from `python\auto\.env.example`. The `.env` file is ignored by Git and should hold local secrets only.

Example `python\auto\.env`:

```dotenv
PALMMICRO_DB_HOST=127.0.0.1
PALMMICRO_DB_PORT=3306
PALMMICRO_DB_USER=<your user>
PALMMICRO_DB_DATABASE=<your database>
PALMMICRO_DB_PASSWORD=<your password>
```

The MySQL adapter uses PyMySQL when created from environment variables:

```powershell
python -m pip install PyMySQL
```

## CLI Examples

Generate migrated stock data and official estimates:

```powershell
python -B python\auto\cli/fundcli.py "SZ159518,SZ161125" --data --pretty
```

Estimate with realtime source values:

```powershell
python -B python\auto\cli/fundcli.py "SZ159518" --source XOP=140 --source CNY=7.1 --pretty
```

Reverse-estimate the peer value from a fund value:

```powershell
python -B python\auto\cli/fundcli.py "SZ159518" --reverse SZ159518=1.376 --reverse CNY=7.1 --pretty
```

## Tests

Run without writing bytecode cache:

```powershell
python -B -m unittest discover -s python\auto -p test_*.py
```

For the Chinese stock/ETF reference data migration, see `README_china_stock_migration.md`.
