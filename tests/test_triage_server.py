#!/usr/bin/env python3
"""Tests for scripts/triage_server.py's pure decision/logging logic.

No server or browser involved — these exercise load_queue/apply_keep/
apply_decline/append_decline_log/pop_decline_log/resolve_listing directly
against a throwaway data/ dir, the same way mark_stale.py's own logic would
be tested.

Run: python3 tests/test_triage_server.py
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / 'scripts'

try:
    import yaml  # noqa: F401
except ImportError:
    import types
    import json as _json
    fake_yaml = types.ModuleType('yaml')
    fake_yaml.safe_load = _json.loads
    fake_yaml.safe_dump = _json.dumps
    sys.modules['yaml'] = fake_yaml

sys.path.insert(0, str(SCRIPTS_DIR))


LISTING_TEMPLATE = """---
title: {company} — {role}
company: {company}
role: {role}
status: {status}
url: https://jobs.example.com/{slug}
location: Berlin
level: Senior
type: Backend
first_added: {first_added}
source: test
---

Why it fits: {blurb}

## Communications

| Date | Channel | Direction | Contact | Summary |
|------|---------|-----------|---------|---------|
"""


class TriageServerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='triage_test_data_')
        os.environ['JOB_SEARCH_DATA'] = self._tmp
        # Force a fresh import bound to this test's JOB_SEARCH_DATA — lib.py
        # reads the env var lazily via data_root(), but the module-level
        # LISTINGS_DIR/DECLINE_LOG constants in triage_server are bound at
        # import time, so reimport per test.
        for mod in ('triage_server', 'mark_stale', 'lib'):
            sys.modules.pop(mod, None)
        import triage_server
        self.mod = triage_server
        self.listings_dir = Path(self._tmp) / 'listings'
        self.listings_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop('JOB_SEARCH_DATA', None)

    def _write_listing(self, slug, company='Acme', role='Backend Engineer',
                        status='To Apply', first_added='2026-08-01', blurb='great fit', extra=''):
        text = LISTING_TEMPLATE.format(
            company=company, role=role, status=status, slug=slug,
            first_added=first_added, blurb=blurb,
        )
        if extra:
            text = text.replace('source: test\n---', f'source: test\n{extra}\n---')
        path = self.listings_dir / f'{slug}.md'
        path.write_text(text)
        return path

    def test_load_queue_filters_to_apply_and_unreviewed(self):
        self._write_listing('acme-backend', status='To Apply')
        self._write_listing('other-role', status='Applied')
        self._write_listing('already-reviewed', status='To Apply', extra='reviewed: 2026-08-02')

        queue = self.mod.load_queue()

        self.assertEqual([item['file'] for item in queue], ['acme-backend.md'])

    def test_load_queue_sorts_oldest_first_added_first(self):
        self._write_listing('later', company='Later Co', first_added='2026-08-10')
        self._write_listing('earlier', company='Earlier Co', first_added='2026-08-01')

        queue = self.mod.load_queue()

        self.assertEqual([item['company'] for item in queue], ['Earlier Co', 'Later Co'])

    def test_apply_keep_stamps_reviewed_and_leaves_status(self):
        path = self._write_listing('acme-backend')
        import datetime

        self.mod.apply_keep(path, datetime.date(2026, 8, 25))

        text = path.read_text()
        self.assertIn('reviewed: 2026-08-25', text)
        self.assertIn('status: To Apply', text)
        self.assertIn('keep_intent: apply', text)

    def test_apply_keep_reverts_a_prior_decline_in_the_same_session(self):
        """Regression: revisiting a declined card (via Prev/Next) and hitting
        Keep must flip status back to To Apply, not just stamp reviewed."""
        import datetime
        path = self._write_listing('acme-backend')

        self.mod.apply_decline(path, 'stack_gap', 'no Rust', datetime.date(2026, 8, 25))
        self.assertIn('status: Skipped', path.read_text())

        self.mod.apply_keep(path, datetime.date(2026, 8, 25))

        self.assertIn('status: To Apply', path.read_text())

    def test_apply_keep_reconsider_tags_intent_and_leaves_status_to_apply(self):
        path = self._write_listing('acme-backend')
        import datetime

        self.mod.apply_keep(path, datetime.date(2026, 8, 25), intent='reconsider')

        text = path.read_text()
        self.assertIn('status: To Apply', text)
        self.assertIn('keep_intent: reconsider', text)
        self.assertIn('reviewed: 2026-08-25', text)
        self.assertIn('Marked to reconsider later', text)

    def test_apply_decline_sets_skipped_and_logs_reason(self):
        path = self._write_listing('acme-backend', blurb='needs Rust experience')
        import datetime

        self.mod.apply_decline(path, 'stack_gap', 'no Rust experience', datetime.date(2026, 8, 25))

        text = path.read_text()
        self.assertIn('status: Skipped', text)
        self.assertIn('decline_reason: stack_gap', text)
        self.assertIn('reviewed: 2026-08-25', text)
        self.assertIn('Tech-stack gap', text)
        self.assertIn('no Rust experience', text)

    def test_apply_keep_clears_stale_decline_reason(self):
        """Regression: flipping a previously-declined listing back to Keep
        must not leave a contradictory decline_reason behind."""
        path = self._write_listing('acme-backend')
        import datetime

        self.mod.apply_decline(path, 'stack_gap', 'no Rust', datetime.date(2026, 8, 25))
        self.assertIn('decline_reason: stack_gap', path.read_text())

        self.mod.apply_keep(path, datetime.date(2026, 8, 25))
        self.assertNotIn('decline_reason', path.read_text())

    def test_apply_decline_clears_stale_keep_intent(self):
        """Symmetric regression: flipping a previously-kept listing to
        Decline must not leave a contradictory keep_intent behind."""
        path = self._write_listing('acme-backend')
        import datetime

        self.mod.apply_keep(path, datetime.date(2026, 8, 25), intent='reconsider')
        self.assertIn('keep_intent: reconsider', path.read_text())

        self.mod.apply_decline(path, 'stack_gap', 'no Rust', datetime.date(2026, 8, 25))
        self.assertNotIn('keep_intent', path.read_text())

    def test_apply_decline_escapes_pipe_in_note(self):
        """A literal `|` in a free-text note must not survive into the
        Communications table row — it would split into an extra column and
        corrupt every downstream naive `split('|')` parser."""
        path = self._write_listing('acme-backend')
        import datetime

        self.mod.apply_decline(path, 'stack_gap', 'no Go | Rust experience', datetime.date(2026, 8, 25))

        text = path.read_text()
        comm_line = [ln for ln in text.splitlines() if 'Declined in triage' in ln][0]
        self.assertEqual(comm_line.count('|'), 6)  # exactly the 5 table-cell delimiters
        self.assertIn('no Go ｜ Rust experience', text)

    def test_decline_log_append_and_pop_round_trips(self):
        entry = {'id': 'aaa11111', 'date': '2026-08-25', 'file': 'acme-backend.md', 'company': 'Acme',
                  'role': 'Backend Engineer', 'reason': 'stack_gap', 'note': 'no Rust'}

        self.mod.append_decline_log(entry)
        logged = self.mod._read_decline_log()
        self.assertEqual(logged['declines'], [entry])

        popped = self.mod.pop_decline_log()
        self.assertEqual(popped, entry)

    def test_read_decline_log_backfills_id_on_legacy_entries(self):
        """Regression: entries written before the `id` field existed must get
        one lazily on read, or --mark-seen could never dismiss them."""
        legacy_entry = {'date': '2026-08-25', 'file': 'acme-backend.md', 'company': 'Acme',
                         'role': 'Backend Engineer', 'reason': 'company_fit', 'note': ''}
        self.mod.append_decline_log(legacy_entry)

        logged = self.mod._read_decline_log()

        self.assertTrue(logged['declines'][0].get('id'))
        # The backfill must also persist to disk, not just the in-memory copy.
        reloaded = self.mod._read_decline_log()
        self.assertEqual(reloaded['declines'][0]['id'], logged['declines'][0]['id'])

    def test_pop_decline_log_on_empty_log_returns_none(self):
        self.assertIsNone(self.mod.pop_decline_log())

    def test_resolve_listing_rejects_path_traversal(self):
        self._write_listing('acme-backend')
        outside = Path(self._tmp) / 'secret.txt'
        outside.write_text('nope')

        self.assertIsNone(self.mod.resolve_listing('../secret.txt'))
        self.assertIsNone(self.mod.resolve_listing('nonexistent.md'))
        self.assertIsNone(self.mod.resolve_listing(''))
        resolved = self.mod.resolve_listing('acme-backend.md')
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, 'acme-backend.md')


class TriageServerHttpTest(unittest.TestCase):
    """End-to-end: real HTTPServer in a background thread, real HTTP calls —
    covers the layer the pure-function tests above don't (routing, JSON
    (de)serialization, the session-counter undo bookkeeping)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='triage_test_http_')
        os.environ['JOB_SEARCH_DATA'] = self._tmp
        for mod in ('triage_server', 'mark_stale', 'lib'):
            sys.modules.pop(mod, None)
        import triage_server
        self.mod = triage_server
        self.listings_dir = Path(self._tmp) / 'listings'
        self.listings_dir.mkdir(parents=True)
        (self.listings_dir / 'acme-backend.md').write_text(LISTING_TEMPLATE.format(
            company='Acme', role='Backend Engineer', status='To Apply',
            slug='acme-backend', first_added='2026-08-01', blurb='great fit',
        ))

        from http.server import HTTPServer
        self.server = HTTPServer(('127.0.0.1', 0), self.mod.TriageHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop('JOB_SEARCH_DATA', None)

    def _post(self, path, payload):
        req = urllib.request.Request(
            f'http://127.0.0.1:{self.port}{path}',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def test_decline_then_undo_restores_file_and_log_and_counters(self):
        res = self._post('/api/decide', {
            'file': 'acme-backend.md', 'action': 'decline', 'reason': 'stack_gap',
            'note': 'no Rust', 'company': 'Acme', 'role': 'Backend Engineer',
        })
        self.assertTrue(res['ok'])
        self.assertEqual(self.mod._session_counts['declined'], 1)
        self.assertIn('status: Skipped', (self.listings_dir / 'acme-backend.md').read_text())
        self.assertEqual(len(self.mod._read_decline_log()['declines']), 1)

        undo_res = self._post('/api/undo', {})
        self.assertTrue(undo_res['ok'])
        self.assertEqual(self.mod._session_counts['declined'], 0)
        self.assertIn('status: To Apply', (self.listings_dir / 'acme-backend.md').read_text())
        self.assertEqual(self.mod._read_decline_log()['declines'], [])

    def test_decide_on_unknown_file_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post('/api/decide', {'file': 'does-not-exist.md', 'action': 'keep'})
        self.assertEqual(ctx.exception.code, 404)

    def test_keep_defaults_to_apply_intent(self):
        res = self._post('/api/decide', {'file': 'acme-backend.md', 'action': 'keep'})
        self.assertTrue(res['ok'])
        self.assertEqual(self.mod._session_counts['apply'], 1)
        self.assertEqual(self.mod._session_counts['reconsider'], 0)
        self.assertIn('keep_intent: apply', (self.listings_dir / 'acme-backend.md').read_text())

    def test_keep_reconsider_then_undo_restores_file_and_counters(self):
        res = self._post('/api/decide', {'file': 'acme-backend.md', 'action': 'keep', 'intent': 'reconsider'})
        self.assertTrue(res['ok'])
        self.assertEqual(self.mod._session_counts['reconsider'], 1)
        text = (self.listings_dir / 'acme-backend.md').read_text()
        self.assertIn('keep_intent: reconsider', text)
        self.assertIn('status: To Apply', text)

        undo_res = self._post('/api/undo', {})
        self.assertTrue(undo_res['ok'])
        self.assertEqual(self.mod._session_counts['reconsider'], 0)
        self.assertNotIn('keep_intent', (self.listings_dir / 'acme-backend.md').read_text())

    def test_keep_with_unknown_intent_returns_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post('/api/decide', {'file': 'acme-backend.md', 'action': 'keep', 'intent': 'bogus'})
        self.assertEqual(ctx.exception.code, 400)

    def test_redeciding_same_kind_does_not_double_count(self):
        """Regression: revisiting a card via Prev/Next and re-clicking the
        same outcome must not inflate the session tally."""
        self._post('/api/decide', {'file': 'acme-backend.md', 'action': 'keep', 'intent': 'apply'})
        self._post('/api/decide', {'file': 'acme-backend.md', 'action': 'keep', 'intent': 'apply'})
        self.assertEqual(self.mod._session_counts['apply'], 1)

    def test_changing_bucket_nets_counts_instead_of_double_counting(self):
        """A listing moved from one bucket to another (Change status, or a
        Prev/Next re-decide) should still count as one listing, not two."""
        self._post('/api/decide', {'file': 'acme-backend.md', 'action': 'keep', 'intent': 'apply'})
        self._post('/api/decide', {'file': 'acme-backend.md', 'action': 'keep', 'intent': 'reconsider'})
        self.assertEqual(self.mod._session_counts['apply'], 0)
        self.assertEqual(self.mod._session_counts['reconsider'], 1)

    def test_revert_restores_pristine_file_removes_log_entry_and_count(self):
        original = (self.listings_dir / 'acme-backend.md').read_text()

        res = self._post('/api/decide', {
            'file': 'acme-backend.md', 'action': 'decline', 'reason': 'stack_gap',
            'note': 'no Rust', 'company': 'Acme', 'role': 'Backend Engineer',
        })
        self.assertTrue(res['ok'])
        self.assertEqual(len(self.mod._read_decline_log()['declines']), 1)

        revert_res = self._post('/api/revert', {'file': 'acme-backend.md'})
        self.assertTrue(revert_res['ok'])
        self.assertEqual((self.listings_dir / 'acme-backend.md').read_text(), original)
        self.assertEqual(self.mod._read_decline_log()['declines'], [])
        self.assertEqual(self.mod._session_counts['declined'], 0)

    def test_revert_on_undecided_file_returns_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post('/api/revert', {'file': 'acme-backend.md'})
        self.assertEqual(ctx.exception.code, 400)

    def test_redeclining_replaces_decline_log_entry_instead_of_stacking(self):
        """A file declined twice in a row (revisited and re-declined with a
        different reason, without ever being Kept in between) must leave one
        decline-log entry reflecting the final call — not two — so
        job-search step 0b sees one decline per listing, not an inflated
        repeat count. Revert then clears that single entry."""
        self._post('/api/decide', {
            'file': 'acme-backend.md', 'action': 'decline', 'reason': 'stack_gap',
            'note': 'no Rust', 'company': 'Acme', 'role': 'Backend Engineer',
        })
        self._post('/api/decide', {
            'file': 'acme-backend.md', 'action': 'decline', 'reason': 'other',
            'note': 'changed my mind', 'company': 'Acme', 'role': 'Backend Engineer',
        })
        declines = self.mod._read_decline_log()['declines']
        self.assertEqual(len(declines), 1)
        self.assertEqual(declines[0]['reason'], 'other')
        self.assertEqual(self.mod._session_counts['declined'], 1)

        self._post('/api/revert', {'file': 'acme-backend.md'})
        self.assertEqual(self.mod._read_decline_log()['declines'], [])
        self.assertEqual(self.mod._session_counts['declined'], 0)

    def test_redeclining_does_not_stack_communications_rows(self):
        """Each re-decide rewrites the single triage Communications row
        rather than appending another — the audit trail shows the final
        decision once."""
        path = self.listings_dir / 'acme-backend.md'
        self._post('/api/decide', {
            'file': 'acme-backend.md', 'action': 'decline', 'reason': 'stack_gap',
            'note': 'no Rust', 'company': 'Acme', 'role': 'Backend Engineer',
        })
        self._post('/api/decide', {
            'file': 'acme-backend.md', 'action': 'decline', 'reason': 'other',
            'note': 'changed my mind', 'company': 'Acme', 'role': 'Backend Engineer',
        })
        triage_rows = [ln for ln in path.read_text().splitlines() if '(triage)' in ln]
        self.assertEqual(len(triage_rows), 1)
        self.assertIn('changed my mind', triage_rows[0])
        self.assertNotIn('no Rust', path.read_text())

    def test_flip_to_apply_drops_the_prior_decline_communications_row(self):
        """Decline -> Apply must not leave a 'Declined in triage' row under a
        listing that's now status: To Apply."""
        path = self.listings_dir / 'acme-backend.md'
        self._post('/api/decide', {
            'file': 'acme-backend.md', 'action': 'decline', 'reason': 'stack_gap',
            'note': 'no Rust', 'company': 'Acme', 'role': 'Backend Engineer',
        })
        self._post('/api/decide', {'file': 'acme-backend.md', 'action': 'keep', 'intent': 'apply'})
        text = path.read_text()
        self.assertNotIn('(triage)', text)
        self.assertNotIn('Declined in triage', text)
        self.assertIn('status: To Apply', text)

    def test_decide_with_unknown_reason_returns_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post('/api/decide', {
                'file': 'acme-backend.md', 'action': 'decline', 'reason': 'bogus',
                'company': 'Acme', 'role': 'Backend Engineer',
            })
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.mod._read_decline_log()['declines'], [])
        self.assertIn('status: To Apply', (self.listings_dir / 'acme-backend.md').read_text())

    def test_undo_preserves_a_communications_row_added_after_the_decision(self):
        """Regression: undo/revert restore the touched frontmatter fields
        surgically, not by overwriting the whole file — so a Communications
        row appended (by a Gmail/WhatsApp sync, or by hand) between the
        decision and the undo is not silently discarded."""
        path = self.listings_dir / 'acme-backend.md'
        self._post('/api/decide', {
            'file': 'acme-backend.md', 'action': 'decline', 'reason': 'stack_gap',
            'note': 'no Rust', 'company': 'Acme', 'role': 'Backend Engineer',
        })
        # Simulate an external edit landing while the review session is open.
        external_row = '| 2026-08-26 | email | in | recruiter@example.com | Follow-up from recruiter |'
        path.write_text(self.mod.append_comm_row(path.read_text(), external_row))

        undo_res = self._post('/api/undo', {})
        self.assertTrue(undo_res['ok'])
        text = path.read_text()
        self.assertIn(external_row, text)          # external edit survived
        self.assertIn('status: To Apply', text)    # decision was still undone
        self.assertNotIn('decline_reason', text)
        self.assertNotIn('Declined in triage', text)

    def test_keeping_a_declined_listing_removes_its_decline_log_entry(self):
        """Regression: re-deciding Declined -> Apply/Reconsider must clean
        up the stale decline-log entry immediately, not just when Removed
        via the Review panel — otherwise job-search step 0b would still
        suggest blocklisting a company the user just decided to apply to."""
        self._post('/api/decide', {
            'file': 'acme-backend.md', 'action': 'decline', 'reason': 'company_fit',
            'note': '', 'company': 'Acme', 'role': 'Backend Engineer',
        })
        self.assertEqual(len(self.mod._read_decline_log()['declines']), 1)

        res = self._post('/api/decide', {'file': 'acme-backend.md', 'action': 'keep', 'intent': 'apply'})
        self.assertTrue(res['ok'])
        self.assertEqual(self.mod._read_decline_log()['declines'], [])
        self.assertEqual(self.mod._session_counts['declined'], 0)
        self.assertEqual(self.mod._session_counts['apply'], 1)

    def test_undo_after_keeping_a_declined_listing_restores_its_decline_log_entry(self):
        self._post('/api/decide', {
            'file': 'acme-backend.md', 'action': 'decline', 'reason': 'company_fit',
            'note': '', 'company': 'Acme', 'role': 'Backend Engineer',
        })
        self._post('/api/decide', {'file': 'acme-backend.md', 'action': 'keep', 'intent': 'apply'})
        self.assertEqual(self.mod._read_decline_log()['declines'], [])

        undo_res = self._post('/api/undo', {})
        self.assertTrue(undo_res['ok'])
        self.assertEqual(len(self.mod._read_decline_log()['declines']), 1)
        self.assertEqual(self.mod._session_counts['declined'], 1)
        self.assertEqual(self.mod._session_counts['apply'], 0)
        self.assertIn('status: Skipped', (self.listings_dir / 'acme-backend.md').read_text())

    def test_page_includes_live_stats_strip_wired_into_every_mutation_point(self):
        # No JS engine here, so this can't assert on rendered numbers — it's
        # a regression guard that the live-stats markup ships and stays
        # wired into every place client-side `decisions` changes (render(),
        # changeStatus(), revertItem()); undo() is covered indirectly since
        # it already calls render(). Losing any of these would silently
        # freeze the dashboard instead of erroring.
        html = self.mod.render_page([{
            'file': 'acme-backend.md', 'company': 'Acme', 'role': 'Backend Engineer',
            'location': 'Berlin', 'level': 'Senior', 'type': 'Backend',
            'url': '', 'first_added': '2026-08-01', 'blurb': 'great fit',
        }])
        self.assertIn('id="live-stats"', html)
        self.assertIn('function renderStats()', html)
        render_body = html.split('function render()', 1)[1].split('function renderDone()', 1)[0]
        self.assertIn('renderStats();', render_body)
        change_status_body = html.split('async function changeStatus(', 1)[1].split('async function revertItem(', 1)[0]
        self.assertIn('renderStats();', change_status_body)
        revert_item_body = html.split('async function revertItem(', 1)[1].split('function renderDone()', 1)[0]
        self.assertIn('renderStats();', revert_item_body)


if __name__ == '__main__':
    unittest.main()
