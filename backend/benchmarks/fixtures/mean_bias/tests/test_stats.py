import unittest

from pkg.stats import mean


class TestMean(unittest.TestCase):
    def test_three(self):
        self.assertEqual(mean([2, 4, 6]), 4)

    def test_one(self):
        self.assertEqual(mean([1]), 1.0)


if __name__ == "__main__":
    unittest.main()
