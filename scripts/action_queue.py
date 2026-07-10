"""
Action queue: deterministic numbered human-action queue.

Reads the case store and renders a numbered queue of the items that need the
user's attention (CAPTCHA solves, phone verification, ID uploads, account
creation, etc. — see references/mechanisms/*), collapsing cases whose
broker_ids share a data-broker owner group (data/brokers.json owner_groups)
into a single queue item so the user files one centralized request instead
of one per brand.

Follows deadlines.py's LATE-IMPORT pattern rather than build_dashboard.py's:
case_store.py fixes CASE_STORE_PATH from QUIETLINE_CASE_STORE at *import*
time, so `import case_store` must happen inside main(), after the env var is
set from --case-store, not at module load time. The queue-building logic
itself (select/build/render) is a pure function of an already-loaded case
list plus brokers.json data — it never imports case_store — so it stays
trivially testable (see evals/run_evals.py eval-06) and reusable.

PII rule: output contains broker names, URLs, and instruction text only —
never the user's name, phone, address, or DOB. No network access.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

BROKERS_PATH = Path(__file__).parent.parent / "data" / "brokers.json"

TERMINAL_STATUSES = {"confirmed-removed"}
WAITING_STATUSES = {"submitted"}

# Mechanism rank: sections are ordered captcha-handoff, phone-verification,
# id-upload, account-required, then everything else (web-form, email-request,
# ca-drop, unknown) grouped last. Lower rank sorts first.
MECHANISM_RANK = {
    "captcha-handoff": 0,
    "phone-verification": 1,
    "id-upload": 2,
    "account-required": 3,
}
DEFAULT_RANK = 4

SECTION_TITLES = {
    "captcha-handoff": "CAPTCHA required",
    "phone-verification": "Phone verification required",
    "id-upload": "Government ID upload required",
    "account-required": "Account creation required",
}

DEFAULT_INSTRUCTIONS = {
    "captcha-handoff": "Solve the CAPTCHA on the pre-filled form, then click Submit.",
    "phone-verification": (
        "A verification code has been sent to your phone. Enter it in the form to complete the request."
    ),
    "id-upload": "Review the redaction guidance, upload your redacted government ID, then submit.",
    "account-required": "Create an account (or log in) using your opt-out email, then complete the deletion request.",
}
DEFAULT_INSTRUCTION_FALLBACK = "Complete the pending step on the broker's site, then let the agent know."


def _load_brokers() -> dict:
    with open(BROKERS_PATH) as f:
        return json.load(f)


def _section_title(mechanism: str) -> str:
    return SECTION_TITLES.get(mechanism, "Other action required")


def _default_instruction(mechanism: str) -> str:
    return DEFAULT_INSTRUCTIONS.get(mechanism, DEFAULT_INSTRUCTION_FALLBACK)


def is_selected(case: dict, include_failed: bool = False) -> bool:
    """
    Return True if this case belongs in the human-action queue.

    Selected: status == 'awaiting-user', OR a non-empty human_action_required
    on a case whose status isn't terminal (confirmed-removed) or
    submitted-and-waiting. 'failed' cases only qualify via the second branch
    and only when --include-failed is passed.
    """
    status = case.get("status")
    if status == "awaiting-user":
        return True
    if status in TERMINAL_STATUSES or status in WAITING_STATUSES:
        return False
    action = case.get("human_action_required")
    if not action:
        return False
    if status == "failed" and not include_failed:
        return False
    return True


def _determine_mechanism(case: dict, brokers_by_id: dict) -> str:
    """The case record's own `mechanism` field wins; else fall back to brokers.json."""
    mechanism = case.get("mechanism")
    if mechanism:
        return str(mechanism)
    broker = brokers_by_id.get(case.get("broker_id", ""))
    if broker is None:
        return "unknown"
    mechanism_type = broker.get("mechanism_type") or "unknown"
    if mechanism_type == "web-form" and broker.get("captcha_required"):
        return "captcha-handoff"
    return str(mechanism_type)


def _build_single_item(case: dict, brokers_by_id: dict) -> dict:
    broker_id = case["broker_id"]
    mechanism = _determine_mechanism(case, brokers_by_id)
    broker = brokers_by_id.get(broker_id, {})
    instruction = case.get("human_action_required") or _default_instruction(mechanism)
    return {
        "title": broker.get("name", broker_id),
        "mechanism": mechanism,
        "covers": [broker_id],
        "instruction": instruction,
        "sort_key": broker_id,
    }


def _build_group_item(group_key: str, members: list, group: dict, brokers_by_id: dict) -> dict:
    members_sorted = sorted(members, key=lambda c: c["broker_id"])
    covers = [c["broker_id"] for c in members_sorted]
    mechanisms = [_determine_mechanism(c, brokers_by_id) for c in members_sorted]
    mechanism = min(mechanisms, key=lambda m: MECHANISM_RANK.get(m, DEFAULT_RANK))
    instruction = next(
        (c.get("human_action_required") for c in members_sorted if c.get("human_action_required")),
        None,
    ) or _default_instruction(mechanism)
    return {
        "title": group.get("display_name", group_key),
        "mechanism": mechanism,
        "covers": covers,
        "instruction": instruction,
        "sort_key": group_key,
    }


def build_queue(cases: list, brokers_data: dict, include_failed: bool = False) -> list:
    """
    Build the numbered action-queue items for the given cases.

    Pure function: takes an already-loaded case list and brokers.json data,
    so it never touches the case store or filesystem, and produces the same
    output every time it's called with the same inputs (numbering is
    reassigned deterministically here, not carried over from the store).
    """
    brokers_by_id = {b["id"]: b for b in brokers_data.get("brokers", [])}
    owner_groups = brokers_data.get("owner_groups", {})
    group_of_broker = {member: key for key, group in owner_groups.items() for member in group.get("members", [])}

    selected = [c for c in cases if is_selected(c, include_failed)]

    grouped: dict[str, list] = {}
    standalone: list = []
    for case in selected:
        group_key = group_of_broker.get(case.get("broker_id"))
        if group_key:
            grouped.setdefault(group_key, []).append(case)
        else:
            standalone.append(case)

    items = []
    for group_key, members in grouped.items():
        # Only collapse when 2+ *selected* cases share the group — a lone
        # selected member of a group is just a normal standalone item.
        if len(members) >= 2:
            items.append(_build_group_item(group_key, members, owner_groups[group_key], brokers_by_id))
        else:
            standalone.extend(members)

    for case in standalone:
        items.append(_build_single_item(case, brokers_by_id))

    # Sort by mechanism rank, then a stable per-item key (group key or
    # broker_id) — independent of case-store iteration order, so repeated
    # runs on an unchanged store are byte-identical.
    items.sort(key=lambda it: (MECHANISM_RANK.get(it["mechanism"], DEFAULT_RANK), it["sort_key"]))
    for i, item in enumerate(items, start=1):
        item["number"] = i
    return items


def render_markdown(items: list, limit: Optional[int] = None) -> str:
    if not items:
        return "# QuietLine — Action Queue\n\nNothing needs your attention right now. All caught up.\n"

    total = len(items)
    shown = items[:limit] if limit else items
    remaining = total - len(shown)

    noun_verb = "site needs" if total == 1 else "sites need"
    lines = ["# QuietLine — Action Queue", "", f"{total} {noun_verb} your attention.", ""]

    current_section = None
    for item in shown:
        section = _section_title(item["mechanism"])
        if section != current_section:
            lines.append(f"## {section}")
            lines.append("")
            current_section = section
        covers_note = f" (covers: {', '.join(item['covers'])})" if len(item["covers"]) > 1 else ""
        lines.append(f"{item['number']}. **{item['title']}**{covers_note} — {item['instruction']}")
        lines.append("")

    if remaining > 0:
        item_word = "item" if remaining == 1 else "items"
        lines.append(f"*{remaining} more {item_word} pending — raise --limit to see them.*")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_json(items: list, limit: Optional[int] = None) -> str:
    total = len(items)
    shown = items[:limit] if limit else items
    remaining = total - len(shown)
    payload = {
        "count": total,
        "shown": len(shown),
        "remaining": remaining,
        "items": [{k: v for k, v in item.items() if k != "sort_key"} for item in shown],
    }
    return json.dumps(payload, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the QuietLine human-action queue")
    parser.add_argument(
        "--case-store",
        help="Path to cases.json (default: ~/.quietline/cases.json)",
    )
    parser.add_argument(
        "--output",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Show only the first N items (batching); notes how many remain",
    )
    parser.add_argument(
        "--include-failed",
        action="store_true",
        help="Also include 'failed' cases that carry a pending human_action_required",
    )
    args = parser.parse_args()

    if args.case_store:
        os.environ["QUIETLINE_CASE_STORE"] = args.case_store

    # LATE IMPORT (matches deadlines.py's main()): case_store.py fixes
    # CASE_STORE_PATH from QUIETLINE_CASE_STORE at import time, so the env
    # var must be set above *before* this import runs for --case-store to
    # take effect.
    import case_store as cs  # noqa: PLC0415

    cases = cs.list_cases()
    brokers_data = _load_brokers()
    items = build_queue(cases, brokers_data, include_failed=args.include_failed)

    if args.output == "json":
        print(render_json(items, limit=args.limit))
    else:
        print(render_markdown(items, limit=args.limit))

    sys.exit(0)


if __name__ == "__main__":
    main()
