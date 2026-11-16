import itertools
import unittest
from fractions import Fraction as F
from archive import Store
from analysis import Analysis
from results import Results, Failure
from tasks import Tasks
from variables import Linear, Observation
from errors import ImpossibleObservation, NotRun
from samples import MODELS


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.addCleanup(self.store.close)
        self.analysis = Analysis(self.store)

    def save_run(self, key, model_id, observations, contrasts):
        self.analysis.save(key, model_id, 1, observations, contrasts)
        return self.analysis.run(key)

    def test_full_joint_covariance(self):
        self.store.put_model("m", MODELS[3])
        result = self.save_run("t", "independent", [Observation.make("x", {"x": 1}, 2)],
                               [Linear.make("y", {"y": 1}), Linear.make("sum", {"x": 1, "y": 1})])
        self.assertEqual(result.means, (F(0), F(2)))
        self.assertEqual(result.covariance, ((F(1), F(1)), (F(1), F(1))))
        self.assertTrue(all(isinstance(v, F) for row in result.covariance for v in row))
        self.assertEqual(Results(self.store).get("t"), result)

    def test_singular_redundant_and_impossible(self):
        self.store.put_model("m", MODELS[0])
        contrasts = [Linear.make("y", {"y": 1}), Linear.make("relation", {"y": 1, "x": -2})]
        one = [Observation.make("x", {"x": 1}, 3)]
        a = self.save_run("one", "joint", one, contrasts)
        self.assertEqual(a.means, (F(6), F(0)))
        self.assertEqual(a.covariance, ((F(0), F(0)), (F(0), F(0))))
        for i, observations in enumerate(itertools.permutations(
                one + [Observation.make("y", {"y": 1}, 6), Observation.make("twice", {"x": 2}, 6)])):
            b = self.save_run("redundant-" + str(i), "joint", observations, contrasts)
            self.assertEqual((a.means, a.covariance), (b.means, b.covariance))
        self.analysis.save("bad", "joint", 1, one + [Observation.make("y", {"y": 1}, 7)], contrasts)
        with self.assertRaises(ImpossibleObservation):
            self.analysis.run("bad")
        self.assertEqual(Results(self.store).get("bad"), Failure("bad"))
        self.assertEqual(Tasks(self.store).status("bad"), "impossible")
        self.assertNotIn("means", self.store.connection.execute(
            "SELECT payload FROM results_v1 WHERE task_id='bad'").fetchone()[0])

    def test_deterministic_offsets_and_zero_rows(self):
        self.store.put_model("m", MODELS[2])
        contrasts = [Linear.make("value", {"x": 2, "y": -1}, offset=3),
                     Linear.make("constant", {}, offset="1/3")]
        result = self.save_run("fixed", "fixed",
                               [Observation.make("equal", {"x": 1}, 5, offset=3),
                                Observation.make("constant", {}, 7, offset=7)], contrasts)
        self.assertEqual(result.means, (F(8), F(1, 3)))
        self.assertEqual(result.covariance, ((F(0), F(0)), (F(0), F(0))))
        self.analysis.save("bad", "fixed", 1, [Observation.make("false", {}, 1)], contrasts)
        with self.assertRaises(ImpossibleObservation):
            self.analysis.run("bad")

    def test_publication_failure_rolls_back(self):
        self.store.put_model("m", MODELS[0])
        self.analysis.save("t", "joint", 1, [], [Linear.make("y", {"y": 1})])
        def fail():
            raise RuntimeError("before result commit")
        with self.assertRaises(RuntimeError):
            self.analysis.run("t", before_publish=fail)
        self.assertEqual(Tasks(self.store).status("t"), "saved")
        with self.assertRaises(NotRun):
            Results(self.store).get("t")
        first = self.analysis.run("t")
        with self.assertRaises(RuntimeError):
            self.analysis.run("t", before_publish=fail)
        self.assertEqual(Results(self.store).get("t"), first)
        self.analysis.save("bad", "joint", 1, [Observation.make("zero", {}, 1)],
                           [Linear.make("y", {"y": 1})])
        with self.assertRaises(RuntimeError):
            self.analysis.run("bad", before_publish=fail)
        self.assertEqual(Tasks(self.store).status("bad"), "saved")
        with self.assertRaises(NotRun):
            Results(self.store).get("bad")

    def test_latent_generator_independent_projection_oracle(self):
        # x=m+Bz, z~N(0,I). Observation r*z=t. Oracle projects z
        # onto the single row r, independently of the SDK1 covariance updates.
        for n in range(1, 6):
            for rank in range(0, 4):
                mean = [F(i - 2, 3) for i in range(n)]
                b = [[F(((i + 1) * (j + 2)) % 5 - 2, j + 1)
                      for j in range(rank)] for i in range(n)]
                cov = [[sum((b[i][k] * b[j][k] for k in range(rank)), F(0))
                        for j in range(n)] for i in range(n)]
                names = ["v" + str(i) for i in range(n)]
                model_id = "g-" + str(n) + "-" + str(rank)
                self.store.put_model(model_id, {"id": model_id, "revision": 1, "names": names,
                                               "mean": mean, "covariance": cov})
                c = [F(i + 1) for i in range(n)]
                r = [sum((c[i] * b[i][k] for i in range(n)), F(0)) for k in range(rank)]
                norm = sum((v * v for v in r), F(0))
                t = F(2, 5) if norm else F(0)
                value = sum((c[i] * mean[i] for i in range(n)), F(0)) + t
                rows = [[F((i + j) % 3 - 1) for i in range(n)] for j in range(3)]
                projected = [[sum((row[i] * b[i][k] for i in range(n)), F(0))
                              for k in range(rank)] for row in rows]
                expected_means = tuple(sum((row[i] * mean[i] for i in range(n)), F(j, 7)) +
                    (sum((a * z for a, z in zip(p, r)), F(0)) * t / norm if norm else 0)
                    for j, (row, p) in enumerate(zip(rows, projected)))
                expected_cov = tuple(tuple(
                    sum((a * z for a, z in zip(p, s)), F(0)) -
                    (sum((a * z for a, z in zip(p, r)), F(0)) *
                     sum((a * z for a, z in zip(s, r)), F(0)) / norm if norm else 0)
                    for s in projected) for p in projected)
                result = self.save_run(model_id, model_id,
                    [Observation.make("measured", dict(zip(names, c)), value)],
                    [Linear.make("c" + str(j), dict(zip(names, row)), F(j, 7))
                     for j, row in enumerate(rows)])
                self.assertEqual((result.means, result.covariance), (expected_means, expected_cov))
