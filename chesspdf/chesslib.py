"""Pure chess/PGN helpers shared by all stages (no I/O, no paths)."""

from __future__ import annotations

import io
import re

import chess
import chess.pgn

TOKEN_RE = re.compile(
    r"(?P<num>\d+\.(?:\.\.)?)|(?P<san>[KQRBNP]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|O-O-O[+#]?|O-O[+#]?)(?P<sufx>[!?]*)|(?P<res>1-0|0-1|1/2-1/2|\*)"
)

_DASHES = str.maketrans({"–": "-", "‑": "-", "−": "-"})


def normalize_movetext(moves: str) -> str:
    """Fold typographic dashes and digit-zero castling into PGN spellings."""
    s = moves.translate(_DASHES)
    s = re.sub(r"\b0-0-0", "O-O-O", s)
    return re.sub(r"\b0-0", "O-O", s)


def infer_castling(placement: str) -> str:
    """Plausible castling rights for a bare diagram: king and rook still on
    their home squares. Extra rights are harmless to replay; missing ones
    make the book's own castling moves illegal."""

    def expand(row: str) -> str:
        return "".join(" " * int(ch) if ch.isdigit() else ch for ch in row)

    rows = placement.split("/")
    r1, r8 = expand(rows[-1]), expand(rows[0])
    rights = ""
    if len(r1) == 8 and r1[4] == "K":
        rights += "K" if r1[7] == "R" else ""
        rights += "Q" if r1[0] == "R" else ""
    if len(r8) == 8 and r8[4] == "k":
        rights += "k" if r8[7] == "r" else ""
        rights += "q" if r8[0] == "r" else ""
    return rights or "-"


def full_fen(fen: str, turn: str | None = None) -> str:
    """Complete a FEN. Fields already present in `fen` win; a bare placement
    gets `turn` and castling rights inferred from home squares."""
    parts = fen.split()
    placement = parts[0]
    return " ".join(
        [
            placement,
            parts[1] if len(parts) > 1 else (turn or "w"),
            parts[2] if len(parts) > 2 else infer_castling(placement),
            parts[3] if len(parts) > 3 else "-",
            parts[4] if len(parts) > 4 else "0",
            parts[5] if len(parts) > 5 else "1",
        ]
    )


def structural_check(fen_board: str) -> str | None:
    """None if the piece-placement field passes basic sanity, else a reason."""
    try:
        board = chess.Board(f"{fen_board} w - - 0 1")
    except ValueError as exc:
        return f"unparseable: {exc}"
    wk = len(board.pieces(chess.KING, chess.WHITE))
    bk = len(board.pieces(chess.KING, chess.BLACK))
    if (wk, bk) != (1, 1):
        return f"kings: {wk} white, {bk} black"
    back = chess.SquareSet(chess.BB_RANK_1 | chess.BB_RANK_8)
    for color in (chess.WHITE, chess.BLACK):
        if board.pieces(chess.PAWN, color) & back:
            return "pawn on rank 1/8"
        if len(board.pieces(chess.PAWN, color)) > 8:
            return "more than 8 pawns"
    return None


def first_mover(moves: str) -> str | None:
    m = re.match(r"\s*\d+\s*\.(\.\.)?", moves)
    return None if not m else ("b" if m.group(1) else "w")


def strip_variations(moves: str) -> str:
    """Drop {comments} and (variations), keeping the mainline text."""
    out: list[str] = []
    depth_p = depth_b = 0
    for ch in moves:
        if ch == "{":
            depth_b += 1
        elif ch == "}":
            depth_b = max(0, depth_b - 1)
        elif depth_b == 0 and ch == "(":
            depth_p += 1
        elif depth_b == 0 and ch == ")":
            depth_p = max(0, depth_p - 1)
        elif depth_p == 0 and depth_b == 0:
            out.append(ch)
    return "".join(out)


def try_parse(fen: str, turn: str, movetext: str) -> chess.pgn.Game | None:
    """Full parse; None unless every move (variations included) is legal.
    `fen` may be a bare placement (castling inferred) or a full FEN."""
    pgn = f'[SetUp "1"]\n[FEN "{full_fen(fen, turn)}"]\n\n{movetext}\n'
    try:
        game = chess.pgn.read_game(io.StringIO(pgn))
    except Exception:  # noqa: BLE001 — OCR'd movetext can crash the parser
        return None
    if game is None or game.errors or game.next() is None:
        return None
    return game


def replay_error(fen: str, turn: str, movetext: str) -> str | None:
    """None if the movetext replays cleanly, else an error description.
    `fen` may be a bare placement (castling inferred) or a full FEN."""
    pgn = f'[SetUp "1"]\n[FEN "{full_fen(fen, turn)}"]\n\n{movetext}\n'
    try:
        game = chess.pgn.read_game(io.StringIO(pgn))
    except Exception as exc:  # noqa: BLE001
        return f"PARSE: parser crash: {type(exc).__name__}"
    if game is None:
        return "PARSE: empty"
    if game.errors:
        err = game.errors[0]
        kind = "ILLEGAL" if isinstance(err, chess.IllegalMoveError) else "PARSE"
        return f"{kind}: {err}"
    if game.next() is None:
        return "PARSE: no moves"
    return None


def mainline_tokens(moves: str) -> list[str]:
    text = strip_variations(moves)
    return [m.group("san") for m in TOKEN_RE.finditer(text) if m.group("san")]


def replay_sans(fen: str, turn: str, sans: list[str]) -> tuple[int, chess.Board]:
    """`fen` may be a bare placement (castling inferred) or a full FEN."""
    board = chess.Board(full_fen(fen, turn))
    for i, san in enumerate(sans):
        try:
            board.push_san(san)
        except ValueError:
            return i, board
    return len(sans), board


def tail_replays(board: chess.Board, sans: list[str]) -> bool:
    b = board.copy()
    for san in sans:
        try:
            b.push_san(san)
        except ValueError:
            return False
    return True


def rebuild_movetext(sans: list[str], turn: str) -> str:
    out, move_no, white = [], 1, turn == "w"
    if not white:
        out.append(f"{move_no}...")
    for san in sans:
        if white:
            out.append(f"{move_no}.")
        out.append(san)
        if not white:
            move_no += 1
        white = not white
    return " ".join(out)


def transform_book_variations(movetext: str) -> str:
    """Shift top-level paren groups one mainline token right (book style puts
    the reply's alternatives before the reply itself)."""
    tokens: list[tuple[str, str]] = []
    buf = ""
    i = 0
    while i < len(movetext):
        ch = movetext[i]
        if ch == "{":
            j = movetext.find("}", i)
            j = len(movetext) - 1 if j == -1 else j
            tokens.append(("comment", movetext[i : j + 1]))
            i = j + 1
            continue
        if ch == "(":
            depth, start = 1, i
            i += 1
            while i < len(movetext) and depth:
                if movetext[i] == "(":
                    depth += 1
                elif movetext[i] == ")":
                    depth -= 1
                elif movetext[i] == "{":
                    k = movetext.find("}", i)
                    i = k if k != -1 else len(movetext) - 1
                i += 1
            tokens.append(("group", movetext[start:i]))
            continue
        buf += ch
        if ch.isspace():
            if buf.strip():
                tokens.append(("main", buf.strip()))
            buf = ""
        i += 1
    if buf.strip():
        tokens.append(("main", buf.strip()))

    out: list[str] = []
    pending: list[str] = []
    for kind, text in tokens:
        if kind == "group":
            pending.append(text)
        elif kind == "main":
            is_move = not re.fullmatch(r"\d+\.(\.\.)?|1-0|0-1|1/2-1/2|\*", text)
            out.append(text)
            if is_move and pending:
                out.extend(pending)
                pending = []
        else:
            out.append(text)
    out.extend(pending)
    return " ".join(out)
