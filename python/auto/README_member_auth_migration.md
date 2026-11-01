# 账号体系迁移（`account/` 目录 → `/account/*`）

> 日期：2026-11-02　范围：**认证内核**（登录 / 注册 / 改密 / 改邮箱 / 重置密码 / 关号
> + 资料页 + 编辑资料 + 登出）
> 未包含：分组真库化（`/account` `/group` `/portfolio` 换掉演示数据）、
> 博客评论、`account/visitor` 与 IP 统计 —— 见 §八

## 一、这一轮到底迁了什么

`account/` 目录有 57 个文件，但**真正的实现只有 4 个**：

| PHP 实现 | 行数 | 本地 |
| --- | --- | --- |
| `account/php/_editemail.php` + `_editemailform.php` | 100 + 196 | `web/services/auth_service.py` + `web/routes/account_auth.py` |
| `account/php/_profile.php` | 495 | `web/routes/account_auth.py` 的 `profile_page()` |
| `account/php/_editprofileform.php` + `_submitprofile.php` | 77 + 68 | 同上，`edit_profile_page()` |
| `php/sql/sqlmember.php` | 164 | `python/auto/core/membersql.py` |

其余 50 来个是 4 行壳子（`login.php` / `logincn.php` 各一份）或统计工具页
（`benfordslaw` / `chisquaredtest` / `primenumber` …，与账号无关）。

**六个"邮箱表单"页合成一个处理函数**：它们 `require` 的是同一个
`_editemail.php`，行为差异只有"标题 / 哪些字段可编辑 / 要不要登录"三件事，
全部落在 `web/routes/account_auth.py` 的 `PAGE_CONFIG` 表里。这与分类页
"19 个壳子 → 一个注册表"是同一个判断。

| URL | 对应 PHP | 需登录 | 字段 |
| --- | --- | --- | --- |
| `/account/login` | `logincn.php` | 否 | 邮箱 + 密码 |
| `/account/register` | `registercn.php` | 否 | 邮箱 + 密码 + 重复 |
| `/account/password` | `passwordcn.php` | 是 | 邮箱**只读** + 密码 + 重复 |
| `/account/updateemail` | `updateemailcn.php` | 是 | 只有邮箱 |
| `/account/reminder` | `remindercn.php` | 否 | 只有邮箱 |
| `/account/closeaccount` | `closeaccountcn.php` | 是 | 邮箱（非管理员只读） |
| `/account/profile` | `profilecn.php` | 是 | — |
| `/account/editprofile` | `editprofilecn.php` | 是 | 五个资料字段 + status |
| `/account/logout` | **无** | — | **本地新增**，见 §四 |

## 二、迁移的前置：schema 缺两样东西

演示库的 `member` 表是 2026-10-24 为 `stockgroup` 的外键目标建的**最小集**
（`id + email + register + login + status + activity`），当时账号体系暂缓。
要跑登录，缺：

1. `member.password` 列 —— `SqlExecLogin()` 拿 `md5()` 和它比；
2. `profile` 表 —— `SqlGetProfileByMemberId()` 查它，而这张表**从来没建过**
   （PHP 源码里也没有 DDL，`SqlUpdateProfile()` 直接写 INSERT/UPDATE）。

补丁：`python/dbtools/patch_member_auth.sql`（幂等，`apply_patch.py` 执行）。
打之前先 `python dbtools/backup_db.py` —— 实测 **827 KB / 33 张表**。

补丁内容与理由：

| 变更 | 理由 |
| --- | --- |
| `member.password char(32) latin1` | 逐字抄 `MemberSql::Create()`。定长 32 是因为存 md5 十六进制 |
| `member.ip varchar(64)` | **偏离**：PHP 是 `Add_id('ip')` → INT + 外键指向 `ipaddress` 表。本地不迁 IP 统计，也没有那张表，直接存 IP 字符串 |
| `profile` 建表 | 列名与长度从 `_editprofileform.php` 的 `maxlength` 反推（64/64/128/128 + TEXT） |
| `profile.UNIQUE(member_id)` | **本地补的约束**：PHP 是"查到 UPDATE、查不到 INSERT"，本身假定 1:1；没有约束并发会插出两行，而 `SqlGetProfileByMemberId` 只取第一行 |

演示会员 `demo@palmmicro.com` 的密码由补丁设成 `demo1234` 的 md5，
条件是 `password = ''` —— 用户自己改过密码之后重跑补丁不会覆盖回去。

## 三、五处照抄（都能被"顺手修正"改坏）

### 1. 密码：校验用清洗值，落库用原值

`_loginAccount()` 判空用 `$strPassword`（`SqlCleanString($_POST['password'])`），
比对却用 `$_POST['password']` **原值**。`_changePassword()` 同理：
`md5($strPassword) == $member['password']` 用清洗值，
`SqlUpdatePasswordField(...$_POST['password'])` 用原值。

`SqlCleanString()` = `trim()` + **无条件** `stripslashes()`
（`php/url.php:127`，包着它的 `if (get_magic_quotes_gpc())` 被注释掉了，
而那个配置在 PHP 5.4 之后恒为 false —— 所以 `stripslashes()` **永远在跑**）。

后果：密码里有两个连续反斜杠时，清洗值少一个。于是 `change_password` 会出现
"校验说你新旧密码相同（拒绝），但真要写库时写的是原值"这种分裂。
**照抄**，`AuthService.change_password()` 里两处用不同变量，`test_member_auth.py`
的 `test_validation_uses_cleaned_value_but_write_uses_raw` /
`test_write_is_raw_when_cleaned_value_differs` 各钉一面。

注意末尾**单个**反斜杠不会被剥（`stripslashes` 只处理 `\\` / `\'` / `\"` / `\0`），
所以 `"abc\"` 清洗前后一样。

### 2. 关闭帐号之后不清 session

`_profile.php:404` 的 `$this->Logout();` 被注释掉了。删完会员行，session 里
还留着那个已不存在的 `SESS_ID`；下一次请求 `GetLoginEmail()` 查不到人 →
`IsAdmin()` 为假 → 页面表现为"掉登录"。

照抄。本地 `_apply_session()` 对 `closeaccount` 什么都不做，
`test_account_auth_routes.py::test_close_self_keeps_session` 断言
"session 里 `SESS_ID` 仍然是 1，但会员行已经没了"。

### 3. 重置密码会登出，且是"两层哈希"

```php
$strPassword = $strEmail . DebugGetTime();   // email + 'HH:MM:SS'
$strPassword = md5($strPassword);
$strPassword = substr($strPassword, 16);     // 取**后** 16 位
SqlUpdatePasswordField($strEmail, $strPassword);   // 落库时**再** md5 一次
```

所以"明文新密码"是 `md5(email + 时分秒)` 的后 16 个十六进制字符，库里存它的
md5。少一层就是"信里给了密码却登不进来"。

`Logout()` 在结尾 —— 已登录的人用重置功能会被踢下线。照抄。

时区取自 `php/debug.php:4` 的 `Asia/Shanghai`（`DEBUG_TIME_ZONE`）；
`AuthService` 的 `clock` 参数就是为它留的（测试用固定时间复现同一个密码）。

### 4. 改登录邮箱会把自己算作"已注册"

```php
if (filter_var_email($strEmail)) {
    if (SqlGetIdByEmail($strEmail))  $arErrMsg[] = ACCT_ERR_EMAIL_REGISTERED;
}
if ($strEmail == $member['email'])   $arErrMsg[] = ACCT_ERR_EMAIL_UNCHANGED;
```

填自己的邮箱 → 第一个判断查到的就是自己 → 两个错误码一起出现，
页面上先显示"登录电子邮件已经注册"（顶在"新旧电子邮件没有区别"前面）。
**照抄**：`test_same_email` 断言 `[2, 4]`。排除自己会让文案与生产端不一致。

注意这个判断顺序与"注册"不同：注册是"已注册 / 格式"二选一，
改邮箱是"格式合法才查重，然后无条件比相等"。

### 5. `GetWhoseDisplay()` 的残缺文案

```php
if (($str = SqlGetNameByMemberId(...)) == false)  $str = SqlGetEmailById(...);
return $str.('的');
```

名字与邮箱**都**取不到时拼出来的是 `false.'的'` → `'的'`（只有一个"的"）。
照抄，`get_whose_display()` 返回 `'的'` 而不是空串 —— 调用方（资料页标题）
会显示成"的"加"资料"，虽然难看，但那是 PHP 的行为。

## 四、与 PHP 的四条偏离

1. **不发邮件**。PHP 在注册 / 重置 / 关号后调 `EmailHtml()`。本地没有 SMTP，
   也不该在演示环境真发信。默认 `Mailer` 只把"给谁 / 主题 / 正文"记进
   `AuthResult.payload`，重置密码那条**把新密码直接显示在页面上**
   —— 否则用户永远拿不到。要接真邮件时给 `AuthService(mailer=...)` 传一个
   同签名的 callable，服务层不用改。

2. **只做中文页**。`account/` 每页都有中英两份，本地只迁中文
   （与 `/autotractor` 等页一致）。`_editemailform.php` 里
   `$bChinese = false` 那一整套分支不迁。

3. **新增 `/account/logout`**。PHP **没有**登出入口：`Logout()` 只在
   `_remindPassword()` 里被调用，`EchoLinks()` 里也没有链接。
   不加的话演示时"换个身份看看"只能清 cookie。导航栏里也顺手加了入口
   （PHP 的 `AccountMenu()` 只挂在 account 页自己的 layout 上）。

4. **成功统一跳 `/account/profile`**。PHP 是 `SwitchToSess()`（跳回进入表单
   前的页面，靠 session 记来源），本地没有这套机制。重置/关号跳登录页，
   其余跳资料页。

## 五、`AUTH_ENABLED`：补上"清 cookie 就提权"那个洞

原来 `current_admin_email()` 三级回落，最后一级是 `DEMO_ADMIN_EMAIL`
（= 管理员）。没有登录页时这无所谓；登录一跑起来它就是后门：**浏览器清掉
cookie 就是管理员**。

现在加了开关 `PALMMICRO_AUTH=1`（或 `create_app({"AUTH_ENABLED": True})`）：
启用后最后一级变成空串，没有 session 就不是管理员。

默认**关闭**，两个理由：现有一批写入口测试与冒烟脚本依赖"默认是管理员"；
演示环境开箱能用。要验真实闸门：

```powershell
$env:PALMMICRO_AUTH = "1"
$env:PALMMICRO_ADMIN_EMAIL = "woody@palmmicro.com"   # 演示库没这个会员，只能靠 env 给身份
```

`test_admin_auth.py` 两种状态都覆盖（13 例）。

**顺带说清楚一件事**：`member.password` 存的是 **md5**，不是 bcrypt/argon2。
这是照抄 PHP 的，属于安全债。本地没有换哈希算法的余地 —— 换了就登不进
生产库导出的帐号，而且 PHP 那边还在写 md5。真要升级得两边同时改。

## 六、验证

| 层 | 结果 |
| --- | --- |
| `test_member_auth.py`（服务层，patch membersql） | **39 例**：校验顺序、六个动作的成功/失败、清洗值 vs 原值、`is_valid_email` 表驱动 |
| `test_membersql.py`（sqlite 跑真 SQL） | **22 例**：读写、`ORDER BY login DESC`、read-modify-write 的 activity、关号清三处 |
| `test_account_auth_routes.py`（sqlite + test_client） | **24 例**：session 写入、guard、字段显隐、关号不清 session、重置清 session |
| `test_admin_auth.py` | **13 例**：演示回落 vs `AUTH_ENABLED`、env 覆盖、键名照抄 |
| 全量 | auto **1688 OK** / web **1324 OK**（+85）/ `compileall` OK |

真库冒烟（XAMPP MariaDB `stock_data`）：

```
200  /account/login          200  /account/register
302  /account/password       302  /account/profile      ← 未登录跳登录页
302  /account/editprofile    302  /account/logout
POST /account/login (正确密码)   → 302 /account/profile，cookie 里 SESS_ID=1 + email
POST /account/login (错误密码)   → 400，页面显示"请检查登录电子邮件帐号和密码"
POST /account/password (新旧不同) → 302，登出后用新密码能登入
POST /account/password (新旧相同) → 400
```

`member.login` 被写成 `2026-11-02`（DATE 列截断 NOW()，与 PHP 一致），
`member.ip` = `127.0.0.1`。

## 七、文件清单

| 文件 | 说明 |
| --- | --- |
| `python/dbtools/patch_member_auth.sql` | 补两列 + 建 profile（幂等） |
| `python/auto/core/membersql.py` | `sqlmember.php` 的移植：会员 11 个函数 + 资料 4 个 + 关号 |
| `python/web/services/auth_service.py` | 六个动作 + 错误码表 + `Mailer` + `get_whose_display()` |
| `python/web/routes/account_auth.py` | 9 条路由 + `PAGE_CONFIG` |
| `python/web/templates/account_auth.html` | 六合一表单 |
| `python/web/templates/account_profile.html` | 资料页 |
| `python/web/templates/account_edit_profile.html` | 编辑资料 |
| `python/web/services/admin_auth.py` | 加 `AUTH_ENABLED` / `current_member_id()` |
| `python/web/app.py` / `routes/__init__.py` | `auth_source_factory` 注入口 + 蓝图 + 导航栏的 `nav_member` |
| `python/web/tests/test_member_auth.py` / `test_membersql.py` / `test_account_auth_routes.py` / `test_admin_auth.py` | 98 例 |

## 八、这一轮**没做**的部分

按影响面排：

1. **`/account` / `/group` / `/portfolio` 仍是演示数据**。它们读的是
   `web/routes/account_demo_data.py` 里写死的样例，而不是 `stockgroup` /
   `stockgroupitem` / `stocktransaction` 三张表。SQL 层（`core/stockgroupsql.py`
   991 行）早已迁完，缺的是"按登录会员取分组"的服务与页面改造。
   **这是下一段的主要工作**，也是这一轮只算"认证内核"的原因。
2. **资料页缺两段**：博客评论（用户明确不迁）与基金申购金额
   （`fundpurchase` 表 PHP 里已注释掉一半，金额改存 `groupitemamount`，
   那一项走 `/stock/<symbol>/edit/editstockamount`）。
3. **`?email=` 查别人的资料**。PHP `Account::__construct()` 会从
   `UrlGetQueryValue('email')` 取 `strMemberId`，管理员据此看别人的资料
   （`GetWhoseDisplay()` 的"某某的"分支）。本地没接这个参数 ——
   `get_whose_display()` 已经有 `member_id` 参数，缺的是路由传值 + 权限判定。
4. **`account/visitor` / `ip.php` / `member2` 表**。IP 统计与爬虫标注
   （`IsCrawler` / `SetMalicious` / `_allowCrawler` 的 tick 逻辑）整体不迁。
5. **六个账号统计工具页**（`benfordslaw` / `chisquaredtest` / `cramersrule` /
   `linearregression` / `primenumber` / `simpletest`）—— 与基金业务无关，
   属于"娱乐/教学"页，不在迁移范围。
