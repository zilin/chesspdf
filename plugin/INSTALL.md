# Installing the chesspdf plugin

The plugin has **two parts, installed separately**:

| Part | What it is | Where it goes |
|---|---|---|
| **Engine** — the `chesspdf` CLI | Python package (this repo) doing all real work: probe, ingest, recognition, replay verification, PGN assembly | installed once with pip/uv, on your `PATH` |
| **Knowledge** — this `plugin/` directory | `plugin.json` + `skills/onboard-book/SKILL.md`, the playbook that teaches your agent how to drive the CLI | copied into your agent host's skills/plugins location |

You do **not** copy the whole repository into your agent host — only the
`plugin/` directory (and for hosts that take bare skills, only
`plugin/skills/onboard-book/`). The engine is a normal system install.

---

## 1. Install the engine (all hosts, once)

```sh
uv tool install chesspdf                 # or: pipx install chesspdf
chesspdf --help                          # verify
```

(For development, install from a checkout instead:
`uv tool install /path/to/chesspdf`.)

Scanned books need a Gemini API key:

```sh
export GEMINI_API_KEY=...                # or put it in a .env in your working dir
```

Born-digital books (text-layer boards) run with **zero** API calls.

## 2. Install the knowledge, per host

### Claude Code

Project-level (recommended while iterating):

```sh
cp -r plugin/skills/onboard-book <your-project>/.claude/skills/
```

Or user-level for all projects: `cp -r plugin/skills/onboard-book ~/.claude/skills/`.
Or, if you publish this repo as a plugin marketplace:

```
/plugin marketplace add <repo-or-path>
/plugin install chesspdf@<marketplace>
```

### Codex CLI (Agent Plugins 1.0)

The `plugin/` directory already is a compliant Agent Plugins 1.0 package
(skills-only; `mcp.json` is optional in the spec and deliberately absent):

```sh
cp -r plugin ~/.codex/plugins/chesspdf        # global
# or: cp -r plugin <your-project>/.codex/plugins/chesspdf
```

### Antigravity CLI (agy)

```sh
agy plugin install /path/to/chess-tools/plugin
agy plugin list                               # verify
```

Or skip the plugin mechanism and drop the bare skill into a workspace:
`cp -r plugin/skills/onboard-book <workspace>/.agents/skills/`.

### pi

pi takes bare skills (same SKILL.md format, no plugin wrapper):

```sh
cp -r plugin/skills/onboard-book ~/.pi/agent/skills/
```

## 3. Use it

In any of the hosts above, in a directory containing (or next to) your book
PDF:

> Use onboard-book to convert `my_book.pdf` to PGN. Put the hint lines in the
> problems PGN as comments.

The agent will check its prerequisites itself (`chesspdf --version`,
`GEMINI_API_KEY`) and ask you only when something is genuinely missing or
when the book defeats both pipeline templates.

## Updating / uninstalling

- Engine: `uv tool upgrade chesspdf` / `uv tool uninstall chesspdf`.
- Knowledge: re-copy the `plugin/` directory (it is plain files; hosts pick
  up changes on next session). Remove by deleting the copied directory
  (`agy plugin uninstall chesspdf` on Antigravity).

## What's inside `plugin/`

```
plugin/
├── plugin.json                  # Agent Plugins 1.0 manifest (also valid for agy)
├── INSTALL.md                   # this file
└── skills/onboard-book/SKILL.md # the playbook (single source of truth)
```

One source, four hosts; only the copy destination differs. If you edit the
playbook, edit it here and re-copy — never fork per host.
