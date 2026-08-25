---
name: network-scan
description: Resolve your LinkedIn contacts' current employers to career-page URLs, check those pages for matching open roles, and flag "warm contact here" on any listing (new or existing) at a company where you know someone. Meant to run occasionally (e.g. monthly) — use when the user asks to scan their network, check contacts' companies for openings, or find warm-intro opportunities. NOT part of the regular /job-search sweep.
---

# Network Scan — Warm-Contact Company Sweep

<!-- version: bare-v1.0 -->

Resolve the companies where your LinkedIn contacts currently work to direct career-page URLs, check those pages for roles matching your configured profile, and mark matching listings — new or already-tracked — with `warm_contact: true` so you know where a warm intro is possible before you apply cold.

**Not a substitute for `/job-search`.** This is a separate, occasional command (monthly is a reasonable cadence) because your contacts' current employers don't change often enough to justify re-resolving them on every sweep. Careers-page resolution is cached in `data/company-careers.yaml` with a freshness window (default 30 days — see step 0a) precisely so repeat runs are cheap.

**Companion skills:** roles this scan finds land in `data/listings/` exactly like `/job-search` output — run `/score-listings` and `/apply` on them the same way. Existing listings that get retroactively flagged `warm_contact: true` (step 6) show up in the next `/score-listings` HTML report with a badge.

All personal specifics (contacts CSV path, cache freshness, profile) live in `./config.yaml` — nothing user-specific is hard-coded here.

## Steps

### 0. Anchor to today's date

Same rule as `/job-search` step 0: establish today's actual date from the environment context (`currentDate` in the system context, or `date +%F`) and use it as `{today}` everywhere below — for `last_checked` cache stamps, `first_added` on new listings, and freshness-window math. Never infer it from a file timestamp.

### 0a. Load config + preflight

1. **Read `./config.yaml`.** If missing, STOP: `cp config.yaml.example config.yaml`, fill it in (same as `/job-search`), then re-run.

2. **Require `search.linkedin_contacts_csv_path`.** If unset or the file doesn't exist, STOP and tell the user:
   > No LinkedIn contacts export found. See `docs/linkedin-contacts-export-guide.md` for how to download your Connections.csv from LinkedIn, then set `search.linkedin_contacts_csv_path` in `config.yaml` to its location and re-run `/network-scan`.

3. **Bind these variables** from config, same names as `/job-search` where they overlap so config semantics stay consistent across skills:
   - `{role_focus}`, `{interests}`, `{anti_interests}`, `{seniority}`, `{locations}`, `{languages}` ← `profile.*`
   - `{cv_path}` ← `applicant.cv_path` (read the CV if present — gives the role-matching pass real signal beyond `{role_focus}`; proceed without it if unset, same graceful-degrade rule `/job-search` uses)
   - `{company_blocklist}`, `{rejected_companies}`, `{agency_blocklist}` ← `search.*` (skip resolving these companies at all — no point finding a warm contact at a company you've already blocklisted or been rejected by)
   - `{contacts_csv_path}` ← `search.linkedin_contacts_csv_path`
   - `{freshness_days}` ← `search.network_scan.cache_freshness_days`, default **30** if unset. (Chosen to match this skill's intended monthly cadence — a 7-day window, like the reference implementation this pattern is modeled on uses for its weekly-run design, would make every monthly run treat the *entire* cache as stale and re-resolve everything, defeating the point of caching.)
   - `{default_contact_limit}` ← `search.network_scan.default_contact_limit`, default **50** if unset.

4. **Parse `$ARGUMENTS`** for the contact scope:
   - A number (e.g. `100`) → use as the contact limit for this run.
   - `all` → use every contact in the export. Warn the user first if the export has 200+ rows ("this may take a while / spend a lot of WebSearch + browser time").
   - Empty/missing → use `{default_contact_limit}`.

5. **Probe Playwright MCP** (`mcp__playwright__*` tools via ToolSearch) — needed for step 3. If unavailable, nudge: `claude mcp add playwright -- npx -y @playwright/mcp@latest`, then continue with step 2 anyway (careers-page *resolution* only needs WebSearch) and skip step 3, reporting it under a `sources_skipped`-style note at the end.

6. **Ensure the data tree exists:** `mkdir -p data/listings`. `data/company-careers.yaml` is created fresh in step 2 if it doesn't exist yet.

### 1. Load contacts and extract employer companies

Read `{contacts_csv_path}`. **LinkedIn's export has a preamble** — one or more `Notes:`-style lines before the real header row. Locate the header row (starts with `First Name,Last Name,...,Company,Position,Connected On`) rather than assuming line 1 is the header:

```python
import csv, io, pathlib
raw = pathlib.Path(contacts_csv_path).expanduser().read_text()
lines = raw.splitlines()
header_idx = next(i for i, l in enumerate(lines) if l.startswith('First Name,Last Name'))
rows = list(csv.DictReader(io.StringIO('\n'.join(lines[header_idx:]))))
```

1. **Sort by `Connected On` descending** (most recently connected first) and take the first N per the bound contact limit (or all, if `all`).
2. **Extract `Company`** per selected contact. Skip blank values. Use judgment to skip obvious non-employers (freelance/self-employed status, a recruiter's personal brand name, a school) — when uncertain, keep it; a wasted resolution attempt is cheap, a missed warm contact is not.
3. **Drop companies in `{company_blocklist}` / `{rejected_companies}` / `{agency_blocklist}`** — no point resolving or scanning these.
4. **Group into a lookup** keyed by company name:
   ```json
   {"Google": [{"name": "Jane Smith", "position": "PM Director", "url": "https://linkedin.com/in/janesmith"}], ...}
   ```
5. Report: "Found X unique companies from Y contacts (of Z total in the export). Checking careers pages..."

### 2. Resolve careers pages — WebSearch only, no browser

Load `data/company-careers.yaml` if it exists (see schema below); start with an empty registry if not.

Split companies from step 1 into:
- **Cached, fresh** — `last_checked` within `{freshness_days}` — reuse as-is.
- **Cached, stale** — `last_checked` older than `{freshness_days}` — re-resolve.
- **Uncached** — not in the registry, or `ignored: true` (skip these entirely — see step 8 feedback loop).

Report: "X companies from cache (fresh), Y need resolution..."

**Batch and parallelize.** Split companies needing resolution into batches of ~10. Spawn one `general-purpose` subagent per batch (via the Agent tool), all in parallel. Give each subagent:

> You are resolving careers-page URLs for a batch of companies. For each company: use `WebSearch` (do NOT use browser automation — this step is search-only, for speed) with a query like `"<Company>" careers jobs`. From the results, identify the official careers/jobs page and classify it:
> - `direct` — the company's own domain (careers.company.com, company.com/careers or /jobs)
> - `greenhouse` — `boards.greenhouse.io/<slug>` or `job-boards.greenhouse.io/<slug>` or `<slug>.greenhouse.io`
> - `lever` — `jobs.lever.co/<slug>`
> - `workday` — `*.myworkdayjobs.com`
> - `other_ats` — Ashby (`jobs.ashbyhq.com`), Comeet, BambooHR, SmartRecruiters, etc.
> - `not_found` — no careers page found after a real search attempt (`careers_url: null`)
>
> For well-known companies, use existing knowledge to skip redundant searches when confident. Don't navigate to or verify URLs — extract from search results only. Process the whole batch even if some entries fail. Return one JSON object keyed by company name: `{"<Company>": {"careers_url": "...", "type": "direct|greenhouse|lever|workday|other_ats|not_found"}}`.

Merge every batch's results into the registry (see schema below), set `last_checked: {today}` on every company just resolved, and save `data/company-careers.yaml`.

Report: "Resolved X new careers pages, Y from cache, Z not found."

**`data/company-careers.yaml` schema** (YAML, matching this repo's `data/sources.yaml` / `data/company-index.yaml` convention of a documented registry file rather than an opaque JSON blob):

```yaml
# Warm-contact company careers cache, built and refreshed by /network-scan.
# Maps each contact's employer to a resolved careers-page URL + ATS type.
#
# Freshness window: search.network_scan.cache_freshness_days in config.yaml
# (default 30 — this skill is meant to run ~monthly; contacts' employers
# don't change often enough to justify re-resolving on a shorter cycle).
# An entry older than the window is re-resolved on the next /network-scan run.
#
# type: direct | greenhouse | lever | workday | other_ats | not_found
# ignored: true — set after the user says "skip <company>"; future runs
#   don't re-resolve or re-scan it (see SKILL.md step 8).

last_updated: 2026-08-08

companies:
  Google:
    careers_url: https://careers.google.com
    type: direct
    last_checked: 2026-08-08
    last_found_roles: 0
    contacts:
      - name: Jane Smith
        position: PM Director
        linkedin: https://linkedin.com/in/janesmith
  Small Startup LLC:
    careers_url: null
    type: not_found
    last_checked: 2026-08-08
    last_found_roles: 0
    contacts:
      - name: John Doe
        position: Eng Manager
        linkedin: https://linkedin.com/in/johndoe
```

Always overwrite the `contacts:` list for a company with this run's selection (contacts you know there may change as your network grows) — the URL/type/history fields are what's cached and reused; the contact list just reflects "who do I currently know here."

### 3. Scan resolved careers pages for matching roles — Playwright

**Gate:** skip this step (note in the final report) if Playwright MCP isn't available per step 0a's probe.

Take every company with a non-null `careers_url` and not `ignored: true`. Split into batches of ~5. Spawn one `general-purpose` subagent per batch (up to 5 concurrent, to avoid overwhelming the browser), each with its own tab (`tabs_context_mcp` then `tabs_create_mcp` from the Playwright MCP tools). Give each subagent:

> For each company in your batch (name, careers_url, ATS type, network contacts):
> 1. **Try the direct ATS JSON API first**, same as `/job-search`'s company-board sweep — it's faster and more reliable than driving the UI when the board slug is recoverable from the careers_url:
>    - Greenhouse: `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true`
>    - Lever: `https://api.lever.co/v0/postings/<slug>?mode=json`
>    - Ashby: `https://api.ashbyhq.com/posting-api/job-board/<slug>`
>    Fall back to Playwright navigation if the API call fails, the type is `direct`/`workday`/`other_ats`, or no slug can be extracted.
> 2. **For a Playwright visit**: navigate to `careers_url`. Use the page's own search/filter (Greenhouse: search box or department filter; Lever: search bar or team filter; Workday: keyword search field; direct/other: browse or use any on-page search).
> 3. **Search using `{role_focus}` + `{interests}` keywords.** Try a couple of keyword variations if the first returns nothing.
> 4. **Extract listings**: title, location, url, department (if shown).
> 5. **Filter to fit**: role title/domain matches `{interests}` and not `{anti_interests}`; seniority in `{seniority}`; location matches `{locations}` (respect `profile.accept_remote_without_location_match` the same way `/job-search` does — drop remote-without-location-match roles unless it's true).
> 6. Return, per company: `{"company", "careers_url", "total_roles_seen", "matching_roles": [{"title", "location", "url", "department", "notes"}]}`. Empty `matching_roles` is a valid result — it still updates the cache's role count.
>
> Budget ~1-2 minutes per company. If a page fails to load or is behind auth, return `{"error": "page failed to load"}` for that company and move on — don't get stuck. Don't click Apply buttons.

Collect all results. Update `last_found_roles` and `last_checked` in `data/company-careers.yaml` for every company scanned (even zero-result ones — that's still fresh information).

### 4. Deduplicate against the existing listings tree

Build the **same dedup index `/job-search` step 1 builds** — every existing listing's `(company.lower(), role.lower())` from frontmatter, with the same fuzzy-title tolerance (e.g. "Senior ML Engineer" ~ "Senior Machine Learning Engineer") and the same "aggregator vs. canonical URL" collapse rule from `/job-search` step 4:

```python
import yaml, pathlib
existing = {}
for p in pathlib.Path('data/listings').glob('*.md'):
    text = p.read_text()
    fm_end = text.find('\n---', 4)
    fm = yaml.safe_load(text[4:fm_end])
    existing[(fm['company'].lower(), fm['role'].lower())] = p
```

For every matching role found in step 3: if `(company.lower(), role.lower())` already exists (exact or fuzzy match), it's a dup — no new file (its `warm_contact` flag gets set in step 6 instead, since the company is now a resolved contact company regardless). Otherwise it's genuinely new — goes to step 5.

### 5. Write new listings

For each genuinely new matching role, write `data/listings/<company-slug>-<role-slug>.md` using **the exact same frontmatter schema `/job-search` step 5 uses**, plus two warm-contact fields:

```yaml
---
title: <Company> — <Role>
company: <Company>
role: <Role>
status: To Apply
url: <canonical careers/ATS URL for the posting>
location: <city — from or near {locations}>
level: <Senior | Staff | Principal | Lead — per {seniority}>
type: <short role-family tag>
first_added: {today}
source: network_scan
warm_contact: true
contact_name: "<Name> (<Position>)"
---

Found via network scan — <Name> (<Position>) currently works at <Company>. <Brief context about the role.>

## Communications

| Date | Channel | Direction | Contact | Summary |
|------|---------|-----------|---------|---------|
```

If multiple contacts exist at the same company, join them in `contact_name` with `; ` (e.g. `"Jane Smith (PM Director); John Doe (Eng Manager)"`). Same canonical-URL rule as `/job-search`: never save an aggregator link when the direct posting URL is resolvable.

### 6. Flag warm_contact on existing listings at resolved companies

This is the retroactive half — a listing already in `data/listings/` (found by `/job-search`, long before you ran this scan) at a company where you *also* turn out to know someone should get flagged too, not just net-new roles from step 3.

Build a JSON array from every company in step 1's contact lookup (regardless of whether a careers page or matching role was found — "I know someone there" doesn't require that):

```json
[{"company": "Google", "contacts": ["Jane Smith (PM Director)"]}, ...]
```

Run:
```bash
python3 scripts/write_warm_contacts.py <<'JSON'
[{"company": "...", "contacts": ["..."]}, ...]
JSON
```

This surgically upserts `warm_contact: true` and `contact_name` into every existing listing whose `company` frontmatter case-insensitively matches (never rewrites the whole file — same surgical-upsert convention as `scripts/write_match_scores.py`). Report how many existing listings got flagged this run.

### 7. Save the cache

Confirm `data/company-careers.yaml` is written with `last_updated: {today}` and every company touched this run has current `last_checked` / `last_found_roles` / `contacts`.

### 8. Report results + learn from feedback

Present to the user:

```markdown
## Network Scan Results — {today}
Scanned N companies from M contacts (of T total in your export).

### New Matches Found
1. **Senior ML Engineer at Google** — warm contact: Jane Smith (PM Director)
   Location: ... · Apply: <url> · written to data/listings/google-senior-ml-engineer.md

### Existing Listings Now Flagged warm_contact
- Stripe — Staff Backend Engineer (already tracked, now flagged — contact: John Doe)

### Companies Checked, No Matching Roles
Google (12 roles seen, none matching), Stripe (4 roles, none matching), ...

### Companies Without a Resolvable Careers Page
Acme Corp, Small Startup LLC, ...

### Skipped (Playwright unavailable / gated)
[if step 3 was gated off]
```

End with: "Run `/score-listings` and `/apply` next — warm-contact listings carry a `warm_contact` flag you can spot in the match report." (Note in your own final summary to the user, separately from this file's output, whether `data/reports/match-scores-latest.html` already surfaces a `warm_contact` badge — see the note at the top of `scripts/generate_match_report.py` if unsure.)

**Feedback loop** (if the user reacts to results in the same conversation):
- **"Skip `<company>`"** → set `ignored: true` on that company's entry in `data/company-careers.yaml`. Future runs skip resolving and scanning it.
- **User corrects a careers URL** → update that company's `careers_url` / `type` in the cache directly.
- **User adjusts profile fit** (e.g. "also count fintech roles") → that's a `config.yaml` `profile.*` edit, same as any other profile tuning; don't special-case it here.

## What NOT to do

- Don't fold this into `/job-search` — it stays a separate, occasionally-run command.
- Don't use browser automation for careers-page *resolution* (step 2) — WebSearch only, for speed; browser automation is only for step 3's actual role-scanning pass.
- Don't re-resolve a company whose cache entry is still fresh (within `{freshness_days}`) — that defeats the point of the cache.
- Don't overwrite an existing listing's `status`, `Communications` table, or any field besides `warm_contact` / `contact_name` when flagging step 6 — `scripts/write_warm_contacts.py` upserts surgically for exactly this reason.
