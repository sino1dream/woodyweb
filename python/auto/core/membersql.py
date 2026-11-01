"""会员表（``member``）与资料表（``profile``）的 Sql 访问层。

对应 PHP ``php/sql/sqlmember.php``（164 行）里的 ``MemberSql`` 类与一串
模块级函数，外加 ``account/php/_profile.php`` 的 ``AcctDeleteMember()``。

## 一、为什么现在才迁

2026-10-24 建 ``stockgroup`` 那批表时只把 ``member`` 建了"最小集"
（id + email + register + login + status + activity），当时账号体系暂缓，
``SqlGetIdByEmail()`` 只服务"基金申购金额"那个会员级写入口。
真正要跑登录，缺两样东西：``member.password`` 列与 ``profile`` 表。
两者都由 ``dbtools/patch_member_auth.sql`` 补上（该补丁的存在理由见其文件头）。

## 二、与 PHP 的三处结构偏离

1. **``ip`` 列是 VARCHAR 而不是 INT**。PHP 的 ``MemberSql::Create()`` 用
   ``$this->Add_id('ip')`` —— INT UNSIGNED + 外键指向 ``ipaddress`` 表，
   ``GetIpLink()`` 拿这个 id 再去查表。本地不迁 IP 统计（``account/visitor``
   那一族），也没有 ``ipaddress`` 表，所以直接存 IP 字符串。
   影响面：``_profile.php`` 的"上次登录IP地址"那行本地直接显示字符串，
   不再是链接。
2. **``member2`` 表不建**。PHP 的 ``MemberSql`` 构造时传 ``TABLE_MEMBER.'2'``
   （遗留命名，注释里就写着 member2），但全部**读写函数**用的是
   ``TABLE_MEMBER``（= ``member``）。本模块只迁"真正被读写的那个"，
   ``member2`` 是死代码 —— 这一点在 README 里记明，不假装迁了。
3. **不建 ``FULLTEXT(signature)`` 与 ``datetime index``**。演示库的
   ``member`` 是简化版，全文索引只服务博客评论搜索，评论整体不迁。

## 三、``SqlCleanString()`` 那个 ``stripslashes`` 坑

``php/sql.php:147`` 的 ``SqlCleanString()`` = ``UrlCleanString()`` +
``mysqli_real_escape_string()``，而 ``UrlCleanString()``（``php/url.php:127``）
是 ``trim()`` + **无条件** ``stripslashes()``。``stripslashes`` 外面那层
``if (get_magic_quotes_gpc())`` 被注释掉了，而 ``magic_quotes_gpc`` 在
PHP 5.4 之后恒为 false —— 也就是说这个 ``stripslashes()`` **永远在跑**。

后果：密码里带反斜杠时，表单值会被吞掉一层（``abc\\def`` → ``abcdef``）。
而 ``_ProfileAccount::Run()`` 的写法是"校验用 clean 过的、落库用
``$_POST['password']`` 原值"（见 ``_loginAccount`` 传
``$_POST['password']``），两处口径不同。本模块不拼接 SQL（全参数化），
所以只需要在**校验**路径上复刻 ``sql_clean_string()``，落库仍用原值。

## 四、返回值的"PHP false"约定

``SqlGetIdByEmail()`` / ``SqlGetEmailById()`` / ``SqlGetNameByMemberId()``
查不到都返回 ``false``，本模块统一返回 ``None``（见 ``AGENTS.md`` 第 3 条）。
id 一律转 ``str``：PHP 从 mysqli 拿到的就是字符串，上层（session 里存的
``SESS_ID``）也按字符串比，转成 int 会让"``'1' == 1``"这种比较在
不同调用点表现不一。
"""

from __future__ import annotations

import hashlib
from typing import Any

#: ``member.status`` 的两个有意义取值（``_profile.php`` 的展示分支）。
#: 1 = 不接收任何邮件（注册时的默认值，见 ``SqlInsertMember``），
#: 2 = 接收 Palmmicro 邮件订阅；其它值一律显示"帐号受限制"。
STATUS_NO_EMAIL = 1
STATUS_SUBSCRIBED = 2

#: 注册时写的 ``status``：PHP ``SqlInsertMember`` 硬编码 ``'1'``。
DEFAULT_STATUS = STATUS_NO_EMAIL


# ---------------------------------------------------------------------------
# 内部小工具：兼容 DictCursor 与普通 cursor
# ---------------------------------------------------------------------------

def _connection_of(source: Any) -> Any:
    """``source`` 既可以是 data source（有 ``.connection``）也可以是连接本身。

    与 ``core.stockgroupsql`` 同一约定 —— 调用方传哪个都行，测试里传
    假连接最省事。
    """
    return getattr(source, "connection", source)


def _field(row: Any, key: str, index: int) -> Any:
    """从一行里取字段：dict（DictCursor）按下标键，tuple 按下标位置。"""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return row[index] if index < len(row) else None


def _fetch_one(source: Any, sql: str, params: tuple = ()) -> Any:
    connection = _connection_of(source)
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()


def _fetch_all(source: Any, sql: str, params: tuple = ()) -> list:
    connection = _connection_of(source)
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        return list(cursor.fetchall() or [])
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()


def _execute(source: Any, sql: str, params: tuple = ()) -> int:
    connection = _connection_of(source)
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        return int(cursor.rowcount or 0)
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()


# ---------------------------------------------------------------------------
# 密码与输入清洗
# ---------------------------------------------------------------------------

def hash_password(password: Any) -> str:
    """PHP ``md5($strPassword)`` 的等价物。

    注意 PHP 的 ``md5()`` 对入参先做字符串转换（``null`` → ``''``），
    所以这里也走 ``str()``。编码用 UTF-8：PHP 不指定编码时按字节处理，
    而 Python 的 ``str.encode()`` 默认就是 UTF-8 —— 对纯 ASCII 密码两者
    完全一致；非 ASCII 密码在 PHP 侧取决于源码/请求的字节序列，
    本地唯一能对齐的口径是 UTF-8（浏览器表单默认也是它）。
    """
    if password is None:
        password = ""
    return hashlib.md5(str(password).encode("utf-8")).hexdigest()


def _php_stripslashes(text: str) -> str:
    """PHP ``stripslashes()``：``\\\\`` → ``\\``，``\\'`` → ``'``，``\\"`` → ``"``。

    只处理这三种（外加 ``\\0`` 那个 NUL 转义），其它 ``\\x`` **原样保留** ——
    PHP 的实现就是这样，不是"去掉所有反斜杠"。复刻它是因为
    ``sql_clean_string()`` 的校验口径要跟 PHP 一致（见模块 docstring 三）。
    """
    out = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\" and index + 1 < length:
            nxt = text[index + 1]
            if nxt == "\\" or nxt == "'" or nxt == '"':
                out.append(nxt)
                index += 2
                continue
            if nxt == "0":
                out.append("\x00")
                index += 2
                continue
        out.append(char)
        index += 1
    return "".join(out)


def sql_clean_string(value: Any) -> str:
    """``SqlCleanString()`` 的**校验口径**子集：``trim()`` + ``stripslashes()``。

    去掉了 ``mysqli_real_escape_string()`` 那一层 —— 本模块全参数化查询，
    再转义一次只会把反斜杠写进库里（那是 PHP 那个年代的必需品，
    在这里纯属自找麻烦）。所以这个函数只用于"判空 / 判相等"，
    **不要**拿它的结果去落库：PHP 自己也是拿原值落库的
    （``_loginAccount`` 传 ``$_POST['password']``）。

    公开名字（不带下划线）是因为它跨模块用：``web/services/auth_service.py``
    的六个认证动作全部按这个口径校验。PHP 那边 ``SqlCleanString()`` 也是
    全局函数，谁都能调。
    """
    if value is None:
        return ""
    return _php_stripslashes(str(value).strip())


# ---------------------------------------------------------------------------
# member 表
# ---------------------------------------------------------------------------

def sql_get_id_by_email(source: Any, email: Any) -> str | None:
    """``SqlGetIdByEmail()``（``sqlmember.php:76``）：email → member id。

    与 ``core.stockgroupsql.sql_get_id_by_email()`` 语义相同、实现各一份。
    **这里的重复是刻意的**：那个模块的职责是分组表，会员查询只是它为了
    "会员级写入口"顺手带的一条；本模块是会员表的主场，反向 import
    ``stockgroupsql`` 会让"会员 → 分组"的依赖方向倒过来（分组代码已经
    依赖 member 表的存在）。两条 SELECT 的代价远小于一次循环依赖。
    """
    row = _fetch_one(source, "SELECT id FROM member WHERE email = %s LIMIT 1",
                     (str(email),))
    if row is None:
        return None
    value = _field(row, "id", 0)
    return None if value is None else str(value)


def sql_get_member_by_id(source: Any, member_id: Any) -> dict | None:
    """``SqlGetMemberById()``：整行（``SqlGetTableDataById`` 的形状）。

    返回 dict 而不是 pymysql 的默认 tuple —— 调用方（资料页、权限判断）
    都是按列名取值，转一次省得上层记列序。列序与
    ``dbtools/patch_member_auth.sql`` 打完补丁后的表一致：
    ``id, email, password, register, login, status, activity, ip``。
    """
    row = _fetch_one(
        source,
        "SELECT id, email, password, register, login, status, activity, ip "
        "FROM member WHERE id = %s LIMIT 1", (member_id,))
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    keys = ("id", "email", "password", "register", "login", "status",
            "activity", "ip")
    return {key: row[index] for index, key in enumerate(keys)
            if index < len(row)}


def sql_get_email_by_id(source: Any, member_id: Any) -> str | None:
    """``SqlGetEmailById()``（``sqlmember.php:100``）：id → email。

    查不到返回 ``None``（PHP 是 ``false``）。``GetLoginEmail()`` 靠它把
    session 里的 ``SESS_ID`` 换成邮箱，再判 ``IsAdmin()``。
    """
    row = _fetch_one(source, "SELECT email FROM member WHERE id = %s LIMIT 1",
                     (member_id,))
    if row is None:
        return None
    value = _field(row, "email", 0)
    return None if value is None else str(value)


def sql_get_member_by_ip(source: Any, ip: Any) -> list:
    """``SqlGetMemberByIp()``：同一个 IP 登录过的会员，按 ``login`` 倒序。

    PHP 版是 ``SqlGetTableData(TABLE_MEMBER, where ip, '`login` DESC')``，
    返回 mysqli 结果集；这里返回 dict 列表。``SqlGetMemberByIp`` 在生产端
    只被"疑似多帐号"的排查用到，本地没有对应页面 —— 保留函数是为了
    ``account/visitor`` 那一族将来接上时不用回来补。
    """
    rows = _fetch_all(
        source,
        "SELECT id, email, register, login, status, activity, ip "
        "FROM member WHERE ip = %s ORDER BY `login` DESC", (str(ip),))
    result = []
    for row in rows:
        if isinstance(row, dict):
            result.append(dict(row))
        else:
            keys = ("id", "email", "register", "login", "status", "activity", "ip")
            result.append({key: row[index] for index, key in enumerate(keys)
                           if index < len(row)})
    return result


def sql_exec_login(source: Any, email: Any, password: Any) -> str | None:
    """``SqlExecLogin()``（``sqlmember.php:58``）：email + 明文密码 → member id。

    PHP 是 ``SELECT * FROM member WHERE email=? AND password=md5(?)``，
    密码比较在**库侧**做（等值比较）。本模块同样把 md5 拼进 WHERE，
    而不是"先查密码再在 Python 里比" —— 后者会多一次传输，而且
    大小写敏感性由库的列 collation 决定（``latin1_general_ci``
    不区分大小写），两种写法必须落在同一个比较语义上。
    """
    row = _fetch_one(
        source,
        "SELECT id FROM member WHERE email = %s AND password = %s LIMIT 1",
        (str(email), hash_password(password)))
    if row is None:
        return None
    value = _field(row, "id", 0)
    return None if value is None else str(value)


def sql_insert_member(source: Any, email: Any, password: Any,
                      ip: str = "") -> bool:
    """``SqlInsertMember()``（``sqlmember.php:67``）：注册新会员。

    PHP 是 ``INSERT INTO member(id, email, password, ip, register, login,
    status, activity) VALUES('0', ..., NOW(), NOW(), '1', '0')``：
    id 显式给 ``'0'`` 走 AUTO_INCREMENT、时间用 ``NOW()``（服务器时区）、
    ``status`` 固定 1、``activity`` 固定 0。本模块逐项照抄。

    ``ip`` 列本地是 VARCHAR（模块 docstring 二.1），PHP 那边写的是
    ``UrlGetIp()`` 的字符串—— 它交给 INT 列时会变成 0 或一个奇怪的
    整数，这里存原文反而更接近 PHP 想表达的意思。
    """
    rows = _execute(
        source,
        "INSERT INTO member (id, email, password, ip, register, login, status, activity) "
        "VALUES (0, %s, %s, %s, NOW(), NOW(), %s, 0)",
        (str(email), hash_password(password), str(ip or ""), DEFAULT_STATUS))
    return rows > 0


def sql_update_password_field(source: Any, email: Any, password: Any) -> bool:
    """``SqlUpdatePasswordField()``：按 **email** 改密码（不是按 id）。

    PHP 的签名就是 email，调用现场传的是 ``$member['email']`` 或表单里的
    邮箱（``_remindPassword``）。按 email 改的副作用是"改密码"这个动作
    在 email 变更之后依然对得上 —— 照抄，不换成更"规范"的 id。
    """
    rows = _execute(source,
                    "UPDATE member SET password = %s WHERE email = %s LIMIT 1",
                    (hash_password(password), str(email)))
    return rows > 0


def sql_update_login_email(source: Any, member_id: Any, email: Any) -> bool:
    """``SqlUpdateLoginEmail()``：改登录邮箱（按 id）。

    这是"改登录帐号"那个动作。PHP 只改 ``member.email``，**不动**
    ``stockgroup`` 里的归属 —— 分组挂在 member_id 上，换邮箱不影响归属。
    """
    rows = _execute(source,
                    "UPDATE member SET email = %s WHERE id = %s LIMIT 1",
                    (str(email), member_id))
    return rows > 0


def sql_update_login_field(source: Any, email: Any, ip: str) -> bool:
    """``SqlUpdateLoginField()``：登录成功后记 IP 与登录时间。

    PHP 写的是 ``login = NOW()``，而演示库这一列是 ``DATE``（生产端是
    ``datetime``）。MySQL 对 DATE 列写 NOW() 会**截断成日期**且不报警告，
    与 PHP 完全一致，所以这里照写 NOW() 而不是在 Python 侧格式化日期 ——
    换成本地日期反而会在跨时区时和生产端错开一天。
    """
    rows = _execute(source,
                    "UPDATE member SET ip = %s, login = NOW() WHERE email = %s LIMIT 1",
                    (str(ip or ""), str(email)))
    return rows > 0


def sql_change_activity(source: Any, member_id: Any, delta: int) -> bool:
    """``SqlChangeActivity()``：活动指数加减 —— **读-改-写**，不是 ``activity+n``。

    PHP 的实现是先 ``SqlGetMemberById()`` 拿到当前值再加，再 UPDATE 回去。
    两者的差别在并发下：读-改-写会丢更新。**照抄**，因为"活动指数"只是
    个粗略计数，PHP 那边就是这行为，换成原子 ``activity = activity + n``
    会让本地与生产端的数据对不上（对照脚本比的就是这个数）。
    """
    member = sql_get_member_by_id(source, member_id)
    if member is None:
        return False
    try:
        current = int(member.get("activity") or 0)
    except (TypeError, ValueError):
        current = 0
    return _execute(source,
                    "UPDATE member SET activity = %s WHERE id = %s LIMIT 1",
                    (current + int(delta), member_id)) > 0


def sql_update_status(source: Any, member_id: Any, status: Any) -> bool:
    """``SqlUpdateStatus()``：1 = 不收邮件，2 = 订阅（见 ``_profile.php`` 的展示分支）。

    表单里是单选框（``_editprofileform.php`` 两个 radio，value 1 / 2），
    所以调用方传进来的就是这两个值之一；不做取值校验 —— PHP 也不校验，
    传 ``'0'`` 进去页面就显示"帐号受限制"。
    """
    return _execute(source,
                    "UPDATE member SET status = %s WHERE id = %s LIMIT 1",
                    (status, member_id)) > 0


# ---------------------------------------------------------------------------
# profile 表
# ---------------------------------------------------------------------------

def sql_get_profile_by_member_id(source: Any, member_id: Any) -> dict | None:
    """``SqlGetProfileByMemberId()``：一个会员一行资料。

    查不到返回 ``None``（PHP ``false``）—— ``_profile.php`` 与
    ``_editprofileform.php`` 都按"没有资料就显示空串"处理，**不**自动建行。
    """
    row = _fetch_one(
        source,
        "SELECT id, member_id, name, phone, address, web, signature "
        "FROM profile WHERE member_id = %s LIMIT 1", (member_id,))
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    keys = ("id", "member_id", "name", "phone", "address", "web", "signature")
    return {key: row[index] for index, key in enumerate(keys)
            if index < len(row)}


def sql_get_name_by_member_id(source: Any, member_id: Any) -> str | None:
    """``SqlGetNameByMemberId()``：资料里的名字（``GetWhoseDisplay`` 用它）。

    **没有资料行时返回 ``None`` 而不是空串** —— 调用方
    （``GetWhoseDisplay``）拿到假值后会回落到 email，若返回空串就会
    显示"的"这种残缺文案。
    """
    profile = sql_get_profile_by_member_id(source, member_id)
    if profile is None:
        return None
    name = profile.get("name")
    if name is None:
        return None
    name = str(name)
    return name or None


def sql_update_profile(source: Any, member_id: Any, name: Any, phone: Any,
                       address: Any, web: Any, signature: Any) -> bool:
    """``SqlUpdateProfile()``：有资料行就 UPDATE，没有就 INSERT。

    PHP 的判据是 ``if (SqlGetProfileByMemberId($id))`` —— 注意它把
    ``$id`` 当 member_id 传（函数签名却叫 ``$id``，容易看错）。照抄。

    ``signature`` 允许空：PHP 的 ``UPDATE profile SET ... signature = ''``
    会把签名清空，``_profile.php`` 那边显示成空行（不是"未填写"）。
    """
    values = (str(name or ""), str(phone or ""), str(address or ""),
              str(web or ""), str(signature or ""))
    if sql_get_profile_by_member_id(source, member_id) is not None:
        rows = _execute(
            source,
            "UPDATE profile SET name = %s, phone = %s, address = %s, "
            "web = %s, signature = %s WHERE member_id = %s LIMIT 1",
            values + (member_id,))
        return rows > 0
    rows = _execute(
        source,
        "INSERT INTO profile (id, member_id, name, phone, address, web, signature) "
        "VALUES (0, %s, %s, %s, %s, %s, %s)",
        (member_id,) + values)
    return rows > 0


def sql_delete_profile_by_member_id(source: Any, member_id: Any) -> int:
    """``SqlDeleteProfileByMemberId()``：删资料行，返回删掉的行数。

    PHP 的第三个参数 ``'1'`` 是 ``SqlDeleteTableData`` 的 "最多删几行"。
    本地 profile 有 ``UNIQUE(member_id)``，所以至多一行。
    """
    return _execute(source, "DELETE FROM profile WHERE member_id = %s LIMIT 1",
                    (member_id,))


# ---------------------------------------------------------------------------
# 关闭帐号：AcctDeleteMember()
# ---------------------------------------------------------------------------

def sql_delete_member(source: Any, member_id: Any) -> dict:
    """``AcctDeleteMember()``（``account/php/_profile.php:20``）的本地对应物。

    PHP 原顺序是::

        SqlDeleteFundPurchaseByMemberId();     // 基金申购金额表
        SqlDeleteStockGroupByMemberId();       // 分组 + 成员 + 交易
        SqlDeleteBlogCommentByMemberId();      // 博客评论
        SqlDeleteProfileByMemberId();          // 资料
        SqlDeleteTableDataById(TABLE_MEMBER);  // 会员本身

    本地三处不同：

    * **基金申购表不存在**（``fundpurchase`` 那套在 PHP 里已被注释掉一半，
      金额改存 ``groupitemamount``），删分组时会随 ``stockgroupitem`` 一起走
      分组自己的清理路径；
    * **博客评论整体不迁**（用户明确"博客内容不迁"），没有 comment 表；
    * ``SqlDeleteStockGroupByMemberId()`` 在 ``core.stockgroupsql`` 里已经
      按 PHP 的"手工先删 item 再删组"实现，这里直接复用。

    返回值是各行数，方便调用方记日志 / 测试断言 —— PHP 那边全靠
    ``SqlDieByQuery`` 的副作用，没有返回值。
    """
    from core import stockgroupsql

    groups = stockgroupsql.sql_delete_stock_group_by_member_id(source, member_id)
    profile = sql_delete_profile_by_member_id(source, member_id)
    member = _execute(source, "DELETE FROM member WHERE id = %s LIMIT 1",
                      (member_id,))
    return {"groups": groups, "profile": profile, "member": member}
