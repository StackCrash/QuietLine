# QuietLine

**A Claude Cowork skill that finds where your phone number is exposed across data brokers and people-search sites, drives opt-out and deletion requests to completion, and re-checks on a schedule.**

Spam and solicitation calls are mostly a data-exposure problem. Your number leaks into an ecosystem of data brokers and people-search sites, gets bought and resold, and that fuels robocalls, phishing, and telemarketing. QuietLine reproduces the data-removal outcome inside Claude Cowork using tools you already have: the browsing agent to find and fill opt-out forms, your email connector to clear confirmation loops, web search to discover exposures, and your own legal opt-out rights — which are free by law.

> [!NOTE]
> QuietLine operates exclusively on **your own** data. It will not run discovery or submit requests on behalf of a third party. The ownership affirmation at setup is the gate.

---

## What QuietLine does

| Phase | What happens |
|---|---|
| **0 — Setup** | Collect identity fields (name, phone, location), confirm ownership, obtain consent scopes, optionally set up a dedicated opt-out email |
| **1 — Discovery** | Search the open web + broker registry to surface which sites hold your number; produce a ranked Discovery Report |
| **2 — Opt-out execution** | Submit deletion requests via the correct mechanism per broker; batch human-required steps into a single short queue |
| **3 — Confirmation** | Auto-clear confirmation email loops (with inbox consent); generate a numbered human action queue for CAPTCHAs and phone verification |
| **4 — Tracking** | Maintain a durable case record per broker; render a status dashboard on request; flag missed statutory deadlines |
| **5 — Monitoring** | Re-scan every 90 days; diff against the last run; re-file reappeared listings; send a concise periodic summary |

### Opt-out mechanisms supported

| Mechanism | Automation level |
|---|---|
| Web form, no CAPTCHA | Full (browsing agent) |
| Web form + CAPTCHA | Partial — agent fills everything, you solve + submit |
| Email request | Full — drafted and sent with consent |
| Account required | Partial — agent sets up flow, you complete account creation |
| Phone verification | Human — agent prepares, you complete the call/text |
| ID upload | Human-gated — you redact and approve each upload; never auto-submitted |
| CA DROP (one-shot) | Assisted — single deletion request to all CA-registered brokers |

CAPTCHA sites are pre-filled and held open in their own browser tabs, then handed to you as one batch solve-session checklist (`action_queue.py --session`) instead of one interruption per site; a single `mark-batch` call records however many you finish in one pass.

---

## How it compares to doing it manually

Manual opt-outs are legitimate but brutal. Industry estimates put a thorough first pass at 40–300+ hours across hundreds of sites, each with its own flow. And data reappears within 3–6 months, so it's recurring maintenance, not a one-time chore. QuietLine does the tedious parts — the discovery, drafting, form-filling, tracking, and follow-up — and batches the remaining ~20% of steps that genuinely require a human into a short, focused queue.

---

## Honest limitations

QuietLine is designed to be transparent about what it cannot do:

- **Not fully hands-off.** ~10–20% of opt-outs require you (CAPTCHAs, phone verification, ID uploads).
- **Coverage is broad, not exhaustive.** The registry targets the highest-risk and most common sites; a proprietary commercial crawler may find additional niche brokers.
- **Reappearance is inherent.** Data commonly returns in 3–6 months — hence Phase 5.
- **Public records persist.** Voter rolls and property records can be suppressed at the broker layer but cannot be erased at the source.
- **Doesn't stop the last-hop call.** QuietLine reduces the *supply* of your number; blocking an individual incoming call is a device/carrier function.
- **Broker compliance varies.** Some slow-walk or ignore requests. QuietLine escalates with statutory language but cannot force action beyond citing the law.

---

## Data sources

The broker registry (`data/brokers.json`) currently curates 51 brokers, assembled from free, public sources and periodically refreshed. `scripts/validate_registry.py validate` checks the registry against its schema shape (required fields, enums, owner-group consistency) and reports the current count.

- **State data-broker registries** — California CPPA, Vermont, Texas, Oregon (authoritative, broker-declared lists)
- **Privacy Rights Clearinghouse** consolidated database (750+ brokers)
- **Community opt-out guides** (per-site opt-out URLs and mechanism notes)
- **California DROP** — as a first-class action path for CA residents (single request covers all CA-registered brokers)

Each registry entry stores: `name`, `owner/parent`, `opt_out_url`, `mechanism_type`, `publicly_searchable`, `jurisdiction_notes`, `difficulty`, `reappearance_tendency`, `last_verified`. Opt-out URLs are re-verified before use; `verify_url.py` falls back to the broker's homepage "Privacy / Do Not Sell My Info" footer link when a stored URL is stale.

---

## Privacy and data handling

QuietLine handles sensitive personal data as a core input — these aren't footnotes.

- Collects the **minimum** each broker requires; never submits DOB/address to a broker that doesn't need it.
- Prefers a **dedicated opt-out email address** so confirmation traffic is quarantined from your primary inbox.
- Stores your identity profile and case records in **your own Cowork storage** — not transmitted anywhere except the specific broker forms required to complete a request.
- ID documents are **never auto-submitted**; you redact and approve each upload individually.
- Logs what PII was sent to which broker so you have a complete audit trail.
- Supports a `purge` command that deletes the stored identity profile and case history on request.

---

## Safety

| Rule | Enforcement |
|---|---|
| **Own-data only** | Script-enforced (SAFE-1): `discover.py` exits 3 unless ownership is affirmed (`--affirm-ownership` or `case_store.py mark-affirmation`); submissions are blocked without an affirmed case store |
| **No anti-bot circumvention** | CAPTCHAs and equivalent controls are always routed to you via the batch solve-session queue (see `references/mechanisms/captcha-handoff.md`), never solved by the agent |
| **Truthful requests only** | Submissions use your real, accurate details and lawful statutory language |
| **Respectful automation** | Submissions are paced and rate-limited; no site hammering |
| **Verified navigation only** | Agent-followed (SAFE-4): `url_guard.py` checks the current page's domain scope before any PII entry or submit, and before opening a confirmation-email link; out-of-scope redirects are recorded to the case store as failed, not followed |
| **No guarantees** | Results require ongoing maintenance; the skill states this plainly |
| **Full transparency** | Every action taken on your behalf is logged and reviewable |

---

## Architecture

QuietLine is implemented as a Cowork skill following Anthropic's skill-authoring conventions.

```
quietline/
├── SKILL.md                      # Lean orchestrator — phases, routing, safety gates (< 500 lines)
├── scripts/                      # Deterministic logic — executed, not reasoned
│   ├── discover.py               #   Query registry, check searchable brokers, dedupe by parent owner (SAFE-1 gated)
│   ├── verify_url.py             #   Confirm an opt-out URL is live; fall back to homepage privacy footer
│   ├── url_guard.py              #   SAFE-4 domain-scope verdict for any URL before PII entry
│   ├── case_store.py             #   Read/write/update case records; idempotency guard (no double-submits)
│   ├── diff_scan.py              #   Diff a re-scan vs last run → {still-clear, reappeared, new}
│   ├── deadlines.py              #   Compute statutory response deadlines; flag overdue for escalation
│   ├── render_email.py           #   Fill a jurisdiction-parameterized email from templates
│   ├── build_dashboard.py        #   Render the status dashboard from the case store
│   ├── action_queue.py           #   Numbered human-action queue; collapses cases by owner group
│   ├── confirmation.py           #   Classify a saved confirmation email (link/confirmed/rejected/unrelated)
│   ├── rescan.py                 #   Phase-5 orchestration: discover + diff + optional apply in one run
│   ├── validate_registry.py      #   Validate/merge broker registry fragments against the schema shape
│   └── package_skill.py          #   Build the distributable quietline.skill zip (used by release.yml)
├── references/                   # Loaded on demand (progressive disclosure)
│   ├── mechanisms/               #   One playbook per opt-out mechanism
│   │   ├── web-form.md
│   │   ├── email-request.md
│   │   ├── account-required.md
│   │   ├── phone-verification.md
│   │   ├── id-upload.md
│   │   └── captcha-handoff.md
│   └── jurisdictions/            #   One path per jurisdiction; read only the user's
│       ├── ca-drop.md            #     California DROP one-shot path
│       └── state-statutes.md     #     Per-state statutory language + deadlines
├── assets/templates/             # Email and dashboard templates
├── data/                         # Large data — queried by scripts, NOT loaded into context
│   ├── brokers.json              #   Target list with mechanism metadata
│   └── jurisdictions.json        #   Per-state statutory params + DROP eligibility
├── schemas/
│   └── case_record.json          #   Status-tracking schema for durable storage
└── evals/
    ├── evals.json                #   Test cases including the SAFE-1 release-blocking safety eval
    ├── fixtures/                  #   Sample confirmation emails/HTML used by the classifier + link-extraction evals
    └── run_evals.py               #   Eval runner (11/11 must pass)
```

### Design principles

**Progressive disclosure.** `SKILL.md` stays under 500 lines as a lean orchestrator. Per-broker detail lives in `data/brokers.json` (queried by scripts, never loaded wholesale); per-mechanism playbooks and jurisdiction paths live in `references/` and are loaded only when needed.

**Scripts for anything deterministic.** Diffing scans, computing statutory deadlines, deduplicating brokers by parent owner, rendering templated emails, and reading/writing the case store are all deterministic — so they live in `scripts/` and are executed, not re-reasoned on each run. This makes the workflow resumable, idempotent, and auditable.

**Domain-organized references.** Each opt-out mechanism and each jurisdiction path gets its own file; `SKILL.md` reads only the relevant one for the current broker/user.

---

## Non-functional guarantees

These are backed by scripts, not agent reasoning, so they hold across sessions:

| Guarantee | Mechanism |
|---|---|
| **Resumable** | Any run can be interrupted and resumed from the last persisted state |
| **Idempotent** | Re-running never double-submits an already-confirmed removal |
| **Transparent** | `build_dashboard.py` always answers "what have you done / what's pending / what failed" |
| **Graceful degradation** | Broken URLs and unavailable tools are logged as `failed`; the run continues |
| **Portable jurisdiction logic** | Adding a new state's statute is a data change, not a rewrite of `SKILL.md` |

---

## Getting started

### Run the eval suite

```bash
python3 evals/run_evals.py
```

All 11 evals must pass. Eval #5 (SAFE-1: own-data only) is a release-blocking gate — a non-zero exit here means the ownership gate has regressed.

```bash
# Verbose output with per-assertion detail
python3 evals/run_evals.py --verbose

# Run a single eval
python3 evals/run_evals.py --eval-id eval-05-safety-own-data-only
```

### Run the scripts directly

> [!NOTE]
> `discover.py` enforces SAFE-1: it exits 3 unless ownership is affirmed. Either pass
> `--affirm-ownership` on the command, or run `case_store.py mark-affirmation` once up
> front so subsequent commands (including submissions) pass the gate.

```bash
# Affirm ownership once (or pass --affirm-ownership on discover.py directly)
python3 scripts/case_store.py mark-affirmation

# Discovery — see which brokers may hold your number
python3 scripts/discover.py --name "Your Name" --phone "+15551234567" --state CA \
  --affirm-ownership --output table

# Verify a broker's opt-out URL is live
python3 scripts/verify_url.py --broker-id whitepages --dry-run

# Case store — initialize, mark status, dashboard
python3 scripts/case_store.py init --name "Your Name" --phone "+15551234567" --state CA
python3 scripts/case_store.py list
python3 scripts/build_dashboard.py

# Check for overdue cases
python3 scripts/deadlines.py --flag-overdue

# Render a deletion email (preview before sending)
python3 scripts/render_email.py --template deletion --broker-id radaris \
  --state CA --name "Your Name" --phone "+15551234567" --email "optout@yourdomain.com"

# Render the numbered human-action queue (CAPTCHAs, phone verification, etc.)
python3 scripts/action_queue.py --output markdown

# Phase-5 re-scan: discover + diff against the case store in one pass
python3 scripts/rescan.py --dry-run          # read-only preview
python3 scripts/rescan.py --apply            # refile reappeared, register new, record scan dates
```

### Verify static correctness

```bash
# Validate all JSON files
for f in data/*.json schemas/*.json evals/evals.json; do
  python3 -m json.tool "$f" > /dev/null && echo "OK: $f" || echo "FAIL: $f"
done

# Check SKILL.md stays under 500 lines
wc -l SKILL.md
```

### Tools QuietLine relies on (all in Cowork)

| Tool | Used for |
|---|---|
| Claude in Chrome | Locate listings, fill and submit forms, complete confirmation clicks |
| Email connector | Read/clear confirmation loops; send deletion emails (with consent) |
| Web search | Discovery and exposure enrichment |
| File/persistent storage | Case database and identity profile across sessions |
| Scheduling | Trigger 90-day re-scans and deadline follow-ups |

---

## Jurisdiction support

| State | Statute | Deadline | DROP eligible |
|---|---|---|---|
| California | CCPA / CPRA / Delete Act | 45 days | ✓ |
| Texas | TDPSA (eff. July 2024) | 45 days | — |
| Virginia | VCDPA | 45 days | — |
| Colorado | CPA | 45 days | — |
| Connecticut | CTDPA | 45 days | — |
| Oregon | OCPA (ORS 646A.570-646A.589) | 45 days | — |
| New Jersey | NJDPA (P.L. 2023, c.266) | 45 days | — |
| All others | FCRA + broker privacy policy | 45 days (norm) | — |

To add a new state: add an entry to `data/jurisdictions.json` and a corresponding section to `references/jurisdictions/state-statutes.md`.

---

## Roadmap

All milestones below have shipped as of v0.2.0.

| Milestone | Scope | Status |
|---|---|---|
| **M0 — Manual-assist MVP** | Discovery + Discovery Report + drafted deletion emails + tracked action queue | ✓ shipped |
| **M1 — Automated form-filling** | Claude-in-Chrome form submission for no-CAPTCHA sites; human-step batching | ✓ shipped |
| **M2 — Confirmation automation** | Consented inbox handling to auto-clear email loops | ✓ shipped |
| **M3 — Monitoring loop** | Scheduled 90-day re-scans, diffing, deadline-based escalation | ✓ shipped |
| **M4 — Jurisdiction expansion** | CA DROP one-shot path; per-state statutory templates; consolidated-owner shortcuts | ✓ shipped |
| **M5 — Harden & package** | Full eval suite (SAFE-1 must pass 100%); package `.skill` for one-click install | ✓ shipped |

---

## Open questions

- **Registry freshness:** who/what keeps `brokers.json` current as opt-out URLs churn? (Mitigation: link re-verification + homepage-footer fallback; periodic registry refresh.)
- **Inbox permission scope:** how narrowly can email access be scoped to opt-out confirmation mail only?
- **Terms-of-service tension:** some brokers' ToS discourage automated interaction. QuietLine's stance is "act as a legitimate individual exercising legal rights, at human pace, never defeating controls" — legal review recommended before broad deployment.
- **DROP dependence:** the CA one-shot path is powerful but covers only CA-registered brokers; individual opt-outs remain the backbone.
- **Verification friction UX:** the batching/queue design is the make-or-break detail for the ~20% human-step experience.

---

## Success metrics

- **Coverage:** # of brokers discovered vs. # in the registry that plausibly hold your data
- **Completion rate:** % of discovered listings reaching `confirmed-removed`
- **Human-effort ratio:** median user minutes per run (target: single-digit minutes after setup)
- **Durability:** % of removals still clear at the next 90-day re-scan
- **Outcome proxy:** user-reported change in spam/solicitation call volume over 60–90 days
- **Trust:** zero incidents of submitting on non-owned data; zero anti-bot circumvention attempts
