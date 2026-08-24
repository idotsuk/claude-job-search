#!/usr/bin/env python3
"""Open a job's canonical URL in the persistent Chrome session, attempt to
prefill known fields from config.yaml (applicant block), then leave the
window open for the user to review custom answers and click Submit themselves.

Usage:
  python3 scripts/apply_driver.py <starting_url>
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import lib

CFG = lib.load_config()
SESSION = lib.session_dir('linkedin')
CHROME = lib.cfg_get(
    CFG, 'integrations.browser.chrome_path',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')

APPLICANT = CFG.get('applicant') or {}
CV = Path(str(APPLICANT.get('cv_path', ''))).expanduser()

STD = {
    'first_name': APPLICANT.get('first_name', ''),
    'last_name': APPLICANT.get('last_name', ''),
    'full_name': f"{APPLICANT.get('first_name', '')} {APPLICANT.get('last_name', '')}".strip(),
    'email': APPLICANT.get('email', ''),
    'phone': str(APPLICANT.get('phone', '')).replace(' ', ''),
    'linkedin': APPLICANT.get('linkedin', ''),
    'github': APPLICANT.get('github', ''),
    'city': APPLICANT.get('city', ''),
    'country': APPLICANT.get('country', ''),
}


def prefill_common(page):
    """Try common ATS field patterns. Best-effort, idempotent. Logs each fill."""
    filled = []
    skipped = []

    def try_fill(selector, value, label):
        if not value:
            return False
        try:
            locs = page.locator(selector)
            count = locs.count()
            for i in range(count):
                el = locs.nth(i)
                if not el.is_visible():
                    continue
                try:
                    el.fill(value, timeout=2000)
                    filled.append(f'{label} (sel={selector!r})')
                    return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    # First Name
    for sel in ['input[name*="first" i]', 'input[id*="first" i]', 'input[aria-label*="first name" i]', 'input[placeholder*="first name" i]']:
        if try_fill(sel, STD['first_name'], 'First Name'):
            break
    else:
        if STD['first_name']:
            skipped.append('First Name')
    # Last Name
    for sel in ['input[name*="last" i]', 'input[id*="last" i]', 'input[aria-label*="last name" i]', 'input[placeholder*="last name" i]']:
        if try_fill(sel, STD['last_name'], 'Last Name'):
            break
    else:
        if STD['last_name']:
            skipped.append('Last Name')
    # Full Name (single field)
    for sel in ['input[name="name"]', 'input[id="name"]', 'input[aria-label="Full name" i]']:
        if try_fill(sel, STD['full_name'], 'Full Name'):
            break
    else:
        if STD['full_name']:
            skipped.append('Full Name')
    # Email
    for sel in ['input[type="email"]', 'input[name*="email" i]', 'input[id*="email" i]', 'input[aria-label*="email" i]']:
        if try_fill(sel, STD['email'], 'Email'):
            break
    else:
        if STD['email']:
            skipped.append('Email')
    # Phone
    for sel in ['input[type="tel"]', 'input[name*="phone" i]', 'input[id*="phone" i]', 'input[aria-label*="phone" i]']:
        if try_fill(sel, STD['phone'], 'Phone'):
            break
    else:
        if STD['phone']:
            skipped.append('Phone')
    # LinkedIn
    for sel in ['input[name*="linkedin" i]', 'input[id*="linkedin" i]', 'input[aria-label*="linkedin" i]', 'input[placeholder*="linkedin" i]']:
        if try_fill(sel, STD['linkedin'], 'LinkedIn URL'):
            break
    else:
        if STD['linkedin']:
            skipped.append('LinkedIn URL')
    # GitHub / Website
    for sel in ['input[name*="github" i]', 'input[id*="github" i]', 'input[aria-label*="github" i]', 'input[name*="website" i]', 'input[placeholder*="website" i]']:
        if try_fill(sel, STD['github'], 'GitHub/Website'):
            break
    else:
        if STD['github']:
            skipped.append('GitHub/Website')

    # Resume upload
    try:
        file_inputs = page.locator('input[type="file"]')
        uploaded = False
        for i in range(file_inputs.count()):
            inp = file_inputs.nth(i)
            try:
                inp.set_input_files(str(CV), timeout=3000)
                filled.append(f'Resume upload → {CV.name}')
                uploaded = True
                break
            except Exception:
                pass
        if not uploaded:
            skipped.append('Resume upload')
    except Exception:
        skipped.append('Resume upload')

    return filled, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('url', help='Starting URL (job page or apply page)')
    args = ap.parse_args()

    if not APPLICANT:
        print('No applicant: block in config.yaml — fill it before running /apply.', file=sys.stderr)
        sys.exit(1)
    if not CV.exists():
        print(f'Resume not found at {CV} (config applicant.cv_path)', file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(SESSION),
            executable_path=CHROME,
            headless=False,
            viewport={'width': 1400, 'height': 950},
            args=['--disable-blink-features=AutomationControlled'],
        )
        page = ctx.new_page()
        page.goto(args.url, timeout=30000, wait_until='domcontentloaded')
        try:
            page.wait_for_load_state('load', timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(3500)
        print(f'URL after nav: {page.url}', file=sys.stderr)
        print(f'Title: {page.title()}', file=sys.stderr)

        # Try clicking "Apply" if present
        for sel in ['a:has-text("Apply")', 'button:has-text("Apply")', 'a:has-text("Apply now")', 'button:has-text("Apply now")']:
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible():
                    btn.click(timeout=4000)
                    print(f'Clicked Apply: {sel}', file=sys.stderr)
                    page.wait_for_timeout(3500)
                    break
            except Exception:
                pass

        print(f'After Apply, URL: {page.url}', file=sys.stderr)
        print('Attempting prefill...', file=sys.stderr)
        filled, skipped = prefill_common(page)
        print(f'\nFilled ({len(filled)}):', file=sys.stderr)
        for f in filled:
            print(f'  ✓ {f}', file=sys.stderr)
        if skipped:
            print('\nSkipped:', file=sys.stderr)
            for s in skipped:
                print(f'  - {s}', file=sys.stderr)

        print('\nBrowser left open. Review fields, fill custom answers, click Submit manually.', file=sys.stderr)
        # Keep running indefinitely — user closes the window manually
        try:
            while True:
                page.wait_for_timeout(10000)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
