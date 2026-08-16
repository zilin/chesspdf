"""chesspdf CLI.

  python -m chesspdf.cli import-legacy --src . --bundle books/imagination/bundle
  python -m chesspdf.cli status   --bundle books/imagination/bundle
  python -m chesspdf.cli verify   --bundle books/imagination/bundle
  python -m chesspdf.cli assemble --bundle books/imagination/bundle
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .bundle import Bundle, Puzzle
from .chesslib import first_mover


def load_jsonl(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                out[r["id"]] = r
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def cmd_import_legacy(args: argparse.Namespace) -> None:
    src = Path(args.src).resolve()
    bundle = Bundle.create(args.bundle, title=args.title)

    for sub in ("problem_images", "solution_images"):
        for f in sorted((src / sub).glob("*.png")):
            dst = bundle.root / sub / f.name
            if not dst.exists():
                shutil.copy2(f, dst)

    import re as _re

    def solution_key(raw_id: str) -> str:
        m = _re.match(r"\s*(\d+)", raw_id)
        return m.group(1) if m else raw_id.replace("*", "")

    solutions: dict[str, str] = {}
    names: dict[str, str] = {}
    for jpath in (src / "solution_jsons").glob("*.json"):
        try:
            for s in json.loads(jpath.read_text()).get("solutions", []):
                key = solution_key(s["id"])
                solutions.setdefault(key, s["moves"])
                m = _re.match(r"\s*\d+\s+(.+)", s["id"])
                if m:
                    names[key] = m.group(1).strip()
        except (json.JSONDecodeError, KeyError):
            continue

    fen_fixes = load_jsonl(src / "fen_fixes.jsonl")
    move_fixes = load_jsonl(src / "moves_fixes.jsonl")
    overrides = load_jsonl(src / "human_overrides.jsonl")
    images = bundle.image_index()

    n = 0
    for jpath in sorted((src / "problem_jsons").glob("*.json")):
        try:
            prob = json.loads(jpath.read_text())
        except json.JSONDecodeError:
            continue
        pid = prob["id"].replace("*", "")
        starred = prob["id"].count("*")

        fen, fen_src = prob["fen"].split()[0], "original"
        fix = fen_fixes.get(pid)
        if fix and fix.get("status") in ("FIXED", "SHIFT_FIXED", "MOVES_SUSPECT") and fix.get("fen"):
            fen, fen_src = fix["fen"], f"recognize:{fix['status']}"

        moves, moves_src = solutions.get(pid), "original"
        mfix = move_fixes.get(pid)
        if mfix and mfix.get("status") in ("REPAIRED", "REOCRED") and mfix.get("moves"):
            moves, moves_src = mfix["moves"], f"repair:{mfix['status']}"

        turn = (fix or {}).get("to_move") or (first_mover(moves) if moves else None) \
            or prob.get("to_move", "w")

        status = "recognized"
        ov = overrides.get(pid)
        if ov:
            if ov.get("verdict") == "exclude":
                status = "excluded"
            else:
                status = "approved"
                fen, fen_src = ov.get("fen", fen).split()[0] or fen, "human"
                turn = ov.get("to_move", turn)
                if ov.get("moves"):        # human-corrected solution text
                    moves, moves_src = ov["moves"], "human"

        p = Puzzle(pid, {
            "id": pid, "image": images.get(pid, Path("")).name or None,
            "fen": fen, "to_move": turn, "moves": moves, "status": status,
            "difficulty": starred, "game_name": names.get(pid),
            "hint": prob.get("hint"),
            "sources": {"fen": fen_src, "moves": moves_src},
            "ledger": [],
        })
        p.ledger_add("import", "LEGACY", f"fen={fen_src} moves={moves_src}")
        bundle.write(p)
        n += 1
    bundle.compact()
    bundle.event("import-legacy", f"{n} puzzles from {src}")
    print(f"imported {n} puzzles -> {bundle.root}")


def cmd_status(args: argparse.Namespace) -> None:
    bundle = Bundle(args.bundle)
    print(json.dumps(bundle.manifest(), indent=1, ensure_ascii=False))
    print("puzzles:", bundle.status_counts())


def cmd_verify(args: argparse.Namespace) -> None:
    from .stages import verify
    bundle = Bundle(args.bundle)
    print(verify.run(bundle))
    bundle.compact()


def cmd_assemble(args: argparse.Namespace) -> None:
    from .stages import assemble
    bundle = Bundle(args.bundle)
    print(assemble.run(bundle))


def _delegate(module: str, argv: list[str]) -> None:
    """Run a root-level engine module's main() (gradual migration target)."""
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    _sys.argv = [module] + argv
    importlib.import_module(module).main()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="chesspdf",
        description="Chess-book PDF -> verified PGN: one CLI for the whole pipeline.")
    parser.add_argument("--book", default="books/imagination",
                        help="book folder (default: books/imagination)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import-legacy", help="build a bundle from the flat chess-tools layout")
    p.add_argument("--src", default=".")
    p.add_argument("--bundle", required=True)
    p.add_argument("--title", default="Imagination in Chess")
    p.set_defaults(fn=cmd_import_legacy)

    for name, fn in (("status", cmd_status), ("verify", cmd_verify),
                     ("assemble", cmd_assemble)):
        p = sub.add_parser(name)
        p.add_argument("--bundle", required=True)
        p.set_defaults(fn=fn)

    for name, module, help_ in (
            ("probe", "chesspdf.probe", "inspect a PDF / render pages / book state (JSON)"),
            ("template", "chesspdf.templates", "scaffold a book folder from a pipeline template"),
            ("audit", "chesspdf.audit", "consistency audit (replay verification)"),
            ("fix-fens", "chesspdf.fix_fens", "re-recognize + repair failing FENs"),
            ("fix-moves", "chesspdf.fix_moves", "gap-fill + re-OCR broken solutions"),
            ("fix-shifts", "chesspdf.fix_shifts", "zero-cost whole-board shift repair"),
            ("build", "chesspdf.build_pgn", "legacy flat-layout PGN build"),
            ("review", "chesspdf.review_app", "launch the human review web app")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(fn=lambda a, m=module: _delegate(m, a.rest))

    args = parser.parse_args()
    import os
    os.environ["CHESSPDF_BOOK"] = str(Path(args.book).resolve())
    args.fn(args)


if __name__ == "__main__":
    main()
