-- 给 member 表补认证列，并建 profile 表 —— 账号体系迁移的前置 schema。
--
-- 为什么需要这个补丁：演示库的 member 表是 2026-10-24 为 stockgroup 的外键
-- 目标建的"最小集"（id + email + register + login + status + activity），
-- 当时账号体系暂缓，所以没有 password；profile 表则**从来没有**建过。
-- 登录（SqlExecLogin 比 md5）与资料页（SqlGetProfileByMemberId）都跑不起来。
--
-- 用法（连接配置复用 python/auto/.env，与 Web 层一致）：
--
--     cd F:\woody\woodyweb\python
--     D:\python\python.exe -B dbtools\apply_patch.py dbtools\patch_member_auth.sql --dry-run
--     D:\python\python.exe -B dbtools\apply_patch.py dbtools\patch_member_auth.sql
--
-- 幂等：两条 ALTER 重复执行会报 "Duplicate column name"（1060），
-- apply_patch.py 当成"已经加过了"跳过；CREATE TABLE 带 IF NOT EXISTS；
-- 那条 UPDATE 带 password = '' 条件，跑第二遍时匹配 0 行。
--
-- 打补丁前先备份：python dbtools\backup_db.py（实测 826 KB / 33 张表）。

-- 1) password：PHP 的 MemberSql::Create() 是
--    `password` CHAR(32) CHARACTER SET latin1 COLLATE latin1_general_ci。
--    存的是 md5() 的 32 位十六进制小写，所以定长 32。
--    默认空串而不是 NULL：PHP 的 _changePassword 直接拿 md5 和它比，
--    遇到 NULL 会变成 NULL 比较（永远不相等），空串的行为更接近"密码错"。
ALTER TABLE `member`
  ADD COLUMN `password` char(32) CHARACTER SET latin1 COLLATE latin1_general_ci NOT NULL DEFAULT '' AFTER `email`;

-- 2) ip：PHP 那边是 Add_id('ip') —— INT UNSIGNED + 外键指向 ipaddress 表，
--    GetIpLink() 拿这个 id 再去查 ipaddress。本地不迁 IP 统计，也没有
--    ipaddress 表，所以这里直接存 IP 字符串（VARCHAR 而非 INT）。
--    这是一处**刻意的 schema 偏离**，记在 README_member_auth_migration.md。
ALTER TABLE `member`
  ADD COLUMN `ip` varchar(64) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL DEFAULT '' AFTER `activity`;

-- 3) 演示会员的登录密码：明文 demo1234，md5 = 6e9bece1914809fb8493146417e722f6。
--    只在 password 还是空串时填 —— 用户自己改过密码后重跑本补丁不会覆盖回去。
UPDATE `member`
  SET `password` = '6e9bece1914809fb8493146417e722f6'
  WHERE `email` = 'demo@palmmicro.com' AND `password` = '';

-- 4) profile：PHP 源码里**没有** DDL（SqlUpdateProfile 只写 INSERT/UPDATE），
--    列名与长度从 account/php/_editprofileform.php 的 maxlength 反推：
--    name 64 / phone 64 / address 128 / web 128 / signature 用 textarea。
--    member_id 加 UNIQUE 是本地补的约束：PHP 的 SqlUpdateProfile 是
--    "查到就 UPDATE、查不到就 INSERT"，本身假定 1:1；并发下没有约束
--    会插出两行，而 SqlGetProfileByMemberId 只取第一行。
CREATE TABLE IF NOT EXISTS `profile` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY ,
  `member_id` INT UNSIGNED NOT NULL ,
  `name` VARCHAR( 64 ) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL DEFAULT '' ,
  `phone` VARCHAR( 64 ) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL DEFAULT '' ,
  `address` VARCHAR( 128 ) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL DEFAULT '' ,
  `web` VARCHAR( 128 ) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL DEFAULT '' ,
  `signature` TEXT CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL ,
  UNIQUE ( `member_id` ) ,
  FOREIGN KEY (`member_id`) REFERENCES `member`(`id`) ON DELETE CASCADE
) ENGINE = MYISAM CHARACTER SET utf8 COLLATE utf8_unicode_ci;
