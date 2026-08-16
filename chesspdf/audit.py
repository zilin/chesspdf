#!/usr/bin/env python3
"""Phase 0 consistency audit: verify every recognized FEN against its
recognized solution line, with zero model calls.

For each problem id:
  STRUCT   - FEN fails structural checks (kings/pawns/parse)
  NO_SOL   - no solution moves found for this id
  TURN     - to_move disagrees with the solution's first mover (repairable)
  PARSE    - solution text does not parse as PGN movetext
  ILLEGAL  - solution replays illegally from the FEN (strong wrong-FEN signal)
  ENGINE   - replays legally, but stockfish disagrees the first move is strong
  OK       - everything consistent

Usage:
  <python-with-python-chess> audit.py [--engine] [--report audit_report.json]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
import os
from pathlib import Path

from .chesslib import first_mover, replay_error, strip_variations, structural_check

HERE = Path(os.environ.get("CHESSPDF_BOOK", "books/imagination")).resolve()


def load_env() -> None:
    """Load API keys from the book's .env, falling back to the working dir's."""
    for candidate in (HERE / ".env", Path.cwd() / ".env"):
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
            return


def load_problems() -> dict[str, dict]:
    problems = {}
    for path in (HERE / "problem_jsons").glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        problems[data["id"].replace("*", "")] = data
    return problems


def solution_key(raw_id: str) -> str:
    """Normalize a solution id to its numeric prefix: the back-of-book section
    prints ids like '334 Ebralidze-Blagidze, Tbilisi 1949'."""
    m = re.match(r"\s*(\d+)", raw_id)
    return m.group(1) if m else raw_id.replace("*", "")


def load_solutions() -> dict[str, str]:
    solutions = {}
    for path in (HERE / "solution_jsons").glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for sol in data.get("solutions", []):
            solutions.setdefault(solution_key(sol["id"]), sol["moves"])
    return solutions


def load_solution_names() -> dict[str, str]:
    """Game attributions ('Ebralidze-Blagidze, Tbilisi 1949') when printed."""
    names = {}
    for path in (HERE / "solution_jsons").glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for sol in data.get("solutions", []):
            raw = sol["id"]
            m = re.match(r"\s*\d+\s+(.+)", raw)
            if m:
                names[solution_key(raw)] = m.group(1).strip()
    return names


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(HERE / "audit_report.json"))
    args = parser.parse_args()

    problems = load_problems()
    solutions = load_solutions()
    print(f"{len(problems)} problems, {len(solutions)} solutions, "
          f"{len(set(problems) & set(solutions))} paired")
    images = {p.stem.replace("*", "") for p in (HERE / "problem_images").glob("*.png")}
    if unrecognized := sorted(images - set(problems)):
        print(f"WARNING: {len(unrecognized)} images with no recognition: {unrecognized}")

    report = {}
    for pid, prob in sorted(problems.items(), key=lambda kv: int(re.sub(r"\D", "", kv[0]) or 0)):
        fen_board = prob["fen"].split()[0]
        entry: dict = {"fen": fen_board, "to_move": prob.get("to_move")}

        if (err := structural_check(fen_board)) is not None:
            entry.update(status="STRUCT", detail=err)
            report[pid] = entry
            continue

        moves = solutions.get(pid)
        if not moves:
            entry.update(status="NO_SOL")
            report[pid] = entry
            continue

        mover = first_mover(moves)
        turn = prob.get("to_move", "w")
        if mover and mover != turn:
            entry["turn_mismatch"] = f"json says {turn}, solution starts with {mover}"
            turn = mover  # trust the solution's own numbering

        err = replay_error(fen_board, turn, moves)
        if err is not None:
            # A wrong side-to-move can masquerade as an illegal first move.
            other = "b" if turn == "w" else "w"
            err2 = replay_error(fen_board, other, moves)
            if err2 is None:
                entry.update(status="TURN", detail=f"replays only as {other}; {err}")
                report[pid] = entry
                continue
            # Book-style variations ("(1... fxg5 ...)" = replies, not
            # alternatives) violate PGN semantics; retry mainline-only to
            # separate notation-convention failures from wrong-FEN failures.
            main = strip_variations(moves)
            err3 = replay_error(fen_board, turn, main)
            if err3 is None:
                entry.update(status="VAR_ONLY", detail=f"mainline ok; full: {err}")
            else:
                err4 = replay_error(fen_board, other, main)
                if err4 is None:
                    entry.update(status="TURN", detail=f"mainline replays only as {other}")
                    report[pid] = entry
                    continue
                entry.update(status=err.split(":")[0], detail=f"{err} | mainline: {err3}")
                report[pid] = entry
                continue

        if entry.get("turn_mismatch"):
            entry.update(status="TURN", detail=entry["turn_mismatch"])
            report[pid] = entry
            continue

        if "status" not in entry:
            entry.update(status="OK")
        report[pid] = entry

    Path(args.report).write_text(json.dumps(report, indent=1))
    counts = Counter(e["status"] for e in report.values())
    print("\n=== Audit summary ===")
    for status, n in counts.most_common():
        print(f"{status:8s} {n}")
    fails = [pid for pid, e in report.items() if e["status"] not in ("OK",)]
    print(f"\nNeeds attention: {len(fails)} of {len(report)}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
