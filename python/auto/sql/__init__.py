"""数据库访问与持久化层：所有"直接跟库/落盘文件打交道"的模块都放这里。

为什么单独一档：

* 这一层是**唯一**知道 SQL 与表结构的层（``stocksql`` / ``historytable`` /
  ``quotestore`` / ``fundeststore`` / ``stockhistory``），上层（``calc`` /
  ``updater`` / ``web``）只调它的方法，不写裸 SQL。归档时能一眼看出
  "改库要动哪些文件"；
* ``smaconfig`` 虽然写的是文件而不是 SQL，但它是"配置持久化"，
  与库的定位一致（同一个 PHP 源 ``php/stock/stockref.php`` 的配置读写），
  所以也归到这里；
* 只有这一层允许 import ``pymysql``，其余层不得直接依赖驱动。

依赖方向：``sql`` → ``core``（``stockdata`` 的连接配置）与 ``ref``
（``smaconfig`` 复用 ``stockref`` 的配置文件名约定）；其它层 → ``sql``。
"""

