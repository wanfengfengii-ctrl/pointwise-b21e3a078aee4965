import unittest
from archive import Store
from vendor.sdk1 import Model
from errors import InvalidModel, RequestConflict, UnknownRevision
from samples import MODELS


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.addCleanup(self.store.close)

    def test_roundtrip(self):
        model = Model("full", 1, ("y", "x"), (2, 1), ((4, 1), (1, 2)))
        self.assertEqual(self.store.put_model("r", model), ("full", 1))
        self.assertEqual(self.store.get_model("full", 1), model)
        self.assertEqual(self.store.head("full"), 1)
        self.assertEqual(self.store.revisions("full"), (1,))
        with self.assertRaises(UnknownRevision):
            self.store.get_model("full", 2)

    def test_invalid_covariance_is_atomic(self):
        good = MODELS[0]
        self.store.put_model("ok", good)
        bad = dict(good, revision=2, covariance=((1, 2), (2, 1)))
        with self.assertRaises(InvalidModel):
            self.store.put_model("bad", bad)
        self.assertEqual(self.store.head("joint"), 1)
        self.assertEqual(self.store.revisions("joint"), (1,))
        self.assertIsNone(self.store.receipt("bad"))
        for covariance in (((0, 1), (1, 0)), ((1, 0), (1, 1)), ((-1, 0), (0, 1))):
            with self.assertRaises(InvalidModel):
                self.store.put_model("bad", dict(good, revision=2, covariance=covariance))

    def test_receipt_replay_conflict(self):
        self.store.put_model("one", MODELS[0])
        self.store.put_model("two", MODELS[1])
        self.assertEqual(self.store.put_model("one", MODELS[0]), ("joint", 1))
        with self.assertRaises(RequestConflict):
            self.store.put_model("one", MODELS[1])
        self.assertEqual(self.store.revisions("joint"), (1, 2))
        self.assertEqual(self.store.head("joint"), 2)
        self.assertEqual(self.store.receipt("one"), ("joint", 1))

    def test_revision_and_model_validation(self):
        for bad in (dict(MODELS[0], names=["x", "x"]),
                    dict(MODELS[0], mean=[1]),
                    dict(MODELS[0], mean=[1.0, 2]),
                    dict(MODELS[0], revision=True)):
            with self.assertRaises(InvalidModel):
                self.store.put_model("bad", bad)
        with self.assertRaises(InvalidModel):
            self.store.put_model("gap", MODELS[1])
        self.assertEqual(self.store.revisions("joint"), ())
