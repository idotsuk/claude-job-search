---
name: tailor-cv
description: Generate a per-job tailored CV (subtle reordering/rephrasing of real content, never fabricated) rendered to a one-page PDF from the user's fixed HTML/CSS template. Runs manually against one listing by company/role query, or in batch against every scored "To Apply" listing above the auto-tailor threshold. Use when the user wants a tailored resume/CV for a specific job, or wants to generate tailored CVs after a /score-listings run.
---

# Tailor CV

<!-- version: bare-v1.0 -->

Produce a tailored, one-page CV PDF for a specific job listing: reorder/rephrase real content from the user's CV baseline and career notes for relevance to that job's JD, fill the fixed HTML/CSS template deterministically, render to PDF, and mark the listing `cv_status: draft` for later review.

**Companion skill, not chained:** intended to run right after `/score-listings`, same as `/apply` follows `/job-search` — but it is its own separate command. Nothing about running `/score-listings` automatically triggers this skill; the user (or a separate invocation) runs `/tailor-cv` deliberately.

**Two trigger modes:**
- **Manual** — `/tailor-cv <company or role query>` — fuzzy-matches one listing regardless of its `match_score` (or lack of one), **and regardless of `cv_status`** — this is also how to force a re-tailor: running manual mode again on a listing that already has `cv_status: draft` re-generates the payload from scratch and overwrites `data/cv-outputs/<stem>/` (fresh JD fetch, fresh `content-payload.json`, fresh PDF), useful after editing `data/career-notes.md` or a JD change.
- **Batch** — `/tailor-cv` with no argument — processes every eligible `To Apply` listing automatically (see step 2). Batch mode is one-shot by design (skips anything with `cv_status` already set) — use manual mode to re-tailor a specific listing.

## Core rules (read first)

1. **Never fabricate.** New bullets, rephrased bullets, and the summary line may only restate/reorganize facts that already exist in `data/cv-base-content.yaml` or `data/career-notes.md` (excluding its "Open Questions" section — those are unresolved, not facts). Never invent scope, metrics, tools, or outcomes not present in the source material.
2. **Changes are subtle.** Reordering bullets for relevance, light rephrasing for emphasis/keyword alignment with the JD, at most 1-2 genuinely-warranted new bullets per relevant role. This is not a rewrite — most of a tailored CV should read identically to the baseline.
3. **One page is a hard requirement**, enforced mechanically by `scripts/render_cv.py`, not by hoping the model gets it right. If a draft overflows, trim content and re-render — never truncate content unpredictably by leaving it to the renderer.
4. **`data/cv-base-content.yaml` is read-only ground truth.** Never edit it, never regenerate it from the docx — it was extracted once as this project's canonical "default/full" CV content.
5. **On render failure (not exactly one page), do NOT write `cv_status`.** Report the failure to the user and leave the listing's frontmatter untouched.

## Prerequisites

- `templates/cv-template.html` + `templates/fonts/*.ttf` — the shared, generic default design. Ships with the repo, read-only, works for any user out of the box.
- `data/cv-base-content.yaml` — the user's own baseline CV content, structured to match `templates/CV_TEMPLATE_SCHEMA.md`. **Does not exist for a new user** — step 0 below builds it automatically, once, from their real CV at `applicant.cv_path`.
- `data/cv-template/cv-template.html` + `data/cv-template/fonts/*.ttf` — **optional**, a personalized template matching the user's own CV's actual design (fonts, colors, layout), built by step 0 only if they provide their CV as a `.docx` and opt in. If they don't, this doesn't exist and rendering falls back to the shared default template — nothing else about tailoring changes.
- `data/career-notes.md` (read-only; may not exist — degrade to CV-baseline-only tailoring if so, same graceful-degradation pattern as `/score-listings` step 0.4).
- `scripts/render_cv.py`, Python Playwright (`python3 -c "import playwright"`), and — only for the personalized-template path — `python-docx` (`python3 -c "import docx"`). If missing, nudge: `pip install -r scripts/requirements.txt && python -m playwright install chromium` (same nudge pattern as `/job-search`).
- `config.yaml` → `tailoring.auto_tailor_min_score` (used only in batch mode).

## Steps

### 0. Preflight

1. **Read `./config.yaml`.** If missing, STOP with the same instruction `/job-search`/`/apply`/`/score-listings` use: `cp config.yaml.example config.yaml`, fill it in, re-run.
2. **Bootstrap `data/cv-base-content.yaml` on first run, if it doesn't exist yet.** This is what makes the skill work for a brand-new user with their own CV, not just the CV this project happened to be built against:
   a. Verify `applicant.cv_path` is set and exists (same check `/apply` step 1 and `/score-listings` step 0.2 use). If missing/unset, STOP and ask the user for the path, same as those skills.
   b. **Read the CV** with the `Read` tool (handles PDF and DOCX content directly).
   c. **Ask the user one question**: do they also have this CV as a Word (`.docx`) file, and would they like their tailored CVs to preserve its exact original look (fonts, colors, layout) rather than a clean built-in default? Frame it as genuinely optional — either answer produces a working result:
      > I don't have your CV in a structured format yet. I can extract your content now either way. Separately: do you have this CV as a `.docx`, and want your tailored CVs to match its exact design? If yes, share that file's path. If you'd rather just use a clean built-in template look, that's the default — no extra step needed.
      Store nothing about this choice in config — it's a one-time build decision, re-askable any time by simply invoking the docx-personalization sub-step again later (see step 4's note).
   d. **Extract content into `data/cv-base-content.yaml`** (always, regardless of the docx answer) — read the CV text from step 2b and write it out matching `templates/CV_TEMPLATE_SCHEMA.md`'s exact shape (top-level scalars `name/summary/location/phone/email/linkedin_url/linkedin_display`, `companies` → `roles` → `bullets`, `education`, `army` — or whatever the CV's equivalent optional section is, keep the same shape — `core_expertise`, `skill_groups`). **Restate only what the CV actually says** — this is ground truth every future tailoring pass starts from, so never invent scope, metrics, or sections the CV doesn't have. If the CV has a section this schema has no named slot for (e.g. Certifications, Publications, Volunteer work), use judgment: fold it into the closest existing section-shape, or tell the user it isn't currently representable and ask how they'd like to handle it — don't silently drop real content.
   e. **If they provided a `.docx` and opted in to design personalization**: replicate the same method `templates/CV_TEMPLATE_SCHEMA.md` documents for how *this repo's own* default template was built — `python-docx` + raw OOXML inspection (`styles.xml` for fonts/colors, `numbering.xml` for the bullet glyph, the docx's table grid for column-width ratios, embedded font relationships for the actual TTF files) — against **their** docx, and write the result to `data/cv-template/cv-template.html` (identical `{{PLACEHOLDER}}`/`<!--BLOCK:...-->`/`<!--SECTION:...-->` schema to the shared template — reuse `templates/cv-template.html` itself as the structural skeleton, only replacing the extracted design tokens and re-pointing embedded fonts) + `data/cv-template/fonts/*.ttf` (their fonts, extracted the same way). Requires `python-docx`; if missing, nudge the install command and fall back to (f) for this run.
   f. **If no docx was provided, or extraction fails**: use the shared `templates/cv-template.html` as-is — tell the user their tailored CVs will use the clean built-in default look, and that they can provide a `.docx` later to upgrade (no need to delete or redo anything already built — re-run this sub-step manually by asking to "personalize my CV template from `<path.docx>`" any time, since `data/cv-base-content.yaml` already existing means the normal flow below won't re-trigger it automatically).
   g. Report what was built (content only, or content + personalized template) before continuing.
3. **Verify `templates/cv-template.html` and `data/cv-base-content.yaml` both exist** (the latter guaranteed by step 2 above having just run, or already present from an earlier run). If `templates/cv-template.html` is somehow missing, STOP — that's a repo-integrity problem, not a per-user setup gap.
4. **Read `data/cv-base-content.yaml`** (YAML) and hold it in context for the whole run — this is the baseline every tailored payload starts from. Read it once per `/tailor-cv` invocation, not once per listing.
5. **Respect `career_notes.use_interview` in config.yaml, same setting `/score-listings` step 0.4 owns.** If `false`, skip `data/career-notes.md` entirely even if it exists — this is a deliberate resume-only choice, not a missing file, and it should behave identically here. Otherwise (`true`, or unset — this skill doesn't ask the interview question itself, only `/score-listings` does), read `data/career-notes.md` if it exists, holding it in context, **excluding everything under its "Open Questions" heading** — trim that section out mentally (or literally, if pasting into a subagent prompt later) before using it as source material. If the file doesn't exist, proceed with `cv-base-content.yaml` alone.
6. **Bind `{auto_tailor_min_score}`** from `tailoring.auto_tailor_min_score` (default `70` if unset) — used only by batch mode (step 2b).

### 1. Determine mode and build the target list

- **Manual** (`/tailor-cv <query>`): fuzzy-match the query (case-insensitive substring match is enough — don't over-engineer) against `company` and `role`/`title` frontmatter across `data/listings/*.md`.
  - **Zero matches** → tell the user, show a few close-sounding listings if any exist, stop.
  - **One match** → proceed with that listing, regardless of its `match_score` or whether it already has one.
  - **Multiple matches** → list them (`Company — Role [status, match_score if present]`) and ask the user to pick one before proceeding. Don't guess.
- **Batch** (`/tailor-cv` with no argument):
  ```bash
  grep -l '^status: To Apply' data/listings/*.md | xargs grep -L '^cv_status:'
  ```
  Gives every `To Apply` listing that hasn't been tailored yet (the `-L` step means "no `cv_status:` line present" — never re-tailors something already processed, same one-shot convention `/score-listings` uses for `match_score`). For each candidate, parse `match_score` from frontmatter and keep only those with `match_score >= {auto_tailor_min_score}`. Listings with no `match_score` at all are skipped in batch mode (run `/score-listings` first, or use manual mode to target them directly regardless of score).
  - If the resulting list is empty, tell the user why (e.g. "no To Apply listings scored ≥70 without a cv_status yet") and stop — no need to touch anything else.

### 2. Per listing: gather source material

For each listing in the target list (manual mode: just the one; batch mode: loop, one at a time is fine — this step needs real judgment per listing, don't fan out to subagents the way `/score-listings` does for pure scoring):

1. Read the listing's frontmatter (`company`, `role`, `url`, `match_verdict` if present — it's a useful hint of what already stood out) and body text.
2. **Get the JD**: if `url` looks like a real posting (not a placeholder agency domain, not a generic company-locations page) → `WebFetch` it. Otherwise fall back to the listing's own stored body prose. Same "real URL vs. stored body fallback" logic as `/score-listings` step 2.1-2.2 — no need for a `confidence` field here, just use whichever source is available.
3. Hold `cv-base-content.yaml` (step 0.3) and the trimmed `career-notes.md` (step 0.4) alongside the JD text.

### 3. Produce the tailored content payload

This is the judgment step — decide, per role on the CV:
- **Which existing bullets to keep**, and in what order (most JD-relevant first).
- **Light rephrasing** of a bullet's wording where it helps keyword/emphasis alignment with the JD — never change what it claims happened.
- **At most 1-2 new bullets per relevant role**, only if `career-notes.md` genuinely has true, specific, JD-relevant material for that role that isn't already on the baseline CV. If nothing in career-notes.md is both true and clearly relevant, add nothing — an empty-handed pass is a correct outcome, not a failure.
- **The summary line** (`{{SUMMARY}}`) may be lightly adjusted to foreground the most JD-relevant framing of the same true background — not rewritten wholesale.
- **Sidebar (`core_expertise`, `skill_groups`)**: reordering only is usually enough — bring the JD-relevant tags/tools earlier. Don't add tools that aren't in the baseline unless career-notes.md explicitly ties them to a role and the JD calls for them.
- **Education / Army**: leave as-is unless a specific JD reason exists to trim (e.g. severe space pressure) — use `drop_sections: ["ARMY"]` in the payload as a last resort for space, not a first instinct.

Keep this deliberately unprescriptive beyond "subtle, truthful, one-page-respecting" — no house style rules, no readability targets, no banned-word lists. Those get calibrated later against the user's actual reaction to real output (per `docs/tailoring-plan.md` decision 1), not guessed upfront here.

**One calibrated rule so far, from a real reviewed draft: never use an em dash (—) or en dash (–) as a sentence connector/parenthetical aside in generated or rephrased text.** It reads as an obvious AI-writing tell. Use a comma, a period (split into two sentences), or a colon instead. This applies to the summary line and any rephrased/new bullet — not to dates or ranges (e.g. `2022–2026` in `{{COMPANY_DATES}}`/`{{ROLE_DATES}}` is untouched, since those aren't generated prose).

**Second calibrated rule: generalize granular technical-mechanism detail from `career-notes.md` by default.** Some career-notes entries carry real depth on purpose (specific tools, debugging techniques, exact parameter values) — good for interview material, sometimes flagged inline with an explicit "register note." When drawing a new bullet from material like that, default to stating the analysis/skill/outcome in normal CV register (what was investigated, what was found, why it mattered) rather than the literal step-by-step mechanism — unless the JD itself specifically signals it wants that depth (e.g. an ML infra/platform role explicitly asking for framework-internals-level debugging experience), in which case the extra specificity is a legitimate JD-match signal, not noise.

**Third calibrated rule: the same over-specificity trap applies to naming the internal feature/product under investigation, not just tool mechanics.** A real reviewed draft turned "led a team investigation that found feature X's apparent benefit was actually a confound caused by Y" into a bullet naming the exact internal feature and the exact confound mechanism — too specific and too inside-baseball for a CV line, even though the underlying finding is genuinely impressive. Default to describing the *shape* of the accomplishment instead: what kind of initiative it was (e.g. "led a team investigation into a model's pain points"), what rigor was applied (e.g. "validated which candidate issues were genuine versus correlational artifacts"), and what lasting impact it had (e.g. "built the findings into ongoing offline and online evaluation") — without naming the specific internal feature or the specific confound. This also has a practical upside: a single well-generalized bullet like this can absorb what would otherwise be two or three narrower bullets (the investigation, the specific finding, and the "became permanent infrastructure" follow-through), freeing space for other material. Reserve naming the exact internal mechanism for interview conversation, not the CV.

Build the full payload matching `scripts/render_cv.py`'s expected shape (documented in that script's module docstring and mirrored by `data/cv-base-content.yaml`'s structure): top-level scalars `name, summary, location, phone, email, linkedin_url, linkedin_display`, plus `companies` (each with `roles`, each with `bullets`), `education`, `army`, `core_expertise`, `skill_groups`, and optionally `drop_sections`.

### 4. Render

**Output directory naming — reuse the listing's own filename, don't re-derive slugs.** The matched listing file is already named `<company-slug>-<role-slug>.md` by `/job-search` step 5. Take that filename's stem (strip `.md`) and use it directly as the `data/cv-outputs/<stem>/` directory name — e.g. listing `data/listings/palo-alto-networks-principal-senior-ml-platform-engineer-cortex.md` → output dir `data/cv-outputs/palo-alto-networks-principal-senior-ml-platform-engineer-cortex/`. This guarantees the two trees stay in exact 1:1 correspondence with zero risk of the slugification rule drifting between the two skills — never independently slugify `company`/`role` frontmatter fields yourself.

1. Write the payload to `data/cv-outputs/<stem>/content-payload.json` (keeps a debuggable record of exactly what was generated, alongside the rendered `cv.html`).
2. **Pick the template**: if `data/cv-template/cv-template.html` exists (the user opted into design personalization in step 0), pass it explicitly; otherwise omit `--template` and the script defaults to the shared `templates/cv-template.html`. Run:
   ```bash
   python3 scripts/render_cv.py --input data/cv-outputs/<stem>/content-payload.json --out-dir data/cv-outputs/<stem> [--template data/cv-template/cv-template.html]
   ```
3. **On success** (exit 0, script prints `OK: wrote ... (1 page).`): proceed to step 5.
4. **On failure** (non-zero exit, most commonly the one-page check): the script's stderr says why. If it's a page-overflow, trim the payload (drop the lowest-relevance new/rephrased bullet first, then consider `drop_sections: ["ARMY"]` as a last resort) and re-render — allow yourself up to ~3 trim-and-retry attempts. If it still doesn't fit after that, or the failure is something else (template/Playwright error), **stop, do not write `cv_status`**, and report the failure plus the script's error output to the user for this listing (continue with the rest of the batch if in batch mode — one listing's failure shouldn't block the others).

### 5. Write status back

After a successful render, upsert `cv_status: draft` into the listing's frontmatter via the companion script (same surgical-upsert pattern as `scripts/write_match_scores.py` — never a full file rewrite, body/Communications table untouched):

```bash
python3 scripts/write_cv_status.py <<'JSON'
[{"listing_filename": "<stem>.md", "cv_status": "draft", "cv_output_dir": "data/cv-outputs/<stem>"}]
JSON
```

In batch mode, collect all successful listings and pass them as one JSON array in a single script call, same "don't write incrementally per item" convention `/score-listings` step 3 uses.

The script also stamps `cv_generated: <today>`. `cv_output_dir` lets a later review step (not built here — see `docs/tailoring-plan.md` build order item 5/6) find the PDF without re-deriving the slug.

### 6. Report results

Summarize:
- **Manual mode**: which listing was tailored, output path (`data/cv-outputs/<stem>/cv.pdf`), a short note on what changed (reordered X, added a bullet about Y, adjusted the summary) and why (tie it to something specific in the JD).
- **Batch mode**: how many listings were eligible, how many succeeded, how many failed and why (page-overflow after retries vs. other errors), and the same per-listing "what changed" summary for each success.
- Remind the user this output is `cv_status: draft` — review happens later via the match-score report (a future step surfaces draft CVs there; this skill doesn't build that UI) or by opening the PDF directly.
- Do **not** claim `/apply` will automatically pick this up — that wiring is a separate, not-yet-built step per `docs/tailoring-plan.md`.
