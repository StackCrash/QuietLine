# Jurisdiction: California — DROP (Data Removal One-stop Portal)

## Overview

California's **Data Removal One-stop Portal (DROP)** is a free service operated by the California Privacy Protection Agency (CPPA) that lets CA residents submit a single deletion request that covers all data brokers **registered with the CPPA**. This is more efficient than filing individual opt-outs for each CA-registered broker.

**File CA DROP first**, then handle non-CA-registered brokers individually using standard opt-out paths.

## Eligibility

- User must be a **California resident**.
- DROP covers only brokers that are required to register with the CPPA under the Delete Act (SB 362, effective January 1, 2026 for large brokers).
- **Non-registered brokers** (smaller operations, out-of-state brokers that don't meet the CA registration threshold) are **not covered** by DROP — handle these individually.

## How to check CA-registered status

In `data/brokers.json`, check `ca_registered: true`. Brokers flagged `ca_registered: true` are eligible for the DROP path.

The CPPA maintains the authoritative registry at: `https://oag.ca.gov/privacy/databrokerslist`

## Agent steps for the CA DROP path

1. **Confirm CA residency** (already collected in Phase 0 via `state: "CA"`).
2. **Identify all CA-registered brokers** in the discovery report (`ca_registered: true` AND `jurisdiction_path: "ca-drop"`).
3. **Navigate to the DROP portal.** As of 2026, the portal is being rolled out by the CPPA — check the current URL in `data/jurisdictions.json` under `jurisdictions.CA.drop_url`.
4. **Submit a single deletion request** through DROP covering all CA-registered brokers. The portal handles routing to each broker.
5. **Mark all CA-registered broker cases** as `submitted` with `mechanism: "ca-drop"`:
   ```
   for broker_id in ca_registered_brokers:
       case_store.mark --broker-id <broker_id> --status submitted
   ```
6. **Monitor** for the 45-day response window per CA law.

## Statutory framework

| Law | Provision |
|---|---|
| CCPA (Cal. Civ. Code §1798.105) | Right to deletion; 45-day response window |
| CPRA | Extended deletion rights; CPPA enforcement |
| Delete Act (SB 362) | Requires large brokers to register with CPPA; enables DROP |

**Statutory language for deletion requests (CA):**
> Pursuant to the California Consumer Privacy Act (Cal. Civ. Code §1798.105), I request that you delete all personal information you have collected about me, including but not limited to my name, phone number(s), addresses, and any associated records. Please confirm deletion within 45 business days.

## After DROP submission

- Check the case store in 45 days: `python scripts/deadlines.py --flag-overdue`
- For CA-registered brokers that haven't responded, send the CA escalation email via `render_email.py --template escalation --state CA`.
- Brokers that miss the 45-day window may be reported to the CPPA at: https://cppa.ca.gov/enforcement/

## What DROP does NOT cover

- Brokers not registered with the CPPA (handle individually).
- Non-California brokers (handle individually with generic or state-specific opt-out path).
- Public records (voter rolls, property records) — these cannot be removed via DROP.
- Breached data that has already spread — DROP removes active listings but doesn't recall distributed copies.
