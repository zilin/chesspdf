# Development notes — pipeline internals

(The user-facing README lives at the repo root; this file keeps the full
pipeline documentation and measured results. Some step-by-step commands
below predate the `chesspdf` console script and per-book folders.)

Converts chess puzzle books (tested with *Imagination in Chess*, 755 puzzles)
into ChessBase-ready PGN with **machine-verified positions and solutions**.

The core idea: vision-model recognition is never trusted on its own. Every
recognized position is **cross-validated by replaying the book's own solution
moves** with python-chess; failures are diagnosed, repaired
deterministically where possible, and the small residue goes to a human review
web app. Every puzzle carries a ledger of how its data was obtained.

## Prerequisites

- Python 3.10+ with `python-chess`, `pillow`, `numpy`, `google-genai`
- [uv](https://docs.astral.sh/uv/) for the legacy split/recognize steps
- `GEMINI_API_KEY` in `.env`  (no engine or other binary dependencies)

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install python-chess pillow numpy google-genai pymupdf pydantic python-dotenv
```

## One CLI for everything

```sh
python -m chesspdf.cli --book books/<name> audit            # classify all puzzles (replay verification)
python -m chesspdf.cli fix-fens         # repair failing FENs (vision + CV + verify)
python -m chesspdf.cli fix-moves        # repair broken solutions
python -m chesspdf.cli fix-shifts       # zero-cost whole-board shift repair
python -m chesspdf.cli review           # human review web app (standalone: review_app.py)
python -m chesspdf.cli import-legacy --src . --bundle bundles/book
python -m chesspdf.cli verify --bundle bundles/book
python -m chesspdf.cli assemble --bundle bundles/book   # 4 PGN exports
```

Layout: `chesspdf/` is the product package (bundle state machine + stages +
CLI). All engine modules now live inside `chesspdf/`. `review_app.py` is standalone. `experiments/` holds benchmark
and simulation scripts. Legacy `cli.py` keeps the original split/recognize
steps until they are ported.

## Pipeline overview

```
PDF ─(cli.py split-problems / split-solutions)→ problem_images/, solution_images/
    ─(cli.py recognize-problems / recognize-solutions)→ problem_jsons/, solution_jsons/
    ─(audit.py)→ audit_report.json        # classify every puzzle by consistency
    ─(fix_fens.py)→ fen_fixes.jsonl       # re-recognize + verify failing FENs
    ─(fix_moves.py)→ moves_fixes.jsonl    # gap-fill + targeted re-OCR of solutions
    ─(fix_shifts.py)→ (appends)           # detect whole-board one-square shifts
    ─(review_app.py)→ human_overrides.jsonl  # human verdicts (highest priority)
    ─(build_pgn.py / chesspdf assemble)→ verified + review PGN files
```

### Step-by-step

1. **Split & first recognition** (legacy CLI, unchanged):
   ```sh
   uv run cli.py split-problems --pdf book.pdf --pages 10-15
   uv run cli.py recognize-problems --model pro
   uv run cli.py split-solutions --pdf book.pdf --pages 80-85
   uv run cli.py recognize-solutions --model pro
   ```

2. **Audit** — zero-cost consistency classification (FEN structure, side to
   move vs. solution, full solution replay, optional engine sanity):
   ```sh
   python audit.py [--engine]
   ```
   Statuses: `OK`, `VAR_ONLY` (mainline replays; book-style variations),
   `ILLEGAL`, `STRUCT`, `PARSE`, `TURN`, `NO_SOL`.

3. **Repair passes** (each idempotent & resumable, results append to jsonl):
   ```sh
   python fix_fens.py    # ILLEGAL/STRUCT: square-listing re-recognition,
                         # replay-verified, escalating fast → strong model
   python fix_moves.py   # MOVES_SUSPECT: gap-fill search (unique legal move
                         # completing the line, Stockfish arbitration),
                         # then targeted re-OCR of the printed solution
   python fix_shifts.py [ids]  # whole-board shift detection (zero API cost)
   ```
   `fix_fens` also runs a classical-CV occupancy cross-check
   (`chesspdf/occupancy.py`: frame detection + white-dilation ink statistics,
   validated at 99.7% cell accuracy on the 756 ground-truth boards) — it
   proposes verify-gated global-shift and per-piece realignments and feeds
   disagreement squares into the targeted recheck, all at zero API cost.
   Variation replay is recorded as a confidence signal (never a veto:
   variation text is itself OCR).

4. **Human review** — side-by-side web app at http://127.0.0.1:8899/:
   ```sh
   python review_app.py
   ```
   Original diagram vs. editable rendered board; provenance panel (how each
   FEN/solution was obtained and verified); model-disagreement squares
   highlighted orange; candidate-FEN one-click loading; click-to-move pieces,
   palette editing; verdicts append to `human_overrides.jsonl` and outrank
   every automated source.

5. **Assemble final PGN** (either the legacy script or the package):
   ```sh
   python build_pgn.py
   # or, via the bundle-based package:
   python -m chesspdf.cli import-legacy --src . --bundle bundles/book
   python -m chesspdf.cli verify   --bundle bundles/book
   python -m chesspdf.cli assemble --bundle bundles/book
   ```
   Outputs `*_verified.pgn` (every game replays legally; variations kept where
   possible, else mainline + verbatim book text as a comment) and
   `*_review.pgn` (position finalized, solution kept as text). Both files are
   fully re-parsed before writing.

## The `chesspdf` package

`chesspdf/` is the productized form: a **Bundle** directory is the single
source of truth (images + `state/puzzles.jsonl` state machine + event log +
exports), and each stage is an idempotent function over it — the foundation
for a future web app / multi-book CLI. `chesspdf/chesslib.py` holds all pure
chess logic (replay, structural checks, book-variation transform).

## Hard-won lessons encoded here

- `*` in image filenames is the book's difficulty marker — a literal
  character. Never glob puzzle ids (`49*.png` would match `499**.png`).
- The book prints variations inline after `;`/`.` with the mainline move
  first; standard PGN wants parenthesized variations after the actual reply.
  Re-OCR prompts normalize at transcription time.
- Solution ids in the back section carry game attributions
  (`334 Ebralidze-Blagidze, Tbilisi 1949`) — pair by numeric prefix, keep the
  attribution for PGN headers.
- Whole-board one-square shifts are a systematic crop-offset failure mode —
  cheap to detect by replay after shifting (`fix_shifts.py`).
- Two vision models agreeing does **not** make a reading safe: their errors
  correlate on the same blurry glyphs (measured: 3/19 human-corrected cases
  had model agreement). Solution replay is the only strong verifier; humans
  arbitrate the rest.

## Prompt-engineering experiment (measured on the 756-FEN eval set)

Single-shot gemini-3.7-flash, A/B on 171 failures + 50 controls:

| prompt | rescue | regression | overall | verdict |
|---|---|---|---|---|
| v1 baseline listing | — | — | 77.4% | — |
| v2 + self-check & self-correct | 25% | 17% | ~70% | rejected: model second-guesses correct boards |
| v3 + square-color report, code-side parity fix | 28% | 38% | ~55% | rejected: parity cannot distinguish a one-square shift from inverted light/dark perception |
| v4 + one sentence: "white pieces are hollow outlines; black are solid" | 38% | 8% | ~79.4% | **adopted** |

Lesson: on degraded scans, prompt-level gains are small and only style-note
shaped; parity/self-verification mechanisms backfire. Accuracy beyond ~80%
comes from verification-anchored repair (solution replay), not prompting.
Stockfish arbitration resolved exactly 1 of 756 puzzles and is being retired
as a dependency; ambiguous gap-fills route to re-OCR / human instead.
