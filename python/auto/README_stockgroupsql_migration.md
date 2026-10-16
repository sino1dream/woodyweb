# stockgroup / stockgroupitem 迁移 README

对应 PHP：``php/sql/sqlstockgroup.php``（338 行）+ 其依赖的
``sqlkeystring.php`` / ``sqlkeytable.php`` / ``sqlkey.php`` / ``sqltable.php``
/ ``_sqlcommon.php``，以及后台页面 ``woody/res/php/_editstockgroup.php``、
``_editgroupform.php``、``_submitgroup.php``。

Python 交付物：

* ``auto/core/stockgroupsql.py`` —— 数据层（``StockGroupSql`` /
  ``StockGroupItemSql`` + 模块级函数）；
* ``dbtools/seed_stock_group_tables.py`` —— 建五张表 + 演示种子；
* ``auto/test_stockgroupsql.py`` —— 32 个单测；
* ``auto/_scratch/_php_stockgroup_check.php`` + ``_php_stockgroup_compare.py``
  —— 与真实 PHP 的 SQL 逐例对照；
* ``web/routes/admin.py`` + ``web/templates/admin_*.html`` —— 后台管理页。

## 一、迁了什么、没迁什么

| PHP 侧 | Python | 说明 |
| --- | --- | --- |
| ``StockGroupSql``（KeyStringSql） | :class:`core.stockgroupsql.StockGroupSql` | 会员 + 组名的键值表 CRUD |
| ``StockGroupItemSql``（KeyTableSql） | :class:`core.stockgroupsql.StockGroupItemSql` | 分组成员 CRUD + diff 更新 |
| 模块函数 11 个 | 同名 snake_case 函数 | 见下 |
| ``GroupItemAmountSql`` / ``GroupItemExtraSql`` | **未迁方法** | 后台页不用；表照建（对称） |
| ``$trans_sql``（StockTransactionSql） | **未迁** | 用户明确 stocktransaction 缓 |
| 账号登录（StockAccount / sqlmember） | **未迁** | 演示环境固定 member_id=1，见 §5 |

模块函数对照（PHP → Python）：

| PHP | Python |
| --- | --- |
| ``SqlGetStockGroupId`` | ``sql_get_stock_group_id`` |
| ``SqlGetStockGroupItemId`` | ``sql_get_stock_group_item_id`` |
| ``SqlGetStocksArray`` | ``sql_get_stocks_array`` |
| ``SqlUpdateStockGroup`` | ``sql_update_stock_group`` |
| ``SqlDeleteStockGroupItemByGroupId`` | ``sql_delete_stock_group_item_by_group_id`` |
| ``SqlDeleteStockGroupByMemberId`` | ``sql_delete_stock_group_by_member_id`` |
| ``SqlGetMyStockGroupItemId`` | ``sql_get_my_stock_group_item_id`` |
| ``_getStockIdArray``（_submitgroup.php） | ``get_input_stock_id_array`` |
| ``in_arrayAll`` | ``is_group_name_reserved`` |
| ``'@'.前缀``（_submitgroup.php:72） | ``prefix_reserved_group_name`` |
| ``SqlGetStockGroupItemSymbolArray`` | **未迁** | 要 A/H/US 三Bucket 分类，迁 symbols 后补 |
| ``SqlDeleteStockGroupItemByStockId`` | **未迁** | 删股票前的引用检查，删股票功能未做 |

## 二、表结构（五张，``dbtools/seed_stock_group_tables.py`` 建）

演示库里这五张表**从来不存在**（``check_schema.py`` 的 14 张核心表里也
没有）。DDL 逐点对应 PHP：

* ``stockgroup`` = ``KeyStringSql::Create()``：``id`` + ``member_id``
  （``Add_id(TABLE_MEMBER)``）+ ``groupname VARCHAR(64)`` +
  ``UNIQUE (groupname, member_id)``；
* ``stockgroupitem`` = ``SqlCreateStockGroupItemTable()`` 注释版：
  ``id`` + ``stockgroup_id`` + ``stock_id`` + ``quantity`` + ``cost``
  + ``record`` + ``INDEX (record)`` + ``UNIQUE (stock_id, stockgroup_id)``；
* ``member`` = **最小集**（``id/email/register/login/status/activity``）——
  PHP 的 ``MemberSql`` 管的是 ``member2`` 表（遗留命名），而 FK 指向
  ``member``；我们只要 FK 有目标；
* ``groupitemamount`` / ``groupitemextra`` = ``IntSql`` 两张附表，
  后台不用但建出来保持对称（``groupitemextra`` 的键列叫 ``record``，
  不是默认的 ``num`` —— ``GroupItemExtraSql`` 构造时传了第二个参数）。

引擎 ``MYISAM`` + ``utf8_unicode_ci``，与 PHP DDL 和演示库现有 25 张表
一致。**FK 因此不生效**（MyISAM 忽略 FK），删分组靠数据层手工先删
item —— 与 PHP 行为逐字一致。

## 三、与 PHP 的已知偏差（逐条）

1. **构造即建表 → 显式建表**。PHP ``TableSql::__construct()`` 会跑
   ``CREATE TABLE IF NOT EXISTS``；Python 侧收敛到
   ``dbtools/seed_stock_group_tables.py``。web 是长进程，不该每次构造
   都发 DDL。
2. **INSERT 省 id 列**。PHP ``InsertArray`` 无 id 键时拼
   ``INSERT INTO t(id, ...) VALUES('0', ...)``（MyISAM 自增惯例）；
   Python 直接省略 id 列。功能等价（对照脚本标 equivalence）。
3. **COUNT 别名** ``as total`` → ``AS n``。纯别名。
4. **删除无级联交易**。PHP 删 group item 前先
   ``$sql->trans_sql->Delete($strId)``；stocktransaction 未迁，
   ``sql_update_stock_group`` 移除分支与
   ``sql_delete_stock_group_item_by_group_id`` 都跳过那一步。
   后果：删成员不删交易（表不存在，无从删）。交易体系迁回来后要补。
5. **无登录态**，见 §5。
6. ``SqlGetStockGroupItemSymbolArray`` / ``SqlDeleteStockGroupItemByStockId``
   未迁（见 §一表末两行）。

## 四、PHP 逐例对照（SQL 级）

``auto/_scratch/_php_stockgroup_compare.py``：PHP 侧
（``_php_stockgroup_check.php``，``_sqlcommon.php`` 整个 require、
``UpdateArray`` / ``SqlGet*TableData`` 拼装句从源码抠、执行句换成
return）对 Python 侧实际发出的 SQL（内存 fake 记录后按 PHP 风格嵌回
参数）。

**结果：31 例，严格一致 26、语义等价 5（§三 的 2/3）、差异 0。**

对照抓出来的真偏差（已修）：``get_all`` 的 LIMIT 分支原来写成
"start/num 都非零才加 LIMIT"，漏掉 ``_SqlBuildLimit(0, n)`` → ``LIMIT n``
这一支，且 ``LIMIT s , n`` 少个空格（PHP 是 ``' , '``）。修了 core 与
单测（``test_get_all_limit_branches``）。这正是 SQL 拼装层值得逐例对照
的原因——功能测试对"少个 LIMIT"完全不敏感。

## 五、账号登录：未迁，固定 member_id

PHP 的后台页（``editstockgroupcn.php``）入口是
``new StockAccount('edit')``：``Run()`` 里 ``if ($strLoginId =
GetLoginId())`` 才执行 ``Process``——**未登录时提交静默不执行**；
``IsAdmin()`` 比对 ``ADMIN_EMAIL``；``IsGroupReadOnly()`` 管别的小组。

Python 后台页（``web/routes/admin.py``）**不接登录态**：member 表只有
一条演示会员（id=1），所有写操作都以 ``DEMO_MEMBER_ID = 1`` 归属。
页面顶部有常驻说明。这是演示环境的取舍——登录体系（session + 密码 +
注册）是独立大项，与 stocktransaction 同批暂缓。若将来迁：
``SqlExecLogin``（email + md5(password) 比对）→ Flask session
（``SESS_ID``）→ ``IsAdmin`` 用环境变量配管理员邮箱，
``StockAccount('edit')`` 的守卫对应路由层的 ``login_required`` 装饰器。

## 六、后台管理页（``/admin/*``）

路由清单（照 PHP 两个页面的形状）：

| 路由 | PHP 对应 | 说明 |
| --- | --- | --- |
| ``GET /admin/groups`` | （新）列表 | 全部分组 + 成员数 + 编辑/删除链接 |
| ``GET /admin/group/new`` | ``editstockgroupcn.php?new=`` | 新建表单（组名预填 ``@符号``） |
| ``GET /admin/group/<id>`` | ``editstockgroupcn.php?edit=`` | 编辑表单（组名 + 成员符号多行） |
| ``POST /admin/group/submit`` | ``submitgroup.php`` | 按 submit 值分派：新建/编辑/删除 |

与 PHP 的差异：

* PHP 删除走 ``?delete=<id>``（GET 副作用），我们用 POST——CSRF 友好，
  记为**有意偏差**；
* PHP 的 ``_debugStockGroup`` 用 ``trigger_error`` 记日志，我们给页面
  flash 反馈（新增行为）；
* 组名重名保护（``in_arrayAll``）照搬：符号表命中时加 ``'@'`` 前缀；
  编辑分支对已有重名组保留旧名（PHP ``_submitgroup.php:59`` 同款）。

## 七、测试与验证

* ``auto/test_stockgroupsql.py``：32 例，覆盖 CRUD、diff 方向、
  字符串键、``record==0`` 过滤、LIMIT 三分支、符号解析（切分/大小写/
  ref 兜底）、重名保护；
* ``web/tests/test_admin.py``：11 例，覆盖列表/新建（含符号跳过报数、
  重名加 ``@``）/编辑（diff、旧名保留）/删除（先成员后组），不连库
  （``admin_source_factory`` 注入内存 fake）；
* PHP 逐例对照 31 例 0 差异（§四）；
* 演示库实跑：``dbtools/seed_stock_group_tables.py --apply`` 建五表 +
  1 会员 + 2 分组 + 5 成员；读路径（``sql_get_stocks_array`` /
  ``get_group_id`` / ``count`` / ``count_by_stock_id``）全部验证；
* 后台页端到端（真库）：新建「端到端测试组」（成员 QQQ/SPY + 跳过
  NOSUCH 有提示）→ 编辑（小写 ``sz161125`` 归一、diff 移除 SPY）→
  删除（先成员后组），随后库恢复为 2 个演示分组。

全量：auto 1248 + web 734 全绿，``compileall`` 通过。

## 八、后续（未迁）

* ``stocktransaction`` 三件套（表 + Sql 层 + 后台交易管理）——用户明确缓；
* ``groupitemamount`` / ``groupitemextra`` 的读写（资金汇总段落用时）；
* 登录态（§五）；
* ``SqlGetStockGroupItemSymbolArray`` 的 A/H/US 分类（等 stockgroup 三表
  齐 + symbols 迁完）。
