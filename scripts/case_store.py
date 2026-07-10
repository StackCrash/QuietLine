"""
Case store: read/write/update per-broker opt-out case records.

All records are persisted as a JSON file at CASE_STORE_PATH (default:
~/.quietline/cases.json). The store is append-safe — every state transition
writes a new audit_log entry rather than mutating prior history.

Idempotency guarantee: upsert() refuses to regress a 'confirmed-removed' case
back to 'submitted' or any earlier status. This prevents re-submission of
already-completed removals.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import jsonschema

    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False

CASE_STORE_PATH = Path(os.environ.get("QUIETLINE_CASE_STORE", Path.home() / ".quietline" / "cases.json"))
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "case_record.json"

TERMINAL_STATUSES = {"confirmed-removed"}
STATUS_ORDER = [
    "discovered",
    "submitted",
    "awaiting-confirmation",
    "awaiting-user",
    "confirmed-removed",
    "failed",
    "reappeared",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_schema() -> Optional[dict]:
    if not _HAS_JSONSCHEMA or not SCHEMA_PATH.exists():
        return None
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def _load_store() -> dict:
    if not CASE_STORE_PATH.exists():
        return {"profile": None, "affirmation": None, "cases": {}}
    with open(CASE_STORE_PATH) as f:
        return json.load(f)


def _save_store(store: dict) -> None:
    CASE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CASE_STORE_PATH, "w") as f:
        json.dump(store, f, indent=2)


def _validate(record: dict, schema: Optional[dict]) -> None:
    if schema is None or not _HAS_JSONSCHEMA:
        return
    jsonschema.validate(record, schema)


def init_profile(profile: dict) -> None:
    """Store the user's identity profile (minimum PII for this run)."""
    store = _load_store()
    store["profile"] = {**profile, "created_at": _now()}
    _save_store(store)
    print(f"Profile initialised for {profile.get('name', '(unnamed)')}")


def mark_affirmation(session_id: str = "cli") -> None:
    """Record the ownership affirmation required by SAFE-1."""
    store = _load_store()
    store["affirmation"] = {"timestamp": _now(), "session_id": session_id}
    _save_store(store)
    print(f"Ownership affirmation recorded at {store['affirmation']['timestamp']}")


def get(broker_id: str) -> Optional[dict]:
    """Return the case record for broker_id, or None if not found."""
    store = _load_store()
    return store["cases"].get(broker_id)


def upsert(record: dict) -> dict:
    """
    Insert or update a case record.

    Idempotency guard: will not regress a 'confirmed-removed' case to any
    earlier status. Returns the resulting record.

    `record` may be a partial patch (as sent by `mark()`) rather than a full
    case record — schema validation runs after merging against any existing
    record, against the complete object that actually gets persisted.
    """
    broker_id = record["broker_id"]
    store = _load_store()
    existing = store["cases"].get(broker_id)

    if existing and existing.get("status") in TERMINAL_STATUSES:
        new_status = record.get("status", existing["status"])
        if new_status != existing["status"]:
            print(
                f"[idempotency] {broker_id} is already '{existing['status']}'; refusing to regress to '{new_status}'.",
                file=sys.stderr,
            )
            return existing

    if existing:
        merged = {**existing, **record}
        audit_log = existing.get("audit_log", [])
        audit_log.append(
            {
                "timestamp": _now(),
                "action": f"status → {record.get('status', existing.get('status'))}",
                "actor": record.get("_actor", "agent"),
                "detail": record.get("notes"),
            }
        )
        merged["audit_log"] = audit_log
        merged.pop("_actor", None)
    else:
        merged = {
            "created_at": _now(),
            "audit_log": [
                {
                    "timestamp": _now(),
                    "action": "created",
                    "actor": record.get("_actor", "agent"),
                    "detail": None,
                }
            ],
            **record,
        }
        merged.pop("_actor", None)
        if "pii_sent" not in merged:
            merged["pii_sent"] = []

    schema = _load_schema()
    _validate(merged, schema)

    store["cases"][broker_id] = merged
    _save_store(store)
    return merged


def mark(broker_id: str, status: str, note: Optional[str] = None, actor: str = "agent") -> dict:
    """Convenience wrapper: update just the status (and optional note) of a case."""
    existing = get(broker_id)
    if existing is None:
        raise ValueError(f"No case found for broker_id '{broker_id}'. Run discover.py first.")
    patch = {"broker_id": broker_id, "status": status, "_actor": actor}
    if note:
        patch["notes"] = note
    now = _now()
    if status == "submitted" and not existing.get("submitted_at"):
        patch["submitted_at"] = now
    if status == "confirmed-removed" and not existing.get("confirmed_at"):
        patch["confirmed_at"] = now
    return upsert(patch)


def list_cases(status: Optional[str] = None) -> list:
    """Return all cases, optionally filtered by status."""
    store = _load_store()
    cases = list(store["cases"].values())
    if status:
        cases = [c for c in cases if c.get("status") == status]
    return cases


def purge(confirm: bool = False) -> None:
    """Delete the entire case store and identity profile. Irreversible."""
    if not confirm:
        print("Pass --confirm to purge all QuietLine data.", file=sys.stderr)
        sys.exit(1)
    if CASE_STORE_PATH.exists():
        CASE_STORE_PATH.unlink()
        print(f"Purged {CASE_STORE_PATH}")
    else:
        print("Nothing to purge.")


def main() -> None:
    parser = argparse.ArgumentParser(description="QuietLine case store")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialise a user identity profile")
    p_init.add_argument("--name", required=True)
    p_init.add_argument("--phone", required=True)
    p_init.add_argument("--state", required=True)
    p_init.add_argument("--extra", default="{}", help="JSON dict of additional fields")

    p_affirm = sub.add_parser("mark-affirmation", help="Record the ownership affirmation (SAFE-1)")
    p_affirm.add_argument("--session-id", default="cli")

    p_get = sub.add_parser("get", help="Print a single case record")
    p_get.add_argument("broker_id")

    p_mark = sub.add_parser("mark", help="Update the status of a case")
    p_mark.add_argument("--broker-id", required=True)
    p_mark.add_argument("--status", required=True, choices=STATUS_ORDER)
    p_mark.add_argument("--note")
    p_mark.add_argument("--actor", default="agent", choices=["agent", "user"])

    p_list = sub.add_parser("list", help="List all cases (optionally filtered by status)")
    p_list.add_argument("--status", choices=STATUS_ORDER)

    p_purge = sub.add_parser("purge", help="Delete all QuietLine data")
    p_purge.add_argument("--confirm", action="store_true")

    args = parser.parse_args()

    if args.command == "init":
        profile = {"name": args.name, "phone": args.phone, "state": args.state, **json.loads(args.extra)}
        init_profile(profile)

    elif args.command == "mark-affirmation":
        mark_affirmation(session_id=args.session_id)

    elif args.command == "get":
        record = get(args.broker_id)
        if record:
            print(json.dumps(record, indent=2))
        else:
            print(f"No case found for '{args.broker_id}'", file=sys.stderr)
            sys.exit(1)

    elif args.command == "mark":
        result = mark(args.broker_id, args.status, note=args.note, actor=args.actor)
        print(json.dumps(result, indent=2))

    elif args.command == "list":
        cases = list_cases(status=args.status)
        print(json.dumps(cases, indent=2))

    elif args.command == "purge":
        purge(confirm=args.confirm)


if __name__ == "__main__":
    main()
