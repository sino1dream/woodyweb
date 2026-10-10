# woodyweb 协作约定

本文件由 DSH 自动加载为工作区指令，对本目录下的所有会话生效。

## PHP → Python 迁移约定

1. **所有新写的 Python 代码必须使用中文注释。**
   - 模块 docstring、类 docstring、函数/方法 docstring 用中文；
   - 行内注释用中文；
   - 不要新增英文注释。已有的英文注释在被改动的代码块内可以顺手改成中文，
     但不要为了改注释而大面积重写无关代码。

2. 注释要写“为什么”，不要复述代码在做什么。涉及与 PHP 行为差异、
   边界条件、魔数来源的地方必须说明原因，并注明对应的 PHP 文件与函数名。

3. 命名与接口保持现有约定：
   - `python/auto` 是平铺模块（非包），模块之间直接 `import`，测试需在该目录内运行；
   - 函数与变量用 snake_case，同时对 PHP 原名保留 camelCase 别名（见
     `python/auto/stocksymbol.py` 的别名区）；
   - PHP 返回 `false` 的字符串类方法映射为 `None`，布尔类方法映射为 `False`。

4. 迁移的每一步都要：
   - 写清对应的 PHP 源文件路径与原函数/类名；
   - 补充 `python/auto/test_*.py` 下的单元测试；
   - 在 `python/auto/README_*_migration.md` 记录范围、取舍与已知偏差；
   - 有条件时用真实 PHP 做逐项对照验证（参考
     `python/auto/_php_port_compare.py` 与 `_php_port_check.php`）。

参考文档：`python/WEB_MIGRATION_PLAN.md`（迁移总计划）、
`python/auto/README_stocksymbol_migration.md`（已完成的第 1 步样例）。
