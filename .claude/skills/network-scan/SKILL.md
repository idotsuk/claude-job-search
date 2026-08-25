---
name: network-scan
description: Cross-reference your LinkedIn contacts' current employers against the listings /job-search already found, and flag "you know someone here" — with who — on any matching listing. Meant to run occasionally (e.g. monthly) — use when the user asks to scan their network, check for warm contacts, or find who to reach out to before applying. NOT a job-discovery skill — job-search is the only source of new roles.
---

# Network Scan — Warm-Contact Cross-Reference

<!-- version: bare-v2.0 -->

Reads your exported LinkedIn connections, extracts each contact's current employer, and checks whether that company already has a tracked listing in `data/listings/` — one that `/job-search` (or `/network-scan` itself, in an earlier run) found independently. Where it does, flags the listing `warm_contact: true` and `contact_name: "<Name> (<Position>)"` so you know **who to reach out to before applying cold**.

**This skill does not discover jobs.** Finding new roles is `/job-search`'s job, and only `/job-search`'s — it has the source registry, the dedup logic, the canonical-URL resolution, all of it. `/network-scan` never visits a company's careers page, never searches for openings, and never writes a new listing. Its only output is a flag on listings that already exist.

**Not a substitute for `/job-search`.** This is a separate, occasional command (monthly is a reasonable cadence) because your contacts' current employers don't change often enough to justify re-checking on every sweep.

**Companion skills:** run `/job-search` first (it's the one finding roles), then `/network-scan` to see which of those roles you have a contact at, then `/score-listings` and `/apply` as usual — a `warm_contact` badge shows up in the match report.

All personal specifics (contacts CSV path, profile) live in `./config.yaml` — nothing user-specific is hard-coded here.

## Steps

### 0. Anchor to today's date

Same rule as `/job-search` step 0: establish today's actual date from the environment context (`currentDate` in the system context, or `date +%F`) and use it as `{today}` everywhere below. Never infer it from a file timestamp.

### 0a. Load config + preflight

1. **Read `./config.yaml`.** If missing, STOP: `cp config.yaml.example config.yaml`, fill it in (same as `/job-search`), then re-run.
2. **Require `search.linkedin_contacts_csv_path`.** If unset or the file doesn't exist, STOP and tell the user:
   > No LinkedIn contacts export found. See `docs/linkedin-contacts-export-guide.md` for how to download your Connections.csv from LinkedIn, then set `search.linkedin_contacts_csv_path` in `config.yaml` to its location and re-run `/network-scan`.
3. **Bind these variables** from config:
   - `{contacts_csv_path}` ← `search.linkedin_contacts_csv_path`
   - `{company_blocklist}`, `{rejected_companies}`, `{agency_blocklist}` ← `search.*` (no point flagging a warm contact at a company you've already blocklisted or been rejected by — skip resolving these companies at all)
   - `{default_contact_limit}` ← `search.network_scan.default_contact_limit`, default **50** if unset.
4. **Parse `$ARGUMENTS`** for the contact scope:
   - A number (e.g. `100`) → use as the contact limit for this run.
   - `all` → use every contact in the export. Warn the user first if the export has 200+ rows.
   - Empty/missing → use `{default_contact_limit}`.
5. No browser, no MCP, no network access needed for this skill at all — it's pure local file matching (contacts CSV × existing `data/listings/*.md`). Nothing to probe or gate.

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
2. **Extract `Company`** per selected contact. Skip blank values, and use judgment to skip obvious non-employers/non-signal: freelance/self-employed status, a recruiter's or headhunter's personal business (their own name as the "company," or a business whose name/position is plainly a staffing/sourcing/headhunting practice — e.g. "Talent Sourcer," "Global Head Hunter," a recruiting-agency's own name), a school/alumni-association bot account, or military/non-corporate service. When genuinely uncertain whether a company is a real employer, keep it — a wasted comparison is free (this step does no network calls), a missed warm contact is not.
3. **Drop companies in `{company_blocklist}` / `{rejected_companies}` / `{agency_blocklist}`** — no point flagging these.
4. **Group into a lookup** keyed by company name: `{"Google": [{"name": "Jane Smith", "position": "PM Director", "url": "https://linkedin.com/in/janesmith"}], ...}`.
5. Report: "Found X unique companies from Y contacts (of Z total in the export)."

### 2. Cross-reference against the existing listings tree

Build the **same dedup index `/job-search` step 1 builds** — every existing listing's `company` frontmatter, normalized lowercase:

```python
import yaml, pathlib
existing = {}
for p in pathlib.Path('data/listings').glob('*.md'):
    text = p.read_text()
    fm_end = text.find('\n---', 4)
    fm = yaml.safe_load(text[4:fm_end])
    existing.setdefault(fm.get('company', '').strip().lower(), []).append(p.name)
```

For every company in step 1's lookup: check for an exact case-insensitive match against `existing`'s keys, then a light fuzzy match (e.g. `difflib.get_close_matches(..., cutoff=0.9)`) to catch trivial naming variants (e.g. "Acme Inc." vs "Acme"). A company can match multiple listing files (multiple tracked roles at the same employer) — that's expected, flag all of them.

Build a JSON array of every company that matched at least one listing:
```json
[{"company": "Acme", "contacts": ["Jane Smith (PM Director)"]}, ...]
```

### 3. Flag warm_contact on the matching listings

Run:
```bash
python3 scripts/write_warm_contacts.py <<'JSON'
[{"company": "...", "contacts": ["..."]}, ...]
JSON
```

This surgically upserts `warm_contact: true` and `contact_name` into every matching listing's frontmatter (never rewrites the whole file — same surgical-upsert convention as `scripts/write_match_scores.py`). If multiple contacts exist at the same company, they arrive already joined with `; ` in the `contacts` array entry (build that join yourself before calling the script — one string per company, not a list the script joins).

### 4. Report results

```markdown
## Network Scan Results — {today}
Checked N unique companies from M contacts (of T total in your export).

### Warm Contacts Found — reach out before applying
- **Acme — Senior ML Engineer** (`data/listings/acme-senior-ml-engineer.md`) — you know Jane Smith (PM Director) there.
- **Acme — Staff Backend Engineer** — same contact, second tracked role at this company.

### Checked, No Match
X companies from your contacts don't currently have a tracked listing — nothing to flag; this isn't a signal that no roles exist there, only that /job-search hasn't surfaced one yet.
```

End with: "Run `/score-listings` and `/apply` next — warm-contact listings carry a `warm_contact` flag you can spot in the match report, and now you know exactly who to message first."

**Feedback loop** (if the user reacts to results in the same conversation):
- **User adjusts profile fit or blocklists** → that's a `config.yaml` edit, same as any other profile tuning; don't special-case it here.

## What NOT to do

- **Don't visit any company's careers page, don't WebSearch for openings, don't write a new listing.** That is `/job-search`'s job. This skill only flags listings that already exist.
- Don't fold this into `/job-search` — it stays a separate, occasionally-run command, because it's cheap to run and doesn't need the sweep's cadence.
- Don't overwrite an existing listing's `status`, `Communications` table, or any field besides `warm_contact` / `contact_name` — `scripts/write_warm_contacts.py` upserts surgically for exactly this reason.
