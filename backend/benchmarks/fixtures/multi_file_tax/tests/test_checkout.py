import unittest

from pkg.checkout import total


class TestCheckout(unittest.TestCase):
    def test_ca(self):
        self.assertAlmostEqual(total(100, "CA"), 107.25, places=4)

    def test_unknown(self):
        self.assertEqual(total(50, "ZZ"), 50)


if __name__ == "__main__":
    unittest.main()
