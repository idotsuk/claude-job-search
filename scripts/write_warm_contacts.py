#!/usr/bin/env python3
"""
Flag existing listings as warm-contact roles.

Reads a JSON array from stdin (or --input FILE) of:
  [{"company": "<Company>", "contacts": ["Jane Smith (PM Director)", ...]}, ...]

For every listing in data/listings/*.md whose `company` frontmatter matches
one of the given companies (case-insensitive), surgically upserts:
  warm_contact: true
  contact_name: "<contacts joined with '; '>"

Never rewrites the whole file — same surgical-upsert convention as
scripts/write_match_scores.py, so key order/comments/body/Communications
table stay untouched. Used by /network-scan step 6 to retroactively flag
listings that predate the scan (e.g. found earlier by /job-search) at a
company where a LinkedIn contact now also works.

Run with: python3 scripts/write_warm_contacts.py < companies.json
          python3 scripts/write_warm_contacts.py --input companies.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lib

LISTINGS_DIR = lib.data_root() / 'listings'


def parse_field(fm_raw, field):
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
    s = str(s).replace('\\', '\\\\').replace('"', '\\"')
    return ' '.join(s.split())


def strip_quotes(s):
    s = (s or '').strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1]
    return s


def write_warm_contacts(entries, listings_dir):
    # Build a case-insensitive company -> contacts lookup, merging duplicate
    # company entries in the input (e.g. if the same company shows up twice
    # across contact batches).
    by_company = {}
    for entry in entries:
        company = (entry.get('company') or '').strip()
        if not company:
            continue
        key = company.lower()
        contacts = entry.get('contacts') or []
        by_company.setdefault(key, {'company': company, 'contacts': []})
        for c in contacts:
            c = str(c).strip()
            if c and c not in by_company[key]['contacts']:
                by_company[key]['contacts'].append(c)

    if not by_company:
        return []

    results = []
    for path in sorted(listings_dir.glob('*.md')):
        if path.name == 'README.md':
            continue
        text = path.read_text()
        fm_raw = split_frontmatter(text)
        if fm_raw is None:
            continue

        listing_company = strip_quotes(parse_field(fm_raw, 'company') or '')
        if not listing_company:
            continue

        match = by_company.get(listing_company.lower())
        if not match:
            continue

        contact_name = yaml_escape('; '.join(match['contacts'])) if match['contacts'] else ''

        new_text = text
        new_text = upsert_field(new_text, 'warm_contact', 'true')
        if contact_name:
            new_text = upsert_field(new_text, 'contact_name', f'"{contact_name}"')

        if new_text != text:
            path.write_text(new_text)
            results.append({
                'file': path.name,
                'company': listing_company,
                'contact_name': contact_name,
                'changed': True,
            })
        else:
            results.append({
                'file': path.name,
                'company': listing_company,
                'contact_name': contact_name,
                'changed': False,
            })
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
        print('Expected a JSON array of {"company", "contacts"} entries.', file=sys.stderr)
        sys.exit(2)

    if not LISTINGS_DIR.exists():
        print(f'No listings dir at {LISTINGS_DIR}.', file=sys.stderr)
        sys.exit(2)

    results = write_warm_contacts(entries, LISTINGS_DIR)
    changed = [r for r in results if r['changed']]

    for r in changed:
        print(f"  flagged  {r['company']:<30}  {r['file']}")

    print(f'\nFlagged {len(changed)} listing(s) as warm_contact '
          f'({len(results) - len(changed)} already up to date).')


if __name__ == '__main__':
    main()
