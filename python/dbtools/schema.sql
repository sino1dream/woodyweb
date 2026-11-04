-- Palmmicro 站点核心表结构
--
-- 来源：由 PHP 建表逻辑权威生成，不是手写。
--   生成方式见 python/dbtools/ddl_generate.php：临时把 php/sql/sqltable.php 里
--   硬编码的库名改成 stock_data，跑一次表类构造（TableSql 构造函数里会
--   CREATE TABLE IF NOT EXISTS），再用 SHOW CREATE TABLE 导出。
--   生成后已把 sqltable.php 还原，SHA256 校验一致。
--
-- 与 PHP 保持一致的两点：
--   1. 引擎固定 MYISAM（PHP 的 sqltable.php 写死 ENGINE = MYISAM）。
--      MYISAM 会忽略 FOREIGN KEY，所以 PHP 原本写的 FK 在建表时被 MySQL 丢弃，
--      只留下 KEY 索引 —— 这里保留 MySQL 实际建出来的样子，不臆造外键。
--   2. 字符集 utf8 / utf8_unicode_ci；stock.symbol 单独用 latin1，与 PHP 一致。
--
-- 所有语句都是 IF NOT EXISTS，可重复执行；不会删除已有数据。

SET NAMES utf8;

-- ---------------------------------------------------------------------------
-- 符号主表：几乎所有查询的入口，symbol 唯一
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `stock` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  -- 交易符号，如 SZ161130、^HSI、hf_ES；PHP 用 latin1 存放
  `symbol` varchar(32) CHARACTER SET latin1 COLLATE latin1_general_ci NOT NULL,
  -- 显示名（可能含中文），utf8
  `name` varchar(128) COLLATE utf8_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `symbol` (`symbol`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 基金仓位：stockdata.py 把 close 当仓位用（get_position）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `fundposition` (
  `id` int(10) unsigned NOT NULL,
  `close` double(13,6) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 最近一次校准值快照：对应 stockdata.py 的 get_last_calibration
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `lastcalibration` (
  `id` int(10) unsigned NOT NULL,
  `close` double(13,6) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 数据日期：每只符号只存一行，记录最新可用日期
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `holdingsdate` (
  `id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

CREATE TABLE IF NOT EXISTS `stockhistorydate` (
  `id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 历史行情：close 精度 3 位（价格），adjclose 精度 6 位（复权）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `stockhistory` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `stock_id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  `close` double(10,3) NOT NULL,
  `volume` bigint(20) unsigned NOT NULL,
  `adjclose` double(13,6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `date` (`date`,`stock_id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 基金净值历史：对应 get_netvalue_close
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `netvaluehistory` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `stock_id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  `close` double(13,6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `date` (`date`,`stock_id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 校准历史：基金估值的关键表
--   close = 校准值（如基金净值 / 标的指数点位 的比例），num = 当日取样次数
--   对应 stockdata.py 的 get_calibration_now / get_calibration_from_date
--   注意 PHP 的表名是 calibrationhistory（不是 calibration）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `calibrationhistory` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `stock_id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  `close` double(13,6) NOT NULL,
  `time` time NOT NULL,
  `num` int(10) unsigned NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `date` (`date`,`stock_id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 期货理论溢价：DailyCloseSql('futurepremium')（php/sql/sqldailyclose.php:270）
--
-- 这是 ``smaparagraph`` 的 ``EchoFutureSmaParagraph()`` 要用的一张表
-- （``php/ui/smaparagraph.php:192``）。它存的是**年化利率的百分点**：
--
-- .. code-block:: php
--
--     $fRate = floatval($strClose) / 100.0;     // 5.00625 → 0.0500625
--     return 1.0 + $fRate * $iDay / 365.0;
--
-- 即"离交割还有几天，按这个年化利率算下来该有多少溢价"。
-- 一个符号一行（``GetCloseNow`` / ``GetDateNow`` 都取**最近一条**），
-- 所以这里跟 ``fundest`` 一样只建索引，不做历史归档。
--
-- 列结构与 ``fundest`` 一致（DailyCloseSql 的建表是同一套），
-- 没有 ``time`` 列 —— 利率一天一个值，时刻没有意义。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `futurepremium` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `stock_id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  `close` double(13,6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `date` (`date`,`stock_id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 估值缓存：DailyTimeSql('fundest')，比 calibrationhistory 少一个 num 列
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `fundest` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `stock_id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  `close` double(13,6) NOT NULL,
  `time` time NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `date` (`date`,`stock_id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 季报文本：close 是 varchar，存正文
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `quarterreport` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `stock_id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  `close` varchar(8192) COLLATE utf8_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `date` (`date`,`stock_id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 场内份额历史（单位：万份）—— 对应 PHP SharesHistorySql
--   **注意**：PHP 里表名是 shareshistory，不是 fundshares
--   （php/sql/sqldailyclose.php:303 `new DailyCloseSql('shareshistory')`）。
--   形状与 netvaluehistory 完全一致，因为它同样继承 DailyCloseSql。
--   迁移「基金场内份额」页面时新增，见 python/auto/historytable.py
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `shareshistory` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `stock_id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  `close` double(13,6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `date` (`date`,`stock_id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 指数移动均线：表名带天数，StockEmaSql(50) / StockEmaSql(200)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `stockema50` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `stock_id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  `close` double(13,6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `date` (`date`,`stock_id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

CREATE TABLE IF NOT EXISTS `stockema200` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `stock_id` int(10) unsigned NOT NULL,
  `date` date NOT NULL,
  `close` double(13,6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `date` (`date`,`stock_id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 持仓明细：stock_id 是基金，holding_id 是其持有的标的
--   持仓型基金的估值（fundcalc.calc_with_holdings）需要这张表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `holdings` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `stock_id` int(10) unsigned NOT NULL,
  `holding_id` int(10) unsigned NOT NULL,
  `ratio` double(13,6) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 基金/标的配对：id 是基金，stock_id 是被跟踪的标的
--   对应 stockdata.py 的 get_fund_pair_id（WHERE id = ? 取 stock_id）
--   注意：PHP 的 PairSql 用 'stock' 前缀，所以"值列"也叫 stock_id，
--   没有 fund_id 这种列名。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `fundpair` (
  `id` int(10) unsigned NOT NULL,
  `stock_id` int(10) unsigned NOT NULL,
  PRIMARY KEY (`id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 基金 -> 跟踪标的（est 符号）的**库里覆盖表**，2026-11-03 加
--
-- 为什么需要它：est 映射一直是**硬编码**的（``calc/fundcalc.py`` 的
-- ``qdii_est_symbol_map()`` 等，逐行照抄 PHP ``qdiiref.php:19-77``）。
-- 代码里的表只有作者当初配过的那批，页面「跟踪代码」列因此对
-- 另一些基金永远是空的 —— 而且 est 为空不只是少一格显示：
-- 估值链在 ``_get_est_symbol()`` 就断了，官方EST / EST日期 / 溢价
-- 整组全空，页面上看像"没数据"，其实是没映射。
--
-- 优先级：**本表 > 代码里的硬编码表**（删行即回退到硬编码）。
-- 表里只放"代码没有、需要补"的那些；已有映射想覆盖也允许，
-- 但改之前先确认新旧值是否同一口径（指数 vs ETF 差几十倍）。
--
-- ``est_symbol`` 存**符号文本**而不是 stock.id：允许先配后补数据
-- （标的还没入库时不至于写不进去），代价是可能写进拼错的符号 ——
-- 写入方必须做存在性体检，跑批时也要能一眼看出哪个符号在 stock 表里不存在。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `fundestmap` (
  `id` int(10) unsigned NOT NULL COMMENT '基金在 stock 表的 id',
  `est_symbol` varchar(16) NOT NULL COMMENT '跟踪标的符号，如 GLD / KWEB / ^NDX',
  PRIMARY KEY (`id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 期货与指数的配对（实时 EST 用）
--
-- 对应 PHP ``FundPairReference`` 里那个 ``realtime_callback`` + 它的自动校准：
--
--   * ``CheckAdjustFactorTime()`` —— 期货与指数两个行情的**日期与时刻相同**时，
--     才认为可以校准（即"美股收盘那一刻"两者对齐）；
--   * ``AverageCalibraion()`` —— 那一刻写 ``校准值 = QdiiGetCalibration(期货价, 汇率, 指数价)``
--     （``WriteDailyAverage`` 按平均值累加，同一天多次校准不会互相覆盖）；
--   * 之后实时 EST 就用 ``期货实时价 × 校准比例``。
--
-- 为什么单独一张表而不是复用 ``fundpair``：语义不同（这里是"期货跟踪哪个指数"，
-- 不是"基金跟踪哪个标的"），而且 ``fundpair`` 在 demo 库里只有 id + stock_id
-- 两列（PHP ``PairSql`` 的真实形状是 id + stock_id + pair_id）。
--
-- 本项目实测确认（2026-10-07）：``hq.sinajs.cn/list=hf_NQ`` 可用且字段与
-- PHP ``_onSinaFuture($ar)`` 的映射一致（``[0]`` 价、``[4]`` 高、``[5]`` 低、
-- ``[6]`` 时间、``[7]`` 昨收、``[8]`` 开、``[12]`` 日期、``[13]`` 名称）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `futurespair` (
  `id` int(10) unsigned NOT NULL,
  `stock_id` int(10) unsigned NOT NULL,
  PRIMARY KEY (`id`),
  KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- 实时行情快照（Python 侧新增，PHP 没有这张表）
--
-- 为什么需要它：实时行情是「后台定时抓 + 页面直读」。如果只放在进程内存里，
-- 抓取进程写的缓存**Web 进程读不到**（分进程部署或多 worker 时必然踩到）。
-- 放进库里，两边就共享同一份最新报价。
--
-- 只保留每个符号的最新一条（id 即 stock_id），不存历史：
-- 历史收盘价属于 stockhistory，两者口径不同，不要混在一张表里。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `realtimequote` (
  -- 即 stock.id，一个符号一行
  `id` int(10) unsigned NOT NULL,
  -- 库里符号，如 SZ161130，便于直接按符号查
  `symbol` varchar(32) CHARACTER SET latin1 COLLATE latin1_general_ci NOT NULL,
  -- 数据源代码，如 sz161130；回读时能看出源是否一致
  `source_symbol` varchar(32) CHARACTER SET latin1 COLLATE latin1_general_ci NOT NULL,
  -- 最新价 / 昨收：用 VARCHAR 与 PHP 的"原始字符串"口径保持一致
  `price` varchar(32) COLLATE utf8_unicode_ci NOT NULL,
  `prev_price` varchar(32) COLLATE utf8_unicode_ci NOT NULL,
  -- 数据源返回的名称
  `name` varchar(128) COLLATE utf8_unicode_ci NOT NULL,
  -- 行情自带的日期与时间（不是写入时间）
  `quote_date` date NOT NULL,
  `quote_time` time NOT NULL,
  -- 数据源标识（sina 等），换源后便于排查
  `source` varchar(32) CHARACTER SET latin1 COLLATE latin1_general_ci NOT NULL,
  -- 抓取时刻，页面据此判断这条行情有多新
  `fetched_at` datetime NOT NULL,
  -- 五档盘口（JSON 文本），**可为空** —— 见下面的专门说明
  `orderbook` text COLLATE utf8_unicode_ci,
  -- 当日均价（VWAP）。只有国内商品期货（``nf_``）有值，见下面的专门说明
  `vwap` varchar(32) COLLATE utf8_unicode_ci NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `symbol` (`symbol`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- ---------------------------------------------------------------------------
-- realtimequote.orderbook：五档盘口为什么是**一列 JSON** 而不是 20 列
--
-- PHP 的 ``_onSinaDataCN()``（php/stock/stockref.php:316）把盘口存进四个平行数组：
--
-- .. code-block:: php
--
--     for ($i = 10; $i < 20; $i += 2) {
--         $this->arBidQuantity[] = $ar[$i];
--         $this->arBidPrice[]    = $ar[$i + 1];
--         $this->arAskQuantity[] = $ar[$i + 10];
--         $this->arAskPrice[]    = $ar[$i + 11];
--     }
--
-- 也就是"买一到买五 / 卖一到卖五"，共 20 个值。拆成 20 列会碰到三件麻烦事：
--
-- 1. **缺档是有语义的**。PHP 的 ``GetAskQuantity($i)`` 在数组没那么长时返回
--    ``false``，而 ``php/ui/tradingparagraph.php`` 正是靠这个假值决定"这一档
--    **整行不渲染**"。摊平成列之后，"没有这一档"只能表示成空串或 NULL，
--    读回来还得再判断一次，反而绕；
-- 2. UPSERT 语句会膨胀到 30 列，而本表有 4 个读取入口
--    （``quotestore`` / ``quoteupdater`` / ``stockdata`` / ``futurescli``），
--    每加一个入口就多一处"漏字段"的风险；
-- 3. 档位个数是数据源说了算（A 股五档，将来换源可能十档），
--    一列 JSON 不用改表。
--
-- 所以这里存 JSON，值**保持数据源给的字符串**（与 PHP 一样不做数值化，
-- "0" 与 0.0 的差别在渲染层有用）：
--
-- .. code-block:: json
--
--     {"bid_price": ["4.79", "4.78"], "bid_quantity": ["1200", "3800"],
--      "ask_price": ["4.80", "4.81"], "ask_quantity": ["900", "1500"]}
--
-- 编解码只有一份实现：``quotestore.encode_orderbook()`` / ``decode_orderbook()``。
-- 老库（本列还不存在）与坏 JSON 都按"没有盘口"处理，不影响原有字段。
--
-- 加列用 ``sql/patch_realtimequote_orderbook.sql`` —— 本文件是
-- ``CREATE TABLE IF NOT EXISTS``，已存在的表不会被它改动。
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 中间层估值数值（Python 侧新增，PHP 没有这张表）
--
-- 背景：PHP 的 yahoostock.php 把中间层标的（QQQ/SPY/XOP/INDA…）的当日数值
-- 直接写进 netvaluehistory，导致那张表混了两种口径：
--   · 基金**官方单位净值**（FundReference::GetOfficialNetValue → php/stock.php）
--   · 中间层**估值序列**（YahooUpdateNetValue → _yahooStockGetData）
-- 分开之后语义清晰，溢价率也不会被中间层数据污染。
--
-- 关于量级（重要，别再被绕住）：
--   php/stock/qdiiref.php:193
--       fFactor = QdiiGetCalibration(est, cny, netvalue)
--               = est_value * cny / net_value
--   即 calibrationhistory 里的值是**从"源值 + 净值"反算出来的派生量**，
--   而估算公式 est_value * cny / fFactor 恒等于净值，
--   所以**与源用量纲无关**。换源时让 calibrationhistory 跟着重算即可
--   （AdjustFactor 本来就这么做），不需要硬编码换算系数。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `estquote` (
  -- 即 stock.id，一个中间层标的一行
  `id` int(10) unsigned NOT NULL,
  `symbol` varchar(32) CHARACTER SET latin1 COLLATE latin1_general_ci NOT NULL,
  -- 数据源代码，如 gb_qqq；换源后便于排查
  `source_symbol` varchar(32) CHARACTER SET latin1 COLLATE latin1_general_ci NOT NULL,
  -- 中间层数值（新浪 gb_* 就是真实市价）
  `close` varchar(32) COLLATE utf8_unicode_ci NOT NULL,
  `prev_close` varchar(32) COLLATE utf8_unicode_ci NOT NULL,
  `quote_date` date NOT NULL,
  `quote_time` time NOT NULL,
  `source` varchar(32) CHARACTER SET latin1 COLLATE latin1_general_ci NOT NULL,
  `fetched_at` datetime NOT NULL,
  -- 当日均价（VWAP）：只有国内商品期货（nf_）有，来自新浪行情的第 28 个字段。
  -- 商品期货基金按均价计净值，官方 EST 取它而不是现价（fundpairref.php:242）；
  -- 其他标的一律空串。老库用 dbtools/patch_estquote_vwap.py 补这一列。
  `vwap` varchar(32) COLLATE utf8_unicode_ci NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `symbol` (`symbol`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;
