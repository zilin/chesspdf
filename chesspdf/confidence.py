#!/usr/bin/env python3
"""Confidence tiers for verified FENs. Calibrated on sim_rerun.jsonl truth labels."""
import json, re
from .chesslib import mainline_tokens

def tier(moves: str | None, note: str = "", conf: str = "", cv_mm: int | None = None) -> str:
    """cv_mm: occupancy-mask mismatch count for the final FEN (None = no mask).
    Replay only constrains participating pieces; the mask constrains bystanders."""
    if conf == "low" or "arbitration" in (note or ""):
        return "LOW"
    if not moves:
        return "MEDIUM"
    sans = mainline_tokens(moves)
    strong = sum(1 for s in sans if any(c in s for c in "x+#"))
    anchor = len(sans) >= 6 or (len(sans) >= 4 and strong >= 2) or "#" in moves
    if anchor and (cv_mm is None or cv_mm == 0):
        return "HIGH"   # None = mask not computed; anchor alone decides
    return "MEDIUM"

if __name__ == "__main__":
    from .audit import load_solutions
    sols = load_solutions()
    truth = {json.loads(l)['id']: json.loads(l)['fen'] for l in open('books/imagination/bundle/state/puzzles.jsonl') if l.strip()}
    stats = {}
    for l in open('experiments/sim_rerun.jsonl'):
        r = json.loads(l)
        if r['status'] != 'FIXED':
            continue
        from .occupancy import occupancy_mask, OCCUPIED
        from .fix_fens import image_for
        mask = occupancy_mask(image_for(r['id']))
        cv_mm = None
        if mask:
            bd = {}
            for i, row in enumerate(r['fen'].split('/')):
                f = 0
                for ch in row:
                    if ch.isdigit(): f += int(ch)
                    else: bd[chr(97+f)+str(8-i)] = ch; f += 1
            cv_mm = sum(1 for s, st in mask.items() if st != 'uncertain'
                        and (s in bd) != (st == OCCUPIED))
        t = tier(sols.get(r['id']), r.get('note', ''), r.get('confidence', ''), cv_mm)
        ok = r['fen'].split()[0] == truth[r['id']]
        a, b = stats.get(t, (0, 0)); stats[t] = (a + ok, b + 1)
    for t, (ok, n) in sorted(stats.items()):
        print(f"{t}: {ok}/{n} correct = {ok/n:.1%}")
