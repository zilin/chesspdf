#!/usr/bin/env python3
"""Mastering Chess Strategy ingest (scanned book, CV-anchored).

Exercise pages 301-366 (printed page == 0-based index), 2x3-ish grid with
chapter headings interleaved. Boards are solid-bordered squares: the border
is the anchor. Each cell crop = header line ("Position N (White/Black to
play)") + board + hint sentence below — the recognition model reads all
three, so ingest does not need to know puzzle numbers.

Crops go to staging/p{page}_{k}.png (k = detection order: left column
top-to-bottom, then right column); recognize.py assigns real ids.
"""
import json
import sys
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

BOOK = Path(__file__).resolve().parent
CFG = json.loads((BOOK / 'layout.json').read_text())
DETECT_Z = CFG['detect_zoom']
CROP_Z = CFG['crop_zoom']
INK = CFG['ink_threshold']


def row_max_runs(ink):
    """longest horizontal dark run per row."""
    out = np.zeros(ink.shape[0], dtype=int)
    for y in range(ink.shape[0]):
        row = ink[y]
        if not row.any():
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        out[y] = (np.where(d == -1)[0] - np.where(d == 1)[0]).max()
    return out


def border_groups(ink_half, min_run):
    """y centers of horizontal border lines in one page half."""
    rows = np.where(row_max_runs(ink_half) > min_run)[0]
    groups = []
    for y in rows:
        if groups and y - groups[-1][-1] <= 5:
            groups[-1].append(y)
        else:
            groups.append([y])
    return [int(np.mean(g)) for g in groups]


def board_x_extent(ink, t, b, x_off):
    """column extent of the board between border rows t..b."""
    band = ink[t:b, :].T                      # columns of the band
    runs = row_max_runs(band)                 # longest vertical run per column
    cols = np.where(runs > 0.8 * (b - t))[0]
    return x_off + int(cols.min()), x_off + int(cols.max())


def page_boards(page):
    """[(x0, y0, x1, y1)] board rects in page points, left col then right."""
    pix = page.get_pixmap(matrix=fitz.Matrix(DETECT_Z, DETECT_Z))
    img = np.asarray(Image.frombytes('RGB', (pix.width, pix.height), pix.samples).convert('L'))
    ink = img < INK
    H, W = ink.shape
    out = []
    for x_lo, x_hi in ((0, W // 2), (W // 2, W)):
        ys = border_groups(ink[:, x_lo:x_hi], CFG['min_border_run'] * DETECT_Z)
        if len(ys) % 2:
            raise ValueError(f'p{page.number}: odd border count {ys}')
        for t, b in zip(ys[::2], ys[1::2]):
            x0, x1 = board_x_extent(ink[:, x_lo:x_hi], t, b, x_lo)
            out.append(tuple(v / DETECT_Z for v in (x0, t, x1, b)))
    return out


def cell_rect(board, page_rect):
    x0, y0, x1, y1 = board
    m = CFG['cell']
    return fitz.Rect(max(0, x0 + m['dx0']), max(0, y0 + m['dy0']),
                     min(page_rect.x1, x1 + m['dx1']), min(page_rect.y1, y1 + m['dy1']))


if __name__ == '__main__':
    doc = fitz.open(BOOK / 'source.pdf')
    staging = BOOK / 'staging'
    staging.mkdir(exist_ok=True)
    pages = [int(a) for a in sys.argv[1:]] or range(*CFG['exercise_pages'])
    total = 0
    for pno in pages:
        page = doc[pno]
        boards = page_boards(page)
        pix = page.get_pixmap(matrix=fitz.Matrix(CROP_Z, CROP_Z))
        img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
        for k, board in enumerate(boards):
            r = cell_rect(board, page.rect)
            crop = img.crop(tuple(int(v * CROP_Z) for v in (r.x0, r.y0, r.x1, r.y1)))
            crop.save(staging / f'p{pno}_{k}.png')
        total += len(boards)
        print(f'p{pno}: {len(boards)} boards')
    print(f'total {total} cells -> {staging}')
