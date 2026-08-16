#!/usr/bin/env python3
"""Recognize staged exercise cells: header (id + side), hint sentence, and the
board as a square listing (the measured-best prompt style from imagination).

One flash call per cell, resumable via recognition.jsonl (append-only, keyed
by cell name). When every cell is done and ids cover 1..382 exactly, emits
problem_jsons/{id}.json {id, fen, to_move, hint} + problem_images/{id}.png.
"""
import json
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BOOK = Path(__file__).resolve().parent
ROOT = BOOK.parents[1]

for line in (ROOT / '.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('\'"'))

from chesspdf.fix_fens import LINE_RE, MODEL_FAST, PIECE, ask, to_fen

CFG = json.loads((BOOK / 'layout.json').read_text())
LOG = BOOK / 'recognition.jsonl'

PROMPT = """This image is one chess exercise from a book. It contains, top to bottom:
1. a headline like "Position 12 (White to play)"
2. a chess diagram, White at the bottom. WHITE pieces are drawn as hollow
   outlines; BLACK pieces are solid filled shapes.
3. a short hint sentence below the diagram.

Reply in exactly this format:
HEADER: <position number> <white|black>
then list the piece on every occupied square, rank 8 down to rank 1, one line
per occupied square, exactly like:
e4: white pawn
Use piece names king/queen/rook/bishop/knight/pawn. Finally:
HINT: <the hint sentence, verbatim>"""

HEADER_RE = re.compile(r'HEADER:\D*(\d+)\D*?(white|black)', re.I)
HINT_RE = re.compile(r'HINT:\s*(.+)', re.I)


def recognize(cell: Path) -> dict:
    text = ask(cell.read_bytes(), PROMPT, MODEL_FAST)
    rec = {'cell': cell.stem}
    if m := HEADER_RE.search(text):
        rec['number'] = int(m.group(1))
        rec['to_move'] = 'w' if m.group(2).lower() == 'white' else 'b'
    if m := HINT_RE.search(text):
        rec['hint'] = m.group(1).strip()
    board = {}
    for sq, color, piece in LINE_RE.findall(text):
        sym = PIECE[piece.lower()]
        board[sq.lower()] = sym.upper() if color.lower() == 'white' else sym
    rec['fen'] = to_fen(board)
    return rec


def main() -> None:
    done = {}
    if LOG.exists():
        for line in LOG.read_text().splitlines():
            r = json.loads(line)
            if 'number' in r:      # incomplete records retry on the next run
                done[r['cell']] = r
    cells = sorted((BOOK / 'staging').glob('p*.png'),
                   key=lambda p: (int(p.stem.split('_')[0][1:]), int(p.stem.split('_')[1])))
    todo = [c for c in cells if c.stem not in done]
    if len(sys.argv) > 1:
        todo = todo[:int(sys.argv[1])]
    print(f'{len(cells)} cells, {len(todo)} to recognize')
    with ThreadPoolExecutor(max_workers=8) as ex, LOG.open('a') as f:
        for rec in ex.map(recognize, todo):
            f.write(json.dumps(rec) + '\n')
            f.flush()
            print(rec['cell'], rec.get('number'), rec.get('to_move'))
            done[rec['cell']] = rec

    lo, hi = CFG['expected_ids']
    by_id: dict[int, list] = {}
    for r in done.values():
        if 'number' in r:
            by_id.setdefault(r['number'], []).append(r)
    missing = [i for i in range(lo, hi + 1) if i not in by_id]
    dups = {i: [r['cell'] for r in v] for i, v in by_id.items() if len(v) > 1}
    unnumbered = [r['cell'] for r in done.values() if 'number' not in r]
    if missing or dups or unnumbered:
        print(f'NOT EMITTING: missing={missing} dups={dups} unnumbered={unnumbered}')
        return

    (BOOK / 'problem_jsons').mkdir(exist_ok=True)
    (BOOK / 'problem_images').mkdir(exist_ok=True)
    for i, (rec,) in sorted(by_id.items()):
        (BOOK / 'problem_jsons' / f'{i}.json').write_text(json.dumps({
            'id': str(i), 'fen': rec['fen'], 'to_move': rec['to_move'],
            'hint': rec.get('hint'),
        }, indent=1))
        shutil.copy2(BOOK / 'staging' / f"{rec['cell']}.png",
                     BOOK / 'problem_images' / f'{i}.png')
    print(f'emitted {len(by_id)} problems')


if __name__ == '__main__':
    main()
