# Agent notes — chesspdf

Read this + README.md (user-facing) + docs/DEVELOPMENT.md (pipeline
internals) before changing anything.

## What this is

Chess book PDF -> replay-verified PGN. The one architectural idea that
matters: **recognition is never trusted; every position must let the book's
own solution replay legally** (python-chess). Deterministic code repairs
failures; humans arbitrate the residue via `chesspdf review`.

## Layout

- `chesspdf/` — the package. `chesslib.py` is the single source for pure
  chess logic (replay, `full_fen`/`infer_castling` — never hardcode `- -`
  castling fields; `normalize_movetext` folds typographic dashes and `0-0`).
  `pagegrid.py` + `textboard.py` decode born-digital books from layout.json
  profiles; `occupancy.py` is the classical-CV cross-check; `probe.py` holds
  the agent-facing probe/render/report JSON tools; stages live in
  `bundle.py` + `stages/`; `review_app.py` is the human review web app
  (bundled piece SVGs in `chesspdf/pieces/`).
- `plugin/` — the agent plugin (Agent Plugins 1.0 layout): the
  `onboard-book` SKILL.md playbook + INSTALL.md. Knowledge lives in the
  skill, capability lives in the CLI; never put host-specific instructions
  in the skill.
- Tool surface is **CLI-only by design** (no MCP): JSON out, idempotent,
  resumable. `SKILL.md` and `--help` are the discoverability contract —
  maintain them to interface-doc standard.

## The regression discipline (do not break)

The evaluation truth sets (three complete books, 2,266 replay-verified
puzzles) live in a **private** companion repo, not here. Behavioral changes
to recognition, repair, or replay logic must be regression-tested there
before release (install this package editable from a sibling checkout and
rerun the books). If you cannot run that regression, say so in the PR/commit
instead of assuming safety — this discipline has caught multiple
plausible-but-wrong designs (see docs/DEVELOPMENT.md's prompt table).

## Measured facts to respect (details in docs/DEVELOPMENT.md)

- Cheap-model-first is deliberate: the pipeline is model-robust; accuracy
  comes from verification-anchored repair, not prompting.
- Self-check / self-correct prompt additions measured net negative; a
  hollow-vs-solid style note measured positive. Don't relearn this.
- Two models agreeing does not make a reading correct — errors correlate.
- Legal boundary: personal-use format conversion only; no book content in
  this repo, its history, or the published package (sdist is whitelisted in
  pyproject — keep it that way).
