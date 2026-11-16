import unittest
from archive import Store
from analysis import Analysis
from variables import Linear, Observation
from results import Results
from tasks import Tasks
from errors import UnknownVariable, UnknownTask, UnknownRevision, TaskConflict, NotRun, InvalidTask
from samples import MODELS


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.addCleanup(self.store.close)
        self.store.put_model("m", MODELS[0])
        self.analysis = Analysis(self.store)

    def test_unknown_variable_not_saved(self):
        for obs, contrasts in (([Observation.make("z", {"z": 1}, 0)], [Linear.make("y", {"y": 1})]),
                               ([], [Linear.make("z", {"z": 1})])):
            with self.assertRaises(UnknownVariable):
                self.analysis.save("bad", "joint", 1, obs, contrasts)
            with self.assertRaises(UnknownTask):
                Tasks(self.store).get("bad")

    def test_conflicts_and_states(self):
        contrast = [Linear.make("y", {"y": 1})]
        task = self.analysis.save("t", "joint", 1, [], contrast)
        self.assertEqual(self.analysis.save("t", "joint", 1, [], contrast), task)
        with self.assertRaises(NotRun):
            Results(self.store).get("t")
        with self.assertRaises(TaskConflict):
            self.analysis.save("t", "joint", 1, [Observation.make("x", {"x": 1}, 3)], contrast)
        self.assertEqual(Tasks(self.store).status("t"), "saved")
        self.analysis.run("t")
        self.analysis.save("t", "joint", 1, [], contrast)
        self.assertEqual(Tasks(self.store).status("t"), "success")
        with self.assertRaises(UnknownRevision):
            self.analysis.save("missing", "joint", 2, [], contrast)
        with self.assertRaises(UnknownTask):
            self.analysis.run("missing")

    def test_limits_and_names(self):
        with self.assertRaises(InvalidTask):
            self.analysis.save("none", "joint", 1, [], [])
        with self.assertRaises(InvalidTask):
            self.analysis.save("duplicate", "joint", 1, [], [Linear.make("a", {}), Linear.make("a", {})])
        with self.assertRaises(InvalidTask):
            self.analysis.save("many", "joint", 1,
                               [Observation.make(str(i), {"x": 1}, 1) for i in range(7)],
                               [Linear.make("y", {"y": 1})])
