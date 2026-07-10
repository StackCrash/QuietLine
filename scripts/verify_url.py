"""
Verify that a broker's opt-out URL is live.

On non-200 / timeout / missing URL, falls back to the broker's homepage and
scans for a "Privacy" or "Do Not Sell My Info" footer-style link. Returns a
structured result that discover.py and SKILL.md can act on.

In --dry-run mode, returns canned successful results without making real HTTP
requests — used by the eval harness to test the script contract.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

BROKERS_PATH = Path(__file__).parent.parent / "data" / "brokers.json"

PRIVACY_LINK_PATTERNS = [
    "do not sell",
    "opt out",
    "opt-out",
    "privacy request",
    "data request",
    "personal information",
    "remove my",
    "delete my",
    "privacy",
]

TIMEOUT_SECONDS = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_broker(broker_id: str) -> Optional[dict]:
    with open(BROKERS_PATH) as f:
        data = json.load(f)
    return next((b for b in data["brokers"] if b["id"] == broker_id), None)


def _check_url(url: str) -> tuple[bool, int]:
    """Return (is_live, http_status). Requires requests library."""
    try:
        import requests  # noqa: PLC0415

        resp = requests.head(url, timeout=TIMEOUT_SECONDS, allow_redirects=True)
        if resp.status_code == 405:
            resp = requests.get(url, timeout=TIMEOUT_SECONDS, allow_redirects=True)
        return resp.status_code < 400, resp.status_code
    except Exception:
        return False, 0


class LinkExtractor(HTMLParser):
    """
    Collects (href, combined-text) pairs for every <a> tag in an HTML
    document. "combined-text" is the anchor's visible text (accumulated
    across any nested tags, e.g. <a href="/p"><span>Privacy</span> Choices</a>)
    plus its `title` attribute, since brokers put the matching phrase in
    either place.

    Tolerant of malformed markup (unclosed anchors, stray end tags) because
    html.parser.HTMLParser itself never raises on malformed HTML — an anchor
    that's never closed simply never gets finalized into self.links.
    """

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._in_anchor: bool = False
        self._current_href: str = ""
        self._current_title: str = ""
        self._current_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "a":
            attrs_dict = dict(attrs)
            self._in_anchor = True
            self._current_href = attrs_dict.get("href") or ""
            self._current_title = attrs_dict.get("title") or ""
            self._current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._current_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_anchor:
            visible_text = "".join(self._current_text_parts).strip()
            combined = f"{visible_text} {self._current_title}".strip()
            self.links.append((self._current_href, combined))
            self._in_anchor = False
            self._current_href = ""
            self._current_title = ""
            self._current_text_parts = []


def extract_links(html: str) -> list[tuple[str, str]]:
    """
    Parse an HTML document and return (href, text) pairs for every anchor,
    where `text` combines the anchor's visible text with its `title`
    attribute. Pure and offline — no network, no filesystem.
    """
    parser = LinkExtractor()
    parser.feed(html)
    return parser.links


def match_privacy_links(links: list[tuple[str, str]]) -> list[str]:
    """
    Filter (href, text) pairs down to hrefs whose href or text matches one of
    PRIVACY_LINK_PATTERNS. Order-preserving; pure and offline.
    """
    matched = []
    for href, text in links:
        combined = f"{href} {text}".lower()
        if any(pat in combined for pat in PRIVACY_LINK_PATTERNS):
            matched.append(href)
    return matched


def _find_privacy_link(homepage: str) -> Optional[str]:
    """
    Fetch the broker homepage and scan for a privacy / opt-out footer link.
    Returns the resolved URL if found, None otherwise.
    """
    try:
        import requests  # noqa: PLC0415

        resp = requests.get(homepage, timeout=TIMEOUT_SECONDS)
        links = extract_links(resp.text)

        for href in match_privacy_links(links):
            resolved = urljoin(homepage, href)
            live, _ = _check_url(resolved)
            if live:
                return resolved
    # Best-effort fallback: any failure here just means "no privacy link found".
    except Exception:  # noqa: S110  # nosec B110
        pass
    return None


def verify(broker_id: str, dry_run: bool = False) -> dict:
    """
    Verify the opt-out URL for a broker.

    Returns:
        {
            broker_id: str,
            live: bool,
            resolved_url: str | None,
            source: "stored" | "fallback" | "not_found",
            http_status: int,
            checked_at: iso8601,
        }
    """
    broker = _load_broker(broker_id)
    if broker is None:
        return {
            "broker_id": broker_id,
            "live": False,
            "resolved_url": None,
            "source": "not_found",
            "http_status": 0,
            "checked_at": _now(),
            "error": f"broker_id '{broker_id}' not found in brokers.json",
        }

    if dry_run:
        stored_url = broker.get("opt_out_url")
        return {
            "broker_id": broker_id,
            "live": stored_url is not None,
            "resolved_url": stored_url,
            "source": "stored" if stored_url else "not_found",
            "http_status": 200 if stored_url else 0,
            "checked_at": _now(),
            "dry_run": True,
        }

    stored_url = broker.get("opt_out_url")
    if stored_url:
        live, status = _check_url(stored_url)
        if live:
            return {
                "broker_id": broker_id,
                "live": True,
                "resolved_url": stored_url,
                "source": "stored",
                "http_status": status,
                "checked_at": _now(),
            }

    # Fallback: scan the homepage for a privacy link
    homepage = broker.get("homepage", "")
    if homepage:
        fallback_url = _find_privacy_link(homepage)
        if fallback_url:
            return {
                "broker_id": broker_id,
                "live": True,
                "resolved_url": fallback_url,
                "source": "fallback",
                "http_status": 200,
                "checked_at": _now(),
            }

    return {
        "broker_id": broker_id,
        "live": False,
        "resolved_url": None,
        "source": "not_found",
        "http_status": 0,
        "checked_at": _now(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a broker opt-out URL")
    parser.add_argument("--broker-id", required=True, help="Broker ID from brokers.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Return canned results without real HTTP requests (for testing)",
    )
    args = parser.parse_args()

    result = verify(args.broker_id, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    if not result["live"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
