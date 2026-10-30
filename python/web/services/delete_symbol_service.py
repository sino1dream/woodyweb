"""删标的的 Web 封装（``woody/res/deletesymbol.php``）。

分派逻辑全在 :mod:`deletesymbol`（``python/auto/deletesymbol.py``）—— 那一层
既给页面用，也给脚本/单测用，不依赖 Flask。这里只做三件 Web 才有的事：

1. 把 :func:`deletesymbol.plan` 的原始结果翻成页面要的形状（能不能删、按钮
   该不该禁用、提示语）；
2. 执行前把"删了就回不来"的具体后果写清楚（哪张表多少行、MyISAM 没事务，
   中途失败会留半删状态）；
3. 执行后给一句人话（:func:`deletesymbol.describe`）。

## 为什么这页比 PHP 多一个"预览"

PHP 是 ``<a href="...deletesymbol.php?symbol=X">`` 点下去直接删，
``confirm()`` 里只有一句"确认删除 XXX？"。本地改成 GET 预览 + POST 执行，
理由不只是"写操作不用 GET"：

* 五道闸门里有一道（``stockgroupitem``）在**绝大多数符号上都会命中** ——
  演示库里几乎每只标的都在某个分组里。PHP 被拦时静默跳回，管理员看到的是
  "什么都没发生"，只能猜；预览页直接把"被哪道闸门拦住"摆出来。
* ``calibrationhistory`` 那道闸门在 PHP 里是**带副作用的**（有就删、恒返回
  false），所以"被 stockgroupitem 拦下"时校准值已经没了。预览页先算清楚
  "会删哪些行、会不会被拦"，这个坑才看得见。
"""

from __future__ import annotations

from typing import Any, Dict

import deletesymbol

#: 预览页上"这一步会发生什么"的固定提示（与 PHP 的差异逐条列清）。
EFFECT_NOTES = (
    "PHP 的 deletesymbol.php 是 GET 副作用、点下去直接删、删完跳回什么都不显示；"
    "本版改成 GET 预览 + POST 执行。",
    "被闸门拦住时 PHP 静默跳回；本版把拦路的闸门全列出来（五道全查，不短路）。",
    "PHP 在闸门阶段就会删掉校准历史（_deleteHasCalibration 恒返回 false），"
    "本版等闸门全过再删 —— 被拦下时一行都不动。",
    "默认只删 PHP 删的那 6 张表；残留表要显式勾选才清（见页面下半部分）。",
    "MyISAM 没有事务：删到一半出错会留半删状态。要可回滚先跑 dbtools/backup_db.py。",
)


def preview(connection: Any, symbol: str) -> Dict[str, Any]:
    """只读：能不能删、删哪些、会留下什么。"""
    result = deletesymbol.plan(connection, symbol)
    result["can_delete"] = bool(result["found"]) and not result["blocked"]
    result["notes"] = list(EFFECT_NOTES)
    result["dirty_orphans"] = [item for item in result["orphans"] if item["rows"]]
    return result


def execute(connection: Any, symbol: str, *, include_orphans: bool = False) -> Dict[str, Any]:
    """真删，返回 ``{"ok", "message", "result"}``（与 ``submit_service`` 同一形状）。"""
    result = deletesymbol.apply(
        connection, symbol, include_orphans=include_orphans, commit=False
    )
    ok = bool(result["applied"])
    result["can_delete"] = bool(result["found"]) and not result["blocked"]
    result["notes"] = list(EFFECT_NOTES)
    result["dirty_orphans"] = [item for item in result["orphans"] if item["rows"]]
    return {"ok": ok, "message": deletesymbol.describe(result), "result": result}
