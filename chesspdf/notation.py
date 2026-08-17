"""Read pre-SAN algebraic notation into SAN, resolved against the position.

Books printed before standard algebraic settled write moves that no SAN
parser can read:

    S           knight (Springer)              Sd4    -> Nd4
    P-prefix    pawn moves                     Pd4    -> d4
    XxY         capture by piece CLASS, with   KxR    -> the king's capture
                no target square               QxRP      of a rook / of a
                                                         rook-file pawn
    Sd5-e7      long form (from-to)
    Kd8(e8)     two destinations, same continuation

Nothing here guesses: a token is only accepted when exactly one legal move
matches it (and, for a whole line, when exactly one resolution replays the
line to the end) — the same unique-survivor rule the repair passes use.

`convert_mainline` is what callers usually want: hand it the printed text
and it returns SAN for the line the book leads with, or None.
"""

from __future__ import annotations

import re
import unicodedata

import chess

from .chesslib import full_fen

PIECE_LETTER = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK,
                "B": chess.BISHOP, "S": chess.KNIGHT, "N": chess.KNIGHT,
                "P": chess.PAWN}
# a capture target may name the file a pawn stands on: 'QxRP' = queen takes
# the rook-file pawn, 'KS' = the king's-side knight
FILE_CLASS = {"R": "ah", "S": "bg", "N": "bg", "B": "cf", "Q": "d", "K": "e"}

OLD_MARKERS = re.compile(r"\b[SP][a-h][1-8]\b|\b[KQRBSP]x[KQRBSP]{1,2}\b")
# Long form first ('Sd5-e7', 'Pe2xSf3'), else the short form — otherwise the
# optional from-square swallows the destination ('Sd4' reads as from=d4).
TOKEN_LONG = re.compile(
    r"^(?P<pc>[KQRBSNP])?(?P<from>[a-h][1-8])"
    r"(?:-|(?P<cap>x)(?P<target>[KQRBSNP]{1,2})?)(?P<to>[a-h][1-8])"
    r"(?:=(?P<promo>[KQRBSN]))?(?P<suffix>[+#]*)$")
TOKEN_SHORT = re.compile(
    r"^(?P<side>[KQ])?(?P<pc>[KQRBSNP])?(?:(?P<cap>x)(?P<target>[KQRBSNP]{1,2})?)?"
    r"(?P<to>[a-h][1-8])?(?:=(?P<promo>[KQRBSN]))?(?P<suffix>[+#]*)$")


def looks_pre_san(text: str) -> bool:
    """True if the text uses markers no SAN parser can read."""
    return bool(OLD_MARKERS.search(text or ""))


def clean(text: str) -> str:
    """Drop OCR combining marks and normalize punctuation."""
    text = "".join(c for c in unicodedata.normalize("NFD", text)
                   if not unicodedata.combining(c))
    for a, b in (("×", "x"), ("‐", "-"), ("–", "-"), ("—", "-"), ("!", ""),
                 ("?", ""), ("ı", "1")):
        text = text.replace(a, b)
    return text


def candidates(board: chess.Board, token: str) -> list[chess.Move]:
    """Every legal move the token could denote, in this position."""
    m = TOKEN_LONG.match(token) or TOKEN_SHORT.match(token)
    if not m or not (m.group("to") or m.group("cap")):
        return []
    mover = PIECE_LETTER.get(m.group("pc") or "P")
    to_sq = chess.parse_square(m.group("to")) if m.group("to") else None
    from_sq = (chess.parse_square(m.group("from"))
               if m.groupdict().get("from") else None)
    promo = PIECE_LETTER.get(m.group("promo")) if m.group("promo") else None
    target = m.group("target") or ""

    # 'QSf5' — the QUEEN'S knight, i.e. the one starting on the queenside
    side = m.groupdict().get("side") if m.group("pc") else None
    side_files = {"Q": "abcd", "K": "efgh"}.get(side or "", "")

    out = []
    for mv in board.legal_moves:
        if board.piece_type_at(mv.from_square) != mover:
            continue
        if side_files and chess.square_name(mv.from_square)[0] not in side_files:
            continue
        if to_sq is not None and mv.to_square != to_sq:
            continue
        if from_sq is not None and mv.from_square != from_sq:
            continue
        if (mv.promotion or None) != promo:
            continue
        if m.group("cap"):
            captured = board.piece_type_at(mv.to_square)
            if captured is None and not board.is_en_passant(mv):
                continue
            if target:
                # the LAST letter is the captured piece; a leading letter, if
                # any, names the file class it stands on ('RP' = rook-file pawn)
                want = PIECE_LETTER.get(target[-1])
                if captured is not None and want is not None and captured != want:
                    continue
                if len(target) > 1:
                    files = FILE_CLASS.get(target[0], "")
                    if files and chess.square_name(mv.to_square)[0] not in files:
                        continue
        out.append(mv)
    return out


def _alternatives(token: str) -> list[str]:
    """'Sd4+(f4+)' prints two destinations for the same idea."""
    m = re.match(r"^(.*?)\(([^)]*)\)$", token)
    if not m or not re.search(r"[a-h][1-8]", m.group(2)):
        return [re.sub(r"\([^)]*\)", "", token)]
    head, alt = m.group(1), m.group(2)
    prefix = re.match(r"^[KQRBSNP]?", head).group(0)
    return [head, prefix + alt if not alt[0].isalpha() or alt[0].islower() else alt]


def line_tokens(text: str) -> list[str]:
    """Move tokens of the line the book leads with (its first printed row)."""
    row = clean(text).strip().splitlines()[0] if text.strip() else ""
    row = re.sub(r"\((?:threat|any)[^)]*\)", "", row, flags=re.I)
    # keep an alternative group glued to its move: 'Kd8 (e8)' is one token
    row = re.sub(r"\(([^)]*)\)", lambda m: "(" + re.sub(r"[\s;,]+", "", m.group(1)) + ")", row)
    row = re.sub(r"\s+\(", "(", row)
    out = []
    for chunk in re.split(r"[;,\s]+", row):
        chunk = chunk.strip().rstrip(".").strip()
        if not chunk or re.fullmatch(r"\d+\.*|\.{2,}", chunk):
            continue
        chunk = re.sub(r"^\d+\.+", "", chunk)
        if not chunk:
            continue
        # 'threat' means no reply is given: what follows is what the key move
        # threatens, not a continuation, so the replayable line stops here
        if re.fullmatch(r"threat|any", chunk, re.I):
            break
        if not re.search(r"[a-h][1-8]|castles?|O-O", chunk, re.I):
            continue                      # OCR debris ('*2', '\', a stray 'I')
        out.append(chunk)
    return out


def to_san_line(fen: str, turn: str, tokens: list[str],
                max_states: int = 4000) -> list[str] | None:
    """Resolve the tokens into SAN. None unless exactly one reading works."""
    if not tokens:
        return None
    results: list[list[str]] = []
    stack = [(chess.Board(full_fen(fen, turn)), 0, [])]
    states = 0
    while stack:
        board, i, out = stack.pop()
        states += 1
        if states > max_states:
            return None
        if i == len(tokens):
            results.append(out)
            if len(results) > 1:
                return None
            continue
        moves: list[chess.Move] = []
        if re.fullmatch(r"castles?", tokens[i], re.I):   # pre-SAN word form
            for side in ("O-O", "O-O-O"):
                try:
                    moves.append(board.parse_san(side))
                except ValueError:
                    pass
        for variant in _alternatives(tokens[i]):
            try:                                  # already SAN?
                moves.append(board.parse_san(variant))
                continue
            except ValueError:
                pass
            moves.extend(candidates(board, variant))
        seen = set()
        for mv in moves:
            if mv in seen:
                continue
            seen.add(mv)
            b2 = board.copy()
            san = b2.san(mv)
            b2.push(mv)
            stack.append((b2, i + 1, out + [san]))
    return results[0] if len(results) == 1 else None


def convert_mainline(fen: str, turn: str, text: str) -> list[str] | None:
    """Printed pre-SAN text -> the SAN line it leads with, or None."""
    return to_san_line(fen, turn, line_tokens(text))
