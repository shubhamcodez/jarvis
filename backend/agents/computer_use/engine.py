"""Move selection: Stockfish if present, else python-chess alpha-beta."""
from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:
    import chess
    import chess.engine

    HAS_CHESS = True
except ImportError:
    HAS_CHESS = False
    chess = None  # type: ignore


_PIECE = {1: 100, 2: 320, 3: 330, 4: 500, 5: 900, 6: 20000}

# Midgame PST (white, rank 1 first). From common simplified tables.
_PST = {
    1: [  # pawn
        0, 0, 0, 0, 0, 0, 0, 0,
        50, 50, 50, 50, 50, 50, 50, 50,
        10, 10, 20, 30, 30, 20, 10, 10,
        5, 5, 10, 25, 25, 10, 5, 5,
        0, 0, 0, 20, 20, 0, 0, 0,
        5, -5, -10, 0, 0, -10, -5, 5,
        5, 10, 10, -20, -20, 10, 10, 5,
        0, 0, 0, 0, 0, 0, 0, 0,
    ],
    2: [  # knight
        -50, -40, -30, -30, -30, -30, -40, -50,
        -40, -20, 0, 0, 0, 0, -20, -40,
        -30, 0, 10, 15, 15, 10, 0, -30,
        -30, 5, 15, 20, 20, 15, 5, -30,
        -30, 0, 15, 20, 20, 15, 0, -30,
        -30, 5, 10, 15, 15, 10, 5, -30,
        -40, -20, 0, 5, 5, 0, -20, -40,
        -50, -40, -30, -30, -30, -30, -40, -50,
    ],
    3: [  # bishop
        -20, -10, -10, -10, -10, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 10, 10, 5, 0, -10,
        -10, 5, 5, 10, 10, 5, 5, -10,
        -10, 0, 10, 10, 10, 10, 0, -10,
        -10, 10, 10, 10, 10, 10, 10, -10,
        -10, 5, 0, 0, 0, 0, 5, -10,
        -20, -10, -10, -10, -10, -10, -10, -20,
    ],
    4: [  # rook
        0, 0, 0, 0, 0, 0, 0, 0,
        5, 10, 10, 10, 10, 10, 10, 5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        0, 0, 0, 5, 5, 0, 0, 0,
    ],
    5: [  # queen
        -20, -10, -10, -5, -5, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 5, 5, 5, 0, -10,
        -5, 0, 5, 5, 5, 5, 0, -5,
        0, 0, 5, 5, 5, 5, 0, -5,
        -10, 5, 5, 5, 5, 5, 0, -10,
        -10, 0, 5, 0, 0, 0, 0, -10,
        -20, -10, -10, -5, -5, -10, -10, -20,
    ],
    6: [  # king midgame
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -20, -30, -30, -40, -40, -30, -30, -20,
        -10, -20, -20, -20, -20, -20, -20, -10,
        20, 20, 0, 0, 0, 0, 20, 20,
        20, 30, 10, 0, 0, 10, 30, 20,
    ],
}


def _eval(board: "chess.Board") -> int:
    if board.is_checkmate():
        return -100000 if board.turn else 100000
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
    score = 0
    for sq, piece in board.piece_map().items():
        val = _PIECE[piece.piece_type]
        pst = _PST[piece.piece_type]
        idx = sq if piece.color == chess.WHITE else chess.square_mirror(sq)
        val += pst[idx]
        score += val if piece.color == chess.WHITE else -val
    return score


def _search(board: "chess.Board", depth: int, alpha: int, beta: int) -> int:
    if depth == 0 or board.is_game_over():
        return _eval(board)
    moves = list(board.legal_moves)
    moves.sort(key=lambda m: (board.is_capture(m), board.gives_check(m)), reverse=True)
    if board.turn == chess.WHITE:
        best = -10**9
        for m in moves:
            board.push(m)
            best = max(best, _search(board, depth - 1, alpha, beta))
            board.pop()
            alpha = max(alpha, best)
            if beta <= alpha:
                break
        return best
    best = 10**9
    for m in moves:
        board.push(m)
        best = min(best, _search(board, depth - 1, alpha, beta))
        board.pop()
        beta = min(beta, best)
        if beta <= alpha:
            break
    return best


def heuristic_move(board: "chess.Board", depth: int = 3) -> Optional["chess.Move"]:
    best_move = None
    if board.turn == chess.WHITE:
        best_val, alpha, beta = -10**9, -10**9, 10**9
        for m in board.legal_moves:
            board.push(m)
            val = _search(board, depth - 1, alpha, beta)
            board.pop()
            if val > best_val:
                best_val, best_move = val, m
            alpha = max(alpha, best_val)
        return best_move
    best_val = 10**9
    alpha, beta = -10**9, 10**9
    for m in board.legal_moves:
        board.push(m)
        val = _search(board, depth - 1, alpha, beta)
        board.pop()
        if val < best_val:
            best_val, best_move = val, m
        beta = min(beta, best_val)
    return best_move


@lru_cache(maxsize=1)
def find_stockfish() -> Optional[str]:
    env = (os.environ.get("STOCKFISH_PATH") or "").strip()
    if env and Path(env).exists():
        return env
    which = shutil.which("stockfish") or shutil.which("stockfish.exe")
    if which:
        return which
    candidates = [
        Path("C:/Program Files/Stockfish/stockfish.exe"),
        Path("C:/Program Files (x86)/Stockfish/stockfish.exe"),
        Path.home() / "stockfish" / "stockfish.exe",
        Path.home() / "stockfish" / "stockfish",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def best_move(fen: str, *, movetime_ms: int = 400, depth: int = 3) -> Optional[str]:
    """Return UCI move string, or None if the position is terminal / unreadable."""
    if not HAS_CHESS:
        raise RuntimeError("python-chess is not installed. poetry add chess")
    board = chess.Board(fen)
    if board.is_game_over():
        return None
    engine_path = find_stockfish()
    if engine_path:
        try:
            with chess.engine.SimpleEngine.popen_uci(engine_path) as eng:
                res = eng.play(board, chess.engine.Limit(time=max(0.05, movetime_ms / 1000.0)))
                if res.move:
                    return res.move.uci()
        except Exception:
            pass
    mv = heuristic_move(board, depth=depth)
    return mv.uci() if mv else None
