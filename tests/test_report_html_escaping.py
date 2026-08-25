#!/usr/bin/env python3
"""Regression test for generate_run_report.py's run-report body rendering.

Bug: generate() converted run['body'] (agent-written prose that quotes
scraped/untrusted content -- job titles, company names, recruiter message
snippets) with markdown.Markdown().convert() and inserted the result into
the HTML template with no escaping. python-markdown passes raw HTML in its
source through unmodified (no safe_mode since 3.0), so any <script> or
onerror= payload sitting in run-N.md's body would execute when the report
was opened -- contradicting SKILL.md's "safe to share or open offline"
contract. Every other dynamic field in the same template.format() call
(date, version, stem, and everything render_pipeline_table/render_stale_table
emit) was already html.escape()'d; report_html was the one field that wasn't.

Fix: html.escape(body) before markdown conversion, so raw HTML in the
source is neutralized before python-markdown ever sees it.

Run: python3 tests/test_report_html_escaping.py
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
    _REAL_MARKDOWN = True
except ImportError:
    _REAL_MARKDOWN = False
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


class ReportHtmlEscapingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import generate_run_report
        cls.generate_run_report = generate_run_report

    def _render(self, body):
        run = {
            'n': 7,
            'stem': 'run-7',
            'fm': {'date': '2026-08-24', 'version': 'bare-v1.0'},
            'body': body,
        }
        return self.generate_run_report.generate(run, listings=[], chart_b64=None)

    def test_script_tag_in_scraped_content_is_not_executable(self):
        body = (
            'Summary: added listing for **Acme Corp** -- role title '
            '<script>alert(document.cookie)</script> scraped from LinkedIn.'
        )
        out = self._render(body)
        self.assertNotIn('<script>alert(document.cookie)</script>', out)
        self.assertIn('&lt;script&gt;', out)

    def test_event_handler_attribute_is_not_live(self):
        body = 'New role: <img src=x onerror=alert(1)> Staff Engineer'
        out = self._render(body)
        self.assertNotIn('<img src=x onerror=alert(1)>', out)

    @unittest.skipUnless(_REAL_MARKDOWN, 'requires the real markdown package to verify conversion')
    def test_markdown_syntax_still_renders(self):
        body = 'Added **Acme Corp** listing.'
        out = self._render(body)
        self.assertIn('<strong>Acme Corp</strong>', out)


if __name__ == '__main__':
    unittest.main()
