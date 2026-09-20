"""Unit tests for the SWE harness (no API key)."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from benchmarks.catalog import SWE_FIXTURES, swe_fixture_path
from benchmarks.runner import apply_gold, score_workspace
from tools.overlay_workspace import OverlayWorkspace
from tools.patch_apply import apply_unique_replace


class TestPatchApply(unittest.TestCase):
    def test_unique(self):
        out = apply_unique_replace("a = 1\nb = 2\n", "a = 1", "a = 3")
        self.assertTrue(out["ok"])
        self.assertEqual(out["content"], "a = 3\nb = 2\n")

    def test_missing(self):
        out = apply_unique_replace("a = 1\n", "zzz", "q")
        self.assertFalse(out["ok"])

    def test_ambiguous(self):
        out = apply_unique_replace("x\nx\n", "x", "y")
        self.assertFalse(out["ok"])


class TestOverlay(unittest.TestCase):
    def test_read_write_patch(self):
        dest = Path(tempfile.mkdtemp(prefix="ada-ov-"))
        try:
            (dest / "foo.py").write_text("n = 1\n", encoding="utf-8")
            ws = OverlayWorkspace(dest)
            self.assertIn("foo.py", ws.list_rel_paths())
            ws.apply_patch("foo.py", "n = 1", "n = 2")
            self.assertEqual(ws.raw_text("foo.py"), "n = 2\n")
            self.assertEqual((dest / "foo.py").read_text(encoding="utf-8"), "n = 1\n")
            ws.apply_to_root()
            self.assertEqual((dest / "foo.py").read_text(encoding="utf-8"), "n = 2\n")
        finally:
            shutil.rmtree(dest, ignore_errors=True)


class TestFixturesGold(unittest.TestCase):
    def test_buggy_fails_gold_passes(self):
        for item in SWE_FIXTURES:
            src = swe_fixture_path(item)
            work = Path(tempfile.mkdtemp(prefix="ada-gold-"))
            try:
                shutil.copytree(
                    src,
                    work,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("gold", "__pycache__"),
                )
                before = score_workspace(work)
                self.assertFalse(before.get("passed"), f"{item['id']} should fail before gold")
                apply_gold(src, work)
                after = score_workspace(work)
                self.assertTrue(after.get("passed"), f"{item['id']} gold should pass: {after.get('summary')}")
            finally:
                shutil.rmtree(work, ignore_errors=True)


class TestSkills(unittest.TestCase):
    def test_discover_repo_skills(self):
        from tools.skills import discover_skills, select_skills, skills_for_prompt

        root = str(BACKEND.parent)
        skills = discover_skills(root)
        names = {s["name"] for s in skills}
        self.assertIn("swe-fix", names)
        matched = select_skills(skills, "use swe-fix on this failing test")
        self.assertTrue(matched)
        text = skills_for_prompt("fix the failing tests in this repo", root)
        self.assertIn("swe-fix", text)


class TestRecap(unittest.TestCase):
    def test_empty_recap(self):
        from memory.chat_search import recap_markdown

        md = recap_markdown("does-not-exist")
        self.assertIn("# Recap", md)


if __name__ == "__main__":
    unittest.main()

