"""Independent exact cross-checks for the upgraded SDK2 representation.

Oracles are latent generators x = mean + B z (z ~ N(0, I)) with hand
projection formulas, and the direct density identity energy(mean + Bz) =
|z|^2. Nothing here consults SDK1 outputs.
"""
import unittest
from fractions import Fraction as F

from archive import Store
from analysis import Analysis
from db import atomic
from errors import ImpossibleObservation, RequestConflict
from exact import dot, matmul, transpose
from migration import upgrade
from results import Results, Failure
from variables import Linear, Observation
from vendor import sdk2


def latent_payload(model_id, names, mean, b):
    """v1 payload of x = mean + b z, z ~ N(0, I); b is n x r full column rank."""
    n, r = len(names), len(b[0]) if b else 0
    cov = [[sum((b[i][k] * b[j][k] for k in range(r)), F(0)) for j in range(n)]
           for i in range(n)]
    return {"id": model_id, "revision": 1, "names": list(names),
            "mean": [str(v) for v in mean],
            "covariance": [[str(v) for v in row] for row in cov]}


def latent_covariance(names, b):
    n, r = len(names), len(b[0]) if b else 0
    return tuple(tuple(sum((b[i][k] * b[j][k] for k in range(r)), F(0))
                       for j in range(n)) for i in range(n))


def point_at(mean, b, z):
    if not b:
        return list(mean)
    return [mean[i] + dot(b[i], z) for i in range(len(mean))]


def projection_oracle(mean, b, a, v, p, offsets):
    """Moments of P x + offsets given A x = v, for x = mean + B z, z ~ N(0, I).

    Requires C = A B to have pairwise orthogonal nonzero rows, so the exact
    conditional is z | Cz=t with mean sum_k c_k' t_k / |c_k|^2 and covariance
    I - sum_k c_k' c_k / |c_k|^2, computed here by plain projections.
    """
    c = matmul(a, b)
    for i in range(len(c)):
        for j in range(i):
            assert not dot(c[i], c[j]), "oracle needs orthogonal condition rows"
    d = [dot(row, row) for row in c]
    assert all(d)
    t = [v[i] - dot(a[i], mean) for i in range(len(a))]
    r = len(b[0])
    zhat = [sum((c[k][j] * t[k] / d[k] for k in range(len(c))), F(0)) for j in range(r)]
    sz = [[(F(1) if i == j else F(0)) -
           sum((c[k][i] * c[k][j] / d[k] for k in range(len(c))), F(0))
           for j in range(r)] for i in range(r)]
    xmean = [mean[i] + dot(b[i], zhat) for i in range(len(mean))]
    xcov = matmul(matmul(b, sz), transpose(b))
    means = tuple(dot(row, xmean) + off for row, off in zip(p, offsets))
    cov = tuple(tuple(dot(r1, [dot(col, r2) for col in xcov]) for r2 in p) for r1 in p)
    return means, cov


class DensityIdentityTests(unittest.TestCase):
    """energy(mean + Bz) == |z|^2 exactly; off-support points are rejected."""

    def setUp(self):
        self.store = Store()
        self.addCleanup(self.store.close)

    def test_full_rank_rank_deficient_and_point(self):
        cases = (
            ("full", ["x", "y"], [F(1), F(-2)],
             [[F(1), F(0)], [F(1), F(1)]]),
            ("flat", ["x", "y", "z"], [F(0), F(1), F(2)],
             [[F(1), F(0)], [F(1), F(0)], [F(0), F(1)]]),
            ("point", ["s"], [F(3)], []),
        )
        for i, (model_id, names, mean, b) in enumerate(cases):
            self.store.put_model("seed-" + str(i), latent_payload(model_id, names, mean, b))
        upgrade(self.store)
        grid = ([F(0)] * 2, [F(1)] * 2, [F(-1, 2), F(2, 3)], [F(2), F(-3)])
        for model_id, names, mean, b in cases:
            factor = sdk2.load(self.store.connection, model_id, 1)
            r = len(b[0]) if b else 0
            for z in grid:
                z = list(z[:r]) + [F(0)] * max(0, r - 2)
                self.assertEqual(factor.energy(point_at(mean, b, z)),
                                 sum((v * v for v in z), F(0)))
        # Hand-computed density at an arbitrary point of the full-rank case:
        # x - mean = (-1, 2) = B z with z = (-1, 3), so energy = 1 + 9 = 10.
        self.assertEqual(sdk2.load(self.store.connection, "full", 1).energy([0, 0]), F(10))
        flat = sdk2.load(self.store.connection, "flat", 1)
        self.assertEqual(flat.energy([0, 1, 2]), F(0))
        with self.assertRaises(ValueError):
            flat.energy([0, 0, 2])  # y - x must equal 1 on the support
        point = sdk2.load(self.store.connection, "point", 1)
        self.assertEqual(point.energy([3]), F(0))
        with self.assertRaises(ValueError):
            point.energy([4])

    def test_recovered_moments_and_variable_reorder(self):
        names = ["a", "b", "c"]
        mean = [F(1), F(0), F(-1)]
        b = [[F(1), F(0)], [F(1), F(1)], [F(0), F(2)]]
        cov = latent_covariance(names, b)
        self.store.put_model("w1", latent_payload("cal", names, mean, b))
        perm = (2, 1, 0)
        reordered = {"id": "cal-re", "revision": 1, "names": ["c", "b", "a"],
                     "mean": [str(mean[i]) for i in perm],
                     "covariance": [[str(cov[i][j]) for j in perm] for i in perm]}
        self.store.put_model("w2", reordered)
        upgrade(self.store)
        got = self.store.get_model("cal", 1)
        self.assertEqual((got.names, got.mean, got.covariance),
                         (tuple(names), tuple(mean), cov))
        got_re = self.store.get_model("cal-re", 1)
        self.assertEqual(got_re.names, ("c", "b", "a"))
        self.assertEqual(got_re.mean, tuple(mean[i] for i in perm))
        self.assertEqual(got_re.covariance,
                         tuple(tuple(cov[i][j] for j in perm) for i in perm))
        # The reordered law is the same density on permuted coordinates.
        factor = sdk2.load(self.store.connection, "cal", 1)
        factor_re = sdk2.load(self.store.connection, "cal-re", 1)
        for z in ([F(0), F(0)], [F(2), F(-1)], [F(1, 3), F(5, 7)]):
            x = point_at(mean, b, z)
            self.assertEqual(factor_re.energy([x[i] for i in perm]), factor.energy(x))


class ConditioningOracleTests(unittest.TestCase):
    """Multi-condition moments against an independent projection oracle."""

    def setUp(self):
        self.store = Store()
        self.addCleanup(self.store.close)
        self.analysis = Analysis(self.store)

    def test_rank_deficient_multi_condition(self):
        names = ["p", "q", "r", "s"]
        mean = [F(1), F(0), F(-1), F(2)]
        b = [[F(1), F(2), F(0)],
             [F(2), F(-1), F(1)],
             [F(0), F(1), F(1)],
             [F(1), F(0), F(1)]]
        a = [[F(1), F(0), F(0), F(0)], [F(0), F(1), F(0), F(0)]]
        v = [F(4), F(-2)]
        p = [[F(0), F(0), F(1), F(0)], [F(1)] * 4, [F(0), F(1), F(0), F(-1)]]
        offsets = [F(0), F(0), F(1, 2)]
        expected = projection_oracle(mean, b, a, v, p, offsets)
        self.store.put_model("w", latent_payload("latent", names, mean, b))
        upgrade(self.store)
        obs = [Observation.make("c1", {"p": 1}, v[0]),
               Observation.make("c2", {"q": 1}, v[1])]
        contrasts = [Linear.make("r", {"r": 1}),
                     Linear.make("sum", {"p": 1, "q": 1, "r": 1, "s": 1}),
                     Linear.make("tilt", {"q": 1, "s": -1}, F(1, 2))]
        self.analysis.save("base", "latent", 1, obs, contrasts)
        base = self.analysis.run("base")
        self.assertEqual((base.means, base.covariance), expected)
        self.assertTrue(all(isinstance(x, F) for x in base.means))
        self.assertTrue(all(isinstance(x, F) for row in base.covariance for x in row))
        # Redundant compatible observations do not change the result.
        redundant = obs + [Observation.make("again", {"p": 1}, v[0]),
                           Observation.make("combo", {"p": 1, "q": 1}, v[0] + v[1]),
                           Observation.make("const", {}, 0),
                           Observation.make("double", {"p": 2}, 2 * v[0])]
        self.analysis.save("redundant", "latent", 1, redundant, contrasts)
        again = self.analysis.run("redundant")
        self.assertEqual((again.means, again.covariance), (base.means, base.covariance))
        # Contradictions are rejected with no partial numeric publication.
        self.analysis.save("bad", "latent", 1,
                           obs + [Observation.make("clash", {"p": 1}, v[0] + 1)], contrasts)
        with self.assertRaises(ImpossibleObservation):
            self.analysis.run("bad")
        self.assertEqual(Results(self.store).get("bad"), Failure("bad"))
        self.analysis.save("bad2", "latent", 1,
                           obs + [Observation.make("clash", {"p": 1, "q": 1},
                                                   v[0] + v[1] + 1)], contrasts)
        with self.assertRaises(ImpossibleObservation):
            self.analysis.run("bad2")
        self.assertEqual(Results(self.store).get("bad2"), Failure("bad2"))

    def test_full_rank_multi_condition(self):
        names = ["u", "v", "w"]
        mean = [F(1), F(2), F(3)]
        b = [[F(1), F(0), F(0)], [F(0), F(1), F(0)], [F(0), F(0), F(1)]]
        a = [[F(1), F(1), F(0)], [F(1), F(-1), F(2)]]
        v = [F(4), F(7)]
        p = [[F(0), F(0), F(1)], [F(1), F(1), F(1)], [F(2), F(0), F(-1)]]
        offsets = [F(0), F(0), F(1, 3)]
        expected = projection_oracle(mean, b, a, v, p, offsets)
        self.store.put_model("w", latent_payload("full", names, mean, b))
        upgrade(self.store)
        obs = [Observation.make("o1", {"u": 1, "v": 1}, v[0]),
               Observation.make("o2", {"u": 1, "v": -1, "w": 2}, v[1])]
        contrasts = [Linear.make("w", {"w": 1}),
                     Linear.make("sum", {"u": 1, "v": 1, "w": 1}),
                     Linear.make("mix", {"u": 2, "w": -1}, F(1, 3))]
        self.analysis.save("full", "full", 1, obs, contrasts)
        result = self.analysis.run("full")
        self.assertEqual((result.means, result.covariance), expected)
        # A full-rank model admits every non-constant value; only a false
        # constant contradicts its support.
        self.analysis.save("clash", "full", 1,
                           obs + [Observation.make("false", {}, 1)], contrasts)
        with self.assertRaises(ImpossibleObservation):
            self.analysis.run("clash")

    def test_single_point_support(self):
        payload = {"id": "fixed", "revision": 1, "names": ["x", "y"],
                   "mean": ["2", "-1"], "covariance": [["0", "0"], ["0", "0"]]}
        self.store.put_model("w", payload)
        upgrade(self.store)
        contrasts = [Linear.make("y", {"y": 1}), Linear.make("mix", {"x": 2, "y": 1}, 1)]
        self.analysis.save("ok", "fixed", 1,
                           [Observation.make("x", {"x": 1}, 2),
                            Observation.make("y", {"y": 1}, -1),
                            Observation.make("const", {}, 0)], contrasts)
        result = self.analysis.run("ok")
        self.assertEqual(result.means, (F(-1), F(4)))
        self.assertEqual(result.covariance, ((F(0), F(0)), (F(0), F(0))))
        self.analysis.save("bad", "fixed", 1, [Observation.make("y", {"y": 1}, 0)],
                           [Linear.make("y", {"y": 1})])
        with self.assertRaises(ImpossibleObservation):
            self.analysis.run("bad")
        self.assertEqual(Results(self.store).get("bad"), Failure("bad"))


class NativeAndWritePathTests(unittest.TestCase):
    """Native factor layouts and the legacy write interface after upgrade."""

    def setUp(self):
        self.store = Store()
        self.addCleanup(self.store.close)

    def test_native_factor_layouts_are_consumed(self):
        upgrade(self.store)
        # Redundant hard equations; residuals cannot vanish simultaneously.
        joint = sdk2.FactorModel("joint", 1, ["y", "x"], [[1, 1], [2, 2]], [3, 6],
                                 [[1, 0], [0, 1]], [0, 0], [1, 2])
        # Hand: support y+x=3, z-coordinate precision 3 centered at 1, so
        # means (2, 1) and covariance ((1/3, -1/3), (-1/3, 1/3)).
        balanced = sdk2.FactorModel("balanced", 1, ["z"], [], [],
                                    [[1], [1]], [1, 3], [1, 1])
        # Hand: energy (z-1)^2 + (z-3)^2 = 2(z-2)^2 + 2 -> mean 2, var 1/2.
        plane = sdk2.FactorModel("plane", 1, ["u", "v"], [[1, 1]], [2],
                                 [[1, 0], [0, 1]], [0, 0], [1, 1])
        # Hand: support u+v=2, energy u^2+v^2 -> mean (1,1), var 1/2, cov -1/2.
        with atomic(self.store.connection):
            for factor in (joint, balanced, plane):
                sdk2.append(self.store.connection, factor)
        got = self.store.get_model("joint", 1)
        self.assertEqual(got.mean, (F(2), F(1)))
        self.assertEqual(got.covariance, ((F(1, 3), F(-1, 3)), (F(-1, 3), F(1, 3))))
        self.analysis = Analysis(self.store)
        self.analysis.save("j", "joint", 1, [Observation.make("x", {"x": 1}, 2)],
                           [Linear.make("y", {"y": 1})])
        conditioned = self.analysis.run("j")
        self.assertEqual((conditioned.means, conditioned.covariance), ((F(1),), ((F(0),),)))
        self.analysis.save("b", "balanced", 1, [], [Linear.make("z", {"z": 1})])
        rb = self.analysis.run("b")
        self.assertEqual((rb.means, rb.covariance), ((F(2),), ((F(1, 2),),)))
        self.analysis.save("p", "plane", 1, [],
                           [Linear.make("u", {"u": 1}), Linear.make("v", {"v": 1})])
        rp = self.analysis.run("p")
        self.assertEqual(rp.means, (F(1), F(1)))
        self.assertEqual(rp.covariance, ((F(1, 2), F(-1, 2)), (F(-1, 2), F(1, 2))))
        self.analysis.save("pc", "plane", 1, [Observation.make("u", {"u": 1}, F(1, 2))],
                           [Linear.make("v", {"v": 1})])
        rpc = self.analysis.run("pc")
        self.assertEqual((rpc.means, rpc.covariance), ((F(3, 2),), ((F(0),),)))

    def test_legacy_interface_writes_target_storage(self):
        upgrade(self.store)
        mean = [F(1), F(2)]
        b = [[F(1)], [F(2)]]
        payload = latent_payload("joint", ["x", "y"], mean, b)
        self.assertEqual(self.store.put_model("req", payload), ("joint", 1))
        row = self.store.connection.execute(
            "SELECT factors FROM models_v2 WHERE id='joint' AND revision=1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(self.store.connection.execute(
            "SELECT count(*) FROM models_v1").fetchone()[0], 0)
        factor = sdk2.load(self.store.connection, "joint", 1)
        for z in ([F(0)], [F(3)], [F(-2, 5)]):
            self.assertEqual(factor.energy(point_at(mean, b, z)), z[0] ** 2)
        got = self.store.get_model("joint", 1)
        self.assertEqual((got.mean, got.covariance),
                         ((F(1), F(2)), ((F(1), F(2)), (F(2), F(4)))))
        self.assertEqual(self.store.put_model("req", payload), ("joint", 1))
        with self.assertRaises(RequestConflict):
            self.store.put_model("req", dict(payload, mean=["0", "0"]))
        self.assertEqual(self.store.receipt("req"), ("joint", 1))


if __name__ == "__main__":
    unittest.main()
