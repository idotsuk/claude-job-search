#!/usr/bin/env python3
"""Generic Playwright page dump for probation smoke-tests.

Renders a URL in a persistent Chrome profile and dumps rendered text plus
all links (href + text) as JSON. For SPA pages plain HTTP fetch can't see.

Usage:
    python3 scripts/page_dump.py <URL> [--wait MS] [--scroll]
"""

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import lib

CFG = lib.load_config()
SESSION = lib.session_dir('pagedump')
CHROME = lib.cfg_get(
    CFG, 'integrations.browser.chrome_path',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('url')
    ap.add_argument('--wait', type=int, default=4000, help='ms to wait after load')
    ap.add_argument('--scroll', action='store_true', help='scroll to bottom to trigger lazy load')
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
        try:
            page.goto(args.url, timeout=45000)
        except Exception as e:
            print(json.dumps({'error': f'goto failed: {e}'}))
            ctx.close()
            return
        page.wait_for_timeout(args.wait)
        if args.scroll:
            for _ in range(15):
                page.evaluate('window.scrollBy(0, 1500)')
                page.wait_for_timeout(400)
        data = page.evaluate("""() => ({
            title: document.title,
            text: document.body ? document.body.innerText.slice(0, 30000) : '',
            links: Array.from(document.querySelectorAll('a[href]')).slice(0, 800).map(a => ({
                href: a.href, text: (a.innerText || '').trim().slice(0, 120)
            })),
        })""")
        ctx.close()
        print(json.dumps(data, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
