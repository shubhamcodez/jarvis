"""Assemble and sanity-check FEN from a VLM placement string."""
from __future__ import annotations

import re
from typing import Optional

try:
    import chess

    HAS_CHESS = True
except ImportError:
    HAS_CHESS = False
    chess = None  # type: ignore


_PLACEMENT = re.compile(r"^([rnbqkpRNBQKP1-8]+/){7}[rnbqkpRNBQKP1-8]+$")


def normalize_placement(raw: str) -> Optional[str]:
    s = (raw or "").strip().split()[0]
    s = s.replace("\\", "/")
    if not _PLACEMENT.match(s):
        return None
    ranks = s.split("/")
    if len(ranks) != 8:
        return None
    for rank in ranks:
        n = 0
        for ch in rank:
            if ch.isdigit():
                n += int(ch)
            else:
                n += 1
        if n != 8:
            return None
    return s


def board_from_placement(placement: str, side: str = "w") -> Optional["chess.Board"]:
    if not HAS_CHESS:
        return None
    place = normalize_placement(placement)
    if not place:
        return None
    stm = "b" if str(side).lower().startswith("b") else "w"
    for castle in ("KQkq", "KQk", "KQq", "KQ", "Kkq", "Qkq", "kq", "Kk", "Kq", "Qk", "Qq", "K", "Q", "k", "q", "-"):
        fen = f"{place} {stm} {castle} - 0 1"
        try:
            board = chess.Board(fen)
        except ValueError:
            continue
        if board.is_valid():
            return board
    return None


def piece_count(placement: str) -> int:
    return sum(1 for ch in placement if ch.isalpha())


_GLYPH = {
    "K": "♔",
    "Q": "♕",
    "R": "♖",
    "B": "♗",
    "N": "♘",
    "P": "♙",
    "k": "♚",
    "q": "♛",
    "r": "♜",
    "b": "♝",
    "n": "♞",
    "p": "♟",
}


def _cell_token(raw) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    if s in (".", "-", " ", "empty", "None"):
        return ""
    if s.lower() in ("wp", "white pawn"):
        return "P"
    if s.lower() in ("bp", "black pawn"):
        return "p"
    if len(s) == 1 and s in "KQRBNPkqrbnp":
        return s
    return ""


def grid_to_placement(ranks) -> Optional[str]:
    """8 rows × 8 files (row 0 = rank 8, file 0 = a) → FEN placement."""
    if not isinstance(ranks, list) or len(ranks) != 8:
        return None
    fen_ranks = []
    for row in ranks:
        if not isinstance(row, (list, tuple, str)) or len(row) != 8:
            return None
        empty = 0
        out = []
        for cell in row:
            tok = _cell_token(cell)
            if not tok:
                empty += 1
                continue
            if empty:
                out.append(str(empty))
                empty = 0
            out.append(tok)
        if empty:
            out.append(str(empty))
        fen_ranks.append("".join(out) or "8")
    return normalize_placement("/".join(fen_ranks))


def format_grid(placement: str) -> str:
    """Human-readable 8×8 with file/rank labels."""
    place = normalize_placement(placement)
    if not place:
        return ""
    lines = ["    a b c d e f g h", "    ---------------"]
    for i, rank in enumerate(place.split("/")):
        row = []
        for ch in rank:
            if ch.isdigit():
                row.extend(["."] * int(ch))
            else:
                row.append(_GLYPH.get(ch, ch))
        n = 8 - i
        lines.append(f"  {n} {' '.join(row)}  {n}")
    lines.append("    ---------------")
    lines.append("    a b c d e f g h")
    return "\n".join(lines)
