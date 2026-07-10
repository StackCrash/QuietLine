# Mechanism: Web Form + CAPTCHA (Human Handoff)

**Automation level:** Partial — the agent fills all form fields, then pauses and hands to the user to solve the CAPTCHA and click Submit.

## When to use

Route here when `brokers.json` has `mechanism_type: "web-form"` and `captcha_required: true`, OR when a CAPTCHA appears unexpectedly on a broker routed via `web-form.md`.

**SAFE-2 reminder:** Never attempt to solve, bypass, or route around a CAPTCHA programmatically. This is non-negotiable.

## Agent steps (pre-handoff)

1. **Verify the opt-out URL** via `scripts/verify_url.py --broker-id <id>`.
2. **Navigate to the opt-out URL** in Chrome.
3. **Locate the listing** (same as `web-form.md` step 3).
4. **Fill all form fields** — name, phone, state, and any other required fields — leaving the CAPTCHA unsolved.
5. **Pause** and add this broker to the human-action queue (do not submit yet).
6. Mark the case `awaiting-user`:
   ```
   case_store.mark --broker-id <id> --status awaiting-user
   ```
   Set `human_action_required`: "Solve CAPTCHA on the pre-filled form, then click Submit."

## Human queue entry format

```
[Spokeo] — CAPTCHA required
  The form at https://www.spokeo.com/optout is pre-filled with your name and phone number.
  1. Open the link (or switch to the Chrome tab if still open).
  2. Solve the CAPTCHA.
  3. Click Submit.
  Expected: a confirmation email will arrive within a few minutes.
```

## After the user completes the CAPTCHA

When the user confirms they submitted:
- Mark the case `awaiting-confirmation`.
- Update `actor` to `"user"` in the audit log entry for the submission.
- Watch for the confirmation email and complete the loop as in `web-form.md`.

## Common failure modes

| Symptom | Action |
|---|---|
| CAPTCHA loop doesn't resolve | Note in case store; user may need to try again later |
| Form resets after CAPTCHA | Re-fill from scratch; note the issue |
| No confirmation email | Re-queue for next human session |

## Queueing

Marking the case `awaiting-user` with a specific `human_action_required` string (as in step 6 above) is what makes it appear in the queue — `scripts/action_queue.py` reads the case store, not this file, to render the numbered list the user sees. Sections are ordered by mechanism rank, and `captcha-handoff` is always rendered first.
