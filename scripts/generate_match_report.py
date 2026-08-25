#!/usr/bin/env python3
"""
Generate the CV match-score HTML report.

Reads every listing with a match_score in its frontmatter (from any past
/score-listings run — not just the latest), and renders a self-contained
HTML card grid sorted by score descending: a ring gauge, status chip, and
a CSS-only hover tooltip showing the verdict. No external JS/CDN — same
self-contained-HTML convention as generate_run_report.py.

Always overwrites data/reports/match-scores-latest.html.

Run with: python3 scripts/generate_match_report.py
"""

import html
import math
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import lib

DATA = lib.data_root()
LISTINGS_DIR = DATA / 'listings'
REPORTS_DIR = DATA / 'reports'
OUT_PATH = REPORTS_DIR / 'match-scores-latest.html'

STATUS_COLORS = {
    'To Apply': '#42a5f5', 'Applied': '#26a69a', 'Screen': '#ffa726',
    'Interviewing': '#ab47bc', 'Offer': '#66bb6a',
    'Stale': '#8d6e63',
    'Rejected': '#ef5350', 'Skipped': '#bdbdbd', 'Passed': '#bdbdbd',
}


def parse_frontmatter(text):
    if not text.startswith('---'):
        return None, text
    end = text.find('\n---', 4)
    if end < 0:
        return None, text
    try:
        fm = yaml.safe_load(text[4:end])
    except Exception:
        return None, text
    return fm if isinstance(fm, dict) else None, text[end + 4:].lstrip()


def status_chip(status):
    bg = STATUS_COLORS.get(status, '#9e9e9e')
    return f'<span class="chip" style="background:{bg}">{html.escape(status)}</span>'


def load_scored_listings():
    """Every listing with a match_score, regardless of current status —
    this report reflects all scored listings, not just the live To-Apply
    queue, so a listing that's since moved to Applied still shows."""
    out = []
    for p in sorted(LISTINGS_DIR.glob('*.md')):
        if p.name == 'README.md':
            continue
        fm, _ = parse_frontmatter(p.read_text())
        if not fm or fm.get('match_score') is None:
            continue
        try:
            score = int(fm['match_score'])
        except (TypeError, ValueError):
            continue
        out.append({
            'file': p.name,
            'company': fm.get('company', ''),
            'role': fm.get('role', ''),
            'status': str(fm.get('status', '')).strip(),
            'url': fm.get('url') or '',
            'score': score,
            'verdict': str(fm.get('match_verdict', '')),
            'confidence': fm.get('match_confidence', 'full'),
            'computed': fm.get('match_computed', ''),
            'warm_contact': bool(fm.get('warm_contact')),
            'contact_name': str(fm.get('contact_name', '')),
        })
    out.sort(key=lambda L: -L['score'])
    return out


def score_color(score):
    if score >= 70:
        return '#4f9d69'
    if score >= 45:
        return '#c98a2c'
    return '#c8564a'


def ring_gauge_svg(score, size=76):
    r = size / 2 - 7
    circumference = 2 * math.pi * r
    offset = circumference * (1 - score / 100)
    color = score_color(score)
    cx = cy = size / 2
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<circle cx="{cx}" cy="{cy}" r="{r:.2f}" fill="none" stroke="#e3e6eb" stroke-width="7"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r:.2f}" fill="none" stroke="{color}" stroke-width="7" '
        f'stroke-linecap="round" stroke-dasharray="{circumference:.2f}" '
        f'stroke-dashoffset="{offset:.2f}" transform="rotate(-90 {cx} {cy})"/>'
        f'<text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central" '
        f'font-size="{size * 0.3:.0f}" font-weight="700" fill="#1a3a5c">{score}</text>'
        f'</svg>'
    )


def render_listing_card(L):
    ring = ring_gauge_svg(L['score'])
    low_conf_badge = (
        '<span class="low-confidence" title="No live posting could be fetched — '
        'scored from the stored listing summary instead">&#9888; estimated from summary</span>'
        if L['confidence'] == 'low' else ''
    )
    warm_contact_title = (
        f'Warm contact: {html.escape(L["contact_name"])}' if L['contact_name'] else 'Warm contact at this company'
    )
    warm_contact_badge = (
        f'<span class="warm-contact" title="{warm_contact_title}">&#129309; warm contact</span>'
        if L['warm_contact'] else ''
    )
    url_html = (
        f'<a href="{html.escape(str(L["url"]))}" target="_blank" class="apply" title="Open job posting">&#8599;</a>'
        if L['url'] and not str(L['url']).startswith(('mailto:', 'whatsapp')) else ''
    )
    return (
        '<div class="card">'
        f'<div class="ring-wrap">{ring}'
        f'<div class="tooltip">{html.escape(L["verdict"])}</div>'
        '</div>'
        '<div class="card-body">'
        f'<div class="company">{html.escape(L["company"])}</div>'
        f'<div class="role">{html.escape(L["role"])}{url_html}</div>'
        '<div class="meta-row">'
        f'{status_chip(L["status"])}{low_conf_badge}{warm_contact_badge}'
        '</div>'
        '</div>'
        '</div>'
    )


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CV Match Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          max-width: 1100px; margin: 30px auto; padding: 0 24px; color: #222; line-height: 1.55; }}
  h1 {{ font-size: 28px; color: #1a3a5c; border-bottom: 3px solid #1565c0; padding-bottom: 10px; }}
  .meta {{ color: #666; font-size: 14px; margin-top: -6px; }}
  .stats {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 16px 0 28px; }}
  .stat {{ background: #f7f9fc; padding: 10px 16px; border-radius: 6px; min-width: 90px; text-align: center; }}
  .stat-num {{ font-size: 22px; font-weight: 700; color: #1a3a5c; }}
  .stat-label {{ font-size: 12px; color: #555; text-transform: uppercase; letter-spacing: 0.4px; }}
  .chip {{ display: inline-block; padding: 2px 10px; border-radius: 12px; color: white; font-size: 12px; font-weight: 600; }}
  .low-confidence {{ display: inline-block; margin-left: 6px; padding: 2px 10px; border-radius: 12px;
                      background: #fdf1de; color: #c98a2c; font-size: 11px; font-weight: 600; }}
  .warm-contact {{ display: inline-block; margin-left: 6px; padding: 2px 10px; border-radius: 12px;
                    background: #e7f5ea; color: #2e7d4f; font-size: 11px; font-weight: 600; cursor: help; }}
  a {{ color: #1565c0; }}
  a.apply {{ color: #777; text-decoration: none; margin-left: 4px; font-size: 13px; }}
  a.apply:hover {{ color: #1565c0; }}

  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-top: 20px; }}
  .card {{ display: flex; gap: 14px; background: #fff; border: 1px solid #e3e6eb; border-radius: 10px;
           padding: 16px; box-shadow: 0 1px 2px rgba(0,0,0,0.04), 0 4px 14px -6px rgba(0,0,0,0.08); }}
  .ring-wrap {{ position: relative; flex-shrink: 0; cursor: help; }}
  .tooltip {{ visibility: hidden; opacity: 0; position: absolute; z-index: 10; top: 0; left: 90px; width: 260px;
              background: #1a3a5c; color: #fff; padding: 10px 12px; border-radius: 6px; font-size: 12.5px;
              line-height: 1.45; transition: opacity 0.15s; pointer-events: none; }}
  .ring-wrap:hover .tooltip {{ visibility: visible; opacity: 1; }}
  .card-body {{ flex: 1; min-width: 0; }}
  .company {{ font-size: 15px; font-weight: 700; color: #1a3a5c; }}
  .role {{ font-size: 13px; color: #555; margin-top: 2px; line-height: 1.35; }}
  .meta-row {{ margin-top: 10px; }}
  .footer {{ color: #999; font-size: 12px; margin-top: 40px; padding-top: 16px; border-top: 1px solid #eee; }}
</style>
</head>
<body>
<h1>CV Match Report</h1>
<p class="meta">Every scored listing, sorted best-fit first. Hover a ring for the reasoning. Generated: {generated}</p>

<div class="stats">
  <div class="stat"><div class="stat-num">{count}</div><div class="stat-label">Scored</div></div>
  <div class="stat"><div class="stat-num">{avg}</div><div class="stat-label">Avg. score</div></div>
  <div class="stat"><div class="stat-num">{low_conf_count}</div><div class="stat-label">Low confidence</div></div>
</div>

<div class="grid">
{cards}
</div>

<div class="footer">
  Source: <code>data/listings/</code> (<code>match_score</code> / <code>match_verdict</code> frontmatter). Self-contained — no external requests.
</div>
</body>
</html>
"""


def main():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    listings = load_scored_listings()

    if not listings:
        print('No scored listings yet — run /score-listings first. Skipping report generation.')
        return

    from datetime import datetime
    generated = datetime.now().strftime('%Y-%m-%d %H:%M')

    avg = round(sum(L['score'] for L in listings) / len(listings))
    low_conf_count = sum(1 for L in listings if L['confidence'] == 'low')
    cards = '\n'.join(render_listing_card(L) for L in listings)

    out = HTML_TEMPLATE.format(
        generated=html.escape(generated),
        count=len(listings),
        avg=avg,
        low_conf_count=low_conf_count,
        cards=cards,
    )
    OUT_PATH.write_text(out)
    print(f'Wrote {OUT_PATH} ({len(listings)} scored listing(s), avg score {avg}).')


if __name__ == '__main__':
    main()
