"""
Diff a fresh discovery scan against the prior case-store state.

Outputs three categories:
  still_clear   — confirmed-removed cases still absent from the new scan
  reappeared    — cases previously confirmed-removed that show up again
  new           — broker IDs in the new scan not in the case store at all

The agent uses this output to decide what to re-file (reappeared + new) and
what to report as stable (still_clear).
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import case_store as cs


def diff(new_scan: list) -> dict:
    """
    Compare new_scan (output of discover.py) against the current case store.

    Args:
        new_scan: list of Discovery Report rows from discover.py

    Returns:
        {
            still_clear: list of broker_ids confirmed-removed and still not in new scan,
            reappeared: list of broker_ids previously confirmed-removed now in new scan,
            new: list of broker_ids not previously seen,
        }
    """
    new_scan_ids = {row["id"] for row in new_scan}
    existing_cases = cs.list_cases()
    existing_by_id = {c["broker_id"]: c for c in existing_cases}

    still_clear = []
    reappeared = []
    new_ids = []

    for broker_id in new_scan_ids:
        case = existing_by_id.get(broker_id)
        if case is None:
            new_ids.append(broker_id)
        elif case.get("status") == "confirmed-removed":
            reappeared.append(broker_id)

    for broker_id, case in existing_by_id.items():
        if case.get("status") == "confirmed-removed" and broker_id not in new_scan_ids:
            still_clear.append(broker_id)

    return {
        "still_clear": sorted(still_clear),
        "reappeared": sorted(reappeared),
        "new": sorted(new_ids),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Diff a new scan against prior case-store state")
    parser.add_argument(
        "--scan-file",
        help="Path to JSON file containing discover.py output (default: read from stdin)",
    )
    parser.add_argument(
        "--case-store",
        help="Path to cases.json (default: ~/.quietline/cases.json)",
    )
    args = parser.parse_args()

    if args.case_store:
        os.environ["QUIETLINE_CASE_STORE"] = args.case_store

    if args.scan_file:
        with open(args.scan_file) as f:
            new_scan = json.load(f)
    else:
        if sys.stdin.isatty():
            print("Pass --scan-file or pipe discover.py output via stdin", file=sys.stderr)
            sys.exit(1)
        new_scan = json.load(sys.stdin)

    result = diff(new_scan)
    print(json.dumps(result, indent=2))

    total_action = len(result["reappeared"]) + len(result["new"])
    print(
        f"\n{len(result['still_clear'])} still clear · "
        f"{len(result['reappeared'])} reappeared · "
        f"{len(result['new'])} new · "
        f"{total_action} need action",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
