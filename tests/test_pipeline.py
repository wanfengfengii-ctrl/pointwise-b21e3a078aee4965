import tempfile
import unittest
from pathlib import Path
from fractions import Fraction as F
from archive import Store
from analysis import Analysis
from results import Results, Failure
from variables import Linear, Observation
from vendor.sdk1 import Model
from samples import build_legacy, MODELS
from errors import ImpossibleObservation
from tasks import Tasks


class PipelineTests(unittest.TestCase):
    def test_revision_binding_reopen_and_receipt(self):
        with tempfile.TemporaryDirectory(prefix="joint-test-") as directory:
            path = Path(directory) / "db.sqlite"
            with Store(path) as store:
                store.put_model("r1", MODELS[0])
                analysis = Analysis(store)
                analysis.save("old", "joint", 1, [Observation.make("x", {"x": 1}, 3)],
                              [Linear.make("y", {"y": 1})])
                before = analysis.run("old")
                store.put_model("r2", MODELS[1])
                analysis.save("new", "joint", 2, [Observation.make("x", {"x": 1}, 7)],
                              [Linear.make("y", {"y": 1})])
                newer = analysis.run("new")
                self.assertEqual(newer.means, (F(13),))
                self.assertEqual(newer.covariance, ((F(9, 2),),))
                self.assertEqual(analysis.run("old"), before)
            with Store(path) as reopened:
                self.assertEqual(Analysis(reopened).run("old"), before)
                self.assertEqual(Results(reopened).get("new"), newer)
                self.assertEqual(reopened.put_model("r1", MODELS[0]), ("joint", 1))
                self.assertEqual(reopened.head("joint"), 2)
                self.assertEqual(reopened.revisions("joint"), (1, 2))

    def test_frozen_complete_legacy_database(self):
        with tempfile.TemporaryDirectory(prefix="joint-fixture-") as directory:
            path = Path(directory) / "legacy.sqlite"
            build_legacy(path)
            with Store(path) as store:
                self.assertEqual(store.revisions("joint"), (1, 2))
                self.assertEqual(store.get_model("joint", 1), Model.from_payload(MODELS[0]))
                self.assertEqual(store.get_model("joint", 2).names, ("y", "x"))
                self.assertEqual(Results(store).get("old-task").means, (F(6),))
                self.assertEqual(Analysis(store).run("waiting-task").means, (F(8),))
                self.assertEqual(store.put_model("seed-0", MODELS[0]), ("joint", 1))
                Analysis(store).save("impossible", "fixed", 1,
                                     [Observation.make("x", {"x": 1}, 0)],
                                     [Linear.make("y", {"y": 1})])
                with self.assertRaises(ImpossibleObservation):
                    Analysis(store).run("impossible")
            with Store(path) as reopened:
                self.assertEqual(Results(reopened).get("impossible"), Failure("impossible"))
                self.assertEqual(Tasks(reopened).status("waiting-task"), "success")

    def test_publication_rollback_reopen_retry(self):
        with tempfile.TemporaryDirectory(prefix="joint-rollback-") as directory:
            path = Path(directory) / "db.sqlite"
            build_legacy(path)
            def fail():
                raise RuntimeError("before commit")
            with Store(path) as store:
                with self.assertRaises(RuntimeError):
                    Analysis(store).run("waiting-task", before_publish=fail)
            with Store(path) as reopened:
                self.assertEqual(Tasks(reopened).status("waiting-task"), "saved")
                self.assertEqual(Analysis(reopened).run("waiting-task").means, (F(8),))
            with Store(path) as reopened:
                self.assertEqual(Results(reopened).get("waiting-task").means, (F(8),))
