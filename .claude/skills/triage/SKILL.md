---
name: triage
description: Launch a local one-at-a-time review UI over the "To Apply" queue — keep a listing queued or decline it with a categorized reason (company fit, role fit, tech-stack gap, other). Decisions write back to the listing file and to data/decline-log.yaml for future /job-search runs to learn from. Use when the user wants to review, triage, clean up, or "go through" their To Apply pile, or asks for a visual/interactive way to review listings one by one.
---

# Triage — Review the "To Apply" Queue

<!-- version: bare-v1.0 -->

Runs `scripts/triage_server.py`, a local server that serves a card-based review UI at `http://127.0.0.1:8934/` (one browser tab, opened automatically). The user reviews each `status: To Apply` listing and either **Keeps** it (stays queued for `/apply`) or **Declines** it with a reason. All file writes happen from the server itself as the user clicks/presses keys — this skill's job is to launch the session, wait for it to end, and report back.

## Prerequisites

- **Listings tree**: `data/listings/*.md`. Populated by `/job-search`. If empty or the directory doesn't exist, tell the user there's nothing to triage yet and stop.
- No extra Python dependency beyond what `/job-search` already requires (`pyyaml`) — the server itself is stdlib-only.

## Steps

### 1. Check there's something to review

Run `python3 scripts/triage_server.py --port 8934` (foreground, in this skill's shell — it blocks until the session ends). If it prints `Nothing to triage — no To Apply listings pending review.` and exits immediately, relay that to the user and stop; don't treat it as an error.

### 2. Launch and wait

The script opens the browser tab itself and blocks until the user clicks **Finish** in the page (or the process is interrupted). Do not background this process and do not poll it — just run it and wait for it to exit. While it's running, the user is interacting with the browser tab directly; you have nothing to do until it exits.

If port 8934 is already in use (a previous session didn't shut down cleanly), retry once with `--port 8935`.

### 3. Report results

On exit the script prints a one-line summary: `Session ended. Kept: N, Declined: M.` Relay that to the user in your own words — don't just paste the raw line. If `M > 0`, mention that declined listings moved to `status: Skipped` with their reason recorded, and that `/job-search`'s next run will read `data/decline-log.yaml` for repeat-pattern suggestions (see below) — it does not auto-block anything on its own.

## How decisions feed back into `/job-search`

Every decline appends one entry to `data/decline-log.yaml` (`id`, `date`, `file`, `company`, `role`, `reason` — one of `company_fit`/`role_fit`/`stack_gap`/`other` — and an optional freeform `note`). Before its next scan, `job-search/SKILL.md` step 0b reads this log and asks about it directly — **a single decline is enough, it doesn't wait for a repeat** — e.g. "Acme declined for company fit — add to `search.company_blocklist`?", or a stack_gap/role_fit/other note naming a technology, title, or place → `profile.anti_interests` / `search.location_blocklist`. This runs *before* that run's scan, so a yes filters the very same run. This is **advisory only**: nothing auto-edits `config.yaml` without an explicit yes, and a "no" is remembered (that entry is tagged `suggested: true`) so it doesn't nag you again on the same decline — a genuinely new decline still gets its own fresh ask.

## Notes

- **Prev/Next** (`←`/`→` or the header buttons) browse the queue freely, independent of deciding — revisiting an already-decided card shows a badge with its current decision and lets you change it (Keep/Decline again just overwrites). Deciding on a card always advances one step forward from wherever you are.
- **Decline reasons**: picking "Don't want to work at this company" declines immediately — it rarely needs elaboration. The other three reasons (role fit, tech-stack gap, other) only *select* on click: the picker stays open with the note field focused so you can write what's missing before confirming (button or Enter). This is deliberate — a bare `role_fit`/`stack_gap` tag with no note doesn't carry enough signal for the repeat-pattern questions in `job-search/SKILL.md` step 0b to be useful later.
- A listing reviewed with **Keep** is stamped `reviewed: <date>` in its frontmatter so it won't resurface in a later `/triage` session; its `status` stays (or reverts to) `To Apply` even if it was previously declined earlier in the same session.
- Undo (the `U` key, or the Undo button) reverts only the single most-recent decision server-side — it's meant for "oops, wrong button." Prev/Next + re-deciding is the general way to revise an earlier call.
- This is a local-only session: nothing here calls out to Gmail/WhatsApp/Calendar or any external service.
