"""Live-board play: locate board → engine move → click squares (not free-form VLM clicks)."""
from __future__ import annotations

import base64
from typing import Any, Optional

from .chess import suggested_uci
from .chess_geom import square_center_screen
from .fen import grid_to_placement, normalize_placement
from .memory import WorkingMemory
from .perception import crop_rect, overlay_board_grid, resize_for_vision
from .vision import ask_board_placement, ask_chess_scene


def looks_like_live_game(goal: str) -> bool:
    low = (goal or "").lower()
    site = any(s in low for s in ("chess.com", "lichess", "chess"))
    play = any(p in low for p in ("play", "win", "game", "move", "click", "piece"))
    return site and play


def _click(x: float, y: float, thought: str, description: str, *, space: str = "vision") -> dict[str, Any]:
    return {
        "action": "click",
        "x": x,
        "y": y,
        "thought": thought,
        "description": description,
        "coord_space": space,
    }


def maybe_live_game_action(
    *,
    api_key: str,
    provider: str,
    vis_b64: str,
    vw: int,
    vh: int,
    shot,
    sx: float,
    sy: float,
    goal: str,
    mem: WorkingMemory,
) -> Optional[dict[str, Any]]:
    """Return a grounded action, or None to fall back to the generic VLM."""
    pending = mem.landmarks.get("pending_to")
    board_lm = mem.landmarks.get("board")
    if pending and board_lm:
        try:
            x, y = square_center_screen(
                pending,
                int(board_lm["x"]),
                int(board_lm["y"]),
                int(board_lm["w"]),
                int(board_lm["h"]),
                bool(mem.landmarks.get("white_at_bottom", True)),
            )
        except Exception:
            mem.landmarks.pop("pending_to", None)
        else:
            mem.landmarks.pop("pending_to", None)
            return _click(x, y, f"Complete move to {pending}.", f"Click {pending}", space="screen")

    try:
        scene = ask_chess_scene(api_key, provider, vis_b64, vw, vh, goal)
    except Exception:
        return None

    phase = str(scene.get("phase") or "other").lower()
    if phase == "over":
        result = scene.get("result") or "game over"
        return {
            "action": "done",
            "thought": f"Game finished ({result}).",
            "description": f"Game over: {result}",
        }

    if phase != "ingame":
        click = scene.get("click")
        if isinstance(click, dict) and click.get("x") is not None:
            return _click(
                click["x"],
                click["y"],
                f"Start or confirm a game ({click.get('description') or phase}).",
                click.get("description") or "Click play control",
            )
        return None

    if scene.get("our_turn") is False:
        return {
            "action": "wait",
            "seconds": 2.4,
            "thought": "Opponent to move.",
            "description": "Wait for opponent",
        }

    board = scene.get("board")
    if not isinstance(board, dict):
        return None
    bx = int(board["x"] * sx)
    by = int(board["y"] * sy)
    bw = max(32, int(board["w"] * sx))
    bh = max(32, int(board["h"] * sy))
    white_bottom = bool(scene.get("white_at_bottom", True))
    our = str(scene.get("our_side") or ("w" if white_bottom else "b"))
    mem.landmarks["board"] = {"x": bx, "y": by, "w": bw, "h": bh}
    mem.landmarks["white_at_bottom"] = white_bottom
    mem.landmarks["our_side"] = our

    try:
        crop = crop_rect(shot, bx, by, bw, bh)
        labeled = overlay_board_grid(crop, white_bottom)
        vis, *_ = resize_for_vision(labeled, max_side=768)
        read = ask_board_placement(
            api_key,
            provider,
            base64.b64encode(vis).decode("ascii"),
            side_hint=our,
        )
    except Exception:
        return None

    place = normalize_placement(str(read.get("fen") or read.get("fen_placement") or ""))
    if not place:
        place = grid_to_placement(read.get("ranks"))
    side = str(read.get("side_to_move") or our)
    uci = suggested_uci({"fen": place or "", "ranks": read.get("ranks"), "side_to_move": side})
    if not uci or len(uci) < 4:
        return {
            "action": "wait",
            "seconds": 1.2,
            "thought": "Could not read a legal engine move from the board.",
            "description": "Re-read board",
        }

    src, dst = uci[:2], uci[2:4]
    mem.landmarks["pending_to"] = dst
    try:
        x, y = square_center_screen(src, bx, by, bw, bh, white_bottom)
    except Exception:
        mem.landmarks.pop("pending_to", None)
        return None
    return _click(x, y, f"Play {uci}.", f"Click {src} then {dst}", space="screen")
