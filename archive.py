"""Immutable SDK1 model archive and request receipts."""
import json
from db import connect, initialize, atomic
from vendor.sdk1 import Model
from variables import identifier
from errors import InvalidModel, RequestConflict, UnknownRevision


def encode(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Store:
    def __init__(self, path=":memory:"):
        self.path = str(path)
        self.connection = connect(self.path)
        try:
            initialize(self.connection)
        except BaseException:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def put_model(self, request_id, model):
        identifier(request_id, "request id")
        try:
            model = Model.from_payload(model.payload() if isinstance(model, Model) else model)
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidModel(str(exc)) from exc
        payload = encode(model.payload())
        with atomic(self.connection):
            receipt = self.connection.execute(
                "SELECT payload,model_id,revision FROM receipts_v1 WHERE request_id=?",
                (request_id,)).fetchone()
            if receipt:
                if receipt[0] != payload:
                    raise RequestConflict(request_id)
                return receipt[1], receipt[2]
            head = self.connection.execute("SELECT revision FROM heads_v1 WHERE id=?",
                                           (model.id,)).fetchone()
            expected = 1 if head is None else head[0] + 1
            if model.revision != expected:
                raise InvalidModel("new revision must be head+1 (first revision is 1)")
            self.connection.execute("INSERT INTO models_v1 VALUES(?,?,?)",
                                    (model.id, model.revision, payload))
            self.connection.execute(
                "INSERT INTO heads_v1 VALUES(?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision",
                (model.id, model.revision))
            self.connection.execute("INSERT INTO receipts_v1 VALUES(?,?,?,?)",
                                    (request_id, payload, model.id, model.revision))
        return model.id, model.revision

    def get_model(self, model_id, revision):
        row = self.connection.execute("SELECT payload FROM models_v1 WHERE id=? AND revision=?",
                                      (model_id, revision)).fetchone()
        if row is None:
            raise UnknownRevision((model_id, revision))
        return Model.from_payload(json.loads(row[0]))

    def head(self, model_id):
        row = self.connection.execute("SELECT revision FROM heads_v1 WHERE id=?", (model_id,)).fetchone()
        if row is None:
            raise UnknownRevision(model_id)
        return row[0]

    def revisions(self, model_id):
        return tuple(row[0] for row in self.connection.execute(
            "SELECT revision FROM models_v1 WHERE id=? ORDER BY revision", (model_id,)))

    def receipt(self, request_id):
        row = self.connection.execute(
            "SELECT model_id,revision FROM receipts_v1 WHERE request_id=?", (request_id,)).fetchone()
        return None if row is None else tuple(row)
