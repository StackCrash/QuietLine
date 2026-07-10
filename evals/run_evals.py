"""
QuietLine eval harness runner.

Executes the 6 test cases in evals.json against the actual scripts using
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
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make scripts importable
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import discover as discover_mod
import case_store as cs_mod
import deadlines as dl_mod
import action_queue as aq_mod


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


EVAL_RUNNERS = {
    "eval-01-discovery": run_eval_01_discovery,
    "eval-02-mechanism-routing": run_eval_02_mechanism_routing,
    "eval-03-idempotency": run_eval_03_idempotency,
    "eval-04-deadline-escalation": run_eval_04_deadlines,
    "eval-05-safety-own-data-only": run_eval_05_safety,
    "eval-06-action-queue": run_eval_06_action_queue,
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
