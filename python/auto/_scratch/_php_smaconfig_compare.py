"""拿真实 PHP 对照 SMA 缓存文件的路径规则与 INI 格式。

用法::

    python -B _php_smaconfig_compare.py
    python -B _php_smaconfig_compare.py --selftest

对照两件事（都在**临时目录**里做，不碰工作区）：

1. **路径规则** —— PHP 的 ``DebugGetConfigFileName()`` 与 Python 的
   ``stockref.config_file_name()`` 是否拼出同一个字符串。
   这条看着简单，但符号里的 ``^ / . :`` 等字符要换成 ``_``，
   少换一个就变成"两个进程各写一份、互不相认"；
2. **文件格式** —— ``php/class/ini_file.php`` 的 ``INIFile`` 写出来的文件
   Python 能不能读，反过来 Python 写的 PHP 能不能读。

这类"能读但读错"的错最阴：路径错一个字符 = 缓存永不命中（只是慢，不报错）。

## ⚠️ ``root`` 的含义与 PHP 差一级

PHP 拼的是 ``$_SERVER['DOCUMENT_ROOT'].'/debug/config/<符号>.txt'``，
而 Python 的 ``stockref.config_file_name(symbol, root)`` 拼的是
``<root>/config/<符号>.txt`` —— 也就是说：

    Python 的 root  ==  PHP 的 DOCUMENT_ROOT.'/debug'

Python 这么定义是因为 ``debug`` 这一级在 ``stockprefetch.DEFAULT_QUOTE_DIRECTORY``
里已经算进去了（``debug/sina`` 整体当目录名）。所以对照时传给 PHP 的是
``base``，传给 Python 的是 ``base/debug``。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
# 脚本挪到 python/auto/_scratch/ 之后，sys.path[0] 只剩 _scratch，
# 所以要把 python/auto 与 python 两级目录显式补进来，才 import 得到 ref/、sql/ 这些子包。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


PHP_EXE = "D:/xampp/php/php.exe"

#: 与 ``_php_smaconfig_check.php`` 的 ``$arSymbols`` **必须逐项一致**。
SYMBOLS = ("^NDX", "hf_NQ", "SZ161130", "A/B", "QQQ", "fx_susdcny", "^GSPC", "hf_ES")

#: PHP 会读的那份文件（由 Python 先写好）。
PYTHON_FILE_NAME = "from_python.txt"


def run(root: str) -> dict:
    """在 ``root`` 里跑一遍 PHP harness。"""
    _write_python_side(os.path.join(root, PYTHON_FILE_NAME))
    process = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", "_php_smaconfig_check.php", root],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        # 脚本名是相对的：**换目录跑就找不到**。按本文件所在目录固定下来。
        cwd=str(Path(__file__).resolve().parent),
    )
    if process.returncode != 0:
        sys.stderr.write(process.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP harness 失败，返回码 %d" % process.returncode)
    return json.loads(process.stdout.decode("utf-8"))


def _write_python_side(path: str) -> None:
    """Python 侧按自己的格式写一份，交给 PHP 去读。"""
    from sql.smaconfig import ConfigSmaFileStore

    store = ConfigSmaFileStore(path)
    store.set_var("SMA", "Date", "2026-10-09")
    store.set_var("SMA", "D5", "100.5")
    store.set_var("SMA", "DBOLLUP", "101.25")
    store.set_var("SMA", "WBOLLUP", "102.75")
    if not store.save_data():
        raise SystemExit("Python 侧写文件失败：%r" % (store.last_error,))


def compare(reference: dict) -> tuple:
    """比对，返回 ``(差异说明列表, 条数)``。"""
    from sql.smaconfig import ConfigSmaFileStore
    from ref.stockref import config_file_name

    problems = []
    root = reference["root"]

    # ① 路径规则。注意 root 差一级：Python 的 root 是 PHP 的 DOCUMENT_ROOT.'/debug'
    python_root = os.path.join(root, "debug")
    for item in reference["paths"]:
        python_path = config_file_name(item["symbol"], python_root).replace("\\", "/")
        if python_path != item["path"]:
            problems.append("路径 %s  php=%s python=%s"
                            % (item["symbol"], item["path"], python_path))

    # ② PHP 写的 → Python 读
    store = ConfigSmaFileStore(reference["php_written_file"])
    for name, expected in (("Date", "2026-10-09"), ("D5", "100.5"),
                           ("DBOLLUP", "101.25")):
        got = store.read_var("SMA", name)
        if got != expected:
            problems.append("PHP 写的 %s  php=%s python=%s" % (name, expected, got))

    # ③ Python 写的 → PHP 读
    for item in reference["read_from_python"]:
        if item["value"] is None:
            problems.append("PHP 读不到 Python 写的 %s" % item["name"])
            continue
        expected = {"Date": "2026-10-09", "D5": "100.5",
                    "DBOLLUP": "101.25", "WBOLLUP": "102.75"}[item["name"]]
        if item["value"] != expected:
            problems.append("PHP 读 Python 的 %s  php=%s 期望=%s"
                            % (item["name"], item["value"], expected))

    return problems, len(problems)


def selftest(reference: dict) -> int:
    """注入错误后必须能抓到 —— 对照脚本自己先证明自己是有效的。"""
    from sql import smaconfig
    from ref import stockref

    original = {"config_file_name": stockref.config_file_name}

    def run() -> int:
        problems, _ = compare(reference)
        return len(problems)

    baseline = run()
    if baseline:
        print("基线就有 %d 处差异，先修实现再谈自检" % baseline)
        _restore(stockref, original)
        return 1

    caught = 0

    # ① 忘了把符号转小写 —— 大小写不一致时两边各写一份
    stockref.config_file_name = lambda symbol, root="debug": (
        os.path.join(root, "config", str(symbol) + ".txt"))
    smaconfig.config_file_name = stockref.config_file_name
    if run() > 0:
        caught += 1
    _restore(stockref, original)
    smaconfig.config_file_name = original["config_file_name"]

    # ② 忘了换掉不安全字符（'^' 之类）—— 路径里留下非法字符
    stockref.config_file_name = lambda symbol, root="debug": (
        os.path.join(root, "config", str(symbol).lower() + ".txt"))
    smaconfig.config_file_name = stockref.config_file_name
    if run() > 0:
        caught += 1
    _restore(stockref, original)
    smaconfig.config_file_name = original["config_file_name"]

    # ③ 段名读成小写 —— PHP 写的是 ``[SMA]``，读成 ``[sma]`` 就一条都取不到。
    #    注意要 patch ``_parse`` 而不是 ``_serialize``：``_serialize`` 只在
    #    Python 写文件时用，而那时 PHP harness 早就跑完了，改它验不到东西。
    original_parse = smaconfig._parse

    def lower_sections(text):
        return {key.lower(): value for key, value in original_parse(text).items()}

    smaconfig._parse = lower_sections
    if run() > 0:
        caught += 1
    smaconfig._parse = original_parse

    print("=== 自检 ===")
    print("  注入 3 类错误，抓到 %d 类" % caught)
    return 0 if caught == 3 else 1


def _restore(module, original: dict) -> None:
    for name, value in original.items():
        setattr(module, name, value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true",
                        help="注入错误，验证对照本身抓得到")
    parser.add_argument("--reference", help="已有的 PHP 输出（JSON）")
    args = parser.parse_args()

    temp_root = None
    if args.reference:
        with open(args.reference, "r", encoding="utf-8") as handle:
            reference = json.load(handle)
    else:
        # 临时目录**必须活到比对结束** —— 比对要读 PHP 写在那里的文件，
        # 早删一秒就会得到"全读不到"的假差异。
        temp_root = tempfile.mkdtemp(prefix="smacfg-php-")
        reference = run(temp_root)

    try:
        if args.selftest:
            return selftest(reference)

        problems, _ = compare(reference)
        print("对照例数：%d" % (len(reference["paths"]) + 3 + 4))
        print("意外差异：%d" % len(problems))
        for text in problems:
            print("  " + text)
        return 1 if problems else 0
    finally:
        if temp_root:
            shutil.rmtree(temp_root, True)


if __name__ == "__main__":
    sys.exit(main())
