"""Assemble stage: emit ChessBase-ready PGN from the bundle state.

Tiering per puzzle: full movetext → book-convention transform → mainline with
the original text preserved as a comment. Human-excluded puzzles are skipped;
puzzles that fail every tier go to the review PGN. Both outputs are re-parsed
before writing; a parse error fails the build."""

from __future__ import annotations

import io
import re

import chess
import chess.pgn

from ..bundle import Bundle
from ..chesslib import strip_variations, structural_check, transform_book_variations, try_parse


def run(bundle: Bundle) -> dict[str, int]:
    title = bundle.manifest().get("title", "Chess Puzzles")
    verified: list[str] = []
    review: list[str] = []
    problems: list[str] = []      # position-only, no spoilers
    solutions: list[str] = []     # every puzzle, best available solution
    stats = {"full": 0, "transformed": 0, "mainline": 0, "unparsed": 0, "skipped": 0}

    def sort_key(pid: str) -> int:
        return int(re.sub(r"\D", "", pid) or 0)

    puzzles = bundle.puzzles()
    for pid in sorted(puzzles, key=sort_key):
        p = puzzles[pid]
        if p.status == "excluded":
            stats["skipped"] += 1
            continue
        fen = (p.fen or "").split()[0] if p.fen else ""
        if not fen or structural_check(fen) is not None:
            stats["skipped"] += 1
            continue
        turn = p.to_move if p.to_move in ("w", "b") else "w"
        moves = p.moves
        to_move_label = "White to move" if turn == "w" else "Black to move"
        headers = {
            "Event": f"{title}: Puzzle {pid} ({to_move_label})",
            "White": f"Puzzle {pid}",
            "Black": to_move_label,
            "Round": pid,
            "Result": "*",
        }
        # "Ebralidze-Blagidze, Tbilisi 1949" -> real game headers
        if p.game_name:
            m = re.match(r"^([^-,]+?)\s*-\s*([^,]+?)(?:,\s*(.*?))?\s*(\d{4})?(?:/\d{2})?$",
                         p.game_name.strip())
            if m and m.group(2):
                headers["White"] = m.group(1).strip()
                headers["Black"] = m.group(2).strip()
                if m.group(3):
                    headers["Site"] = m.group(3).strip()
                if m.group(4):
                    headers["Date"] = f"{m.group(4)}.??.??"
            else:
                headers["Black"] = p.game_name.strip()

        game = None
        if moves:
            game = try_parse(fen, turn, moves)
            if game is not None:
                stats["full"] += 1
            else:
                game = try_parse(fen, turn, transform_book_variations(moves))
                if game is not None:
                    stats["transformed"] += 1
                else:
                    game = try_parse(fen, turn, strip_variations(moves))
                    if game is not None:
                        stats["mainline"] += 1
                        game.comment = "Book solution (verbatim): " + re.sub(r"[{}]", "'", moves.strip())

        # the book's hint line (if captured) rides along as a comment,
        # visible in the position-only view without spoiling the solution
        hint = "{ " + re.sub(r"[{}]", "'", str(p.hint).strip()) + " } " if p.hint else ""
        problems.append(
            "\n".join(f'[{k} "{v}"]' for k, v in headers.items())
            + f'\n[SetUp "1"]\n[FEN "{fen} {turn} - - 0 1"]\n\n{hint}*'
        )
        if game is not None:
            result = game.headers.get("Result", "*")
            headers["Result"] = result if result in ("1-0", "0-1", "1/2-1/2") else "*"
            for k, v in headers.items():
                game.headers[k] = v
            exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
            text = game.accept(exporter).strip()
            verified.append(text)
            solutions.append(text)
        else:
            stats["unparsed"] += 1
            body = ""
            if moves:
                body = "{ Book solution (text): " + re.sub(r"[{}]", "'", moves.strip()) + " } "
            text = (
                "\n".join(f'[{k} "{v}"]' for k, v in headers.items())
                + f'\n[SetUp "1"]\n[FEN "{fen} {turn} - - 0 1"]\n\n{body}*'
            )
            review.append(text)
            solutions.append(text)

    def check(text: str, label: str) -> int:
        n = 0
        stream = io.StringIO(text)
        while True:
            g = chess.pgn.read_game(stream)
            if g is None:
                break
            if g.errors:
                raise SystemExit(f"{label}: game {g.headers.get('Round')}: {g.errors[0]}")
            n += 1
        return n

    vtext = "\n\n".join(verified) + "\n"
    rtext = "\n\n".join(review) + "\n"
    ptext = "\n\n".join(problems) + "\n"
    stext = "\n\n".join(solutions) + "\n"
    nv, nr = check(vtext, "verified"), check(rtext, "review")
    np_, ns = check(ptext, "problems"), check(stext, "solutions")
    out = bundle.root / "exports"
    (out / "puzzles_verified.pgn").write_text(vtext)
    (out / "puzzles_review.pgn").write_text(rtext)
    (out / "puzzles_problems.pgn").write_text(ptext)
    (out / "puzzles_solutions.pgn").write_text(stext)
    stats.update(verified_games=nv, review_games=nr,
                 problem_games=np_, solution_games=ns)
    bundle.event("assemble", f"{stats}")
    return stats
