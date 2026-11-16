"""Persist a task only after binding all expressions to its revision."""
from dataclasses import dataclass
import json
from archive import encode
from db import atomic, version
from exact import q
from variables import Linear, Observation, identifier
from errors import InvalidTask, TaskConflict, UnknownTask


def _table(connection):
    return "tasks_v2" if version(connection) == "2" else "tasks_v1"


@dataclass(frozen=True)
class Task:
    id: str
    model_id: str
    revision: int
    observations: tuple
    contrasts: tuple

    def payload(self):
        return {"model_id": self.model_id, "revision": self.revision,
                "observations": [v.payload() for v in self.observations],
                "contrasts": [v.payload() for v in self.contrasts]}

    @classmethod
    def decode(cls, task_id, payload):
        obj = json.loads(payload)
        return cls(task_id, obj["model_id"], obj["revision"],
                   tuple(Observation.from_payload(v) for v in obj["observations"]),
                   tuple(Linear.from_payload(v) for v in obj["contrasts"]))


class Tasks:
    def __init__(self, store):
        self.store = store

    def save(self, task_id, model_id, revision, observations, contrasts):
        identifier(task_id, "task id")
        model = self.store.get_model(model_id, revision)
        try:
            observations = tuple(Observation(
                Linear.make(v.expression.name, v.expression.terms, v.expression.offset), q(v.value))
                                 for v in observations)
            contrasts = tuple(Linear.make(v.name, v.terms, v.offset) for v in contrasts)
            if len(observations) > 6 or not 1 <= len(contrasts) <= 8:
                raise InvalidTask("at most 6 observations and 1..8 contrasts")
            for group in (tuple(v.expression for v in observations), contrasts):
                if len({v.name for v in group}) != len(group):
                    raise InvalidTask("duplicate expression name")
                for expression in group:
                    expression.bind(model.names)
        except (TypeError, ValueError, AttributeError, ZeroDivisionError) as exc:
            raise InvalidTask(str(exc)) from exc
        task = Task(task_id, model_id, revision, observations, contrasts)
        payload = encode(task.payload())
        table = _table(self.store.connection)
        with atomic(self.store.connection):
            old = self.store.connection.execute(
                "SELECT payload FROM " + table + " WHERE task_id=?", (task_id,)).fetchone()
            if old:
                if old[0] != payload:
                    raise TaskConflict(task_id)
            else:
                self.store.connection.execute(
                    "INSERT INTO " + table + " VALUES(?,?,?,?,?)",
                    (task_id, model_id, revision, payload, "saved"))
        return task

    def get(self, task_id):
        row = self.store.connection.execute(
            "SELECT payload FROM " + _table(self.store.connection) + " WHERE task_id=?",
            (task_id,)).fetchone()
        if row is None:
            raise UnknownTask(task_id)
        return Task.decode(task_id, row[0])

    def status(self, task_id):
        row = self.store.connection.execute(
            "SELECT status FROM " + _table(self.store.connection) + " WHERE task_id=?",
            (task_id,)).fetchone()
        if row is None:
            raise UnknownTask(task_id)
        return row[0]
