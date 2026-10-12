# `python/auto` 目录分档（2026-10-12）

## 1. 为什么要分档

分档前 `python/auto` 根目录有 **100 个 .py**：正式模块、命令行入口、
一次性排查脚本、PHP 对照脚本全部平铺在一起，找文件只能靠文件名猜。
现在按"这一层负责什么"分成 8 个包 + 1 个一次性脚本目录：

| 目录 | 放什么 | 文件数 | 依赖方向（只允许往右依赖） |
| --- | --- | --- | --- |
| `core/` | 符号体系、数据源门面、时区 | 3 | 依赖根，不依赖任何上层 |
| `sql/` | 数据库访问与持久化 | 6 | → `core`、`ref` |
| `source/` | 外部数据源（唯一发 HTTP 的层） | 9 | → `core` |
| `calc/` | 纯计算（不碰库不联网） | 6 | → `core`、`sql` |
| `ref/` | 标的引用对象（一组互相继承的对象模型） | 4 | → `core`、`calc`、`sql` |
| `updater/` | 定时更新任务（有时间/调度语义） | 7 | → `source`、`calc`、`sql`、`ref` |
| `ui/` | 表格与价格展示 | 2 | → `core` |
| `cli/` | 命令行入口（可执行脚本） | 8 | → 任意下层 |
| `_scratch/` | 一次性排查 / PHP 对照脚本 | 33 | 不参与打包与测试发现 |

根目录剩下的是 **测试**（`test_*.py`，28 个）与三个入口脚本
（`main.py` / `telegram.py` / `palmmicroapi.py`）。

每个包的 `__init__.py` 里都写了"为什么单独一档"，改归属前先看它。

## 2. 模块映射表（旧平铺路径 → 新路径）

历史 README 里写的是旧路径，按下表对照即可（路径形态的引用已批量更新，
散文里光秃秃的 ``stockref.py`` 没逐个改，靠这张表）。

| 旧 | 新 | 旧 | 新 |
| --- | --- | --- | --- |
| `stocksymbol.py` | `core/stocksymbol.py` | `refobj.py` | `ref/refobj.py` |
| `stockdata.py` | `core/stockdata.py` | `stockref.py` | `ref/stockref.py` |
| `tzutil.py` | `core/tzutil.py` | `refquote.py` | `ref/refquote.py` |
| `stocksql.py` | `sql/stocksql.py` | `externallink.py` | `ref/externallink.py` |
| `quotestore.py` | `sql/quotestore.py` | `uicolumn.py` | `ui/uicolumn.py` |
| `fundeststore.py` | `sql/fundeststore.py` | `pricepool.py` | `ui/pricepool.py` |
| `historytable.py` | `sql/historytable.py` | `fundcli.py` | `cli/fundcli.py` |
| `stockhistory.py` | `sql/stockhistory.py` | `fundestcli.py` | `cli/fundestcli.py` |
| `smaconfig.py` | `sql/smaconfig.py` | `futurescli.py` | `cli/futurescli.py` |
| `sinaquotes.py` | `source/sinaquotes.py` | `navcli.py` | `cli/navcli.py` |
| `chinamoney.py` | `source/chinamoney.py` | `forexcli.py` | `cli/forexcli.py` |
| `chinastock.py` | `source/chinastock.py` | `quotecli.py` | `cli/quotecli.py` |
| `forexsource.py` | `source/forexsource.py` | `quoteupdatercli.py` | `cli/quoteupdatercli.py` |
| `navsource.py` | `source/navsource.py` | `autocalibrationcli.py` | `cli/autocalibrationcli.py` |
| `futuresquotes.py` | `source/futuresquotes.py` | `quoteupdater.py` | `updater/quoteupdater.py` |
| `usnavsource.py` | `source/usnavsource.py` | `navupdater.py` | `updater/navupdater.py` |
| `httpjson.py` | `source/httpjson.py` | `forexupdater.py` | `updater/forexupdater.py` |
| `quotesource.py` | `source/quotesource.py` | `stockprefetch.py` | `updater/stockprefetch.py` |
| `fundcalc.py` | `calc/fundcalc.py` | `indexhistory.py` | `updater/indexhistory.py` |
| `fundestcalc.py` | `calc/fundestcalc.py` | `autocalibration.py` | `updater/autocalibration.py` |
| `futurescalib.py` | `calc/futurescalib.py` | `estcalibration.py` | `updater/estcalibration.py` |
| `futurepremium.py` | `calc/futurepremium.py` | `_debug_*` / `_probe_*` / `_php_*` / `_verify_*` | `_scratch/` |
| `pairestimate.py` | `calc/pairestimate.py` | | |
| `fundgroups.py` | `calc/fundgroups.py` | | |

## 3. import 写法

统一写成 **`from 包 import 模块`**，不要写 `import 包.模块`：

```python
from sql.historytable import TableRow      # 对
import sql.historytable                    # 错：只把顶层名 sql 绑进来，
                                           # 后面写 historytable.X 会 NameError
from core import stockdata as sd           # 别名用这种
```

这条不是风格洁癖：`import a.b` 的绑定语义与平铺时代的 `import b` 不同，
分档时踩过（7 个 CLI 脚本集体 `NameError: name 'smaconfig' is not defined`）。

## 4. 运行方式

```bat
cd F:\woody\woodyweb\python\auto
python -B -m unittest discover -s . -t . -p "test_*.py"     :: auto 全部测试

cd F:\woody\woodyweb\python
python -B -m unittest discover -s web/tests -t . -p "test_*.py"

python -B auto\cli\fundcli.py --help                         :: 命令行入口
python -B auto\_scratch\_php_numfmt_compare.py               :: PHP 对照脚本
```

`web` 侧的 `bootstrap.install_paths()` 会把 `python/auto` 与 `python`
加入 `sys.path`，所以 `web` 里的 `from sql.historytable import ...` 照常可用。

## 5. 已知取舍与坑

1. **`python/sql` 与 `auto/sql` 同名。** `python/sql` 是建表/种子/备份脚本
   （不是包，没有 `__init__.py`），`auto/sql` 是数据库访问包（有）。
   Python 的解析规则让**有 `__init__.py` 的那个胜出**，所以 `import sql.xxx`
   总能落到 `auto/sql`；但谁要是给 `python/sql` 也补一个 `__init__.py`，
   解析就会静默跑到错的目录去。`test_layout.py` 里的
   `SqlPackageShadowTest` 专门钉住这件事。**建议后续把 `python/sql`
   改名成 `python/dbtools`**，彻底消掉同名。
2. **测试文件留在根目录。** `unittest discover -s .` 的入口就是这里，
   且 `test_*.py` 需要 import 各子包，放在根目录最省事；搬进 `tests/`
   要额外处理 `sys.path` 与相互 import（`test_smaconfig.py` 会 import
   `test_stockhistory`），收益不大。
3. **`_scratch/` 故意不加 `__init__.py`。** 加了会被 `unittest discover`
   当包递归进去；不加又能避免它干扰包解析。代价是脚本运行时
   `sys.path[0]` 只有 `_scratch` 自己，所以里面的脚本都显式补了
   `python/auto` 与 `python` 两级路径（见各文件里那段注释）。
4. **所有 `__file__` 推导都多了一层。** 三类地方改过：
   `cli/*.py` 与 `updater/estcalibration.py` 的 `AUTO_DIR`、
   `core/stockdata.py` 里 `.env` 的默认路径（`core/../.env`）、
   `_scratch/_php_*_check.php` 里的 `__DIR__ . '/../../php'`（→ 三级）。
   以后再挪目录，这三类要一起改。
5. **`import` 改写是脚本化批量做的**（363 处，129 个文件），改完靠
   `test_layout.py` 的 AST 全量扫描兜底：任何一处回退到平铺写法都会变红。

## 6. 本次改动的验证记录

| 项目 | 结果 |
| --- | --- |
| `auto` 单元测试 | 885 → 893（新增 `test_layout.py` 8 个）全绿 |
| `web` 单元测试 | 414 全绿 |
| 12 个 PHP 对照脚本 | 全部跑通（`_php_port_compare` 的 1 项是既有已知差异） |
| `_php_rtrim0_fuzz` | 2016 个值，0 不一致 |
| 8 个 CLI `--help` | 全部可运行 |
| `python -m compileall python/auto` | 无语法错误 |

## 7. 没做的事

* `python/sql` 改名（见取舍 1）；
* 测试文件搬进 `tests/`（见取舍 2）；
* 历史 README 散文里的裸模块名（``quotestore.py`` 这种）未逐个改，按第 2 节映射表对照。
