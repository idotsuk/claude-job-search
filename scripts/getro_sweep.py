#!/usr/bin/env python3
"""Getro VC-portfolio job-board scraper.

Many VC portfolio boards run on Getro. They share DOM: a keyword input, a
location input, '.job-card' tiles, and a 'Load more' button for pagination
(no &start=N URL).

Usage:
    python3 scripts/getro_sweep.py <BOARD_URL> [--keyword KW] [--location LOC]

Stdout: JSON array of {company, title, location, url, posted}.
Stderr: progress log.

Examples:
    python3 scripts/getro_sweep.py https://jobs.examplevc.com/jobs
    python3 scripts/getro_sweep.py https://someboard.getro.com/jobs --keyword 'machine learning'
"""

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import lib

CFG = lib.load_config()
SESSION = lib.session_dir('getro')
CHROME = lib.cfg_get(
    CFG, 'integrations.browser.chrome_path',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
MAX_LOAD_MORE = 80  # safety cap; each click loads ~20 cards


EXTRACT_JS = """() => {
    const out = [];
    const seen = new Set();
    document.querySelectorAll('.job-card, [data-testid="job-list-item"]').forEach(card => {
        const titleEl = card.querySelector('[data-testid="job-title-link"]');
        if (!titleEl) return;
        const href = (titleEl.href || '').split('?')[0].split('#')[0];
        if (!href || seen.has(href)) return;
        seen.add(href);
        const titleText = (card.querySelector('[itemprop="title"]') || titleEl).innerText.trim();
        const companyMeta = card.querySelector('meta[itemprop="name"]');
        const companyLink = card.querySelector('[data-testid="company-link"]');
        const company = (companyMeta && companyMeta.content) || (companyLink && companyLink.innerText.trim()) || '';
        const locMeta = card.querySelector('meta[itemprop="addressLocality"]');
        const locVisible = card.querySelector('.hYaoSM');
        const location = (locMeta && locMeta.content) || (locVisible && locVisible.innerText.trim()) || '';
        const allText = card.innerText || '';
        const posted = (allText.match(/(\\d+\\s+(hour|day|week|month|year)s?\\s+ago|just\\s+posted|today|yesterday)/i) || [''])[0];
        out.push({title: titleText, company, location, posted, url: href});
    });
    return out;
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('url')
    ap.add_argument('--keyword', default='', help='Optional keyword to type into the search box')
    ap.add_argument('--location', default='', help='Location filter (post-hoc; default: first config location)')
    ap.add_argument('--keep', action='store_true', help='Leave browser open at the end')
    args = ap.parse_args()

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(SESSION),
            executable_path=CHROME,
            headless=False,
            viewport={'width': 1400, 'height': 950},
            args=['--disable-blink-features=AutomationControlled', '--window-position=-32000,-32000', '--disable-backgrounding-occluded-windows'],
        )
        page = ctx.new_page()
        page.goto(args.url, timeout=30000)
        page.wait_for_load_state('networkidle', timeout=20000)
        page.wait_for_timeout(2000)
        print(f'Loaded: {page.url}', file=sys.stderr)

        # Keyword filter (server-side, narrows the load-more burden).
        # Location is filtered post-hoc in Python because Getro's location filter
        # is a popover that doesn't always render reliably under automation.
        if args.keyword:
            try:
                kw_box = page.get_by_placeholder('Job title, company or keyword').first
                kw_box.click()
                kw_box.fill(args.keyword)
                page.keyboard.press('Enter')
                page.wait_for_timeout(2500)
                print(f'Keyword → {args.keyword!r}', file=sys.stderr)
            except Exception as e:
                print(f'No keyword input or failed: {e}', file=sys.stderr)

        page.wait_for_timeout(1500)

        # Click "Load more" repeatedly
        prev_count = 0
        for i in range(MAX_LOAD_MORE):
            current = page.evaluate("document.querySelectorAll('.job-card, [data-testid*=\"job-card\"], a[href*=\"/jobs/\"][class*=\"job\"]').length")
            if current == prev_count:
                # Try one more time after extra wait (Getro sometimes lazy-loads)
                page.wait_for_timeout(1500)
                current = page.evaluate("document.querySelectorAll('.job-card, [data-testid*=\"job-card\"], a[href*=\"/jobs/\"][class*=\"job\"]').length")
                if current == prev_count:
                    break
            btn = page.get_by_role('button', name='Load more')
            if btn.count() == 0:
                print(f'  Load-more #{i+1}: button gone, stopping', file=sys.stderr)
                break
            try:
                btn.first.scroll_into_view_if_needed(timeout=2000)
                btn.first.click(timeout=2000)
                page.wait_for_timeout(1100)
            except Exception as e:
                print(f'  Load-more #{i+1} click err: {e}', file=sys.stderr)
                break
            prev_count = current
            new_count = page.evaluate("document.querySelectorAll('.job-card, [data-testid*=\"job-card\"], a[href*=\"/jobs/\"][class*=\"job\"]').length")
            print(f'  Load-more #{i+1}: {prev_count} → {new_count}', file=sys.stderr)

        rows = page.evaluate(EXTRACT_JS)
        print(f'Extracted: {len(rows)} cards', file=sys.stderr)

        if not args.keep:
            ctx.close()

    loc = args.location or str((lib.cfg_get(CFG, 'profile.locations') or [''])[0])
    if loc:
        kept = [r for r in rows if loc.lower() in (r.get('location') or '').lower()]
        print(f'Location filter {loc!r}: {len(rows)} → {len(kept)}', file=sys.stderr)
        rows = kept
    print(json.dumps(rows, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
