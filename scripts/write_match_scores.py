#!/usr/bin/env python3
"""
Write CV match scores into listing frontmatter.

Reads a JSON array from stdin (or --input FILE) of:
  [{"listing_filename": "<slug>.md", "score": <int 0-100>,
    "verdict": "<string>", "confidence": "full"|"low"}, ...]

Surgically upserts match_score / match_verdict / match_confidence /
match_computed into each listing's frontmatter — never rewrites the whole
file, so key order/comments/body/Communications table stay untouched.

Run with: python3 scripts/write_match_scores.py < scores.json
          python3 scripts/write_match_scores.py --input scores.json
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


def parse_field(fm_raw, field):
    """Best-effort parse of a single frontmatter key (surgical, no yaml load)."""
    m = re.search(rf'^{re.escape(field)}:\s*(.+?)\s*$', fm_raw, re.M)
    if not m:
        return None
    return m.group(1).strip()


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


def yaml_escape(s):
    """Escape a string for a single-line double-quoted YAML scalar."""
    s = str(s).replace('\\', '\\\\').replace('"', '\\"')
    s = ' '.join(s.split())  # collapse newlines/whitespace to single spaces
    return s


def write_scores(entries, listings_dir, today):
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

        status = parse_field(fm_raw, 'status')
        if status != 'To Apply':
            results.append({'file': fname, 'ok': False, 'reason': f'status changed to {status!r}, skipped'})
            continue

        try:
            score = int(entry['score'])
        except (KeyError, TypeError, ValueError):
            results.append({'file': fname, 'ok': False, 'reason': f'invalid score: {entry.get("score")!r}'})
            continue
        if not (0 <= score <= 100):
            results.append({'file': fname, 'ok': False, 'reason': f'score out of range: {score}'})
            continue

        confidence = entry.get('confidence')
        if confidence not in ('full', 'low'):
            results.append({'file': fname, 'ok': False, 'reason': f'invalid confidence: {confidence!r}'})
            continue

        verdict = yaml_escape(entry.get('verdict', ''))
        if not verdict:
            results.append({'file': fname, 'ok': False, 'reason': 'empty verdict'})
            continue

        new_text = text
        new_text = upsert_field(new_text, 'match_score', str(score))
        new_text = upsert_field(new_text, 'match_verdict', f'"{verdict}"')
        new_text = upsert_field(new_text, 'match_confidence', confidence)
        new_text = upsert_field(new_text, 'match_computed', today.isoformat())
        path.write_text(new_text)
        results.append({'file': fname, 'ok': True, 'score': score, 'confidence': confidence})
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
        print('Expected a JSON array of score entries.', file=sys.stderr)
        sys.exit(2)

    if not LISTINGS_DIR.exists():
        print(f'No listings dir at {LISTINGS_DIR}.', file=sys.stderr)
        sys.exit(2)

    today = datetime.date.today()
    results = write_scores(entries, LISTINGS_DIR, today)

    ok = [r for r in results if r['ok']]
    failed = [r for r in results if not r['ok']]

    for r in ok:
        print(f"  scored {r['score']:>3}  ({r['confidence']:>4})  {r['file']}")

    if failed:
        print('\nFailed / skipped:', file=sys.stderr)
        for r in failed:
            print(f"  {r['file']}: {r['reason']}", file=sys.stderr)

    print(f'\nWrote {len(ok)} score(s).' + (f' {len(failed)} failed.' if failed else ''))

    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
