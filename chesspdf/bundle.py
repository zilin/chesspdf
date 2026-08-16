"""The Bundle: a directory that is the single source of truth for one book.

Layout:
  <bundle>/
    manifest.json           book metadata + profile + stage progress
    source.pdf              (optional)
    problem_images/<id>.png the '*' difficulty stars are literal filename chars
    solution_images/page_N.png
    state/puzzles.jsonl     append-only; one JSON per line; last write wins per id
    state/events.jsonl      append-only audit trail of stage runs and verdicts
    exports/                generated PGN files

Puzzle record fields:
  id, image, fen, to_move, moves, status, sources{fen,moves}, ledger[list of
  {stage, verdict, detail}], plus free extras. Status machine:
  detected → recognized → verified | needs_review | no_solution
           → approved | excluded  (human)     → exported (assemble)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUSES = ("detected", "recognized", "verified", "needs_review",
            "no_solution", "approved", "excluded")


@dataclass
class Puzzle:
    id: str
    data: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.data[name]
        except KeyError:
            return None

    def ledger_add(self, stage: str, verdict: str, detail: str = "") -> None:
        self.data.setdefault("ledger", []).append(
            {"stage": stage, "verdict": verdict, "detail": detail[:300]})


class Bundle:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.state_dir = self.root / "state"
        self.puzzles_file = self.state_dir / "puzzles.jsonl"
        self.events_file = self.state_dir / "events.jsonl"

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def create(cls, root: Path | str, title: str) -> "Bundle":
        b = cls(root)
        for d in (b.root, b.state_dir, b.root / "problem_images",
                  b.root / "solution_images", b.root / "exports"):
            d.mkdir(parents=True, exist_ok=True)
        if not b.manifest_file.exists():
            b.save_manifest({"title": title, "created": time.strftime("%F %T"),
                             "profile": {}, "stages": {}})
        return b

    @property
    def manifest_file(self) -> Path:
        return self.root / "manifest.json"

    def manifest(self) -> dict:
        return json.loads(self.manifest_file.read_text())

    def save_manifest(self, m: dict) -> None:
        self.manifest_file.write_text(json.dumps(m, indent=1, ensure_ascii=False))

    # -- puzzle state ------------------------------------------------------
    def puzzles(self) -> dict[str, Puzzle]:
        out: dict[str, Puzzle] = {}
        if self.puzzles_file.exists():
            for line in self.puzzles_file.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    out[d["id"]] = Puzzle(d["id"], d)  # last write wins
                except (json.JSONDecodeError, KeyError):
                    continue
        return out

    def write(self, puzzle: Puzzle) -> None:
        with self.puzzles_file.open("a") as f:
            f.write(json.dumps(puzzle.data, ensure_ascii=False) + "\n")

    def event(self, stage: str, summary: str, **extra: Any) -> None:
        rec = {"ts": time.strftime("%F %T"), "stage": stage, "summary": summary, **extra}
        with self.events_file.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def compact(self) -> None:
        """Rewrite puzzles.jsonl keeping only the latest record per id."""
        latest = self.puzzles()
        tmp = self.puzzles_file.with_suffix(".tmp")
        with tmp.open("w") as f:
            for p in latest.values():
                f.write(json.dumps(p.data, ensure_ascii=False) + "\n")
        tmp.replace(self.puzzles_file)

    # -- convenience -------------------------------------------------------
    def image_index(self) -> dict[str, Path]:
        return {p.stem.replace("*", ""): p
                for p in sorted((self.root / "problem_images").glob("*.png"))}

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.puzzles().values():
            counts[p.status or "?"] = counts.get(p.status or "?", 0) + 1
        return counts
