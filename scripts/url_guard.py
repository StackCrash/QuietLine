"""
SAFE-4 verified-navigation gate: deterministic domain-scope verdicts.

Some data brokers maliciously redirect — or spawn a new tab/window and
redirect the ORIGINAL tab — to an out-of-scope site once a form is submitted
or a link is clicked. That bait-and-switch would otherwise trick a browsing
agent into typing PII into, or clicking "submit" on, a page that is no
longer the broker's own site.

The browsing agent must run this script twice per broker interaction:
  1. On the CURRENT page URL, before entering any PII and before clicking
     any submit/confirm control.
  2. On a confirmation-email link, before opening it.

Exit codes:
  0 — in scope  (the URL's registrable domain is on the broker's allowlist)
  4 — out of scope (unknown broker, disallowed scheme, userinfo trick,
      unparseable URL, or a registrable domain not on the allowlist)

Allowlist sources (see allowed_domains_for_broker): the broker's own
homepage and opt_out_url, its owner group's centralized_opt_out_url (if
any), and any broker-specific "allowed_domains" entries.

This module is entirely offline — stdlib only, no network access — so it
can run as a hard gate before any live HTTP request or form interaction.

PII rule: the verdict dict below contains only broker ids, domains, and
URLs supplied by the caller. Never add a user-identity field (name, phone,
address, DOB, email) to this module's output.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

BROKERS_PATH = Path(__file__).parent.parent / "data" / "brokers.json"

# Small, hardcoded set of two-part public suffixes this registry's brokers
# are known to use (or plausibly could use for an international mirror/
# affiliate). This is NOT the Public Suffix List (https://publicsuffix.org/)
# and is not trying to be one — it exists purely to avoid pulling in a new
# runtime dependency (e.g. `tldextract`/`publicsuffix2`) for a handful of
# common cases. A hostname that lives on an unlisted multi-part public
# suffix (any two-part suffix not in this set, or any three-part-or-longer
# suffix at all) will have its registrable domain computed INCORRECTLY by
# registrable_domain() below. This is a known, documented limitation, not a
# bug — if QuietLine needs to support a broker on an affected TLD, add the
# suffix here (a small data change) rather than reaching for a dependency.
TWO_PART_SUFFIXES = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.jp",
        "ne.jp",
        "or.jp",
        "co.nz",
        "com.br",
        "co.in",
        "co.za",
        "com.mx",
        "com.sg",
    }
)


def registrable_domain(hostname: str) -> str:
    """
    Return a naive eTLD+1 ("registrable domain") for hostname.

    This is a small hardcoded heuristic, NOT the Public Suffix List — see
    the TWO_PART_SUFFIXES comment above for the documented limitation:
    hostnames on an unlisted multi-part public suffix will be truncated
    incorrectly. Kept deliberately simple to avoid a new dependency.

    Rules: lowercase, strip a trailing dot; if the last two labels match an
    entry in TWO_PART_SUFFIXES, return the last three labels (or the whole
    hostname if it has fewer than three labels); otherwise return the last
    two labels (or the whole hostname if it has fewer than two).
    """
    host = hostname.lower().rstrip(".")
    labels = host.split(".")

    if len(labels) <= 2:
        return host

    last_two = ".".join(labels[-2:])
    if last_two in TWO_PART_SUFFIXES:
        return ".".join(labels[-3:])

    return last_two


def _host_of(url: str) -> Optional[str]:
    """Return urlparse(url).hostname, or None if the URL can't be parsed."""
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def allowed_domains_for_broker(broker_id: str, brokers_data: dict) -> set:
    """
    Return the set of registrable domains a broker's opt-out flow is allowed
    to touch: its homepage, its opt_out_url (if set), its owner group's
    centralized_opt_out_url (if the broker is a member of an owner group
    that has one — see data/brokers.json's owner_groups structure), and
    every entry of the broker's optional "allowed_domains" list (bare
    domains, each normalized through registrable_domain()).

    Returns an empty set if broker_id is not found in brokers_data.
    """
    broker = next(
        (b for b in brokers_data.get("brokers", []) if isinstance(b, dict) and b.get("id") == broker_id),
        None,
    )
    if broker is None:
        return set()

    domains: set = set()

    homepage_host = _host_of(broker.get("homepage") or "")
    if homepage_host:
        domains.add(registrable_domain(homepage_host))

    opt_out_url = broker.get("opt_out_url")
    if opt_out_url:
        opt_out_host = _host_of(opt_out_url)
        if opt_out_host:
            domains.add(registrable_domain(opt_out_host))

    for group in brokers_data.get("owner_groups", {}).values():
        if not isinstance(group, dict):
            continue
        if broker_id not in (group.get("members") or []):
            continue
        centralized_url = group.get("centralized_opt_out_url")
        if centralized_url:
            centralized_host = _host_of(centralized_url)
            if centralized_host:
                domains.add(registrable_domain(centralized_host))

    for entry in broker.get("allowed_domains") or []:
        if isinstance(entry, str) and entry:
            domains.add(registrable_domain(entry))

    return domains


def _classify_url(url: str, allowed: set) -> tuple:
    """
    Classify `url` against an already-resolved `allowed` domain set.

    Ordered checks (first match wins):
      1. scheme is not exactly "https"          -> "disallowed_scheme"
         (covers missing scheme, javascript:, data:, http:, etc.)
      2. userinfo present in the URL            -> "userinfo_present"
         (rejected UNCONDITIONALLY, even if the resolved host is allowed —
         https://evil.com@www.whitepages.com/ resolves to a legitimate
         host, and a host-only check would wrongly let it through)
      3. no parseable hostname                  -> "unparseable"
      4. registrable domain not on the allowlist -> "off_domain"
      5. otherwise                               -> "in_scope" (True)

    Returns (in_scope: bool, reason: str, registrable_domain: str | None).
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "unparseable", None

    if parsed.scheme != "https":
        return False, "disallowed_scheme", None

    if parsed.username is not None or "@" in parsed.netloc:
        return False, "userinfo_present", None

    host = parsed.hostname
    if not host:
        return False, "unparseable", None

    domain = registrable_domain(host)
    if domain not in allowed:
        return False, "off_domain", domain

    return True, "in_scope", domain


def check(broker_id: str, url: str, brokers_data: dict) -> dict:
    """
    Return a verified-navigation verdict for `url` against `broker_id`'s
    domain allowlist. See _classify_url for the ordered-check breakdown;
    this wraps it with the broker-not-found check and the response shape.

    Returns:
        {
            broker_id: str,
            url: str,
            in_scope: bool,
            reason: str,
            registrable_domain: str | None,
            allowed_domains: list[str] (sorted),
        }
    """
    allowed = allowed_domains_for_broker(broker_id, brokers_data)
    broker_exists = any(isinstance(b, dict) and b.get("id") == broker_id for b in brokers_data.get("brokers", []))

    if not broker_exists:
        in_scope, reason, domain = False, "broker_not_found", None
    else:
        in_scope, reason, domain = _classify_url(url, allowed)

    return {
        "broker_id": broker_id,
        "url": url,
        "in_scope": in_scope,
        "reason": reason,
        "registrable_domain": domain,
        "allowed_domains": sorted(allowed),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SAFE-4 verified-navigation check: verdict on whether a URL is in-scope for a broker"
    )
    parser.add_argument("--broker-id", required=True, help="Broker ID from data/brokers.json")
    parser.add_argument("--url", required=True, help="URL to check (current page URL or a candidate link)")
    args = parser.parse_args()

    with open(BROKERS_PATH) as f:
        brokers_data = json.load(f)

    verdict = check(args.broker_id, args.url, brokers_data)
    print(json.dumps(verdict, indent=2))
    sys.exit(0 if verdict["in_scope"] else 4)


if __name__ == "__main__":
    main()
