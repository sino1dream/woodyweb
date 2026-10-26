"""``calc.linearsolve`` 的单测。

对照 PHP：``php/tutorial/cramersrule.php`` 与
``php/tutorial/gaussianelimination.php``。逐例对照脚本是
``_scratch/_php_linearsolve_compare.py``（同一批用例喂给 PHP 与 Python，
比较到 1e-9）。

用例挑的是**穷举持仓页真实会遇到**的形状：未知数 2–4 个、系数是
10^4 量级的指数点位混 0.01 量级的汇率比例。不要用随手编的 1/2/3 整数矩阵 ——
那种矩阵选不选主元、用不用特例公式都算得出同一个数，测不出偏差。
"""

import unittest

from calc.linearsolve import (
    LinearSolveError,
    cramers_rule,
    determinant,
    gaussian_elimination,
    solve_overdetermined,
)


class DeterminantTest(unittest.TestCase):
    def test_one_by_one(self):
        self.assertEqual(determinant([[5.0]]), 5.0)

    def test_two_by_two(self):
        # 3*4 - 2*1 = 10
        self.assertEqual(determinant([[3.0, 2.0], [1.0, 4.0]]), 10.0)

    def test_three_by_three(self):
        # 1*(5*9-6*8) - 2*(4*9-6*7) + 3*(4*8-5*7) = -3 + 12 - 9 = 0 → 换一组非奇异
        matrix = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 10.0]]
        self.assertAlmostEqual(determinant(matrix), -3.0, places=12)

    def test_four_by_four(self):
        """4×4 走递归展开（PHP 只有 1/2/3 维特例）。

        矩阵第 2 列只有第 3 行一个 1，沿该列展开：
        ``det = (-1)^(2+1) * det([[1,2,-1],[3,0,5],[1,5,0]])``
        而内层 = ``1*(0-25) - 2*(0-5) + (-1)*(15-0) = -25 +10 -15 = -30``，
        故 ``det = 30``。
        """
        matrix = [
            [1.0, 0.0, 2.0, -1.0],
            [3.0, 0.0, 0.0, 5.0],
            [2.0, 1.0, 4.0, -3.0],
            [1.0, 0.0, 5.0, 0.0],
        ]
        self.assertAlmostEqual(determinant(matrix), 30.0, places=12)

    def test_not_square_raises(self):
        with self.assertRaises(LinearSolveError):
            determinant([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])


class CramersRuleTest(unittest.TestCase):
    def test_two_unknowns(self):
        # 2x + y = 5; x + 3y = 10  → x = 1, y = 3
        self.assertEqual(cramers_rule([[2.0, 1.0, 5.0], [1.0, 3.0, 10.0]]), [1.0, 3.0])

    def test_three_unknowns(self):
        # x + y + z = 6; 2x - y + z = 3; x + 2y - z = 2  → 1, 2, 3
        solution = cramers_rule([
            [1.0, 1.0, 1.0, 6.0],
            [2.0, -1.0, 1.0, 3.0],
            [1.0, 2.0, -1.0, 2.0],
        ])
        for got, expected in zip(solution, (1.0, 2.0, 3.0)):
            self.assertAlmostEqual(got, expected, places=12)

    def test_singular_matrix_raises(self):
        # 第二行是第一行的两倍 → 行列式 0
        with self.assertRaises(LinearSolveError):
            cramers_rule([[1.0, 2.0, 3.0], [2.0, 4.0, 6.0]])

    def test_not_square_raises(self):
        """方程数与未知数不等时**抛异常**，不退化成最小二乘。

        调用方 ``_echoVerifyHoldingsItem`` 靠这个异常判定"数据不够"，
        自动兜底会让它以为解出来了。
        """
        with self.assertRaises(LinearSolveError):
            cramers_rule([[1.0, 1.0, 2.0], [1.0, -1.0, 0.0], [2.0, 0.0, 2.0],
                          [1.0, 2.0, 3.0]])

    def test_empty_raises(self):
        with self.assertRaises(LinearSolveError):
            cramers_rule([])


class GaussianEliminationTest(unittest.TestCase):
    def test_three_unknowns_with_pivot_swap(self):
        # 第一行首元为 0，必须靠列主元交换才能解
        solution = gaussian_elimination([
            [0.0, 1.0, 1.0, 5.0],
            [1.0, 0.0, 1.0, 4.0],
            [1.0, 1.0, 0.0, 5.0],
        ])
        for got, expected in zip(solution, (2.0, 3.0, 2.0)):
            self.assertAlmostEqual(got, expected, places=12)

    def test_singular_raises(self):
        with self.assertRaises(LinearSolveError):
            gaussian_elimination([[1.0, 1.0, 2.0], [1.0, 1.0, 3.0]])


class SolveOverdeterminedTest(unittest.TestCase):
    def test_more_equations_than_unknowns(self):
        """4 个方程解 3 个未知数 —— 穷举页的常态形状。

        方程由 x=1.2 / y=0.8 / z=2.0 生成，所以最小二乘解应精确回到原值。
        系数乘上不同的量级（a 是 7000 量级的"指数点位"，b 是 0.01 量级的
        "汇率比例"），与真实方程行同构。
        """
        truth = (1.2, 0.8, 2.0)
        rows = [
            (7623.68, 0.99, 0.01, 1.0),
            (7663.43, 1.02, 0.02, 1.0),
            (7712.57, 0.98, 0.03, 1.0),
            (7690.73, 1.01, 0.04, 1.0),
        ]
        equations = []
        for a, b, c, d in rows:
            equations.append([a, b, c, a * truth[0] + b * truth[1] + c * truth[2]])
        solution = solve_overdetermined(equations)
        for got, expected in zip(solution, truth):
            self.assertAlmostEqual(got, expected, places=6)

    def test_fewer_equations_than_unknowns_raises(self):
        """1 个方程 3 个未知数 —— 连法方程都搭不起来。"""
        with self.assertRaises(LinearSolveError):
            solve_overdetermined([[1.0, 2.0, 3.0, 4.0]])

    def test_exactly_square_matches_gaussian(self):
        equations = [
            [1.0, 1.0, 1.0, 6.0],
            [2.0, -1.0, 1.0, 3.0],
            [1.0, 2.0, -1.0, 2.0],
        ]
        normal = solve_overdetermined(equations)
        direct = gaussian_elimination(equations)
        for got, expected in zip(normal, direct):
            self.assertAlmostEqual(got, expected, places=9)


if __name__ == "__main__":
    unittest.main()
