"""Immutable model archive and request receipts over the active storage version.

Version '1' serves the original SDK1 tables. After migration.upgrade flips
meta to '2', the same interface is served from the SDK2 tables: put_model
converts the distribution to a FactorModel, and get_model recovers the exact
moments from the stored factor. The v1 tables are then frozen audit data.
"""
import json
from db import connect, initialize, atomic, version
from vendor.sdk1 import Model
from vendor import sdk2
import factors
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
        if version(self.connection) == "2":
            return self._put_factor_model(request_id, model, payload)
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

    def _put_factor_model(self, request_id, model, payload):
        # The legacy write interface stores only the target factor; the
        # canonical SDK1 payload is kept solely as the request identity.
        factor = factors.from_moments(model.id, model.revision, model.names,
                                      model.mean, model.covariance)
        with atomic(self.connection):
            receipt = self.connection.execute(
                "SELECT payload,model_id,revision FROM receipts_v2 WHERE request_id=?",
                (request_id,)).fetchone()
            if receipt:
                if receipt[0] != payload:
                    raise RequestConflict(request_id)
                return receipt[1], receipt[2]
            head = self.connection.execute("SELECT revision FROM heads_v2 WHERE id=?",
                                           (model.id,)).fetchone()
            expected = 1 if head is None else head[0] + 1
            if model.revision != expected:
                raise InvalidModel("new revision must be head+1 (first revision is 1)")
            sdk2.append(self.connection, factor)
            self.connection.execute("INSERT INTO receipts_v2 VALUES(?,?,?,?)",
                                    (request_id, payload, model.id, model.revision))
        return model.id, model.revision

    def get_model(self, model_id, revision):
        if version(self.connection) == "2":
            try:
                factor = sdk2.load(self.connection, model_id, revision)
            except KeyError:
                raise UnknownRevision((model_id, revision)) from None
            mean, covariance = factors.moments(factor)
            return Model(model_id, revision, factor.names, mean, covariance)
        row = self.connection.execute("SELECT payload FROM models_v1 WHERE id=? AND revision=?",
                                      (model_id, revision)).fetchone()
        if row is None:
            raise UnknownRevision((model_id, revision))
        return Model.from_payload(json.loads(row[0]))

    def head(self, model_id):
        table = "heads_v2" if version(self.connection) == "2" else "heads_v1"
        row = self.connection.execute("SELECT revision FROM " + table + " WHERE id=?",
                                      (model_id,)).fetchone()
        if row is None:
            raise UnknownRevision(model_id)
        return row[0]

    def revisions(self, model_id):
        table = "models_v2" if version(self.connection) == "2" else "models_v1"
        return tuple(row[0] for row in self.connection.execute(
            "SELECT revision FROM " + table + " WHERE id=? ORDER BY revision", (model_id,)))

    def receipt(self, request_id):
        table = "receipts_v2" if version(self.connection) == "2" else "receipts_v1"
        row = self.connection.execute(
            "SELECT model_id,revision FROM " + table + " WHERE request_id=?",
            (request_id,)).fetchone()
        return None if row is None else tuple(row)
