# State Privacy Statutes — Opt-Out Reference

This file provides per-state statutory language, response deadlines, and escalation notes for data-broker deletion requests. SKILL.md reads only the relevant section via the table of contents below.

**Table of contents:**
- [California (CA)](#california-ca)
- [Texas (TX)](#texas-tx)
- [Virginia (VA)](#virginia-va)
- [Colorado (CO)](#colorado-co)
- [Connecticut (CT)](#connecticut-ct)
- [All other states (default)](#all-other-states-default)

All statutory language and deadline parameters are also machine-readable in `data/jurisdictions.json` (source of truth for `deadlines.py` and `render_email.py`). This file is the human-readable companion.

---

## California (CA)

**Statutes:** CCPA (Cal. Civ. Code §1798.100–.199), CPRA, Delete Act (SB 362)
**Response deadline:** 45 days (extendable once to 90 days with notice)
**CA DROP eligible:** Yes — see `references/jurisdictions/ca-drop.md`

**Deletion language:**
> Pursuant to the California Consumer Privacy Act (Cal. Civ. Code §1798.105), I request that you delete all personal information you have collected about me, including but not limited to my name, phone number(s), addresses, and any associated records. Please confirm deletion within 45 business days.

**Escalation language (post-deadline):**
> This is a follow-up to my deletion request submitted on {submitted_date}. Under the CCPA, you are required to respond within 45 business days. That deadline has passed. Please confirm immediate deletion or I will file a complaint with the California Attorney General's office.

**Enforcement:** California Privacy Protection Agency (CPPA) — https://cppa.ca.gov/enforcement/

---

## Texas (TX)

**Statutes:** Texas Data Privacy and Security Act (TDPSA, S.B. 2, effective July 1, 2024)
**Response deadline:** 45 days
**CA DROP eligible:** No

**Deletion language:**
> Pursuant to the Texas Data Privacy and Security Act (Tex. Bus. & Com. Code Ch. 541), I request that you delete all personal information you have collected about me, including my name, phone number(s), and associated records. Please confirm deletion within 45 days.

**Escalation language:**
> This is a follow-up to my deletion request submitted on {submitted_date}. Under the Texas Data Privacy and Security Act, you are required to respond within 45 days. That deadline has passed. Please confirm immediate deletion.

**Enforcement:** Texas Attorney General — https://www.texasattorneygeneral.gov

---

## Virginia (VA)

**Statutes:** Virginia Consumer Data Protection Act (VCDPA, Va. Code Ann. §59.1-575 et seq., effective January 1, 2023)
**Response deadline:** 45 days
**CA DROP eligible:** No

**Deletion language:**
> Pursuant to the Virginia Consumer Data Protection Act (Va. Code Ann. §59.1-575 et seq.), I request that you delete all personal information you have collected about me. Please confirm deletion within 45 days.

**Escalation language:**
> This is a follow-up to my deletion request submitted on {submitted_date}. Under the VCDPA, you are required to respond within 45 days. That deadline has passed. Please confirm immediate deletion.

**Enforcement:** Virginia Attorney General — https://www.oag.state.va.us

---

## Colorado (CO)

**Statutes:** Colorado Privacy Act (CPA, C.R.S. §6-1-1301 et seq., effective July 1, 2023)
**Response deadline:** 45 days
**CA DROP eligible:** No

**Deletion language:**
> Pursuant to the Colorado Privacy Act (C.R.S. §6-1-1301 et seq.), I request that you delete all personal information you have collected about me. Please confirm deletion within 45 days.

**Escalation language:**
> This is a follow-up to my deletion request submitted on {submitted_date}. Under the Colorado Privacy Act, you are required to respond within 45 days. That deadline has passed. Please confirm immediate deletion.

**Enforcement:** Colorado Attorney General — https://coag.gov

---

## Connecticut (CT)

**Statutes:** Connecticut Data Privacy Act (CTDPA, Conn. Gen. Stat. §42-515 et seq., effective July 1, 2023)
**Response deadline:** 45 days
**CA DROP eligible:** No

**Deletion language:**
> Pursuant to the Connecticut Data Privacy Act (Conn. Gen. Stat. §42-515 et seq.), I request that you delete all personal information you have collected about me. Please confirm deletion within 45 days.

**Escalation language:**
> This is a follow-up to my deletion request submitted on {submitted_date}. Under the CTDPA, you are required to respond within 45 days. That deadline has passed. Please confirm immediate deletion.

**Enforcement:** Connecticut Attorney General — https://portal.ct.gov/AG

---

## All other states (default)

**Statutes:** FCRA (15 U.S.C. §1681 et seq.) applies to certain data uses; no comprehensive federal deletion right exists as of 2026.
**Response deadline:** 45 days (industry norm; brokers' own privacy policies typically state 30–45 days)
**CA DROP eligible:** No

**Opt-out language (no specific statute):**
> I am requesting the removal of my personal information from your database, including my name, phone number(s), and associated records. Please process this opt-out request and confirm removal within 30 days.

**Escalation language:**
> This is a follow-up to my removal request submitted on {submitted_date}. I have not received a confirmation of removal. Please process this request immediately and confirm.

**Notes:**
- Without a state privacy statute, deletion requests are honored on a voluntary basis or pursuant to the broker's own privacy policy.
- Cite the broker's own stated privacy policy timeframe when escalating.
- If the user's state enacts a new privacy law, add it to `data/jurisdictions.json` and add a section here with the statutory details.

---

*Source of truth for machine use: `data/jurisdictions.json`. This file is for agent reference and human inspection only.*
