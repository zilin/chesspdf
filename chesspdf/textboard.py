"""Decode chess diagrams typeset as TEXT in Marroquin diagram fonts
(Chess Merida et al.) straight from the PDF text layer — no vision model.

Glyph encoding (shared across the Marroquin family, PUA = 0xF000 + ASCII):
lowercase = piece on light square, UPPERCASE = on dark square.
White K Q R B N P = k q r b n p; Black = l w t v m o; empty = ' ' light /
'+' dark. Line format per rank: [rank label 0xC0+r] [8 square glyphs]
[right border '5'].

PDF quirks handled here (all observed, not assumed):
- get_text(clip=...) filters by span intersection -> filter per glyph instead.
- space glyphs vanish at span boundaries and collapsed bboxes misplace chars,
  so squares are read in STREAM order and dropped spaces are re-inserted at
  the unique positions allowed by the case-must-match-square-color rule.
- a corrected board may be overlaid on a ghost of another in a different span
  color: layers are decoded per color and the last-drawn wins.
"""

from __future__ import annotations

from itertools import combinations

import fitz

PIECE = {  # decoded ASCII (lowercased) -> FEN letter
    "k": "K", "q": "Q", "r": "R", "b": "B", "n": "N", "p": "P",
    "l": "k", "w": "q", "t": "r", "v": "b", "m": "n", "o": "p",
}
EMPTY = {" ", "+"}


def is_light(rank: int, file_idx: int) -> bool:  # file_idx 0-based
    return (rank + file_idx + 1) % 2 == 1


def valid_row(rank: int, cells: list[str]) -> bool:
    for f, ch in enumerate(cells):
        light = is_light(rank, f)
        if ch == " " and not light:
            return False
        if ch == "+" and light:
            return False
        if ch not in EMPTY and ch.islower() != light:
            return False
    return True


def complete_row(rank: int, chars: list[str]) -> list[str]:
    """chars in stream order (spaces may be missing) -> unique valid 8 cells."""
    if len(chars) > 8:
        raise ValueError(f"rank {rank}: {len(chars)} squares: {chars!r}")
    solutions = set()
    for slots in combinations(range(8), len(chars)):
        cells = [" "] * 8
        for slot, ch in zip(slots, chars):
            cells[slot] = ch
        if valid_row(rank, cells):
            solutions.add(tuple(cells))
    if len(solutions) != 1:
        raise ValueError(f"rank {rank}: {len(solutions)} valid fills for {chars!r}")
    return list(solutions.pop())


def cell_layers(page: fitz.Page, clip: fitz.Rect, font: str, row_bucket: float) -> dict:
    """Diagram glyphs in clip, grouped by span color -> {color: (last_seq, rows)}
    where rows = {y_bucket: [(seq, ch), ...]} in stream order."""
    layers: dict = {}
    seq = 0
    for b in page.get_text("rawdict", clip=clip)["blocks"]:
        if b["type"] != 0:
            continue
        for line in b.get("lines", []):
            for s in line["spans"]:
                if font not in s["font"]:
                    continue
                color = s.get("color", 0)
                for c in s["chars"]:
                    seq += 1
                    x0, y0 = c["bbox"][0], c["bbox"][1]
                    if not (clip.x0 <= x0 < clip.x1 and clip.y0 <= y0 < clip.y1):
                        continue
                    ch = chr(ord(c["c"]) & 0xFF)  # strip PUA offset
                    last, rows = layers.setdefault(color, [0, {}])
                    layers[color][0] = seq
                    rows.setdefault(round(y0 / row_bucket), []).append((seq, ch))
    return layers


def decode_layer(rows: dict) -> str:
    """one color layer's rows -> FEN placement, or raise ValueError."""
    ranks: dict[int, list[str]] = {}
    for row in rows.values():
        row.sort()  # stream order
        chs = [ch for _, ch in row]
        labels = [ch for ch in chs if 0xC0 <= ord(ch) <= 0xC7]
        if len(labels) != 1:
            continue  # border / file-letter rows
        rank = ord(labels[0]) - 0xC0 + 1  # 0xC0='1' ... 0xC7='8'
        after = chs[chs.index(labels[0]) + 1:]
        if "5" in after:
            after = after[:after.index("5")]  # right border
        sq = [ch for ch in after if ch in EMPTY or ch.lower() in PIECE]
        ranks[rank] = complete_row(rank, sq)
    if sorted(ranks) != list(range(1, 9)):
        raise ValueError(f"ranks found: {sorted(ranks)}")
    fen_rows = []
    for rank in range(8, 0, -1):
        row_s, run = "", 0
        for ch in ranks[rank]:
            if ch in EMPTY:
                run += 1
            else:
                if run:
                    row_s += str(run)
                    run = 0
                row_s += PIECE[ch.lower()]
        if run:
            row_s += str(run)
        fen_rows.append(row_s)
    return "/".join(fen_rows)


def decode_board(page: fitz.Page, clip: fitz.Rect, font: str = "Merida",
                 row_bucket: float = 6) -> tuple[str, str | None]:
    """-> (placement, note). Last-drawn layer wins when layers disagree."""
    boards = []  # (last_seq, placement)
    errs = []
    for color, (last, rows) in cell_layers(page, clip, font, row_bucket).items():
        try:
            boards.append((last, decode_layer(rows)))
        except ValueError as e:
            errs.append(f"color {color:#x}: {e}")
    if not boards:
        raise ValueError("; ".join(errs) or "no diagram glyphs")
    boards.sort()
    distinct = {p for _, p in boards}
    note = f"{len(distinct)} distinct layers, last-drawn kept" if len(distinct) > 1 else None
    return boards[-1][1], note
