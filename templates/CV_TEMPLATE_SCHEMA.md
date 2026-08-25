# CV template schema

`templates/cv-template.html` is a deterministic fill target for the
`/tailor-cv` skill. This document describes the placeholder and
repeating-block convention so that skill can fill the template
programmatically without guessing.

## Source of truth for the design

The template's fonts, colors, spacing, and section order were extracted
directly from the user's own CV, a Google Docs export (`.docx`) held under
`data/cv-source/` (gitignored — real personal content), via `python-docx` +
raw OOXML inspection — not eyeballed or recreated from a generic resume
look. See "Fidelity notes" below for the handful of deliberate deviations.

- **Layout**: single page, US Letter, two-column body (main column ~68.5%
  width / sidebar ~31.5% width — the exact ratio from the source docx's
  table grid: 7605/3495 twips), header spans both columns above.
- **Fonts**: Merriweather (serif — name, job/degree titles, bullet body
  text) and Open Sans (sans-serif — section headings, contact block, skill
  group labels). Both are embedded as real font files under
  `templates/fonts/*.ttf`, extracted from the source docx itself (Google
  Docs bundles them when exporting to .docx), not a substitution.
- **Colors**: `#2079c7` (section titles, skill-group labels), `#1155cc`
  (email/LinkedIn links), `#000000` (name, company/degree/role titles),
  `#666666` (bullet/body text — this is the docx's actual paragraph
  default color, not a stylistic choice).
- **Bullets**: filled circle `●`, matching the source's `numbering.xml`
  bullet glyph for the experience/education list style.

## Two kinds of placeholders

### 1. Scalar placeholders — `{{PLACEHOLDER_NAME}}`

Simple find-and-replace. A filler script just does
`html.replace("{{NAME}}", value)` for each one. Top-level scalars:

| Placeholder | Meaning |
|---|---|
| `{{NAME}}` | Full name, header |
| `{{SUMMARY}}` | The one professional-summary paragraph (this is the field most likely to get subtly rephrased per job) |
| `{{LOCATION}}` | City, country |
| `{{PHONE}}` | Phone number, displayed as-is (not a link) |
| `{{EMAIL}}` | Email address (used both as display text and inside `mailto:{{EMAIL}}`) |
| `{{LINKEDIN_URL}}` | Full LinkedIn URL for the `href` |
| `{{LINKEDIN_DISPLAY}}` | Short LinkedIn text shown to the reader (e.g. `linkedin.com/in/...`) |

All scalar values must already be HTML-escaped by the filler (the template
does no escaping itself — this is a static template, not a live app).

### 2. Repeating blocks — `<!--BLOCK:NAME--> ... <!--/BLOCK:NAME-->`

Marks a region that gets duplicated once per item in a list. The filler:

1. Locates `<!--BLOCK:X-->...<!--/BLOCK:X-->`.
2. Takes the HTML between the markers as a mini-template.
3. For each item, fills that mini-template's own scalar placeholders (and,
   if the block has a nested block inside it, recurses — see below).
4. Concatenates the rendered copies and replaces the whole
   `<!--BLOCK:X-->...<!--/BLOCK:X-->` region (markers included) with the
   result.

Blocks can **nest** — fill innermost blocks first, then the outer one,
since the outer block's own scalar-fill pass should not touch text that
still belongs to an unfilled inner block.

| Block | Nested inside | Inner placeholders / nested blocks |
|---|---|---|
| `COMPANY` | (top-level, one per employer) | `{{COMPANY_NAME}}`, `{{COMPANY_META}}`, `{{COMPANY_DATES}}`, nested `ROLE` block |
| `ROLE` | `COMPANY` (one per title held at that employer) | `{{ROLE_TITLE}}`, `{{ROLE_DATES}}`, nested `BULLET` block |
| `BULLET` | `ROLE` | `{{BULLET_TEXT}}` |
| `EDU_ENTRY` | (top-level, one per degree) | `{{EDU_TITLE}}`, `{{EDU_ORG}}`, `{{EDU_DATES}}`, nested `EDU_BULLET` block |
| `EDU_BULLET` | `EDU_ENTRY` | `{{EDU_BULLET_TEXT}}` |
| `ARMY_ENTRY` | (top-level, one per service entry) | `{{ARMY_TITLE}}`, `{{ARMY_ORG}}`, `{{ARMY_DATES}}`, nested `ARMY_BULLET` block |
| `ARMY_BULLET` | `ARMY_ENTRY` | `{{ARMY_BULLET_TEXT}}` |
| `CORE_EXPERTISE_ITEM` | (top-level, one per expertise tag) | `{{CORE_EXPERTISE_ITEM}}` — one bullet row per tag. Source docx has each CORE EXPERTISE tag as its own bulleted paragraph (verified against the raw XML); do not pre-join these with `" · "` |
| `SKILL_GROUP` | (top-level, one per labeled skill group) | `{{SKILL_GROUP_LABEL}}`, `{{SKILL_GROUP_ITEMS}}` (pre-joined `" · "` string) — unlike `CORE_EXPERTISE_ITEM`, STACK/CLOUD & INFRASTRUCTURE/LANGUAGES really are single dot-joined lines in the source docx, confirmed against the raw XML (each group is one bulleted paragraph with items separated by `·` and grouped via manual line breaks). Don't "fix" this to match CORE_EXPERTISE_ITEM's one-per-row style. Rendered as label on its own line (`.sub-header`, matching CORE EXPERTISE's own label), then the dot-joined items string on the line below (`.skill-items`) — label and items are NOT on the same line. |

**Why `COMPANY` always wraps at least one `ROLE`, even for a single-role
job:** the source docx sometimes puts company+role+dates on one line
(single-role jobs, e.g. a one-title employer) and sometimes splits company
header from role sub-line (multi-role/promotion jobs, e.g. an employer with
several sequential titles). The template always renders two lines (company
header, then role sub-line) for uniformity, so a filler script never needs
to special-case single-role employers. See "Fidelity notes" for how to
reproduce each source pattern with the placeholder fields.

**Composing `COMPANY_META` / `COMPANY_DATES` / role suffixes:** these
placeholders are inserted with no added punctuation or spaces around them
in the template, so the caller supplies leading punctuation/spaces itself.
Illustrative examples (the actual real-content fill lives in the gitignored
`data/cv-source/cv-template-sample.html`, not in this repo):

- Multi-role employer with a ticker: `COMPANY_NAME="Acme Corp"`, `COMPANY_META=" (NASDAQ: ACME), "`, `COMPANY_DATES="2021–2026"` → renders `Acme Corp (NASDAQ: ACME), 2021–2026`
- Single-role employer (no ticker, no company-level date range — the role carries the date instead): `COMPANY_NAME="Beta Systems"`, `COMPANY_META=""`, `COMPANY_DATES=""`
- Role suffix: `ROLE_TITLE="Senior Engineer – Platform Team"`, `ROLE_DATES=" (2022-2026)"` → renders `Senior Engineer – Platform Team (2022-2026)`
- Education: `EDU_TITLE="MSc, Computer Science"`, `EDU_ORG=", State University"`, `EDU_DATES=", 2021-2026"` → renders `MSc, Computer Science, State University, 2021-2026`

### 3. Optional section wrappers — `<!--SECTION:NAME--> ... <!--/SECTION:NAME-->`

Wrap each top-level section (`EXPERIENCE`, `EDUCATION`, `ARMY`, `SKILLS`)
including its `.section-title` heading. These exist so a future tailoring
pass can **drop an entire section** (most plausibly `ARMY`, if a specific
application needs the space) by deleting everything between and including
the markers, rather than needing to special-case "no army section" inside
the section's own block logic. For a normal fill where every section is
populated, just strip the `<!--SECTION:...-->` / `<!--/SECTION:...-->`
comment markers themselves and leave the content — that's what
`templates/cv-template-sample.html` does.

## Reference implementation

The one-off script used to produce `data/cv-source/cv-template-sample.html`
(a generic recursive block-filler, ~120 lines, plus the real CV content
as Python dicts) validated this exact convention end-to-end — every
placeholder resolved, no leftover `{{...}}` tokens. It was a throwaway
validation script (not committed — the real fill logic belongs to the
`/tailor-cv` skill, out of scope here), but the convention it exercised
is exactly what's documented above and is what a future filler should
implement:

1. A `find_block(html, name)` that locates the first
   `<!--BLOCK:name-->...<!--/BLOCK:name-->` region.
2. A `render_block(html, name, items, item_renderer)` that replaces that
   region with `item_renderer(inner_template, item)` repeated per item.
3. Nested blocks get rendered innermost-first (`BULLET` inside `ROLE`
   inside `COMPANY`, etc.) before the outer block's own scalar fill runs.
4. A final `fill_scalars(html, mapping)` pass for the top-level
   placeholders (`{{NAME}}`, `{{SUMMARY}}`, ...).
5. Assert no `{{[A-Z_]+}}` pattern remains in the output — an unresolved
   placeholder should fail loudly, not render literally into the CV.

## Fidelity notes (compromises, be honest about these)

- **Bottom margin**: source docx has `bottom margin: 0`. The template uses
  `0.3in` instead — a literal 0 bottom margin risks clipped last-line text
  in headless Chromium's print renderer. Top (`0.4in`) and left/right
  (`0.6in`) margins are exact matches.
- **Single-role company layout**: as described above, a single-role
  employer is normalized to always render a company line + a separate role
  sub-line, even though the source docx put company, role, and dates on one
  combined line for that entry. Multi-role employers already matched this
  two-line shape natively. This is a minor, deliberate normalization for a
  uniform fill schema — not a design compromise driven by the general
  "generic template" problem this project exists to avoid.
- **Fonts are the literal source files**, not substitutes — no compromise
  there. They're Apache-2.0 (Open Sans) / SIL OFL (Merriweather) licensed,
  safe to keep in this repo.
- Everything else (colors, section order, spacing values, bullet glyph,
  column ratio, italic/bold patterns per element) was read directly from
  the docx's OOXML (`styles.xml`, `numbering.xml`, run-level `w:rPr`
  overrides) rather than approximated.

## Files

- `templates/cv-template.html` — the reusable template with placeholders/blocks (this schema describes it) — generic, safe to commit
- `templates/fonts/*.ttf` — the 8 font files (Merriweather/Open Sans × regular/bold/italic/bold-italic) referenced by `@font-face` in the template — generic, safe to commit
- `data/cv-source/cv-template-sample.html` — the template filled with the user's real, current CV content (first real render, produced by the throwaway script described above) — **gitignored, real PII, never commit**
- `data/cv-source/cv-template-sample.pdf` — that sample rendered to PDF via Playwright/Chromium, confirmed one page (`/Count 1` in the PDF's page tree) — **gitignored, real PII, never commit**
