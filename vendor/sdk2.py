"""Offline SDK2 port: constrained quadratic factors, storage and energy.

Probability density on H*x=h is proportional to exp(-energy(x)/2),
with respect to Euclidean measure on that affine subspace.
This module deliberately has no moments or conditioning API.
"""
from dataclasses import dataclass
import json
from exact import q, dot, affine, semidefinite
from variables import identifier, validate_names


@dataclass(frozen=True)
class FactorModel:
    id: str
    revision: int
    names: tuple
    hard: tuple
    rhs: tuple
    residuals: tuple
    targets: tuple
    weights: tuple

    def __post_init__(self):
        identifier(self.id, "model id")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("positive revision required")
        names = validate_names(self.names)
        n = len(names)
        hard = tuple(tuple(q(v) for v in row) for row in self.hard)
        rhs = tuple(q(v) for v in self.rhs)
        residuals = tuple(tuple(q(v) for v in row) for row in self.residuals)
        targets = tuple(q(v) for v in self.targets)
        weights = tuple(q(v) for v in self.weights)
        if any(len(row) != n for row in hard + residuals):
            raise ValueError("factor dimensions")
        if len(hard) != len(rhs) or len(residuals) != len(targets) or len(targets) != len(weights):
            raise ValueError("factor lengths")
        if any(w <= 0 for w in weights):
            raise ValueError("strictly positive residual weights required")
        _, basis = affine(hard, rhs, n)
        restricted = [[sum((w * dot(row, a) * dot(row, b)
                             for row, w in zip(residuals, weights)), q(0))
                       for b in basis] for a in basis]
        if not semidefinite(restricted, strict=True):
            raise ValueError("energy must be positive definite on ker(H)")
        for key, value in (("names", names), ("hard", hard), ("rhs", rhs),
                           ("residuals", residuals), ("targets", targets), ("weights", weights)):
            object.__setattr__(self, key, value)

    def payload(self):
        return {"id": self.id, "revision": self.revision, "names": list(self.names),
                "hard": [[str(v) for v in r] for r in self.hard],
                "rhs": [str(v) for v in self.rhs],
                "residuals": [[str(v) for v in r] for r in self.residuals],
                "targets": [str(v) for v in self.targets],
                "weights": [str(v) for v in self.weights]}

    @classmethod
    def from_payload(cls, obj):
        return cls(**obj)

    def energy(self, values):
        values = tuple(q(v) for v in values)
        if len(values) != len(self.names):
            raise ValueError("point dimensions")
        if any(dot(row, values) != rhs for row, rhs in zip(self.hard, self.rhs)):
            raise ValueError("point outside hard support")
        return sum((w * (dot(row, values) - target) ** 2
                    for row, target, w in zip(self.residuals, self.targets, self.weights)), q(0))


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS models_v2(
        id TEXT NOT NULL, revision INTEGER NOT NULL, factors TEXT NOT NULL,
        PRIMARY KEY(id,revision))""",
    """CREATE TABLE IF NOT EXISTS heads_v2(
        id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
        FOREIGN KEY(id,revision) REFERENCES models_v2(id,revision))""",
    """CREATE TABLE IF NOT EXISTS receipts_v2(
        request_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
        model_id TEXT NOT NULL, revision INTEGER NOT NULL,
        FOREIGN KEY(model_id,revision) REFERENCES models_v2(id,revision))""",
    """CREATE TABLE IF NOT EXISTS tasks_v2(
        task_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, revision INTEGER NOT NULL,
        payload TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('saved','success','impossible')),
        FOREIGN KEY(model_id,revision) REFERENCES models_v2(id,revision))""",
    """CREATE TABLE IF NOT EXISTS results_v2(
        task_id TEXT PRIMARY KEY REFERENCES tasks_v2(task_id),
        status TEXT NOT NULL CHECK(status IN ('success','impossible')), payload TEXT NOT NULL)"""
)


def install(connection):
    """Caller owns the transaction; no implicit commit or version switch."""
    for statement in SCHEMA:
        connection.execute(statement)


def append(connection, model):
    """Native append; caller owns commit. Does not manufacture old write receipts."""
    model = FactorModel.from_payload(model.payload())
    head = connection.execute("SELECT revision FROM heads_v2 WHERE id=?", (model.id,)).fetchone()
    if model.revision != (1 if head is None else head[0] + 1):
        raise ValueError("new revision must be head+1")
    connection.execute("INSERT INTO models_v2 VALUES(?,?,?)",
                       (model.id, model.revision, json.dumps(model.payload(), sort_keys=True)))
    connection.execute(
        "INSERT INTO heads_v2 VALUES(?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision",
        (model.id, model.revision))
    return model.id, model.revision


def load(connection, model_id, revision):
    row = connection.execute("SELECT factors FROM models_v2 WHERE id=? AND revision=?",
                             (model_id, revision)).fetchone()
    if row is None:
        raise KeyError((model_id, revision))
    return FactorModel.from_payload(json.loads(row[0]))
