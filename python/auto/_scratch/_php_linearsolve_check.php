<?php
// 线性方程组求解的 PHP 对照 harness。
//
// 对应 python/auto/calc/linearsolve.py。被 require 的两个文件是纯函数
// （无全局状态、无数据库），所以这里不需要 _stock.php 那一套引导。
//
// 约定（与 _scratch 下其它 harness 一致）：stdout 只承载 JSON，
// 诊断走 stderr，文件末尾不写闭合标签（写闭合标签会让尾随空白被输出），
// 用 php -n -d display_errors=stderr 跑。

// 本文件在 python/auto/_scratch/ 下，PHP 源码在仓库根的 php/tutorial/ ——
// 三层上去。用 __DIR__ 拼绝对路径，免得从别的 cwd 跑时相对路径失效。
require_once(__DIR__.'/../../../php/tutorial/cramersrule.php');
require_once(__DIR__.'/../../../php/tutorial/gaussianelimination.php');

function run_case($strGroup, $arEquation)
{
    $ar = array('group' => $strGroup, 'equation' => $arEquation);

    // determinant() 收的是**系数矩阵**（方阵），不是增广格式 ——
    // CramersRule 内部自己会 array_slice 掉常数项，这里要手动切。
    $ar['det'] = null;
    if (count($arEquation) == count($arEquation[0]) - 1)
    {
        $arMatrix = array();
        foreach ($arEquation as $arRow)
        {
            $arMatrix[] = array_slice($arRow, 0, count($arRow) - 1);
        }
        $ar['det'] = determinant($arMatrix);
    }

    try
    {
        $ar['cramer'] = CramersRule($arEquation);
    }
    catch (Exception $e)
    {
        $ar['cramer'] = $e->getMessage();
    }

    try
    {
        $ar['gauss'] = GaussianElimination($arEquation);
    }
    catch (Exception $e)
    {
        $ar['gauss'] = $e->getMessage();
    }

    try
    {
        $ar['overdetermined'] = SolveOverdetermined($arEquation);
    }
    catch (Exception $e)
    {
        $ar['overdetermined'] = $e->getMessage();
    }
    return $ar;
}

$arTest = array(
    // 单测里的四个方阵，外加穷举页真实遇到的两组形状（系数是 10^4 量级
    // 的指数点位混 0.01 量级的汇率比例）。
    array('square2', array(array(2.0, 1.0, 5.0), array(1.0, 3.0, 10.0))),
    array('square3', array(array(1.0, 1.0, 1.0, 6.0), array(2.0, -1.0, 1.0, 3.0), array(1.0, 2.0, -1.0, 2.0))),
    array('pivot3', array(array(0.0, 1.0, 1.0, 5.0), array(1.0, 0.0, 1.0, 4.0), array(1.0, 1.0, 0.0, 5.0))),
    array('over43', array(
        array(7623.68, 0.99, 0.01, 1.0),
        array(7663.43, 1.02, 0.02, 1.0),
        array(7712.57, 0.98, 0.03, 1.0),
        array(7690.73, 1.01, 0.04, 1.0),
    )),
    // 穷举页实际方程行的形状：a 项是标的价差、末项是汇率比例、常数 1/n
    array('exhaustive3', array(
        array(0.0031, -0.0018, 0.0059, -0.1243),
        array(0.0044, 0.0007, 0.0021, -0.1187),
        array(0.0012, -0.0035, 0.0048, -0.1301),
    )),
    // 奇异矩阵：第三行 = 第一行 × 2
    array('singular', array(array(1.0, 2.0, 3.0), array(2.0, 4.0, 6.0))),
    // 非方阵（4 方程 3 未知数走 CramersRule 应当抛异常）
    array('notsquare', array(
        array(1.0, 1.0, 2.0),
        array(1.0, -1.0, 0.0),
        array(2.0, 0.0, 2.0),
        array(1.0, 2.0, 3.0),
    )),
    // 4x4（走拉普拉斯递归分支）
    array('square4', array(
        array(1.0, 0.0, 2.0, -1.0, 0.0),
        array(3.0, 0.0, 0.0, 5.0, 0.0),
        array(2.0, 1.0, 4.0, -3.0, 0.0),
        array(1.0, 0.0, 5.0, 0.0, 0.0),
    )),
);

$arOut = array();
foreach ($arTest as $arItem)
{
    $arOut[] = run_case($arItem[0], $arItem[1]);
}
echo json_encode($arOut);
