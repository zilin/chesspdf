#!/usr/bin/env python3
"""Human review web app: original diagram vs. rendered FEN, side by side.

  python -m chesspdf.review_app [--book books/x] [--books-root books] [--port 8899]

Every book found under --books-root (default: the book's parent) is listed in
the header's picker, so one process serves them all; --book only sets which
one opens first. Left: the cropped book diagram. Right: an editable board
rendered from the currently recognized FEN — click a palette piece then a
square to place it (eraser clears), or edit the FEN text directly. Verdicts
append to that book's human_overrides.jsonl, which every assembly step honors
above all automated sources.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import os as _os
if "--book" in __import__("sys").argv:
    _i = __import__("sys").argv.index("--book")
    _os.environ["CHESSPDF_BOOK"] = str(Path(__import__("sys").argv[_i + 1]).resolve())
_os.environ.setdefault("CHESSPDF_BOOK", str(Path("books/imagination").resolve()))
BOOK = Path(_os.environ["CHESSPDF_BOOK"])

from chesspdf.audit import load_problems, load_solutions
from chesspdf.chesslib import (first_mover, mainline_tokens, normalize_movetext,
                               replay_sans, san_candidates, strip_variations,
                               structural_check, transform_book_variations, try_parse)

PKG = Path(__file__).resolve().parent          # bundled piece SVGs live here
BOOKS_ROOT = BOOK.parent                       # overridden in main()


def discover_books() -> dict[str, Path]:
    """Book name -> folder, for every folder under BOOKS_ROOT that has
    recognized puzzles. The UI's book picker is built from this."""
    found: dict[str, Path] = {}
    for d in sorted(BOOKS_ROOT.iterdir()) if BOOKS_ROOT.is_dir() else []:
        if d.is_dir() and ((d / "problem_jsons").is_dir() or (d / "bundle").is_dir()
                           or (d / "fens.json").exists()):
            found[d.name] = d
    found.setdefault(BOOK.name, BOOK)          # always serve the launch book
    return found


def image_index(book: Path) -> dict[str, Path]:
    return {p.stem.replace("*", ""): p
            for p in sorted((book / "problem_images").glob("*.png"))}


def load_jsonl(path: Path, key: str = "id") -> dict[str, dict]:
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                out[r[key]] = r  # last write wins
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def tier_of(pid, moves, fix, ov):
    if ov:
        return "HUMAN"
    try:
        from chesspdf.confidence import tier
        return tier(moves, (fix or {}).get("note", ""), (fix or {}).get("confidence", ""))
    except Exception:
        return "?"


def load_native(book: Path) -> tuple[dict[str, dict], dict[str, str]]:
    """Born-digital books emit fens.json/solutions.json (+ overrides.json for
    book misprints) instead of the problem_jsons/solution_jsons pair."""
    fens = json.loads((book / "fens.json").read_text())
    raw = json.loads((book / "solutions.json").read_text()) \
        if (book / "solutions.json").exists() else {}
    over = json.loads((book / "overrides.json").read_text()) \
        if (book / "overrides.json").exists() else {}
    problems, solutions = {}, {}
    for pid, fen in fens.items():
        parts = fen.split()
        problems[pid] = {"id": pid, "fen": parts[0],
                         "to_move": parts[1] if len(parts) > 1 else "w"}
        entry = raw.get(pid) or {}
        main = over.get(pid, {}).get("main") or (
            entry.get("main") if isinstance(entry, dict) else entry)
        if main:
            solutions[pid] = normalize_movetext(main)
    return problems, solutions


def replay_audit(problems: dict[str, dict], solutions: dict[str, str]) -> dict[str, dict]:
    """Stand-in for audit_report.json: run the oracle live (cheap, and it is
    the only verdict that matters). Tokenizes the mainline the way verify.py
    does rather than parsing it as PGN — printed solutions carry evaluation
    symbols ('+-') and lost span-boundary spaces ('11.Re1Kf7') that fail a
    strict parse while replaying perfectly."""
    report = {}
    for pid, prob in problems.items():
        moves = solutions.get(pid)
        if not moves:
            report[pid] = {"status": "NO_SOL"}
            continue
        fen = prob["fen"].split()[0]
        turn = first_mover(moves) or prob.get("to_move", "w")
        sans = mainline_tokens(moves)
        n, _ = replay_sans(fen, turn, sans)
        ok = bool(sans) and n == len(sans)
        report[pid] = {"status": "OK" if ok else "ILLEGAL",
                       "detail": "" if ok else f"stops at ply {n + 1}"
                                               f" ({sans[n] if n < len(sans) else '-'})"}
    return report


def build_dataset(book: Path) -> list[dict]:
    problems = load_problems(book)
    solutions = load_solutions(book)
    if not problems and (book / "fens.json").exists():
        problems, solutions = load_native(book)
    audit = json.loads((book / "audit_report.json").read_text()) \
        if (book / "audit_report.json").exists() else replay_audit(problems, solutions)
    fen_fixes = load_jsonl(book / "fen_fixes.jsonl")
    move_fixes = load_jsonl(book / "moves_fixes.jsonl")
    overrides = load_jsonl(book / "human_overrides.jsonl")
    # Once a book has a bundle it is the single source of truth: it already
    # carries what verify decided (e.g. a corrected side to move). Re-deriving
    # those values here is how this panel drifted from the PGN build.
    bundle_state = load_jsonl(book / "bundle" / "state" / "puzzles.jsonl")
    images = image_index(book)

    rows = []
    for pid, prob in problems.items():
        if pid not in images:
            continue
        fix = fen_fixes.get(pid)
        ov = overrides.get(pid)
        fen = prob["fen"].split()[0]
        source = "original"
        candidates = []
        if fix and fix.get("status") in ("FIXED", "SHIFT_FIXED", "MOVES_SUSPECT") and fix.get("fen"):
            fen, source = fix["fen"], f"fix:{fix['status']}"
        if fix and fix.get("status") == "UNRESOLVED":
            source = "fix:UNRESOLVED"
            for label, key in (("原始识别", None), ("flash 读取", "fen_fast"),
                               ("pro 读取", "fen_strong")):
                cand = prob["fen"].split()[0] if key is None else fix.get(key)
                if cand and all(c["fen"] != cand for c in candidates):
                    candidates.append({"label": label, "fen": cand})
        bp = bundle_state.get(pid, {})
        if bp.get("fen"):
            fen = bp["fen"].split()[0]
        if ov and ov.get("fen"):        # newer than the last rebuild
            fen, source = ov["fen"], "human"

        rounds = (fix or {}).get("rounds")
        if ov:
            fen_prov = "人工修正" if ov.get("verdict") == "fixed" else "人工确认"
        elif source == "fix:UNRESOLVED":
            fen_prov = "两次模型读取不一致 — 用下方候选按钮对照原图选择"
        elif source.startswith("fix:"):
            fen_prov = {1: "重识别一轮即通过验证", 2: "重识别 + 可疑格放大重看",
                        3: "重识别 + 强模型仲裁"}.get(rounds, "重识别")
            if source == "fix:MOVES_SUSPECT":
                fen_prov += "（双模型一致；解答另行修复）"
        else:
            fen_prov = "原始识别（首轮 image model），未被后续修改"

        mfix = move_fixes.get(pid, {})
        moves_prov = {"REPAIRED": "解答经洞补全修复（引擎仲裁缺失着法）",
                      "REOCRED": "解答从书页原文重读并验证",
                      "UNREPAIRED": "解答修复失败，待人工/agent",
                      }.get(mfix.get("status"), "解答为原始 OCR")

        moves = solutions.get(pid)
        # precedence: human verdict > bundle state > automated repair > OCR
        eff_moves = (ov or {}).get("moves") or bp.get("moves") \
            or (mfix["moves"] if mfix.get("status") in ("REPAIRED", "REOCRED")
                and mfix.get("moves") else moves)
        turn = (ov or {}).get("to_move") or bp.get("to_move") \
            or (fix or {}).get("to_move") \
            or (first_mover(eff_moves) if eff_moves else None) or prob.get("to_move", "w")

        astat = audit.get(pid, {}).get("status", "?")
        mstat = move_fixes.get(pid, {}).get("status")
        # Where does the mainline actually die? Judge the same movetext the
        # export would use, so this panel never disagrees with the PGN build.
        # Judge exactly as the export does — bundle books through the same
        # three-tier PGN parse, native books (no bundle) by mainline replay,
        # the way their own verify step does.
        ok = False
        if eff_moves:
            if (book / "bundle").is_dir():
                ok = any(try_parse(fen, turn, mv) is not None
                         for mv in (eff_moves, transform_book_variations(eff_moves),
                                    strip_variations(eff_moves)))
            else:
                sans = mainline_tokens(normalize_movetext(eff_moves))
                n, _ = replay_sans(fen, turn, sans)
                ok = bool(sans) and n == len(sans)
        # only when it fails does the human need to know where it dies
        breaks_at = None
        if eff_moves and not ok:
            sans = mainline_tokens(normalize_movetext(eff_moves))
            n, board = replay_sans(fen, turn, sans)
            if sans and n < len(sans):
                cands = san_candidates(board, sans[n])
                breaks_at = {"ply": n + 1, "san": sans[n], "candidates": len(cands)}
        # Classify by outcome, never by which repair stage happened to run —
        # otherwise a fixed FEN marks a puzzle 'verified' while its solution
        # still fails, and the app disagrees with the PGN build.
        if not eff_moves:
            review = "no-solution"
        elif not ok:
            review = "unsolved" if ov else "needs-review"
        elif ov:
            review = "reviewed"
        else:
            review = "verified"

        verify_prov = {
            "OK": "✅ 解答完整重放通过", "VAR_ONLY": "✅ 主线重放通过（书籍变例记法非标准）",
            "TURN": "✅ 重放通过（先行方已修正）", "ILLEGAL": "❌ 解答从此局面重放失败",
            "STRUCT": "❌ FEN 结构错误", "PARSE": "❌ 解答文本损坏", "NO_SOL": "⚠️ 无解答可验证",
        }.get(astat, astat)
        if mfix.get("status") in ("REPAIRED", "REOCRED"):
            verify_prov = "✅ 解答修复后重放通过"
        if not ok and eff_moves and not breaks_at:
            verify_prov += " — 着法可重放，但整段文本无法解析为 PGN（多为粘连/评估符号），导出时退化为文本解答"
        if breaks_at:
            why = ("书中记法不完整：此局面下有 "
                   f"{breaks_at['candidates']} 个合法着法都写作这一步（需 R8e7 式消歧）"
                   if breaks_at["candidates"] > 1 else "在此局面下不合法（核对相关棋子）")
            verify_prov += (f" — 主线第 {breaks_at['ply']} 手 "
                            f"<b>{breaks_at['san']}</b> {why}")

        rows.append({
            "id": pid, "fen": fen, "to_move": turn, "source": source,
            "audit": astat, "review": review,
            "image": f"/img/{book.name}/{images[pid].name}",
            "moves": (moves or "")[:300],
            "verdict": (ov or {}).get("verdict"),
            "eff_moves": eff_moves or "",
            "breaks_at": breaks_at,
            "prov": {"fen": fen_prov, "verify": verify_prov, "moves": moves_prov},
            "candidates": candidates,
            "tier": tier_of(pid, moves, fix, ov),
        })

    def key(r: dict) -> tuple[int, int]:
        prio = {"needs-review": 0, "unsolved": 1, "no-solution": 2,
                "verified": 3, "reviewed": 4}
        return (prio.get(r["review"], 0), int(re.sub(r"\D", "", r["id"]) or 0))
    rows.sort(key=key)
    return rows


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FEN Review</title>
<style>
:root { --sq: 44px; --pal: 40px; }
body { font-family: -apple-system, sans-serif; margin: 0; background: #f4f2ee; color: #222; }
header { display: flex; gap: 16px; align-items: center; padding: 10px 16px;
         background: #2f2a25; color: #eee; flex-wrap: wrap; }
header select, header button { font-size: 14px; padding: 4px 8px; }
#stage { display: flex; gap: 24px; padding: 20px; justify-content: center; flex-wrap: wrap; }
.panel { background: #fff; border-radius: 10px; padding: 14px; box-shadow: 0 1px 6px #0002; }
.panel h3 { margin: 0 0 8px; font-size: 14px; color: #666; }
#left-panel { width: 470px; }
#right-panel { width: 500px; }
#diagram { width: 460px; height: 520px; object-fit: contain; display: block;
           background: #fafafa; border-radius: 6px; }
#boardrow { display: flex; gap: 10px; justify-content: center; align-items: center; }
#board { display: grid; grid-template-columns: repeat(8, var(--sq));
         border: 3px solid #4a3f33; width: fit-content; }
.sq { width: var(--sq); height: var(--sq); display: flex; align-items: center;
      justify-content: center; font-size: calc(var(--sq) * .78); cursor: pointer;
      user-select: none; line-height: 1; }
.light { background: #efe0c7; } .dark { background: #b58a5f; }
.sq.sel { outline: 3px solid #e33; outline-offset: -3px; }
.sq.diff { box-shadow: inset 0 0 0 3px #ff9800; }
.pc { width: 90%; height: 90%; pointer-events: none; }
.palcol { display: flex; flex-direction: column; gap: 5px; }
.palcol button { font-size: 22px; width: var(--pal); height: var(--pal); padding: 2px;
                 cursor: pointer; background: #eee; border: 1px solid #bbb;
                 border-radius: 6px; display: flex; align-items: center;
                 justify-content: center; }
.palcol button.active { background: #ffd76e; border-color: #b98; }
#fen, #soltext { width: 100%; font-family: monospace; font-size: 13px; padding: 6px;
                 box-sizing: border-box; }
#solbox { display: none; margin-top: 8px; }
#solbox label { display: block; font-size: 12px; color: #777; margin-bottom: 3px; }
#moves { font-size: 12px; color: #555; max-width: 480px; white-space: pre-wrap; margin-top: 8px; }
.actions { display: flex; gap: 10px; margin-top: 12px; }
.actions button { font-size: 15px; padding: 8px 14px; border-radius: 8px; border: 0; cursor: pointer; }
.actions button:disabled { opacity: .35; cursor: default; }
#ok { background: #2e7d32; color: #fff; } #fix { background: #1565c0; color: #fff; }
#skip { background: #757575; color: #fff; }
#meta { font-size: 13px; color: #777; }
/* outline-only so it stays legible on light header buttons and on the
   coloured action buttons alike */
kbd { font: inherit; font-size: .8em; line-height: 1.4; padding: 0 4px; margin-left: 5px;
      border: 1px solid currentColor; border-radius: 4px; opacity: .55; }
@media (max-width: 700px) {
  /* palettes flank the board here too: leave room for two 34px columns */
  :root { --sq: min(calc((100vw - 116px) / 8), 34px); --pal: 34px; }
  header { padding: 6px 8px; gap: 8px; font-size: 14px; }
  #stage { padding: 4px; gap: 6px; }
  .panel { padding: 8px; }
  #left-panel, #right-panel { width: 100%; box-sizing: border-box; }
  .panel h3 { margin: 0 0 4px; font-size: 12px; }
  #diagram { width: auto; max-width: 100%; height: auto; max-height: 30vh; margin: 0 auto; }
  /* no height cap: the smaller board leaves room to show the whole panel */
  #prov { font-size: 11px; line-height: 1.45; padding: 5px 7px; margin: 5px 0; }
  #moves { display: none; }
  #boardrow { gap: 6px; }
  .palcol { gap: 4px; }
  .palcol button { font-size: 18px; }
  #fen { font-size: 11px; padding: 4px; }
  .actions { margin-top: 6px; gap: 6px; flex-wrap: nowrap; }
  .actions button { font-size: 13px; padding: 8px 8px; white-space: nowrap; flex: 1; }
  kbd { display: none; }
}
</style></head><body>
<header>
  <b>FEN Review</b>
  <select id="book"></select>
  <select id="filter">
    <option value="needs-review">待复核</option>
    <option value="unsolved">已复核但解答仍不通过</option>
    <option value="no-solution">无解答</option>
    <option value="verified">已机器验证</option>
    <option value="reviewed">已人工复核</option>
    <option value="spotcheck">抽查队列</option>
    <option value="all">全部</option>
  </select>
  <span id="pos"></span>
  <button onclick="nav(-1)">◀ 上一题 <kbd>←</kbd></button>
  <button onclick="nav(1)">下一题 ▶ <kbd>→</kbd></button>
  <span id="meta"></span>
</header>
<div id="stage">
  <div class="panel" id="left-panel"><h3>原图</h3><img id="diagram"></div>
  <div class="panel" id="right-panel">
    <h3>识别结果（可编辑 — 不选调色板时:点棋子再点目标格=搬移）</h3>
    <div id="boardrow">
      <div class="palcol" id="pal-black"></div>
      <div id="board"></div>
      <div class="palcol" id="pal-white"></div>
    </div>
    <div id="prov" style="font-size:13px;background:#f7f4ed;border-radius:6px;
         padding:8px 10px;margin:8px 0;line-height:1.6"></div>
    <div id="cands" style="margin:6px 0"></div>
    <input id="fen" spellcheck="false">
    <div style="margin-top:6px">
      先行方:
      <label><input type="radio" name="turn" value="w"> 白</label>
      <label><input type="radio" name="turn" value="b"> 黑</label>
    </div>
    <div id="solbox">
      <label for="soltext">解答着法（改完按「保存修正」，保存前会先验证能否重放）</label>
      <textarea id="soltext" spellcheck="false" rows="3"></textarea>
    </div>
    <div class="actions">
      <button id="ok" onclick="save('ok')">✓ 正确 <kbd>Enter</kbd></button>
      <button id="fix" onclick="save('fixed')">保存修正</button>
      <button id="skip" onclick="nav(1)">忽略跳过 <kbd>→</kbd></button>
    </div>
    <div id="moves"></div>
  </div>
</div>
<script>
const GLYPH = {K:'♔',Q:'♕',R:'♖',B:'♗',N:'♘',P:'♙',
               k:'♚',q:'♛',r:'♜',b:'♝',n:'♞',p:'♟'};
let data = [], view = [], idx = 0, board = {}, brush = null, sel = null, dirty = false;
let marks = new Set(), solOriginal = '';

function setDirty(d) {
  dirty = d;
  document.getElementById('fix').disabled = !d;
  document.getElementById('ok').disabled = d;
}

function fenToBoard(fen) {
  const b = {}; const rows = fen.split('/'); let ok = rows.length === 8;
  rows.forEach((row, r) => { let f = 0;
    for (const ch of row) { if (/\d/.test(ch)) f += +ch;
      else { b[String.fromCharCode(97+f) + (8-r)] = ch; f++; } }
    if (f !== 8) ok = false; });
  return ok ? b : null;
}
function boardToFen() {
  let rows = [];
  for (let r = 8; r >= 1; r--) { let row = '', e = 0;
    for (let f = 0; f < 8; f++) { const p = board[String.fromCharCode(97+f)+r];
      if (p) { row += (e||'') + p; e = 0; } else e++; }
    rows.push(row + (e||'')); }
  return rows.join('/');
}
function drawBoard() {
  const el = document.getElementById('board'); el.innerHTML = '';
  for (let r = 8; r >= 1; r--) for (let f = 0; f < 8; f++) {
    const sq = String.fromCharCode(97+f)+r, p = board[sq];
    const d = document.createElement('div');
    d.className = 'sq ' + ((r+f)%2 ? 'dark' : 'light');  // a1 (r=1,f=0) must be dark
    if (p) { const c = (p === p.toUpperCase() ? 'w' : 'b') + p.toUpperCase();
      d.innerHTML = `<img class="pc" src="/pieces/${c}.svg" draggable="false">`; }
    if (sq === sel) d.classList.add('sel');
    if (marks.has(sq)) d.classList.add('diff');
    d.title = sq;
    d.onclick = () => {
      if (brush === 'x') { delete board[sq]; sel = null; }
      else if (brush) { board[sq] = brush; sel = null; }
      else if (sel === null) {          // no brush: pick up a piece to move it
        if (!board[sq]) return;
        sel = sq; drawBoard(); return;
      } else if (sel === sq) { sel = null; drawBoard(); return; }
      else { board[sq] = board[sel]; delete board[sel]; sel = null; }
      document.getElementById('fen').value = boardToFen(); setDirty(true); drawBoard(); };
    el.appendChild(d);
  }
}
function drawPalette() {
  // black pieces flank the board on the left, white on the right (+ eraser)
  for (const [id, pieces] of [['pal-black', ['k','q','r','b','n','p']],
                              ['pal-white', ['K','Q','R','B','N','P','x']]]) {
    const el = document.getElementById(id); el.innerHTML = '';
    for (const p of pieces) {
      const b = document.createElement('button');
      if (p === 'x') { b.textContent = '⌫'; b.title = '清除格子'; }
      else { const c = (p === p.toUpperCase() ? 'w' : 'b') + p.toUpperCase();
        b.innerHTML = `<img class="pc" src="/pieces/${c}.svg" draggable="false">`; }
      b.onclick = () => { brush = brush === p ? null : p; drawPalette(); };
      if (brush === p) b.classList.add('active');
      el.appendChild(b);
    }
  }
}
function show() {
  if (!view.length) { document.getElementById('pos').textContent = '此类别为空'; return; }
  idx = Math.max(0, Math.min(idx, view.length - 1));
  const it = view[idx];
  document.getElementById('pos').textContent = `#${it.id}  (${idx+1}/${view.length})`;
  document.getElementById('meta').textContent =
    `audit=${it.audit} source=${it.source}` + (it.verdict ? ` verdict=${it.verdict}` : '');
  document.getElementById('diagram').src = it.image;
  document.getElementById('fen').value = it.fen;
  board = fenToBoard(it.fen) || {};
  document.querySelectorAll('input[name=turn]').forEach(x => x.checked = x.value === it.to_move);
  document.getElementById('moves').textContent = it.moves ? '解答: ' + it.moves : '(无解答)';
  const pv = it.prov || {};
  let provHtml =
    `<b>置信档:</b> ${it.tier || '?'}<br><b>FEN 来源:</b> ${pv.fen || '?'}<br><b>验证:</b> ${pv.verify || '?'}<br><b>解答:</b> ${pv.moves || '?'}`;
  marks = new Set();
  for (const c of (it.candidates || [])) {
    const cb = fenToBoard(c.fen);
    if (!cb) continue;
    for (let r = 1; r <= 8; r++) for (let f = 0; f < 8; f++) {
      const sq = String.fromCharCode(97+f)+r;
      if ((cb[sq] || '') !== (board[sq] || '')) marks.add(sq);
    }
  }
  if (marks.size) {
    provHtml += `<br><b style="color:#e65100">🟧 模型读取分歧格 (${marks.size}):</b> ` +
                [...marks].sort().join(', ') + ' — 重点核对这些格子';
  } else if ((it.candidates || []).length) {
    provHtml += '<br>各候选读取一致';
  }
  document.getElementById('prov').innerHTML = provHtml;
  const cd = document.getElementById('cands'); cd.innerHTML = '';
  for (const c of (it.candidates || [])) {
    const b = document.createElement('button');
    b.textContent = '载入: ' + c.label; b.style.marginRight = '6px';
    b.onclick = () => { document.getElementById('fen').value = c.fen;
      board = fenToBoard(c.fen) || board; sel = null; setDirty(true); drawBoard(); };
    cd.appendChild(b);
  }
  // the solution editor only appears when the mainline actually fails
  const solbox = document.getElementById('solbox');
  solOriginal = it.eff_moves || '';
  document.getElementById('soltext').value = solOriginal;
  solbox.style.display = it.breaks_at ? 'block' : 'none';
  drawBoard(); drawPalette(); setDirty(false);
}
function applyFilter() {
  const f = document.getElementById('filter').value;
  view = f === 'all' ? data
       : f === 'spotcheck' ? data.filter(r => r.tier === 'LOW' || r.tier === 'MEDIUM')
       : data.filter(r => r.review === f);
  idx = 0; show();
}
function nav(d) { idx += d; show(); }
async function save(verdict) {
  const it = view[idx]; if (!it) return;
  const fen = document.getElementById('fen').value.trim();
  const turn = document.querySelector('input[name=turn]:checked')?.value || it.to_move;
  const body = { id: it.id, verdict, fen, to_move: turn, book: currentBook() };
  const st = document.getElementById('soltext');
  if (document.getElementById('solbox').style.display !== 'none'
      && st.value.trim() && st.value.trim() !== solOriginal.trim()) {
    body.moves = st.value.trim();
  }
  const r = await fetch('/api/save', { method: 'POST', body: JSON.stringify(body) });
  if (!r.ok) { alert('保存失败: ' + await r.text()); return; }
  it.verdict = verdict; it.review = 'reviewed'; it.fen = fen; it.to_move = turn;
  nav(1);
}
document.getElementById('fen').addEventListener('change', e => {
  const b = fenToBoard(e.target.value.trim());
  if (b) { board = b; setDirty(true); drawBoard(); } else alert('FEN 无法解析');
});
document.querySelectorAll('input[name=turn]').forEach(x => x.onchange = () => setDirty(true));
document.getElementById('soltext').addEventListener('input', () => setDirty(true));
document.addEventListener('keydown', e => {
  if (e.target.id === 'fen') return;
  if (e.target.id === 'soltext') {          // typing a solution: only Enter saves
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) save('fixed');
    return;
  }
  if (e.key === 'ArrowLeft') nav(-1);
  else if (e.key === 'ArrowRight') nav(1);
  // Enter always saves: the verdict follows whether anything was edited.
  // (It used to be swallowed once the form was dirty, so a typed correction
  // could be lost by navigating away.)
  else if (e.key === 'Enter') save(dirty ? 'fixed' : 'ok');
});
document.getElementById('filter').onchange = applyFilter;

function currentBook() { return document.getElementById('book').value; }
async function loadBook() {
  document.getElementById('pos').textContent = '载入中…';
  const r = await fetch('/api/puzzles?book=' + encodeURIComponent(currentBook()));
  data = await r.json();
  document.title = 'FEN Review — ' + currentBook();
  applyFilter();
}
document.getElementById('book').onchange = loadBook;
fetch('/api/books').then(r => r.json()).then(books => {
  const sel = document.getElementById('book');
  sel.innerHTML = '';
  for (const b of books) {
    const o = document.createElement('option');
    o.value = b.name; o.textContent = `${b.name} (${b.puzzles})`;
    if (b.current) o.selected = true;
    sel.appendChild(o);
  }
  loadBook();
});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not ctype.startswith("image/"):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def book_for(self, name: str | None) -> Path | None:
        """Resolve a book name from the request against the discovered set —
        never trust the path in a URL."""
        books = discover_books()
        return books.get(name or BOOK.name)

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        params = parse_qs(query)
        if path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/api/books":
            books = discover_books()
            out = [{"name": n, "puzzles": len(image_index(d)),
                    "current": n == BOOK.name} for n, d in books.items()]
            self._send(200, json.dumps(out).encode(), "application/json")
        elif path == "/api/puzzles":
            book = self.book_for((params.get("book") or [None])[0])
            if book is None:
                self._send(404, b"unknown book", "text/plain")
                return
            self._send(200, json.dumps(build_dataset(book)).encode(), "application/json")
        elif path.startswith("/pieces/"):
            name = Path(path[8:]).name
            # pieces ship with the package; a book folder may override them
            for f in (BOOK / "pieces" / name, PKG / "pieces" / name):
                if f.exists():
                    self._send(200, f.read_bytes(), "image/svg+xml")
                    return
            self._send(404, b"not found", "text/plain")
        elif path.startswith("/img/"):
            parts = path[5:].split("/")
            book = self.book_for(parts[0]) if len(parts) > 1 else self.book_for(None)
            f = (book / "problem_images" / Path(parts[-1]).name) if book else None
            if f and f.exists():
                self._send(200, f.read_bytes(), "image/png")
            else:
                self._send(404, b"not found", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if self.path != "/api/save":
            self._send(404, b"not found", "text/plain")
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            pid, verdict = str(body["id"]), body["verdict"]
            fen = str(body.get("fen", "")).split()[0]
            book = self.book_for(body.get("book"))
            if book is None:
                raise ValueError(f"unknown book {body.get('book')!r}")
            if verdict not in ("ok", "fixed", "exclude"):
                raise ValueError(f"bad verdict {verdict!r}")
            if verdict != "exclude" and structural_check(fen) is not None:
                raise ValueError(f"FEN failed checks: {structural_check(fen)}")
            rec = {"id": pid, "verdict": verdict, "fen": fen,
                   "to_move": body.get("to_move", "w")}
            # a corrected solution is only accepted if it replays — the same
            # oracle the pipeline uses, applied at the point of entry
            moves = (body.get("moves") or "").strip()
            if moves:
                sans = mainline_tokens(normalize_movetext(moves))
                n, _ = replay_sans(fen, rec["to_move"], sans)
                if not sans:
                    raise ValueError("解答里没有可识别的着法")
                if n < len(sans):
                    raise ValueError(f"第 {n + 1} 手 {sans[n]} 走不通，解答未保存")
                rec["moves"] = moves
            with (book / "human_overrides.jsonl").open("a") as f:
                f.write(json.dumps(rec) + "\n")
            self._send(200, b"ok", "text/plain")
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._send(400, str(exc).encode(), "text/plain")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--book", default=None, help="book folder (consumed pre-import)")
    parser.add_argument("--books-root", default=None,
                        help="folder holding all books (default: the book's parent); "
                             "every book found there is selectable in the UI")
    args = parser.parse_args()
    if args.books_root:
        global BOOKS_ROOT
        BOOKS_ROOT = Path(args.books_root).resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    names = ", ".join(discover_books())
    print(f"Review app: http://{args.host}:{args.port}/  (Ctrl-C to stop)")
    print(f"books in {BOOKS_ROOT}: {names}  (default: {BOOK.name})")
    server.serve_forever()


if __name__ == "__main__":
    main()
