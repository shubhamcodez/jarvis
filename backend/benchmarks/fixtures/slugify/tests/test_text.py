import unittest

from pkg.text import slugify


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_collapse(self):
        self.assertEqual(slugify("too   many   spaces"), "too-many-spaces")

    def test_empty(self):
        self.assertEqual(slugify("   "), "")

    def test_strip_junk(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_trim_hyphens(self):
        self.assertEqual(slugify("--Keep this--"), "keep-this")


if __name__ == "__main__":
    unittest.main()
