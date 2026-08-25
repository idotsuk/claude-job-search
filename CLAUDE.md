# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

An agentic job-search pipeline distributed as five Claude Code skills (`.claude/skills/job-search`, `.claude/skills/apply`, `.claude/skills/network-scan`, `.claude/skills/score-listings`, `.claude/skills/tailor-cv`) plus a set of Python helper scripts they shell out to. There is no app server — the skills *are* the product, and each `SKILL.md` is the authoritative spec for its behavior. When changing pipeline behavior, edit the relevant `SKILL.md` first; the Python scripts are deliberately thin (scraping, file-munging, chart/report/CV rendering, frontmatter bookkeeping) and should stay in sync with what the skill documents as their contract.

The skills themselves are generic and contain zero personal information. Everything user-specific lives in gitignored files (`config.yaml`, `standard-answers.md`) and the gitignored `data/` tree, which the skills read/write at runtime.

## Setup

```bash
cp config.yaml.example config.yaml        # edit profile/locations/keywords before first run
pip install -r scripts/requirements.txt   # pyyaml, playwright, matplotlib, markdown
python -m playwright install              # needed for the Python playwright package path
claude mcp add playwright -- npx -y @playwright/mcp@latest   # needed for /apply and interactive board fallbacks
```

Run the pipeline from inside Claude Code — there is no way to "run" these skills outside it, they're prompts, not executables:

```
claude
/job-search       # run the sweep skill
/score-listings   # score the To Apply queue against your CV
/tailor-cv        # generate a per-job tailored CV for a scored listing
/apply            # work the To Apply queue
/network-scan     # occasional: flag warm-contact companies from your LinkedIn connections
```

## Commands

A small regression test suite exists under `tests/` (plain `python3 tests/test_*.py`, no pytest framework, no CI wiring yet) — run a test file directly when touching the script it covers. Beyond that, there's no build/lint suite; the operative "commands" are the maintenance scripts the skills call:

```bash
python3 scripts/mark_stale.py [--dry-run] [--days N] [--today YYYY-MM-DD]   # demote inactive Applied/Screen/Interviewing -> Stale
python3 scripts/source_yield.py [--since YYYY-MM-DD]                        # per-source yield report from sources.yaml + listings
python3 scripts/generate_chart.py                                           # writes data/reports/chart-latest.png
python3 scripts/generate_run_report.py                                      # writes data/reports/run-N.html per run
python3 scripts/generate_match_report.py                                    # writes data/reports/match-scores-latest.html
python3 scripts/write_match_scores.py < scores.json                        # surgical frontmatter upsert, used by /score-listings
python3 scripts/clear_match_scores.py --all                                 # clear match_score/verdict/confidence/computed to force a rescore
python3 scripts/write_cv_status.py < status.json                           # surgical frontmatter upsert, used by /tailor-cv
python3 scripts/write_warm_contacts.py < contacts.json                     # surgical frontmatter upsert, used by /network-scan
python3 scripts/render_cv.py --input payload.json --out-dir data/cv-outputs/<stem>  # fills templates/cv-template.html, renders one-page PDF

# sweep scripts (stdout JSON, stderr progress log) — invoked by the job-search skill, runnable standalone for debugging:
python3 scripts/linkedin_sweep.py [--keep]
python3 scripts/getro_sweep.py <BOARD_URL> [--keyword KW] [--location LOC]
python3 scripts/page_dump.py <URL> [--wait MS] [--scroll]
python3 scripts/apply_driver.py <starting_url>
```

All scripts assume CWD = repo root and read `./config.yaml` + `./data/` via `scripts/lib.py` (override with `JOB_SEARCH_CONFIG` / `JOB_SEARCH_DATA` env vars — see "Testing scripts against a scratch tree" below). `lib.load_config()` exits with code 2 and a stderr message if `config.yaml` is missing — don't add a different missing-config path in new scripts. `lib.py` also holds `STATUS_ORDER` / `STATUS_COLORS`, the canonical listing-status enum shared by `generate_chart.py` and `generate_run_report.py` so their status breakdowns can't drift apart — reuse these constants rather than re-declaring a status list in any new script.

There are no lint commands configured — verify script changes with `python3 -m py_compile scripts/*.py` and, where a `tests/test_*.py` file exists for the script you touched, run it.

## Architecture

### Five skills, one shared data model

- **`/job-search`** (`.claude/skills/job-search/SKILL.md`) — the sweep. Reads `config.yaml`, loads `data/sources.yaml` (the source registry) and `data/listings/*.md`, searches LinkedIn + company boards + discovered sources in parallel, dedupes, writes new listing files, syncs Gmail/WhatsApp/Calendar, updates the registry, writes a run report + chart.
- **`/score-listings`** (`.claude/skills/score-listings/SKILL.md`) — scores every `To Apply` listing 0-100 for pure technical/domain fit against the user's CV (and, optionally, a richer `data/career-notes.md` background file — see "Career notes: resume-only vs. interview" below), writes the score/verdict into frontmatter, renders `data/reports/match-scores-latest.html`.
- **`/tailor-cv`** (`.claude/skills/tailor-cv/SKILL.md`) — generates a per-job tailored CV (subtle reorder/rephrase of real content, never fabricated) from `templates/cv-template.html`, rendered to a one-page PDF via `scripts/render_cv.py` + Playwright.
- **`/apply`** (`.claude/skills/apply/SKILL.md`) — companion skill. Walks the `status: To Apply` queue one role at a time via Playwright MCP, prefills known ATS fields from `config.yaml`'s `applicant:` block, **never submits without explicit per-role user confirmation**.
- **`/network-scan`** (`.claude/skills/network-scan/SKILL.md`) — occasional (e.g. monthly), separate from the regular sweep. Resolves LinkedIn contacts' current employers to careers pages, scans them for matching roles, and flags `warm_contact: true` on new and existing listings.
- **`scripts/*.py`** — the parts that need real code rather than agentic reasoning: browser automation (Playwright), chart/HTML/PDF rendering, YAML/SQLite bookkeeping. `scripts/lib.py` is the only shared module (`load_config`, `cfg_get` with dotted-path + `~`-expansion, `data_root`, `session_dir`, `STATUS_ORDER`/`STATUS_COLORS`). All other scripts are standalone entry points, not imported by each other.

Skill markdown is the source of truth for *process* (what order steps happen in, how to classify an email, when to demote a source); scripts are the source of truth for anything that must be exact or repeatable (yield math, staleness dates, chart data, frontmatter edits). When changing behavior, check whether the logic belongs in the skill (judgment call, varies by run) or a script (deterministic, should not be re-derived by the model each time).

### The data model (`data/`, gitignored, created on first run)

- `data/listings/<company-slug>-<role-slug>.md` — one file per tracked role: YAML frontmatter (`title, company, role, status, url, location, level, type, first_added, source`, plus optional `match_score/match_verdict/match_confidence/match_computed`, `cv_status/cv_output_dir/cv_generated`, `warm_contact/contact_name`) + free-text body + a `## Communications` table (Date | Channel | Direction | Contact | Summary) that Gmail/WhatsApp scans append rows to. `status` moves through `To Apply → Applied → Screen → Interviewing → Offer`, or sideways to `Rejected / Skipped / Passed / Stale`. `Stale` is applied automatically by `scripts/mark_stale.py` when an Applied/Screen/Interviewing listing has no logged activity for `pipeline.stale_after_days`.
- `data/sources.yaml` — the source registry, the pipeline's self-growing memory across runs, not a static list. Ships seeded with three demotion-exempt tier-1 sources (LinkedIn, Gmail, WhatsApp) via `templates/sources.seed.yaml`. Every listing's `source:` frontmatter must cite a registry `id` — that's what `scripts/source_yield.py` and the promotion/demotion logic key on. Lifecycle: `discovery (probation) → smoke-test → tier 2 → tier 1 or dropped/parked`. Demotion is **days-based and yield-weighted** (`dry_days = today - last_success`, see the file's own header comment for the exact `dry_days`/`proven` rules), not run-counted — a fixed "N zero runs" rule breaks when run cadence changes; don't reintroduce one. Sources that fail because the scraper doesn't exist yet or hit anti-bot blocks (`unreachable`) are tracked separately from sources that ran clean and found nothing (`empty`) — **tooling failure never counts as signal-empty**; only `empty` advances `consecutive_zeroes` toward a drop, `unreachable` advances `unreachable_streak` toward `parked` instead. This distinction is threaded through the skill, the scripts, and the registry schema — preserve it in any change.
- `data/company-index.yaml` — persistent per-company triage cache used by curated-list sweeps (job-search step 3f), so a "best startups of 2026" article never gets re-triaged company-by-company on a later run.
- `data/company-careers.yaml` — `/network-scan`'s careers-page resolution cache, keyed by company name, with a configurable freshness window.
- `data/career-notes.md` — optional, user-authored richer background beyond the one-page CV (see below); read by `/score-listings` and `/tailor-cv` when `career_notes.use_interview` is `true`.
- `data/cv-base-content.yaml`, `data/cv-outputs/<stem>/` — `/tailor-cv`'s baseline CV content (bootstrapped once from the user's own CV on first run, see below) and per-listing tailored output (payload + rendered PDF).
- `data/cv-template/cv-template.html` + `data/cv-template/fonts/*.ttf` — optional, per-user personalized template matching the user's own CV's design, built by `/tailor-cv` step 0 only if they opt in with a `.docx`. Falls back to the shared `templates/cv-template.html` when absent.
- `data/runs/run-N.md`, `data/reports/chart-latest.png`, `data/reports/run-N.html`, `data/reports/match-scores-latest.html` — per-run audit trail and self-contained shareable reports.

### Config-driven, not hard-coded

`config.yaml` (copied from `config.yaml.example`, gitignored) is read once per skill invocation and its values are bound to named placeholders (`{role_focus}`, `{locations}`, `{li_keywords}`, etc.) used throughout the skill steps — search for these names in the `SKILL.md` files to see exactly where a config value takes effect. Nothing user-specific (companies, keywords, PII) should ever be added to the skill markdown or scripts themselves; if a script or skill step needs a new tunable, it belongs in `config.yaml.example` with a comment, not inline.

### Career notes: resume-only vs. interview

`career_notes.use_interview` in `config.yaml` (a tri-state boolean: `true` / `false` / unset) controls whether `/score-listings` and `/tailor-cv` draw on more than the one-page CV. Unset → `/score-listings` asks the user once (resume-only vs. a short interview to build `data/career-notes.md`) and persists the answer. `false` → both skills ignore `data/career-notes.md` even if it exists. `true` → both skills read it (excluding its `## Open Questions` section, which holds unresolved/unverified material that must never factor into scoring or tailoring). Do not hardcode any user's name into that section heading or anywhere else in the skill text — it must read identically for every user.

### Integrations are additive, never required

Gmail, WhatsApp (via a local GOWA SQLite bridge, see `docs/whatsapp-setup.md`), Google Calendar, and Playwright MCP are all optional and independently gated — `/job-search` step 0a probes each and skips + nudges rather than failing when one is missing. When adding a new integration or source type, follow this pattern: probe availability first, degrade gracefully, record what was skipped in the run report's `sources_skipped`.

### Company career boards are hit via ATS JSON APIs before Playwright

Greenhouse/Lever/Ashby/Comeet endpoints are documented in `job-search/SKILL.md` step 3b — curl-and-parse is faster and more reliable than driving a browser for these. Playwright is reserved for authenticated/anti-bot surfaces (LinkedIn) and boards with no discoverable API. Every Playwright-driven source uses a **persistent Chrome profile** at `data/.sessions/<source>/` via `lib.session_dir()`, launched against **system Chrome, not bundled Chromium** (`integrations.browser.chrome_path`) — bundled Chromium gets anti-bot challenged, especially on LinkedIn.

### Source scrapers follow one shape

New per-source scrapers live at `scripts/<source>_sweep.py` and mirror `scripts/linkedin_sweep.py`: use `lib.session_dir(name)` for a persistent Chrome profile (so login survives across runs), page/scroll until the result count stabilizes with a hard cap, emit JSON to stdout for the shared downstream dedup/filter pipeline, and log progress to stderr. `scripts/getro_sweep.py` is a generic driver reused across many Getro-hosted VC portfolio boards rather than one-off scripts per board.

### Frontmatter is edited surgically, never round-tripped

Scripts that touch listing files (`mark_stale.py`, `write_match_scores.py`, `clear_match_scores.py`, `write_cv_status.py`, `write_warm_contacts.py`) do regex-based upsert/removal on individual frontmatter fields rather than parsing the whole file through a YAML round-trip, so key order/comments/body/Communications table stay untouched by an edit that only needs to touch one or two fields.

### `/apply` is a strict human-in-the-loop gate

Not an automation shortcut: it prefills known ATS fields from `config.yaml`'s `applicant:` block but never clicks Submit, never opens the next queue item, and never fills essay/cover-letter fields without explicit per-role user confirmation. Any change to `apply/SKILL.md` or `apply_driver.py` must preserve this contract — it's stated as a hard rule at the top of the skill, not an implementation detail.

## Testing scripts against a scratch tree

Path/config overrides for testing without touching real personal data: `JOB_SEARCH_DATA` (default `./data`), `JOB_SEARCH_CONFIG` (default `./config.yaml`) — see `scripts/lib.py`. Point these at a scratch directory with copies of the listing(s) you're testing against before running a script that mutates frontmatter, verify the result, then discard the scratch copy.

## Working in this repo

- Treat `.claude/skills/*/SKILL.md` as the spec. If you change scraping/dedup/registry/status/scoring/tailoring logic, update the corresponding numbered step in the skill doc in the same change — the prose there is what an agent actually executes at runtime.
- Never add real names, emails, phone numbers, or other PII to tracked files (including examples/docs/templates) — use obviously-fake placeholders (`jane@example.com` style), matching the existing `.example` templates. This applies to design/schema documentation too: don't use a real person's real employer/school as a "worked example" in a shipped doc, even without contact info attached. The PII-guard CI job (see below) catches phone numbers and non-placeholder emails in tracked files, but it does not catch a real name or employer used as prose — write clean the first time.
- `data/` and gitignored config files are real user state when present locally — never read them into anything that leaves the machine, and never suggest `git add -f` on them.

## Privacy invariants (load-bearing, not just style)

- `config.yaml`, `standard-answers.md`, and `data/` are gitignored and must stay that way — real PII, browser session cookies, and full pipeline history live there. Never suggest `git add -f` on these paths.
- `.github/workflows/pii-guard.yml` runs on every push/PR and fails CI if tracked files contain phone-number-like strings or non-placeholder email addresses (only `*.example` files and `@example.com`/`@users.noreply` domains are exempt). Keep any new example/template files using obviously fake placeholder contact info.
- `/apply` must never click a real Submit button without explicit per-role user confirmation, and must never fill essay/cover-letter fields — this is enforced in the skill instructions, not code, so preserve it when editing `.claude/skills/apply/SKILL.md`.
