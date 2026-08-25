#!/usr/bin/env python3
"""Regression test for scripts/apply_driver.py's prefill_common().

Bug: fields whose value is configured but that fail to match any known
selector on the page were never recorded in the returned `skipped` list,
so the /apply pre-submit review ("Skipped:" block) had nothing to show
the user even when several fields silently failed to fill.

Run: python3 tests/test_apply_driver.py
"""

import json
import os
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / 'scripts'

# playwright / pyyaml may not be installed in every environment this test
# runs in; stub them just enough for apply_driver.py's module-level imports
# to succeed. Real installs are used as-is when present.
try:
    import playwright.sync_api  # noqa: F401
except ImportError:
    pw = types.ModuleType('playwright')
    pw_sync = types.ModuleType('playwright.sync_api')
    pw_sync.sync_playwright = lambda: None
    sys.modules['playwright'] = pw
    sys.modules['playwright.sync_api'] = pw_sync

try:
    import yaml  # noqa: F401
except ImportError:
    fake_yaml = types.ModuleType('yaml')
    fake_yaml.safe_load = json.loads
    fake_yaml.dump = json.dumps
    sys.modules['yaml'] = fake_yaml


class FakeLocator:
    """Stands in for a Playwright Locator that matches zero elements."""

    def count(self):
        return 0

    def nth(self, i):
        return self

    def is_visible(self):
        return True

    def fill(self, value, timeout=2000):
        raise Exception('no such element')


class FakePage:
    """A page where every selector query comes up empty — simulates an ATS
    form whose field names don't match apply_driver.py's hardcoded guesses."""

    def locator(self, selector):
        return FakeLocator()


class PrefillCommonSkippedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._config_path = Path('/tmp/apply_driver_test_config.yaml')
        cls._cv_path = Path('/tmp/apply_driver_test_cv.pdf')
        cls._config_path.write_text(json.dumps({
            'applicant': {
                'first_name': 'Jane',
                'last_name': 'Doe',
                'email': 'jane@example.com',
                'cv_path': str(cls._cv_path),
                # phone/linkedin/github/city/country deliberately left unset —
                # empty-valued fields were never attempted and must not be
                # reported as skipped (that would just be config noise).
            }
        }))
        cls._cv_path.write_text('fake cv contents')

        os.environ['JOB_SEARCH_CONFIG'] = str(cls._config_path)
        os.environ['JOB_SEARCH_DATA'] = '/tmp/apply_driver_test_data'

        sys.path.insert(0, str(SCRIPTS_DIR))
        import apply_driver
        cls.apply_driver = apply_driver

    def test_fields_that_fail_to_match_are_recorded_as_skipped(self):
        filled, skipped = self.apply_driver.prefill_common(FakePage())

        self.assertEqual(filled, [], 'sanity check: nothing should match FakePage')
        self.assertIn('First Name', skipped)
        self.assertIn('Last Name', skipped)
        self.assertIn('Email', skipped)
        self.assertIn('Resume upload', skipped)

    def test_unconfigured_optional_fields_are_not_reported_as_skipped(self):
        _, skipped = self.apply_driver.prefill_common(FakePage())

        self.assertNotIn('Phone', skipped)
        self.assertNotIn('LinkedIn URL', skipped)
        self.assertNotIn('GitHub/Website', skipped)


if __name__ == '__main__':
    unittest.main()
