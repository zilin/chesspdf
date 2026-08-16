#!/bin/sh
# Regenerate the final PGNs for Mastering Chess Strategy in one shot.
# Run from the repo root:  sh books/mastering/rebuild.sh
# Assumes recognition + solutions OCR are complete (problem_jsons/ and
# solution_jsons/ populated); merges automated fixes + human verdicts,
# re-verifies every puzzle by solution replay, and emits the PGNs here.
set -e
BOOK=books/mastering
PY=./.venv/bin/python
$PY -m chesspdf.cli --book $BOOK import-legacy --src $BOOK --bundle $BOOK/bundle --title "Mastering Chess Strategy"
$PY -m chesspdf.cli --book $BOOK verify   --bundle $BOOK/bundle
$PY -m chesspdf.cli --book $BOOK assemble --bundle $BOOK/bundle
cp $BOOK/bundle/exports/puzzles_problems.pgn  $BOOK/mastering_problems.pgn
cp $BOOK/bundle/exports/puzzles_solutions.pgn $BOOK/mastering_solutions.pgn
cp $BOOK/bundle/exports/puzzles_verified.pgn  $BOOK/mastering_final.pgn
cp $BOOK/bundle/exports/puzzles_review.pgn    $BOOK/mastering_text_solutions.pgn
echo "Done."
