-- 给已有的 realtimequote 表补上五档盘口列。
--
-- 为什么要有这个补丁文件：schema.sql 里是 CREATE TABLE IF NOT EXISTS，
-- 已存在的表**不会被它改动**（这是刻意的：重跑建表脚本不许动数据）。
-- 所以给旧库加列必须单独一条 ALTER。
--
-- 用法（连接配置复用 python/auto/.env，与 Web 层一致）：
--
--     cd F:\woody\woodyweb\python
--     D:\python\python.exe -B sql\apply_patch.py sql\patch_realtimequote_orderbook.sql
--
-- 本语句幂等：MySQL 没有 ADD COLUMN IF NOT EXISTS，所以重复执行会报
-- "Duplicate column name"，apply_patch.py 会把它当成"已经加过了"跳过。
-- 列可为空，旧行因此不需要回填 —— 没有盘口时按"无挂单"处理。

ALTER TABLE `realtimequote`
  ADD COLUMN `orderbook` text COLLATE utf8_unicode_ci DEFAULT NULL
  COMMENT '五档盘口 JSON：{bid_price,bid_quantity,ask_price,ask_quantity}' AFTER `fetched_at`;
