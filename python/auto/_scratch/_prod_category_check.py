#!/usr/bin/env python
"""分类页的**生产端**漂移检测：17 个分类的号列表 vs 线上页面。

## 为什么需要这一层

本地 PHP 仓库是**旧快照**（``qdiicn.php`` 的规则数组 33 只，生产端 41 只），
所以 ``_php_category_compare.py`` 只能证明"与本地 PHP 一致"，证明不了
"与生产端一致"。这一批页面是纯列表页，行的多少完全由数组决定，
漂移就是**页面上少几只基金**，不会报错、也不会有别的症状。

发现漂移时的处置（与 qdii 的先例一致）：

* 判断是"本地仓库落后"还是"生产端刚加的"——看新符号是不是同一族；
* 落后的，Python 侧按生产端的**顺序**补上，并在 ``core/stocksymbol.py``
  或 ``CATEGORY_PAGES`` 里写明抓取日期与位置，像
  ``qdii_get_symbol_array()`` 那样留痕；
* **不**回头改 16 个分类共用的渲染逻辑 —— 数组是数据，不是逻辑。

## 跑法

::

    D:/python/python.exe -B auto/_scratch/_prod_category_check.py           # 只报告
    D:/python/python.exe -B auto/_scratch/_prod_category_check.py --fetch   # 强制重抓

抓取结果缓存在 ``_prod_category_cache/``，避免反复打扰对方站点。
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path


SCRATCH_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRATCH_DIR.parents[1]
AUTO_DIR = SCRATCH_DIR.parent
CACHE_DIR = SCRATCH_DIR / "_prod_category_cache"

for path in (str(PYTHON_DIR), str(AUTO_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

BASE_URL = "https://palmmicro.com/woody/res/"

#: 生产端参考数据表里每个符号都是一个新浪外链，链接**文本**就是代码。
#: 注意 href 与 ">" 之间还有 `target=_blank`，别写成紧邻。
SYMBOL_LINK = re.compile(
    r"finance\.sina\.com\.cn/fund/quotes/\d{6}/bc\.shtml\"[^>]*>((?:SH|SZ)\d{6})<"
)


def fetch_page(php_page: str, refresh: bool = False) -> str:
    """抓一个分类页（带磁盘缓存）。"""
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / (php_page + ".html")
    if cache.is_file() and not refresh:
        return cache.read_text(encoding="utf-8")
    request = urllib.request.Request(
        BASE_URL + php_page, headers={"User-Agent": "Mozilla/5.0"}
    )
    html = urllib.request.urlopen(request, timeout=30).read().decode("utf-8", "replace")
    cache.write_text(html, encoding="utf-8")
    return html


def main() -> int:
    refresh = "--fetch" in sys.argv

    from web.bootstrap import install_paths

    install_paths()
    from web.services.category_page_service import CATEGORY_PAGES, get_category_names

    drifted, missing, failed = [], [], []

    print("=" * 88)
    print("%-14s %-8s %-8s %s" % ("分类", "线上", "本地", "备注"))
    print("-" * 88)

    for name in get_category_names():
        definition = CATEGORY_PAGES[name]
        try:
            html = fetch_page(definition["php"], refresh=refresh)
        except Exception as exc:
            failed.append(name)
            print("%-14s %-8s %-8s 抓取失败：%s" % (name, "-", "-", exc))
            continue
        online = SYMBOL_LINK.findall(html)
        local = list(definition["symbols"]())

        if not online:
            failed.append(name)
            print("%-14s %-8s %-8s 页面里解析不到符号（页面结构变了？）" % (name, 0, len(local)))
            continue

        if online == local:
            print("%-14s %-8d %-8d 一致" % (name, len(online), len(local)))
            continue

        only_online = [s for s in online if s not in local]
        only_local = [s for s in local if s not in online]
        note = []
        if only_online:
            note.append("线上多：%s" % " ".join(only_online))
            missing.append(name)
        if only_local:
            note.append("本地多：%s" % " ".join(only_local))
        if not only_online and not only_local:
            note.append("集合相同但**顺序不同**（表行序会不一致）")
        drifted.append(name)
        print("%-14s %-8d %-8d %s" % (name, len(online), len(local), "；".join(note)))

        # 把线上顺序也存下来，补数组时照着它排，别自己猜位置
        (CACHE_DIR / (name + ".order.json")).write_text(
            json.dumps(online, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        time.sleep(0.5)

    print("-" * 88)
    print(
        "共 %d 个分类：一致 %d，漂移 %d（其中线上更多 %d），抓取/解析失败 %d"
        % (
            len(get_category_names()),
            len(get_category_names()) - len(drifted) - len(failed),
            len(drifted),
            len(missing),
            len(failed),
        )
    )
    if drifted:
        print("漂移分类：%s" % ", ".join(drifted))
    return 1 if (drifted or failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
