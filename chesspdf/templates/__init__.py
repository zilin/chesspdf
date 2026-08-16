"""Pipeline templates: scaffold a new book folder from a proven family.

    chesspdf template                       # list families
    chesspdf template scanned books/mybook  # copy drivers + example layout.json

Families (each fully worked on a finished book):
- borndigital: boards are TEXT in a diagram font (zero API calls).
  Drivers: ingest.py (vector-anchored grid), fens.py (textboard decode),
  solutions.py (bold=mainline span parsing).
- scanned: vision pipeline. Drivers: ingest.py (CV board-border detection),
  recognize.py (flash square-listing reads), solutions_ocr.py (per-page OCR
  with cross-page stitching), rebuild.sh (bundle -> 4 PGNs).

The copied layout.json holds the previous book's numbers as a worked
example — refit every value to the new book (see the onboard-book skill).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAMILIES = sorted(p.name for p in HERE.iterdir() if p.is_dir() and p.name != "__pycache__")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(f"families: {', '.join(FAMILIES)}\n"
              f"usage: chesspdf template <family> <dest_book_dir>")
        return
    family, dest = args[0], Path(args[1])
    if family not in FAMILIES:
        sys.exit(f"unknown family {family!r}; available: {', '.join(FAMILIES)}")
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for f in sorted((HERE / family).iterdir()):
        if f.name == "__pycache__":
            continue
        target = dest / f.name
        if target.exists():
            print(f"skip (exists): {target}")
            continue
        shutil.copy2(f, target)
        copied.append(f.name)
    print(f"scaffolded {dest} from {family!r}: {', '.join(copied)}\n"
          f"next: put the book at {dest / 'source.pdf'} and refit layout.json")


if __name__ == "__main__":
    main()
