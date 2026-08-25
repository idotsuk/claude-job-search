---
name: job-search
description: Run a job-search sweep — find new roles matching the user's configured profile across LinkedIn, job boards, and company career pages; sync application statuses from Gmail and WhatsApp; track everything in a local listings tree; and produce a run report with charts. Use when the user asks to search for jobs, find new job listings, update their job pipeline, or check application statuses.
---

# Job Search Update

<!-- version: bare-v1.0 -->

Search for new job opportunities matching the user's configured profile, deduplicate against existing listings in `data/listings/`, add only new entries, sync statuses from connected channels, and log a run report.

**Companion skill:** after a run, use `/apply` to work through the "To Apply" queue (Playwright-driven, one role at a time, user-confirmed submits).

All personal specifics (profile, keywords, blocklists, integration paths) live in `./config.yaml` — nothing user-specific is hard-coded here. All state lives in `./data/` (gitignored).

## Steps

### 0. Anchor to today's date (do this first, every run)

Before anything else, establish **today's actual date** from the environment context (the `currentDate` line in the system context, or `date +%F`). Write it down and use it as `{today}` everywhere below. Do NOT infer "today" from the most recent email timestamp, the previous run's date, or a calendar-event date — those are frequently in the past or future relative to now. Every `applied_date` / `last_update` / body-note date and every run-report `date:` MUST be this anchored value, written as an absolute `YYYY-MM-DD`.

### 0a. Load config + preflight (the binding step)

1. **Read `./config.yaml`.** If it does not exist, STOP and tell the user:
   > No config found. Run `cp config.yaml.example config.yaml`, edit it (profile, locations, LinkedIn keywords at minimum), then re-run `/job-search`.

   Do not create `data/` or write anything before config exists.

2. **Bind these variables** from config and use them everywhere below. Echo the bound values (one compact block) into the conversation and later into the run log, so misbinding is visible:
   - `{role_focus}`, `{interests}`, `{anti_interests}`, `{seniority}`, `{locations}`, `{languages}`, `{timezone}` ← `profile.*`
   - `{cv_path}` ← `applicant.cv_path`
   - `{target_companies}`, `{company_blocklist}`, `{location_blocklist}`, `{rejected_companies}`, `{agency_blocklist}`, `{excluded_domains}` ← `search.*`
   - `{li_keywords}`, `{li_location}`, `{li_tpr}` ← `search.linkedin.*`; `{discovery_hints}` ← `search.discovery_hints`
   - `{gmail_enabled}`, `{gmail_window}`, `{wa_enabled}`, `{wa_chat_db}`, `{wa_contacts_db}`, `{wa_keywords}` (only the language sets in `{languages}`), `{wa_max_chat}`, `{cal_enabled}`, `{chrome_path}` ← `integrations.*`
   - `{stale_days}` ← `pipeline.stale_after_days`

3. **Create the data tree** if missing: `mkdir -p data/listings data/runs data/reports data/.sessions`. If `data/sources.yaml` doesn't exist, copy the seed: `cp templates/sources.seed.yaml data/sources.yaml`.

4. **Probe integrations** and build an availability map. NEVER hard-fail on a missing integration — skip its step, nudge once, and record it in the run report's `sources_skipped:` list:
   - **Playwright (Python)** — `python3 -c "import playwright"`. Powers `scripts/linkedin_sweep.py` and the other sweep scripts. If missing, nudge: `pip install -r scripts/requirements.txt && python -m playwright install`. This is the highest-value integration — without it the LinkedIn sweep (usually the top source) is skipped.
   - **Playwright MCP** — are `mcp__playwright__*` tools available (ToolSearch)? Used for interactive board fallbacks and by `/apply`. If missing, nudge: `claude mcp add playwright -- npx -y @playwright/mcp@latest`.
   - **Gmail** — is a Gmail MCP/connector available (search tools for gmail/search_threads)? Gate step 6 on this AND `{gmail_enabled}`. If missing, nudge to connect a Gmail integration.
   - **WhatsApp** — does `{wa_chat_db}` exist on disk AND `{wa_enabled}`? If either is false, skip step 7 and nudge: see `docs/whatsapp-setup.md` (full bridge setup, ~20 min). If both true, that only proves the bridge was *set up* — the local GOWA process may be intentionally left off between runs, not always-on. Step 7 reads the SQLite file directly and works fine offline, so this check is about freshness, not availability. Check liveness: call `mcp__whatsapp__whatsapp_connection_status` (ToolSearch it if not yet loaded).
     - Returns `is_connected: true` and `is_logged_in: true` → bridge is live, data is current as of now; gate step 7 open, no prompt needed.
     - Tool isn't registered, the call errors, or it returns not-connected → bridge is offline, the file may be stale. **Ask the user**: "WhatsApp bridge isn't running — (s)tart it to sync fresh messages first, (u)se the existing data as-is, or (k)ip this step?"
       - **Start** → run `cd ~/gowa/src && nohup ./whatsapp mcp > /tmp/gowa-mcp.log 2>&1 & disown`, wait ~3s, re-check `whatsapp_connection_status` (reconnecting a paired device catches up on messages received while offline), then gate step 7 open.
       - **Use existing** → gate step 7 open as-is; note in the run report that WhatsApp data may be stale (bridge was offline this run) so a gap in coverage is visible, not silent.
       - **Skip** → skip step 7 this run and record `whatsapp_db (bridge not running, skipped by user)` in `sources_skipped` — distinct from the never-set-up case, so the run report doesn't wrongly suggest a full setup is needed.
   - **Calendar** — is a Google Calendar MCP available AND `{cal_enabled}`? Gate step 7a.

   ATS JSON APIs and web search need no integration — a run with zero connections still sweeps company boards and discovers sources.

5. **Ask for the CV if it's missing.** If `{cv_path}` is unset or the file doesn't exist, ask the user for the path to their current CV/resume (once per run, not per step) and write it back to `config.yaml` under `applicant.cv_path`. Having the CV on file lets triage and `/apply` work from the user's actual experience instead of the one-line `role_focus`. If the user declines or doesn't have one handy, continue the run normally and note it under `sources_skipped`-style remarks in the run report body — never block a run on it.

### 0b. Triage-suggestion pass (interactive, before the scan)

Runs here — before any searching — so that a suggestion you accept can filter *this* run's results, not just the next one.

1. If `data/decline-log.yaml` doesn't exist, skip this step silently — nothing to learn from yet.
2. Otherwise run `python3 scripts/triage_suggestions.py` and read its JSON: every decline-log entry (`id`, `date`, `file`, `company`, `role`, `reason`, `note`, and `suggested: true` if it's already been asked about).
3. Group entries **that include at least one without `suggested: true`** into candidates — a group built entirely from already-`suggested` entries has nothing new to say and must not be re-asked. Unlike an early version of this step, **a single decline is enough to ask about — don't wait for a repeat**; a repeat just makes the ask more confident, it isn't the bar for asking at all:
   - **`reason: company_fit`** → group by `company` (case-insensitive); even one decline at a company is a candidate → add the company to `search.company_blocklist`.
   - **`reason: stack_gap`** → group by shared technology/keyword in `note` (your own judgment reading the free text, not a literal string match); a note with no obvious match to any other entry is still its own one-entry candidate → add the keyword to `profile.anti_interests`.
   - **`reason: role_fit`** → group by shared title keyword across `role`/`note`, same one-entry-is-enough rule → `anti_interests`.
   - **`reason: other`** → judge the `note`: if it names a place (city/region), group by that place → candidate: add it to `search.location_blocklist`. Otherwise treat it like `role_fit`/`stack_gap` → `anti_interests`. A note-free `other` entry has nothing to act on — skip it, there's no signal to ask about.
   - Skip any candidate whose value is already present in the relevant `config.yaml` list — nothing left to suggest.
4. For each candidate, ask the user directly and concretely — one plain yes/no question per candidate, naming the specific decline(s) behind it, e.g.: *"You declined Acme for company fit — add Acme to `search.company_blocklist`?"* (or, once it's a repeat: *"You've now declined 2 roles at Acme for company fit..."*).
5. On **yes**: edit `config.yaml` yourself right then (append to the named list, matching its existing formatting — `search.location_blocklist` is a new plain string list, same shape as `company_blocklist`, add it under `search:` with a one-line comment if it isn't in the file yet) so this run's search/filter steps already see it.
6. **Either way** (yes or no): run `python3 scripts/triage_suggestions.py --mark-seen <id> <id> ...` for every entry that contributed to that candidate. This is what makes a "no" stick — that exact entry won't be asked about again; a *different* future decline (a new company, a new keyword, or a second decline reinforcing an already-declined one) still gets its own fresh ask.
7. Track what was actually **applied** (not merely asked) as `{triage_config_updates}`, a list of short strings like `"company_blocklist += Acme"`, for the run report (step 8).

Never touch `config.yaml` from this step without an explicit yes — same rule as any other config change.

### 1. Load existing listings

```bash
ls data/listings/*.md | wc -l   # baseline count
```

For each file, read frontmatter to build the dedup index:
```python
import yaml, pathlib
existing = {}
for p in pathlib.Path('data/listings').glob('*.md'):
    text = p.read_text()
    fm_end = text.find('\n---', 4)
    fm = yaml.safe_load(text[4:fm_end])
    existing[(fm['company'].lower(), fm['role'].lower())] = p
```

### 2. Load run history

```bash
ls data/runs/*.md
```

Read each run file's frontmatter to find the highest run number → next run = N+1. (First ever run: N=1.)

### 3. Search for new opportunities

**Source of truth: `data/sources.yaml`.** That file lists every source we sweep, with `tier`, `last_yield`, `consecutive_zeroes`, and a `probation` queue for unsmoke-tested candidates. Read it first — don't hard-code source lists in this skill.

The seed registry ships with only the three tier-1 sources (LinkedIn, Gmail, WhatsApp). **The registry grows itself**: step 3d discovers candidates every run, step 3c smoke-tests them, and the yield metrics promote or drop them. Expect a thin tier-2 for the first 3–5 runs — that's normal, not broken.

Registry conventions:
- **Tier-1 is demotion-exempt.** Zero-yield runs on tier-1 sources never demote them.
- **Self-healing company sweep (the curated company watchlist):** if `{target_companies}` is non-empty and no `company_careers_direct` entry exists in the registry, append one (`tier: 1`, `type: company_careers`) before sweeping. This is the zero-token watchlist source described in step 3a below.

Run all of the following in parallel during one search pass.

#### a) Company-specific searches — the curated company watchlist (only if `{target_companies}` is non-empty)

`{target_companies}` (`search.target_companies` in config.yaml) doubles as a curated watchlist: a pre-configured list of companies to poll directly every run, independent of whatever LinkedIn/Getro/curated-list sources happen to surface that day — the same "direct API polling against a pre-configured company list" pattern career-ops uses at scale in `portals.yml`, adapted here rather than copied. Each entry is either:

- **A rich entry with known ATS coordinates** — `{name, ats, board}` (e.g. `{name: Wiz, ats: greenhouse, board: wiz}`). **Zero-token, no resolution step**: call the board's public JSON API directly, using the exact same endpoint patterns as step 3b (Greenhouse/Lever/Ashby/Comeet). This is the whole point of the watchlist — skip discovery entirely for companies you already know the board for. Never invent or guess ATS coordinates for an entry that doesn't specify them.
- **A plain company-name string** (legacy/lower-effort form, unchanged from before this feature) — resolve the canonical careers board first (check `data/company-index.yaml` for a cached `ats:` ref from a prior curated-list sweep, else search `site:careers.<company>.com "<role>"` or the company's known ATS), then sweep it. This costs a small resolution step every run. If resolution succeeds, mention the discovered `{ats, board}` in the run report so the user can paste it back into config.yaml as a rich entry — turning it zero-token for every future run.

Either form is matched against `{seniority}` + `{interests}` the same as any other source, deduped via step 4, and written via step 5 with `source: company_careers_direct` (the registry id for this source, from the self-healing bullet above) so the run report and `scripts/source_yield.py` attribute results back to the watchlist. Don't stop early. If the list is empty, skip this substep entirely — the registry + discovery pipeline covers the field.

**Design note (why this isn't a separate `data/company-watchlist.yaml`):** a user-curated "poll this company directly" list and per-company ATS metadata to skip resolution are the same underlying idea once a `target_companies` entry can be a plain name *or* a name+ATS-coordinates mapping — no need for a second file or a second lifecycle. `data/company-index.yaml` (step 3f) already established the `<ats>:<slug>` convention this reuses. Keeping watchlist entries in `config.yaml` — rather than a new gitignored data file — keeps ad-hoc "add Company X to the watchlist" edits in the same place as the user's other personal company lists (`target_companies` itself, `company_blocklist`, `rejected_companies`), instead of splitting one concept across two files.

#### b) Tier-1 + tier-2 sources from registry
Sweep every source in `data/sources.yaml` with `tier: 1` or `tier: 2` using its `query_template` or `url`. Tier-1 is always swept; tier-2 is swept AND measured.

**LinkedIn must use the authenticated Playwright scrape via `scripts/linkedin_sweep.py`** (skip + nudge if Python Playwright unavailable). Key contract — DON'T improvise on these:

1. **System Chrome, not bundled Chromium.** The script launches `launch_persistent_context(executable_path={chrome_path}, user_data_dir='data/.sessions/linkedin/', headless=False, ...)`. LinkedIn aggressively challenges bundled Chromium; system Chrome with persistent cookies works. First run only needs interactive login; subsequent runs reuse the session.
2. **Quoted exact-phrase keywords ONLY** (`{li_keywords}` from config). LinkedIn matches `keywords=` against the entire job posting body + skill tags, not just titles. Unquoted keywords pull in unrelated roles at matching companies (~10% hit rate); quoted phrases force title-side matching (~30%+ hit rate). Keep phrases specific to `{role_focus}`; avoid overly generic phrases that surface mid-level noise. Tune per run and record findings in the registry entry's `notes:` — that's where keyword lore accumulates, not in this skill.
3. **No `f_E` seniority filter.** It's not the noise axis. The seniority signal is in the keyword itself or in the post-hoc title regex. Adding `f_E=4,5,6` doesn't fix off-title matches and shrinks the corpus.
4. **Filter URL:** `https://www.linkedin.com/jobs/search/?keywords=<phrase>&location={li_location}&f_TPR={li_tpr}&sortBy=DD` (recent window + location + most recent first).
5. **Pagination:** loop `&start=0, 25, 50, …` up to `max_pages` per keyword. Each page also intra-scrolls the inner list container until the card count stabilizes (LinkedIn virtualizes). Stop early when LinkedIn shows the `.jobs-search-no-results-banner` OR a page returns 0 new URLs.
6. **Post-hoc filter:** drop companies in `{company_blocklist}` + `{agency_blocklist}` + `{rejected_companies}`, roles matching `{anti_interests}` / `{excluded_domains}`, and postings whose location(s) are **all** on `{location_blocklist}` (a place name filter, distinct from `{locations}`'s positive match) — split a multi-office `location` on commas first; a role offering even one non-blocked office stays. Dedupe by URL first, then by `(company, role)` slug similarity against the listings tree.
7. **Remote-without-location drop.** Unless `accept_remote_without_location_match` is true, drop roles whose location is `*Remote*` without any `{locations}` match — remote-anywhere posts rarely accept out-of-region candidates.

Known pitfalls (don't re-try):
- Google `site:` queries against LinkedIn/job boards — too thin; they miss most listings.
- Bundled Chromium for the Playwright scrape — anti-bot challenged; use system Chrome.
- Unquoted broad keywords — ~10% signal density; use quoted phrases.
- Overly tight posting-age windows (past week) usually miss slow-moving reposts; past 2 weeks is the default.

**Playwright is MANDATORY for all SPA-rendered sources. Do NOT use WebFetch / Google `site:` searches for these.**

**For company careers boards, try the ATS JSON API before Playwright.** The SPA problem is fetch-vs-rendered-DOM; a direct `curl` to the board's JSON API sidesteps it entirely and is faster and more reliable than driving the UI:
- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true` (EU-hosted boards still serve from the US endpoint)
- Lever: `https://api.lever.co/v0/postings/<slug>?mode=json`
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/<slug>`
- Comeet: `https://www.comeet.co/careers-api/2.0/company/<uid>/positions?token=<token>&details=true` — uid + token sit in the careers page source; `details=true` needed for JDs
- HiBob / Framer / Next.js sites: positions JSON is usually embedded in the page payload — curl + grep before reaching for a browser

Playwright remains the tool for authenticated/anti-bot surfaces (LinkedIn, login-walled boards) and boards whose API genuinely can't be located.

The same persistent-Chrome runtime is reused — per-source profiles live under `data/.sessions/<source>/`, so the user logs in **once per source** and subsequent runs reuse cookies.

**When a registry source needs a scraper**, build it as `scripts/<source>_sweep.py`, mirroring `linkedin_sweep.py`'s shape: pagination loop with a `MAX_PAGES`-style cap and "stop at zero new" rule, persistent profile at `data/.sessions/<source>/`, JSON to stdout for the shared downstream filter pipeline, and the same post-hoc blocklist filter. A generic driver for Getro-hosted boards ships as `scripts/getro_sweep.py` — many VC portfolio job boards are Getro-hosted and share a DOM. For quick SPA smoke-tests, `scripts/page_dump.py` renders any URL and dumps text + links.

**Tier-2 demotion rule for SPA-blocked sources:** If a source is `unreachable` solely because the scraper hasn't been written yet (vs. real anti-bot), don't demote — flag in the run report under "needs Playwright scraper." The `unreachable_streak >= 3` threshold applies only after Playwright has been attempted and still failed — and it moves the source to `parked` (`retry_after: today+30d`), never to `dropped`: tooling-blocked is not signal-empty.

**Parked retries:** any `parked:` entry whose `retry_after` has passed gets one smoke test this run. Success → restore to `tier: 2` (streaks reset, `last_success` set); still unreachable → `retry_after += 30d`, note in the run report.

#### c) Probation smoke-tests
For each entry in `sources.yaml:probation`, attempt the source. **Use Playwright first for any VC portfolio / careers / Getro / Greenhouse / Lever / Workday URL** — plain fetch returns SPA shells for these. WebFetch is only acceptable for plain-HTML news feeds or GitHub-style static pages.

**Distinguish "we crawled and found nothing" from "we couldn't crawl"** — record one of three outcomes in `last_status`:

- `success` — page rendered, listings extracted (may be 0)
- `empty` — page rendered, 0 matching roles found
- `unreachable` — HTTP 4xx/5xx, Cloudflare/anti-bot block, OR **scraper not yet written** (in which case the run-report must say so; this DOES NOT count toward the 3-strike unreachable-demotion rule)

Then count how many *new* (not already in listings tree) matching roles surface and apply:

- **≥1 valid result** → promote to `tier: 2` with `last_yield`, `runs_active: 1`, `consecutive_zeroes: 0`, `last_status: success`, `last_success: today`. Move out of `probation`.
- **0 results, `last_status: empty`, first test** → leave in `probation` with `consecutive_zeroes: 1` and note the test date.
- **0 results, `last_status: empty`, second test** → drop ONLY if this test is **≥7 days after the first zero-yield test**; if sooner, skip the re-test and leave it queued (a daily run cadence must not fast-fail a board that posts weekly). On a qualifying second zero → move to `dropped` with the reason in `notes`.
- **0 results, `last_status: unreachable`** → leave in `probation`, **DO NOT increment `consecutive_zeroes`**. Flag the source in the run report under "unreachable sources" so it's visible the cause was tooling, not signal. After 3 consecutive `unreachable` runs, move to `parked` with `retry_after: today+30d`.

The same `last_status` logic applies to tier-2 sources in step 7b: scrape failures never count toward yield-based demotion.

#### d) Discover new candidates (always, baked into the same step)
Before finishing the search pass, run 2–3 meta-searches for net-new sources we aren't already tracking. Build queries from `{locations}`, `{interests}`, and `{discovery_hints}`. Query templates (substitute your market):
- `"<region> VC portfolio jobs" {year}` / `"<region> <domain> VC portfolio" hiring`
- `<local tech press> funding round hiring {year}`
- `"<region>" <domain> "we're hiring" {year} site:substack.com OR site:newsletter.io`
- **Curated-list triggers:** `"most promising" <region> startups {year} list` / `<region> startups raised funding roundup {month} {year}` / `<region> tech acquisitions {year}` / `"best startups to work for" <region> {year}` — each hit is a candidate for a step-3f list sweep, seeded as `type: company_index`.

For each plausible new source: add it to `sources.yaml:probation` with `seeded: {today}`, `url`, and a one-line `rationale`. Aim for 1–2 net-new probation entries per run. The probation→tier-2 pipeline is the iterative-improvement mechanism — don't skip it, especially in early runs when the registry is still just the seed.

#### e) Track attribution per result
Every listing surfaced must be tied to the source `id` from sources.yaml. When you write the listing in step 5, set `source:` to the exact registry ID (e.g. `source: linkedin_authenticated`). That's what `scripts/source_yield.py` keys on.

#### f) Curated-list sweeps
When a curated company list surfaces — annual "best / most promising startups" rankings, funding-round roundups, acquisition news, "best places to work" lists — treat the LIST as a company index and cross every company against its careers board:

1. **Article already swept?** Check sources.yaml first — every swept list gets a registry entry with a "do not re-sweep before" note. If the article (or edition) is already registered, stop; don't re-scrape static content.
2. **Extract the full company list** from the article. curl + strip-HTML beats WebFetch on long list articles (WebFetch truncates the body).
3. **Diff against `data/company-index.yaml`** — the persistent per-company triage cache (create it with a schema-comment header on first use). Terminal `skip:*` entries are never re-probed — this includes `skip:rejected` company-level closures, so closed companies cost zero even when they headline a list. `yield`/`no_fit` entries reuse their saved `ats` ref (hit the board API directly, no board re-discovery) and are only re-probed if `last_checked` > 30d; `recheck` entries are probed every sweep. Only genuinely net-new companies go through full discovery. Append the new list's id to `sources:` on every overlapping entry. **Caution on `skip:rejected`:** a role-level Rejected listing in the tree does NOT close a company (large companies have rejected reqs and live tracked roles) — only add it for companies in `{rejected_companies}` or when the user declares the company closed; when they do (in any conversation), append the entry to the index then and there.
4. **Intake triage of net-new companies before any careers lookup**: drop companies in `{excluded_domains}` and apply the `{rejected_companies}` / `{company_blocklist}` rules. Record skip reasons in the run report.
5. **Fan out parallel subagents** in batches of ~6 companies to find each careers board and enumerate roles. Include founder names in each batch prompt — generic company names only disambiguate via founders.
6. **ATS JSON APIs first** (see step 3b), Playwright only as fallback.
7. Filter to `{locations}` + `{seniority}` + `{interests}`, dedupe vs the tree, write listings with `source:` set to the list's registry id.
8. **Update company-index.yaml**: append-or-update one entry per company touched this sweep (normalized lowercase name, `ats` ref in `comeet:<slug>` / `ashby:<slug>` / `greenhouse:<slug>` / `lever:<slug>` / `site:<url>` form, status `yield | no_fit | skip:<reason> | unreachable | recheck`, `last_checked: {today}`, source ids).
9. **Register the list itself** in sources.yaml: one-off/annual lists get `type: company_index` with an explicit "do not re-sweep before <next edition>" note (static content must not accrue consecutive_zeroes); recurring feeds (funding/M&A roundups) go through normal probation.

This pattern is the highest-yield way to convert a single press article into dozens of triaged boards — active careers-cross-referencing of a named company list, distinct from passively monitoring a news front page (dup-heavy, low yield).

### 4. Deduplicate

For each found opportunity, check against `existing` by **(company.lower(), role.lower())** with a similarity tolerance (e.g. "Senior ML Engineer" matches "Senior Machine Learning Engineer"). A listing is a duplicate if:
- Same company AND same/very similar role title
- An aggregator listing and a canonical company-careers listing for the same role are duplicates — keep the canonical one. If the existing entry uses an aggregator URL but the canonical is now resolvable, update the existing entry's `url` to canonical rather than creating a new entry.

Track which results came from which source for the stats log.

### 5. Add new entries

For each genuinely new listing, write `data/listings/<company-slug>-<role-slug>.md`:

```yaml
---
title: <Company> — <Role>
company: <Company>
role: <Role>
status: To Apply
url: <canonical company careers URL>
location: <city — from or near {locations}>
level: <Senior | Staff | Principal | Lead — per {seniority}>
type: <short role-family tag, e.g. Backend | ML Engineer | Platform | SRE>
first_added: YYYY-MM-DD
source: <registry id from sources.yaml>
---

Brief context about the role and why it's a good fit.

## Communications

| Date | Channel | Direction | Contact | Summary |
|------|---------|-----------|---------|---------|
```

**Multi-location postings**: if the source lists more than one office for the same role (e.g. "Tel Aviv or Netanya"), record all of them in `location:` comma-separated (`Tel Aviv, Netanya`) — don't collapse to a single city. Step 3's `{location_blocklist}` filter only drops a listing when *every* comma-separated location is blocked, so a role stays visible as long as at least one offered office isn't on the blocklist.

The empty `## Communications` table is seeded on creation; future Gmail/WhatsApp scans append rows here rather than writing free-form body notes. Conventions:
- **Date**: `YYYY-MM-DD` (anchored to step 0 `{today}` for events processed in this run; use the actual event date for past events being backfilled).
- **Channel**: `Gmail` · `WhatsApp` · `LinkedIn` · `Phone` · `Zoom` · `In-person` · `Comeet` · `Greenhouse` · `Workday` · `Lever` · etc.
- **Direction**: `` `<--` `` inbound (recruiter→user) · `` `-->` `` outbound (user→recruiter) · `` `meet` `` scheduled interview/event.
- **Contact**: `Name (Role at Company)`. Include the contact handle inline when distinct, e.g. `Jane Roe (Recruiter, +4930…)`.
- **Summary**: one-line action. For scheduled meets, include exact date+time+timezone+meeting URL inline.

**Canonical URL rule**: `url` must be the canonical company careers URL (e.g. `careers.<company>.com`, `jobs.<company>.com`, the company's Greenhouse/Lever/Workday/Comeet board). Never save a LinkedIn, Glassdoor, or other aggregator URL. If you discovered the role on an aggregator, resolve the canonical URL before saving — search `site:careers.<company>.com "<role title>"` (or the company's known ATS), and follow the aggregator's "apply" button if needed. If no canonical URL can be found after a real attempt, save the aggregator link with body note `[link: aggregator only — no canonical found]`.

### 6. Scan Gmail for status updates

**Gate:** skip this step (and note it in `sources_skipped`) if no Gmail MCP is connected or `{gmail_enabled}` is false. Nudge once with how to connect. Tool names vary by Gmail integration — use whatever thread-search/thread-read tools the connected integration exposes; the intent below is what matters.

**Do NOT use `from:` filters.** They miss real applications whenever a sender domain isn't in a hardcoded list (companies routinely send from lookalike domains — e.g. a `*.example-mail.io` regional subdomain instead of `example.com`; ATS mail comes from `*-notifications.com` domains).

**Instead: pull ALL inbox emails in the window and classify each one.**

#### a) Pull the inbox window
- Default window: `{gmail_window}` days, or since the previous run's date if known. Use `newer_than:{gmail_window}d in:inbox -in:trash -in:spam`.
- Page through results until exhausted. Don't stop at the first page if there are more.
- Also pull the sent folder for outbound: `newer_than:{gmail_window}d in:sent` — outbound replies to recruiters reveal active threads.

#### b) Classify each thread by snippet/subject/sender
For each thread (work from snippet + subject + sender — do NOT read the full thread until decided it's job-related), classify as job-related if ANY of:

1. **Sender domain matches a tracked company** (resolve from the listings' `company` frontmatter on each run; never hardcode). Match the *root* domain.
2. **Sender is a known ATS / recruiting platform**: `greenhouse.io`, `greenhouse-mail.io` (any regional subdomain), `lever.co`, `comeet.com`, `comeet-notifications.com`, `ashbyhq.com`, `workday.com`, `workdayjobs.com`, `smartrecruiters.com`, `bamboohr.com`, `eightfold.ai`, `myworkdayjobs.com`, `taleo.net`, `successfactors.com`, `icims.com`, `jobvite.com`, `recruitee.com`, `breezy.hr`, `teamtailor.com`.
3. **Sender pattern matches a generic recruiting alias**: local-part contains `careers`, `jobs`, `recruiting`, `recruiter`, `talent`, `hr`, `hiring`, `noreply-jobs`, `no-reply` paired with a company-looking domain.
4. **Subject contains application/process keywords (case-insensitive)**: `apply`, `applied`, `application`, `we received`, `we got it`, `thank you for your interest`, `thanks for applying`, `interview`, `schedule`, `phone screen`, `screening call`, `intro`, `tech screen`, `assessment`, `take home`, `coding challenge`, `next step`, `next round`, `move forward`, `unfortunately`, `regret to inform`, `not moving forward`, `not proceed`, `other candidates`, `not selected`, `offer`, `welcome aboard`, `we are pleased`.
5. **Snippet text suggests it**: "your application", "the role", "the position", "join our team", "recruiter at", "talent acquisition", "your profile caught our attention", "open role".
6. **Outbound thread** (in:sent) where the recipient domain matches rules 1–3.

When uncertain, treat it as job-related and process.

#### c) Process each job-related thread
1. Read the full thread.
2. Determine the status update: Applied, Screen, Interviewing, Offer, Rejected, Skipped.
3. Identify the company and role title from the email content.
4. Match to a listing file by company + role similarity (slug match on `<company-slug>-<role-slug>.md`).
5. If the listing isn't in the tree, create a new file (step 5 schema).
6. Edit the file: update `status` frontmatter, add `applied_date` / `last_update` as appropriate, and **append a row to the `## Communications` table** (see step 5 conventions). If the table doesn't exist yet, add the section header + table header first. Use the **anchored {today}** from step 0 for the row's Date when processing the event in this run, OR the actual event date when backfilling historical events from a thread.

**Recording interview/event dates — read carefully, do not guess:**
- **Distinguish a prep/coordination/intro call from the actual interview stage.** A short slot (e.g. 20–30 min) titled "prep", "initial prep", "intro", "availability", or sent by a *coordinator* is NOT the technical interview — it's logistics. Record it as such; do not log it as the technical interview. The real interview is usually a separate, longer slot scheduled afterward (often by a different coordinator).
- **Quote the exact event date+time with year** straight from the confirmation email (e.g. `Mon 2026-06-01 1–2pm CEST`). Never paraphrase a calendar event into "scheduled {today}".
- **Sanity-check every event date against the anchored {today}.** If an event date is in the past relative to {today}, it already happened — write it as completed, not upcoming. If it's in the future, write it as scheduled. A calendar invite for "this Monday" means nothing without resolving it to an absolute date and comparing to {today}.
- When a thread shows multiple stages (e.g. recruiter screen → prep call → design interview → coding interview), record the **latest confirmed state** plus the concrete upcoming date(s), not just the first event you read.

#### d) Sanity check
If you processed 0 emails but the inbox window had > 0 threads matching ATS sender patterns, you missed the scan — re-run.

### 7. Scan WhatsApp for recruiter messages

**Gate:** decided in step 0a (config + file-existence check, plus a live `whatsapp_connection_status` check with a start/use-stale/skip prompt if the bridge is set up but not currently running). If step 0a's answer was "skip," skip this step — it's already recorded in `sources_skipped`. Otherwise proceed (the scan works the same whether the bridge is live or the data is a stale snapshot — only freshness differs).

Messages live in `{wa_chat_db}` (SQLite). Contact names in `{wa_contacts_db}:whatsmeow_contacts`.

#### a) Sweep DB for recruiter-keyword chats

Bias toward **low-volume chats** (≤`{wa_max_chat}` total messages) — high-volume chats are almost always friends discussing their own jobs, not recruiters.

Build the keyword clause at runtime from `{wa_keywords}` — one `LIKE '%<kw>%'` term per keyword across all language sets in `{languages}` (use `LOWER(m.content)` for Latin-script keywords, raw `m.content` for non-Latin scripts):

```sql
ATTACH '{wa_contacts_db}' AS wa;

WITH chat_counts AS (
  SELECT chat_jid, COUNT(*) AS total FROM messages GROUP BY chat_jid
),
hits AS (
  SELECT m.chat_jid, COUNT(*) AS hits, MAX(m.timestamp) AS last_msg
  FROM messages m
  WHERE m.is_from_me = 0
    AND m.timestamp > date('now', '-180 days')
    AND (
       LOWER(m.content) LIKE '%recruit%'
    OR LOWER(m.content) LIKE '%hiring%'
    -- ... one LIKE term per configured keyword, all languages in {languages} ...
    )
  GROUP BY m.chat_jid
)
SELECT
  COALESCE(NULLIF(wc.full_name,''), wc.push_name, wc.first_name, 'Unknown') AS name,
  h.chat_jid, h.hits, cc.total AS chat_total, h.last_msg
FROM hits h
LEFT JOIN chat_counts cc ON cc.chat_jid = h.chat_jid
LEFT JOIN wa.whatsmeow_contacts wc ON wc.their_jid = h.chat_jid
WHERE cc.total <= {wa_max_chat}
ORDER BY h.last_msg DESC;
```

#### b) For each candidate chat, pull recent messages

```sql
SELECT timestamp, CASE is_from_me WHEN 1 THEN 'me' ELSE '<--' END, substr(content, 1, 300)
FROM messages
WHERE chat_jid = ?
ORDER BY timestamp DESC LIMIT 20;
```

Read the recruiter's inbound messages for: **company name**, **role title**, **candidate stage signals** (`"received your CV"` → Applied; `"like to schedule a call"` → Screen; `"interview"` → Interviewing; `"not moving forward"` → Rejected). The user's outbound replies often disambiguate company.

Filter out false positives — friends discussing their own interviews show up loud. Cues: chat history well above `{wa_max_chat}` messages, casual tone, group chats of known friends.

#### c) Sync findings to the listings tree

For each real recruiter conversation:
1. **Match company + role** to an existing listing file (slug match on the filename).
2. **If matched** — update `status` frontmatter, **append a row to the `## Communications` table** with `Channel: WhatsApp`, `Contact: {name} ({role}, {jid})`, and the appropriate Direction (see step 5 conventions). Add the section + table header if missing.
3. **If new** — create a listing file (step 5 schema). Set `source: whatsapp_db`. Body should record: recruiter name, WhatsApp JID, and any company context the recruiter volunteered.

#### d) Track WhatsApp-derived updates separately for the run report.

### 7a. Calendar sync — every scheduled interview must be in the calendar

**Gate:** skip (and note in `sources_skipped`) unless `{cal_enabled}` is true AND a Google Calendar MCP is available.

After the Gmail (step 6) and WhatsApp (step 7) scans, make sure every upcoming interview/screen has a calendar event.

1. **Collect scheduled events**: every `meet` row across the listings tree whose date+time is in the future relative to `{today}` (in practice: listings in Screen/Interviewing touched this run, plus any `meet` rows added in steps 6–7).
2. **Load the Calendar tools** in ONE ToolSearch call (list/create/update event).
3. **Diff against the calendar**: list events on the primary calendar over `[{today}, {today}+14d]`. Match by company/interviewer keyword in the summary AND start time (±15 min). Known traps:
   - **Gmail "Invitation from an unknown sender"** — Google does NOT auto-add invites from first-time senders. The invite email existing ≠ the event existing. Verify via list_events, never assume.
   - ATS bots (Paradox/Workday) sometimes DO auto-create events from their emails — dedupe before creating, don't double-book.
   - A canceled-and-rescheduled screen may leave a stale event at the OLD time — check the old slot and flag/remove it.
4. **Create what's missing**:
   - Exact date/time from the confirmation email (never paraphrased), `timeZone: {timezone}`. Duration: as stated in the invite; default 30 min for phone screens, 60 min for interviews.
   - Description (plain text with real newlines — do NOT escape HTML entities like `&lt;br&gt;`; they render as literal text): meeting URL + password/PIN + dial-in, interviewer/recruiter name+role, req ID, and a `[Created by job-search calendar sync, run-N]` provenance line. Put the meeting URL in `location` too.
   - **NEVER add external attendees** — creating an event with attendees emails them an invite. Create self-only blocks; the organizer's real invite stays theirs.
5. **Conflicts**: if two events overlap, still create both, prefix each summary with `⚠️ CONFLICT w/ <other> — `, cross-reference in both descriptions, and surface the conflict prominently in the run report AND the final user summary.
6. **Record** the number of events created as `calendar_synced` in the run-report frontmatter (0 when the step is gated off).

### 7b. Update sources.yaml and mark stale

Two scripted maintenance steps before the run report:

1. **Update `data/sources.yaml`** with this run's results:
   - For every source swept: bump `last_tried` to `{today}`, set `last_yield` to the count of listings added from that source this run, set `last_status` to `success` | `empty` | `unreachable` per step 3c, increment `cumulative_yield`, increment `runs_active`. If `last_yield >= 1`, also set `last_success: {today}`.
   - `consecutive_zeroes` update rule (informational): if `last_yield >= 1`, reset to 0. Else if `last_status == empty`, increment by 1. Else if `last_status == unreachable`, leave unchanged.
   - **Maintain `proven:`** from listings attribution: if any listing with `source: <id>` has an `applied_date` → at least `proven: application`; if any such listing reached Screen/Interviewing (ever, not just currently) → `proven: loop`. Never downgrade `proven`.
   - **Backfill `last_success`** for any tier-2 entry missing it: the latest `first_added` among listings attributed to that source; if none, its `seeded`/first-sweep date.
   - For each new candidate discovered in step 3d: append to the `probation` list with `seeded: {today}`, `url`, and one-line `rationale`.
   - **Apply demotion rules (days-based + yield-weighted — see the registry header):**
     - `dry_days` = `{today}` − `last_success` (never-successful: − first sweep date).
     - `tier: 2` and `dry_days >= 14` and swept ≥3 times in that window → suggest moving to `dropped` in the run report (don't auto-move; flag for review).
     - `proven: application` → threshold is `dry_days >= 28` instead of 14.
     - `proven: loop` or `tier: 1` → **never** suggest demotion on dryness.
     - One-off/annual list sources never accrue dryness — they retire on their own "do not re-sweep before next edition" note.
     - `unreachable_streak >= 3` → move to `parked` with `retry_after: {today}+30d`; never `dropped` for reachability.
   - Apply probation outcome rules from step 3c (second zero-yield test must be ≥7 days after the first).

2. **Auto-stale sweep**: `python3 scripts/mark_stale.py`. Demotes any Applied/Screen/Interviewing listing inactive for >`{stale_days}` days to `status: Stale`. Run before the chart so Interviewing reflects real momentum, not ghosts.

### 8. Write the run report

Create `data/runs/run-N.md` (next run number from step 2):

```yaml
---
title: Run #N
date: YYYY-MM-DD
version: bare-v1.0
existing: <count of listings before this run>
new_added: <count of new listings added>
dupes_skipped: <count of duplicates found and skipped>
sources_used: [linkedin_authenticated, gmail_inbox, ...]      # IDs from sources.yaml
sources_skipped: [whatsapp_db, ...]                           # gated off / integration missing (with nudge shown)
sources_promoted: [...]                                       # probation → tier-2 this run
sources_demoted: [...]                                        # tier-2 → probation/dropped this run
sources_unreachable: [...]                                    # 0-yield due to tooling (NOT counted toward cz)
new_probation: [...]                                          # discovered this run
new_companies: [...]
auto_stale: <count of listings auto-demoted to Stale>
calendar_synced: <count of calendar events created in step 7a; 0 if gated off>
triage_config_updates: [...]                                  # config.yaml changes applied from step 0b; [] if none
config_bound: "<one-line echo of the key bound values: role_focus | locations | # keywords>"
---

Brief summary of what changed vs. prior run. Include a per-source yield table generated by `python3 scripts/source_yield.py --since {prev_run_date}`.
```

### 9. Generate evaluation chart

```bash
python3 scripts/generate_chart.py
```

Two-panel chart — left: per-run new-listings delta bars + cumulative line; right: status-breakdown pie (To Apply / Applied / Screen / Interviewing / Offer / Stale / Rejected / Skipped; **Stale** is its own status — it separates the work pile from genuinely active loops). Saved to `data/reports/chart-latest.png`; display it to the user.

### 9b. Generate per-run HTML report

After the chart is saved, run the report generator:

```bash
python3 scripts/generate_run_report.py
```

This embeds the chart (base64-inline PNG), renders the run-N.md prose, and appends a live "Active processes" + "Applied" pipeline snapshot derived from each listing's `## Communications` table. Output: `data/reports/run-N.html` per substantive run. The HTML is self-contained — safe to share or open offline. Mention the new report path in your final summary.

### 10. Report results
Summarize:
- How many existing listings were found
- How many new listings were added (with details)
- How many duplicates were skipped
- Any Gmail status updates synced
- Any WhatsApp status updates synced
- Any new companies discovered
- **Per-source yield this run** (from `scripts/source_yield.py --since {prev_run_date}`) — which sources earned their seat, which are on probation
- **Probation outcomes** — what was smoke-tested, what was promoted, what was dropped
- **New probation entries** — net-new candidates seeded for next run
- **Auto-stale count** — how many listings moved to Stale
- **Triage config updates** — any `company_blocklist`/`anti_interests` changes applied this run from step 0b's repeat-decline questions, asked and applied *before* the scan ran (so this run's results already reflect them). Mention `/triage` if the `To Apply` pile is large and hasn't been reviewed recently.
- **Calendar sync** — events created in step 7a, and any ⚠️ scheduling conflicts (conflicts must appear in the final user summary, not just the run file)
- **Integrations skipped this run** — one line each with the concrete enable command/pointer (Playwright install, Gmail connector, `docs/whatsapp-setup.md`, calendar). This is the recurring nudge — keep it short, never blocking.
- **Delta vs. last run**: change in new-added count, new sources that worked, hit rate comparison
