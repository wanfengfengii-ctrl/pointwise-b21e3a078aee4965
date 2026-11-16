"""One atomic publication per task, never partial numeric rows."""
from dataclasses import dataclass
import json
from fractions import Fraction
from archive import encode
from db import atomic, version
from errors import UnknownTask, NotRun


def _tables(connection):
    suffix = "v2" if version(connection) == "2" else "v1"
    return "tasks_" + suffix, "results_" + suffix


@dataclass(frozen=True)
class Result:
    task_id: str
    model_id: str
    revision: int
    labels: tuple
    means: tuple
    covariance: tuple


@dataclass(frozen=True)
class Failure:
    task_id: str
    error: str = "ImpossibleObservation"


class Results:
    def __init__(self, store):
        self.store = store

    def publish(self, task, result, before_commit=None):
        if isinstance(result, Failure):
            if result.task_id != task.id or result.error != "ImpossibleObservation":
                raise ValueError("failure identity")
            status, payload = "impossible", {"error": result.error}
        else:
            labels = tuple(v.name for v in task.contrasts)
            n = len(labels)
            if (result.task_id, result.model_id, result.revision, result.labels) != (
                    task.id, task.model_id, task.revision, labels):
                raise ValueError("result identity")
            if len(result.means) != n or len(result.covariance) != n or any(
                    len(row) != n for row in result.covariance):
                raise ValueError("incomplete result")
            numbers = list(result.means) + [v for row in result.covariance for v in row]
            if any(not isinstance(v, Fraction) for v in numbers):
                raise ValueError("result must use Fraction")
            status = "success"
            payload = {"model_id": result.model_id, "revision": result.revision,
                       "labels": list(result.labels), "means": [str(v) for v in result.means],
                       "covariance": [[str(v) for v in row] for row in result.covariance]}
        tasks, results = _tables(self.store.connection)
        with atomic(self.store.connection, before_commit):
            self.store.connection.execute(
                "INSERT INTO " + results + " VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                "status=excluded.status,payload=excluded.payload", (task.id, status, encode(payload)))
            self.store.connection.execute("UPDATE " + tasks + " SET status=? WHERE task_id=?",
                                          (status, task.id))

    def get(self, task_id):
        tasks, results = _tables(self.store.connection)
        exists = self.store.connection.execute(
            "SELECT 1 FROM " + tasks + " WHERE task_id=?", (task_id,)).fetchone()
        if exists is None:
            raise UnknownTask(task_id)
        row = self.store.connection.execute(
            "SELECT status,payload FROM " + results + " WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise NotRun(task_id)
        obj = json.loads(row[1])
        if row[0] == "impossible":
            return Failure(task_id, obj["error"])
        return Result(task_id, obj["model_id"], obj["revision"], tuple(obj["labels"]),
                      tuple(Fraction(v) for v in obj["means"]),
                      tuple(tuple(Fraction(v) for v in r) for r in obj["covariance"]))
