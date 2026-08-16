---
name: onboard-book
description: Convert a chess puzzle book PDF into replay-verified PGN files (problems + solutions). Use when the user provides a chess book PDF and wants its exercises extracted. Requires GEMINI_API_KEY for scanned books.
---

# Onboard a chess book

You are onboarding a chess puzzle book: PDF in, four PGN files out. Everything
below was measured on three finished books (756 + 1128 + 382 puzzles), not
assumed. Work autonomously; escalate honestly (see "When you are stuck").

## The one non-negotiable rule

**Never trust recognition — trust replay.** A puzzle is correct only when the
book's own printed solution replays legally from the extracted FEN
(python-chess). Every pipeline decision is scored by this oracle:
- detected cell count must equal the book's own count (TOC / numbering)
- solution ids must cover 1..N exactly, no gaps, no duplicates
- final acceptance = replay rate; residue goes to the human review queue

Corollaries: two models agreeing on a FEN does not make it right (errors
correlate). Do NOT add "double-check your answer" style prompts to vision
reads — measured effect was negative (−7 to −22 points). A style note like
"WHITE pieces are hollow outlines; BLACK are solid" helps (+2).

## Prerequisites

1. `chesspdf --help` works (else: `uv tool install chesspdf` or run
   `python -m chesspdf.cli` from the repo).
2. Scanned books need `GEMINI_API_KEY` in the environment or repo-root `.env`.
   If missing, stop and ask the user.

## Step 0 — Probe and read the book

```
chesspdf probe probe <book.pdf>                       # JSON verdict
chesspdf probe render <book.pdf> <out_dir> <pages…>   # then READ the images
```

Render and *look at* (a) the table of contents — find the exercises and
solutions page ranges and the total puzzle count; (b) 2–3 exercise pages —
note the grid (columns, headers, hints under boards, side-to-move markers);
(c) 1–2 solution pages — note per-entry structure (id, players/event,
mainline vs commentary styling). Printed page numbers usually differ from
PDF indices — calibrate by rendering.

Create `books/<name>/`, copy the PDF to `source.pdf`, and record every
layout number you choose in `books/<name>/layout.json` — never inline
magic numbers in scripts.

## Decision: which template

Three shapes, decided by the probe output. Take the cheapest one that fits:
text costs nothing and cannot be misread, so let a model see only what is
genuinely pictorial.

**A. Born-digital with a diagram font** (`born_digital: true` and a
`diagram_font_candidates` entry like Chess-Merida): boards are TEXT.
Zero vision calls needed. `chesspdf template borndigital books/<name>`
scaffolds the drivers (the copied layout.json is a worked example — refit
every number to this book):
- anchors + cells + numbering: `chesspdf.pagegrid` (layout.json holds anchor
  size, column split, cell offsets, side-glyph font/map)
- board decode: `chesspdf.textboard` (Marroquin PUA encoding; handles span
  space-loss, doubled text layers, per-color overlay layers)
- solutions: parse text spans (bold = mainline is a common house style)

**B. Born-digital with vector diagrams** (`born_digital: true` but
`diagram_font_candidates` empty — the common modern PDF): headers, hints and
the whole solutions section come free from the text layer; only the boards
need a model. `chesspdf template vector books/<name>` scaffolds:
- ingest.py: CV board-border detection (same detector as C)
- recognize.py: one flash call per cell for the diagram
- solutions.py: parse the solutions section from the text layer — no OCR,
  so those cannot be misread at all
Measured on the generated sample book: 100/100 cells, 100/100 solutions,
100/100 exact FENs, fix cascade never needed.

**C. Scanned** (`born_digital: false`, full-page images): everything visual.
`chesspdf template scanned books/<name>` scaffolds the drivers (refit
layout.json to this book):
- ingest: CV board-border detection (long dark runs per page half), cell =
  header + board + hint crop
- recognize: ONE flash call per cell returning header id/side + hint +
  square listing (`e4: white pawn` lines — measured better than direct FEN);
  resumable jsonl log; emit only when ids cover 1..N exactly
- solutions: one flash call per page -> PGN movetext per entry, with
  cross-page continuation stitching; normalize ids to numeric-prefix form

**None fits** (no numbered anchors, mixed layouts): see "When you are
stuck". Do not improvise a fourth architecture silently.

To rehearse the whole flow first, `chesspdf sample --out sample_book.pdf`
writes a CC0 book plus `truth.json`; `chesspdf score --book <dir>` then
grades a run against that answer key (exit code non-zero on any wrong
position). Real books have no answer key — there, replay is the oracle.

## Fix cascade (scanned books; order matters)

```
CHESSPDF_BOOK=books/<name> python -m chesspdf.audit          # phase-0 truth
python -m chesspdf.fix_fens        # re-recognize ILLEGAL/STRUCT; flash first
python -m chesspdf.fix_moves       # gap-fill + targeted re-OCR
python -m chesspdf.fix_shifts      # zero-cost whole-board shift check
```

**Feed fix_moves ALL replay-failing puzzles** — not only the MOVES_SUSPECT
set from fix_fens. Puzzles whose FEN is fine but whose solution text is
broken are otherwise missed (this exact gap cost 53 puzzles on book 3:
295→348 verified after fixing it).

## Assemble and hand off

```
sh books/<name>/rebuild.sh    # import-legacy -> verify -> assemble -> 4 PGNs
```

problems PGN carries the hint line (if any) as a comment; solution
attributions ("N Players, Site Year") become real game headers. Expected
outcomes: born-digital ≈ 100% verified; scanned ≈ 50–60% first audit →
85–92% after the cascade. Residue: tell the user to run
`python review_app.py --book books/<name> --host 0.0.0.0` and review on any
device; then rerun rebuild.sh. Their verdicts outrank every automated source.

## Trap table (all observed, keep checking for them)

- Misprinted anchor numbers → trust page sequence anchored by the majority
  of printed numbers, log corrections
- Doubled text layers → dedup identical glyphs by position
- Corrected-board overlays → decode per span color, last-drawn wins
- `get_text(clip=…)` filters by span intersection → filter per glyph
- Span-boundary space loss → stream order + unique valid fill (square-color
  parity constrains it)
- Book variation style `(alternatives)` printed BEFORE the reply →
  `transform_book_variations`; typographic dashes and `0-0` →
  `normalize_movetext`
- Castling: always complete FENs with `chesslib.full_fen` (infers rights
  from home squares); a hardcoded `- -` silently breaks castling solutions
- `*` in ids/filenames = literal difficulty stars, never glob
- Warn on images-without-recognition (a silently missing puzzle hid for
  months once)

## When you are stuck

- A template almost fits: write the smallest slot function (anchor finder /
  board decoder / solution parser) in the book folder, keep the contract
  (same JSON shapes), and score it with the oracle before proceeding.
- Neither template fits, or replay stays < 50% after the cascade with no
  diagnosable cause: STOP. Report the probe JSON, rendered sample pages,
  and what you tried. Say plainly this book needs expert/manual help.
  An honest failure beats a confident wrong PGN.

## Track the run

Keep a short onboarding log (interventions needed, template hit or miss,
parameter iterations, final replay rate) — it feeds the template library
and this skill's next revision.
