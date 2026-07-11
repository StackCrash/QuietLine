"""
Verify that a broker's opt-out URL is live.

On non-200 / timeout / missing URL, falls back to the broker's homepage and
scans for a "Privacy" or "Do Not Sell My Info" footer-style link. Returns a
structured result that discover.py and SKILL.md can act on.

SAFE-4 domain-scope hardening: this module no longer trusts a redirect
target, or a homepage-scraped link, just because the HTTP response looked
healthy. Some brokers maliciously redirect (or open a new tab and redirect
the original one) to an out-of-scope site once a form loads. Every URL this
module is about to probe or accept is checked against scripts/url_guard.py's
per-broker domain allowlist:

  - `_find_privacy_link` resolves each homepage candidate href with urljoin
    and discards it WITHOUT making any HTTP request at all if url_guard.check
    says it's out of scope — untrusted homepage HTML must not be able to
    steer even a HEAD request to an attacker's host.
  - `_check_url` takes the FINAL URL after redirects (`resp.url`) and runs
    it through url_guard.check. A response that is off-domain is never
    reported "live", regardless of its HTTP status: a 200 on the wrong
    domain is not "live".

verify()'s result dict gains an "off_domain" key (see verify()'s docstring):
True when the stored-URL check, or the accepted fallback link, resolved to a
URL outside the broker's domain scope. --dry-run keeps returning canned
results with no network access and off_domain always False.

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

sys.path.insert(0, str(Path(__file__).parent))
import url_guard

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


def _load_registry() -> dict:
    """Load data/brokers.json once. Callers pass the result to _load_broker/_check_url/etc."""
    with open(BROKERS_PATH) as f:
        return json.load(f)


def _load_broker(broker_id: str, brokers_data: dict) -> Optional[dict]:
    return next((b for b in brokers_data.get("brokers", []) if b["id"] == broker_id), None)


def _check_url(url: str, broker_id: str, brokers_data: dict) -> dict:
    """
    Probe `url` and return {"live", "http_status", "off_domain", "final_url"}.

    Follows redirects, then checks the FINAL URL (resp.url) against
    url_guard's per-broker domain allowlist. If the final URL is out of
    scope, off_domain is True and live is False regardless of HTTP status —
    a 200 on the wrong domain is not "live". Requires the requests library.
    """
    try:
        import requests  # noqa: PLC0415

        resp = requests.head(url, timeout=TIMEOUT_SECONDS, allow_redirects=True)
        if resp.status_code == 405:
            resp = requests.get(url, timeout=TIMEOUT_SECONDS, allow_redirects=True)

        final_url = resp.url
        verdict = url_guard.check(broker_id, final_url, brokers_data)
        if not verdict["in_scope"]:
            return {"live": False, "http_status": resp.status_code, "off_domain": True, "final_url": final_url}

        return {
            "live": resp.status_code < 400,
            "http_status": resp.status_code,
            "off_domain": False,
            "final_url": final_url,
        }
    except Exception:
        return {"live": False, "http_status": 0, "off_domain": False, "final_url": None}


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


def _find_privacy_link(homepage: str, broker_id: str, brokers_data: dict) -> Optional[str]:
    """
    Fetch the broker homepage and scan for a privacy / opt-out footer link.
    Returns the resolved URL if found, None otherwise.

    Untrusted homepage HTML must not be able to steer even a HEAD request to
    an attacker's host: each candidate href is resolved with urljoin against
    the homepage and, before any HTTP request is made against it, checked
    with url_guard.check — an out-of-scope candidate is skipped entirely.
    """
    try:
        import requests  # noqa: PLC0415

        resp = requests.get(homepage, timeout=TIMEOUT_SECONDS)
        links = extract_links(resp.text)

        for href in match_privacy_links(links):
            resolved = urljoin(homepage, href)
            if not url_guard.check(broker_id, resolved, brokers_data)["in_scope"]:
                continue
            check_result = _check_url(resolved, broker_id, brokers_data)
            if check_result["live"]:
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
            off_domain: bool,
            checked_at: iso8601,
        }

    "off_domain" is True when the stored opt_out_url's (or the accepted
    fallback link's) final URL, after following redirects, resolved outside
    the broker's url_guard.py domain scope — see this module's docstring.
    --dry-run makes no network calls and always reports off_domain=False.
    """
    brokers_data = _load_registry()
    broker = _load_broker(broker_id, brokers_data)
    if broker is None:
        return {
            "broker_id": broker_id,
            "live": False,
            "resolved_url": None,
            "source": "not_found",
            "http_status": 0,
            "off_domain": False,
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
            "off_domain": False,
            "checked_at": _now(),
            "dry_run": True,
        }

    stored_url = broker.get("opt_out_url")
    if stored_url:
        check_result = _check_url(stored_url, broker_id, brokers_data)
        if check_result["live"]:
            return {
                "broker_id": broker_id,
                "live": True,
                "resolved_url": stored_url,
                "source": "stored",
                "http_status": check_result["http_status"],
                "off_domain": False,
                "checked_at": _now(),
            }
        if check_result["off_domain"]:
            return {
                "broker_id": broker_id,
                "live": False,
                "resolved_url": stored_url,
                "source": "stored",
                "http_status": check_result["http_status"],
                "off_domain": True,
                "checked_at": _now(),
            }

    # Fallback: scan the homepage for a privacy link
    homepage = broker.get("homepage", "")
    if homepage:
        fallback_url = _find_privacy_link(homepage, broker_id, brokers_data)
        if fallback_url:
            return {
                "broker_id": broker_id,
                "live": True,
                "resolved_url": fallback_url,
                "source": "fallback",
                "http_status": 200,
                "off_domain": False,
                "checked_at": _now(),
            }

    return {
        "broker_id": broker_id,
        "live": False,
        "resolved_url": None,
        "source": "not_found",
        "http_status": 0,
        "off_domain": False,
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
