"""Desktop follow-up routing + loop-guard fingerprints (no display / API)."""
from __future__ import annotations

import unittest

from agents.computer_use.budget import implied_duration
from agents.computer_use.chess_play import looks_like_live_game
from agents.supervisor import (
    _heuristic_desktop_task,
    compute_supervisor_decision,
    extract_desktop_goal,
)
from observability.guards import action_fingerprint, should_stop_streak


class DesktopHeuristicTests(unittest.TestCase):
    def test_chess_com_routes_desktop(self):
        d = _heuristic_desktop_task("I have chess.com open, start playing and win the game for me")
        self.assertTrue(d["run_agent"])
        self.assertEqual(d["agent"], "desktop")

    def test_try_again_resumes_prior_goal(self):
        turns = [
            {
                "role": "assistant",
                "content": "Desktop task (goal: Click on chess.com interface to start a new game.).\n\nStopped.",
            }
        ]
        d = _heuristic_desktop_task("try again", turns)
        self.assertTrue(d["run_agent"])
        self.assertEqual(d["agent"], "desktop")
        self.assertIn("chess.com", d["goal"])

    def test_you_were_just_doing_that(self):
        turns = [
            {
                "role": "assistant",
                "content": "Desktop task (goal: Play chess.com until a win.).\n\nLoop guard.",
            }
        ]
        d = _heuristic_desktop_task("bro, you were just doing that", turns)
        self.assertTrue(d["run_agent"])
        self.assertIn("win", d["goal"].lower())

    def test_coding_mode_does_not_override_chess(self):
        d = compute_supervisor_decision(
            "unused",
            "openai",
            "play chess.com and win",
            coding_mode=True,
        )
        self.assertEqual(d["agent"], "desktop")

    def test_extract_goal(self):
        g = extract_desktop_goal("Desktop task (goal: Click Play Online on chess.com).\n\nAgent:")
        self.assertEqual(g, "Click Play Online on chess.com")


class LiveGameTests(unittest.TestCase):
    def test_looks_like_live_game(self):
        self.assertTrue(looks_like_live_game("play chess.com and win the game"))
        self.assertFalse(looks_like_live_game("explain the rules of chess"))

    def test_implied_duration(self):
        self.assertEqual(implied_duration("win the game for me"), 15 * 60)
        self.assertIsNone(implied_duration("open chrome"))


class StreakTests(unittest.TestCase):
    def test_different_click_targets_do_not_stop(self):
        hist = [
            {"action": "click", "fingerprint": action_fingerprint({"action": "click", "x": 100, "y": 100})},
            {"action": "click", "fingerprint": action_fingerprint({"action": "click", "x": 400, "y": 400})},
            {"action": "click", "fingerprint": action_fingerprint({"action": "click", "x": 120, "y": 500})},
            {"action": "click", "fingerprint": action_fingerprint({"action": "click", "x": 200, "y": 300})},
        ]
        self.assertFalse(should_stop_streak("click", "moving pieces", hist, streak_limit=4))

    def test_same_pixel_bucket_stops(self):
        fp = action_fingerprint({"action": "click", "x": 532, "y": 164})
        hist = [{"action": "click", "fingerprint": fp, "thought": "Play Online"}] * 5
        self.assertTrue(should_stop_streak("click", "Play Online", hist, streak_limit=5))


if __name__ == "__main__":
    unittest.main()
