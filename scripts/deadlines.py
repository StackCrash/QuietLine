"""
Compute statutory response deadlines and flag overdue cases.

Reads jurisdiction data from data/jurisdictions.json and the case store via
case_store.py. A case is overdue when:
  submitted_at + response_deadline_days < now  AND  status not in terminal statuses
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

JURISDICTIONS_PATH = Path(__file__).parent.parent / "data" / "jurisdictions.json"
TERMINAL_STATUSES = {"confirmed-removed"}


def _load_jurisdictions() -> dict:
    with open(JURISDICTIONS_PATH) as f:
        return json.load(f)["jurisdictions"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def get_deadline_days(jurisdiction: Optional[str], jurisdictions: dict) -> int:
    """Return the response deadline in calendar days for the given state code."""
    key = (jurisdiction or "").upper()
    entry = jurisdictions.get(key) or jurisdictions["default"]
    return entry["response_deadline_days"]


def compute_deadline(submitted_at: str, jurisdiction: Optional[str], jurisdictions: dict) -> datetime:
    """Return the deadline datetime for a submitted case."""
    days = get_deadline_days(jurisdiction, jurisdictions)
    return _parse_dt(submitted_at) + timedelta(days=days)


def is_overdue(case: dict, jurisdictions: dict) -> bool:
    """Return True if this case is past its statutory response deadline."""
    if case.get("status") in TERMINAL_STATUSES:
        return False
    submitted_at = case.get("submitted_at")
    if not submitted_at:
        return False
    deadline = compute_deadline(submitted_at, case.get("jurisdiction"), jurisdictions)
    return _now() > deadline


def days_overdue(case: dict, jurisdictions: dict) -> int:
    """Return the number of days past the deadline (0 if not yet overdue)."""
    if not is_overdue(case, jurisdictions):
        return 0
    submitted_at = case.get("submitted_at")
    assert submitted_at, "is_overdue() already returned True, so submitted_at must be set"  # noqa: S101
    deadline = compute_deadline(submitted_at, case.get("jurisdiction"), jurisdictions)
    delta = _now() - deadline
    return max(0, delta.days)


def flag_overdue(cases: list, jurisdictions: dict) -> list:
    """Return only the cases that are past their statutory deadline."""
    return [c for c in cases if is_overdue(c, jurisdictions)]


def main() -> None:
    parser = argparse.ArgumentParser(description="QuietLine statutory deadline checker")
    parser.add_argument(
        "--flag-overdue",
        action="store_true",
        help="Print all cases past their statutory response deadline",
    )
    parser.add_argument(
        "--broker-id",
        help="Check deadline for a single broker",
    )
    parser.add_argument(
        "--case-store",
        help="Path to cases.json (default: ~/.quietline/cases.json)",
    )
    args = parser.parse_args()

    if args.case_store:
        os.environ["QUIETLINE_CASE_STORE"] = args.case_store

    # Import here to avoid circular issues at module level
    import case_store as cs  # noqa: PLC0415

    jurisdictions = _load_jurisdictions()

    if args.broker_id:
        case = cs.get(args.broker_id)
        if case is None:
            print(f"No case found for '{args.broker_id}'", file=sys.stderr)
            sys.exit(1)
        overdue = is_overdue(case, jurisdictions)
        d_over = days_overdue(case, jurisdictions)
        submitted_at = case.get("submitted_at")
        if submitted_at:
            deadline = compute_deadline(submitted_at, case.get("jurisdiction"), jurisdictions)
            deadline_str = deadline.date().isoformat()
        else:
            deadline_str = "(not submitted)"
        print(
            json.dumps(
                {
                    "broker_id": args.broker_id,
                    "status": case.get("status"),
                    "submitted_at": submitted_at,
                    "deadline_date": deadline_str,
                    "overdue": overdue,
                    "days_overdue": d_over,
                },
                indent=2,
            )
        )
        sys.exit(1 if overdue else 0)

    if args.flag_overdue:
        cases = cs.list_cases()
        overdue_cases = flag_overdue(cases, jurisdictions)
        if not overdue_cases:
            print("No overdue cases.")
            sys.exit(0)
        print(f"{len(overdue_cases)} overdue case(s):")
        for c in overdue_cases:
            print(
                json.dumps(
                    {
                        "broker_id": c["broker_id"],
                        "status": c["status"],
                        "submitted_at": c.get("submitted_at"),
                        "days_overdue": days_overdue(c, jurisdictions),
                    },
                    indent=2,
                )
            )
        sys.exit(1)

    parser.print_help()


if __name__ == "__main__":
    main()
