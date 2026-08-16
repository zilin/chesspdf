#!/usr/bin/env python3
"""Zero-cost repair pass: detect whole-board shift errors.

A common vision failure puts every piece one rank/file off. For each
unverified puzzle, try shifting each candidate FEN (original, re-recognized,
fast/strong reads) by one square in each direction; if exactly one shifted
variant lets the solution mainline replay, accept it.

Writes accepted repairs into fen_fixes.jsonl as status SHIFT_FIXED.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .audit import load_problems, load_solutions
from .chesslib import replay_error, strip_variations, structural_check

HERE = Path(os.environ.get("CHESSPDF_BOOK", "books/imagination")).resolve()
FIXES = HERE / "fen_fixes.jsonl"


def expand(fen: str) -> list[list[str]]:
    grid = []
    for row in fen.split("/"):
        cells: list[str] = []
        for ch in row:
            cells.extend([""] * int(ch) if ch.isdigit() else [ch])
        grid.append(cells)
    return grid


def collapse(grid: list[list[str]]) -> str:
    rows = []
    for cells in grid:
        row, empty = "", 0
        for c in cells:
            if c:
                row += (str(empty) if empty else "") + c
                empty = 0
            else:
                empty += 1
        rows.append(row + (str(empty) if empty else ""))
    return "/".join(rows)


def shifts(fen: str) -> list[tuple[str, str]]:
    """All one-square shifts that do not push a piece off the board."""
    grid = expand(fen)
    if any(len(r) != 8 for r in grid) or len(grid) != 8:
        return []
    out = []
    empty_row = [""] * 8
    if grid[0] == empty_row:      # room to shift up (toward rank 8)
        out.append(("up", collapse(grid[1:] + [empty_row])))
    if grid[7] == empty_row:      # down
        out.append(("down", collapse([empty_row] + grid[:7])))
    if all(r[0] == "" for r in grid):   # left (toward a-file)
        out.append(("left", collapse([r[1:] + [""] for r in grid])))
    if all(r[7] == "" for r in grid):   # right
        out.append(("right", collapse([[""] + r[:7] for r in grid])))
    return out


def verifies(fen: str, moves: str) -> str | None:
    if structural_check(fen) is not None:
        return None
    main = strip_variations(moves)
    for turn in ("w", "b"):
        if replay_error(fen, turn, main) is None:
            return turn
    return None


def main() -> None:
    import sys
    only = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
    problems = load_problems()
    solutions = load_solutions()
    records = {}
    for line in FIXES.read_text().splitlines():
        try:
            r = json.loads(line)
            records[r["id"]] = r
        except (json.JSONDecodeError, KeyError):
            continue
    if only:  # explicit ids: consider them even without a fix record
        for pid in only:
            records.setdefault(pid, {"id": pid, "status": "MOVES_SUSPECT"})

    repaired = 0
    for pid, rec in sorted(records.items()):
        if only is not None and pid not in only:
            continue
        if rec.get("status") not in ("MOVES_SUSPECT", "UNRESOLVED"):
            continue
        moves = solutions.get(pid)
        if not moves:
            continue
        candidates = {c for c in (rec.get("fen"), rec.get("fen_fast"),
                                  rec.get("fen_strong"),
                                  problems.get(pid, {}).get("fen", "").split()[0]) if c}
        hits = []
        for cand in candidates:
            for direction, shifted in shifts(cand):
                turn = verifies(shifted, moves)
                if turn is not None:
                    hits.append((shifted, turn, f"{direction} of {cand[:20]}…"))
        unique_fens = {h[0] for h in hits}
        if len(unique_fens) == 1:
            shifted, turn, how = hits[0]
            out = {"id": pid, "old_fen": rec.get("fen"), "fen": shifted,
                   "to_move": turn, "status": "SHIFT_FIXED",
                   "note": f"board shift detected: {how}", "rounds": 0}
            with FIXES.open("a") as f:
                f.write(json.dumps(out) + "\n")
            print(f"{pid:>6s} SHIFT_FIXED  {how}")
            repaired += 1
    print(f"\n{repaired} puzzles repaired by shift detection")


if __name__ == "__main__":
    main()
