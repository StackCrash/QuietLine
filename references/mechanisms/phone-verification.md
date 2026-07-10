# Mechanism: Phone Verification

**Automation level:** Human — the agent prepares the request and walks the user through the verification step; the user completes it.

## When to use

Route here when `brokers.json` has `mechanism_type: "phone-verification"`, or when a broker's opt-out flow requires calling or texting a code to the number being removed before completing the request.

## Why this requires the user

Some brokers verify that the person requesting deletion controls the phone number being removed by sending a code via SMS or automated call to that number. The agent cannot receive SMS messages or answer phone calls. The user must complete this step.

## Agent steps (pre-handoff)

1. **Navigate** to the opt-out URL.
2. **Fill** all form fields up to the phone-verification step (name, phone, state, email).
3. **Trigger** the verification code send (click "Send Code" or equivalent).
4. **Pause** immediately and add to the human-action queue:
   - Mark the case `awaiting-user`.
   - Set `human_action_required`: "A code has been sent to your phone. Enter it in the form within [timeframe] to complete the deletion request."

## Human queue entry format

```
[BrokerName] — Phone verification required
  A verification code was sent to: <phone_number>
  1. Check your phone for the SMS (or pick up the automated call).
  2. Enter the code in the form — the form is open in your browser.
  3. Click Submit / Confirm.
  The code expires in [N] minutes.
  Let me know when done so I can mark this completed.
```

## After the user completes verification

When the user confirms they entered the code and submitted:
- Mark the case `awaiting-confirmation` or `confirmed-removed` depending on whether the broker sends a follow-up confirmation.
- Audit log actor: `"user"`.

## Common failure modes

| Symptom | Action |
|---|---|
| Code doesn't arrive | Wait 5 minutes, then request a new code (most forms have a "resend" option) |
| Code expired before user could enter it | Request a fresh code; re-queue |
| Broker calls the number (voice verification) | Walk the user through the voice flow; note it in case store |
| Broker requires the number be active/reachable | Note in case store; if number is ported or inactive, deletion may still be possible via email escalation |

## Queueing

Marking the case `awaiting-user` with the specific `human_action_required` string from step 4 above is what surfaces it — `scripts/action_queue.py` renders the numbered queue from the case store, not from this file. `phone-verification` items rank second, right after any `captcha-handoff` items.
