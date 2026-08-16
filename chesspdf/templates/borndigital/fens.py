#!/usr/bin/env python3
"""Woodpecker FEN extraction: the boards are Chess-Merida-Regular TEXT, decoded
deterministically by chesspdf.textboard (no vision model). Grid + all tuned
numbers come from layout.json via chesspdf.pagegrid."""
import json
import sys
from pathlib import Path

import fitz

BOOK = Path(__file__).resolve().parent
from chesspdf.chesslib import full_fen, structural_check
from chesspdf.pagegrid import Layout, page_cells
from chesspdf.textboard import decode_board

if __name__ == '__main__':
    layout = Layout.load(BOOK)
    doc = fitz.open(BOOK / 'source.pdf')
    sides = json.loads((BOOK / 'sides.json').read_text())
    pages = [int(a) for a in sys.argv[1:]] or range(len(doc))
    fens, errors = {}, []
    for pno in pages:
        page = doc[pno]
        for cell in page_cells(page, layout):
            num = str(cell.num)
            try:
                placement, note = decode_board(page, cell.rect,
                                               layout.board_font, layout.board_row_bucket)
                if note:
                    print(f'p{pno} #{num}: {note}', file=sys.stderr)
                if (err := structural_check(placement)) is not None:
                    errors.append((pno, num, f'sanity: {err}'))
                fens[num] = full_fen(placement, sides.get(num))
            except ValueError as e:
                errors.append((pno, num, str(e)))
    (BOOK / 'fens.json').write_text(json.dumps(fens, indent=0))
    print(f'{len(fens)} FENs written, {len(errors)} errors')
    for e in errors[:40]:
        print('ERR', *e)
