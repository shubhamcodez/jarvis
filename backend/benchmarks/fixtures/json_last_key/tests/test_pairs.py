import unittest

from pkg.pairs import parse_pairs


class TestPairs(unittest.TestCase):
    def test_three(self):
        self.assertEqual(parse_pairs("a=1,b=2,c=3"), {"a": "1", "b": "2", "c": "3"})

    def test_one(self):
        self.assertEqual(parse_pairs("only=yes"), {"only": "yes"})

    def test_empty(self):
        self.assertEqual(parse_pairs(""), {})


if __name__ == "__main__":
    unittest.main()
