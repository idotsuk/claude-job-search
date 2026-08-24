#!/usr/bin/env python3
"""
Generate a self-contained HTML report for each job-search run.

For each run-N.md with substantive body content:
- Embed the per-run delta chart inline (base64 PNG)
- Render the run-report markdown
- Append a live "Active pipeline" snapshot (Screen / Interviewing / Offer)
- Save to data/reports/run-N.html
"""

import base64
import html
import re
import sys
from pathlib import Path

import yaml
import markdown

sys.path.insert(0, str(Path(__file__).parent))
import lib

DATA = lib.data_root()
LISTINGS_DIR = DATA / 'listings'
RUNS_DIR = DATA / 'runs'
REPORTS_DIR = DATA / 'reports'
CHART_PATH = DATA / 'reports' / 'chart-latest.png'

STATUS_ORDER = lib.STATUS_ORDER
STATUS_COLORS = lib.STATUS_COLORS


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


def parse_comms_rows(body):
    """Return every row of the body's ## Communications table as dicts (in order)."""
    if '## Communications' not in body:
        return []
    section = body.split('## Communications', 1)[1]
    rows = [ln.strip() for ln in section.splitlines() if ln.strip().startswith('|')]
    if len(rows) < 3:
        return []
    out = []
    for line in rows[2:]:  # skip header + alignment
        parts = [p.strip() for p in line.strip('|').split('|')]
        if len(parts) < 5:
            continue
        out.append({'date': parts[0], 'channel': parts[1], 'direction': parts[2],
                    'contact': parts[3], 'summary': parts[4]})
    return out


_AUTO_STALE_PREV_RE = re.compile(r'was \*\*(\w[\w ]*?)\*\*')


def stale_meta(comms):
    """For a Stale listing, extract prior status and last real-activity date
    by reading the auto-stale comm row (always the last) and the prior row(s)."""
    if not comms:
        return {'prev_status': None, 'last_real_activity': None}
    auto = comms[-1]
    prev_status = None
    m = _AUTO_STALE_PREV_RE.search(auto['summary'])
    if m:
        prev_status = m.group(1)
    # Most recent NON-auto activity = the row before the auto one
    real = None
    for row in reversed(comms[:-1]):
        try:
            import datetime as _dt
            _dt.date.fromisoformat(row['date'][:10])
            real = row
            break
        except ValueError:
            continue
    return {
        'prev_status': prev_status,
        'last_real_activity': real['date'] if real else None,
        'last_real_contact': real['contact'] if real else None,
        'last_real_summary': real['summary'] if real else None,
    }


def load_listings():
    out = []
    for p in sorted(LISTINGS_DIR.glob('*.md')):
        if p.name == 'README.md':
            continue
        fm, body = parse_frontmatter(p.read_text())
        if not fm:
            continue
        comms = parse_comms_rows(body)
        out.append({
            'file': p.name,
            'company': fm.get('company', ''),
            'role': fm.get('role', ''),
            'status': str(fm.get('status', '')).strip(),
            'location': fm.get('location', ''),
            'url': fm.get('url') or '',
            'last_update': fm.get('last_update'),
            'first_added': fm.get('first_added'),
            'comms_last': comms[-1] if comms else None,
            'stale_meta': stale_meta(comms) if str(fm.get('status', '')).strip() == 'Stale' else None,
        })
    return out


def load_runs():
    out = []
    for p in sorted(RUNS_DIR.glob('run-*.md')):
        m = re.match(r'run-(\d+)', p.stem)
        if not m:
            continue
        text = p.read_text()
        fm, body = parse_frontmatter(text)
        if not fm:
            continue
        out.append({
            'n': int(m.group(1)), 'stem': p.stem, 'fm': fm,
            'body': body, 'path': p,
        })
    out.sort(key=lambda r: (str(r['fm'].get('date', '')), r['n']))
    return out


def embed_image_b64(path):
    if not path.exists():
        return None
    return base64.b64encode(path.read_bytes()).decode('ascii')


def status_chip(status):
    bg = STATUS_COLORS.get(status, '#9e9e9e')
    return f'<span class="chip" style="background:{bg}">{html.escape(status)}</span>'


def listing_link(file_md):
    """Relative link from data/reports/ to the listing markdown file."""
    return f'../listings/{file_md}'


def render_stale_table(listings, today_iso):
    """Stale work pile, sorted freshest-cold first (most recoverable on top).
    Each row shows: previous status, days inactive, last real contact/summary."""
    import datetime as _dt
    rows = [L for L in listings if L['status'] == 'Stale']
    if not rows:
        return '<p><em>No stale listings.</em></p>'

    today = _dt.date.fromisoformat(today_iso)
    enriched = []
    for L in rows:
        sm = L.get('stale_meta') or {}
        last = sm.get('last_real_activity')
        days = None
        if last:
            try:
                days = (today - _dt.date.fromisoformat(last[:10])).days
            except ValueError:
                pass
        enriched.append((days if days is not None else 10**6, L, sm, last, days))
    enriched.sort(key=lambda x: x[0])

    tbody = []
    for _, L, sm, last, days in enriched:
        prev = sm.get('prev_status') or '—'
        days_cell = f'{days}d' if days is not None else 'undated'
        days_color = '#388e3c' if (days is not None and days <= 21) else ('#f57c00' if days is not None and days <= 60 else '#c62828')
        last_real = (
            f"<small><strong>{html.escape(last)}</strong> · "
            f"{html.escape(sm.get('last_real_contact') or '')}<br>"
            f"<span style='color:#555'>{html.escape(sm.get('last_real_summary') or '')}</span></small>"
            if last else '<small style="color:#999">no prior activity recorded</small>'
        )
        role_html = (
            f'<a href="{html.escape(listing_link(L["file"]))}" class="obs">'
            f'{html.escape(L["role"])}</a>'
        )
        careers_html = ''
        if L['url'] and not str(L['url']).startswith(('mailto:', 'whatsapp')):
            careers_html = (
                f' <a href="{html.escape(str(L["url"]))}" target="_blank" '
                f'class="apply" title="Open job posting">↗</a>'
            )
        tbody.append(
            f'<tr><td><span class="chip" style="background:#8d6e63">Stale</span>'
            f'<br><small style="color:#777">was {html.escape(prev)}</small></td>'
            f'<td><strong style="color:{days_color}">{days_cell}</strong></td>'
            f'<td><strong>{html.escape(L["company"])}</strong><br>'
            f'<small>{role_html}{careers_html}</small></td>'
            f'<td>{html.escape(str(L["location"]))}</td>'
            f'<td>{last_real}</td></tr>'
        )
    return (
        '<p><small style="color:#666">Sorted freshest-cold first — top rows are the highest-leverage candidates to push back into Interviewing. '
        '<span style="color:#388e3c">≤21d</span> · <span style="color:#f57c00">≤60d</span> · <span style="color:#c62828">>60d / undated</span></small></p>'
        '<table class="pipeline"><thead><tr>'
        '<th>Status</th><th>Days cold</th><th>Company / Role</th>'
        '<th>Location</th><th>Last real activity</th></tr></thead><tbody>'
        + ''.join(tbody) + '</tbody></table>'
    )


def render_pipeline_table(listings, statuses):
    rows = [L for L in listings if L['status'] in statuses]
    rows.sort(key=lambda r: (statuses.index(r['status']), r['company']))
    if not rows:
        return '<p><em>No active roles in this bucket.</em></p>'
    tbody = []
    for L in rows:
        comms = L['comms_last']
        comms_cell = (
            f"<small>{html.escape(comms['date'])} · {html.escape(comms['channel'])} "
            f"{html.escape(comms['direction'])} {html.escape(comms['contact'])}<br>"
            f"<span style='color:#555'>{html.escape(comms['summary'])}</span></small>"
            if comms else '<small style="color:#999">—</small>'
        )
        role_html = (
            f'<a href="{html.escape(listing_link(L["file"]))}" class="obs">'
            f'{html.escape(L["role"])}</a>'
        )
        careers_html = ''
        if L['url'] and not L['url'].startswith('mailto:') and not L['url'].startswith('whatsapp'):
            careers_html = (
                f' <a href="{html.escape(L["url"])}" target="_blank" '
                f'class="apply" title="Open job posting">↗</a>'
            )
        tbody.append(
            f"<tr><td>{status_chip(L['status'])}</td>"
            f"<td><strong>{html.escape(L['company'])}</strong><br>"
            f"<small>{role_html}{careers_html}</small></td>"
            f"<td>{html.escape(str(L['location']))}</td>"
            f"<td>{comms_cell}</td></tr>"
        )
    return (
        '<table class="pipeline"><thead><tr>'
        '<th>Status</th><th>Company / Role</th><th>Location</th>'
        '<th>Last comms</th></tr></thead><tbody>'
        + ''.join(tbody) + '</tbody></table>'
    )


def status_summary_strip(listings):
    counts = {}
    for L in listings:
        counts[L['status']] = counts.get(L['status'], 0) + 1
    parts = []
    for s in STATUS_ORDER:
        c = counts.get(s, 0)
        if c == 0:
            continue
        bg = STATUS_COLORS.get(s, '#9e9e9e')
        parts.append(
            f'<div class="stat" style="border-top:4px solid {bg}">'
            f'<div class="stat-num">{c}</div>'
            f'<div class="stat-label">{html.escape(s)}</div></div>'
        )
    return '<div class="stats">' + ''.join(parts) + '</div>'


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Job Search — Run #{n}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          max-width: 1100px; margin: 30px auto; padding: 0 24px; color: #222; line-height: 1.55; }}
  h1, h2, h3 {{ color: #1a3a5c; }}
  h1 {{ font-size: 28px; border-bottom: 3px solid #1565c0; padding-bottom: 10px; }}
  h2 {{ font-size: 20px; margin-top: 36px; padding-top: 12px; border-top: 1px solid #eee; }}
  h3 {{ font-size: 16px; margin-top: 24px; }}
  .meta {{ color: #666; font-size: 14px; margin-top: -6px; }}
  .stats {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 16px 0 24px; }}
  .stat {{ background: #f7f9fc; padding: 10px 16px; border-radius: 6px; min-width: 90px; text-align: center; }}
  .stat-num {{ font-size: 22px; font-weight: 700; color: #1a3a5c; }}
  .stat-label {{ font-size: 12px; color: #555; text-transform: uppercase; letter-spacing: 0.4px; }}
  .chip {{ display: inline-block; padding: 2px 10px; border-radius: 12px; color: white; font-size: 12px; font-weight: 600; }}
  img.chart {{ max-width: 100%; border: 1px solid #e0e0e0; border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.04); }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0 24px; font-size: 14px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee; vertical-align: top; }}
  th {{ background: #f4f7fb; font-weight: 600; color: #1a3a5c; }}
  table.pipeline tr:hover {{ background: #fafbfd; }}
  code, pre {{ background: #f4f7fb; padding: 1px 5px; border-radius: 3px; font-size: 13px; }}
  pre {{ padding: 12px; overflow-x: auto; }}
  blockquote {{ border-left: 3px solid #1565c0; margin: 12px 0; padding: 4px 14px; background: #f7f9fc; color: #444; }}
  .footer {{ color: #999; font-size: 12px; margin-top: 40px; padding-top: 16px; border-top: 1px solid #eee; }}
  a {{ color: #1565c0; }}
  a.obs {{ color: #6a3d8a; font-weight: 600; text-decoration: none; border-bottom: 1px dotted #6a3d8a; }}
  a.obs:hover {{ background: #f0e8f5; }}
  a.apply {{ color: #777; text-decoration: none; margin-left: 4px; font-size: 12px; }}
  a.apply:hover {{ color: #1565c0; }}
</style>
</head>
<body>
<h1>Job Search — Run #{n}</h1>
<p class="meta">Date: <strong>{date}</strong> · Version: <code>{version}</code> · Generated: {generated}</p>

<h2>Pipeline snapshot</h2>
{stats}

<h2>Active processes (Interviewing · Screen · Offer)</h2>
{active_table}

<h2>Stale (work pile — engineer back to Interviewing)</h2>
{stale_table}

<h2>Run delta chart</h2>
{chart_html}

<h2>Run report</h2>
{report_html}

<h2>Applied (awaiting response)</h2>
{applied_table}

<div class="footer">
  Source: <code>data/runs/{stem}.md</code>.
  Listings tree: <code>data/listings/</code>.
</div>
</body>
</html>
"""


def generate(run, listings, chart_b64):
    fm = run['fm']
    body = run['body']

    # Only generate HTML if the run has actual content
    if not body.strip() or body.strip() == '_(empty)_':
        return None

    md = markdown.Markdown(extensions=['tables', 'fenced_code'])
    report_html = md.convert(body)

    chart_html = (
        f'<img class="chart" src="data:image/png;base64,{chart_b64}" '
        f'alt="Job search delta chart" />'
        if chart_b64 else f'<p><em>Chart image not found at {CHART_PATH}</em></p>'
    )

    active = render_pipeline_table(listings, ['Interviewing', 'Screen', 'Offer'])
    applied = render_pipeline_table(listings, ['Applied'])
    today_iso = str(fm.get('date', '')) or __import__('datetime').date.today().isoformat()
    stale = render_stale_table(listings, today_iso)

    from datetime import datetime
    generated = datetime.now().strftime('%Y-%m-%d %H:%M %Z').strip()

    return HTML_TEMPLATE.format(
        n=run['n'],
        date=html.escape(str(fm.get('date', ''))),
        version=html.escape(str(fm.get('version', '') or '—')),
        generated=html.escape(generated),
        stats=status_summary_strip(listings),
        active_table=active,
        stale_table=stale,
        chart_html=chart_html,
        report_html=report_html,
        applied_table=applied,
        stem=html.escape(run['stem']),
    )


def main():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    listings = load_listings()
    runs = load_runs()
    chart_b64 = embed_image_b64(CHART_PATH)
    if chart_b64 is None:
        print(f'WARNING: {CHART_PATH} not found — reports will omit the chart.', file=sys.stderr)

    generated = []
    for run in runs:
        html_out = generate(run, listings, chart_b64)
        if html_out is None:
            continue
        out_path = REPORTS_DIR / f"{run['stem']}.html"
        out_path.write_text(html_out)
        generated.append(out_path)

    print(f'Generated {len(generated)} report(s):')
    for p in generated:
        print(f'  {p}')


if __name__ == '__main__':
    main()
