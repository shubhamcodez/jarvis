import unittest

from pkg.counter import count_to


class TestCounter(unittest.TestCase):
    def test_three(self):
        self.assertEqual(count_to(3), [0, 1, 2])

    def test_zero(self):
        self.assertEqual(count_to(0), [])


if __name__ == "__main__":
    unittest.main()
