#!/usr/bin/env python3
"""
Auto-demote inactive Applied/Screen/Interviewing listings to Stale.

Rule: if status is in {Applied, Screen, Interviewing} AND the most recent
activity date is more than the threshold ago, set status to Stale and
record the transition. Threshold comes from config.yaml
(pipeline.stale_after_days, default 7) or the --days flag.

Most-recent-activity is derived from (in priority order):
  1. last_update frontmatter field
  2. the latest YYYY-MM-DD date in the body (typically the Communications table)
  3. first_added frontmatter field
  4. if none of the above: treat as fully stale (undated → was never tracked)

Stale is a work pile, not a terminal state. To recover, edit the file and
set status back to Applied/Screen/Interviewing manually.

Run with: python3 scripts/mark_stale.py [--dry-run] [--days N]
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lib

LISTINGS_DIR = lib.data_root() / 'listings'

ACTIVE_STATUSES = {'Applied', 'Screen', 'Interviewing'}
DATE_RE = re.compile(r'\b(20\d\d-\d\d-\d\d)\b')


def split_frontmatter(text):
    if not text.startswith('---'):
        return None, None, text
    end = text.find('\n---', 4)
    if end < 0:
        return None, None, text
    fm_raw = text[4:end]
    body = text[end + 4:]
    if body.startswith('\n'):
        body = body[1:]
    return fm_raw, end, body


def parse_field(fm_raw, field):
    """Best-effort parse of a single frontmatter key (we avoid yaml here to
    keep edits surgical — never rewrite the whole frontmatter)."""
    m = re.search(rf'^{re.escape(field)}:\s*(.+?)\s*$', fm_raw, re.M)
    if not m:
        return None
    return m.group(1).strip()


def to_date(s):
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def latest_activity(fm_raw, body):
    """Most-recent activity date. Prose dates ("Backfilled 2026-06-07") are
    ignored — only Communications-table rows count for activity. Frontmatter
    fields are the fallback when the table is empty/missing."""
    fm_dates = []
    for field in ('last_update', 'first_added'):
        d = to_date(parse_field(fm_raw, field))
        if d:
            fm_dates.append(d)
    table_dates = []
    for line in body.splitlines():
        if not line.lstrip().startswith('|'):
            continue
        # skip header/separator rows like `|---|---|...`
        if re.match(r'\s*\|[\s|:-]+\|\s*$', line):
            continue
        for m in DATE_RE.findall(line):
            d = to_date(m)
            if d:
                table_dates.append(d)
    candidates = table_dates if table_dates else fm_dates
    return max(candidates) if candidates else None


def upsert_field(text, field, value):
    """Set or insert a frontmatter field, preserving file structure."""
    pattern = rf'^({re.escape(field)}:)\s*.*$'
    if re.search(pattern, text, re.M):
        return re.sub(pattern, rf'\1 {value}', text, count=1, flags=re.M)
    # insert before closing '---'
    closing = text.find('\n---', 4)
    if closing < 0:
        return text
    return text[:closing] + f'\n{field}: {value}' + text[closing:]


def set_status(text, new_status):
    return re.sub(r'^status:\s*.*$', f'status: {new_status}', text, count=1, flags=re.M)


def append_comm_row(text, row):
    """Append a row to the ## Communications table if one exists; else
    add the section + header + row at the end of the file."""
    if '## Communications' in text:
        # the table is the last section — append at end of file
        if not text.endswith('\n'):
            text += '\n'
        return text + row + '\n'
    if not text.endswith('\n'):
        text += '\n'
    return text + (
        '\n## Communications\n\n'
        '| Date | Channel | Direction | Contact | Summary |\n'
        '|------|---------|-----------|---------|---------|\n'
        f'{row}\n'
    )


def process_file(path, today, threshold_days, dry_run=False):
    text = path.read_text()
    fm_raw, _, body = split_frontmatter(text)
    if fm_raw is None:
        return None
    status = parse_field(fm_raw, 'status')
    if status not in ACTIVE_STATUSES:
        return None
    activity = latest_activity(fm_raw, body)
    if activity is None:
        days = None
        days_label = 'undated'
    else:
        days = (today - activity).days
        days_label = f'{days}d'
    if activity is not None and days <= threshold_days:
        return None
    # demote to Stale
    new_text = set_status(text, 'Stale')
    new_text = upsert_field(new_text, 'last_update', today.isoformat())
    comm_row = (
        f'| {today.isoformat()} | — | — | (auto) | '
        f'Auto-stale: was **{status}**, no activity for {days_label}'
        f'{" since " + activity.isoformat() if activity else ""}. '
        f'Threshold: {threshold_days} days. |'
    )
    new_text = append_comm_row(new_text, comm_row)
    if not dry_run:
        path.write_text(new_text)
    return {
        'file': path.name,
        'prev_status': status,
        'last_activity': activity.isoformat() if activity else None,
        'days_inactive': days,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--days', type=int, default=None,
                        help='Override threshold days (default: config pipeline.stale_after_days).')
    parser.add_argument('--today', default=None,
                        help='Override today (YYYY-MM-DD), for testing.')
    args = parser.parse_args()

    if args.days is not None:
        threshold_days = args.days
    else:
        cfg = lib.load_config()
        threshold_days = int(lib.cfg_get(cfg, 'pipeline.stale_after_days', 7))

    today = to_date(args.today) if args.today else datetime.date.today()
    if not today:
        print(f'Invalid --today: {args.today}', file=sys.stderr)
        sys.exit(2)

    if not LISTINGS_DIR.exists():
        print(f'No listings dir at {LISTINGS_DIR}.')
        return

    transitions = []
    for path in sorted(LISTINGS_DIR.glob('*.md')):
        if path.name == 'README.md':
            continue
        result = process_file(path, today, threshold_days, dry_run=args.dry_run)
        if result:
            transitions.append(result)

    if not transitions:
        print(f'No listings stale beyond {threshold_days}d.')
        return

    print(f'{"[dry-run] " if args.dry_run else ""}'
          f'Marked {len(transitions)} listing(s) as Stale '
          f'(threshold: {threshold_days}d, today: {today}):')
    by_status = {}
    for t in sorted(transitions, key=lambda x: (x['prev_status'], -(x['days_inactive'] or 99999))):
        by_status.setdefault(t['prev_status'], []).append(t)
    for prev, items in by_status.items():
        print(f'\n  was {prev} → Stale ({len(items)}):')
        for t in items:
            d = f'{t["days_inactive"]}d' if t['days_inactive'] is not None else 'undated'
            print(f'    {d:>8}  {t["file"]}'
                  f'{"  (last: " + t["last_activity"] + ")" if t["last_activity"] else ""}')


if __name__ == '__main__':
    main()
