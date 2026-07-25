"""Phase 2.5: the audit log read endpoint and the projector-facing dashboard.

The audit log is the demo's closing artifact -- the thing that turns "our
agents respected their principals' permissions" from a claim into something a
judge can read off a screen. So the page leads with the two principals' real
permissions (fetched from GitHub as each identity, see principals.py) and then
shows every check performed, with the identity each call actually ran as.

Routes:
  GET /                       the dashboard
  GET /audit-log              rows, optionally ?session_id=
  GET /principals            the permission matrix
  GET /latency               Groq numbers, if perception has written any

Run:  python -m enforcement.dashboard        (serves on :8000)

Degradation is deliberate. `sessions`, `bugs`, and the Groq latency numbers
belong to perception and do not exist in this branch's database. Every read of
them is guarded so the page renders correctly on a machine where only
audit_log exists -- which is the machine this was built on.
"""

import json
import sqlite3

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from enforcement.db import get_connection
from enforcement.principals import load_cached

# Not in main's shared schema. The build card asks for Groq latency numbers on
# the dashboard and says perception writes them to the shared store in Phase
# 1.6, but no table for them was ever specified. Guessed name, guarded read,
# and flagged in HANDOFF.md rather than silently assumed.
LATENCY_TABLE = "latencies"


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def fetch_audit_log(session_id: str | None = None) -> list[dict]:
    conn = get_connection()
    if session_id:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE session_id IS ? ORDER BY id", (session_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def fetch_latency() -> dict:
    """Groq latency numbers, if perception's side of the store exists yet."""
    conn = get_connection()
    try:
        rows = conn.execute(f"SELECT * FROM {LATENCY_TABLE} ORDER BY id DESC LIMIT 10").fetchall()
        return {"available": True, "rows": _rows_to_dicts(rows)}
    except sqlite3.OperationalError:
        return {
            "available": False,
            "note": f"no `{LATENCY_TABLE}` table yet -- perception writes these in its Phase 1.6",
        }
    finally:
        conn.close()


async def audit_log_endpoint(request):
    return JSONResponse(fetch_audit_log(request.query_params.get("session_id")))


async def principals_endpoint(request):
    data = load_cached()
    if data is None:
        return JSONResponse(
            {"available": False, "note": "run `python -m enforcement.principals` to fetch"},
            status_code=200,
        )
    return JSONResponse(data)


async def latency_endpoint(request):
    return JSONResponse(fetch_latency())


async def index(request):
    return HTMLResponse(PAGE)


PAGE = """
<!doctype html>
<meta charset="utf-8">
<title>Night Shift — audit log</title>
<style>
  :root { color-scheme: dark; }
  body { background:#0d1117; color:#e6edf3; font:16px/1.5 ui-sans-serif,system-ui,sans-serif;
         margin:0; padding:32px 40px; }
  h1 { font-size:28px; margin:0 0 4px; letter-spacing:-.02em; }
  .sub { color:#8b949e; margin-bottom:28px; }
  h2 { font-size:15px; text-transform:uppercase; letter-spacing:.08em; color:#8b949e;
       margin:32px 0 12px; font-weight:600; }
  table { border-collapse:collapse; width:100%; font-size:15px; }
  th { text-align:left; color:#8b949e; font-weight:600; padding:8px 12px;
       border-bottom:1px solid #30363d; font-size:13px; text-transform:uppercase;
       letter-spacing:.05em; }
  td { padding:9px 12px; border-bottom:1px solid #21262d; vertical-align:top; }
  tr:last-child td { border-bottom:none; }
  code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:14px; }
  .pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:13px;
          font-weight:600; }
  .allow { background:#12261e; color:#3fb950; }
  .deny { background:#2b1416; color:#f85149; }
  .escalated { background:#2b2213; color:#d29922; }
  .write { color:#3fb950; font-weight:600; }
  .read { color:#8b949e; }
  .none { color:#f85149; }
  .ident { color:#79c0ff; font-weight:600; }
  a { color:#79c0ff; }
  .muted { color:#6e7681; font-style:italic; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px; }
  .card { border:1px solid #30363d; border-radius:8px; padding:16px 18px; }
  .card h3 { margin:0 0 2px; font-size:17px; }
  .login { color:#8b949e; font-size:14px; margin-bottom:12px; }
  .perm { display:flex; justify-content:space-between; padding:4px 0; font-size:14px; }
  .wrap { overflow-x:auto; }
</style>

<h1>Night Shift</h1>
<div class="sub">Every tool call below executed as a real engineer's own GitHub credentials.</div>

<h2>Principals and their real permissions</h2>
<div id="principals" class="grid"><span class="muted">loading…</span></div>

<h2>Audit log</h2>
<div class="wrap"><table>
  <thead><tr>
    <th>Bug</th><th>Proposing</th><th>Responding</th><th>Ran as</th>
    <th>Decision</th><th>Reason</th><th>Commit</th><th>Time</th>
  </tr></thead>
  <tbody id="log"><tr><td colspan="8" class="muted">loading…</td></tr></tbody>
</table></div>

<h2>Groq latency</h2>
<div id="latency" class="muted">loading…</div>

<script>
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function load() {
  const [log, principals, latency] = await Promise.all([
    fetch('/audit-log').then(r => r.json()),
    fetch('/principals').then(r => r.json()),
    fetch('/latency').then(r => r.json()),
  ]);

  const pc = document.getElementById('principals');
  if (principals.available === false) {
    pc.innerHTML = '<span class="muted">' + esc(principals.note) + '</span>';
  } else {
    const repos = principals.repos || [];
    pc.innerHTML = Object.values(principals.principals || {}).map(p => `
      <div class="card">
        <h3>${esc(p.identifier)}</h3>
        <div class="login">GitHub: ${esc(p.github_login || 'unknown')} · ${esc(p.agent_id)}</div>
        ${repos.map(r => {
          const lvl = (p.repos || {})[r] || 'unknown';
          return `<div class="perm"><span>${esc(r)}</span>
                  <span class="${esc(lvl)}">${esc(lvl)}</span></div>`;
        }).join('')}
      </div>`).join('');
  }

  const tb = document.getElementById('log');
  tb.innerHTML = log.length ? log.map(r => `
    <tr>
      <td><code>${esc(r.bug_id)}</code></td>
      <td>${esc(r.proposing_agent || '—')}</td>
      <td>${esc(r.responding_agent || '—')}</td>
      <td class="ident">${esc(r.identity_used || '—')}</td>
      <td><span class="pill ${esc(r.decision)}">${esc(r.decision)}</span></td>
      <td>${esc(r.reason)}</td>
      <td>${r.commit_url ? `<a href="${esc(r.commit_url)}" target="_blank">commit ↗</a>` : '—'}</td>
      <td class="muted">${esc((r.timestamp || '').slice(11, 19))}</td>
    </tr>`).join('')
    : '<tr><td colspan="8" class="muted">no checks recorded yet</td></tr>';

  const lc = document.getElementById('latency');
  if (!latency.available) {
    lc.className = 'muted';
    lc.textContent = latency.note;
  } else {
    lc.className = '';
    lc.innerHTML = latency.rows.map(r => `<code>${esc(JSON.stringify(r))}</code>`).join('<br>');
  }
}

load();
setInterval(load, 2000);
</script>
"""

app = Starlette(routes=[
    Route("/", index),
    Route("/audit-log", audit_log_endpoint),
    Route("/principals", principals_endpoint),
    Route("/latency", latency_endpoint),
])


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
