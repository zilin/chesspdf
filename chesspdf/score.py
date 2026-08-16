#!/usr/bin/env python3
"""Grade a pipeline run against a book's truth.json.

  chesspdf score --book books/sample [--stage bundle|problems] [--list]

Real books can only be judged by replay (does the printed solution work from
the extracted position?) plus human review. A generated book (see
`chesspdf sample`) ships an answer key, so extraction can be graded outright
— position exact, side to move exact, solution moves exact — with no human
in the loop. Exits non-zero if any position is wrong, so it can gate CI.
"""
import argparse
import json
import sys
from pathlib import Path

from .chesslib import mainline_tokens, normalize_movetext


def load_run(BOOK: Path, stage: str) -> dict[str, dict]:
    """What the pipeline produced, keyed by puzzle id."""
    out: dict[str, dict] = {}
    state = BOOK / 'bundle' / 'state' / 'puzzles.jsonl'
    if stage == 'bundle' and state.exists():
        for line in state.read_text().splitlines():
            r = json.loads(line)
            out[r['id']] = {'fen': (r.get('fen') or '').split()[0],
                            'to_move': r.get('to_move'), 'moves': r.get('moves')}
        return out
    for f in sorted((BOOK / 'problem_jsons').glob('*.json')):
        r = json.loads(f.read_text())
        out[r['id']] = {'fen': r['fen'].split()[0], 'to_move': r.get('to_move'),
                        'moves': None}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--book', default=None, help='book folder (default: $CHESSPDF_BOOK)')
    ap.add_argument('--stage', default='bundle', choices=('bundle', 'problems'))
    ap.add_argument('--list', action='store_true', help='print every mismatch')
    args = ap.parse_args()

    import os
    BOOK = Path(args.book or os.environ.get('CHESSPDF_BOOK', '.')).resolve()
    truth_file = BOOK / 'truth.json'
    if not truth_file.exists():
        sys.exit(f'no answer key at {truth_file} — only generated books have one')
    truth = json.loads(truth_file.read_text())
    run = load_run(BOOK, args.stage)
    tally = {'fen_ok': 0, 'turn_ok': 0, 'moves_ok': 0, 'missing': 0}
    bad_fen, bad_moves = [], []

    for pid, want in truth.items():
        got = run.get(pid)
        if not got:
            tally['missing'] += 1
            continue
        want_fen, want_turn = want['fen'].split()[0], want['fen'].split()[1]
        if got['fen'] == want_fen:
            tally['fen_ok'] += 1
        else:
            bad_fen.append(pid)
        tally['turn_ok'] += got['to_move'] == want_turn
        if got['moves']:
            want_sans = want['moves'].split()
            got_sans = mainline_tokens(normalize_movetext(got['moves']))
            if got_sans[:len(want_sans)] == want_sans:
                tally['moves_ok'] += 1
            else:
                bad_moves.append(pid)

    n = len(truth)
    print(f"scored {n} puzzles from stage={args.stage}")
    for k in ('fen_ok', 'turn_ok', 'moves_ok'):
        print(f"  {k:8s} {tally[k]:3d}/{n}  {tally[k] / n:.1%}")
    if tally['missing']:
        print(f"  missing  {tally['missing']}")
    if args.list:
        print('  wrong FEN:', ', '.join(bad_fen) or 'none')
        print('  wrong moves:', ', '.join(bad_moves) or 'none')
    sys.exit(0 if tally['fen_ok'] == n else 1)


if __name__ == '__main__':
    main()
