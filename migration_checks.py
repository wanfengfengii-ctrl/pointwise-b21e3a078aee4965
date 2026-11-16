"""Public upgrade checks and a computed end-to-end demonstration; no converter."""
import argparse
from contextlib import contextmanager
from fractions import Fraction as F
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from archive import Store
from analysis import Analysis
from db import atomic
from errors import ImpossibleObservation, RequestConflict, NotRun
from results import Results, Failure
from samples import build_legacy, MODELS
from tasks import Tasks
from variables import Linear, Observation
from vendor import sdk1, sdk2


V1 = ("models_v1", "heads_v1", "receipts_v1", "tasks_v1", "results_v1")
V2 = tuple(name.replace("_v1", "_v2") for name in V1)


def upgrade(store, before_commit=None):
    return importlib.import_module("migration").upgrade(store, before_commit=before_commit)


def state(store, tables):
    return {table: store.connection.execute(
        "SELECT * FROM " + table + " ORDER BY 1,2").fetchall() for table in tables}


def version(store):
    return store.connection.execute(
        "SELECT value FROM meta WHERE key='version'").fetchone()[0]


def native_joint():
    return sdk2.FactorModel("joint", 3, ["y", "x"], [[1, 1], [2, 2]], [3, 6],
                            [[1, 0], [0, 1]], [0, 0], [1, 2])


@contextmanager
def target_only(store):
    def authorize(action, table, column, database, source):
        if action == sqlite3.SQLITE_READ and table in V1:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    store.connection.set_authorizer(authorize)
    try:
        with patch.object(sdk1.Model, "conditional",
                          side_effect=AssertionError("SDK1 conditioning after upgrade")):
            yield
    finally:
        store.connection.set_authorizer(None)


def fixture_check():
    # Checks supplied ports/data, independently of the missing upgrade.
    model = native_joint()
    assert model.energy([2, 1]) == F(6)
    assert model.energy([1, 2]) == F(9)
    try:
        model.energy([2, 2])
    except ValueError:
        pass
    else:
        raise AssertionError("native hard support not enforced")
    with Store() as store:
        for i, payload in enumerate(MODELS):
            store.put_model("fixture-" + str(i), payload)
        a = Analysis(store)
        a.save("fixture", "joint", 1, [Observation.make("x", {"x": 1}, 3)],
               [Linear.make("y", {"y": 1})])
        result = a.run("fixture")
        assert result.means == (F(6),)
        assert result.covariance == ((F(0),),)
    return {"native_energy_at_2_1": str(model.energy([2, 1])),
            "legacy_conditional_y": str(result.means[0])}


class MigrationChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="joint-migration-")
        self.path = Path(self.directory.name) / "archive.sqlite"
        build_legacy(self.path)
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def reopen(self):
        self.store.close()
        self.store = Store(self.path)

    def test_archive_tasks_and_native_handoff(self):
        a, tasks, results = Analysis(self.store), Tasks(self.store), Results(self.store)
        a.save("previous-failure", "fixed", 1,
               [Observation.make("bad", {"x": 1}, 0)], [Linear.make("y", {"y": 1})])
        with self.assertRaises(ImpossibleObservation):
            a.run("previous-failure")
        original = state(self.store, V1)
        old_result = results.get("old-task")
        self.assertIsNone(upgrade(self.store))
        self.assertEqual(version(self.store), "2")
        for old, new in zip(V1, V2):
            self.assertEqual(len(original[old]), len(state(self.store, (new,))[new]))
        with target_only(self.store):
            for payload in MODELS:
                m = self.store.get_model(payload["id"], payload["revision"])
                self.assertEqual((m.names, m.mean, m.covariance),
                                 (sdk1.Model.from_payload(payload).names,
                                  sdk1.Model.from_payload(payload).mean,
                                  sdk1.Model.from_payload(payload).covariance))
            self.assertEqual(results.get("old-task"), old_result)
            self.assertEqual(results.get("previous-failure"), Failure("previous-failure"))
            self.assertEqual(tasks.status("waiting-task"), "saved")
            self.assertEqual(a.run("waiting-task").means, (F(8),))
            self.assertEqual(a.run("old-task"), old_result)
            factor = sdk2.load(self.store.connection, "joint", 1)
            self.assertEqual(factor.energy([3, 6]) - factor.energy([1, 2]), F(4))
            with self.assertRaises(ValueError):
                factor.energy([3, 7])
            with atomic(self.store.connection):
                sdk2.append(self.store.connection, native_joint())
            self.assertEqual(self.store.head("joint"), 3)
            self.assertEqual(self.store.revisions("joint"), (1, 2, 3))
            m = self.store.get_model("joint", 3)
            self.assertEqual(m.names, ("y", "x"))
            self.assertEqual(m.mean, (F(2), F(1)))
            self.assertEqual(m.covariance, ((F(1, 3), F(-1, 3)), (F(-1, 3), F(1, 3))))
            a.save("native", "joint", 3, [], [Linear.make("y", {"y": 1}),
                                           Linear.make("sum", {"x": 1, "y": 1})])
            n = a.run("native")
            self.assertEqual(n.means, (F(2), F(3)))
            self.assertEqual(n.covariance, ((F(1, 3), F(0)), (F(0), F(0))))
            a.save("native-conditioned", "joint", 3,
                   [Observation.make("x", {"x": 1}, 2)], [Linear.make("y", {"y": 1})])
            self.assertEqual(a.run("native-conditioned").means, (F(1),))
            self.assertEqual(a.run("old-task"), old_result)
            self.assertEqual(self.store.put_model("seed-0", MODELS[0]), ("joint", 1))
            latest = dict(MODELS[1], revision=4, mean=["4", "1"])
            with self.assertRaises(RequestConflict):
                self.store.put_model("seed-0", latest)
            self.assertEqual(self.store.put_model("after", latest), ("joint", 4))
            a.save("after-write", "joint", 4, [], [Linear.make("y", {"y": 1})])
            self.assertEqual(a.run("after-write").means, (F(4),))
        self.assertEqual(state(self.store, V1), original)
        self.reopen()
        with target_only(self.store):
            self.assertEqual(Analysis(self.store).run("old-task"), old_result)
            self.assertEqual(Results(self.store).get("native"), n)
            self.assertEqual(self.store.head("joint"), 4)
            self.assertEqual(self.store.receipt("seed-0"), ("joint", 1))

    def test_precommit_failure_reopen_and_idempotence(self):
        original = state(self.store, V1)
        calls = []
        def fail():
            calls.append(version(self.store))
            self.assertEqual(version(self.store), "2")
            raise RuntimeError("public-before-commit")
        with self.assertRaisesRegex(RuntimeError, "public-before-commit"):
            upgrade(self.store, before_commit=fail)
        self.assertEqual(calls, ["2"])
        self.assertEqual(version(self.store), "1")
        self.assertEqual(state(self.store, V1), original)
        self.assertEqual(Results(self.store).get("old-task").means, (F(6),))
        self.reopen()
        self.assertEqual(version(self.store), "1")
        self.assertIsNone(upgrade(self.store))
        first = state(self.store, V2)
        def unexpected():
            raise AssertionError("idempotent upgrade invoked hook")
        self.assertIsNone(upgrade(self.store, before_commit=unexpected))
        self.assertEqual(state(self.store, V2), first)
        self.assertEqual(state(self.store, V1), original)
        self.reopen()
        with target_only(self.store):
            self.assertEqual(Analysis(self.store).run("waiting-task").means, (F(8),))

    def test_independent_multicondition_and_publication(self):
        # Independent construction: x=(1+a,2+2a,-1+b,4+a+b+c),
        # a,b,c independent unit-variance variables. Conditions fix a=2,b=3.
        payload = {"id": "latent", "revision": 1, "names": ["p", "q", "r", "s"],
                   "mean": [1, 2, -1, 4],
                   "covariance": [[1, 2, 0, 1], [2, 4, 0, 2],
                                  [0, 0, 1, 1], [1, 2, 1, 3]]}
        self.store.put_model("latent", payload)
        a = Analysis(self.store)
        obs = [Observation.make("p", {"p": 1}, 3), Observation.make("q", {"q": 1}, 6),
               Observation.make("r", {"r": 1}, 2), Observation.make("twice", {"p": 2}, 6),
               Observation.make("constant", {}, 0),
               Observation.make("relation", {"q": 1, "p": -2}, 0)]
        contrasts = [Linear.make("q", {"q": 1}), Linear.make("s", {"s": 1}),
                     Linear.make("s+r", {"s": 1, "r": 1})]
        a.save("multi", "latent", 1, obs, contrasts)
        a.save("bad", "latent", 1,
               [Observation.make("p", {"p": 1}, 3), Observation.make("q", {"q": 1}, 7)],
               contrasts)
        a.save("fixed", "fixed", 1, [Observation.make("x", {"x": 1}, 2)],
               [Linear.make("y", {"y": 1})])
        upgrade(self.store)
        with target_only(self.store):
            def fail():
                raise RuntimeError("publication")
            with self.assertRaisesRegex(RuntimeError, "publication"):
                a.run("multi", before_publish=fail)
            self.assertEqual(Tasks(self.store).status("multi"), "saved")
            with self.assertRaises(NotRun):
                Results(self.store).get("multi")
            result = a.run("multi")
            self.assertEqual(result.means, (F(6), F(9), F(11)))
            self.assertEqual(result.covariance,
                             ((F(0), F(0), F(0)), (F(0), F(1), F(1)), (F(0), F(1), F(1))))
            self.assertTrue(all(isinstance(v, F) for v in result.means))
            self.assertTrue(all(isinstance(v, F) for row in result.covariance for v in row))
            with self.assertRaises(ImpossibleObservation):
                a.run("bad")
            self.assertEqual(Results(self.store).get("bad"), Failure("bad"))
            raw = self.store.connection.execute(
                "SELECT payload FROM results_v2 WHERE task_id='bad'").fetchone()[0]
            self.assertNotIn("means", json.loads(raw))
            fixed = a.run("fixed")
            self.assertEqual((fixed.means, fixed.covariance), ((F(-1),), ((F(0),),)))

    def test_native_residuals_are_not_equalities(self):
        upgrade(self.store)
        factor = sdk2.FactorModel("balanced", 1, ["z"], [], [],
                                  [[1], [1]], [1, 3], [1, 1])
        with atomic(self.store.connection):
            sdk2.append(self.store.connection, factor)
        with target_only(self.store):
            a = Analysis(self.store)
            a.save("balanced", "balanced", 1, [], [Linear.make("z", {"z": 1})])
            result = a.run("balanced")
            self.assertEqual(result.means, (F(2),))
            self.assertEqual(result.covariance, ((F(1, 2),),))


def demo():
    with tempfile.TemporaryDirectory(prefix="joint-upgrade-demo-") as directory:
        path = Path(directory) / "archive.sqlite"
        build_legacy(path)
        with Store(path) as store:
            old = Results(store).get("old-task")
            def fail():
                raise RuntimeError("demo-before-commit")
            try:
                upgrade(store, before_commit=fail)
            except RuntimeError as exc:
                if str(exc) != "demo-before-commit":
                    raise
            else:
                raise AssertionError("failure hook was not invoked")
            assert version(store) == "1"
            assert Results(store).get("old-task") == old
        with Store(path) as store:
            a = Analysis(store)
            upgrade(store)
            with target_only(store):
                assert a.run("old-task") == old
                with atomic(store.connection):
                    sdk2.append(store.connection, native_joint())
                a.save("native-demo", "joint", 3, [], [Linear.make("y", {"y": 1})])
                new = a.run("native-demo")
                assert new.means == (F(2),)
                assert new.covariance == ((F(1, 3),),)
                assert a.run("old-task") == old
        with Store(path) as store:
            with target_only(store):
                reopened = Results(store).get("native-demo")
                assert reopened == new
                assert Analysis(store).run("old-task") == old
                return {"version": version(store), "old_revision": old.revision,
                        "old_y": str(old.means[0]), "native_revision": new.revision,
                        "native_y": str(new.means[0]), "native_variance": str(new.covariance[0][0]),
                        "head": store.head("joint"), "reopened_equal": reopened == new,
                        "rollback_kept_old_result": True}


def main():
    parser = argparse.ArgumentParser()
    import sys
    mode = sys.argv[1:] or ["--verify"]
    if mode == ["--fixtures"]:
        print(json.dumps(fixture_check(), sort_keys=True))
        return 0
    if mode == ["--demo"]:
        print(json.dumps(demo(), sort_keys=True))
        return 0
    if mode != ["--verify"]:
        parser.error("use --fixtures, --verify or --demo")
    root = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), top_level_dir=str(root))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(MigrationChecks))
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
