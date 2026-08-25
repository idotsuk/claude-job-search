---
name: score-listings
description: Score every "To Apply" listing 0-100 against the user's CV for pure technical/domain fit, write the score and a plain-English verdict into each listing's frontmatter, and render a self-contained HTML match report. Use when the user wants to see which tracked job listings best match their background before working the /apply queue.
---

# Score Listings — CV Match Report

<!-- version: bare-v1.0 -->

Compute a 0-100 technical/domain-fit score and a specific plain-English verdict for every `status: To Apply` listing that doesn't have one yet, write both into that listing's own frontmatter, and render a self-contained HTML report with a ring-gauge visualization per listing.

**Companion skills:** run after `/job-search` populates the queue, before `/apply` works it — `/apply` step 3 surfaces `match_score` when present to help order the queue.

Scoring is **pure technical/domain fit only** — skills, domain, tech stack, day-to-day work. Seniority/location/salary are NOT scored here; `/job-search`'s config-driven intake already filtered on those before a listing reached `To Apply`.

A listing with `match_score` already set is always skipped by the default run — scores don't silently go stale-and-recompute on their own. **To force a re-score**, invoke with `rescore` as the argument (see step 0d): `/score-listings rescore` (every scored listing) or `/score-listings rescore <company or role text>` (only listings whose `company`/`role` frontmatter matches, case-insensitive) clears the old score fields first, then runs the normal scoring pass so those listings get freshly scored in the same run.

## Steps

### 0. Preflight

1. **Read `./config.yaml`.** If missing, STOP: `cp config.yaml.example config.yaml`, fill it in, then re-run (same pattern as `/job-search` step 0a and `/apply` step 1).
2. **Verify the CV file at `applicant.cv_path` exists.** If missing or unset, STOP and ask the user for the path (same as `/apply` step 1) — do not proceed without a real CV to score against.
3. **Read the CV** with the `Read` tool (it handles PDF content directly). Hold it in context for the rest of the run — do not re-read it per listing.
4. **Determine career-notes mode from `career_notes.use_interview` in config.yaml:**
   - **`true`** → `data/career-notes.md` is in play. If it already exists, read it and hold it in context alongside the CV — it holds true, sourced accomplishments and background that go beyond the one-page CV, so scoring reflects the candidate's actual full background, not just what fit on one page. Skip the file's own **"Open Questions"** section entirely — those are unresolved questions the user hasn't answered yet, not verified facts, and must not factor into scoring. If the file does **not** exist yet, run **step 0b** below first, then read the file it produces.
   - **`false`** → resume-only mode. Do not read or reference `data/career-notes.md` even if it happens to exist — proceed to step 1 with the CV alone.
   - **unset / missing** → first-time ask. Ask the user directly (one question, `AskUserQuestion` or plain conversation):
     > Score from your resume alone, or spend a few minutes answering some questions so I can build a richer background file (`data/career-notes.md`) that captures things that didn't fit on one page — better matches, especially for adjacent-but-not-identical roles?
     Options: **"Resume only"** (fast, nothing further needed) / **"Answer a few questions"** (one-time setup, better matching from here on).
     Write the boolean choice into `config.yaml` under `career_notes.use_interview` — a surgical edit (add/update just that one line under the `career_notes:` block; don't touch anything else in the file) — then continue per the `true`/`false` branch above. This is asked once; every later run reads the saved value and skips straight to the right branch.

### 0b. Career-notes interview (only when triggered by step 0.4 above)

Runs exactly once per user — the moment `career_notes.use_interview: true` is set and `data/career-notes.md` doesn't exist yet. Every later run finds the file already there, so this step is skipped from then on (nothing here re-runs automatically; a user who wants to redo or extend it can delete/edit `data/career-notes.md` directly).

1. **Scan the CV for what's thin, not what's missing outright** — the goal is spotting places where more real detail would materially change how a scoring pass reads the candidate:
   - Skills/stack entries named once but never tied to a specific project or bullet.
   - Bullets that claim scope or impact vaguely ("led a team", "improved performance") without a number, scale, or concrete before/after.
   - A role with noticeably thinner bullets than the others — usually compressed to fit one page, not because less happened there.
   - An employment gap or short-tenure role with no stated reason — worth having a truthful answer on file, not to interrogate.
2. **Ask a short, targeted set of questions** (aim for 4-8, not an exhaustive interrogation) directly in the conversation, grounded in what step 1 actually found — never generic ("tell me about yourself"). E.g.: "You list `<tool>` in your skills but it's not tied to a project anywhere — where did you actually use it, and for what?" / "The `<bullet>` about `<X>` — do you recall an actual number or scale, even approximate?" / "Your `<role>` section reads thinner than the rest — was there real work there that didn't make the page?" Wait for real answers. If the user doesn't recall specifics, that becomes an **Open Questions** entry — never invent a plausible-sounding fact to fill the gap.
3. **Write `data/career-notes.md`** (create fresh, or extend if it already exists in some partial form):
   - A short header stating the file's purpose (ground truth for `/score-listings` and `/tailor-cv`, beyond what fits on the one-page CV — never invent facts, only record what the user actually said).
   - One `##` section per CV role/employer, with bullets carrying the extra detail/context gathered.
   - A **Cross-role patterns** section for anything that's a genuine repeated theme across roles (only if the interview surfaced one — don't force it).
   - An **Open Questions** section — exactly this heading; `score-listings` and `tailor-cv` both key off it verbatim to exclude unresolved material from scoring/tailoring evidence — for anything without a confident answer.
4. Tell the user the file was created and that future `/score-listings` / `/tailor-cv` runs will pick it up automatically with no further setup.

### 0d. Optional rescore (only if invoked with a `rescore` argument)

Parse `$ARGUMENTS`. Rescoring only ever targets `status: To Apply` listings — same scope as step 1's queue — since anything else won't be picked up there anyway:
- No `rescore` keyword at all → skip this step entirely, proceed straight to step 1 (default behavior: score only what's unscored).
- `rescore` alone → clear match fields from every `To Apply` listing that currently has one (e.g. useful right after `career_notes.use_interview` flips from `false` to `true`, to redo every score with the richer background now available).
- `rescore <query>` → clear match fields only from `To Apply` listings whose `company` or `role` frontmatter case-insensitively contains `<query>` (substring match, e.g. `rescore taboola` or `rescore data scientist`).

For `rescore` alone:
```bash
python3 scripts/clear_match_scores.py <<'JSON'
["<every filename from> grep -l '^status: To Apply' data/listings/*.md"]
JSON
```
(build that JSON array by reading the `To Apply` filenames yourself first — the script only clears exactly the filenames it's given; `--all` clears every listing regardless of status, which is broader than this skill's scope, so don't use it here.)

For `rescore <query>`, first grep/read frontmatter to build the matching `To Apply` filename list yourself, then the same `clear_match_scores.py <<'JSON' [...]` call with just those names.

Report how many listings were cleared, then fall through into step 1 as normal — those listings now have no `match_score`, so the queue-building grep in step 1 picks them up automatically alongside anything else unscored.

### 1. Build the scoring queue

```bash
grep -L '^match_score:' $(grep -l '^status: To Apply' data/listings/*.md)
```

Two-stage filter: first the same `To Apply` queue `/apply` step 2 builds, then drop anything that already has a score (`-L` = files NOT matching). If the result is empty, tell the user "Nothing to score — every To Apply listing already has a match_score." and stop — no need to touch the report.

For each remaining file, read its frontmatter (`company`, `role`, `url`) and body prose — this is the batch for step 2.

### 2. Fan out scoring

Split the queue into batches of ~8-10 listings. For a small queue (a handful of listings) skip the fan-out and score them directly in this conversation — no need to spawn an agent for 2-3 listings.

Launch one `general-purpose` subagent per batch, in parallel. Each subagent does NOT share this conversation's context, so its prompt must include:
- The full CV text (paste inline).
- The full text of `data/career-notes.md` if it was read in step 0.4 (paste inline), **excluding its "Open Questions" section** — trim that section out before pasting; it's unresolved and must not be used as scoring evidence. If `career_notes.use_interview` is `false`, or the file doesn't exist, omit this and tell the subagent to score off the CV alone.
- The scoring rubric (section below) — paste it verbatim.
- Its slice of listings as `{filename, company, role, url, body}`.

Each subagent, per listing in its batch:
1. **If `url` is a real posting URL** (not an agency-placeholder domain like `gotfriends.co.il`, and the fetch succeeds with real content) → WebFetch it for the live job description.
2. **Otherwise** (placeholder domain, dead link, JS-shell with no extractable text) → fall back to the listing's own stored body prose as the JD source, and mark `confidence: "low"`.
3. Apply the rubric, produce `{score: 0-100, verdict: string, confidence: "full"|"low"}`.
4. Return one JSON array per batch: `[{"listing_filename": "<slug>.md", "score": <int>, "verdict": "<string>", "confidence": "full"|"low"}, ...]`.

Collect every batch's results before moving on — don't write anything incrementally per batch, to avoid partial-write races if a batch fails.

### Scoring rubric (paste verbatim into each fan-out subagent's prompt)

Score **pure technical/domain fit** between the candidate's full background and this job description, 0-100. Do NOT factor in seniority, location, salary, or company size — those are already filtered upstream. Score only:

- **Domain overlap** — does the candidate's actual project/product domain (per the CV and career-notes.md) match what this role's JD says the team works on day-to-day? (e.g. recommendation systems vs. computer vision vs. NLP vs. agentic/LLM systems vs. robotics are different domains even when the underlying ML engineering skill is similar.)
- **Technical stack overlap** — languages, frameworks, infra (e.g. PyTorch/TensorFlow, distributed training, MLOps tooling, specific model architectures) the JD calls out as required/preferred vs. what the candidate demonstrates hands-on.
- **Core competency match** — does the JD's central technical ask (e.g. "3D perception", "diffusion models", "LLM inference optimization", "agentic systems") actually appear in the candidate's real project descriptions — not just an adjacent/transferable skill?

**Use the career-notes.md excerpt as equally valid evidence, not just the CV.** The one-page CV necessarily leaves out true, relevant experience — career-notes.md holds that fuller material (per-role detail beyond the CV's bullets, plus cross-role patterns). If it describes hands-on work in the JD's domain/stack/core competency that isn't on the CV, count it toward the score exactly as you would a CV bullet — the goal is scoring the candidate's actual background, not penalizing them for what didn't fit on one page. Do not use anything from career-notes.md's "Open Questions" section (it should not have been included in your prompt at all, but if it appears, disregard it — those are open, unverified questions, not facts). When the CV and career-notes.md describe the same experience at different levels of detail, use the fuller career-notes.md version to judge fit, but don't double-count it as two separate pieces of evidence.

**Score bands** (guidance, not hard rules — use judgment within a band):
- 85-100: direct domain + stack match, CV shows hands-on work in this exact technical area.
- 65-84: strong adjacent fit — core ML/eng skill transfers cleanly, but domain specifics differ in ways a hiring team would notice.
- 40-64: partial fit — some technical overlap (e.g. same language/framework) but the role's core competency isn't demonstrated in the CV at all.
- 0-39: weak/no technical or domain overlap.

**Verdict requirements** — write 2-4 sentences that:
1. Name the candidate's actual relevant experience (specific project domains/technologies present in the CV or career-notes.md — not generic "has ML experience"). If a piece of evidence came from career-notes.md rather than the CV itself, it's fine to state it plainly (e.g. "her background also includes...") — no need to flag it as coming from a different source.
2. Name the JD's specific core requirement(s), quoting or closely paraphrasing the JD's own language for the technical area.
3. State explicitly what's present vs. missing, and WHY that gap (or match) drives the score.
4. Never write generic filler like "good technical background" or "some relevant experience" without naming specifics.

Target quality bar (a real prior verdict — replicate this specificity level):
> "Strong ML engineering foundation with production deep learning experience, but in the wrong domain. All experience is in recommendation systems and sequential user modeling — no computer vision, 3D perception, or ADAS-related work. Missing the core technical area (vision) that defines this role."

If scoring off a stored listing body (`confidence: "low"`) rather than the live JD, the verdict may be shorter/more hedged since the source material is thinner — but must still name whatever specifics the body text gives, never pure filler.

### 3. Write scores into frontmatter

Hand the collected `[{listing_filename, score, verdict, confidence}, ...]` list (merged across all batches) to a single script pass — do not use per-file `Edit` calls.

```bash
python3 scripts/write_match_scores.py <<'JSON'
[{"listing_filename": "...", "score": 78, "verdict": "...", "confidence": "full"}, ...]
JSON
```

The script upserts `match_score` / `match_verdict` / `match_confidence` / `match_computed` into each listing's frontmatter, surgically (never rewrites the whole file). It exits non-zero and prints which entries failed if any listing no longer exists or its `status` changed out from under the run — surface that to the user rather than silently ignoring it.

### 4. Generate the match report

```bash
python3 scripts/generate_match_report.py
```

Renders **every** listing that has a `match_score` (from this run and all prior runs — the report always reflects full current state) to `data/reports/match-scores-latest.html`. Self-contained: inline CSS, no external JS/CDN, safe to share or open offline. Overwrites the file every run — no dated history (same convention as `data/reports/chart-latest.png`).

### 5. Report results

Summarize:
- How many listings were scored this run vs. already-scored/skipped.
- Score distribution (informal — no hard cutoff is defined, just a summary).
- Which listings used the low-confidence stored-body fallback, and why (no real URL / fetch failed).
- Report path: `data/reports/match-scores-latest.html` — mention it's safe to open directly in a browser.
- Remind the user: run `/apply` next — its Tier A/B/C ranking now also shows `match_score` where present.
