"""
Discovery: identify which brokers in the registry may hold the user's data.

Loads data/brokers.json, deduplicates by owner group (so a single submission
covers the whole PeopleConnect network rather than filing per-brand), and
emits a Discovery Report row per broker or owner group.

Real web-search enrichment (checking live listings) is a later milestone.
In the current skeleton, discovery returns all brokers with publicly_searchable=true
as "possibly listed" — the agent's browsing step confirms or dismisses each.

In --dry-run mode, returns results without real web lookups.
"""

import argparse
import json
from pathlib import Path
from typing import Optional

BROKERS_PATH = Path(__file__).parent.parent / "data" / "brokers.json"


def _load_brokers() -> dict:
    with open(BROKERS_PATH) as f:
        return json.load(f)


def _dedupe_by_owner(brokers: list, owner_groups: dict) -> list:
    """
    Collapse broker entries that share an owner group into a single consolidated
    entry pointing at the centralized opt-out URL.

    Returns a list where consolidated networks appear once (with a 'covers' list)
    and standalone brokers appear as-is.
    """
    seen_owners: dict[str, dict] = {}
    standalone: list[dict] = []

    group_member_ids = {member_id for group in owner_groups.values() for member_id in group.get("members", [])}

    for broker in brokers:
        bid = broker["id"]

        matched_group_key = next(
            (k for k, g in owner_groups.items() if bid in g.get("members", [])),
            None,
        )

        if matched_group_key:
            if matched_group_key not in seen_owners:
                group = owner_groups[matched_group_key]
                seen_owners[matched_group_key] = {
                    "id": matched_group_key,
                    "name": group["display_name"],
                    "opt_out_url": group["centralized_opt_out_url"],
                    "mechanism_type": "web-form",
                    "difficulty": "easy",
                    "covers": group["members"],
                    "consolidated": True,
                    "notes": group.get("notes", ""),
                }
        elif bid not in group_member_ids:
            standalone.append(broker)

    return list(seen_owners.values()) + standalone


def discover(
    name: str,
    phone: str,
    state: Optional[str] = None,
    dry_run: bool = False,
) -> list:
    """
    Run discovery for the given identity and return a list of Discovery Report rows.

    Each row contains:
        id, name, opt_out_url, mechanism_type, difficulty, publicly_searchable,
        jurisdiction_path, consolidated (bool), covers (list), notes
    """
    data = _load_brokers()
    brokers = data["brokers"]
    owner_groups = data.get("owner_groups", {})

    # In a future milestone, filter by live-search confirmation here.
    # For now, include all publicly_searchable brokers as "possibly listed".
    candidates = [b for b in brokers if b.get("publicly_searchable")]

    deduped = _dedupe_by_owner(candidates, owner_groups)

    # Annotate with jurisdiction path
    ca_drop_eligible = (state or "").upper() == "CA"
    report = []
    for entry in deduped:
        row = {
            "id": entry["id"],
            "name": entry["name"],
            "opt_out_url": entry.get("opt_out_url"),
            "mechanism_type": entry["mechanism_type"],
            "difficulty": entry.get("difficulty", "unknown"),
            "publicly_searchable": entry.get("publicly_searchable", False),
            "consolidated": entry.get("consolidated", False),
            "covers": entry.get("covers", []),
            "jurisdiction_path": ("ca-drop" if ca_drop_eligible and entry.get("ca_registered") else "standard"),
            "ca_registered": entry.get("ca_registered", False),
            "listing_url": None,
            "status": "possibly-listed",
            "notes": entry.get("notes", ""),
        }
        report.append(row)

    # Sort by risk: high-visibility sites first, then by difficulty
    difficulty_rank = {"easy": 0, "medium": 1, "hard": 2, "unknown": 3}
    report.sort(key=lambda r: (difficulty_rank.get(r["difficulty"], 3), r["name"].lower()))

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="QuietLine discovery scan")
    parser.add_argument("--name", required=True, help="Full legal name")
    parser.add_argument("--phone", required=True, help="Phone number to remove")
    parser.add_argument("--state", help="Two-letter state code (unlocks CA DROP path for CA)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Return results without live web searches (for testing)",
    )
    parser.add_argument(
        "--output",
        choices=["json", "table"],
        default="json",
        help="Output format",
    )
    args = parser.parse_args()

    report = discover(name=args.name, phone=args.phone, state=args.state, dry_run=args.dry_run)

    if args.output == "table":
        print(f"{'ID':<20} {'Name':<30} {'Mechanism':<20} {'Difficulty':<10} {'Jurisdiction Path'}")
        print("-" * 90)
        for row in report:
            covers = f" (+{len(row['covers']) - 1} brands)" if len(row["covers"]) > 1 else ""
            print(
                f"{row['id']:<20} {row['name'] + covers:<30} "
                f"{row['mechanism_type']:<20} {row['difficulty']:<10} {row['jurisdiction_path']}"
            )
        consolidated = sum(1 for r in report if r.get("consolidated"))
        print(f"\n{len(report)} broker target(s) ({consolidated} consolidated groups)")
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
