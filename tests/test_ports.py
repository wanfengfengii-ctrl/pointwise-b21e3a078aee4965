import unittest
from fractions import Fraction as F
from db import connect, atomic
from exact import affine, semidefinite
from vendor.sdk1 import Model
from vendor import sdk2
from errors import ImpossibleObservation


class PortTests(unittest.TestCase):
    def test_generic_elimination(self):
        point, basis = affine([[1, 2, 0], [2, 4, 0]], [3, 6], 3)
        self.assertEqual(point, [F(3), F(0), F(0)])
        self.assertEqual(basis, [[F(-2), F(1), F(0)], [F(0), F(0), F(1)]])
        with self.assertRaises(ValueError):
            affine([[0]], [1], 1)
        self.assertEqual(affine([], [], 0), ([], []))
        self.assertTrue(semidefinite([[0, 0], [0, 1]]))
        self.assertFalse(semidefinite([[0, 0], [0, 1]], strict=True))
        self.assertFalse(semidefinite([[0, 1], [1, 0]]))

    def test_sdk1_port(self):
        model = Model("r", 1, ["a", "b"], [2, -1], [[2, -2], [-2, 2]])
        self.assertEqual(Model.from_payload(model.payload()), model)
        means, covariance = model.conditional([([1, 1], 1)], [([1, 0], 0), ([0, 1], 0)])
        self.assertEqual(means, (F(2), F(-1)))
        self.assertEqual(covariance, ((F(2), F(-2)), (F(-2), F(2))))
        with self.assertRaises(ImpossibleObservation):
            model.conditional([([1, 1], 2)], [([1, 0], 0)])

    def test_sdk2_validation_energy_and_native_storage(self):
        # Native factor fixture: this is not an SDK1 converter.
        model = sdk2.FactorModel("native", 1, ["a", "b"],
                                [[1, 1]], [3], [[1, 0]], [1], [2])
        self.assertEqual(model.energy([1, 2]), F(0))
        self.assertEqual(model.energy([3, 0]), F(8))
        with self.assertRaises(ValueError):
            model.energy([1, 3])
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        with atomic(connection):
            sdk2.install(connection)
            sdk2.append(connection, model)
        self.assertEqual(sdk2.load(connection, "native", 1), model)
        with self.assertRaises(ValueError):
            with atomic(connection):
                sdk2.append(connection, model)
        with self.assertRaises(KeyError):
            sdk2.load(connection, "missing", 1)
        self.assertEqual(connection.execute("SELECT count(*) FROM models_v2").fetchone()[0], 1)

    def test_sdk2_redundancy_zero_support_and_invalid_factors(self):
        fixed = sdk2.FactorModel("fixed", 1, ["x"], [[1], [2]], [4, 8], [], [], [])
        self.assertEqual(fixed.energy([4]), F(0))
        free = sdk2.FactorModel("free", 1, ["x"], [], [], [[1], [2]], [0, 0], [1, 1])
        self.assertEqual(free.energy([2]), F(20))
        for hard, rhs, residuals, targets, weights in (
                ([[1], [2]], [4, 9], [], [], []),
                ([], [], [], [], []),
                ([], [], [[1]], [0], [0]),
                ([], [], [[1, 2]], [0], [1]),
                ([], [], [[1]], [], [1])):
            with self.assertRaises(ValueError):
                sdk2.FactorModel("bad", 1, ["x"], hard, rhs, residuals, targets, weights)

    def test_atomic_ddl_failure_retry(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE original(value TEXT)")
        connection.execute("INSERT INTO original VALUES('kept')")
        def fail():
            raise RuntimeError("before commit")
        with self.assertRaises(RuntimeError):
            with atomic(connection, fail):
                sdk2.install(connection)
                connection.execute("DELETE FROM original")
        self.assertEqual(connection.execute("SELECT value FROM original").fetchall(), [("kept",)])
        self.assertIsNone(connection.execute(
            "SELECT name FROM sqlite_master WHERE name='models_v2'").fetchone())
        with atomic(connection):
            sdk2.install(connection)
        with atomic(connection):
            sdk2.install(connection)
        self.assertEqual(connection.execute("SELECT count(*) FROM models_v2").fetchone()[0], 0)
