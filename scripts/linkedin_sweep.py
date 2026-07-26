#!/usr/bin/env python3
"""
LinkedIn authenticated jobs sweep for the /job-search skill.

Uses Playwright with a persistent Chrome profile at `data/.sessions/linkedin/`.
First run opens a visible browser window — log in once and the session
persists for future runs.

Keywords, location, posting-age window, and pagination cap come from
config.yaml (search.linkedin). Chrome executable from integrations.browser.

Usage:
  python3 scripts/linkedin_sweep.py          # JSON to stdout
  python3 scripts/linkedin_sweep.py --keep   # leave browser open at the end
"""

import argparse
import json
import sys
import time
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

import lib

CFG = lib.load_config()
SESSION_DIR = lib.session_dir('linkedin')
KEYWORDS = lib.cfg_get(CFG, 'search.linkedin.keywords') or []
LOCATION = lib.cfg_get(CFG, 'search.linkedin.location_param', '')
DEFAULT_TPR = lib.cfg_get(CFG, 'search.linkedin.time_window', 'r1209600')
MAX_PAGES = int(lib.cfg_get(CFG, 'search.linkedin.max_pages', 10))
CHROME = lib.cfg_get(
    CFG, 'integrations.browser.chrome_path',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')


def search_url(keywords, start=0, tpr=DEFAULT_TPR):
    params = {
        'keywords': keywords,
        'location': LOCATION,
        'f_TPR': tpr,
        'sortBy': 'DD',
    }
    if start:
        params['start'] = start
    return f'https://www.linkedin.com/jobs/search/?{urlencode(params)}'


def looks_logged_in(page):
    """Permissive: anything on linkedin.com that isn't a known auth page."""
    url = page.url.lower()
    if 'linkedin.com' not in url:
        return False
    bad = ('/login', '/authwall', '/uas/login', '/checkpoint', '/signup')
    return not any(b in url for b in bad)


def wait_for_login(page, timeout=420):
    page.goto('https://www.linkedin.com/feed/', timeout=30000)
    page.wait_for_timeout(2000)
    if looks_logged_in(page):
        print('Already logged in (persisted session).', file=sys.stderr)
        return True
    print('Log in via the browser window. Waiting up to 7 min...', file=sys.stderr)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if looks_logged_in(page):
            print('Login detected.', file=sys.stderr)
            return True
        time.sleep(3)
    return False


SCROLL_JS = """async () => {
    // Find the scrollable left pane that contains the job cards.
    const cands = [
        document.querySelector('div.jobs-search-results-list'),
        document.querySelector('.scaffold-layout__list > div'),
        document.querySelector('.scaffold-layout__list-container'),
        document.scrollingElement,
    ].filter(Boolean);
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const countCards = () => document.querySelectorAll(
      '.job-card-container, .jobs-search-results__list-item, .scaffold-layout__list-container li, li[data-occludable-job-id], .base-card'
    ).length;

    let prev = -1, stable = 0;
    for (let i = 0; i < 30; i++) {
        for (const el of cands) {
            try { el.scrollBy(0, 1200); } catch(e) {}
        }
        window.scrollBy(0, 1200);
        await sleep(450);
        const n = countCards();
        if (n === prev) stable++; else { stable = 0; prev = n; }
        if (stable >= 3) break;
    }
    return prev;
}"""


EXTRACT_JS = """() => {
    const out = [];
    const seen = new Set();
    const cards = document.querySelectorAll(
      '.job-card-container, .jobs-search-results__list-item, .scaffold-layout__list-container li, li[data-occludable-job-id], .base-card'
    );
    cards.forEach(card => {
        const link = card.querySelector('a.job-card-container__link, a.job-card-list__title, a.base-card__full-link, a[href*="/jobs/view/"]');
        if (!link) return;
        const href = link.href || '';
        if (!href || seen.has(href)) return;
        seen.add(href);
        const title = (card.querySelector('.job-card-list__title, .base-search-card__title, [aria-label]') || link).innerText.trim();
        const company = (card.querySelector('.job-card-container__primary-description, .job-card-container__company-name, .base-search-card__subtitle, .artdeco-entity-lockup__subtitle') || {}).innerText || '';
        const loc = (card.querySelector('.job-card-container__metadata-item, .artdeco-entity-lockup__caption, .job-search-card__location') || {}).innerText || '';
        const posted = (card.querySelector('time, .job-card-container__listed-time, .job-search-card__listdate, .job-search-card__listdate--new') || {}).innerText || '';
        out.push({
            title: title.trim(),
            company: company.trim(),
            location: loc.trim(),
            posted: posted.trim(),
            url: href.split('?')[0],
        });
    });
    return out;
}"""


def scrape_page(page):
    try:
        page.wait_for_selector(
            '.job-card-container, .jobs-search-results__list-item, '
            '.scaffold-layout__list-container li, .base-card',
            timeout=15000,
        )
    except Exception:
        return []
    page.wait_for_timeout(1200)
    try:
        page.evaluate(SCROLL_JS)
    except Exception as e:
        print(f'  scroll err: {e}', file=sys.stderr)
    return page.evaluate(EXTRACT_JS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', action='store_true', help='leave browser open at end')
    ap.add_argument('--tpr', default=DEFAULT_TPR, help='f_TPR window (e.g. r86400 for past 24h)')
    args = ap.parse_args()

    if not KEYWORDS or not LOCATION:
        print(json.dumps({'error': 'search.linkedin.keywords / location_param missing in config.yaml'}))
        sys.exit(2)

    with sync_playwright() as p:
        print(f'executable_path: {CHROME}', file=sys.stderr)
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            executable_path=CHROME,
            headless=False,
            viewport={'width': 1400, 'height': 950},
            args=['--disable-blink-features=AutomationControlled', '--window-position=-32000,-32000', '--disable-backgrounding-occluded-windows'],
        )
        page = ctx.new_page()
        page.goto('https://www.linkedin.com/jobs/', timeout=30000)
        page.wait_for_timeout(2500)

        if not looks_logged_in(page):
            if not wait_for_login(page):
                print(json.dumps({'error': 'not logged in within timeout'}))
                if not args.keep:
                    ctx.close()
                return

        all_hits = []
        for kw in KEYWORDS:
            print(f'Sweeping: {kw}', file=sys.stderr)
            kw_hits = {}
            for pg in range(MAX_PAGES):
                start = pg * 25
                page.goto(search_url(kw, start=start, tpr=args.tpr), timeout=30000)
                page.wait_for_timeout(1500)
                if 'no-results' in (page.content() or '').lower() or page.locator('.jobs-search-no-results-banner').count() > 0:
                    print(f'  page {pg + 1}: empty (no-results banner)', file=sys.stderr)
                    break
                hits = []
                for attempt in range(2):
                    try:
                        hits = scrape_page(page)
                        break
                    except Exception as e:
                        # Frame-detached races: reload and retry once
                        print(f'  page {pg + 1} attempt {attempt + 1} error: {e}', file=sys.stderr)
                        if attempt == 0:
                            try:
                                page.goto(search_url(kw, start=start, tpr=args.tpr), timeout=30000)
                                page.wait_for_timeout(2500)
                            except Exception as e2:
                                print(f'  page {pg + 1} reload error: {e2}', file=sys.stderr)
                new = sum(1 for h in hits if h['url'] not in kw_hits)
                for h in hits:
                    kw_hits.setdefault(h['url'], h)
                print(f'  page {pg + 1}: {len(hits)} on page, +{new} new, {len(kw_hits)} total', file=sys.stderr)
                if not hits or new == 0:
                    break
            for h in kw_hits.values():
                h['keyword'] = kw
            all_hits.extend(kw_hits.values())

        if not args.keep:
            ctx.close()

    print(json.dumps(all_hits, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
