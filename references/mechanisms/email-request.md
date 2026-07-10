# Mechanism: Email Request

**Automation level:** Full (draft + send with `inbox-send` consent).

## When to use

Route here when `brokers.json` has `mechanism_type: "email-request"`. These brokers have no self-service opt-out form; deletion must be requested by email.

## Agent steps

1. **Render the deletion email:**
   ```bash
   python scripts/render_email.py \
     --template deletion \
     --broker-id <id> \
     --state <state> \
     --name "<full_name>" \
     --phone "<phone>" \
     --email "<opt_out_email>"
   ```
   This produces a rendered subject + body + recipient address from `data/jurisdictions.json` and `assets/templates/deletion_email.md`.

2. **Present the draft to the user** for review (regardless of `inbox-send` consent — the user should always see what's being sent on their behalf before a first send to each new broker).

3. **Send the email** via the Cowork email connector using the rendered `to`, `subject`, and `body` fields. Use the dedicated opt-out email address (FR-0.5) as the From address if set up.

4. **Record submission** in the case store:
   ```
   case_store.mark --broker-id <id> --status awaiting-confirmation
   ```
   Log the email subject in the audit entry.

5. Set `pii_sent` to at minimum `["name", "phone"]` plus any additional fields the email included.

## What to write to case_store

```json
{
  "broker_id": "<id>",
  "status": "awaiting-confirmation",
  "mechanism": "email-request",
  "pii_sent": ["name", "phone", "state"],
  "opt_out_email_used": "<to_address>"
}
```

## Confirmation and follow-up

- **Watch the inbox** for a reply confirming deletion (with `inbox-read` consent).
- If no reply arrives within **30 days**, send the escalation email:
  ```bash
  python scripts/render_email.py \
    --template escalation \
    --broker-id <id> \
    --state <state> \
    --name "<full_name>" \
    --phone "<phone>" \
    --email "<opt_out_email>" \
    --submitted-date <original_date> \
    --deadline-date <deadline_date> \
    --days-overdue <n>
  ```
- If still no response after escalation, mark the case `failed` and note the dark pattern.

## Confirmation loop

Every candidate reply — not just ones that "look like" a confirmation — should go through the deterministic classifier rather than being read and judged by eyeballing the inbox:

1. **Save the raw email body** to a file (the agent never pipes a live mailbox into the script; it saves a copy first).
2. **Classify it:**
   ```bash
   python scripts/confirmation.py --email-file <path> --broker-id <id>
   ```
   This never makes a network call and never touches the mailbox itself — it only reasons over the saved text/HTML. It returns one of `confirmation-link`, `already-confirmed`, `rejection`, or `unrelated`.
3. **Act on the JSON:**
   - `confirmation-link` — with `inbox-click` consent, open `confirmation_url` (a browsing agent click, not a script fetch) to complete the loop; the case stays `awaiting-confirmation` until you do.
   - `already-confirmed` — apply directly: `python scripts/confirmation.py --email-file <path> --broker-id <id> --apply` (moves the case to `confirmed-removed`).
   - `rejection` — apply with `--apply` (moves the case to `failed`), then consult "Common failure modes" below.
   - `unrelated` — no case-store action; keep watching the inbox.

## Common failure modes

| Symptom | Action |
|---|---|
| Email bounces | Try alternate address (e.g. `optout@`, `dnssmi@`, or webmaster) |
| Auto-reply says "use our web form" | Re-route to `web-form.md` or `captcha-handoff.md` |
| No reply after 30 days | Send escalation email; mark `failed` if still no response after another 15 days |
