# Mechanism: Account Required

**Automation level:** Partial — the agent sets up the flow and navigates to the opt-out page; the user completes account creation or login and makes the final request.

## When to use

Route here when `brokers.json` has `mechanism_type: "account-required"`, or when a web-form broker redirects to an account creation/login gate before allowing the opt-out form.

## Why this requires the user

Some brokers require creating an account with your real email address before you can submit a deletion request. The agent can navigate to the sign-up page, but account creation involves choosing a password and potentially confirming a new email — steps the user should own, especially if using their primary email.

**Recommendation:** use the dedicated opt-out email address (FR-0.5) for all account creations to quarantine sign-up confirmation emails.

## Agent steps (pre-handoff)

1. **Navigate** to the broker's opt-out URL.
2. **Detect** the account gate (sign-up / log-in prompt before opt-out).
3. **Pre-fill** the sign-up form with: first name, last name, and the dedicated opt-out email address.
4. **Pause** and add to the human-action queue:
   - Mark the case `awaiting-user`.
   - Set `human_action_required`: "Create account with your opt-out email, confirm the email link, then navigate back to the opt-out/deletion page."

## Human queue entry format

```
[BrokerName] — Account creation required
  The sign-up form is pre-filled with your name and opt-out email address.
  1. Choose a password and complete account creation.
  2. Click the confirmation link in the email sent to your opt-out address.
  3. Once logged in, navigate to: Settings → Privacy → Delete My Data.
  4. Confirm the deletion request.
  Let me know when done so I can update your case record.
```

## After the user completes the account step

When the user confirms deletion was requested:
- Mark the case `awaiting-confirmation` (if broker sends a separate confirmation) or `confirmed-removed` (if the account deletion is immediate).
- Set the submission actor to `"user"` in the audit log.

## Common failure modes

| Symptom | Action |
|---|---|
| Sign-up email goes to spam | Ask user to check spam/junk folder |
| Broker only allows deletion by deleting the account entirely | Note this clearly; user may not want to lose account data |
| Account creation fails (email already exists) | User may need to log into a prior account; note in case store |
