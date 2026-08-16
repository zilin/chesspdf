#!/usr/bin/env python3
"""Woodpecker solutions extraction (deterministic, from the PDF text layer).

Layout (Quality Chess house style, pages 224-379):
- entry header: bold "N. White – Black" + ", Site Year" (comma/venue regular)
- MAINLINE moves: bold spans (incl. figurines in SPAriesFig-Bold)
- variations & commentary: regular/italic spans (figurines in SPTimeFig-Roman)
- U+F0FC (Wingdings tick) marks end of a line's solution; page furniture is
  page numbers / running titles / chapter pages.

Output solutions.json: num -> {white, black, event, main, text}
`main` is the bold mainline, figurines mapped to English letters; `text` is
the full body prose (both styles) for the record.
"""
import json, re, sys
import fitz
from pathlib import Path

BOOK = Path(__file__).resolve().parent
from chesspdf.pagegrid import Layout

SOL = Layout.load(BOOK).solutions
PAGES = range(*SOL['pages'])
FIG = SOL['figurines']
FURNITURE = tuple(re.compile(p) for p in SOL['furniture'])
EXCLUDE_FONTS = tuple(SOL['exclude_fonts'])
MAX_BODY_SIZE = SOL['max_body_size']

def norm(s):
    for k, v in FIG.items():
        s = s.replace(k, v)
    return s

def extract():
    doc = fitz.open(BOOK / 'source.pdf')
    entries = {}
    cur = None       # current entry dict
    anomalies = []
    expected = 1
    for pno in PAGES:
        for b in doc[pno].get_text('dict')['blocks']:
            if b['type'] != 0:
                continue
            for l in b.get('lines', []):
                spans = [s for s in l['spans']
                         if not any(f in s['font'] for f in EXCLUDE_FONTS)]
                if not spans:
                    continue
                plain = ''.join(s['text'] for s in spans).strip()
                if not plain or any(p.match(plain) for p in FURNITURE):
                    continue
                if spans[0]['size'] > MAX_BODY_SIZE:   # chapter titles
                    continue
                first_bold = 'Bold' in spans[0]['font']
                m = re.match(r'(\d+)\.\s+(.+)', plain) if first_bold else None
                if m and int(m.group(1)) == expected:
                    num = int(m.group(1))
                    head = m.group(2)
                    players, _, event = head.partition(',')
                    white, _, black = players.partition('–')
                    cur = entries[num] = {
                        'white': white.strip(), 'black': black.strip(),
                        'event': event.strip(), 'main': '', 'text': ''}
                    expected = num + 1
                    continue
                if cur is None:
                    continue
                # skip flavour-quote boxes: attribution = all-bold line with no
                # move content; body = all-italic lines
                if all('Italic' in s['font'] for s in spans):
                    continue
                if (all('Bold' in s['font'] for s in spans)
                        and not any(c.isdigit() for c in plain)
                        and re.fullmatch(r"[A-Z][a-zA-Z.'\- ]+", plain)):
                    continue
                bold = ''.join(s['text'] for s in spans if 'Bold' in s['font'])
                cur['main'] += norm(bold)
                cur['text'] += norm(plain) + ' '
    for e in entries.values():
        main = re.sub(r'\s+', ' ', e['main']).strip()
        # chapter-divider player names sometimes glue onto the last line
        main = re.sub(r"\s*[A-Z][a-zA-Z.'\-]+(?:\s+[A-Z][a-zA-Z.'\-]+)+\s*$", '', main)
        e['main'] = main
        e['text'] = re.sub(r'\s+', ' ', e['text']).strip()
    return entries, anomalies

if __name__ == '__main__':
    entries, anomalies = extract()
    nums = sorted(entries)
    print(f'{len(entries)} entries, {nums[0]}..{nums[-1]}')
    missing = sorted(set(range(1, 1129)) - set(nums))
    if missing:
        print('MISSING:', missing[:20])
    empty_main = [n for n in nums if not entries[n]['main']]
    if empty_main:
        print('empty mainline:', empty_main[:20])
    (BOOK / 'solutions.json').write_text(
        json.dumps({str(n): entries[n] for n in nums}, indent=0, ensure_ascii=False))
