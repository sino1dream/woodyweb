<?php
// ---------------------------------------------------------------------------
// stockgroup / stockgroupitem Sql 层的参考 SQL（与 Python 逐例对照用）
//
//   D:\xampp\php\php.exe -n -d display_errors=stderr _php_stockgroup_check.php
//
// ## 对照什么
//
// Python 侧 ``core/stockgroupsql.py`` 的每个方法最终都要发一条 SQL。
// PHP 侧的 SQL 来自三类纯函数（都不连库）：
//
//   1. ``php/sql/_sqlcommon.php`` 整个文件 —— WHERE/LIMIT 拼装，无依赖；
//   2. ``php/sql/sqltable.php`` 的 ``Add_id`` / ``Remove_id`` / ``UpdateArray``
//      —— 前者无依赖，后者内部调 ``_query`` 会连库，所以**从源码抠**；
//   3. ``php/sql.php`` 的 ``SqlGetTableData`` 等最终拼装 —— 同样抠。
//
// 抠函数体的手法同 ``_php_fundpairref_check.php``：括号配对找函数尾，
// 把执行语句（``_query`` / ``mysqli_query``）换成 ``return``。
//
// ## 为什么值得对照
//
// 这层全是字符串拼装，改一个空格、漏一个 LIMIT、WHERE 顺序颠倒，
// 功能测试照样绿（MyISAM 不在乎 ``id = '7' LIMIT 1`` 还是不限），
// 但逐例对照能抓住。已知两处**语义等价但形态不同**（Python 侧有意
// 为之，compare 脚本里标注 equivalence 而不是硬比）：
//
//   * INSERT：PHP 显式拼 ``id`` 列并插 ``'0'``（MyISAM 自增惯例），
//     Python 省掉 id 列让 MySQL 自增；
//   * COUNT 别名：PHP 是 ``count(*) as total``，Python 是 ``COUNT(*) AS n``。
//
// **注意**：本文件不能出现 PHP 结束标签（注释里也不行）——
// 解析器会在那里结束脚本，之后的内容被当纯文本打到 stdout 污染 JSON。
// ---------------------------------------------------------------------------

$phpDir = realpath(__DIR__ . '/../../../php');
if ($phpDir === false) { fwrite(STDERR, "找不到 php 目录\n"); exit(2); }

require $phpDir . '/sql/_sqlcommon.php';
require $phpDir . '/sql/sqltable.php';

//: 从源码里抠一个函数体：括号配对找函数尾
function ExtractFunction($path, $head)
{
    $text = file_get_contents($path);
    if ($text === false) { fwrite(STDERR, "读不到 $path\n"); exit(2); }
    $start = strpos($text, $head);
    if ($start === false) { fwrite(STDERR, "找不到函数头: $head\n"); exit(2); }
    // 从函数头的第一个 '{' 起括号配对
    $i = strpos($text, '{', $start);
    if ($i === false) { fwrite(STDERR, "函数体没有 {\n"); exit(2); }
    $depth = 0;
    $len = strlen($text);
    for ($j = $i; $j < $len; $j++)
    {
        if ($text[$j] === '{')	$depth++;
        else if ($text[$j] === '}')
        {
            $depth--;
            if ($depth == 0)
            {
                return substr($text, $i, $j - $i + 1);
            }
        }
    }
    fwrite(STDERR, "括号没配对\n");
    exit(2);
}

// TableSql 的实例方法，但构造函数会连库建表 —— 继承一个不调父构造的壳子
class FakeTableSql extends TableSql
{
    public function __construct()
    {
        // 故意不调 parent::__construct()：那会 CREATE TABLE 连库
    }
}

// 把 UpdateArray 抠出来，执行句换成 return SQL
$updateArrayBody = ExtractFunction($phpDir . '/sql/sqltable.php', 'function UpdateArray(');
$updateArrayBody = str_replace('return $this->_query($strQuery, \'update data failed\');', 'return $strQuery;', $updateArrayBody);
if (strpos($updateArrayBody, '_query') !== false)
{
    fwrite(STDERR, "UpdateArray 里还有 _query 没换掉\n");
    exit(2);
}
// 注意：抠出的函数体直接当方法体用，形参名必须与 PHP 原名一致
//（$ar / $strWhere / $strLimit），否则拼装句里的变量全空。
eval('class SqlProbeTableSql extends TableSql { public function __construct() {} public function UpdateArrayProbe($ar, $strWhere, $strLimit = false) { ' . $updateArrayBody . ' } }');

// 把 SqlGetTableData / SqlGetSingleTableData / SqlDeleteTableData / SqlCountTableData
// 的拼装句抠出来（只要 $strQry 那一行，不要 mysqli 部分）
function ExtractSqlProbe($path, $head, $needle)
{
    $body = ExtractFunction($path, $head);
    // 找 "$strQry = ...;" 那一行（可能跨行拼接），到分号为止
    $pos = strpos($body, $needle);
    if ($pos === false) { fwrite(STDERR, "找不到 $needle in $head\n"); exit(2); }
    $end = strpos($body, ';', $pos);
    if ($end === false) { fwrite(STDERR, "$needle 那行没有分号\n"); exit(2); }
    return substr($body, $pos, $end - $pos + 1);
}

$selectBody  = ExtractSqlProbe($phpDir . '/sql.php', 'function SqlGetTableData(', '$strQry = ');
$singleBody  = ExtractSqlProbe($phpDir . '/sql.php', 'function SqlGetSingleTableData(', '$strQry = ');
$deleteBody  = ExtractSqlProbe($phpDir . '/sql.php', 'function SqlDeleteTableData(', '$strQry = ');
$countBody   = ExtractSqlProbe($phpDir . '/sql.php', 'function SqlCountTableData(', '$strQry = ');

// 形参名用 PHP 原名（$strTableName / $strWhere / $strOrder / $strLimit）——
// 抠出的拼装句直接引用这些变量。
eval('function probe_select($strTableName, $strWhere, $strOrder, $strLimit) { global $selectBody; ' . $selectBody . ' return $strQry; }');
eval('function probe_single($strTableName, $strWhere, $strOrder) { global $singleBody; ' . $singleBody . ' return $strQry; }');
eval('function probe_delete($strTableName, $strWhere, $strLimit) { global $deleteBody; ' . $deleteBody . ' return $strQry; }');
eval('function probe_count($strTableName, $strWhere) { global $countBody; ' . $countBody . ' return $strQry; }');

// ---------------------------------------------------------------------------
// 用例矩阵：每条对应 Python 侧 stockgroupsql 的一个方法调用
// ---------------------------------------------------------------------------

// UpdateArray 拼装句引用 $this->strName —— 壳子没调父构造，手动设表名
$probeGroup = new SqlProbeTableSql();
$probeGroup->strName = 'stockgroup';
$probeItem = new SqlProbeTableSql();
$probeItem->strName = 'stockgroupitem';
$cases = array();

// -- Add_id / Remove_id（TableSql::Add_id / Remove_id） ----------------------
$cases['add_id_member']     = $probeGroup->Add_id('member');
$cases['add_id_stockgroup'] = $probeGroup->Add_id('stockgroup');
$cases['remove_id_stockgroup'] = $probeGroup->Remove_id('stockgroup_id');
$cases['remove_id_member']  = $probeGroup->Remove_id('member_id');

// -- WHERE 拼装（_sqlcommon.php） --------------------------------------------
$cases['where_member_group'] = _SqlBuildWhereAndArray(array('member_id' => '1', 'groupname' => '演示分组A'));
$cases['where_id']           = _SqlBuildWhere_id('7');
$cases['where_stock']        = _SqlBuildWhere_stock('2');
$cases['where_key_stock']    = _SqlBuildWhereAndArray(array('stockgroup_id' => '1', 'stock_id' => '2'));
$cases['where_string']       = _SqlBuildWhere('groupname', '演示分组A');

// -- LIMIT 拼装（_SqlBuildLimit） --------------------------------------------
$cases['limit_zero_num']     = _SqlBuildLimit(0, 10);
$cases['limit_start_num']    = _SqlBuildLimit(5, 10);
$cases['limit_both_zero']    = _SqlBuildLimit(0, 0);

// -- StockGroupSql 各方法的最终 SQL ------------------------------------------
// GetRecord(member_id, groupname)：GetSingleData(BuildWhere_key_string(...))
$cases['select_group_record'] = probe_single('stockgroup', _SqlBuildWhereAndArray(array('member_id' => '1', 'groupname' => '演示分组A')), false);
// GetRecordById(id)：SqlGetSingleTableData(table, _SqlBuildWhere_id(id))
$cases['select_group_by_id'] = probe_single('stockgroup', _SqlBuildWhere_id('7'), false);
// GetAll(member_id)：GetData(BuildWhere_key(member_id), 'groupname ASC')
$cases['select_group_all'] = probe_select('stockgroup', _SqlBuildWhere('member_id', '1'), '`groupname` ASC', _SqlBuildLimit(0, 0));
// CountByString：CountData(BuildWhere_string(...))
$cases['count_group_by_name'] = probe_count('stockgroup', _SqlBuildWhere('groupname', '演示分组A'));
// InsertString：InsertArray(['member_id'=>'1','groupname'=>'x']) —— 无 id 键，
// InsertArray 会拼 INSERT INTO t(id, ...) VALUES('0', ...)。这里只验字段顺序。
$cases['insert_group'] = "INSERT INTO stockgroup(id, member_id, groupname) VALUES('0', '1', '演示分组A')";
// UpdateString：UpdateById(['groupname'=>'y'], id)
$cases['update_group'] = $probeGroup->UpdateArrayProbe(array('groupname' => '改名'), _SqlBuildWhere_id('7'), '1');
// DeleteById：DeleteData(_SqlBuildWhere_id(id)) —— 不传 limit
$cases['delete_group_by_id'] = probe_delete('stockgroup', _SqlBuildWhere_id('7'), false);
// DeleteByString：DeleteData(BuildWhere_string(...))
$cases['delete_group_by_string'] = probe_delete('stockgroup', _SqlBuildWhere('groupname', '演示分组A'), false);

// -- StockGroupItemSql 各方法的最终 SQL --------------------------------------
// GetAll()：GetData(BuildWhere_key(), '`id` ASC', _SqlBuildLimit(0,0))
$cases['select_item_all'] = probe_select('stockgroupitem', _SqlBuildWhere('stockgroup_id', '1'), '`id` ASC', _SqlBuildLimit(0, 0));
// GetRecordById(item_id)
$cases['select_item_by_id'] = probe_single('stockgroupitem', _SqlBuildWhere_id('9'), false);
// GetRecord(stock_id)：GetSingleData(BuildWhere_key_stock(...))
$cases['select_item_by_group_stock'] = probe_single('stockgroupitem', _SqlBuildWhereAndArray(array('stockgroup_id' => '1', 'stock_id' => '2')), false);
// Count()
$cases['count_item_by_group'] = probe_count('stockgroupitem', _SqlBuildWhere('stockgroup_id', '1'));
// CountByStockId：CountData(_SqlBuildWhere_stock(...))
$cases['count_item_by_stock'] = probe_count('stockgroupitem', _SqlBuildWhere_stock('2'));
// Insert(stock_id)：InsertArrays(MakeFieldKeyId(), ['stock_id'=>..], _makePrivateFieldArray())
$cases['insert_item'] = "INSERT INTO stockgroupitem(id, stockgroup_id, stock_id, quantity, cost, record) VALUES('0', '1', '2', '0', '0.0', '0')";
// Update：UpdateById(_makePrivateFieldArray(...), item_id)
$cases['update_item'] = $probeItem->UpdateArrayProbe(array('quantity' => '5', 'cost' => '1.5', 'record' => '3'), _SqlBuildWhere_id('9'), '1');
// DeleteById：DeleteData(_SqlBuildWhere_id(item_id)) —— 不传 limit
$cases['delete_item_by_id'] = probe_delete('stockgroupitem', _SqlBuildWhere_id('9'), false);
// DeleteAll()：DeleteData(BuildWhere_key())
$cases['delete_item_all'] = probe_delete('stockgroupitem', _SqlBuildWhere('stockgroup_id', '1'), false);

// -- _stock_symbol_of / _get_stock_id 的替身 SQL（_scratch 级） --------------
// Python 侧是手写的 SELECT symbol/id，PHP 侧对应 SqlGetStockSymbol 的
// "SELECT symbol FROM stock WHERE id = ... LIMIT 1"（见 php/sql/sqlstock.php）。
$cases['select_symbol_by_id'] = "SELECT symbol FROM stock WHERE id = '2' LIMIT 1";
$cases['select_id_by_symbol'] = "SELECT id FROM stock WHERE symbol = 'SZ161125' LIMIT 1";

fwrite(STDERR, "cases: " . count($cases) . "\n");
echo json_encode($cases, JSON_UNESCAPED_UNICODE);
