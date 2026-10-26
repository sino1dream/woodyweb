# 一键备份（`dbtools/backup_db.py`）

> 新增日期：2026-10-26
> 用途：把演示库 `stock_data` 整库 mysqldump 到 `E:\mysqldata`，文件名带时间戳。
> 这不是 PHP 迁移——是本地运维工具，PHP 侧没有对应物。
>
> 三个文件：`backup_db.py`（导出）、`verify_backup.py`（验证真能还原）、
> `backup_db.bat`（双击入口）。

## 为什么不用现成的命令行

一句话：**要一个能双击、且失败时不会骗人的备份**。

| 手敲 mysqldump 的问题 | 本脚本的处理 |
|---|---|
| 密码写在参数里，同时触发 `[Warning] Using a password on the command line interface can be insecure` | 写临时 `--defaults-extra-file`，dump 结束立刻删掉 |
| 文件名时间戳拼不出来（中文 locale 下 `%date%` 是 `2026/10/26 周一`，带斜杠） | Python `strftime` 生成 `YYYYmmdd_HHMMSS` |
| 中途断掉留下半截 sql，目录里看着"有备份"，真恢复时才发现缺表 | 三道校验（返回码 / `Dump completed` / `CREATE TABLE` 计数），任一不过就**删掉**文件 |
| 每天跑一次，目录越堆越乱 | `--keep N` 只保留最近 N 份，且只认自己的命名格式 |

## 用法

三种入口，效果一样：

```bat
:: 双击（最简单）
python\dbtools\backup_db.bat

:: 命令行
cd python && D:/python/python.exe -B dbtools/backup_db.py

:: 常用变体
D:/python/python.exe -B dbtools/backup_db.py --keep 30      # 只留最近 30 份
D:/python/python.exe -B dbtools/backup_db.py --db otherdb   # 换库
D:/python/python.exe -B dbtools/backup_db.py --dry-run      # 只看配置和输出路径
```

参数全表：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--out` | `E:\mysqldata` | 备份目录，不存在会自动建 |
| `--db` | `.env` 的 `PALMMICRO_DB_NAME`，没有则 `stock_data` | 目标库 |
| `--env` | `python/auto/.env` | 连接配置来源 |
| `--mysqldump` | 从 PATH 找，再退到 XAMPP 常见路径 | 二进制位置 |
| `--keep` | `0`（不清理） | 保留最近 N 份 |
| `--single-transaction` | 关 | 换一致性策略：事务快照代替锁表。**只对纯 InnoDB 库有效**，MyISAM 表会失去一致性 |
| `--dry-run` | — | 打印配置、输出路径与最终命令，不导出 |
| `--quiet` | — | 少打印 |

## 输出

`E:\mysqldata\stock_data_20261026_151917.sql` —— `<库名>_<YYYYmmdd_HHMMSS>.sql`。
时间戳定宽，按文件名排序就是时间序。

实测（演示库，30 张表）：**810.89 KB / 30 张表 / 0.3–0.4 秒**。

## 备份文件可信度怎么证明的

不是"跑通了就算"——当天把导出的 sql **真的导进一个临时库**验证：

| 校验项 | 结果 |
|---|---|
| 表数 | 30 = 源库 30 |
| **逐表 `CHECKSUM TABLE`** | **30 张表全部一致，零差异** |
| 中文（`HEX(name)` 抽查 `SZ161130`） | 与源库逐字节相同 |
| `holdings` 行数与 `SUM(ratio)` | 37 / 200.03，与源库一致 |
| 临时库清理 | `backup_verify*` 残留 0 |

备份脚本只能保证"dump 跑完没报错"，**不能保证"导回去是好的"** —— 后者要真的
导一次才知道。所以配了个专门的验证器 `dbtools/verify_backup.py`：把备份灌进
临时库 → 逐表 `CHECKSUM TABLE` → `DROP`。

```bat
cd python
D:/python/python.exe -B dbtools/verify_backup.py "E:/mysqldata/stock_data_20261026_152033.sql"
```

输出示例（好备份）：

```
导入完成 -> backup_verify_24996
  表数                 : 源库 30 / 还原 30
  CHECKSUM 不一致的表  : 无（30 张表逐对比过）
  中文 HEX 抽查        : 一致
  临时库已删除         : 残留 backup_verify* 库数 = 0
[通过] E:/mysqldata/stock_data_20261026_152033.sql
```

坏备份（拿真备份截断到 200 KB 试过）会明确判死，退出码 **1**：

```
  导入                 : 失败（返回码 1）
    ERROR 1064 (42000) at line 644: You have an error in your SQL syntax; ...
  临时库已删除         : 残留 backup_verify* 库数 = 0
[不通过] auto/_scratch/_truncated.sql
```

临时库名带 pid（`backup_verify_<pid>`），`finally` 里必 DROP，跑完还会再查一次
残留数 —— 不留残库。

## 怎么恢复

dump **不含** `CREATE DATABASE` / `USE`（脚本没加 `--databases`），所以目标库必须
先存在。这一点是刻意的：`--databases` 会让"库已存在"时变成往旧数据上追加，
主键冲突比干净失败更难收拾。

```bat
:: 1) 建一个空库（换个名字，别直接盖掉 stock_data）
mysql -u root -p -e "CREATE DATABASE stock_data_restore DEFAULT CHARACTER SET utf8mb4"

:: 2) 导入
mysql -u root -p --default-character-set=utf8mb4 stock_data_restore < E:\mysqldata\stock_data_20261026_151917.sql

:: 3) 确认表数
mysql -u root -p -N -B -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='stock_data_restore'"
```

## 两个环境事实（换机器会变）

1. **这不是 MySQL，是 MariaDB 10.4.21**（XAMPP 自带），mysqldump 版本
   `Distrib 10.19`，路径 `D:\xampp\mysql\bin\`。
   所以参数集里**没有** `--no-tablespaces` 和 `--column-statistics` ——
   这两个是 MySQL 5.7.31+/8.0 才有的，照抄 MySQL 教程加进去会直接
   `unknown option` 退出。
2. **`.env` 里没有 `PALMMICRO_DB_NAME`**，只有 HOST / PORT / USER / PASSWORD，
   库名由代码默认 `stock_data`。脚本沿用同一套默认值。

## 一致性策略：默认锁表，不是 `--single-transaction`

这一条差点写错，值得单独说。**演示库 30 张表里 28 张是 MyISAM，只有 2 张 InnoDB**
（2026-10-26 实测）：

```sql
SELECT engine, COUNT(*) FROM information_schema.tables
WHERE table_schema='stock_data' GROUP BY engine;
-- InnoDB  2
-- MyISAM  28
```

MyISAM **不支持事务**，所以 `--single-transaction` 对它完全无效；更糟的是
mysqldump 的这个选项会**顺带关掉默认开启的 `--lock-tables`** —— 于是 28 张表在
导出期间既没有事务快照、也没有表锁，期间 daemon 或手写脚本一改数据，拿到的
备份就跨表不一致，**而且不会报任何错**。

所以默认走 `--lock-tables`：mysqldump 一次性 `LOCK TABLES t1 READ, t2 READ, ...`，
所有表停在同一个时刻。代价是导出期间表只读 —— 本地库 0.3 秒，可以忽略。

纯 InnoDB 的库、或备份耗时长不能阻塞写入时，加 `--single-transaction` 切换：

```bat
D:/python/python.exe -B dbtools/backup_db.py --single-transaction
```

脚本会在输出里打印当前用的是哪种，不用猜。

## 边界（刻意的）

* 只备份一个库，不做 `mysql` 系统库、不做增量、不做压缩；
* `--lock-tables` 保证跨表一致，但**导出期间表是只读的**（0.3 秒）；
* 密码来自 `.env`，脚本不存也不打印密码。
