"""Offline SDK1: exact moments and exact linear conditioning."""
from dataclasses import dataclass
from fractions import Fraction
from exact import q, dot, semidefinite
from variables import validate_names, identifier
from errors import InvalidModel, ImpossibleObservation


@dataclass(frozen=True)
class Model:
    id: str
    revision: int
    names: tuple
    mean: tuple
    covariance: tuple

    def __post_init__(self):
        try:
            identifier(self.id, "model id")
            if type(self.revision) is not int or self.revision < 1:
                raise ValueError("revision must be a positive integer")
            names = validate_names(self.names)
            mean = tuple(q(v) for v in self.mean)
            cov = tuple(tuple(q(v) for v in row) for row in self.covariance)
            n = len(names)
            if len(mean) != n or len(cov) != n or any(len(row) != n for row in cov):
                raise ValueError("model dimensions")
            if not semidefinite(cov):
                raise ValueError("covariance must be symmetric positive semidefinite")
            object.__setattr__(self, "names", names)
            object.__setattr__(self, "mean", mean)
            object.__setattr__(self, "covariance", cov)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise InvalidModel(str(exc)) from exc

    def payload(self):
        return {"id": self.id, "revision": self.revision, "names": list(self.names),
                "mean": [str(v) for v in self.mean],
                "covariance": [[str(v) for v in row] for row in self.covariance]}

    @classmethod
    def from_payload(cls, obj):
        return cls(obj["id"], obj["revision"], obj["names"], obj["mean"], obj["covariance"])

    def conditional(self, equations, projections):
        """equations: (coefficient vector, rhs); projections: (vector, offset).

        Sequential exact conditioning preserves all cross-covariances.
        A zero innovation variance is a deterministic equality, not a pivot.
        """
        mean = list(self.mean)
        cov = [list(row) for row in self.covariance]
        for row, rhs in equations:
            if len(row) != len(mean):
                raise ValueError("observation dimensions")
            cross = [dot(c, row) for c in cov]
            variance = dot(row, cross)
            residual = q(rhs) - dot(row, mean)
            if not variance:
                if residual:
                    raise ImpossibleObservation("exact observations contradict model support")
                continue
            mean = [m + c * residual / variance for m, c in zip(mean, cross)]
            cov = [[cov[i][j] - cross[i] * cross[j] / variance
                    for j in range(len(mean))] for i in range(len(mean))]
        vectors = [list(row) for row, _ in projections]
        means = tuple(dot(row, mean) + q(offset) for row, offset in projections)
        covariance = tuple(tuple(dot(a, [dot(c, b) for c in cov]) for b in vectors)
                           for a in vectors)
        return means, covariance
