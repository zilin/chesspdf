#!/usr/bin/env python3
"""Repair solution movetext for puzzles whose FEN is validated (or agreed by
independent reads) but whose mainline will not replay.

Strategy per puzzle, cheapest first:
  1. GAP FILL — replay the mainline until it breaks; if the break looks like a
     missing ply, try every legal move at the gap and keep candidates whose
     insertion lets the ENTIRE remaining mainline replay. Only a UNIQUE
     survivor is accepted (ambiguity routes to re-OCR / human review).
  2. SAN REPAIR — if the breaking token itself is illegal, try close variants
     (piece-letter swaps B/R/N/K, file/rank off-by-one is deliberately NOT
     guessed) plus 'the unique legal move matching destination square'.
  3. Anything still broken is left for re-OCR / the agent queue.

Writes moves_fixes.jsonl with {id, moves, status, note}.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import chess

from .audit import load_env, load_problems, load_solutions, solution_key
from .chesslib import (SAN_RE, disambiguate_line, first_mover, mainline_tokens,
                       rebuild_movetext, replay_sans, tail_replays)

HERE = Path(os.environ.get("CHESSPDF_BOOK", "books/imagination")).resolve()
OUT = HERE / "moves_fixes.jsonl"


def gap_fill(fen: str, turn: str, sans: list[str]) -> tuple[list[str] | None, str]:
    """Try inserting one legal move at the break point."""
    i, board = replay_sans(fen, turn, sans)
    if i == len(sans):
        return sans, "already-legal"
    survivors = []
    for mv in board.legal_moves:
        b2 = board.copy()
        san = b2.san(mv)
        b2.push(mv)
        if tail_replays(b2, sans[i:]):
            survivors.append(san)
    if len(survivors) == 1:
        fixed = sans[:i] + survivors + sans[i:]
        return fixed, f"gap-filled '{survivors[0]}' at ply {i + 1}"
    return None, f"break at ply {i + 1} ('{sans[i]}'), {len(survivors)} gap candidates"


def san_repair(fen: str, turn: str, sans: list[str]) -> tuple[list[str] | None, str]:
    """If the breaking SAN is itself corrupt, accept the unique legal move
    sharing its destination square (and, when present, its from-file/rank
    hint: what OCR gets wrong is the piece figurine — 'Rde7' read as 'Nde7' —
    while the algebraic characters beside it are plain text and reliable)."""
    i, board = replay_sans(fen, turn, sans)
    if i == len(sans):
        return sans, "already-legal"
    bad = sans[i]
    dest = re.findall(r"[a-h][1-8]", bad)
    if not dest:
        return None, "no destination square in token"
    target = chess.parse_square(dest[-1])

    def survivors(matches) -> list[str]:
        out = []
        for mv in board.legal_moves:
            if mv.to_square != target or not matches(mv):
                continue
            b2 = board.copy()
            san = b2.san(mv)
            b2.push(mv)
            if tail_replays(b2, sans[i + 1:]):
                out.append(san)
        return out

    m = SAN_RE.match(bad.rstrip("!?"))
    ff, fr = (m.group("ff"), m.group("fr")) if m else (None, None)
    if ff or fr:
        cands = survivors(
            lambda mv: (not ff or chess.square_file(mv.from_square) == ord(ff) - 97)
            and (not fr or chess.square_rank(mv.from_square) == int(fr) - 1))
        if len(cands) == 1:
            return (sans[:i] + cands + sans[i + 1:],
                    f"replaced '{bad}' with '{cands[0]}' (kept its from-square hint)")

    cands = survivors(lambda mv: True)
    if len(cands) == 1:
        return sans[:i] + cands + sans[i + 1:], f"replaced '{bad}' with '{cands[0]}'"
    return None, f"'{bad}': {len(cands)} same-destination candidates"


def page_index() -> dict[str, Path]:
    """Map puzzle id -> the solution page image containing its solution."""
    idx: dict[str, Path] = {}
    for jpath in (HERE / "solution_jsons").glob("page_*.json"):
        img = HERE / "solution_images" / (jpath.stem + ".png")
        if not img.exists():
            continue
        try:
            data = json.loads(jpath.read_text())
        except json.JSONDecodeError:
            continue
        for sol in data.get("solutions", []):
            idx[solution_key(sol["id"])] = img
    return idx


REOCR_PROMPT = """This book page contains numbered chess puzzle solutions.
Transcribe the solution for puzzle number {pid} as STANDARD PGN movetext:
- The mainline (the moves actually played) stays at the top level.
- This book prints alternative lines inline after ';' or '.', or in brackets:
  convert every alternative/refuted line into a PGN variation in (parentheses),
  placed immediately AFTER the mainline move it is an alternative to.
- Keep move numbers, '...' for Black-first, +/#, !/?, promotions, and the
  result token. Put prose annotations in {{curly braces}}.
Reply with the movetext only, no preamble."""


def reocr(pid: str, page: Path) -> str | None:
    from google import genai
    from google.genai import types

    load_env()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    r = client.models.generate_content(
        model="gemini-pro-latest",
        contents=[types.Part.from_bytes(data=page.read_bytes(), mime_type="image/png"),
                  REOCR_PROMPT.format(pid=pid)],
    )
    return r.text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", default=None)
    parser.add_argument("--force", action="store_true",
                        help="redo --ids even if already in moves_fixes.jsonl")
    args = parser.parse_args()

    problems = load_problems()
    solutions = load_solutions()
    fixes = {}
    fpath = HERE / "fen_fixes.jsonl"
    if fpath.exists():
        for line in fpath.read_text().splitlines():
            r = json.loads(line)
            if r.get("status") in ("FIXED", "SHIFT_FIXED", "MOVES_SUSPECT"):
                fixes[r["id"]] = r

    # Human verdicts outrank every automated source — including for the FEN a
    # repair is computed against. A repair derived from a FEN the human later
    # corrected is invalid, so re-running those ids needs --force.
    overrides = {}
    opath = HERE / "human_overrides.jsonl"
    if opath.exists():
        for line in opath.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("verdict") != "exclude":
                overrides[r["id"]] = r

    if args.ids:
        targets = [i.strip() for i in args.ids.split(",")]
    else:
        targets = [pid for pid, r in fixes.items() if r["status"] == "MOVES_SUSPECT"]

    done = set()
    best: dict[str, dict] = {}       # id -> the best repair seen so far
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            done.add(r["id"])
            if r.get("status") in ("REPAIRED", "REOCRED") and r.get("moves"):
                best[r["id"]] = r
    if args.force:
        done -= set(targets)

    counts: dict[str, int] = {}
    for pid in targets:
        if pid in done or pid not in solutions:
            continue
        moves = solutions[pid]
        ov = overrides.get(pid, {})
        fen = ov.get("fen") or fixes.get(pid, {}).get("fen") \
            or problems[pid]["fen"].split()[0]
        turn = ov.get("to_move") or first_mover(moves) \
            or fixes.get(pid, {}).get("to_move") or problems[pid].get("to_move", "w")
        sans = mainline_tokens(moves)
        rec = {"id": pid, "fen": fen, "to_move": turn}

        # printed SAN is often under-disambiguated ('Re7' with two rooks
        # reaching e7); resolve before any repair so later passes see a
        # strictly legal line
        pre = ""
        resolved = disambiguate_line(fen, turn, sans)
        if resolved is not None and resolved != sans:
            sans, pre = resolved, "disambiguated printed SAN; "

        fixed, note = gap_fill(fen, turn, sans)
        note = pre + note
        if fixed is None:
            fixed, note2 = san_repair(fen, turn, sans)
            note = f"{note}; {note2}"
        if fixed is not None:
            rec.update(status="REPAIRED", note=note,
                       moves=rebuild_movetext(fixed, turn))
        else:
            # Targeted re-OCR of this solution from its printed page.
            page = page_index().get(pid)
            new_text = None
            if page is not None:
                try:
                    new_text = reocr(pid, page)
                except Exception as exc:  # noqa: BLE001
                    note += f"; reocr failed: {type(exc).__name__}"
            if new_text:
                turn2 = first_mover(new_text) or turn
                sans2 = mainline_tokens(new_text)
                fixed2, note3 = gap_fill(fen, turn2, sans2)
                if fixed2 is not None:
                    rec.update(status="REOCRED", to_move=turn2,
                               note=f"{note}; reocr ok ({note3})",
                               moves=new_text.strip() if note3 == "already-legal"
                               else rebuild_movetext(fixed2, turn2))
                else:
                    rec.update(status="UNREPAIRED", note=f"{note}; reocr still breaks: {note3}",
                               reocr_text=new_text.strip()[:400])
            else:
                rec.update(status="UNREPAIRED", note=note)
        # Re-OCR is non-deterministic, so a --force rerun can come back empty
        # where an earlier attempt succeeded. Never let a failed retry bury a
        # repair that was already verified.
        prior = best.get(pid)
        if rec["status"] == "UNREPAIRED" and prior is not None:
            print(f"{pid:>6s} kept earlier {prior['status']} (retry failed)")
            counts["KEPT"] = counts.get("KEPT", 0) + 1
            continue
        best[pid] = rec if rec["status"] in ("REPAIRED", "REOCRED") and rec.get("moves") else prior
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        with OUT.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"{pid:>6s} {rec['status']:11s} {note}")
    print(f"\n{counts}")


if __name__ == "__main__":
    main()
