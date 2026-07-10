"""
Confirmation-email classifier for QuietLine opt-out flows.

Classifies a SAVED opt-out confirmation email into exactly one of
confirmation-link, already-confirmed, rejection, or unrelated. This script
never touches a mailbox and never makes a network call — the agent saves the
raw email body (text or HTML) to a file (or pipes it via --stdin) and this
script reasons over that saved copy only.

Classification order (first match wins):
  1. confirmation-link  — the email asks the user to click to confirm/verify
     the opt-out AND a valid https:// candidate link is extractable.
  2. already-confirmed   — the email states the removal/deletion/suppression
     is complete.
  3. rejection            — unable to process / cannot verify / denied /
     additional info required.
  4. unrelated            — none of the above.

Decoy-handling behavior (documented per M2 task spec): the confirmation-link
classification requires BOTH the pattern match AND an extractable https://
URL. If the email's language matches confirmation-link phrasing but no valid
https:// URL can be extracted (e.g. the only link present is http://, or a
javascript:/data:/relative link), the result does NOT fall back to
confirmation-link with a null URL — it falls through to the
already-confirmed/rejection checks and, finding no match there either, is
classified 'unrelated'. Rationale: the classification is meant to describe
what the agent can *act on*; if nothing is safely clickable, telling the
agent "confirmation-link, but no URL" invites it to go hunting in the raw
HTML for something to click, defeating the point of restricting extraction
to https://. See fixtures/confirm_link_http_only.html, which asserts this in
eval-07.

URL extraction treats HTML as untrusted input (see CLAUDE.md): parsed with
stdlib html.parser only (no third-party HTML libraries), never fetched.
Only absolute https:// URLs are accepted; javascript:, data:, http://, and
relative URLs are always rejected regardless of anchor text.

PII rule: non-verbose stdout output is only the classification JSON below —
it never echoes email body content. --verbose may print short matched
snippets to stderr for debugging.
"""

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))

# Phrases that indicate the email is asking the recipient to click a link to
# confirm or verify the opt-out/removal request.
CONFIRMATION_LINK_PATTERNS = [
    "click the link below to confirm",
    "click here to confirm",
    "click below to confirm",
    "click below to verify",
    "confirm your opt-out",
    "confirm your opt out",
    "confirm your removal",
    "confirm your deletion request",
    "confirm your request",
    "confirm this request",
    "confirm my opt-out",
    "please confirm",
    "verify your opt-out",
    "verify your opt out",
    "verify your request",
    "verify this request",
    "activate your opt-out",
    "activate your opt out",
]

# Phrases that indicate the removal/deletion/suppression has already completed.
ALREADY_CONFIRMED_PATTERNS = [
    "removal request has been completed",
    "removal has been completed",
    "your removal is complete",
    "successfully removed",
    "successfully opted out",
    "successfully deleted",
    "has been successfully removed",
    "information has been removed",
    "record has been deleted",
    "record has been removed",
    "deletion is complete",
    "deletion has been completed",
    "suppression is complete",
    "has been suppressed",
    "opt-out is complete",
    "opt out is complete",
    "opt-out has been completed",
    "opt out has been completed",
    "we have completed your request",
    "your listing has been removed",
]

# Phrases that indicate the broker cannot or will not process the request.
REJECTION_PATTERNS = [
    "unable to process your request",
    "unable to process this request",
    "we were unable to process",
    "cannot verify your identity",
    "unable to verify your identity",
    "could not verify your identity",
    "could not be verified",
    "request has been denied",
    "request denied",
    "we are unable to honor",
    "unable to locate",
    "we could not locate",
    "additional information is required",
    "additional information required",
    "please provide additional",
    "insufficient information",
    "unable to complete your request",
]

# Used to prefer the "right" link among several extracted https:// candidates
# (anchor text/href, or the text surrounding a bare URL in a plain-text email).
LINK_PREFERENCE_PATTERNS = ["confirm", "verify", "opt-out", "opt out", "complete"]

# Conservative: a single negated-character-class run, no nested quantifiers —
# safe against ReDoS.
_HTTPS_URL_RE = re.compile(r"https://[^\s<>\"']+")

STATUS_BY_CLASSIFICATION = {
    "confirmation-link": None,
    "already-confirmed": "confirmed-removed",
    "rejection": "failed",
    "unrelated": None,
}


class _EmailHTMLParser(HTMLParser):
    """
    Extracts visible plain text and (href, visible_text) anchor pairs from an
    HTML email body. Stdlib html.parser only, per CLAUDE.md's untrusted-input
    rule — never fetches or resolves anything, just parses the given string.
    """

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self.anchors: list[tuple[str, str]] = []
        self._current_href: Optional[str] = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "a":
            self._current_href = dict(attrs).get("href")
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href is not None:
            self.anchors.append((self._current_href, "".join(self._current_text).strip()))
            self._current_href = None
            self._current_text = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)
        if self._current_href is not None:
            self._current_text.append(data)

    def text(self) -> str:
        return " ".join(self._chunks)


def _is_valid_https_url(url: Optional[str]) -> bool:
    """Accept only absolute https:// URLs; reject javascript:, data:, http://, relative."""
    if not url:
        return False
    candidate = url.strip()
    if not candidate.lower().startswith("https://"):
        return False
    parsed = urlparse(candidate)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _looks_like_html(text: str, filename: Optional[str] = None) -> bool:
    if filename and filename.lower().endswith((".html", ".htm")):
        return True
    lowered = text.lower()
    return "<html" in lowered or "<body" in lowered or "<a " in lowered or "<a\t" in lowered


def _extract_html_confirmation_url(html: str, anchors: list[tuple[str, str]]) -> Optional[str]:
    valid = [(href, txt) for href, txt in anchors if _is_valid_https_url(href)]
    if not valid:
        return None
    preferred = [
        (href, txt)
        for href, txt in valid
        if any(p in href.lower() or p in txt.lower() for p in LINK_PREFERENCE_PATTERNS)
    ]
    if preferred:
        return preferred[0][0].strip()
    if len(valid) == 1:
        return valid[0][0].strip()
    return None


def _extract_text_confirmation_url(text: str) -> Optional[str]:
    urls = [u.rstrip(".,;)>\"'") for u in _HTTPS_URL_RE.findall(text)]
    valid = [u for u in urls if _is_valid_https_url(u)]
    if not valid:
        return None

    for line in text.splitlines():
        line_lower = line.lower()
        if any(p in line_lower for p in LINK_PREFERENCE_PATTERNS):
            for u in valid:
                if u in line:
                    return u

    if len(valid) == 1:
        return valid[0]
    return None


def _first_match(lowered_text: str, patterns: list[str]) -> Optional[str]:
    for pattern in patterns:
        if pattern in lowered_text:
            return pattern
    return None


def classify(raw: str, is_html: bool) -> dict:
    """
    Classify a saved confirmation email. Pure function — no filesystem or
    network access, no case-store interaction — so it stays trivially
    testable (see evals/run_evals.py eval-07).

    Returns a dict with keys: classification, confirmation_url,
    matched_pattern, suggested_status.
    """
    if is_html:
        parser = _EmailHTMLParser()
        try:
            parser.feed(raw)
        except Exception:
            # Malformed HTML: fall back to raw text for pattern matching and
            # skip link extraction rather than raising on untrusted input.
            plain_text = raw
            confirmation_url = None
        else:
            plain_text = parser.text()
            confirmation_url = _extract_html_confirmation_url(raw, parser.anchors)
    else:
        plain_text = raw
        confirmation_url = _extract_text_confirmation_url(raw)

    lowered = " ".join(plain_text.split()).lower()

    link_match = _first_match(lowered, CONFIRMATION_LINK_PATTERNS)
    if link_match and confirmation_url:
        return {
            "classification": "confirmation-link",
            "confirmation_url": confirmation_url,
            "matched_pattern": link_match,
            "suggested_status": STATUS_BY_CLASSIFICATION["confirmation-link"],
        }

    confirmed_match = _first_match(lowered, ALREADY_CONFIRMED_PATTERNS)
    if confirmed_match:
        return {
            "classification": "already-confirmed",
            "confirmation_url": None,
            "matched_pattern": confirmed_match,
            "suggested_status": STATUS_BY_CLASSIFICATION["already-confirmed"],
        }

    rejection_match = _first_match(lowered, REJECTION_PATTERNS)
    if rejection_match:
        return {
            "classification": "rejection",
            "confirmation_url": None,
            "matched_pattern": rejection_match,
            "suggested_status": STATUS_BY_CLASSIFICATION["rejection"],
        }

    return {
        "classification": "unrelated",
        "confirmation_url": None,
        "matched_pattern": None,
        "suggested_status": STATUS_BY_CLASSIFICATION["unrelated"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify a saved QuietLine opt-out confirmation email")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--email-file", help="Path to the saved raw email body (text or HTML)")
    source.add_argument("--stdin", action="store_true", help="Read the email body from stdin")
    parser.add_argument("--broker-id", help="Broker ID to attach to output (required for --apply)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the suggested status via case_store.mark (requires --broker-id)",
    )
    parser.add_argument(
        "--case-store",
        help="Path to cases.json (default: ~/.quietline/cases.json)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print matched-pattern context to stderr (never full email body)",
    )
    args = parser.parse_args()

    if args.apply and not args.broker_id:
        print("Error: --apply requires --broker-id.", file=sys.stderr)
        sys.exit(1)

    if args.email_file:
        path = Path(args.email_file)
        try:
            raw = path.read_text(errors="replace")
        except OSError as e:
            print(f"Error reading --email-file: {e}", file=sys.stderr)
            sys.exit(1)
        filename: Optional[str] = path.name
    else:
        raw = sys.stdin.read()
        filename = None

    is_html = _looks_like_html(raw, filename)
    result = classify(raw, is_html)

    if args.verbose:
        print(
            f"[confirmation] is_html={is_html} classification={result['classification']} "
            f"matched_pattern={result['matched_pattern']!r}",
            file=sys.stderr,
        )

    output = {
        "classification": result["classification"],
        "confirmation_url": result["confirmation_url"],
        "matched_pattern": result["matched_pattern"],
        "suggested_status": result["suggested_status"],
        "broker_id": args.broker_id,
    }
    print(json.dumps(output, indent=2))

    if args.apply:
        if result["suggested_status"] is None:
            print(
                f"No-op: classification '{result['classification']}' has no suggested status to apply.",
                file=sys.stderr,
            )
            return

        if args.case_store:
            os.environ["QUIETLINE_CASE_STORE"] = args.case_store

        # LATE IMPORT (matches action_queue.py / deadlines.py's main()):
        # case_store.py fixes CASE_STORE_PATH from QUIETLINE_CASE_STORE at
        # *import* time, so the env var must be set above before this import
        # runs for --case-store to take effect.
        import case_store as cs  # noqa: PLC0415

        cs.mark(
            args.broker_id,
            result["suggested_status"],
            note=f"confirmation.py classified '{result['classification']}' (pattern: {result['matched_pattern']!r})",
        )


if __name__ == "__main__":
    main()
