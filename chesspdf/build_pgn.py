#!/usr/bin/env python3
"""Assemble the final, machine-verified PGN from recognized problems,
solutions, audit results, and fen_fixes overlays.

Per puzzle, in order of preference:
  1. full movetext parses legally           -> emit full solution (variations kept)
  2. book-convention transform parses       -> emit transformed
  3. mainline parses                        -> emit mainline; original text kept as comment
  4. nothing parses                         -> goes to the review file

Outputs:
  imagination_verified.pgn  - puzzles whose emitted moves replay legally
  imagination_review.pgn    - the rest (FEN present, moves unverified)
Both re-parsed with python-chess before writing; a parse error anywhere fails
the build loudly.
"""

from __future__ import annotations

import io
import json
import re
import os
from pathlib import Path

import chess
import chess.pgn

from .audit import load_problems, load_solutions
from .chesslib import (
    first_mover,
    strip_variations,
    structural_check,
    transform_book_variations,
    try_parse,
)

HERE = Path(os.environ.get("CHESSPDF_BOOK", "books/imagination")).resolve()
EVENT = "Imagination in Chess"


def load_fixes() -> dict[str, dict]:
    fixes = {}
    path = HERE / "fen_fixes.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") in ("FIXED", "SHIFT_FIXED", "MOVES_SUSPECT"):
                fixes[r["id"]] = r
    return fixes


def load_jsonl(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                out[r["id"]] = r  # last write wins
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def emit(game_headers: dict[str, str], game: chess.pgn.Game) -> str:
    for k, v in game_headers.items():
        game.headers[k] = v
    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter)


def main() -> None:
    problems = load_problems()
    solutions = load_solutions()
    fixes = load_fixes()
    move_fixes = load_jsonl(HERE / "moves_fixes.jsonl")
    overrides = load_jsonl(HERE / "human_overrides.jsonl")

    verified: list[str] = []
    review: list[str] = []
    skipped: list[str] = []
    stats = {"full": 0, "transformed": 0, "mainline": 0, "unparsed": 0, "bad_fen": 0}

    def sort_key(pid: str) -> int:
        return int(re.sub(r"\D", "", pid) or 0)

    for pid in sorted(problems, key=sort_key):
        prob = problems[pid]
        ov = overrides.get(pid)
        if ov and ov.get("verdict") == "exclude":
            skipped.append(pid)
            continue
        fix = fixes.get(pid)
        fen = (fix["fen"] if fix else prob["fen"]).split()[0]
        moves = solutions.get(pid)
        mfix = move_fixes.get(pid)
        if mfix and mfix.get("status") in ("REPAIRED", "REOCRED") and mfix.get("moves"):
            moves = mfix["moves"]
        turn = (fix or {}).get("to_move") or (first_mover(moves) if moves else None) \
            or prob.get("to_move", "w")
        if ov:  # human verdicts outrank every automated source
            fen = ov.get("fen", fen).split()[0] or fen
            turn = ov.get("to_move", turn)
        if turn not in ("w", "b"):
            turn = "w"

        headers = {
            "Event": EVENT,
            "White": f"Puzzle {pid}",
            "Black": "White to move" if turn == "w" else "Black to move",
            "Round": pid,
            "Result": "*",
        }

        if structural_check(fen) is not None:
            stats["bad_fen"] += 1
            skipped.append(pid)
            continue

        game = None
        if moves:
            game = try_parse(fen, turn, moves)
            if game is not None:
                stats["full"] += 1
            else:
                game = try_parse(fen, turn, transform_book_variations(moves))
                if game is not None:
                    stats["transformed"] += 1
                else:
                    main_only = strip_variations(moves)
                    game = try_parse(fen, turn, main_only)
                    if game is not None:
                        stats["mainline"] += 1
                        note = re.sub(r"[{}]", "'", moves.strip())
                        game.comment = f"Book solution (verbatim): {note}"

        if game is not None:
            result = game.headers.get("Result", "*")
            headers["Result"] = result if result in ("1-0", "0-1", "1/2-1/2") else "*"
            verified.append(emit(headers, game))
        else:
            stats["unparsed"] += 1
            body = ""
            if moves:
                note = re.sub(r"[{}]", "'", moves.strip())
                body = "{ UNVERIFIED book solution: " + note + " } "
            review.append(
                "\n".join(f'[{k} "{v}"]' for k, v in headers.items())
                + f'\n[SetUp "1"]\n[FEN "{fen} {turn} - - 0 1"]\n\n{body}*\n'
            )

    def check(text: str, label: str) -> None:
        count = 0
        stream = io.StringIO(text)
        while True:
            try:
                g = chess.pgn.read_game(stream)
            except Exception as exc:  # noqa: BLE001
                raise SystemExit(f"{label}: generated PGN failed to parse: {exc}")
            if g is None:
                break
            if g.errors:
                raise SystemExit(f"{label}: game {g.headers.get('Round')} has errors: {g.errors[0]}")
            count += 1
        print(f"{label}: {count} games, parses clean")

    verified_text = "\n\n".join(v.strip() for v in verified) + "\n"
    review_text = "\n\n".join(r.strip() for r in review) + "\n"
    check(verified_text, "verified")
    check(review_text, "review")
    (HERE / "imagination_verified.pgn").write_text(verified_text)
    (HERE / "imagination_review.pgn").write_text(review_text)
    print(f"stats: {stats}")
    if skipped:
        print(f"skipped (unusable FEN, awaiting fix): {skipped}")


if __name__ == "__main__":
    main()
