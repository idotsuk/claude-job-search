#!/usr/bin/env python3
"""Regression test for generate_run_report.py / generate_chart.py STATUS_ORDER drift.

Bug: each script kept its own private copy of STATUS_ORDER/STATUS_COLORS.
generate_chart.py's copy included 'Passed' (a real status set by /apply
when the user declines a role); generate_run_report.py's copy didn't, so
Passed listings were silently dropped from the HTML report's "Pipeline
snapshot" stats strip with no warning.

Fix: both scripts now import STATUS_ORDER/STATUS_COLORS from lib.py
instead of hardcoding their own. This test locks that in two ways:
1. Both scripts' STATUS_ORDER/STATUS_COLORS are literally lib's (identity,
   not just equality) — so a future private re-declaration would be a
   noticeable diff, not a silent drift.
2. status_summary_strip() actually renders a status ('Passed') that isn't
   one of the original hand-picked statuses in either list.

Run: python3 tests/test_status_order_sync.py
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / 'scripts'

try:
    import yaml  # noqa: F401
except ImportError:
    import json
    fake_yaml = types.ModuleType('yaml')
    fake_yaml.safe_load = json.loads
    fake_yaml.dump = json.dumps
    sys.modules['yaml'] = fake_yaml

try:
    import markdown  # noqa: F401
except ImportError:
    fake_markdown = types.ModuleType('markdown')

    class _FakeMarkdown:
        def __init__(self, *a, **k):
            pass

        def convert(self, text):
            return text

    fake_markdown.Markdown = _FakeMarkdown
    sys.modules['markdown'] = fake_markdown

try:
    import matplotlib  # noqa: F401
except ImportError:
    fake_mpl = types.ModuleType('matplotlib')
    fake_mpl.use = lambda *a, **k: None
    fake_pyplot = types.ModuleType('matplotlib.pyplot')
    fake_patches = types.ModuleType('matplotlib.patches')
    sys.modules['matplotlib'] = fake_mpl
    sys.modules['matplotlib.pyplot'] = fake_pyplot
    sys.modules['matplotlib.patches'] = fake_patches

sys.path.insert(0, str(SCRIPTS_DIR))


class StatusOrderSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import lib
        import generate_chart
        import generate_run_report
        cls.lib = lib
        cls.generate_chart = generate_chart
        cls.generate_run_report = generate_run_report

    def test_both_scripts_share_lib_status_order(self):
        self.assertIs(self.generate_chart.STATUS_ORDER, self.lib.STATUS_ORDER)
        self.assertIs(self.generate_run_report.STATUS_ORDER, self.lib.STATUS_ORDER)

    def test_both_scripts_share_lib_status_colors(self):
        self.assertIs(self.generate_chart.STATUS_COLORS, self.lib.STATUS_COLORS)
        self.assertIs(self.generate_run_report.STATUS_COLORS, self.lib.STATUS_COLORS)

    def test_passed_status_appears_in_report_stats_strip(self):
        listings = [
            {'status': 'Applied'},
            {'status': 'Passed'},
            {'status': 'Passed'},
            {'status': 'Rejected'},
        ]
        html_out = self.generate_run_report.status_summary_strip(listings)
        self.assertIn('Passed', html_out, 'Passed status silently dropped from stats strip')
        self.assertIn('<div class="stat-num">2</div>', html_out)


if __name__ == '__main__':
    unittest.main()
