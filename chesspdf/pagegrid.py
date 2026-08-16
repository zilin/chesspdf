"""Vector-anchored puzzle grid for born-digital books.

Anchor shapes (the circled puzzle numbers) define the grid; every book-tuned
number lives in books/<book>/layout.json, loaded as a Layout. The mechanism
(anchor detection, column-major ordering, sequence-corrected numbering,
side-to-move glyph lookup, cell geometry) is book-independent.

layout.json schema (values shown are books/woodpecker's):
{
  "zoom": 2.2,                       # render scale for cell images
  "anchor_size": [18, 25],           # anchor bbox width/height range, points
  "anchor_items": 4,                 # path segments in the anchor drawing
  "column_split": 0.4,               # x/page-width fraction dividing columns
  "cell": {"dx0": -2, "dy0": -27, "dy1": 166},   # cell rect around the anchor
  "side": {"font": "Wingdings3",     # side-to-move glyphs and their meaning
           "glyphs": {"\uf071": "b", "\uf072": "w"}},
  "board": {"font": "Merida",        # substring of the diagram font name
            "row_bucket": 6}         # y quantum grouping glyphs into ranks
}
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import fitz


@dataclass(frozen=True)
class Layout:
    zoom: float
    anchor_size: tuple[float, float]
    anchor_items: int
    column_split: float
    cell_dx0: float
    cell_dy0: float
    cell_dy1: float
    side_font: str
    side_glyphs: dict[str, str]
    board_font: str
    board_row_bucket: float
    # free-form section for the book's solutions parser (page range,
    # figurine map, furniture regexes, ...)
    solutions: dict = field(default_factory=dict)

    @classmethod
    def load(cls, book: Path) -> Layout:
        d = json.loads((Path(book) / "layout.json").read_text())
        return cls(
            zoom=d.get("zoom", 2.0),
            anchor_size=tuple(d["anchor_size"]),
            anchor_items=d.get("anchor_items", 4),
            column_split=d["column_split"],
            cell_dx0=d["cell"]["dx0"],
            cell_dy0=d["cell"]["dy0"],
            cell_dy1=d["cell"]["dy1"],
            side_font=d["side"]["font"],
            side_glyphs=d["side"]["glyphs"],
            board_font=d["board"]["font"],
            board_row_bucket=d["board"]["row_bucket"],
            solutions=d.get("solutions", {}),
        )


@dataclass(frozen=True)
class Cell:
    num: int
    rect: fitz.Rect
    side: str | None          # 'w' / 'b' / None if the glyph is missing


def anchors(page: fitz.Page, layout: Layout) -> list[fitz.Rect]:
    lo, hi = layout.anchor_size
    out = []
    for d in page.get_drawings():
        r = d["rect"]
        if lo < r.width < hi and lo < r.height < hi and len(d["items"]) == layout.anchor_items:
            out.append(r)
    return sorted(out, key=lambda r: (round(r.y0), r.x0))


def side_glyphs(page: fitz.Page, layout: Layout) -> list[tuple[fitz.Rect, str]]:
    out = []
    for b in page.get_text("rawdict")["blocks"]:
        if b["type"] != 0:
            continue
        for line in b.get("lines", []):
            for s in line["spans"]:
                if s["font"] != layout.side_font:
                    continue
                for c in s["chars"]:
                    if c["c"] in layout.side_glyphs:
                        out.append((fitz.Rect(c["bbox"]), layout.side_glyphs[c["c"]]))
    # some pages render the text layer twice; dedup identical glyphs
    seen, uniq = set(), []
    for r, s in out:
        k = (round(r.x0), round(r.y0), s)
        if k not in seen:
            seen.add(k)
            uniq.append((r, s))
    return uniq


def page_cells(page: fitz.Page, layout: Layout) -> list[Cell]:
    """Puzzle cells on a page, column-major, numbering sequence-corrected."""
    cs = anchors(page, layout)
    if not cs:
        return []
    W = page.rect.width
    split = W * layout.column_split
    left_xs = sorted({round(r.x0) for r in cs})
    c0x = min(left_xs)
    # reading order is column-major: left column top-to-bottom, then right
    cs = sorted(cs, key=lambda r: (r.x0 > split, round(r.y0)))
    printed = []
    for r in cs:
        t = page.get_text("text", clip=r).strip()
        printed.append(int(t) if t.isdigit() else None)
    # a few books misprint anchor numbers; trust the page sequence, anchored
    # by the majority of printed numbers
    bases = Counter(p - i for i, p in enumerate(printed) if p is not None)
    base = bases.most_common(1)[0][0]
    tris = side_glyphs(page, layout)
    out = []
    for i, r in enumerate(cs):
        num = base + i
        if printed[i] != num:
            print(f"p{page.number}: printed #{printed[i]} -> corrected #{num}",
                  file=sys.stderr)
        col = 0 if r.x0 < split else 1
        # cell bounds: from anchor to just before next column's anchor / margin
        x0 = r.x0 + layout.cell_dx0
        x1 = (min(x for x in left_xs if x > split) + layout.cell_dx0) \
            if col == 0 and any(x > split for x in left_xs) else (W - c0x)
        rect = fitz.Rect(x0, r.y0 + layout.cell_dy0, x1, r.y0 + layout.cell_dy1)
        hits = [s for t, s in tris if rect.x0 <= t.x0 <= rect.x1 and rect.y0 <= t.y0 <= rect.y1]
        out.append(Cell(num, rect, hits[0] if len(hits) == 1 else None))
    return out
