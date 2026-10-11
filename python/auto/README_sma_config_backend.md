# SMA 缓存的文件后端（`smaconfig.py`）—— 单独评审

对应 `php/stockhis.php:365 _configSMA()` 里的那个 `INIFile`。
第二刀（`README_stockhistory_migration.md` 第八节）把配置换成了可替换的
`ConfigSmaStore`，**默认纯内存**；这一份是"要不要真的写文件"的设计说明，
因为那件事的三条代价都不小。

结论先摆在前面：

| 问题 | 结论 |
|---|---|
| 路径从哪来 | **不用等 `MyStockReference`**（它只贡献一个字符串），但 `root` 必须显式给；拿不到就退回内存 |
| 并发写会不会互相覆盖 | 会**丢更新**，但**无害**（内容是纯函数）；真正要解决的是"读到半个文件"，用原子替换 |
| 测试怎么不污染工作区 | 默认内存 + 测试用临时目录 + **绝不拿 CWD 当 root** |

**默认仍然是内存** —— 要启用得显式设 `PALMMICRO_SMA_CACHE_DIR`。
这是本版最重要的性质：不开开关就一个字节都不写盘。

---

## 一、路径从哪来

### PHP 的链条

```php
// php/stockhis.php:367
$cfg = new INIFile($this->stock_ref->strConfigName);

// php/stock/stockref.php:38
$this->strConfigName = DebugGetConfigFileName($this->GetSymbol());

// php/debug.php:258 → 240 → 207 → 133
function DebugGetConfigFileName($strSymbol)
{
    return DebugGetSymbolFile('config', $strSymbol);
}
function DebugGetSymbolFile($strSection, $strSymbol)
{
    $strPath = DebugGetPath($strSection);        // UrlGetRootDir().'debug' .'/'. $strSection
    $str = strtolower($strSymbol);
    $str = str_replace(array('/', '+', ',', '^', '.', ':', '%'), '_', $str);
    return "$strPath/$str.txt";
}
```

最终是 `<DOCUMENT_ROOT>/debug/config/<小写且替换过的符号>.txt`。

### 看着依赖 `MyStockReference`，其实不依赖

`MyStockReference` 未迁，但这一段**只用到它的 `strConfigName` 一个属性**，
而那个属性是 `GetSymbol()` 的**纯函数** —— 没有数据库、没有 I/O、没有状态。
Python 侧 `stockref.StockReference.config_name` 早就算好了同一条规则，
所以这里根本不需要等 `MyStockReference` 迁完。

本版把 `sina_quote_file_name()` 泛化成 `symbol_file_name(symbol, root, section)`，
`config_file_name()` / `sina_quote_file_name()` 都只是按 section 分派 ——
因为 PHP 那三个包装器（`DebugGetSinaFileName` / `DebugGetYahooFileName` /
`DebugGetConfigFileName`）拼的本来就是同一条规则。

### 真正缺的是 `root`

`$_SERVER['DOCUMENT_ROOT']` 在常驻进程里根本不存在。所以 `root` 变成显式输入，
优先级：

1. 直接给 `path`（脚本、测试）；
2. 给 `root`，按 PHP 的规则拼 `<root>/config/<符号>.txt`；
3. 读环境变量 `PALMMICRO_SMA_CACHE_DIR`；
4. **都没有 → 退回内存实现，一个字节都不写。**

第 4 条是**默认值**，理由很具体：Flask 进程的 CWD 不确定，拿它当 root 就是
"渲染页面在工作区里乱写文件"。宁可永远用内存，也不猜一个目录出来写。

`test_smaconfig.NoWorkspacePollutionTest` 把 CWD 切到临时目录再建
`StockHistory`，断言事后目录是空的 —— 谁哪天把默认值改成 CWD，那条立刻炸。

---

## 二、并发写同一份配置会不会互相覆盖

### PHP 本身就没有保护

```php
// php/class/ini_file.php:90
function save_data()
{
    $fp = fopen($this->INI_FILE_NAME,'w');    // ← 'w' 先截断，没有 flock
    ...
}
```

`'w'` 是**先截断再写**，全程无锁。PHP 一个请求一个进程，撞上的概率低；
PHP-FPM 下多进程是真会撞，只是撞了也不太疼（见下）。

### 两种坏结果，只有一种值得治

| | 后果 | 本版 |
|---|---|---|
| **读到半个文件** | 写一半被别人读到，解析出垃圾 | **治**：原子替换 |
| **丢更新** | 两个线程都算完都写，后写的赢 | **不治**：无害 |

**原子替换**：写同目录下的临时文件（必须同目录 —— `os.replace` 只在同一个
文件系统里是原子的，跨卷会退化成"复制+删除"），再 `os.replace()` 盖上去。
读者只会看到"旧完整文件"或"新完整文件"，永远看不到半个。

`ConcurrencyTest.test_write_is_atomic_replace` 在 `os.replace` 上装了个 spy，
在替换**发生之前**读一次磁盘，断言那时仍是完整的旧文件。

**丢更新为什么不用加锁**：这份缓存的内容是 `(symbol, start_date)` 的
**纯函数** —— 两个线程算同一个 key 得到的是同样的字节。丢更新的唯一后果是
"下次多算一遍"，**写错数据是不可能的**。为这个上文件锁（还要处理
`fcntl` / `msvcrt` 两套 API 的跨平台包袱）买不到任何正确性。

### 写失败不能让页面 500，但也不能静默

Windows 上 `os.replace` 可能因为"目标被别的进程打开"而抛 `OSError`。
此时清掉临时文件、记进 `last_error`、**让页面继续** —— 缓存是可选的。
本项目吃过静默吞异常的亏，所以错误一律留痕（有测试钉）。

---

## 三、测试怎么不污染工作区

三道闸门，逐条有测试：

1. **默认就是内存** —— 不传 `path`/`root`、环境变量也没设时，
   `config_sma_store()` 返回内存实现；
2. **文件后端必须显式给路径** —— 测试一律 `tempfile.mkdtemp()` +
   `addCleanup(shutil.rmtree)`；
3. **不自动拿 CWD 当 root** —— 这条最关键（理由见第一节）。

另外 `stockhistory.py` 被 `ModuleIsImportCleanTest` 钉着**不许出现 `open(`**，
所以落盘必须单独成模块 —— 这不是洁癖，而是让"渲染层永不落盘"这件事
可以被一条 import 检查证明，而不是靠人记得。

---

## 四、文件格式：与 PHP 的 `INIFile` 互通

`[段名]\n键=值\n`。**保留未知段**并原样写回 —— PHP 的 `save_data()` 也是
写全部段（`read_groups()` 拿全部），丢掉别人写的段不是我们的权利。

两处刻意**不照抄** PHP：

* `save_data()` 用 `for(reset($g); $key=key($g); next($g))` 遍历，
  **碰到 falsy 键名就停**。文件若在第一个 `[段]` 之前有内容，那个空段名会让
  循环立刻结束 → **写出一个空文件**。本版不复制这个 bug；
* `parse_data()` 用 `explode('=', $line)` 只取 `[1]`，值里带 `=` 会被截断。
  本版原样保留第一个 `=` 之后的全部内容。

两条都不影响互通（SMA 值都是数字），但**别指望靠 Python 这边去修 PHP 那边的文件**。

---

## 五、一处新增的偏离：拿不到起始日期时**不写**缓存

这一条是做真库验证时才发现的，不在原先的三个问题里，但它直接决定
"开了开关之后每个请求往磁盘写什么"。

`_calcStartDate()` 在没有可用历史时返回 `None`（PHP 是 `false`）。此时 PHP
**照样写**：`Date=`（空）+ 一串 0；而下次请求 `'' == ''` 判定为**命中**，
于是把那张全是 0 的表当缓存读回去。

演示库里 `^NDX` 正好一行历史都没有，一开开关就中招 —— 实测每渲染一次页面
就落一份 `[SMA]\nDate=None\nD5=0.0\n…`。本版在这个分支直接跳过缓存：

```python
current = self.start_date
if not current:
    self._get_ema(50)          # 后面两步照做，PHP 也是无条件做
    self._get_ema(200)
    return
```

没有日期就没有 key，写下去的东西既不能命中也没有意义。
`ConfigSmaTest.test_no_start_date_means_no_cache_is_written` 钉住。

---

## 六、接线

```
web/app.py  →  sma_service_factory（默认 default_sma_service_factory）
                    └─ SmaService(config_factory=smaconfig.file_store_factory())
                            └─ _build_history() 把存放处交给 StockHistory(config=…)
```

`file_store_factory()` 返回的工厂**读不到目录时恒给内存实现**，于是
"开不开"只在一个地方判断。环境变量在**每次构造**时才读，改了不用重启进程，
测试也能在同一进程里来回切（`SmaConfigBackendTest.test_the_switch_is_read_per_request_not_at_import_time`）。

注入了自定义 `history_factory` 时服务**不再插手** config —— 那种情况下构造
方式归调用方管（测试就是这么绕开真实 `StockHistory` 的）。

---

## 七、待评审（需要你拍板的）

1. **默认内存是否可接受？** 代价是缓存**跨进程**失效：每个 Flask worker
   各算各的，页面首次渲染的 CPU 开销回到 PHP 的水平。要真缓存得设环境变量
   并接受落盘。
2. **要不要干脆换成进程内 LRU？** 比文件简单得多（无路径、无并发、
   无污染问题），也能跨请求复用。文件后端的唯一优势是**多进程共享**和
   **与 PHP 产出的文件互通**（迁移期两边并行时，Python 能读 PHP 写的缓存）。
   如果 PHP 那套届时已经下线，文件后端就没必要存在了 —— 这一条值得先确认。
3. **缓存目录的归属**：现在走环境变量，没有接进 `web/config.py`。
   要接的话建议加一项 `SMA_CACHE_DIR`，读不到就 `None`（=内存）。

---

## 八、验证

| 项 | 结果 |
|---|---|
| `auto/test_smaconfig.py` | 26 例 |
| `web/tests/test_sma_route.py::SmaConfigBackendTest` | 7 例 |
| PHP 逐项对照 `_php_smaconfig_compare.py` | 15 例，**0 意外差异**；`--selftest` 抓到 3/3 注入错误 |
| 全量 | `auto` **877**（3.7 / 3.13 双绿）、`web` **294** |
| 真库 | 开开关渲染 `/fund/SZ161130`、`/fund/SH513100` 均 200，落盘路径正确；关开关时工作区零新增 |
