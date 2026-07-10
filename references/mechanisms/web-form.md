# Mechanism: Web Form (No CAPTCHA)

**Automation level:** Full — the browsing agent locates the listing, fills the form, and submits.

## When to use

Route here when `brokers.json` has `mechanism_type: "web-form"` and `captcha_required: false`.

## Agent steps

1. **Verify the opt-out URL** using `scripts/verify_url.py --broker-id <id>`. If the stored URL is dead, use the `resolved_url` from the fallback result.
2. **Navigate to the opt-out URL** in Chrome.
3. **Locate the user's listing** by searching name + state/phone on the form. If a specific `listing_url` was found during discovery, navigate there directly.
4. **Fill the form** with the minimum required fields (name, phone, state — do not volunteer DOB or address unless the form requires it).
5. **Submit** the form.
6. **Record the outcome** in the case store:
   - On successful submission → `case_store.mark --broker-id <id> --status awaiting-confirmation`
   - On error or broken form → `case_store.mark --broker-id <id> --status failed --note "<error>"`

## What to write to case_store

```json
{
  "broker_id": "<id>",
  "status": "awaiting-confirmation",
  "mechanism": "web-form",
  "pii_sent": ["name", "phone", "state"],
  "opt_out_url_used": "<resolved_url>"
}
```

## Common failure modes

| Symptom | Action |
|---|---|
| Form returns 404 | Mark `failed`; note "opt-out form missing"; escalate to user |
| Form requires account creation | Re-route to `account-required.md` |
| CAPTCHA appears unexpectedly | Re-route to `captcha-handoff.md`; do not attempt to solve |
| Confirmation email not received within 7 days | Re-run submission; if still nothing, mark for escalation |

## After submission

Watch for a confirmation email (with `inbox-read` consent). When the link arrives:
- Open the confirmation link.
- Mark the case `confirmed-removed`.
- Log the action with actor `"agent"`.

If no email arrives within 7 days, re-check the case and either re-submit or escalate.
