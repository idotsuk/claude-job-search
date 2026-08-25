# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

An agentic job-search pipeline distributed as two Claude Code skills (`.claude/skills/job-search`, `.claude/skills/apply`) plus a set of Python helper scripts they shell out to. There is no server, build step, or test suite — the "application" is the skill markdown (read by Claude at run time) backed by scripts for the parts that need real browser automation, chart rendering, or SQLite/YAML bookkeeping.

The skills themselves are generic and contain zero personal information. Everything user-specific lives in two gitignored files (`config.yaml`, `standard-answers.md`) and the gitignored `data/` tree, which the skills read/write at runtime.

## Commands

```bash
pip install -r scripts/requirements.txt   # pyyaml, playwright, matplotlib, markdown
python -m playwright install              # only needed if using Playwright MCP separately
cp config.yaml.example config.yaml        # first-time setup; then edit it
python3 -m py_compile scripts/*.py        # syntax-check scripts (no test suite exists)
```

There are no lint or test commands configured — verify script changes with `py_compile` and by running the script directly against real `data/` output.

Running the actual product happens inside Claude Code, not the shell:

```
claude
/job-search     # run the sweep skill
/apply          # work the To-Apply queue
```

### Standalone script usage (all assume CWD = repo root)

- `python3 scripts/linkedin_sweep.py [--keep]` — authenticated LinkedIn scrape, JSON to stdout
- `python3 scripts/getro_sweep.py <BOARD_URL> [--keyword KW] [--location LOC]` — Getro-hosted VC portfolio boards
- `python3 scripts/page_dump.py <URL> [--wait MS] [--scroll]` — generic SPA render + text/link dump for smoke-testing new sources
- `python3 scripts/apply_driver.py <starting_url>` — opens a job posting in the persistent Chrome session and prefills known fields
- `python3 scripts/mark_stale.py [--dry-run] [--days N]` — demotes inactive Applied/Screen/Interviewing listings to Stale
- `python3 scripts/source_yield.py [--since YYYY-MM-DD]` — per-source yield report from `data/sources.yaml` + listings attribution
- `python3 scripts/generate_chart.py` — writes `data/reports/chart-latest.png`
- `python3 scripts/generate_run_report.py` — writes `data/reports/run-N.html` (self-contained, chart inlined)

Path/config overrides (for testing against a scratch tree): `JOB_SEARCH_DATA` (default `./data`), `JOB_SEARCH_CONFIG` (default `./config.yaml`) — see `scripts/lib.py`.

## Architecture

### Two skills, one shared data model

- **`/job-search`** (`.claude/skills/job-search/SKILL.md`) — the sweep. Reads `config.yaml`, loads `data/sources.yaml` (the source registry) and `data/listings/*.md`, searches LinkedIn + company boards + discovered sources in parallel, dedupes, writes new listing files, syncs Gmail/WhatsApp/Calendar, updates the registry, writes a run report + chart.
- **`/apply`** (`.claude/skills/apply/SKILL.md`) — companion skill. Walks the `status: To Apply` queue one role at a time via Playwright MCP, prefills known ATS fields from `config.yaml`'s `applicant:` block, **never submits without explicit per-role user confirmation**.
- **`scripts/*.py`** — the parts that need real code rather than agentic reasoning: browser automation (Playwright), chart rendering (matplotlib), HTML report generation, YAML/SQLite bookkeeping. `scripts/lib.py` is the only shared module (`load_config`, `cfg_get` with dotted-path + `~`-expansion, `data_root`, `session_dir`). All other scripts are standalone entry points, not imported by each other.

Skill markdown is the source of truth for *process* (what order steps happen in, how to classify an email, when to demote a source); scripts are the source of truth for anything that must be exact or repeatable (yield math, staleness dates, chart data). When changing behavior, check whether the logic belongs in the skill (judgment call, varies by run) or a script (deterministic, should not be re-derived by the model each time).

### The data model (`data/`, gitignored, created on first run)

- `data/listings/<company-slug>-<role-slug>.md` — one file per tracked role: YAML frontmatter (`title, company, role, status, url, location, level, type, first_added, source`) + free-text body + a `## Communications` table (Date | Channel | Direction | Contact | Summary) that Gmail/WhatsApp scans append rows to. `status` moves through `To Apply → Applied → Screen → Interviewing → Offer`, or sideways to `Rejected / Skipped / Passed / Stale`. `Stale` is applied automatically by `scripts/mark_stale.py` when an Applied/Screen/Interviewing listing has no logged activity for `pipeline.stale_after_days`.
- `data/sources.yaml` — the source registry, the pipeline's memory across runs. Ships seeded with three demotion-exempt tier-1 sources (LinkedIn, Gmail, WhatsApp) via `templates/sources.seed.yaml`. Every listing's `source:` frontmatter must cite a registry `id` — that's what `scripts/source_yield.py` and the promotion/demotion logic key on. Lifecycle: `discovery (probation) → smoke-test → tier 2 → tier 1 or dropped/parked`. Demotion is **days-based and yield-weighted** (see the file's own header comment for `dry_days`/`proven` rules), not run-counted — a fixed "N zero runs" rule breaks when run cadence changes. Sources that fail because the scraper doesn't exist yet or hit anti-bot blocks (`unreachable`) are tracked separately from sources that ran clean and found nothing (`empty`); only `empty` counts toward drop thresholds.
- `data/company-index.yaml` — persistent per-company triage cache used by curated-list sweeps (step 3f), so a "best startups of 2026" article never gets re-triaged company-by-company on a later run.
- `data/runs/run-N.md`, `data/reports/chart-latest.png`, `data/reports/run-N.html` — per-run audit trail and self-contained shareable reports.

### Config-driven, not hard-coded

`config.yaml` (copied from `config.yaml.example`, gitignored) is read once per `/job-search` or `/apply` run and its values are bound to named placeholders (`{role_focus}`, `{locations}`, `{li_keywords}`, etc.) used throughout the skill steps — search for these names in the SKILL.md files to see exactly where a config value takes effect. Nothing user-specific (companies, keywords, PII) should ever be added to the skill markdown or scripts themselves; if a script or skill step needs a new tunable, it belongs in `config.yaml.example` with a comment, not inline.

### Integrations are additive, never required

Gmail, WhatsApp (via a local GOWA SQLite bridge, see `docs/whatsapp-setup.md`), Google Calendar, and Playwright MCP are all optional and independently gated — `/job-search` step 0a probes each and skips + nudges rather than failing when one is missing. When adding a new integration or source type, follow this pattern: probe availability first, degrade gracefully, record what was skipped in the run report's `sources_skipped`.

### Source scrapers follow one shape

New per-source scrapers live at `scripts/<source>_sweep.py` and mirror `scripts/linkedin_sweep.py`: use `lib.session_dir(name)` for a persistent Chrome profile (so login survives across runs), page/scroll until the result count stabilizes with a hard cap, emit JSON to stdout for the shared downstream dedup/filter pipeline, and log progress to stderr. `scripts/getro_sweep.py` is a generic driver reused across many Getro-hosted VC portfolio boards rather than one-off scripts per board.

## Privacy invariants (load-bearing, not just style)

- `config.yaml`, `standard-answers.md`, and `data/` are gitignored and must stay that way — real PII, browser session cookies, and full pipeline history live there. Never suggest `git add -f` on these paths.
- `.github/workflows/pii-guard.yml` runs on every push/PR and fails CI if tracked files contain phone-number-like strings or non-placeholder email addresses (only `*.example` files and `@example.com`/`@users.noreply` domains are exempt). Keep any new example/template files using obviously fake placeholder contact info.
- `/apply` must never click a real Submit button without explicit per-role user confirmation, and must never fill essay/cover-letter fields — this is enforced in the skill instructions, not code, so preserve it when editing `.claude/skills/apply/SKILL.md`.
