#!/usr/bin/env python3
"""Sample-book solutions: read them straight from the PDF text layer.

This book is born-digital with vector diagrams — the boards need visual
recognition, but every word does not. Extracting the solutions section as
text costs nothing and cannot mis-OCR, so only the diagrams go to a model.
The printed shape is one entry per line, `N. <SAN movetext>`, followed by
its `lichess <id>` attribution on the next line.

Writes solution_jsons/page_{pno}.json in the format audit/load_solutions want.
"""
import json
import re
import sys
from pathlib import Path

import fitz

BOOK = Path(__file__).resolve().parent
CFG = json.loads((BOOK / 'layout.json').read_text())
ENTRY_RE = re.compile(r'^(\d+)\.\s+(.+)$')
SOURCE_RE = re.compile(r'^lichess\s+(\S+)$')

if __name__ == '__main__':
    doc = fitz.open(BOOK / 'source.pdf')
    out_dir = BOOK / 'solution_jsons'
    out_dir.mkdir(exist_ok=True)
    lo, hi = CFG['solution_pages']
    total = 0
    for pno in range(lo, hi):
        solutions, pending = [], None
        for line in doc[pno].get_text().splitlines():
            line = line.strip()
            if m := ENTRY_RE.match(line):
                pending = {'id': m.group(1), 'moves': m.group(2).strip()}
                solutions.append(pending)
            elif (m := SOURCE_RE.match(line)) and pending is not None:
                pending['id'] = f"{pending['id']} lichess {m.group(1)}"
                pending = None
        (out_dir / f'page_{pno}.json').write_text(
            json.dumps({'solutions': solutions}, indent=1))
        total += len(solutions)
        print(f'p{pno}: {len(solutions)} solutions')
    lo_id, hi_id = CFG['expected_ids']
    ids = set()
    for f in out_dir.glob('page_*.json'):
        for s in json.loads(f.read_text())['solutions']:
            ids.add(int(s['id'].split()[0]))
    missing = [i for i in range(lo_id, hi_id + 1) if i not in ids]
    print(f'{total} solutions -> {out_dir}' + (f'  MISSING {missing}' if missing else ''))
