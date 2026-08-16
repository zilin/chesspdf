#!/usr/bin/env python3
"""Human review web app: original diagram vs. rendered FEN, side by side.

  <python-with-python-chess> review_app.py [--port 8899]

Left: the cropped book diagram. Right: an editable board rendered from the
currently recognized FEN. Click a piece in the palette then click squares to
place it (eraser to clear); or edit the FEN text directly. Verdicts append to
human_overrides.jsonl, which build_pgn.py honors above every other source.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import os as _os
if "--book" in __import__("sys").argv:
    _i = __import__("sys").argv.index("--book")
    _os.environ["CHESSPDF_BOOK"] = str(Path(__import__("sys").argv[_i + 1]).resolve())
_os.environ.setdefault("CHESSPDF_BOOK", str(Path("books/imagination").resolve()))
BOOK = Path(_os.environ["CHESSPDF_BOOK"])

from chesspdf.audit import load_problems, load_solutions
from chesspdf.chesslib import first_mover, structural_check

HERE = BOOK
PKG = Path(__file__).resolve().parent          # bundled piece SVGs live here
OVERRIDES = BOOK / "human_overrides.jsonl"


def image_index() -> dict[str, Path]:
    return {p.stem.replace("*", ""): p
            for p in sorted((HERE / "problem_images").glob("*.png"))}


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


def build_dataset() -> list[dict]:
    problems = load_problems()
    solutions = load_solutions()
    audit = json.loads((HERE / "audit_report.json").read_text()) \
        if (HERE / "audit_report.json").exists() else {}
    fen_fixes = load_jsonl(HERE / "fen_fixes.jsonl")
    move_fixes = load_jsonl(HERE / "moves_fixes.jsonl")
    overrides = load_jsonl(OVERRIDES)
    images = image_index()

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
        if ov and ov.get("fen"):
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
        turn = (ov or {}).get("to_move") or (fix or {}).get("to_move") \
            or (first_mover(moves) if moves else None) or prob.get("to_move", "w")

        astat = audit.get(pid, {}).get("status", "?")
        mstat = move_fixes.get(pid, {}).get("status")
        if ov:
            review = "reviewed"
        elif astat in ("OK", "VAR_ONLY", "TURN") \
                or (fix or {}).get("status") in ("FIXED", "SHIFT_FIXED") \
                or mstat in ("REPAIRED", "REOCRED"):
            review = "verified"
        elif astat == "NO_SOL":
            review = "no-solution"
        else:
            review = "needs-review"

        verify_prov = {
            "OK": "✅ 解答完整重放通过", "VAR_ONLY": "✅ 主线重放通过（书籍变例记法非标准）",
            "TURN": "✅ 重放通过（先行方已修正）", "ILLEGAL": "❌ 解答从此局面重放失败",
            "STRUCT": "❌ FEN 结构错误", "PARSE": "❌ 解答文本损坏", "NO_SOL": "⚠️ 无解答可验证",
        }.get(astat, astat)
        if mfix.get("status") in ("REPAIRED", "REOCRED"):
            verify_prov = "✅ 解答修复后重放通过"

        rows.append({
            "id": pid, "fen": fen, "to_move": turn, "source": source,
            "audit": astat, "review": review,
            "image": f"/img/{images[pid].name}",
            "moves": (moves or "")[:300],
            "verdict": (ov or {}).get("verdict"),
            "prov": {"fen": fen_prov, "verify": verify_prov, "moves": moves_prov},
            "candidates": candidates,
            "tier": tier_of(pid, moves, fix, ov),
        })

    def key(r: dict) -> tuple[int, int]:
        prio = {"needs-review": 0, "no-solution": 1, "verified": 2, "reviewed": 3}
        return (prio.get(r["review"], 0), int(re.sub(r"\D", "", r["id"]) or 0))
    rows.sort(key=key)
    return rows


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FEN Review — __BOOK__</title>
<style>
:root { --sq: 56px; }
body { font-family: -apple-system, sans-serif; margin: 0; background: #f4f2ee; color: #222; }
header { display: flex; gap: 16px; align-items: center; padding: 10px 16px;
         background: #2f2a25; color: #eee; flex-wrap: wrap; }
header select, header button { font-size: 14px; padding: 4px 8px; }
#stage { display: flex; gap: 24px; padding: 20px; justify-content: center; flex-wrap: wrap; }
.panel { background: #fff; border-radius: 10px; padding: 14px; box-shadow: 0 1px 6px #0002; }
.panel h3 { margin: 0 0 8px; font-size: 14px; color: #666; }
#left-panel { width: 470px; }
#right-panel { width: 490px; }
#diagram { width: 460px; height: 520px; object-fit: contain; display: block;
           background: #fafafa; border-radius: 6px; }
#board { display: grid; grid-template-columns: repeat(8, var(--sq));
         border: 3px solid #4a3f33; width: fit-content; margin: 0 auto; }
.sq { width: var(--sq); height: var(--sq); display: flex; align-items: center;
      justify-content: center; font-size: calc(var(--sq) * .78); cursor: pointer;
      user-select: none; line-height: 1; }
.light { background: #efe0c7; } .dark { background: #b58a5f; }
.sq.sel { outline: 3px solid #e33; outline-offset: -3px; }
.sq.diff { box-shadow: inset 0 0 0 3px #ff9800; }
.pc { width: 90%; height: 90%; pointer-events: none; }
#palette { display: flex; gap: 6px; margin: 10px 0; flex-wrap: wrap; }
#palette button { font-size: 26px; width: 44px; height: 44px; cursor: pointer;
                  background: #eee; border: 1px solid #bbb; border-radius: 6px; }
#palette button.active { background: #ffd76e; border-color: #b98; }
#fen { width: 100%; font-family: monospace; font-size: 13px; padding: 6px; box-sizing: border-box; }
#moves { font-size: 12px; color: #555; max-width: 480px; white-space: pre-wrap; margin-top: 8px; }
.actions { display: flex; gap: 10px; margin-top: 12px; }
.actions button { font-size: 15px; padding: 8px 14px; border-radius: 8px; border: 0; cursor: pointer; }
.actions button:disabled { opacity: .35; cursor: default; }
#ok { background: #2e7d32; color: #fff; } #fix { background: #1565c0; color: #fff; }
#skip { background: #757575; color: #fff; }
#meta { font-size: 13px; color: #777; }
kbd { background: #eee; border-radius: 3px; padding: 1px 5px; font-size: 12px; }
@media (max-width: 700px) {
  :root { --sq: min(calc((100vw - 48px) / 8), 34px); }
  header { padding: 6px 8px; gap: 8px; font-size: 14px; }
  #stage { padding: 4px; gap: 6px; }
  .panel { padding: 8px; }
  #left-panel, #right-panel { width: 100%; box-sizing: border-box; }
  .panel h3 { margin: 0 0 4px; font-size: 12px; }
  #diagram { width: auto; max-width: 100%; height: auto; max-height: 30vh; margin: 0 auto; }
  #prov { max-height: 3em; overflow-y: auto; font-size: 11px; padding: 4px 6px; margin: 4px 0; }
  #moves { display: none; }
  #palette { gap: 4px; margin: 6px 0; }
  #palette button { width: 40px; height: 40px; font-size: 24px; }
  #fen { font-size: 11px; padding: 4px; }
  .actions { margin-top: 6px; gap: 6px; flex-wrap: nowrap; }
  .actions button { font-size: 13px; padding: 8px 8px; white-space: nowrap; flex: 1; }
  kbd { display: none; }
}
</style></head><body>
<header>
  <b>FEN Review · __BOOK__</b>
  <select id="filter">
    <option value="needs-review">待复核</option>
    <option value="no-solution">无解答</option>
    <option value="verified">已机器验证</option>
    <option value="reviewed">已人工复核</option>
    <option value="spotcheck">抽查队列</option>
    <option value="all">全部</option>
  </select>
  <span id="pos"></span>
  <button onclick="nav(-1)">◀ 上一题 <kbd>←</kbd></button>
  <button onclick="nav(1)">下一题 <kbd>→</kbd></button>
  <span id="meta"></span>
</header>
<div id="stage">
  <div class="panel" id="left-panel"><h3>原图</h3><img id="diagram"></div>
  <div class="panel" id="right-panel">
    <h3>识别结果（可编辑 — 不选调色板时:点棋子再点目标格=搬移）</h3>
    <div id="board"></div>
    <div id="palette"></div>
    <div id="prov" style="font-size:13px;background:#f7f4ed;border-radius:6px;
         padding:8px 10px;margin:8px 0;line-height:1.6"></div>
    <div id="cands" style="margin:6px 0"></div>
    <input id="fen" spellcheck="false">
    <div style="margin-top:6px">
      先行方:
      <label><input type="radio" name="turn" value="w"> 白</label>
      <label><input type="radio" name="turn" value="b"> 黑</label>
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
let marks = new Set();

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
  const el = document.getElementById('palette'); el.innerHTML = '';
  for (const p of ['K','Q','R','B','N','P','k','q','r','b','n','p','x']) {
    const b = document.createElement('button');
    if (p === 'x') b.textContent = '⌫';
    else { const c = (p === p.toUpperCase() ? 'w' : 'b') + p.toUpperCase();
      b.innerHTML = `<img class="pc" src="/pieces/${c}.svg" draggable="false">`; }
    b.onclick = () => { brush = brush === p ? null : p; drawPalette(); };
    if (brush === p) b.classList.add('active');
    el.appendChild(b);
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
  const body = { id: it.id, verdict, fen, to_move: turn };
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
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' && e.target.id === 'fen') return;
  if (e.key === 'ArrowLeft') nav(-1);
  else if (e.key === 'ArrowRight') nav(1);
  else if (e.key === 'Enter' && !dirty) save('ok');
});
document.getElementById('filter').onchange = applyFilter;
fetch('/api/puzzles').then(r => r.json()).then(d => { data = d; applyFilter(); });
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

    def do_GET(self) -> None:
        if self.path == "/":
            self._send(200, PAGE.replace("__BOOK__", BOOK.name).encode(),
                       "text/html; charset=utf-8")
        elif self.path == "/api/puzzles":
            self._send(200, json.dumps(build_dataset()).encode(), "application/json")
        elif self.path.startswith("/pieces/"):
            name = Path(self.path[8:]).name
            # pieces ship with the package; a book folder may override them
            for f in (HERE / "pieces" / name, PKG / "pieces" / name):
                if f.exists():
                    self._send(200, f.read_bytes(), "image/svg+xml")
                    return
            self._send(404, b"not found", "text/plain")
        elif self.path.startswith("/img/"):
            name = Path(self.path[5:]).name
            f = HERE / "problem_images" / name
            if f.exists():
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
            if verdict not in ("ok", "fixed", "exclude"):
                raise ValueError(f"bad verdict {verdict!r}")
            if verdict != "exclude" and structural_check(fen) is not None:
                raise ValueError(f"FEN failed checks: {structural_check(fen)}")
            rec = {"id": pid, "verdict": verdict, "fen": fen,
                   "to_move": body.get("to_move", "w")}
            with OVERRIDES.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            self._send(200, b"ok", "text/plain")
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._send(400, str(exc).encode(), "text/plain")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--book", default=None, help="book folder (consumed pre-import)")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Review app [{BOOK.name}]: http://{args.host}:{args.port}/  (Ctrl-C to stop)")
    server.serve_forever()


if __name__ == "__main__":
    main()
