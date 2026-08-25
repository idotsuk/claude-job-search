#!/usr/bin/env python3
"""
Clear CV match scores from listing frontmatter, to force a rescore.

Two modes:
  --all              Clear match_score/verdict/confidence/computed from every
                      listing under data/listings/ that currently has one set.
  (stdin JSON array)  Clear only the named files, e.g.:
                      echo '["acme-senior-ml-engineer.md"]' | python3 scripts/clear_match_scores.py

Surgically removes match_score / match_verdict / match_confidence /
match_computed lines from each file's frontmatter — never rewrites the whole
file, so key order/comments/body/Communications table stay untouched. Any
other frontmatter field is left exactly as-is, including `status` (a
Rejected/Applied/etc. listing with an old score can be cleared and rescored
too — rescoring doesn't imply moving it back to To Apply).

Run with: python3 scripts/clear_match_scores.py --all
          python3 scripts/clear_match_scores.py < filenames.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lib

LISTINGS_DIR = lib.data_root() / 'listings'
MATCH_FIELDS = ('match_score', 'match_verdict', 'match_confidence', 'match_computed')


def split_frontmatter(text):
    if not text.startswith('---'):
        return None
    end = text.find('\n---', 4)
    if end < 0:
        return None
    return text[4:end]


def clear_fields(text):
    """Remove each match_* field's line from frontmatter, if present."""
    fm_raw = split_frontmatter(text)
    if fm_raw is None:
        return text, False
    new_fm = fm_raw
    changed = False
    for field in MATCH_FIELDS:
        new_fm, n = re.subn(rf'^{re.escape(field)}:.*\n?', '', new_fm, count=1, flags=re.M)
        if n:
            changed = True
    if not changed:
        return text, False
    new_fm = new_fm.rstrip('\n')  # last remaining field must not carry a trailing blank line before ---
    fm_end = text.find('\n---', 4)
    return text[:4] + new_fm + text[fm_end:], True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true', help='Clear every scored listing, not just named ones.')
    args = ap.parse_args()

    if not LISTINGS_DIR.exists():
        print(f'No listings dir at {LISTINGS_DIR}.', file=sys.stderr)
        sys.exit(2)

    if args.all:
        targets = sorted(LISTINGS_DIR.glob('*.md'))
    else:
        raw = sys.stdin.read()
        try:
            names = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f'Invalid JSON input: {e}', file=sys.stderr)
            sys.exit(2)
        if not isinstance(names, list):
            print('Expected a JSON array of filenames, or use --all.', file=sys.stderr)
            sys.exit(2)
        targets = [LISTINGS_DIR / n for n in names]

    cleared, missing, untouched = [], [], []
    for path in targets:
        if not path.exists():
            missing.append(path.name)
            continue
        text = path.read_text()
        new_text, changed = clear_fields(text)
        if changed:
            path.write_text(new_text)
            cleared.append(path.name)
        else:
            untouched.append(path.name)

    for name in cleared:
        print(f'  cleared  {name}')
    if missing:
        print('\nNot found:', file=sys.stderr)
        for name in missing:
            print(f'  {name}', file=sys.stderr)

    print(f'\nCleared {len(cleared)} listing(s).'
          + (f' {len(untouched)} had no score to clear.' if untouched else '')
          + (f' {len(missing)} not found.' if missing else ''))


if __name__ == '__main__':
    main()
