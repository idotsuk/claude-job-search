#!/usr/bin/env python3
"""
Write cv_status (and cv_output_dir) into listing frontmatter after a
successful /tailor-cv render.

Reads a JSON array from stdin (or --input FILE) of:
  [{"listing_filename": "<slug>.md", "cv_status": "draft"|"approved",
    "cv_output_dir": "data/cv-outputs/<slug>"}, ...]

Surgically upserts cv_status / cv_output_dir / cv_generated into each
listing's frontmatter — never rewrites the whole file, so key order/
comments/body/Communications table stay untouched. Mirrors
scripts/write_match_scores.py's approach exactly.

Run with: python3 scripts/write_cv_status.py < statuses.json
          python3 scripts/write_cv_status.py --input statuses.json
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lib

LISTINGS_DIR = lib.data_root() / 'listings'
VALID_STATUSES = ('draft', 'approved')


def split_frontmatter(text):
    if not text.startswith('---'):
        return None
    end = text.find('\n---', 4)
    if end < 0:
        return None
    return text[4:end]


def upsert_field(text, field, value):
    """Set or insert a frontmatter field, preserving file structure."""
    pattern = rf'^({re.escape(field)}:)\s*.*$'
    if re.search(pattern, text, re.M):
        return re.sub(pattern, rf'\1 {value}', text, count=1, flags=re.M)
    closing = text.find('\n---', 4)
    if closing < 0:
        return text
    return text[:closing] + f'\n{field}: {value}' + text[closing:]


def write_statuses(entries, listings_dir, today):
    results = []
    for entry in entries:
        fname = entry.get('listing_filename', '')
        path = listings_dir / fname
        if not fname or not path.exists():
            results.append({'file': fname or '(missing filename)', 'ok': False, 'reason': 'file not found'})
            continue

        text = path.read_text()
        fm_raw = split_frontmatter(text)
        if fm_raw is None:
            results.append({'file': fname, 'ok': False, 'reason': 'no frontmatter'})
            continue

        status = entry.get('cv_status')
        if status not in VALID_STATUSES:
            results.append({'file': fname, 'ok': False, 'reason': f'invalid cv_status: {status!r}'})
            continue

        out_dir = entry.get('cv_output_dir', '')
        if not out_dir:
            results.append({'file': fname, 'ok': False, 'reason': 'missing cv_output_dir'})
            continue

        new_text = text
        new_text = upsert_field(new_text, 'cv_status', status)
        new_text = upsert_field(new_text, 'cv_output_dir', out_dir)
        new_text = upsert_field(new_text, 'cv_generated', today.isoformat())
        path.write_text(new_text)
        results.append({'file': fname, 'ok': True, 'cv_status': status})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', help='Read JSON from this file instead of stdin.')
    args = ap.parse_args()

    raw = Path(args.input).read_text() if args.input else sys.stdin.read()

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f'Invalid JSON input: {e}', file=sys.stderr)
        sys.exit(2)

    if not isinstance(entries, list):
        print('Expected a JSON array of cv_status entries.', file=sys.stderr)
        sys.exit(2)

    if not LISTINGS_DIR.exists():
        print(f'No listings dir at {LISTINGS_DIR}.', file=sys.stderr)
        sys.exit(2)

    today = datetime.date.today()
    results = write_statuses(entries, LISTINGS_DIR, today)

    ok = [r for r in results if r['ok']]
    failed = [r for r in results if not r['ok']]

    for r in ok:
        print(f"  cv_status={r['cv_status']:>8}  {r['file']}")

    if failed:
        print('\nFailed / skipped:', file=sys.stderr)
        for r in failed:
            print(f"  {r['file']}: {r['reason']}", file=sys.stderr)

    print(f'\nWrote {len(ok)} cv_status update(s).' + (f' {len(failed)} failed.' if failed else ''))

    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
