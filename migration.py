"""Atomic v1 -> v2 storage upgrade; v1 tables stay as a frozen audit trail."""
import json
from db import atomic, version
from factors import from_moments
from vendor import sdk1, sdk2


def upgrade(store, before_commit=None):
    """Move all business state to the SDK2 tables and switch meta to '2'.

    Every models_v1 revision is converted to an equivalent FactorModel and
    appended in (id, revision) order, which rebuilds heads_v2; receipts,
    tasks and results are copied verbatim. Safe to repeat: a store already
    at version '2' is left untouched and the hook is not called. Unknown
    versions are rejected without any write. before_commit runs once, after
    every conversion and the version switch, still inside the transaction;
    its exception rolls the whole upgrade back.
    """
    connection = store.connection
    current = version(connection)
    if current == "2":
        return None
    if current != "1":
        raise ValueError("unsupported archive version")
    with atomic(connection, before_commit):
        sdk2.install(connection)
        rows = connection.execute(
            "SELECT id,revision,payload FROM models_v1 ORDER BY id,revision").fetchall()
        for model_id, revision, payload in rows:
            model = sdk1.Model.from_payload(json.loads(payload))
            sdk2.append(connection, from_moments(model.id, model.revision, model.names,
                                                 model.mean, model.covariance))
        connection.execute(
            "INSERT INTO receipts_v2(request_id,payload,model_id,revision) "
            "SELECT request_id,payload,model_id,revision FROM receipts_v1")
        connection.execute(
            "INSERT INTO tasks_v2(task_id,model_id,revision,payload,status) "
            "SELECT task_id,model_id,revision,payload,status FROM tasks_v1")
        connection.execute(
            "INSERT INTO results_v2(task_id,status,payload) "
            "SELECT task_id,status,payload FROM results_v1")
        connection.execute("UPDATE meta SET value='2' WHERE key='version'")
    return None
