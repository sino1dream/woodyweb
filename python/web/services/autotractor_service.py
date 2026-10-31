"""拖拉机自动化说明页（``woody/res/autotractorcn.php`` → ``/autotractor``）。

## 对应 PHP

页面文件本身只有 4 行壳子：

.. code-block:: php

    <?php
    require('php/_autotractor.php');
    require('../../php/ui/_dispcn.php');
    ?>

真正的内容在 ``woody/res/php/_autotractor.php``（98 行）。整页**没有一个数字**：
没有数据库、没有行情、没有引用对象，只有 ``EchoAll()`` 里一串拼出来的 HTML。
所以这一页的风险不在计算，而在**文案与顺序**：漏一段、多一句、把有序列表写成
无序的，都不报错，只是与生产端不一样。本模块因此把内容按 PHP 的 echo 顺序拍成
一个 ``blocks`` 列表，模板逐个渲染，顺序由数据决定而不是由模板决定。

## 段落与 PHP 的对应

========================================  ==========================================
PHP ``EchoAll()`` 里的语句               ``blocks`` 里的块
========================================  ==========================================
``EchoHtmlElement(GetRemarkElement(...))``  ``{"type": "paragraph"}``
``echo GetListElement(array(...))``          ``{"type": "list", "ordered": True}``
``echo GetListElement(array(...), false)``   ``{"type": "list", "ordered": False}``
``EchoHtmlElement($str)``                    同 paragraph（多段用 ``<br />`` 连）
``echo GetKnownBugs(array(...))``            ``{"type": "bugs"}``
========================================  ==========================================

``GetKnownBugs()`` 是 ``GetHeadElement('已知问题')`` + ``GetListElement($arBug)``，
即 ``<h3>已知问题</h3>`` 加一个**有序**列表 —— 独立成一种块，因为标题是固定的。

## 元素函数：从 ``<font color>`` 换成 CSS 类

PHP 的 ``GetRemarkElement()`` / ``GetInfoElement()`` / ``GetFontElement()`` 输出的是
``<font color=green|blue|red>``。这一层是**显示**，不是语义，本版统一换成语义化的
``<span class="remark|info|alert|quote">``（配色在 ``static/style.css``），与
``fund_detail.html`` 那边"颜色只做数据、不再 echo ``<font color>``"的取舍一致。
文本一个字都没改。

## 刻意偏离

* **不渲染两张截图**（``ImgAutoQuote(PATH_BLOG_PHOTO.'autoit066screen.jpg', ...)``
  与 ``GetWoodyImgQuote('20201029.jpg', ...)``）。与分类页不渲染
  ``ImgStockGroup()`` 是同一个决定：``ImgAutoQuote()`` 会在服务端用 GD 现生成
  缩放图并写盘，Python 侧没有这条链路；图片本身属于博客资源，不在本次迁移范围。
  删掉的是图片和它那句 ``<q>`` 说明，前后文字仍连得上。
* **不渲染 ``$acct->EchoLinks('chaos')``**：那是页脚，``base.html`` 统一给。
* 稿子里被 ``/* */`` 注掉的两条安装步骤（Tesseract 验证码识别、三个脚本文件）
  **照 PHP 现状不渲染** —— 它们不是"待补"，是作者已经弃用的写法。

## 外链与文件链接

``GetFileLink('/autoit/yinhe.au3')`` → ``<a href="/autoit/yinhe.au3" target="_blank">yinhe.au3</a>``
（PHP 的 ``GetFileLink()`` = ``GetExternalLink($strPathName, basename($strPathName))``）。
本地没有这几个文件，链接会 404；它们的宿主是生产端站点根，换域名才有意义，
所以**不改成生产端绝对地址**，保持与 PHP 一样的根相对路径。
"""

from __future__ import annotations

import posixpath

from web.bootstrap import install_paths

install_paths()

from ref.externallink import XUEQIU_URL, get_external_link  # noqa: E402


#: ``php/stocklink.php:292`` 的 ``AUTO_TRACTOR_DISPLAY``。也是页面 ``<h1>``。
AUTO_TRACTOR_DISPLAY = "拖拉机自动化"

#: ``_autotractor.php:5`` 的 ``YINHE_AU3_VER``。脚本目前是 ``0.99``。
YINHE_AU3_VER = "99"

#: ``GetMetaDescription()`` 的原文（``CheckMetaDescription()`` 只做长度告警、
#: 不截断，所以照抄整句）。
META_DESCRIPTION = (
    "利用PC上的AutoIt脚本工具软件实现华宝油气(SZ162411)等场内基金拖拉机账户的"
    "自动化登录、申购、卖出和全部撤单。解放双手，提高十几万套利党人的时间效率。"
)

#: ``_autotractor.php`` 里那几个下载/官网地址，集中在这里免得散在文案里。
NEPTUNE_URL = (
    "https://www.chinastock.com.cn/newsite/online/downloadCenterDetail.html?softName=neptune"
)
HUABAO_URL = "https://www.cnhbstock.com/view/software/software.html?col=0"
AUTOIT_DOWNLOAD_URL = "https://www.autoitscript.com/site/autoit/downloads/"
GITHUB_URL = "https://github.com/woody152/web/"
YUFEI_BRANCH_URL = "https://github.com/wzqwsrf/web/tree/feature-yufei/autoit"

#: 雪球用户页（``GetXueqiuIdLink()``）：``GetXueqiuUrl().'u/'.ID``。
#: 注意 PHP 的 ``GetXueqiuUrl()`` 带结尾斜杠（``https://xueqiu.com/``），
#: ``ref.externallink.XUEQIU_URL`` 不带 —— 拼的时候自己补。
XUEQIU_UID = "2747564710"
XUEQIU_DISPLAY = "磨刀霍霍向猪羊01"


# ---------------------------------------------------------------------------
# PHP ``php/ui/htmlelement.php`` 里那几个元素函数的替身。
# 只做"文本 → 行内 HTML"，不碰布局；``<font color>`` 换成 CSS 类（见模块注释）。
# ---------------------------------------------------------------------------


def _remark(text: str) -> str:
    """``GetRemarkElement()``（``<font color=green>``）：小标题。"""
    return '<span class="remark">%s</span>' % text


def _info(text: str) -> str:
    """``GetInfoElement()``（``<font color=blue>``）：界面上的按钮/控件名。"""
    return '<span class="info">%s</span>' % text


def _alert(text: str) -> str:
    """``GetFontElement($text)`` 的缺省色（red）：警示语。"""
    return '<span class="alert">%s</span>' % text


def _quote(text: str) -> str:
    """``GetQuoteElement()``（``<font color=gray>``）：引述。"""
    return '<span class="quote">%s</span>' % text


def _bold(text: str) -> str:
    """``GetBoldElement()``。"""
    return "<b>%s</b>" % text


def _file_link(path: str) -> str:
    """``GetFileLink()``：链接文本取 ``basename()``，与 PHP 一致。"""
    return get_external_link(path, posixpath.basename(path))


def _xueqiu_id_link(uid: str, display: str) -> str:
    """``GetXueqiuIdLink()``：``GetXueqiuUrl().'u/'.$strId``。"""
    return get_external_link(XUEQIU_URL + "/u/" + uid, display)


def _newline() -> str:
    """``GetHtmlNewLine()``。PHP 用 ``<br />``。"""
    return "<br />"


# ---------------------------------------------------------------------------
# 内容。逐段对照 ``_autotractor.php:11-83``。
# ---------------------------------------------------------------------------


def _install_steps() -> list:
    """``_autotractor.php:19-22`` 那四条（有序）。

    第 23-24 行那两条被 ``/* */`` 注掉（Tesseract 方案），按现状不渲染。
    """
    nepturn = _bold("海王星单独委托版3.29")
    huabao = _bold("通达信版独立交易8.28")
    autoit = _bold("AutoIt3.exe")
    tc = _bold("Tc.exe")
    autoit_full = _bold(r"C:\Program Files (x86)\AutoIt3\AutoIt3.exe")

    return [
        "在" + get_external_link(NEPTUNE_URL, "银河证券官网")
        + r"下载并在缺省路径C:\中国银河证券海王星独立交易\位置安装" + nepturn + "的" + tc
        + "，桌面图标会显示" + _info("中国银河证券海王星独立交易")
        + "，注意它不同于" + _alert("海王星金融终端")
        + "软件。VPN下在银河官网下载经常会停留在8M字节不动，要关闭后才能完整下载。",

        "在" + get_external_link(HUABAO_URL, "华宝证券官网")
        + r"下载并在缺省路径C:\tc_hbzq\位置安装" + huabao + "的" + tc
        + "，桌面图标会显示" + _info("华宝证券独立交易") + "。",

        "下载并安装开源的" + get_external_link(AUTOIT_DOWNLOAD_URL, "AutoIt")
        + "工具软件包。普通用户实际仅需用到x86版本的" + autoit_full
        + "文件。通达信还在使用上世纪WIN95时代开始的WIN32控件，去用x64的工具链就无法兼容了。"
        + "一定要小心软件来源，千万不要运行来历不明的.exe文件，同时也要留意不要让本机上的"
        + "杀毒软件误拦截" + autoit + "的运行。",

        "在本页面下载银河华宝拖拉机自动化PC软件脚本的2个文件到同一个子目录下，分别是"
        + _file_link("/autoit/yinhe.au3") + "和"
        + _file_link("/debug/autoitscript/yinheaccounts.au3") + "。",
    ]


def _update_paragraph() -> str:
    """``_autrotractor.php:30-31``（软件更新）。

    版本号两处都由 ``YINHE_AU3_VER`` 拼出来：``0.99`` 与 ``yinhe099.au3``。
    """
    return (
        _remark("软件更新：") + _newline()
        + "只有" + _info("yinhe.au3") + "文件是会日常更新的，目前版本为"
        + _bold("0." + YINHE_AU3_VER)
        + "。下载前注意清除浏览器的下载文件缓存，确保下载到正确的版本。"
        + "由于更新时无法覆盖所有的测试，每次下载新版本前注意备份好上一个能用的版本。"
        + "例如保存成：" + _info("yinhe0" + YINHE_AU3_VER + ".au3")
    )


def _develop_paragraph() -> str:
    """``_autotractor.php:34-37``（软件开发）。"""
    return (
        _remark("软件开发：") + _newline()
        + "本网站全部源代码都公开放在了全球最大同性交友网站"
        + get_external_link(GITHUB_URL, "GitHub")
        + "。想自己进一步修改软件的除了上面的.au3文件外，还可能需要去下载制作用户界面时用到的"
        + _info("yinhe.kxf") + "和" + _info("yinheaccount.kxf") + "两个文件。"
        + _newline()
        + _xueqiu_id_link(XUEQIU_UID, XUEQIU_DISPLAY)
        + "增加了场外申购和场外转托管到场内的"
        + get_external_link(YUFEI_BRANCH_URL, "代码")
        + "，感兴趣的可以自行下载测试。"
    )


def _execute_paragraph() -> str:
    """``_autotractor.php:41-47``（软件执行）。

    PHP 在两句话中间插了 ``ImgAutoQuote()`` 那张主界面截图（第 44 行），
    本版不渲染图片，两句话直接用 ``<br />`` 接起来（见模块注释"刻意偏离"）。
    """
    all_software = _bold("海王星单独委托版3.29") + "或者" + _bold("通达信版独立交易8.28")
    return (
        _remark("软件执行：") + _newline()
        + "首先关闭本机上所有在运行的" + all_software + "，在运行x86版本的"
        + _bold("AutoIt3.exe") + "后，它会提示输入文件。给它" + _info("yinhe.au3")
        + "或者" + _info("yinhe0" + YINHE_AU3_VER + ".au3") + "都可以执行。"
        + _newline()
        + "在弹出的用户主界面用鼠标点击" + _info("执行自动操作") + "按键后，会看到它自动运行"
        + all_software + "，然后一步步在每个打勾的" + _info("客户号")
        + "的全部可用账户中各自执行选择的" + _info("操作")
        + "。除了按ESC键主动退出和响应AutoIt脚本自己的错误提示消息框外，"
        + "在结束前不能操作任何键盘或者鼠标，否则脚本可能会出错。"
    )


def _operations() -> list:
    """``_autotractor.php:51-58`` 那七种操作（**无序**，``$bOrder = false``）。"""
    fund = _info("基金代码") + "加亮选中基金"
    quantity = _info("卖出或者赎回总数量")
    all_software = _bold("海王星单独委托版3.29") + "或者" + _bold("通达信版独立交易8.28")

    return [
        "转账回银行：把剩余可转银行资金全部转回，这里假定了银行资金密码和该客户号的"
        "登录密码是一样的。暂时不支持需要双重密码的华宝证券。",

        "逆回购：把剩余可用资金下单比场内价格低一毛卖出204001。",

        "场内申购：按当日限购金额自动申购" + fund + "。",

        "赎回：按" + quantity + "赎回" + fund + "。暂时不支持华宝证券。",

        "卖出：按" + _info("卖出价格") + "和" + quantity + "卖出" + fund
        + "。全部卖出时填一个大于所有账号数量之和再加100的数即可，因为软件会对100股以下的"
        + "碎股四舍五入。银河证券在前一日的17:30会短暂开放夜市委托，打算第二天抢跌停卖的"
        + "可以提前在这里执行，注意要手工输入正确的跌停价。",

        "全部撤单：" + fund + "的全部申购、赎回或者卖出订单都会被一次性撤销。暂时不支持华宝证券。",

        "仅登录查询：跟前面所有操作不同，这里登录全部打勾的客户号后不会自动退出"
        + all_software + "。",
    ]


def _account_items() -> list:
    """``_autotractor.php:65-70`` 那五条管理客户号说明（有序）。

    PHP 第 61 行先 echo 一个 remark「管理客户号：」，紧跟着是
    ``GetWoodyImgQuote('20201029.jpg', ...)`` 那张截图 —— 本版不渲染图片，
    所以这一段的 heading 直接接列表。
    """
    menu = _info("添加或者修改选中客户号")
    return [
        "修改：加亮选中" + _quote("不是前面打勾") + "某客户号时，按鼠标右键，选择"
        + menu + "菜单，会继续弹出对话框修改当前客户号"
        + _quote("上图中是23050008000") + "。",

        "添加：没选中任何客户号时，按鼠标右键，选择" + menu
        + "菜单，会继续弹出对话框添加新客户号。",

        "批量手工添加和修改：脚本运行后会把客户号和密码存储在注册表中。"
        + "用AutoIt自带的SciTE.exe编辑" + _info("yinheaccount.au3")
        + "，缺省下载文件里面有3个账号的位置，不够的可以自己加。注意别换行，"
        + "增加账号和密码后记得改动数字3。"
        + _newline()
        + "保存后在客户号区域按鼠标右键，选择" + _info("清除全部客户号记录")
        + "，然后关闭AutoIt.exe软件重新运行，就会使用改动后的客户号和密码。",

        "点击" + _info("客户号") + "可以切换全选或者全不选",

        _alert("废弃电脑前，要记得清除全部客户号记录，避免泄露。"),
    ]


def _known_bugs() -> list:
    """``_autotractor.php:73-83`` 的十条已知问题（``GetKnownBugs()`` → 有序）。"""
    return [
        "从0.61版本开始，使用海王星3.07新增加的PIN码安全方式登录。第一次从验证码切换到"
        "PIN码时需要手工操作，以后如果被提示没有PIN码或者过期，也需要手工在证书管理中"
        "使用默认PIN码申请一下。事实上，因为AutoIt只是模拟鼠标键盘操作，碰到任何问题时"
        "先手工操作一下看看总是有帮助的。",

        "在小屏幕电脑上，海王星窗口内容可能会被遮挡，需要手工最大化后才能继续运行。"
        "可以先手工登录一次，把窗口扩大到比最大化小一点的状态后退出，下一次就能成功自动运行。",

        "登录账户时，出现登录安全提示窗口不自动关闭时，可以把主窗口往上拉一点，"
        "安全提示信息不挡住多账户按钮就可以",

        "使用双屏或者多屏如果有问题的话，改成单屏显示。",

        "卖出一个账户后就退出时，需要把卖出或者赎回总数量设置到足够大，比如1000000。",

        "除了Parallels Desktop for Mac外，海王星不能在大多数虚拟机中使用。",

        "网速很重要！在目前代码中有大量模拟按键或者鼠标后等待一秒的被动行为，"
        "在网速慢的时候会因为等待时间不够长而出错。我就可能需要在运行代码前先手工把"
        "电脑上的网络从天威宽带切换到自己手机上的移动4G热点。",

        "电脑上同时安装了海王星和双子星时可能会有异常，建议先卸载双子星，"
        "等海王星和autoit配合工作后再重新安装双子星。",

        "在基金概要文件那部分，浏览器会弹出框让选择打开或者下载，需要手工点一下，"
        "要不到不了下一步。这是要给PDF文件设置缺省打开的应用程序，给浏览器安装adobe的"
        "阅读PDF插件后能解决这个问题。在电脑上安装一下Adobe官方的免费PDF阅读器软件也可以"
        "解决这个问题。安装最后问是否设置为默认应用程序的时候要确认。另外也不要安装过多的"
        "PDF阅读器，有问题是优先确认电脑上只有一个PDF阅读器，把其它多余的都卸载。",

        r"WIN7系统下海王星不能正常退出。可以运行系统自带的注册表编辑器regedit.exe，"
        r"依次定位到HKEY_CURRENT_USER\Software\Microsoft\Windows\WindowsError Reporting，"
        "在右侧窗口中找到并双击打开DontshowUI，然后在弹出的窗口中将默认值0修改为1。",
    ]


def build_page() -> dict:
    """整页内容。``blocks`` 的顺序 = PHP ``EchoAll()`` 里 echo 的顺序。

    模板只按 ``type`` 分派渲染（``paragraph`` / ``list`` / ``bugs``），
    不参与决定顺序 —— 否则"漏一段"这种错误在模板里看不出来。
    """
    return {
        "title": AUTO_TRACTOR_DISPLAY,
        "display": AUTO_TRACTOR_DISPLAY,
        "meta_description": META_DESCRIPTION,
        "blocks": [
            {"type": "paragraph", "html": _remark("完整软件安装步骤：")},
            {"type": "list", "ordered": True, "items": _install_steps()},
            {"type": "paragraph", "html": _update_paragraph()},
            {"type": "paragraph", "html": _develop_paragraph()},
            {"type": "paragraph", "html": _execute_paragraph()},
            {"type": "list", "ordered": False, "items": _operations()},
            {"type": "paragraph", "html": _remark("管理客户号：")},
            {"type": "list", "ordered": True, "items": _account_items()},
            {"type": "bugs", "heading": "已知问题", "items": _known_bugs()},
        ],
    }
