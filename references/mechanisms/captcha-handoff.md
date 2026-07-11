# Mechanism: Web Form + CAPTCHA (Human Handoff)

**Automation level:** Partial — the agent fills all form fields, then pauses and hands to the user to solve the CAPTCHA and click Submit.

## When to use

Route here when `brokers.json` has `mechanism_type: "web-form"` and `captcha_required: true`, OR when a CAPTCHA appears unexpectedly on a broker routed via `web-form.md`.

**Never attempt to solve, bypass, or route around a CAPTCHA programmatically — always route it to the human. This is non-negotiable.**

## Batch solve session

CAPTCHA brokers are not handled one at a time. Phase 2 runs in two waves:

- **Wave A — automated brokers.** Every broker that doesn't need a human (per `SKILL.md` Phase 2's mechanism routing) is submitted first, in full.
- **Wave B — CAPTCHA pre-fill.** For **each** captcha-required broker, in turn:
  1. **Verify the opt-out URL** via `scripts/verify_url.py --broker-id <id>`.
  2. **Open the opt-out URL in its own Chrome tab** (leave prior Wave-B tabs open — don't close or reuse them).
  3. **Locate the listing** (same as `web-form.md` step 3).
  4. **Fill all form fields** — name, phone, state, and any other required fields — leaving the CAPTCHA unsolved.
  5. Mark the case `awaiting-user` with `human_action_required` set (e.g. "Solve CAPTCHA on the pre-filled form, then click Submit."):
     ```
     case_store.mark --broker-id <id> --status awaiting-user
     ```
  6. Space this out from the next broker per SAFE-3 (rate-limited — don't hammer sites back to back).

Only **after all** Wave-B brokers are pre-filled does the agent hand the user anything — one checklist, not a per-site interruption:

```bash
python scripts/action_queue.py --session
```

## Solve-session checklist format

`--session` renders only `captcha-handoff` items, renumbered independently of the full action queue, one line per open tab:

```
- [ ] 1. **Spokeo** — Tab 1
  - spokeo: https://www.spokeo.com/optout
  - Solve the CAPTCHA on the pre-filled form, then click Submit.
  - Expected: A confirmation email should arrive within a few minutes of submitting.

When you're done (even partially), tell the agent which numbers you completed — it records them in one step:

python scripts/case_store.py mark-batch --broker-ids <id1,id2,...> --status awaiting-confirmation
```

## After the solve session

The user reports which numbers they finished ("done 1, 3, 5" or "all done") — not per broker, and not while other tabs are still open. The agent maps those numbers back to `broker_id`s using the same session items, then records completion in **one** call:

```
case_store.py mark-batch --broker-ids <id1,id2,id3> --status awaiting-confirmation
```

`actor` defaults to `"user"` on `mark-batch`, so the audit log correctly shows the user (not the agent) performed the submission. Then watch for the confirmation email and complete the loop as in `web-form.md`.

Partially-completed sessions are fine: unfinished items simply stay `awaiting-user` and reappear the next time `--session` is rendered — the checklist is resumable across multiple sittings.

## Common failure modes

| Symptom | Action |
|---|---|
| CAPTCHA loop doesn't resolve | Note in case store; user may need to try again later |
| Form resets after CAPTCHA | Re-fill from scratch; note the issue |
| No confirmation email | Re-queue for next human session |

## Queueing

Marking the case `awaiting-user` with a specific `human_action_required` string (as in Wave B step 5 above) is what makes it appear in the queue — `scripts/action_queue.py` reads the case store, not this file, to render the numbered list the user sees. `--session` renders only `captcha-handoff` items as the solve checklist; the plain (no-flag) queue still covers every mechanism, sectioned and ordered by mechanism rank with `captcha-handoff` first.
