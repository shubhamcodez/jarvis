"""Desktop follow-up routing + loop-guard fingerprints (no display / API)."""
from __future__ import annotations

import unittest

from agents.computer_use.budget import implied_duration
from agents.computer_use.chess_play import looks_like_live_game
from agents.supervisor import (
    _heuristic_desktop_task,
    compute_supervisor_decision,
    extract_desktop_goal,
    looks_like_task_continuation,
)
from memory.compaction import compact_history
from memory.prompt_assembly import assemble_turn_context
from memory.thread_context import format_thread_context, summarize_agent_trace
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

    def test_unfinished_thread_plus_short_followup(self):
        thread = {
            "last_route": "desktop",
            "last_goal": "Play chess.com until a win.",
            "last_status": "unfinished",
        }
        d = _heuristic_desktop_task("bro, you were just doing that", [], thread)
        self.assertEqual(d["agent"], "desktop")
        self.assertEqual(d["goal"], "Play chess.com until a win.")

    def test_thanks_does_not_resume(self):
        thread = {
            "last_route": "desktop",
            "last_goal": "Play chess.com until a win.",
            "last_status": "unfinished",
        }
        self.assertIsNone(_heuristic_desktop_task("thanks", [], thread))
        self.assertFalse(looks_like_task_continuation("thanks"))


class LiveGameTests(unittest.TestCase):
    def test_looks_like_live_game(self):
        self.assertTrue(looks_like_live_game("play chess.com and win the game"))
        self.assertFalse(looks_like_live_game("explain the rules of chess"))

    def test_implied_duration(self):
        self.assertEqual(implied_duration("win the game for me"), 15 * 60)
        self.assertIsNone(implied_duration("open chrome"))


class ContextPackTests(unittest.TestCase):
    def test_summarize_keeps_goal_and_unfinished(self):
        dump = (
            "Desktop task (goal: Click on chess.com and win.).\n\n"
            "Agent thought process:\n\n"
            + "\n".join(f"Step {i} — click Play Online\n  Action: click\n  → Clicked at (532, 164)" for i in range(1, 20))
            + "\nLoop guard: repeated action; stopping.\nStopped after 9 events (goal not marked done)."
        )
        summary = summarize_agent_trace(dump)
        self.assertIn("chess.com", summary)
        self.assertIn("unfinished", summary.lower())
        self.assertLess(len(summary), len(dump) // 2)

    def test_compact_history_does_not_drop_desktop_goal(self):
        dump = "Desktop task (goal: Play chess.com until a win.).\n\n" + ("Step click\n" * 4000)
        dump += "Loop guard: repeated action; stopping.\nStopped after 9 events (goal not marked done)."
        hist, stats = compact_history(
            [
                {"role": "user", "content": "win the game for me"},
                {"role": "assistant", "content": dump},
                {"role": "user", "content": "try again"},
            ],
            token_budget=800,
            keep_recent=4,
        )
        blob = "\n".join(str(m.get("content") or "") for m in hist)
        self.assertIn("chess.com", blob)
        self.assertIn("unfinished", blob.lower())

    def test_assemble_includes_thread_context(self):
        pack = assemble_turn_context(
            user_message="try again",
            history=[{"role": "assistant", "content": "Desktop task (goal: Play chess.com.).\nStopped."}],
            thread_context_text=format_thread_context(
                {
                    "last_route": "desktop",
                    "last_goal": "Play chess.com.",
                    "last_status": "unfinished",
                }
            ),
        )
        self.assertIn("THREAD CONTEXT", pack.system)
        self.assertIn("desktop", pack.system.lower())
        self.assertIn("Play chess.com", pack.system)


class GuiPolicyTests(unittest.TestCase):
    def test_off_does_not_claim_screen_control(self):
        from unittest.mock import patch

        from agents.execution_policy import gui_policy_for_prompt
        from memory.prompt_assembly import assemble_turn_context

        with patch("config.is_desktop_armed", return_value=False), patch(
            "agents.execution_policy.mode_label", return_value="agent"
        ):
            note = gui_policy_for_prompt()
            self.assertIn("Off", note)
            self.assertIn("cannot see or click", note)
            pack = assemble_turn_context(user_message="hi")
            self.assertIn("GUI POLICY", pack.system)
            self.assertIn("Off", pack.system)

    def test_armed_does_not_deny_screen(self):
        from unittest.mock import patch

        from agents.execution_policy import gui_policy_for_prompt

        with patch("config.is_desktop_armed", return_value=True), patch(
            "agents.execution_policy.mode_label", return_value="agent"
        ):
            note = gui_policy_for_prompt()
            self.assertIn("Armed", note)
            self.assertIn("Do not say you cannot", note)


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
