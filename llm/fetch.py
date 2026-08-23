"""Plain-HTTP page fetch for the cheap research path.

Fetching a firm's own site and extracting from that text costs a fraction
of a web-search-enabled model call. Every failure here is non-fatal: the
caller falls back to full research, so a dead link or a bot-blocked site
degrades cost, never correctness.
"""

from __future__ import annotations

import re

import requests

from framework.logging import get_logger, log_event

logger = get_logger("utilyze.fetch")

_USER_AGENT = "Mozilla/5.0 (compatible; UtilyzeResearchBot/1.0)"

_DROP_BLOCKS = re.compile(
    r"<(script|style|noscript|svg|head)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


def html_to_text(html: str) -> str:
    """Strip markup to readable text. Deliberately dependency-free — this
    feeds a language model, not a parser, so approximate is fine."""
    text = _DROP_BLOCKS.sub(" ", html)
    text = _TAG.sub("\n", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def fetch_page_text(
    url: str, *, timeout: float = 15.0, max_chars: int = 20_000
) -> str | None:
    """Fetch a URL and return its text, or None if anything goes wrong."""
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"},
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        log_event(logger, "fetch_failed", url=url, error=str(exc)[:200], level="WARNING")
        return None

    if response.status_code != 200:
        log_event(logger, "fetch_bad_status", url=url, status=response.status_code, level="WARNING")
        return None

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        log_event(logger, "fetch_not_text", url=url, content_type=content_type, level="WARNING")
        return None

    text = html_to_text(response.text)
    if not text:
        return None
    return text[:max_chars]
