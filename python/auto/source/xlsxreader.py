"""最小 xlsx 读取器：只够读 SPDR 那类"一张表 + 表头 + 数字列"的文件。

## 为什么不用 openpyxl

本站点的运行环境是 Python 3.7 + ``pymysql`` + ``flask``（见
``web/requirements.txt``），**没有** ``openpyxl`` / ``pandas``。为了一个
净值 XLS 引入一个新三方依赖不划算 —— 而且 xlsx 本质是"zip 里装几张 XML"，
读它需要的代码不超过一百行。

PHP 那边用的是 ``PHPExcel_IOFactory``（``woody/res/php/_spdrnetvaluexls.php:5``），
换来的是"什么格式都能开"（xls/xlsx/csv，iShares 的老式 .xls 也算）。本模块
**只认 xlsx**，代价写在 :func:`read_rows` 的 docstring 里。

## 支持的子集

Excel / LibreOffice / openpyxl 写出来的这两类单元格：

===========================  ==========================================
``<c t="s"><v>26</v></c>``   共享字符串：``v`` 是 ``xl/sharedStrings.xml`` 的下标
``<c><v>769.5</v></c>``      数字（``t="n"`` 或不写 ``t``）
===========================  ==========================================

另外顺手支持 ``t="str"``（公式缓存值，当字符串）与 ``t="inlineStr"``
（``<is><t>…</t></is>``，不用共享表的那种）。

**不支持**：日期序列号（``t="d"`` 或带日期样式的数字）。SPDR 的日期列是
**共享字符串** ``"29-Oct-2026"``（实测，见 ``updater/etfnav.py``），所以不需要
把 Excel 的 1900 起点换算成日历日期 —— 那套换算还带着 1900 闰年 bug，
能不碰就不碰。
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

#: 主命名空间。xlsx 的 XML 全部带这个前缀（``<worksheet xmlns="…/main">``），
#: ElementTree 展开后标签名会变成 ``{ns}row``，所以要按它拼。
_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

#: ``A5`` / ``AB12`` → 列号（0 基）。只要字母部分。
_CELL_REF = re.compile(r"^([A-Z]+)")


class XlsxError(Exception):
    """文件不是 xlsx、或者结构超出了本模块支持的子集。"""


def read_rows(data: bytes, sheet_index: int = 0) -> List[List[Any]]:
    """把第 ``sheet_index`` 张表读成"行 → 单元格"的二维列表。

    行下标 = Excel 的 ``row r`` 属性 − 1（**0 基，第 1 行是 0**），缺的单元格
    补 ``None``，所以 ``rows[4][1]`` 就是 ``B5``。

    **空行也占位**：XML 里跳过的行号（如第 3 行整行空白）会在列表里留一个
    空列表。这一点是刻意的 —— PHP 那边是
    ``for ($row = 1; $row <= $highestRow; $row++)`` 逐行 ``rangeToArray()``，
    行号就是 Excel 行号；不占位的话，调用方按"第 4 行是表头"取的逻辑会在
    任何一份少几行的表上错位（SPDR 的 SPY 文件第 3 行就是空的）。

    只支持 **xlsx**（zip 容器）。拿到老式 ``.xls``（OLE 复合文档，
    ``D0 CF 11 E0`` 开头）会抛 :class:`XlsxError` —— 那是 PHPExcel 能读、
    我们不读的格式，见模块 docstring。
    """
    if not data:
        raise XlsxError("空内容")
    if not data.startswith(b"PK"):
        raise XlsxError("不是 xlsx（zip）容器，可能是老式 .xls 或 HTML 错误页")

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise XlsxError("zip 解不开：%s" % error)

    shared = _read_shared_strings(archive)
    sheet_path = _sheet_path(archive, sheet_index)
    return _read_sheet(archive, sheet_path, shared)


def read_first_sheet(data: bytes) -> List[List[Any]]:
    """``read_rows(data, 0)`` 的简写（PHP ``getSheet(0)``）。"""
    return read_rows(data, 0)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _read_shared_strings(archive: zipfile.ZipFile) -> List[str]:
    """``xl/sharedStrings.xml`` → 字符串列表（下标即 ``<c t="s"><v>N</v>``）。

    一个 ``<si>`` 里可能有多个 ``<t>``（富文本被切成几段 run），要拼起来 ——
    只取第一个会丢掉后半截，症状是名称列只剩前几个字。
    """
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    strings: List[str] = []
    root = ElementTree.fromstring(raw)
    for item in root.findall(_MAIN_NS + "si"):
        strings.append("".join(node.text or "" for node in item.iter(_MAIN_NS + "t")))
    return strings


def _sheet_path(archive: zipfile.ZipFile, sheet_index: int) -> str:
    """按 ``workbook.xml`` 里的顺序找第 N 张表的 XML 路径。

    **不能直接拼 ``xl/worksheets/sheet{n}.xml``**：那个数字是"创建顺序"，
    不是标签页顺序，删过标签页的工作簿里两者会错位。PHPExcel 也是按
    ``workbook.xml`` + rels 解析的。
    """
    try:
        raw = archive.read("xl/workbook.xml")
    except KeyError:
        raise XlsxError("缺少 xl/workbook.xml")
    root = ElementTree.fromstring(raw)
    sheets = root.find(_MAIN_NS + "sheets")
    items = list(sheets) if sheets is not None else []
    if not items:
        raise XlsxError("workbook.xml 里没有 sheets")
    if sheet_index >= len(items):
        raise XlsxError("只有 %d 张表，取不到第 %d 张" % (len(items), sheet_index))

    # rels 的 Id → Target（形如 worksheets/sheet1.xml）
    targets = _read_rels(archive)
    rel_id = items[sheet_index].get(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    )
    target = targets.get(rel_id)
    if not target:
        raise XlsxError("找不到第 %d 张表的 rels 目标" % sheet_index)
    if target.startswith("/"):
        return target.lstrip("/")
    return "xl/" + target


def _read_rels(archive: zipfile.ZipFile) -> Dict[str, str]:
    """``xl/_rels/workbook.xml.rels`` → ``{rId: Target}``。"""
    try:
        raw = archive.read("xl/_rels/workbook.xml.rels")
    except KeyError:
        return {}
    root = ElementTree.fromstring(raw)
    return {
        node.get("Id"): node.get("Target")
        for node in root
        if node.get("Id") and node.get("Target")
    }


def _read_sheet(archive: zipfile.ZipFile, path: str, shared: List[str]) -> List[List[Any]]:
    """逐 ``<row>`` 解析。按 ``r`` 属性占位，缺的列补 ``None``，所以行等长、下标可直取。"""
    try:
        raw = archive.read(path)
    except KeyError:
        raise XlsxError("缺少 %s" % path)

    rows: List[List[Any]] = []
    for _, node in ElementTree.iterparse(io.BytesIO(raw), events=("end",)):
        if node.tag != _MAIN_NS + "row":
            continue
        values: List[Any] = []
        for cell in node.findall(_MAIN_NS + "c"):
            index = _column_index(cell.get("r"))
            if index < 0:
                index = len(values)
            while len(values) <= index:
                values.append(None)
            values[index] = _cell_value(cell, shared)
        row_index = _column_row_index(node.get("r"), len(rows))
        while len(rows) < row_index:
            rows.append([])
        rows.append(values)
        node.clear()
    return rows


def _column_row_index(reference: Optional[str], fallback: int) -> int:
    """``"AB12"`` → 11（0 基行号）。没有 ``r`` 属性时接在已有行后面。"""
    if not reference:
        return fallback
    digits = reference.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    try:
        return int(digits) - 1
    except ValueError:
        return fallback


def _column_index(reference: Optional[str]) -> int:
    """``"AB12"`` → 27（0 基）。没有 ``r`` 属性时返回 -1，由调用方按顺序补。"""
    if not reference:
        return -1
    match = _CELL_REF.match(reference)
    if not match:
        return -1
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _cell_value(cell: Any, shared: List[str]) -> Any:
    """取单元格的值：共享字符串查表、数字转 ``float``、其它原样。"""
    kind = cell.get("t")
    if kind == "inlineStr":
        node = cell.find(_MAIN_NS + "is")
        if node is None:
            return None
        return "".join(text.text or "" for text in node.iter(_MAIN_NS + "t"))

    value = cell.find(_MAIN_NS + "v")
    if value is None or value.text is None:
        return None
    text = value.text

    if kind == "s":
        try:
            return shared[int(text)]
        except (ValueError, IndexError):
            return None
    if kind in ("str", "e"):
        #: ``str`` 是公式缓存值；``e`` 是错误值（``#N/A``），当文本报给调用方看
        return text
    if kind == "b":
        return text == "1"
    try:
        return float(text)
    except ValueError:
        return text
