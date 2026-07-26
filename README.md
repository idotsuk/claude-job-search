# claude-job-search

An agentic job-search pipeline for [Claude Code](https://claude.com/claude-code). One command sweeps your job market, tracks every role in a local markdown tree, syncs application statuses from your inbox and messages, and reports what changed — run after run, getting better at finding roles as its source registry learns your market.

**What you get:**

- **`/job-search`** — the sweep. LinkedIn (authenticated, via Playwright), company career boards (ATS JSON APIs first — Greenhouse/Lever/Ashby/Comeet — no browser needed), curated startup-list sweeps, plus a self-growing source registry: every run discovers new candidate sources, smoke-tests them, and promotes the ones that yield. Gmail and WhatsApp scans update your pipeline statuses automatically. Ends with a run report, a delta chart, and a self-contained HTML dashboard.
- **`/apply`** — the application assistant. Works your "To Apply" queue one role at a time: opens the canonical posting, prefills standard fields from your config, and **never submits without your explicit go-ahead**.
- **A plain-files data model** — every tracked role is a markdown file with YAML frontmatter and a communications log. Grep it, edit it, sync it to Obsidian, or pipe it anywhere.

Everything personal lives in two gitignored files (`config.yaml`, `standard-answers.md`) and the gitignored `data/` tree. The skills themselves contain zero personal information.

## Quick start

```bash
git clone https://github.com/<you>/claude-job-search.git
cd claude-job-search
cp config.yaml.example config.yaml
# edit config.yaml — profile, locations, LinkedIn keywords at minimum

# optional but strongly recommended (powers the LinkedIn + board sweeps):
pip install -r scripts/requirements.txt

claude          # start Claude Code in the repo
/job-search     # first run
```

The first run creates `data/`, seeds the source registry, and tells you exactly which integrations are missing and how to enable each. Nothing is required — the pipeline degrades gracefully.

## Opt-in integrations

Each integration is optional. The skill checks availability at the start of every run, skips what's missing, and lists what you'd gain by enabling it. Enable them in `config.yaml` (`integrations:`) as you go.

### Playwright — the important one

Two related pieces:

| Piece | Install | Powers |
|---|---|---|
| Python `playwright` package | `pip install -r scripts/requirements.txt` | `scripts/linkedin_sweep.py` — the authenticated LinkedIn sweep, usually the single highest-yield source — plus the Getro/board scrapers |
| Playwright MCP | `claude mcp add playwright -- npx -y @playwright/mcp@latest` | Interactive board fallbacks in `/job-search`, and all of `/apply` (form prefill) |

The LinkedIn sweep uses your **system Chrome** with a persistent profile at `data/.sessions/linkedin/` — you log in once in a visible window, and the session is reused on every later run. Your credentials never pass through Claude; you type them into LinkedIn's own login page.

**Without Playwright:** ATS JSON APIs, web search, and source discovery still run. You lose the LinkedIn sweep and SPA-board scraping — a real dent, which is why this is the first thing to set up.

### Gmail

Connect a Gmail MCP/connector to Claude Code (e.g. the claude.ai Gmail connector). The skill scans your inbox window each run and turns recruiter/ATS emails into status updates on your tracked roles — Applied → Screen → Interviewing → Rejected transitions happen without you touching a file.

**Without it:** statuses only change when you edit them or when WhatsApp provides the signal.

### WhatsApp (GOWA bridge)

A local bridge that syncs your WhatsApp history into a SQLite file on your machine — recruiter DMs become status updates and new listings. Setup takes ~20 minutes: see **[docs/whatsapp-setup.md](docs/whatsapp-setup.md)**. All data stays local.

**Without it:** step 7 is skipped; recruiter DMs stay in your phone.

### Google Calendar

Connect a Google Calendar MCP and set `integrations.calendar.enabled: true`. Every scheduled interview found in Gmail/WhatsApp gets verified against your calendar; missing events are created (self-only — it never emails your interviewers).

**Without it:** the run report still lists upcoming interviews; you book them yourself.

## How it works

A run walks these steps (`.claude/skills/job-search/SKILL.md`):

1. **Anchor today's date**, load config, preflight integrations.
2. **Load** the listings tree and run history.
3. **Sweep** every registered source in parallel: LinkedIn (quoted-phrase keywords, pagination, post-hoc blocklists), company boards (ATS JSON APIs before any browser), tier-2 boards, probation smoke-tests, and 2–3 meta-searches that discover **new** sources for next run. Curated startup lists get cross-referenced company-by-company against a persistent triage cache.
4. **Dedup** against the tree (company + role similarity; canonical URLs beat aggregators).
5. **Write** one markdown file per new role.
6. **Gmail scan** — classify the whole inbox window (no brittle `from:` filters), update statuses, log every touch in the role's communications table.
7. **WhatsApp scan** — keyword sweep over the local DB, biased to low-volume chats (recruiters, not friends).
8. **Maintain** — update source metrics (promote/demote), auto-demote stale applications.
9. **Report** — run file, two-panel chart, self-contained HTML dashboard.

### The source registry

`data/sources.yaml` is the pipeline's memory. It ships with just three tier-1 sources — LinkedIn, Gmail, WhatsApp — and grows itself:

```
discovery (step 3d) → probation → smoke-test → tier 2 → measured every run
                                      ↓                        ↓
                                   dropped  ←  consecutive zero-yield runs
```

Expect the registry to take **3–5 runs** to learn your market. Sources that can't be crawled (anti-bot, SPA without a scraper yet) are tracked separately from sources that yield nothing — tooling failures never get a source dropped.

### The data model

One file per role in `data/listings/`:

```yaml
---
title: Acme — Senior Backend Engineer
company: Acme
role: Senior Backend Engineer
status: To Apply        # → Applied → Screen → Interviewing → Offer
                        #   (or Rejected / Skipped / Passed / Stale)
url: https://jobs.acme.com/senior-backend
location: Berlin
level: Senior
type: Backend
first_added: 2026-07-26
source: linkedin_authenticated
---

Why it fits.

## Communications

| Date | Channel | Direction | Contact | Summary |
|------|---------|-----------|---------|---------|
```

`Stale` is automatic: any Applied/Screen/Interviewing role with no logged activity for `pipeline.stale_after_days` (default 7) gets demoted by `scripts/mark_stale.py`, so "Interviewing" always means real momentum.

## Data & privacy

- **`config.yaml`**, **`standard-answers.md`**, and **`data/`** are gitignored — your profile, PII, browser sessions, and full pipeline history never leave your machine and physically can't be pushed with this repo's `.gitignore`.
- Never `git add -f` those paths. If you want your *state* under version control, `git init` a separate private repo **inside** `data/` — the outer repo ignores it entirely.
- Browser sessions (`data/.sessions/`) hold real login cookies. Treat the directory like a password.

## `/apply` contract

- Never clicks Submit without your explicit per-role "yes".
- Never opens the next role without asking.
- Never fills essay/cover-letter fields — those need your voice.
- Flags companies where you've been rejected recently or have an application in flight, instead of silently re-applying.

## Requirements

- Claude Code (skills are project-scoped; they load automatically when you run `claude` in this repo)
- Python 3.10+ with `pip install -r scripts/requirements.txt` (pyyaml, playwright, matplotlib, markdown)
- Google Chrome (for the authenticated sweeps; path configurable via `integrations.browser.chrome_path`)

## Roadmap

- Cold-outreach queue (funding-signal targeting with a hard per-message approval gate) — deliberately left out of v1.
- More shared scrapers (`scripts/<source>_sweep.py` pattern) as the community's registries discover common boards.

## License

MIT
