"""Exact relations between moment models and SDK2 factor models.

A Gaussian with mean m and positive semidefinite covariance S is the density
proportional to exp(-(x-m)' S+ (x-m) / 2) on the affine support m + range(S),
relative to Euclidean measure there; S+ is the Moore-Penrose inverse. An SDK2
FactorModel stores exactly that law: hard equations carve out the support and
the weighted squared residuals are the energy. Conditioning on exact linear
observations restricts the support and keeps the energy, so every computation
below is exact rational arithmetic with no SDK1 probability code.
"""
from fractions import Fraction
from exact import q, dot, transpose, matmul, affine
from errors import ImpossibleObservation
from vendor.sdk2 import FactorModel


def _ldl(matrix):
    """Exact L D L' decomposition of a symmetric PSD matrix.

    Returns (L, d) with L unit lower triangular and d the diagonal entries,
    so matrix = L * diag(d) * L'. For a PSD input a zero pivot implies a
    zero row/column, so no pivoting is ever needed.
    """
    n = len(matrix)
    a = [[q(v) for v in row] for row in matrix]
    if any(len(row) != n for row in a):
        raise ValueError("square matrix required")
    L = [[Fraction(1) if i == j else Fraction(0) for j in range(n)] for i in range(n)]
    d = [Fraction(0)] * n
    for k in range(n):
        d[k] = a[k][k] - sum((L[k][j] * L[k][j] * d[j] for j in range(k)), Fraction(0))
        for i in range(k + 1, n):
            num = a[i][k] - sum((L[i][j] * L[k][j] * d[j] for j in range(k)), Fraction(0))
            if d[k]:
                L[i][k] = num / d[k]
            elif num:
                raise ValueError("matrix is not positive semidefinite")
    return L, d


def _inverse(matrix):
    """Exact inverse of a nonsingular rational square matrix."""
    n = len(matrix)
    if any(len(row) != n for row in matrix):
        raise ValueError("square matrix required")
    aug = [[q(v) for v in row] + [Fraction(1) if i == j else Fraction(0)
                                  for j in range(n)] for i, row in enumerate(matrix)]
    for c in range(n):
        p = next((i for i in range(c, n) if aug[i][c]), None)
        if p is None:
            raise ValueError("singular matrix")
        aug[c], aug[p] = aug[p], aug[c]
        scale = aug[c][c]
        aug[c] = [v / scale for v in aug[c]]
        for i in range(n):
            if i != c and aug[i][c]:
                scale = aug[i][c]
                aug[i] = [v - scale * w for v, w in zip(aug[i], aug[c])]
    return [row[n:] for row in aug]


def from_moments(model_id, revision, names, mean, covariance):
    """Build the SDK2 factor of N(mean, covariance), keeping degeneracy exact.

    Hard rows are a kernel basis of the covariance, so the support is exactly
    mean + range(covariance); the residual energy is (x-mean)' S+ (x-mean)
    there. Zero variances stay zero; no epsilon is ever introduced.
    """
    names = tuple(names)
    n = len(names)
    mean = tuple(q(v) for v in mean)
    L, d = _ldl(covariance)
    cols = [j for j in range(n) if d[j] > 0]
    if cols:
        # covariance = F * diag(d_cols) * F' with F of full column rank, so
        # S+ = F (F'F)^{-1} diag(1/d_cols) (F'F)^{-1} F' exactly.
        F = [[L[i][j] for j in cols] for i in range(n)]
        M = _inverse(matmul(transpose(F), F))
        dinv = [1 / d[j] for j in cols]
        W = matmul([[M[a][b] * dinv[b] for b in range(len(cols))]
                    for a in range(len(cols))], M)
        precision = matmul(matmul(F, W), transpose(F))
    else:
        precision = [[Fraction(0)] * n for _ in range(n)]
    L2, d2 = _ldl(precision)
    residuals = [[L2[i][j] for i in range(n)] for j in range(n) if d2[j] > 0]
    weights = [d2[j] for j in range(n) if d2[j] > 0]
    targets = [dot(row, mean) for row in residuals]
    _, kernel = affine([[q(v) for v in row] for row in covariance],
                       [Fraction(0)] * n, n)
    hard = [list(row) for row in kernel]
    rhs = [dot(row, mean) for row in hard]
    return FactorModel(model_id, revision, list(names), hard, rhs,
                       residuals, targets, weights)


def _support_moments(point, basis, residuals, targets, weights, n):
    """Mean and covariance of exp(-energy/2) on point + span(basis).

    With x = point + K z the energy is a positive definite quadratic in z,
    so z is Gaussian with precision K'R'WK and mean K'R'W(t - Rp) solved
    against that precision.
    """
    r = len(basis)
    if r == 0:
        return (tuple(point),
                tuple(tuple(Fraction(0) for _ in range(n)) for _ in range(n)))
    K = transpose([list(v) for v in basis])
    RK = matmul([list(row) for row in residuals], K)
    Rp = [dot(row, point) for row in residuals]
    lam = [[sum((weights[i] * RK[i][a] * RK[i][b] for i in range(len(RK))), Fraction(0))
            for b in range(r)] for a in range(r)]
    eta = [sum((weights[i] * RK[i][a] * (targets[i] - Rp[i])
                for i in range(len(RK))), Fraction(0)) for a in range(r)]
    inv = _inverse(lam)
    zmean = [dot(row, eta) for row in inv]
    mean = tuple(point[i] + dot(K[i], zmean) for i in range(n))
    covariance = tuple(tuple(row) for row in matmul(matmul(K, inv), transpose(K)))
    return mean, covariance


def moments(factor):
    """Exact (mean, covariance) of any valid FactorModel, native or migrated."""
    n = len(factor.names)
    point, basis = affine([list(row) for row in factor.hard], list(factor.rhs), n)
    return _support_moments(point, basis, factor.residuals, factor.targets,
                            factor.weights, n)


def condition(factor, equations):
    """Exact (mean, covariance) given precise observations (row, rhs).

    Conditioning restricts the hard support by the observation equations and
    keeps the energy. An inconsistent augmented system means the observations
    leave the model support and raises ImpossibleObservation.
    """
    n = len(factor.names)
    hard = [list(row) for row in factor.hard]
    rhs = list(factor.rhs)
    for row, value in equations:
        if len(row) != n:
            raise ValueError("observation dimensions")
        hard.append([q(v) for v in row])
        rhs.append(q(value))
    try:
        point, basis = affine(hard, rhs, n)
    except ValueError:
        raise ImpossibleObservation(
            "exact observations contradict model support") from None
    return _support_moments(point, basis, factor.residuals, factor.targets,
                            factor.weights, n)
