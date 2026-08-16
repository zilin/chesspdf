#!/usr/bin/env python3
"""Deterministic FEN repair for problems the audit flagged ILLEGAL/STRUCT.

Per problem: ask Gemini for a square-by-square listing of the diagram,
assemble the FEN in code, verify by replaying the recognized solution
mainline, and only when verification fails re-ask about the suspect squares.
Escalates to a stronger model for a final arbitration read. Writes results to
fen_fixes.jsonl (idempotent: already-fixed ids are skipped on rerun).

Usage:
  <python-with-deps> fix_fens.py [--limit N] [--workers 4] [--ids 7,10,...]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import chess
from google import genai
from google.genai import types

from .audit import load_env, load_problems, load_solutions
from .chesslib import (
    mainline_tokens,
    replay_error,
    strip_variations,
    structural_check,
    transform_book_variations,
)
from .occupancy import EMPTY, OCCUPIED, occupancy_mask

HERE = Path(os.environ.get("CHESSPDF_BOOK", "books/imagination")).resolve()
FIXES = HERE / "fen_fixes.jsonl"
MODEL_FAST = "gemini-3.5-flash"
MODEL_STRONG = "gemini-pro-latest"

LISTING_PROMPT = """This is a chess diagram from a book, White at the bottom. In this book's
printing, WHITE pieces are drawn as hollow outlines; BLACK pieces are solid
filled shapes.
List the piece on every occupied square, rank 8 down to rank 1, one line per
occupied square, exactly like:
e4: white pawn
Use piece names king/queen/rook/bishop/knight/pawn. After the listing, on a
final line, report the caption under the board if any (e.g. CAPTION: White to move)."""

RECHECK_PROMPT = """This is the same chess diagram (White at the bottom).
Look very carefully at ONLY these squares: {squares}.
For each, answer one line exactly like 'e4: white pawn' or 'e4: empty'.
Distinguish carefully between bishop/pawn and king/queen shapes."""

PIECE = {"king": "k", "queen": "q", "rook": "r", "bishop": "b", "knight": "n", "pawn": "p"}
LINE_RE = re.compile(r"\b([a-h][1-8])\s*[:\-]\s*(white|black)\s+(king|queen|rook|bishop|knight|pawn)", re.I)
EMPTY_RE = re.compile(r"\b([a-h][1-8])\s*[:\-]\s*empty", re.I)

_tls = threading.local()


def client() -> genai.Client:
    if not hasattr(_tls, "c"):
        _tls.c = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _tls.c


def ask(image_bytes: bytes, prompt: str, model: str) -> str:
    delay = 2.0
    for attempt in range(5):
        try:
            r = client().models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt],
            )
            return r.text or ""
        except Exception as exc:  # noqa: BLE001 — 429/5xx/stream errors: back off and retry
            if attempt == 4:
                raise
            time.sleep(delay)
            delay *= 2
    return ""


def parse_listing(text: str) -> tuple[dict[str, str], str | None]:
    board: dict[str, str] = {}
    for sq, color, piece in LINE_RE.findall(text):
        sym = PIECE[piece.lower()]
        board[sq.lower()] = sym.upper() if color.lower() == "white" else sym
    caption = None
    m = re.search(r"caption[:\s]*.*?(white|black)\s+to\s+(move|play)", text, re.I)
    if m:
        caption = "w" if m.group(1).lower() == "white" else "b"
    return board, caption


def to_fen(board: dict[str, str]) -> str:
    rows = []
    for rank in range(8, 0, -1):
        row, empty = "", 0
        for file in "abcdefgh":
            piece = board.get(f"{file}{rank}")
            if piece:
                row += (str(empty) if empty else "") + piece
                empty = 0
            else:
                empty += 1
        rows.append(row + (str(empty) if empty else ""))
    return "/".join(rows)


def verify(fen: str, moves: str | None, to_move: str) -> tuple[bool, str | None, set[str]]:
    """Return (ok, resolved_turn, suspect_squares_from_break)."""
    if structural_check(fen) is not None:
        return False, None, set()
    if not moves:
        return False, None, set()  # no solution: cannot verify here
    main = strip_variations(moves)
    for turn in (to_move, "b" if to_move == "w" else "w"):
        err = replay_error(fen, turn, main)
        if err is None:
            # Variations are a confidence signal, never a veto: variation
            # text is itself OCR and may be mangled independently of the FEN.
            vars_ok = "(" not in moves \
                or replay_error(fen, turn, moves) is None \
                or replay_error(fen, turn, transform_book_variations(moves)) is None
            verify.last_variations_ok = vars_ok
            return True, turn, set()
    # extract the failing SAN to implicate squares
    err = replay_error(fen, to_move, main)
    suspects: set[str] = set()
    m = re.search(r"illegal san: '([^']+)'", err or "")
    if m:
        san = m.group(1)
        for sq in re.findall(r"[a-h][1-8]", san):
            suspects.add(sq)
    return False, None, suspects


def fix_one(pid: str, prob: dict, moves: str | None, image: Path) -> dict:
    img = image.read_bytes()
    rec: dict = {"id": pid, "old_fen": prob["fen"].split()[0]}

    listing = ask(img, LISTING_PROMPT, MODEL_FAST)
    board, caption = parse_listing(listing)
    fen1 = to_fen(board)
    to_move = caption or prob.get("to_move", "w")
    ok, turn, suspects = verify(fen1, moves, to_move)
    rec.update(fen=fen1, to_move=turn or to_move, caption=caption)
    if ok:
        rec.update(status="FIXED", rounds=1,
                   variations_ok=getattr(verify, "last_variations_ok", None))
        return rec

    # Round 1.5: classical-CV occupancy cross-check (no API cost).
    mask = occupancy_mask(image)
    if mask:
        def mismatch(bd: dict) -> int:
            return sum(1 for sq, st in mask.items() if st != "uncertain"
                       and (sq in bd) != (st == OCCUPIED))

        def shift_board(bd: dict, df: int, dr: int) -> dict | None:
            out = {}
            for sq, piece in bd.items():
                f, r = ord(sq[0]) - 97 + df, int(sq[1]) + dr
                if not (0 <= f <= 7 and 1 <= r <= 8):
                    return None
                out[chr(97 + f) + str(r)] = piece
            return out

        # a whole-board shift shows as a globally better mask agreement
        base_mm = mismatch(board)
        for df, dr in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            cand = shift_board(board, df, dr)
            if cand is not None and mismatch(cand) < base_mm - len(board) // 2:
                fen_s = to_fen(cand)
                ok, turn, _ = verify(fen_s, moves, to_move)
                if ok:
                    rec.update(fen=fen_s, to_move=turn, status="FIXED",
                               rounds=1.5, note="cv global shift")
                    return rec
                board = cand      # even unverified, the aligned board is the
                break             # better base for per-piece + later rounds

        adjusted = dict(board)
        moved = False
        for sq, piece in list(adjusted.items()):
            if mask.get(sq) == EMPTY:
                f, r = ord(sq[0]) - 97, int(sq[1])
                nbrs = [chr(97 + f + df) + str(r + dr)
                        for df in (-1, 0, 1) for dr in (-1, 0, 1)
                        if (df or dr) and 0 <= f + df <= 7 and 1 <= r + dr <= 8]
                cands = [n for n in nbrs
                         if mask.get(n) == OCCUPIED and n not in adjusted]
                if len(cands) == 1:
                    del adjusted[sq]
                    adjusted[cands[0]] = piece
                    moved = True
        if moved:
            fen15 = to_fen(adjusted)
            ok, turn, _ = verify(fen15, moves, to_move)
            if ok:
                rec.update(fen=fen15, to_move=turn, status="FIXED",
                           rounds=1.5, note="cv occupancy realignment")
                return rec

    # Round 2: targeted recheck of implicated + disagreeing squares.
    targets = set(suspects)
    if mask:
        for sq, state in mask.items():
            model_occ = sq in board
            if state == OCCUPIED and not model_occ:
                targets.add(sq)
            elif state == EMPTY and model_occ:
                targets.add(sq)
    # squares where the fresh read disagrees with the original recognition
    try:
        old = chess.Board(prob["fen"].split()[0] + " w - - 0 1")
        for sq in chess.SQUARES:
            name = chess.square_name(sq)
            old_piece = old.piece_at(sq)
            old_sym = old_piece.symbol() if old_piece else None
            if board.get(name) != old_sym and (board.get(name) or old_sym):
                targets.add(name)
    except ValueError:
        pass
    if targets:
        recheck = ask(img, RECHECK_PROMPT.format(squares=", ".join(sorted(targets))), MODEL_STRONG)
        patch, _ = parse_listing(recheck)
        for sq, _e in EMPTY_RE.findall(recheck):
            board.pop(sq.lower(), None)
        board.update(patch)
        fen2 = to_fen(board)
        ok, turn, _ = verify(fen2, moves, to_move)
        rec.update(fen=fen2, to_move=turn or to_move)
        if ok:
            rec.update(status="FIXED", rounds=2)
            return rec

    # Round 3: full strong-model read as arbitration.
    listing3 = ask(img, LISTING_PROMPT, MODEL_STRONG)
    board3, caption3 = parse_listing(listing3)
    fen3 = to_fen(board3)
    ok, turn, _ = verify(fen3, moves, caption3 or to_move)
    if ok:
        rec.update(fen=fen3, to_move=turn, status="FIXED", rounds=3)
        return rec

    # Unverifiable. If two independent reads agree, the FEN is probably right
    # and the *solution* is suspect; otherwise leave it for the agent queue.
    if fen3 == fen1:
        rec.update(fen=fen3, status="MOVES_SUSPECT", rounds=3)
        return rec
    # CV arbitration: the occupancy mask picks between disagreeing reads
    # (~79% accurate on eval data) — adopt only on a clear margin.
    if mask:
        def _mm(fen: str) -> int:
            bd = {}
            for i, row in enumerate(fen.split("/")):
                f = 0
                for ch in row:
                    if ch.isdigit():
                        f += int(ch)
                    else:
                        bd[chr(97 + f) + str(8 - i)] = ch
                        f += 1
            return sum(1 for s, st in mask.items() if st != "uncertain"
                       and (s in bd) != (st == OCCUPIED))
        m1, m3 = _mm(fen1), _mm(fen3)
        if min(m1, m3) == 0 and abs(m1 - m3) >= 2:  # winner must match mask perfectly
            pick = fen1 if m1 < m3 else fen3
            rec.update(fen=pick, status="FIXED", rounds=3.5,
                       note=f"cv arbitration ({min(m1,m3)} vs {max(m1,m3)} mask mismatches)",
                       confidence="low")
            return rec
    rec.update(status="UNRESOLVED", fen_fast=fen1, fen_strong=fen3, rounds=3)
    return rec


_IMAGE_INDEX: dict[str, Path] | None = None


def image_for(pid: str) -> Path | None:
    """Exact lookup: '*' in filenames is the book's difficulty marker, a
    literal character — never glob (49*.png would match 499**.png)."""
    global _IMAGE_INDEX
    if _IMAGE_INDEX is None:
        _IMAGE_INDEX = {p.stem.replace("*", ""): p
                        for p in sorted((HERE / "problem_images").glob("*.png"))}
    return _IMAGE_INDEX.get(pid)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--ids", default=None)
    args = parser.parse_args()

    load_env()

    report = json.loads((HERE / "audit_report.json").read_text())
    problems = load_problems()
    solutions = load_solutions()

    done = set()
    if FIXES.exists():
        for line in FIXES.read_text().splitlines():
            try:
                r = json.loads(line)
                if r.get("status") != "ERROR":
                    done.add(r["id"])
            except json.JSONDecodeError:
                pass

    if args.ids:
        targets = [i.strip() for i in args.ids.split(",")]
    else:
        targets = [pid for pid, e in report.items()
                   if e["status"] in ("ILLEGAL", "STRUCT") and pid not in done]
    targets = [t for t in targets if image_for(t)]
    if args.limit:
        targets = targets[: args.limit]
    print(f"{len(targets)} problems to fix ({len(done)} already done)")

    lock = threading.Lock()
    counts: dict[str, int] = {}
    t0 = time.monotonic()

    def work(pid: str) -> dict:
        try:
            return fix_one(pid, problems[pid], solutions.get(pid), image_for(pid))
        except Exception as exc:  # noqa: BLE001
            return {"id": pid, "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(work, pid): pid for pid in targets}
        for i, fut in enumerate(as_completed(futures)):
            rec = fut.result()
            with lock:
                with FIXES.open("a") as f:
                    f.write(json.dumps(rec) + "\n")
                counts[rec["status"]] = counts.get(rec["status"], 0) + 1
                print(f"[{i + 1}/{len(targets)}] {rec['id']:>6s} {rec['status']:14s} "
                      f"rounds={rec.get('rounds')} {counts}")
    print(f"\nDone in {(time.monotonic() - t0) / 60:.1f} min: {counts}")


if __name__ == "__main__":
    main()
