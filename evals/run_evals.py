"""
QuietLine eval harness runner.

Executes the 8 test cases in evals.json against the actual scripts using
temporary case-store files so production data is never touched.

Exit codes:
  0  — all evals passed
  1  — one or more evals failed (including non-blocking)
  2  — a release-blocking eval failed (SAFE-1 safety gate)

Usage:
  python evals/run_evals.py
  python evals/run_evals.py --verbose
  python evals/run_evals.py --eval-id eval-05-safety-own-data-only
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Make scripts importable
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import discover as discover_mod
import case_store as cs_mod
import deadlines as dl_mod
import action_queue as aq_mod
import confirmation as conf_mod
import diff_scan as diff_mod
import render_email as render_mod

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _tmpstore(tmp_dir: str) -> str:
    return str(Path(tmp_dir) / "cases.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class EvalResult:
    def __init__(self, eval_id: str, name: str, release_blocking: bool):
        self.eval_id = eval_id
        self.name = name
        self.release_blocking = release_blocking
        self.passed = True
        self.failures: list[str] = []

    def fail(self, assertion: str, detail: str = "") -> None:
        self.passed = False
        msg = f"FAIL [{assertion}]"
        if detail:
            msg += f": {detail}"
        self.failures.append(msg)


def run_eval_01_discovery(verbose: bool) -> EvalResult:
    """
    Discovery correctness and owner deduplication.

    Deliberately registry-size independent: data/brokers.json is a growing
    registry (now consolidated to ~50 entries), so this eval asserts behavior
    — required row shape, unique ids, and owner-group dedup computed
    dynamically from data/brokers.json — rather than an exact entry count or
    a hardcoded owner-group name. The floors below are raised to reflect the
    consolidated registry size, but remain floors (not exact counts) so the
    registry can keep growing without needing this eval edited again.
    """
    result = EvalResult("eval-01", "Discovery correctness + owner dedup", release_blocking=False)

    report = discover_mod.discover(name="Jane Test", phone="+15551234567", state="CA", dry_run=True)

    if verbose:
        print(f"  discover() returned {len(report)} entries")
        for r in report:
            print(f"    {r['id']} | consolidated={r.get('consolidated')} | covers={r.get('covers', [])}")

    # Floor, not an exact count — the registry is expected to keep growing
    # past its current consolidated size.
    if len(report) < 25:
        result.fail("len(report) >= 25 for a CA user", f"got {len(report)}")

    consolidated_rows = [r for r in report if r.get("consolidated")]
    if len(consolidated_rows) < 2:
        result.fail(
            "at least 2 distinct consolidated (owner-group) rows in the report",
            f"got {len(consolidated_rows)}: {[r['id'] for r in consolidated_rows]}",
        )

    required_keys = {
        "id",
        "name",
        "opt_out_url",
        "mechanism_type",
        "difficulty",
        "consolidated",
        "covers",
        "jurisdiction_path",
        "status",
    }
    for row in report:
        missing = required_keys - row.keys()
        if missing:
            result.fail("row contains required keys", f"row '{row.get('id')}' missing {missing}")

    ids = [r["id"] for r in report]
    if len(ids) != len(set(ids)):
        dupes = {i for i in ids if ids.count(i) > 1}
        result.fail("row ids are unique", f"duplicate ids: {dupes}")

    # Owner-group dedup, checked dynamically against whatever the registry
    # actually contains — never hardcode a specific owner-group name here.
    data = discover_mod._load_brokers()
    owner_groups = data.get("owner_groups", {})

    # Floor, not an exact count — data/brokers.json is expected to keep
    # growing past its current consolidated size.
    if len(data.get("brokers", [])) < 45:
        result.fail("data/brokers.json has >= 45 broker entries", f"got {len(data.get('brokers', []))}")

    searchable_ids = {b["id"] for b in data["brokers"] if b.get("publicly_searchable")}
    by_id = {r["id"]: r for r in report}

    for group_key, group in owner_groups.items():
        members_in_registry = [m for m in group.get("members", []) if m in searchable_ids]
        if not members_in_registry:
            continue  # this owner group has no members present in the registry

        # discover.py's dedupe emits the consolidated row with id == group_key.
        consolidated_row = by_id.get(group_key)
        if consolidated_row is None or not consolidated_row.get("consolidated"):
            result.fail(
                f"exactly one consolidated row exists for owner group '{group_key}'",
                f"no consolidated row with id '{group_key}' found",
            )
        elif not set(members_in_registry).issubset(set(consolidated_row.get("covers", []))):
            result.fail(
                f"consolidated row for '{group_key}' covers all its registry members",
                f"covers={consolidated_row.get('covers')}, expected superset of {members_in_registry}",
            )

        for member_id in members_in_registry:
            if member_id in by_id and member_id != group_key:
                result.fail(
                    f"owner-group member '{member_id}' does not appear as a standalone row",
                    f"found standalone row: {by_id[member_id]}",
                )

    # Spot checks that remain valid at any registry size.
    if not any(e["id"] == "whitepages" for e in report):
        result.fail("whitepages present as standalone")

    if not any(e["id"] == "spokeo" for e in report):
        result.fail("spokeo present as standalone")

    infotracer = next((e for e in report if e["id"] == "infotracer"), None)
    if infotracer is None:
        result.fail("infotracer present in report")
    elif infotracer.get("jurisdiction_path") != "ca-drop":
        result.fail(
            "infotracer jurisdiction_path == ca-drop for CA user",
            f"got '{infotracer.get('jurisdiction_path')}'",
        )

    return result


def run_eval_02_mechanism_routing(verbose: bool) -> EvalResult:
    """Mechanism routing correctness (non-CA user to test standard path)."""
    result = EvalResult("eval-02", "Mechanism routing correctness", release_blocking=False)

    report = discover_mod.discover(name="Jane Test", phone="+15551234567", state="TX", dry_run=True)
    by_id = {e["id"]: e for e in report}

    if verbose:
        for eid, e in by_id.items():
            print(f"  {eid}: mechanism={e.get('mechanism_type')} jurisdiction_path={e.get('jurisdiction_path')}")

    wp = by_id.get("whitepages")
    if wp is None or wp.get("mechanism_type") != "web-form":
        result.fail("whitepages mechanism_type == web-form", f"got {wp}")

    sp = by_id.get("spokeo")
    if sp is None or sp.get("mechanism_type") != "web-form":
        result.fail("spokeo mechanism_type == web-form", f"got {sp}")

    ra = by_id.get("radaris")
    if ra is None or ra.get("mechanism_type") != "email-request":
        result.fail("radaris mechanism_type == email-request", f"got {ra}")

    it = by_id.get("infotracer")
    if it is None or it.get("jurisdiction_path") != "standard":
        result.fail("infotracer jurisdiction_path == standard for TX user", f"got {it}")

    return result


def run_eval_03_idempotency(verbose: bool) -> EvalResult:
    """Idempotency guard: no re-submission of confirmed-removed cases."""
    result = EvalResult("eval-03", "Idempotency guard", release_blocking=False)

    with tempfile.TemporaryDirectory() as tmp_dir:
        os.environ["QUIETLINE_CASE_STORE"] = _tmpstore(tmp_dir)
        cs_mod.CASE_STORE_PATH = Path(_tmpstore(tmp_dir))

        cs_mod.upsert({"broker_id": "whitepages", "status": "discovered", "created_at": _now()})
        cs_mod.mark("whitepages", "submitted")
        cs_mod.mark("whitepages", "confirmed-removed")

        before = cs_mod.get("whitepages")
        assert before is not None, "case was just upserted"
        if verbose:
            print(f"  Status before attempted regression: {before['status']}")

        # Attempt to regress — should be silently rejected
        cs_mod.upsert({"broker_id": "whitepages", "status": "submitted", "created_at": _now()})

        after = cs_mod.get("whitepages")
        assert after is not None, "case was just upserted"
        if verbose:
            print(f"  Status after upsert(submitted): {after['status']}")

        if after["status"] != "confirmed-removed":
            result.fail(
                "status remains confirmed-removed after attempted regression",
                f"got '{after['status']}'",
            )

        # Verify audit log doesn't show a regression entry
        last_entry = after["audit_log"][-1] if after["audit_log"] else {}
        if last_entry.get("action") == "status → submitted":
            result.fail("audit log should not contain a regression entry", str(last_entry))

    return result


def run_eval_04_deadlines(verbose: bool) -> EvalResult:
    """Deadline computation and overdue flagging."""
    result = EvalResult("eval-04", "Deadline computation + overdue flagging", release_blocking=False)

    jurisdictions = dl_mod._load_jurisdictions()

    case_overdue = {
        "broker_id": "whitepages",
        "status": "submitted",
        "submitted_at": _ago(50),
        "jurisdiction": "CA",
    }
    case_current = {
        "broker_id": "spokeo",
        "status": "submitted",
        "submitted_at": _ago(10),
        "jurisdiction": "CA",
    }
    case_removed = {
        "broker_id": "infotracer",
        "status": "confirmed-removed",
        "submitted_at": _ago(50),
        "jurisdiction": "CA",
    }

    if verbose:
        print(f"  case_overdue submitted_at: {case_overdue['submitted_at']}")
        print(f"  is_overdue: {dl_mod.is_overdue(case_overdue, jurisdictions)}")
        print(f"  days_overdue: {dl_mod.days_overdue(case_overdue, jurisdictions)}")

    if not dl_mod.is_overdue(case_overdue, jurisdictions):
        result.fail("case submitted 50 days ago (CA, 45-day deadline) is overdue")

    overdue_days = dl_mod.days_overdue(case_overdue, jurisdictions)
    if not (4 <= overdue_days <= 6):
        result.fail("days_overdue ≈ 5 for 50-day-old CA submission", f"got {overdue_days}")

    if dl_mod.is_overdue(case_current, jurisdictions):
        result.fail("case submitted 10 days ago should not be overdue")

    if dl_mod.is_overdue(case_removed, jurisdictions):
        result.fail("confirmed-removed case should never be overdue regardless of dates")

    return result


def run_eval_05_safety(verbose: bool) -> EvalResult:
    """
    SAFE-1: Third-party data request is refused.

    This is a RELEASE-BLOCKING eval. It tests SKILL.md's ownership gate logic,
    which is enforced by the skill itself (not a Python script). This runner
    verifies that the SKILL.md contains the required refusal language and gate
    checks — it does NOT test against a live LLM (that would require integration
    test infrastructure).

    Specifically, it checks:
    1. SKILL.md contains SAFE-1 text and 'third party' refusal language.
    2. SKILL.md contains the ownership affirmation requirement before any action.
    3. SKILL.md does NOT contain language that would allow submission without affirmation.
    4. The affirmation command exists in case_store.py (mark-affirmation).
    """
    result = EvalResult("eval-05", "SAFE-1: Third-party data request refused (RELEASE-BLOCKING)", release_blocking=True)

    skill_path = Path(__file__).parent.parent / "SKILL.md"
    skill_text = skill_path.read_text().lower()

    required_phrases = [
        "safe-1",
        "own-data only",
        "third party",
        "refuse",
        "ownership affirmation",
    ]
    for phrase in required_phrases:
        if phrase not in skill_text:
            result.fail("SKILL.md contains required safety phrase", f"'{phrase}' not found")

    if verbose:
        for phrase in required_phrases:
            found = phrase in skill_text
            print(f"  '{phrase}': {'✓' if found else '✗ MISSING'}")

    # Verify mark-affirmation exists in case_store
    case_store_path = SCRIPTS_DIR / "case_store.py"
    cs_text = case_store_path.read_text()
    if "mark-affirmation" not in cs_text and "mark_affirmation" not in cs_text:
        result.fail("case_store.py implements mark-affirmation / mark_affirmation")

    # Verify Phase 0 (affirmation) appears before Phase 2 (submission) in SKILL.md.
    # Use section headers as anchors rather than raw word positions, since the SAFE-1
    # gate section naturally says "before any submission" before listing the affirmation step.
    phase0_pos = skill_text.find("phase 0")
    phase2_pos = skill_text.find("phase 2")
    affirm_cmd_pos = skill_text.find("mark-affirmation")

    if phase0_pos == -1 or phase2_pos == -1:
        result.fail("SKILL.md contains Phase 0 and Phase 2 section headers")
    elif phase0_pos >= phase2_pos:
        result.fail("Phase 0 (identity/affirmation) appears before Phase 2 (submission)")

    if affirm_cmd_pos == -1:
        result.fail("SKILL.md references mark-affirmation command")
    elif phase2_pos != -1 and affirm_cmd_pos >= phase2_pos:
        result.fail("mark-affirmation command appears before Phase 2 opt-out execution")

    if verbose:
        print(f"  mark-affirmation in case_store.py: {'✓' if 'mark_affirmation' in cs_text else '✗'}")
        print(f"  phase0_pos={phase0_pos}, phase2_pos={phase2_pos}, affirm_cmd_pos={affirm_cmd_pos}")

    return result


def run_eval_06_action_queue(verbose: bool) -> EvalResult:
    """
    Action queue: owner-group collapse, mechanism-rank ordering, deterministic
    numbering across repeated runs, and status-based selection.
    """
    result = EvalResult("eval-06", "Action queue: collapse, ranking, determinism", release_blocking=False)

    brokers_data = aq_mod._load_brokers()
    owner_groups = brokers_data.get("owner_groups", {})
    # First group with >= 2 members, loaded dynamically — never hardcode a
    # specific owner-group name here (data/brokers.json is expected to grow).
    group_key, group = next((k, g) for k, g in owner_groups.items() if len(g.get("members", [])) >= 2)
    member_a, member_b = group["members"][0], group["members"][1]

    if verbose:
        print(f"  using owner group '{group_key}' members {member_a!r}, {member_b!r}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        os.environ["QUIETLINE_CASE_STORE"] = _tmpstore(tmp_dir)
        cs_mod.CASE_STORE_PATH = Path(_tmpstore(tmp_dir))

        # Two cases in the same owner group -> must collapse to one item.
        cs_mod.upsert(
            {
                "broker_id": member_a,
                "status": "awaiting-user",
                "created_at": _now(),
                "human_action_required": f"Complete opt-out for {member_a}.",
            }
        )
        cs_mod.upsert(
            {
                "broker_id": member_b,
                "status": "awaiting-user",
                "created_at": _now(),
                "human_action_required": f"Complete opt-out for {member_b}.",
            }
        )

        # A captcha case — must rank before phone-verification.
        cs_mod.upsert(
            {
                "broker_id": "advancedbackgroundchecks",
                "status": "awaiting-user",
                "created_at": _now(),
                "mechanism": "captcha-handoff",
                "human_action_required": "Solve CAPTCHA and click Submit.",
            }
        )

        # A phone-verification case.
        cs_mod.upsert(
            {
                "broker_id": "411-com",
                "status": "awaiting-user",
                "created_at": _now(),
                "mechanism": "phone-verification",
                "human_action_required": "Enter the code sent to your phone.",
            }
        )

        # Excluded: submitted-and-waiting, and terminal (confirmed-removed).
        cs_mod.upsert({"broker_id": "whitepages", "status": "discovered", "created_at": _now()})
        cs_mod.mark("whitepages", "submitted")

        cs_mod.upsert({"broker_id": "infotracer", "status": "discovered", "created_at": _now()})
        cs_mod.mark("infotracer", "submitted")
        cs_mod.mark("infotracer", "confirmed-removed")

        # Two independent runs against the unchanged store must be byte-identical.
        items_1 = aq_mod.build_queue(cs_mod.list_cases(), brokers_data)
        rendered_1 = aq_mod.render_markdown(items_1)
        items_2 = aq_mod.build_queue(cs_mod.list_cases(), brokers_data)
        rendered_2 = aq_mod.render_markdown(items_2)

        if verbose:
            print(rendered_1)

        if rendered_1 != rendered_2:
            result.fail("two consecutive runs on the same store produce byte-identical markdown")

        covered_ids = {bid for item in items_1 for bid in item["covers"]}
        if "whitepages" in covered_ids:
            result.fail("submitted (waiting) case must not appear in the queue", "whitepages found")
        if "infotracer" in covered_ids:
            result.fail("confirmed-removed case must not appear in the queue", "infotracer found")

        group_item = next(
            (it for it in items_1 if {member_a, member_b}.issubset(set(it["covers"]))),
            None,
        )
        if group_item is None:
            result.fail(
                f"owner-group cases ({member_a}, {member_b}) collapse into one queue item",
                f"items covers: {[it['covers'] for it in items_1]}",
            )

        captcha_item = next((it for it in items_1 if "advancedbackgroundchecks" in it["covers"]), None)
        phone_item = next((it for it in items_1 if "411-com" in it["covers"]), None)
        if captcha_item is None or phone_item is None:
            result.fail("both the captcha and phone-verification cases appear in the queue")
        elif captcha_item["number"] >= phone_item["number"]:
            result.fail(
                "captcha item is numbered before the phone-verification item (mechanism ranking)",
                f"captcha number={captcha_item['number']}, phone number={phone_item['number']}",
            )

    # Empty store -> friendly message, exit 0, no crash.
    with tempfile.TemporaryDirectory() as tmp_dir2:
        os.environ["QUIETLINE_CASE_STORE"] = _tmpstore(tmp_dir2)
        cs_mod.CASE_STORE_PATH = Path(_tmpstore(tmp_dir2))
        empty_items = aq_mod.build_queue(cs_mod.list_cases(), brokers_data)
        empty_rendered = aq_mod.render_markdown(empty_items)
        if "Nothing needs your attention" not in empty_rendered:
            result.fail("empty queue renders a friendly message", empty_rendered[:200])

    return result


def _classify_fixture(name: str) -> dict:
    raw = (FIXTURES_DIR / name).read_text()
    is_html = conf_mod._looks_like_html(raw, name)
    return conf_mod.classify(raw, is_html)


def _run_confirmation_apply(email_file: Path, broker_id: str, case_store: str) -> subprocess.CompletedProcess:
    # Fixed, hardcoded argv (interpreter path + this repo's own script + a
    # broker_id drawn from data/brokers.json below) — nothing here originates
    # from untrusted input, unlike the generic bandit S603 concern.
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(SCRIPTS_DIR / "confirmation.py"),
            "--email-file",
            str(email_file),
            "--broker-id",
            broker_id,
            "--apply",
            "--case-store",
            case_store,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _check_fixture_classifications(result: EvalResult, verbose: bool) -> None:
    """Assert each fixture classifies correctly (the pure classify() path, no subprocess)."""
    link_html = _classify_fixture("confirm_link.html")
    if verbose:
        print(f"  confirm_link.html -> {link_html}")
    if link_html["classification"] != "confirmation-link":
        result.fail("confirm_link.html classifies as confirmation-link", f"got {link_html['classification']}")
    if link_html["confirmation_url"] != "https://example.com/optout/confirm?token=abc123def456":
        result.fail(
            "confirm_link.html extracts the real https confirmation URL, not a decoy",
            f"got {link_html['confirmation_url']}",
        )
    if link_html["confirmation_url"] and (
        "javascript:" in link_html["confirmation_url"] or link_html["confirmation_url"].startswith("/")
    ):
        result.fail("extracted URL must not be the javascript: or relative decoy", link_html["confirmation_url"])

    link_txt = _classify_fixture("confirm_link.txt")
    if verbose:
        print(f"  confirm_link.txt -> {link_txt}")
    if link_txt["classification"] != "confirmation-link":
        result.fail("confirm_link.txt classifies as confirmation-link", f"got {link_txt['classification']}")
    if not (link_txt["confirmation_url"] or "").startswith("https://"):
        result.fail("confirm_link.txt extracts an https confirmation URL", f"got {link_txt['confirmation_url']}")

    # Documented behavior (scripts/confirmation.py docstring): confirmation-link
    # requires a valid https:// URL; an http://-only link does not qualify, so
    # classification falls through to 'unrelated' rather than reporting
    # confirmation-link with a null URL.
    http_only = _classify_fixture("confirm_link_http_only.html")
    if verbose:
        print(f"  confirm_link_http_only.html -> {http_only}")
    if http_only["classification"] != "unrelated":
        result.fail(
            "confirm_link_http_only.html classifies as 'unrelated' (documented http://-only behavior)",
            f"got {http_only['classification']}",
        )
    if http_only["confirmation_url"] is not None:
        result.fail("confirm_link_http_only.html never extracts an http:// URL", http_only["confirmation_url"])

    already_confirmed = _classify_fixture("already_confirmed.txt")
    if verbose:
        print(f"  already_confirmed.txt -> {already_confirmed}")
    if already_confirmed["classification"] != "already-confirmed":
        result.fail(
            "already_confirmed.txt classifies as already-confirmed", f"got {already_confirmed['classification']}"
        )
    if already_confirmed["suggested_status"] != "confirmed-removed":
        result.fail(
            "already-confirmed suggests status confirmed-removed", f"got {already_confirmed['suggested_status']}"
        )

    rejection = _classify_fixture("rejection.txt")
    if verbose:
        print(f"  rejection.txt -> {rejection}")
    if rejection["classification"] != "rejection":
        result.fail("rejection.txt classifies as rejection", f"got {rejection['classification']}")
    if rejection["suggested_status"] != "failed":
        result.fail("rejection suggests status failed", f"got {rejection['suggested_status']}")

    unrelated = _classify_fixture("unrelated.txt")
    if verbose:
        print(f"  unrelated.txt -> {unrelated}")
    if unrelated["classification"] != "unrelated":
        result.fail("unrelated.txt classifies as unrelated", f"got {unrelated['classification']}")
    if unrelated["suggested_status"] is not None:
        result.fail("unrelated suggests no status change", f"got {unrelated['suggested_status']}")


def _check_confirmation_apply(result: EvalResult, verbose: bool) -> None:
    """--apply path against a temp store, seeded via upsert() (eval-06's pattern)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        os.environ["QUIETLINE_CASE_STORE"] = _tmpstore(tmp_dir)
        cs_mod.CASE_STORE_PATH = Path(_tmpstore(tmp_dir))

        cs_mod.upsert({"broker_id": "whitepages", "status": "awaiting-confirmation", "created_at": _now()})
        cs_mod.upsert({"broker_id": "spokeo", "status": "awaiting-confirmation", "created_at": _now()})

        already_result = _run_confirmation_apply(
            FIXTURES_DIR / "already_confirmed.txt", "whitepages", _tmpstore(tmp_dir)
        )
        if verbose:
            print(f"  --apply (already-confirmed) stdout:\n{already_result.stdout}")
            print(f"  --apply (already-confirmed) stderr:\n{already_result.stderr}")
        if already_result.returncode != 0:
            result.fail("confirmation.py --apply exits 0 for already-confirmed", already_result.stderr)

        after_whitepages = cs_mod.get("whitepages")
        if after_whitepages is None or after_whitepages.get("status") != "confirmed-removed":
            result.fail(
                "--apply moves already-confirmed case to confirmed-removed",
                f"got {after_whitepages.get('status') if after_whitepages else None}",
            )

        rejection_result = _run_confirmation_apply(FIXTURES_DIR / "rejection.txt", "spokeo", _tmpstore(tmp_dir))
        if verbose:
            print(f"  --apply (rejection) stdout:\n{rejection_result.stdout}")
        if rejection_result.returncode != 0:
            result.fail("confirmation.py --apply exits 0 for rejection", rejection_result.stderr)

        after_spokeo = cs_mod.get("spokeo")
        if after_spokeo is None or after_spokeo.get("status") != "failed":
            result.fail(
                "--apply moves rejection case to failed",
                f"got {after_spokeo.get('status') if after_spokeo else None}",
            )


def run_eval_07_confirmation(verbose: bool) -> EvalResult:
    """
    Confirmation-email classification: each fixture classifies correctly,
    confirm_link.html extracts the real https confirmation URL (never the
    javascript:/relative decoys), confirm_link_http_only.html falls through
    to 'unrelated' (documented decoy-handling behavior — see
    scripts/confirmation.py's module docstring), and --apply moves a case to
    the suggested status via case_store.mark.
    """
    result = EvalResult("eval-07", "Confirmation-email classification", release_blocking=False)
    _check_fixture_classifications(result, verbose)
    _check_confirmation_apply(result, verbose)
    return result


def _seed_monitoring_cases() -> None:
    """Case A and case B both start confirmed-removed; case C is never added."""
    cs_mod.upsert({"broker_id": "whitepages", "status": "discovered", "created_at": _now()})
    cs_mod.mark("whitepages", "submitted")
    cs_mod.mark("whitepages", "confirmed-removed")

    cs_mod.upsert({"broker_id": "spokeo", "status": "discovered", "created_at": _now()})
    cs_mod.mark("spokeo", "submitted")
    cs_mod.mark("spokeo", "confirmed-removed")


def _check_diff_and_refile(result: EvalResult, fake_scan: list, verbose: bool) -> None:
    """diff() classification, then the reappeared -> submitted refile + guard."""
    diff_result = diff_mod.diff(fake_scan)
    if verbose:
        print(f"  diff() -> {diff_result}")

    if "whitepages" not in diff_result["reappeared"]:
        result.fail("A (whitepages) appears in diff()'s reappeared", str(diff_result))
    if "infotracer" not in diff_result["new"]:
        result.fail("C (infotracer) appears in diff()'s new", str(diff_result))
    if "spokeo" not in diff_result["still_clear"]:
        result.fail("B (spokeo) appears in diff()'s still_clear", str(diff_result))

    # mark(A, 'reappeared') succeeds: status changes and audit_log grows.
    before = cs_mod.get("whitepages")
    before_log_len = len(before["audit_log"]) if before else 0
    reappeared_record = cs_mod.mark("whitepages", "reappeared")
    if verbose:
        print(f"  after mark(A, 'reappeared'): status={reappeared_record['status']}")
    if reappeared_record["status"] != "reappeared":
        result.fail(
            "mark(A, 'reappeared') changes status to 'reappeared'",
            f"got '{reappeared_record['status']}'",
        )
    if len(reappeared_record.get("audit_log", [])) <= before_log_len:
        result.fail("mark(A, 'reappeared') appends an audit_log entry")

    # mark(A, 'submitted') then succeeds (refile).
    refiled_record = cs_mod.mark("whitepages", "submitted")
    if verbose:
        print(f"  after mark(A, 'submitted'): status={refiled_record['status']}")
    if refiled_record["status"] != "submitted":
        result.fail(
            "mark(A, 'submitted') refiles the case after 'reappeared'",
            f"got '{refiled_record['status']}'",
        )

    # mark(B, 'submitted') directly from confirmed-removed is still refused.
    cs_mod.mark("spokeo", "submitted")
    b_after = cs_mod.get("spokeo")
    if b_after is None or b_after["status"] != "confirmed-removed":
        result.fail(
            "mark(B, 'submitted') direct from confirmed-removed is refused (guard still holds)",
            f"got '{b_after['status'] if b_after else None}'",
        )


def _check_diff_scan_case_store_flag(
    result: EvalResult, tmp_dir: str, store_path: str, fake_scan: list, verbose: bool
) -> None:
    """Regression test for Fix 2: --case-store must point the CLI at this temp store."""
    scan_file = Path(tmp_dir) / "scan.json"
    scan_file.write_text(json.dumps(fake_scan))
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(SCRIPTS_DIR / "diff_scan.py"),
            "--scan-file",
            str(scan_file),
            "--case-store",
            store_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if verbose:
        print(f"  subprocess stdout:\n{proc.stdout}")
        print(f"  subprocess stderr:\n{proc.stderr}")
    if proc.returncode != 0:
        result.fail("diff_scan.py --case-store subprocess exits 0", proc.stderr)

    try:
        subprocess_result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        subprocess_result = {}
        result.fail("diff_scan.py --case-store prints valid JSON to stdout", proc.stdout)

    # By this point A (whitepages) has been refiled to 'submitted', so this
    # scan should now report B (spokeo, still confirmed-removed and absent
    # from the scan) as still_clear and C (infotracer) as new — values that
    # could only come from *this* temp store, not the default.
    if "spokeo" not in subprocess_result.get("still_clear", []):
        result.fail(
            "diff_scan.py --case-store reflects the passed store (spokeo in still_clear)",
            str(subprocess_result),
        )
    if "infotracer" not in subprocess_result.get("new", []):
        result.fail(
            "diff_scan.py --case-store reflects the passed store (infotracer in new)",
            str(subprocess_result),
        )


def _seed_rescan_store(name: str = "Jane Test", phone: str = "+15551234567", state: str = "CA") -> None:
    """
    Profile + a single confirmed-removed case, for the rescan.py --apply
    subprocess check. whitepages is standalone, publicly_searchable, and
    present in every discover() report (see eval-01/02), so a fresh scan is
    guaranteed to contain it (-> reappeared) plus plenty of ids the store
    has never seen (-> new).
    """
    cs_mod.init_profile({"name": name, "phone": phone, "state": state})
    cs_mod.upsert({"broker_id": "whitepages", "status": "discovered", "created_at": _now()})
    cs_mod.mark("whitepages", "submitted")
    cs_mod.mark("whitepages", "confirmed-removed")


def _run_rescan_apply(case_store: str) -> subprocess.CompletedProcess:
    # Fixed, hardcoded argv (interpreter path + this repo's own script + a
    # temp case-store path) — nothing here originates from untrusted input,
    # unlike the generic bandit S603 concern.
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(SCRIPTS_DIR / "rescan.py"),
            "--apply",
            "--dry-run",
            "--case-store",
            case_store,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _check_rescan_apply(result: EvalResult, verbose: bool) -> None:
    """
    scripts/rescan.py --apply, run as a subprocess against a freshly seeded
    store (profile + one confirmed-removed, publicly-searchable case). No
    --name/--phone/--state flags are passed, so this also exercises
    rescan.py's stored-profile fallback.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        store_path = _tmpstore(tmp_dir)
        os.environ["QUIETLINE_CASE_STORE"] = store_path
        cs_mod.CASE_STORE_PATH = Path(store_path)

        _seed_rescan_store()

        proc = _run_rescan_apply(store_path)
        if verbose:
            print(f"  rescan.py --apply stdout:\n{proc.stdout}")
            print(f"  rescan.py --apply stderr:\n{proc.stderr}")
        if proc.returncode != 0:
            result.fail("rescan.py --apply subprocess exits 0", proc.stderr)

        try:
            rescan_result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            rescan_result = {}
            result.fail("rescan.py --apply prints valid JSON to stdout", proc.stdout)

        if "whitepages" not in rescan_result.get("reappeared", []):
            result.fail(
                "rescan.py --apply's JSON reports the seeded case as reappeared",
                str(rescan_result),
            )

        # The subprocess wrote through its own process-local case_store
        # import, bound to the same file this process has patched
        # CASE_STORE_PATH to — re-reading here reflects what it wrote.
        after = cs_mod.get("whitepages")
        if after is None or after["status"] != "reappeared":
            result.fail(
                "rescan.py --apply marks the seeded confirmed-removed case 'reappeared'",
                f"got {after.get('status') if after else None}",
            )

        all_cases = cs_mod.list_cases()
        new_discovered = [c for c in all_cases if c.get("status") == "discovered"]
        if not new_discovered:
            result.fail("rescan.py --apply registers at least one new case as 'discovered'", str(all_cases))

        store = cs_mod._load_store()
        last_scan_date = store.get("last_scan_date")
        next_scan_date = store.get("next_scan_date")
        if verbose:
            print(f"  last_scan_date={last_scan_date} next_scan_date={next_scan_date}")

        if not last_scan_date:
            result.fail("rescan.py --apply writes store['last_scan_date'] (read by build_dashboard.py)", str(store))
        if not next_scan_date:
            result.fail("rescan.py --apply writes store['next_scan_date'] (read by build_dashboard.py)", str(store))

        if last_scan_date and next_scan_date:
            delta_days = (datetime.fromisoformat(next_scan_date) - datetime.fromisoformat(last_scan_date)).days
            if not (85 <= delta_days <= 95):
                result.fail(
                    "next_scan_date is ~90 days after last_scan_date",
                    f"got a {delta_days}-day delta",
                )


def run_eval_08_monitoring_refile(verbose: bool) -> EvalResult:
    """
    Phase-5 monitoring flow: confirmed-removed cases that reappear in a fresh
    scan can be marked 'reappeared' and then refiled ('reappeared' ->
    'submitted'), while the idempotency guard still refuses a direct
    confirmed-removed -> submitted transition. Also regression-tests
    diff_scan.py's --case-store flag (Fix 2: the env var must be set before
    case_store is imported, not after), and scripts/rescan.py --apply end to
    end: reappeared/new classification, the case-store writes, and the
    last_scan_date/next_scan_date pair build_dashboard.py reads.
    """
    result = EvalResult("eval-08", "Monitoring refile: reappeared -> submitted", release_blocking=False)

    with tempfile.TemporaryDirectory() as tmp_dir:
        store_path = _tmpstore(tmp_dir)
        os.environ["QUIETLINE_CASE_STORE"] = store_path
        cs_mod.CASE_STORE_PATH = Path(store_path)

        _seed_monitoring_cases()

        # Fake scan (discovery-report-shaped rows): contains A and C, not B.
        fake_scan = [{"id": "whitepages"}, {"id": "infotracer"}]

        _check_diff_and_refile(result, fake_scan, verbose)
        _check_diff_scan_case_store_flag(result, tmp_dir, store_path, fake_scan, verbose)

    _check_rescan_apply(result, verbose)

    return result


def run_eval_09_email_rendering(verbose: bool) -> EvalResult:
    """
    Email rendering across template x jurisdiction, plus regression coverage
    for the escalation-template bug (render_email.py used to populate only
    'statutory_language', leaving {escalation_language} as a literal,
    unresolved placeholder in every rendered escalation email).

    For each (template, state) in {deletion, escalation} x {CA, TX, None
    (default)}: subject is non-empty, body contains the jurisdiction's
    statutory/escalation sentence from data/jurisdictions.json (formatted
    with the same submitted_date render() used), and body has zero
    unresolved {word} placeholders. Also checks: radaris (email-request
    mechanism) resolves 'to' from its opt_out_email; an unknown broker id
    raises ValueError; and rendering succeeds for an owner-group member
    broker id (intelius, a peopleconnect member).
    """
    result = EvalResult(
        "eval-09",
        "Email rendering: template x jurisdiction, radaris to-address, unknown broker, owner-group member",
        release_blocking=False,
    )

    jurisdictions = render_mod._load_json(render_mod.JURISDICTIONS_PATH)["jurisdictions"]
    today = date.today().isoformat()

    for template_type in ("deletion", "escalation"):
        for state in ("CA", "TX", None):
            rendered = render_mod.render(
                template_type=template_type,
                broker_id="whitepages",
                state=state,
                user_name="Jane Test",
                phone_numbers=["+15551234567"],
                user_email="jane@example.com",
            )
            if verbose:
                print(f"  [{template_type}/{state}] subject={rendered['subject']!r}")

            if not rendered["subject"].strip():
                result.fail(f"{template_type}/{state}: rendered subject is non-empty")

            jur = jurisdictions.get((state or "").upper()) or jurisdictions["default"]
            raw_language = jur.get(f"{template_type}_language") or jur.get("statutory_language", "")
            expected_language = raw_language.format(submitted_date=today)

            if expected_language not in rendered["body"]:
                result.fail(
                    f"{template_type}/{state}: body contains the jurisdiction's {template_type}_language sentence",
                    f"expected substring not found (first 80 chars): {expected_language[:80]!r}",
                )

            # Regression trap: the pre-fix code only populated 'statutory_language'
            # in the variables dict, never 'escalation_language', so every rendered
            # escalation email contained the literal text '{escalation_language}'.
            # This assertion fails against that code and passes only once both
            # template placeholders are populated.
            leftover = re.search(r"\{\w+\}", rendered["body"])
            if leftover is not None:
                result.fail(
                    f"{template_type}/{state}: body has zero unresolved {{placeholder}}s",
                    f"found leftover placeholder: {leftover.group(0)!r}",
                )

    # radaris is an email-request broker with no opt_out_url; 'to' must resolve
    # from its opt_out_email (data/brokers.json), not a fabricated privacy@ address.
    radaris_rendered = render_mod.render(
        template_type="deletion",
        broker_id="radaris",
        state="CA",
        user_name="Jane Test",
        phone_numbers=["+15551234567"],
        user_email="jane@example.com",
    )
    brokers_data = render_mod._load_json(render_mod.BROKERS_PATH)
    radaris_broker = render_mod._get_broker("radaris", brokers_data)
    expected_to = radaris_broker.get("opt_out_email") if radaris_broker else None
    if verbose:
        print(f"  radaris 'to': {radaris_rendered['to']!r} (expected {expected_to!r})")
    if not expected_to or radaris_rendered["to"] != expected_to:
        result.fail(
            "radaris 'to' resolves from its opt_out_email in data/brokers.json",
            f"got {radaris_rendered['to']!r}, expected {expected_to!r}",
        )

    # An unknown broker id must error clearly (ValueError), not render garbage.
    try:
        render_mod.render(
            template_type="deletion",
            broker_id="not-a-real-broker-id",
            state="CA",
            user_name="Jane Test",
            phone_numbers=["+15551234567"],
            user_email="jane@example.com",
        )
        result.fail("render() raises for an unknown broker id", "no exception was raised")
    except ValueError:
        pass
    except Exception as e:  # noqa: BLE001 - intentionally broad to report wrong-exception-type failures
        result.fail("render() raises ValueError (not some other exception) for an unknown broker id", repr(e))

    # Owner-group member broker id (intelius, a peopleconnect member per
    # data/brokers.json's owner_groups) still renders correctly on its own —
    # action_queue.py's owner-group collapse is a display concern, not a
    # constraint on render_email.py.
    intelius_rendered = render_mod.render(
        template_type="deletion",
        broker_id="intelius",
        state="CA",
        user_name="Jane Test",
        phone_numbers=["+15551234567"],
        user_email="jane@example.com",
    )
    if verbose:
        print(f"  intelius subject={intelius_rendered['subject']!r}")
    if not intelius_rendered["subject"].strip():
        result.fail("intelius (owner-group member) renders a non-empty subject")
    if re.search(r"\{\w+\}", intelius_rendered["body"]) is not None:
        result.fail("intelius (owner-group member) body has zero unresolved placeholders", intelius_rendered["body"])

    return result


EVAL_RUNNERS = {
    "eval-01-discovery": run_eval_01_discovery,
    "eval-02-mechanism-routing": run_eval_02_mechanism_routing,
    "eval-03-idempotency": run_eval_03_idempotency,
    "eval-04-deadline-escalation": run_eval_04_deadlines,
    "eval-05-safety-own-data-only": run_eval_05_safety,
    "eval-06-action-queue": run_eval_06_action_queue,
    "eval-07-confirmation-classification": run_eval_07_confirmation,
    "eval-08-monitoring-refile": run_eval_08_monitoring_refile,
    "eval-09-email-rendering": run_eval_09_email_rendering,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run QuietLine eval suite")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--eval-id", help="Run only a specific eval by ID")
    args = parser.parse_args()

    runners = EVAL_RUNNERS
    if args.eval_id:
        if args.eval_id not in runners:
            print(f"Unknown eval ID: {args.eval_id}. Available: {list(runners.keys())}")
            sys.exit(1)
        runners = {args.eval_id: runners[args.eval_id]}

    results: list[EvalResult] = []
    safety_blocked = False

    print(f"\n{'=' * 60}")
    print(f"QuietLine Eval Harness — {len(runners)} eval(s)")
    print(f"{'=' * 60}\n")

    for eval_id, runner in runners.items():
        print(f"[{eval_id}] ", end="", flush=True)
        try:
            res = runner(verbose=args.verbose)
        except Exception as e:
            res = EvalResult(eval_id, eval_id, release_blocking=eval_id == "eval-05-safety-own-data-only")
            res.fail("no exception raised", str(e))

        results.append(res)

        if res.passed:
            print(f"PASS  {res.name}")
        else:
            tag = " [RELEASE-BLOCKING]" if res.release_blocking else ""
            print(f"FAIL{tag}  {res.name}")
            for f in res.failures:
                print(f"        {f}")
            if res.release_blocking:
                safety_blocked = True

    print(f"\n{'=' * 60}")
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    print(f"Results: {passed}/{len(results)} passed, {failed} failed")

    if safety_blocked:
        print("\n*** RELEASE BLOCKED: SAFE-1 safety eval failed. ***")
        print("*** Fix the ownership gate before packaging. ***\n")
        sys.exit(2)
    elif failed > 0:
        sys.exit(1)
    else:
        print("\nAll evals passed.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
