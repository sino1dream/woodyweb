"""净值 / 仓位走势图 —— 对应 ``php/dateimagefile.php`` + ``php/imagefile.php``。

## 数据口径忠实照抄 PHP

``DateImageFile::Draw($arFocus, $arBase)``（``dateimagefile.php:23``）的两条线
来自 ``PageCsvFile::ReadColumn()``（``csvfile.php:122``，本版不迁 CSV，
由服务层直接收集等价数据）：

* **基准线 base** = 净值列，**所有**候选拐点行（没算出仓位的行也在里面）；
* **仓位线 focus** = 仓位列，只含**算出仓位**那些行（两列的行被
  ``count($arWord) > $iColumn`` 判掉），所以是稀疏的断线；
* 图上四项文字标注（开始 / 结束日期、最大 / 最小值）全部取自 **focus**；
* ``fMaxY``/``fMinY`` 的取法是**双尺度**：base 线按 ``max(base)`` 归一，
  focus 线按 ``min/max(focus)`` 再夹 0 归一（``dateimagefile.php:28,43-48``）。
  两条线画在同一区域但用不同尺度 —— 照抄，并在图上用左右两个 Y 轴刻度标明，
  免得看起来像"两条线不对数"。

## 视觉上是刻意重做的（known deviation）

PHP 用 GD 画黑底图（``imagecreatetruecolor`` 默认黑、白字、simkai 15pt、
640×360）。本版按浅色主题重做，记录在此：

1. 白底 + 深灰文字 + 浅灰网格（IDE 浅色主题，黑底图在页里太突兀）；
2. 2x 超采样后 LANCZOS 缩放到 800×420（PHP 是 640×360），消锯齿；
3. PHP 的 9 条**竖**等分虚线（``DrawAxisY(i*iCount/10)``）保留"10 等分"的
   数量语义，改成竖的浅网格线 + 底部日期刻度，横方向另加 5 条带刻度的网格；
4. 两条线各配一个 Y 轴刻度（左=净值、右=仓位），数据点加圆点标记；
5. 红线仍是仓位、绿线仍是净值（PHP ``_set_line_color`` 红 /
   ``_set_compare_color`` 绿），换成现代一点的色值；
6. **四项标注画在图内**：PHP 把它们作为 HTML 文本摆在图片旁边
   （``GetAll()`` 返回"标注 + 名字 + 符号 + 图片链接"，
   ``dateimagefile.php:76-83``；``fundpositioncn.php:58`` 整段拼进页面）。
   本版排进图内顶部的专用横条（两列、只占 margin），``<img>`` 自包含 ——
   页面上不必再维护一段要与图对齐的文字。**内容口径不变**（见
   :meth:`NetValuePositionChart.text_lines`）。

## 取不到字体怎么办

PHP 的 ``DebugGetFontName('simkai')`` 指向站点字体目录；本版按
Windows / macOS / Linux 的常见路径依次找 ``simkai``，都找不到时退回
Pillow 默认字体（中文会变方块，但图照样出 —— 不因为字体让整页 500）。
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont  # type: ignore[import]

#: ``php/debug.php:11`` 的 ``MIN_FLOAT_VAL``。
MIN_FLOAT_VAL = 0.0000005

#: PHP ``PageImageFile`` 的画布比例：高 = 宽 × 360 / 640（imagefile.php:159-160）。
DEFAULT_WIDTH = 640
DEFAULT_RATIO = 360.0 / 640.0

#: 浅色主题配色（视觉重做，见模块 docstring 第 2 节）。
COLOR_BACKGROUND = "#ffffff"
COLOR_TEXT = "#333333"
COLOR_MUTED = "#888888"
COLOR_GRID = "#e6e6e6"
COLOR_GRID_STRONG = "#c8c8c8"
COLOR_NETVALUE = "#2e7d32"     # 基准线（PHP compare 绿的现代色值）
COLOR_POSITION = "#d32f2f"     # 仓位线（PHP line 红的现代色值）
COLOR_REFERENCE = "#b0b0b0"    # 仓位轴 0 / 1 参考线（PHP dashed）

#: simkai 字体探测顺序（PHP 用站点自带的 simkai.ttf）。
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\simkai.ttf",
    "/System/Library/Fonts/Supplemental/Kaiti.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def _load_font(size: int) -> Any:
    """按 :data:`FONT_CANDIDATES` 找 simkai，失败退回 Pillow 默认字体。"""
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def number_format(value: Any, precision: int = 2) -> str:
    """PHP ``number_format($fVal, 2)``：两位小数、保留尾零（不削 ``1`` → ``1.00``）。

    与 :func:`sql.historytable.php_number_format` 同一口径 —— 仓位那几格
    要拿字符串跟登记仓位比，尾零不能削。千分位同样不实现：
    本图的入参（仓位、QDII 净值）都远小于 1000。
    """
    try:
        return "%.*f" % (precision, float(value))
    except (TypeError, ValueError):
        return str(value)


def to_pairs(points: Sequence[Any]) -> List[Tuple[str, float]]:
    """把 ``(date, value)`` 序列规范成 ``[(str(date), float), ...]``。

    调用方给的是服务层收集的列表（可能是 JSON 反序列化出来的 list），
    这里统一成内部结构；空值行**不允许**出现（focus 的稀疏性由服务层保证）。
    """
    pairs: List[Tuple[str, float]] = []
    for point in points:
        date, value = point[0], point[1]
        try:
            pairs.append((str(date), float(value)))
        except (TypeError, ValueError):
            continue
    return pairs


def reverse_pairs(points: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """照抄 PHP 的 ``array_reverse($ar)``：写入顺序是 date DESC，画线要正序。

    顺序是语义的一部分：PHP 在 ``Draw()`` 里对 base 与 focus **各** reverse 一次
    （``dateimagefile.php:32,37``），正序后才好按索引连线。
    """
    return list(reversed(list(points)))


class NetValuePositionChart:
    """净值 + 仓位双尺度折线图（``DateImageFile`` + ``PageImageFile`` 的合并）。

    几何计算与文字标注是纯函数部分（可单测）；PIL 绘制集中在
    :meth:`render_png`。四个标注项与 ``_textDateVal`` 家族逐字对应。
    """

    def __init__(self,
                 base: Sequence[Any],
                 focus: Sequence[Any],
                 symbol: str = "",
                 name: str = "仓位") -> None:
        #: 基准线（净值）：写入顺序 DESC，所有候选拐点行。
        self.base = to_pairs(base)
        #: 仓位线：写入顺序 DESC，只含算出仓位的行。
        self.focus = to_pairs(focus)
        self.symbol = symbol
        #: PHP ``GetAll($strName, $strCompare)`` 的 $strName = 仓位列名。
        self.name = name
        #: ``PageImageFile`` 的边界（PHP 里 ``$fMaxX`` 无默认值，Draw 里先赋值）。
        self.max_x: Optional[float] = None
        self.min_x: float = 0.0
        self.max_y: Optional[float] = None
        self.min_y: float = 0.0

    # -- 几何（照抄 PageImageFile） ---------------------------------------

    def _get_pos(self, value: float, f_max: Optional[float],
                 f_min: float) -> float:
        """``_getPos()``：归一化到 [0,1]，``|max-min| <= MIN_FLOAT_VAL`` 时给 0。"""
        if f_max is None:
            return 0.0
        diff = f_max - f_min
        if abs(diff) > MIN_FLOAT_VAL:
            return (value - f_min) / diff
        return 0.0

    def get_pos_x(self, index: float, width: int) -> int:
        """``GetPosX()``：``intval(宽 * (i - 0) / (count - 0))``。

        注意 ``fMinX`` 恒 0、``fMaxX`` = base 行数，所以最后一个点落在
        ``(n-1)/n`` 处、不到右边框 —— 照抄，不"修正"成 (n-1) 归一。
        """
        return int(width * self._get_pos(index, self.max_x, self.min_x))

    def get_pos_y(self, value: float, height: int) -> int:
        """``GetPosY()``：``intval(高 * (1 - (v - min) / (max - min)))``。

        调用前必须先定好 ``max_y``/``min_y`` —— PHP 的两段尺度切换就在这里：
        base 线用 ``max(base)``，focus 线用 ``min/max(focus)`` 夹 0。
        """
        return int(height * (1.0 - self._get_pos(value, self.max_y, self.min_y)))

    # -- 尺度与标注（照抄 DateImageFile::Draw） ---------------------------

    def setup_base_scale(self) -> None:
        """``Draw()`` 开头：``fMaxX = count(base); fMaxY = max(base)``。"""
        self.max_x = float(len(self.base))
        if self.base:
            self.max_y = max(value for _, value in self.base)
        else:
            self.max_y = 0.0

    def setup_focus_scale(self) -> None:
        """``Draw()`` 中段：``fMaxY = max(focus)``（负则 0）、``fMinY = min(focus)``（正则 0）。

        夹 0 的意图是让仓位轴的 0 与 1 两条参考线（``DrawAxisX()`` ×3）
        有機會落在区间内 —— PHP 接着调用 ``DrawAxisX(0.0)``、
        ``DrawAxisX(1.0)``、``DrawAxisX(-1.0)``，条件都是
        ``fMaxY > fVal && fMinY < fVal``。
        """
        if not self.focus:
            return
        self.max_y = max(value for _, value in self.focus)
        if self.max_y < 0.0:
            self.max_y = 0.0
        self.min_y = min(value for _, value in self.focus)
        if self.min_y > 0.0:
            self.min_y = 0.0

    def reference_values(self) -> List[float]:
        """``DrawAxisX()`` 三次调用的实参：0.0、1.0、-1.0，按条件过滤。

        **focus 为空时一条都不给** —— PHP 的 ``Draw()`` 在
        ``count($arFocus) < 2`` 时提前 ``return true``（只画了基准线），
        根本走不到 ``DrawAxisX``（``dateimagefile.php:35``）。

        另注意 PHP 的判据是**严格**不等号（``fMaxY > $fVal && $fMinY < $fVal``），
        而仓位恒正 → ``fMinY`` 被夹成 0.0 → 0 与 -1 都画不出来；
        仓位不超过 1 → 1 也画不出来。所以真实数据下这三条参考线
        **一条都不出现**，这是照抄结果，不是漏画。
        """
        if not self.focus or self.max_y is None:
            return []
        return [f for f in (0.0, 1.0, -1.0)
                if self.max_y > f and self.min_y < f]

    def text_lines(self) -> List[Tuple[str, str]]:
        """四项标注：开始 / 结束日期、最大 / 最小值，全部取自 **focus**。

        对应 ``_textCurDateVal``（``key``+``current``，首元素 / ``end`` 后末元素）
        与 ``_textSearchDateVal``（``max`` + ``array_search`` 找**第一个**匹配的
        日期）。``array_search`` 返回首个匹配键，所以并列最大值取最旧那天 ——
        Python 用同一策略（顺序取第一个）。

        **一处刻意偏离**：PHP 的"最小值"标注用的是夹 0 之后的 ``$fMinY``
        （``dateimagefile.php:46-47``），而仓位恒为正 → ``fMinY`` 永远是 0.0、
        ``array_search(0.0)`` 找不到日期，页面实际显示成"最小值： 0.00"。
        夹 0 的本意只是让 ``DrawAxisX()`` 的 0/1 参考线有机会落进区间
        （:meth:`reference_values` 照抄这个条件），不该连累文字标注。
        本版标注一律用**真实**最小/最大值。
        """
        if not self.focus:
            return []
        pairs = reverse_pairs(self.focus)
        first_date, first_value = pairs[0]
        last_date, last_value = pairs[-1]
        max_value = max(value for _, value in pairs)
        min_value = min(value for _, value in pairs)
        max_date = next(date for date, value in pairs if value == max_value)
        min_date = next(date for date, value in pairs if value == min_value)
        return [
            ("开始日期", "%s %s" % (first_date, number_format(first_value))),
            ("结束日期", "%s %s" % (last_date, number_format(last_value))),
            ("最大值", "%s %s" % (max_date, number_format(max_value))),
            ("最小值", "%s %s" % (min_date, number_format(min_value))),
        ]

    def can_draw(self) -> bool:
        """``Draw()`` 的第一道闸门：``count($arBase) < 2`` 直接不画。"""
        return len(self.base) >= 2

    # -- 渲染 --------------------------------------------------------------

    def render_png(self, width: int = 800, height: Optional[int] = None) -> Optional[bytes]:
        """画 PNG（bytes）。``base`` 少于两点返回 ``None``（PHP 返回 false）。"""
        if not self.can_draw():
            return None
        if height is None:
            #: PHP ``intval($iWidth * 360 / DEFAULT_WIDTH)`` 的宽高比。
            height = int(width * DEFAULT_RATIO)
        scale = 2  #: 2x 超采样消锯齿（视觉重做，见 docstring）。
        big = (width * scale, height * scale)
        image = Image.new("RGB", big, COLOR_BACKGROUND)
        draw = ImageDraw.Draw(image)
        font = _load_font(13 * scale)
        font_small = _load_font(11 * scale)

        margin_left, margin_right = 64 * scale, 64 * scale
        margin_top, margin_bottom = 70 * scale, 40 * scale
        plot_w = big[0] - margin_left - margin_right
        plot_h = big[1] - margin_top - margin_bottom
        if plot_w <= 0 or plot_h <= 0:
            return None

        # 尺度切换顺序照抄 PHP：base 尺度（max_x + max(base)）→ focus 尺度
        # （min/max(focus) 夹 0）→ 用完再切回。③④⑤ 各自调用前重设，
        # 因为 ``max_y`` 是两条线共用的一个字段（PHP 就这么复用的）。
        self.setup_base_scale()
        self.setup_focus_scale()

        base = reverse_pairs(self.base)
        focus = reverse_pairs(self.focus)

        def px(index: float) -> int:
            #: 几何走 :meth:`get_pos_x` 同一份实现，只是把"整幅宽"换成绘图区宽。
            return margin_left + self.get_pos_x(index, plot_w)

        def py(value: float, f_max: float, f_min: float) -> int:
            #: 显式传尺度，不读 ``self.max_y`` —— 那个字段是两条线共用的，
            #: 随时会被 ``setup_*_scale`` 改掉（PHP 也是同一个坑）。
            return margin_top + int(plot_h * (1.0 - self._get_pos(value, f_max, f_min)))

        # ① 竖网格：PHP 的 9 条等分虚线（i*iCount/10），改成浅网格线
        for i in range(1, 10):
            x = px(i * len(base) / 10.0)
            draw.line([(x, margin_top), (x, margin_top + plot_h)],
                      fill=COLOR_GRID, width=scale)

        # ② 横网格 + 左右 Y 轴刻度（5 条；视觉重做，PHP 只有虚线没有刻度值）
        base_min = min(value for _, value in base)
        base_max = max(value for _, value in base)
        for i in range(5):
            frac = i / 4.0
            y = margin_top + int(plot_h * (1.0 - frac))
            draw.line([(margin_left, y), (margin_left + plot_w, y)],
                      fill=COLOR_GRID if i else COLOR_GRID_STRONG, width=scale)
            net_label = base_min + (base_max - base_min) * frac
            draw.text((margin_left - 8 * scale, y), number_format(net_label, 2),
                      font=font_small, fill=COLOR_MUTED, anchor="rs")
            if self.focus:
                pos_label = self.min_y + (self.max_y - self.min_y) * frac
                draw.text((margin_left + plot_w + 8 * scale, y),
                          number_format(pos_label, 2), font=font_small,
                          fill=COLOR_MUTED, anchor="ls")

        # ③ 仓位轴参考线 0 / 1（PHP DrawAxisX(0.0)/(1.0)/(-1.0)）
        for ref in self.reference_values():
            y = py(ref, self.max_y, self.min_y)
            _dashed_line(draw, margin_left, y, margin_left + plot_w, y,
                         COLOR_REFERENCE, scale)

        # ④ 基准线（净值，绿）—— 尺度切回 max(base)
        self.setup_base_scale()
        net_points = [(px(i), py(value, self.max_y, 0.0))
                      for i, (_, value) in enumerate(base)]
        draw.line(net_points, fill=COLOR_NETVALUE, width=2 * scale, joint="curve")
        for x, y in net_points:
            draw.ellipse([x - 3 * scale, y - 3 * scale, x + 3 * scale, y + 3 * scale],
                         outline=COLOR_NETVALUE, fill=COLOR_BACKGROUND,
                         width=scale)

        # ⑤ 仓位线（红，稀疏）—— 尺度再切到 focus 的 min/max
        if focus:
            self.setup_focus_scale()
            pos_points: List[Tuple[int, int]] = []
            for i, (date, _) in enumerate(base):
                for f_date, f_value in focus:
                    if f_date == date:
                        pos_points.append((px(i), py(f_value, self.max_y, self.min_y)))
                        break
            if len(pos_points) > 1:
                draw.line(pos_points, fill=COLOR_POSITION, width=2 * scale,
                          joint="curve")
            for x, y in pos_points:
                draw.ellipse([x - 3 * scale, y - 3 * scale, x + 3 * scale, y + 3 * scale],
                             fill=COLOR_POSITION)

        # ⑥ X 轴日期刻度（最多 5 个，避免重叠）
        for date, index in _sparse_labels([date for date, _ in base], 5):
            draw.text((px(index), margin_top + plot_h + 8 * scale), date,
                      font=font_small, fill=COLOR_MUTED, anchor="ma")

        # ⑦ 标题与信息块（PHP GetAll：name + compare + 四项标注）
        #: PHP 把这四项标注画在图片**外面**（``GetAll()`` 返回 HTML 文本 +
        #: 图片链接，``dateimagefile.php:76-83``）。本版把它们排进图内顶部
        #: 的专用横条，``<img>`` 因此自包含（页面上不用再维护一段对应文字）。
        #: 两列排布 + 只占 margin，不侵占绘图区 —— 一列四行会溢出到净值线
        #: 起点上（实测踩过：第 3、4 行正好压在绘图区顶边上）。
        title = ("%s %s" % (self.name, self.symbol)).strip()
        draw.text((margin_left, 12 * scale), title, font=font,
                  fill=COLOR_TEXT, anchor="la")
        lines = self.text_lines()
        if lines:
            half = (len(lines) + 1) // 2
            for column, group in enumerate((lines[:half], lines[half:])):
                x = margin_left + column * (plot_w // 2)
                for row, (label, value) in enumerate(group):
                    draw.text((x, (32 + row * 16) * scale), "%s：%s" % (label, value),
                              font=font_small, fill=COLOR_MUTED, anchor="la")

        # ⑧ 图例（右上）。没有 focus 时不摆「仓位」项 —— 图上既然没有红线，
        # 摆个空图例反而让人以为线被裁掉了（PHP 的 GetAll 恒定输出两项，
        # 但那是 HTML 文本，看不到"图上有没有这条线"的对照）。
        legend_items = [(COLOR_NETVALUE, "净值")]
        if focus:
            legend_items.append((COLOR_POSITION, "仓位"))
        _legend(draw, margin_left + plot_w, 16 * scale, font_small, scale,
                *legend_items)

        image = image.resize((width, height), Image.LANCZOS)
        return _png_bytes(image)


def _dashed_line(draw: Any, x1: int, y1: int, x2: int, y2: int,
                 color: str, scale: int) -> None:
    """照抄 ``imagesetstyle`` 的 6 像素周期：2 实 4 虚（imagefile.php:115）。

    Pillow 没有等价的样式线，按像素下标逐点着色。线段很短（图宽 ≤ 800），
    逐点画的代价可以忽略。
    """
    dx, dy = x2 - x1, y2 - y1
    steps = max(abs(dx), abs(dy))
    if steps == 0:
        return
    for i in range(steps + 1):
        if i % 6 >= 2:
            continue
        draw.point((x1 + round(dx * i / steps), y1 + round(dy * i / steps)),
                   fill=color)


def _sparse_labels(dates: Sequence[str], limit: int) -> List[Tuple[str, int]]:
    """从日期序列里挑最多 ``limit`` 个等距标签（含首尾）。"""
    count = len(dates)
    if count == 0:
        return []
    if count <= limit:
        step = 1
    else:
        step = (count - 1) / float(limit - 1)
    picked: List[Tuple[str, int]] = []
    for i in range(limit):
        index = min(count - 1, int(round(i * step)))
        picked.append((dates[index], index))
    return picked


def _legend(draw: Any, right: int, top: int, font: Any, scale: int,
            *items: Tuple[str, str]) -> None:
    """右上角图例：色块 + 文字。"""
    y = top
    for color, label in items:
        draw.rectangle([right - 150 * scale, y, right - 138 * scale, y + 12 * scale],
                       fill=color)
        draw.text((right - 132 * scale, y + 6 * scale), label, font=font,
                  fill=COLOR_TEXT, anchor="lm")
        y += 20 * scale


def _png_bytes(image: Image.Image) -> bytes:
    """PNG 编码成 bytes（HTTP 响应用；PHP 是 ``imagejpeg`` 落盘再指 URL）。"""
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def draw_netvalue_position_chart(base: Sequence[Any],
                                 focus: Sequence[Any],
                                 symbol: str = "",
                                 name: str = "仓位",
                                 width: int = 800) -> Optional[bytes]:
    """入口：给 base / focus 两个序列，返回 PNG bytes。

    ``base`` 少于两点返回 ``None`` —— 页面上那 ``<img>`` 因此不出现，
    与 PHP ``if ($jpg->Draw(...))``（``fundpositioncn.php:58``）一致。
    """
    chart = NetValuePositionChart(base, focus, symbol=symbol, name=name)
    return chart.render_png(width=width)
