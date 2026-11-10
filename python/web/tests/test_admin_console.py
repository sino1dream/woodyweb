"""后台控制台（``/admin`` 首页）的测试。

这一页收的是「生产端未展示」的功能项，分三类：

1. PHP 有页面但不进任何导航（16 项 ``edit*.php`` + 删标的 / 分组 / 交易 / 抓取）；
2. 生产端根本没有（符号管理、索引页、登出、状态页、JSON API）；
3. 生产端有、本地未迁（``uploadfile.php`` / ``debugcn.php``）。

钉的事：

1. 16 项 chip **从 ``STOCK_OPTIONS`` 生成**，不是另抄一份 —— 数量、key、
   会员级标记都跟着那份定义走；
2. 需要符号的链接带 ``__SYMBOL__`` 占位符 + ``data-symbol-href``（前端逐个改写，
   17 个链接共用一次输入）；「修改季报持仓」是静态索引链接，不带这两样；
3. 季报指向 ``/holdings/quarter`` 索引页、持仓指向专门页，都不给 ``<option>``
   链接（不在两处写入口）；
4. 未迁清单只有「结论 + 说明」，不给 href —— 不能点进去的地方不该长得像能点；
5. 计数拿不到（表不存在 / 连接断了）时页面仍然 200，只是卡片没有数字。
"""

import re
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.app import create_app  # noqa: E402
from web.services import admin_console  # noqa: E402
from web.services.stock_option_service import STOCK_OPTIONS  # noqa: E402


def fake_url_for(endpoint, **kwargs):
    """够用的 ``url_for`` 替身：``endpoint?k=v``，不做真正的地图校验。"""
    if not kwargs:
        return "/" + endpoint
    pairs = ",".join("%s=%s" % (key, value) for key, value in sorted(kwargs.items()))
    return "/%s?%s" % (endpoint, pairs)


def section_of(sections, key):
    for section in sections:
        if section["key"] == key:
            return section
    raise AssertionError("没有 %s 这一段" % key)


# ---------------------------------------------------------------------------
# 清单数据本体（不连库）
# ---------------------------------------------------------------------------

class BuildSectionsTest(unittest.TestCase):
    def setUp(self):
        self.sections = admin_console.build_sections(fake_url_for, is_admin=True)

    def test_four_sections_in_order(self):
        self.assertEqual(
            ["hand-url", "hand-url-pages", "local-only", "missing"],
            [section["key"] for section in self.sections])

    def test_sixteen_options_come_from_the_single_source(self):
        """16 项必须与 ``STOCK_OPTIONS`` 一一对应 —— 加第 17 项时不会漏。"""
        entries = section_of(self.sections, "hand-url")["entries"]
        self.assertEqual(len(STOCK_OPTIONS), len(entries))
        self.assertEqual([spec.submit for spec in STOCK_OPTIONS],
                         [entry["title"] for entry in entries])

    def test_option_hrefs_use_the_symbol_placeholder(self):
        """除季报索引页外，15 项都要符号。"""
        entries = section_of(self.sections, "hand-url")["entries"]
        for entry in entries:
            if entry["title"] == "修改季报持仓":
                continue
            self.assertIn(admin_console.SYMBOL_PLACEHOLDER, entry["href"],
                          entry["title"])
            self.assertTrue(entry["needs_symbol"], entry["title"])

    def test_report_points_at_the_index_and_holdings_at_its_own_page(self):
        """季报先进索引页（挑基金），持仓仍直接进专门页（要符号）。"""
        entries = {e["title"]: e for e in section_of(self.sections, "hand-url")["entries"]}
        quarter = entries["修改季报持仓"]
        self.assertIn("holdings.quarter_report_index", quarter["href"])
        self.assertNotIn(admin_console.SYMBOL_PLACEHOLDER, quarter["href"])
        self.assertFalse(quarter["needs_symbol"])
        self.assertIn("/holdings/quarter", quarter["note"])
        holdings = entries["修改持仓"]
        self.assertIn("holdings.exhaustive_holdings_page", holdings["href"])
        self.assertTrue(holdings["needs_symbol"])
        # 其余 14 项走通用编辑页
        self.assertIn("option=editcalibration", entries["手工校准值"]["href"])

    def test_member_level_option_is_marked(self):
        """16 项里只有「基金申购金额」不要求管理员（PHP 同款）。"""
        entries = {e["title"]: e for e in section_of(self.sections, "hand-url")["entries"]}
        self.assertEqual(admin_console.ACCESS_MEMBER, entries["基金申购金额"]["access"])
        self.assertEqual("登录即可", entries["基金申购金额"]["access_label"])
        self.assertEqual(admin_console.ACCESS_ADMIN, entries["手工校准值"]["access"])
        member_level = [e for e in entries.values()
                        if e["access"] == admin_console.ACCESS_MEMBER]
        self.assertEqual(1, len(member_level))

    def test_hand_url_pages_cover_delete_group_transaction_submit(self):
        entries = section_of(self.sections, "hand-url-pages")["entries"]
        php = " ".join(entry["php"] for entry in entries)
        for name in ("deletesymbol.php", "editstockgroupcn.php",
                     "editstocktransactioncn.php", "submithistory.php"):
            self.assertIn(name, php)
        # 只有"删标的"需要符号
        with_symbol = [e["title"] for e in entries if e["needs_symbol"]]
        self.assertEqual(["删标的"], with_symbol)

    def test_local_only_entries_all_have_links(self):
        for entry in section_of(self.sections, "local-only")["entries"]:
            self.assertTrue(entry["href"], entry["title"])
            # 生产端没有这一页，所以 php 一律是占位符
            self.assertEqual("（无）", entry["php"])

    def test_local_only_keeps_the_json_api_entry(self):
        entries = {e["title"]: e for e in section_of(self.sections, "local-only")["entries"]}
        self.assertIn("status.api_status", entries["JSON API"]["href"])
        self.assertIn("/api/fund/", entries["JSON API"]["note"])
        self.assertIn("/api/quotes/refresh", entries["JSON API"]["note"])

    def test_missing_entries_have_no_links(self):
        """未迁的两条不能点 —— 给个死链比不给更糟。"""
        entries = section_of(self.sections, "missing")["entries"]
        self.assertEqual(2, len(entries))
        for entry in entries:
            self.assertNotIn("href", entry)
            self.assertIn(entry["verdict"], ("缺口", "不迁"))

    def test_quick_links_are_three_cards(self):
        cards = admin_console.quick_links(fake_url_for)
        self.assertEqual(["符号管理", "分组管理", "抓取工具"],
                         [card["title"] for card in cards])
        for card in cards:
            self.assertTrue(card["href"])
            self.assertTrue(card["count_key"])

    def test_entry_counts_formats_or_skips(self):
        counts = admin_console.entry_counts(symbols=None, groups=4)
        self.assertNotIn("symbols", counts)       # 拿不到就不给这条
        self.assertEqual("4 个分组", counts["groups"])
        self.assertTrue(counts["tools"].endswith("个工具"))


# ---------------------------------------------------------------------------
# 路由（假的 count 数据源）
# ---------------------------------------------------------------------------

class CountCursor:
    def __init__(self, source):
        self.source = source
        self._rows = []
        self.closed = False

    def execute(self, sql, params=()):
        squeezed = " ".join(sql.split())
        match = re.match(r"SELECT COUNT\(\*\) AS n FROM `(\w+)`", squeezed)
        if match is None:
            raise AssertionError("控制台只会发 COUNT，收到：%s" % sql)
        rows = getattr(self.source, match.group(1), None)
        if rows is None:
            raise AssertionError("假数据源没有表 %s" % match.group(1))
        self._rows = [{"n": len(rows)}]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        self.closed = True


class ConsoleSource:
    """只够控制台用的数据源：两张表的行数 + 连接外观。"""

    def __init__(self, broken=False):
        self.stock = [{"id": i, "symbol": "S%d" % i} for i in range(1, 8)]
        self.stockgroup = [{"id": 1, "member_id": 1, "groupname": "演示分组A"},
                           {"id": 2, "member_id": 1, "groupname": "演示分组B"}]
        self.broken = broken

    @property
    def connection(self):
        return self

    def cursor(self):
        if self.broken:
            raise RuntimeError("连接断了")
        return CountCursor(self)

    def rollback(self):
        pass


class ConsoleRouteTest(unittest.TestCase):
    def _client(self, source):
        return create_app({"TESTING": True},
                          admin_source_factory=lambda: source).test_client()

    def test_console_renders_all_four_sections(self):
        html = self._client(ConsoleSource()).get("/admin").get_data(as_text=True)
        for title in ("生产端只能手敲 URL", "其它只能手敲 URL 的页面",
                      "生产端根本没有", "生产端有、本地未迁"):
            self.assertIn(title, html)

    def test_cards_show_live_counts(self):
        html = self._client(ConsoleSource()).get("/admin").get_data(as_text=True)
        self.assertIn("7 个符号", html)
        self.assertIn("2 个分组", html)

    def test_counts_missing_does_not_break_the_page(self):
        """库出问题时后台首页更得打得开 —— 数字是装饰，不能连坐。"""
        client = self._client(ConsoleSource(broken=True))
        response = client.get("/admin")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("后台 · 控制台", html)
        # 两张表数不出来 → 只有"抓取工具"那张卡还带数字（它不查库）
        self.assertEqual(["3 个工具"],
                         re.findall(r'console-card-count">([^<]+)<', html))

    def test_symbol_query_sets_the_picker_and_fallback(self):
        html = self._client(ConsoleSource()).get(
            "/admin?symbol=nf_AU0").get_data(as_text=True)
        self.assertIn('value="nf_AU0"', html)
        # 兜底索引链接由服务端生成，不依赖 JS
        self.assertIn('href="/stock/nf_AU0/edit"', html)

    def test_default_symbol_is_qqq(self):
        html = self._client(ConsoleSource()).get("/admin").get_data(as_text=True)
        self.assertIn('value="QQQ"', html)

    def test_chips_carry_the_placeholder_for_the_frontend_rewrite(self):
        html = self._client(ConsoleSource()).get("/admin").get_data(as_text=True)
        chips = re.findall(r'<a class="option-chip" href="([^"]+)"', html)
        self.assertEqual(len(STOCK_OPTIONS), len(chips))
        # 季报那一项是索引页，静态链接；其余 15 项要符号。
        with_placeholder = [href for href in chips
                            if admin_console.SYMBOL_PLACEHOLDER in href]
        self.assertEqual(len(STOCK_OPTIONS) - 1, len(with_placeholder))
        self.assertIn('href="/holdings/quarter"', html)
        # 表格里另有两条也要跟着符号走（删标的 / 可编辑项索引）。
        # 数"属性对"而不是只数 data-symbol-link：后者在脚本的
        # querySelectorAll 里还会再出现一次。
        self.assertEqual(len(STOCK_OPTIONS) + 1,
                         html.count("data-symbol-link data-symbol-href"))

    def test_admin_identity_is_shown(self):
        html = self._client(ConsoleSource()).get("/admin").get_data(as_text=True)
        self.assertIn("当前身份：管理员", html)


if __name__ == "__main__":
    unittest.main()
