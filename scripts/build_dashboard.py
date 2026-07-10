"""
Render the QuietLine status dashboard from the current case store.

Fills assets/templates/dashboard.md with live counts and per-section lists.
Prints the rendered dashboard to stdout (the agent embeds it in its response
or writes it to a file for the user to inspect).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import case_store as cs
import deadlines as dl

TEMPLATE_PATH = Path(__file__).parent.parent / "assets" / "templates" / "dashboard.md"
JURISDICTIONS_PATH = Path(__file__).parent.parent / "data" / "jurisdictions.json"


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _load_jurisdictions() -> dict:
    with open(JURISDICTIONS_PATH) as f:
        return json.load(f)["jurisdictions"]


def _format_case_line(case: dict) -> str:
    bid = case.get("broker_id", "?")
    status = case.get("status", "?")
    note = case.get("notes", "")
    action = case.get("human_action_required", "")
    parts = [f"- **{bid}** [{status}]"]
    if action:
        parts.append(f": {action}")
    elif note:
        parts.append(f": {note}")
    return "".join(parts)


def render(pending_only: bool = False, profile: Optional[dict] = None) -> str:
    cases = cs.list_cases()
    store = cs._load_store()
    profile = profile or store.get("profile") or {}
    jurisdictions = _load_jurisdictions()

    by_status: dict[str, list] = {
        "confirmed-removed": [],
        "awaiting-confirmation": [],
        "awaiting-user": [],
        "failed": [],
        "reappeared": [],
        "discovered": [],
        "submitted": [],
    }
    for c in cases:
        s = c.get("status", "discovered")
        by_status.setdefault(s, []).append(c)

    awaiting_confirmed = by_status["awaiting-confirmation"] + by_status["submitted"]
    overdue_cases = dl.flag_overdue(cases, jurisdictions)

    def fmt_list(case_list: list) -> str:
        if not case_list:
            return "_None_"
        return "\n".join(_format_case_line(c) for c in case_list)

    template = TEMPLATE_PATH.read_text()

    filled = template
    replacements = {
        "generated_at": _now_str(),
        "profile_name": profile.get("name", "(not set)"),
        "profile_phone": profile.get("phone", "(not set)"),
        "last_scan_date": store.get("last_scan_date", "(not yet run)"),
        "next_scan_date": store.get("next_scan_date", "(not scheduled)"),
        "count_confirmed_removed": str(len(by_status["confirmed-removed"])),
        "count_awaiting_confirmation": str(len(awaiting_confirmed)),
        "count_awaiting_user": str(len(by_status["awaiting-user"])),
        "count_failed": str(len(by_status["failed"])),
        "count_reappeared": str(len(by_status["reappeared"])),
        "count_discovered": str(len(by_status["discovered"])),
        "count_total": str(len(cases)),
        "count_overdue": str(len(overdue_cases)),
        "days_until_next_scan": store.get("days_until_next_scan", "~90"),
        "awaiting_user_list": fmt_list(by_status["awaiting-user"]),
        "overdue_list": fmt_list(overdue_cases),
        "failed_list": fmt_list(by_status["failed"]),
        "confirmed_list": fmt_list(by_status["confirmed-removed"]),
    }

    for key, val in replacements.items():
        filled = filled.replace(f"{{{key}}}", val)

    if pending_only:
        lines = filled.splitlines()
        output_lines = []
        in_pending = False
        for line in lines:
            if "Needs your action" in line or "Overdue" in line:
                in_pending = True
            elif line.startswith("## ") and in_pending:
                in_pending = False
            if in_pending or line.startswith("# ") or "Summary" in line:
                output_lines.append(line)
        filled = "\n".join(output_lines)

    return filled


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the QuietLine status dashboard")
    parser.add_argument(
        "--pending-only",
        action="store_true",
        help="Show only items that need the user's action",
    )
    parser.add_argument(
        "--case-store",
        help="Path to cases.json (default: ~/.quietline/cases.json)",
    )
    args = parser.parse_args()

    if args.case_store:
        os.environ["QUIETLINE_CASE_STORE"] = args.case_store

    print(render(pending_only=args.pending_only))


if __name__ == "__main__":
    main()
