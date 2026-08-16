#!/usr/bin/env python3
"""Typeset a CC0 sample puzzle book — a real book to try the pipeline on.

  chesspdf sample --out mybook.pdf            # 100 puzzles + answer key

Public-domain chess books all predate modern algebraic notation (US public
domain ends at 1929, SAN spread in the 1970s), so no book exists that is both
freely redistributable and written the way books are today. This generates
one from the Lichess puzzle database (CC0, no rights reserved), laid out the
way an exercise book is: a 2x3 grid of numbered diagrams with a hint line,
then a solutions section in SAN.

400 CC0 puzzles ship in the package, so this works offline; --csv takes a
decompressed lichess_db_puzzle.csv to draw from the full six million.

Alongside the PDF it writes truth.json, the answer key: because the book is
generated from known data, a pipeline run over it can be graded outright
(see `chesspdf score`) instead of reviewed by hand.

In each database row the FEN is the position BEFORE the losing move, and the
first UCI move creates the puzzle position; the rest is the solution.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
from pathlib import Path

import chess
import fitz
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg

PKG = Path(__file__).resolve().parent
PKG_PIECES = PKG / "pieces"
BUNDLED_CSV = PKG / "data" / "puzzles_cc0.csv"
PIECE_FILE = {"K": "wK", "Q": "wQ", "R": "wR", "B": "wB", "N": "wN", "P": "wP",
              "k": "bK", "q": "bQ", "r": "bR", "b": "bB", "n": "bN", "p": "bP"}

PAGE_W, PAGE_H = 595, 842                     # A4 points
COLS, ROWS = 2, 3
BOARD = 190                                   # board side in points
CELL_W, CELL_H = PAGE_W / COLS, 250
MARGIN_TOP = 70

THEME_HINT = {
    "mateIn1": "Mate in one.",
    "mateIn2": "Mate in two.",
    "mateIn3": "Mate in three.",
    "fork": "Look for a fork.",
    "pin": "A pin decides.",
    "skewer": "Look for a skewer.",
    "discoveredAttack": "A discovered attack wins.",
    "hangingPiece": "Something is hanging.",
    "deflection": "Deflect a defender.",
    "sacrifice": "A sacrifice breaks through.",
    "endgame": "Find the winning idea in the endgame.",
}


def piece_svg_body(symbol: str) -> str:
    """Inner markup of a piece SVG, for inlining into the page."""
    raw = (PKG_PIECES / f"{PIECE_FILE[symbol]}.svg").read_text()
    return raw[raw.index(">", raw.index("<svg")) + 1: raw.rindex("</svg>")]


PIECE_BODY = {s: piece_svg_body(s) for s in PIECE_FILE}


def board_svg(board: chess.Board, x: float, y: float, size: float) -> str:
    """A diagram, White at the bottom, hatched dark squares like print books."""
    sq = size / 8
    out = [f'<rect x="{x}" y="{y}" width="{size}" height="{size}" '
           f'fill="#fff" stroke="#000" stroke-width="1.2"/>']
    for rank in range(8):
        for file in range(8):
            if (rank + file) % 2 == 1:         # dark square (a1 is dark)
                out.append(f'<rect x="{x + file * sq}" y="{y + rank * sq}" '
                           f'width="{sq}" height="{sq}" fill="#d8d8d8"/>')
            piece = board.piece_at(chess.square(file, 7 - rank))
            if piece:
                scale = sq / 45
                out.append(
                    f'<g transform="translate({x + file * sq},{y + rank * sq}) '
                    f'scale({scale})">{PIECE_BODY[piece.symbol()]}</g>')
    return "".join(out)


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text_svg(x: float, y: float, s: str, size: float = 10.5,
             weight: str = "normal", anchor: str = "middle",
             family: str = "DejaVu Sans, Helvetica, sans-serif") -> str:
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}" fill="#000">{esc(s)}</text>')


def svg_page(body: str) -> bytes:
    # rendered through svglib/reportlab so the generator needs no native
    # cairo install
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAGE_W}" '
           f'height="{PAGE_H}" viewBox="0 0 {PAGE_W} {PAGE_H}">'
           f'<rect width="{PAGE_W}" height="{PAGE_H}" fill="#fff"/>{body}</svg>')
    drawing = svg2rlg(io.BytesIO(svg.encode()))
    return renderPDF.drawToString(drawing)


def load_puzzles(csv_path: Path, want: int, seed: int) -> list[dict]:
    """Pick a spread of short, solvable-looking puzzles across themes."""
    rows = []
    with csv_path.open(newline="") as f:
        for r in csv.DictReader(f):
            try:                               # the last row of a truncated
                rating = int(r["Rating"])      # prefix is incomplete
            except (KeyError, ValueError, TypeError):
                continue
            themes = r.get("Themes", "").split()
            if not (900 <= rating <= 1900) or "short" not in themes:
                continue
            rows.append(r)
    random.Random(seed).shuffle(rows)
    out = []
    for r in rows:
        board = chess.Board(r["FEN"])
        ucis = r["Moves"].split()
        if len(ucis) < 2:
            continue
        try:
            board.push_uci(ucis[0])            # the blunder; puzzle starts here
            game_board = board.copy()
            sans = []
            for u in ucis[1:]:
                sans.append(board.san(chess.Move.from_uci(u)))
                board.push_uci(u)
        except (ValueError, AssertionError):
            continue
        themes = r.get("Themes", "").split()
        hint = next((THEME_HINT[t] for t in themes if t in THEME_HINT),
                    "Find the best move.")
        out.append({"fen": game_board.fen(), "turn": game_board.turn,
                    "sans": sans, "hint": hint, "rating": int(r["Rating"]),
                    "url": r.get("GameUrl", ""), "id": r["PuzzleId"]})
        if len(out) == want:
            break
    return out


def puzzle_pages(puzzles: list[dict]) -> list[bytes]:
    pages = []
    for start in range(0, len(puzzles), COLS * ROWS):
        chunk = puzzles[start:start + COLS * ROWS]
        body = [text_svg(PAGE_W / 2, 40, "Sample Tactics — Exercises", 14, "bold")]
        for i, p in enumerate(chunk):
            col, row = i % COLS, i // COLS
            cx = col * CELL_W + CELL_W / 2
            top = MARGIN_TOP + row * CELL_H
            side = "White to play" if p["turn"] == chess.WHITE else "Black to play"
            body.append(text_svg(cx, top, f"Puzzle {start + i + 1} ({side})", 11, "bold"))
            body.append(board_svg(chess.Board(p["fen"]), cx - BOARD / 2, top + 10, BOARD))
            body.append(text_svg(cx, top + BOARD + 30, p["hint"], 9.5))
        body.append(text_svg(PAGE_W / 2, PAGE_H - 30,
                             str(start // (COLS * ROWS) + 1), 9))
        pages.append(svg_page("".join(body)))
    return pages


def solution_pages(puzzles: list[dict]) -> list[bytes]:
    """One entry per puzzle: number, SAN mainline, source puzzle id."""
    lines: list[tuple[str, str]] = []
    for i, p in enumerate(puzzles, 1):
        move_no = chess.Board(p["fen"]).fullmove_number
        text, n = [], move_no
        for j, san in enumerate(p["sans"]):
            white_to_move = (p["turn"] == chess.WHITE) == (j % 2 == 0)
            if white_to_move:
                text.append(f"{n}.{san}")
            else:
                text.append(f"{n}...{san}" if j == 0 else san)
                n += 1
        lines.append((f"{i}. {' '.join(text)}", f"lichess {p['id']}"))

    pages, per_page = [], 34
    for start in range(0, len(lines), per_page):
        body = [text_svg(PAGE_W / 2, 40, "Solutions", 14, "bold")]
        y = 70
        for text, source in lines[start:start + per_page]:
            body.append(text_svg(45, y, text, 9.5, anchor="start"))
            body.append(text_svg(PAGE_W - 45, y, source, 7, anchor="end"))
            y += 20
        pages.append(svg_page("".join(body)))
    return pages


def title_page(n: int) -> bytes:
    lines = [
        (300, "Sample Tactics", 26, "bold"),
        (330, f"{n} puzzles in algebraic notation", 12, "normal"),
        (420, "Puzzles from the Lichess puzzle database (CC0).", 10, "normal"),
        (438, "database.lichess.org — no rights reserved.", 10, "normal"),
        (476, "Generated as a redistributable sample book for chesspdf:", 10, "normal"),
        (494, "a book PDF whose every solution can be replay-verified.", 10, "normal"),
    ]
    return svg_page("".join(text_svg(PAGE_W / 2, y, s, size, w)
                            for y, s, size, w in lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None,
                    help="a decompressed lichess_db_puzzle.csv (CC0); "
                         "defaults to the 400 puzzles bundled with chesspdf")
    ap.add_argument("--puzzles", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="sample_book.pdf")
    args = ap.parse_args()

    puzzles = load_puzzles(Path(args.csv) if args.csv else BUNDLED_CSV,
                           args.puzzles, args.seed)
    print(f"selected {len(puzzles)} puzzles")

    doc = fitz.open()
    for pdf_bytes in ([title_page(len(puzzles))] + puzzle_pages(puzzles)
                      + solution_pages(puzzles)):
        doc.insert_pdf(fitz.open("pdf", pdf_bytes))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print(f"wrote {out} — {doc.page_count} pages")

    # Ship the answer key: a pipeline run over this book can be scored
    # automatically, with no human review in the loop.
    truth = {str(i): {"fen": p["fen"], "moves": " ".join(p["sans"]),
                      "lichess_id": p["id"]}
             for i, p in enumerate(puzzles, 1)}
    (out.parent / "truth.json").write_text(json.dumps(truth, indent=1))
    print(f"wrote {out.parent / 'truth.json'} — {len(truth)} answers")


if __name__ == "__main__":
    main()
