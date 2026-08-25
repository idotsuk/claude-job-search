#!/usr/bin/env python3
"""
Interactive one-at-a-time review for the `To Apply` queue.

Serves a local card UI at http://localhost:{port}/ : Keep leaves the listing
queued (stamps `reviewed:`) with a `keep_intent` of either `apply` (ready
now) or `reconsider` (still queued, but flagged for a second look before
/apply works it — see apply/SKILL.md step 2); Decline picks a reason and
demotes the listing to `status: Skipped`, writing `decline_reason:` and a
Communications row — same surgical-frontmatter-edit approach as
mark_stale.py, reused directly from there rather than re-implemented.

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
from mark_stale import (
    upsert_field, remove_field, set_status, append_comm_row, remove_comm_row,
    parse_field, split_frontmatter,
)

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

# Per-file session bookkeeping, keyed by listing filename:
#   fields_pristine   snapshot of TRIAGE_FIELDS the *first* time the file is
#                     decided this session — what a full Remove/revert puts
#                     back (surgically, per-field), so an edit made to the
#                     file from outside meanwhile survives the revert.
#   kind              current bucket: 'apply' / 'reconsider' / 'declined' / None
#   decline_log_ids   every decline-log id this file has produced and not yet
#                     had cleaned up this session (at most one after a
#                     re-decline — a fresh decline replaces the prior entry).
#   comm_rows         the exact Communications row string(s) this session's
#                     current decision added, so a re-decide can remove them
#                     before writing the new one instead of stacking.
_session_state = {}

# Frontmatter fields the decide path writes. Snapshotted before a listing's
# first decision so /api/undo and /api/revert can restore them one at a time
# (to the snapshotted value, or absent) rather than overwriting the whole
# file — which would silently discard anything appended to it (a Gmail/
# WhatsApp Communications row, a manual edit) between the decision and the
# undo. Matches the repo-wide "frontmatter is edited surgically" invariant.
TRIAGE_FIELDS = ('status', 'reviewed', 'keep_intent', 'decline_reason')


def snapshot_fields(text):
    fm_raw, _, _ = split_frontmatter(text)
    if fm_raw is None:
        return {f: None for f in TRIAGE_FIELDS}
    return {f: parse_field(fm_raw, f) for f in TRIAGE_FIELDS}


def restore_fields(text, snap):
    """Put each TRIAGE_FIELDS entry back to its snapshotted value, or remove
    it if it was absent when snapshotted. Per-field regex edits — every
    other frontmatter key and the whole body are left as-is."""
    for field, value in snap.items():
        if value is None:
            text = remove_field(text, field)
        elif field == 'status':
            text = set_status(text, value)
        else:
            text = upsert_field(text, field, value)
    return text

# Session-only tally, printed when the server stops — the skill relays this
# back to the user without re-scanning the listings tree.
_session_counts = {'apply': 0, 'reconsider': 0, 'declined': 0}


def _adjust_counts(new_kind, old_kind):
    """Net a bucket transition into _session_counts — the one place this
    arithmetic happens, so /api/decide, /api/undo, and /api/revert (which
    each move a listing between buckets, including to/from no bucket at
    all) can't drift out of sync with each other. A no-op when the kind
    doesn't actually change (e.g. re-clicking the same outcome)."""
    if new_kind == old_kind:
        return
    if new_kind is not None:
        _session_counts[new_kind] += 1
    if old_kind is not None:
        _session_counts[old_kind] -= 1


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


def _table_cell(text):
    """Sanitize free text for embedding in a `|`-delimited Communications
    table cell. A literal `|` would otherwise split into an extra column
    when re-split downstream (generate_run_report.py's parse_comms_rows
    does a naive line.split('|')), silently truncating everything after
    it; a newline would break the one-row-per-line table structure the
    same way. Swap the pipe for a visually identical full-width character
    rather than stripping it, so the user's text isn't silently altered."""
    return text.replace('|', '｜').replace('\n', ' ').strip()


def apply_keep(path, today, intent='apply'):
    """Stamp reviewed + ensure status is To Apply — the latter matters when
    Keep is applied to a card that was Declined earlier in the same session
    (revisited via Prev/Next) and needs to flip back from Skipped.

    `intent` is 'apply' (ready to work now) or 'reconsider' (still queued,
    but flagged for a second look — see apply/SKILL.md step 2). Both leave
    status: To Apply; only the keep_intent field differs. A reconsider also
    gets a Communications row, same as a decline, since it's a decision
    worth an audit trail — a plain apply-keep stays silent as before.

    Also clears a stale `decline_reason` left over from an earlier decline
    of this same listing (revisited this session or a prior one) — leaving
    it in place alongside `status: To Apply` would read as an unresolved
    tech-stack gap that no longer applies.

    Returns the Communications row it appended (reconsider only), or None,
    so the caller can strip it again on a later re-decide / undo."""
    text = path.read_text()
    text = set_status(text, 'To Apply')
    text = upsert_field(text, 'reviewed', today.isoformat())
    text = upsert_field(text, 'keep_intent', intent)
    text = remove_field(text, 'decline_reason')
    row = None
    if intent == 'reconsider':
        row = f'| {today.isoformat()} | — | — | (triage) | Marked to reconsider later |'
        text = append_comm_row(text, row)
    path.write_text(text)
    return row


def apply_decline(path, reason_key, note, today):
    """Symmetric to apply_keep: also clears a stale `keep_intent` left over
    from an earlier Apply/Reconsider of this listing, so a declined listing
    never carries a contradictory 'still worth reconsidering' flag.

    Returns the Communications row it appended, so the caller can strip it
    again on a later re-decide / undo instead of leaving it stacked."""
    label = DECLINE_LABELS.get(reason_key, 'Other')
    text = path.read_text()
    text = set_status(text, 'Skipped')
    text = upsert_field(text, 'decline_reason', reason_key or 'other')
    text = upsert_field(text, 'reviewed', today.isoformat())
    text = remove_field(text, 'keep_intent')
    note = _table_cell(note) if note else note
    summary = f'Declined in triage: {label}' + (f' — {note}' if note else '')
    row = f'| {today.isoformat()} | — | — | (triage) | {summary} |'
    text = append_comm_row(text, row)
    path.write_text(text)
    return row


def _write_decline_log(data):
    DECLINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    DECLINE_LOG.write_text(DECLINE_LOG_HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


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
        _write_decline_log(data)
    return data


def append_decline_log(entry):
    data = _read_decline_log()
    data['declines'].append(entry)
    _write_decline_log(data)


def pop_decline_log():
    """Remove the most recent decline-log entry, if any (Undo support)."""
    if not DECLINE_LOG.exists():
        return None
    data = _read_decline_log()
    if not data['declines']:
        return None
    entry = data['declines'].pop()
    _write_decline_log(data)
    return entry


def remove_decline_log_entries(ids):
    """Remove every decline-log entry whose id is in `ids` (a listing can
    accumulate more than one this session if declined, revisited, and
    declined again) — used when a listing is reverted or re-decided away
    from Decline, so a suggestion tied to an abandoned decline doesn't
    linger. No-op if `ids` is empty, to avoid a pointless read/write."""
    if not ids:
        return
    ids = set(ids)
    data = _read_decline_log()
    data['declines'] = [e for e in data['declines'] if e.get('id') not in ids]
    _write_decline_log(data)


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
            if action not in ('keep', 'decline'):
                self._json({'ok': False, 'error': 'unknown action'}, 400)
                return
            intent = payload.get('intent') or 'apply'
            if action == 'keep' and intent not in ('apply', 'reconsider'):
                self._json({'ok': False, 'error': 'unknown intent'}, 400)
                return
            reason = payload.get('reason') or 'other'
            if action == 'decline' and reason not in DECLINE_LABELS:
                # Symmetric to the `intent` check above: an unrecognized
                # reason would be persisted to frontmatter and the decline
                # log where job-search step 0b can't categorize it, so it
                # would silently never produce a suggestion and never get
                # marked `suggested` — re-read every run.
                self._json({'ok': False, 'error': 'unknown reason'}, 400)
                return
            note = str(payload.get('note') or '').strip()
            today = datetime.date.today()

            prev_text = target.read_text()
            state = _session_state.get(target.name)
            if state is None:
                state = {'fields_pristine': snapshot_fields(prev_text), 'kind': None,
                         'decline_log_ids': [], 'comm_rows': []}
                _session_state[target.name] = state
            prev_kind = state['kind']
            # Snapshot the fields *this* decision is about to overwrite, for a
            # single-step Undo that restores them without touching anything
            # else in the file.
            fields_before = snapshot_fields(prev_text)

            # Re-deciding a file already decided this session: strip the
            # Communications row(s) the earlier decision wrote (exact match,
            # so a row added from outside meanwhile is left alone) before the
            # new decision writes its own — otherwise each pass stacks another
            # "Declined in triage" / "Marked to reconsider later" row.
            removed_comm_rows = list(state['comm_rows'])
            if removed_comm_rows:
                text = target.read_text()
                for row in removed_comm_rows:
                    text = remove_comm_row(text, row)
                target.write_text(text)
            state['comm_rows'] = []

            # A re-decide away from (or from one Decline to another) also
            # supersedes the decline-log entries the file produced earlier
            # this session — job-search step 0b should see one decline per
            # listing reflecting the final call, not a stack. Snapshot them
            # so Undo can put them back.
            removed_decline_entries = []
            if state['decline_log_ids']:
                removed_ids = set(state['decline_log_ids'])
                removed_decline_entries = [e for e in _read_decline_log()['declines'] if e.get('id') in removed_ids]
                remove_decline_log_entries(state['decline_log_ids'])
                state['decline_log_ids'] = []

            if action == 'keep':
                added_row = apply_keep(target, today, intent)
                logged = False
                kind = intent
            else:
                added_row = apply_decline(target, reason, note, today)
                entry_id = uuid.uuid4().hex[:8]
                append_decline_log({
                    'id': entry_id,
                    'date': today.isoformat(),
                    'file': target.name,
                    'company': str(payload.get('company', '')),
                    'role': str(payload.get('role', '')),
                    'reason': reason,
                    'note': note,
                })
                state['decline_log_ids'].append(entry_id)
                logged = True
                kind = 'declined'

            if added_row:
                state['comm_rows'].append(added_row)
            # Re-deciding a file already decided this session (via Prev/Next,
            # or Change status in the Review panel) is a bucket *transition*,
            # not a fresh addition — net the counts so a listing moved from
            # one bucket to another is still counted once, not twice.
            _adjust_counts(kind, prev_kind)
            state['kind'] = kind
            _last_action = {
                'file': target.name, 'fields_before': fields_before,
                'added_comm_row': added_row, 'removed_comm_rows': removed_comm_rows,
                'logged': logged, 'kind': kind, 'prev_kind': prev_kind,
                'removed_decline_entries': removed_decline_entries,
            }
            self._json({'ok': True})
            return

        if path == '/api/undo':
            if _last_action is None:
                self._json({'ok': False, 'error': 'nothing to undo'}, 400)
                return
            target = LISTINGS_DIR / _last_action['file']
            text = target.read_text()
            if _last_action['added_comm_row']:
                text = remove_comm_row(text, _last_action['added_comm_row'])
            for row in _last_action['removed_comm_rows']:
                text = append_comm_row(text, row)
            text = restore_fields(text, _last_action['fields_before'])
            target.write_text(text)
            state = _session_state.get(_last_action['file'])
            if state is not None:
                state['comm_rows'] = list(_last_action['removed_comm_rows'])
            if _last_action['logged']:
                popped = pop_decline_log()
                if state and popped and state['decline_log_ids'] and state['decline_log_ids'][-1] == popped.get('id'):
                    state['decline_log_ids'].pop()
            for entry in _last_action['removed_decline_entries']:
                append_decline_log(entry)
                if state is not None:
                    state['decline_log_ids'].append(entry['id'])
            _adjust_counts(_last_action['prev_kind'], _last_action['kind'])
            if state:
                state['kind'] = _last_action['prev_kind']
            restored = _last_action['file']
            _last_action = None
            self._json({'ok': True, 'file': restored})
            return

        if path == '/api/revert':
            target = resolve_listing(payload.get('file', ''))
            if target is None:
                self._json({'ok': False, 'error': 'unknown listing'}, 404)
                return
            state = _session_state.get(target.name)
            if state is None or state['kind'] is None:
                self._json({'ok': False, 'error': 'nothing to revert'}, 400)
                return
            text = target.read_text()
            for row in state['comm_rows']:
                text = remove_comm_row(text, row)
            text = restore_fields(text, state['fields_pristine'])
            target.write_text(text)
            remove_decline_log_entries(state['decline_log_ids'])
            _adjust_counts(None, state['kind'])
            if _last_action is not None and _last_action['file'] == target.name:
                _last_action = None
            del _session_state[target.name]
            self._json({'ok': True, 'file': target.name})
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
  .badge-apply { background: #e8f5e9; color: #2e7d32; }
  .badge-reconsider { background: #fff6e0; color: #8a6100; }
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
  .btn-reconsider { background: #fff6e0; color: #8a6100; flex: 1; }
  .btn-reconsider:hover { background: #fbecc4; }
  .btn-keep { background: #e8f5e9; color: #2e7d32; flex: 1; }
  .btn-keep:hover { background: #d7edd9; }
  .btn-nav { background: #eef2f7; color: #1a3a5c; padding: 6px 12px; font-size: 13px; font-weight: 600; }
  .btn-nav:hover:not(:disabled) { background: #dde6f0; }
  .btn-finish-header { margin-left: 8px; }
  .btn-undo { background: none; color: #777; font-weight: 500; padding: 6px 10px; }
  .btn-undo:hover { color: #1565c0; }
  .btn-undo:disabled:hover { color: #777; }
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(20, 30, 45, .45);
                    align-items: flex-start; justify-content: center; padding: 40px 16px; z-index: 10; }
  .modal-overlay.open { display: flex; }
  .modal { background: #fff; border-radius: 10px; padding: 24px 28px; max-width: 640px; width: 100%;
           max-height: 85vh; overflow-y: auto; }
  .modal h2 { margin: 0 0 4px; color: #1a3a5c; font-size: 20px; display: flex; justify-content: space-between; align-items: center; }
  .modal .btn-close { background: none; color: #999; font-size: 20px; padding: 2px 8px; }
  .modal .empty-note { color: #999; font-size: 14px; margin: 20px 0; }
  .review-section { margin-top: 18px; }
  .review-section h3 { margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: .03em; }
  .review-section.apply h3 { color: #2e7d32; }
  .review-section.reconsider h3 { color: #8a6100; }
  .review-section.declined h3 { color: #c62828; }
  .review-row { padding: 8px 0; border-bottom: 1px solid #eee; font-size: 14px; }
  .review-row:last-child { border-bottom: none; }
  .review-row .rr-title { font-weight: 600; color: #222; }
  .review-row .rr-link { font-weight: 500; color: #1565c0; font-size: 12px; text-decoration: none; }
  .review-row .rr-link:hover { text-decoration: underline; }
  .review-row .rr-detail { color: #777; font-size: 12px; margin-top: 2px; }
  .rr-actions { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
  .rr-btn { background: #eef2f7; color: #1a3a5c; font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 12px; }
  .rr-btn:hover { background: #dde6f0; }
  .rr-btn.rr-remove { background: none; color: #999; margin-left: auto; }
  .rr-btn.rr-remove:hover { color: #c62828; }
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
    <button class="btn-undo" id="undo-btn" onclick="undo()" disabled>Undo</button>
    <button class="btn-nav" onclick="openReview()">Review (V)</button>
    <button class="btn-nav btn-finish-header" onclick="finish()">Finish for now</button>
  </span>
</h1>
<div class="progress-wrap"><div class="progress-bar" id="progress-bar"></div></div>

<div id="stage"></div>
<div class="toast" id="toast"></div>
<div class="modal-overlay" id="review-overlay" onclick="if (event.target === this) closeReview();">
  <div class="modal">
    <h2>Session so far <button class="btn-close" onclick="closeReview()">&times;</button></h2>
    <div id="review-body"></div>
  </div>
</div>

<script>
const queue = __QUEUE_JSON__;
const reasons = __REASONS_JSON__;
let idx = 0;
const decisions = {};      // file -> { type: 'apply'|'reconsider'|'decline', reason, note }
let lastDecidedFile = null; // enables one-level Undo, matches server-side _last_action
let lastDecidedPrevValue = null; // decisions[lastDecidedFile] before that change, so Undo restores a re-decide correctly instead of just deleting it
let pickerOpen = false;
let selectedReason = null; // reason picked in the open picker, pending confirmation
let reviewOpen = false;

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

// Guards every server-mutating action (decide/undo/revert) against
// overlapping calls — without this, OS key-repeat holding K/R/D, or a fast
// double-click, fires a second request before the first's response lands,
// and both would act on the same queue[idx] (or the same file). Also
// centralizes error handling: fetch/JSON failures no longer leave the caller
// awaiting forever, and a non-ok response is surfaced instead of silently
// treated as success.
let inFlight = false;

async function postGuarded(path, body) {
  if (inFlight) return null;
  inFlight = true;
  try {
    const res = await post(path, body);
    if (!res.ok) toast(res.error || 'Could not save — try again');
    return res;
  } catch (e) {
    toast('Could not reach the server — try again');
    return null;
  } finally {
    inFlight = false;
  }
}

function syncUndoButton() {
  const btn = document.getElementById('undo-btn');
  if (btn) btn.disabled = !lastDecidedFile;
}

function render() {
  label.textContent = queue.length ? `${Math.min(idx, queue.length - 1) + 1} / ${queue.length}` : '';
  bar.style.width = queue.length ? `${(Math.min(idx, queue.length) / queue.length) * 100}%` : '0%';
  prevBtn.disabled = idx <= 0;
  nextBtn.disabled = idx >= queue.length;
  syncUndoButton();
  pickerOpen = false;
  if (idx >= queue.length) {
    renderDone();
    return;
  }
  const item = queue[idx];
  const decided = decisions[item.file];
  let badge = '';
  if (decided) {
    if (decided.type === 'apply') {
      badge = `<div class="badge badge-apply">✓ To Apply</div>`;
    } else if (decided.type === 'reconsider') {
      badge = `<div class="badge badge-reconsider">⏳ Reconsider later</div>`;
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
        <button class="btn-reconsider" onclick="keep('reconsider')">Reconsider later</button>
        <button class="btn-keep" onclick="keep('apply')">Apply</button>
      </div>
      <div class="reasons" id="reasons">
        <div class="reason-grid">${reasonButtons}</div>
        <input class="note-input" id="note" oninput="updateConfirmState()" placeholder="Add a note — what's the gap, or why it's not a fit">
        <div class="reason-actions">
          <button class="btn-confirm-decline" id="confirm-decline-btn" onclick="confirmSelected()" disabled>Confirm decline</button>
          <button class="btn-cancel" onclick="closePicker()">Cancel (Esc)</button>
        </div>
      </div>
      <div class="hint">← / → prev / next · K apply · R reconsider · D decline · 1–4 pick a reason once open, Enter confirms · U undo · V review · F finish for now · Esc cancel</div>
    </div>`;
}

// Buckets every decision made so far into { apply, reconsider, declined },
// each entry carrying the item's company/role plus any decline reason/note,
// for reuse by both the Review panel and the Finish/done screens.
function groupDecisions() {
  const groups = { apply: [], reconsider: [], declined: [] };
  for (const item of queue) {
    const d = decisions[item.file];
    if (!d) continue;
    const row = { file: item.file, company: item.company, role: item.role, url: item.url, reason: d.reason, note: d.note };
    if (d.type === 'apply') groups.apply.push(row);
    else if (d.type === 'reconsider') groups.reconsider.push(row);
    else groups.declined.push(row);
  }
  return groups;
}

const KIND_LABELS = { apply: 'Apply', reconsider: 'Reconsider', declined: 'Decline' };

// `interactive` gates the per-row Remove/Change-status controls: they only
// make sense while the server is still alive to act on them (Review panel,
// opened any time before Finish). The Finish screen renders this same
// function with interactive=false — by the time it's on screen the server
// has already been told to shut down, so there's nothing left to write to.
function renderGroupedSummary(groups, interactive) {
  const sections = [
    ['apply', 'To apply', groups.apply],
    ['reconsider', 'Reconsider later', groups.reconsider],
    ['declined', 'Declined', groups.declined],
  ];
  const total = groups.apply.length + groups.reconsider.length + groups.declined.length;
  if (!total) return '<p class="empty-note">Nothing decided yet this session.</p>';
  return sections.map(([cls, title, rows]) => {
    if (!rows.length) return '';
    const items = rows.map(r => {
      const label = r.reason ? (reasons.find(x => x[0] === r.reason) || [null, 'Other'])[1] : '';
      const detail = [label, r.note].filter(Boolean).join(' — ');
      const rUrl = safeUrl(r.url);
      const link = rUrl ? ` <a class="rr-link" href="${esc(rUrl)}" target="_blank" rel="noopener">Open ↗</a>` : '';
      let actions = '';
      if (interactive) {
        const moves = ['apply', 'reconsider', 'declined'].filter(k => k !== cls)
          .map(k => `<button class="rr-btn" data-action="move" data-file="${esc(r.file)}" data-kind="${k}">→ ${KIND_LABELS[k]}</button>`).join('');
        actions = `<div class="rr-actions">${moves}<button class="rr-btn rr-remove" data-action="remove" data-file="${esc(r.file)}">Remove</button></div>`;
      }
      return `<div class="review-row"><div class="rr-title">${esc(r.company)} — ${esc(r.role)}${link}</div>${detail ? `<div class="rr-detail">${esc(detail)}</div>` : ''}${actions}</div>`;
    }).join('');
    return `<div class="review-section ${cls}"><h3>${title} (${rows.length})</h3>${items}</div>`;
  }).join('');
}

function refreshReviewPanel() {
  if (reviewOpen) document.getElementById('review-body').innerHTML = renderGroupedSummary(groupDecisions(), true);
}

// Re-decide a listing straight from the Review panel. Moving to Apply or
// Reconsider is a one-click transition (decide() already handles re-deciding
// an already-decided file). Moving to Decline needs a reason, so instead of
// duplicating that picker inline, jump to the listing's own card and open it
// there — same UI as deciding it normally.
async function changeStatus(file, newKind) {
  if (newKind === 'declined') {
    closeReview();
    const i = queue.findIndex(q => q.file === file);
    if (i >= 0) { idx = i; render(); openPicker(); }
    return;
  }
  const res = await postGuarded('/api/decide', { file, action: 'keep', intent: newKind });
  if (!res || !res.ok) return;
  lastDecidedPrevValue = decisions[file] || null;
  decisions[file] = { type: newKind };
  lastDecidedFile = file;
  syncUndoButton();
  refreshReviewPanel();
}

// Fully reverts a listing to how it looked before this session touched it —
// clears status/keep_intent/decline_reason/reviewed and any decline-log
// entries it produced, and drops it back into the live (unreviewed) queue.
async function revertItem(file) {
  const res = await postGuarded('/api/revert', { file });
  if (!res || !res.ok) return;
  delete decisions[file];
  if (lastDecidedFile === file) lastDecidedFile = null;
  syncUndoButton();
  refreshReviewPanel();
  toast('Removed — back in the queue');
}

function renderDone() {
  const groups = groupDecisions();
  const total = groups.apply.length + groups.reconsider.length + groups.declined.length;
  stage.innerHTML = `
    <div class="done">
      <h2>Queue clear</h2>
      <p style="color:#666">${total ? 'Reviewed ' + total + ' listing(s) this session.' : 'Nothing was in the To Apply queue.'}</p>
      <div class="summary">
        <div><div class="stat-num">${groups.apply.length}</div><div class="stat-label">To apply</div></div>
        <div><div class="stat-num">${groups.reconsider.length}</div><div class="stat-label">Reconsider</div></div>
        <div><div class="stat-num">${groups.declined.length}</div><div class="stat-label">Declined</div></div>
      </div>
      <button class="btn-nav" onclick="prev()">‹ Back to review</button>
      <button class="btn-nav" onclick="openReview()">Review (V)</button>
      <button class="btn-finish" onclick="finish()">Finish</button>
    </div>`;
}

function openReview() {
  reviewOpen = true;
  document.getElementById('review-body').innerHTML = renderGroupedSummary(groupDecisions(), true);
  document.getElementById('review-overlay').classList.add('open');
}

function closeReview() {
  reviewOpen = false;
  document.getElementById('review-overlay').classList.remove('open');
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
  updateConfirmState();
  noteEl.focus();
}

// Every reason but company_fit requires a note before Confirm decline
// enables — a bare role_fit/stack_gap tag with no note doesn't carry
// enough signal for job-search step 0b's repeat-pattern questions.
function updateConfirmState() {
  const note = document.getElementById('note').value.trim();
  const ready = !!selectedReason && (selectedReason === 'company_fit' || note.length > 0);
  document.getElementById('confirm-decline-btn').disabled = !ready;
}

function closePicker() {
  pickerOpen = false;
  const el = document.getElementById('reasons');
  if (el) el.classList.remove('open');
}

async function keep(intent) {
  if (idx >= queue.length || inFlight) return;
  const item = queue[idx];
  const res = await postGuarded('/api/decide', { file: item.file, action: 'keep', intent });
  if (!res || !res.ok) return;
  lastDecidedPrevValue = decisions[item.file] || null;
  decisions[item.file] = { type: intent };
  lastDecidedFile = item.file;
  next();
}

async function doDecline(reasonKey) {
  if (idx >= queue.length || inFlight) return;
  const item = queue[idx];
  const note = document.getElementById('note').value.trim();
  const res = await postGuarded('/api/decide', {
    file: item.file, action: 'decline', reason: reasonKey, note,
    company: item.company, role: item.role,
  });
  if (!res || !res.ok) return;
  lastDecidedPrevValue = decisions[item.file] || null;
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
  updateConfirmState();
  document.getElementById('note').focus();
}

function confirmSelected() {
  // Enter (the keyboard path here) bypasses the Confirm button's disabled
  // state, so the note requirement has to be re-checked here too, not just
  // reflected in the button.
  if (!selectedReason) return;
  const note = document.getElementById('note').value.trim();
  if (selectedReason !== 'company_fit' && !note) return;
  doDecline(selectedReason);
}

async function undo() {
  if (!lastDecidedFile) { toast('Nothing to undo'); return; }
  const res = await postGuarded('/api/undo', {});
  if (!res || !res.ok) return;
  const prevValue = lastDecidedPrevValue;
  lastDecidedFile = null;
  lastDecidedPrevValue = null;
  if (prevValue) decisions[res.file] = prevValue; else delete decisions[res.file];
  const restoredIdx = queue.findIndex(q => q.file === res.file);
  idx = restoredIdx >= 0 ? restoredIdx : idx;
  render();
  refreshReviewPanel();
  syncUndoButton();
  toast('Undone');
}

async function finish() {
  await post('/api/finish', {});
  const groups = groupDecisions();
  const decided = groups.apply.length + groups.reconsider.length + groups.declined.length;
  const remaining = Math.max(0, queue.length - decided);
  document.body.innerHTML = `
    <div class="done">
      <h2>Session saved</h2>
      <div class="summary">
        <div><div class="stat-num">${groups.apply.length}</div><div class="stat-label">To apply</div></div>
        <div><div class="stat-num">${groups.reconsider.length}</div><div class="stat-label">Reconsider</div></div>
        <div><div class="stat-num">${groups.declined.length}</div><div class="stat-label">Declined</div></div>
      </div>
      ${renderGroupedSummary(groups, false)}
      <p style="color:#666; margin-top: 20px;">${remaining ? remaining + ' listing(s) still queued for next time.' : 'Queue clear.'} This session has ended (read-only) — run /triage again to change anything above. You can close this tab.</p>
    </div>`;
}

document.addEventListener('keydown', (e) => {
  if (reviewOpen) {
    if (e.key === 'Escape') closeReview();
    return;
  }
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
  if (k === 'v') { openReview(); return; }
  if (idx >= queue.length) return;
  if (k === 'k') keep('apply');
  else if (k === 'r') keep('reconsider');
  else if (k === 'd') openPicker();
});

// Delegated instead of inline onclick="...('${file}')": file is a listing
// filename slug that ultimately traces back to scraped company/role text
// (job-search's curated-list/board sweeps), and interpolating it into an
// inline event-handler attribute is unsafe even when HTML-escaped — the
// browser HTML-decodes the attribute before compiling it as JS, so an
// escaped quote there still reopens a JS-string-breakout the same way an
// unescaped one would. Reading it from a data-attribute avoids that class of
// bug entirely.
document.getElementById('review-body').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const { action, file, kind } = btn.dataset;
  if (action === 'move') changeStatus(file, kind);
  else if (action === 'remove') revertItem(file);
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
        print(
            f"Session ended. To Apply: {_session_counts['apply']}, "
            f"Reconsider: {_session_counts['reconsider']}, Declined: {_session_counts['declined']}."
        )


if __name__ == '__main__':
    main()
