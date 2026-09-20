"""Observability writes to jarvis-observability; agent steps persist."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ObservabilityPathTests(unittest.TestCase):
    def test_canonical_dir_is_jarvis(self):
        from observability.config import legacy_obs_dir, obs_dir

        self.assertTrue(str(obs_dir()).replace("\\", "/").endswith("jarvis-observability"))
        self.assertTrue(str(legacy_obs_dir()).replace("\\", "/").endswith("ada-observability"))

    def test_migrates_legacy_ada_then_logs_actions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ada = root / "ada-observability"
            (ada / "traces").mkdir(parents=True)
            (ada / "traces" / "spans.jsonl").write_text(
                '{"ts": 1, "name": "legacy-span"}\n', encoding="utf-8"
            )
            with patch("observability.config._root", return_value=root):
                from observability.config import ensure_dirs, obs_dir
                from observability.actions import list_actions, log_agent_action

                ensure_dirs()
                dest = obs_dir() / "traces" / "spans.jsonl"
                self.assertTrue(dest.exists())
                self.assertIn("legacy-span", dest.read_text(encoding="utf-8"))
                log_agent_action(
                    step=2,
                    action="click",
                    description="Play Online",
                    thought="start a game",
                    result="Clicked at (1, 2)",
                    done=False,
                    agent="desktop",
                )
                acts = list_actions(limit=10)
                self.assertTrue(acts)
                self.assertEqual(acts[-1]["action"], "click")
                self.assertEqual(acts[-1]["description"], "Play Online")
                action_file = obs_dir() / "traces" / "actions.jsonl"
                self.assertTrue(action_file.exists())
                self.assertFalse((ada / "traces" / "actions.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
