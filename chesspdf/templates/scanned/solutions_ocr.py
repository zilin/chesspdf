#!/usr/bin/env python3
"""OCR the solutions section (two-column annotated games) into the
imagination-compatible solution_jsons/ format, via one flash call per page.

Each page -> solution_pages/{pno}.json:
  {"solutions": [{"id": "N Players, Event Year", "moves": "..."}],
   "continuation": "movetext belonging to the previous page's last solution"}
Resumable (skips existing page files). The stitch step then appends each
page's continuation to the previous solution and writes
solution_jsons/page_{pno}.json for audit/load_solutions.
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz

BOOK = Path(__file__).resolve().parent
ROOT = BOOK.parents[1]

for line in (ROOT / '.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('\'"'))

from chesspdf.fix_fens import MODEL_FAST, ask

CFG = json.loads((BOOK / 'layout.json').read_text())
PAGES_DIR = BOOK / 'solution_pages'
OUT_DIR = BOOK / 'solution_jsons'

PROMPT = """This book page contains chess exercise solutions in two columns
(read the LEFT column fully, then the RIGHT column). Each solution starts with
an italic line "Position N", then the players "A-B" in bold and the event
below, then annotated moves: the MAINLINE is in bold, alternatives and prose
commentary in regular type.

Transcribe every solution that STARTS on this page. Reply with JSON only:
{"solutions": [{"id": "<N> <players>, <event>", "moves": "<movetext>"}],
 "continuation": "<movetext of any text at the top of the left column that
 continues a solution begun on a previous page, else empty string>"}

movetext rules — STANDARD PGN:
- mainline moves at the top level with move numbers; if the solution starts
  with a Black move, begin "1...".
- alternative lines go in (parentheses) placed immediately AFTER the mainline
  move they are an alternative to.
- prose commentary goes in {curly braces}. Never use { or } otherwise.
- figurine piece symbols map to K Q R B N; keep +, #, !, ?, =Q promotions,
  and any result like 1-0.
"""


def ocr_page(pno: int) -> None:
    out = PAGES_DIR / f'{pno}.json'
    if out.exists():
        return
    doc = fitz.open(BOOK / 'source.pdf')
    pix = doc[pno].get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
    text = ask(pix.tobytes('png'), PROMPT, MODEL_FAST)
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        print(f'p{pno}: no JSON in reply', file=sys.stderr)
        return
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        print(f'p{pno}: bad JSON: {e}', file=sys.stderr)
        return
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    print(f'p{pno}: {len(data.get("solutions", []))} solutions'
          + (' +cont' if data.get('continuation') else ''))


def stitch() -> None:
    lo, hi = CFG['solution_pages']
    pages = []
    for pno in range(lo, hi):
        f = PAGES_DIR / f'{pno}.json'
        if not f.exists():
            print(f'stitch: p{pno} missing, aborting', file=sys.stderr)
            return
        pages.append((pno, json.loads(f.read_text())))
    last = None                     # last solution dict seen, across pages
    for pno, data in pages:
        for sol in data.get('solutions', []):
            # audit.solution_key wants the numeric prefix first
            sol['id'] = re.sub(r'^\s*Position\s+', '', sol['id'])
        cont = (data.get('continuation') or '').strip()
        if cont and last is not None:
            last['moves'] = (last['moves'].rstrip() + ' ' + cont).strip()
        if data.get('solutions'):
            last = data['solutions'][-1]
    OUT_DIR.mkdir(exist_ok=True)
    n = 0
    for pno, data in pages:
        (OUT_DIR / f'page_{pno}.json').write_text(
            json.dumps({'solutions': data.get('solutions', [])},
                       indent=1, ensure_ascii=False))
        n += len(data.get('solutions', []))
    print(f'stitched {n} solutions -> {OUT_DIR}')


if __name__ == '__main__':
    PAGES_DIR.mkdir(exist_ok=True)
    lo, hi = CFG['solution_pages']
    pages = [int(a) for a in sys.argv[1:]] or list(range(lo, hi))
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(ocr_page, pages))
    if not sys.argv[1:]:
        stitch()
