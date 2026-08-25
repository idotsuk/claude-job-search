# claude-job-search

An agentic job-search pipeline for [Claude Code](https://claude.com/claude-code). One command sweeps your job market, tracks every role in a local markdown tree, scores each one against your actual CV, tailors a one-page resume per job, syncs application statuses from your inbox and messages, and reports what changed — run after run, getting better at finding roles as its source registry learns your market.

**What you get:**

- **`/job-search`** — the sweep. LinkedIn (authenticated, via Playwright), company career boards (ATS JSON APIs first — Greenhouse/Lever/Ashby/Comeet — no browser needed), a curated company watchlist (`search.target_companies` in `config.yaml` — give a company its known ATS board and it's polled directly, zero-token, no discovery step needed), curated startup-list sweeps, plus a self-growing source registry: every run discovers new candidate sources, smoke-tests them, and promotes the ones that yield. Gmail and WhatsApp scans update your pipeline statuses automatically. Ends with a run report, a delta chart, and a self-contained HTML dashboard.
- **`/triage`** — a local, one-at-a-time review UI for the "To Apply" pile: keep a listing queued, or decline it with a reason (company fit, role fit, tech-stack gap, other) and an optional note. Every decision writes back to the listing and to a decision log for `/job-search` to learn from on its next run.
- **`/score-listings`** — scores every "To Apply" listing 0–100 for pure technical/domain fit against your CV, with a plain-English verdict explaining the number, and a self-contained HTML report. Run this before you start applying, so you work the best-fit roles first.
- **`/tailor-cv`** — generates a per-job tailored, one-page CV: reorders and lightly rephrases your real content for relevance, never invents anything. Manual mode targets one listing by name; batch mode processes every scored listing above your configured threshold.
- **`/apply`** — the application assistant. Works your "To Apply" queue one role at a time: opens the canonical posting, prefills standard fields from your config, surfaces a warm-contact nudge right before you'd apply cold if `/network-scan` found one, and **never submits without your explicit go-ahead**.
- **`/network-scan`** — an occasional (e.g. monthly) check of your LinkedIn connections' current employers against the listings `/job-search` already found: flags "you know someone here — reach out before applying" on any match. Doesn't discover jobs itself; finding roles is `/job-search`'s job alone.
- **A plain-files data model** — every tracked role is a markdown file with YAML frontmatter and a communications log. Grep it, edit it, sync it to Obsidian, or pipe it anywhere.

Everything personal lives in gitignored files (`config.yaml`, `standard-answers.md`) and the gitignored `data/` tree. The skills themselves contain zero personal information — clone this repo and it works the same for anyone, starting from *your* CV, not the CV it happened to be built against.

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

The first run creates `data/`, seeds the source registry, and — if `applicant.cv_path` isn't set yet — asks for the path to your CV. Everything else degrades gracefully: nothing is required to get useful output.

```
/job-search       # sweep for new roles, run this regularly (weekly is typical)
/triage           # optional: work the To Apply pile one card at a time, keep or decline
/score-listings   # rank the queue against your CV; asks a one-time setup question the first time (see below)
/tailor-cv        # optional: generate a tailored CV PDF for a specific role, or a batch of top-scored ones
/apply            # work the queue, highest-scored first; never submits without your go-ahead
/network-scan     # optional, occasional: flag which tracked listings you have a contact at
```

### Bringing your own CV

The first time `/tailor-cv` runs (directly, or via `/score-listings` if you've asked it to build a richer background file — see below), it builds `data/cv-base-content.yaml` from **your** CV at `applicant.cv_path`, not a template someone else wrote. Two things happen automatically:

1. **Your content** gets extracted into the structured format every tailored CV starts from — your actual roles, bullets, education, skills. Nothing invented, nothing borrowed from anyone else's resume.
2. **Your design, if you want it.** If you also have your CV as a Word (`.docx`) file, `/tailor-cv` asks whether you'd like your tailored CVs to match its exact original look (fonts, colors, layout) — it extracts that design directly from the document, the same way the repo's own default template was originally built. Say no (or don't have a `.docx`) and you get a clean, generic built-in default instead — nothing else changes, and you can always personalize it later just by pointing `/tailor-cv` at your `.docx` when you're ready.

This only ever happens once. After that, both `data/cv-base-content.yaml` and (if you built one) your personalized template are reused on every future run.

### Career notes: resume-only, or a short interview

A one-page CV leaves things out. `/score-listings` asks, the first time it runs, whether you want:

- **Resume only** — fast, scores strictly from your CV as-is, no further setup.
- **A short interview** — a handful of targeted questions (not generic "tell me about yourself") about the parts of your CV that read thin — a tool mentioned but never tied to a project, a vague impact claim with no number, a compressed role. Your answers get written to `data/career-notes.md`, which both `/score-listings` and `/tailor-cv` then treat as equally valid evidence alongside your CV, permanently, on every future run.

Your answer is saved to `config.yaml` (`career_notes.use_interview`) so you're only asked once. Change your mind later by editing that value directly.

## Opt-in integrations

Each integration is optional. The skill checks availability at the start of every run, skips what's missing, and lists what you'd gain by enabling it. Enable them in `config.yaml` (`integrations:`) as you go.

### Playwright — the important one

Two related pieces:

| Piece | Install | Powers |
|---|---|---|
| Python `playwright` package | `pip install -r scripts/requirements.txt` | `scripts/linkedin_sweep.py` — the authenticated LinkedIn sweep, usually the single highest-yield source — plus the Getro/board scrapers, and `/tailor-cv`'s PDF rendering |
| Playwright MCP | `claude mcp add playwright -- npx -y @playwright/mcp@latest` | Interactive board fallbacks in `/job-search`, and all of `/apply` (form prefill) — `/network-scan` needs no browser at all |

The LinkedIn sweep uses your **system Chrome** with a persistent profile at `data/.sessions/linkedin/` — you log in once in a visible window, and the session is reused on every later run. Your credentials never pass through Claude; you type them into LinkedIn's own login page.

**Without Playwright:** ATS JSON APIs, web search, and source discovery still run. You lose the LinkedIn sweep and SPA-board scraping, and `/tailor-cv` can't render a PDF (it can still produce the HTML) — a real dent, which is why this is the first thing to set up.

### python-docx — only if you want a personalized CV design

`pip install -r scripts/requirements.txt` also installs `python-docx`, needed only if you want `/tailor-cv` to extract your own CV's design from a `.docx` file (see "Bringing your own CV" above). Skip it and you still get full tailoring against the clean built-in default template.

### Gmail

Connect a Gmail MCP/connector to Claude Code (e.g. the claude.ai Gmail connector). The skill scans your inbox window each run and turns recruiter/ATS emails into status updates on your tracked roles — Applied → Screen → Interviewing → Rejected transitions happen without you touching a file.

**Without it:** statuses only change when you edit them or when WhatsApp provides the signal.

### WhatsApp (GOWA bridge)

A local bridge that syncs your WhatsApp history into a SQLite file on your machine — recruiter DMs become status updates and new listings. Setup takes ~20 minutes: see **[docs/whatsapp-setup.md](docs/whatsapp-setup.md)**. All data stays local.

**Without it:** step 7 is skipped; recruiter DMs stay in your phone.

### Google Calendar

Connect a Google Calendar MCP and set `integrations.calendar.enabled: true`. Every scheduled interview found in Gmail/WhatsApp gets verified against your calendar; missing events are created (self-only — it never emails your interviewers).

**Without it:** the run report still lists upcoming interviews; you book them yourself.

### LinkedIn contacts export — only for `/network-scan`

`/network-scan` needs a CSV of your LinkedIn connections. See **[docs/linkedin-contacts-export-guide.md](docs/linkedin-contacts-export-guide.md)** for how to download it, then point `search.linkedin_contacts_csv_path` at it in `config.yaml`.

**Without it:** `/network-scan` won't run; nothing else is affected — it's entirely separate from the regular `/job-search` sweep.

## How it works

### `/job-search`

A run walks these steps (`.claude/skills/job-search/SKILL.md`):

1. **Anchor today's date**, load config, preflight integrations.
2. **Load** the listings tree and run history.
3. **Sweep** every registered source in parallel: LinkedIn (quoted-phrase keywords, pagination, post-hoc blocklists), the curated company watchlist (pre-configured `{name, ats, board}` entries hit the board's JSON API directly, no discovery step), other company boards (ATS JSON APIs before any browser), tier-2 boards, probation smoke-tests, and 2–3 meta-searches that discover **new** sources for next run. Curated startup lists get cross-referenced company-by-company against a persistent triage cache.
4. **Dedup** against the tree (company + role similarity; canonical URLs beat aggregators).
5. **Write** one markdown file per new role.
6. **Gmail scan** — classify the whole inbox window (no brittle `from:` filters), update statuses, log every touch in the role's communications table.
7. **WhatsApp scan** — keyword sweep over the local DB, biased to low-volume chats (recruiters, not friends).
8. **Maintain** — update source metrics (promote/demote), auto-demote stale applications.
9. **Report** — run file, two-panel chart, self-contained HTML dashboard.

### `/score-listings`

Builds the queue of unscored `To Apply` listings, fans out to parallel subagents (batches of ~8–10), and for each: fetches the live job description (or falls back to the stored listing body if the URL isn't fetchable), scores 0–100 on domain overlap / stack overlap / core-competency match against your CV (and `data/career-notes.md`, if you opted into the interview), and writes a specific plain-English verdict — never generic filler. Renders `data/reports/match-scores-latest.html` with a ring-gauge per listing. Re-score anything with `/score-listings rescore` (everything) or `/score-listings rescore <query>` (a filtered subset).

### `/tailor-cv`

For a single listing (`/tailor-cv <company or role>`) or in batch (`/tailor-cv` with no argument, every scored listing above `tailoring.auto_tailor_min_score`): reorders and lightly rephrases your real CV content for relevance to that job's JD, fills your template (default or personalized — see "Bringing your own CV"), and renders a one-page PDF via `scripts/render_cv.py`. Enforces one page mechanically, not by hoping the model gets it right. Re-run manual mode on an already-tailored listing to redo it from scratch.

### `/network-scan`

Reads your exported LinkedIn connections, extracts each unique current employer, and checks whether that company already has a tracked listing in `data/listings/` — found by `/job-search`, or by an earlier `/network-scan` run. Where it does, flags `warm_contact: true` and `contact_name` so you know exactly who to message before applying. It never visits a careers page or writes a new listing — job discovery stays entirely with `/job-search`.

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
match_score: 82                  # written by /score-listings
match_verdict: "..."
cv_status: draft                 # written by /tailor-cv
warm_contact: true                # written by /network-scan
---

Why it fits.

## Communications

| Date | Channel | Direction | Contact | Summary |
|------|---------|-----------|---------|---------|
```

`Stale` is automatic: any Applied/Screen/Interviewing role with no logged activity for `pipeline.stale_after_days` (default 7) gets demoted by `scripts/mark_stale.py`, so "Interviewing" always means real momentum.

## `/triage`

Run `/triage` to work through the "To Apply" pile one card at a time in a local browser tab (`scripts/triage_server.py`, no extra install — stdlib only). **Keep** leaves a listing queued; **Decline** demotes it to `status: Skipped` with a categorized reason and, for anything but company fit, a required note on what's missing. Prev/Next let you browse and revise freely; **Finish for now** ends the session anytime, progress saved.

Every decline is also logged to `data/decline-log.yaml`. Before its *next* scan, `/job-search` (step 0b) reads it and asks about each one directly — a single decline is enough, it doesn't wait for a repeat — e.g. "You declined Acme for company fit — add it to `company_blocklist`?" A yes edits `config.yaml` (`company_blocklist`, `profile.anti_interests`, or `search.location_blocklist`) immediately, in time to filter that same run; either way, that decline is marked seen so it won't ask again on the same evidence.

## Data & privacy

- **`config.yaml`**, **`standard-answers.md`**, and **`data/`** are gitignored — your profile, PII, browser sessions, CV content/design, and full pipeline history never leave your machine and physically can't be pushed with this repo's `.gitignore`.
- Never `git add -f` those paths. If you want your *state* under version control, `git init` a separate private repo **inside** `data/` — the outer repo ignores it entirely.
- Browser sessions (`data/.sessions/`) hold real login cookies. Treat the directory like a password.
- A `.github/workflows/pii-guard.yml` CI check fails the build if a phone number or non-placeholder email ever lands in a tracked (non-`data/`) file — a safety net, not a substitute for care.

## `/apply` contract

- Never clicks Submit without your explicit per-role "yes".
- Never opens the next role without asking.
- Never fills essay/cover-letter fields — those need your voice.
- Flags companies where you've been rejected recently or have an application in flight, instead of silently re-applying.

## Requirements

- Claude Code (skills are project-scoped; they load automatically when you run `claude` in this repo)
- Python 3.10+ with `pip install -r scripts/requirements.txt` (pyyaml, playwright, matplotlib, markdown, python-docx)
- Google Chrome (for the authenticated sweeps; path configurable via `integrations.browser.chrome_path`)

## Roadmap

- Cold-outreach queue (funding-signal targeting with a hard per-message approval gate) — deliberately left out of v1.
- More shared scrapers (`scripts/<source>_sweep.py` pattern) as the community's registries discover common boards.

## License

MIT
