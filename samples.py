"""Finite public business data, including one complete legacy database.

build_legacy() is a frozen SQL producer: it does not call Store or Analysis.
"""
import json
import sqlite3


MODELS = (
    {"id": "joint", "revision": 1, "names": ["x", "y"],
     "mean": ["1", "2"], "covariance": [["1", "2"], ["2", "4"]]},
    {"id": "joint", "revision": 2, "names": ["y", "x"],
     "mean": ["10", "5"], "covariance": [["9", "3"], ["3", "2"]]},
    {"id": "fixed", "revision": 1, "names": ["x", "y"],
     "mean": ["2", "-1"], "covariance": [["0", "0"], ["0", "0"]]},
    {"id": "independent", "revision": 1, "names": ["x", "y"],
     "mean": ["0", "0"], "covariance": [["1", "0"], ["0", "1"]]}
)

LEGACY_SCHEMA = (
    "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
    "CREATE TABLE models_v1(id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(id,revision))",
    "CREATE TABLE heads_v1(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,FOREIGN KEY(id,revision) REFERENCES models_v1(id,revision))",
    "CREATE TABLE receipts_v1(request_id TEXT PRIMARY KEY,payload TEXT NOT NULL,model_id TEXT NOT NULL,revision INTEGER NOT NULL,FOREIGN KEY(model_id,revision) REFERENCES models_v1(id,revision))",
    "CREATE TABLE tasks_v1(task_id TEXT PRIMARY KEY,model_id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('saved','success','impossible')),FOREIGN KEY(model_id,revision) REFERENCES models_v1(id,revision))",
    "CREATE TABLE results_v1(task_id TEXT PRIMARY KEY REFERENCES tasks_v1(task_id),status TEXT NOT NULL CHECK(status IN ('success','impossible')),payload TEXT NOT NULL)"
)


def build_legacy(path):
    """Create a fresh v1 database; refuse an existing populated database."""
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            for statement in LEGACY_SCHEMA:
                connection.execute(statement)
            connection.execute("INSERT INTO meta VALUES('version','1')")
            for i, model in enumerate(MODELS):
                payload = json.dumps(model, sort_keys=True, separators=(",", ":"))
                connection.execute("INSERT INTO models_v1 VALUES(?,?,?)",
                                   (model["id"], model["revision"], payload))
                connection.execute(
                    "INSERT INTO heads_v1 VALUES(?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision",
                    (model["id"], model["revision"]))
                connection.execute("INSERT INTO receipts_v1 VALUES(?,?,?,?)",
                                   ("seed-" + str(i), payload, model["id"], model["revision"]))
            expression = {"name": "x_exact", "terms": [["x", "1"]], "offset": "0"}
            contrast = {"name": "y", "terms": [["y", "1"]], "offset": "0"}
            for task_id, value in (("old-task", "3"), ("waiting-task", "4")):
                task = {"model_id": "joint", "revision": 1,
                        "observations": [{"expression": expression, "value": value}],
                        "contrasts": [contrast]}
                connection.execute("INSERT INTO tasks_v1 VALUES(?,?,?,?,?)",
                                   (task_id, "joint", 1, json.dumps(task, sort_keys=True,
                                    separators=(",", ":")), "success" if task_id == "old-task" else "saved"))
            result = {"model_id": "joint", "revision": 1, "labels": ["y"],
                      "means": ["6"], "covariance": [["0"]]}
            connection.execute("INSERT INTO results_v1 VALUES(?,?,?)",
                               ("old-task", "success", json.dumps(result)))
    finally:
        connection.close()
