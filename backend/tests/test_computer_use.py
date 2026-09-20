"""Unit tests for computer-use helpers (no display / API)."""
from __future__ import annotations

import unittest

from agents.computer_use.budget import parse_duration, steps_for_budget
from agents.computer_use.chess import analyze_position
from agents.computer_use.chess_geom import square_center_screen, square_to_frac
from agents.computer_use.fen import (
    board_from_placement,
    format_grid,
    grid_to_placement,
    normalize_placement,
    piece_count,
)
from agents.computer_use.verify import expects_visual_change


class BudgetTests(unittest.TestCase):
    def test_parse_duration(self):
        self.assertIsNone(parse_duration("open chrome"))
        self.assertEqual(parse_duration("keep going for 10 minutes"), 600.0)
        self.assertEqual(parse_duration("play for 1 hour"), 3600.0)
        self.assertGreaterEqual(steps_for_budget(600), 200)


class GeomTests(unittest.TestCase):
    def test_a1_white_bottom_left(self):
        fx, fy = square_to_frac("a1", True)
        self.assertAlmostEqual(fx, 0.5 / 8)
        self.assertAlmostEqual(fy, 7.5 / 8)

    def test_h8_white_top_right(self):
        fx, fy = square_to_frac("h8", True)
        self.assertAlmostEqual(fx, 7.5 / 8)
        self.assertAlmostEqual(fy, 0.5 / 8)

    def test_center_pixels(self):
        x, y = square_center_screen("e4", 0, 0, 800, 800, True)
        self.assertEqual(x, 450)
        self.assertEqual(y, 450)


class FenTests(unittest.TestCase):
    def test_start_placement(self):
        p = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
        self.assertEqual(normalize_placement(p), p)
        self.assertEqual(piece_count(p), 32)
        board = board_from_placement(p, "w")
        self.assertIsNotNone(board)
        self.assertTrue(board.turn)

    def test_rejects_bad(self):
        self.assertIsNone(normalize_placement("not-a-fen"))
        self.assertIsNone(board_from_placement("8/8/8/8/8/8/8/8", "w"))

    def test_grid_to_placement_start(self):
        ranks = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        p = grid_to_placement(ranks)
        self.assertEqual(p, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")
        self.assertIn("a b c d e f g h", format_grid(p))


class EngineToolTests(unittest.TestCase):
    def test_analyze_start(self):
        from agents.computer_use.engine import HAS_CHESS

        if not HAS_CHESS:
            self.skipTest("python-chess not installed")
        out = analyze_position(
            {
                "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
                "side_to_move": "w",
            }
        )
        self.assertIn("White to move", out)
        self.assertIn("Suggested:", out)


class VerifyTests(unittest.TestCase):
    def test_click_expects_change(self):
        self.assertTrue(expects_visual_change("click"))
        self.assertFalse(expects_visual_change("wait"))
        self.assertFalse(expects_visual_change("analyze_chess"))


if __name__ == "__main__":
    unittest.main()
