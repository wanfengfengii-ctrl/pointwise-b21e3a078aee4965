"""Small generic rational linear algebra. No probability-model conversion."""
from fractions import Fraction


def q(value):
    if isinstance(value, bool) or not isinstance(value, (int, str, Fraction)):
        raise ValueError("expected int, rational string or Fraction")
    return Fraction(value)


def dot(a, b):
    if len(a) != len(b):
        raise ValueError("dot dimensions")
    return sum((x * y for x, y in zip(a, b)), Fraction(0))


def transpose(a, columns=None):
    if not a:
        return [[] for _ in range(columns or 0)]
    if any(len(row) != len(a[0]) for row in a):
        raise ValueError("ragged matrix")
    return [list(col) for col in zip(*a)]


def matmul(a, b):
    bt = transpose(b)
    if a and len(a[0]) != len(b):
        raise ValueError("product dimensions")
    return [[dot(row, col) for col in bt] for row in a]


def affine(a, b, n):
    """Return a particular vector and a list of kernel basis vectors.

    Solve A*x=b by exact RREF; ValueError means inconsistent equations.
    Empty A and n=0 are supported.
    """
    if len(a) != len(b) or any(len(row) != n for row in a):
        raise ValueError("equation dimensions")
    rows = [[q(v) for v in row] + [q(rhs)] for row, rhs in zip(a, b)]
    pivots = []
    r = 0
    for c in range(n):
        p = next((i for i in range(r, len(rows)) if rows[i][c]), None)
        if p is None:
            continue
        rows[r], rows[p] = rows[p], rows[r]
        scale = rows[r][c]
        rows[r] = [v / scale for v in rows[r]]
        for i in range(len(rows)):
            if i != r and rows[i][c]:
                scale = rows[i][c]
                rows[i] = [v - scale * w for v, w in zip(rows[i], rows[r])]
        pivots.append(c)
        r += 1
    if any(not any(row[:n]) and row[n] for row in rows):
        raise ValueError("inconsistent equations")
    point = [Fraction(0)] * n
    for i, c in enumerate(pivots):
        point[c] = rows[i][n]
    basis = []
    for c in range(n):
        if c not in pivots:
            v = [Fraction(0)] * n
            v[c] = Fraction(1)
            for i, p in enumerate(pivots):
                v[p] = -rows[i][c]
            basis.append(v)
    return point, basis


def semidefinite(matrix, strict=False):
    """Exact symmetric LDL-style pivot elimination; supports dimension zero."""
    n = len(matrix)
    a = [[q(v) for v in row] for row in matrix]
    if any(len(row) != n for row in a):
        return False
    if any(a[i][j] != a[j][i] for i in range(n) for j in range(n)):
        return False
    for k in range(n):
        if a[k][k] < 0:
            return False
        if not a[k][k]:
            if strict or any(a[k][j] for j in range(k + 1, n)):
                return False
            continue
        for i in range(k + 1, n):
            for j in range(i, n):
                a[j][i] = a[i][j] = a[i][j] - a[i][k] * a[k][j] / a[k][k]
    return True
