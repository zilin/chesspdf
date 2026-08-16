#!/bin/sh
# Regenerate the final PGNs for the CC0 sample book in one shot.
# Run from the repo root:  sh books/sample/rebuild.sh
# Assumes ingest.py, recognize.py and solutions.py have run; merges any
# fixes and human verdicts, re-verifies by solution replay, emits the PGNs,
# then grades the result against truth.json.
set -e
BOOK=books/sample
PY=./.venv/bin/python
$PY -m chesspdf.cli --book $BOOK import-legacy --src $BOOK --bundle $BOOK/bundle --title "Sample Tactics"
$PY -m chesspdf.cli --book $BOOK verify   --bundle $BOOK/bundle
$PY -m chesspdf.cli --book $BOOK assemble --bundle $BOOK/bundle
cp $BOOK/bundle/exports/puzzles_problems.pgn  $BOOK/sample_problems.pgn
cp $BOOK/bundle/exports/puzzles_solutions.pgn $BOOK/sample_solutions.pgn
cp $BOOK/bundle/exports/puzzles_verified.pgn  $BOOK/sample_final.pgn
cp $BOOK/bundle/exports/puzzles_review.pgn    $BOOK/sample_text_solutions.pgn
# generated books ship an answer key; real ones do not
[ -f $BOOK/truth.json ] && $PY -m chesspdf.score --book $BOOK --stage bundle
echo "Done."
