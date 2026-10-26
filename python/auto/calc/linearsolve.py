"""线性方程组求解：克莱姆法则与超定方程最小二乘。

对应 PHP 两个 tutorial 文件（作者写的**教学示例**，不是第三方库）：

* ``php/tutorial/cramersrule.php``  —— ``CramersRule()`` / ``determinant()``
* ``php/tutorial/gaussianelimination.php`` —— ``GaussianElimination()`` /
  ``SolveOverdetermined()``

调用方只有一个：穷举持仓页
（``woody/res/exhaustiveholdingscn.php`` 的
``_echoVerifyHoldingsItem`` 用 ``CramersRule``、``_echoExhaustiveHoldingsItem``
用 ``SolveOverdetermined``）。两个函数都抛异常表示"解不出来"，PHP 那边
``try/catch`` 之后只写一行调试日志，本版返回 ``None`` —— 页面不该因为某个
季度解不出方程就整页 500（与穷举页其它分支的约定一致）。

方程统一是**增广**格式：``[a, b, c, ..., d]`` 表示 ``a*x + b*y + c*z + ... = d``，
系数个数 = 未知数个数 + 1。这个约定照抄 PHP，不要在调用方换成"系数矩阵 +
常数向量"两参数 —— 穷举页组方程时是按行往里塞的，换形制会让那边多一层转换。

**异常语义与 PHP 的差别**：PHP 抛 ``Exception`` + 中文消息；本版抛
:class:`LinearSolveError`（``ValueError`` 子类）。消息文本照抄，因为它是
作者用来排查"为什么这一行没解"的唯一线索，改了就没法跟 PHP 日志对照。
"""

from __future__ import annotations

from typing import Sequence

#: PHP 里判断"行列式/主元算不算 0"的阈值（两个文件各写一遍 ``1e-12``）。
#: 别按 double 的机器精度去理解它：方程里出现的是 10^4 量级的指数点位与
#: 0.01 量级的外汇比例，混在一起时 1e-12 的实际含义是"精确奇异"。
SINGULAR_EPSILON = 1e-12


class LinearSolveError(ValueError):
    """方程无解/不唯一。对应 PHP 那两个文件里抛的 ``Exception``。"""


# ---------------------------------------------------------------------------
# 克莱姆法则（``cramersrule.php``）
# ---------------------------------------------------------------------------
def determinant(matrix: Sequence[Sequence[float]]) -> float:
    """``determinant($matrix)``：递归拉普拉斯展开，支持任意维度。

    1×1 / 2×2 / 3×3 三个分支与 PHP 一样**走特例公式**，不是"优化的递归"——
    3×3 那条对角线法则在浮点上是**另一串运算顺序**，换成递归展开后末尾
    几位可能不同。穷举页把这个值除到比例里再 ``number_format(…, 0)``，
    差一点就可能让整行在"匹配/不匹配"之间翻面（那一行会因此丢掉黄底），
    所以照抄运算顺序。
    """
    size = len(matrix)
    for row in matrix:
        if len(row) != size:
            raise LinearSolveError("矩阵不是方阵，无法计算行列式")

    if size == 1:
        return matrix[0][0]
    if size == 2:
        return matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
    if size == 3:
        return (matrix[0][0] * matrix[1][1] * matrix[2][2]
                + matrix[0][1] * matrix[1][2] * matrix[2][0]
                + matrix[0][2] * matrix[1][0] * matrix[2][1]
                - matrix[0][2] * matrix[1][1] * matrix[2][0]
                - matrix[0][1] * matrix[1][0] * matrix[2][2]
                - matrix[0][0] * matrix[1][2] * matrix[2][1])

    # 更高维度：拉普拉斯展开（沿第一行）
    total = 0.0
    for col in range(size):
        sub_matrix = []
        for i in range(1, size):
            sub_row = []
            for j in range(size):
                if j != col:
                    sub_row.append(matrix[i][j])
            sub_matrix.append(sub_row)
        sign = 1 if col % 2 == 0 else -1
        total += sign * matrix[0][col] * determinant(sub_matrix)
    return total


def cramers_rule(equations: Sequence[Sequence[float]]) -> list:
    """``CramersRule($equations)``：方阵用行列式比值求解。

    PHP 要求 ``方程个数 == 未知数个数``，否则抛异常；本版同样抛
    :class:`LinearSolveError`（**不**自动退化成最小二乘 —— 调用方
    ``_echoVerifyHoldingsItem`` 正是靠这个异常来判定"这一季数据不够"）。
    """
    if not equations:
        raise LinearSolveError("方程列表为空")

    count = len(equations)
    dim = len(equations[0]) - 1
    if count != dim:
        raise LinearSolveError("方程个数(%d)与未知数个数(%d)不匹配，不是方阵" % (count, dim))

    matrix = [list(row[:dim]) for row in equations]
    constants = [row[dim] for row in equations]

    det_d = determinant(matrix)
    if abs(det_d) < SINGULAR_EPSILON:
        raise LinearSolveError("矩阵奇异，行列式为零，无法使用克莱姆法则")

    solution = []
    for i in range(dim):
        modified = [list(row) for row in matrix]
        for j in range(count):
            modified[j][i] = constants[j]
        solution.append(determinant(modified) / det_d)
    return solution


# ---------------------------------------------------------------------------
# 高斯消元与超定方程组（``gaussianelimination.php``）
# ---------------------------------------------------------------------------
def gaussian_elimination(equations: Sequence[Sequence[float]]) -> list:
    """``GaussianElimination($equations)``：列主元消元 + 回代。

    **列主元**（每次挑绝对值最大的那一行）是 PHP 原文就有的，不是为了
    数值稳定而加的：穷举页的方程行里同时有 10^4 量级的指数点位与
    0.01 量级的汇率比例，不选主元时回代误差会把比例算成负几十。
    """
    if not equations:
        raise LinearSolveError("方程列表为空")

    size = len(equations)
    dim = len(equations[0]) - 1
    if size != dim:
        raise LinearSolveError("方程个数(%d)与未知数个数(%d)不匹配，不是方阵" % (size, dim))

    # 增广矩阵（equations 格式本来就是增广的，这一步是照抄的结构对齐）
    augmented = [list(equations[i][:dim]) + [equations[i][dim]] for i in range(size)]

    for i in range(size):
        max_row = i
        for k in range(i + 1, size):
            if abs(augmented[k][i]) > abs(augmented[max_row][i]):
                max_row = k
        if max_row != i:
            augmented[i], augmented[max_row] = augmented[max_row], augmented[i]
        if abs(augmented[i][i]) < SINGULAR_EPSILON:
            raise LinearSolveError("矩阵奇异，无法求解（主元为零）")

        for k in range(i + 1, size):
            factor = augmented[k][i] / augmented[i][i]
            for j in range(i, size + 1):
                augmented[k][j] -= factor * augmented[i][j]

    solution = [0.0] * size
    for i in range(size - 1, -1, -1):
        total = 0.0
        for j in range(i + 1, size):
            total += augmented[i][j] * solution[j]
        solution[i] = (augmented[i][size] - total) / augmented[i][i]
    return solution


def solve_overdetermined(equations: Sequence[Sequence[float]]) -> list:
    """``SolveOverdetermined($equations)``：正规方程 + 高斯消元（最小二乘）。

    先累加 ``MᵀM`` 与 ``Mᵀb`` 得到法方程，再交给
    :func:`gaussian_elimination`。**方程数可以多于未知数**（这是穷举页的
    常态：净值涨跌超过阈值的日子往往不止 4 天）。

    注意 PHP 只在 ``count($arEq) >= $iCount`` 时调用，且**每次都用全部累积的
    方程重解**（不是增量更新）—— 所以越往后行里的比例越稳。
    """
    if not equations:
        raise LinearSolveError("方程列表为空")

    dim = len(equations[0]) - 1
    count = len(equations)
    if count < dim:
        raise LinearSolveError("方程个数(%d)少于未知数个数(%d)" % (count, dim))

    matrix = [[0.0] * dim for _ in range(dim)]
    vector = [0.0] * dim
    for equation in equations:
        coefficients = list(equation[:dim])
        constant = equation[dim]
        for i in range(dim):
            for j in range(dim):
                matrix[i][j] += coefficients[i] * coefficients[j]
            vector[i] += coefficients[i] * constant

    normal = [matrix[i] + [vector[i]] for i in range(dim)]
    return gaussian_elimination(normal)


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名）
# ---------------------------------------------------------------------------
CramersRule = cramers_rule
GaussianElimination = gaussian_elimination
SolveOverdetermined = solve_overdetermined
