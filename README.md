# chesspdf

Convert chess puzzle books you own — PDF in, **replay-verified PGN** out.

Point it at a chess book PDF and get four PGN files: a spoiler-free problems
file (one position per puzzle, with the book's hint as a comment), a
solutions file, and verified/review splits. Works on both born-digital PDFs
(boards typeset in a chess font — decoded deterministically, zero API calls)
and scanned books (vision recognition via the Gemini API).

## Why trust the output

Vision models misread chess diagrams too often to trust. chesspdf never
does: **a position is accepted only when the book's own printed solution
replays legally from it** (python-chess). Failures go through deterministic
repair passes; the small residue lands in a human review web app instead of
silently shipping wrong. Every puzzle carries a ledger of how its data was
obtained and verified.

Measured across three complete books (2,266 puzzles): born-digital books
verify at ~100% with zero API calls; scanned books reach ~90% machine-verified
before any human review.

## Install

```sh
uv tool install chesspdf        # or: pipx install chesspdf
export GEMINI_API_KEY=...       # only needed for scanned books
```

## Quickstart

```sh
# 1. What kind of book is this?
chesspdf probe probe mybook.pdf                 # JSON: born-digital or scanned,
                                                # fonts, candidate puzzle pages

# 2. Render a few pages and look at the layout
chesspdf probe render mybook.pdf ./pages 10 11 300

# 3. Run the pipeline for your book folder (see docs/DEVELOPMENT.md)
chesspdf --book books/mybook audit              # replay-verify everything
chesspdf --book books/mybook fix-fens           # repair failing positions
chesspdf --book books/mybook fix-moves          # repair broken solutions
chesspdf --book books/mybook review             # human review web app
```

### Or let an agent do it

The `plugin/` directory packages this workflow as an **agent skill**
(`onboard-book`) for Claude Code, Codex CLI, Antigravity CLI, and pi: install
the CLI once, copy one directory into your agent host, then just say *"use
onboard-book to convert mybook.pdf"*. The agent probes the PDF, picks a
pipeline, tunes layout parameters against the built-in verification oracle,
and hands you only the residue. See
[`plugin/INSTALL.md`](https://github.com/zilin/chesspdf/blob/main/plugin/INSTALL.md).

## Legal

chesspdf is a **personal-use format conversion tool**: use it on books you
own, for your own study. Do not redistribute extracted positions, solutions,
or any other book content. This repository and the published package contain
code only — no book content.

## More

- [`docs/DEVELOPMENT.md`](https://github.com/zilin/chesspdf/blob/main/docs/DEVELOPMENT.md)
  — pipeline internals, the bundle state machine, measured lessons (error
  taxonomy, prompt A/B results, CV cross-check accuracy)
- [`plugin/skills/onboard-book/SKILL.md`](https://github.com/zilin/chesspdf/blob/main/plugin/skills/onboard-book/SKILL.md)
  — the distilled onboarding playbook agents follow

MIT © Zilin Du
