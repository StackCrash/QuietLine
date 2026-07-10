"""
Validate and merge the broker registry (data/brokers.json).

The registry grows via fragment files researched independently (one broker
family per fragment) before being merged into the canonical file. This
script is the structural gatekeeper for both stages:

  validate  — check a registry (or a fragment) against the required schema
              shape: required fields, enums, URL/date formats, and (for
              full registries only) owner_groups <-> broker consistency.
  merge     — combine a base registry with a glob of fragment files, with
              fragments winning on id/key conflicts, then validate the
              result.

Kept deliberately dependency-free (stdlib only) so it can run before any
other tooling is trusted.
"""

import argparse
import glob
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

DEFAULT_REGISTRY_PATH = Path(__file__).parent.parent / "data" / "brokers.json"

REQUIRED_FIELDS = [
    "id",
    "name",
    "owner",
    "parent_id",
    "homepage",
    "mechanism_type",
    "captcha_required",
    "publicly_searchable",
    "ca_registered",
    "jurisdiction_notes",
    "difficulty",
    "reappearance_tendency",
    "last_verified",
    "notes",
]
NULLABLE_FIELDS = {"parent_id", "jurisdiction_notes"}
STRING_FIELDS = {
    "id",
    "name",
    "owner",
    "parent_id",
    "homepage",
    "mechanism_type",
    "jurisdiction_notes",
    "difficulty",
    "reappearance_tendency",
    "last_verified",
    "notes",
    "opt_out_url",
    "opt_out_email",
}
BOOL_FIELDS = ("captcha_required", "publicly_searchable", "ca_registered")

MECHANISM_TYPES = {"web-form", "email-request", "account-required", "phone-verification", "id-upload"}
DIFFICULTIES = {"easy", "medium", "hard"}
REAPPEARANCE_TENDENCIES = {"low", "medium", "high"}

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _check_broker(broker: Any, idx: int) -> list[str]:
    """Return a list of '<label>: <message>' errors for a single broker entry."""
    if not isinstance(broker, dict):
        return [f"brokers[{idx}]: entry must be an object"]

    bid = broker.get("id")
    label = bid if isinstance(bid, str) and bid else f"brokers[{idx}]"
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in broker:
            errors.append(f"{label}: missing required field '{field}'")
        elif field not in NULLABLE_FIELDS and broker[field] is None:
            errors.append(f"{label}: field '{field}' must not be null")

    for field in STRING_FIELDS:
        value = broker.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{label}: field '{field}' must be a string")

    if isinstance(bid, str) and bid and not ID_RE.match(bid):
        errors.append(f"{label}: id '{bid}' does not match required pattern ^[a-z0-9][a-z0-9_-]*$")

    for field in BOOL_FIELDS:
        if field in broker and not isinstance(broker[field], bool):
            errors.append(f"{label}: field '{field}' must be a boolean")

    mechanism_type = broker.get("mechanism_type")
    if isinstance(mechanism_type, str) and mechanism_type not in MECHANISM_TYPES:
        errors.append(f"{label}: mechanism_type '{mechanism_type}' must be one of {sorted(MECHANISM_TYPES)}")

    difficulty = broker.get("difficulty")
    if isinstance(difficulty, str) and difficulty not in DIFFICULTIES:
        errors.append(f"{label}: difficulty '{difficulty}' must be one of {sorted(DIFFICULTIES)}")

    reappearance = broker.get("reappearance_tendency")
    if isinstance(reappearance, str) and reappearance not in REAPPEARANCE_TENDENCIES:
        errors.append(
            f"{label}: reappearance_tendency '{reappearance}' must be one of {sorted(REAPPEARANCE_TENDENCIES)}"
        )

    last_verified = broker.get("last_verified")
    if isinstance(last_verified, str):
        try:
            datetime.strptime(last_verified, "%Y-%m-%d")
        except ValueError:
            errors.append(f"{label}: last_verified '{last_verified}' is not a valid ISO date (YYYY-MM-DD)")

    homepage = broker.get("homepage")
    if isinstance(homepage, str) and not homepage.startswith("https://"):
        errors.append(f"{label}: homepage must start with 'https://'")

    opt_out_url = broker.get("opt_out_url")
    opt_out_email = broker.get("opt_out_email")
    if opt_out_url is not None:
        if isinstance(opt_out_url, str) and not opt_out_url.startswith("https://"):
            errors.append(f"{label}: opt_out_url must start with 'https://'")
    else:
        has_email_fallback = (
            mechanism_type == "email-request" and isinstance(opt_out_email, str) and opt_out_email.strip() != ""
        )
        if not has_email_fallback:
            errors.append(
                f"{label}: opt_out_url is required unless mechanism_type is 'email-request' "
                "with a non-empty opt_out_email"
            )

    return errors


def _check_duplicate_ids(brokers: list) -> list[str]:
    errors: list[str] = []
    seen_ids: dict[str, int] = {}
    for idx, broker in enumerate(brokers):
        if not isinstance(broker, dict):
            continue
        bid = broker.get("id")
        if not (isinstance(bid, str) and bid):
            continue
        if bid in seen_ids:
            errors.append(f"brokers[{idx}]: duplicate id '{bid}' (already used at brokers[{seen_ids[bid]}])")
        else:
            seen_ids[bid] = idx
    return errors


def _check_owner_groups(brokers: list, owner_groups: dict) -> list[str]:
    """
    Verify owner_groups <-> broker parent_id consistency in both directions:
      - every member listed in a group must exist as a broker with matching parent_id
      - every broker with a non-null parent_id must be listed in that group's members
    """
    errors: list[str] = []
    broker_by_id = {
        broker["id"]: broker
        for broker in brokers
        if isinstance(broker, dict) and isinstance(broker.get("id"), str) and broker.get("id")
    }

    for group_key, group in owner_groups.items():
        if not isinstance(group, dict):
            errors.append(f"owner_groups.{group_key}: value must be an object")
            continue

        if not isinstance(group.get("name"), str) and not isinstance(group.get("display_name"), str):
            errors.append(f"owner_groups.{group_key}: missing a 'name' (or 'display_name') string")

        members = group.get("members")
        if not isinstance(members, list) or not members:
            errors.append(f"owner_groups.{group_key}: 'members' must be a non-empty list")
            continue

        for member_id in members:
            broker = broker_by_id.get(member_id)
            if broker is None:
                errors.append(f"owner_groups.{group_key}: member '{member_id}' does not exist as a broker entry")
            elif broker.get("parent_id") != group_key:
                errors.append(
                    f"owner_groups.{group_key}: member '{member_id}' has parent_id "
                    f"'{broker.get('parent_id')}', expected '{group_key}'"
                )

    for broker in brokers:
        if not isinstance(broker, dict):
            continue
        parent_id = broker.get("parent_id")
        bid = broker.get("id")
        if not parent_id:
            continue
        group = owner_groups.get(parent_id)
        if group is None:
            errors.append(
                f"owner_groups.{parent_id}: broker '{bid}' has parent_id '{parent_id}' but no such owner group exists"
            )
            continue
        group_members = group.get("members") if isinstance(group, dict) else None
        if isinstance(group_members, list) and bid not in group_members:
            errors.append(
                f"owner_groups.{parent_id}: broker '{bid}' has parent_id '{parent_id}' but is not listed in members"
            )

    return errors


def validate_registry(data: Any, fragment: bool = False) -> list[str]:
    """
    Validate a registry (or fragment) document. Returns a list of
    '<label>: <message>' error strings (empty list means clean).
    """
    if not isinstance(data, dict):
        return ["root: registry file must contain a JSON object"]

    errors: list[str] = []

    if fragment:
        fragment_id = data.get("fragment_id")
        if not (isinstance(fragment_id, str) and fragment_id):
            errors.append("root: fragment mode requires a non-empty 'fragment_id'")

    brokers = data.get("brokers")
    if not isinstance(brokers, list):
        errors.append("root: 'brokers' must be a list")
        brokers = []

    owner_groups = data.get("owner_groups") or {}
    if not isinstance(owner_groups, dict):
        errors.append("root: 'owner_groups' must be an object")
        owner_groups = {}

    for idx, broker in enumerate(brokers):
        errors.extend(_check_broker(broker, idx))

    errors.extend(_check_duplicate_ids(brokers))

    if not fragment:
        errors.extend(_check_owner_groups(brokers, owner_groups))

    return errors


def _load_json(path: Path) -> Any:
    with open(path) as f:
        return json.load(f)


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        data = _load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR root: could not read/parse '{path}': {exc}", file=sys.stderr)
        return 1

    errors = validate_registry(data, fragment=args.fragment)
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)

    broker_count = len(data.get("brokers", [])) if isinstance(data, dict) else 0
    if errors:
        print(f"{len(errors)} error(s) found in {path} ({broker_count} brokers checked)", file=sys.stderr)
        return 1

    print(f"OK: {path} is valid ({broker_count} brokers)", file=sys.stderr)
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    base_path = Path(args.base)
    out_path = Path(args.out)

    base_data: dict = _load_json(base_path) if base_path.exists() else {"brokers": [], "owner_groups": {}}

    brokers_by_id: dict[str, dict] = {
        broker["id"]: broker for broker in base_data.get("brokers", []) if isinstance(broker, dict) and "id" in broker
    }
    owner_groups: dict[str, dict] = dict(base_data.get("owner_groups") or {})

    fragment_paths = sorted(glob.glob(args.fragments))
    for fragment_path_str in fragment_paths:
        fragment_data = _load_json(Path(fragment_path_str))
        for broker in fragment_data.get("brokers", []):
            if isinstance(broker, dict) and "id" in broker:
                brokers_by_id[broker["id"]] = broker
        for key, value in (fragment_data.get("owner_groups") or {}).items():
            owner_groups[key] = value

    merged_brokers = sorted(brokers_by_id.values(), key=lambda broker: broker["id"])

    result = dict(base_data)
    result["brokers"] = merged_brokers
    result["owner_groups"] = owner_groups
    result["last_refreshed"] = date.today().isoformat()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n")

    print(
        f"Merged {len(fragment_paths)} fragment(s) + base into {out_path} ({len(merged_brokers)} brokers)",
        file=sys.stderr,
    )

    errors = validate_registry(result, fragment=False)
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    if errors:
        print(f"{len(errors)} error(s) found in merged output {out_path}", file=sys.stderr)
        return 1

    print(f"OK: merged output {out_path} is valid", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and merge the QuietLine broker registry")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Validate a registry or fragment file")
    p_validate.add_argument(
        "--path",
        default=str(DEFAULT_REGISTRY_PATH),
        help=f"Path to registry JSON (default: {DEFAULT_REGISTRY_PATH})",
    )
    p_validate.add_argument(
        "--fragment",
        action="store_true",
        help="Validate as a fragment: require 'fragment_id', skip owner_groups consistency checks",
    )

    p_merge = sub.add_parser("merge", help="Merge fragment files into a base registry")
    p_merge.add_argument("--fragments", required=True, help="Glob pattern for fragment JSON files")
    p_merge.add_argument("--base", required=True, help="Path to base registry JSON")
    p_merge.add_argument("--out", required=True, help="Path to write the merged registry JSON")

    args = parser.parse_args()

    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "merge":
        return cmd_merge(args)
    return 1  # unreachable: subparsers are required


if __name__ == "__main__":
    sys.exit(main())
