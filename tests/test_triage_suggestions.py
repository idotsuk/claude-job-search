#!/usr/bin/env python3
"""Tests for scripts/triage_suggestions.py — the read/mark-seen half of
job-search SKILL.md step 0b's triage-suggestion pass. Pattern detection
itself is prose the agent executes, not code; this only covers the
mechanical dump-as-JSON and --mark-seen tagging this script is responsible
for, against a throwaway data/ dir (same approach as test_triage_server.py).

Run: python3 tests/test_triage_suggestions.py
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
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


class TriageSuggestionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='triage_suggestions_test_data_')
        os.environ['JOB_SEARCH_DATA'] = self._tmp
        for mod in ('triage_suggestions', 'triage_server', 'mark_stale', 'lib'):
            sys.modules.pop(mod, None)
        import triage_server
        import triage_suggestions
        self.server_mod = triage_server
        self.mod = triage_suggestions

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop('JOB_SEARCH_DATA', None)

    def _run_main(self, argv):
        old_argv = sys.argv
        sys.argv = ['triage_suggestions.py'] + argv
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                self.mod.main()
        finally:
            sys.argv = old_argv
        return buf.getvalue()

    def test_dump_with_no_args_returns_no_entries_when_log_missing(self):
        out = self._run_main([])
        self.assertEqual(json.loads(out), [])

    def test_dump_returns_all_entries_including_id(self):
        self.server_mod.append_decline_log({
            'id': 'aaa11111', 'date': '2026-08-25', 'file': 'acme-backend.md',
            'company': 'Acme', 'role': 'Backend Engineer', 'reason': 'company_fit', 'note': '',
        })

        out = self._run_main([])

        entries = json.loads(out)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['id'], 'aaa11111')
        self.assertNotIn('suggested', entries[0])

    def test_mark_seen_tags_only_matching_entries(self):
        self.server_mod.append_decline_log({
            'id': 'aaa11111', 'date': '2026-08-25', 'file': 'acme-backend.md',
            'company': 'Acme', 'role': 'Backend Engineer', 'reason': 'company_fit', 'note': '',
        })
        self.server_mod.append_decline_log({
            'id': 'bbb22222', 'date': '2026-08-25', 'file': 'acme-frontend.md',
            'company': 'Acme', 'role': 'Frontend Engineer', 'reason': 'company_fit', 'note': '',
        })

        marked_out = self._run_main(['--mark-seen', 'aaa11111'])
        self.assertEqual(json.loads(marked_out), {'marked': 1})

        entries = {e['id']: e for e in json.loads(self._run_main([]))}
        self.assertTrue(entries['aaa11111'].get('suggested'))
        self.assertNotIn('suggested', entries['bbb22222'])

    def test_mark_seen_unknown_id_marks_nothing_and_does_not_crash(self):
        self.server_mod.append_decline_log({
            'id': 'aaa11111', 'date': '2026-08-25', 'file': 'acme-backend.md',
            'company': 'Acme', 'role': 'Backend Engineer', 'reason': 'company_fit', 'note': '',
        })

        out = self._run_main(['--mark-seen', 'does-not-exist'])

        self.assertEqual(json.loads(out), {'marked': 0})


if __name__ == '__main__':
    unittest.main()
