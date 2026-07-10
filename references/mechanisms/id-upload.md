# Mechanism: ID Upload

**Automation level:** Human-gated — the agent explains what's needed and helps with redaction guidance; the user decides whether to upload and does so themselves. The agent never auto-submits an ID document.

## When to use

Route here when a broker's opt-out flow requires uploading a government-issued ID (e.g., driver's license, passport) to verify identity before processing a deletion request.

## Why this is always human-gated (DH-4)

Identity documents are highly sensitive. A mistake here — uploading to the wrong URL, or uploading an insufficiently redacted document — has serious consequences. For this reason:
- The agent **never automatically uploads** an ID document, regardless of other consent scopes granted.
- The user **reviews the redaction** and **uploads manually** for each individual broker.
- Each upload is logged in the audit trail with `actor: "user"`.

## Agent steps

1. **Detect** the ID-upload requirement during form navigation.
2. **Mark the case** `awaiting-user` with a clear explanation:
   ```
   human_action_required: "ID upload required. See instructions below."
   ```
3. **Explain** exactly what the broker is asking for (which ID type, what format, how to submit).
4. **Provide redaction guidance:**

### Redaction guidance (show to user)

Before uploading any ID document, redact everything except the fields the broker specifically requires (usually full name, address, and the ID number is optional). Use any image editor or PDF annotator:

**Redact (cover with black boxes):**
- Photo/portrait
- Signature
- Date of birth (unless specifically required)
- ID/license number (unless specifically required)
- Magnetic stripe / barcode

**Keep visible:**
- Full legal name
- Current address (if the broker's form requires address verification)
- State of issue

Save as a new file — never alter your original ID document. Name the file something neutral (e.g., `optout_id.pdf`).

5. **Add to the human-action queue:**

```
[BrokerName] — Government ID upload required
  This broker requires identity verification via a government ID before processing your deletion request.

  Before uploading:
  1. Create a redacted copy of your driver's license (see redaction guide above).
  2. Keep visible: your full name and address.
  3. Cover: photo, signature, DOB, ID number, barcode.

  To upload:
  1. Go to: <opt_out_url>
  2. Fill in your name and phone number.
  3. When prompted for ID, upload your redacted copy.
  4. Submit.

  This is the only QuietLine action that requires uploading an ID. Let me know when done.
```

## After the user uploads

When the user confirms submission:
- Mark the case `awaiting-confirmation` or `confirmed-removed`.
- Log `actor: "user"`, and note in `pii_sent: ["name", "phone", "redacted_id"]`.
- Never log the actual ID document contents.

## Common failure modes

| Symptom | Action |
|---|---|
| Broker rejects redacted ID | Note in case store; some brokers require unredacted IDs — user decides whether to proceed |
| Upload portal is broken | Try again later; note in case store; consider email escalation instead |
| Broker asks for Social Security Number | Do not proceed; mark `failed` with note "broker requested SSN — declined"; advise user this is unusual and potentially a scam |
