"""
Phase-5 re-scan orchestration: run discovery, diff against the case store,
and (optionally) apply the result.

Replaces the old two-command discover.py -> diff_scan.py sequence with a
single invocation. Runs entirely in-process (calls discover.discover() and
diff_scan.diff() as functions, not subprocesses):

    python scripts/rescan.py                 # read-only: scan + diff, no writes
    python scripts/rescan.py --apply          # also refile/register and record scan dates

Identity is read from the case store's stored profile by default (the
name/phone/state collected back in Phase 0), so the user doesn't have to
re-supply it every 90 days. --name/--phone/--state override individual
fields for a one-off rerun; if neither the profile nor the overrides supply
a name and phone, this exits with a clear error rather than guessing.

--apply is the only mode that writes:
  - each `reappeared` broker_id -> case_store.mark(id, "reappeared", ...)
  - each `new` broker_id        -> case_store.upsert({"status": "discovered", ...})
  - the store's top-level `last_scan_date` / `next_scan_date` (the exact
    keys build_dashboard.py reads — see its `replacements` dict) are set to
    today and today + 90 days.
Without --apply, nothing is written to the store.

LATE IMPORT: case_store.py fixes CASE_STORE_PATH from QUIETLINE_CASE_STORE
at *import* time, so `import case_store` must happen after --case-store has
set the env var, not at module load time (same pattern as diff_scan.py's
diff() and action_queue.py's main()). discover.py and diff_scan.py don't
bind any path at import time, so they're imported normally, at module load.

PII rule: the resolved name/phone are used only internally to call
discover.discover() — the summary line (stderr) and JSON payload (stdout)
are broker-id-centric and never print the user's name or phone.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

import diff_scan  # noqa: E402
import discover  # noqa: E402

RESCAN_INTERVAL_DAYS = 90


def _today_iso_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _next_scan_date_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=RESCAN_INTERVAL_DAYS)).date().isoformat()


def _resolve_identity(
    name: Optional[str],
    phone: Optional[str],
    state: Optional[str],
    profile: Optional[dict],
) -> tuple[str, str, Optional[str]]:
    """Merge CLI overrides with the stored profile; raise if name/phone are still missing."""
    profile = profile or {}
    resolved_name = name or profile.get("name")
    resolved_phone = phone or profile.get("phone")
    resolved_state = state or profile.get("state")
    if not resolved_name or not resolved_phone:
        raise ValueError(
            "No identity available for re-scan: run "
            "'python scripts/case_store.py init --name ... --phone ... --state ...' first, "
            "or pass --name/--phone explicitly."
        )
    return resolved_name, resolved_phone, resolved_state


def run_rescan(
    name: Optional[str] = None,
    phone: Optional[str] = None,
    state: Optional[str] = None,
    dry_run: bool = False,
    apply: bool = False,
) -> dict:
    """
    Run discovery + diff against the current case store, optionally applying results.

    Returns a broker-id-centric dict: {still_clear, reappeared, new, scan_count,
    applied, last_scan_date, next_scan_date}.
    """
    # LATE IMPORT (matches diff_scan.py's diff() / action_queue.py's main()):
    # case_store.py fixes CASE_STORE_PATH from QUIETLINE_CASE_STORE at *import*
    # time, so this import must happen here, after main() has set the env var
    # from --case-store, not at module load time.
    import case_store as cs  # noqa: PLC0415

    store = cs._load_store()
    profile = store.get("profile")
    resolved_name, resolved_phone, resolved_state = _resolve_identity(name, phone, state, profile)

    scan = discover.discover(name=resolved_name, phone=resolved_phone, state=resolved_state, dry_run=dry_run)
    diff_result = diff_scan.diff(scan)

    scan_date = _today_iso_date()
    next_date = _next_scan_date_iso()

    if apply:
        for broker_id in diff_result["reappeared"]:
            cs.mark(broker_id, "reappeared", note=f"Reappeared in 90-day re-scan on {scan_date}.")
        for broker_id in diff_result["new"]:
            cs.upsert(
                {
                    "broker_id": broker_id,
                    "status": "discovered",
                    "notes": f"Found in 90-day re-scan on {scan_date}.",
                }
            )

        # Reload after mark()/upsert() have each independently saved the
        # store, then record the scan dates at the exact keys
        # build_dashboard.py reads (top-level of the store, alongside
        # "profile"/"affirmation"/"cases").
        store = cs._load_store()
        store["last_scan_date"] = scan_date
        store["next_scan_date"] = next_date
        cs._save_store(store)

    return {
        **diff_result,
        "scan_count": len(scan),
        "applied": apply,
        "last_scan_date": scan_date,
        "next_scan_date": next_date,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="QuietLine 90-day re-scan: discover, diff, optionally apply")
    parser.add_argument("--name", help="Full legal name (default: stored profile)")
    parser.add_argument("--phone", help="Phone number to check (default: stored profile)")
    parser.add_argument("--state", help="Two-letter state code (default: stored profile)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass through to discover.py: no live web lookups",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write results: refile reappeared cases, register new cases, record scan dates",
    )
    parser.add_argument(
        "--case-store",
        help="Path to cases.json (default: ~/.quietline/cases.json)",
    )
    args = parser.parse_args()

    if args.case_store:
        os.environ["QUIETLINE_CASE_STORE"] = args.case_store

    try:
        result = run_rescan(
            name=args.name,
            phone=args.phone,
            state=args.state,
            dry_run=args.dry_run,
            apply=args.apply,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    total_action = len(result["reappeared"]) + len(result["new"])
    print(
        f"{len(result['still_clear'])} still clear · "
        f"{len(result['reappeared'])} reappeared · "
        f"{len(result['new'])} new · "
        f"{total_action} need action",
        file=sys.stderr,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
