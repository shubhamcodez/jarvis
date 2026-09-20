"""Deterministic board geometry. Squares → pixels; no VLM coordinates."""
from __future__ import annotations

FILES = "abcdefgh"


def normalize_square(square: str) -> str:
    s = (square or "").strip().lower()
    if len(s) != 2 or s[0] not in FILES or s[1] not in "12345678":
        raise ValueError(f"invalid square: {square!r}")
    return s


def square_to_frac(square: str, white_at_bottom: bool) -> tuple[float, float]:
    """Center of a square as (x, y) in 0–1, top-left origin."""
    s = normalize_square(square)
    file_i = FILES.index(s[0])
    rank_i = int(s[1]) - 1
    if white_at_bottom:
        col, row = file_i, 7 - rank_i
    else:
        col, row = 7 - file_i, rank_i
    return (col + 0.5) / 8.0, (row + 0.5) / 8.0


def square_cell_px(
    square: str,
    width: int,
    height: int,
    white_at_bottom: bool,
    inset: float = 0.0,
) -> tuple[int, int, int, int]:
    """Pixel box (x0,y0,x1,y1) of a square inside a board image."""
    s = normalize_square(square)
    file_i = FILES.index(s[0])
    rank_i = int(s[1]) - 1
    if white_at_bottom:
        col, row = file_i, 7 - rank_i
    else:
        col, row = 7 - file_i, rank_i
    cw, ch = width / 8.0, height / 8.0
    pad_x, pad_y = cw * inset, ch * inset
    x0 = int(round(col * cw + pad_x))
    y0 = int(round(row * ch + pad_y))
    x1 = int(round((col + 1) * cw - pad_x))
    y1 = int(round((row + 1) * ch - pad_y))
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def square_center_screen(
    square: str,
    board_x: int,
    board_y: int,
    board_w: int,
    board_h: int,
    white_at_bottom: bool,
) -> tuple[int, int]:
    fx, fy = square_to_frac(square, white_at_bottom)
    return int(round(board_x + fx * board_w)), int(round(board_y + fy * board_h))
