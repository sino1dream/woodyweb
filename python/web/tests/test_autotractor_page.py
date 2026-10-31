"""拖拉机自动化说明页的测试（``woody/res/autotractorcn.php`` → ``/autotractor``）。

**全部离线**：整页没有一个数字，也没有数据库/行情依赖，测试只守三件事：

1. **块的顺序**（``build_page()["blocks"]``）—— PHP ``EchoAll()`` 里 echo 的
   先后就是它，顺序错了页面读起来会跳；
2. **关键文案与非死链** —— 版本号由 ``YINHE_AU3_VER`` 拼出来（两处）、
   三个外链与两个 ``.au3`` 下载链接的 href/显示名；
3. **两条刻意偏离** —— 不出现 ``<font color>``（换成 CSS 类）、
   不出现 ``<img``（PHP 那两张截图本版不渲染）。

"与 PHP 逐段一致"不在这里断言 —— 那要对真 PHP 跑，见
``auto/_scratch/_php_autotractor_compare.py``（13 段/26 条，见
``README_autotractor_migration.md``）。
"""

import re
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.app import create_app  # noqa: E402
from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services.autotractor_service import (  # noqa: E402
    AUTO_TRACTOR_DISPLAY,
    META_DESCRIPTION,
    YINHE_AU3_VER,
    build_page,
)


def _blocks_by_type(page, kind):
    return [block for block in page["blocks"] if block["type"] == kind]


def _lists(page):
    return [block for block in page["blocks"] if block["type"] == "list"]


class BuildPageTest(unittest.TestCase):
    """页面数据：顺序、文案、链接。"""

    def setUp(self):
        self.page = build_page()

    def test_block_order_matches_php(self):
        """PHP ``EchoAll()`` 里 echo 的 9 个东西，一个不多一个不少。"""
        self.assertEqual(
            ["paragraph", "list", "paragraph", "paragraph", "paragraph",
             "list", "paragraph", "list", "bugs"],
            [block["type"] for block in self.page["blocks"]],
        )

    def test_title_and_meta_description(self):
        self.assertEqual("拖拉机自动化", self.page["title"])
        self.assertEqual(AUTO_TRACTOR_DISPLAY, self.page["display"])
        self.assertEqual(META_DESCRIPTION, self.page["meta_description"])
        # PHP CheckMetaDescription() 只做长度告警（上限 156 字），不截断
        self.assertLess(len(self.page["meta_description"]), 156)
        self.assertIn("SZ162411", self.page["meta_description"])

    def test_install_steps(self):
        """四条安装步骤，前两条各自带官网外链与 ``Tc.exe``。"""
        steps = _lists(self.page)[0]
        self.assertTrue(steps["ordered"], "安装步骤在 PHP 里是 GetListElement() 缺省有序")
        self.assertEqual(4, len(steps["items"]))

        self.assertIn("chinastock.com.cn", steps["items"][0])
        self.assertIn("银河证券官网", steps["items"][0])
        self.assertIn("cnhbstock.com", steps["items"][1])
        self.assertIn("华宝证券官网", steps["items"][1])
        # 第三条是 AutoIt 本体，第四条是两个脚本下载链接
        self.assertIn("autoitscript.com", steps["items"][2])
        self.assertIn('href="/autoit/yinhe.au3"', steps["items"][3])
        self.assertIn('href="/debug/autoitscript/yinheaccounts.au3"', steps["items"][3])

    def test_file_link_display_is_basename(self):
        """``GetFileLink()`` = ``GetExternalLink($path, basename($path))``。"""
        steps = _lists(self.page)[0]
        self.assertIn(">yinhe.au3</a>", steps["items"][3])
        self.assertIn(">yinheaccounts.au3</a>", steps["items"][3])

    def test_version_number_appears_twice(self):
        """``YINHE_AU3_VER`` 在两处拼：``0.99`` 与 ``yinhe099.au3``。"""
        self.assertEqual("99", YINHE_AU3_VER)
        text = " ".join(block.get("html", "") for block in self.page["blocks"])
        self.assertIn("目前版本为<b>0.99</b>", text)
        self.assertIn('<span class="info">yinhe099.au3</span>', text)
        # 执行那一段也要给出同一个文件名（PHP 复用 $strYinheAu3Now）
        self.assertIn('yinhe099.au3</span>都可以执行', text)

    def test_develop_paragraph_links(self):
        """GitHub 仓库、雪球作者页、场外申购分支的代码。"""
        blocks = _blocks_by_type(self.page, "paragraph")
        develop = [b for b in blocks if "软件开发" in b["html"]][0]["html"]
        self.assertIn("https://github.com/woody152/web/", develop)
        self.assertIn("https://xueqiu.com/u/2747564710", develop)
        self.assertIn("https://github.com/wzqwsrf/web/tree/feature-yufei/autoit", develop)
        self.assertIn("磨刀霍霍向猪羊01", develop)

    def test_operations_are_unordered(self):
        """七种操作在 PHP 里是 ``GetListElement($ar, false)`` —— **无序**。"""
        operations = _lists(self.page)[1]
        self.assertFalse(operations["ordered"])
        self.assertEqual(7, len(operations["items"]))
        for keyword in ("转账回银行", "逆回购", "场内申购", "赎回", "卖出", "全部撤单", "仅登录查询"):
            with self.subTest(keyword=keyword):
                self.assertTrue(
                    any(item.startswith(keyword) for item in operations["items"]),
                    "缺少操作：%s" % keyword,
                )

    def test_account_items(self):
        """五条管理客户号说明（有序），最后一条是红字提醒。"""
        account = _lists(self.page)[2]
        self.assertTrue(account["ordered"])
        self.assertEqual(5, len(account["items"]))
        self.assertIn("修改：", account["items"][0])
        self.assertIn("添加：", account["items"][1])
        self.assertIn("批量手工添加和修改：", account["items"][2])
        # 第 3 条中间用 <br /> 断成两段（PHP 是同一个字符串里拼 GetHtmlNewLine()）
        self.assertIn("<br />", account["items"][2])
        self.assertIn('<span class="alert">废弃电脑前', account["items"][4])

    def test_known_bugs(self):
        """``GetKnownBugs()`` = ``<h3>已知问题</h3>`` + **有序**十条。"""
        bugs = _blocks_by_type(self.page, "bugs")
        self.assertEqual(1, len(bugs))
        self.assertEqual("已知问题", bugs[0]["heading"])
        self.assertEqual(10, len(bugs[0]["items"]))
        self.assertIn("PIN码", bugs[0]["items"][0])
        # 最后一条里的注册表路径含反斜杠，别在转义上翻车
        self.assertIn(r"HKEY_CURRENT_USER\Software\Microsoft", bugs[0]["items"][9])

    def test_no_font_element_anywhere(self):
        """刻意偏离：``<font color=…>`` 换成语义类，整页不该再出现 ``<font``。"""
        for index, block in enumerate(self.page["blocks"]):
            with self.subTest(block=index):
                self.assertNotIn("<font", block.get("html", ""))
                for item in block.get("items", []):
                    self.assertNotIn("<font", item)

    def test_no_images(self):
        """刻意偏离：两张截图（``ImgAutoQuote`` / ``GetWoodyImgQuote``）不渲染。"""
        for block in self.page["blocks"]:
            with self.subTest(block=block["type"]):
                self.assertNotIn("<img", block.get("html", ""))
                for item in block.get("items", []):
                    self.assertNotIn("<img", item)

    def test_css_classes_used_exist_in_stylesheet(self):
        """用到的颜色类都要在 style.css 里有定义，否则页面会"少一块颜色"。

        注意 ``.autotractor-list`` 只以 ``.autotractor-list li`` 的形式出现
        （它没有自己的声明块），所以那一项单独放松成"出现过"。
        """
        css = (ROOT_DIR / "python" / "web" / "static" / "style.css").read_text(encoding="utf-8")
        for name in ("remark", "info", "alert", "quote"):
            with self.subTest(class_name=name):
                self.assertRegex(css, r"\.%s\s*\{" % name)
        self.assertIn(".autotractor-list li", css)


class RouteTest(unittest.TestCase):
    """``/autotractor`` 能开、渲染出整页内容。"""

    def setUp(self):
        app = create_app()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_page_renders(self):
        response = self.client.get("/autotractor")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("<h1>拖拉机自动化</h1>", html)
        self.assertIn("完整软件安装步骤", html)
        self.assertIn("<h3>已知问题</h3>", html)
        # 4 条安装 + 7 条操作 + 5 条客户号 + 10 条已知问题，列表项总数 26
        self.assertEqual(26, len(re.findall(r"<li>", html)))
        # 三种列表标签都在：安装(ol) / 操作(ul) / 客户号(ol) / 已知问题(ol)
        self.assertIn("<ol class=\"autotractor-list\">", html)
        self.assertIn("<ul class=\"autotractor-list\">", html)

    def test_page_url_is_the_php_page_name(self):
        """页面名照 PHP（``autotractor.php``，``cn`` 后缀被 UrlGetPage() 剥掉）。"""
        rules = {rule.rule for rule in self.client.application.url_map.iter_rules()}
        self.assertIn("/autotractor", rules)

    def test_title_tag(self):
        html = self.client.get("/autotractor").get_data(as_text=True)
        self.assertIn("<title>拖拉机自动化</title>", html)
