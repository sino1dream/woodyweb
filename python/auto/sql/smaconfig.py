"""SMA 缓存的**文件**后端 —— 对应 ``php/stockhis.php:365 _configSMA()``。

数值核心 ``stockhistory.py`` 有一条硬约束：**模块里不许出现 ``open(``**
（``test_stockhistory.ModuleIsImportCleanTest`` 钉着），所以落盘的活只能单独
成模块。本模块不 import ``stockhistory``，两边靠鸭子类型对接。

## 一、路径从哪来（PHP 依赖 ``MyStockReference``，本版不需要）

.. code-block:: php

    $cfg = new INIFile($this->stock_ref->strConfigName);

    // php/stock/stockref.php:38
    $this->strConfigName = DebugGetConfigFileName($this->GetSymbol());

    // php/debug.php:258 → 240
    function DebugGetSymbolFile($strSection, $strSymbol)
    {
        $strPath = DebugGetPath($strSection);           // → UrlGetRootDir().'debug/config'
        $str = strtolower($strSymbol);
        $str = str_replace(array('/', '+', ',', '^', '.', ':', '%'), '_', $str);
        return "$strPath/$str.txt";
    }

看着依赖未迁的 ``MyStockReference``，其实**只用到 ``strConfigName`` 这一个
纯字符串属性**：它是 ``GetSymbol()`` 的纯函数。Python 侧
``StockReference.config_name`` 早就算好了同一条规则（见
``stockref.config_file_name``），所以这里根本不需要等 ``MyStockReference`` 迁完。

真正缺的是 **``root``**：PHP 用 ``$_SERVER['DOCUMENT_ROOT']``，常驻进程里没有
这个东西。所以本版把 root 变成显式输入，优先级：

1. 直接给 ``path``（测试、脚本）；
2. 给 ``root``，按 PHP 的规则拼出 ``<root>/config/<符号>.txt``；
3. 读环境变量 ``PALMMICRO_SMA_CACHE_DIR``；
4. **都没有 → 退回内存实现，一个字节都不写**。

第 4 条是默认值，理由是：Flask 进程的 CWD 不确定，拿它当 root 就是"渲染页面
在工作区里乱写文件"。要落盘必须有人显式说清楚写到哪。

## 二、并发写同一份文件会不会互相覆盖

**PHP 本身就没有保护**：``INIFile::save_data()`` 是 ``fopen($f, 'w')`` ——
先截断再写，没有 ``flock``。PHP 一个请求一个进程，撞上的概率低，
但 PHP-FPM 下多进程是真的会撞，只是撞了也不疼（见下）。

常驻进程（多线程/多 worker）暴露得更多，有两种坏结果：

1. **读到半个文件** —— 写一半被别人读到，解析出垃圾；
2. **丢更新** —— 两个线程都算完都写，后写的赢。

本版用**原子替换**解决第 1 条：写同目录下的临时文件，再 ``os.replace()``
盖上去。读者只会看到"旧完整文件"或"新完整文件"，永远看不到半个。

第 2 条**不需要加锁**，理由是这份缓存的内容是 ``(symbol, start_date)`` 的
**纯函数**：两个线程算同一个 key 得到的是同样的字节，丢更新的唯一后果是
"下次多算一遍"。**写错数据是不可能的**。为这个上文件锁（还要处理
``fcntl`` / ``msvcrt`` 两套 API）买不到任何正确性。

``os.replace()`` 在 Windows 上可能因为"目标被别的进程打开"而抛
``OSError``。此时清掉临时文件、记进 :attr:`last_error`、**让页面继续** ——
缓存是可选的，但错误不能静默吞掉（本项目为此吃过亏）。

## 三、测试怎么不污染工作区

三道闸门，逐条都有测试钉住：

1. **默认就是内存**，不写盘 —— 不传 ``path``/``root``、环境变量也没设时，
   :func:`config_sma_store` 返回内存实现；
2. **文件后端必须显式给路径** —— 测试一律 ``tempfile.mkdtemp()``，
   用完 ``shutil.rmtree``；
3. **不自动拿 CWD 当 root** —— 这条最关键：宁可永远用内存，
   也不猜一个目录出来写。

## 文件格式：与 PHP 的 ``INIFile`` 互通

``[段名]\\n键=值\\n``。保留**未知段**并原样写回 —— PHP 的 ``save_data()``
也是写全部段，丢掉别人写的段不是我们的权利。

两处刻意**不照抄** PHP 的 ``INIFile``：

* PHP 的 ``save_data()`` 用 ``for(reset($g); $k=key($g); next($g))`` 遍历，
  **碰到 falsy 的键名就停**。文件若在第一个 ``[段]`` 之前有内容，
  那个空段名会让循环立刻结束 → **写出一个空文件**。本版不复制这个 bug；
* PHP 读值用 ``explode('=', $line)`` 只取 ``[1]``，值里带 ``=`` 会被截断。
  本版原样保留第一个 ``=`` 之后的全部内容。SMA 值都是数字，两条都不影响互通。
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any, Dict

from ref.stockref import config_file_name

#: PHP 的 ``preg_match("/^\[([[:alnum:]]+)\]/", $data)`` —— 段名是**纯 ASCII
#: 字母数字**，且必须从行首开始。用 ``[A-Za-z0-9]`` 而不是 ``\w``，
#: 因为 PHP 的 ``[:alnum:]`` 不带下划线。
_SECTION_PATTERN = re.compile(r"^\[([A-Za-z0-9]+)\]")

#: 落盘根目录的环境变量。不设 → 退回内存实现。
ENV_CACHE_DIR = "PALMMICRO_SMA_CACHE_DIR"

#: 与 PHP ``SMA_SECTION`` 同名，但本模块是通用的，段名由调用方给。
DEFAULT_ENCODING = "utf-8"


class ConfigSmaFileStore:
    """INI 文件的读写实现，接口与 ``stockhistory.ConfigSmaStore`` 一致。

    对应 ``php/class/ini_file.php`` 的 ``INIFile``：``group_exists`` /
    ``read_var`` / ``set_var`` / ``save_data`` 四个调用点。

    ## 读：坏文件当"没有缓存"

    文件不存在、读不出、或内容不是合法 INI 时，一律当成空 ——
    后果只是"这次重算一遍"，而重算本来就是 PHP 的兜底路径。
    错误记在 :attr:`last_error` 里，不抛。
    """

    def __init__(self, path: str, encoding: str = DEFAULT_ENCODING) -> None:
        self.path = str(path)
        self.encoding = encoding
        #: 最近一次读/写的失败原因（``None`` 表示一切正常）。
        #: 缓存失败不是致命错误，**但必须看得见** —— 静默吞异常会掩盖真问题。
        self.last_error: Any = None
        self._groups: Dict[str, Dict[str, str]] = {}
        self._load()

    # -- 读 ---------------------------------------------------------------
    def has_section(self, section: str) -> bool:
        return section in self._groups

    def read_var(self, section: str, name: str) -> Any:
        """``read_var()``：没有返回 ``None``（PHP 是 ``false``）。"""
        return self._groups.get(section, {}).get(name)

    def set_var(self, section: str, name: str, value: Any) -> None:
        self._groups.setdefault(section, {})[name] = str(value)

    # -- 写 ---------------------------------------------------------------
    def save_data(self) -> bool:
        """``save_data()``：原子替换整个文件。返回是否写成功。

        失败时**不抛** —— 页面不该因为写不了缓存而 500。
        原因看 :attr:`last_error`。
        """
        try:
            _atomic_write(self.path, _serialize(self._groups), self.encoding)
        except OSError as exc:
            # Windows 上 os.replace 撞上"目标被占用"会走到这里。
            # 临时文件由 _atomic_write 自己清掉。
            self.last_error = exc
            return False
        self.last_error = None
        return True

    # -- 调试/测试 --------------------------------------------------------
    def snapshot(self) -> Dict[tuple, str]:
        """与内存实现同形状：``{(section, name): value}``。"""
        return {(section, name): value
                for section, values in self._groups.items()
                for name, value in values.items()}

    def sections(self) -> Dict[str, Dict[str, str]]:
        return {section: dict(values) for section, values in self._groups.items()}

    # -- PHP 原名 ---------------------------------------------------------
    def group_exists(self, section: str) -> bool:
        return self.has_section(section)

    GroupExists = group_exists
    ReadVar = read_var
    SetVar = set_var
    SaveData = save_data

    # -- 内部 -------------------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding=self.encoding) as handle:
                text = handle.read()
        except FileNotFoundError:
            return                      # 第一次算，正常
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self.last_error = exc      # 读不出来 = 没有缓存，不抛
            return
        self._groups = _parse(text)


# ---------------------------------------------------------------------------
# 工厂：决定"用文件还是用内存"
# ---------------------------------------------------------------------------
def config_sma_store(symbol: Any = None, root: Any = None, path: Any = None) -> Any:
    """按优先级给出配置存放处；**拿不到路径就退回内存**。

    ``path`` → ``root`` → ``PALMMICRO_SMA_CACHE_DIR`` → 内存。
    最后那一档是默认值，也是"测试不污染工作区"的第一道闸门。
    """
    if path:
        return ConfigSmaFileStore(path)
    root = root or os.getenv(ENV_CACHE_DIR)
    if root and symbol:
        return ConfigSmaFileStore(config_file_name(_symbol_of(symbol), root))
    return _memory_store()


def file_store_factory(root: Any = None) -> Any:
    """给 ``SmaService`` 用的工厂：返回一个 ``symbol -> store`` 的可调用。

    ``root`` 为空时取环境变量；环境变量也没有时，返回的工厂**恒给内存实现** ——
    于是"不开这个开关就绝不落盘"这件事只在一个地方判断。

    ## 环境变量在**每次构造**时才读

    不是建工厂时读一次。这样测试可以在同一个进程里 ``setenv`` / ``delenv``
    来回切，不用重建服务；生产上改了环境变量也不需要重启进程才生效
    （一次 ``os.getenv`` 的代价可以忽略）。
    """
    def build(symbol: Any) -> Any:
        resolved = root or os.getenv(ENV_CACHE_DIR)
        if not resolved or not symbol:
            return _memory_store()
        return ConfigSmaFileStore(config_file_name(_symbol_of(symbol), resolved))

    return build


def unlink_config_file(symbol: Any) -> bool:
    """对应 PHP ``unlinkConfigFile($strSymbol)``（``php/debug.php:263``）。

    PHP 那边是 ``unlinkEmptyFile(DebugGetConfigFileName($strSymbol))`` ——
    删掉这只标的的配置文件，让下一次计算重新写一份。拆股/分红改了复权价之后
    必须删：缓存里的 SMA 是按旧 ``adjclose`` 递推出来的，留着就与历史序列打架。

    **只删、不建目录、不报错**。没开 ``PALMMICRO_SMA_CACHE_DIR`` 时本来就没有
    文件；PHP 的 ``unlinkEmptyFile`` 对不存在的文件也是静默通过。
    返回 ``True`` 表示真的删掉了一个文件（测试与日志用得上）。
    """
    root = os.getenv(ENV_CACHE_DIR)
    if not root or not symbol:
        return False
    path = config_file_name(_symbol_of(symbol), root)
    if not os.path.exists(path):
        return False
    _remove(path)
    return True


def _memory_store() -> Any:
    """内存实现定义在 ``stockhistory`` 里（那边不许出现 I/O），这里**延迟导入**。

    延迟是为了让本模块可以脱离数值核心单独 import —— 测试文件后端
    不需要先跑通整条 ``StockHistory`` 链路。
    """
    from sql.stockhistory import ConfigSmaStore

    return ConfigSmaStore()


def _symbol_of(ref: Any) -> str:
    """``GetSymbol()``；传进来的也可能是字符串。"""
    if isinstance(ref, str):
        return ref
    for name in ("get_symbol", "GetSymbol"):
        method = getattr(ref, name, None)
        if callable(method):
            try:
                value = method()
            except Exception:
                value = None
            if value:
                return str(value)
    return ""


# ---------------------------------------------------------------------------
# INI 的解析与序列化（与 php/class/ini_file.php 互通）
# ---------------------------------------------------------------------------
def _parse(text: str) -> Dict[str, Dict[str, str]]:
    """``INIFile::parse()`` + ``parse_data()``。

    段头用 PHP 那条 ``^[[:alnum:]]+`` 的规则（**纯 ASCII 字母数字**）；
    段头之前的行归到空段名下 —— PHP 也是这样（``$CURRENT_GROUP`` 初值是 ``''``）。
    """
    groups: Dict[str, Dict[str, str]] = {}
    current = ""
    for line in text.split("\n"):
        header = _SECTION_PATTERN.match(line)
        if header is not None:
            current = header.group(1)
            groups.setdefault(current, {})
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        groups.setdefault(current, {})[key] = value
    return groups


def _serialize(groups: Dict[str, Dict[str, str]]) -> str:
    """``INIFile::save_data()`` 的输出：``[段]\\n键=值\\n``。"""
    blocks = []
    for section, values in groups.items():
        blocks.append("[%s]" % section)
        blocks.extend("%s=%s" % (key, value) for key, value in values.items())
    return "".join(block + "\n" for block in blocks)


def _atomic_write(path: str, text: str, encoding: str) -> None:
    """写临时文件再 ``os.replace`` —— 读者永远看不到半个文件。

    ## 为什么必须是**同目录**的临时文件

    ``os.replace`` 只在同一个文件系统里是原子的；跨卷会退化成"复制+删除"。
    所以临时文件建在目标文件旁边，而不是 ``tempfile.gettempdir()``。

    ## 为什么不用文件锁

    见模块 docstring 第二节：文件内容是 ``(symbol, start_date)`` 的纯函数，
    并发写的唯一后果是"多算一遍"，加锁买不到正确性，却要背
    ``fcntl`` / ``msvcrt`` 两套 API 的跨平台包袱。
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    stem = os.path.basename(path)
    handle_fd, temp_path = tempfile.mkstemp(dir=directory, prefix=stem + ".", suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
        # mkstemp 建出来是 0600，PHP 的 fopen('w') 是 0644。
        # 尽量对齐，不行（部分文件系统/平台）就算了 —— 权限不是正确性问题。
        try:
            os.chmod(temp_path, 0o644)
        except OSError:
            pass
        os.replace(temp_path, path)
    except BaseException:
        _remove(temp_path)
        raise


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
