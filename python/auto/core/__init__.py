"""最底层：符号体系（``stocksymbol``）、数据源门面（``stockdata``）、时区（``tzutil``）。

为什么单独一档：

* 这三个模块被**几乎所有**其它层 import（``stockdata`` 有 26 个引用方），
  是依赖图的根，放在最显眼的位置，避免"底层散落在子目录里"；
* ``stocksymbol`` 是 PHP 符号规则的唯一翻译点（``php/stocksymbol.php``），
  ``tzutil`` 只做时区换算，两者都不依赖任何上层；
* ``stockdata`` 虽然带着 MySQL 连接配置，但它是**门面**（既提供
  ``MemoryFundDataSource`` 也提供 ``MySqlFundDataSource``），
  上层通过它拿数据而不直接用 ``pymysql``，所以留在基础层；
  真正的表访问在 ``sql`` 层。

注意：``stockdata`` 里的 ``.env`` 默认路径按"本文件所在目录的上一级"
（即 ``python/auto/.env``）解析，因为本文件位于 ``core/`` 子目录。
"""

