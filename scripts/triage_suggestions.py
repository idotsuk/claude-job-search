#!/usr/bin/env python3
"""
Read/mutate data/decline-log.yaml on behalf of job-search SKILL.md's
triage-suggestion pass. Pattern detection (grouping by company, spotting a
shared keyword across free-text notes) needs judgment over free text, so
that stays in the skill's prose, executed by the agent — this script only
does the mechanical parts, mirroring triage_server.py's own append/pop
helpers rather than reimplementing YAML handling:

  triage_suggestions.py                 -> dump every decline-log entry as
                                            JSON (stdout), including `id`
                                            and `suggested` if present
  triage_suggestions.py --mark-seen ID...  -> tag those entries
                                            `suggested: true` so a pattern
                                            built only from already-seen
                                            entries won't be re-asked about

Never touches config.yaml — that edit (on a "yes") is the skill's job.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from triage_server import DECLINE_LOG, DECLINE_LOG_HEADER, _read_decline_log


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mark-seen', nargs='+', metavar='ID', default=None,
                         help='Entry ids (from the default JSON dump) to tag suggested: true.')
    args = parser.parse_args()

    data = _read_decline_log()

    if args.mark_seen:
        ids = set(args.mark_seen)
        marked = 0
        for entry in data['declines']:
            if entry.get('id') in ids:
                entry['suggested'] = True
                marked += 1
        DECLINE_LOG.parent.mkdir(parents=True, exist_ok=True)
        DECLINE_LOG.write_text(DECLINE_LOG_HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        print(json.dumps({'marked': marked}))
        return

    print(json.dumps(data['declines']))


if __name__ == '__main__':
    main()
