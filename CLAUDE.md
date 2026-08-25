# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An agentic job-search pipeline delivered as two Claude Code skills (`.claude/skills/job-search`, `.claude/skills/apply`) plus Python helper scripts they shell out to. There is no app server and no test suite — the skills *are* the product, and `SKILL.md` is the authoritative spec for their behavior. When changing pipeline behavior, edit the relevant `SKILL.md` first; the Python scripts are deliberately thin (scraping, file-munging, chart/report rendering) and should stay in sync with what the skill documents as their contract.

## Setup

```bash
cp config.yaml.example config.yaml   # edit profile/locations/keywords before first run
pip install -r scripts/requirements.txt   # pyyaml, playwright, matplotlib, markdown
python -m playwright install              # needed for the Python playwright package path
claude mcp add playwright -- npx -y @playwright/mcp@latest   # needed for /apply and interactive board fallbacks
```

Run the pipeline from inside Claude Code: `/job-search` (sweep) then `/apply` (work the queue). There is no way to "run" these skills outside Claude Code — they're prompts, not executables.

## Commands

No build/lint/test suite exists in this repo. The operative "commands" are the maintenance scripts the skills call:

```bash
python3 scripts/mark_stale.py [--dry-run] [--days N] [--today YYYY-MM-DD]   # demote inactive Applied/Screen/Interviewing -> Stale
python3 scripts/source_yield.py [--since YYYY-MM-DD]                        # per-source yield report from sources.yaml + listings
python3 scripts/generate_chart.py                                           # writes data/reports/chart-latest.png
python3 scripts/generate_run_report.py                                      # writes data/reports/run-N.html per run

# sweep scripts (stdout JSON, stderr progress log) — invoked by the job-search skill, runnable standalone for debugging:
python3 scripts/linkedin_sweep.py [--keep]
python3 scripts/getro_sweep.py <BOARD_URL> [--keyword KW] [--location LOC]
python3 scripts/page_dump.py <URL> [--wait MS] [--scroll]
python3 scripts/apply_driver.py <starting_url>
```

All scripts assume CWD = repo root and read `./config.yaml` + `./data/` via `scripts/lib.py` (override with `JOB_SEARCH_CONFIG` / `JOB_SEARCH_DATA` env vars). `lib.load_config()` exits with code 2 and a stderr message if `config.yaml` is missing — don't add a different missing-config path in new scripts.

## Architecture

**Everything personal is external to the skill text.** `config.yaml` and `standard-answers.md` (both gitignored, only `.example` variants tracked) hold the user's profile, keywords, blocklists, and applicant PII; `data/` (fully gitignored) holds all run state. The skills read config at the start of every run and bind it into named `{variables}` — never hardcode a user's profile/keywords/companies into `SKILL.md` or the scripts. CI (`.github/workflows/pii-guard.yml`) fails the build if a phone number or non-placeholder email lands in a tracked file, so treat that boundary as load-bearing, not advisory.

**The data model is plain markdown files, not a database.** One file per role at `data/listings/<company-slug>-<role-slug>.md`: YAML frontmatter (`status`, `url`, `source`, dates, etc.) plus a `## Communications` table that every Gmail/WhatsApp sync and status change appends a row to. Scripts that touch these files do surgical regex edits on frontmatter fields (see `mark_stale.py`'s `upsert_field`/`set_status`) rather than round-tripping through a YAML parser, to avoid reformatting content that wasn't touched.

**`data/sources.yaml` is the pipeline's self-growing memory**, not a static list. Flow: discovery (skill step 3d) → `probation` → smoke-test → `tier: 2` → measured every run → promote/demote/`parked` based on yield. Tier-1 sources (LinkedIn, Gmail, WhatsApp) are demotion-exempt. The demotion policy is days-based and yield-weighted (`dry_days = today - last_success`), not a simple N-run counter — see the header comment in `templates/sources.seed.yaml` for the exact rules, and don't reintroduce a run-counted strike rule (it breaks when cadence changes). Critically, **tooling failure (`unreachable`) never counts as signal-empty** — only `empty` (crawled successfully, found nothing) advances `consecutive_zeroes`; `unreachable` advances `unreachable_streak` toward `parked` instead. This distinction is threaded through the skill, the scripts, and the registry schema — preserve it in any change.

**Company career boards are hit via ATS JSON APIs before Playwright** (Greenhouse/Lever/Ashby/Comeet endpoints are documented in `job-search/SKILL.md` step 3b) — curl-and-parse is faster and more reliable than driving a browser for these. Playwright is reserved for authenticated/anti-bot surfaces (LinkedIn) and boards with no discoverable API. Every Playwright-driven source uses a **persistent Chrome profile** at `data/.sessions/<source>/` via `lib.session_dir()`, launched against **system Chrome, not bundled Chromium** (`integrations.browser.chrome_path`) — bundled Chromium gets anti-bot challenged, especially on LinkedIn. New scraper scripts should mirror `linkedin_sweep.py`'s shape: pagination loop with a stop-at-zero-new rule, JSON to stdout for the shared dedup/filter pipeline, persistent profile per source.

**`/apply` is a strict human-in-the-loop gate**, not an automation shortcut: it prefills known ATS fields from `config.yaml`'s `applicant:` block but never clicks Submit, never opens the next queue item, and never fills essay/cover-letter fields without explicit per-role user confirmation. Any change to `apply/SKILL.md` or `apply_driver.py` must preserve this contract — it's stated as a hard rule at the top of the skill, not an implementation detail.

## Working in this repo

- Treat `.claude/skills/*/SKILL.md` as the spec. If you change scraping/dedup/registry/status logic, update the corresponding numbered step in the skill doc in the same change — the prose there is what an agent actually executes at runtime.
- Never add real names, emails, phone numbers, or other PII to tracked files (including examples/docs) — use obviously-fake placeholders (`jane@example.com` style), matching the existing `.example` templates. The PII-guard CI job will catch tracked-file violations, but write clean the first time.
- `data/` and gitignored config files are real user state when present locally — never read them into anything that leaves the machine, and never suggest `git add -f` on them.
