#!/usr/bin/env python3
"""
Per-source yield report for the job-search registry.

Reads sources.yaml + the listings tree, counts how many listings cite each
source id in their `source:` frontmatter, and prints a per-source breakdown
including consecutive zero-yield streaks (drives demotion).

Usage: python3 scripts/source_yield.py [--since YYYY-MM-DD]
       python3 scripts/source_yield.py --since 2026-06-01
"""

import argparse
import datetime
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import lib

LISTINGS_DIR = lib.data_root() / 'listings'
SOURCES_FILE = lib.data_root() / 'sources.yaml'


def parse_frontmatter(text):
    if not text.startswith('---'):
        return None
    end = text.find('\n---', 4)
    if end < 0:
        return None
    try:
        fm = yaml.safe_load(text[4:end])
    except Exception:
        return None
    return fm if isinstance(fm, dict) else None


def normalize_source(raw, canonical_ids=None):
    """Best-effort normalization of free-form source strings to registered IDs.
    `canonical_ids` should be the set of all sources.yaml IDs (sources + probation);
    pass it in from main() so new sources are auto-recognized without code edits.
    Falls back to '<unregistered:...>' so we can spot listings that need
    source: backfilled."""
    if not raw:
        return '<unset>'
    s = str(raw).lower()
    canonical = canonical_ids or set()
    norm = s.replace(' ', '_').replace('-', '_')
    if norm in canonical:
        return norm
    for c in canonical:
        if c in norm:
            return c
    # heuristic mapping for legacy free-form values
    if 'linkedin' in s:
        return 'linkedin_authenticated'
    if 'gmail' in s or 'comeet' in s or 'greenhouse' in s or 'workday' in s or 'ashby' in s:
        return 'gmail_inbox'
    if 'whatsapp' in s:
        return 'whatsapp_db'
    if 'recruiter' in s or 'agency' in s:
        return 'gmail_inbox'  # recruiter-sourced thread always shows up in gmail
    if 'careers' in s or 'company' in s:
        return 'company_careers_direct'
    return f'<unregistered:{raw[:40]}>'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--since', default=None,
                        help='Only count listings with first_added >= this date (YYYY-MM-DD)')
    args = parser.parse_args()

    since = None
    if args.since:
        try:
            since = datetime.date.fromisoformat(args.since)
        except ValueError:
            print(f'Bad --since: {args.since}', file=sys.stderr)
            sys.exit(2)

    if not SOURCES_FILE.exists():
        print(f'No registry at {SOURCES_FILE}', file=sys.stderr)
        sys.exit(2)
    registry = yaml.safe_load(SOURCES_FILE.read_text()) or {}
    canonical_ids = {s['id'] for s in registry.get('sources', [])} | \
                    {s['id'] for s in registry.get('probation', [])}

    counts = Counter()
    for p in sorted(LISTINGS_DIR.glob('*.md')):
        if p.name == 'README.md':
            continue
        fm = parse_frontmatter(p.read_text())
        if not fm:
            continue
        if since:
            fa = fm.get('first_added')
            try:
                fa_d = datetime.date.fromisoformat(str(fa)[:10]) if fa else None
            except ValueError:
                fa_d = None
            if not fa_d or fa_d < since:
                continue
        src = normalize_source(fm.get('source'), canonical_ids)
        counts[src] += 1

    registered_ids = {s['id'] for s in registry.get('sources', [])}
    probation_ids = {s['id'] for s in registry.get('probation', [])}

    window = f'(first_added >= {since})' if since else '(all time)'
    print(f'\nSource yield {window}\n' + '─' * 60)
    rows = []
    for sid, n in counts.most_common():
        if sid in registered_ids:
            tier = next(s.get('tier', '?') for s in registry['sources'] if s['id'] == sid)
            rows.append((n, sid, f'tier-{tier}'))
        elif sid in probation_ids:
            rows.append((n, sid, 'probation'))
        else:
            rows.append((n, sid, 'unregistered/legacy'))
    for n, sid, tag in rows:
        print(f'  {n:>5}  {sid:<40} [{tag}]')

    # Sources in registry that surfaced 0 listings in window
    zero = [s['id'] for s in registry.get('sources', []) if s['id'] not in counts]
    if zero:
        print(f'\n0-yield in window:')
        for sid in zero:
            cz = next(s.get('consecutive_zeroes', 0) for s in registry['sources'] if s['id'] == sid)
            flag = '  ← candidate for demotion' if cz >= 4 else ''
            print(f'  {sid:<40} (cz={cz}){flag}')

    total = sum(counts.values())
    print(f'\nTotal listings counted: {total}\n')


if __name__ == '__main__':
    main()
