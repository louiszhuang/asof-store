import unittest

from asof_store import AsOfStore


class AsOfStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = AsOfStore.from_memory()

    def test_get_returns_latest_value_at_or_before_timestamp(self) -> None:
        self.store.put(20, "item", "later")
        self.store.put(10, "item", "earlier")
        self.store.put(30, "item", "latest")

        self.assertEqual(self.store.get(10, "item"), "earlier")
        self.assertEqual(self.store.get(25, "item"), "later")
        self.assertEqual(self.store.get(30, "item"), "latest")

    def test_put_replaces_value_at_same_timestamp(self) -> None:
        self.store.put(10, "item", "old")
        self.store.put(10, "item", "new")

        self.assertEqual(self.store.get(10, "item"), "new")

    def test_get_returns_none_when_no_prior_value_exists(self) -> None:
        self.store.put(10, "item", "value")

        self.assertIsNone(self.store.get(9, "item"))
        self.assertIsNone(self.store.get(10, "missing"))

    def test_as_of_context_uses_fixed_timestamp(self) -> None:
        self.store.put(10, "item", "original")

        with self.store.as_of(15) as snapshot:
            self.store.put(20, "item", "future")
            self.assertEqual(snapshot.get("item"), "original")
            self.assertIsNone(snapshot.get("missing"))


if __name__ == "__main__":
    unittest.main()