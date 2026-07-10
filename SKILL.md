---
name: quietline
description: >-
  Find where the user's phone number is exposed across data brokers and
  people-search sites and drive opt-out / deletion requests to completion,
  then re-check on a schedule. Use this whenever the user mentions spam calls,
  robocalls, telemarketers, or solicitation calls; wants to remove, delete, or
  opt out their phone number or personal info from data brokers or people-search
  sites (Whitepages, Spokeo, Radaris, BeenVerified, etc.); asks how to stop
  unwanted calls at the source; or wants to find and remove their personal
  information from the internet — even if they don't say "data broker" or
  "removal" explicitly.
---

# QuietLine

QuietLine is a semi-automated caseworker: it handles ~80% of the data-broker removal process autonomously and batches the remaining ~20% (CAPTCHAs, phone verification, ID uploads) into a short human queue. It never defeats anti-bot controls, never submits data on behalf of anyone other than the operating user, and logs every action taken.

---

## Safety gates — enforced before anything else

**SAFE-1 — Own-data only (mandatory, refuse if violated)**

Before discovery, form-filling, or any submission:
1. Ask the user to confirm the name, phone number, and identity details belong to **them**.
2. If the request looks like it targets a third party (e.g., "find my ex's number," "look up my neighbor"), **refuse** and explain why. This is a release-blocking safety gate; see `evals/evals.json` eval #5.
3. Record the ownership affirmation (timestamp + session ID) in the case store via `python scripts/case_store.py mark-affirmation`.

**SAFE-2 — No anti-bot circumvention**

Never attempt to solve, bypass, or route around a CAPTCHA, phone-verification step, or equivalent control programmatically. Always route these to the human. See the `references/mechanisms/captcha-handoff.md` playbook.

**SAFE-3 — Truthful requests only**

All opt-out submissions use the user's real, accurate details and lawful statutory language. No impersonation; no fabricated legal claims.

**SAFE-4 — Rate-limited, respectful automation**

Space submissions to behave like a legitimate individual exercising legal rights, not a scraper. Do not hammer sites.

---

## Consent scopes — collect during Phase 0 (FR-0.4)

Obtain explicit consent for each before using:

| Scope | What it enables |
|---|---|
| `inbox-read` | Read connected email to detect confirmation messages |
| `inbox-click` | Open verification links in confirmation emails |
| `inbox-send` | Send deletion request emails from the connected inbox |
| `form-submit` | Submit opt-out forms to third-party broker sites on the user's behalf |
| `dedicated-email` | Use a separate opt-out email address (recommended; protects primary inbox) |

Offer to help set up a dedicated opt-out email address (FR-0.5) so confirmation traffic and any resulting marketing is quarantined from the primary inbox.

---

## Phase 0 — Setup, Consent & Identity Basis

Collect the minimum identity fields brokers match on. Do **not** front-load sensitive fields; collect only what each broker actually requires.

Required minimum:
- Full legal name + common variants (maiden name, former names)
- Phone number(s) to remove
- Current city/state + prior cities/states (up to ~5 years)

Collect only when a specific broker requires it:
- Date of birth
- Street address

Determine jurisdiction (state of residence) once and store it — California residents get the CA DROP path (see `references/jurisdictions/ca-drop.md`); other states get per-state statutory language from `references/jurisdictions/state-statutes.md`.

Store all collected fields in the case store:
```
python scripts/case_store.py init --profile "{...}"
```

---

## Phase 1 — Discovery / Scan

Run discovery to surface which brokers hold a matching listing:

```bash
python scripts/discover.py --name "Full Name" --phone "+15551234567" --state CA
```

This:
1. Queries `data/brokers.json` for all entries with `publicly_searchable: true`
2. Deduplicates by `owner` field (e.g., BeenVerified + PeopleFinders + Intelius all owned by PeopleConnect → one opt-out covers all)
3. Emits a Discovery Report row per broker: `{name, listing_url_if_found, mechanism_type, difficulty, jurisdiction_path}`

For non-publicly-searchable brokers, include them in the scope regardless (opt-out forms are available even without a confirmed listing).

Before acting, verify each broker's opt-out URL is live:
```bash
python scripts/verify_url.py --broker-id whitepages
```
If the stored URL is dead, `verify_url.py` falls back to the broker's homepage and scans for a "Privacy" or "Do Not Sell My Info" footer link.

**Present the Discovery Report and get user go-ahead before any submissions.**

---

## Phase 2 — Opt-Out Execution

Route each broker to the correct mechanism playbook. Read only the relevant file.

### Mechanism routing

| Mechanism | Reference file | Automation level |
|---|---|---|
| Web form, no CAPTCHA | `references/mechanisms/web-form.md` | Full (browsing agent) |
| Web form + CAPTCHA | `references/mechanisms/captcha-handoff.md` | Partial — agent fills, human solves |
| Email request | `references/mechanisms/email-request.md` | Full (draft + send with consent) |
| Requires account creation | `references/mechanisms/account-required.md` | Partial — agent sets up, user completes |
| Phone verification | `references/mechanisms/phone-verification.md` | Human — agent prepares, user calls/texts |
| ID upload | `references/mechanisms/id-upload.md` | Human-gated — user redacts + uploads; never auto-submitted |
| CA DROP (one-shot) | `references/jurisdictions/ca-drop.md` | Assisted — single request covers all CA-registered brokers |

**Batching rule (FR-2.2):** Collect all human-required steps (CAPTCHAs, phone verification, ID uploads) into a single queue. Do not interrupt the user per-site. After the automated wave completes, render the queue deterministically rather than hand-assembling it:

```bash
python scripts/action_queue.py
```

This selects every `awaiting-user` case (plus any non-terminal case still carrying a `human_action_required`), collapses owner-group members into one item, and orders/numbers the queue for you. Use `--limit N` to batch a long queue and `--output json` for a machine-readable form.

**Consolidated owners (FR-2.3):** When a broker is part of a network with a single centralized opt-out (e.g., PeopleConnect covers BeenVerified, PeopleFinders, Intelius, US Search), use the centralized URL rather than filing for each brand individually.

For each submitted case, write to the case store:
```bash
python scripts/case_store.py mark --broker-id <id> --status submitted
```

For email requests, render the deletion email first:
```bash
python scripts/render_email.py --template deletion --broker-id <id> --state CA
```

---

## Phase 3 — Confirmation & Verification Handling

With `inbox-read` + `inbox-click` consent:
- Poll the connected inbox for confirmation emails
- Save each candidate reply to a file, then classify it deterministically rather than judging it by eye:
  ```bash
  python scripts/confirmation.py --email-file <path> --broker-id <id>
  ```
  This never touches the mailbox or the network — it only reasons over the saved copy — and returns one of `confirmation-link`, `already-confirmed`, `rejection`, `unrelated`. See `references/mechanisms/email-request.md`'s "Confirmation loop" for the full routing.
- `confirmation-link`: open `confirmation_url` via the browsing agent (with `inbox-click` consent) to complete the loop.
- `already-confirmed` / `rejection`: apply the suggested status directly with `--apply` (moves the case to `confirmed-removed` or `failed`, respectively) instead of hand-writing `case_store.py mark`.

For steps that need the user, render the numbered queue with the script rather than composing it by hand — it is the deterministic, resumable source of truth:
```bash
python scripts/action_queue.py
```
Present its output to the user as-is (or truncate with `--limit` for batching); it already applies mechanism-rank ordering and owner-group collapse.

Detect and record failures (broken links, dark patterns, ignored requests) with status `failed`:
```bash
python scripts/case_store.py mark --broker-id <id> --status failed --note "opt-out form returns 404"
```

---

## Phase 4 — Tracking & Reporting

Render the status dashboard at any time:
```bash
python scripts/build_dashboard.py
```

Flag brokers past their statutory response deadline:
```bash
python scripts/deadlines.py --flag-overdue
```

For overdue cases, escalate with a firmer deletion demand citing the missed statutory window:
```bash
python scripts/render_email.py --template escalation --broker-id <id> --state CA
```

Valid case statuses: `discovered` → `submitted` → `awaiting-confirmation` → `awaiting-user` → `confirmed-removed` | `failed` | `reappeared`

---

## Phase 5 — Monitoring & Re-Removal

Re-run discovery on the 90-day schedule (or user-triggered) and diff against the last run:
```bash
python scripts/discover.py --name "Full Name" --phone "+15551234567" --state CA
python scripts/diff_scan.py
```

`diff_scan.py` outputs `{still_clear, reappeared, new}`. For reappeared or new brokers, re-enter Phase 2 for those entries only.

Send the user a concise periodic summary:
> X still clear · Y reappeared and re-filed · Z new sites found · 2 need your action

---

## Resume from state

At the start of any session, read the case store to determine where to resume:
```bash
python scripts/case_store.py list
python scripts/build_dashboard.py
```

Do not re-submit any case already at `confirmed-removed` (idempotency guard is enforced in `case_store.py`). For cases at `awaiting-user` or `failed`, surface them immediately so the user can unblock them.

---

## Privacy handling

- Collect only the minimum PII each broker requires (DH-1)
- Log what PII was sent to which broker for the user's audit trail (DH-5)
- Never auto-submit ID documents (DH-4) — user redacts and approves each
- The case store lives in the user's own Cowork storage (DH-3)
- To purge all stored data: `python scripts/case_store.py purge --confirm`

---

## Data files (never load into context)

| File | Purpose |
|---|---|
| `data/brokers.json` | Target list with mechanism metadata — queried by scripts only |
| `data/jurisdictions.json` | Per-state statutory params + CA DROP eligibility |

These are queried by scripts; loading them wholesale into context would exceed the context budget for no benefit.

---

## Limitations (state plainly)

1. **Not fully hands-off.** ~10–20% of opt-outs require the user (CAPTCHA, phone/ID verification).
2. **Reappearance is inherent.** Data commonly returns in 3–6 months — hence Phase 5.
3. **Public records persist.** Voter/property records can be suppressed at the broker layer but not erased at the source.
4. **Broker compliance varies.** Some ignore or slow-walk requests; the skill escalates but cannot force action beyond citing the law.
5. **Coverage is broad but not exhaustive.** The registry targets the highest-risk and most common sites; a proprietary commercial crawler may find additional niche brokers.
