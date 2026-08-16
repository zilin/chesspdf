"""Agent-facing inspection tools: probe / render / report. All JSON to stdout.

`probe` is the first ten minutes of onboarding a new book, mechanized: does
the PDF have a text layer, which fonts (any PUA-heavy diagram font?), where
are the vector drawings and full-page images, which pages look like puzzle
grids. The onboard-book skill reads this to pick a pipeline template.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import fitz


def probe(pdf: Path, sample_step: int = 1) -> dict:
    doc = fitz.open(pdf)
    n = len(doc)
    pages_with_text = 0
    total_chars = 0
    fonts: Counter[str] = Counter()
    pua_fonts: Counter[str] = Counter()
    full_page_image_pages = 0
    drawing_counts: dict[int, int] = {}
    pages_with_diagram_font: list[int] = []

    for pno in range(0, n, sample_step):
        page = doc[pno]
        d = page.get_text("rawdict")
        chars = 0
        page_fonts: set[str] = set()
        page_pua = False
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            for line in b.get("lines", []):
                for s in line["spans"]:
                    page_fonts.add(s["font"])
                    fonts[s["font"]] += len(s["chars"])
                    chars += len(s["chars"])
                    pua = sum(1 for c in s["chars"] if 0xE000 <= ord(c["c"]) <= 0xF8FF)
                    if pua:
                        pua_fonts[s["font"]] += pua
                        page_pua = True
        if chars:
            pages_with_text += 1
        total_chars += chars
        if page_pua:
            pages_with_diagram_font.append(pno)
        imgs = page.get_images()
        if len(imgs) == 1 and not chars:
            full_page_image_pages += 1
        dr = len(page.get_drawings())
        if dr:
            drawing_counts[pno] = dr

    text_frac = pages_with_text / max(1, n // sample_step)
    born_digital = text_frac > 0.5
    # pages dense in small vector drawings are grid-anchor candidates
    anchor_pages = sorted(p for p, c in drawing_counts.items() if c >= 4)
    return {
        "pdf": str(pdf),
        "pages": n,
        "page_size": [round(doc[0].rect.width, 1), round(doc[0].rect.height, 1)],
        "born_digital": born_digital,
        "text_pages_fraction": round(text_frac, 3),
        "total_chars": total_chars,
        "full_page_image_pages": full_page_image_pages,
        "fonts": [{"name": f, "chars": c} for f, c in fonts.most_common(15)],
        "diagram_font_candidates": [
            {"name": f, "pua_chars": c} for f, c in pua_fonts.most_common(5)],
        "pages_with_diagram_font": _ranges(pages_with_diagram_font),
        "vector_anchor_candidate_pages": _ranges(anchor_pages),
        "hint": ("born-digital: if a diagram font exists, boards are TEXT -> "
                 "grid template (pagegrid + textboard). Otherwise scanned -> "
                 "CV template (border detection + vision recognition). "
                 "Find the exercises/solutions page ranges in the TOC (render it)."),
    }


def _ranges(pages: list[int]) -> list[str]:
    """Compress [3,4,5,9] -> ['3-5', '9']."""
    out: list[str] = []
    run: list[int] = []
    for p in pages:
        if run and p == run[-1] + 1:
            run.append(p)
        else:
            if run:
                out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
            run = [p]
    if run:
        out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
    return out


def render(pdf: Path, pages: list[int], out_dir: Path, zoom: float = 1.5) -> dict:
    doc = fitz.open(pdf)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for pno in pages:
        p = out_dir / f"page_{pno}.png"
        doc[pno].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(p)
        written.append(str(p))
    return {"rendered": written, "zoom": zoom,
            "hint": "read these image files to inspect the layout"}


def report(book: Path) -> dict:
    """Condensed pipeline state for a book folder: what exists, what verifies."""
    out: dict = {"book": str(book)}
    audit = book / "audit_report.json"
    if audit.exists():
        statuses = Counter(e["status"] for e in json.loads(audit.read_text()).values())
        out["audit"] = dict(statuses)
    for name in ("fen_fixes", "moves_fixes", "human_overrides"):
        f = book / f"{name}.jsonl"
        if f.exists():
            latest: dict[str, str] = {}
            for line in f.read_text().splitlines():
                try:
                    r = json.loads(line)
                    latest[r["id"]] = r.get("status") or r.get("verdict") or "?"
                except (json.JSONDecodeError, KeyError):
                    continue
            out[name] = dict(Counter(latest.values()))
    state = book / "bundle" / "state" / "puzzles.jsonl"
    if state.exists():
        statuses = Counter()
        review_ids = []
        for line in state.read_text().splitlines():
            try:
                r = json.loads(line)
                statuses[r["status"]] += 1
                if r["status"] == "needs_review":
                    review_ids.append(r["id"])
            except (json.JSONDecodeError, KeyError):
                continue
        out["bundle"] = dict(statuses)
        out["needs_review_ids"] = sorted(review_ids, key=lambda i: int(i) if i.isdigit() else 0)
    counts = {}
    for sub in ("problem_images", "problem_jsons", "solution_jsons"):
        d = book / sub
        if d.exists():
            counts[sub] = sum(1 for _ in d.iterdir())
    out["files"] = counts
    return out


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: probe <pdf> | render <pdf> <out_dir> <page> [page...] [--zoom Z]"
                 " | report <book_dir>")
    cmd, rest = args[0], args[1:]
    if cmd == "probe":
        result = probe(Path(rest[0]))
    elif cmd == "render":
        zoom = 1.5
        if "--zoom" in rest:
            i = rest.index("--zoom")
            zoom = float(rest[i + 1])
            rest = rest[:i] + rest[i + 2:]
        result = render(Path(rest[0]), [int(p) for p in rest[2:]], Path(rest[1]), zoom)
    elif cmd == "report":
        result = report(Path(rest[0]))
    else:
        sys.exit(f"unknown command {cmd!r}")
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
