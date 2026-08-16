"""Classical-CV square-occupancy mask for book chess diagrams.

No model involved: locate the board frame from ink profiles, split into 8x8
cells, dilate the whites (MaxFilter) to erase the thin hatching of dark
squares, and measure the remaining ink in each cell's center. Piece glyphs
(thick strokes) survive the dilation; empty squares (white or hatched) do not.

Returns per-square: OCCUPIED / EMPTY / UNCERTAIN (metric inside the margin
band). Calibrated and validated against the 756 ground-truth boards; see
`python -m chesspdf.occupancy --validate`.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

OCCUPIED, EMPTY, UNCERTAIN = "occupied", "empty", "uncertain"

# Calibrated on the Imagination in Chess scans (see --validate).
DARK_THRESHOLD = 128          # grayscale binarization
LINE_FRACTION = 0.55          # a frame line row/col is mostly dark
CENTER_CROP = 0.60            # analyze the central 60% of each cell
T_EMPTY = 0.015               # center ink below this -> empty
T_OCCUPIED = 0.035            # center ink above this -> occupied


def _runs(indices: np.ndarray) -> list[int]:
    """Centers of consecutive index runs (each frame edge is one run)."""
    if len(indices) == 0:
        return []
    centers, start, prev = [], indices[0], indices[0]
    for i in indices[1:]:
        if i > prev + 2:
            centers.append((start + prev) // 2)
            start = i
        prev = i
    centers.append((start + prev) // 2)
    return [int(c) for c in centers]


def find_board(dark: np.ndarray) -> tuple[int, int, int, int] | None:
    """Locate the outer frame, exploiting that a chessboard is square: pick
    the row-line pair whose spacing best matches the column span."""
    h, w = dark.shape
    rows = _runs(np.where(dark.mean(axis=1) > LINE_FRACTION)[0])
    cols = _runs(np.where(dark.mean(axis=0) > LINE_FRACTION)[0])
    if len(rows) < 2 or len(cols) < 2:
        return None
    min_span = min(h, w) * 0.4
    best = None
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            rspan = rows[j] - rows[i]
            if rspan < min_span:
                continue
            for k in range(len(cols)):
                for m in range(k + 1, len(cols)):
                    cspan = cols[m] - cols[k]
                    if cspan < min_span:
                        continue
                    err = abs(rspan - cspan)
                    if err > 0.04 * max(rspan, cspan):
                        continue
                    score = (err, -(rspan + cspan))    # squarest, then largest
                    if best is None or score < best[0]:
                        best = (score, rows[i], rows[j], cols[k], cols[m])
    if best is None:
        return None
    _, top, bottom, left, right = best
    inset = max(2, (bottom - top) // 100)
    return top + inset, left + inset, bottom - inset, right - inset


def occupancy_mask(image_path: Path | str) -> dict[str, str] | None:
    """Map square name -> occupied/empty/uncertain, or None if no board found."""
    img = Image.open(image_path).convert("L")
    # white dilation erases thin hatch lines; piece strokes survive
    filtered = img.filter(ImageFilter.MaxFilter(3))
    raw = np.asarray(img) < DARK_THRESHOLD
    box = find_board(raw)
    if box is None:
        return None
    top, left, bottom, right = box
    clean = np.asarray(filtered) < DARK_THRESHOLD
    inks: dict[str, float] = {}
    ch = (bottom - top) / 8.0
    cw = (right - left) / 8.0
    for r in range(8):          # r=0 is rank 8 (top)
        for f in range(8):
            y0 = top + r * ch
            x0 = left + f * cw
            # central crop of the cell
            my = ch * (1 - CENTER_CROP) / 2
            mx = cw * (1 - CENTER_CROP) / 2
            cell = clean[int(y0 + my): int(y0 + ch - my),
                         int(x0 + mx): int(x0 + cw - mx)]
            if cell.size == 0:
                return None
            inks[chr(97 + f) + str(8 - r)] = float(cell.mean())

    def classify(lo: float, hi: float) -> dict[str, str]:
        out = {}
        for sq, ink in inks.items():
            out[sq] = EMPTY if ink <= lo else OCCUPIED if ink >= hi else UNCERTAIN
        return out

    mask = classify(T_EMPTY, T_OCCUPIED)
    occupied = sum(1 for v in mask.values() if v == OCCUPIED)
    if occupied > 34:
        # Coarse hatching survived the dilation (varies between scans): the
        # global threshold over-fires. Rescue: hatch residue and piece glyphs
        # form two clusters above T_EMPTY — split at the widest gap.
        band = sorted(v for v in inks.values() if v > T_EMPTY)
        best_gap, edge = 0.0, None
        for a, b in zip(band, band[1:]):
            if b - a > best_gap:
                best_gap, edge = b - a, (a, b)
        if edge is not None and best_gap >= 0.03:
            mask = classify(edge[0] + best_gap * 0.25, edge[1] - best_gap * 0.25)
            occupied = sum(1 for v in mask.values() if v == OCCUPIED)
    if occupied > 32:
        return None            # implausible: refuse rather than mislead
    return mask


def _truth_occupancy(fen: str) -> dict[str, bool]:
    occ: dict[str, bool] = {}
    for i, row in enumerate(fen.split("/")):
        f = 0
        for ch in row:
            if ch.isdigit():
                for _ in range(int(ch)):
                    occ[chr(97 + f) + str(8 - i)] = False
                    f += 1
            else:
                occ[chr(97 + f) + str(8 - i)] = True
                f += 1
    return occ


def validate(root: Path) -> None:
    import json

    truth = {}
    for line in (root / "books/imagination/bundle/state/puzzles.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            truth[r["id"]] = r["fen"]
    images = {p.stem.replace("*", ""): p
              for p in (root / "problem_images").glob("*.png")}

    boards = cells = wrong = unsure = no_board = 0
    board_perfect = 0
    worst: list[tuple[int, str]] = []
    for pid, fen in truth.items():
        img = images.get(pid)
        if img is None:
            continue
        mask = occupancy_mask(img)
        if mask is None:
            no_board += 1
            continue
        occ = _truth_occupancy(fen)
        bad = 0
        for sq, is_occ in occ.items():
            cells += 1
            if mask[sq] == UNCERTAIN:
                unsure += 1
            elif (mask[sq] == OCCUPIED) != is_occ:
                wrong += 1
                bad += 1
        boards += 1
        board_perfect += bad == 0
        worst.append((bad, pid))
    worst.sort(reverse=True)
    print(f"boards: {boards} ({no_board} frame-detection failures)")
    print(f"cells: {cells}, wrong: {wrong} ({wrong / cells:.3%}), "
          f"uncertain: {unsure} ({unsure / cells:.3%})")
    print(f"boards with zero wrong cells: {board_perfect}/{boards} "
          f"= {board_perfect / boards:.1%}")
    print("worst boards:", worst[:8])


if __name__ == "__main__":
    import sys

    validate(Path(os.environ.get("CHESSPDF_BOOK", "books/imagination")))
    sys.exit(0)
