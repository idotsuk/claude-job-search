#!/usr/bin/env python3
"""
Fill a CV template (templates/cv-template.html by default, or a personalized
--template override) with a tailored content payload and render it to a
one-page PDF via Playwright/Chromium.

This is the deterministic half of the /tailor-cv skill: the skill (agentic,
judgment) decides *what* the CV should say for a given job; this script
(mechanical, repeatable) turns that decision into pixel-identical HTML/PDF
output. It never makes content decisions itself.

Input: a JSON payload matching templates/CV_TEMPLATE_SCHEMA.md's
placeholder/block shape, structured like data/cv-base-content.yaml (see that
file for the canonical full-content example). Top-level keys:

  name, summary, location, phone, email, linkedin_url, linkedin_display  (scalars)
  companies: [ { company_name, company_meta, company_dates,
                 roles: [ { role_title, role_dates,
                            bullets: [str, ...] }, ... ] }, ... ]
  education: [ { edu_title, edu_org, edu_dates, bullets: [str, ...] }, ... ]
  army:      [ { army_title, army_org, army_dates, bullets: [str, ...] }, ... ]
  core_expertise: [str, ...]                     # one CORE_EXPERTISE_ITEM per entry
  skill_groups:   [ { label, items: [str, ...] }, ... ]   # items get " · "-joined
  drop_sections: [str, ...]                       # optional, e.g. ["ARMY"] to
                                                    # omit a whole section (title
                                                    # + content) for space

All string values are plain text (not pre-escaped) — this script HTML-escapes
every value on fill, per the template schema's "filler must escape, template
does not" convention.

Usage:
  python3 scripts/render_cv.py --input payload.json --out-dir data/cv-outputs/<company-slug>-<role-slug>
  cat payload.json | python3 scripts/render_cv.py --out-dir data/cv-outputs/<company-slug>-<role-slug>

Writes <out-dir>/cv.html and <out-dir>/cv.pdf. Exits non-zero (and does NOT
leave a stale cv.pdf behind) if the rendered PDF is not exactly one page —
one-page is a hard requirement, not a suggestion.
"""

import argparse
import glob
import html as html_lib
import json
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import lib

REPO_ROOT = Path(__file__).parent.parent
TEMPLATE_PATH = REPO_ROOT / 'templates' / 'cv-template.html'
FONTS_DIR = REPO_ROOT / 'templates' / 'fonts'

SECTION_NAMES = ['EXPERIENCE', 'EDUCATION', 'ARMY', 'SKILLS']


def esc(value) -> str:
    return html_lib.escape(str(value if value is not None else ''), quote=True)


# ------------------------------------------------------------------ blocks --

def find_block(text: str, name: str):
    """Locate <!--BLOCK:name-->...<!--/BLOCK:name-->. Returns (start, end,
    inner_template) where start/end span the full region including markers."""
    start_marker = f'<!--BLOCK:{name}-->'
    end_marker = f'<!--/BLOCK:{name}-->'
    start = text.find(start_marker)
    if start < 0:
        raise ValueError(f'BLOCK:{name} not found in template')
    inner_start = start + len(start_marker)
    inner_end = text.find(end_marker, inner_start)
    if inner_end < 0:
        raise ValueError(f'BLOCK:{name} has no closing marker')
    end = inner_end + len(end_marker)
    return start, end, text[inner_start:inner_end]


def render_block(text: str, name: str, items, item_renderer) -> str:
    """Replace <!--BLOCK:name-->...<!--/BLOCK:name--> with item_renderer(inner, item)
    concatenated once per item (markers stripped)."""
    start, end, inner_template = find_block(text, name)
    rendered = ''.join(item_renderer(inner_template, item) for item in items)
    return text[:start] + rendered + text[end:]


def fill_scalars(text: str, mapping: dict) -> str:
    for key, value in mapping.items():
        text = text.replace('{{' + key + '}}', esc(value))
    return text


# ------------------------------------------------------- per-item renderers --

def render_bullet(template: str, bullet_text: str) -> str:
    return fill_scalars(template, {'BULLET_TEXT': bullet_text})


def render_edu_bullet(template: str, bullet_text: str) -> str:
    return fill_scalars(template, {'EDU_BULLET_TEXT': bullet_text})


def render_army_bullet(template: str, bullet_text: str) -> str:
    return fill_scalars(template, {'ARMY_BULLET_TEXT': bullet_text})


def render_role(template: str, role: dict) -> str:
    # Innermost first: fill BULLET before this role's own scalars.
    t = render_block(template, 'BULLET', role.get('bullets', []), render_bullet)
    return fill_scalars(t, {
        'ROLE_TITLE': role.get('role_title', ''),
        'ROLE_DATES': role.get('role_dates', ''),
    })


def render_company(template: str, company: dict) -> str:
    t = render_block(template, 'ROLE', company.get('roles', []), render_role)
    return fill_scalars(t, {
        'COMPANY_NAME': company.get('company_name', ''),
        'COMPANY_META': company.get('company_meta', ''),
        'COMPANY_DATES': company.get('company_dates', ''),
    })


def render_edu_entry(template: str, entry: dict) -> str:
    t = render_block(template, 'EDU_BULLET', entry.get('bullets', []), render_edu_bullet)
    return fill_scalars(t, {
        'EDU_TITLE': entry.get('edu_title', ''),
        'EDU_ORG': entry.get('edu_org', ''),
        'EDU_DATES': entry.get('edu_dates', ''),
    })


def render_army_entry(template: str, entry: dict) -> str:
    t = render_block(template, 'ARMY_BULLET', entry.get('bullets', []), render_army_bullet)
    return fill_scalars(t, {
        'ARMY_TITLE': entry.get('army_title', ''),
        'ARMY_ORG': entry.get('army_org', ''),
        'ARMY_DATES': entry.get('army_dates', ''),
    })


def render_core_expertise_item(template: str, item: str) -> str:
    return fill_scalars(template, {'CORE_EXPERTISE_ITEM': item})


def render_skill_group(template: str, group: dict) -> str:
    items_joined = ' · '.join(group.get('items', []))
    # SKILL_GROUP_ITEMS is a single pre-joined string per CV_TEMPLATE_SCHEMA.md —
    # escape the whole joined string once, not each item separately, so the
    # " · " separators themselves aren't at risk of double-escaping.
    t = template.replace('{{SKILL_GROUP_LABEL}}', esc(group.get('label', '')))
    t = t.replace('{{SKILL_GROUP_ITEMS}}', esc(items_joined))
    return t


# ------------------------------------------------------------- sections -----

def strip_or_drop_sections(text: str, drop_sections) -> str:
    """For each known section name: if it's in drop_sections, remove the whole
    <!--SECTION:X-->...<!--/SECTION:X--> region (title + content); otherwise
    just strip the marker comments and keep the content, per
    CV_TEMPLATE_SCHEMA.md's normal-fill convention."""
    drop_set = set(drop_sections or [])
    for name in SECTION_NAMES:
        start_marker = f'<!--SECTION:{name}-->'
        end_marker = f'<!--/SECTION:{name}-->'
        start = text.find(start_marker)
        if start < 0:
            continue
        end = text.find(end_marker, start)
        if end < 0:
            raise ValueError(f'SECTION:{name} has no closing marker')
        end += len(end_marker)
        if name in drop_set:
            text = text[:start] + text[end:]
        else:
            inner_start = start + len(start_marker)
            inner_end = end - len(end_marker)
            text = text[:start] + text[inner_start:inner_end] + text[end:]
    return text


# ------------------------------------------------------------------- fill ---

def fill_template(template_html: str, payload: dict, fonts_dir: Path = FONTS_DIR) -> str:
    text = template_html

    text = strip_or_drop_sections(text, payload.get('drop_sections'))

    text = render_block(text, 'COMPANY', payload.get('companies', []), render_company)
    text = render_block(text, 'EDU_ENTRY', payload.get('education', []), render_edu_entry)
    text = render_block(text, 'ARMY_ENTRY', payload.get('army', []), render_army_entry)
    text = render_block(text, 'CORE_EXPERTISE_ITEM', payload.get('core_expertise', []),
                         render_core_expertise_item)
    text = render_block(text, 'SKILL_GROUP', payload.get('skill_groups', []), render_skill_group)

    text = fill_scalars(text, {
        'NAME': payload.get('name', ''),
        'SUMMARY': payload.get('summary', ''),
        'LOCATION': payload.get('location', ''),
        'PHONE': payload.get('phone', ''),
        'EMAIL': payload.get('email', ''),
        'LINKEDIN_URL': payload.get('linkedin_url', ''),
        'LINKEDIN_DISPLAY': payload.get('linkedin_display', ''),
    })

    # Point @font-face at the template's own fonts/*.ttf (sibling directory of
    # whichever template.html was filled — the shared templates/fonts/ by
    # default, or a personalized data/cv-template/fonts/ override) via
    # absolute file:// URIs, since cv.html lives under data/cv-outputs/<slug>/
    # rather than next to the fonts dir — a relative "fonts/..." URL would 404.
    fonts_uri = fonts_dir.resolve().as_uri()
    text = text.replace('url("fonts/', f'url("{fonts_uri}/')

    leftover = re.findall(r'\{\{[A-Z_]+\}\}', text)
    if leftover:
        raise ValueError(f'Unresolved placeholder(s) left in output: {sorted(set(leftover))}')
    leftover_blocks = re.findall(r'<!--/?(?:BLOCK|SECTION):[A-Z_]+-->', text)
    if leftover_blocks:
        raise ValueError(f'Unresolved block/section marker(s) left in output: {sorted(set(leftover_blocks))}')

    return text


# --------------------------------------------------------------- rendering --

def find_fallback_chromium() -> Optional[str]:
    """Known-working fallback location for this machine's Playwright install
    (see scripts/render_cv.py module docstring / build history): the default
    `playwright install` Chromium download path doesn't always match where
    Playwright's Python API expects it. Glob broadly so a Playwright version
    bump (chromium-1234 -> chromium-1300, say) doesn't silently break this."""
    pattern = str(Path.home() / 'Library' / 'Caches' / 'ms-playwright' /
                  'chromium-*' / 'chrome-mac-arm64' / '*.app' / 'Contents' / 'MacOS' / '*')
    candidates = sorted(glob.glob(pattern))
    return candidates[0] if candidates else None


def render_pdf(html_path: Path, pdf_path: Path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = None
        launch_errors = []
        try:
            browser = p.chromium.launch()
        except Exception as e:  # noqa: BLE001 - genuinely want to try the fallback on any failure
            launch_errors.append(f'default launch: {e}')
            fallback = find_fallback_chromium()
            if not fallback:
                raise RuntimeError(
                    'Playwright default Chromium launch failed and no fallback '
                    f'executable found under ~/Library/Caches/ms-playwright/. '
                    f'Errors: {launch_errors}. Run: python -m playwright install chromium'
                )
            print(f'  playwright default launch failed, falling back to {fallback}', file=sys.stderr)
            try:
                browser = p.chromium.launch(executable_path=fallback)
            except Exception as e2:
                raise RuntimeError(
                    f'Fallback Chromium launch also failed ({fallback}): {e2}. '
                    f'Prior error: {launch_errors}'
                )

        try:
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri())
            page.evaluate('document.fonts.ready')
            page.pdf(
                path=str(pdf_path),
                print_background=True,
                prefer_css_page_size=True,
            )
        finally:
            browser.close()


def count_pdf_pages(pdf_path: Path) -> int:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(pdf_path))
        try:
            return doc.page_count
        finally:
            doc.close()
    except ImportError:
        print('  (PyMuPDF not installed, falling back to regex page-count check)', file=sys.stderr)
        data = pdf_path.read_bytes()
        m = re.search(rb'/Type\s*/Pages.*?/Count\s+(\d+)', data, re.S)
        if m:
            return int(m.group(1))
        # Fallback: count distinct page objects.
        return len(re.findall(rb'/Type\s*/Page[^s]', data))


# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--input', help='Path to JSON payload file. Default: read JSON from stdin.')
    ap.add_argument('--out-dir', required=True, help='Output directory. Writes cv.html and cv.pdf here.')
    ap.add_argument('--template', help=(
        'Path to the template HTML to fill. Default: templates/cv-template.html '
        '(the shared generic design). Pass a personalized template here (e.g. '
        'data/cv-template/cv-template.html) to use a design extracted from the '
        "user's own CV instead — its fonts/*.ttf must live in a sibling "
        "'fonts/' directory next to the template file, same layout as templates/."
    ))
    args = ap.parse_args()

    raw = Path(args.input).read_text() if args.input else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f'Invalid JSON payload: {e}', file=sys.stderr)
        sys.exit(2)

    template_path = Path(args.template) if args.template else TEMPLATE_PATH
    fonts_dir = template_path.parent / 'fonts'

    if not template_path.exists():
        print(f'Template not found at {template_path}', file=sys.stderr)
        sys.exit(2)
    if not fonts_dir.exists():
        print(f'Fonts directory not found at {fonts_dir} (expected as a sibling of the template)', file=sys.stderr)
        sys.exit(2)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / 'cv.html'
    pdf_path = out_dir / 'cv.pdf'

    print(f'Filling template ({template_path}) for {payload.get("name", "?")} ...', file=sys.stderr)
    try:
        filled = fill_template(template_path.read_text(), payload, fonts_dir=fonts_dir)
    except ValueError as e:
        print(f'Template fill failed: {e}', file=sys.stderr)
        sys.exit(1)

    html_path.write_text(filled)
    print(f'  wrote {html_path}', file=sys.stderr)

    print('Rendering PDF via Playwright ...', file=sys.stderr)
    try:
        render_pdf(html_path, pdf_path)
    except Exception as e:  # noqa: BLE001
        print(f'PDF render failed: {e}', file=sys.stderr)
        sys.exit(1)

    pages = count_pdf_pages(pdf_path)
    print(f'  {pdf_path} -> {pages} page(s)', file=sys.stderr)
    if pages != 1:
        pdf_path.unlink(missing_ok=True)
        print(
            f'ERROR: rendered CV is {pages} page(s), not 1. One-page is a hard '
            f'requirement. Trim content (fewer/shorter bullets, drop a section '
            f'via drop_sections) and re-render. cv.pdf was NOT written; '
            f'cv.html left at {html_path} for inspection.',
            file=sys.stderr,
        )
        sys.exit(1)

    print(f'OK: wrote {html_path} and {pdf_path} (1 page).')


if __name__ == '__main__':
    main()
