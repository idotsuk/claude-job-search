# Job-search tool comparison: claude-job-search vs. proficiently vs. career-ops

Reviewed 2026-08-08. Goal: decide what to fold into `claude-job-search` (the base we're keeping) from the other two, to get one system that finds the most relevant jobs, scores match quality, produces a CV you actually like, applies, and visualizes the pipeline.

## At a glance

| | **claude-job-search** (base) | **proficiently** | **career-ops** |
|---|---|---|---|
| Where it lives | `~/Projects/claude-job-search` (your friend's, you extended it) | `~/.claude/plugins/.../proficiently` (plugin) + data in `~/.proficiently/` | `~/Projects/career-ops` (3rd-party OSS, most GitHub stars) |
| Trigger | `/job-search`, `/apply`, `/score-listings` (yours) | `/proficiently:job-search`, `:apply`, `:tailor-resume`, `:cover-letter`, `:network-scan`, `:setup` | `/career-ops pipeline` (+ `interview`, `oferta`, `apply`, others) |
| History format | Per-listing Markdown file, YAML frontmatter + Communications log | Append-only Markdown log tables (`job-history.md`) | Markdown table (`applications.md`) as source of truth + derived SQLite index |
| Dedup | Structural: index of `(company, role)` from frontmatter, fuzzy title match | Soft: LLM reads whole history file and eyeballs it each run | Structural: locked writes via `set-status.mjs`, append-only `status-log.tsv` ledger |
| Discovery sources | LinkedIn (Playwright), ATS JSON APIs, Getro VC boards, curated news/funding roundups, self-growing source discovery, **Gmail + WhatsApp mining** | `hiring.cafe` (boutique aggregator) resolved to real employer URL; **LinkedIn-contacts → employer career pages** via WebSearch | Zero-token ATS API scans (Greenhouse/Ashby/Lever/BambooHR/Workday/...) against a **curated company list**, funding-news RSS feeds, manual URL/JD paste |
| Why it finds jobs others miss | Recruiter DMs/emails that never hit a job board at all | Goes straight to the employer's ATS via contacts' current companies — bypasses boards entirely | Direct API polling of a large pre-configured company list, incl. companies too small/new to show up on aggregators |
| Match scoring | Your add-on: LLM-judged 0-100 vs. CV, technical/domain fit only, permanent once scored | `fit-scoring.md`: dealbreakers → must-haves → nice-to-haves → High/Med/Low/Skip, re-evaluated each run | Full "Blocks A-G" evaluation report per offer + numeric score, gates PDF generation and answer-drafting |
| User profile depth | Thin: structured `config.yaml` (contact info, prefs, blocklists) — CV itself is read live from PDF, not paraphrased | Rich: `profile.md` built via structured interview, Situation→Action→Result accomplishments, continuously corrected from resume-review feedback | Richest: `cv.md` + `config/profile.yml` (archetypes, narrative, proof points) + `modes/_profile.md` + story bank + voice-DNA, continuous-capture loop after every evaluation |
| CV tailoring | **None** — uploads your static PDF as-is | Full LLM rewrite of resume content per job, fixed HTML/CSS output, has an explicit "kill AI-sounding prose" pass | Template-engine: agent fills a JSON payload, deterministic `{{PLACEHOLDER}}` HTML template renders it — only 4 style tokens are meant to be user-editable (accent color, font, size, margin) |
| Apply automation | Playwright-driven, prefills standard fields, hard-gated on per-role human confirmation before Submit | Same pattern (Greenhouse/Lever/Workday specific quirks documented), 2-stage approval, file uploads still manual (MCP image-upload limitation) | No autonomous submission at all — drafts answers for copy-paste only, explicit "never click Submit" rule |
| Visualization | matplotlib chart (new/cumulative + status pie) + generated HTML run report + your match-score HTML report with SVG ring gauges | **None** — markdown/JSON/CSV only | Terminal-only Go/Bubble Tea TUI dashboard reading `applications.md` directly — no HTML/browser artifact |

## Deep dive: how each one actually works

### 1. claude-job-search (your base)

**Storage**: `data/listings/<company>-<role>.md` — one file per role, YAML frontmatter (status, source, your `match_score/verdict/confidence` fields) + a `## Communications` table logging every WhatsApp/email touchpoint. `data/sources.yaml` tracks each discovery source's yield over time with a tier/probation/parked/dropped lifecycle — sources that stop producing results get automatically demoted. `data/runs/run-N.md` is a per-run audit log.

**Discovery**: LinkedIn via Playwright, ATS JSON APIs (Greenhouse/Lever/Ashby/Comeet) via direct curl before falling back to a browser, Getro-hosted VC portfolio boards, curated "best startups"/funding-news articles cross-referenced against a company index, and a self-growing discovery loop that runs 2-3 meta web-searches per run looking for new sources to add to the probation queue.

**The WhatsApp/email mechanism** (the part you like most): Gmail is scanned by pulling the *entire* inbox window (no `from:` filter, since that misses real recruiters at non-obvious domains) and classifying every thread with 6 heuristics. WhatsApp is scanned by running raw SQL `LIKE` queries against a local SQLite DB populated by a self-hosted bridge (GOWA), biased toward low-volume chats and keyed off keyword lists in English and Hebrew. Both write status transitions and Communications rows back into the listing files — this is genuinely unique among the three; neither proficiently nor career-ops reads your messaging history at all.

**Match score** (your add-on): pure LLM judgment against your live CV vs. the JD, technical/domain fit only, fanned out to parallel subagents, surgically upserted into frontmatter via regex so it never clobbers the rest of the file.

**Gap**: no CV tailoring exists in this repo at all — `/apply` explicitly refuses to touch resume/cover-letter content, "those need your voice." It just uploads your static PDF.

### 2. proficiently

**Storage**: append-only Markdown log (`job-history.md`), one table per search run — dedup is done by the LLM reading the whole file and eyeballing title/company matches, not a structural index. `company-careers.json` is the one real structured cache: company name → resolved careers URL (direct site or ATS board), with a 7-day freshness window.

**Discovery, the interesting part**: `network-scan` resolves each of your LinkedIn contacts' current employer to a careers URL via plain WebSearch (explicitly *not* browser automation for this step), builds `company-careers.json` from that, then a second pass visits each cached page with browser automation to look for matching roles. This means it's finding jobs specifically at companies where you have a warm contact — a fundamentally different targeting strategy than either of the other two tools. `job-search` itself is a single-aggregator tool (`hiring.cafe`), used mainly to resolve through to the real employer posting rather than as the source of truth.

**Profile depth**: `profile.md` is built through a proper structured interview (`conduct-interview.md`) capturing Situation→Action→Result accomplishments with metrics per role, plus a synthesized "superpower"/cross-role-patterns section. Critically, this file gets corrected in place any time you push back on a factual detail while reviewing a tailored resume — it's a living document, not a one-time snapshot.

**CV tailoring**: full LLM rewrite per job (not template substitution), with an unusually elaborate mandatory "critique and rewrite" pass specifically built to strip AI-sounding prose (no emdashes, no "leveraging"/"demonstrating ability to" filler, Flesch readability >90). But it's bound by strict anti-fabrication rules — it can only reframe/reorganize facts already in `resume/` + `profile.md`, never infer or embellish. The fixed output format (`resume-template/FORMAT.md`) is plain HTML-in-markdown rendered via `md-to-pdf`. This combination — rule-bound rewriting plus a thin, generic-reading fixed template — is likely exactly why you find the output mediocre: it's not that the writing engine is bad, it's that (a) it's capped by whatever's in `profile.md`, and (b) the visual format has no room to be distinctive.

### 3. career-ops

**Storage**: everything is plain files, explicitly "files are canonical, databases are derived." `applications.md` is a single Markdown table; direct edits are forbidden by convention — additions go through TSV drop files merged by a script, status changes go through a locked, atomic `set-status.mjs`, with every transition also appended to a `status-log.tsv` ledger. This is the most rigorously engineered of the three for data integrity.

**Discovery**: mostly zero-token — direct HTTP calls to public ATS APIs (Greenhouse, Ashby, Lever, BambooHR, Teamtailor, Workday, Breezy) against a curated list of 100+ pre-configured companies in `portals.yml`, plus a reverse-ATS keyword sweep and a funding-news RSS feed (TechCrunch/PR Newswire/Guardian/HN) that surfaces recently-funded companies as review-only candidates. No LinkedIn scraping, no messaging integration.

**Profile depth (the part you like)**: the richest of the three. `cv.md` (canonical facts) + `config/profile.yml` (structured: target-role archetypes with fit tiers, narrative/superpowers, proof points with hero metrics, negotiation preferences) + `modes/_profile.md` (freeform narrative) + a `story-bank.md` of accumulated STAR+R interview stories + a `voice-dna.md` writing-style guardrail file. It has a hard rule that CV/cover-letter generation may only draw from these named files, never fabricate — and after every evaluation, corrections get written back into them. This is a stronger and more structured version of the same "living profile" idea proficiently has.

**CV generation (why it feels rigid)**: this is the key finding for your complaint. It is *not* LLM-authored HTML — the agent fills a JSON payload of structured fields, and a deterministic script (`build-cv-html.mjs`) fills a fixed `{{PLACEHOLDER}}` HTML template. Design (fonts, gradient header color, layout, spacing) is baked into `templates/cv-template.html`. Only 4 things are meant to be user-tunable via `config/profile.yml → style:`: accent color, font family, font size, margin. Everything else — header layout, section chrome, spacing, box styling — requires editing the template file directly, and that file is flagged as "system layer" (safe to auto-update), meaning a hand-edit risks being silently overwritten on the next `update-system.mjs` run. The documented-safe way to get a custom look is to create a *new* named template file (`templates/cv-template.<yourname>.html`) and point `config/profile.yml → cv.template` at it — the updater won't touch a filename it doesn't recognize. There's precedent for this (a `cv-template.zh-minimal.html` variant already exists in the repo).

**Apply**: no autonomous submission anywhere — strictly draft-and-copy-paste, with an explicit rule to never click Submit.

**Visualization**: terminal-only (Go/Bubble Tea TUI), reads `applications.md` directly. No browser/HTML artifact.

## What this suggests for integration into claude-job-search

Not a decision yet — just what the research surfaces as the live options, to work through in the grill session:

1. **Discovery gap to close**: claude-job-search has no "check contacts' current employers directly" pathway (proficiently's `company-careers.json`/network-scan idea) and no curated-company zero-token ATS polling at career-ops' scale (100+ companies via direct API, no LLM tokens spent on discovery itself). Either could plausibly be ported in as an additional source in `data/sources.yaml`.
2. **CV tailoring is the one feature missing entirely** from your base tool. Two different reference implementations exist: proficiently's rule-bound LLM rewrite (good prose discipline, weak/generic template) vs. career-ops' template-engine approach (rigid design, but a documented safe customization path via template swap + JSON payload). Since you said you want to *keep your current format or a simpler one* with *subtle* changes rather than career-ops' current fixed design, this points toward building a tailoring step closer to career-ops' architecture (deterministic template render) but with a template you design yourself, rather than adopting either system's output wholesale.
3. **Profile depth**: your `config.yaml` is much thinner than either competitor's profile system. Both proficiently's structured interview and career-ops' `profile.yml`/story-bank/voice-DNA setup are candidates to emulate — career-ops' is more elaborate, proficiently's is simpler and already proven to produce decent prose.
4. **Everything else** (history storage, dedup, visualization, apply-gating) claude-job-search already does at least as well as, and in some respects (WhatsApp/email mining, match-score ring-gauge report) meaningfully better than, either competitor.
