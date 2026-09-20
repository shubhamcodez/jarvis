"""Optional chess engine tool. The general computer-use loop decides whether to call it."""
from __future__ import annotations

from typing import Any

from .engine import HAS_CHESS, best_move
from .fen import board_from_placement, format_grid, grid_to_placement, normalize_placement


def analyze_position(action: dict[str, Any]) -> str:
    """Score a position the model already read from the screenshot."""
    if not HAS_CHESS:
        return "chess package not installed (poetry add chess)"
    place = normalize_placement(str(action.get("fen") or action.get("fen_placement") or ""))
    if not place:
        place = grid_to_placement(action.get("ranks"))
    if not place:
        return "analyze_chess needs fen or ranks (8 rows, rank 8 first, a–h)."
    side = str(action.get("side_to_move") or action.get("side") or "w")
    board = board_from_placement(place, side)
    if board is None:
        return f"Illegal position: {place}"
    stm = "w" if board.turn else "b"
    if board.is_game_over():
        return f"{format_grid(place)}\n\nGame over: {board.result()}"
    try:
        uci = best_move(board.fen(), movetime_ms=400, depth=3)
    except Exception as e:
        return f"Engine error: {e}"
    if not uci:
        return f"{format_grid(place)}\n\nNo legal move."
    label = uci
    try:
        import chess

        label = f"{board.san(chess.Move.from_uci(uci))} ({uci})"
    except Exception:
        pass
    who = "White" if stm == "w" else "Black"
    return f"{format_grid(place)}\n\n{who} to move. Suggested: {label}"


def suggested_uci(action: dict[str, Any]) -> str | None:
    """Engine UCI for a VLM-read position, or None."""
    if not HAS_CHESS:
        return None
    place = normalize_placement(str(action.get("fen") or action.get("fen_placement") or ""))
    if not place:
        place = grid_to_placement(action.get("ranks"))
    if not place:
        return None
    side = str(action.get("side_to_move") or action.get("side") or "w")
    board = board_from_placement(place, side)
    if board is None or board.is_game_over():
        return None
    try:
        return best_move(board.fen(), movetime_ms=400, depth=3)
    except Exception:
        return None
