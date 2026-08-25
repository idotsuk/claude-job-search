#!/usr/bin/env python3
"""
Interactive one-at-a-time review for the `To Apply` queue.

Serves a local card UI at http://localhost:{port}/ : Keep leaves the listing
queued (stamps `reviewed:`); Decline picks a reason and demotes the listing
to `status: Skipped`, writing `decline_reason:` and a Communications row —
same surgical-frontmatter-edit approach as mark_stale.py, reused directly
from there rather than re-implemented.

Every decision also appends a structured entry (with a stable `id`) to
data/decline-log.yaml so a future /job-search run can spot repeat patterns
(e.g. three "company_fit" declines at one company) and ask about adding a
config.yaml filter — see scripts/triage_suggestions.py, which reads and
tags these entries. This script only ever appends/pops entries here;
nothing in this file edits config.yaml.

Run with: python3 scripts/triage_server.py [--port N] [--no-browser]
The session ends when you click Finish in the page (or Ctrl-C).
"""

import argparse
import datetime
import json
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import lib
from mark_stale import upsert_field, set_status, append_comm_row, split_frontmatter

LISTINGS_DIR = lib.data_root() / 'listings'
DECLINE_LOG = lib.data_root() / 'decline-log.yaml'

DECLINE_REASONS = [
    ('company_fit', "Don't want to work at this company"),
    ('role_fit', 'Role/title not of interest'),
    ('stack_gap', 'Tech-stack gap'),
    ('other', 'Other'),
]
DECLINE_LABELS = dict(DECLINE_REASONS)

DECLINE_LOG_HEADER = (
    '# Structured decline history from /triage.\n'
    '# Read by scripts/triage_suggestions.py, which job-search SKILL.md uses\n'
    '# to spot repeat patterns (e.g. 3+ declines at one company) and ask\n'
    '# about a config.yaml filter — advisory only, never auto-applied.\n'
    '# Entries tagged `suggested: true` have already been asked about; a\n'
    '# pattern only re-triggers once fresh (untagged) evidence lands.\n\n'
)

# In-memory, single most-recent decision — enough for a one-level Undo in a
# local, single-viewer review session. Not persisted across server restarts.
_last_action = None

# Session-only tally, printed when the server stops — the skill relays this
# back to the user without re-scanning the listings tree.
_session_counts = {'kept': 0, 'declined': 0}


def load_queue():
    """Every `To Apply`, not-yet-reviewed listing, oldest-first."""
    items = []
    if not LISTINGS_DIR.exists():
        return items
    for p in sorted(LISTINGS_DIR.glob('*.md')):
        if p.name == 'README.md':
            continue
        text = p.read_text()
        fm_raw, _, body = split_frontmatter(text)
        if fm_raw is None:
            continue
        try:
            fm = yaml.safe_load(fm_raw)
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue
        if str(fm.get('status', '')).strip() != 'To Apply' or fm.get('reviewed'):
            continue
        blurb = body.split('## Communications')[0].strip()
        items.append({
            'file': p.name,
            'company': str(fm.get('company', '')),
            'role': str(fm.get('role', '')),
            'location': str(fm.get('location', '')),
            'level': str(fm.get('level', '')),
            'type': str(fm.get('type', '')),
            'url': str(fm.get('url') or ''),
            'first_added': str(fm.get('first_added') or ''),
            'blurb': blurb,
        })
    items.sort(key=lambda x: (x['first_added'], x['company']))
    return items


def apply_keep(path, today):
    """Stamp reviewed + ensure status is To Apply — the latter matters when
    Keep is applied to a card that was Declined earlier in the same session
    (revisited via Prev/Next) and needs to flip back from Skipped."""
    text = path.read_text()
    text = set_status(text, 'To Apply')
    text = upsert_field(text, 'reviewed', today.isoformat())
    path.write_text(text)


def apply_decline(path, reason_key, note, today):
    label = DECLINE_LABELS.get(reason_key, 'Other')
    text = path.read_text()
    text = set_status(text, 'Skipped')
    text = upsert_field(text, 'decline_reason', reason_key or 'other')
    text = upsert_field(text, 'reviewed', today.isoformat())
    summary = f'Declined in triage: {label}' + (f' — {note}' if note else '')
    text = append_comm_row(text, f'| {today.isoformat()} | — | — | (triage) | {summary} |')
    path.write_text(text)


def _read_decline_log():
    if not DECLINE_LOG.exists():
        return {'declines': []}
    data = yaml.safe_load(DECLINE_LOG.read_text()) or {}
    if not isinstance(data.get('declines'), list):
        data['declines'] = []
    # Lazily backfill `id` on entries written before it existed — --mark-seen
    # can't target an id-less entry, so without this a pre-upgrade decline
    # could never be dismissed and would be asked about every run.
    backfilled = False
    for entry in data['declines']:
        if not entry.get('id'):
            entry['id'] = uuid.uuid4().hex[:8]
            backfilled = True
    if backfilled:
        DECLINE_LOG.write_text(DECLINE_LOG_HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return data


def append_decline_log(entry):
    DECLINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    data = _read_decline_log()
    data['declines'].append(entry)
    DECLINE_LOG.write_text(DECLINE_LOG_HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def pop_decline_log():
    """Remove the most recent decline-log entry, if any (Undo support)."""
    if not DECLINE_LOG.exists():
        return None
    data = _read_decline_log()
    if not data['declines']:
        return None
    entry = data['declines'].pop()
    DECLINE_LOG.write_text(DECLINE_LOG_HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return entry


def resolve_listing(file_name):
    """Reject anything that isn't a plain filename directly inside
    LISTINGS_DIR — file_name comes from the client. Returns the resolved
    Path, or None if it doesn't check out."""
    if not file_name or '/' in file_name or '\\' in file_name:
        return None
    target = (LISTINGS_DIR / file_name).resolve()
    if target.parent != LISTINGS_DIR.resolve() or not target.is_file():
        return None
    return target


def render_page(queue):
    queue_json = json.dumps(queue).replace('</', '<\\/')
    reasons_json = json.dumps(DECLINE_REASONS).replace('</', '<\\/')
    return (
        PAGE_TEMPLATE
        .replace('__QUEUE_JSON__', queue_json)
        .replace('__REASONS_JSON__', reasons_json)
    )


class TriageHandler(BaseHTTPRequestHandler):
    server_version = 'TriageServer/1'
    # HTTPServer is single-threaded and handles one connection at a time —
    # without a read timeout, a client that connects and stalls mid-request
    # (a stale browser tab retrying, a half-open probe) parks the accept
    # loop forever and starves every other request. BaseHTTPRequestHandler
    # already treats socket.timeout as "close this connection" internally.
    timeout = 10

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet; errors still surface via send_error

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path != '/':
            self.send_error(404)
            return
        body = render_page(load_queue()).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global _last_action, _session_counts
        path = urlparse(self.path).path
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            payload = json.loads(raw or b'{}')
        except json.JSONDecodeError:
            self._json({'ok': False, 'error': 'bad json'}, 400)
            return

        if path == '/api/decide':
            target = resolve_listing(payload.get('file', ''))
            if target is None:
                self._json({'ok': False, 'error': 'unknown listing'}, 404)
                return
            action = payload.get('action')
            today = datetime.date.today()
            prev_text = target.read_text()
            if action == 'keep':
                apply_keep(target, today)
                logged = False
                _session_counts['kept'] += 1
            elif action == 'decline':
                reason = payload.get('reason') or 'other'
                note = str(payload.get('note') or '').strip()
                apply_decline(target, reason, note, today)
                append_decline_log({
                    'id': uuid.uuid4().hex[:8],
                    'date': today.isoformat(),
                    'file': target.name,
                    'company': str(payload.get('company', '')),
                    'role': str(payload.get('role', '')),
                    'reason': reason,
                    'note': note,
                })
                logged = True
                _session_counts['declined'] += 1
            else:
                self._json({'ok': False, 'error': 'unknown action'}, 400)
                return
            _last_action = {'file': target.name, 'prev_text': prev_text, 'logged': logged, 'action': action}
            self._json({'ok': True})
            return

        if path == '/api/undo':
            if _last_action is None:
                self._json({'ok': False, 'error': 'nothing to undo'}, 400)
                return
            target = LISTINGS_DIR / _last_action['file']
            target.write_text(_last_action['prev_text'])
            if _last_action['logged']:
                pop_decline_log()
            _session_counts['kept' if _last_action['action'] == 'keep' else 'declined'] -= 1
            restored = _last_action['file']
            _last_action = None
            self._json({'ok': True, 'file': restored})
            return

        if path == '/api/finish':
            self._json({'ok': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        self.send_error(404)


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Job Search — Triage</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 720px; margin: 30px auto; padding: 0 24px; color: #222; line-height: 1.55; }
  h1 { color: #1a3a5c; font-size: 22px; border-bottom: 3px solid #1565c0; padding-bottom: 10px;
       display: flex; justify-content: space-between; align-items: center; }
  .nav-controls { display: flex; align-items: center; gap: 10px; font-size: 14px; font-weight: normal; color: #666; }
  .progress-wrap { background: #eef2f7; border-radius: 6px; height: 8px; margin: 14px 0 26px; overflow: hidden; }
  .progress-bar { background: #1565c0; height: 100%; width: 0%; transition: width .2s; }
  .card { background: #f7f9fc; border: 1px solid #e0e0e0; border-radius: 10px; padding: 24px 28px; min-height: 220px; }
  .card h2 { margin: 0 0 2px; color: #1a3a5c; font-size: 22px; }
  .card .role { font-size: 16px; color: #333; margin-bottom: 10px; }
  .badge { display: inline-block; padding: 4px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; margin-bottom: 14px; }
  .badge-keep { background: #e8f5e9; color: #2e7d32; }
  .badge-decline { background: #fdeceb; color: #c62828; }
  .chips { margin-bottom: 14px; }
  .chip { display: inline-block; padding: 2px 10px; border-radius: 12px; background: #dde6f0;
          color: #1a3a5c; font-size: 12px; font-weight: 600; margin-right: 6px; }
  .blurb { color: #444; white-space: pre-wrap; font-size: 14px; }
  .posting-link { display: inline-block; margin-top: 14px; color: #1565c0; font-size: 13px; text-decoration: none; }
  .posting-link:hover { text-decoration: underline; }
  .actions { display: flex; gap: 12px; margin-top: 24px; }
  button { font: inherit; border: none; border-radius: 8px; padding: 12px 20px; cursor: pointer; font-weight: 600; }
  button:disabled { opacity: .35; cursor: default; }
  .btn-decline { background: #fdeceb; color: #c62828; flex: 1; }
  .btn-decline:hover { background: #fadedb; }
  .btn-keep { background: #e8f5e9; color: #2e7d32; flex: 1; }
  .btn-keep:hover { background: #d7edd9; }
  .btn-nav { background: #eef2f7; color: #1a3a5c; padding: 6px 12px; font-size: 13px; font-weight: 600; }
  .btn-nav:hover:not(:disabled) { background: #dde6f0; }
  .btn-finish-header { margin-left: 8px; }
  .btn-undo { background: none; color: #777; font-weight: 500; padding: 6px 10px; }
  .btn-undo:hover { color: #1565c0; }
  .btn-undo:disabled:hover { color: #777; }
  .reasons { display: none; margin-top: 18px; border-top: 1px solid #eee; padding-top: 18px; }
  .reasons.open { display: block; }
  .reason-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; }
  .btn-reason { background: #fff; border: 1px solid #ddd; color: #333; text-align: left; font-weight: 500; }
  .btn-reason:hover { border-color: #c62828; color: #c62828; }
  .btn-reason.active { border-color: #c62828; background: #fdeceb; color: #c62828; }
  .note-input { width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid #ddd;
                border-radius: 6px; font: inherit; margin-bottom: 10px; }
  .reason-actions { display: flex; gap: 10px; align-items: center; }
  .btn-confirm-decline { background: #c62828; color: #fff; }
  .btn-confirm-decline:disabled { background: #fdeceb; color: #c62828; }
  .btn-cancel { background: none; color: #999; }
  .hint { color: #999; font-size: 12px; margin-top: 18px; }
  .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
           background: #1a3a5c; color: white; padding: 8px 18px; border-radius: 20px;
           font-size: 13px; opacity: 0; transition: opacity .2s; pointer-events: none; }
  .toast.show { opacity: 1; }
  .done { text-align: center; padding: 60px 0; }
  .done h2 { color: #1a3a5c; }
  .summary { display: flex; justify-content: center; gap: 24px; margin: 20px 0 30px; flex-wrap: wrap; }
  .summary .stat-num { font-size: 26px; font-weight: 700; color: #1a3a5c; }
  .summary .stat-label { font-size: 12px; color: #666; text-transform: uppercase; }
  .btn-finish { background: #1565c0; color: white; padding: 12px 28px; }
</style>
</head>
<body>
<h1>Triage
  <span class="nav-controls">
    <button class="btn-nav" id="prev-btn" onclick="prev()">‹ Prev</button>
    <span id="progress-label"></span>
    <button class="btn-nav" id="next-btn" onclick="next()">Next ›</button>
    <button class="btn-nav btn-finish-header" onclick="finish()">Finish for now</button>
  </span>
</h1>
<div class="progress-wrap"><div class="progress-bar" id="progress-bar"></div></div>

<div id="stage"></div>
<div class="toast" id="toast"></div>

<script>
const queue = __QUEUE_JSON__;
const reasons = __REASONS_JSON__;
let idx = 0;
const decisions = {};      // file -> { type: 'keep'|'decline', reason, note }
let lastDecidedFile = null; // enables one-level Undo, matches server-side _last_action
let pickerOpen = false;
let selectedReason = null; // reason picked in the open picker, pending confirmation

const stage = document.getElementById('stage');
const bar = document.getElementById('progress-bar');
const label = document.getElementById('progress-label');
const toastEl = document.getElementById('toast');
const prevBtn = document.getElementById('prev-btn');
const nextBtn = document.getElementById('next-btn');

const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ESC_MAP[c]);
}

// Only http(s) URLs are safe to render as an href — scraped listing URLs are
// untrusted (job-search's curated-list/board sweeps), and a javascript: URL
// would execute in this page's origin on click. new URL() with no base
// throws on anything that isn't already absolute, so this can't be fooled by
// a relative path resolving against the page's own http(s) origin.
function safeUrl(u) {
  if (typeof u !== 'string' || !u) return '';
  try {
    const parsed = new URL(u);
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') ? u : '';
  } catch {
    return '';
  }
}

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  setTimeout(() => toastEl.classList.remove('show'), 1400);
}

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

function render() {
  label.textContent = queue.length ? `${Math.min(idx, queue.length - 1) + 1} / ${queue.length}` : '';
  bar.style.width = queue.length ? `${(Math.min(idx, queue.length) / queue.length) * 100}%` : '0%';
  prevBtn.disabled = idx <= 0;
  nextBtn.disabled = idx >= queue.length;
  pickerOpen = false;
  if (idx >= queue.length) {
    renderDone();
    return;
  }
  const item = queue[idx];
  const decided = decisions[item.file];
  let badge = '';
  if (decided) {
    if (decided.type === 'keep') {
      badge = `<div class="badge badge-keep">✓ Kept</div>`;
    } else {
      const label = (reasons.find(r => r[0] === decided.reason) || [null, 'Other'])[1];
      badge = `<div class="badge badge-decline">✗ Declined — ${esc(label)}${decided.note ? ': ' + esc(decided.note) : ''}</div>`;
    }
  }
  const chips = [item.location, item.level, item.type].filter(Boolean).map(c => `<span class="chip">${esc(c)}</span>`).join('');
  const itemUrl = safeUrl(item.url);
  const link = itemUrl ? `<a class="posting-link" href="${esc(itemUrl)}" target="_blank" rel="noopener">Open posting ↗</a>` : '';
  const reasonButtons = reasons.map(([key, text], i) =>
    `<button class="btn-reason" data-key="${key}" onclick="selectReason('${key}')">${i + 1}. ${esc(text)}</button>`).join('');
  stage.innerHTML = `
    <div class="card">
      ${badge}
      <h2>${esc(item.company)}</h2>
      <div class="role">${esc(item.role)}</div>
      <div class="chips">${chips}</div>
      <div class="blurb">${esc(item.blurb) || '<em style="color:#999">No description recorded.</em>'}</div>
      ${link}
      <div class="actions">
        <button class="btn-decline" onclick="openPicker()">Decline</button>
        <button class="btn-keep" onclick="keep()">Keep</button>
      </div>
      <div class="reasons" id="reasons">
        <div class="reason-grid">${reasonButtons}</div>
        <input class="note-input" id="note" placeholder="Add a note — what's the gap, or why it's not a fit">
        <div class="reason-actions">
          <button class="btn-confirm-decline" id="confirm-decline-btn" onclick="confirmSelected()" disabled>Confirm decline</button>
          <button class="btn-cancel" onclick="closePicker()">Cancel (Esc)</button>
        </div>
      </div>
      <div class="hint">← / → prev / next · K keep · D decline · 1–4 pick a reason once open, Enter confirms · U undo · F finish for now · Esc cancel</div>
    </div>`;
}

function renderDone() {
  const values = Object.values(decisions);
  const counts = { keep: 0, company_fit: 0, role_fit: 0, stack_gap: 0, other: 0 };
  values.forEach(d => { const k = d.type === 'keep' ? 'keep' : d.reason; counts[k] = (counts[k] || 0) + 1; });
  const declined = values.length - counts.keep;
  stage.innerHTML = `
    <div class="done">
      <h2>Queue clear</h2>
      <p style="color:#666">${values.length ? 'Reviewed ' + values.length + ' listing(s) this session.' : 'Nothing was in the To Apply queue.'}</p>
      <div class="summary">
        <div><div class="stat-num">${counts.keep}</div><div class="stat-label">Kept</div></div>
        <div><div class="stat-num">${declined}</div><div class="stat-label">Declined</div></div>
      </div>
      <button class="btn-nav" onclick="prev()">‹ Back to review</button>
      <button class="btn-finish" onclick="finish()">Finish</button>
    </div>`;
}

function prev() {
  idx = Math.max(0, idx - 1);
  render();
}

function next() {
  idx = Math.min(queue.length, idx + 1);
  render();
}

function openPicker() {
  if (idx >= queue.length) return;
  pickerOpen = true;
  document.getElementById('reasons').classList.add('open');
  const noteEl = document.getElementById('note');
  const decided = decisions[queue[idx].file];
  const priorDecline = decided && decided.type === 'decline';
  selectedReason = priorDecline ? decided.reason : null;
  noteEl.value = (priorDecline && decided.note) ? decided.note : '';
  document.querySelectorAll('.btn-reason').forEach(b => b.classList.toggle('active', b.dataset.key === selectedReason));
  document.getElementById('confirm-decline-btn').disabled = !selectedReason;
  noteEl.focus();
}

function closePicker() {
  pickerOpen = false;
  const el = document.getElementById('reasons');
  if (el) el.classList.remove('open');
}

async function keep() {
  if (idx >= queue.length) return;
  const item = queue[idx];
  await post('/api/decide', { file: item.file, action: 'keep' });
  decisions[item.file] = { type: 'keep' };
  lastDecidedFile = item.file;
  next();
}

async function doDecline(reasonKey) {
  if (idx >= queue.length) return;
  const item = queue[idx];
  const note = document.getElementById('note').value.trim();
  await post('/api/decide', {
    file: item.file, action: 'decline', reason: reasonKey, note,
    company: item.company, role: item.role,
  });
  decisions[item.file] = { type: 'decline', reason: reasonKey, note };
  lastDecidedFile = item.file;
  next();
}

// Picking a reason selects it and keeps the picker open so a note can be added —
// except company_fit, which rarely needs elaboration and declines immediately.
function selectReason(key) {
  if (key === 'company_fit') { doDecline(key); return; }
  selectedReason = key;
  document.querySelectorAll('.btn-reason').forEach(b => b.classList.toggle('active', b.dataset.key === key));
  document.getElementById('confirm-decline-btn').disabled = false;
  document.getElementById('note').focus();
}

function confirmSelected() {
  if (!selectedReason) return;
  doDecline(selectedReason);
}

async function undo() {
  if (!lastDecidedFile) { toast('Nothing to undo'); return; }
  const res = await post('/api/undo', {});
  lastDecidedFile = null;
  if (!res.ok) { toast('Nothing more to undo'); return; }
  delete decisions[res.file];
  const restoredIdx = queue.findIndex(q => q.file === res.file);
  idx = restoredIdx >= 0 ? restoredIdx : idx;
  render();
  toast('Undone');
}

async function finish() {
  await post('/api/finish', {});
  const values = Object.values(decisions);
  const keep = values.filter(d => d.type === 'keep').length;
  const declined = values.length - keep;
  const remaining = Math.max(0, queue.length - values.length);
  document.body.innerHTML = `
    <div class="done">
      <h2>Session saved</h2>
      <div class="summary">
        <div><div class="stat-num">${keep}</div><div class="stat-label">Kept</div></div>
        <div><div class="stat-num">${declined}</div><div class="stat-label">Declined</div></div>
      </div>
      <p style="color:#666">${remaining ? remaining + ' listing(s) still queued for next time.' : 'Queue clear.'} You can close this tab.</p>
    </div>`;
}

document.addEventListener('keydown', (e) => {
  if (pickerOpen) {
    if (e.key === 'Escape') { closePicker(); return; }
    if (e.key === 'Enter') { confirmSelected(); return; }
    if (document.activeElement && document.activeElement.id === 'note') return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= reasons.length) selectReason(reasons[n - 1][0]);
    return;
  }
  if (e.key === 'ArrowLeft') { prev(); return; }
  if (e.key === 'ArrowRight') { next(); return; }
  const k = e.key.toLowerCase();
  if (k === 'u') { undo(); return; }
  if (k === 'f') { finish(); return; }
  if (idx >= queue.length) return;
  if (k === 'k') keep();
  else if (k === 'd') openPicker();
});

render();
</script>
</body>
</html>
"""


def open_browser(url):
    """Best-effort, non-blocking tab open. Deliberately bypasses the stdlib
    webbrowser module on macOS: its default controller shells out through
    osascript/AppleScript and calls .wait() on it, which can block
    indefinitely on a "let this app control Chrome" permission prompt when
    run from a non-interactive shell (nobody there to click it) — and
    because that happens on the same process, it can stall this server's
    request handling too, not just the browser launch. Plain `open <url>`
    (macOS) / `xdg-open` (Linux) is fire-and-forget: spawned, never waited
    on, can't block us. Falls back to the stdlib module elsewhere (e.g.
    Windows) where that failure mode doesn't apply."""
    try:
        if sys.platform == 'darwin':
            subprocess.Popen(['open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith('linux'):
            subprocess.Popen(['xdg-open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(url)
    except Exception:
        pass  # opening the tab is a convenience, never worth failing the session over


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8934)
    parser.add_argument('--no-browser', action='store_true', help='Do not auto-open the browser tab.')
    args = parser.parse_args()

    queue_len = len(load_queue())
    if queue_len == 0:
        print('Nothing to triage — no To Apply listings pending review.')
        return

    server = HTTPServer(('127.0.0.1', args.port), TriageHandler)
    url = f'http://127.0.0.1:{args.port}/'
    print(f'{queue_len} listing(s) to review. Serving at {url}')
    print('Click Finish in the page (or Ctrl-C here) to end the session.')
    if not args.no_browser:
        threading.Timer(0.3, lambda: open_browser(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print(f"Session ended. Kept: {_session_counts['kept']}, Declined: {_session_counts['declined']}.")


if __name__ == '__main__':
    main()
