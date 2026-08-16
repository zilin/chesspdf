"""Verify stage: cross-validate each puzzle's FEN against its solution.

Pure local computation (python-chess + optional stockfish). Writes a ledger
entry per check and routes each puzzle to verified / needs_review /
no_solution.
"""

from __future__ import annotations

from ..bundle import Bundle, Puzzle
from ..chesslib import first_mover, replay_error, strip_variations, structural_check


def run(bundle: Bundle, only_ids: set[str] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pid, p in sorted(bundle.puzzles().items()):
        if only_ids and pid not in only_ids:
            continue
        if p.status in ("approved", "excluded"):
            continue  # human verdicts are final
        verdict = _verify_one(p)
        counts[verdict] = counts.get(verdict, 0) + 1
        bundle.write(p)
    bundle.event("verify", f"{counts}")
    return counts


def _verify_one(p: Puzzle) -> str:
    fen = (p.fen or "").split()[0] if p.fen else ""
    moves = p.moves

    if (err := structural_check(fen)) is not None:
        p.ledger_add("verify", "STRUCT", err)
        p.data["status"] = "needs_review"
        return "needs_review"

    if not moves:
        p.ledger_add("verify", "NO_SOL")
        p.data["status"] = "no_solution"
        return "no_solution"

    turn = p.to_move or first_mover(moves) or "w"
    mover = first_mover(moves)
    if mover and mover != turn:
        turn = mover  # the solution's own numbering is more trustworthy
        p.data["to_move"] = turn

    # full text first, then mainline only
    if replay_error(fen, turn, moves) is None:
        p.ledger_add("verify", "REPLAY_FULL_OK")
        p.data["status"] = "verified"
        return "verified"
    main = strip_variations(moves)
    for t in (turn, "b" if turn == "w" else "w"):
        if replay_error(fen, t, main) is None:
            p.data["to_move"] = t
            p.ledger_add("verify", "REPLAY_MAIN_OK",
                         "variations not standard PGN (book style)")
            p.data["status"] = "verified"
            return "verified"

    err = replay_error(fen, turn, main) or "?"
    p.ledger_add("verify", "REPLAY_FAIL", err)
    p.data["status"] = "needs_review"
    return "needs_review"
