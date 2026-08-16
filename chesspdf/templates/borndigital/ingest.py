#!/usr/bin/env python3
"""Woodpecker ingest: cell images + side-to-move, via the vector-anchored grid
in chesspdf.pagegrid. All book-tuned numbers live in layout.json."""
import json
import sys
from pathlib import Path

import fitz
from PIL import Image

BOOK = Path(__file__).resolve().parent
from chesspdf.pagegrid import Layout, page_cells

if __name__ == '__main__':
    layout = Layout.load(BOOK)
    doc = fitz.open(BOOK / 'source.pdf')
    (BOOK / 'problem_images').mkdir(exist_ok=True)
    meta = {}
    pages = [int(a) for a in sys.argv[1:]] or range(len(doc))
    for pno in pages:
        page = doc[pno]
        cells = page_cells(page, layout)
        if not cells:
            continue
        pix = page.get_pixmap(matrix=fitz.Matrix(layout.zoom, layout.zoom))
        img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
        for c in cells:
            crop = img.crop(tuple(int(v * layout.zoom)
                                  for v in (c.rect.x0, c.rect.y0, c.rect.x1, c.rect.y1)))
            crop.save(BOOK / f'problem_images/{c.num}.png')
            meta[str(c.num)] = c.side
            print(f'p{pno} #{c.num}: side={c.side}')
    mfile = BOOK / 'sides.json'
    old = json.loads(mfile.read_text()) if mfile.exists() else {}
    old.update(meta)
    mfile.write_text(json.dumps(old, indent=0))
