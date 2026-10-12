# PHP 源码临时改动记录

本文件记录为完成迁移而对 **PHP 源码**做过的临时改动，便于审计与还原。

---

## 1. `php/sql/sqltable.php` 库名 `n5gl0n39mnyn183l_camman` → `stock_data`

**目的**：从 PHP 权威生成建表 DDL。
`php/sql/sqltable.php` 的建表语句把库名写死成生产库：

```php
// 改前
$strQuery = 'CREATE TABLE IF NOT EXISTS `n5gl0n39mnyn183l_camman`.`'
// AlterTable / DropTable 同理
```

因此本地无法建表，需要临时改成 `stock_data`。

**改动位置**（共 3 处，方法名 → 行号按改前的文件）：
- `CreateTable()` 第 88 行
- `AlterTable()` 第 99 行
- `DropTable()` 第 108 行

**改动方式**：只替换库名字符串，其余内容（含缩进）一字未动。

**执行**：`D:\xampp\php\php.exe python/dbtools/ddl_generate.php`
（PATH 里的 `C:\php` 没有 mysqli 扩展，必须用 XAMPP 那个）
输出：14 张表的 `SHOW CREATE TABLE`，整理成 `python/dbtools/schema.sql`。

**还原**：改回 `n5gl0n39mnyn183l_camman`，并做哈希校验：

| 项目 | SHA256 |
| --- | --- |
| 改动前（原始文件） | `A5601668520D7471B3F5A2FA96A13CA465E611010B257B3A90F0D5300F87C824` |
| 还原后实测 | `A5601668520D7471B3F5A2FA96A13CA465E611010B257B3A90F0D5300F87C824` |

**校验命令**：

```powershell
(Get-FileHash php\sql\sqltable.php -Algorithm SHA256).Hash
git status --porcelain -- php        # 应为空
```

还原后 `git status -- php` 无输出，确认 PHP 侧已回到原状。

---

## 2. 没有其它 PHP 改动

除上述临时改动（已还原）外，本次迁移没有修改任何 PHP 文件。
