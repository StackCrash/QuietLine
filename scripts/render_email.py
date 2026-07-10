"""
Render a deletion or escalation email from a template.

Fills jurisdiction-parameterized fields from data/jurisdictions.json and
broker details from data/brokers.json. Does NOT send the email — sending
is the agent's responsibility via the Cowork email connector. Returns the
rendered subject + body as JSON for the agent to review and dispatch.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

JURISDICTIONS_PATH = Path(__file__).parent.parent / "data" / "jurisdictions.json"
BROKERS_PATH = Path(__file__).parent.parent / "data" / "brokers.json"
TEMPLATES_PATH = Path(__file__).parent.parent / "assets" / "templates"


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _get_broker(broker_id: str, brokers_data: dict) -> Optional[dict]:
    return next((b for b in brokers_data["brokers"] if b["id"] == broker_id), None)


def _get_jurisdiction(state: Optional[str], jurisdictions: dict) -> dict:
    key = (state or "").upper()
    return jurisdictions.get(key) or jurisdictions["default"]


def _fill_template(template: str, variables: dict) -> str:
    """Replace {var} placeholders; leave unmatched placeholders intact."""

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        val = variables.get(key)
        if val is None:
            return match.group(0)
        return str(val)

    return re.sub(r"\{(\w+)\}", replacer, template)


def _extract_subject(rendered: str) -> tuple[str, str]:
    """Split rendered template into (subject, body) on the first blank line after Subject."""
    lines = rendered.splitlines()
    subject = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("**Subject:**"):
            subject = line.replace("**Subject:**", "").strip()
            body_start = i + 2
            break
    body = "\n".join(lines[body_start:]).strip()
    return subject, body


def render(
    template_type: str,
    broker_id: str,
    state: Optional[str],
    user_name: str,
    phone_numbers: list[str],
    user_email: str,
    listing_url: Optional[str] = None,
    submitted_date: Optional[str] = None,
    deadline_date: Optional[str] = None,
    days_overdue: Optional[int] = None,
) -> dict:
    """
    Render a deletion or escalation email.

    Returns a dict with keys: broker_id, template, subject, body, to_address.
    """
    template_file = TEMPLATES_PATH / f"{template_type}_email.md"
    if not template_file.exists():
        raise FileNotFoundError(f"Template not found: {template_file}")

    brokers_data = _load_json(BROKERS_PATH)
    jurisdictions = _load_json(JURISDICTIONS_PATH)["jurisdictions"]

    broker = _get_broker(broker_id, brokers_data)
    if broker is None:
        raise ValueError(f"Broker '{broker_id}' not found in brokers.json")

    jur = _get_jurisdiction(state, jurisdictions)
    statutory_language = jur.get(f"{template_type}_language") or jur.get("statutory_language", "")
    rendered_language = statutory_language.format(submitted_date=submitted_date or date.today().isoformat())
    today = date.today().isoformat()

    variables = {
        "broker_name": broker["name"],
        "full_name": user_name,
        "phone_numbers": ", ".join(phone_numbers),
        "listing_url": listing_url or "(not found — please search your records for my name and phone number)",
        # Both keys are populated with the same jurisdiction-parameterized sentence:
        # deletion_email.md references {statutory_language}, escalation_email.md
        # references {escalation_language}. jur.get(f"{template_type}_language") already
        # selects the right sentence for the current template_type, so both placeholders
        # resolve correctly regardless of which template is being rendered.
        "statutory_language": rendered_language,
        "escalation_language": rendered_language,
        "submitted_date": submitted_date or today,
        "deadline_date": deadline_date or "",
        "days_overdue": str(days_overdue or 0),
        "followup_date": today,
        "user_email": user_email,
        "attorney_general_url": jur.get("attorney_general_url", "your state Attorney General's office"),
    }

    with open(template_file) as f:
        raw = f.read()

    # Strip markdown preamble (everything up to the first --- separator before the template)
    parts = raw.split("---")
    template_body = parts[-1].strip() if len(parts) > 1 else raw

    rendered = _fill_template(template_body, variables)
    subject, body = _extract_subject(rendered)

    homepage_host = broker["homepage"].replace("https://", "").replace("www.", "").rstrip("/")
    to_address = broker.get("opt_out_email") or f"privacy@{homepage_host}"

    return {
        "broker_id": broker_id,
        "broker_name": broker["name"],
        "template": template_type,
        "to": to_address,
        "subject": subject,
        "body": body,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a QuietLine opt-out email")
    parser.add_argument("--template", required=True, choices=["deletion", "escalation"])
    parser.add_argument("--broker-id", required=True)
    parser.add_argument("--state", help="Two-letter state code for statutory language")
    parser.add_argument("--name", required=True, help="User's full legal name")
    parser.add_argument("--phone", required=True, nargs="+", help="Phone number(s) to remove")
    parser.add_argument("--email", required=True, help="Opt-out email address")
    parser.add_argument("--listing-url")
    parser.add_argument("--submitted-date")
    parser.add_argument("--deadline-date")
    parser.add_argument("--days-overdue", type=int)
    args = parser.parse_args()

    try:
        result = render(
            template_type=args.template,
            broker_id=args.broker_id,
            state=args.state,
            user_name=args.name,
            phone_numbers=args.phone,
            user_email=args.email,
            listing_url=args.listing_url,
            submitted_date=args.submitted_date,
            deadline_date=args.deadline_date,
            days_overdue=args.days_overdue,
        )
        print(json.dumps(result, indent=2))
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
