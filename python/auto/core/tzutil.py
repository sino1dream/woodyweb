"""时区获取：优先用 ``zoneinfo``，在旧解释器上回退到纯标准库实现。

为什么需要这个模块
------------------
项目当前的运行环境是 Python 3.7（Flask 依赖装在那里），而 ``zoneinfo`` 是
Python 3.9 才进入标准库的。行情解析必须做「上海时间 -> 纽约时间」这类换算，
不能因为解释器版本就静默算错，所以这里给出两级方案：

1. 有 ``zoneinfo``（3.9+，或已安装 ``backports.zoneinfo``）时直接用真实时区库；
2. 否则回退到 ``_OffsetTimeZone``：用固定偏移 + 美东/欧洲夏令时规则近似。

回退实现的精度与边界
--------------------
* ``Asia/Shanghai``、``Asia/Tokyo``、``Asia/Kolkata`` 没有夏令时，固定偏移是精确的；
* ``America/New_York`` 用「3 月第二个周日 ~ 11 月第一个周日」的 DST 规则，
  ``Europe/Paris`` / ``Europe/Berlin`` 用「3 月最后周日 ~ 10 月最后周日」的规则。
  历史上美国规则变更、以及个别年份的例外不在覆盖范围内，但行情只关心近期日期。
* 固定偏移的 ``tzinfo`` 无法按日期自行切换夏令时，因此取时区请统一用
  :func:`localize`，不要直接对时区对象调用 ``replace``。

注意文件名：不能叫 ``timezone.py``，否则会遮蔽标准库同名模块。
"""

from __future__ import annotations

import datetime as _datetime

# 各时区的候选偏移（小时）：第一个是夏令时偏移（如果有），最后一个是标准偏移
_ZONE_OFFSETS = {
    "Asia/Shanghai": (8,),
    "Asia/Tokyo": (9,),
    "Asia/Kolkata": (5.5,),
    "America/New_York": (-4, -5),
    "Europe/Paris": (2, 1),
    "Europe/Berlin": (2, 1),
}

_DST_ZONES = ("America/New_York", "Europe/Paris", "Europe/Berlin")


def _first_weekday_on_or_after(year: int, month: int, weekday: int) -> _datetime.date:
    """返回某年某月第一个指定星期几的日期（weekday：周一 0 ... 周日 6）。"""
    first = _datetime.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + _datetime.timedelta(days=offset)


def _last_weekday_on_or_before(year: int, month: int, weekday: int) -> _datetime.date:
    """返回某年某月最后一个指定星期几的日期。"""
    day = 31
    while True:
        try:
            candidate = _datetime.date(year, month, day)
        except ValueError:
            day -= 1
            continue
        if candidate.weekday() == weekday:
            return candidate
        day -= 1


def _is_dst(naive: _datetime.datetime, time_zone_name: str) -> bool:
    """按规则判断某个「当地墙上时间」是否处于夏令时。"""
    moment = naive.date()
    if time_zone_name == "America/New_York":
        # 3 月第二个周日 02:00 开始，11 月第一个周日 02:00 结束
        start = _first_weekday_on_or_after(naive.year, 3, 6) + _datetime.timedelta(days=7)
        end = _first_weekday_on_or_after(naive.year, 11, 6)
        return start <= moment < end
    if time_zone_name in ("Europe/Paris", "Europe/Berlin"):
        start = _last_weekday_on_or_before(naive.year, 3, 6)
        end = _last_weekday_on_or_before(naive.year, 10, 6)
        return start <= moment < end
    return False


class _LocalOffsetTimeZone(_datetime.tzinfo):
    """纯标准库的近似时区：按传入时刻的日期决定标准/夏令时偏移。

    为什么不能用 ``datetime.timezone(timedelta(...))`` 那种纯固定偏移对象？
    因为 ``astimezone()`` 会完全采信目标时区给出的 ``utcoffset()``，一个恒定
    偏移会在美东冬季把 21:00+08:00 换算成 09:00 而不是正确的 08:00。
    所以这里让 ``utcoffset()`` 依据 ``dt`` 自己的年月日（在 ``astimezone`` 内部
    传入的是 UTC 值，取其日期判断 DST 在边界之外的日期上与当地日期一致）来选偏移。
    """

    def __init__(self, name: str):
        self.name = name
        self._offsets = _ZONE_OFFSETS.get(name, (0,))

    def _offset_for(self, dt):
        offsets = self._offsets
        if dt is not None and self.name in _DST_ZONES and _is_dst(dt, self.name):
            return _datetime.timedelta(hours=offsets[0])
        return _datetime.timedelta(hours=offsets[-1])

    def utcoffset(self, dt):
        return self._offset_for(dt)

    def dst(self, dt):
        if dt is not None and self.name in _DST_ZONES and _is_dst(dt, self.name):
            # 夏令时的增量 = 夏令时偏移 - 标准偏移，单位小时
            return _datetime.timedelta(hours=self._offsets[0] - self._offsets[-1])
        return _datetime.timedelta(0)

    def tzname(self, dt):
        return self.name

    def fromutc(self, dt):
        return (dt + self._offset_for(dt)).replace(tzinfo=self)

    def __repr__(self):
        return "_LocalOffsetTimeZone(%r)" % (self.name,)


def get_time_zone(time_zone_name: str):
    """取得时区对象；优先 ``zoneinfo``，不可用时回退到近似实现。

    回退实现会按日期自行切换夏令时，因此可以直接用于 ``astimezone()`` 的目标。
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(time_zone_name)
    except ImportError:
        pass
    except Exception:
        # zoneinfo 存在但缺少 tzdata 时也会失败，同样回退
        pass

    return _LocalOffsetTimeZone(time_zone_name)


def uses_zoneinfo() -> bool:
    """当前解释器是否能使用 ``zoneinfo``。"""
    try:
        import zoneinfo  # noqa: F401

        return True
    except ImportError:
        return False


def localize(naive: _datetime.datetime, time_zone_name: str):
    """把「当地墙上时间」挂上正确的时区。

    必须走这里而不是 ``naive.replace(tzinfo=get_time_zone(...))``：直接
    ``replace`` 时 Python 只把 tzinfo 挂上去，不会按日期选偏移，在美东/欧洲的
    夏令时期间会得到错误的偏移。
    """
    try:
        from zoneinfo import ZoneInfo

        return naive.replace(tzinfo=ZoneInfo(time_zone_name))
    except ImportError:
        pass
    except Exception:
        pass

    return naive.replace(tzinfo=get_time_zone(time_zone_name))
