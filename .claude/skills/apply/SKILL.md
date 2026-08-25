---
name: apply
description: Work through the job-application "To Apply" queue one role at a time — open each posting via Playwright, prefill standard fields from the user's configured answers, and submit only after explicit user confirmation. Use when the user wants to apply to jobs from their tracked listings.
---

# Apply — Job Application Run

<!-- version: bare-v1.0 -->

Work through "To Apply" entries in `data/listings/`, one at a time, with the user driving each submission. Prefill standard fields from `config.yaml`, leave CV/custom answers for the user, never auto-submit. Optional recruiter WhatsApp follow-ups.

## Core rules (read first)

1. **ALWAYS ask before submitting an application.** After prefilling the form, summarize what's filled and ask "Submit?" — never click Submit without explicit go-ahead.
2. **ALWAYS ask before opening the next role** in the queue. Don't preemptively navigate. One role at a time.
3. **Never click "Submit" or "Apply" buttons that fire the application** until the user says go.
4. Prefilling form fields IS allowed without explicit per-field confirmation — the user reviews before submit.

## Prerequisites

- **`config.yaml`** with a filled `applicant:` block. If missing, STOP and instruct: `cp config.yaml.example config.yaml`, fill the `applicant:` section.
- **Playwright MCP** connected (`mcp__playwright__*` tools available). If not, instruct user: `claude mcp add playwright -- npx -y @playwright/mcp@latest` then restart Claude Code.
- **WhatsApp bridge** (optional) — only if `integrations.whatsapp.enabled` is true and roles have recruiter contacts to follow up with. See `docs/whatsapp-setup.md`.
- **Listings tree**: `data/listings/*.md` (each with frontmatter: `company, role, status, url, location, ...`). Populated by `/job-search`.

## Steps

### 1. Load standard answers

Read `config.yaml` → the `applicant:` block (name, email, phone, LinkedIn, GitHub, city, country, pronouns, CV path, salary expectation, notice period, work setup, work authorization). If `applicant.standard_answers_file` is set, read that file too for freeform supplements (mailing address, current-company wording).

**Verify the CV file at `applicant.cv_path` actually exists** before starting. If missing, ask the user for the current path and update config.yaml. This is the always-required fallback CV; a per-listing tailored PDF (from `/tailor-cv`) may override it for individual roles — see step 4a.

### 2. Pull the "To Apply" queue

```bash
grep -l '^status: To Apply' data/listings/*.md
```

For each match, read frontmatter (`company`, `role`, `url`, `location`, `status`, `keep_intent`) and the body for any role-specific notes.

### 3. Triage + prioritize

**Reconsider-flagged check:** any candidate with `keep_intent: reconsider` (set by `/triage`'s "Reconsider later" action) was deliberately deferred rather than marked ready — present it in the queue with a 🔁 marker and a brief "flagged to reconsider — still want to apply?" instead of ranking it in as a plain Tier A/B/C candidate. Don't skip it automatically; let the user decide, same as the other flagged cases below.

**Company-history check:** for every To Apply candidate, before ranking, look up ALL listings for the same company (`grep -l "^company: <X>" data/listings/*.md`, then their statuses):
- Any `status: Rejected` at the company within ~2 months → recommend Passed for the new req (same recruiter, same ATS profile; a genuinely different level/team may justify an exception — surface it, let the user decide).
- Any `status: Applied/Screen/Interviewing` at the company still unresolved → recommend HOLD (add a hold note in the body, keep To Apply); don't run concurrent applications at one company.
- Companies in config `search.rejected_companies` → recommend Passed.
- Present all such cases in the queue with a ⚠️ marker instead of silently including them.

Rank the To Apply list by fit against the config profile:
- **Tier A (apply first):** target company (`search.target_companies`) + role title matches `profile.interests` + seniority in `profile.seniority`.
- **Tier B:** strong domain fit with `profile.interests`/`profile.role_focus` but not a target company.
- **Tier C (last):** borderline (adjacent domains, small startups, Easy-Apply only).

**If a listing has `match_score`/`match_verdict` frontmatter** (set by `/score-listings`), use it as a secondary sort within each tier — highest `match_score` first — and surface the one-line verdict alongside the fit note, e.g. `"[82] strong domain match — production LLM/agentic experience directly overlaps this role's stack."` Listings without `match_score` keep today's unchanged tier-only ordering — never block or reorder the queue waiting on a score.

**If a listing has `warm_contact: true`/`contact_name` frontmatter** (set by `/network-scan`), surface it inline in the queue too — e.g. `"🤝 warm contact: Gilad Sagi (Associate Account Manager)"` — right alongside the match-score line. Don't use it to reorder tiers (a warm contact doesn't change technical fit), just make it visible so the user can decide to reach out before applying.

Group secondarily by ATS for momentum (Greenhouse → Ashby → Comeet → SmartRecruiters → Workday → Microsoft careers → others). Greenhouse/Ashby are fastest; Workday is slowest (often requires account creation).

Present the ordered list to the user. Ask: "Start with #1 [Company — Role]?"

### 4. Per-role loop

For each role:

#### a) Confirm
"**[Company] — [Role]** · [Location], [hybrid/onsite] · [ATS] · [1-line fit note] · Apply?"

**If `warm_contact: true`/`contact_name` is set on this listing**, lead with it before asking to apply — this is the moment it's actually actionable, not just informational: "🤝 You know **{contact_name}** here (via `/network-scan`) — worth reaching out before applying cold. Apply anyway, or hold off first?" Don't block on this or assume the user has reached out; just make sure they can't miss it at the one point where cold-applying is about to become irreversible.

If user says skip/pass/no → update the listing file's frontmatter: `status: Passed`, append a note in the body explaining why. Move to next.

If user says yes → open canonical URL (the `url` field from frontmatter). Never the LinkedIn aggregator unless that's all that exists.

**Resolve which CV to use for this role**, from the listing's own frontmatter:
- If `cv_status: approved` **and** `cv_output_dir` is set **and** `<cv_output_dir>/cv.pdf` actually exists on disk → use that tailored PDF. This is a listing that's been through `/tailor-cv` and the user has reviewed and approved the draft.
- Otherwise — `cv_status` missing, `cv_status: draft` (generated but not yet reviewed), or the file doesn't actually exist — **silently fall back to `applicant.cv_path`**, exactly as today. Never use a `draft`/unreviewed tailored CV for a real submission; that's an explicit design decision (nothing goes out that the user hasn't seen).

Hold the resolved path as `{cv_path}` for the rest of this role's loop.

#### b) Prefill

For each known ATS pattern, prefill these fields without asking (all values from `applicant.*`):
- First Name / Last Name / Full Name
- Email
- Phone (with country code)
- Country selector (some forms auto-detect from phone)
- LinkedIn Profile
- Website / GitHub
- Location (combobox) → `applicant.city` (or matched city if onsite-specific)
- Visa sponsorship questions → from `applicant.work_authorization`
- Pronouns (if asked and `applicant.pronouns` is set; skip otherwise)
- GDPR / privacy consent → checked / "Confirm"
- Resume upload → use `mcp__playwright__browser_file_upload` with `{cv_path}` (resolved above — the approved tailored PDF if one exists for this listing, otherwise `applicant.cv_path`)

For unique custom fields:
- Salary expectation → `applicant.salary_expectation` (convert monthly/annual as the form requires)
- Notice period → `applicant.notice_period`
- Work setup preference → `applicant.work_setup`
- Work authorization per country → `applicant.work_authorization`
- "Side businesses" / "other obligations" → from the standard-answers file, default No

**Do NOT prefill** "Why this role?" / cover letter / "Tell us about a project" essay fields. Leave those for the user — they need the user's voice.

#### c) Summarize + ask to submit

After prefill, list what's filled (one line per field) and any open custom-essay fields the user must address. Include which CV was used, same reporting style as the other prefilled fields:
- `"Resume: Using tailored CV (reviewed/approved)"` when `{cv_path}` resolved to the tailored PDF, or
- `"Resume: Using default CV (no approved tailored version for this listing)"` when it fell back to `applicant.cv_path`.

Ask: **"Submit?"**

If user says yes → click Submit, verify success (URL changes to /confirmation, or "Application submitted" appears).

If form rejects (validation error), surface the error and ask user what to fill.

#### d) Mark Applied in the listing file

Edit the listing's frontmatter:
- `status: Applied`
- Add `applied_date: YYYY-MM-DD` (today, ISO)
- In the body, append: `_Applied {today} via [ATS name]._`

#### e) WhatsApp recruiter follow-up (if applicable)

**Gate:** only if `integrations.whatsapp.enabled` and the DB at `integrations.whatsapp.chat_db` exists.

Search the WhatsApp DB for any chat mentioning the company or specific role context:

```sql
ATTACH '<integrations.whatsapp.contacts_db>' AS wa;
SELECT COALESCE(NULLIF(wc.full_name,''), wc.push_name, wc.first_name, 'Unknown') AS name,
       m.chat_jid, MAX(m.timestamp) AS last_msg
FROM messages m
LEFT JOIN wa.whatsmeow_contacts wc ON wc.their_jid = m.chat_jid
WHERE LOWER(m.content) LIKE '%<company-lowercase>%'
GROUP BY m.chat_jid
ORDER BY last_msg DESC LIMIT 5;
```

If a recruiter chat exists:
1. Pull last 15 messages to understand context (active interview? prior rejection? ghosted?).
2. Draft a short message **matching the chat's language**. Tone: casual, brief, link the new posting.
3. **Show the draft to the user** with the recruiter's name and ask "Send to [name]?"
4. Only send via the WhatsApp MCP send tool after confirmation.
5. Append the WhatsApp follow-up to the listing file's body.

#### f) Move on

Ask: "**Next: #N [Company — Role]. Apply?**" or summarize remaining queue and let user pick a different next role.

### 5. Session summary

When user signals stop ("done", "that's enough", "stop"), summarize:
- Count of Applied this session
- Count of Passed this session
- Count remaining in queue
- Any WhatsApp follow-ups sent
- Any forms that hit blockers (custom essay fields user needs to revisit)

## ATS-specific notes

- **Greenhouse** (`*.greenhouse.io`, `job-boards.greenhouse.io`): single-page form, country dropdown auto-fills from phone, GDPR checkbox required, often has "Confirm" dropdown for privacy. Fast.
- **Ashby** (`jobs.ashbyhq.com`): single-page, has "Autofill from resume" feature (upload CV at top to auto-populate). Location combobox uses live search — type slowly. Pronouns + visa often required.
- **Comeet** (`comeet.com/jobs/<company>`): simple form, redirect from LinkedIn often lands here. Fast.
- **Workday** (`*.myworkdayjobs.com`): heavy multi-step. Usually requires creating an account first. Slow — budget ~10 min/application. Pre-warn user.
- **SmartRecruiters** (`jobs.smartrecruiters.com/<company>`): single-page, OAuth-friendly.
- **Microsoft careers** (`apply.careers.microsoft.com`): MS account required. Often redirects.
- **Lever** (`jobs.lever.co/<company>`): single-page, similar to Greenhouse.
- **LinkedIn Easy Apply** (no canonical link found): if the listing's `url` is a `linkedin.com/jobs/view/...` URL, the role is Easy-Apply-only. User must apply via LinkedIn directly.
- **Spark Hire** (`Powered by Spark Hire` footer, often embedded on a company's own custom-built careers page — the URL won't look like a dedicated ATS domain): the form frequently sits inside a cross-origin iframe. If using browser tools that only support pixel-coordinate clicks (no direct DOM/accessibility-tree access into the iframe, as with Chrome extension-based automation rather than a dedicated Playwright MCP), field clicks may silently fail to focus the real input, or a click can land on the wrong element after the page reflows between actions (observed: a misfire navigated away to the site's Privacy Notice page instead of filling the Email field). If 2 field-fill attempts don't visibly register (zoom in to confirm — don't trust a screenshot that looks unchanged), stop retrying and hand off to the user to complete manually, per the "avoid rabbit holes" rule. Don't burn more than ~2 attempts on this ATS type specifically.

## What to skip

- Forms requiring work authorization the user doesn't have (check `applicant.work_authorization` — e.g. US-visa-attestation-only forms when sponsorship would be needed).
- Roles at companies with a `status: Rejected` listing or in `search.rejected_companies`.
- Roles already `status: Applied`.

## Don't

- Don't apply to multiple roles at one company in a single session without flagging to the user — some companies cap applications per period.
- Don't fill in "Why do you want to work here?" / "Describe a project" essay fields. These need the user's voice.
- Don't auto-mark Applied without confirmed submit success (URL change or success message).
- Don't send WhatsApp messages without explicit per-message confirmation.
