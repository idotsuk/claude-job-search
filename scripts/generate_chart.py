#!/usr/bin/env python3
"""
Generate the job-search delta + status-breakdown chart at
data/reports/chart-latest.png.

Buckets each listing into a run by `first_added` date. Listings without
first_added collapse into the earliest run as a single "initial seed" bar —
we don't have per-run provenance for those.
"""

import bisect
import re
import sys
from pathlib import Path

import yaml
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

sys.path.insert(0, str(Path(__file__).parent))
import lib

DATA = lib.data_root()
OUT = DATA / 'reports' / 'chart-latest.png'

STATUS_ORDER = lib.STATUS_ORDER
STATUS_COLORS = lib.STATUS_COLORS


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


def main():
    runs = []
    for p in sorted((DATA / 'runs').glob('run-*.md')):
        fm = parse_frontmatter(p.read_text())
        if not fm:
            continue
        m = re.match(r'run-(\d+)', p.stem)
        if not m:
            continue
        runs.append({
            'n': int(m.group(1)), 'date': str(fm.get('date', '')),
            'version': str(fm.get('version', '') or ''), 'file': p.name,
        })
    runs.sort(key=lambda r: (r['date'], r['n']))
    if not runs:
        print('No runs yet — skipping chart.', file=sys.stderr)
        return

    listings = []
    status_counts = {}
    for p in (DATA / 'listings').glob('*.md'):
        if p.name == 'README.md':
            continue
        fm = parse_frontmatter(p.read_text())
        if not fm:
            continue
        fa = fm.get('first_added')
        listings.append({'first_added': str(fa) if fa else None})
        status = str(fm.get('status', '')).strip()
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
    total_n = sum(status_counts.values())

    run_dates = [r['date'] for r in runs]
    buckets = {r['file']: 0 for r in runs}
    for L in listings:
        if L['first_added']:
            idx = bisect.bisect_right(run_dates, L['first_added']) - 1
        else:
            idx = 0  # collapse undated imports into the initial run
        if idx < 0:
            idx = 0
        buckets[runs[idx]['file']] += 1

    cum = []
    running = 0
    for r in runs:
        r['new'] = buckets[r['file']]
        running += r['new']
        cum.append(running)

    fig = plt.figure(figsize=(16, 7))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.7, 1])
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])

    xs = list(range(len(runs)))
    labels = [f"#{r['n']}\n{r['date']}" for r in runs]
    bars = [r['new'] for r in runs]
    ax1.bar(xs, bars, color='#2e7d32', edgecolor='black', linewidth=0.5)
    peak = max(bars) if any(bars) else 1
    for i, n in enumerate(bars):
        if n > 0:
            ax1.text(i, n + peak * 0.02, str(n), ha='center', va='bottom',
                     fontsize=9, fontweight='bold', color='#1b5e20')
    ax1.set_xticks(xs)
    ax1.set_xticklabels(labels, rotation=45, ha='right', fontsize=9)
    ax1.set_ylabel('New listings added per run', fontsize=11)
    ax1.set_title(f'Job Search Run Delta — runs #1 → #{runs[-1]["n"]}',
                  fontsize=13, fontweight='bold')
    ax1.grid(axis='y', alpha=0.3)

    ax1b = ax1.twinx()
    ax1b.plot(xs, cum, color='#1565c0', marker='o', linewidth=2, markersize=6)
    ax1b.set_ylabel('Cumulative listings (line)', color='#1565c0', fontsize=11)
    ax1b.tick_params(axis='y', labelcolor='#1565c0')
    ax1b.text(xs[-1] - 0.2, cum[-1] + 2, f'tree={cum[-1]}',
              color='#1565c0', fontsize=10, fontweight='bold', ha='right')

    ax1.legend(handles=[
        mpatches.Patch(color='#2e7d32', label='New listings per run'),
        plt.Line2D([0], [0], color='#1565c0', marker='o', label='Cumulative tree size'),
    ], loc='upper left', framealpha=0.95, fontsize=9)
    ax1.set_xlim(-0.6, len(runs) - 0.4)

    labels_p, sizes, cols = [], [], []
    for s in STATUS_ORDER:
        c = status_counts.get(s, 0)
        if c > 0:
            labels_p.append(f'{s} ({c})')
            sizes.append(c)
            cols.append(STATUS_COLORS[s])
    if sizes:
        _, _, autotexts = ax2.pie(
            sizes, labels=labels_p, colors=cols, autopct='%1.1f%%',
            startangle=90, textprops={'fontsize': 10}, pctdistance=0.78,
            wedgeprops=dict(edgecolor='white', linewidth=1.5),
        )
        for t in autotexts:
            t.set_color('white')
            t.set_fontweight('bold')
    ax2.set_title(f'Status Breakdown (n={total_n})', fontsize=13, fontweight='bold')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(OUT, dpi=130, bbox_inches='tight')
    print(f'Saved {OUT}. Tree size: {cum[-1]}; n={total_n}')


if __name__ == '__main__':
    main()
